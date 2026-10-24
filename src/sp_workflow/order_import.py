"""起單資料確認與匯入（#86、#97、ADR 0006、0008、0009、0010）。

讀起單建檔（`order_terms.collect_orders`）之後，作業人員在視窗裡逐檔確認、修改，再把通過檢查的商品寫進資料庫。對外：
- `drafts_from`：讀取結果 → 可以編輯的 `OrderDraft`（欄位是 `product_record.FIELDS`）。
  資料庫已有、還沒鎖住的商品，起單建檔空白的欄帶入資料庫的值；兩邊不同的欄由 `OrderDraft.differences` 列出。
- `check_draft`：逐欄檢查，回傳 {欄位: 問題}；空的就可以匯入。
- `import_orders`：匯入（`import_drafts`）、重算核對條件、重讀資料庫的值，作業人員按一次「匯入資料庫」做的事。
  `import_drafts`：通過的寫進 `workflow.product`，每次與每檔都留紀錄；整次在同一個 transaction 裡。
  `reload_stored` 在匯入後重讀資料庫的值與鎖住狀態（畫面上改到一半的值不動）。
- `load_correction`／`apply_correction`：已鎖住的商品更正條件（#55、ADR 0014）：維持鎖住、核對結果不動，
  有核對條件的重算價格，每次在 `workflow.term_correction` 留紀錄。
- `missing_products`／`open_maintenance`／`save_maintenance`：人工維護（#115、ADR 0017）：列出還沒鎖住、缺必填欄的商品
  與只有交易明細的 TDCC；打開任何一檔（已鎖住的照更正規則存檔），還沒鎖住的可以留空白，存檔後重算核對條件。
  這檔的交易明細（`trade_rows`，#116、ADR 0018）和商品條件一起存檔，在 `workflow.trade_row_correction` 另外留紀錄。
寫入商品條件的規則（鎖列、打開後沒變、檢查、上手與面額、重算、紀錄）都在 `product_write`（#124），這裡只讀與編排。
已鎖住（`locked_at` 有值：TS 核對通過，或 migration 0006 之前就在資料庫）的商品不能匯入，要改走更正（`apply_correction`）；
migration 0006 之後、清單更新停用（#87）之前由清單更新建立的商品沒有鎖，Order 匯入可以接手
（條件以 Order 為準，ISIN 與分公司配置保留）。承作金額以交易明細為準，Order 匯入不寫 `order_amount`（#97）。
傳入的 `conn` 由 `database.connect` 建立，資料表版本已確認（#200），這裡不再檢查。
"""

from __future__ import annotations

import getpass
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

from sp_workflow.check_terms import CalcReceipt, CheckTermsError, recalculate
from sp_workflow.database import OperatorError
from sp_workflow.order_terms import TDCC_FIELD, OrderRow
from sp_workflow.product_record import (
    FCN,
    FIELDS,
    PRODUCT_COLUMNS,
    REQUIRED,
    STRIKE_FIELD,
    TRADE_FIELD,
    fields_of,
    missing_fields,
    to_json,
)
from sp_workflow.product_write import (
    LOCKED,
    MAINTAIN_LOCKED,
    MAINTAIN_NEW,
    MAINTAIN_OPEN,
    NEW,
    OVERWRITTEN,
    SAME,
    SKIPPED,
    CorrectionError,
    CorrectionReceipt,
    ImportOutcome,
    Mode,
    OrderImportLine,
    blocking_problems,
    describe,
    field_edits,
    has_trade_rows,
    import_terms,
    maintenance_mode,
    not_correctable,
    read_stored,
    same_value,
    save_terms,
)
from sp_workflow.status import OrderReview
from sp_workflow.trade_rows import TradeRowsDraft

if TYPE_CHECKING:
    import psycopg

    from fcn_checker.check_config import ConfigPaths
    from fcn_checker.trading_calendar import TradingCalendar

# 匯入前每檔的狀態（`review_status`）：擋匯入、讀取時有提醒但可以匯入、可以匯入；另有已鎖住（LOCKED）
BLOCKED, TO_CHECK, READY = OrderReview.BLOCKED, OrderReview.TO_CHECK, OrderReview.READY
IMPORTABLE = frozenset((TO_CHECK, READY))


class OrderImportError(OperatorError):
    """整批無法匯入（設定檔有問題）；訊息是給作業人員看的中文。"""


@dataclass(frozen=True)
class ImportConfig:
    denominations: Mapping[str, Decimal]  # 幣別 → 單位面額預設值
    issuer_names: Mapping[str, str]  # 上手代號 → 發行機構的寫法（同清單更新）
    issuer_prefixes: Mapping[str, str]  # TDCC 前三碼 → 上手代號（上手編號對照）
    calendar: TradingCalendar  # 交易所假日表（明日開盤價的 Strike date，#101）


def load_import_config(paths: ConfigPaths) -> ImportConfig:
    """從審查標準、參考條件表格式、上手編號對照與交易所假日表設定檔讀面額預設值、發行機構寫法、上手編號與假日。"""
    from fcn_checker.config import (
        load_exchange_holidays,
        load_issuer_prefixes,
        load_reference_format,
        load_review_standard,
    )
    from fcn_checker.ingestion import IngestionError

    try:
        review = load_review_standard(paths.review_standard)
        reference = load_reference_format(paths.reference_format)
        prefixes = load_issuer_prefixes(paths.issuer_prefixes)
        calendar = load_exchange_holidays(paths.exchange_holidays)
    except IngestionError as exc:
        raise OrderImportError(str(exc)) from exc
    return ImportConfig(
        {currency: Decimal(amount) for currency, amount in review.denomination.items()},
        dict(reference.issuer_values),
        prefixes,
        calendar,
    )


@dataclass
class OrderDraft:
    """一檔商品的確認資料。`values` 是畫面上目前的值（畫面直接改這份）；`original` 是起單建檔讀到的值。"""

    row: OrderRow
    original: Mapping[str, object]
    values: dict[str, object]
    stored: Mapping[str, object] | None = None  # 打開時資料庫的值（同一套欄名）；資料庫沒有這檔時 None
    locked: str = ""  # 已鎖住的原因；空字串＝可以改
    database_checked: bool = False  # 打開時有沒有連資料庫看過（沒看過就不能覆蓋資料庫裡的這檔）
    correcting: bool = False  # 更正（#55）：空白會寫進資料庫，所以清空、補上的欄也算不同
    input_errors: dict[str, str] = field(default_factory=dict)  # 畫面上寫錯、還沒解讀成值的欄 → 說明

    @property
    def tdcc(self) -> str:
        return self.row.tdcc

    @property
    def issuer(self) -> str:
        return self.row.issuer

    @property
    def notes(self) -> Mapping[str, str]:
        """讀起單建檔時有問題、要標紅的欄 → 說明；整檔的問題在 TDCC。"""
        return self.row.notes

    def differences(self) -> dict[str, object]:
        """兩邊都有值但不同的欄 → 資料庫的值（畫面標黃）；更正時清空、補上的欄也算。"""
        if self.stored is None or self.locked:
            return {}
        return {
            name: stored
            for name, stored in self.stored.items()
            if (self.correcting or (stored is not None and self.values.get(name) is not None))
            and not same_value(self.values.get(name), stored)
        }

    def set_value(self, name: str, value: object, *, follow: bool = True) -> list[str]:
        """畫面改了一欄，回傳跟著改的其他欄。

        改 Trade date 時，Strike date 原本等於舊的 Trade date（一般情況），或因為 Trade date 空白才空著，
        就跟著改；明日開盤價算出的、或作業人員自己填的 Strike date 不動。`follow` 是 False（畫面上 Strike date
        還有沒解讀的輸入）時也不動。
        """
        followed = []
        if name == TRADE_FIELD and follow:
            strike = self.values.get(STRIKE_FIELD)
            same = strike is not None and same_value(strike, self.values.get(TRADE_FIELD))
            if same or (strike is None and STRIKE_FIELD not in self.row.must_fill):
                self.values[STRIKE_FIELD] = value
                followed.append(STRIKE_FIELD)
        self.values[name] = value
        return followed

    def edits(self) -> list[tuple[str, object, object]]:
        """人工修改：(欄位, 起單建檔的值, 改成)。資料庫帶入的值也算（確認後的值和起單建檔不同的都列）。"""
        return field_edits(self.original, self.values)


def drafts_from(rows: Iterable[OrderRow], conn: psycopg.Connection | None = None) -> list[OrderDraft]:
    """起單建檔裡找到的每一檔轉成確認資料；有資料庫時帶入資料庫的值與鎖住狀態。

    找不到的列（起單建檔沒有這檔、TDCC 不是 12 碼）沒有確認資料，畫面直接顯示 `OrderRow.notes`。
    好幾列不同、或起單建檔算不出來的欄（`OrderRow.must_fill`）不帶入資料庫的值，一定要作業人員填。
    """
    drafts = []
    for row in rows:
        if not row.found:
            continue
        original = {name: row.values.get(name) for name in FIELDS}
        stored = None if conn is None else read_stored(conn, row.tdcc)
        fields = None if stored is None else stored.fields
        locked = "" if stored is None else stored.locked
        values = dict(original)
        if fields is not None and not locked:
            for name, value in fields.items():
                if values.get(name) is None and value is not None and name not in row.must_fill:
                    values[name] = value
        drafts.append(OrderDraft(row, original, values, fields, locked, database_checked=conn is not None))
    return drafts


def reload_stored(drafts: Iterable[OrderDraft], conn: psycopg.Connection) -> None:
    """重讀每檔資料庫的值與鎖住狀態（例如匯入後），`values` 不動。"""
    for draft in drafts:
        stored = read_stored(conn, draft.tdcc)
        draft.stored = None if stored is None else stored.fields
        draft.locked = "" if stored is None else stored.locked
        draft.database_checked = True


# ---------- 檢查 ----------


def check_draft(draft: OrderDraft, config: ImportConfig) -> dict[str, str]:
    """逐欄檢查確認後的值，回傳 {欄位: 問題}（整檔的問題放在 TDCC）；空的就可以匯入。畫面上寫錯的欄也算。"""
    if draft.locked:
        return {TDCC_FIELD: draft.locked}
    return blocking_problems(draft, config, Mode.IMPORT)


def review_status(row: OrderRow, draft: OrderDraft | None, config: ImportConfig) -> tuple[str, str, bool]:
    """匯入前這檔的 (狀態, 原因, 是否要處理)：待確認、請確認、可匯入、已鎖住；找不到的照讀取狀態。"""
    if draft is None:
        return row.status, describe(row.notes), True
    if draft.locked:
        return LOCKED, draft.locked, False
    problems = check_draft(draft, config)
    if problems:
        return BLOCKED, describe(problems), True
    if row.notes:
        return TO_CHECK, describe(row.notes), False
    return READY, "", False


# ---------- 匯入 ----------


@dataclass(frozen=True)
class OrderImportReceipt:
    import_id: int
    lines: tuple[OrderImportLine, ...]

    def count(self, result: str) -> int:
        return sum(line.result == result for line in self.lines)

    def result_of(self, tdcc: str) -> str | None:
        return next((line.result for line in self.lines if line.tdcc == tdcc), None)


@dataclass(frozen=True)
class OrderImportResult:
    """按一次「匯入資料庫」的結果：匯入、重算核對條件（#88）、重讀資料庫的值。

    重算和匯入分開兩個 transaction（ADR 0012）：重算失敗時匯入照樣保留，`recalc` 是 None、`recalc_error` 寫原因。
    """

    receipt: OrderImportReceipt
    recalc: CalcReceipt | None = None
    recalc_error: str = ""
    reload_error: str = ""  # 匯入成功但重讀資料庫狀態失敗


def import_orders(
    drafts: Sequence[OrderDraft], conn: psycopg.Connection, config: ImportConfig, *, user: str | None = None
) -> OrderImportResult:
    """匯入（`import_drafts`），再重算還沒鎖住的商品、重讀每檔資料庫的值與鎖住狀態。

    匯入失敗時丟 `OrderImportError`（或資料庫的錯誤），什麼都不寫；匯入之後的重算、重讀失敗不丟錯誤，
    匯入已經寫進資料庫，原因放在結果裡。
    """
    import psycopg

    receipt = import_drafts(drafts, conn, config, user=user)
    recalc, recalc_error = None, ""
    try:
        recalc = recalculate(conn, config.calendar)
    except (CheckTermsError, psycopg.Error) as exc:
        recalc_error = _first_line(exc)
    try:
        reload_stored(drafts, conn)
    except psycopg.Error as exc:
        return OrderImportResult(receipt, recalc, recalc_error, _first_line(exc))
    return OrderImportResult(receipt, recalc, recalc_error)


def import_drafts(
    drafts: Sequence[OrderDraft], conn: psycopg.Connection, config: ImportConfig, *, user: str | None = None
) -> OrderImportReceipt:
    """把通過檢查的商品寫進資料庫；沒通過的不匯入並寫原因。整次在同一個 transaction 裡，出錯就全部不寫。

    匯入成功的檔，`values` 會補上匯入時保留的資料庫值（畫面跟資料庫一致）。
    `order_import_line.source_mail`（#40 時是 Order 原信檔名）寫起單建檔的檔名與列號。
    """
    with conn.transaction():
        import_id = conn.execute(
            "INSERT INTO workflow.order_import (imported_by, summary) VALUES (%s, '{}') RETURNING import_id",
            (user or getpass.getuser(),),
        ).fetchone()[0]
        lines = []
        seen: set[str] = set()
        for seq, draft in enumerate(drafts, 1):
            if draft.tdcc in seen:
                outcome = ImportOutcome(OrderImportLine(draft.tdcc, SKIPPED, "同一批出現兩次"))
            else:
                outcome = import_terms(conn, draft, config, import_id)
            seen.add(draft.tdcc)
            lines.append(outcome.line)
            conn.execute(
                "INSERT INTO workflow.order_import_line"
                " (import_id, seq, tdcc, result, detail, source_mail, confirmed, previous, edits)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb)",
                (
                    import_id,
                    seq,
                    draft.tdcc,
                    outcome.line.result,
                    outcome.line.detail or None,
                    _source_of(draft.row),
                    to_json(outcome.confirmed),
                    to_json(outcome.previous),
                    to_json([list(edit) for edit in outcome.edits] or None),
                ),
            )
        receipt = OrderImportReceipt(import_id, tuple(lines))
        counts = {result: receipt.count(result) for result in (NEW, OVERWRITTEN, SAME, SKIPPED, LOCKED)}
        conn.execute(
            "UPDATE workflow.order_import SET summary = %s::jsonb WHERE import_id = %s",
            (json.dumps({"counts": counts}, ensure_ascii=False), import_id),
        )
    return receipt


def _source_of(row: OrderRow) -> str | None:
    if row.source is None:
        return None
    return f"{row.source.name} 第 {'、'.join(map(str, row.lines))} 列"


def _first_line(exc: Exception) -> str:
    return str(exc).splitlines()[0] if str(exc) else type(exc).__name__


# ---------- 更正（#55） ----------


def load_correction(conn: psycopg.Connection, tdcc: str, config: ImportConfig) -> OrderDraft:
    """已鎖住的商品 → 可以編輯的確認資料（值、打開時的值都是資料庫目前的條件）；不能更正時丟 `CorrectionError`。"""
    stored = read_stored(conn, tdcc)
    if refused := not_correctable(stored):
        raise CorrectionError(f"{tdcc}：{refused}")
    assert stored is not None
    fields = stored.fields
    row = OrderRow(tdcc, LOCKED, issuer_code(tdcc, stored.columns["issuer"], config), dict(fields))
    return OrderDraft(row, dict(fields), dict(fields), dict(fields), database_checked=True, correcting=True)


def issuer_code(tdcc: str, issuer: str, config: ImportConfig) -> str:
    """上手代號：TDCC 前三碼；對照沒有時用資料庫的發行機構寫法反查（上手不改，只用來檢查與顯示）。"""
    code = config.issuer_prefixes.get(tdcc[:3], "")
    if code in config.issuer_names:
        return code
    return next((c for c, name in config.issuer_names.items() if name == issuer), code)


def apply_correction(
    conn: psycopg.Connection,
    draft: OrderDraft,
    config: ImportConfig,
    *,
    trades: TradeRowsDraft | None = None,
    reason: str = "",
    user: str | None = None,
) -> CorrectionReceipt:
    """把更正後的條件寫進資料庫：商品維持鎖住、核對結果不動，有核對條件的重算價格，每次留一筆紀錄。

    照起單資料匯入的規則檢查；上手、發行日不改，單位面額只有幣別改了時換成新幣別的預設值。
    `trades` 是這檔的交易明細（人工維護，#116）：改了的格子一起寫入、另外留一筆紀錄；改到核對條件會用到的欄
    （分公司配置、交叉比對）時，條件沒改也重算。打開之後資料庫的條件或交易明細被改過、或已經不是鎖住的，不寫入。
    整次在同一個 transaction 裡；寫入後 `draft`、`trades` 換成資料庫存的值（同重新打開）。
    """
    return save_terms(conn, draft, config, Mode.CORRECT, trades=trades, reason=reason, user=user)


# ---------- 人工維護（#115） ----------

MISSING_NOTE = "缺，可以先存、之後再補"  # 還沒鎖住的商品空著的必填欄：畫面標紅但不擋存檔


@dataclass(frozen=True)
class MissingProduct:
    """「檢查」列出的一檔：還沒鎖住而且缺必填欄的商品，或只有交易明細的 TDCC。"""

    tdcc: str
    issuer: str  # 上手代號
    status: str  # 未鎖住／只有交易明細
    values: Mapping[str, object]  # 畫面的欄 → 資料庫的值（只有交易明細時都是空白）
    missing: tuple[str, ...]  # 缺的必填欄，依 `REQUIRED` 的順序


def missing_products(conn: psycopg.Connection, config: ImportConfig) -> tuple[MissingProduct, ...]:
    """還沒鎖住、缺必填欄的商品，和只有交易明細（還沒有商品條件）的 TDCC，依 TDCC 排。

    缺資料以起單資料的必填欄（`product_record.REQUIRED`）為準，ISIN、發行日這類由 TS 回填的不算；
    只有交易明細的，必填欄全部算缺。
    """
    found = []
    with conn.transaction():
        for tdcc, *row in conn.execute(
            f"SELECT tdcc, {', '.join(PRODUCT_COLUMNS)} FROM workflow.product WHERE locked_at IS NULL"
        ):
            columns = dict(zip(PRODUCT_COLUMNS, row, strict=True))
            missing = tuple(missing_fields(columns))
            if missing:
                issuer = issuer_code(tdcc, columns["issuer"], config)
                found.append(MissingProduct(tdcc, issuer, MAINTAIN_OPEN, fields_of(columns), missing))
        for (tdcc,) in conn.execute(
            "SELECT DISTINCT tdcc FROM workflow.trade_row t"
            " WHERE NOT EXISTS (SELECT 1 FROM workflow.product p WHERE p.tdcc = t.tdcc)"
        ):
            issuer = config.issuer_prefixes.get(tdcc[:3], "")
            found.append(MissingProduct(tdcc, issuer, MAINTAIN_NEW, dict.fromkeys(FIELDS), REQUIRED))
    return tuple(sorted(found, key=lambda product: product.tdcc))


def open_maintenance(conn: psycopg.Connection, tdcc: str, config: ImportConfig) -> OrderDraft:
    """打開一檔來維護：資料庫有商品條件的帶出目前的值（已鎖住的同 `load_correction`）；只有交易明細的
    從空白開始（商品種類 FCN，上手依上手編號對照）。兩邊都沒有時丟 `CorrectionError`。"""
    stored = read_stored(conn, tdcc)
    if stored is not None and stored.locked:
        return load_correction(conn, tdcc, config)
    if stored is not None:
        fields = stored.fields
        row = OrderRow(tdcc, MAINTAIN_OPEN, issuer_code(tdcc, stored.columns["issuer"], config), dict(fields))
        return OrderDraft(row, dict(fields), dict(fields), dict(fields), database_checked=True, correcting=True)
    if not has_trade_rows(conn, tdcc):
        raise CorrectionError(f"{tdcc}：交易明細和商品條件都沒有這檔，不能在這裡新增")
    values: dict[str, object] = {**dict.fromkeys(FIELDS), "Product": FCN}  # 交易明細只有 FCN
    row = OrderRow(tdcc, MAINTAIN_NEW, config.issuer_prefixes.get(tdcc[:3], ""), dict(values))
    return OrderDraft(row, dict(values), dict(values), dict(values), database_checked=True, correcting=True)


def maintenance_problems(draft: OrderDraft, config: ImportConfig) -> dict[str, str]:
    """畫面標紅用：擋存檔的問題，加上還沒鎖住的商品空著的必填欄（`MISSING_NOTE`，不擋存檔）。

    已鎖住的照更正的規則（空白也擋）。上手的問題放在 TDCC；資料庫已有的商品上手不改，不擋。
    """
    problems = blocking_problems(draft, config, maintenance_mode(draft))
    if draft.row.status == MAINTAIN_LOCKED:
        return problems
    blank = [name for name in REQUIRED if draft.values.get(name) is None and name not in problems]
    return {**problems, **dict.fromkeys(blank, MISSING_NOTE)}


def save_maintenance(
    conn: psycopg.Connection,
    draft: OrderDraft,
    config: ImportConfig,
    *,
    trades: TradeRowsDraft | None = None,
    reason: str = "",
    user: str | None = None,
) -> CorrectionReceipt:
    """人工維護存檔。已鎖住的照更正（同 `apply_correction`）；還沒鎖住的可以留空白（格式錯的擋），寫入後重算核對條件；
    只有交易明細的新建商品條件（來源「人工維護」）。商品條件每次在 `workflow.term_correction` 留一筆（`locked` 是
    false）。`trades` 是這檔的交易明細（#116）：改了的格子一起寫入，在 `workflow.trade_row_correction` 另外留一筆。
    沒改任何欄時不寫入。打開之後資料庫的這檔（商品條件或交易明細）被改過、鎖住、建立了商品條件或交易明細沒了，不寫入。
    整次在同一個 transaction 裡；寫入後 `draft`、`trades` 換成資料庫存的值（同重新打開）。
    """
    return save_terms(conn, draft, config, maintenance_mode(draft), trades=trades, reason=reason, user=user)
