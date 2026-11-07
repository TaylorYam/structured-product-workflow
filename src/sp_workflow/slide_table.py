"""給 PPT 用的報表表格（#483）：盤勢速報（`market_brief`）與標的曝險分析（`exposure`）共用的欄規格、常數與寫表。

一欄的表頭、寬度、數字格式、表頭底色寫成一個 `Column`；呼叫端給欄規格與列，這裡寫成已排好版的工作表。版面兩種：
- 投影片（`slide_book`、`write_slide`，盤勢速報）：A1 標題、A3 起一塊連續的表格，深藍表頭白字（可用 \\n 換兩行）、
  細框線、全部置中、關格線、不合併儲存格；複製到 PPT 不用再調整（#467、#469）。字型（#473）：活頁簿預設
  （Normal 樣式）是微軟正黑體，格子用 Calibri 覆蓋；Calibri 沒有中文字，Excel 用微軟正黑體顯示中文——英文、數字
  是 Calibri，中文是微軟正黑體。這條規則只寫在這裡。
- 清單（`list_book`，曝險表的「全部商品」）：`file_tools.table_book` 的版面（第 1 列粗體表頭、凍結第一列、
  自動篩選），表頭照各欄的底色（#360）、全部置中、文字格存成文字。曝險表要不要改成投影片版面待使用者決定
  （路線圖 500 決策記錄 #483），改的話只要換用投影片版面。
報表各自的上色（跌破、合計、熱圖、昨收線）由呼叫端在寫好的格子上加。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from sp_workflow.file_tools import excel_value, table_book, text_cell

DEFAULT_FONT_NAME = "微軟正黑體"  # 活頁簿預設：顯示中文
FONT_NAME = "Calibri"  # 格子：英文、數字
CENTER = Alignment(horizontal="center", vertical="center")
BROKEN_FILL = PatternFill("solid", fgColor="FFC7CE")  # 跌破：淺紅（Excel 內建的淺紅色填滿）
HEADER_FILL = PatternFill("solid", fgColor="1F4E79")  # 投影片表頭深藍
THIN = Side(style="thin", color="BFBFBF")  # 投影片表格的細框線
HEADER_ALIGN = Alignment(horizontal="center", vertical="center", wrap_text=True)  # 表頭可用 \n 換兩行
ROW_HEIGHT = 20  # 點；20 列約 5.6 吋，放得進 16:9 投影片
TITLE_ROW_HEIGHT = 28
TOP = 3  # 投影片表格的表頭列


def font(*, size: int = 11, bold: bool = False, color: str | None = None) -> Font:
    """投影片格子的字型（Calibri）；報表自己的強調（例：下跌的深紅字）也從這裡拿，字型名只寫一次。
    沒指定的屬性不傳，寫出的字型跟 `Font(name=…, size=…)` 一樣。"""
    extra: dict[str, object] = {}
    if bold:
        extra["bold"] = True
    if color is not None:
        extra["color"] = color
    return Font(name=FONT_NAME, size=size, **extra)


HEADER_FONT = font(bold=True, color="FFFFFF")
BODY_FONT = font()
BOLD_FONT = font(bold=True)
TITLE_FONT = font(size=16, bold=True)


@dataclass(frozen=True)
class Column:
    """表格的一欄：表頭、欄寬、數字格式（文字格不套）、表頭底色（清單版面用；投影片表頭一律深藍）。"""

    header: str
    width: float
    number_format: str | None = None
    fill: PatternFill | None = None


# ---------------------------------------------------------------- 投影片


def slide_book() -> openpyxl.Workbook:
    """空的活頁簿（沒有工作表），預設字型改成微軟正黑體（openpyxl 沒有公開的設定方式，改 Normal 樣式與第 0 號字型）。"""
    book = openpyxl.Workbook()
    default = Font(name=DEFAULT_FONT_NAME, size=11)
    book._fonts[0] = default  # noqa: SLF001
    for style in book._named_styles:  # noqa: SLF001
        if style.name == "Normal":
            style.font = default
    book.remove(book.active)
    return book


def write_slide(sheet: Worksheet, title: str, columns: Sequence[Column], rows: Sequence[Sequence[object]]) -> int:
    """標題放 A1、表格從第 3 列開始（表頭＋資料），關格線、設欄寬、套字型框線置中；回傳資料第一列的列號。"""
    sheet.sheet_view.showGridLines = False
    sheet["A1"] = title
    sheet["A1"].font = TITLE_FONT
    sheet.row_dimensions[1].height = TITLE_ROW_HEIGHT
    for i, column in enumerate(columns, start=1):
        sheet.column_dimensions[get_column_letter(i)].width = column.width
    border = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
    for c, column in enumerate(columns, start=1):
        cell = sheet.cell(TOP, c, column.header)
        cell.fill, cell.font, cell.alignment, cell.border = HEADER_FILL, HEADER_FONT, HEADER_ALIGN, border
    lines = max(column.header.count("\n") + 1 for column in columns)
    sheet.row_dimensions[TOP].height = ROW_HEIGHT + 4 + 11 * (lines - 1)
    for r, values in enumerate(rows, start=TOP + 1):
        sheet.row_dimensions[r].height = ROW_HEIGHT
        for c, (value, column) in enumerate(zip(values, columns, strict=True), start=1):
            cell = sheet.cell(r, c, excel_value(value))
            cell.font, cell.alignment, cell.border = BODY_FONT, CENTER, border
            if isinstance(value, str):
                cell.data_type = "s"
            elif column.number_format and value is not None:
                cell.number_format = column.number_format
    return TOP + 1


# ---------------------------------------------------------------- 清單


def list_book(title: str, columns: Sequence[Column], rows: Iterable[Sequence[object]]) -> openpyxl.Workbook:
    """一張工作表（`title`）的清單：`table_book` 的版面加上各欄表頭底色、數字格式、全部置中、文字格存成文字。"""
    book = table_book(
        title,
        [c.header for c in columns],
        ([excel_value(value) for value in row] for row in rows),
        widths=[c.width for c in columns],
        auto_filter=True,
    )
    sheet = book.active
    for cell, column in zip(sheet[1], columns, strict=True):
        if column.fill is not None:
            cell.fill = column.fill
        cell.alignment = CENTER
    for row in sheet.iter_rows(min_row=2):
        for cell, column in zip(row, columns, strict=True):
            text_cell(cell)
            if column.number_format and cell.value is not None and not isinstance(cell.value, str):
                cell.number_format = column.number_format
            cell.alignment = CENTER
    return book
