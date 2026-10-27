"""匯出資料庫的商品條件（#122）：把 `workflow.product` 整張寫成一份 Excel，給作業人員留存或對照。
商品總表（#291，`export_summary`）是給人看的版本：欄位照新商品總表，見該函式說明；後面接在庫部位的分析分頁（#293，`summary_analysis`）。

只讀資料庫，不改任何資料。第 1 列是資料庫的欄名，最後多一欄「存續狀態」（最新一筆存續事件，#152），依 TDCC 排序；值照資料庫的型別寫入（日期、數字、文字），
有時區的時間換成本機時間（Excel 不收時區），Excel 不收的控制字元拿掉（`file_tools.save_rows`）。先寫到同一個資料夾的暫存檔，
寫好才換成選的檔名，寫不進去時丟 `file_tools.FileWriteError`、不留半個檔、原本的同名檔不動。
傳入的 `conn` 由 `database.connect` 建立，資料表版本已確認（#200），這裡不再檢查。
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import openpyxl
from openpyxl.cell.cell import Cell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from psycopg.rows import dict_row

from sp_workflow.file_tools import MAX_WIDTH, excel_value, save_export, save_rows, table_book, text_cell
from sp_workflow.lifecycle import Lifecycle, lifecycle_status
from sp_workflow.summary_analysis import add_analysis_sheets, enable_spill, summary_table

if TYPE_CHECKING:
    import psycopg

TABLE = "workflow.product"
LIFECYCLE_COLUMN = "存續狀態"


@dataclass(frozen=True)
class ExportReceipt:
    path: Path
    count: int  # 匯出幾檔


def export_name(now: dt.datetime | None = None) -> str:
    """存檔視窗預設的檔名。"""
    return f"workflow_product_{now or dt.datetime.now():%Y%m%d_%H%M%S}.xlsx"


@contextmanager
def _snapshot(conn: psycopg.Connection) -> Iterator[None]:
    """一個 REPEATABLE READ 的 transaction：裡面每個 SELECT 看同一個時間點（預設的 READ COMMITTED 每句各看各的）。"""
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        yield


def export_products(conn: psycopg.Connection, path: Path) -> ExportReceipt:
    with _snapshot(conn), conn.cursor() as cur:  # 商品與存續事件讀同一個時間點
        cur.execute(f"SELECT * FROM {TABLE} ORDER BY tdcc")
        columns = tuple(column.name for column in cur.description or ())
        rows = cur.fetchall()
        tdcc = columns.index("tdcc")
        statuses = lifecycle_status(conn, (row[tdcc] for row in rows))
    save_rows(path, TABLE, (*columns, LIFECYCLE_COLUMN), [(*row, statuses[row[tdcc]].status) for row in rows])
    return ExportReceipt(path, len(rows))


# ---------- 商品總表（#291） ----------

SUMMARY_TITLE = "商品總表"
DASH = "-"  # 資料庫沒有的值，照舊庫存總表寫 -
MIN_TRADE_GROUPS = 2  # 範本的分公司／FC／金額組數
CJK_FONT = "微軟正黑體"
LATIN_FONT = "Calibri"
HEADER_SIZE = 10  # 範本表頭的字級
HEADER_FILLS = (
    ("存續狀態", "F8CBAD"),  # 存續：橙
    ("Product", "C5E0B4"),  # 基本：綠
    ("交易日", "FFE699"),  # 日期：金
    ("期初定價", "BDD7EE"),  # 條件：藍
    ("UL_1", "DBDBDB"),  # 標的：灰
    ("分公司_1", "F8CBAD"),  # 承作（含多出來的組）：橙
)  # 表頭區塊的第一欄 → 底色：範本用 Office 佈景主題色「較淺 40%」，這裡寫成顏色值，不靠檔案的佈景主題
_CJK = re.compile(r"[\u3000-\u303f\u3400-\u9fff\uff00-\uffef]")
_CENTER = Alignment(horizontal="center", vertical="center")
PRODUCT_FIELDS = {
    "Product": "product", "發行機構": "issuer", "TDCC Code": "tdcc", "ISIN Code": "isin",
    "單位面額": "denomination", "承作幣別": "currency", "交易日": "trade_date", "期初定價日": "strike_date", "發行日": "issue_date",
    "最終比價日": "final_valuation_date", "到期日": "maturity_date", "期初定價": "initial_pricing",
    "KO(%)": "ko_pct", "KO(Freq)": "ko_freq", "KO(memo)": "ko_memory", "K(%)": "strike_pct", "KI(%)": "ki_pct",
    "KI(Freq)": "ki_freq", "UF": "upfront_pct", "Coupon p.a. (%)": "coupon_pct", "天期(月)": "tenor_months",
    "Non-Call(月)": "non_call_months",
}  # fmt: skip  # 總表欄名 → workflow.product 的欄，照欄名取值
UL_FIELDS = ("進場價", "執行價", "下限價", "KO價")
_UNDERLYINGS = tuple(f"underlying_{n}" for n in range(1, 6))
SUMMARY_COLUMNS = (
    "存續狀態", "事件發生日", "事件標的",
    "Product", "發行機構", "TDCC Code", "ISIN Code", "單位面額", "承作幣別", "承作總額", "私銀註記", "新加坡註記",
    "交易日", "期初定價日", "發行日", *(f"比價日_{i}" for i in range(1, 13)), "最終比價日", "到期日",
    "期初定價", "KO(%)", "KO(Freq)", "KO(memo)", "K(%)", "KI(%)", "KI(Freq)", "UF", "Coupon p.a. (%)",
    "天期(月)", "Non-Call(月)", "Lookback 期間",
    *(name for n in range(1, 6) for name in (f"UL_{n}", *(f"UL_{n}_{field}" for field in UL_FIELDS))),
)  # fmt: skip
FROZEN = f"{get_column_letter(SUMMARY_COLUMNS.index('TDCC Code') + 2)}2"  # 凍結表頭與 TDCC 以左的欄


def summary_name(now: dt.datetime | None = None) -> str:
    """商品總表存檔視窗預設的檔名。"""
    return f"商品總表_{now or dt.datetime.now():%Y%m%d_%H%M%S}.xlsx"


def trade_columns(groups: int) -> tuple[str, ...]:
    """交易明細的欄：一筆交易列一組分公司、FC、金額。"""
    return tuple(name for n in range(1, groups + 1) for name in (f"分公司_{n}", f"FC_{n}", f"金額_{n}"))


def export_summary(conn: psycopg.Connection, path: Path) -> ExportReceipt:
    """商品總表（#291）：所有商品一檔一列、依 TDCC 排序，欄位照新商品總表；只讀資料庫。

    存續是最新一筆還有效的事件（沒有是在庫）；承作總額是交易明細加總，交易列照順序往右排成分公司／FC／金額，
    組數是交易最多那一檔的筆數（至少 `MIN_TRADE_GROUPS`）。資料庫沒有的值（含私銀註記、新加坡註記、Lookback 期間）寫 `-`。
    總表是 Excel 表格，後面接用公式從總表即時計算的在庫部位分析分頁（#293）。
    """
    with _snapshot(conn), conn.cursor(row_factory=dict_row) as cur:  # 商品、事件、比價日、價格、交易列讀同一個時間點
        fields = ", ".join([*PRODUCT_FIELDS.values(), *_UNDERLYINGS])
        products = cur.execute(f"SELECT {fields} FROM {TABLE} ORDER BY tdcc").fetchall()
        tdccs = [product["tdcc"] for product in products]
        statuses = lifecycle_status(conn, tdccs)
        autocall: dict[str, dict[int, dt.date | None]] = {}
        for tdcc, slot, value in conn.execute("SELECT tdcc, slot, value FROM workflow.autocall_date"):
            autocall.setdefault(tdcc, {})[slot] = value
        prices: dict[str, dict[int, tuple[object, ...]]] = {}
        for tdcc, seq, *values in conn.execute(
            "SELECT tdcc, seq, initial_price, strike_price, ki_price, ko_price FROM workflow.underlying_price"
        ):
            prices.setdefault(tdcc, {})[seq] = tuple(values)
        trades: dict[str, list[tuple[str, str, Decimal]]] = {}
        for tdcc, branch, fc, amount in conn.execute(
            "SELECT tdcc, branch, fc_name, amount FROM workflow.trade_row ORDER BY tdcc, row_seq"
        ):
            trades.setdefault(tdcc, []).append((branch, fc, amount))
    groups = max([MIN_TRADE_GROUPS, *(len(trades.get(tdcc, ())) for tdcc in tdccs)])
    columns = (*SUMMARY_COLUMNS, *trade_columns(groups))
    rows = [
        _summary_cells(p, statuses[t], autocall.get(t, {}), prices.get(t, {}), trades.get(t, []))
        for p, t in zip(products, tdccs, strict=True)
    ]
    book = _summary_book(columns, [[cells.get(column) for column in columns] for cells in rows])
    add_analysis_sheets(book, groups)
    save_export(book, path, finish=enable_spill)
    return ExportReceipt(path, len(rows))


def _summary_cells(
    product: dict[str, object],
    lifecycle: Lifecycle,
    autocall: dict[int, dt.date | None],
    prices: dict[int, tuple[object, ...]],
    trades: list[tuple[str, str, Decimal]],
) -> dict[str, object]:
    """一檔的總表格子（總表欄名 → 值）；沒放的欄寫 `-`。"""
    event = lifecycle.latest
    cells: dict[str, object] = {name: product[field] for name, field in PRODUCT_FIELDS.items()}
    cells.update(
        {"存續狀態": lifecycle.status, "事件發生日": event and event.event_date, "事件標的": event and event.underlying}
    )
    cells.update({f"比價日_{slot}": value for slot, value in autocall.items()})
    for seq, field in enumerate(_UNDERLYINGS, start=1):
        if product[field]:
            cells[f"UL_{seq}"] = product[field]
            cells.update(zip((f"UL_{seq}_{name}" for name in UL_FIELDS), prices.get(seq, ()), strict=False))
    if trades:
        cells["承作總額"] = sum(amount for _, _, amount in trades)
    for n, (branch, fc, amount) in enumerate(trades, start=1):
        cells.update({f"分公司_{n}": branch, f"FC_{n}": fc, f"金額_{n}": amount})
    return cells


def _summary_book(columns: Sequence[str], rows: Sequence[Sequence[object]]) -> openpyxl.Workbook:
    values = [[DASH if value is None else excel_value(value) for value in row] for row in rows]
    widths = [_width(name) for name in columns]
    for row in values:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], _width(value))
    book = table_book(
        SUMMARY_TITLE,
        columns,
        values,
        widths=[min(width + 2, MAX_WIDTH) for width in widths],
        style=_summary_cell,
    )
    sheet = book.active
    starts = dict(HEADER_FILLS)
    fill = PatternFill()
    for cell in sheet[1]:  # 表頭照範本：依區塊上底色、10 號字
        if cell.value in starts:
            fill = PatternFill(fill_type="solid", fgColor=starts[cell.value])
        cell.fill = fill
        _font_and_alignment(cell, size=HEADER_SIZE)
    sheet.freeze_panes = FROZEN
    summary_table(sheet, len(columns), len(values))  # 表格自帶篩選按鈕，分析分頁的公式也參照它（#293）
    return book


def _summary_cell(cell: Cell) -> None:
    text_cell(cell)
    if isinstance(cell.value, dt.date):
        cell.number_format = "yyyy/m/d"
    _font_and_alignment(cell)


def _font_and_alignment(cell: Cell, size: float | None = None) -> None:
    """全部置中；有中文字的格子用微軟正黑體，其他（英文、數字、日期、-）用 Calibri。表頭的粗體保留。"""
    cjk = isinstance(cell.value, str) and _CJK.search(cell.value)
    cell.font = Font(name=CJK_FONT if cjk else LATIN_FONT, bold=cell.font.b, size=size)
    cell.alignment = _CENTER


def _width(value: object) -> int:
    """欄寬：中文字算兩格，日期照 yyyy/mm/dd 的長度。"""
    if isinstance(value, dt.date):
        return 10
    text = str(value)
    return len(text) + len(_CJK.findall(text))
