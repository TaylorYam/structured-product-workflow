"""起單資料確認與匯入（#86、#97、ADR 0006、0008、0009、0010）。

讀起單建檔（`order_terms.collect_orders`）之後，作業人員在視窗裡逐檔確認、修改，再把通過檢查的商品寫進資料庫。對外：
- `drafts_from`：讀取結果 → 可以編輯的 `OrderDraft`（欄位是 `order_terms.FIELDS`）。
  資料庫已有、還沒鎖住的商品，起單建檔空白的欄帶入資料庫的值；兩邊不同的欄由 `OrderDraft.differences` 列出。
- `check_draft`：逐欄檢查，回傳 {欄位: 問題}；空的就可以匯入。
- `import_drafts`：通過的寫進 `workflow.product`，每次與每檔都留紀錄；整次在同一個 transaction 裡。
  `reload_stored` 在匯入後重讀資料庫的值與鎖住狀態（畫面上改到一半的值不動）。
- `load_correction`／`apply_correction`：已鎖住的商品更正條件（#55、ADR 0014）：維持鎖住、核對結果不動，
  有核對條件的重算價格，每次在 `workflow.term_correction` 留紀錄。
- `missing_products`／`open_maintenance`／`save_maintenance`：人工維護（#115、ADR 0017）：列出還沒鎖住、缺必填欄的商品
  與只有交易明細的 TDCC；打開任何一檔（已鎖住的照更正規則存檔），還沒鎖住的可以留空白，存檔後重算核對條件。
  這檔的交易明細（`trade_rows`，#116、ADR 0018）和商品條件一起存檔，在 `workflow.trade_row_correction` 另外留紀錄。
規則式、不猜值：單位面額依幣別預設值（審查標準 `[denomination]`），發行機構寫法依參考條件表 `[issuer_values]`。
已鎖住（`locked_at` 有值：TS 核對通過，或 migration 0006 之前就在資料庫）的商品不能匯入，要改走更正（`apply_correction`）；
migration 0006 之後、清單更新停用（#87）之前由清單更新建立的商品沒有鎖，Order 匯入可以接手
（條件以 Order 為準，ISIN 與分公司配置保留）。承作金額以交易明細為準，Order 匯入不寫 `order_amount`（#97）。
"""

from __future__ import annotations

import datetime as dt
import getpass
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from sp_workflow.check_terms import CalcLine, recalculate, recalculate_corrected
from sp_workflow.daily_import import FCN
from sp_workflow.database import DatabaseError, require_current_schema
from sp_workflow.order_terms import (
    BARRIER_TYPES,
    CHOICES,
    COLUMN_FIELDS,
    FIELDS,
    KINDS,
    KO_TYPES,
    PRICING_FIELD,
    REQUIRED,
    STRIKE_FIELD,
    TDCC_FIELD,
    TRADE_FIELD,
    UNDERLYINGS,
    UPFRONT_FIELD,
    FieldKind,
    OrderRow,
    missing_fields,
    plain,
    unknown_issuer,
)
from sp_workflow.trade_rows import (
    CHECK_TERM_FIELDS,
    TradeEdit,
    TradeRowsDraft,
    changed_since_opened,
    read_rows,
    trade_problems,
    unmatched_names,
    write_trade_rows,
)

if TYPE_CHECKING:
    import psycopg

    from fcn_checker.check_config import ConfigPaths
    from fcn_checker.trading_calendar import TradingCalendar

STATUS_IN_STOCK = "在庫"
# workflow.product.source：商品資料由哪裡建立（#86）；清單更新已停用（#87），只剩以前建立的商品
INVENTORY_SOURCE = "清單更新"
ORDER_SOURCE = "Order"  # 「匯入起單資料」（#97 起從起單建檔讀）
MANUAL_SOURCE = "人工維護"  # 只有交易明細的 TDCC 在人工維護建立（#115）

# 匯入結果
NEW = "新增"
OVERWRITTEN = "覆蓋"
SAME = "沒有變"
SKIPPED = "不匯入"
LOCKED = "已鎖住"
IMPORTED = frozenset((NEW, OVERWRITTEN, SAME))
# 匯入前每檔的狀態（`review_status`）：擋匯入、讀取時有提醒但可以匯入、可以匯入；另有已鎖住（LOCKED）
BLOCKED, TO_CHECK, READY = "待確認", "請確認", "可匯入"
IMPORTABLE = frozenset((TO_CHECK, READY))

_PCT = Decimal("0.000001")
_AMOUNT = Decimal("0.0001")
_HUNDRED = Decimal(100)

# KO Type → 資料庫的 (ko_freq, ko_memory)；Barrier Type → ki_freq（同清單更新的代碼）
KO_CODES = dict(zip(KO_TYPES, (("D", "N"), ("D", "Y"), ("P", "N"), ("P", "Y")), strict=True))
KI_CODES = dict(zip(BARRIER_TYPES, ("-", "AM", "D"), strict=True))  # 每期觀察 MKI 目前不支援，讀到時標紅
_TYPES: dict[FieldKind, tuple[type, ...]] = {
    FieldKind.TEXT: (str,),
    FieldKind.CHOICE: (str,),
    FieldKind.PERCENT: (Decimal, int),
    FieldKind.INT: (int,),
    FieldKind.DATE: (dt.date,),
}

_CONFLICT = "起單建檔裡這檔有好幾列、這欄不同，要填正確的值（正確是空白的話請改起單建檔後重新讀取）"
# 人工維護可以空白的必填欄（#115）：資料庫的商品種類不可空白（ADR 0016）
_BLANK_ALLOWED = frozenset(REQUIRED) - {"Product"}

# 資料庫商品欄位（和畫面的欄的對照是 `order_terms.COLUMN_FIELDS`）
_PRODUCT_COLUMNS = (
    "product", "issuer", "currency", "trade_date", "issue_date", "final_valuation_date", "maturity_date",
    "initial_pricing", "non_call_months", "underlying_1", "underlying_2", "underlying_3", "underlying_4",
    "underlying_5", "strike_pct", "ko_freq", "ko_memory", "ko_type", "ko_pct", "coupon_pct", "upfront_pct",
    "tenor_months", "ki_freq", "barrier_type", "ki_pct", "denomination", "strike_date",
)  # fmt: skip
# 由 KO Type／Barrier Type 換算的代碼欄、發行機構與面額：覆蓋時用哪個名稱說明
_DERIVED_COLUMNS = {
    "ko_freq": "KO Type",
    "ko_memory": "KO Type",
    "ki_freq": "Barrier Type",
    "issuer": "上手",
    "denomination": "單位面額",
}


class OrderImportError(Exception):
    """整批無法匯入（資料表不是最新版本、設定檔有問題）；訊息是給作業人員看的中文。"""


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
            and not _same(self.values.get(name), stored)
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
            same = strike is not None and _same(strike, self.values.get(TRADE_FIELD))
            if same or (strike is None and STRIKE_FIELD not in self.row.must_fill):
                self.values[STRIKE_FIELD] = value
                followed.append(STRIKE_FIELD)
        self.values[name] = value
        return followed

    def edits(self) -> list[tuple[str, object, object]]:
        """人工修改：(欄位, 起單建檔的值, 改成)。資料庫帶入的值也算（確認後的值和起單建檔不同的都列）。"""
        return _edits(self.original, self.values)


def drafts_from(rows: Iterable[OrderRow], conn: psycopg.Connection | None = None) -> list[OrderDraft]:
    """起單建檔裡找到的每一檔轉成確認資料；有資料庫時帶入資料庫的值與鎖住狀態。

    找不到的列（起單建檔沒有這檔、TDCC 不是 12 碼）沒有確認資料，畫面直接顯示 `OrderRow.notes`。
    好幾列不同、或起單建檔算不出來的欄（`OrderRow.must_fill`）不帶入資料庫的值，一定要作業人員填。
    資料表不是最新版本時丟 `DatabaseError`。
    """
    if conn is not None:
        require_current_schema(conn)
    drafts = []
    for row in rows:
        if not row.found:
            continue
        original = {name: row.values.get(name) for name in FIELDS}
        stored = None if conn is None else _stored(conn, row.tdcc)
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
        stored = _stored(conn, draft.tdcc)
        draft.stored = None if stored is None else stored.fields
        draft.locked = "" if stored is None else stored.locked
        draft.database_checked = True


@dataclass(frozen=True)
class _Stored:
    """資料庫裡的一檔商品：原始欄位、畫面的欄、鎖住原因。"""

    columns: dict[str, object]
    fields: dict[str, object]
    source: str
    locked: str


def _stored(conn: psycopg.Connection, tdcc: str) -> _Stored | None:
    with conn.transaction():
        found = conn.execute(
            f"SELECT {', '.join(_PRODUCT_COLUMNS)}, source, locked_at FROM workflow.product WHERE tdcc = %s", (tdcc,)
        ).fetchone()
    if found is None:
        return None
    columns = dict(zip(_PRODUCT_COLUMNS, found[: len(_PRODUCT_COLUMNS)], strict=True))
    source, locked_at = found[len(_PRODUCT_COLUMNS) :]
    if locked_at is None:
        locked = ""
    elif source == INVENTORY_SOURCE:
        locked = "以前清單更新匯入的商品，條件已鎖住，要改請到「7 人工維護」"
    else:
        locked = "已核對通過，條件已鎖住，要改請到「7 人工維護」"
    return _Stored(columns, _fields_of(columns), source, locked)


def _fields_of(columns: Mapping[str, Any]) -> dict[str, object]:
    fields: dict[str, object] = {}
    for column, name in COLUMN_FIELDS.items():
        value = columns[column]
        if column == "upfront_pct" and value is not None:
            value = _HUNDRED - value
        fields[name] = plain(value) if isinstance(value, Decimal) else value
    return fields


# ---------- 檢查 ----------


def check_draft(draft: OrderDraft, config: ImportConfig) -> dict[str, str]:
    """逐欄檢查確認後的值，回傳 {欄位: 問題}（整檔的問題放在 TDCC）；空的就可以匯入。畫面上寫錯的欄也算。"""
    if draft.locked:
        return {TDCC_FIELD: draft.locked}
    return {**_value_problems(draft.values, draft, config), **draft.input_errors}


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


def _value_problems(
    values: Mapping[str, object], draft: OrderDraft, config: ImportConfig, *, allow_blank: bool = False
) -> dict[str, str]:
    """`allow_blank`（人工維護，#115）：必填欄（商品種類除外）與有 KI 時的 KI(%) 可以空白，只檢查有填的欄。"""
    problems: dict[str, str] = {}
    if not draft.issuer:
        problems[TDCC_FIELD] = unknown_issuer(draft.tdcc)
    elif draft.issuer not in config.issuer_names:
        problems[TDCC_FIELD] = f"上手 {draft.issuer} 在參考條件表設定沒有發行機構寫法"
    for name in FIELDS:
        value = values.get(name)
        if value is None:
            if name in draft.row.conflicts:
                problems[name] = _CONFLICT
            elif name in draft.row.unresolved:
                problems[name] = "起單建檔算不出來，要填"
            elif name in REQUIRED and not (allow_blank and name in _BLANK_ALLOWED):
                problems[name] = "必填"
            continue
        if not isinstance(value, _TYPES[KINDS[name]]) or isinstance(value, bool):
            problems[name] = f"要填{KINDS[name].value}"
        elif KINDS[name] is FieldKind.CHOICE and value not in CHOICES[name]:
            problems[name] = f"要選 {'／'.join(CHOICES[name])}"

    def filled(name: str) -> bool:
        return name not in problems and values.get(name) is not None

    if filled("Product") and str(values["Product"]).upper() != FCN:
        problems["Product"] = "目前只做 FCN"
    if filled("Currency") and values["Currency"] not in config.denominations:
        problems["Currency"] = "這個幣別沒有單位面額預設值"
    for name in ("Guaranteed Periods (m)", "Tenor (m)"):
        if filled(name) and values[name] < 1:
            problems[name] = "至少 1"
    if filled(TRADE_FIELD) and filled(STRIKE_FIELD) and values[STRIKE_FIELD] < values[TRADE_FIELD]:
        problems[STRIKE_FIELD] = "不能早於 Trade date"
    underlyings = [values.get(name) is not None for name in UNDERLYINGS]
    if any(later and not earlier for earlier, later in zip(underlyings, underlyings[1:], strict=False)):
        gap = UNDERLYINGS[underlyings.index(False)]
        problems.setdefault(gap, "標的要從 BBG Code 1 依序填，中間不能空")
    if filled("Barrier Type") and "KI Barrier (%)" not in problems:
        ki = values.get("KI Barrier (%)")
        if values["Barrier Type"] == "NONE" and ki is not None:
            problems["KI Barrier (%)"] = "Barrier Type 是 NONE 時要空白"
        elif values["Barrier Type"] != "NONE" and ki is None:
            # 人工維護也擋：KI(%) 不在必填欄，空著存進去「檢查」找不到、寄信會寫成「-」
            hint = "；還不確定就把 Barrier Type 也先空著" if allow_blank else ""
            problems["KI Barrier (%)"] = f"有 KI 時必填{hint}"
    return problems


def describe(problems: Mapping[str, str]) -> str:
    """{欄位: 問題} → 一行說明。"""
    return "；".join(f"{name}：{text}" for name, text in problems.items())


# ---------- 匯入 ----------


@dataclass(frozen=True)
class OrderImportLine:
    tdcc: str
    result: str  # 新增／覆蓋／沒有變／不匯入／已鎖住
    detail: str = ""

    @property
    def imported(self) -> bool:
        return self.result in IMPORTED


@dataclass(frozen=True)
class OrderImportReceipt:
    import_id: int
    lines: tuple[OrderImportLine, ...]

    def count(self, result: str) -> int:
        return sum(line.result == result for line in self.lines)

    def result_of(self, tdcc: str) -> str | None:
        return next((line.result for line in self.lines if line.tdcc == tdcc), None)


@dataclass(frozen=True)
class _Outcome:
    line: OrderImportLine
    confirmed: Mapping[str, object] | None = None  # 確認後的值（畫面的欄）
    previous: Mapping[str, object] | None = None  # 覆蓋前資料庫的欄位
    edits: Sequence[tuple[str, object, object]] = ()


def import_drafts(
    drafts: Sequence[OrderDraft], conn: psycopg.Connection, config: ImportConfig, *, user: str | None = None
) -> OrderImportReceipt:
    """把通過檢查的商品寫進資料庫；沒通過的不匯入並寫原因。整次在同一個 transaction 裡，出錯就全部不寫。

    匯入成功的檔，`values` 會補上匯入時保留的資料庫值（畫面跟資料庫一致）。
    `order_import_line.source_mail`（#40 時是 Order 原信檔名）寫起單建檔的檔名與列號。
    """
    try:
        require_current_schema(conn)
    except DatabaseError as exc:
        raise OrderImportError(str(exc)) from exc
    with conn.transaction():
        import_id = conn.execute(
            "INSERT INTO workflow.order_import (imported_by, summary) VALUES (%s, '{}') RETURNING import_id",
            (user or getpass.getuser(),),
        ).fetchone()[0]
        lines = []
        seen: set[str] = set()
        for seq, draft in enumerate(drafts, 1):
            if draft.tdcc in seen:
                outcome = _Outcome(OrderImportLine(draft.tdcc, SKIPPED, "同一批出現兩次"))
            else:
                outcome = _import_one(conn, draft, config, import_id)
            if outcome.line.imported and outcome.confirmed is not None:
                draft.values.update(outcome.confirmed)
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
                    _json(outcome.confirmed),
                    _json(outcome.previous),
                    _json([list(edit) for edit in outcome.edits] or None),
                ),
            )
        receipt = OrderImportReceipt(import_id, tuple(lines))
        counts = {result: receipt.count(result) for result in (NEW, OVERWRITTEN, SAME, SKIPPED, LOCKED)}
        conn.execute(
            "UPDATE workflow.order_import SET summary = %s::jsonb WHERE import_id = %s",
            (json.dumps({"counts": counts}, ensure_ascii=False), import_id),
        )
    return receipt


def _import_one(conn: psycopg.Connection, draft: OrderDraft, config: ImportConfig, import_id: int) -> _Outcome:
    stored = _stored(conn, draft.tdcc)  # 匯入當下重新看一次，不用畫面打開時的狀態
    if stored is not None and stored.locked:
        return _Outcome(OrderImportLine(draft.tdcc, LOCKED, stored.locked), previous=stored.columns)
    if stored is not None and not draft.database_checked:
        reason = "讀取時沒有連上資料庫，看不到資料庫裡這檔的值；請重新讀取後再確認"
        return _Outcome(OrderImportLine(draft.tdcc, SKIPPED, reason), previous=stored.columns)
    values = _keep_database_values(draft, stored)
    edits = _edits(draft.original, values)
    problems = {**_value_problems(values, draft, config), **draft.input_errors}
    if problems:
        line = OrderImportLine(draft.tdcc, SKIPPED, describe(problems))
        return _Outcome(line, values, None if stored is None else stored.columns, edits)
    product = _product_columns(values, draft.issuer, config)
    if stored is None:
        columns = ("tdcc", *_PRODUCT_COLUMNS, "status_event", "source", "order_import_id")
        conn.execute(
            f"INSERT INTO workflow.product ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))})",
            (draft.tdcc, *(product[c] for c in _PRODUCT_COLUMNS), STATUS_IN_STOCK, ORDER_SOURCE, import_id),
        )
        return _Outcome(OrderImportLine(draft.tdcc, NEW), values, None, edits)
    changed = [c for c in _PRODUCT_COLUMNS if not _same(stored.columns[c], product[c])]
    if not changed and stored.source == ORDER_SOURCE:
        return _Outcome(OrderImportLine(draft.tdcc, SAME), values, stored.columns, edits)
    assignments = ", ".join(f"{c} = %s" for c in _PRODUCT_COLUMNS)
    conn.execute(
        f"UPDATE workflow.product SET {assignments}, source = %s, order_import_id = %s WHERE tdcc = %s",
        (*(product[c] for c in _PRODUCT_COLUMNS), ORDER_SOURCE, import_id, draft.tdcc),
    )
    names = list(dict.fromkeys(COLUMN_FIELDS.get(c) or _DERIVED_COLUMNS[c] for c in changed))
    detail = "改了：" + "、".join(names) if names else ""
    if stored.source != ORDER_SOURCE:
        detail = "；".join(filter(None, (f"{stored.source}建立的商品改由起單資料匯入接手", detail)))
    return _Outcome(OrderImportLine(draft.tdcc, OVERWRITTEN, detail), values, stored.columns, edits)


def _keep_database_values(draft: OrderDraft, stored: _Stored | None) -> dict[str, object]:
    """空白的欄不蓋掉資料庫的值，除非作業人員在畫面上看過那個值、刻意清空（打開時資料庫就有）。

    好幾列不同、或起單建檔算不出來的欄（`OrderRow.must_fill`）一定要作業人員填，不用資料庫的值補。
    """
    values = dict(draft.values)
    if stored is None:
        return values
    seen = draft.stored or {}
    for name, value in stored.fields.items():
        if (
            values.get(name) is None
            and value is not None
            and seen.get(name) is None
            and name not in draft.row.must_fill
        ):
            values[name] = value
    return values


def _product_columns(values: Mapping[str, Any], issuer: str, config: ImportConfig) -> dict[str, object]:
    """畫面的值 → 資料庫商品欄位；空白的欄（人工維護，#115）寫空白，換算的代碼與單位面額也是空白。"""
    ko_freq, ko_memory = KO_CODES.get(values.get("KO Type"), (None, None))
    barrier, currency, upfront = values.get("Barrier Type"), values.get("Currency"), values.get(UPFRONT_FIELD)
    return {
        "product": FCN,
        "issuer": config.issuer_names.get(issuer),  # 更正時不用（上手不改）
        "currency": currency,
        "trade_date": values.get(TRADE_FIELD),
        "strike_date": values.get(STRIKE_FIELD),
        "issue_date": values.get("Issue date"),
        "final_valuation_date": values.get("Final valuation date"),
        "maturity_date": values.get("Maturity date"),
        "initial_pricing": values.get(PRICING_FIELD),
        "non_call_months": values.get("Guaranteed Periods (m)"),
        **{f"underlying_{n}": values.get(name) for n, name in enumerate(UNDERLYINGS, 1)},
        "strike_pct": _pct(values.get("Strike (%)")),
        "ko_freq": ko_freq,
        "ko_memory": ko_memory,
        "ko_type": values.get("KO Type"),
        "ko_pct": _pct(values.get("KO Barrier (%)")),
        "coupon_pct": _pct(values.get("Coupon p.a. (%)")),
        "upfront_pct": None if upfront is None else _pct(_HUNDRED - Decimal(upfront)),
        "tenor_months": values.get("Tenor (m)"),
        "ki_freq": KI_CODES.get(barrier),
        "barrier_type": barrier,
        "ki_pct": _pct(values.get("KI Barrier (%)")),
        "denomination": None if currency is None else Decimal(config.denominations[currency]).quantize(_AMOUNT),
    }


def _source_of(row: OrderRow) -> str | None:
    if row.source is None:
        return None
    return f"{row.source.name} 第 {'、'.join(map(str, row.lines))} 列"


# ---------- 更正（#55） ----------

FIXED_IN_CORRECTION = ("Issue date",)  # TS 回填的欄，不在更正範圍


class CorrectionError(Exception):
    """這檔不能更正（不在資料庫、還沒鎖住、檢查沒過、打開後被改過）；訊息是給作業人員看的中文。"""


@dataclass(frozen=True)
class CorrectionReceipt:
    """更正或人工維護存檔的結果；`correction_id` 是 `workflow.term_correction` 的那一筆，
    `trade_correction_id` 是 `workflow.trade_row_correction` 的那一筆（#116）。"""

    tdcc: str
    correction_id: int | None  # 沒有改商品條件時 None（沒有寫入）
    changes: tuple[tuple[str, object, object], ...] = ()  # (欄位或 UL_n_價格, 更正前, 更正後)
    calc: CalcLine | None = None  # 重算後的核對條件；沒有重算（沒有核對條件、沒改到影響核對條件的欄）是 None
    locked: bool = True  # 存檔時已鎖住（更正）；False 是還沒鎖住的商品（人工維護，#115）
    created: bool = False  # 只有交易明細的 TDCC 新建了商品條件
    missing: tuple[str, ...] = ()  # 存檔後還空著的必填欄（只有還沒鎖住的商品會有）
    trade_correction_id: int | None = None  # 沒有改交易明細時 None
    trade_changes: tuple[TradeEdit, ...] = ()  # (row_seq, 欄, 存檔前, 存檔後)
    unmatched: tuple[str, ...] = ()  # 改了的 FC 名字裡對不到信箱的

    @property
    def saved(self) -> bool:
        """有寫入（商品條件或交易明細）。"""
        return self.correction_id is not None or self.trade_correction_id is not None


def load_correction(conn: psycopg.Connection, tdcc: str, config: ImportConfig) -> OrderDraft:
    """已鎖住的商品 → 可以編輯的確認資料（值、打開時的值都是資料庫目前的條件）；不能更正時丟 `CorrectionError`。"""
    _require_schema(conn)
    stored = _stored(conn, tdcc)
    _require_locked(tdcc, stored)
    assert stored is not None
    fields = stored.fields
    row = OrderRow(tdcc, LOCKED, _issuer_code(tdcc, stored.columns["issuer"], config), dict(fields))
    return OrderDraft(row, dict(fields), dict(fields), dict(fields), database_checked=True, correcting=True)


def _issuer_code(tdcc: str, issuer: str, config: ImportConfig) -> str:
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
    _require_schema(conn)
    tdcc = draft.tdcc
    with conn.transaction():
        conn.execute("SELECT 1 FROM workflow.product WHERE tdcc = %s FOR UPDATE", (tdcc,))
        stored = _stored(conn, tdcc)
        _require_locked(tdcc, stored)
        assert stored is not None
        if _changed_since_opened(stored, draft):
            raise CorrectionError(f"{tdcc}：資料庫裡這檔的條件在打開之後改過了，請重新載入再更正")
        _require_trades_unchanged(conn, trades)
        values = draft.values
        problems = {**_value_problems(values, draft, config), **draft.input_errors}
        problems.pop(TDCC_FIELD, None)  # 只有上手的問題放在 TDCC；上手不改，不擋更正
        for name in FIXED_IN_CORRECTION:
            if not _same(values.get(name), stored.fields.get(name)):
                problems[name] = "TS 回填的欄位，不能更正"
        _raise_problems(f"{tdcc} 沒有更正", problems, trades)
        product = _product_columns(values, draft.issuer, config)
        product["issuer"], product["issue_date"] = stored.columns["issuer"], stored.columns["issue_date"]
        if values["Currency"] == stored.fields["Currency"]:
            product["denomination"] = stored.columns["denomination"]
        changed = [c for c in _PRODUCT_COLUMNS if not _same(stored.columns[c], product[c])]
        trade_changes = [] if trades is None else write_trade_rows(conn, trades)
        if not changed and not trade_changes:
            return CorrectionReceipt(tdcc, None)
        changes = _changes(stored.fields, stored.columns, values, product, changed) if changed else []
        if changed:
            assignments = ", ".join(f"{c} = %s" for c in changed)
            conn.execute(
                f"UPDATE workflow.product SET {assignments} WHERE tdcc = %s", (*(product[c] for c in changed), tdcc)
            )
        affected = any(name in CHECK_TERM_FIELDS for _, name, _, _ in trade_changes)
        recalc = recalculate_corrected(conn, config.calendar, tdcc, stored.columns, trades_changed=affected)
        calc, price_changes = (None, ()) if recalc is None else recalc
        changes += price_changes
        user = user or getpass.getuser()
        correction_id = None
        if changed:
            correction_id = _record_terms(conn, tdcc, changes, stored.columns, locked=True, reason=reason, user=user)
        trade_id = None
        if trades is not None and trade_changes:
            prices = () if changed else price_changes  # 商品條件也改了時，價格記在商品條件的紀錄
            trade_id = _record_trades(conn, trades, trade_changes, prices, locked=True, reason=reason, user=user)
        saved = _saved_fields(conn, tdcc)
        saved_trades = None if trades is None else read_rows(conn, tdcc)
    _reopen(draft, saved)
    if trades is not None and saved_trades is not None:
        trades.reopen(saved_trades)
    return CorrectionReceipt(
        tdcc, correction_id, tuple(changes), calc, **_trade_receipt(trades, trade_changes, trade_id)
    )


def _changed_since_opened(stored: _Stored, draft: OrderDraft) -> bool:
    """資料庫裡這檔的條件和打開時看到的（`draft.stored`）不同：打開之後有別人改過。"""
    seen = draft.stored or {}
    return any(not _same(stored.fields[name], seen.get(name)) for name in stored.fields)


def _saved_fields(conn: psycopg.Connection, tdcc: str) -> dict[str, object]:
    stored = _stored(conn, tdcc)
    assert stored is not None
    return stored.fields


def _reopen(draft: OrderDraft, saved: Mapping[str, object]) -> None:
    """存檔後當成重新打開：畫面與打開時的值都換成資料庫存的值（百分比存成小數 6 位），
    同一檔接著改、再存時才不會誤判成打開之後有別人改過。"""
    draft.values, draft.original, draft.stored = dict(saved), dict(saved), dict(saved)


def _require_schema(conn: psycopg.Connection) -> None:
    try:
        require_current_schema(conn)
    except DatabaseError as exc:
        raise CorrectionError(str(exc)) from exc


def _require_locked(tdcc: str, stored: _Stored | None) -> None:
    if stored is None:
        raise CorrectionError(f"{tdcc}：資料庫沒有這檔商品")
    if not stored.locked:
        raise CorrectionError(f"{tdcc}：還沒鎖住（還沒核對通過），不用更正；請重新載入")


def _changes(
    before_fields: Mapping[str, object],
    before_columns: Mapping[str, object],
    values: Mapping[str, object],
    product: Mapping[str, object],
    changed: Sequence[str],
) -> list[tuple[str, object, object]]:
    """存檔紀錄的 [(欄位, 存檔前, 存檔後)]：畫面的欄，加上換算的代碼欄、上手與單位面額
    （畫面上對應的欄沒改時另外記，例：舊資料的代碼和現在的換算不同、幣別換了面額跟著換）。"""
    changes = [
        (name, before_fields.get(name), values.get(name))
        for name in FIELDS
        if not _same(before_fields.get(name), values.get(name))
    ]
    edited = {name for name, _, _ in changes}
    for column in changed:
        source = _DERIVED_COLUMNS.get(column)
        if source is not None and source not in edited:
            item = source if source not in FIELDS else column
            changes.append((item, before_columns.get(column), product[column]))
    return changes


# ---------- 人工維護（#115） ----------

# 打開時這檔的狀態（`OrderDraft.row.status`）：已鎖住的存檔照更正（ADR 0014）
MAINTAIN_LOCKED = LOCKED
MAINTAIN_OPEN = "未鎖住"
MAINTAIN_NEW = "只有交易明細"
MISSING_NOTE = "缺，可以先存、之後再補"  # 還沒鎖住的商品空著的必填欄：畫面標紅但不擋存檔
_ALREADY_CREATED = "打開之後資料庫已經有這檔的商品條件，請重新載入"


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

    缺資料以起單資料的必填欄（`order_terms.REQUIRED`）為準，ISIN、發行日這類由 TS 回填的不算；
    只有交易明細的，必填欄全部算缺。
    """
    _require_schema(conn)
    found = []
    with conn.transaction():
        for tdcc, *row in conn.execute(
            f"SELECT tdcc, {', '.join(_PRODUCT_COLUMNS)} FROM workflow.product WHERE locked_at IS NULL"
        ):
            columns = dict(zip(_PRODUCT_COLUMNS, row, strict=True))
            missing = tuple(missing_fields(columns))
            if missing:
                issuer = _issuer_code(tdcc, columns["issuer"], config)
                found.append(MissingProduct(tdcc, issuer, MAINTAIN_OPEN, _fields_of(columns), missing))
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
    _require_schema(conn)
    stored = _stored(conn, tdcc)
    if stored is not None and stored.locked:
        return load_correction(conn, tdcc, config)
    if stored is not None:
        fields = stored.fields
        row = OrderRow(tdcc, MAINTAIN_OPEN, _issuer_code(tdcc, stored.columns["issuer"], config), dict(fields))
        return OrderDraft(row, dict(fields), dict(fields), dict(fields), database_checked=True, correcting=True)
    if not _has_trade_rows(conn, tdcc):
        raise CorrectionError(f"{tdcc}：交易明細和商品條件都沒有這檔，不能在這裡新增")
    values: dict[str, object] = {**dict.fromkeys(FIELDS), "Product": FCN}  # 交易明細只有 FCN
    row = OrderRow(tdcc, MAINTAIN_NEW, config.issuer_prefixes.get(tdcc[:3], ""), dict(values))
    return OrderDraft(row, dict(values), dict(values), dict(values), database_checked=True, correcting=True)


def maintenance_problems(draft: OrderDraft, config: ImportConfig) -> dict[str, str]:
    """畫面標紅用：擋存檔的問題，加上還沒鎖住的商品空著的必填欄（`MISSING_NOTE`，不擋存檔）。

    已鎖住的照更正的規則（空白也擋）。上手的問題放在 TDCC；資料庫已有的商品上手不改，不擋。
    """
    if draft.row.status == MAINTAIN_LOCKED:
        problems = {**_value_problems(draft.values, draft, config), **draft.input_errors}
        problems.pop(TDCC_FIELD, None)
        return problems
    problems = _maintenance_blocking(draft, config)
    blank = [name for name in REQUIRED if draft.values.get(name) is None and name not in problems]
    return {**problems, **dict.fromkeys(blank, MISSING_NOTE)}


def _maintenance_blocking(draft: OrderDraft, config: ImportConfig) -> dict[str, str]:
    problems = {**_value_problems(draft.values, draft, config, allow_blank=True), **draft.input_errors}
    if draft.row.status == MAINTAIN_OPEN:
        problems.pop(TDCC_FIELD, None)  # 上手沿用資料庫的，不改
    return problems


def save_maintenance(
    conn: psycopg.Connection,
    draft: OrderDraft,
    config: ImportConfig,
    *,
    trades: TradeRowsDraft | None = None,
    reason: str = "",
    user: str | None = None,
) -> CorrectionReceipt:
    """人工維護存檔。已鎖住的照更正（`apply_correction`）；還沒鎖住的可以留空白（格式錯的擋），寫入後重算核對條件；
    只有交易明細的新建商品條件（來源「人工維護」）。商品條件每次在 `workflow.term_correction` 留一筆（`locked` 是
    false）。`trades` 是這檔的交易明細（#116）：改了的格子一起寫入，在 `workflow.trade_row_correction` 另外留一筆。
    沒改任何欄時不寫入。打開之後資料庫的這檔（商品條件或交易明細）被改過、鎖住、建立了商品條件或交易明細沒了，不寫入。
    整次在同一個 transaction 裡；寫入後 `draft`、`trades` 換成資料庫存的值（同重新打開）。
    """
    if draft.row.status == MAINTAIN_LOCKED:
        return apply_correction(conn, draft, config, trades=trades, reason=reason, user=user)
    _require_schema(conn)
    tdcc, values = draft.tdcc, draft.values
    with conn.transaction():
        conn.execute("SELECT 1 FROM workflow.product WHERE tdcc = %s FOR UPDATE", (tdcc,))
        stored = _stored(conn, tdcc)
        _require_unchanged(conn, draft, stored)
        _require_trades_unchanged(conn, trades)
        _raise_problems(f"{tdcc} 沒有存檔", _maintenance_blocking(draft, config), trades)
        product = _product_columns(values, draft.issuer, config)
        if stored is None:
            before_fields, before_columns = dict.fromkeys(FIELDS), dict.fromkeys(_PRODUCT_COLUMNS)
        else:
            before_fields, before_columns = stored.fields, stored.columns
            product["issuer"] = stored.columns["issuer"]  # 上手不改
            if _same(values.get("Currency"), stored.fields["Currency"]):
                product["denomination"] = stored.columns["denomination"]  # 幣別沒改時面額不動（可能是放行的 TS 值）
        changed = [c for c in _PRODUCT_COLUMNS if not _same(before_columns[c], product[c])]
        if stored is None and not _edits(draft.stored or {}, values):
            changed = []  # 新建時商品種類、上手一定有值，要看畫面有沒有改
        trade_changes = [] if trades is None else write_trade_rows(conn, trades)
        if not changed and not trade_changes:
            return CorrectionReceipt(tdcc, None, locked=False)
        if stored is None and changed:
            columns = ("tdcc", *_PRODUCT_COLUMNS, "status_event", "source")
            inserted = conn.execute(
                f"INSERT INTO workflow.product ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))})"
                " ON CONFLICT (tdcc) DO NOTHING RETURNING tdcc",
                (tdcc, *(product[c] for c in _PRODUCT_COLUMNS), STATUS_IN_STOCK, MANUAL_SOURCE),
            ).fetchone()
            if inserted is None:  # 別人同時新增了這檔：還不存在的列 FOR UPDATE 鎖不到
                raise CorrectionError(f"{tdcc}：{_ALREADY_CREATED}")
        elif changed:
            assignments = ", ".join(f"{c} = %s" for c in changed)
            conn.execute(
                f"UPDATE workflow.product SET {assignments} WHERE tdcc = %s", (*(product[c] for c in changed), tdcc)
            )
        exists = stored is not None or bool(changed)  # 只改了交易明細、還沒有商品條件的不重算
        calc = recalculate(conn, config.calendar, tdccs=[tdcc]).lines[0] if exists else None
        changes = _changes(before_fields, before_columns, values, product, changed) if changed else []
        user = user or getpass.getuser()
        correction_id = None
        if changed:
            previous = None if stored is None else stored.columns
            correction_id = _record_terms(conn, tdcc, changes, previous, locked=False, reason=reason, user=user)
        trade_id = None
        if trades is not None and trade_changes:
            trade_id = _record_trades(conn, trades, trade_changes, (), locked=False, reason=reason, user=user)
        saved = _saved_fields(conn, tdcc) if exists else None
        saved_trades = None if trades is None else read_rows(conn, tdcc)
    if saved is not None:
        _reopen(draft, saved)
        draft.row = replace(draft.row, status=MAINTAIN_OPEN)
    if trades is not None and saved_trades is not None:
        trades.reopen(saved_trades)
    return CorrectionReceipt(
        tdcc,
        correction_id,
        tuple(changes),
        calc,
        locked=False,
        created=stored is None and bool(changed),
        missing=tuple(missing_fields(product)) if exists else REQUIRED,
        **_trade_receipt(trades, trade_changes, trade_id),
    )


def _require_unchanged(conn: psycopg.Connection, draft: OrderDraft, stored: _Stored | None) -> None:
    """打開之後資料庫的這檔有沒有變：還沒鎖住的值改過、鎖住了；只有交易明細的建立了商品條件、交易明細沒了。"""
    tdcc = draft.tdcc
    if draft.row.status == MAINTAIN_NEW:
        if stored is not None:
            raise CorrectionError(f"{tdcc}：{_ALREADY_CREATED}")
        if not _has_trade_rows(conn, tdcc):
            raise CorrectionError(f"{tdcc}：交易明細已經沒有這檔，不能新增商品條件")
        return
    if stored is None:
        raise CorrectionError(f"{tdcc}：資料庫已經沒有這檔商品，請重新載入")
    if stored.locked:
        raise CorrectionError(f"{tdcc}：打開之後已經鎖住了（核對通過），請重新載入")
    if _changed_since_opened(stored, draft):
        raise CorrectionError(f"{tdcc}：資料庫裡這檔的條件在打開之後改過了，請重新載入")


def _has_trade_rows(conn: psycopg.Connection, tdcc: str) -> bool:
    return conn.execute("SELECT 1 FROM workflow.trade_row WHERE tdcc = %s LIMIT 1", (tdcc,)).fetchone() is not None


def _require_trades_unchanged(conn: psycopg.Connection, trades: TradeRowsDraft | None) -> None:
    """鎖住這檔的交易明細列；打開之後被改過（#116）就不寫入。"""
    if trades is not None and changed_since_opened(conn, trades):
        raise CorrectionError(f"{trades.tdcc}：資料庫裡這檔的交易明細在打開之後改過了，請重新載入")


def _raise_problems(prefix: str, problems: Mapping[str, str], trades: TradeRowsDraft | None) -> None:
    """商品條件或交易明細有擋存檔的問題時，兩邊一起寫出來、整次不寫入。"""
    found = [describe(problems)] if problems else []
    if trades is not None:
        found += [f"交易明細第 {seq} 列 {name}：{text}" for (seq, name), text in trade_problems(trades).items()]
    if found:
        raise CorrectionError(f"{prefix}：{'；'.join(found)}")


def _record_terms(
    conn: psycopg.Connection,
    tdcc: str,
    changes: Sequence[tuple[str, object, object]],
    previous: Mapping[str, object] | None,
    *,
    locked: bool,
    reason: str,
    user: str,
) -> int:
    """商品條件的紀錄（`workflow.term_correction`）：改了的欄與價格、存檔前的商品欄位（新建時空白）。"""
    return conn.execute(
        "INSERT INTO workflow.term_correction (tdcc, corrected_by, reason, changes, previous, locked)"
        " VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s) RETURNING correction_id",
        (tdcc, user, reason.strip() or None, _json([list(change) for change in changes]), _json(previous), locked),
    ).fetchone()[0]


def _record_trades(
    conn: psycopg.Connection,
    trades: TradeRowsDraft,
    changes: Sequence[TradeEdit],
    price_changes: Sequence[tuple[str, object, object]],
    *,
    locked: bool,
    reason: str,
    user: str,
) -> int:
    """交易明細的紀錄（`workflow.trade_row_correction`，#116）：改了的格子、存檔前的列；只改交易明細時重算後
    改變的價格也記在這裡。"""
    return conn.execute(
        "INSERT INTO workflow.trade_row_correction (tdcc, corrected_by, reason, changes, previous, price_changes, locked)"
        " VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s) RETURNING correction_id",
        (
            trades.tdcc,
            user,
            reason.strip() or None,
            _json([list(change) for change in changes]),
            _json(trades.previous()),
            _json([list(change) for change in price_changes] or None),
            locked,
        ),
    ).fetchone()[0]


def _trade_receipt(trades: TradeRowsDraft | None, changes: Sequence[TradeEdit], trade_id: int | None) -> dict[str, Any]:
    """收據裡交易明細的部分：紀錄、改了的格子、改了的 FC 名字裡對不到信箱的；沒改交易明細時都是空的。"""
    if trades is None or trade_id is None:
        return {}
    return {
        "trade_correction_id": trade_id,
        "trade_changes": tuple(changes),
        "unmatched": unmatched_names(trades.mailbox, changes),
    }


# ---------- 值的轉換 ----------


def _edits(original: Mapping[str, object], values: Mapping[str, object]) -> list[tuple[str, object, object]]:
    return [
        (name, original.get(name), values.get(name))
        for name in FIELDS
        if not _same(original.get(name), values.get(name))
    ]


def _same(a: object, b: object) -> bool:
    if isinstance(a, int | Decimal) and isinstance(b, int | Decimal) and not isinstance(a, bool):
        return Decimal(a) == Decimal(b)
    return a == b


def _pct(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value).quantize(_PCT)  # type: ignore[arg-type]
    except InvalidOperation as exc:
        raise ValueError(f"「{value}」不是數字") from exc


def _json(value: Mapping[str, object] | list[Any] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=_json_value)


def _json_value(value: object) -> object:
    if isinstance(value, Decimal):
        return format(plain(value), "f")
    if isinstance(value, dt.date):
        return value.isoformat()
    raise TypeError(type(value).__name__)
