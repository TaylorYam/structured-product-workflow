"""商品總表的分析分頁（#293）：在庫部位 by 發行機構、分公司、幣別，全部用 Excel 公式從總表即時計算。

「商品總表」分頁是名為 `總表` 的 Excel 表格（Table），公式用結構化參照；在 Excel 裡改總表、或在表格下方新增一列，
分析分頁跟著重算。清單（發行機構、分公司、幣別）與金額是動態陣列公式（`UNIQUE`、`FILTER`、`SORT`…），
新的發行機構、分公司、幣別自動多一列／一欄；需要 Excel 2021 或 Microsoft 365。

每個分頁：第 4 列表頭（A 欄名稱、B 欄起幣別依在庫檔數由多到少），第 5 列總計，第 6 列起每個名稱一列。
動態陣列的長度事先不知道，總計放在表頭下面，格式先套在一塊夠大的範圍，表頭底色與框線用條件式格式只在有值的格子出現。
金額是原幣、不換算；只算存續狀態「在庫」的列，`-` 與空白的名稱不列。分公司照交易明細的 `分公司_n`／`金額_n`，
一檔由幾家分公司承作就分到那幾家。

openpyxl 不支援寫動態陣列：公式以陣列公式寫入，存檔後由 `enable_spill` 在檔案裡補 Excel 的動態陣列中繼資料
（`xl/metadata.xml`、每格 `cm="1"`），否則 Excel 當成舊式陣列公式、不會溢出。檔案裡的公式要用 Excel 的存檔寫法：
新函式加 `_xlfn.` 前綴、`LET` 的變數加 `_xlpm.`、溢出參照 `A6#` 寫成 `_xlfn.ANCHORARRAY(A6)`（`file_formula`）。
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

import openpyxl
from openpyxl.formatting.rule import Rule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.styles.differential import DifferentialStyle
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.formula import ArrayFormula
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.worksheet.worksheet import Worksheet

TABLE_NAME = "總表"
IN_STOCK = "在庫"
ISSUER_SHEET, ISSUER_SHARE_SHEET = "發行機構x幣別", "發行機構占比"
BRANCH_SHEET, BRANCH_SHARE_SHEET = "分公司x幣別", "分公司占比"
HEADER_ROW, TOTAL_ROW, FIRST_ROW = 4, 5, 6
FORMAT_ROWS, FORMAT_COLUMNS = 100, 20  # 先套格式的範圍：名稱列數、幣別欄數
AMOUNT_FORMAT = '#,##0;-#,##0;"-"'  # 0 顯示 -
SHARE_FORMAT = '0.0%;-0.0%;"-"'
NOTES = (
    "依「商品總表」分頁即時計算，只算存續狀態「在庫」；金額是原幣，不同幣別不能相加。",
    "總表改了（含在表格下方新增列）會自動更新；需要 Excel 2021 或 Microsoft 365。",
)
CJK_FONT, LATIN_FONT = "微軟正黑體", "Calibri"
HEAD_COLOR = "1F4E79"
NOTE_COLOR = "595959"

_NEW_FUNCTIONS = {"SORTBY": "_xlfn.", "SORT": "_xlfn._xlws.", "FILTER": "_xlfn._xlws.", "UNIQUE": "_xlfn.",
                  "SEQUENCE": "_xlfn.", "LET": "_xlfn."}  # fmt: skip
_SPILL = re.compile(r"((?:'[^']+'!)?\$?[A-Z]+\$?\d+)#")
_THIN = Side(style="thin", color="BFBFBF")


def summary_table(sheet: Worksheet, columns: int, rows: int) -> None:
    """把總表的範圍設成 Excel 表格（`TABLE_NAME`，有篩選按鈕）；沒有商品時多包一列空白，Excel 表格至少要有一列。"""
    ref = f"A1:{get_column_letter(columns)}{max(rows, 1) + 1}"
    style = TableStyleInfo(name="TableStyleLight1", showRowStripes=False)
    sheet.add_table(Table(displayName=TABLE_NAME, ref=ref, tableStyleInfo=style))


def add_analysis_sheets(book: openpyxl.Workbook, groups: int) -> None:
    """總表後面接四個分析分頁；`groups` 是總表的分公司／FC／金額組數。"""
    currencies = (
        f'LET(c,UNIQUE(FILTER({_col("承作幣別")},{_in_stock()}*({_col("承作幣別")}<>"-"),"")),'
        f'TRANSPOSE(SORTBY(c,COUNTIFS({_col("承作幣別")},c,{_col("存續狀態")},"{IN_STOCK}"),-1)))'
    )
    by = f'{_col("承作幣別")},B{HEADER_ROW}#,{_col("存續狀態")},"{IN_STOCK}"'
    issuers = _sheet(
        book, ISSUER_SHEET, "發行機構", "在庫部位：發行機構 × 幣別（原幣，未換算）", LATIN_FONT, AMOUNT_FORMAT
    )
    _dynamic(issuers, f"B{HEADER_ROW}", currencies, ("c",))
    _dynamic(issuers, f"B{TOTAL_ROW}", f"SUMIFS({_col('承作總額')},{by})")
    _dynamic(
        issuers,
        f"A{FIRST_ROW}",
        f'SORT(UNIQUE(FILTER({_col("發行機構")},{_in_stock()}*({_col("發行機構")}<>"-"),"")))',
    )
    _dynamic(issuers, f"B{FIRST_ROW}", f"SUMIFS({_col('承作總額')},{_col('發行機構')},A{FIRST_ROW}#,{by})")
    _share_sheet(book, ISSUER_SHARE_SHEET, ISSUER_SHEET, "發行機構", LATIN_FONT)

    branches = _sheet(book, BRANCH_SHEET, "分公司", "在庫部位：分公司 × 幣別（原幣，未換算）", CJK_FONT, AMOUNT_FORMAT)
    _dynamic(branches, f"B{HEADER_ROW}", _spill(ISSUER_SHEET, f"B{HEADER_ROW}"))
    _dynamic(branches, f"B{TOTAL_ROW}", "+".join(f"SUMIFS({_col(f'金額_{n}')},{by})" for n in range(1, groups + 1)))
    block = f"{TABLE_NAME}[[分公司_1]:[金額_{groups}]]"  # 第 n 組的分公司在這塊的第 3n-2 欄
    _dynamic(
        branches,
        f"A{FIRST_ROW}",
        f"LET(k,SEQUENCE(ROWS({_col('存續狀態')})*{groups}),i,INT((k-1)/{groups})+1,"
        f"br,INDEX({block},i,MOD(k-1,{groups})*3+1),"
        f'SORT(UNIQUE(FILTER(br,(INDEX({_col("存續狀態")},i)="{IN_STOCK}")*(br<>"-")*(br<>""),""))))',
        ("k", "i", "br"),
    )
    _dynamic(
        branches,
        f"B{FIRST_ROW}",
        "+".join(f"SUMIFS({_col(f'金額_{n}')},{_col(f'分公司_{n}')},A{FIRST_ROW}#,{by})" for n in range(1, groups + 1)),
    )
    _share_sheet(book, BRANCH_SHARE_SHEET, BRANCH_SHEET, "分公司", CJK_FONT)
    book.calculation.fullCalcOnLoad = True  # 公式沒有存值，打開時整本重算


def file_formula(formula: str, names: tuple[str, ...] = ()) -> str:
    """平常看到的公式 → Excel 檔案裡的寫法：新函式加前綴、`LET` 的變數（`names`）加 `_xlpm.`、`A6#` 換成 `ANCHORARRAY`。"""
    for name in names:
        formula = re.sub(rf"(?<![\w.\[]){name}(?![\w\[(])", f"_xlpm.{name}", formula)
    for function, prefix in _NEW_FUNCTIONS.items():
        formula = re.sub(rf"(?<![\w.]){function}\(", f"{prefix}{function}(", formula)
    return _SPILL.sub(r"_xlfn.ANCHORARRAY(\1)", formula)


def enable_spill(path: Path) -> None:
    """存好的 xlsx 補上動態陣列中繼資料，陣列公式才會像 Excel 365 的公式一樣溢出。"""
    with zipfile.ZipFile(path) as source:
        parts = [(item, source.read(item.filename)) for item in source.infolist()]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as target:
        for item, data in parts:
            if item.filename == "[Content_Types].xml":
                data = data.replace(b"</Types>", _CONTENT_TYPE + b"</Types>")
            elif item.filename == "xl/_rels/workbook.xml.rels":
                data = data.replace(b"</Relationships>", _RELATIONSHIP + b"</Relationships>")
            elif item.filename.startswith("xl/worksheets/sheet"):
                data = re.sub(rb'<c (r="[A-Z]+\d+"[^>]*)><f t="array"', rb'<c \1 cm="1"><f t="array"', data)
            target.writestr(item, data)
        target.writestr("xl/metadata.xml", _METADATA)


def _col(name: str) -> str:
    return f"{TABLE_NAME}[{name}]"


def _in_stock() -> str:
    return f'({_col("存續狀態")}="{IN_STOCK}")'


def _spill(sheet: str, ref: str) -> str:
    return f"'{sheet}'!{ref}#"


def _dynamic(sheet: Worksheet, ref: str, formula: str, names: tuple[str, ...] = ()) -> None:
    sheet[ref] = ArrayFormula(ref, "=" + file_formula(formula, names))


def _sheet(book: openpyxl.Workbook, title: str, label: str, heading: str, label_font: str, fmt: str) -> Worksheet:
    """分析分頁的版面：註記、標題、表頭、總計列與名稱列的格式；公式由呼叫端放。"""
    sheet = book.create_sheet(title)
    sheet.sheet_view.showGridLines = False
    for row, note in enumerate(NOTES, start=1):
        sheet.cell(row, 1, note).font = Font(name=CJK_FONT, color=NOTE_COLOR)
    sheet.cell(3, 1, heading).font = Font(name=CJK_FONT, bold=True, size=13)
    last_row, last_column = FIRST_ROW + FORMAT_ROWS - 1, 1 + FORMAT_COLUMNS
    for cell, value in ((sheet.cell(HEADER_ROW, 1), label), (sheet.cell(TOTAL_ROW, 1), "總計")):
        cell.value = value
        cell.font = Font(name=CJK_FONT, bold=True, color="FFFFFF" if value == label else None)
        cell.alignment = Alignment(horizontal="center" if value == label else "left", vertical="center")
    sheet.cell(HEADER_ROW, 1).fill = PatternFill("solid", fgColor=HEAD_COLOR)
    for column in range(2, last_column + 1):
        cell = sheet.cell(HEADER_ROW, column)
        cell.font = Font(name=LATIN_FONT, bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        for row in range(TOTAL_ROW, last_row + 1):
            cell = sheet.cell(row, column)
            cell.font = Font(name=LATIN_FONT, bold=row == TOTAL_ROW)
            cell.number_format = fmt
            cell.alignment = Alignment(horizontal="right", vertical="center")
    for row in range(FIRST_ROW, last_row + 1):
        sheet.cell(row, 1).font = Font(name=label_font)
    sheet.row_dimensions[HEADER_ROW].height = 22
    sheet.column_dimensions["A"].width = 14
    for column in range(2, last_column + 1):
        sheet.column_dimensions[get_column_letter(column)].width = 17
    end = get_column_letter(last_column)
    head = DifferentialStyle(font=Font(color="FFFFFF", bold=True), fill=PatternFill(bgColor=HEAD_COLOR))
    sheet.conditional_formatting.add(f"B{HEADER_ROW}:{end}{HEADER_ROW}", _rule(f'B{HEADER_ROW}<>""', head))
    total = DifferentialStyle(
        border=Border(top=Side(style="thin", color="000000"), bottom=Side(style="thin", color="000000"))
    )
    sheet.conditional_formatting.add(f"A{TOTAL_ROW}:{end}{TOTAL_ROW}", _rule(f'A${HEADER_ROW}<>""', total))
    line = DifferentialStyle(border=Border(bottom=_THIN))
    sheet.conditional_formatting.add(
        f"A{FIRST_ROW}:{end}{last_row}", _rule(f'AND($A{FIRST_ROW}<>"",A${HEADER_ROW}<>"")', line)
    )
    sheet.freeze_panes = f"B{FIRST_ROW}"
    return sheet


def _share_sheet(book: openpyxl.Workbook, title: str, source: str, label: str, label_font: str) -> None:
    """同幣別內的占比：名稱、幣別照 `source` 分頁，金額除以該幣別總計。"""
    sheet = _sheet(book, title, label, f"在庫占比：{label} × 幣別（同幣別內，依金額）", label_font, SHARE_FORMAT)
    _dynamic(sheet, f"B{HEADER_ROW}", _spill(source, f"B{HEADER_ROW}"))
    _dynamic(sheet, f"A{FIRST_ROW}", _spill(source, f"A{FIRST_ROW}"))
    total = _spill(source, f"B{TOTAL_ROW}")
    _dynamic(sheet, f"B{TOTAL_ROW}", f"IFERROR({total}/{total},0)")
    _dynamic(sheet, f"B{FIRST_ROW}", f"IFERROR({_spill(source, f'B{FIRST_ROW}')}/{total},0)")


def _rule(formula: str, style: DifferentialStyle) -> Rule:
    return Rule(type="expression", dxf=style, formula=[formula])


_CONTENT_TYPE = (
    b'<Override PartName="/xl/metadata.xml"'
    b' ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheetMetadata+xml"/>'
)
_RELATIONSHIP = (
    b'<Relationship Id="rIdSpill" Target="metadata.xml"'
    b' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sheetMetadata"/>'
)
_METADATA = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<metadata xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    ' xmlns:xda="http://schemas.microsoft.com/office/spreadsheetml/2017/dynamicarray">'
    '<metadataTypes count="1"><metadataType name="XLDAPR" minSupportedVersion="120000" copy="1" pasteAll="1"'
    ' pasteValues="1" merge="1" splitFirst="1" rowColShift="1" clearFormats="1" clearComments="1" assign="1"'
    ' coerce="1" cellMeta="1"/></metadataTypes>'
    '<futureMetadata name="XLDAPR" count="1"><bk><extLst><ext uri="{bdbb8cdc-fa1e-496e-a857-3c3f30c029c3}">'
    '<xda:dynamicArrayProperties fDynamic="1" fCollapsed="0"/></ext></extLst></bk></futureMetadata>'
    '<cellMetadata count="1"><bk><rc t="1" v="0"/></bk></cellMetadata></metadata>'
)
