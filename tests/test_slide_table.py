"""給 PPT 用的報表表格（#483）：投影片版面（盤勢速報）與清單版面（曝險表）的字型、置中、框線、格線。

報表測試（`test_market_brief`、`test_exposure`）只斷言內容；「貼到 PPT 不走樣」的版面斷言都在這裡。資料全部虛構。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import openpyxl
import pytest
from openpyxl.styles import PatternFill

from sp_workflow.exposure import export_exposure, export_hotspots
from sp_workflow.market_brief import export_brief
from sp_workflow.slide_table import Column, list_book, slide_book, write_slide
from test_exposure import DAY, UNTIL, closes, seed, trades

BLUE = PatternFill("solid", fgColor="B8CCE4")


def table_cells(sheet, top: int):
    return [cell for row in sheet.iter_rows(min_row=top, max_col=sheet.max_column) for cell in row]


# ---------------------------------------------------------------- 投影片


def test_slide_puts_the_title_on_a1_and_the_table_from_row_3(tmp_path: Path) -> None:
    book = slide_book()
    sheet = book.create_sheet("總覽")

    first = write_slide(
        sheet,
        "標題",
        (Column("名稱", 20), Column("面額\n（M）", 12, "0.0"), Column("備註", 10, "0.0")),
        [("甲", 1.25, "文字"), ("乙", None, "=1+1")],
    )
    book.save(tmp_path / "x.xlsx")
    book = openpyxl.load_workbook(tmp_path / "x.xlsx")
    sheet = book["總覽"]

    assert first == 4 and book.sheetnames == ["總覽"], "沒有預設的空白工作表"
    assert book._fonts[0].name == "微軟正黑體", "活頁簿預設字型是微軟正黑體，中文用它顯示"  # noqa: SLF001
    assert (sheet["A1"].value, sheet["A1"].font.name, sheet["A1"].font.sz, sheet["A1"].font.b) == (
        "標題",
        "Calibri",
        16,
        True,
    )
    assert sheet.sheet_view.showGridLines is False
    assert not sheet.merged_cells.ranges and not sheet.conditional_formatting, "貼到 PPT 不走樣"
    header = sheet[3]
    assert [c.value for c in header] == ["名稱", "面額\n（M）", "備註"]
    assert all(c.fill.fgColor.rgb.endswith("1F4E79") and c.font.color.rgb.endswith("FFFFFF") for c in header)
    assert header[1].alignment.wrap_text, "表頭可以換兩行"
    assert sheet.row_dimensions[3].height == 35 and sheet.row_dimensions[4].height == 20
    for cell in table_cells(sheet, 3):
        assert cell.font.name == "Calibri", "英文、數字用 Calibri"
        assert (cell.alignment.horizontal, cell.alignment.vertical) == ("center", "center")
        assert {cell.border.left.style, cell.border.bottom.style} == {"thin"}
    assert sheet["B4"].number_format == "0.0" and sheet["B5"].number_format == "General", "空格不套格式"
    assert (sheet["C5"].value, sheet["C5"].data_type, sheet["C4"].number_format) == ("=1+1", "s", "General"), (
        "文字照文字寫、不套數字格式"
    )
    assert round(sheet.column_dimensions["A"].width, 1) == 20


@pytest.mark.db
def test_every_brief_sheet_is_slide_ready(pg, tmp_path: Path) -> None:
    seed(pg, "147199960001", ["AAA UW", "BBB UN"])
    trades(pg, "147199960001", [("台北", "Amy", 1000000)])
    seed(pg, "147199960002", ["AAA UW", "CCC UN"], barrier="EKI", final=dt.date(2027, 3, 15))
    closes(pg, {"AAA UW": "72", "BBB UN": "95", "CCC UN": "110"})
    path = tmp_path / "盤勢速報.xlsx"

    export_brief(pg, ["AAA"], path, today=DAY)

    book = openpyxl.load_workbook(path)
    assert book._fonts[0].name == "微軟正黑體"  # noqa: SLF001
    for sheet in book.worksheets:
        assert sheet.sheet_view.showGridLines is False, sheet.title
        assert not sheet.merged_cells.ranges and not sheet.conditional_formatting, sheet.title
        assert sheet["A1"].font.name == "Calibri" and sheet["A2"].value is None, "A1 標題、A3 起表格"
        for cell in table_cells(sheet, 3):
            assert cell.font.name == "Calibri", (sheet.title, cell.coordinate)
            assert cell.alignment.horizontal == "center" and cell.border.left.style == "thin", cell.coordinate
    assert book["大跌標的"]["C3"].alignment.wrap_text, "表頭兩行"


# ---------------------------------------------------------------- 清單


def test_list_keeps_the_filtered_frozen_layout(tmp_path: Path) -> None:
    columns = (Column("TDCC", 14, fill=BLUE), Column("面額", 11, "#,##0"), Column("日期", 12, "mm-dd-yy", BLUE))
    book = list_book("全部商品", columns, [("012345678901", 1000, dt.date(2026, 11, 20)), ("1", None, None)])
    book.save(tmp_path / "x.xlsx")
    sheet = openpyxl.load_workbook(tmp_path / "x.xlsx")["全部商品"]

    assert [c.value for c in sheet[1]] == ["TDCC", "面額", "日期"] and all(c.font.b for c in sheet[1])
    assert [c.fill.fill_type for c in sheet[1]] == ["solid", None, "solid"], "表頭照各欄的底色"
    assert (sheet.freeze_panes, sheet.auto_filter.ref) == ("A2", "A1:C3")
    assert sheet.sheet_view.showGridLines is not False, "清單版面不關格線"
    assert {(c.alignment.horizontal, c.alignment.vertical) for c in table_cells(sheet, 1)} == {("center", "center")}
    assert (sheet["A2"].value, sheet["A2"].number_format) == ("012345678901", "@"), "TDCC 保留前導零"
    assert (sheet["B2"].number_format, sheet["C2"].number_format, sheet["B3"].number_format) == (
        "#,##0",
        "mm-dd-yy",
        "General",
    )
    assert {c.border.left.style for c in table_cells(sheet, 1)} == {None}, "清單版面沒有框線"


@pytest.mark.db
@pytest.mark.parametrize("hotspots", [False, True])
def test_exposure_sheets_use_the_list_layout(pg, tmp_path: Path, hotspots: bool) -> None:
    seed(pg, "147199960003", ["AAA UW", "BBB UN"])
    trades(pg, "147199960003", [("台北", "Amy", 100000), ("台中", "Ben", 50000)])
    closes(pg, {"AAA UW": "72", "BBB UN": "95"})
    path = tmp_path / "曝險.xlsx"

    if hotspots:
        export_hotspots(pg, UNTIL, path, today=DAY)
    else:
        export_exposure(pg, ["AAA UW"], UNTIL, path, today=DAY)

    sheet = openpyxl.load_workbook(path)["全部商品"]
    assert all(c.font.b for c in sheet[1]) and all(c.fill.fill_type == "solid" for c in sheet[1]), "粗體、有底色的表頭"
    assert {(c.alignment.horizontal, c.alignment.vertical) for c in table_cells(sheet, 1)} == {("center", "center")}
    assert (sheet.freeze_panes, sheet.auto_filter.ref) == ("A2", sheet.dimensions)
    assert sheet.sheet_view.showGridLines is not False and {c.border.left.style for c in table_cells(sheet, 1)} == {
        None
    }
