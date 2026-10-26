"""人工維護的檢查結果表（`sp_workflow.tabs.cell_table.CellTable`，#115、#121）：列、格子的顏色與字、整張表複製。"""

from __future__ import annotations

import pytest

from fcn_checker.ui import ACCENT_SOFT, SURFACE
from sp_workflow.tabs.cell_table import CellTable
from sp_workflow.tabs.editor import PROBLEM_BG

A, B = "029199990001", "029199990002"
COLUMNS = (("currency", "Currency"), ("coupon", "Coupon p.a. (%)"))


@pytest.fixture
def table(master) -> CellTable:
    selected: list[str] = []
    cells = CellTable(master, ("tdcc", "TDCC"), COLUMNS, selected.append)
    cells.set_rows(
        [
            (A, {"currency": "USD", "coupon": "12.5"}, ()),
            (B, {"currency": "JPY"}, ("coupon",)),  # 缺 Coupon：沒有字、淺紅底
        ]
    )
    return cells


def test_rows_follow_the_screen_order_with_an_optional_header(table: CellTable) -> None:
    assert table.rows(header=True) == [
        ["TDCC", "Currency", "Coupon p.a. (%)"],
        [A, "USD", "12.5"],
        [B, "JPY", ""],
    ]
    assert table.rows([B]) == [[B, "JPY", ""]], "只拿指定的列、沒有表頭"


def test_missing_cells_are_blank_on_a_light_red_background(table: CellTable) -> None:
    assert (table.text(B, "coupon"), table.background(B, "coupon")) == ("", PROBLEM_BG)
    assert (table.text(B, "currency"), table.background(B, "currency")) == ("JPY", SURFACE)
    assert (table.text(A, "tdcc"), table.background(A, "tdcc")) == (A, SURFACE), "第一欄顯示 key"


def test_selecting_a_row_keeps_missing_cells_red(table: CellTable) -> None:
    table.select(B)

    assert table.background(B, "currency") == ACCENT_SOFT and table.background(B, "coupon") == PROBLEM_BG
    assert table.background(A, "currency") == SURFACE

    table.set_rows([(A, {"currency": "USD"}, ())])
    assert table.selected is None, "選中的那列不在表上了就不選"


def test_copy_all_puts_tab_separated_rows_with_the_header_on_the_clipboard(master, table: CellTable) -> None:
    assert table.copy_all() == 2

    assert master.clipboard_get() == f"TDCC\tCurrency\tCoupon p.a. (%)\n{A}\tUSD\t12.5\n{B}\tJPY\t"


def test_copy_all_on_an_empty_table_leaves_the_clipboard_alone(master, table: CellTable) -> None:
    table.set_rows(())
    master.clipboard_clear()
    master.clipboard_append("原本的內容")

    assert table.copy_all() == 0
    assert master.clipboard_get() == "原本的內容"


def test_an_empty_table_says_so(table: CellTable) -> None:
    assert table.placeholder.winfo_manager() == "", "有列時不顯示"
    table.set_rows(())
    assert table.placeholder.cget("text") == "沒有資料" and table.placeholder.winfo_manager() == "place"
