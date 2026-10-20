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
from fcn_checker.saving import SaveReceipt
from sp_workflow import app
from sp_workflow.documents import DocKind
from sp_workflow.local_settings import FolderMemory
from sp_workflow.preparation import PREPARED, SKIPPED, PreparedDocument, PrepareReceipt, ProductOutcome

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
    """雙擊入口用安裝資料夾的設定開同一個視窗；核對的入口（`fcn-panel`、`launch_checker_panel.pyw`）停在核對頁。"""
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

    window.prepare.on_prepared(prepared_receipt(Path("D:/out/待核對_20261019-103000")))

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


# ---------------------------------------------------------------- 步驟銜接（#76）


def prepared_receipt(batch: Path) -> PrepareReceipt:
    """一檔已準備（TS＋IIS）、一檔跳過的準備結果。"""
    ready = ProductOutcome(
        "147000000001",
        PREPARED,
        documents=(
            PreparedDocument(DocKind.TS, output_name="147000000001_TS.pdf"),
            PreparedDocument(DocKind.IIS, output_name="147000000001_IIS.pdf"),
        ),
    )
    skipped = ProductOutcome("147000000002", SKIPPED, "找不到說明書")
    return PrepareReceipt(batch, batch / "準備清單.xlsx", (ready, skipped))


def new_window(master, tmp_path: Path, session: PanelSession | None = None) -> app.PanelWindow:
    return app.PanelWindow(master, FolderMemory.under(tmp_path), session=session or PanelSession(install_root=tmp_path))


def hint_change_button(hint) -> ttk.Button:
    (button,) = [w for w in hint.winfo_children() if isinstance(w, ttk.Button)]
    return button


def test_prepared_pdfs_are_handed_to_the_check_tab_without_switching(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    window.notebook.select(window.prepare.frame)
    batch = tmp_path / "待核對_20261020-103000"

    window.prepare._done(prepared_receipt(batch))

    assert window.check.pdf_paths == (batch / "147000000001_TS.pdf", batch / "147000000001_IIS.pdf"), "只帶已準備的"
    assert window.check.hint.text.get() == (
        f"已帶入「3 準備」的批次（2 份 PDF），選好參考條件表後按「載入／重新載入預覽」：\n{batch}"
    )
    assert window.notebook.select() == str(window.prepare.frame), "不自動切頁"
    assert window.session.preview is None and not window.check.working, "不自動載入預覽或核對"
    assert window.prepare.next_button.winfo_manager() == "pack"

    window.prepare.next_button.invoke()

    assert window.notebook.select() == str(window.check)


def test_choosing_other_pdfs_hides_the_hint(master, tmp_path: Path, monkeypatch) -> None:
    window = new_window(master, tmp_path)
    window.prepare._done(prepared_receipt(tmp_path / "待核對_1"))
    other = tmp_path / "其他" / "147000000009_TS.pdf"
    monkeypatch.setattr(fcn_checker.panel.filedialog, "askopenfilenames", lambda **kwargs: (str(other),))

    hint_change_button(window.check.hint).invoke()

    assert window.check.pdf_paths == (other,) and not window.check.hint.shown


@pytest.mark.parametrize("answer", [False, True])
def test_an_unsaved_check_result_is_kept_unless_the_operator_agrees(master, tmp_path, monkeypatch, answer) -> None:
    window = new_window(master, tmp_path, UnsavedSession(install_root=tmp_path))
    asked = []
    monkeypatch.setattr(fcn_checker.panel.messagebox, "askyesno", lambda *a, **k: asked.append(a[1]) or answer)

    window.prepare._done(prepared_receipt(tmp_path / "待核對_1"))

    assert len(asked) == 1 and "沒存檔的核對結果" in asked[0]
    assert bool(window.check.pdf_paths) is answer and window.check.hint.shown is answer
    assert window.prepare.next_button.winfo_manager() == ("pack" if answer else ""), "沒帶入就沒有下一步"


def test_a_busy_check_tab_does_not_take_the_batch(master, tmp_path: Path, monkeypatch) -> None:
    window = new_window(master, tmp_path)
    window.check.check_pending = Future()  # 核對還在背景跑
    told = []
    monkeypatch.setattr(fcn_checker.panel.messagebox, "showinfo", lambda *a, **k: told.append(a[1]))

    window.prepare._done(prepared_receipt(tmp_path / "待核對_1"))

    assert len(told) == 1 and "正在處理" in told[0]
    assert window.check.pdf_paths == () and not window.check.hint.shown
    assert window.prepare.next_button.winfo_manager() == ""


class SavedSession(PanelSession):
    receipt = SaveReceipt(output=Path("D:/reports/參考條件表_核對結果_20261020-110000.xlsx"))


def test_saved_check_result_is_handed_to_the_return_tab(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path, SavedSession(install_root=tmp_path))
    batch = tmp_path / "待核對_1"
    batch.mkdir()
    (batch / "準備清單.xlsx").write_bytes(b"")
    window.check.receive_pdfs((batch / "147000000001_TS.pdf", batch / "147000000001_IIS.pdf"), "帶入")
    window.notebook.select(window.check)
    result = SavedSession.receipt.output

    window.check._show_receipt(SavedSession.receipt)
    window.check._busy(False)

    assert (window.returning.batch_var.get(), window.returning.result_var.get()) == (str(batch), str(result))
    assert window.returning.hint.text.get() == f"已帶入「4 核對」剛存的核對結果檔：\n{result}\n批次資料夾：{batch}"
    assert window.notebook.select() == str(window.check), "不自動切頁"
    assert window.check.next_button.winfo_manager() == "pack", "存過檔才出現下一步"
    assert window.check.status_label.cget("style") == "Done.TLabel", "存過檔，狀態列變成綠色結果條"

    window.check.next_button.invoke()

    assert window.notebook.select() == str(window.returning.frame)


def test_next_step_after_checking_waits_for_a_saved_result(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)

    window.check._busy(False)

    assert window.check.next_button.winfo_manager() == "", "還沒存檔，不顯示下一步：放回"


@pytest.mark.parametrize("where", ["several folders", "not a batch folder"])
def test_pdfs_outside_one_batch_folder_leave_the_batch_folder_alone(master, tmp_path: Path, where) -> None:
    """核對的 PDF 不在同一個資料夾，或那個資料夾沒有準備清單（例如自己從商品資料夾選的），就不動批次資料夾。"""
    window = new_window(master, tmp_path)
    window.returning.batch_var.set("D:/out/待核對_舊")
    folders = ("a", "b") if where == "several folders" else ("a", "a")
    window.check.receive_pdfs((tmp_path / folders[0] / "1_TS.pdf", tmp_path / folders[1] / "1_IIS.pdf"), "帶入")
    result = tmp_path / "核對結果.xlsx"

    window.check.on_saved(result)

    assert window.returning.batch_var.get() == "D:/out/待核對_舊" and window.returning.result_var.get() == str(result)
    assert window.returning.hint.text.get().endswith(
        "批次資料夾沒有帶入（核對的 PDF 不在同一個批次資料夾），請自己確認。"
    )


@pytest.mark.parametrize("change", ["改選", "batch"])
def test_changing_what_was_handed_to_the_return_tab_hides_the_hint(master, tmp_path, monkeypatch, change) -> None:
    window = new_window(master, tmp_path)
    window.returning.receive(tmp_path / "核對結果.xlsx", tmp_path / "待核對_1")
    assert window.returning.hint.shown
    if change == "改選":
        monkeypatch.setattr(app.filedialog, "askopenfilename", lambda **kwargs: str(tmp_path / "另一份.xlsx"))
        hint_change_button(window.returning.hint).invoke()
        assert window.returning.result_var.get() == str(tmp_path / "另一份.xlsx")
    else:
        window.returning.batch_var.set(str(tmp_path / "待核對_2"))

    assert not window.returning.hint.shown


# ---------------------------------------------------------------- 蘋果 × 麥肯錫風格範本（#83）


def test_sidebar_switches_steps_and_follows_the_next_buttons(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    steps = window.sidebar.steps

    assert [step.cget("text").split()[0] for step in steps.values()] == ["1", "2", "3", "4", "5", "6"]
    steps[str(window.prepare.frame)].invoke()
    master.update()
    assert window.notebook.select() == str(window.prepare.frame)
    assert steps[str(window.prepare.frame)].cget("style") == "CurrentStep.TButton"

    window.prepare._done(prepared_receipt(tmp_path / "待核對_1"))
    window.prepare.next_button.invoke()
    master.update()

    current = [tab for tab, step in steps.items() if step.cget("style") == "CurrentStep.TButton"]
    assert current == [str(window.check)], "「下一步」切頁時步驟欄跟著換"


def test_page_headers_show_their_step(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)

    assert window.prepare.header.step.get() == "步驟 3／6"
    assert window.check.header.step.get() == "步驟 4／6"


def test_pasted_tdccs_are_counted_once_each(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)

    window.prepare.tdcc_text.insert("1.0", "147000000001\n147000000002 147000000001、147000000003")
    master.update()

    assert window.prepare.count_var.get() == "已貼 3 筆", "重複的只算一次，分隔方式同準備"


def test_prepare_shows_key_figures_when_done(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)

    window.prepare._done(prepared_receipt(tmp_path / "待核對_1"))

    assert window.prepare.kpis.values == (
        ("已準備（檔）", 1, False),
        ("跳過（檔）", 1, True),
        ("Word 轉檔（份）", 0, False),
    )
    assert window.prepare.status_label.cget("style") == "Done.TLabel", "做完了，狀態列變成綠色結果條"


def test_check_key_figures_hide_when_results_are_cleared(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    window.check.kpis.show((("通過", 2, False),))

    window.check._clear_results()

    assert window.check.kpis.values == ()
    assert window.check.status_label.cget("style") == "TLabel", "沒有存過檔的結果，狀態列不是綠色"
