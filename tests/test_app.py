"""視窗外殼的冒煙測試：建得起來、分頁順序、維護選單、關閉提醒、步驟銜接與記住資料夾、核對頁在視窗裡的樣子。

各分頁自己的測試在 `test_tabs_*.py`（#138），共用的組視窗方式在 `window_synth`。沒有顯示器（CI）時略過要開視窗的測試。
"""

from __future__ import annotations

import runpy
import threading
import time
import tkinter as tk
from concurrent.futures import Future
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import pytest

import fcn_checker.panel
import sp_workflow.tabs.mail
from fcn_checker.ingestion import IngestionError
from fcn_checker.panel_workflow import PanelSession
from fcn_checker.saving import SaveReceipt
from fcn_checker.ui import Background
from sp_workflow import app
from sp_workflow.app import PanelWindow
from sp_workflow.check_source import EXPORT_FOLDER
from sp_workflow.local_settings import FolderMemory
from sp_workflow.tabs.base import DatabaseLogin, RootFolder
from window_synth import hint_change_button, new_window, prepared_receipt, prepared_result, texts

ROOT = Path(__file__).resolve().parents[1]


def tab_titles(window: PanelWindow) -> list[str]:
    return [window.notebook.tab(tab, "text") for tab in window.notebook.tabs()]


def test_tabs_follow_the_daily_order(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)

    assert master.title() == "FCN Workflow"
    assert tab_titles(window) == [
        "1 每日匯入",
        "2 起單資料",
        "3 準備",
        "4 核對",
        "5 放回",
        "6 寄信草稿",
        "7 人工維護",
        "8 進度",
        "9 出場清單",
    ]
    assert window.notebook.select() == str(window.daily.frame)


def test_checker_entry_starts_on_the_check_tab(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path, start_on_check=True)

    assert window.notebook.select() == str(window.check)


def test_maintenance_actions_live_in_the_menu_only(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    menu = window.maintenance

    labels = [menu.entrycget(i, "label") for i in range(menu.index(tk.END) + 1) if menu.type(i) == "command"]
    assert labels == [
        "審查通過日期…",
        "匯出回報包…",
        "匯入歷史收盤價…",
        "重新比價全部",
        "匯入舊商品的價格與比價日…",
        "信箱維護…",
        "使用的設定檔…",
    ]
    assert not {"審查通過日期…", "匯出回報包…"} & set(texts(window.check, ttk.Button)), "核對頁畫面上不再有維護功能"
    window._refresh_maintenance()
    assert str(menu.entrycget(0, "state")) == "normal"
    assert str(menu.entrycget(1, "state")) == "disabled", "還沒核對，沒有可以匯出的回報包"
    assert str(menu.entrycget(2, "state")) == "normal"
    window.daily._busy(True)
    window._refresh_maintenance()
    assert str(menu.entrycget(2, "state")) == "disabled", "每日匯入頁正在匯入時不能匯歷史收盤價"
    assert str(menu.entrycget(3, "state")) == "disabled", "也不能重新比價"
    assert str(menu.entrycget(4, "state")) == "disabled", "也不能匯入舊商品"


class UnsavedSession(PanelSession):
    has_unsaved_result = True


@pytest.mark.parametrize("answer", [False, True])
def test_closing_with_an_unsaved_check_result_asks_first(master, tmp_path: Path, monkeypatch, answer) -> None:
    window = new_window(master, tmp_path, UnsavedSession(install_root=tmp_path))
    asked = []
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: asked.append(a[1]) or answer)

    window.close()

    assert len(asked) == 1 and "核對結果還沒儲存" in asked[0]
    assert window.check.closed is answer
    if not answer:
        assert master.winfo_exists() and window.notebook.select() == str(window.check), "不關閉，切到核對頁去存檔"


def test_closing_while_the_check_tab_is_working_asks_first(master, tmp_path: Path, monkeypatch) -> None:
    window = new_window(master, tmp_path)
    window.check.check_pending = Future()  # 核對還在背景跑，還沒有結果
    asked = []
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: asked.append(a[1]) or False)

    window.close()

    assert len(asked) == 1 and "核對頁還在處理" in asked[0]
    assert not window.check.closed


def test_closing_without_an_unsaved_result_does_not_ask(master, tmp_path: Path, monkeypatch) -> None:
    window = new_window(master, tmp_path)
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: pytest.fail("不該提醒"))

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
    args = app.parse_args(workflow_argv)
    assert (args.install_root, args.config_dir) == (ROOT, ROOT / "config")
    assert workflow_kwargs == {}
    assert checker_argv == ["--install-root", str(ROOT)] and checker_kwargs == {"start_on_check": True}


def test_window_builds_with_remembered_folders(master, tmp_path: Path) -> None:
    memory = FolderMemory.under(tmp_path)
    memory.remember(root="R:/FCN", out="D:/out", batch="D:/out/待核對_1")

    window = new_window(master, tmp_path)

    assert (window.root_folder.var.get(), window.prepare.out_var.get()) == ("R:/FCN", "D:/out")
    assert str(window.prepare.open_button.cget("state")) == "disabled"
    assert window.returning.batch_var.get() == "D:/out/待核對_1"


def test_prepared_batch_is_filled_into_the_return_tab(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)

    window.prepare.on_prepared(prepared_receipt(Path("D:/out/待核對_20261019-103000")))

    assert window.returning.batch_var.get() == str(Path("D:/out/待核對_20261019-103000"))


def test_install_root_decides_where_folders_are_remembered(tmp_path: Path) -> None:
    assert app.folder_memory(["--install-root", str(tmp_path)]).path == tmp_path / ".local" / "prepare_folders.json"
    assert app.folder_memory([]).path.name == "prepare_folders.json"


def test_daily_import_tab_remembers_files_and_env_path(master, tmp_path: Path) -> None:
    memory = FolderMemory.under(tmp_path)
    memory.remember(daily="D:/每日更新.xlsx")

    window = new_window(master, tmp_path)

    assert window.daily.daily_var.get() == "D:/每日更新.xlsx"
    assert window.daily.db.env_path == tmp_path / ".env" and window.mail.db is window.daily.db


def test_mail_draft_tab_reuses_the_root_and_remembers_its_output(master, tmp_path: Path) -> None:
    memory = FolderMemory.under(tmp_path)
    memory.remember(root="R:/FCN", mail_out="D:/寄信")

    window = new_window(master, tmp_path)

    assert window.mail.root_folder is window.root_folder and window.mail.out_var.get() == "D:/寄信"


# ---------------------------------------------------------------- 共用根目錄（#75）


def test_root_is_set_once_at_the_top_for_two_tabs(master, tmp_path: Path, monkeypatch) -> None:
    memory = FolderMemory.under(tmp_path)
    window = new_window(master, tmp_path)
    tabs = (window.prepare, window.mail)
    assert all(tab.root_folder is window.root_folder for tab in tabs)
    assert not any("根目錄" in texts(tab.frame, ttk.Label) for tab in tabs), "兩頁不再各有根目錄欄位"
    monkeypatch.setattr(filedialog, "askdirectory", lambda **kwargs: "R:/FCN")

    window.root_folder.change()

    assert window.root_folder.var.get() == str(Path("R:/FCN"))
    assert RootFolder(memory).var.get() == str(Path("R:/FCN")), "重開視窗仍是這個根目錄"


def test_root_remembered_before_the_upgrade_is_kept(master, tmp_path: Path) -> None:
    """升級前三頁共用 prepare_folders.json 的 root，升級後直接沿用，不用重選。"""
    record = tmp_path / ".local" / "prepare_folders.json"
    record.parent.mkdir()
    record.write_text('{"root": "R:/FCN", "out": "D:/out", "mail_out": "D:/寄信"}', encoding="utf-8")

    window = new_window(master, tmp_path)

    assert window.root_folder.var.get() == "R:/FCN"


@pytest.mark.parametrize("tab_name", ["prepare", "mail"])
def test_starting_without_a_root_asks_to_set_it_first(master, tmp_path: Path, monkeypatch, tab_name) -> None:
    window = new_window(master, tmp_path)
    tab = getattr(window, tab_name)
    tab.out_var.set("D:/out")
    errors = []
    monkeypatch.setattr(messagebox, "showerror", lambda title, message, **kwargs: errors.append(message))
    monkeypatch.setattr(tab, "run_in_background", lambda *args, **kwargs: pytest.fail("不該開始執行"))

    tab.start()

    assert errors == ["請先在視窗最上方按「變更…」設定根目錄。"]


def test_each_tab_still_remembers_its_own_output(master, tmp_path: Path, monkeypatch) -> None:
    memory = FolderMemory.under(tmp_path)
    memory.remember(root="R:/FCN")
    window = new_window(master, tmp_path)
    monkeypatch.setattr(window.mail.db, "settings", lambda **kwargs: object())  # 寄信草稿先登入資料庫，這裡不連線
    monkeypatch.setattr(sp_workflow.tabs.mail, "mail_template_from_env", lambda env_path: None)
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


def test_prepared_pdfs_are_handed_to_the_check_tab_without_switching(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    window.notebook.select(window.prepare.frame)
    batch = tmp_path / "待核對_20261020-103000"

    window.prepare._done(prepared_result(batch))

    assert window.check.pdf_paths == (batch / "147000000001_TS.pdf", batch / "147000000001_IIS.pdf"), "只帶已準備的"
    assert window.check.hint.text.get() == (
        f"已帶入「3 準備」的批次（2 份 PDF），按「載入／重新載入預覽」：\n{batch}"
    ), "預設資料庫來源，不用選參考條件表（#108）"
    assert window.notebook.select() == str(window.prepare.frame), "不自動切頁"
    assert window.session.preview is None and not window.check.working, "不自動載入預覽或核對"
    assert window.prepare.next_button.winfo_manager() == "pack"

    window.prepare.next_button.invoke()

    assert window.notebook.select() == str(window.check)


def test_choosing_other_pdfs_hides_the_hint(master, tmp_path: Path, monkeypatch) -> None:
    window = new_window(master, tmp_path)
    window.prepare._done(prepared_result(tmp_path / "待核對_1"))
    other = tmp_path / "其他" / "147000000009_TS.pdf"
    monkeypatch.setattr(fcn_checker.panel.filedialog, "askopenfilenames", lambda **kwargs: (str(other),))

    hint_change_button(window.check.hint).invoke()

    assert window.check.pdf_paths == (other,) and not window.check.hint.shown


@pytest.mark.parametrize("answer", [False, True])
def test_an_unsaved_check_result_is_kept_unless_the_operator_agrees(master, tmp_path, monkeypatch, answer) -> None:
    window = new_window(master, tmp_path, UnsavedSession(install_root=tmp_path))
    asked = []
    monkeypatch.setattr(fcn_checker.panel.messagebox, "askyesno", lambda *a, **k: asked.append(a[1]) or answer)

    window.prepare._done(prepared_result(tmp_path / "待核對_1"))

    assert len(asked) == 1 and "沒存檔的核對結果" in asked[0]
    assert bool(window.check.pdf_paths) is answer and window.check.hint.shown is answer
    assert window.prepare.next_button.winfo_manager() == ("pack" if answer else ""), "沒帶入就沒有下一步"


def test_a_busy_check_tab_does_not_take_the_batch(master, tmp_path: Path, monkeypatch) -> None:
    window = new_window(master, tmp_path)
    window.check.check_pending = Future()  # 核對還在背景跑
    told = []
    monkeypatch.setattr(fcn_checker.panel.messagebox, "showinfo", lambda *a, **k: told.append(a[1]))

    window.prepare._done(prepared_result(tmp_path / "待核對_1"))

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
        monkeypatch.setattr(filedialog, "askopenfilename", lambda **kwargs: str(tmp_path / "另一份.xlsx"))
        hint_change_button(window.returning.hint).invoke()
        assert window.returning.result_var.get() == str(tmp_path / "另一份.xlsx")
    else:
        window.returning.batch_var.set(str(tmp_path / "待核對_2"))

    assert not window.returning.hint.shown


# ---------------------------------------------------------------- 蘋果 × 麥肯錫風格範本（#83、#85）


def test_sidebar_switches_steps_and_follows_the_next_buttons(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    steps = window.sidebar.steps

    assert [step.cget("text").split()[0] for step in steps.values()] == ["1", "2", "3", "4", "5", "6", "7", "8", "9"]
    steps[str(window.prepare.frame)].invoke()
    master.update()
    assert window.notebook.select() == str(window.prepare.frame)
    assert steps[str(window.prepare.frame)].cget("style") == "CurrentStep.TButton"

    window.prepare._done(prepared_result(tmp_path / "待核對_1"))
    window.prepare.next_button.invoke()
    master.update()

    current = [tab for tab, step in steps.items() if step.cget("style") == "CurrentStep.TButton"]
    assert current == [str(window.check)], "「下一步」切頁時步驟欄跟著換"


def test_page_headers_show_their_step(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)

    assert window.prepare.header.step.get() == "步驟 3／9"
    assert window.check.header.step.get() == "步驟 4／9"


def test_every_page_header_shows_its_step(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    pages = (
        window.daily,
        window.orders,
        window.prepare,
        window.check,
        window.returning,
        window.mail,
        window.manual,
        window.progress,
        window.exits,
    )

    assert [page.header.step.get() for page in pages] == [f"步驟 {n}／9" for n in range(1, 10)]


def test_check_key_figures_hide_when_results_are_cleared(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    window.check.kpis.show((("通過", 2, False),))

    window.check._clear_results()

    assert window.check.kpis.values == ()
    assert window.check.status_label.cget("style") == "TLabel", "沒有存過檔的結果，狀態列不是綠色"


# ---------------------------------------------------------------- 核對分頁的條件來源（#68）


def shown(widget: tk.Misc) -> bool:
    return widget.winfo_manager() != ""


def test_check_tab_reads_terms_from_the_database_by_default(master, tmp_path: Path) -> None:
    """預設資料庫（#108）：不用選參考條件表，那一列收起來；改選 Excel 才出現。"""
    window = new_window(master, tmp_path)
    check = window.check

    assert window.session.database is not None
    assert texts(check, ttk.Radiobutton)[:2] == ["資料庫（依 PDF 的 TDCC）", "Excel 參考條件表"]
    assert check.source.get() == "database" and window.session.use_database
    assert not shown(check.sheet_button) and str(check.sheet_button.cget("state")) == "disabled"
    assert check.sheet_text.get().startswith("由資料庫產生")
    assert check.status.get() == "請選取說明書 PDF。"

    check.source.set("excel")
    check._source_changed()

    assert not window.session.use_database
    assert shown(check.sheet_button) and str(check.sheet_button.cget("state")) == "normal"
    assert check.status.get() == "請選取參考條件表與說明書 PDF。"

    check.source.set("database")
    check._source_changed()

    assert window.session.use_database and not shown(check.sheet_button)


class Preview:
    rows = ()


def test_check_setup_folds_into_one_line_after_the_preview(master, tmp_path: Path) -> None:
    """載入預覽後頁首說明與選檔的幾列收成一行摘要，「變更…」展開，換 PDF 時也展開（#108）。"""
    window = new_window(master, tmp_path)
    check = window.check
    check.receive_pdfs((tmp_path / "147000000001_TS.pdf", tmp_path / "147000000001_IIS.pdf"), "帶入")
    pdf_button = check.pdf_button
    assert not check.folded and not shown(check.summary)
    assert shown(check.header.description) and shown(pdf_button)

    check._show_preview(Preview())

    assert check.folded and shown(check.summary)
    assert not shown(check.header.description) and not shown(pdf_button) and not shown(check.sheet_button)
    assert not shown(check.hint) and check.hint.shown, "提示條收起，但保留內容"
    assert check.summary_source.get() == "資料庫"
    assert check.pdf_text.get().startswith("已選 2 份")

    check.expand_button.invoke()

    assert not check.folded and not shown(check.summary) and shown(pdf_button) and not shown(check.sheet_button)
    assert shown(check.hint)

    check._show_preview(Preview())
    check.receive_pdfs((tmp_path / "147000000002_TS.pdf",), "帶入")

    assert not check.folded and shown(pdf_button), "換了 PDF，展開讓作業人員看清楚選了什麼"


def fold(check) -> None:
    check.receive_pdfs((Path("D:/out/147000000001_TS.pdf"),), "帶入")
    check._show_preview(Preview())
    assert check.folded


def failed_load(error: Exception):
    def run(check) -> None:
        check._clear()  # 和 load() 一樣先清掉預覽，背景讀取再失敗
        check.pending = Future()
        check.pending.set_exception(error)
        check._after("pending", check._show_preview, "預覽讀取失敗")

    return run


@pytest.mark.parametrize(
    "clear",
    [
        pytest.param(lambda check: check._approval_dates_changed(), id="審查通過日期改了"),
        pytest.param(failed_load(IngestionError("unreadable", "讀不到")), id="來源讀不到"),
        pytest.param(failed_load(OSError("磁碟錯誤")), id="預覽讀取失敗"),
    ],
)
def test_clearing_the_preview_expands_the_setup(master, tmp_path: Path, clear) -> None:
    """清掉預覽時一律展開設定區，不會停在只剩摘要、預覽卻是空的畫面（#108）。"""
    check = new_window(master, tmp_path).check
    fold(check)

    clear(check)

    assert check.shown is None and not check.folded and shown(check.pdf_button) and not shown(check.summary)


def test_a_failed_check_keeps_the_setup_folded(master, tmp_path: Path) -> None:
    """核對失敗時預覽還在，設定區維持收起。"""
    check = new_window(master, tmp_path).check
    fold(check)
    check.check_pending = Future()
    check.check_pending.set_exception(OSError("核對失敗"))

    check._after("check_pending", check._show_outcome, "核對失敗")

    assert check.shown is not None and check.folded


def test_folded_excel_setup_names_the_reference_workbook(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    check = window.check
    check.source.set("excel")
    check._source_changed()
    check.sheet_path = tmp_path / "參考條件表.xlsx"

    check._show_preview(Preview())

    assert check.summary_source.get() == "Excel：參考條件表.xlsx"

    check.expand_button.invoke()

    assert shown(check.sheet_button), "Excel 來源展開時有參考條件表那一列"


# ---------------------------------------------------------------- 結果表複製：核對頁（#121）


def test_check_tab_tables_can_be_copied(master, tmp_path: Path) -> None:
    results = new_window(master, tmp_path).check.results
    results.item_table.insert("", tk.END, values=["未通過", "TS", "029199990001_TS.pdf", "2"])
    results.table.insert("", tk.END, values=["不一致", "Coupon", "12.5", "12", "3"])

    results.item_table.copier.copy_all()
    assert master.clipboard_get() == "狀態\t種類\tPDF 檔名\t問題數\n未通過\tTS\t029199990001_TS.pdf\t2"
    results.table.copier.fill_menu(results.table.get_children()[0], "#2")
    assert results.table.copier.menu.labels() == ["複製「Coupon」", "複製這一列", "全部複製（含表頭）"], (
        "一次只能選一列的表沒有「選取的列」"
    )


# ---------------------------------------------------------------- 視窗外殼整理（#129）


def wait_until(master: tk.Misc, condition, timeout: float = 5.0) -> None:
    """讓 Tk 處理 after 排程，直到 condition 成立。"""
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "等太久"
        master.update()
        time.sleep(0.01)


def test_background_reports_back_on_the_main_thread(master) -> None:
    """全視窗唯一的背景作業寫法：動作在背景執行緒做，做完才回主執行緒呼叫 done；錯誤也在 Future 裡。"""
    background = Background(master)
    done = []

    background.run(threading.get_ident, lambda future: done.append((future.result(), threading.get_ident())))
    background.run(lambda: 1 / 0, lambda future: done.append(type(future.exception())))
    wait_until(master, lambda: len(done) == 2)

    (worker, caller), error = done
    assert worker != threading.get_ident() and caller == threading.get_ident()
    assert error is ZeroDivisionError


def test_a_closed_background_stops_reporting(master) -> None:
    """視窗關閉後背景做完也不再回頭動畫面。"""
    background = Background(master)
    release, done = threading.Event(), []
    future = background.run(release.wait, done.append)

    background.close()
    release.set()
    future.result(timeout=5)
    for _ in range(20):
        master.update()
        time.sleep(0.01)

    assert done == []


def no_password_env(tmp_path: Path) -> None:
    """有資料庫連線設定、沒有密碼的 .env（虛構的帳號）。"""
    (tmp_path / ".env").write_text("PGDATABASE=spw_fake\nPGUSER=operator1\n", encoding="utf-8")


def test_check_tab_asks_the_shell_to_log_in_before_reading_the_database(master, tmp_path, monkeypatch) -> None:
    """資料庫來源連線前，核對頁請外殼在主執行緒登入（問密碼）；取消時不開始、不碰背景。"""
    no_password_env(tmp_path)
    check = new_window(master, tmp_path).check
    asked = []
    monkeypatch.setattr(simpledialog, "askstring", lambda *a, **k: asked.append(a[1]) or None)

    check.load()

    assert asked == ["operator1@localhost 的資料庫密碼"]
    assert check.pending is None and check.status.get() == "沒有登入資料庫，沒有開始。"


def test_database_source_uses_the_settings_the_shell_logged_in_with(master, tmp_path, monkeypatch) -> None:
    """核對的資料庫來源在背景拿外殼登入過的連線設定，不會再問；連不上時外殼忘記密碼，下次重新問。"""
    no_password_env(tmp_path)
    asked = []
    monkeypatch.setattr(simpledialog, "askstring", lambda *a, **k: asked.append(a[1]) or "secret")
    db = DatabaseLogin(master, tmp_path / ".env")
    source = app._check_database(db, tmp_path)
    assert source.folder == tmp_path.resolve() / "runtime" / EXPORT_FOLDER
    assert db.current() is None, "還沒登入"

    assert db.log_in()
    assert source._require().password == "secret" and len(asked) == 1, "背景拿設定不再問"

    (tmp_path / ".env").write_text("PGUSER=operator1\n", encoding="utf-8")  # 別的分頁安靜地讀，連線設定檔壞了
    assert db.settings(quiet=True) is None
    assert source._require().password == "secret", "別的分頁沒讀到，不會把核對頁登出"

    source.on_failure()

    assert db.current() is None and db.password is None
