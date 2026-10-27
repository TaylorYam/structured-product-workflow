"""匯出資料庫的商品條件（#122）：把 `workflow.product` 整張寫成一份 Excel，給作業人員留存或對照。
商品總表（#291，`export_summary`）是給人看的版本：欄位照新商品總表，見該函式說明。

只讀資料庫，不改任何資料。第 1 列是資料庫的欄名，最後多一欄「存續狀態」（最新一筆存續事件，#152），依 TDCC 排序；值照資料庫的型別寫入（日期、數字、文字），
有時區的時間換成本機時間（Excel 不收時區），Excel 不收的控制字元拿掉（`file_tools.save_rows`）。先寫到同一個資料夾的暫存檔，
寫好才換成選的檔名，寫不進去時丟 `file_tools.FileWriteError`、不留半個檔、原本的同名檔不動。
傳入的 `conn` 由 `database.connect` 建立，資料表版本已確認（#200），這裡不再檢查。
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import openpyxl
from openpyxl.cell.cell import Cell
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from sp_workflow.file_tools import MAX_WIDTH, excel_value, save_export, save_rows, table_book, text_cell
from sp_workflow.lifecycle import Lifecycle, lifecycle_status

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


def export_products(conn: psycopg.Connection, path: Path) -> ExportReceipt:
    with conn.transaction(), conn.cursor() as cur:  # 商品與存續事件讀同一個時間點
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
CJK_FONT, LATIN_FONT = "微軟正黑體", "Calibri"
_CJK = re.compile(r"[\u3000-\u303f\u3400-\u9fff\uff00-\uffef]")
_CENTER = Alignment(horizontal="center", vertical="center")
_PRODUCT_COLUMNS = (
    "tdcc, product, issuer, isin, denomination, currency, trade_date, issue_date, final_valuation_date,"
    " maturity_date, initial_pricing, ko_pct, ko_freq, ko_memory, strike_pct, ki_pct, ki_freq, upfront_pct,"
    " coupon_pct, tenor_months, non_call_months, underlying_1, underlying_2, underlying_3, underlying_4, underlying_5"
)
UL_FIELDS = ("進場價", "執行價", "下限價", "KO價")
SUMMARY_COLUMNS = (
    "存續狀態", "存續事件日", "事件標的",
    "Product", "發行機構", "TDCC Code", "ISIN Code", "單位面額", "承作幣別", "承作總額", "私銀註記", "新加坡註記",
    "交易日", "發行日", *(f"比價日_{i}" for i in range(1, 13)), "最終比價日", "到期日",
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
    """
    with conn.transaction():  # 商品、事件、比價日、價格、交易列讀同一個時間點
        products = conn.execute(f"SELECT {_PRODUCT_COLUMNS} FROM {TABLE} ORDER BY tdcc").fetchall()
        tdccs = [row[0] for row in products]
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
    rows = [
        _summary_row(row, statuses[row[0]], autocall.get(row[0], {}), prices.get(row[0], {}), trades.get(row[0], []))
        for row in products
    ]
    columns = (*SUMMARY_COLUMNS, *trade_columns(groups))
    save_export(_summary_book(columns, [[*row, *[None] * (len(columns) - len(row))] for row in rows]), path)
    return ExportReceipt(path, len(rows))


def _summary_row(
    product: tuple[object, ...],
    lifecycle: Lifecycle,
    autocall: dict[int, dt.date | None],
    prices: dict[int, tuple[object, ...]],
    trades: list[tuple[str, str, Decimal]],
) -> list[object]:
    (tdcc, name, issuer, isin, denomination, currency, trade_date, issue_date, final_date, maturity_date, pricing,
     ko, ko_freq, ko_memory, strike, ki, ki_freq, upfront, coupon, tenor, non_call, *codes) = product  # fmt: skip
    event = lifecycle.latest
    row = [
        lifecycle.status, event and event.event_date, event and event.underlying,
        name, issuer, tdcc, isin, denomination, currency, sum(a for _, _, a in trades) if trades else None, None, None,
        trade_date, issue_date, *(autocall.get(slot) for slot in range(1, 13)), final_date, maturity_date,
        pricing, ko, ko_freq, ko_memory, strike, ki, ki_freq, upfront, coupon, tenor, non_call, None,
    ]  # fmt: skip
    for seq, code in enumerate(codes, start=1):
        row += [code, *prices.get(seq, (None,) * len(UL_FIELDS))] if code else [None] * (1 + len(UL_FIELDS))
    for trade in trades:
        row += trade
    return row


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
        auto_filter=True,
    )
    sheet = book.active
    for cell in sheet[1]:
        _font_and_alignment(cell)
    sheet.freeze_panes = FROZEN
    return book


def _summary_cell(cell: Cell) -> None:
    text_cell(cell)
    if isinstance(cell.value, dt.date):
        cell.number_format = "yyyy/m/d"
    _font_and_alignment(cell)


def _font_and_alignment(cell: Cell) -> None:
    """全部置中；有中文字的格子用微軟正黑體，其他（英文、數字、日期、-）用 Calibri。表頭的粗體保留。"""
    cjk = isinstance(cell.value, str) and _CJK.search(cell.value)
    cell.font = Font(name=CJK_FONT if cjk else LATIN_FONT, bold=cell.font.b)
    cell.alignment = _CENTER


def _width(value: object) -> int:
    """欄寬：中文字算兩格，日期照 yyyy/mm/dd 的長度。"""
    if isinstance(value, dt.date):
        return 10
    text = str(value)
    return len(text) + len(_CJK.findall(text))
