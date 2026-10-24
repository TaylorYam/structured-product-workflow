"""商品條件（#203）：起單資料確認與匯入、已鎖住更正、人工維護的「草稿 → 檢查 → 寫入」都在這裡。

由以前的 `order_import`（讀取結果 → 草稿、匯入編排、更正與人工維護的進入點）與 `product_write`（#124，寫入規則）
合併而成。除了核對寫回（`check_source.write_back`，ADR 0013）之外，商品條件只由這裡寫入。

起單資料（#86、#97、ADR 0006、0008、0009、0010）：讀起單建檔（`order_terms.collect_orders`）之後，作業人員在視窗裡
逐檔確認、修改，再把通過檢查的商品寫進資料庫。
- `drafts_from`：讀取結果 → 可以編輯的 `OrderDraft`（欄位是 `product_record.FIELDS`）。
  資料庫已有、還沒鎖住的商品，起單建檔空白的欄帶入資料庫的值；兩邊不同的欄由 `OrderDraft.differences` 列出。
- `check_draft`：逐欄檢查，回傳 {欄位: 問題}；空的就可以匯入。
- `import_orders`：匯入（`import_drafts`）、重算核對條件、重讀資料庫的值，作業人員按一次「匯入資料庫」做的事。
  `import_drafts`：通過的寫進 `workflow.product`（每檔 `import_terms`），每次與每檔都留紀錄；整次在同一個 transaction 裡。
  `reload_stored` 在匯入後重讀資料庫的值與鎖住狀態（畫面上改到一半的值不動）。
- `load_correction`／`apply_correction`：已鎖住的商品更正條件（#55、ADR 0014）：維持鎖住、核對結果不動，
  有核對條件的重算價格，每次在 `workflow.term_correction` 留紀錄。
- `missing_products`／`open_maintenance`／`save_maintenance`：人工維護（#115、ADR 0017）：列出還沒鎖住、缺必填欄的商品
  與只有交易明細的 TDCC；打開任何一檔（已鎖住的照更正規則存檔），還沒鎖住的可以留空白，存檔後重算核對條件。
  這檔的交易明細（`trade_rows`，#116、ADR 0018）和商品條件一起存檔，在 `workflow.trade_row_correction` 另外留紀錄。
已鎖住（`locked_at` 有值：TS 核對通過，或 migration 0006 之前就在資料庫）的商品不能匯入，要改走更正（`apply_correction`）；
migration 0006 之後、清單更新停用（#87）之前由清單更新建立的商品沒有鎖，Order 匯入可以接手
（條件以 Order 為準，ISIN 與分公司配置保留）。承作金額以交易明細為準，Order 匯入不寫 `order_amount`（#97）。

寫入規則：每一檔照同樣的順序：鎖住這檔的商品條件列（與交易明細列）、確認打開之後沒變、依 `Mode` 檢查、組資料庫欄位
（上手不改、幣別沒改面額不動）、INSERT 或 UPDATE 改了的欄、重算核對條件、留紀錄、畫面換成資料庫存的值。
- `import_terms`：起單資料匯入一檔，在 `import_drafts` 整批的 transaction 裡；檢查沒過、已鎖住不寫入，回傳這檔的結果
  （`ImportOutcome`）。重算整批另外做（ADR 0012）。
- `save_terms`：已鎖住的更正與人工維護（#115、#116、ADR 0017、0018）存一檔，自己一個 transaction；不能存時丟
  `CorrectionError`，存了回傳 `CorrectionReceipt`。
- `lock_terms`／`read_stored`：讀這檔資料庫的條件與鎖住狀態；核對寫回也用 `lock_terms` 鎖列。
- `blocking_problems`：擋寫入的問題；畫面標紅（`check_draft`／`maintenance_problems`）也用同一套。
規則式、不猜值：單位面額依幣別預設值（審查標準 `[denomination]`），發行機構寫法依參考條件表 `[issuer_values]`。
畫面的欄 ↔ 資料庫的欄的轉換（KO／KI 代碼、UF 反轉、面額預設）在 `product_record`（#126）。
傳入的 `conn` 由 `database.connect` 建立，資料表版本已確認（#200），這裡不再檢查。
"""

from __future__ import annotations

import datetime as dt
import getpass
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any

from sp_workflow.check_terms import CalcLine, CalcReceipt, CheckTermsError, recalculate, recalculate_corrected
from sp_workflow.database import OperatorError
from sp_workflow.order_terms import TDCC_FIELD, OrderRow, unknown_issuer
from sp_workflow.product_record import (
    CHOICES,
    COLUMN_FIELDS,
    DERIVED_COLUMNS,
    FCN,
    FIELDS,
    KINDS,
    PRODUCT_COLUMNS,
    REQUIRED,
    STRIKE_FIELD,
    TRADE_FIELD,
    UNDERLYINGS,
    FieldKind,
    columns_of,
    fields_of,
    missing_fields,
    to_json,
)
from sp_workflow.status import OrderReview
from sp_workflow.trade_rows import (
    CHECK_TERM_FIELDS,
    TradeEdit,
    TradeRowsDraft,
    read_rows,
    trade_problems,
    unmatched_names,
    write_trade_rows,
)

if TYPE_CHECKING:
    import psycopg

    from fcn_checker.check_config import ConfigPaths
    from fcn_checker.trading_calendar import TradingCalendar

# workflow.product.source：商品資料由哪裡建立（#86）；清單更新已停用（#87），只剩以前建立的商品
INVENTORY_SOURCE = "清單更新"
ORDER_SOURCE = "Order"  # 「匯入起單資料」（#97 起從起單建檔讀）
MANUAL_SOURCE = "人工維護"  # 只有交易明細的 TDCC 在人工維護建立（#115）

# 匯入結果
NEW = "新增"
OVERWRITTEN = "覆蓋"
SAME = "沒有變"
SKIPPED = "不匯入"
LOCKED = OrderReview.LOCKED
IMPORTED = frozenset((NEW, OVERWRITTEN, SAME))

# 人工維護打開時這檔的狀態（`OrderDraft.row.status`）：已鎖住的存檔照更正（ADR 0014）
MAINTAIN_LOCKED = LOCKED
MAINTAIN_OPEN = OrderReview.UNLOCKED
MAINTAIN_NEW = OrderReview.TRADES_ONLY

FIXED_IN_CORRECTION = ("Issue date",)  # TS 回填的欄，不在更正範圍

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
_ALREADY_CREATED = "打開之後資料庫已經有這檔的商品條件，請重新載入"


class Mode(Enum):
    """這次寫入走哪一種規則。"""

    IMPORT = "起單資料匯入"  # 還沒鎖住的商品條件以起單資料為準；已鎖住的不寫
    CORRECT = "已鎖住更正"  # 維持鎖住；上手、發行日不改
    MAINTAIN = "人工維護未鎖住"  # 必填欄可以空白；上手不改
    CREATE = "人工維護新建"  # 只有交易明細的 TDCC 新建商品條件；必填欄可以空白

    @property
    def allow_blank(self) -> bool:
        return self in (Mode.MAINTAIN, Mode.CREATE)


def maintenance_mode(draft: OrderDraft) -> Mode:
    """人工維護打開的這檔存檔時走哪一種規則（依打開時的狀態）。"""
    return {MAINTAIN_LOCKED: Mode.CORRECT, MAINTAIN_NEW: Mode.CREATE}.get(draft.row.status, Mode.MAINTAIN)


class CorrectionError(OperatorError):
    """這檔不能更正或存檔（不在資料庫、鎖住狀態不對、檢查沒過、打開後被改過）；訊息是給作業人員看的中文。"""


@dataclass(frozen=True)
class OrderImportLine:
    tdcc: str
    result: str  # 新增／覆蓋／沒有變／不匯入／已鎖住
    detail: str = ""

    @property
    def imported(self) -> bool:
        return self.result in IMPORTED


@dataclass(frozen=True)
class ImportOutcome:
    """起單資料匯入一檔的結果，和 `workflow.order_import_line` 那一列要記的值。"""

    line: OrderImportLine
    confirmed: Mapping[str, object] | None = None  # 確認後的值（畫面的欄）
    previous: Mapping[str, object] | None = None  # 覆蓋前資料庫的欄位
    edits: Sequence[tuple[str, object, object]] = ()


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
    exited: str = ""  # 改了商品條件、這檔已出場時的存續事件（#169）：比價不會重算，事件不會跟著改

    @property
    def saved(self) -> bool:
        """有寫入（商品條件或交易明細）。"""
        return self.correction_id is not None or self.trade_correction_id is not None


# ---------- 資料庫裡的這檔 ----------


@dataclass(frozen=True)
class StoredTerms:
    """資料庫裡的一檔商品：原始欄位、畫面的欄、鎖住原因。"""

    columns: dict[str, object]
    fields: dict[str, object]
    source: str
    locked: str  # 已鎖住的原因；空字串＝還沒鎖住


def read_stored(conn: psycopg.Connection, tdcc: str) -> StoredTerms | None:
    """資料庫裡這檔目前的條件與鎖住狀態；沒有這檔時 None。"""
    return _select(conn, tdcc, lock=False)


def lock_terms(conn: psycopg.Connection, tdcc: str) -> StoredTerms | None:
    """鎖住這檔的商品條件列（`FOR UPDATE`）再讀；在呼叫端的 transaction 裡執行，鎖到它結束。

    寫入商品條件、核對寫回鎖住前都先經過這裡：兩邊同時處理同一檔時，後到的等先到的寫完，讀到的是寫完的狀態。
    """
    return _select(conn, tdcc, lock=True)


def _select(conn: psycopg.Connection, tdcc: str, *, lock: bool) -> StoredTerms | None:
    with conn.transaction():
        found = conn.execute(
            f"SELECT {', '.join(PRODUCT_COLUMNS)}, source, locked_at FROM workflow.product WHERE tdcc = %s"
            + (" FOR UPDATE" if lock else ""),
            (tdcc,),
        ).fetchone()
    if found is None:
        return None
    columns = dict(zip(PRODUCT_COLUMNS, found[: len(PRODUCT_COLUMNS)], strict=True))
    source, locked_at = found[len(PRODUCT_COLUMNS) :]
    if locked_at is None:
        locked = ""
    elif source == INVENTORY_SOURCE:
        locked = "以前清單更新匯入的商品，條件已鎖住，要改請到「7 人工維護」"
    else:
        locked = "已核對通過，條件已鎖住，要改請到「7 人工維護」"
    return StoredTerms(columns, fields_of(columns), source, locked)


def has_trade_rows(conn: psycopg.Connection, tdcc: str) -> bool:
    return conn.execute("SELECT 1 FROM workflow.trade_row WHERE tdcc = %s LIMIT 1", (tdcc,)).fetchone() is not None


def _guard(
    conn: psycopg.Connection, draft: OrderDraft, trades: TradeRowsDraft | None, mode: Mode
) -> tuple[StoredTerms | None, str]:
    """鎖住這檔的商品條件與交易明細列，回傳 (現況, 不能寫入的原因)；原因是空字串就可以寫。

    起單資料匯入：已鎖住的不寫；讀取時沒連資料庫的不覆蓋。更正與人工維護：鎖住狀態和打開時不同、
    打開之後商品條件或交易明細被改過、只有交易明細的已經建了商品條件或交易明細沒了，都不寫。
    """
    stored = lock_terms(conn, draft.tdcc)
    reason = _stale(conn, draft, stored, mode)
    if not reason and trades is not None:
        opened = {row.seq: row.stored for row in trades.rows}
        if read_rows(conn, trades.tdcc, lock=True) != opened:
            reason = "資料庫裡這檔的交易明細在打開之後改過了，請重新載入"
    return stored, reason


def not_correctable(stored: StoredTerms | None) -> str:
    """這檔不能更正的原因（打開與存檔時都看）；空字串＝已鎖住、可以更正。"""
    if stored is None:
        return "資料庫沒有這檔商品"
    return "" if stored.locked else "還沒鎖住（還沒核對通過），不用更正；請重新載入"


def _stale(conn: psycopg.Connection, draft: OrderDraft, stored: StoredTerms | None, mode: Mode) -> str:
    if mode is Mode.IMPORT:
        if stored is not None and stored.locked:
            return stored.locked
        if stored is not None and not draft.database_checked:
            return "讀取時沒有連上資料庫，看不到資料庫裡這檔的值；請重新讀取後再確認"
        return ""
    if mode is Mode.CREATE:
        if stored is not None:
            return _ALREADY_CREATED
        return "" if has_trade_rows(conn, draft.tdcc) else "交易明細已經沒有這檔，不能新增商品條件"
    if stored is None and mode is Mode.MAINTAIN:
        return "資料庫已經沒有這檔商品，請重新載入"
    if mode is Mode.CORRECT and (refused := not_correctable(stored)):
        return refused
    assert stored is not None
    if mode is Mode.MAINTAIN and stored.locked:
        return "打開之後已經鎖住了（核對通過），請重新載入"
    seen = draft.stored or {}
    if any(not same_value(stored.fields[name], seen.get(name)) for name in stored.fields):
        again = "再更正" if mode is Mode.CORRECT else ""
        return f"資料庫裡這檔的條件在打開之後改過了，請重新載入{again}"
    return ""


# ---------- 檢查 ----------


def blocking_problems(
    draft: OrderDraft, config: ImportConfig, mode: Mode, values: Mapping[str, object] | None = None
) -> dict[str, str]:
    """擋寫入的問題 {欄位: 問題}（整檔的問題放在 TDCC）；畫面上寫錯的欄也算。`values` 預設是畫面上的值。

    人工維護的必填欄可以空白；資料庫已有的商品上手不改，上手的問題不擋。
    """
    problems = _value_problems(draft.values if values is None else values, draft, config, allow_blank=mode.allow_blank)
    problems.update(draft.input_errors)
    if mode in (Mode.CORRECT, Mode.MAINTAIN):
        problems.pop(TDCC_FIELD, None)  # 只有上手的問題放在 TDCC
    return problems


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


def _raise_problems(prefix: str, problems: Mapping[str, str], trades: TradeRowsDraft | None) -> None:
    """商品條件或交易明細有擋存檔的問題時，兩邊一起寫出來、整次不寫入。"""
    found = [describe(problems)] if problems else []
    if trades is not None:
        found += [f"交易明細第 {seq} 列 {name}：{text}" for (seq, name), text in trade_problems(trades).items()]
    if found:
        raise CorrectionError(f"{prefix}：{'；'.join(found)}")


# ---------- 寫入 ----------


def import_terms(conn: psycopg.Connection, draft: OrderDraft, config: ImportConfig, import_id: int) -> ImportOutcome:
    """起單資料匯入一檔：通過檢查、還沒鎖住的寫進資料庫（條件以起單資料為準，ISIN 與分公司配置保留）。

    在呼叫端整批的 transaction 裡執行；`workflow.order_import_line` 由呼叫端寫。匯入成功時 `draft.values`
    補上匯入時保留的資料庫值（畫面跟資料庫一致）。承作金額以交易明細為準，不寫 `order_amount`（#97）。
    """
    tdcc = draft.tdcc
    stored, reason = _guard(conn, draft, None, Mode.IMPORT)  # 匯入當下重新看一次，不用畫面打開時的狀態
    previous = None if stored is None else stored.columns
    if reason:
        result = LOCKED if stored is not None and stored.locked else SKIPPED
        return ImportOutcome(OrderImportLine(tdcc, result, reason), previous=previous)
    values = _keep_database_values(draft, stored)
    edits = field_edits(draft.original, values)
    problems = blocking_problems(draft, config, Mode.IMPORT, values)
    if problems:
        return ImportOutcome(OrderImportLine(tdcc, SKIPPED, describe(problems)), values, previous, edits)
    product = _product_for(values, draft, stored, config, Mode.IMPORT)
    changed = _changed(draft, values, stored, product, Mode.IMPORT)
    if stored is not None and not changed and stored.source == ORDER_SOURCE:
        line = OrderImportLine(tdcc, SAME)
    elif not _write(conn, tdcc, product, changed, stored, {"source": ORDER_SOURCE, "order_import_id": import_id}):
        line = OrderImportLine(tdcc, SKIPPED, "讀取之後資料庫已經有這檔的商品條件，請重新讀取後再確認")
        return ImportOutcome(line, values, previous, edits)
    elif stored is None:
        line = OrderImportLine(tdcc, NEW)
    else:
        names = list(dict.fromkeys(COLUMN_FIELDS.get(c) or DERIVED_COLUMNS[c] for c in changed))
        detail = "改了：" + "、".join(names) if names else ""
        if stored.source != ORDER_SOURCE:
            detail = "；".join(filter(None, (f"{stored.source}建立的商品改由起單資料匯入接手", detail)))
        line = OrderImportLine(tdcc, OVERWRITTEN, detail)
    draft.values.update(values)
    return ImportOutcome(line, values, previous, edits)


def save_terms(
    conn: psycopg.Connection,
    draft: OrderDraft,
    config: ImportConfig,
    mode: Mode,
    *,
    trades: TradeRowsDraft | None = None,
    reason: str = "",
    user: str | None = None,
) -> CorrectionReceipt:
    """更正（`Mode.CORRECT`）或人工維護（`Mode.MAINTAIN`／`Mode.CREATE`）存一檔；整次在同一個 transaction 裡。

    更正：維持鎖住、核對結果不動，有核對條件的重算價格；上手、發行日不改。人工維護：必填欄可以空白（格式錯的擋），
    寫入後重算核對條件；只有交易明細的新建商品條件（來源「人工維護」）。單位面額只有幣別改了時換成新幣別的預設值。
    `trades` 是這檔的交易明細（#116）：改了的格子一起寫入，在 `workflow.trade_row_correction` 另外留一筆；更正時改到
    核對條件會用到的欄（分公司配置、交叉比對）條件沒改也重算。商品條件改了在 `workflow.term_correction` 留一筆。
    沒改任何欄時不寫入；打開之後這檔被改過時丟 `CorrectionError`、不寫入。寫入後 `draft`、`trades` 換成資料庫存的值
    （同重新打開）。
    """
    tdcc, values = draft.tdcc, draft.values
    locked = mode is Mode.CORRECT
    with conn.transaction():
        stored, refused = _guard(conn, draft, trades, mode)
        if refused:
            raise CorrectionError(f"{tdcc}：{refused}")
        problems = blocking_problems(draft, config, mode)
        if locked:
            assert stored is not None
            for name in FIXED_IN_CORRECTION:
                if not same_value(values.get(name), stored.fields.get(name)):
                    problems[name] = "TS 回填的欄位，不能更正"
        _raise_problems(f"{tdcc} {'沒有更正' if locked else '沒有存檔'}", problems, trades)
        product = _product_for(values, draft, stored, config, mode)
        changed = _changed(draft, values, stored, product, mode)
        trade_changes = [] if trades is None else write_trade_rows(conn, trades)
        if not changed and not trade_changes:
            return CorrectionReceipt(tdcc, None, locked=locked)
        source = {"source": MANUAL_SOURCE} if mode is Mode.CREATE else {}
        if changed and not _write(conn, tdcc, product, changed, stored, source):
            raise CorrectionError(f"{tdcc}：{_ALREADY_CREATED}")  # 別人同時新增了這檔：還不存在的列鎖不到
        exists = stored is not None or bool(changed)  # 只改了交易明細、還沒有商品條件的不重算
        calc, price_changes = None, ()
        if locked:
            assert stored is not None
            affected = any(name in CHECK_TERM_FIELDS for _, name, _, _ in trade_changes)
            recalc = recalculate_corrected(conn, config.calendar, tdcc, stored.columns, trades_changed=affected)
            calc, price_changes = (None, ()) if recalc is None else recalc
        elif exists:
            calc = recalculate(conn, config.calendar, tdccs=[tdcc]).lines[0]
        changes = (_changes(values, stored, product, changed) if changed else []) + list(price_changes)
        user = user or getpass.getuser()
        correction_id = None
        if changed:
            previous = None if stored is None else stored.columns
            correction_id = _record_terms(conn, tdcc, changes, previous, locked=locked, reason=reason, user=user)
        trade_id = None
        if trades is not None and trade_changes:
            prices = () if changed else price_changes  # 商品條件也改了時，價格記在商品條件的紀錄
            trade_id = _record_trades(conn, trades, trade_changes, prices, locked=locked, reason=reason, user=user)
        saved = read_stored(conn, tdcc) if exists else None
        saved_trades = None if trades is None else read_rows(conn, tdcc)
    if saved is not None:
        # 存檔後當成重新打開：畫面與打開時的值都換成資料庫存的值（百分比存成小數 6 位），
        # 同一檔接著改、再存時才不會誤判成打開之後有別人改過
        draft.values, draft.original, draft.stored = dict(saved.fields), dict(saved.fields), dict(saved.fields)
        if not locked:
            draft.row = replace(draft.row, status=MAINTAIN_OPEN)
    if trades is not None and saved_trades is not None:
        trades.reopen(saved_trades)
    return CorrectionReceipt(
        tdcc,
        correction_id,
        tuple(changes),
        calc,
        locked=locked,
        created=stored is None and bool(changed),
        missing=() if locked else tuple(missing_fields(product)) if exists else REQUIRED,
        **_trade_receipt(trades, trade_changes, trade_id),
    )


def _keep_database_values(draft: OrderDraft, stored: StoredTerms | None) -> dict[str, object]:
    """起單資料匯入：空白的欄不蓋掉資料庫的值，除非作業人員在畫面上看過那個值、刻意清空（打開時資料庫就有）。

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


def _product_for(
    values: Mapping[str, Any], draft: OrderDraft, stored: StoredTerms | None, config: ImportConfig, mode: Mode
) -> dict[str, object]:
    """畫面的值 → 要寫的資料庫商品欄位。資料庫已有的商品，更正與人工維護時上手不改、幣別沒改時面額不動
    （可能是放行的 TS 值）；更正時發行日不改。起單資料匯入以起單資料為準，上手與面額都照設定換算。"""
    product = columns_of(values, config.issuer_names.get(draft.issuer), config.denominations)
    if stored is None or mode is Mode.IMPORT:
        return product
    product["issuer"] = stored.columns["issuer"]
    if same_value(values.get("Currency"), stored.fields["Currency"]):
        product["denomination"] = stored.columns["denomination"]
    if mode is Mode.CORRECT:
        product["issue_date"] = stored.columns["issue_date"]
    return product


def _changed(
    draft: OrderDraft,
    values: Mapping[str, object],
    stored: StoredTerms | None,
    product: Mapping[str, object],
    mode: Mode,
) -> list[str]:
    """和資料庫不同、要寫的欄（資料庫沒有這檔時和全空白比）。"""
    before = dict.fromkeys(PRODUCT_COLUMNS) if stored is None else stored.columns
    changed = [c for c in PRODUCT_COLUMNS if not same_value(before[c], product[c])]
    if mode is Mode.CREATE and not field_edits(draft.stored or {}, values):
        return []  # 新建時商品種類、上手一定有值，要看畫面有沒有改
    return changed


def _write(
    conn: psycopg.Connection,
    tdcc: str,
    product: Mapping[str, object],
    changed: Sequence[str],
    stored: StoredTerms | None,
    extra: Mapping[str, object],
) -> bool:
    """資料庫沒有這檔時新增（全部欄位與 `extra`），有的只更新改了的欄與 `extra`；回傳有沒有寫入。

    新增時別人同時新增了這檔（還不存在的列 `FOR UPDATE` 鎖不到）不寫入、回傳 False。
    """
    if stored is None:
        columns = ("tdcc", *PRODUCT_COLUMNS, *extra)  # 存續狀態看存續事件（ADR 0019）
        inserted = conn.execute(
            f"INSERT INTO workflow.product ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))})"
            " ON CONFLICT (tdcc) DO NOTHING RETURNING tdcc",
            (tdcc, *(product[c] for c in PRODUCT_COLUMNS), *extra.values()),
        ).fetchone()
        return inserted is not None
    updates = {**{c: product[c] for c in changed}, **extra}
    if updates:
        assignments = ", ".join(f"{c} = %s" for c in updates)
        conn.execute(f"UPDATE workflow.product SET {assignments} WHERE tdcc = %s", (*updates.values(), tdcc))
    return True


# ---------- 紀錄 ----------


def _changes(
    values: Mapping[str, object],
    stored: StoredTerms | None,
    product: Mapping[str, object],
    changed: Sequence[str],
) -> list[tuple[str, object, object]]:
    """存檔紀錄的 [(欄位, 存檔前, 存檔後)]：畫面的欄，加上換算的代碼欄、上手與單位面額
    （畫面上對應的欄沒改時另外記，例：舊資料的代碼和現在的換算不同、幣別換了面額跟著換）。"""
    before_fields = dict.fromkeys(FIELDS) if stored is None else stored.fields
    before_columns = dict.fromkeys(PRODUCT_COLUMNS) if stored is None else stored.columns
    changes = field_edits(before_fields, values)
    edited = {name for name, _, _ in changes}
    for column in changed:
        source = DERIVED_COLUMNS.get(column)
        if source is not None and source not in edited:
            item = source if source not in FIELDS else column
            changes.append((item, before_columns.get(column), product[column]))
    return changes


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
        (tdcc, user, reason.strip() or None, to_json([list(change) for change in changes]), to_json(previous), locked),
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
            to_json([list(change) for change in changes]),
            to_json(trades.previous()),
            to_json([list(change) for change in price_changes] or None),
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


def field_edits(original: Mapping[str, object], values: Mapping[str, object]) -> list[tuple[str, object, object]]:
    """不同的畫面欄：[(欄位, 原本, 現在)]，依 `FIELDS` 的順序。"""
    return [
        (name, original.get(name), values.get(name))
        for name in FIELDS
        if not same_value(original.get(name), values.get(name))
    ]


def same_value(a: object, b: object) -> bool:
    """同一個值；數字不分 int 與 Decimal、不看小數位數。"""
    if isinstance(a, int | Decimal) and isinstance(b, int | Decimal) and not isinstance(a, bool):
        return Decimal(a) == Decimal(b)
    return a == b


# ---------- 起單資料確認與匯入（以前的 order_import） ----------

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
