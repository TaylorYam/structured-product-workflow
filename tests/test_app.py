"""視窗只做冒煙測試：建得起來、分頁順序、維護選單、關閉提醒、帶入與記住資料夾。沒有顯示器（CI）時略過要開視窗的測試。"""

from __future__ import annotations

import datetime as dt
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
from order_synth import CONFIG, missing_row, order_row
from sp_workflow import app
from sp_workflow.check_terms import DONE, TO_CONFIRM, WAIT_PRICES, CalcLine, CalcReceipt, Difference
from sp_workflow.daily_import import NEW_TRADES, PRICE_CONFLICT, ImportLine, ImportReceipt, PriceSummary
from sp_workflow.documents import DocKind
from sp_workflow.local_settings import FolderMemory
from sp_workflow.mail_drafts import DRAFTED, NOT_NEEDED, DraftOutcome, DraftReceipt
from sp_workflow.order_import import NEW as ORDER_NEW
from sp_workflow.order_import import SKIPPED as ORDER_SKIPPED
from sp_workflow.order_import import OrderImportLine, OrderImportReceipt, drafts_from
from sp_workflow.order_terms import PENDING as ORDER_PENDING
from sp_workflow.order_terms import OrderRow
from sp_workflow.preparation import PREPARED, SKIPPED, PreparedDocument, PrepareReceipt, ProductOutcome
from sp_workflow.returning import NOT_RETURNED, RETURNED, ReturnOutcome, ReturnReceipt

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
    assert tab_titles(window) == ["1 每日匯入", "2 起單資料", "3 準備", "4 核對", "5 放回", "6 寄信草稿"]
    assert window.notebook.select() == str(window.daily.frame)


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


def test_order_terms_tab_remembers_its_sheet(master, tmp_path: Path) -> None:
    memory = FolderMemory.under(tmp_path)
    memory.remember(root="R:/FCN", order_sheet="D:/ELN/起單建檔_20261015.xlsm")

    window = app.PanelWindow(master, memory, tmp_path / ".env")

    assert window.orders.sheet_var.get() == "D:/ELN/起單建檔_20261015.xlsm"
    assert not hasattr(window.orders, "root_folder"), "起單資料頁不用根目錄（#97）"
    assert texts(window.orders.buttons, ttk.Button) == ["讀取", "匯入資料庫", "放行差異…"]
    assert str(window.orders.import_button.cget("state")) == "disabled"


def test_choosing_a_sheet_opens_the_last_folder_or_the_daily_sheet_folder(master, tmp_path: Path, monkeypatch) -> None:
    window = new_window(master, tmp_path)
    opened = []
    monkeypatch.setattr(app.filedialog, "askopenfilename", lambda **kwargs: opened.append(kwargs["initialdir"]) or "")
    monkeypatch.setattr(app, "ORDER_SHEET_DIR", tmp_path)

    window.orders._ask_sheet()
    (tmp_path / "202609").mkdir()
    window.orders.sheet_var.set(str(tmp_path / "202609" / "起單建檔_20260930.xlsm"))
    window.orders._ask_sheet()

    assert opened == [str(tmp_path), str(tmp_path / "202609")]
    assert "order_sheet" not in window.orders.memory.load(), "按取消不記"

    chosen = str(tmp_path / "202609" / "起單建檔_20260929.xlsm")
    monkeypatch.setattr(app.filedialog, "askopenfilename", lambda **kwargs: chosen)
    window.orders._ask_sheet()

    assert window.orders.memory.load()["order_sheet"] == chosen, "選了就記，不用等按讀取"


def test_reading_without_a_sheet_asks_to_choose_it_first(master, tmp_path: Path, monkeypatch) -> None:
    window = new_window(master, tmp_path)
    window.orders.tdcc_text.insert("1.0", "029199990001")
    errors = []
    monkeypatch.setattr(app.messagebox, "showerror", lambda title, message, **kwargs: errors.append(message))
    monkeypatch.setattr(window.orders, "run_in_background", lambda *args, **kwargs: pytest.fail("不該開始執行"))

    window.orders.start()

    assert errors == ["請先選擇起單建檔。"]


# ---------------------------------------------------------------- 共用根目錄（#75）


def test_root_is_set_once_at_the_top_for_two_tabs(master, tmp_path: Path, monkeypatch) -> None:
    memory = FolderMemory.under(tmp_path)
    window = app.PanelWindow(master, memory, session=PanelSession(install_root=tmp_path))
    tabs = (window.prepare, window.mail)
    assert all(tab.root_folder is window.root_folder for tab in tabs)
    assert not any("根目錄" in texts(tab.frame, ttk.Label) for tab in tabs), "兩頁不再各有根目錄欄位"
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


@pytest.mark.parametrize("tab_name", ["prepare", "mail"])
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
    monkeypatch.setattr(window.mail.db, "settings", lambda **kwargs: object())  # 寄信草稿先登入資料庫，這裡不連線
    monkeypatch.setattr(app, "mail_template_from_env", lambda env_path: None)
    for tab, out in ((window.prepare, "D:/out"), (window.mail, "D:/寄信")):
        monkeypatch.setattr(tab, "run_in_background", lambda *args, **kwargs: None)
        tab.out_var.set(out)
        tab.start()
    monkeypatch.setattr(window.orders, "run_in_background", lambda *args, **kwargs: None)
    window.orders.sheet_var.set("D:/ELN/起單建檔_20261015.xlsm")
    window.orders.start()

    assert memory.load() == {
        "root": "R:/FCN",
        "order_sheet": str(Path("D:/ELN/起單建檔_20261015.xlsm")),
        "out": str(Path("D:/out")),
        "mail_out": str(Path("D:/寄信")),
    }, "按開始只記各頁的輸出位置與起單建檔，根目錄不變"


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


# ---------------------------------------------------------------- 範本套到其他四頁（#85）


def test_every_page_header_shows_its_step(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    pages = (window.daily, window.orders, window.prepare, window.check, window.returning, window.mail)

    assert [page.header.step.get() for page in pages] == [f"步驟 {n}／6" for n in range(1, 7)]


@pytest.mark.parametrize("tab_name", ["orders", "mail"])
def test_order_and_mail_pages_count_pasted_tdccs(master, tmp_path: Path, tab_name) -> None:
    tab = getattr(new_window(master, tmp_path), tab_name)

    tab.tdcc_text.insert("1.0", "147000000001\n147000000002\n147000000001")
    master.update()

    assert tab.count_var.get() == "已貼 2 筆"


def test_return_page_shows_key_figures_when_done(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    outcomes = (
        ReturnOutcome("147000000001", RETURNED, RETURNED),
        ReturnOutcome("147000000002", NOT_RETURNED, NOT_RETURNED, "核對結果檔沒有這個商品"),
    )

    window.returning._done(ReturnReceipt(tmp_path / "放回清單.xlsx", outcomes))

    assert window.returning.kpis.values == (
        ("已放回或已在（檔）", 1, False),
        ("含待確認（檔）", 0, False),
        ("不放回（檔）", 1, True),
    )
    assert window.returning.status_label.cget("style") == "Done.TLabel"


def test_daily_import_page_shows_key_figures_and_restarts_clean(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    lines = (ImportLine("147000000001", NEW_TRADES), ImportLine("", PRICE_CONFLICT, "AAA UW：值不同"))
    receipt = daily_receipt(tmp_path, lines=lines, prices=PriceSummary(dt.date(2026, 10, 16), 510, 0, ("AAA UW",)))

    window.daily._done(receipt)
    assert window.daily.kpis.values == (
        ("正常（項）", 1, False),
        ("要注意（項）", 1, True),
        ("股價（筆）", 510, False),
        ("核對條件待確認（檔）", 0, False),
    )
    assert (
        "股價：取價日 2026-10-16，510 筆；值不一樣的重複代碼 1 個，價格沒有存：AAA UW。"
        in window.daily.status_var.get()
    )

    window.daily.restart()
    assert window.daily.kpis.values == () and window.daily.status_label.cget("style") == "Card.TLabel"


def test_order_and_mail_pages_show_key_figures_when_done(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    rows = (order_row("029199990001"), missing_row("147000000002"))
    outcomes = (DraftOutcome("147000000001", DRAFTED), DraftOutcome("147000000002", NOT_NEEDED))

    window.orders._done(read_result(*rows))
    window.mail._done(DraftReceipt(tmp_path / "寄信草稿清單.xlsx", outcomes, last_import=None))

    assert window.orders.kpis.values == (("可匯入（檔）", 1, False), ("要處理（檔）", 1, True))
    assert window.mail.kpis.values == (("已建草稿（檔）", 1, False), ("沒有建立（檔）", 1, False)), "不需寄送不算問題"
    assert window.orders.status_label.cget("style") == window.mail.status_label.cget("style") == "Done.TLabel"


def test_a_new_check_result_clears_the_last_return(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    window.returning._done(
        ReturnReceipt(tmp_path / "放回清單.xlsx", (ReturnOutcome("147000000001", RETURNED, RETURNED),))
    )

    window.returning.receive(tmp_path / "核對結果.xlsx", None)

    assert window.returning.kpis.values == () and window.returning.status_label.cget("style") == "Card.TLabel"


# ---------------------------------------------------------------- 每日匯入在第 1 頁，帶入 起單資料（#90）


def daily_receipt(
    tmp_path: Path, tdccs: tuple[str, ...] = (), fcn_rows: int | None = None, *, lines=(), prices=None, calcs=None
) -> app._DailyResult:
    """每日匯入的結果；`calcs` 是重算核對條件的結果（CalcLine），沒給時是空的重算。"""
    receipt = ImportReceipt(
        run_id=7,
        lines=lines,
        people=3,
        skipped_trade_rows=0,
        cancelled_trade_rows=0,
        error_book_path=tmp_path / "每日匯入錯誤.xlsx",
        prices=prices or PriceSummary(dt.date(2026, 10, 16), 1, 0, ()),
        fcn_trade_rows=len(tdccs) if fcn_rows is None else fcn_rows,
        fcn_tdccs=tdccs,
    )
    return app._DailyResult(receipt, app._Recalc(CalcReceipt(tuple(calcs or ())), CONFIG.calendar))


def test_reimporting_a_price_date_says_how_many_prices_were_replaced(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)

    window.daily._done(daily_receipt(tmp_path, prices=PriceSummary(dt.date(2026, 10, 16), 3, 2, ())))

    assert "股價：取價日 2026-10-16，3 筆（取代了這一天原本的 2 筆）。" in window.daily.status_var.get()


def read_result(*rows: OrderRow) -> app._ReadResult:
    """讀完起單建檔的結果（沒有連資料庫）。"""
    return app._ReadResult(rows, drafts_from(rows), CONFIG)


def pasted(tab) -> str:
    return tab.tdcc_text.get("1.0", tk.END).strip()


def test_daily_import_hands_fcn_tdccs_to_the_order_tab_without_switching(master, tmp_path: Path, monkeypatch) -> None:
    window = new_window(master, tmp_path)
    monkeypatch.setattr(window.orders, "run_in_background", lambda *a, **k: pytest.fail("不該自動讀取"))

    window.daily._done(daily_receipt(tmp_path, ("147000000002", "147000000001")))
    master.update()  # 帶入時改了 TDCC 欄位，處理完那次 <<Modified>> 提示條仍在

    assert pasted(window.orders) == "147000000002\n147000000001" and window.orders.count_var.get() == "已貼 2 筆"
    assert window.orders.hint.text.get() == "已帶入每日匯入的 FCN TDCC 2 筆，選好起單建檔後按「讀取」。"
    assert window.daily.status_var.get().endswith("已把 FCN TDCC 2 筆帶到「2 起單資料」。")
    assert window.notebook.select() == str(window.daily.frame), "不自動切頁"
    assert window.daily.next_button.winfo_manager() == "pack"

    window.daily.next_button.invoke()

    assert window.notebook.select() == str(window.orders.frame)


@pytest.mark.parametrize(
    ("receipt_args", "message"),
    [
        ((), "這份交易明細沒有 FCN，起單資料頁沒有帶入。"),
        (((), 2), "這份交易明細的 FCN 都已取消或 TDCC 不是 12 碼，起單資料頁沒有帶入。"),
    ],
)
def test_nothing_is_handed_over_without_fcn_tdccs(master, tmp_path: Path, receipt_args, message) -> None:
    window = new_window(master, tmp_path)
    window.orders.tdcc_text.insert("1.0", "147000000009")

    window.daily._done(daily_receipt(tmp_path, *receipt_args))

    assert window.daily.status_var.get().endswith(message)
    assert pasted(window.orders) == "147000000009" and not window.orders.hint.shown
    assert window.daily.next_button.winfo_manager() == ""


def test_a_rejected_daily_file_hands_nothing_over(master, tmp_path: Path, monkeypatch) -> None:
    window = new_window(master, tmp_path)
    monkeypatch.setattr(app.messagebox, "showerror", lambda *a, **k: None)

    window.daily._done(app.DailyImportError("「交易明細」的欄名和來源表不同，整份不匯入"))

    assert pasted(window.orders) == "" and not window.orders.hint.shown
    assert window.daily.next_button.winfo_manager() == ""


@pytest.mark.parametrize("answer", [False, True])
def test_different_tdccs_on_the_order_tab_are_replaced_only_if_the_operator_agrees(
    master, tmp_path: Path, monkeypatch, answer
) -> None:
    window = new_window(master, tmp_path)
    window.orders.tdcc_text.insert("1.0", "147000000009")
    asked = []
    monkeypatch.setattr(app.messagebox, "askyesno", lambda *a, **k: asked.append(a[1]) or answer)

    window.daily._done(daily_receipt(tmp_path, ("147000000001",)))

    assert len(asked) == 1 and "已經有 1 筆 TDCC" in asked[0]
    assert pasted(window.orders) == ("147000000001" if answer else "147000000009")
    assert window.orders.hint.shown is answer
    assert window.daily.next_button.winfo_manager() == ("pack" if answer else ""), "沒帶入就沒有下一步"
    if not answer:
        assert window.daily.status_var.get().endswith("起單資料頁沒有帶入（正在處理，或保留原本的 TDCC）。")


def test_the_same_tdccs_are_handed_over_again_without_asking(master, tmp_path: Path, monkeypatch) -> None:
    window = new_window(master, tmp_path)
    window.daily._done(daily_receipt(tmp_path, ("147000000001",)))
    monkeypatch.setattr(app.messagebox, "askyesno", lambda *a, **k: pytest.fail("一樣的 TDCC 不用問"))

    window.daily._done(daily_receipt(tmp_path, ("147000000001",)))

    assert window.orders.hint.shown


def test_change_on_the_order_hint_clears_the_handed_over_tdccs(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    window.daily._done(daily_receipt(tmp_path, ("147000000001", "147000000002")))

    hint_change_button(window.orders.hint).invoke()

    assert pasted(window.orders) == "" and window.orders.count_var.get() == "已貼 0 筆"
    assert not window.orders.hint.shown


def test_editing_the_handed_over_tdccs_hides_the_hint(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    window.daily._done(daily_receipt(tmp_path, ("147000000001",)))
    master.update()
    assert window.orders.hint.shown, "帶入本身不算自己改"

    window.orders.tdcc_text.insert(tk.END, "\n147000000003")
    master.update()

    assert not window.orders.hint.shown and window.orders.count_var.get() == "已貼 2 筆"


@pytest.mark.parametrize("busy", ["reading", "importing"])
def test_a_busy_order_tab_does_not_take_the_tdccs(master, tmp_path: Path, monkeypatch, busy) -> None:
    """#93：正在讀取或匯入時不帶入，免得舊的結果蓋在新的 TDCC 底下。"""
    window = new_window(master, tmp_path)
    monkeypatch.setattr(window.orders, "run_in_background", lambda *a, **k: None)
    window.orders.sheet_var.set(str(tmp_path / "起單建檔_20261015.xlsm"))
    window.orders.tdcc_text.insert("1.0", "147000000009")
    if busy == "reading":
        window.orders.start()
    else:
        window.orders._done(read_result(order_row("147000000009")))
        monkeypatch.setattr(window.orders.db, "settings", lambda **k: object())
        window.orders.import_now()
    told = []
    monkeypatch.setattr(app.messagebox, "showinfo", lambda *a, **k: told.append(a[1]))

    window.daily._done(daily_receipt(tmp_path, ("147000000001",)))

    assert len(told) == 1 and "正在處理" in told[0]
    assert pasted(window.orders) == "147000000009" and not window.orders.hint.shown
    assert window.daily.next_button.winfo_manager() == ""


def test_handing_over_clears_the_last_order_results(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    window.orders._done(read_result(OrderRow("147000000009", ORDER_PENDING)))
    window.orders.tdcc_text.delete("1.0", tk.END)

    window.daily._done(daily_receipt(tmp_path, ("147000000001",)))

    orders = window.orders
    assert orders.table.get_children() == () and orders.kpis.values == ()
    assert orders.status_var.get() == orders.READY_TEXT and orders.status_label.cget("style") == "Card.TLabel"
    assert str(orders.import_button.cget("state")) == "disabled"


def test_the_same_tdccs_in_another_order_are_handed_over_without_asking(master, tmp_path: Path, monkeypatch) -> None:
    window = new_window(master, tmp_path)
    window.orders.tdcc_text.insert("1.0", "147000000002\n147000000001")
    monkeypatch.setattr(app.messagebox, "askyesno", lambda *a, **k: pytest.fail("只是順序不同，不用問"))

    window.daily._done(daily_receipt(tmp_path, ("147000000001", "147000000002")))

    assert pasted(window.orders) == "147000000001\n147000000002" and window.orders.hint.shown


def test_starting_another_daily_import_withdraws_the_next_step(master, tmp_path: Path, monkeypatch) -> None:
    """上一次成功、這次重新匯入：開始時先收起「下一步」，這次失敗就不會留著上一次的。"""
    window = new_window(master, tmp_path)
    window.daily._done(daily_receipt(tmp_path, ("147000000001",)))
    assert window.daily.next_button.winfo_manager() == "pack"
    monkeypatch.setattr(window.daily.db, "settings", lambda: object())
    monkeypatch.setattr(window.daily, "run_in_background", lambda *a, **k: None)
    monkeypatch.setattr(app.messagebox, "showerror", lambda *a, **k: None)
    window.daily.daily_var.set(str(tmp_path / "每日更新.xlsx"))

    window.daily.start()
    window.daily._done(app.DailyImportError("每日更新缺少工作表「交易明細」"))

    assert window.daily.next_button.winfo_manager() == ""


# ---------------------------------------------------------------- 起單資料在視窗裡確認（#86、#97、ADR 0009、0010）


def order_window(master, tmp_path: Path, *rows: OrderRow) -> app.PanelWindow:
    window = new_window(master, tmp_path)
    window.orders._done(read_result(*rows))
    return window


def table_rows(tab) -> list[tuple[str, str]]:
    return [tuple(tab.table.item(iid, "values")[:2]) for iid in tab.table.get_children()]


def type_into(editor, name: str, text: str) -> None:
    editor.rows[name].var.set(text)
    editor._commit(name)


def test_products_needing_work_come_first_and_the_first_one_opens(master, tmp_path: Path) -> None:
    ready, missing = order_row("029199990001"), order_row("029199990002", {"Maturity date": None})
    window = order_window(master, tmp_path, ready, missing)

    assert table_rows(window.orders) == [("029199990002", app.BLOCKED), ("029199990001", app.READY)]
    editor = window.orders.editor
    assert editor.draft.tdcc == "029199990002"
    assert editor.rows["Maturity date"].note.cget("text").startswith("必填")
    assert editor.rows["Maturity date"].label.cget("background") == app._PROBLEM_BG
    assert str(window.orders.import_button.cget("state")) == "normal"


def test_filling_a_red_field_makes_the_product_importable(master, tmp_path: Path) -> None:
    window = order_window(master, tmp_path, order_row("029199990002", {"Maturity date": None}))

    type_into(window.orders.editor, "Maturity date", "2027-04-22")

    assert table_rows(window.orders) == [("029199990002", app.READY)]
    assert window.orders.editor.draft.values["Maturity date"].isoformat() == "2027-04-22"
    assert window.orders.unsaved


def test_a_mistyped_field_stays_red_and_keeps_only_that_product_out(master, tmp_path: Path, monkeypatch) -> None:
    window = order_window(master, tmp_path, order_row("029199990001"), order_row("029199990002"))
    started = []
    monkeypatch.setattr(window.orders.db, "settings", lambda **k: object())
    monkeypatch.setattr(window.orders, "run_in_background", lambda *a, **k: started.append(a[1]))

    type_into(window.orders.editor, "Trade date", "10/15")
    window.orders.import_now()

    draft = window.orders.editor.draft
    assert window.orders.editor.rows["Trade date"].note.cget("text") == "日期要寫成 2026-10-21"
    assert window.orders.editor.rows["Trade date"].var.get() == "10/15", "保留打的字"
    assert draft.input_errors == {"Trade date": "日期要寫成 2026-10-21"}, "匯入時這一檔會判不匯入"
    assert dict(table_rows(window.orders))["029199990001"] == app.BLOCKED
    assert dict(table_rows(window.orders))["029199990002"] == app.READY
    assert started == ["匯入資料庫中…"], "其他檔照常匯入"
    assert str(window.orders.editor.rows["Coupon p.a. (%)"].input.cget("state")) == "disabled", "存檔中不能改"


def test_just_browsing_does_not_count_as_unsaved(master, tmp_path: Path) -> None:
    window = order_window(master, tmp_path, order_row("029199990001"), order_row("029199990002"))

    for iid in ("1", "0", "1"):
        window.orders.table.selection_set(iid)
        window.orders._selected()

    assert not window.orders.has_unsaved()


def test_a_field_with_a_reading_problem_stays_red_even_when_it_differs_from_the_database(master, tmp_path) -> None:
    notes = {"Coupon p.a. (%)": "讀到「abc」，不是數字"}
    window = order_window(master, tmp_path, order_row("029199990001", notes=notes))
    draft = window.orders.editor.draft
    draft.stored = {**draft.values, "Coupon p.a. (%)": 12}

    window.orders.editor.refresh()

    row = window.orders.editor.rows["Coupon p.a. (%)"]
    assert row.label.cget("background") == app._PROBLEM_BG
    assert row.note.cget("text") == "起單建檔：讀到「abc」，不是數字；資料庫是 12"
    assert row.database.winfo_manager() == "grid"


def test_conflicting_duplicate_fields_are_red_until_filled(master, tmp_path: Path) -> None:
    row = order_row(
        "029199990001",
        {"Coupon p.a. (%)": None},
        notes={"Coupon p.a. (%)": "這檔有 2 列，這欄不同：第 3 列「12.5」、第 4 列「13」；請填正確的值"},
        conflicts=("Coupon p.a. (%)",),
    )
    window = order_window(master, tmp_path, row)
    field = window.orders.editor.rows["Coupon p.a. (%)"]

    assert table_rows(window.orders) == [("029199990001", app.BLOCKED)]
    assert field.label.cget("background") == app._PROBLEM_BG and "好幾列" in field.note.cget("text")

    type_into(window.orders.editor, "Coupon p.a. (%)", "13")

    assert table_rows(window.orders) == [("029199990001", app.TO_CHECK)]


def test_editing_the_trade_date_moves_an_equal_strike_date(master, tmp_path: Path) -> None:
    window = order_window(master, tmp_path, order_row("029199990001"))
    editor = window.orders.editor

    type_into(editor, "Trade date", "2026-10-16")

    assert editor.rows["Strike date"].var.get() == "2026-10-16"
    assert editor.draft.values["Strike date"].isoformat() == "2026-10-16"


def test_choices_use_drop_downs(master, tmp_path: Path) -> None:
    window = order_window(master, tmp_path, order_row("029199990001"))

    editor = window.orders.editor
    assert isinstance(editor.rows["KO Type"].input, ttk.Combobox)
    assert tuple(editor.rows["Barrier Type"].input.cget("values")) == ("NONE", "EKI", "AKI")
    assert not isinstance(editor.rows["Coupon p.a. (%)"].input, ttk.Combobox)


def test_products_not_in_the_sheet_show_their_reason_only(master, tmp_path: Path) -> None:
    window = order_window(master, tmp_path, missing_row("147000000002"))

    editor = window.orders.editor
    assert editor.draft is None
    assert editor.note_var.get() == "沒有可以改的條件：TDCC：起單建檔_20261015.xlsm 裡沒有這檔"
    assert str(editor.rows["Trade date"].input.cget("state")) == "disabled"
    assert str(window.orders.import_button.cget("state")) == "disabled"


def test_import_results_replace_the_status_column(master, tmp_path: Path) -> None:
    window = order_window(
        master, tmp_path, order_row("029199990001"), order_row("029199990002", {"Maturity date": None})
    )
    type_into(window.orders.editor, "Maturity date", "2027-04-22")
    lines = (
        OrderImportLine("029199990002", ORDER_NEW),
        OrderImportLine("029199990001", ORDER_SKIPPED, "Currency：問題"),
    )

    window.orders._saved(app._SaveResult(OrderImportReceipt(7, lines)))

    assert table_rows(window.orders) == [("029199990002", ORDER_NEW), ("029199990001", ORDER_SKIPPED)]
    assert window.orders.kpis.values == (("已匯入（檔）", 1, False), ("沒匯入（檔）", 1, True))
    assert window.orders.status_var.get().startswith("完成（第 7 次起單資料匯入）：新增 1、覆蓋 0、沒有變 0、不匯入 1")
    assert not window.orders.unsaved
    for iid in ("1", "0"):  # 匯入後點來點去，結果不會被洗掉
        window.orders.table.selection_set(iid)
        window.orders._selected()
    assert table_rows(window.orders) == [("029199990002", ORDER_NEW), ("029199990001", ORDER_SKIPPED)]
    assert not window.orders.has_unsaved()


def test_reload_failure_after_import_says_the_database_was_written(master, tmp_path: Path) -> None:
    window = order_window(master, tmp_path, order_row("029199990001"))
    receipt = OrderImportReceipt(8, (OrderImportLine("029199990001", ORDER_NEW),))

    window.orders._saved(app._SaveResult(receipt, "connection lost"))

    assert "已寫進資料庫，但重新讀取資料庫的值失敗（connection lost）" in window.orders.status_var.get()


def test_closing_with_unsaved_order_edits_asks_and_stays_on_the_order_tab(master, tmp_path: Path, monkeypatch) -> None:
    window = order_window(master, tmp_path, order_row("029199990002", {"Maturity date": None}))
    type_into(window.orders.editor, "Maturity date", "2027-04-22")
    asked = []
    monkeypatch.setattr(app.messagebox, "askyesno", lambda *a, **k: asked.append(a[1]) or False)

    window.close()

    assert len(asked) == 1 and "還沒匯入資料庫" in asked[0]
    assert master.winfo_exists() and window.notebook.select() == str(window.orders.frame)


def test_reading_again_asks_before_dropping_unsaved_edits(master, tmp_path: Path, monkeypatch) -> None:
    window = order_window(master, tmp_path, order_row("029199990002", {"Maturity date": None}))
    window.orders.sheet_var.set(str(tmp_path / "起單建檔_20261015.xlsm"))
    type_into(window.orders.editor, "Maturity date", "2027-04-22")
    asked = []
    monkeypatch.setattr(app.messagebox, "askyesno", lambda *a, **k: asked.append(a[1]) or False)
    monkeypatch.setattr(window.orders, "run_in_background", lambda *a, **k: pytest.fail("沒同意就不該重讀"))

    window.orders.start()

    assert len(asked) == 1 and "重新讀取後會不見" in asked[0]
    assert window.orders.editor.draft.values["Maturity date"].isoformat() == "2027-04-22"


def test_reading_without_a_database_setup_does_not_ask_for_a_password(master, tmp_path: Path, monkeypatch) -> None:
    window = new_window(master, tmp_path)
    window.orders.sheet_var.set(str(tmp_path / "起單建檔_20261015.xlsm"))
    window.orders.tdcc_text.insert("1.0", "029199990001")
    monkeypatch.setattr(app.simpledialog, "askstring", lambda *a, **k: pytest.fail("沒有連線設定就不問密碼"))
    monkeypatch.setattr(app.messagebox, "showerror", lambda *a, **k: pytest.fail("不該跳錯誤"))
    started = []
    monkeypatch.setattr(window.orders, "run_in_background", lambda *a, **k: started.append(a[1]))

    window.orders.start()

    assert started and started[0].startswith("讀取中")


# ---------------------------------------------------------------- 匯入後重算核對條件、放行差異（#88）

UF_DIFF = Difference("UF", "1.99", "0.0215")
NEEDS_RELEASE = CalcLine(
    "029199990001", TO_CONFIRM, "UF不一致：起單資料 1.99、交易明細 0.0215（換算 2.15）", True, (UF_DIFF,)
)


def release_without_database(tab, monkeypatch, result) -> list[str]:
    """放行時不連資料庫：問的話回答「是」，背景工作直接回 result。回傳問過的內容。"""
    asked = []
    monkeypatch.setattr(app.messagebox, "askyesno", lambda *a, **k: asked.append(a[1]) or True)
    monkeypatch.setattr(tab.db, "settings", lambda **kwargs: object())
    monkeypatch.setattr(tab, "run_in_background", lambda action, busy, on_done, **k: on_done(result))
    return asked


def test_daily_import_lists_check_terms_and_releases_a_difference(master, tmp_path: Path, monkeypatch) -> None:
    window = new_window(master, tmp_path)
    calcs = (
        NEEDS_RELEASE,
        CalcLine("029199990002", WAIT_PRICES, "還沒有取價日比 Strike date 2026-10-16 晚的股價檔", False),
        CalcLine("029199990003", DONE, "", False),  # 沒變、已完成的不列
    )

    window.daily._done(daily_receipt(tmp_path, calcs=calcs))

    rows = [tuple(window.daily.table.item(iid, "values")) for iid in window.daily.table.get_children()]
    assert rows == [
        ("029199990001", "核對條件：待確認", NEEDS_RELEASE.reason),
        ("029199990002", "核對條件：等股價", "還沒有取價日比 Strike date 2026-10-16 晚的股價檔"),
    ]
    assert window.daily.kpis.values[-1] == ("核對條件待確認（檔）", 1, True)
    assert "核對條件（還沒鎖住的 3 檔，這次有變 1 檔）：完成 1、等股價 1、等交易明細 0、待確認 1。" in (
        window.daily.status_var.get()
    )
    button = window.daily.releaser.button
    window.daily.table.selection_set("calc-029199990002")
    master.update()
    assert str(button.cget("state")) == "disabled", "沒有差異的不能放行"
    window.daily.table.selection_set("calc-029199990001")
    master.update()
    assert str(button.cget("state")) == "normal"

    released = CalcLine("029199990001", DONE, "已放行的差異：UF（…）", True, (Difference(*UF_DIFF.key, released=True),))
    asked = release_without_database(window.daily, monkeypatch, released)
    button.invoke()

    assert len(asked) == 1 and "UF不一致：起單資料 1.99、交易明細 0.0215（換算 2.15）" in asked[0]
    assert window.daily.table.item("calc-029199990001", "values")[1] == "核對條件：完成"
    assert window.daily.status_var.get().startswith("已放行 029199990001 的差異，重算後：完成")
    assert str(button.cget("state")) == "disabled"


def test_daily_import_says_when_check_terms_were_not_recalculated(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    result = daily_receipt(tmp_path)

    window.daily._done(app._DailyResult(result.receipt, app._Recalc(error="找不到交易所假日表設定檔")))

    assert "核對條件沒有重算（找不到交易所假日表設定檔）" in window.daily.status_var.get()
    assert [caption for caption, _, _ in window.daily.kpis.values] == ["正常（項）", "要注意（項）", "股價（筆）"]


def test_order_import_shows_the_check_terms_of_the_batch(master, tmp_path: Path, monkeypatch) -> None:
    window = order_window(master, tmp_path, order_row("029199990001"), order_row("029199990002"))
    lines = (OrderImportLine("029199990001", ORDER_NEW), OrderImportLine("029199990002", ORDER_NEW))
    calcs = (NEEDS_RELEASE, CalcLine("029199990002", DONE, "", True), CalcLine("029199990009", WAIT_PRICES, "", True))
    recalc = app._Recalc(CalcReceipt(calcs), CONFIG.calendar)

    window.orders._saved(app._SaveResult(OrderImportReceipt(7, lines), recalc=recalc))

    values = {
        window.orders.table.set(iid, "tdcc"): window.orders.table.item(iid, "values") for iid in window.orders.items
    }
    assert values["029199990001"][1:] == (ORDER_NEW, "BARC", TO_CONFIRM, "核對條件：" + NEEDS_RELEASE.reason)
    assert values["029199990002"][1:] == (ORDER_NEW, "BARC", DONE, "")
    assert window.orders.kpis.values[-1] == ("核對條件待確認（檔）", 1, True)
    assert "核對條件（這批匯入的 2 檔）：完成 1、等股價 0、等交易明細 0、待確認 1。" in window.orders.status_var.get()

    iid = next(i for i, (row, _) in window.orders.items.items() if row.tdcc == "029199990001")
    window.orders.table.selection_set(iid)
    master.update()
    released = CalcLine("029199990001", DONE, "已放行的差異：UF（…）", True, (Difference(*UF_DIFF.key, released=True),))
    release_without_database(window.orders, monkeypatch, released)
    window.orders.releaser.button.invoke()

    assert window.orders.table.item(iid, "values")[3:] == (DONE, "核對條件：已放行的差異：UF（…）")
