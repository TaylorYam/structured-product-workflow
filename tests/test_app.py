"""視窗只做冒煙測試：建得起來、分頁順序、維護選單、關閉提醒、帶入與記住資料夾。沒有顯示器（CI）時略過要開視窗的測試。"""

from __future__ import annotations

import runpy
import tkinter as tk
from concurrent.futures import Future
from pathlib import Path
from tkinter import messagebox, ttk

import pytest

import fcn_checker.panel
from fcn_checker.panel import parse_args
from fcn_checker.panel_workflow import PanelSession
from sp_workflow import app
from sp_workflow.local_settings import FolderMemory

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def tk_root():
    try:
        root = tk.Tk()
    except tk.TclError as error:
        pytest.skip(f"沒有顯示器：{error}")
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def master(tk_root):
    """每個測試一個新的視窗；共用同一個 Tk，因為在 Windows 上一再重建 Tk 偶爾會找不到 tk.tcl。"""
    window = tk.Toplevel(tk_root)
    window.withdraw()
    yield window
    if window.winfo_exists():  # 關閉的測試已經關掉視窗
        window.destroy()


def tab_titles(window: app.PanelWindow) -> list[str]:
    return [window.notebook.tab(tab, "text") for tab in window.notebook.tabs()]


def texts(widget: tk.Misc, kind: type[tk.Misc]) -> list[str]:
    """畫面上某一種元件（按鈕、標籤…）的文字，包含所有子元件。"""
    found = [widget.cget("text")] if isinstance(widget, kind) else []
    for child in widget.winfo_children():
        found += texts(child, kind)
    return found


def test_tabs_follow_the_daily_order(master, tmp_path: Path) -> None:
    window = app.PanelWindow(master, FolderMemory.under(tmp_path), session=PanelSession(install_root=tmp_path))

    assert master.title() == "FCN Workflow"
    assert tab_titles(window) == ["1 Order 條件", "2 每日匯入", "3 準備", "4 核對", "5 放回", "6 寄信草稿"]
    assert window.notebook.select() == str(window.orders.frame)


def test_checker_entry_starts_on_the_check_tab(master, tmp_path: Path) -> None:
    window = app.PanelWindow(
        master, FolderMemory.under(tmp_path), session=PanelSession(install_root=tmp_path), start_on_check=True
    )

    assert window.notebook.select() == str(window.check)


def test_maintenance_actions_live_in_the_menu_only(master, tmp_path: Path) -> None:
    window = app.PanelWindow(master, FolderMemory.under(tmp_path), session=PanelSession(install_root=tmp_path))
    menu = window.maintenance

    labels = [menu.entrycget(i, "label") for i in range(menu.index(tk.END) + 1) if menu.type(i) == "command"]
    assert labels == ["審查通過日期…", "匯出回報包…", "使用的設定檔…"]
    assert not {"審查通過日期…", "匯出回報包…"} & set(texts(window.check, ttk.Button)), "核對頁畫面上不再有維護功能"
    window._refresh_maintenance()
    assert str(menu.entrycget(0, "state")) == "normal"
    assert str(menu.entrycget(1, "state")) == "disabled", "還沒核對，沒有可以匯出的回報包"


class UnsavedSession(PanelSession):
    has_unsaved_result = True


@pytest.mark.parametrize("answer", [False, True])
def test_closing_with_an_unsaved_check_result_asks_first(master, tmp_path: Path, monkeypatch, answer) -> None:
    window = app.PanelWindow(master, FolderMemory.under(tmp_path), session=UnsavedSession(install_root=tmp_path))
    asked = []
    monkeypatch.setattr(app.messagebox, "askyesno", lambda *a, **k: asked.append(a[1]) or answer)

    window.close()

    assert len(asked) == 1 and "核對結果還沒儲存" in asked[0]
    assert window.check.closed is answer
    if not answer:
        assert master.winfo_exists() and window.notebook.select() == str(window.check), "不關閉，切到核對頁去存檔"


def test_closing_while_the_check_tab_is_working_asks_first(master, tmp_path: Path, monkeypatch) -> None:
    window = app.PanelWindow(master, FolderMemory.under(tmp_path), session=PanelSession(install_root=tmp_path))
    window.check.check_pending = Future()  # 核對還在背景跑，還沒有結果
    asked = []
    monkeypatch.setattr(app.messagebox, "askyesno", lambda *a, **k: asked.append(a[1]) or False)

    window.close()

    assert len(asked) == 1 and "核對頁還在處理" in asked[0]
    assert not window.check.closed


def test_closing_without_an_unsaved_result_does_not_ask(master, tmp_path: Path, monkeypatch) -> None:
    window = app.PanelWindow(master, FolderMemory.under(tmp_path), session=PanelSession(install_root=tmp_path))
    monkeypatch.setattr(app.messagebox, "askyesno", lambda *a, **k: pytest.fail("不該提醒"))

    window.close()

    assert window.check.closed


class FakeMaster:
    """WindowSize 只用到的視窗方法；螢幕 1920×1080、縮放 100%。"""

    def __init__(self, state: str = "normal", size: tuple[int, int] = (1000, 700)) -> None:
        self._state, self.size, self.calls = state, size, []

    def winfo_screenwidth(self) -> int:
        return 1920

    def winfo_screenheight(self) -> int:
        return 1080

    def winfo_width(self) -> int:
        return self.size[0]

    def winfo_height(self) -> int:
        return self.size[1]

    def minsize(self, width: int, height: int) -> None:
        self.calls.append(("minsize", width, height))

    def geometry(self, value: str) -> None:
        self.calls.append(("geometry", value))

    def state(self, value: str | None = None) -> str | None:
        if value is None:
            return self._state
        self.calls.append(("state", value))
        return None


def same_pixels(value: float) -> int:
    return round(value)


def test_window_opens_maximized_until_the_operator_resizes_it(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(app.sys, "platform", "win32")
    size = app.WindowSize(tmp_path / ".local" / app.WINDOW_FILE)

    first = FakeMaster()
    size.restore(first, same_pixels)
    assert first.calls == [("minsize", 820, 720), ("geometry", "1180x820"), ("state", "zoomed")], "第一次開啟最大化"

    size.remember(FakeMaster("normal", (1300, 900)))
    again = FakeMaster()
    size.restore(again, same_pixels)
    assert ("geometry", "1300x900") in again.calls and ("state", "zoomed") not in again.calls

    size.remember(FakeMaster("zoomed", (1920, 1040)))
    zoomed = FakeMaster()
    size.restore(zoomed, same_pixels)
    assert zoomed.calls[1:] == [("geometry", "1300x900"), ("state", "zoomed")], "還原成一般大小時用上次的大小"

    size.remember(FakeMaster("iconic"))
    reopened = app.WindowSize(tmp_path / ".local" / app.WINDOW_FILE)
    assert reopened.record.load() == {"state": "zoomed", "size": "1300x900"}, "縮到工作列時關閉不改紀錄"


def test_remembered_size_never_exceeds_the_screen(tmp_path: Path) -> None:
    record = tmp_path / app.WINDOW_FILE
    FolderMemory(record).remember(state="normal", size="2560x1440")
    master = FakeMaster()

    app.WindowSize(record).restore(master, same_pixels)

    assert ("geometry", "1920x1080") in master.calls


def test_both_entry_points_open_the_merged_window(monkeypatch) -> None:
    """雙擊入口用安裝資料夾的設定開同一個視窗；核對的入口（`fcn-panel`、核對捷徑）停在核對頁。"""
    opened = []
    monkeypatch.setattr(app, "main", lambda argv, **kwargs: opened.append((argv, kwargs)))
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: pytest.fail(f"啟動失敗：{a}"))
    monkeypatch.syspath_prepend(str(ROOT))

    runpy.run_path(str(ROOT / "launch_panel.pyw"), run_name="__main__")
    fcn_checker.panel.main(["--install-root", str(ROOT)])

    (workflow_argv, workflow_kwargs), (checker_argv, checker_kwargs) = opened
    args = parse_args(workflow_argv)
    assert (args.install_root, args.config_dir) == (ROOT, ROOT / "config")
    assert workflow_kwargs == {}
    assert checker_argv == ["--install-root", str(ROOT)] and checker_kwargs == {"start_on_check": True}


def test_window_builds_with_remembered_folders(master, tmp_path: Path) -> None:
    memory = FolderMemory.under(tmp_path)
    memory.remember(root="R:/FCN", out="D:/out", batch="D:/out/待核對_1")

    window = app.PanelWindow(master, memory)

    assert (window.root_folder.var.get(), window.prepare.out_var.get()) == ("R:/FCN", "D:/out")
    assert str(window.prepare.open_button.cget("state")) == "disabled"
    assert window.returning.batch_var.get() == "D:/out/待核對_1"


def test_prepared_batch_is_filled_into_the_return_tab(master, tmp_path: Path) -> None:
    window = app.PanelWindow(master, FolderMemory.under(tmp_path))

    window.prepare.on_prepared(Path("D:/out/待核對_20261019-103000"))

    assert window.returning.batch_var.get() == str(Path("D:/out/待核對_20261019-103000"))


def test_install_root_decides_where_folders_are_remembered(tmp_path: Path) -> None:
    assert app.folder_memory(["--install-root", str(tmp_path)]).path == tmp_path / ".local" / "prepare_folders.json"
    assert app.folder_memory([]).path.name == "prepare_folders.json"


def test_daily_import_tab_remembers_files_and_env_path(master, tmp_path: Path) -> None:
    memory = FolderMemory.under(tmp_path)
    memory.remember(daily="D:/每日更新.xlsx")

    window = app.PanelWindow(master, memory, tmp_path / ".env")

    assert window.daily.daily_var.get() == "D:/每日更新.xlsx"
    assert window.daily.db.env_path == tmp_path / ".env" and window.mail.db is window.daily.db


def test_mail_draft_tab_reuses_the_root_and_remembers_its_output(master, tmp_path: Path) -> None:
    memory = FolderMemory.under(tmp_path)
    memory.remember(root="R:/FCN", mail_out="D:/寄信")

    window = app.PanelWindow(master, memory, tmp_path / ".env")

    assert window.mail.root_folder is window.root_folder and window.mail.out_var.get() == "D:/寄信"


def test_order_terms_tab_reuses_the_root_and_remembers_its_output(master, tmp_path: Path) -> None:
    memory = FolderMemory.under(tmp_path)
    memory.remember(root="R:/FCN", order_out="D:/Order")

    window = app.PanelWindow(master, memory, tmp_path / ".env")

    assert window.orders.root_folder is window.root_folder and window.orders.out_var.get() == "D:/Order"
    assert str(window.orders.open_button.cget("state")) == "disabled"
    assert str(window.orders.export_button.cget("state")) == "disabled"  # 讀取完、有問題才可按


# ---------------------------------------------------------------- 共用根目錄（#75）


def test_root_is_set_once_at_the_top_for_three_tabs(master, tmp_path: Path, monkeypatch) -> None:
    memory = FolderMemory.under(tmp_path)
    window = app.PanelWindow(master, memory, session=PanelSession(install_root=tmp_path))
    tabs = (window.orders, window.prepare, window.mail)
    assert all(tab.root_folder is window.root_folder for tab in tabs)
    assert not any("根目錄" in texts(tab.frame, ttk.Label) for tab in tabs), "三頁不再各有根目錄欄位"
    monkeypatch.setattr(app.filedialog, "askdirectory", lambda **kwargs: "R:/FCN")

    window.root_folder.change()

    assert window.root_folder.var.get() == str(Path("R:/FCN"))
    assert app.RootFolder(memory).var.get() == str(Path("R:/FCN")), "重開視窗仍是這個根目錄"


def test_root_remembered_before_the_upgrade_is_kept(master, tmp_path: Path) -> None:
    """升級前三頁共用 prepare_folders.json 的 root，升級後直接沿用，不用重選。"""
    record = tmp_path / ".local" / "prepare_folders.json"
    record.parent.mkdir()
    record.write_text('{"root": "R:/FCN", "out": "D:/out", "mail_out": "D:/寄信"}', encoding="utf-8")

    window = app.PanelWindow(master, FolderMemory.under(tmp_path), session=PanelSession(install_root=tmp_path))

    assert window.root_folder.var.get() == "R:/FCN"


@pytest.mark.parametrize("tab_name", ["orders", "prepare", "mail"])
def test_starting_without_a_root_asks_to_set_it_first(master, tmp_path: Path, monkeypatch, tab_name) -> None:
    window = app.PanelWindow(master, FolderMemory.under(tmp_path), session=PanelSession(install_root=tmp_path))
    tab = getattr(window, tab_name)
    tab.out_var.set("D:/out")
    errors = []
    monkeypatch.setattr(app.messagebox, "showerror", lambda title, message, **kwargs: errors.append(message))
    monkeypatch.setattr(tab, "run_in_background", lambda *args, **kwargs: pytest.fail("不該開始執行"))

    tab.start()

    assert errors == ["請先在視窗最上方按「變更…」設定根目錄。"]


def test_each_tab_still_remembers_its_own_output(master, tmp_path: Path, monkeypatch) -> None:
    memory = FolderMemory.under(tmp_path)
    memory.remember(root="R:/FCN")
    window = app.PanelWindow(master, memory, session=PanelSession(install_root=tmp_path))
    monkeypatch.setattr(window.mail.db, "settings", lambda: object())  # 寄信草稿先登入資料庫，這裡不連線
    monkeypatch.setattr(app, "mail_template_from_env", lambda env_path: None)
    for tab, out in ((window.orders, "D:/Order"), (window.prepare, "D:/out"), (window.mail, "D:/寄信")):
        monkeypatch.setattr(tab, "run_in_background", lambda *args, **kwargs: None)
        tab.out_var.set(out)
        tab.start()

    assert memory.load() == {
        "root": "R:/FCN",
        "order_out": str(Path("D:/Order")),
        "out": str(Path("D:/out")),
        "mail_out": str(Path("D:/寄信")),
    }, "按開始只記各頁的輸出位置，根目錄不變"
