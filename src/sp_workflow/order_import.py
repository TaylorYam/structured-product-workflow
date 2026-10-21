"""Order 條件確認與匯入（#86、ADR 0006、0008、0009）。

讀信（`order_terms.collect_orders`）之後，作業人員在視窗裡逐檔確認、修改，再把通過檢查的商品寫進資料庫，
同時匯出跟讀信清單同格式的 Excel 貼到現行的表。對外：
- `drafts_from`：讀信結果 → 可以編輯的 `OrderDraft`（欄位是格式表「Order原始資料」的欄加承作金額）。
  資料庫已有、還沒鎖住的商品，信裡空白的欄帶入資料庫的值；兩邊不同的欄由 `OrderDraft.differences` 列出。
- `parse_field`／`format_field`：畫面輸入的文字 ↔ 欄位的值。
- `check_draft`：逐欄檢查，回傳 {欄位: 問題}；空的就可以匯入。
- `import_drafts`：通過的寫進 `workflow.product`，每次與每檔都留紀錄；整次在同一個 transaction 裡。
  `reload_stored` 在匯入後重讀資料庫的值與鎖住狀態（畫面上改到一半的值不動）。
- `export_drafts`：輸出 Excel（Order原始資料＝確認後的值、Order條件清單／原文對照＝讀信結果、資料來源、人工修改）。
規則式、不猜值：單位面額依幣別預設值（審查標準 `[denomination]`），發行機構寫法依參考條件表 `[issuer_values]`。
已鎖住（`locked_at` 有值：TS 核對通過，或 migration 0006 之前就在資料庫）的商品不能匯入，要改走 #55 的更正；
migration 0006 之後、清單更新停用（#87）之前由清單更新建立的商品沒有鎖，Order 匯入可以接手
（條件以 Order 為準，ISIN 與分公司配置保留）。
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import getpass
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sp_workflow.daily_import import FCN
from sp_workflow.database import DatabaseError, require_current_schema
from sp_workflow.order_template import COLUMNS, FIELD_COLUMNS
from sp_workflow.order_terms import OrderRow, OrderTermsError, new_list_path, parse_date, write_list

if TYPE_CHECKING:
    import psycopg

    from fcn_checker.check_config import ConfigPaths

STATUS_IN_STOCK = "在庫"
# workflow.product.source：商品資料由哪裡建立（#86）；清單更新已停用（#87），只剩以前建立的商品
INVENTORY_SOURCE = "清單更新"
ORDER_SOURCE = "Order"  # 「匯入 Order 條件」

TDCC_FIELD = "TDCC"
AMOUNT_FIELD = "承作金額"
OFFSET_FIELD = "Effective Date Offset(Calendar Days)"
_HEADERS = tuple(c.header.strip() for c in COLUMNS)
_TEMPLATE_INDEX = {name: i for i, name in enumerate(_HEADERS)}
# 畫面上可以改的欄，依格式表順序；承作金額放在幣別後面。TDCC 不能改，發行日偏移由日期算。
EDIT_FIELDS: tuple[str, ...] = tuple(
    name
    for header in _HEADERS
    if header not in (TDCC_FIELD, OFFSET_FIELD)
    for name in ((header, AMOUNT_FIELD) if header == "Currency" else (header,))
)

# 匯入結果
NEW = "新增"
OVERWRITTEN = "覆蓋"
SAME = "沒有變"
SKIPPED = "不匯入"
LOCKED = "已鎖住"
IMPORTED = frozenset((NEW, OVERWRITTEN, SAME))
# 匯入前每檔的狀態（`review_status`）：擋匯入、讀信時有提醒但可以匯入、可以匯入；另有已鎖住（LOCKED）
BLOCKED, TO_CHECK, READY = "待確認", "請確認", "可匯入"
IMPORTABLE = frozenset((TO_CHECK, READY))

_PCT = Decimal("0.000001")
_AMOUNT = Decimal("0.0001")
_HUNDRED = Decimal(100)


class FieldKind(Enum):
    TEXT = "文字"
    CHOICE = "選項"
    PERCENT = "百分比"
    INT = "整數"
    NUMBER = "數字"
    DATE = "日期"


# KO Type → 資料庫的 (ko_freq, ko_memory)；Barrier Type → ki_freq（同清單更新的代碼）
KO_CODES = {
    "Daily": ("D", "N"),
    "Daily Memory": ("D", "Y"),
    "Period End": ("P", "N"),
    "Period End Memory": ("P", "Y"),
}
KI_CODES = {"NONE": "-", "EKI": "AM", "AKI": "D"}  # Order 格式表只有這三種（每期觀察 MKI 讀信時不會出現）
INITIAL_PRICINGS = ("開盤價", "收盤價", "VWAP")
CHOICES: dict[str, tuple[str, ...]] = {
    "KO Type": tuple(KO_CODES),
    "Barrier Type": tuple(KI_CODES),
    "期初價格": INITIAL_PRICINGS,
}
KINDS: dict[str, FieldKind] = {
    **{name: FieldKind.TEXT for name in EDIT_FIELDS},
    **{name: FieldKind.CHOICE for name in CHOICES},
    **{
        name: FieldKind.PERCENT
        for name in ("Strike (%)", "KO Barrier (%)", "Coupon p.a. (%)", "Upfront / NotePrice (%)", "KI Barrier (%)")
    },
    **{name: FieldKind.INT for name in ("Guaranteed Periods (m)", "Tenor (m)", "Observation Frequency (m)")},
    AMOUNT_FIELD: FieldKind.NUMBER,
    **{name: FieldKind.DATE for name in ("Trade date", "Issue date", "Final valuation date", "Maturity date")},
}
_UNDERLYINGS = tuple(f"BBG Code {n}" for n in range(1, 6))
_REQUIRED = (
    "Product",
    "Currency",
    "Guaranteed Periods (m)",
    "BBG Code 1",
    "Strike (%)",
    "KO Type",
    "KO Barrier (%)",
    "Coupon p.a. (%)",
    "Upfront / NotePrice (%)",
    "Tenor (m)",
    "Barrier Type",
    "Trade date",
    "Final valuation date",
    "Maturity date",
    "期初價格",
)
_TYPES: dict[FieldKind, tuple[type, ...]] = {
    FieldKind.TEXT: (str,),
    FieldKind.CHOICE: (str,),
    FieldKind.PERCENT: (Decimal, int),
    FieldKind.INT: (int,),
    FieldKind.NUMBER: (Decimal, int),
    FieldKind.DATE: (dt.date,),
}

# 資料庫商品欄位 ↔ 確認畫面的欄（畫面上的 Upfront / NotePrice = 100 − UF）
_PRODUCT_COLUMNS = (
    "product", "issuer", "currency", "trade_date", "issue_date", "final_valuation_date", "maturity_date",
    "initial_pricing", "non_call_months", "underlying_1", "underlying_2", "underlying_3", "underlying_4",
    "underlying_5", "strike_pct", "ko_freq", "ko_memory", "ko_type", "ko_pct", "coupon_pct", "upfront_pct",
    "tenor_months", "ki_freq", "barrier_type", "ki_pct", "denomination", "order_amount",
)  # fmt: skip
_COLUMN_FIELDS = {
    "product": "Product",
    "currency": "Currency",
    "order_amount": AMOUNT_FIELD,
    "non_call_months": "Guaranteed Periods (m)",
    **{f"underlying_{n}": f"BBG Code {n}" for n in range(1, 6)},
    "strike_pct": "Strike (%)",
    "ko_type": "KO Type",
    "ko_pct": "KO Barrier (%)",
    "coupon_pct": "Coupon p.a. (%)",
    "upfront_pct": "Upfront / NotePrice (%)",
    "tenor_months": "Tenor (m)",
    "barrier_type": "Barrier Type",
    "ki_pct": "KI Barrier (%)",
    "trade_date": "Trade date",
    "issue_date": "Issue date",
    "final_valuation_date": "Final valuation date",
    "maturity_date": "Maturity date",
    "initial_pricing": "期初價格",
}
# 由 KO Type／Barrier Type 換算的代碼欄、發行機構與面額：覆蓋時用哪個名稱說明
_DERIVED_COLUMNS = {
    "ko_freq": "KO Type",
    "ko_memory": "KO Type",
    "ki_freq": "Barrier Type",
    "issuer": "上手",
    "denomination": "單位面額",
}
# 讀信原因裡的欄名 → 畫面的欄（統一欄位與格式表欄名都認；長的先比，避免 KO(%) 吃掉 KO觀察方式）
_REASON_NAMES = sorted(
    {**FIELD_COLUMNS, AMOUNT_FIELD: AMOUNT_FIELD, "KO Memory": "KO Type", **{h: h for h in EDIT_FIELDS}}.items(),
    key=lambda item: -len(item[0]),
)
_TEMPLATE_MISSING = "格式表缺："


class OrderImportError(Exception):
    """整批無法匯入或匯出（資料表不是最新版本、設定檔有問題、輸出位置不對）；訊息是給作業人員看的中文。"""


@dataclass(frozen=True)
class ImportConfig:
    denominations: Mapping[str, Decimal]  # 幣別 → 單位面額預設值
    issuer_names: Mapping[str, str]  # 上手代號 → 發行機構的寫法（同清單更新）


def load_import_config(paths: ConfigPaths) -> ImportConfig:
    """從審查標準與參考條件表格式設定檔讀面額預設值與發行機構寫法。"""
    from fcn_checker.config import load_reference_format, load_review_standard
    from fcn_checker.ingestion import IngestionError

    try:
        review = load_review_standard(paths.review_standard)
        reference = load_reference_format(paths.reference_format)
    except IngestionError as exc:
        raise OrderImportError(str(exc)) from exc
    return ImportConfig(
        {currency: Decimal(amount) for currency, amount in review.denomination.items()},
        dict(reference.issuer_values),
    )


@dataclass
class OrderDraft:
    """一檔商品的確認資料。`values` 是畫面上目前的值（畫面直接改這份）；`mail` 是讀信得到的值。"""

    row: OrderRow
    mail: Mapping[str, object]
    values: dict[str, object]
    stored: Mapping[str, object] | None = None  # 打開時資料庫的值（同一套欄名）；資料庫沒有這檔時 None
    locked: str = ""  # 已鎖住的原因；空字串＝可以改
    database_checked: bool = False  # 打開時有沒有連資料庫看過（沒看過就不能覆蓋資料庫裡的這檔）
    input_errors: dict[str, str] = field(default_factory=dict)  # 畫面上寫錯、還沒解讀成值的欄 → 說明

    @property
    def tdcc(self) -> str:
        return self.row.tdcc

    @property
    def issuer(self) -> str:
        return self.row.issuer

    @property
    def flagged(self) -> frozenset[str]:
        """讀信時有問題、要標紅的欄；整列的問題標在 TDCC。"""
        return frozenset(_HEADERS[i] for i in self.row.flagged if i < len(_HEADERS))

    @property
    def too_many_underlyings(self) -> bool:
        return bool(self.row.values.get("標的6"))

    def reasons_by_field(self) -> dict[str, list[str]]:
        """讀信時的待確認原因，依畫面的欄分開；對不到某一欄的放在 TDCC（整列的問題）。"""
        found: dict[str, list[str]] = {}
        for reason in self.row.reasons:
            for name in _reason_fields(reason):
                found.setdefault(name, []).append(reason)
        return found

    def differences(self) -> dict[str, object]:
        """兩邊都有值但不同的欄 → 資料庫的值（畫面標黃）。"""
        if self.stored is None or self.locked:
            return {}
        return {
            name: stored
            for name, stored in self.stored.items()
            if stored is not None and self.values.get(name) is not None and not _same(self.values[name], stored)
        }

    def edits(self) -> list[tuple[str, object, object]]:
        """人工修改：(欄位, 信裡的值, 改成)。資料庫帶入的值也算（確認後的值和信不同的都列）。"""
        return _edits(self.mail, self.values)

    def template_values(self) -> tuple[object, ...]:
        """格式表一列（Order原始資料）：確認後的值，發行日偏移依日期重算。"""
        values = list(self.row.template)
        for name in EDIT_FIELDS:
            if name in _TEMPLATE_INDEX:
                values[_TEMPLATE_INDEX[name]] = self.values.get(name)
        trade, issue = self.values.get("Trade date"), self.values.get("Issue date")
        both_dates = isinstance(trade, dt.date) and isinstance(issue, dt.date)
        values[_TEMPLATE_INDEX[OFFSET_FIELD]] = (issue - trade).days if both_dates else None
        return tuple(values)


def _reason_fields(reason: str) -> list[str]:
    if reason.startswith(_TEMPLATE_MISSING):
        names = [n.strip() for n in reason.removeprefix(_TEMPLATE_MISSING).split("、")]
        return [n for n in names if n in KINDS] or [TDCC_FIELD]
    if reason.startswith("標的超過 5 檔"):
        return ["BBG Code 5"]
    text = reason.removeprefix("讀不到")
    for name, field_name in _REASON_NAMES:
        if text.startswith(name) and field_name in KINDS:
            return [field_name]
    return [TDCC_FIELD]


def drafts_from(rows: Iterable[OrderRow], conn: psycopg.Connection | None = None) -> list[OrderDraft]:
    """讀到 Order 的每一檔（有格式表列的）轉成確認資料；有資料庫時帶入資料庫的值與鎖住狀態。

    讀不到 Order 的列（找不到 Order、未支援上手、好幾封 Order）沒有確認資料，畫面直接顯示 `OrderRow` 的原因。
    資料表不是最新版本時丟 `DatabaseError`。
    """
    if conn is not None:
        require_current_schema(conn)
    drafts = []
    for row in rows:
        if not row.template:
            continue
        mail = {name: row.template[_TEMPLATE_INDEX[name]] for name in EDIT_FIELDS if name in _TEMPLATE_INDEX}
        mail[AMOUNT_FIELD] = row.values.get(AMOUNT_FIELD)
        stored = None if conn is None else _stored(conn, row.tdcc)
        fields = None if stored is None else stored.fields
        locked = "" if stored is None else stored.locked
        values = dict(mail)
        if fields is not None and not locked:
            for name, value in fields.items():
                if values.get(name) is None and value is not None:
                    values[name] = value
        drafts.append(OrderDraft(row, mail, values, fields, locked, database_checked=conn is not None))
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
        locked = "以前清單更新匯入的商品，條件已鎖住，要改請走更正商品條件（#55）"
    else:
        locked = "已核對通過，條件已鎖住，要改請走更正商品條件（#55）"
    return _Stored(columns, _fields_of(columns), source, locked)


def _fields_of(columns: Mapping[str, Any]) -> dict[str, object]:
    fields: dict[str, object] = {}
    for column, name in _COLUMN_FIELDS.items():
        value = columns[column]
        if column == "upfront_pct" and value is not None:
            value = _HUNDRED - value
        fields[name] = _plain(value) if isinstance(value, Decimal) else value
    return fields


# ---------- 欄位值 ----------


def parse_field(name: str, text: str) -> object:
    """畫面輸入的文字 → 欄位的值；空白回傳 None，不認得時丟 ValueError（中文說明）。"""
    kind = KINDS[name]
    value = text.strip()
    if not value:
        return None
    if kind is FieldKind.TEXT:
        return value
    if kind is FieldKind.CHOICE:
        for choice in CHOICES[name]:
            if choice.casefold() == value.casefold():
                return choice
        raise ValueError(f"要選 {'／'.join(CHOICES[name])}")
    if kind in (FieldKind.PERCENT, FieldKind.NUMBER):
        cleaned = value.replace(",", "").removesuffix("%").strip()
        if not re.fullmatch(r"-?[0-9]+(\.[0-9]+)?", cleaned):
            raise ValueError("不是數字")
        return _plain(Decimal(cleaned))
    if kind is FieldKind.INT:
        if not re.fullmatch(r"[0-9]+", value):
            raise ValueError("不是整數")
        return int(value)
    try:
        return parse_date(value)
    except ValueError:
        raise ValueError("日期要寫成 2026-10-21") from None


def format_field(value: object) -> str:
    """欄位的值 → 畫面顯示的文字。"""
    if value is None:
        return ""
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(_plain(value), "f")
    return str(value)


def check_draft(draft: OrderDraft, config: ImportConfig) -> dict[str, str]:
    """逐欄檢查確認後的值，回傳 {欄位: 問題}（整檔的問題放在 TDCC）；空的就可以匯入。畫面上寫錯的欄也算。"""
    if draft.locked:
        return {TDCC_FIELD: draft.locked}
    return {**_value_problems(draft.values, draft, config), **draft.input_errors}


def review_status(row: OrderRow, draft: OrderDraft | None, config: ImportConfig) -> tuple[str, str, bool]:
    """匯入前這檔的 (狀態, 原因, 是否要處理)：待確認、請確認、可匯入、已鎖住；沒讀到 Order 的照讀信狀態。"""
    if draft is None:
        return row.status, "；".join(row.reasons), True
    if draft.locked:
        return LOCKED, draft.locked, False
    problems = check_draft(draft, config)
    if problems:
        return BLOCKED, describe(problems), True
    if row.reasons:
        return TO_CHECK, "；".join(row.reasons), False
    return READY, "", False


def _value_problems(values: Mapping[str, object], draft: OrderDraft, config: ImportConfig) -> dict[str, str]:
    problems: dict[str, str] = {}
    if draft.issuer not in config.issuer_names:
        problems[TDCC_FIELD] = f"上手 {draft.issuer or '（不明）'} 在參考條件表設定沒有發行機構寫法"
    for name in EDIT_FIELDS:
        value = values.get(name)
        if value is None:
            if name in _REQUIRED:
                problems[name] = "必填"
            continue
        if not isinstance(value, _TYPES[KINDS[name]]) or isinstance(value, bool):
            problems[name] = f"要填{KINDS[name].value}"
        elif KINDS[name] is FieldKind.CHOICE and value not in CHOICES[name]:
            problems[name] = f"要選 {'／'.join(CHOICES[name])}"
    if draft.too_many_underlyings:
        problems["BBG Code 5"] = "標的超過 5 檔，核對只支援 5 檔"
    if "Product" not in problems and str(values["Product"]).upper() != FCN:
        problems["Product"] = "目前只做 FCN"
    if "Currency" not in problems and values["Currency"] not in config.denominations:
        problems["Currency"] = "這個幣別沒有單位面額預設值"
    for name in ("Guaranteed Periods (m)", "Tenor (m)"):
        if name not in problems and values[name] < 1:
            problems[name] = "至少 1"
    filled = [values.get(name) is not None for name in _UNDERLYINGS]
    if any(later and not earlier for earlier, later in zip(filled, filled[1:], strict=False)):
        gap = _UNDERLYINGS[filled.index(False)]
        problems.setdefault(gap, "標的要從 BBG Code 1 依序填，中間不能空")
    if "Barrier Type" not in problems and "KI Barrier (%)" not in problems:
        ki = values.get("KI Barrier (%)")
        if values["Barrier Type"] == "NONE" and ki is not None:
            problems["KI Barrier (%)"] = "Barrier Type 是 NONE 時要空白"
        elif values["Barrier Type"] != "NONE" and ki is None:
            problems["KI Barrier (%)"] = "有 KI 時必填"
    amount = values.get(AMOUNT_FIELD)
    if AMOUNT_FIELD not in problems and amount is not None and amount <= 0:
        problems[AMOUNT_FIELD] = "要大於 0"
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

    匯入成功的檔，`values` 會補上匯入時保留的資料庫值（畫面與 Excel 跟資料庫一致）。
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
                    draft.row.source.name if draft.row.source else None,
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
        reason = "讀信時沒有連上資料庫，看不到資料庫裡這檔的值；請重新讀取後再確認"
        return _Outcome(OrderImportLine(draft.tdcc, SKIPPED, reason), previous=stored.columns)
    values = _keep_database_values(draft, stored)
    edits = _edits(draft.mail, values)
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
    names = list(dict.fromkeys(_COLUMN_FIELDS.get(c) or _DERIVED_COLUMNS[c] for c in changed))
    detail = "改了：" + "、".join(names) if names else ""
    if stored.source != ORDER_SOURCE:
        detail = "；".join(filter(None, ("清單更新建立的商品改由 Order 匯入接手", detail)))
    return _Outcome(OrderImportLine(draft.tdcc, OVERWRITTEN, detail), values, stored.columns, edits)


def _keep_database_values(draft: OrderDraft, stored: _Stored | None) -> dict[str, object]:
    """空白的欄不蓋掉資料庫的值，除非作業人員在畫面上看過那個值、刻意清空（打開時資料庫就有）。"""
    values = dict(draft.values)
    if stored is None:
        return values
    seen = draft.stored or {}
    for name, value in stored.fields.items():
        if values.get(name) is None and value is not None and seen.get(name) is None:
            values[name] = value
    return values


def _product_columns(values: Mapping[str, Any], issuer: str, config: ImportConfig) -> dict[str, object]:
    ko_freq, ko_memory = KO_CODES[values["KO Type"]]
    barrier = values["Barrier Type"]
    return {
        "product": FCN,
        "issuer": config.issuer_names[issuer],
        "currency": values["Currency"],
        "trade_date": values["Trade date"],
        "issue_date": values.get("Issue date"),
        "final_valuation_date": values["Final valuation date"],
        "maturity_date": values["Maturity date"],
        "initial_pricing": values["期初價格"],
        "non_call_months": values["Guaranteed Periods (m)"],
        **{f"underlying_{n}": values.get(f"BBG Code {n}") for n in range(1, 6)},
        "strike_pct": _pct(values["Strike (%)"]),
        "ko_freq": ko_freq,
        "ko_memory": ko_memory,
        "ko_type": values["KO Type"],
        "ko_pct": _pct(values["KO Barrier (%)"]),
        "coupon_pct": _pct(values["Coupon p.a. (%)"]),
        "upfront_pct": _pct(_HUNDRED - Decimal(values["Upfront / NotePrice (%)"])),
        "tenor_months": values["Tenor (m)"],
        "ki_freq": KI_CODES[barrier],
        "barrier_type": barrier,
        "ki_pct": None if values.get("KI Barrier (%)") is None else _pct(values["KI Barrier (%)"]),
        "denomination": Decimal(config.denominations[values["Currency"]]).quantize(_AMOUNT),
        "order_amount": None if values.get(AMOUNT_FIELD) is None else Decimal(values[AMOUNT_FIELD]).quantize(_AMOUNT),
    }


# ---------- 匯出 ----------


def export_drafts(
    out_parent: Path,
    rows: Sequence[OrderRow],
    drafts: Sequence[OrderDraft],
    root: Path,
    unreadable: Sequence[Path],
    config: ImportConfig,
    *,
    receipt: OrderImportReceipt | None = None,
    now: dt.datetime | None = None,
) -> Path:
    """輸出 `Order條件清單_<時間>.xlsx`：Order原始資料放確認後的值（已匯入的不標底色、沒匯入的保留淺紅底），
    Order條件清單、原文對照照讀信結果，另加「人工修改」。`receipt` 是同一次的匯入結果（只匯出 Excel 時為 None）。
    """
    try:
        path = new_list_path(out_parent, now)
    except OrderTermsError as exc:
        raise OrderImportError(str(exc)) from exc
    by_tdcc = {draft.tdcc: draft for draft in drafts}
    out_rows = []
    for row in rows:
        draft = by_tdcc.get(row.tdcc)
        if draft is None or not row.template:
            out_rows.append(row)
            continue
        imported = receipt is not None and receipt.result_of(row.tdcc) in IMPORTED
        flagged = set() if imported else {*draft.flagged, *check_draft(draft, config)}
        out_rows.append(
            dataclasses.replace(
                row,
                template=draft.template_values(),
                flagged=frozenset(_TEMPLATE_INDEX[name] for name in flagged if name in _TEMPLATE_INDEX),
            )
        )
    edits = [(draft.tdcc, name, before, after) for draft in drafts for name, before, after in draft.edits()]
    try:
        write_list(path, out_rows, root, unreadable, edits=edits)
    except OSError as exc:
        raise OrderImportError(f"Excel 寫不進去：{path}（{exc}）") from exc
    return path


# ---------- 值的轉換 ----------


def _edits(mail: Mapping[str, object], values: Mapping[str, object]) -> list[tuple[str, object, object]]:
    return [
        (name, mail.get(name), values.get(name)) for name in EDIT_FIELDS if not _same(mail.get(name), values.get(name))
    ]


def _same(a: object, b: object) -> bool:
    if isinstance(a, int | Decimal) and isinstance(b, int | Decimal) and not isinstance(a, bool):
        return Decimal(a) == Decimal(b)
    return a == b


def _plain(value: Decimal) -> Decimal:
    """去掉多餘的 0，但不變成科學記號（例：80.000000 → 80、2.150000 → 2.15）。"""
    if value == value.to_integral_value():
        return value.quantize(Decimal(1))
    return value.normalize()


def _pct(value: object) -> Decimal:
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
        return format(_plain(value), "f")
    if isinstance(value, dt.date):
        return value.isoformat()
    raise TypeError(type(value).__name__)
