"""回填通用（#364）：ELN 通用回補格式的 Excel 以 TDCC 從資料庫回填 ISIN、比價日；期初定價 VWAP 的列另回填各標的價格。

ELN 通用回補格式的版面同核對結果檔「回填後」（沒有分公司欄）。表頭依參考條件表格式找（只看第一張工作表、`TDCC Code`
那一列，`fcn_checker.orders.reference.locate`），欄位依欄名對、不看欄位字母；缺回填要用的欄時整份不處理。
- 每列以 TDCC 讀資料庫（`product_terms.read_many`）：`ISIN Code` ← ISIN；`比價日_1`～`比價日_12` ← 比價日（不需要填的
  期別寫空值寫法）；`期初定價` 是 VWAP 的列，`UL_n` 四個價格欄 ← 資料庫同代號標的的價格（依代號配對、不看順序；
  資料庫空白的價格，例如 KI 型態 NONE 的下限價，寫空值寫法）。
- 每格沿用核對回填的決策（`fcn_checker.backfill`）：表上空白（含 `#VALUE!` 這類錯誤值）→ 回填；已有相同值 → 不動；
  已有不同值 → 保留原值、原因寫出來；VWAP 的價格欄已有不同值時以資料庫覆寫。日期沿用表上既有日期格的顯示格式。
- 資料庫找不到這檔（例如 BEN 這類還不支援的商品）、還沒有那一項（還沒核對寫回）或標的代號對不到 → 那幾格維持空白，
  原因寫出來。
- 原檔不動，寫到呼叫端選的新檔（`file_tools.save_export`，寫不進去時丟 `FileWriteError`）。只讀資料庫，不改資料。
傳入的 `conn` 由 `database.connect` 建立，資料表版本已確認（#200），這裡不再檢查。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import openpyxl
from openpyxl.worksheet.worksheet import Worksheet

from fcn_checker.backfill import SLOTS, date_format
from fcn_checker.ingestion import IngestionError
from fcn_checker.orders.reference import (
    UNDERLYING_SLOTS,
    autocall_date_key,
    header_columns,
    header_names,
    locate,
    normalize_cell,
    price_key,
    underlying_key,
)
from fcn_checker.rules.reference import PRICE_COLUMNS, VWAP
from sp_workflow.database import OperatorError
from sp_workflow.file_tools import save_export
from sp_workflow.product_terms import Stored, read_many

if TYPE_CHECKING:
    import psycopg

    from fcn_checker.config import ReferenceFormat

NOT_FOUND = "資料庫沒有這檔，維持空白"
_FILLED_FIELDS = ("isin", *(autocall_date_key(n) for n in range(1, SLOTS + 1)))
_VWAP_FIELDS = tuple(
    key
    for i in range(1, UNDERLYING_SLOTS + 1)
    for key in (underlying_key(i), *(price_key(i, c) for c, _ in PRICE_COLUMNS))
)
REQUIRED_FIELDS = ("product_code", *_FILLED_FIELDS, "initial_pricing", *_VWAP_FIELDS)  # 缺任何一欄整份不處理


class GenericBackfillError(OperatorError):
    """整份不處理（檔案讀不了、找不到表頭、缺欄）；訊息是給作業人員看的中文。"""


@dataclass(frozen=True)
class GenericLine:
    """表上一列的結果：回填（含 VWAP 覆寫）幾格，維持空白或不一致的原因。"""

    tdcc: str
    product: str
    filled: int
    reasons: tuple[str, ...]

    @property
    def found(self) -> bool:
        return NOT_FOUND not in self.reasons


@dataclass(frozen=True)
class GenericReceipt:
    path: Path
    lines: tuple[GenericLine, ...]

    @property
    def cells(self) -> int:
        return sum(line.filled for line in self.lines)

    @property
    def not_found(self) -> int:
        return sum(not line.found for line in self.lines)

    @property
    def with_reasons(self) -> int:
        """找得到、但有格子沒填或不一致的列。"""
        return sum(line.found and bool(line.reasons) for line in self.lines)


@dataclass(frozen=True)
class _Target:
    column: int
    expected: Any  # ISIN、日期、價格或空值寫法
    overwrite: bool  # VWAP 的價格欄：已有不同值時以資料庫覆寫


def backfill_generic(conn: psycopg.Connection, source: Path, target: Path, fmt: ReferenceFormat) -> GenericReceipt:
    """讀 `source`、以資料庫回填，寫到 `target`；回傳每列的結果（依表上順序）。"""
    try:
        book = openpyxl.load_workbook(source)
    except Exception as exc:
        raise GenericBackfillError(f"{source.name} 打不開（可能開在 Excel、已損毀或不是 Excel）：{exc}") from exc
    try:
        layout = locate(book, fmt)
    except IngestionError as exc:
        raise GenericBackfillError(f"{exc}，整份不處理") from exc
    sheet = book[layout.sheet]
    by_header = header_columns(sheet, layout)
    columns = _columns(sheet, by_header, fmt)
    headers = {cols[0]: name for name, cols in by_header.items()}
    key = columns["product_code"]
    rows = [
        (r, tdcc)
        for r in range(layout.first_data_row, sheet.max_row + 1)
        if (tdcc := _text(sheet.cell(row=r, column=key).value))
    ]
    stored = read_many(conn, sorted({tdcc for _, tdcc in rows}))
    dates = date_format(sheet, layout.first_data_row)
    lines = tuple(_fill_row(sheet, r, tdcc, columns, headers, stored.get(tdcc), fmt, dates) for r, tdcc in rows)
    save_export(book, target)
    return GenericReceipt(target, lines)


def _columns(sheet: Worksheet, by_header: Mapping[str, list[int]], fmt: ReferenceFormat) -> dict[str, int]:
    """標準欄位 → 欄號（同一標準欄位的欄名取表上先出現的）；缺回填要用的欄時丟 `GenericBackfillError`。"""
    found = {
        std: by_header[name][0]
        for std, names in header_names(fmt).items()
        for name in reversed(names)  # 先宣告的欄名蓋掉後宣告的
        if name in by_header
    }
    missing = [header_names(fmt)[std][0] for std in REQUIRED_FIELDS if std not in found]
    if missing:
        raise GenericBackfillError(f"工作表「{sheet.title}」缺少回填要用的欄：{'、'.join(missing)}，整份不處理")
    return found


def _fill_row(
    sheet: Worksheet,
    row: int,
    tdcc: str,
    columns: Mapping[str, int],
    headers: Mapping[int, str],
    found: Stored | None,
    fmt: ReferenceFormat,
    dates: str,
) -> GenericLine:
    def value(std: str) -> Any:
        return normalize_cell(sheet.cell(row=row, column=columns[std]).value) if std in columns else None

    product = _text(value("product"))
    if found is None:
        return GenericLine(tdcc, product, 0, (NOT_FOUND,))
    empty = fmt.empty_value
    targets: list[_Target] = []
    reasons: list[str] = []
    if found.isin:
        targets.append(_Target(columns["isin"], found.isin, False))
    else:
        reasons.append("資料庫還沒有 ISIN")
    if found.autocall is None:
        reasons.append("資料庫還沒有比價日")
    else:
        for n in range(1, SLOTS + 1):
            day = found.autocall.get(n)
            targets.append(_Target(columns[autocall_date_key(n)], empty if day is None else day, False))
    if fmt.initial_pricing_values.get(value("initial_pricing")) == VWAP:
        targets += _vwap_targets(value, columns, found, empty, reasons)
    filled = 0
    for t in targets:
        cell = sheet.cell(row=row, column=t.column)
        current = None if cell.data_type == "e" else normalize_cell(cell.value)  # 錯誤值（#VALUE!）算空白
        if current is not None and current == t.expected:
            continue
        if current is not None and not t.overwrite:
            reasons.append(f"{headers[t.column]} 表上是 {_shown(current)}、資料庫是 {_shown(t.expected)}，保留原值")
            continue
        cell.value = t.expected
        if isinstance(t.expected, dt.date):
            cell.number_format = dates
        filled += 1
    return GenericLine(tdcc, product, filled, tuple(reasons))


def _vwap_targets(
    value: Any, columns: Mapping[str, int], found: Stored, empty: str, reasons: list[str]
) -> list[_Target]:
    """期初定價 VWAP：表上每檔標的依代號對到資料庫的價格；對不到或資料庫還沒有 VWAP 價格的那一檔不填。"""
    prices = {price.code.strip().upper(): price for price in found.prices}
    targets = []
    for i in range(1, UNDERLYING_SLOTS + 1):
        code = value(underlying_key(i))
        if code is None or code == empty:
            continue
        price = prices.get(str(code).strip().upper())
        if price is None:
            reasons.append(f"UL_{i} {code} 資料庫這檔沒有這個標的")
        elif price.initial is None:
            reasons.append(f"UL_{i} {code} 資料庫還沒有 VWAP 價格")
        else:
            for col, _ in PRICE_COLUMNS:
                amount = getattr(price, col)
                targets.append(_Target(columns[price_key(i, col)], empty if amount is None else amount, True))
    return targets


def _text(value: Any) -> str:
    """格子 → 文字（TDCC、商品種類）；整數的浮點數不帶小數，空白是空字串。"""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return "" if value is None else str(value).strip()


def _shown(value: Any) -> str:
    """原因裡的值：日期寫 YYYY-MM-DD，數字不帶多餘的 0。"""
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return str(value)
