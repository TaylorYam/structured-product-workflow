"""FCN Workflow 視窗：七個分頁照每天的作業順序排（#73、#74、#90、#115、#116）。

1. 每日匯入：選每日更新，把交易明細與每日股價匯入最小 FCN 資料庫（#32、#87）；信箱在資料庫直接維護（#35）。
2. 起單資料：貼 TDCC、選一份起單建檔（#97），在右邊逐檔確認、修改後匯入資料庫（#86）。
   1、2 匯入完成後重算還沒鎖住的商品的核對條件（#88），逐檔顯示狀態；交叉比對的差異可以在這兩頁放行。
3. 準備：貼 TDCC、選輸出位置，產生批次資料夾與準備清單。
4. 核對：參考條件表＋說明書／投資人須知 PDF 的預覽、核對與儲存（`fcn_checker.panel.CheckTab`）。
5. 放回：選批次資料夾與核對結果檔，把核對通過的 TS／IIS 複製回商品資料夾。
6. 寄信草稿：貼核對通過的 TDCC，在 Outlook 建 TS／IIS 草稿給承作的 FC（#29）。
7. 人工維護：按「檢查」列出還沒鎖住、缺資料的商品（缺的格子淺紅底），或輸入 TDCC 打開任何一檔，
   在右邊補齊、修改資料庫裡的商品條件與交易明細列的值；已鎖住的照更正的規則存檔（#55、#115、#116）。
   「匯出商品條件…」把資料庫目前的 `workflow.product` 整張匯出成 Excel，位置自己選（#122）。

1、2、3、5、6、7 頁的結果表右上方有「全部複製」；結果表按右鍵可以只複製一格或一列，
Treeview 的表（7 的檢查結果表除外）另有 Ctrl+A 全選、Ctrl+C 複製選取的列（#121）。

商品資料夾所在的根目錄在視窗最上方設一次，3、6 兩頁共用（#75）。
做完一步，下一頁先帶入結果並用藍色提示條寫出來，按「下一步 →」才切頁，不自動執行（#76）：
每日匯入完成 → 起單資料頁帶入交易明細的 FCN TDCC（#90）；
準備完成 → 核對頁帶入這批 PDF；核對存檔 → 放回頁帶入批次資料夾與核對結果檔。
審查通過日期、匯出回報包與使用的設定檔不是每天的步驟，收在視窗上方的「維護」選單。
視窗只收輸入、在背景執行一個動作並顯示結果，不含判斷邏輯：要連資料庫的按鈕各呼叫 `window_actions` 的一個動作，
連線、transaction 與容錯（ADR 0012）都在那裡（#125）；不連資料庫的直接呼叫 `prepare_batch`／核對／`return_to_folders`。
這個 module 只有外殼：頂端列、步驟欄、「維護」選單、關閉、視窗大小與啟動參數；各分頁一頁一個 module 在 `sp_workflow.tabs`（#138）。
"""

from __future__ import annotations

import argparse
import re
import sys
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk

from fcn_checker.check_config import CONFIG_DIR, DEFAULTS
from fcn_checker.panel import CheckTab
from fcn_checker.panel_workflow import DatabaseSource, PanelSession
from fcn_checker.ui import (
    ACCENT,
    ACCENT_SOFT,
    BACKGROUND,
    BORDER,
    FONT,
    HOVER,
    NAVY,
    SURFACE,
    TEXT_SECONDARY,
    apply_style,
    apply_window_icon,
    enable_windows_dpi_awareness,
    flat,
    flat_states,
    set_windows_app_id,
)
from sp_workflow.check_source import EXPORT_FOLDER, WorkflowDatabase
from sp_workflow.database import default_env_path
from sp_workflow.local_settings import FolderMemory
from sp_workflow.preparation import LIST_NAME, PrepareReceipt
from sp_workflow.tabs.base import TITLE, DatabaseLogin, RootFolder
from sp_workflow.tabs.daily import DailyImportTab
from sp_workflow.tabs.mail import MailDraftTab
from sp_workflow.tabs.manual import MaintenanceTab
from sp_workflow.tabs.orders import OrderTermsTab
from sp_workflow.tabs.prepare import PrepareTab
from sp_workflow.tabs.returning import ReturnTab


@dataclass(frozen=True)
class _CloseWarning:
    message: str
    page: tk.Misc  # 不關閉時切去哪一頁


WINDOW_FILE = "window.json"  # 視窗上次的大小，和上次選的資料夾放在同一個 .local/
APPROVAL_DATES, EXPORT_BUNDLE = "審查通過日期…", "匯出回報包…"  # 「維護」選單依名稱更新能不能按


class WindowSize:
    """記住視窗上次的大小與是否最大化；沒有紀錄時用預設大小，在 Windows 上最大化（沿用原核對視窗）。"""

    def __init__(self, path: Path) -> None:
        self.record = FolderMemory(path)  # 同一種本機紀錄：讀寫失敗當作沒有記錄

    def restore(self, master: tk.Tk, pixels: Callable[[float], int]) -> None:
        screen_width, screen_height = master.winfo_screenwidth(), master.winfo_screenheight()
        width = min(pixels(1180), screen_width - pixels(40))
        height = min(pixels(820), screen_height - pixels(80))
        master.minsize(min(pixels(820), width), min(pixels(720), height))
        record = self.record.load()
        size = re.fullmatch(r"(\d+)x(\d+)", record.get("size", ""))
        if size:  # 換到較小的螢幕時不超出螢幕
            width, height = min(int(size[1]), screen_width), min(int(size[2]), screen_height)
        master.geometry(f"{width}x{height}")
        if sys.platform == "win32" and record.get("state", "zoomed") == "zoomed":
            master.state("zoomed")

    def remember(self, master: tk.Tk) -> None:
        state = master.state()
        if state == "zoomed":
            self.record.remember(state="zoomed")  # 還原成一般大小時沿用上次記住的大小
        elif state == "normal":
            self.record.remember(state="normal", size=f"{master.winfo_width()}x{master.winfo_height()}")
        # 縮到工作列時關閉：保留上一次的紀錄


def _style_shell(master: tk.Tk) -> None:
    """視窗外殼的樣式（#83）：白色頂端列、左側步驟欄；配色沿用 fcn_checker.panel.apply_style 的那一套。"""
    style = ttk.Style(master)
    style.configure("Header.TFrame", background=SURFACE)
    style.configure("Header.TLabel", background=SURFACE, foreground=TEXT_SECONDARY)
    style.configure("HeaderTitle.TLabel", background=SURFACE, foreground=NAVY, font=(FONT, 13, "bold"))
    style.configure("Sidebar.TFrame", background=SURFACE)
    style.configure("SidebarTitle.TLabel", background=SURFACE, foreground=TEXT_SECONDARY, font=(FONT, 10))
    step = {"anchor": "w", "padding": (14, 9)}
    style.configure(
        "Step.TButton", foreground=TEXT_SECONDARY, bordercolor=SURFACE, focuscolor=SURFACE, **step, **flat(SURFACE)
    )
    style.map("Step.TButton", **flat_states([("active", HOVER)]))
    style.configure(
        "CurrentStep.TButton",
        foreground=ACCENT,
        font=(FONT, 11, "bold"),
        bordercolor=ACCENT_SOFT,
        focuscolor=ACCENT_SOFT,
        **step,
        **flat(ACCENT_SOFT),
    )
    style.map("CurrentStep.TButton", **flat_states([("active", ACCENT_SOFT)]))
    style.layout("Workflow.TNotebook.Tab", [])  # 用左側步驟欄切頁，不顯示分頁標籤
    style.configure("Workflow.TNotebook", background=BACKGROUND, borderwidth=0)


class StepSidebar(ttk.Frame):
    """左側步驟欄（#83）：照每天的順序列出 1～7，按了切到那一步；目前所在的那一步用淺藍底標出。

    分頁標籤已藏起來，切頁一律經過 notebook.select（「下一步 →」也是），步驟欄跟著 <<NotebookTabChanged>> 更新。
    """

    def __init__(self, parent: tk.Misc, notebook: ttk.Notebook, pixels: Callable[[float], int]) -> None:
        super().__init__(parent, style="Sidebar.TFrame", padding=(pixels(10), pixels(18)))
        self.notebook = notebook
        ttk.Label(self, text="步驟", style="SidebarTitle.TLabel").pack(anchor="w", padx=pixels(14), pady=(0, pixels(8)))
        self.steps: dict[str, ttk.Button] = {}
        for tab in notebook.tabs():
            number, _, name = notebook.tab(tab, "text").partition(" ")
            step = ttk.Button(
                self, text=f"{number}    {name}", style="Step.TButton", command=lambda t=tab: notebook.select(t)
            )
            step.pack(fill=tk.X, pady=1, ipadx=pixels(40))
            self.steps[tab] = step
        notebook.bind("<<NotebookTabChanged>>", lambda _event: self.refresh(), add="+")

    def refresh(self) -> None:
        current = self.notebook.select()
        for tab, step in self.steps.items():
            step.configure(style="CurrentStep.TButton" if tab == current else "Step.TButton")


class PanelWindow:
    """整個視窗：分頁、「維護」選單與關閉處理；各分頁的功能在各自的類別裡。"""

    def __init__(
        self,
        master: tk.Tk,
        memory: FolderMemory,
        db: DatabaseLogin,
        session: PanelSession,
        *,
        start_on_check: bool = False,
    ) -> None:
        """db 是各分頁共用的資料庫登入；session 是核對的工作階段，建好時已帶著資料庫來源（`build_window` 組好，#129）。"""
        self.master = master
        self.session = session
        self.size = WindowSize(memory.path.with_name(WINDOW_FILE))
        master.title(TITLE)
        self.pixels = apply_style(master)
        _style_shell(master)
        top_bar = ttk.Frame(master, style="Header.TFrame", padding=(self.pixels(18), self.pixels(10)))
        top_bar.pack(fill=tk.X)
        ttk.Label(top_bar, text=TITLE, style="HeaderTitle.TLabel").pack(side=tk.LEFT, padx=(0, self.pixels(28)))
        self.root_folder = RootFolder(memory)
        self.root_folder.build_bar(top_bar, self.pixels)
        tk.Frame(master, height=1, background=BORDER).pack(fill=tk.X)
        body = ttk.Frame(master)
        body.pack(fill=tk.BOTH, expand=True)
        self.notebook = ttk.Notebook(body, style="Workflow.TNotebook")
        # 分頁照每天的作業順序建立，前面自動加上 1～7 的編號；人工維護不是每天的步驟，放最後（#115）
        self.daily = DailyImportTab(
            self.notebook,
            memory,
            db,
            lambda: self.session.paths,
            on_imported=lambda tdccs: self.orders.receive_tdccs(tdccs),
            on_next=lambda: self.notebook.select(self.orders.frame),
        )
        self.orders = OrderTermsTab(self.notebook, memory, db, lambda: self.session.paths)
        self.prepare = PrepareTab(
            self.notebook,
            memory,
            self.root_folder,
            on_prepared=self._prepared,
            on_next=lambda: self.notebook.select(self.check),
        )
        self.check = CheckTab(self.notebook, self.session, self.pixels, on_saved=self._check_saved, login=db.log_in)
        self.check.add_next_step("下一步：放回 →", lambda: self.notebook.select(self.returning.frame))
        self.notebook.add(self.check, text="核對")
        self.returning = ReturnTab(self.notebook, memory)
        self.mail = MailDraftTab(self.notebook, memory, db, self.root_folder)
        self.manual = MaintenanceTab(self.notebook, memory, db, lambda: self.session.paths)
        tabs = self.notebook.tabs()
        for number, tab in enumerate(tabs, 1):
            self.notebook.tab(tab, text=f"{number} {self.notebook.tab(tab, 'text')}")
        self.pages = (self.daily, self.orders, self.prepare, self.returning, self.mail, self.manual)
        for page, tab in [(p.frame, p) for p in self.pages] + [(self.check, self.check)]:
            tab.show_step(f"步驟 {self.notebook.index(page) + 1}／{len(tabs)}")
        self.sidebar = StepSidebar(body, self.notebook, self.pixels)
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y)
        tk.Frame(body, width=1, background=BORDER).pack(side=tk.LEFT, fill=tk.Y)
        self.notebook.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.notebook.select(self.check if start_on_check else 0)
        self.sidebar.refresh()
        self.maintenance = self._maintenance_menu()
        master.protocol("WM_DELETE_WINDOW", self.close)

    def _prepared(self, receipt: PrepareReceipt) -> bool:
        """準備完成：放回頁帶入批次資料夾；核對頁帶入已準備的 PDF 並顯示提示條，回傳核對頁是否帶入。不切頁、不自動核對（#76）。"""
        self.returning.batch_var.set(str(receipt.batch_dir))
        pdfs = receipt.prepared_pdfs
        then = "" if self.session.use_database else "選好參考條件表後"
        return bool(pdfs) and self.check.receive_pdfs(
            pdfs,
            f"已帶入「3 準備」的批次（{len(pdfs)} 份 PDF），{then}按「載入／重新載入預覽」：\n{receipt.batch_dir}",
        )

    def _check_saved(self, result: Path) -> None:
        """核對存檔：放回頁帶入剛存的核對結果檔；PDF 都在同一個有準備清單的資料夾時，那就是這批的批次資料夾（#76）。"""
        folders = {pdf.parent for pdf in self.session.term_sheets}
        batch = folders.pop() if len(folders) == 1 else None
        self.returning.receive(result, batch if batch is not None and (batch / LIST_NAME).is_file() else None)

    def _maintenance_menu(self) -> tk.Menu:
        """視窗上方的「維護」選單：不是每天的步驟，不放在分頁畫面上（#73）。"""
        bar = tk.Menu(self.master)
        menu = tk.Menu(bar, tearoff=False, postcommand=self._refresh_maintenance)
        menu.add_command(label=APPROVAL_DATES, command=self.check.edit_approval_dates)
        menu.add_command(label=EXPORT_BUNDLE, command=self._export_bundle)
        menu.add_separator()
        menu.add_command(label="使用的設定檔…", command=self.check.show_config_paths)
        bar.add_cascade(label="維護", menu=menu)
        self.master.configure(menu=bar)
        return menu

    def _refresh_maintenance(self) -> None:
        """每次打開「維護」選單時，依核對頁目前的狀態決定能不能按。"""
        for label, allowed in (
            (APPROVAL_DATES, self.check.can_edit_approval_dates()),
            (EXPORT_BUNDLE, self.check.can_export_bundle()),
        ):
            self.maintenance.entryconfigure(label, state=tk.NORMAL if allowed else tk.DISABLED)

    def _export_bundle(self) -> None:
        self.notebook.select(self.check)  # 匯出的進度與結果顯示在核對頁
        self.check.export_bundle()

    def restore_size(self) -> None:
        """照上次的大小顯示；第一次開啟時在 Windows 最大化。"""
        self.size.restore(self.master, self.pixels)

    def _close_warning(self) -> _CloseWarning | None:
        if self.check.working:
            return _CloseWarning("核對頁還在處理（讀檔、核對或存檔），關閉後要重新來。", self.check)
        if self.session.has_unsaved_result:
            return _CloseWarning("核對結果還沒儲存，關閉後要重新核對。", self.check)
        if self.orders.working:
            return _CloseWarning("起單資料頁還在處理（讀取或匯入），關閉後要重新來。", self.orders.frame)
        if self.orders.has_unsaved():
            return _CloseWarning("起單資料頁有改過、還沒匯入資料庫的內容，關閉後會不見。", self.orders.frame)
        if self.manual.working:
            return _CloseWarning("人工維護頁還在處理（檢查、讀取或存檔），關閉後要重新來。", self.manual.frame)
        if self.manual.has_unsaved():
            return _CloseWarning("人工維護頁有改過、還沒存檔的內容，關閉後會不見。", self.manual.frame)
        return None

    def close(self) -> None:
        """核對頁還在處理或有還沒存檔的核對結果時先問一次；關閉前記住視窗大小。"""
        warning = self._close_warning()
        if warning and not messagebox.askyesno(
            TITLE,
            f"{warning.message}\n\n確定要關閉嗎？",
            icon=messagebox.WARNING,
            default=messagebox.NO,
            parent=self.master,
        ):
            self.notebook.select(warning.page)
            return
        self.size.remember(self.master)
        self.check.close()
        for page in self.pages:
            page.background.close()
        self.master.destroy()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """視窗的啟動參數（#129 前在 `fcn_checker.panel`）；`fcn-panel`、雙擊入口與 `spw-panel` 共用。"""
    parser = argparse.ArgumentParser(
        description="FCN Workflow 視窗：每日匯入、起單資料、準備、核對、放回與寄信草稿；"
        "核對時以參考條件表預覽並核對多份說明書 PDF，按儲存才寫出核對結果檔。"
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=None,
        help="設定檔資料夾（預設 config）；缺少參考條件表格式或上手編號對照時改用程式內建設定",
    )
    parser.add_argument(
        "--builtin-config-dir", type=Path, default=None, help="程式內建設定資料夾（雙擊入口傳入專案的 config）"
    )
    parser.add_argument("--order-formats-dir", type=Path, help=argparse.SUPPRESS)  # 舊版啟動器仍會傳入
    parser.add_argument("--review-standard", type=Path, default=DEFAULTS.review_standard, help="審查標準設定檔")
    parser.add_argument(
        "--install-root", type=Path, help="雙擊入口提供的安裝目錄；上次選的資料夾、.env 與核對紀錄都在它底下"
    )
    args = parser.parse_args(argv)
    if args.config_dir is None:
        args.config_dir = args.order_formats_dir.parent if args.order_formats_dir else CONFIG_DIR
    return args


def session_from_args(args: argparse.Namespace, database: DatabaseSource | None = None) -> PanelSession:
    """核對的工作階段，建好時就帶著資料庫來源（#129）；根目錄與設定檔一致：雙擊入口給的專案根目錄，直接啟動時為執行目錄。"""
    return PanelSession(
        args.review_standard,
        args.config_dir,
        builtin_config_dir=args.builtin_config_dir,
        install_root=args.install_root,
        database=database,
    )


def folder_memory(argv: list[str] | None = None) -> FolderMemory:
    """依啟動參數決定上次選的資料夾記在哪：有 --install-root 就記在它底下的 .local/。"""
    return _memory_under(parse_args(argv).install_root)


def _memory_under(install_root: Path | None) -> FolderMemory:
    return FolderMemory.under(install_root) if install_root else FolderMemory()


def build_window(master: tk.Tk, args: argparse.Namespace, *, start_on_check: bool = False) -> PanelWindow:
    """依啟動參數組視窗（#129）：資料庫登入 → 核對的資料庫來源 → 建好就帶著它的核對工作階段 → 視窗。"""
    db = DatabaseLogin(master, default_env_path(args.install_root))
    session = session_from_args(args, _check_database(db, args.install_root))
    return PanelWindow(master, _memory_under(args.install_root), db, session, start_on_check=start_on_check)


def _check_database(db: DatabaseLogin, install_root: Path | None) -> WorkflowDatabase:
    """核對分頁從資料庫讀條件（#68）：用視窗登入過的連線設定；產生的參考條件表放在根目錄的 runtime/ 底下。"""
    folder = Path(install_root or ".").resolve() / "runtime" / EXPORT_FOLDER  # 根目錄和 PanelSession 一樣
    return WorkflowDatabase(db.current, folder, on_failure=db.forget_password)


def main(argv: list[str] | None = None, *, start_on_check: bool = False) -> None:
    """開 FCN Workflow 視窗；參數見 `parse_args`（和以前的核對 PANEL 相同），舊的啟動方式照舊有效。"""
    args = parse_args(argv)
    enable_windows_dpi_awareness()
    set_windows_app_id()
    master = tk.Tk()
    window = build_window(master, args, start_on_check=start_on_check)
    window.restore_size()
    apply_window_icon(master)
    master.mainloop()


if __name__ == "__main__":
    main()
