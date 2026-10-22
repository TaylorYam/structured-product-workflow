"""起單資料確認與視窗測試共用的虛構起單建檔與讀取結果。資料全部虛構。"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path

import openpyxl

from fcn_checker.config import load_exchange_holidays
from sp_workflow.order_import import MAINTAIN_OPEN, ImportConfig, OrderDraft
from sp_workflow.order_terms import NOT_FOUND, PENDING, READ, TDCC_FIELD, OrderRow

A = "029199990001"  # BARC
B = "037199990002"  # SG
SHEET = Path("起單建檔_20261015.xlsm")
PREFIXES = {"029": "BARC", "037": "SG", "092": "UBS", "147": "MS"}
CONFIG = ImportConfig(
    {"USD": Decimal(10000), "JPY": Decimal(1000000)},
    {"BARC": "Barclays", "SG": "SG"},
    PREFIXES,
    load_exchange_holidays(Path(__file__).resolve().parents[1] / "config" / "exchange_holidays.toml"),
)

# 起單建檔的一列（畫面的欄名 → 值）
VALUES: dict[str, object] = {
    "Product": "FCN",
    "Currency": "USD",
    "Guaranteed Periods (m)": 1,
    "BBG Code 1": "AAA UW",
    "BBG Code 2": "BBB UN",
    "BBG Code 3": None,
    "BBG Code 4": None,
    "BBG Code 5": None,
    "Strike (%)": Decimal(80),
    "KO Type": "Daily Memory",
    "KO Barrier (%)": Decimal(100),
    "Coupon p.a. (%)": Decimal("12.5"),
    "Upfront / NotePrice (%)": Decimal("98.5"),
    "Tenor (m)": 6,
    "Barrier Type": "EKI",
    "KI Barrier (%)": Decimal(65),
    "Trade date": dt.date(2026, 10, 15),
    "Strike date": dt.date(2026, 10, 15),
    "Issue date": dt.date(2026, 10, 22),
    "Final valuation date": dt.date(2027, 4, 15),
    "Maturity date": dt.date(2027, 4, 22),
    "期初價格": "收盤價",
}


def order_row(
    tdcc: str = A,
    changes: Mapping[str, object] | None = None,
    *,
    notes: Mapping[str, str] | None = None,
    conflicts: Sequence[str] = (),
) -> OrderRow:
    """讀取結果：`changes` 改欄位的值；上手依 `PREFIXES`。"""
    values = {**VALUES, **(changes or {})}
    notes = dict(notes or {})
    status = PENDING if set(notes) - {TDCC_FIELD} or conflicts else READ
    return OrderRow(tdcc, status, PREFIXES.get(tdcc[:3], ""), values, notes, frozenset(conflicts), SHEET, (3,))


def missing_row(tdcc: str) -> OrderRow:
    return OrderRow(tdcc, NOT_FOUND, PREFIXES.get(tdcc[:3], ""), notes={TDCC_FIELD: f"{SHEET.name} 裡沒有這檔"})


def maintenance_draft(status: str = MAINTAIN_OPEN, tdcc: str = A, issuer: str = "BARC", **changes) -> OrderDraft:
    """人工維護打開的一檔（`open_maintenance` 的結果，不連資料庫）：`changes` 改欄位的值。"""
    values = {**VALUES, **changes}
    row = OrderRow(tdcc, status, issuer, dict(values))
    return OrderDraft(row, dict(values), dict(values), dict(values), database_checked=True, correcting=True)


# 起單建檔的表頭（第 2 列、從 B 欄開始；欄名後面的空白照真的檔）
HEADERS = (
    "No.", "TDCC", "Product", "Currency", "Guaranteed Periods (m) ", "BBG Code 1", "BBG Code 2", "BBG Code 3",
    "BBG Code 4", "BBG Code 5", "Strike (%) ", "KO Type", "KO Barrier (%)", "Coupon p.a. (%) ", "UF (%) ",
    "Tenor (m)", "Barrier Type", "KI Barrier (%) ", "Observation Frequency (m)", "OTC", "Funding Spread (bps)",
    "Effective Date Offset(Calendar Days) ", "Trade date", "Issue date", "Final valuation date", "Maturity date",
    "期初價格", "起單FC",
)  # fmt: skip
_FIELD_OF = {"UF (%)": "Upfront / NotePrice (%)"}


def sheet_line(tdcc: str = A, changes: Mapping[str, object] | None = None) -> dict[str, object]:
    """起單建檔的一列（表頭 → 格子），數字是 Excel 讀出來的 int／float、日期是 datetime。"""
    values = {**VALUES, **(changes or {})}
    line: dict[str, object] = {"TDCC": tdcc, "Observation Frequency (m)": 1, "OTC": "Note", "起單FC": "Amy"}
    for header in HEADERS:
        name = _FIELD_OF.get(header.strip(), header.strip())
        if name in values:
            value = values[name]
            if isinstance(value, Decimal):
                value = float(value)
            elif isinstance(value, dt.date) and not isinstance(value, dt.datetime):
                value = dt.datetime.combine(value, dt.time())
            line[header.strip()] = value
    return line


def write_sheet(
    path: Path, lines: Sequence[Mapping[str, object]], *, headers: Sequence[str] = HEADERS, sheets: int = 1
) -> Path:
    """寫一份虛構的起單建檔：第 2 列表頭、從 B 欄開始，No. 欄是公式（同真的檔）。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"起單建檔_{path.stem[-8:]}"
    for i, header in enumerate(headers, 2):
        ws.cell(2, i, header)
    keys = [h.strip() for h in headers]
    for r, line in enumerate(lines, 3):
        ws.cell(r, 2, f'=IF(ISBLANK(C{r}),"",ROW()-2)')
        for key, value in line.items():
            if key in keys:
                ws.cell(r, 2 + keys.index(key), value)
    for n in range(1, sheets):
        wb.create_sheet(f"起單建檔_{n}")
    wb.save(path)
    return path
