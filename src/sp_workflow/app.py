"""FCN Workflow 視窗：九個分頁照每天的作業順序排（#73、#74、#90、#115、#116、#156、#171）。

1. 每日匯入：選每日更新，把交易明細與每日股價匯入最小 FCN 資料庫（#32、#87）；信箱在資料庫裡（#35），
   由「維護」選單的「信箱維護…」另開視窗新增、修改、刪除人員與別名（#158）；
   各上手發行機構與保證機構的評等也在資料庫，由「發行機構評等…」維護（#237）；
   風險等級用的可連結標的白名單由「匯入可連結標的白名單…」匯入（#273）。
2. 起單資料：貼 TDCC、選一份起單建檔（#97），在右邊逐檔確認、修改後匯入資料庫（#86）。
   1、2 匯入完成後重算還沒鎖住的商品的核對條件（#88），逐檔顯示狀態；交叉比對的差異可以在這兩頁放行。
3. 準備：貼 TDCC、選輸出位置，產生批次資料夾與準備清單。
4. 核對：參考條件表＋說明書／投資人須知 PDF 的預覽、核對與儲存（`fcn_checker.panel.CheckTab`）。
5. 放回：選批次資料夾與核對結果檔，把核對通過的 TS／IIS 複製回商品資料夾；「回填通用」以 TDCC 從資料庫回填 ELN 通用回補格式（#364）。
6. 寄信草稿：貼核對通過的 TDCC，在 Outlook 建 TS／IIS 草稿給承作的 FC（#29）。
7. 人工維護：按「檢查」列出還沒鎖住、缺資料的商品（缺的格子淺紅底），或輸入 TDCC 打開任何一檔，
   在右邊補齊、修改資料庫裡的商品條件與交易明細列的值；已鎖住的照更正的規則存檔（#55、#115、#116）。
   「匯出商品條件…」把資料庫目前的 `workflow.product` 整張匯出成 Excel，位置自己選（#122）。
8. 進度：按「重新整理」列出每檔商品的作業進度、各步日期與存續狀態；依交易日、作業進度、存續狀態、上手篩選，
   點欄位排序，「匯出 Excel…」匯出目前的篩選結果（#156）。
9. 出場清單：選一個收盤日（預設收盤價表最新的一天），按「重新整理」列出那天還有效的 KO、接股、到期還本、
   顧客贖回（比價、舊清單、人工都列，寫出來源），「匯出 Excel…」匯出（#171、#188）；等股價、還沒比到那一天的商品
   另外寫出檔數與原因（#282）。

1、2、3、5、6、7、8、9 頁的結果表右上方有「全部複製」；結果表按右鍵可以只複製一格或一列，
Treeview 的表（7 的檢查結果表除外）另有 Ctrl+A 全選、Ctrl+C 複製選取的列（#121）；8 的結果表另有「複製待確認 TDCC」（#289）。

商品資料夾所在的根目錄在視窗最上方設一次，3、6 兩頁共用（#75）。
做完一步，下一頁先帶入結果並用藍色提示條寫出來，按「下一步 →」才切頁，不自動執行（#76）：
每日匯入完成 → 起單資料頁帶入交易明細的 FCN TDCC（#90）；起單資料匯入完成 → 準備頁帶入成功匯入的 TDCC（#281）；回補股價、歷史收盤價比價完成 → 出場清單帶入比到的那一天（#171）；
準備完成 → 核對頁帶入這批 PDF；核對存檔 → 放回頁帶入批次資料夾與核對結果檔；
放回完成 → 寄信草稿頁帶入通過的 TDCC（#281）。
審查通過日期、匯出回報包、信箱維護、發行機構評等、可連結標的白名單與使用的設定檔不是每天的步驟，收在視窗上方的「維護」選單。
唯讀的資料分析收在「分析」選單：「標的曝險分析…」選標的，匯出近期到期商品目前跌破了沒有，與所在的分公司、FC 與面額（#345、#352、#358）；
「重災區掃描…」不挑標的，列出近期到期、目前跌破的全部在庫商品（#362）。
視窗只收輸入、在背景執行一個動作並顯示結果，不含判斷邏輯：要連資料庫的按鈕各呼叫 `window_actions` 的一個動作，
連線、transaction 與容錯（ADR 0012）都在那裡（#125）；不連資料庫的直接呼叫 `prepare_batch`／核對／`return_to_folders`。
這個 module 只有外殼：頂端列、步驟欄、「維護」與「分析」選單、關閉、視窗大小與啟動參數；各分頁一頁一個 module 在 `sp_workflow.tabs`（#138）。
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
    BACKGROUND,
    BORDER,
    CAPTION_FONT,
    DANGER,
    FONT,
    HEADING_FONT,
    SECTION_FONT,
    STEP_DONE,
    STEP_UNDONE,
    TEXT,
    TEXT_SECONDARY,
    Background,
    apply_style,
    apply_window_icon,
    done_mark,
    enable_windows_dpi_awareness,
    set_windows_app_id,
)
from sp_workflow.check_source import EXPORT_FOLDER, WorkflowDatabase
from sp_workflow.database import default_env_path
from sp_workflow.local_settings import FolderMemory
from sp_workflow.preparation import LIST_NAME, PrepareReceipt
from sp_workflow.tabs.base import TITLE, BackgroundFactory, DatabaseLogin, RootFolder
from sp_workflow.tabs.daily import DailyImportTab
from sp_workflow.tabs.exits import ExitListTab
from sp_workflow.tabs.exposure import ExposureWindow
from sp_workflow.tabs.hotspot import HotspotWindow
from sp_workflow.tabs.mail import MailDraftTab
from sp_workflow.tabs.mailbox import MailboxWindow
from sp_workflow.tabs.manual import MaintenanceTab
from sp_workflow.tabs.orders import OrderTermsTab
from sp_workflow.tabs.prepare import PrepareTab
from sp_workflow.tabs.progress import ProgressTab
from sp_workflow.tabs.ratings import RatingsWindow
from sp_workflow.tabs.returning import ReturnTab
from sp_workflow.tabs.splits import SplitsWindow


@dataclass(frozen=True)
class _CloseWarning:
    message: str
    page: tk.Misc  # 不關閉時切去哪一頁


WINDOW_FILE = "window.json"  # 視窗上次的大小，和上次選的資料夾放在同一個 .local/
APPROVAL_DATES, EXPORT_BUNDLE = "審查通過日期…", "匯出回報包…"  # 「維護」選單依名稱更新能不能按
CLOSE_HISTORY = "匯入歷史收盤價…"
REOBSERVE = "重新比價全部"
LEGACY_BACKFILL = "匯入舊商品的價格與比價日…"
OTHER_PRODUCTS = "匯入既有的 FCN 以外商品…"
MAILBOX = "信箱維護…"
RATINGS = "發行機構評等…"
SPLITS = "拆股登記…"
WHITELIST = "匯入可連結標的白名單…"
EXPOSURE = "標的曝險分析…"
HOTSPOTS = "重災區掃描…"


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


STEP_GROUPS = (
    ("每日流程", 6),
    ("查詢與維護", 3),
)  # 步驟欄的分組：1～6 每天照順序做，7～9 需要時才用（#115、#156、#171）


def _style_shell(master: tk.Tk, pixels: Callable[[float], int]) -> None:
    """視窗外殼的樣式（#83）：頂端列與左側步驟欄和視窗底同色、用細線和內容分開（macOS 的側邊欄）；
    配色沿用 `fcn_checker.ui.apply_style` 的那一套，平面的步驟按鈕也在那裡建（#266）。"""
    style = ttk.Style(master)
    style.configure("Header.TFrame", background=BACKGROUND)
    style.configure("Header.TLabel", background=BACKGROUND, foreground=TEXT_SECONDARY)
    style.configure("HeaderWarning.TLabel", background=BACKGROUND, foreground=DANGER, font=CAPTION_FONT)
    style.configure("HeaderTitle.TLabel", background=BACKGROUND, foreground=TEXT, font=(FONT, 12, "bold"))
    style.configure("Sidebar.TFrame", background=BACKGROUND)
    style.configure("SidebarTitle.TLabel", background=BACKGROUND, foreground=TEXT_SECONDARY, font=HEADING_FONT)
    step = {"anchor": "w", "padding": (pixels(10), pixels(7)), "width": 0, "compound": "right"}
    style.configure("Step.TButton", foreground=TEXT, background=BACKGROUND, **step)
    style.map("Step.TButton", foreground=[])
    style.configure("CurrentStep.TButton", foreground=ACCENT, font=SECTION_FONT, **step)
    style.layout("Workflow.TNotebook.Tab", [])  # 用左側步驟欄切頁，不顯示分頁標籤
    style.configure("Workflow.TNotebook", background=BACKGROUND, borderwidth=0, tabmargins=0)


class StepSidebar(ttk.Frame):
    """左側步驟欄（#83）：照每天的順序列出 1～9，分成「每日流程」與「查詢與維護」兩組，按了切到那一步；
    目前所在的那一步用淺藍底標出，這次開視窗後做完的步驟（狀態列變成綠色結果條）名稱後面打綠勾。

    分頁標籤已藏起來，切頁一律經過 notebook.select（「下一步 →」也是），步驟欄跟著 <<NotebookTabChanged>> 更新。
    """

    WIDTH = 208  # 96 DPI 下的像素

    def __init__(self, parent: tk.Misc, notebook: ttk.Notebook, pixels: Callable[[float], int]) -> None:
        super().__init__(parent, style="Sidebar.TFrame", padding=(pixels(10), pixels(16)), width=pixels(self.WIDTH))
        self.pack_propagate(False)
        self.notebook = notebook
        self.steps: dict[str, ttk.Button] = {}
        self.done: set[str] = set()
        tabs = list(notebook.tabs())
        for group, (title, count) in enumerate(STEP_GROUPS):
            ttk.Label(self, text=title, style="SidebarTitle.TLabel").pack(
                anchor="w", padx=pixels(10), pady=(pixels(18) if group else 0, pixels(6))
            )
            for tab in tabs[:count]:
                number, _, name = notebook.tab(tab, "text").partition(" ")
                step = ttk.Button(
                    self, text=f"{number}   {name}", style="Step.TButton", command=lambda t=tab: notebook.select(t)
                )
                step.pack(fill=tk.X, pady=1)
                self.steps[tab] = step
            tabs = tabs[count:]
        notebook.bind("<<NotebookTabChanged>>", lambda _event: self.refresh(), add="+")
        top = self.winfo_toplevel()
        top.bind(STEP_DONE, lambda event: self._mark(event.widget, True), add="+")
        top.bind(STEP_UNDONE, lambda event: self._mark(event.widget, False), add="+")

    def refresh(self) -> None:
        current = self.notebook.select()
        for tab, step in self.steps.items():
            step.configure(style="CurrentStep.TButton" if tab == current else "Step.TButton")

    def _mark(self, widget: tk.Misc | str, done: bool) -> None:
        """狀態列在哪一頁裡，那一步就打勾（或拿掉勾）。"""
        path = str(widget)
        for tab, step in self.steps.items():
            if path.startswith(tab + "."):
                (self.done.add if done else self.done.discard)(tab)
                step.configure(image=done_mark(self) if done else "")


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
        background: BackgroundFactory = Background,
    ) -> None:
        """db 是各分頁共用的資料庫登入；session 是核對的工作階段，建好時已帶著資料庫來源（`build_window` 組好，#129）。

        `background` 建各分頁、信箱維護視窗的背景執行器（一個擁有者一個；測試傳 `ImmediateBackground`，#206）。
        """
        self.master = master
        self.session = session
        self.db = db
        self.background = background
        self.mailbox: MailboxWindow | None = None  # 「信箱維護…」開著時的視窗（#158）
        self.ratings: RatingsWindow | None = None  # 「發行機構評等…」開著時的視窗（#237）
        self.splits: SplitsWindow | None = None  # 「拆股登記…」開著時的視窗（#270）
        self.exposure: ExposureWindow | None = None  # 「標的曝險分析…」開著時的視窗（#345）
        self.hotspots: HotspotWindow | None = None  # 「重災區掃描…」開著時的視窗（#362）
        self.memory = memory
        self.size = WindowSize(memory.path.with_name(WINDOW_FILE))
        master.title(TITLE)
        self.pixels = apply_style(master)
        _style_shell(master, self.pixels)
        top_bar = ttk.Frame(master, style="Header.TFrame", padding=(self.pixels(20), self.pixels(10)))
        top_bar.pack(fill=tk.X)
        ttk.Label(top_bar, text=TITLE, style="HeaderTitle.TLabel").pack(side=tk.LEFT, padx=(0, self.pixels(32)))
        self.root_folder = RootFolder(memory)
        self.root_folder.build_bar(top_bar, self.pixels)
        tk.Frame(master, height=1, background=BORDER).pack(fill=tk.X)
        body = ttk.Frame(master)
        body.pack(fill=tk.BOTH, expand=True)
        self.notebook = ttk.Notebook(body, style="Workflow.TNotebook")
        # 分頁照每天的作業順序建立，前面自動加上 1～9 的編號；人工維護不是每天的步驟（#115），進度頁、出場清單放最後（#156、#171）
        self.daily = DailyImportTab(
            self.notebook,
            memory,
            db,
            lambda: self.session.paths,
            on_imported=lambda tdccs: self.orders.receive_tdccs(tdccs),
            on_next=lambda: self.notebook.select(self.orders.frame),
            on_observed=lambda day: self.exits.receive_day(day),
            on_exits=lambda: self.notebook.select(self.exits.frame),
            background=background,
        )
        self.orders = OrderTermsTab(
            self.notebook,
            memory,
            db,
            lambda: self.session.paths,
            on_imported=lambda tdccs: self.prepare.receive_tdccs(tdccs),
            on_next=lambda: self.notebook.select(self.prepare.frame),
            background=background,
        )
        self.prepare = PrepareTab(
            self.notebook,
            memory,
            db,
            self.root_folder,
            on_prepared=self._prepared,
            on_next=lambda: self.notebook.select(self.check),
            background=background,
        )
        self.check = CheckTab(self.notebook, self.session, self.pixels, on_saved=self._check_saved, login=db.log_in)
        self.check.add_next_step("下一步：放回 →", lambda: self.notebook.select(self.returning.frame))
        self.notebook.add(self.check, text="核對")
        self.returning = ReturnTab(
            self.notebook,
            memory,
            db,
            on_returned=lambda tdccs: self.mail.receive_tdccs(tdccs),
            on_next=lambda: self.notebook.select(self.mail.frame),
            config_paths=lambda: self.session.paths,
            background=background,
        )
        self.mail = MailDraftTab(self.notebook, memory, db, self.root_folder, background=background)
        self.manual = MaintenanceTab(self.notebook, memory, db, lambda: self.session.paths, background=background)
        self.progress = ProgressTab(self.notebook, memory, db, lambda: self.session.paths, background=background)
        self.exits = ExitListTab(self.notebook, memory, db, lambda: self.session.paths, background=background)
        tabs = self.notebook.tabs()
        for number, tab in enumerate(tabs, 1):
            self.notebook.tab(tab, text=f"{number} {self.notebook.tab(tab, 'text')}")
        self.pages = (
            self.daily,
            self.orders,
            self.prepare,
            self.returning,
            self.mail,
            self.manual,
            self.progress,
            self.exits,
        )
        for page, tab in [(p.frame, p) for p in self.pages] + [(self.check, self.check)]:
            tab.show_step(f"步驟 {self.notebook.index(page) + 1}／{len(tabs)}")
        self.sidebar = StepSidebar(body, self.notebook, self.pixels)
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y)
        tk.Frame(body, width=1, background=BORDER).pack(side=tk.LEFT, fill=tk.Y)
        self.notebook.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.notebook.select(self.check if start_on_check else 0)
        self.sidebar.refresh()
        self.maintenance = self._maintenance_menu()
        self._analysis_menu()
        self.go_menu = self._go_menu()
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
        menu.add_command(label=CLOSE_HISTORY, command=self._import_close_history)
        menu.add_command(label=REOBSERVE, command=self._reobserve)
        menu.add_command(label=LEGACY_BACKFILL, command=self._import_legacy)
        menu.add_command(label=OTHER_PRODUCTS, command=self._import_other_products)
        menu.add_command(label=MAILBOX, command=self.open_mailbox)
        menu.add_command(label=RATINGS, command=self.open_ratings)
        menu.add_command(label=SPLITS, command=self.open_splits)
        menu.add_command(label=WHITELIST, command=self._import_whitelist)
        menu.add_separator()
        menu.add_command(label="使用的設定檔…", command=self.check.show_config_paths)
        bar.add_cascade(label="維護", menu=menu)
        self.master.configure(menu=bar)
        return menu

    def _analysis_menu(self) -> None:
        """「分析」選單：唯讀的資料分析，另開視窗（#345）。"""
        bar = self.master.nametowidget(self.master["menu"])
        menu = tk.Menu(bar, tearoff=False)
        menu.add_command(label=EXPOSURE, command=self.open_exposure)
        menu.add_command(label=HOTSPOTS, command=self.open_hotspots)
        bar.add_cascade(label="分析", menu=menu)

    def _go_menu(self) -> tk.Menu:
        """「前往」選單：步驟欄的每一步也能從選單與 Ctrl+1～9 切過去（鍵盤也能操作整個流程）。"""
        bar = self.master.nametowidget(self.master["menu"])
        menu = tk.Menu(bar, tearoff=False)
        for number, tab in enumerate(self.notebook.tabs(), 1):
            menu.add_command(
                label=self.notebook.tab(tab, "text").partition(" ")[2],
                accelerator=f"Ctrl+{number}",
                command=lambda t=tab: self.notebook.select(t),
            )
            for key in (f"<Control-Key-{number}>", f"<Control-KP_{number}>"):
                self.master.bind(key, lambda _event, t=tab: self.notebook.select(t))
        bar.insert_cascade(1 if bar.type(0) == "tearoff" else 0, label="前往", menu=menu)
        return menu

    def _refresh_maintenance(self) -> None:
        """每次打開「維護」選單時，依核對頁目前的狀態決定能不能按。"""
        for label, allowed in (
            (APPROVAL_DATES, self.check.can_edit_approval_dates()),
            (EXPORT_BUNDLE, self.check.can_export_bundle()),
            (CLOSE_HISTORY, self.daily.can_import_history()),
            (REOBSERVE, self.daily.can_import_history()),
            (LEGACY_BACKFILL, self.daily.can_import_history()),
            (OTHER_PRODUCTS, self.daily.can_import_history()),
            (WHITELIST, self.daily.can_import_history()),
        ):
            self.maintenance.entryconfigure(label, state=tk.NORMAL if allowed else tk.DISABLED)

    def _export_bundle(self) -> None:
        self.notebook.select(self.check)  # 匯出的進度與結果顯示在核對頁
        self.check.export_bundle()

    def _import_close_history(self) -> None:
        self.notebook.select(self.daily.frame)  # 匯入的進度與結果顯示在每日匯入頁
        self.daily.import_history()

    def _import_legacy(self) -> None:
        self.notebook.select(self.daily.frame)  # 匯入的進度與結果顯示在每日匯入頁
        self.daily.import_legacy()

    def _import_other_products(self) -> None:
        self.notebook.select(self.daily.frame)  # 匯入的進度與結果顯示在每日匯入頁（#303）
        self.daily.import_other_products()

    def _import_whitelist(self) -> None:
        self.notebook.select(self.daily.frame)  # 匯入的進度與結果顯示在每日匯入頁（#273）
        self.daily.import_whitelist()

    def _reobserve(self) -> None:
        self.notebook.select(self.daily.frame)  # 比價的進度與結果顯示在每日匯入頁
        self.daily.reobserve()

    def open_mailbox(self) -> None:
        """另開信箱維護視窗並讀一次；已經開著就拉到前面，不重開（#158）。"""
        if self._mailbox_open():
            self.mailbox.deiconify()
            self.mailbox.lift()
            self.mailbox.focus_set()
            return
        self.mailbox = MailboxWindow(self.master, self.db, background=self.background)
        self.mailbox.refresh()

    def open_ratings(self) -> None:
        """另開發行機構評等視窗並讀一次；已經開著就拉到前面，不重開（#237）。"""
        if self._ratings_open():
            self.ratings.deiconify()
            self.ratings.lift()
            self.ratings.focus_set()
            return
        self.ratings = RatingsWindow(self.master, self.db, background=self.background)
        self.ratings.refresh()

    def open_splits(self) -> None:
        """另開拆股登記視窗並讀一次；已經開著就拉到前面，不重開（#270）。"""
        if self._splits_open():
            self.splits.deiconify()
            self.splits.lift()
            self.splits.focus_set()
            return
        self.splits = SplitsWindow(self.master, self.db, lambda: self.session.paths, background=self.background)
        self.splits.refresh()

    def open_exposure(self) -> None:
        """另開標的曝險分析視窗並讀一次；已經開著就拉到前面，不重開（#345）。"""
        if self._exposure_open():
            self.exposure.deiconify()
            self.exposure.lift()
            self.exposure.focus_set()
            return
        self.exposure = ExposureWindow(self.master, self.db, self.memory, background=self.background)
        self.exposure.refresh()

    def open_hotspots(self) -> None:
        """另開重災區掃描視窗；已經開著就拉到前面，不重開（#362）。"""
        if self._hotspots_open():
            self.hotspots.deiconify()
            self.hotspots.lift()
            self.hotspots.focus_set()
            return
        self.hotspots = HotspotWindow(self.master, self.db, self.memory, background=self.background)

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

    def _mailbox_open(self) -> bool:
        return self.mailbox is not None and bool(self.mailbox.winfo_exists())

    def _ratings_open(self) -> bool:
        return self.ratings is not None and bool(self.ratings.winfo_exists())

    def _splits_open(self) -> bool:
        return self.splits is not None and bool(self.splits.winfo_exists())

    def _exposure_open(self) -> bool:
        return self.exposure is not None and bool(self.exposure.winfo_exists())

    def _hotspots_open(self) -> bool:
        return self.hotspots is not None and bool(self.hotspots.winfo_exists())

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
        if self._mailbox_open():
            self.mailbox.close()
        if self._ratings_open():
            self.ratings.close()
        if self._splits_open():
            self.splits.close()
        if self._exposure_open():
            self.exposure.close()
        if self._hotspots_open():
            self.hotspots.close()
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


def build_window(
    master: tk.Tk, args: argparse.Namespace, *, start_on_check: bool = False, background: BackgroundFactory = Background
) -> PanelWindow:
    """依啟動參數組視窗（#129）：資料庫登入 → 核對的資料庫來源 → 建好就帶著它的核對工作階段 → 視窗。"""
    db = DatabaseLogin(master, default_env_path(args.install_root))
    session = session_from_args(args, _check_database(db, args.install_root))
    memory = _memory_under(args.install_root)
    return PanelWindow(master, memory, db, session, start_on_check=start_on_check, background=background)


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
