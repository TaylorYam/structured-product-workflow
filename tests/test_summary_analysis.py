"""商品總表的分析分頁（#293）：公式寫法、動態陣列中繼資料；`excel` 標記的測試用真的 Excel 算一次。資料全部虛構。"""

from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

import openpyxl
import pytest

from sp_workflow.file_tools import FileWriteError, save_export
from sp_workflow.summary_analysis import (
    BRANCH_SHARE_SHEET,
    BRANCH_SHEET,
    ISSUER_SHARE_SHEET,
    ISSUER_SHEET,
    NOTES,
    TABLE_NAME,
    add_analysis_sheets,
    enable_spill,
    file_formula,
    summary_table,
)

HEADER = ["存續狀態", "發行機構", "承作幣別", "承作總額", "分公司_1", "FC_1", "金額_1", "分公司_2", "FC_2", "金額_2"]
ROWS = [
    ["在庫", "BNP", "USD", 300, "台北", "Amy", 100, "台中", "Ben", 200],
    ["在庫", "Barclays", "JPY", 5000, "台中", "Ben", 5000, "-", "-", "-"],
    ["KO", "BNP", "USD", 999, "台北", "Amy", 999, "-", "-", "-"],  # 已出場不算
    ["在庫", "BNP", "USD", 50, "新竹", "Cat", 50, "-", "-", "-"],
    ["在庫", "UBS", "USD", "-", "-", "-", "-", "-", "-", "-"],  # 沒有交易明細
]


def write_book(path: Path, rows: list[list[object]], groups: int = 2) -> Path:
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "商品總表"
    for row in [HEADER, *rows]:
        sheet.append(row)
    summary_table(sheet, len(HEADER), len(rows))
    add_analysis_sheets(book, groups)
    save_export(book, path, finish=enable_spill)
    return path


def test_formulas_use_the_excel_file_spelling() -> None:
    formula = file_formula(
        'LET(c,UNIQUE(FILTER(總表[承作幣別],總表[存續狀態]="在庫")),SORTBY(c,COUNTIFS(總表[承作幣別],c),-1))', ("c",)
    )

    assert formula == (
        '_xlfn.LET(_xlpm.c,_xlfn.UNIQUE(_xlfn._xlws.FILTER(總表[承作幣別],總表[存續狀態]="在庫")),'
        "_xlfn.SORTBY(_xlpm.c,COUNTIFS(總表[承作幣別],_xlpm.c),-1))"
    )
    assert file_formula("SORT(A6#)+'發行機構x幣別'!B5#") == (
        "_xlfn._xlws.SORT(_xlfn.ANCHORARRAY(A6))+_xlfn.ANCHORARRAY('發行機構x幣別'!B5)"
    )


def test_the_summary_is_a_table_followed_by_four_analysis_sheets(tmp_path: Path) -> None:
    book = openpyxl.load_workbook(write_book(tmp_path / "x.xlsx", ROWS, groups=2))

    assert book.sheetnames == ["商品總表", ISSUER_SHEET, ISSUER_SHARE_SHEET, BRANCH_SHEET, BRANCH_SHARE_SHEET]
    table = book["商品總表"].tables[TABLE_NAME]
    assert table.ref == "A1:J6" and book["商品總表"].auto_filter.ref is None, "篩選按鈕由表格提供"
    for title in book.sheetnames[1:]:
        sheet = book[title]
        assert [sheet["A1"].value, sheet["A2"].value] == list(NOTES)
        assert (sheet["A5"].value, sheet.freeze_panes) == ("總計", "B6")
    assert book[ISSUER_SHEET]["A4"].value == "發行機構" and book[BRANCH_SHEET]["A4"].value == "分公司"


def test_branch_formulas_cover_every_trade_group(tmp_path: Path) -> None:
    book = openpyxl.load_workbook(write_book(tmp_path / "x.xlsx", ROWS, groups=3))

    amounts = book[BRANCH_SHEET]["B6"].value.text
    assert [f"總表[金額_{n}],總表[分公司_{n}]" in amounts for n in (1, 2, 3)] == [True] * 3
    assert "總表[[分公司_1]:[金額_3]]" in book[BRANCH_SHEET]["A6"].value.text


def test_saved_file_marks_every_formula_as_a_dynamic_array(tmp_path: Path) -> None:
    path = write_book(tmp_path / "x.xlsx", ROWS)

    with zipfile.ZipFile(path) as xlsx:
        names = xlsx.namelist()
        types = xlsx.read("[Content_Types].xml").decode()
        rels = xlsx.read("xl/_rels/workbook.xml.rels").decode()
        sheets = [xlsx.read(name).decode() for name in names if name.startswith("xl/worksheets/sheet")]
    assert "xl/metadata.xml" in names and "/xl/metadata.xml" in types and 'Target="metadata.xml"' in rels
    arrays = [cell for sheet in sheets for cell in re.findall(r"<c [^>]*><f t=\"array\"", sheet)]
    assert len(arrays) == 16 and all('cm="1"' in cell for cell in arrays), "四個分頁各 4 個公式"


def test_a_failed_finish_leaves_no_temp_file(tmp_path: Path) -> None:
    def broken(_path: Path) -> None:
        raise PermissionError(13, "Permission denied")

    book = openpyxl.Workbook()
    with pytest.raises(FileWriteError, match="寫不進"):
        save_export(book, tmp_path / "x.xlsx", finish=broken)
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------- 真的 Excel


@pytest.fixture(scope="module")
def excel():
    if sys.platform != "win32":
        pytest.skip("不是 Windows")
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        pytest.skip("沒有 pywin32")
    pythoncom.CoInitialize()
    try:
        app = win32com.client.DispatchEx("Excel.Application")
    except Exception:
        pytest.skip("無法啟動 Microsoft Excel")
    app.Visible = False
    app.DisplayAlerts = False
    yield app
    app.Quit()
    pythoncom.CoUninitialize()


def grid(book, title: str, rows: int, columns: int) -> list[list[str]]:
    sheet = book.Worksheets(title)
    return [[sheet.Cells(4 + r, 1 + c).Text for c in range(columns)] for r in range(rows)]


@pytest.mark.excel
def test_excel_spills_in_stock_amounts_and_follows_edits(excel, tmp_path: Path) -> None:
    book = excel.Workbooks.Open(str(write_book(tmp_path / "x.xlsx", ROWS)))
    try:
        excel.CalculateFull()
        assert grid(book, ISSUER_SHEET, 5, 3) == [
            ["發行機構", "USD", "JPY"],  # USD 在庫 3 檔、JPY 1 檔
            ["總計", "350", "5,000"],
            ["Barclays", "-", "5,000"],
            ["BNP", "350", "-"],
            ["UBS", "-", "-"],  # 在庫但沒有金額
        ]
        assert sorted(grid(book, BRANCH_SHEET, 5, 3)[2:]) == [
            ["台中", "200", "5,000"],  # 一檔兩家分公司，各分到自己的金額
            ["台北", "100", "-"],
            ["新竹", "50", "-"],
        ]
        assert grid(book, ISSUER_SHARE_SHEET, 4, 3)[1:] == [
            ["總計", "100.0%", "100.0%"],
            ["Barclays", "-", "100.0%"],
            ["BNP", "100.0%", "-"],
        ]

        table = book.Worksheets("商品總表").ListObjects(TABLE_NAME)
        table.ListRows.Add().Range.Value = (("在庫", "HSBC", "EUR", 70, "台北", "Dan", 70, "-", "-", "-"),)
        excel.Calculate()
        assert grid(book, ISSUER_SHEET, 2, 4) == [["發行機構", "USD", "JPY", "EUR"], ["總計", "350", "5,000", "70"]], (
            "表格下方新增一列：新的幣別、發行機構自動多出來（檔數一樣時照總表出現的順序）"
        )
        assert "HSBC" in [row[0] for row in grid(book, ISSUER_SHEET, 7, 1)]
    finally:
        book.Close(False)


@pytest.mark.excel
def test_excel_shows_no_error_values_without_products(excel, tmp_path: Path) -> None:
    book = excel.Workbooks.Open(str(write_book(tmp_path / "x.xlsx", [])))
    try:
        excel.CalculateFull()
        for title in (ISSUER_SHEET, ISSUER_SHARE_SHEET, BRANCH_SHEET, BRANCH_SHARE_SHEET):
            assert not any(text.startswith("#") for row in grid(book, title, 4, 3) for text in row), title
    finally:
        book.Close(False)
