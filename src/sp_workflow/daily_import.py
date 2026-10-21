"""每日匯入：把 `每日更新.xlsx` 匯入最小 FCN 資料庫（#32、ADR 0003）。

對外只有 `import_daily`。規則：
- `每日更新.xlsx` 的 `交易明細`、`清單更新` 第 1 列是欄名，必須和來源表（交易清單月份工作表、庫存總表）
  的欄名與順序完全相同；不同就整份不匯入。程式依欄名讀需要的欄位。
- 只收 Product 為 FCN 的列，其他略過並計數；略過的 TDCC 與 Product 記在 `workflow.other_product`。
- 以 TDCC 為單位、第一次寫入為準：資料庫已有的商品或交易不重複寫入，內容不同只列出差異、不覆蓋。
- 已存在商品的庫存狀態這一版不更新。
- 過渡期（#86、ADR 0009，#87 上線前）：由「匯入 Order 條件」建立的商品，清單更新只補空白的欄
  （分公司配置、ISIN、發行日），有值的不覆蓋、只列出差異；還沒有分公司配置時不逐筆對帳。
- 分公司對帳到每個分公司（交易明細各分公司金額加總 vs 庫存總表承作金額，PB 也算），差異只提醒。
- 信箱由資料庫直接維護（#35、ADR 0004）：匯入不修改 `mailbox`，只用它比對 FC 名字。
- 整次匯入在一個 transaction 內完成，任何錯誤都不留下部分寫入。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Any

import openpyxl
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from sp_workflow.database import DatabaseError, require_current_schema

if TYPE_CHECKING:
    import psycopg

TRADE_SHEET = "交易明細"
INVENTORY_SHEET = "清單更新"
TDCC_DIGITS = 12
FCN = "FCN"
PB = "PB"

TRADE_HEADERS = (
    "編號", "交易日期", "Product", "TDCC", "幣別", "金額", "FC/PB", "分公司", "前台主管/投專",
    "UF", "期初價格", "備註", "募集", "TS/IIS 核對", "核對TS/IIS人員", "起單人員",
)  # fmt: skip
_UNDERLYING_HEADERS = tuple(
    f"UL_{i}{suffix}" for i in range(1, 6) for suffix in ("", "_進場價", "_執行價", "_下限價", "_KO價", "_Memo")
)
INVENTORY_HEADERS = (
    "庫存狀態", "當日比價", "Product", "發行機構", "TDCC Code", "ISIN Code", "私銀註記", "單位面額", "承作幣別",
    *(name for i in range(1, 8) for name in (f"分公司_{i}", f"分公司_{i}_承作金額")),
    "交易日", "發行日", *(f"比價日_{i}" for i in range(1, 13)), "下一比價日", "最終比價日", "到期日", "評價日",
    "期初定價", "KO(%)", "KO(Freq)", "KO(memo)", "K(%)", "KI紀錄", "是否於當日觸及KI", "KI(%)", "KI(Freq)",
    "UF", "Coupon p.a. (%)", "天期(月)", "Non-Call(月)", "Lookback 期間",
    *_UNDERLYING_HEADERS, *(f"U{i}_LAST" for i in range(1, 6)),
)  # fmt: skip

KO_TYPES = {
    ("D", "Y"): "Daily Memory",
    ("D", "N"): "Daily",
    ("P", "N"): "Period End",
    ("P", "Y"): "Period End Memory",
}
BARRIER_TYPES = {"-": "NONE", "AM": "EKI", "D": "AKI", "P": "MKI"}
STATUS_IN_STOCK = "在庫"
ORDER_SOURCE = "Order"  # workflow.product.source：由「匯入 Order 條件」建立（#86）

# 結果的種類
NEW_PRODUCT = "新增商品"
NEW_TRADES = "新增交易"
UNCHANGED = "已存在"
DIFFERENT = "內容不同（未覆蓋）"
BLOCKED = "擋下"
RECONCILE = "分公司對帳差異"
INVENTORY_MISSING = "庫存尚未更新"
TRADES_MISSING = "交易尚未匯入"
FILLED = "補上空白"
NAME_UNMATCHED = "名字對不到信箱"
MAILBOX_EMPTY = "信箱尚未建立"
CANCELLED = "交易已取消"
IMPORT_ERROR_BOOK_NAME = "每日匯入錯誤.xlsx"
_SUCCESS_KINDS = frozenset((NEW_PRODUCT, NEW_TRADES, UNCHANGED, CANCELLED, FILLED))

_PCT = Decimal("0.000001")
_AMOUNT = Decimal("0.0001")
_STATUS_KO = re.compile(r"已於\s*(\d{4})/(\d{1,2})/(\d{1,2})\s*KO")
_STATUS_DELIVERED = re.compile(r"已於\s*(\d{4})/(\d{1,2})/(\d{1,2})\s*承接\s*(.+)")
_STATUS_DELIVERED_OLD = re.compile(r"接\s*(.+?)\s*股票")

_PRODUCT_COLUMNS = (
    "product", "issuer", "isin", "currency", "trade_date", "issue_date", "final_valuation_date", "maturity_date",
    "initial_pricing", "non_call_months", "underlying_1", "underlying_2", "underlying_3", "underlying_4",
    "underlying_5", "strike_pct", "ko_freq", "ko_memory", "ko_type", "ko_pct", "coupon_pct", "upfront_pct",
    "tenor_months", "ki_freq", "barrier_type", "ki_pct", "status_event", "status_date", "status_underlying",
    "status_raw",
)  # fmt: skip
_TRADE_COLUMNS = (
    "number", "trade_date", "product", "currency", "amount", "fc_name", "branch", "supervisor", "uf",
    "initial_pricing", "note", "raised", "ts_iis_checked", "checker", "originator",
)  # fmt: skip


class DailyImportError(Exception):
    """整份無法匯入（檔案或欄名有問題、資料表不是最新版本）；訊息是給作業人員看的中文。"""


@dataclass(frozen=True)
class ImportLine:
    tdcc: str
    kind: str
    detail: str = ""

    @property
    def problem(self) -> bool:
        return self.kind not in _SUCCESS_KINDS


@dataclass(frozen=True)
class ImportReceipt:
    run_id: int
    lines: tuple[ImportLine, ...]
    people: int
    skipped_trade_rows: int
    skipped_inventory_rows: int
    cancelled_trade_rows: int
    error_book_path: Path

    def count(self, kind: str) -> int:
        return sum(line.kind == kind for line in self.lines)


@dataclass(frozen=True)
class Person:
    name: str
    email: str
    unit: str | None
    fixed_cc: bool


@dataclass(frozen=True)
class Mailbox:
    people: tuple[Person, ...]
    aliases: Mapping[str, str]  # 別名（小寫）→ 使用者名稱

    def match(self, name: str) -> Person | None:
        """大小寫不同視為同一人；先查別名。不做模糊比對。"""
        key = _name_key(name)
        target = _name_key(self.aliases.get(key, name))
        return next((p for p in self.people if _name_key(p.name) == target), None)


@dataclass(frozen=True)
class _SheetRow:
    number: int
    values: dict[str, Any]


@dataclass
class _Parsed:
    products: dict[str, dict[str, Any]] = field(default_factory=dict)
    allocations: dict[str, list[tuple[str, Decimal]]] = field(default_factory=dict)
    trades: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    blocked: list[ImportLine] = field(default_factory=list)
    cancelled_lines: list[ImportLine] = field(default_factory=list)
    other_products: dict[str, str] = field(default_factory=dict)  # 略過的非 FCN：TDCC → Product
    skipped_trade_rows: int = 0
    skipped_inventory_rows: int = 0
    cancelled_trade_rows: int = 0


def import_daily(daily_book: Path, conn: psycopg.Connection) -> ImportReceipt:
    """匯入一份每日更新；回傳每檔 TDCC 的結果。整份有問題時丟 `DailyImportError`、不寫入任何資料。"""
    try:
        require_current_schema(conn)
    except DatabaseError as exc:
        raise DailyImportError(str(exc)) from exc
    parsed = _read_daily(daily_book)

    with conn.transaction():
        mailbox = load_mailbox(conn)
        run_id = conn.execute(
            "INSERT INTO workflow.import_run (daily_file_name, daily_sha256, summary) VALUES (%s, %s, '{}') RETURNING run_id",
            (daily_book.name, _sha256(daily_book)),
        ).fetchone()[0]
        lines = [*parsed.blocked, *parsed.cancelled_lines]
        lines += _write_products(conn, parsed, run_id)
        lines += _write_trades(conn, parsed, run_id)
        _write_other_products(conn, parsed, run_id)
        touched = sorted(set(parsed.products) | set(parsed.trades))
        lines += _reconcile(conn, touched)
        lines += _unmatched_names(parsed, mailbox)
        error_book_path = daily_book.parent / IMPORT_ERROR_BOOK_NAME
        receipt = ImportReceipt(
            run_id=run_id,
            lines=tuple(sorted(lines, key=lambda line: line.tdcc)),
            people=len(mailbox.people),
            skipped_trade_rows=parsed.skipped_trade_rows,
            skipped_inventory_rows=parsed.skipped_inventory_rows,
            cancelled_trade_rows=parsed.cancelled_trade_rows,
            error_book_path=error_book_path,
        )
        conn.execute(
            "UPDATE workflow.import_run SET summary = %s::jsonb WHERE run_id = %s",
            (json.dumps(_summary(receipt), ensure_ascii=False), run_id),
        )
        _write_error_book(conn, error_book_path)
    return receipt


@dataclass(frozen=True)
class ImportInfo:
    """一次每日匯入的紀錄（寄信前確認資料是不是最新的）。"""

    run_id: int
    imported_at: dt.datetime
    file_name: str
    sha256: str


def latest_import(conn: psycopg.Connection) -> ImportInfo | None:
    """最近一次每日匯入；還沒匯入過時回傳 None。"""
    with conn.transaction():
        row = conn.execute(
            "SELECT run_id, imported_at, daily_file_name, daily_sha256 FROM workflow.import_run"
            " ORDER BY run_id DESC LIMIT 1"
        ).fetchone()
    return None if row is None else ImportInfo(*row)


def is_pb(name: str) -> bool:
    """交易列的 FC/PB 是 PB（私銀），不寄信、但金額仍算進對帳。"""
    return name.strip().upper() == PB


# ---------- 信箱（資料庫直接維護，#35） ----------


def load_mailbox(conn: psycopg.Connection) -> Mailbox:
    """讀資料庫裡的人員與別名。"""
    people = tuple(
        Person(*row) for row in conn.execute("SELECT name, email, unit, fixed_cc FROM mailbox.person ORDER BY name")
    )
    aliases = dict(conn.execute("SELECT alias_key, person_name FROM mailbox.alias").fetchall())
    return Mailbox(people, aliases)


# ---------- 每日更新 ----------


def _read_daily(path: Path) -> _Parsed:
    wb = _open(path)
    try:
        for sheet in (TRADE_SHEET, INVENTORY_SHEET):
            if sheet not in wb.sheetnames:
                raise DailyImportError(f"每日更新缺少工作表「{sheet}」")
        trade_rows = _rows_with_header(wb[TRADE_SHEET], TRADE_HEADERS)
        inventory_rows = _rows_with_header(wb[INVENTORY_SHEET], INVENTORY_HEADERS)
    finally:
        wb.close()
    parsed = _Parsed()
    _parse_inventory(inventory_rows, parsed)
    _parse_trades(trade_rows, parsed)
    return parsed


def _rows_with_header(sheet: Any, expected: Sequence[str]) -> list[_SheetRow]:
    """檢查第 1 列欄名和順序完全相同（後面多出的空白欄不算），回傳 (列號, {欄名: 值})。"""
    rows = sheet.iter_rows(values_only=True)
    header = [_text(v) for v in next(rows, ())]
    while header and header[-1] is None:
        header.pop()
    if tuple(header) != tuple(expected):
        for i, want in enumerate(expected):
            got = header[i] if i < len(header) else None
            if got != want:
                where = openpyxl.utils.get_column_letter(i + 1)
                raise DailyImportError(
                    f"「{sheet.title}」的欄名和來源表不同，整份不匯入：{where} 欄應為「{want}」，實際是「{got or '空白'}」"
                )
        raise DailyImportError(f"「{sheet.title}」在「{expected[-1]}」之後還有其他欄，整份不匯入")
    width = len(expected)
    result = []
    for number, row in enumerate(rows, 2):
        values = (tuple(row) + (None,) * width)[:width]
        if any(_text(v) is not None for v in values):
            result.append(_SheetRow(number, dict(zip(expected, values, strict=True))))
    return result


def _parse_inventory(rows: Iterable[_SheetRow], parsed: _Parsed) -> None:
    seen: dict[str, tuple[dict[str, Any], list[tuple[str, Decimal]]]] = {}
    conflicted: set[str] = set()
    for sheet_row in rows:
        number, row = sheet_row.number, sheet_row.values
        if (_text(row["Product"]) or "").upper() != FCN:
            parsed.skipped_inventory_rows += 1
            _remember_other(parsed, row["TDCC Code"], row["Product"])
            continue
        where = f"清單更新第 {number} 列"
        try:
            tdcc = _tdcc(row["TDCC Code"])
            product, allocations = _product(row)
        except ValueError as exc:
            parsed.blocked.append(ImportLine(_tdcc_or_raw(row["TDCC Code"]), BLOCKED, f"{where}：{exc}"))
            continue
        if tdcc not in seen:
            seen[tdcc] = (product, allocations)
            continue
        stored_product, stored_allocations = seen[tdcc]
        if stored_product != product:
            conflicted.add(tdcc)
            continue
        amounts = dict(stored_allocations)
        for branch, amount in allocations:
            if branch in amounts and amounts[branch] != amount:
                conflicted.add(tdcc)
                break
            amounts[branch] = amount
        else:
            seen[tdcc] = (stored_product, list(amounts.items()))
    for tdcc, (product, allocations) in seen.items():
        if tdcc in conflicted:
            parsed.blocked.append(ImportLine(tdcc, BLOCKED, "清單更新同一檔出現多列且內容不同"))
        else:
            parsed.products[tdcc] = product
            parsed.allocations[tdcc] = allocations


def _product(row: Mapping[str, Any]) -> tuple[dict[str, Any], list[tuple[str, Decimal]]]:
    ko_freq, ko_memory = _required_text(row, "KO(Freq)").upper(), _required_text(row, "KO(memo)").upper()
    if (ko_freq, ko_memory) not in KO_TYPES:
        raise ValueError(f"KO(Freq)／KO(memo) 的代碼「{ko_freq}／{ko_memory}」不在對照表")
    ki_freq = (_text(row["KI(Freq)"], dash_is_none=False) or "").upper()
    if ki_freq not in BARRIER_TYPES:
        raise ValueError(f"KI(Freq) 的代碼「{ki_freq or '空白'}」不在對照表")
    ki_pct = _pct(row["KI(%)"])
    if ki_freq != "-" and ki_pct is None:
        raise ValueError("KI(%) 沒有值")
    event, status_date, underlying, raw = _status(row["庫存狀態"])
    product = {
        "product": FCN,
        "issuer": _required_text(row, "發行機構"),
        "isin": _required_text(row, "ISIN Code"),
        "currency": _required_text(row, "承作幣別"),
        "trade_date": _required_date(row, "交易日"),
        "issue_date": _required_date(row, "發行日"),
        "final_valuation_date": _required_date(row, "最終比價日"),
        "maturity_date": _required_date(row, "到期日"),
        "initial_pricing": _text(row["期初定價"]),
        "non_call_months": _required_int(row, "Non-Call(月)"),
        "underlying_1": _required_text(row, "UL_1"),
        **{f"underlying_{i}": _text(row[f"UL_{i}"]) for i in range(2, 6)},
        "strike_pct": _required_pct(row, "K(%)"),
        "ko_freq": ko_freq,
        "ko_memory": ko_memory,
        "ko_type": KO_TYPES[(ko_freq, ko_memory)],
        "ko_pct": _required_pct(row, "KO(%)"),
        "coupon_pct": _required_pct(row, "Coupon p.a. (%)"),
        "upfront_pct": _required_pct(row, "UF"),
        "tenor_months": _required_int(row, "天期(月)"),
        "ki_freq": ki_freq,
        "barrier_type": BARRIER_TYPES[ki_freq],
        "ki_pct": ki_pct,
        "status_event": event,
        "status_date": status_date,
        "status_underlying": underlying,
        "status_raw": raw,
    }
    allocations = []
    for i in range(1, 8):
        branch, amount = _text(row[f"分公司_{i}"]), _amount(row[f"分公司_{i}_承作金額"])
        if (branch is None) != (amount is None):
            raise ValueError(f"分公司_{i} 與承作金額只有一邊有值")
        if branch is not None:
            allocations.append((branch, amount))
    return product, allocations


def _status(value: Any) -> tuple[str, dt.date | None, str | None, str | None]:
    """庫存狀態拆成（事件, 日期, 接股標的, 原始文字）；空白是在庫。不認得的寫法丟 ValueError。"""
    raw = _text(value, dash_is_none=False)
    if raw is None:
        return STATUS_IN_STOCK, None, None, None
    if m := _STATUS_KO.fullmatch(raw):
        return "KO", _ymd(m), None, raw
    if raw == "到期還本":
        return "到期還本", None, None, raw
    if m := _STATUS_DELIVERED.fullmatch(raw):
        return "接股", _ymd(m), m.group(4).strip(), raw
    if m := _STATUS_DELIVERED_OLD.fullmatch(raw):
        return "接股", None, m.group(1).strip(), raw
    if raw == "顧客贖回":
        return "顧客贖回", None, None, raw
    raise ValueError(f"庫存狀態的寫法不認得：「{raw}」")


def _parse_trades(rows: Iterable[_SheetRow], parsed: _Parsed) -> None:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    errors: dict[str, list[str]] = defaultdict(list)
    for sheet_row in rows:
        number, row = sheet_row.number, sheet_row.values
        if (_text(row["Product"]) or "").upper() != FCN:
            parsed.skipped_trade_rows += 1
            _remember_other(parsed, row["TDCC"], row["Product"])
            continue
        where = f"交易明細第 {number} 列"
        if _is_cancel_note(row["備註"]):
            parsed.cancelled_trade_rows += 1
            parsed.cancelled_lines.append(
                ImportLine(
                    _tdcc_or_raw(row["TDCC"]),
                    CANCELLED,
                    f"{where}：備註為 Cancel，未匯入資料庫",
                )
            )
            continue
        try:
            tdcc = _tdcc(row["TDCC"])
        except ValueError as exc:
            parsed.blocked.append(ImportLine(_tdcc_or_raw(row["TDCC"]), BLOCKED, f"{where}：{exc}"))
            continue
        try:
            trade = _trade(row)
            groups[tdcc].append(trade)
        except ValueError as exc:
            errors[tdcc].append(f"{where}：{exc}")
    for tdcc, trades in groups.items():
        if errors[tdcc]:
            parsed.blocked.append(ImportLine(tdcc, BLOCKED, "；".join(errors[tdcc]) + "（這檔的交易列都不匯入）"))
        else:
            parsed.trades[tdcc] = trades
    for tdcc in errors.keys() - groups.keys():
        parsed.blocked.append(ImportLine(tdcc, BLOCKED, "；".join(errors[tdcc])))


def _trade(row: Mapping[str, Any]) -> dict[str, Any]:
    number = row["編號"]
    return {
        "number": None if _text(number) is None else _required_int(row, "編號"),
        "trade_date": _required_date(row, "交易日期"),
        "product": FCN,
        "currency": _required_text(row, "幣別"),
        "amount": _required(row, "金額", _amount),
        "fc_name": _required_text(row, "FC/PB"),
        "branch": _required_text(row, "分公司"),
        "supervisor": _text(row["前台主管/投專"]),
        "uf": _pct(row["UF"]),
        "initial_pricing": _text(row["期初價格"]),
        "note": _text(row["備註"]),
        "raised": _text(row["募集"]),
        "ts_iis_checked": _text(row["TS/IIS 核對"]),
        "checker": _text(row["核對TS/IIS人員"]),
        "originator": _text(row["起單人員"]),
    }


# ---------- 寫入資料庫 ----------


def _write_products(conn: psycopg.Connection, parsed: _Parsed, run_id: int) -> list[ImportLine]:
    lines = []
    for tdcc, product in parsed.products.items():
        allocations = parsed.allocations[tdcc]
        stored = _stored_product(conn, tdcc)
        if stored is None:
            columns = ("tdcc", *_PRODUCT_COLUMNS, "first_run_id")
            conn.execute(
                f"INSERT INTO workflow.product ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))})",
                (tdcc, *(product[c] for c in _PRODUCT_COLUMNS), run_id),
            )
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO workflow.branch_allocation (tdcc, seq, branch, amount) VALUES (%s, %s, %s, %s)",
                    [(tdcc, seq, branch, amount) for seq, (branch, amount) in enumerate(allocations, 1)],
                )
            lines.append(ImportLine(tdcc, NEW_PRODUCT))
            continue
        stored_product, stored_allocations, source = stored
        if source == ORDER_SOURCE:
            filled = _fill_blanks(conn, tdcc, product, allocations, stored_product, stored_allocations)
            if filled:
                lines.append(ImportLine(tdcc, FILLED, "Order 匯入的商品，清單更新補上：" + "、".join(filled)))
        # 已存在商品的庫存狀態這一版不更新，也不拿來比
        diffs = [c for c in _PRODUCT_COLUMNS if not c.startswith("status_") and (stored_product[c] != product[c])]
        if stored_allocations != allocations:
            diffs.append("分公司配置")
        if diffs:
            lines.append(ImportLine(tdcc, DIFFERENT, "商品條件和資料庫不同：" + "、".join(diffs)))
        else:
            lines.append(ImportLine(tdcc, UNCHANGED, "商品條件"))
    return lines


def _stored_product(
    conn: psycopg.Connection, tdcc: str
) -> tuple[dict[str, Any], list[tuple[str, Decimal]], str] | None:
    row = conn.execute(f"SELECT {', '.join(_PRODUCT_COLUMNS)}, source FROM workflow.product WHERE tdcc = %s", (tdcc,))
    found = row.fetchone()
    if found is None:
        return None
    allocations = conn.execute(
        "SELECT branch, amount FROM workflow.branch_allocation WHERE tdcc = %s ORDER BY seq", (tdcc,)
    ).fetchall()
    product = dict(zip(_PRODUCT_COLUMNS, found[:-1], strict=True))
    return product, [tuple(a) for a in allocations], found[-1]


def _fill_blanks(
    conn: psycopg.Connection,
    tdcc: str,
    product: Mapping[str, Any],
    allocations: Sequence[tuple[str, Decimal]],
    stored_product: dict[str, Any],
    stored_allocations: list[tuple[str, Decimal]],
) -> list[str]:
    """過渡期：Order 匯入的商品只補資料庫空白的 ISIN、發行日與分公司配置；回傳補了哪些，並更新 stored 供比較。"""
    filled = []
    for column, label in (("isin", "ISIN"), ("issue_date", "發行日")):
        if stored_product[column] is None and product[column] is not None:
            conn.execute(f"UPDATE workflow.product SET {column} = %s WHERE tdcc = %s", (product[column], tdcc))
            stored_product[column] = product[column]
            filled.append(label)
    if not stored_allocations and allocations:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO workflow.branch_allocation (tdcc, seq, branch, amount) VALUES (%s, %s, %s, %s)",
                [(tdcc, seq, branch, amount) for seq, (branch, amount) in enumerate(allocations, 1)],
            )
        stored_allocations[:] = list(allocations)
        filled.append("分公司配置")
    return filled


def _write_trades(conn: psycopg.Connection, parsed: _Parsed, run_id: int) -> list[ImportLine]:
    lines = []
    for tdcc, trades in parsed.trades.items():
        stored = conn.execute(
            f"SELECT {', '.join(_TRADE_COLUMNS)} FROM workflow.trade_row WHERE tdcc = %s ORDER BY row_seq", (tdcc,)
        ).fetchall()
        if not stored:
            columns = ("tdcc", "row_seq", *_TRADE_COLUMNS, "first_run_id")
            with conn.cursor() as cur:
                cur.executemany(
                    f"INSERT INTO workflow.trade_row ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))})",
                    [(tdcc, seq, *(t[c] for c in _TRADE_COLUMNS), run_id) for seq, t in enumerate(trades, 1)],
                )
            lines.append(ImportLine(tdcc, NEW_TRADES, f"{len(trades)} 列"))
        elif len(stored) != len(trades) or any(
            (s != t[c])
            for row, t in zip(stored, trades, strict=False)
            for s, c in zip(row, _TRADE_COLUMNS, strict=True)
        ):
            lines.append(
                ImportLine(tdcc, DIFFERENT, f"交易列和資料庫不同（資料庫 {len(stored)} 列、這次 {len(trades)} 列）")
            )
        else:
            lines.append(ImportLine(tdcc, UNCHANGED, "交易列"))
    return lines


def _remember_other(parsed: _Parsed, tdcc: Any, product: Any) -> None:
    try:
        parsed.other_products.setdefault(_tdcc(tdcc), _text(product) or "（空白）")
    except ValueError:
        pass  # TDCC 不完整的列只計數


def _write_other_products(conn: psycopg.Connection, parsed: _Parsed, run_id: int) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO workflow.other_product (tdcc, product, first_run_id) VALUES (%s, %s, %s)"
            " ON CONFLICT (tdcc) DO NOTHING",
            [(tdcc, product, run_id) for tdcc, product in parsed.other_products.items()],
        )


def _reconcile(conn: psycopg.Connection, tdccs: Sequence[str]) -> list[ImportLine]:
    """用資料庫裡的資料對帳：交易列各分公司金額加總 vs 庫存總表的分公司承作金額。"""
    lines = []
    for tdcc in tdccs:
        has_product = conn.execute("SELECT 1 FROM workflow.product WHERE tdcc = %s", (tdcc,)).fetchone() is not None
        traded = dict(
            conn.execute(
                "SELECT branch, sum(amount) FROM workflow.trade_row WHERE tdcc = %s GROUP BY branch", (tdcc,)
            ).fetchall()
        )
        if not has_product and traded:
            lines.append(ImportLine(tdcc, INVENTORY_MISSING, "交易已匯入，清單更新還沒有這檔，等庫存更新後再匯入"))
            continue
        if has_product and not traded:
            lines.append(ImportLine(tdcc, TRADES_MISSING, "商品條件已匯入，交易明細還沒有這檔"))
            continue
        if not has_product:
            continue
        allocated = dict(
            conn.execute(
                "SELECT branch, sum(amount) FROM workflow.branch_allocation WHERE tdcc = %s GROUP BY branch", (tdcc,)
            ).fetchall()
        )
        if not allocated and _source(conn, tdcc) == ORDER_SOURCE:
            lines.append(ImportLine(tdcc, INVENTORY_MISSING, "Order 條件已匯入，等清單更新補上分公司配置後再對帳"))
            continue
        for branch in sorted(traded.keys() | allocated.keys()):
            inv, trade = allocated.get(branch, Decimal(0)), traded.get(branch, Decimal(0))
            if inv != trade:
                lines.append(
                    ImportLine(
                        tdcc,
                        RECONCILE,
                        f"{branch}：庫存 {_money(inv)}、交易 {_money(trade)}、差額 {_money(trade - inv)}",
                    )
                )
    return lines


def _source(conn: psycopg.Connection, tdcc: str) -> str | None:
    found = conn.execute("SELECT source FROM workflow.product WHERE tdcc = %s", (tdcc,)).fetchone()
    return None if found is None else found[0]


def _unmatched_names(parsed: _Parsed, mailbox: Mailbox) -> list[ImportLine]:
    if not mailbox.people:
        return [ImportLine("", MAILBOX_EMPTY, "mailbox.person 沒有任何人員，FC 名字無法比對")] if parsed.trades else []
    lines = []
    for tdcc, trades in parsed.trades.items():
        names = sorted(
            {t["fc_name"] for t in trades if not is_pb(t["fc_name"]) and mailbox.match(t["fc_name"]) is None}
        )
        if names:
            lines.append(ImportLine(tdcc, NAME_UNMATCHED, "、".join(names)))
    return lines


def _summary(receipt: ImportReceipt) -> dict[str, Any]:
    counts: dict[str, int] = defaultdict(int)
    for line in receipt.lines:
        counts[line.kind] += 1
    return {
        "counts": dict(counts),
        "people": receipt.people,
        "skipped_trade_rows": receipt.skipped_trade_rows,
        "skipped_inventory_rows": receipt.skipped_inventory_rows,
        "lines": [[line.tdcc, line.kind, line.detail] for line in receipt.lines],
    }


def _write_error_book(conn: psycopg.Connection, path: Path) -> None:
    rows = []
    runs = conn.execute(
        "SELECT run_id, imported_at, daily_file_name, summary FROM workflow.import_run ORDER BY run_id"
    ).fetchall()
    for run_id, imported_at, file_name, summary in runs:
        for tdcc, kind, detail in summary.get("lines", []):
            if kind in _SUCCESS_KINDS:
                continue
            rows.append(
                (
                    imported_at.strftime("%Y-%m-%d %H:%M:%S%z"),
                    run_id,
                    file_name,
                    tdcc,
                    kind,
                    detail,
                )
            )

    temp = path.with_name(path.name + ".tmp")
    try:
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = "匯入錯誤"
        headers = ("匯入時間", "批次", "來源檔案", "TDCC", "錯誤類型", "說明")
        sheet.append(headers)
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        for row in rows:
            sheet.append(row)
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        widths = (22, 10, 30, 16, 24, 80)
        for index, width in enumerate(widths, 1):
            sheet.column_dimensions[get_column_letter(index)].width = width
        workbook.save(temp)
        temp.replace(path)
    except OSError as exc:
        temp.unlink(missing_ok=True)
        raise DailyImportError(f"錯誤清單寫入失敗：{path}（{exc}）") from exc


# ---------- 值的轉換 ----------


def _open(path: Path) -> Any:
    try:
        return openpyxl.load_workbook(path, read_only=True, data_only=True)
    except (OSError, ValueError, KeyError) as exc:
        raise DailyImportError(f"打不開 Excel：{path}（{exc}）") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _name_key(name: str) -> str:
    return name.strip().lower()


def _text(value: Any, *, dash_is_none: bool = True) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    if not text or (dash_is_none and text == "-"):
        return None
    return text


def _is_cancel_note(value: Any) -> bool:
    text = (_text(value, dash_is_none=False) or "").casefold()
    return text == "cancel" or "取消" in text


def _tdcc(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = _text(value)
    if text is None or not text.isdigit() or len(text) > TDCC_DIGITS:
        raise ValueError(f"TDCC「{text or '空白'}」不是 12 碼以內的數字")
    return text.zfill(TDCC_DIGITS)


def _tdcc_or_raw(value: Any) -> str:
    try:
        return _tdcc(value)
    except ValueError:
        return _text(value) or "（空白）"


def _decimal(value: Any) -> Decimal | None:
    if _text(value) is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"「{value}」不是數字")
    try:
        return Decimal(str(value).strip().replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"「{value}」不是數字") from exc


def _pct(value: Any) -> Decimal | None:
    number = _decimal(value)
    return None if number is None else number.quantize(_PCT)


def _amount(value: Any) -> Decimal | None:
    number = _decimal(value)
    return None if number is None else number.quantize(_AMOUNT)


def _date(value: Any) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = _text(value)
    if text is None:
        return None
    for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"「{text}」不是日期")


def _required(row: Mapping[str, Any], name: str, convert: Any) -> Any:
    try:
        value = convert(row[name])
    except ValueError as exc:
        raise ValueError(f"{name}：{exc}") from exc
    if value is None:
        raise ValueError(f"{name} 沒有值")
    return value


def _required_text(row: Mapping[str, Any], name: str) -> str:
    return _required(row, name, _text)


def _required_date(row: Mapping[str, Any], name: str) -> dt.date:
    return _required(row, name, _date)


def _required_pct(row: Mapping[str, Any], name: str) -> Decimal:
    return _required(row, name, _pct)


def _required_int(row: Mapping[str, Any], name: str) -> int:
    number = _required(row, name, _decimal)
    if number != number.to_integral_value():
        raise ValueError(f"{name}「{number}」不是整數")
    return int(number)


def _ymd(match: re.Match[str]) -> dt.date:
    try:
        return dt.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError as exc:
        raise ValueError(f"庫存狀態的日期不對：「{match.group(0)}」") from exc


def _money(value: Decimal) -> str:
    return f"{value.normalize():,f}"
