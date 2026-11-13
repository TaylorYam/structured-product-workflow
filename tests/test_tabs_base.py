"""分頁共用的版面（`sp_workflow.tabs.base.Tab`）：直的 TDCC 欄位的計數（#83、#85）、結果表的複製（#121）與背景動作（`ActionRunner`，#206）。"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

import pytest

from fcn_checker.ui import Scrollbar
from sp_workflow.database import DatabaseError
from sp_workflow.tabs.base import ActionRunner, DatabaseLogin, SideWindow, SideWindows
from window_synth import HeldBackground, new_window


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

    assert str(tab.hbar.cget("orient")) == "horizontal"
    below = (tab.hbar.grid_info()["row"], tab.hbar.master)
    assert below == (tab.table.grid_info()["row"] + 1, tab.table.master), "在表的下方"
    assert tab.table.cget("xscrollcommand"), "表捲動時捲軸跟著動"
    last = tab.columns[-1][0]
    assert tab.table.column(last, "minwidth") == tab.columns[-1][2], "最後一欄不會被擠到看不見"


# ---------------------------------------------------------------- 結果表複製（#121）


TABLE_TABS = ["daily", "orders", "prepare", "returning", "mail"]


def headings(tab) -> list[str]:
    return [label for _, label, _ in tab.columns]


def copy_whole_table(tab) -> None:
    """按右鍵選單的最後一項「全部複製（含表頭）」。"""
    tab.copier.fill_menu("", "")
    assert tab.copier.menu.labels()[-1] == "全部複製（含表頭）"
    tab.copier.menu.invoke(tk.END)


@pytest.mark.parametrize("tab_name", [*TABLE_TABS, "progress", "exits"])
def test_result_tables_have_no_copy_all_button(master, tmp_path: Path, tab_name) -> None:
    """表右上方的「全部複製」已拿掉（#557）：表上方只剩左邊的說明，說明不提按鈕。"""
    tab = getattr(new_window(master, tmp_path), tab_name)

    bar = tab.table_area.bar.winfo_children()

    assert [type(widget) for widget in bar] == [ttk.Label]
    assert "全部複製" not in str(bar[0].cget("text"))


@pytest.mark.parametrize("tab_name", TABLE_TABS)
def test_copy_all_puts_the_whole_table_on_the_clipboard(master, tmp_path: Path, tab_name) -> None:
    tab = getattr(new_window(master, tmp_path), tab_name)
    width = len(tab.columns)
    tab.add_row(["029199990001", *(["第一行\n第二行\t尾"] * (width - 1))], True)
    tab.add_row(["029199990002", *([""] * (width - 1))], False)

    copy_whole_table(tab)

    lines = master.clipboard_get().split("\n")
    assert lines[0] == "\t".join(headings(tab))
    assert lines[1].split("\t") == ["029199990001", *(["第一行 第二行 尾"] * (width - 1))], (
        "前導零保留；換行、Tab 換成空白"
    )
    assert lines[2].split("\t") == ["029199990002", *([""] * (width - 1))]


def test_copy_all_on_an_empty_table_leaves_the_clipboard_alone(master, tmp_path: Path) -> None:
    tab = new_window(master, tmp_path).daily
    master.clipboard_clear()
    master.clipboard_append("原本的內容")

    copy_whole_table(tab)

    assert master.clipboard_get() == "原本的內容"


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


# ---------------------------------------------------------------- 背景動作（`ActionRunner`，#206）


def runner_with(master, tmp_path: Path, background, *, password: bool = True) -> tuple[ActionRunner, list]:
    """虛構帳號的 `ActionRunner`；回傳它和 busy 被呼叫的紀錄。`password=False`：連線設定檔沒有密碼，要問。"""
    env = tmp_path / ".env"
    env.write_text("PGDATABASE=spw_fake\nPGUSER=operator1\n" + ("PGPASSWORD=fake\n" if password else ""), "utf-8")
    busy: list[bool] = []
    runner = ActionRunner(DatabaseLogin(master, env), background(master), tk.StringVar(master), busy.append)
    return runner, busy


def test_run_action_does_nothing_without_a_login(master, tmp_path: Path, monkeypatch, immediate) -> None:
    runner, busy = runner_with(master, tmp_path, immediate, password=False)
    monkeypatch.setattr(simpledialog, "askstring", lambda *a, **k: None)  # 取消登入

    started = runner.run_action(lambda settings: pytest.fail("沒登入不做"), "處理中…", lambda r: pytest.fail("不交回"))

    assert started is False and busy == [] and runner.status.get() == ""


def test_run_action_hands_the_result_back_and_restores_the_buttons(master, tmp_path: Path, immediate) -> None:
    runner, busy = runner_with(master, tmp_path, immediate)
    results = []

    started = runner.run_action(lambda settings: settings.user, "處理中…", results.append)

    assert started is True and results == ["operator1"]
    assert busy == [True, False] and runner.working is False


def test_a_database_error_forgets_the_password_and_is_shown(master, tmp_path: Path, monkeypatch, immediate) -> None:
    runner, busy = runner_with(master, tmp_path, immediate)
    errors = []
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: errors.append(a[1]))

    def fail(settings):
        raise DatabaseError("連不上資料庫")

    runner.run_action(fail, "處理中…", lambda r: pytest.fail("出錯不交回"))

    assert errors == ["連不上資料庫"] and runner.status.get() == "連不上資料庫"
    assert runner.db.password is None, "下次重新詢問密碼"
    assert busy == [True, False]


def test_only_one_action_runs_at_a_time(master, tmp_path: Path) -> None:
    runner, busy = runner_with(master, tmp_path, HeldBackground)
    results = []

    assert runner.run_action(lambda settings: 1, "第一件…", results.append) is True
    assert runner.run_action(lambda settings: 2, "第二件…", results.append) is False, "做完前不再開始"
    assert runner.status.get() == "第一件…" and len(runner.background.held) == 1

    runner.background.run_all()

    assert results == [1] and busy == [True, False]
    assert runner.run_action(lambda settings: 3, "第三件…", results.append) is True, "做完後可以再開始"


def test_a_failing_result_handler_does_not_leave_the_buttons_disabled(master, tmp_path: Path) -> None:
    runner, busy = runner_with(master, tmp_path, HeldBackground)

    def show(result) -> None:
        raise RuntimeError("顯示結果時出錯")

    runner.run_action(lambda settings: 1, "處理中…", show)
    with pytest.raises(RuntimeError):
        runner.background.run_all()

    assert busy == [True, False] and runner.working is False, "可以再開始下一件"


# ---------------------------------------------------------------- 選單另開的子視窗（`SideWindow`、`SideWindows`，#481）


class FakeSide(SideWindow):
    """一個假子視窗：一顆按鈕，打開時讀一次（記下讀了幾次）。"""

    def __init__(self, parent, db: DatabaseLogin, background) -> None:
        super().__init__(parent, db, background, "假視窗", "300x200")
        self.refreshed = 0
        self.button = ttk.Button(self, text="做事")
        self.button.pack()
        self.buttons.append(self.button)

    def refresh(self) -> None:
        self.refreshed += 1


def fake_side(master, tmp_path: Path, background=HeldBackground) -> FakeSide:
    env = tmp_path / ".env"
    env.write_text("PGDATABASE=spw_fake\nPGUSER=operator1\nPGPASSWORD=fake\n", "utf-8")
    return FakeSide(master, DatabaseLogin(master, env), background)


def test_side_window_title_and_close_protocol(master, tmp_path: Path) -> None:
    side = fake_side(master, tmp_path)

    assert side.title() == "FCN Workflow — 假視窗"
    assert side.protocol("WM_DELETE_WINDOW"), "按右上角叉叉走 close"
    side.close()
    assert side.background.closed and not side.winfo_exists(), "先停背景執行器再關"


def test_side_window_disables_its_buttons_while_working(master, tmp_path: Path) -> None:
    side = fake_side(master, tmp_path)

    side.runner.run_action(lambda settings: 1, "處理中…", lambda r: side.status_var.set(f"完成 {r}"))

    assert side.working and str(side.button.cget("state")) == "disabled"
    assert side.status_var.get() == "處理中…"
    side.background.run_all()
    assert not side.working and str(side.button.cget("state")) == "normal"
    assert side.status_var.get() == "完成 1"


def test_side_window_errors_pop_up_over_it(master, tmp_path: Path, monkeypatch, immediate) -> None:
    side = fake_side(master, tmp_path, immediate)
    parents = []
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: parents.append(k.get("parent")))

    def fail(settings):
        raise DatabaseError("連不上資料庫")

    side.runner.run_action(fail, "處理中…", lambda r: pytest.fail("出錯不交回"))

    assert parents == [side] and side.status_var.get() == "連不上資料庫"


def test_registry_brings_an_open_window_to_the_front(master, tmp_path: Path) -> None:
    windows = SideWindows()
    built: list[FakeSide] = []

    def build() -> FakeSide:
        built.append(fake_side(master, tmp_path))
        return built[-1]

    first = windows.open("假視窗…", build)
    again = windows.open("假視窗…", build)

    assert again is first and len(built) == 1, "已經開著就拉到前面，不重開"
    assert first.refreshed == 1, "只在打開時讀一次"
    assert windows.get("假視窗…") is first and windows.get("別的…") is None
    first.close()
    assert windows.get("假視窗…") is None, "關掉後不算開著"
    reopened = windows.open("假視窗…", build)
    assert reopened is not first and reopened.refreshed == 1, "關掉後再開是新的一個"


def test_closing_the_main_window_closes_open_side_windows(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    side = window.side_windows.open("假視窗…", lambda: fake_side(master, tmp_path))
    closed = fake_side(master, tmp_path)
    window.side_windows.open("關掉的…", lambda: closed)
    closed.close()

    window.close()

    assert not side.winfo_exists() and side.background.closed, "關主視窗一起關"


# ---------------------------------------------------------------- macOS 質感的結果表與捲軸


def test_empty_result_table_says_so(master, tmp_path: Path) -> None:
    tab = new_window(master, tmp_path).daily
    tab.table.fit_columns()

    assert tab.table.placeholder.cget("text") == "沒有資料" and tab.table.placeholder.winfo_manager() == "place"
    tab.add_row(["029199990001", "已匯入", ""], False)
    tab.table.fit_columns()
    assert tab.table.placeholder.winfo_manager() == "", "有列就收起來"


def test_result_table_headings_align_left(master, tmp_path: Path) -> None:
    tab = new_window(master, tmp_path).daily

    assert {str(tab.table.heading(column, "anchor")) for column, _, _ in tab.columns} == {"w"}, "和欄裡的字對齊"


def test_scrollbar_thumb_is_one_ttk_can_move(master, tmp_path: Path) -> None:
    """ttk 捲軸只認名稱以 `.thumb` 結尾的元件當把手；認不得時把手撐滿整條、拖不動也點不動。"""
    new_window(master, tmp_path)  # 套上視窗的樣式
    style = ttk.Style(master)

    for orient in ("Vertical", "Horizontal"):
        (trough, options), *_ = style.layout(f"{orient}.TScrollbar")
        assert trough.endswith(".trough")
        assert [str(name).rsplit(".", 1)[-1] for name, _ in options["children"]] == ["thumb"], orient


def test_scrollbar_hides_its_thumb_when_nothing_to_scroll(master) -> None:
    bar = Scrollbar(master)

    bar.set(0.0, 1.0)
    assert bar.instate(["disabled"]), "全部看得到：把手藏起來"
    bar.set(0.2, 0.6)
    assert not bar.instate(["disabled"])


def test_status_line_rewraps_when_the_card_is_resized(master, tmp_path: Path) -> None:
    """中英夾雜的狀態列依卡片寬度斷行，視窗縮放時重新斷行（#255）。"""
    tab = new_window(master, tmp_path).orders
    label = tab.status_label
    tab.status_var.set("貼上 TDCC，選一份起單建檔，按「讀取」。只讀起單建檔，不會修改它。" * 2)

    def shown_lines() -> list[str]:
        return str(label.tk.call(label, "cget", "-text")).split("\n")

    label.master.event_generate("<Configure>", width=340, when="now")
    narrow = shown_lines()
    assert label.cget("wraplength") == 320, "卡片寬度減左右留白"
    label.master.event_generate("<Configure>", width=1400, when="now")
    wide = shown_lines()

    assert len(narrow) > len(wide) >= 1, "變寬後行數變少"
    assert narrow[0] != "貼上", "不會只在第一個空白斷出很短的一行"
