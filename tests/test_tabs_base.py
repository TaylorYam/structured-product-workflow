"""分頁共用的版面（`sp_workflow.tabs.base.Tab`）：直的 TDCC 欄位的計數（#83、#85）與結果表的複製（#121）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from window_synth import new_window


def test_pasted_tdccs_are_counted_once_each(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)

    window.prepare.tdcc_text.insert("1.0", "147000000001\n147000000002 147000000001、147000000003")
    master.update()

    assert window.prepare.count_var.get() == "已貼 3 筆", "重複的只算一次，分隔方式同準備"


@pytest.mark.parametrize("tab_name", ["orders", "mail"])
def test_order_and_mail_pages_count_pasted_tdccs(master, tmp_path: Path, tab_name) -> None:
    tab = getattr(new_window(master, tmp_path), tab_name)

    tab.tdcc_text.insert("1.0", "147000000001\n147000000002\n147000000001")
    master.update()

    assert tab.count_var.get() == "已貼 2 筆"


# ---------------------------------------------------------------- 結果表左右捲動（#179）


@pytest.mark.parametrize("tab_name", ["daily", "orders", "prepare", "returning", "mail", "progress", "exits"])
def test_result_tables_scroll_sideways(master, tmp_path: Path, tab_name) -> None:
    tab = getattr(new_window(master, tmp_path), tab_name)

    assert str(tab.hscroll.cget("orient")) == "horizontal"
    assert tab.hscroll.grid_info()["row"] == 2, "在表的下方"
    assert tab.table.cget("xscrollcommand"), "表捲動時捲軸跟著動"
    last = tab.columns[-1][0]
    assert tab.table.column(last, "minwidth") == tab.columns[-1][2], "最後一欄不會被擠到看不見"


# ---------------------------------------------------------------- 結果表複製（#121）


TABLE_TABS = ["daily", "orders", "prepare", "returning", "mail"]


def headings(tab) -> list[str]:
    return [label for _, label, _ in tab.columns]


@pytest.mark.parametrize("tab_name", TABLE_TABS)
def test_copy_all_puts_the_whole_table_on_the_clipboard(master, tmp_path: Path, tab_name) -> None:
    tab = getattr(new_window(master, tmp_path), tab_name)
    width = len(tab.columns)
    tab.add_row(["029199990001", *(["第一行\n第二行\t尾"] * (width - 1))], True)
    tab.add_row(["029199990002", *([""] * (width - 1))], False)

    tab.table_area.button.invoke()

    lines = master.clipboard_get().split("\n")
    assert lines[0] == "\t".join(headings(tab))
    assert lines[1].split("\t") == ["029199990001", *(["第一行 第二行 尾"] * (width - 1))], (
        "前導零保留；換行、Tab 換成空白"
    )
    assert lines[2].split("\t") == ["029199990002", *([""] * (width - 1))]
    assert tab.table_area.button.cget("text") == "已複製 2 列"


def test_copy_all_on_an_empty_table_leaves_the_clipboard_alone(master, tmp_path: Path) -> None:
    tab = new_window(master, tmp_path).daily
    master.clipboard_clear()
    master.clipboard_append("原本的內容")

    tab.table_area.button.invoke()

    assert master.clipboard_get() == "原本的內容"
    assert tab.table_area.button.cget("text") == "表上沒有資料"


def test_right_click_copies_one_cell_or_one_row(master, tmp_path: Path) -> None:
    tab = new_window(master, tmp_path).daily
    tab.add_row(["029199990001", "新增交易", "說明\n第二行"], False)
    item = tab.table.get_children()[0]

    tab.copier.fill_menu(item, "#1")

    assert tab.copier.menu.labels() == [
        "複製「029199990001」",
        "複製這一列",
        "複製選取的列（0 列）",
        "全部複製（含表頭）",
    ]
    assert tab.copier.menu.entrycget(2, "state") == "disabled", "沒選列時不能複製選取的列"
    tab.copier.menu.invoke(0)
    assert master.clipboard_get() == "029199990001"
    tab.copier.menu.invoke(1)
    assert master.clipboard_get() == "029199990001\t新增交易\t說明 第二行"
    tab.copier.fill_menu(item, "#3")
    assert tab.copier.menu.labels()[0] == "複製「說明 第二行」"
    tab.copier.menu.invoke(0)
    assert master.clipboard_get() == "說明 第二行", "一格的換行也換成空白，貼到 Excel 不會變兩列"
    assert tab.table.selection() == (), "右鍵複製不換選取的列"


def test_right_click_outside_the_rows_offers_the_whole_table(master, tmp_path: Path) -> None:
    tab = new_window(master, tmp_path).daily

    tab.copier.fill_menu("", "")

    assert tab.copier.menu.labels() == ["複製選取的列（0 列）", "全部複製（含表頭）"]


def test_select_all_and_copy_selected_rows(master, tmp_path: Path) -> None:
    tab = new_window(master, tmp_path).daily
    for n in (1, 2, 3):
        tab.add_row([f"02919999000{n}", "新增交易", ""], False)

    tab.copier.select_all()
    assert len(tab.table.selection()) == 3, "Ctrl+A 全部反白"
    tab.table.selection_set(tab.table.get_children()[2], tab.table.get_children()[0])
    tab.copier.copy_selected()

    assert master.clipboard_get() == "029199990001\t新增交易\t\n029199990003\t新增交易\t", "照畫面的順序，不含表頭"


def test_ctrl_a_and_ctrl_c_work_on_the_table(master, tmp_path: Path) -> None:
    tab = new_window(master, tmp_path).daily
    for n in (1, 2):
        tab.add_row([f"02919999000{n}", "新增交易", ""], False)
    master.deiconify()
    master.update()
    tab.table.focus_force()
    master.update()
    if master.focus_get() is not tab.table:
        pytest.skip("拿不到鍵盤焦點（例如畫面鎖住）")

    tab.table.event_generate("<Control-a>", when="now")
    tab.table.event_generate("<Control-c>", when="now")

    assert len(tab.table.selection()) == 2
    assert master.clipboard_get() == "029199990001\t新增交易\t\n029199990002\t新增交易\t"
