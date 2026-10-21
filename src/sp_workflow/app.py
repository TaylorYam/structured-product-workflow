"""FCN Workflow 視窗：六個分頁照每天的作業順序排（#73、#74、#90）。

1. 每日匯入：選每日更新，匯入最小 FCN 資料庫（#32）；信箱在資料庫直接維護（#35）。
2. Order 條件：貼 TDCC，讀商品資料夾裡的 (Order) 原信，輸出 Order 條件清單（#40）。
3. 準備：貼 TDCC、選輸出位置，產生批次資料夾與準備清單。
4. 核對：參考條件表＋說明書／投資人須知 PDF 的預覽、核對與儲存（`fcn_checker.panel.CheckTab`）。
5. 放回：選批次資料夾與核對結果檔，把核對通過的 TS／IIS 複製回商品資料夾。
6. 寄信草稿：貼核對通過的 TDCC，在 Outlook 建 TS／IIS 草稿給承作的 FC（#29）。

商品資料夾所在的根目錄在視窗最上方設一次，2、3、6 三頁共用（#75）。
做完一步，下一頁先帶入結果並用藍色提示條寫出來，按「下一步 →」才切頁，不自動執行（#76）：
每日匯入完成 → Order 條件頁帶入交易明細的 FCN TDCC（#90）；
準備完成 → 核對頁帶入這批 PDF；核對存檔 → 放回頁帶入批次資料夾與核對結果檔。
審查通過日期、匯出回報包與使用的設定檔不是每天的步驟，收在視窗上方的「維護」選單。
視窗只收輸入、在背景執行 `read_orders`／`import_daily`／`prepare_batch`／核對／`return_to_folders`／`create_drafts`
並顯示結果，不含判斷邏輯。
"""

from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from collections.abc import Callable, Sequence
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from fcn_checker.panel import (
    ACCENT,
    ACCENT_SOFT,
    BACKGROUND,
    BORDER,
    CONTROL_BORDER,
    DANGER,
    FONT,
    HOVER,
    NAVY,
    NUMBER_FONT,
    SURFACE,
    TEXT_SECONDARY,
    CheckTab,
    HintBar,
    KpiRow,
    NextButton,
    PageHeader,
    apply_style,
    apply_window_icon,
    enable_windows_dpi_awareness,
    flat,
    flat_states,
    parse_args,
    session_from_args,
    set_windows_app_id,
)
from fcn_checker.panel_workflow import PanelSession
from sp_workflow.daily_import import DailyImportError, ImportInfo, ImportReceipt, import_daily, latest_import
from sp_workflow.database import ConnectionSettings, DatabaseError, connect, default_env_path, settings_from_env
from sp_workflow.local_settings import FolderMemory
from sp_workflow.mail_drafts import (
    DRAFTED,
    NOT_NEEDED,
    DraftReceipt,
    MailDraftError,
    create_drafts,
    mail_template_from_env,
)
from sp_workflow.order_export import ExportError, ExportReceipt, can_export, export_problem_mails
from sp_workflow.order_terms import READ as ORDER_READ
from sp_workflow.order_terms import OrderReceipt, OrderTermsError, read_orders
from sp_workflow.preparation import (
    LIST_NAME,
    PREPARED,
    PreparationError,
    PrepareReceipt,
    parse_tdccs,
    prepare_batch,
)
from sp_workflow.returning import PENDING_SUFFIX, ReturnError, ReturnReceipt, return_to_folders

TITLE = "FCN Workflow"
_EXPECTED_ERRORS = (
    PreparationError,
    ReturnError,
    DailyImportError,
    DatabaseError,
    MailDraftError,
    OrderTermsError,
    ExportError,
)  # 訊息本身就是給作業人員看的


class _Tab:
    """一個分頁（#83、#85 版面）：頁首、白色輸入卡片（按鈕、完成後的重點數字與結果條）、下方結果表。

    子類別依序用 page_header、card（卡片裡用 tdcc_column、field、result_area 放內容）與 place_table 排版；
    動作在背景執行，結果回主執行緒顯示，做完用 finish 顯示重點數字並把狀態列變成綠色結果條，重新開始用 restart。
    """

    def __init__(self, notebook: ttk.Notebook, title: str, columns: Sequence[tuple[str, str, int]]) -> None:
        self.master = notebook.winfo_toplevel()
        self.results: queue.Queue[object] = queue.Queue()
        self.frame = ttk.Frame(notebook, padding=(28, 22))
        notebook.add(self.frame, text=title)
        self.frame.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar()
        self.columns = columns
        self.table = ttk.Treeview(self.frame, columns=[c for c, _, _ in columns], show="headings")
        for col, label, width in columns:
            self.table.heading(col, text=label)
            self.table.column(col, width=width, stretch=col == columns[-1][0])
        self.table.tag_configure("problem", foreground=DANGER)

    def page_header(self, title: str, description: str) -> None:
        """第 0 列：步驟 N／6（由視窗填）、標題（只寫功能）與一行說明。"""
        self.header = PageHeader(self.frame, title, description)
        self.header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 14))

    def card(self, row: int) -> ttk.Frame:
        """第 row 列的白色輸入卡片。"""
        card = ttk.Frame(self.frame, style="Card.TFrame", padding=(18, 14))
        card.grid(row=row, column=0, columnspan=2, sticky="ew")
        return card

    def body(self, card: ttk.Frame, column: int = 0) -> ttk.Frame:
        """卡片裡的一欄；有直的 TDCC 欄位時放在它右邊。"""
        card.columnconfigure(column, weight=1)
        body = ttk.Frame(card, style="CardBody.TFrame")
        body.grid(row=0, column=column, sticky="nsew", padx=(28, 0) if column else 0)
        body.columnconfigure(0, weight=1)
        return body

    def tdcc_column(self, card: ttk.Frame) -> None:
        """卡片左邊直的 TDCC 欄位（#83）：一次貼很多筆時往下長，底下即時顯示貼了幾筆（重複只算一次）。"""
        column = ttk.Frame(card, style="CardBody.TFrame")
        column.grid(row=0, column=0, sticky="ns")
        ttk.Label(column, text="TDCC", style="Card.TLabel").pack(anchor="w")
        ttk.Label(column, text="可以一次貼很多筆，換行、空白或逗號分隔", style="CardMuted.TLabel").pack(
            anchor="w", pady=(0, 6)
        )
        box = ttk.Frame(column, style="CardBody.TFrame")
        box.pack(anchor="w")
        self.tdcc_text = tk.Text(
            box,
            height=12,
            width=16,
            font=(NUMBER_FONT, 12),
            relief="flat",
            highlightthickness=1,
            highlightbackground=CONTROL_BORDER,
            highlightcolor=ACCENT,
            padx=8,
            pady=6,
            undo=True,
        )
        scroll = ttk.Scrollbar(box, command=self.tdcc_text.yview)
        self.tdcc_text.configure(yscrollcommand=scroll.set)
        self.tdcc_text.pack(side=tk.LEFT)
        scroll.pack(side=tk.LEFT, fill=tk.Y)
        self.count_var = tk.StringVar(value="已貼 0 筆")
        ttk.Label(column, textvariable=self.count_var, style="CardMuted.TLabel").pack(anchor="w", pady=(6, 0))
        self.tdcc_text.bind("<<Modified>>", self._tdcc_changed)

    def _tdcc_changed(self, _event: object = None) -> None:
        if not self.tdcc_text.edit_modified():  # 清掉旗標時也會通知一次，那次不用重算
            return
        self.tdcc_text.edit_modified(False)  # 清掉旗標，下一次修改才會再通知
        self.count_var.set(f"已貼 {len(parse_tdccs(self.tdcc_text.get('1.0', tk.END)))} 筆")

    def field(
        self, body: ttk.Frame, row: int, label: str, var: tk.StringVar, choose: Callable[[], str], note: str = ""
    ) -> int:
        """卡片裡的一個路徑欄位：標題（旁邊可加一行小字）、輸入框與「選擇…」；回傳下一個可用的列。"""
        title = ttk.Frame(body, style="CardBody.TFrame")
        title.grid(row=row, column=0, columnspan=2, sticky="w", pady=(10 if row else 0, 4))
        ttk.Label(title, text=label, style="Card.TLabel").pack(side=tk.LEFT)
        if note:
            ttk.Label(title, text=note, style="CardMuted.TLabel").pack(side=tk.LEFT, padx=(10, 0))
        ttk.Entry(body, textvariable=var).grid(row=row + 1, column=0, sticky="ew")
        ttk.Button(body, text="選擇…", command=lambda: _set_if_chosen(var, choose)).grid(
            row=row + 1, column=1, padx=(8, 0)
        )
        return row + 2

    def result_area(self, body: ttk.Frame, row: int) -> None:
        """卡片裡按鈕列、完成後的重點數字與狀態列（做完時變成綠色結果條）。"""
        self.buttons = ttk.Frame(body, style="CardBody.TFrame")
        self.buttons.grid(row=row, column=0, columnspan=2, sticky="w", pady=(16, 0))
        self.kpis = KpiRow(body)
        self.kpis.grid(row=row + 1, column=0, columnspan=2, sticky="w", pady=(16, 0))
        self.kpis.hide()
        self.status_label = ttk.Label(body, textvariable=self.status_var, style="Card.TLabel", wraplength=600)
        self.status_label.grid(row=row + 2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        body.bind("<Configure>", lambda event: self.status_label.configure(wraplength=max(300, event.width - 20)))

    def place_table(self, row: int) -> None:
        """結果表與捲軸放在第 row 列，並讓它佔滿剩下的高度。"""
        self.frame.rowconfigure(row, weight=1)
        self.table.grid(row=row, column=0, sticky="nsew", pady=(16, 0))
        scroll = ttk.Scrollbar(self.frame, orient=tk.VERTICAL, command=self.table.yview)
        scroll.grid(row=row, column=1, sticky="ns", pady=(16, 0))
        self.table.configure(yscrollcommand=scroll.set)

    def mark_done(self, done: bool) -> None:
        """狀態列：做完時是綠色結果條（#83），處理中或出錯時是一般顏色。"""
        self.status_label.configure(style="Done.TLabel" if done else "Card.TLabel")

    def restart(self) -> None:
        """重新開始：收起上一次的重點數字，狀態列恢復一般顏色。"""
        self.kpis.hide()
        self.mark_done(False)

    def finish(self, kpis: Sequence[tuple[str, int, bool]], status: str) -> None:
        """做完了：顯示重點數字，狀態列變成綠色結果條。"""
        self.kpis.show(kpis)
        self.status_var.set(status)
        self.mark_done(True)

    def run_in_background(
        self, action: Callable[[], object], busy: str, on_done: Callable[[object], None], *, clear: bool = True
    ) -> None:
        if clear:
            self.table.delete(*self.table.get_children())
        self.status_var.set(busy)

        def work() -> None:
            try:
                self.results.put(action())
            except Exception as exc:  # 背景執行緒的錯誤一律交回主執行緒顯示
                self.results.put(exc)

        def poll() -> None:
            try:
                result = self.results.get_nowait()
            except queue.Empty:
                self.master.after(200, poll)
                return
            if isinstance(result, _EXPECTED_ERRORS):
                self.status_var.set(str(result))
                messagebox.showerror(TITLE, str(result))
            elif isinstance(result, Exception):
                self.status_var.set(f"執行失敗：{result}")
                messagebox.showerror(TITLE, f"執行失敗：{result!r}")
            on_done(result)

        threading.Thread(target=work, daemon=True).start()
        self.master.after(200, poll)

    def add_row(self, values: Sequence[str], problem: bool) -> None:
        self.table.insert("", tk.END, values=list(values), tags=("problem",) if problem else ())


class RootFolder:
    """商品資料夾所在的根目錄：整個視窗只在最上方設一次，Order 條件、準備、寄信草稿三頁共用（#75）。

    記在上次選的資料夾紀錄的 `root`；以前三頁本來就共用這一筆，升級後直接沿用，不用重選。
    """

    def __init__(self, memory: FolderMemory) -> None:
        self.memory = memory
        self.var = tk.StringVar(value=memory.load().get("root", ""))

    def change(self) -> None:
        chosen = _ask_dir("選擇商品資料夾所在的根目錄", self.var)
        if chosen:
            self.var.set(str(Path(chosen)))
            self.memory.remember(root=self.var.get())

    def build_bar(self, header: tk.Misc, pixels: Callable[[float], int]) -> None:
        """視窗頂端列固定顯示根目錄與「變更…」；只能用按鈕改，避免不小心打錯。"""
        ttk.Label(header, text="根目錄", style="Header.TLabel").pack(side=tk.LEFT)
        ttk.Entry(header, textvariable=self.var, state="readonly").pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=pixels(10)
        )
        ttk.Button(header, text="變更…", command=self.change).pack(side=tk.LEFT)

    def with_output(self, out_var: tk.StringVar, out_label: str) -> tuple[Path, Path] | None:
        """按「開始」前：根目錄與這一頁的輸出位置；任一個沒設定時提示，回傳 None。"""
        root_text, out_text = self.var.get().strip(), out_var.get().strip()
        if not root_text:
            messagebox.showerror(TITLE, "請先在視窗最上方按「變更…」設定根目錄。")
            return None
        if not out_text:
            messagebox.showerror(TITLE, f"請先選擇{out_label}。")
            return None
        return Path(root_text), Path(out_text)


class PrepareTab(_Tab):
    def __init__(
        self,
        notebook: ttk.Notebook,
        memory: FolderMemory,
        root_folder: RootFolder,
        on_prepared: Callable[[PrepareReceipt], bool],
        on_next: Callable[[], None],
    ) -> None:
        super().__init__(notebook, "準備", (("tdcc", "TDCC", 130), ("status", "狀態", 70), ("reason", "原因", 520)))
        self.on_prepared = on_prepared
        self.memory = memory
        self.root_folder = root_folder
        self.batch_dir: Path | None = None
        self.out_var = tk.StringVar(value=memory.load().get("out", ""))
        self.status_var.set("貼上 TDCC，選好輸出位置後按「開始準備」（在最上方的根目錄底下找商品資料夾）。")
        self.page_header(
            "準備待核對批次",
            "在最上方的根目錄底下找出每個 TDCC 的中文說明書與投資人須知，改成標準檔名放進新的批次資料夾；"
            "商品資料夾只讀不改。",
        )
        card = self.card(1)
        self.tdcc_column(card)
        body = self.body(card, column=1)
        row = self.field(
            body,
            0,
            "輸出位置",
            self.out_var,
            lambda: _ask_dir("選擇批次資料夾要開在哪裡", self.out_var),
            "每次在這裡開一個新的「待核對_日期時間」資料夾",
        )
        self.result_area(body, row)
        self.start_button = ttk.Button(self.buttons, text="開始準備", command=self.start, style="Primary.TButton")
        self.start_button.pack(side=tk.LEFT)
        self.open_button = ttk.Button(self.buttons, text="打開批次資料夾", command=self.open_batch, state=tk.DISABLED)
        self.open_button.pack(side=tk.LEFT, padx=8)
        self.next_button = NextButton(self.buttons, "下一步：核對 →", on_next)
        self.place_table(2)

    def start(self) -> None:
        text = self.tdcc_text.get("1.0", tk.END)
        paths = self.root_folder.with_output(self.out_var, "輸出位置")
        if paths is None:
            return
        root, out = paths
        self.memory.remember(out=str(out))
        self.start_button.configure(state=tk.DISABLED)
        self.open_button.configure(state=tk.DISABLED)
        self.next_button.withdraw()
        self.restart()
        self.run_in_background(
            lambda: prepare_batch(text, root, out),
            "處理中…（在根目錄底下搜尋商品資料夾，公槽上可能要一點時間）",
            self._done,
        )

    def _done(self, result: object) -> None:
        self.start_button.configure(state=tk.NORMAL)
        if not isinstance(result, PrepareReceipt):
            return
        for o in result.outcomes:
            self.add_row((o.tdcc, o.status, o.reason), problem=o.status != PREPARED)
        prepared = sum(o.status == PREPARED for o in result.outcomes)
        skipped = len(result.outcomes) - prepared
        from_word = sum(d.from_word for o in result.outcomes if o.status == PREPARED for d in o.documents)
        status = f"完成：{prepared} 個商品已準備、{skipped} 個跳過。批次資料夾：{result.batch_dir}"
        if result.unreadable_dirs:
            status += f"\n注意：搜尋時有 {len(result.unreadable_dirs)} 個資料夾讀不到（例如 {result.unreadable_dirs[0]}），裡面的商品資料夾找不到。"
        kpis = (
            ("已準備（檔）", prepared, False),
            ("跳過（檔）", skipped, skipped > 0),
            ("Word 轉檔（份）", from_word, False),
        )
        self.finish(kpis, status)
        self.batch_dir = result.batch_dir
        self.open_button.configure(state=tk.NORMAL)
        if self.on_prepared(result):  # 核對頁真的帶入了這批 PDF，才有下一步
            self.next_button.offer()

    def open_batch(self) -> None:
        if self.batch_dir is not None:
            _open_folder(self.batch_dir)


class ReturnTab(_Tab):
    def __init__(self, notebook: ttk.Notebook, memory: FolderMemory) -> None:
        super().__init__(
            notebook,
            "放回",
            (("tdcc", "TDCC", 130), ("ts", "TS", 70), ("iis", "IIS", 70), ("reason", "原因", 450)),
        )
        self.memory = memory
        self.batch_var = tk.StringVar(value=memory.load().get("batch", ""))
        self.result_var = tk.StringVar()
        self.handed_off: tuple[str, str] | None = None  # 從核對帶入的（批次資料夾, 核對結果檔）
        self.status_var.set("選批次資料夾與「核對」分頁存下的核對結果檔，按「放回資料夾」。")

        self.page_header(
            "放回商品資料夾",
            f"把核對通過的 TS／IIS 用標準檔名複製回商品資料夾；沒通過的那份檔名加「{PENDING_SUFFIX}」。",
        )
        self.hint = HintBar(self.frame, self._change_result)
        self.hint.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        self.hint.hide()
        body = self.body(self.card(2))
        row = self.field(body, 0, "批次資料夾", self.batch_var, lambda: _ask_dir("選擇批次資料夾", self.batch_var))
        row = self.field(body, row, "核對結果檔", self.result_var, self._ask_result)
        self.result_area(body, row)
        self.start_button = ttk.Button(self.buttons, text="放回資料夾", command=self.start, style="Primary.TButton")
        self.start_button.pack(side=tk.LEFT)
        self.place_table(3)
        for var in (self.batch_var, self.result_var):
            var.trace_add("write", lambda *_: self._hide_hint_if_changed())

    def receive(self, result: Path, batch: Path | None) -> None:
        """核對存檔後帶入剛存的核對結果檔與這批的批次資料夾（#76）；不切頁、不自動放回。"""
        self.restart()  # 換了一批要放回的，上一次的結果收起來
        if batch is not None:
            self.batch_var.set(str(batch))
        self.result_var.set(str(result))
        self.handed_off = (self.batch_var.get(), self.result_var.get())
        where = (
            f"批次資料夾：{batch}"
            if batch is not None
            else "批次資料夾沒有帶入（核對的 PDF 不在同一個批次資料夾），請自己確認。"
        )
        self.hint.show(f"已帶入「4 核對」剛存的核對結果檔：\n{result}\n{where}")

    def _hide_hint_if_changed(self) -> None:
        """自己改了帶入的批次資料夾或核對結果檔，提示條就收起來。"""
        if self.handed_off is not None and (self.batch_var.get(), self.result_var.get()) != self.handed_off:
            self.handed_off = None
            self.hint.hide()

    def _change_result(self) -> None:
        _set_if_chosen(self.result_var, self._ask_result)

    def _ask_result(self) -> str:
        return filedialog.askopenfilename(
            title="選擇核對結果檔",
            initialdir=self.batch_var.get() or None,
            filetypes=[("Excel", "*.xlsx"), ("全部檔案", "*.*")],
        )

    def start(self) -> None:
        batch_text, result_text = self.batch_var.get().strip(), self.result_var.get().strip()
        if not batch_text or not result_text:
            messagebox.showerror(TITLE, "請先選擇批次資料夾與核對結果檔。")
            return
        batch, result = Path(batch_text), Path(result_text)
        self.memory.remember(batch=str(batch))
        self.start_button.configure(state=tk.DISABLED)
        self.restart()
        self.run_in_background(lambda: return_to_folders(batch, result), "放回中…", self._done)

    def _done(self, result: object) -> None:
        self.start_button.configure(state=tk.NORMAL)
        if not isinstance(result, ReturnReceipt):
            return
        for o in result.outcomes:
            self.add_row((o.tdcc, o.ts, o.iis, o.reason), problem=o.blocked or o.pending or bool(o.reason))
        blocked = sum(o.blocked for o in result.outcomes)
        pending = sum(o.pending and not o.blocked for o in result.outcomes)
        returned = len(result.outcomes) - blocked - pending
        kpis = (
            ("已放回或已在（檔）", returned, False),
            ("含待確認（檔）", pending, pending > 0),
            ("不放回（檔）", blocked, blocked > 0),
        )
        self.finish(
            kpis,
            f"完成：{returned} 個商品已放回或已在、{pending} 個含「{PENDING_SUFFIX}」檔、"
            f"{blocked} 個不放回。放回清單：{result.list_path}",
        )


class DailyImportTab(_Tab):
    def __init__(
        self,
        notebook: ttk.Notebook,
        memory: FolderMemory,
        db: _DatabaseLogin,
        on_imported: Callable[[Sequence[str]], bool],
        on_next: Callable[[], None],
    ) -> None:
        super().__init__(notebook, "每日匯入", (("tdcc", "TDCC", 130), ("kind", "結果", 150), ("detail", "說明", 440)))
        self.memory = memory
        self.db = db
        self.on_imported = on_imported
        folders = memory.load()
        self.daily_var = tk.StringVar(value=folders.get("daily", ""))
        self.status_var.set(f"選每日更新，按「匯入」。資料庫連線設定：{db.env_path}")

        self.page_header("匯入每日更新", "把每日更新的 FCN 交易與商品條件匯入資料庫；信箱在資料庫直接維護。")
        body = self.body(self.card(1))
        row = self.field(body, 0, "每日更新", self.daily_var, lambda: _ask_excel("選擇每日更新", self.daily_var))
        self.result_area(body, row)
        self.start_button = ttk.Button(self.buttons, text="匯入", command=self.start, style="Primary.TButton")
        self.start_button.pack(side=tk.LEFT)
        self.next_button = NextButton(self.buttons, "下一步：Order 條件 →", on_next)
        self.place_table(2)

    def start(self) -> None:
        daily_text = self.daily_var.get().strip()
        if not daily_text:
            messagebox.showerror(TITLE, "請先選擇每日更新。")
            return
        settings = self.db.settings()
        if settings is None:
            return
        daily = Path(daily_text)
        self.memory.remember(daily=str(daily))
        self.start_button.configure(state=tk.DISABLED)
        self.next_button.withdraw()
        self.restart()

        def run() -> ImportReceipt:
            with connect(settings) as conn:
                return import_daily(daily, conn)

        self.run_in_background(run, "匯入中…", self._done)

    def _done(self, result: object) -> None:
        self.start_button.configure(state=tk.NORMAL)
        self.db.after_run(result)
        if not isinstance(result, ImportReceipt):
            return
        for line in result.lines:
            self.add_row((line.tdcc, line.kind, line.detail), problem=line.problem)
        problems = sum(line.problem for line in result.lines)
        normal = len(result.lines) - problems
        self.finish(
            (("正常（項）", normal, False), ("要注意（項）", problems, problems > 0)),
            f"完成（第 {result.run_id} 次匯入）：{normal} 項正常、{problems} 項要注意；"
            f"略過非 FCN 交易 {result.skipped_trade_rows} 列、清單 {result.skipped_inventory_rows} 列；"
            f"取消交易 {result.cancelled_trade_rows} 列；信箱 {result.people} 人。"
            f"\n錯誤清單：{result.error_book_path}\n{self._hand_off(result)}",
        )

    def _hand_off(self, result: ImportReceipt) -> str:
        """把交易明細的 FCN TDCC 帶到 Order 條件頁（#90）；真的帶入了才有下一步。回傳寫在完成訊息的一句。"""
        if not result.fcn_trade_rows:
            return "這份交易明細沒有 FCN，Order 條件頁沒有帶入。"
        if not result.fcn_tdccs:
            return "這份交易明細的 FCN 都已取消或 TDCC 不是 12 碼，Order 條件頁沒有帶入。"
        if not self.on_imported(result.fcn_tdccs):
            return "Order 條件頁沒有帶入（正在處理，或保留原本的 TDCC）。"
        self.next_button.offer()
        return f"已把 FCN TDCC {len(result.fcn_tdccs)} 筆帶到「2 Order 條件」。"


class _DatabaseLogin:
    """各分頁共用的資料庫連線設定；`.env` 沒有密碼時詢問一次，只留在記憶體。"""

    def __init__(self, master: tk.Misc, env_path: Path) -> None:
        self.master = master
        self.env_path = env_path
        self.password: str | None = None

    def settings(self) -> ConnectionSettings | None:
        try:
            settings = settings_from_env(self.env_path)
        except DatabaseError as exc:
            messagebox.showerror(TITLE, str(exc))
            return None
        if settings.password is not None:
            return settings
        self.password = self.password or simpledialog.askstring(
            TITLE, f"{settings.user}@{settings.host} 的資料庫密碼", show="*", parent=self.master
        )
        return settings.with_password(self.password) if self.password else None

    def after_run(self, result: object) -> None:
        if isinstance(result, DatabaseError):
            self.password = None  # 可能是密碼錯，下次重新詢問


class MailDraftTab(_Tab):
    def __init__(
        self, notebook: ttk.Notebook, memory: FolderMemory, db: _DatabaseLogin, root_folder: RootFolder
    ) -> None:
        super().__init__(
            notebook,
            "寄信草稿",
            (("tdcc", "TDCC", 130), ("status", "狀態", 80), ("to", "收件人", 160), ("reason", "原因", 380)),
        )
        self.memory = memory
        self.db = db
        self.root_folder = root_folder
        self.out_var = tk.StringVar(value=memory.load().get("mail_out", ""))
        self.status_var.set(
            "貼上核對通過的 TDCC，選結果清單位置，按「建立草稿」（在最上方的根目錄底下找商品資料夾）。只建草稿，不會寄出。"
        )

        self.page_header(
            "建立寄信草稿",
            "依資料庫的承作 FC 與信箱，在 Outlook 建 TS／IIS 寄信草稿；只建草稿，不會寄出。",
        )
        card = self.card(1)
        self.tdcc_column(card)
        body = self.body(card, column=1)
        row = self.field(
            body,
            0,
            "結果清單位置",
            self.out_var,
            lambda: _ask_dir("選擇寄信草稿清單要存在哪裡", self.out_var),
            "寄信草稿清單存在這裡",
        )
        self.result_area(body, row)
        self.start_button = ttk.Button(self.buttons, text="建立草稿", command=self.start, style="Primary.TButton")
        self.start_button.pack(side=tk.LEFT)
        self.place_table(2)

    def start(self) -> None:
        text = self.tdcc_text.get("1.0", tk.END)
        paths = self.root_folder.with_output(self.out_var, "結果清單位置")
        if paths is None:
            return
        root, out = paths
        settings = self.db.settings()
        if settings is None:
            return
        try:
            template_path = mail_template_from_env(self.db.env_path)
        except MailDraftError as exc:
            messagebox.showerror(TITLE, str(exc), parent=self.master)
            return
        self.memory.remember(mail_out=str(out))
        self.start_button.configure(state=tk.DISABLED)
        self.restart()

        def check() -> ImportInfo | None:
            with connect(settings) as conn:
                return latest_import(conn)

        def confirm(result: object) -> None:
            # 先讓作業人員看到用的是哪一次每日匯入，確認後才建草稿，避免用到舊資料
            self.db.after_run(result)
            if isinstance(result, Exception):
                self.start_button.configure(state=tk.NORMAL)
                return
            question = (
                f"最近一次每日匯入：{_import_text(result)}\n\n要用這份資料建立草稿嗎？"
                if result is not None
                else "資料庫還沒有任何每日匯入紀錄。仍要繼續嗎？"
            )
            if not messagebox.askyesno(TITLE, question, parent=self.master):
                self.start_button.configure(state=tk.NORMAL)
                self.status_var.set("已取消，沒有建立任何草稿。")
                return

            def run() -> DraftReceipt:
                with connect(settings) as conn:
                    return create_drafts(text, root, out, conn, template_path=template_path)

            self.run_in_background(run, "建立草稿中…（在根目錄底下搜尋商品資料夾）", self._done)

        self.run_in_background(check, "讀取最近一次每日匯入…", confirm)

    def _done(self, result: object) -> None:
        self.start_button.configure(state=tk.NORMAL)
        self.db.after_run(result)
        if not isinstance(result, DraftReceipt):
            return
        problems = [o.status not in (DRAFTED, NOT_NEEDED) for o in result.outcomes]  # 不需寄送的不算問題
        for o, problem in zip(result.outcomes, problems, strict=True):
            self.add_row((o.tdcc, o.status, "、".join(o.to), o.reason), problem=problem)
        drafted = sum(o.status == DRAFTED for o in result.outcomes)
        not_drafted = len(result.outcomes) - drafted
        last = _import_text(result.last_import) if result.last_import else "沒有匯入紀錄"
        status = (
            f"完成：{drafted} 封草稿已建立（在 Outlook 草稿匣，請確認後再寄出）、"
            f"{not_drafted} 檔沒有建立。最近一次每日匯入：{last}。結果清單：{result.list_path}"
        )
        if result.unreadable_dirs:
            status += f"\n注意：搜尋時有 {len(result.unreadable_dirs)} 個資料夾讀不到（例如 {result.unreadable_dirs[0]}），裡面的商品資料夾找不到。"
        self.finish((("已建草稿（檔）", drafted, False), ("沒有建立（檔）", not_drafted, any(problems))), status)


class OrderTermsTab(_Tab):
    READY = (
        "貼上 TDCC，選輸出位置，按「讀取 Order」（在最上方的根目錄底下找商品資料夾）。"
        "只讀商品資料夾裡的 (Order) 原信，不會修改信件。"
    )

    def __init__(self, notebook: ttk.Notebook, memory: FolderMemory, root_folder: RootFolder) -> None:
        super().__init__(
            notebook,
            "Order 條件",
            (("tdcc", "TDCC", 130), ("status", "狀態", 90), ("issuer", "上手", 70), ("reason", "待確認原因", 440)),
        )
        self.memory = memory
        self.root_folder = root_folder
        self.list_path: Path | None = None
        self.receipt: OrderReceipt | None = None
        self.working = False  # 正在讀 Order 或匯出問題信件
        self.out_var = tk.StringVar(value=memory.load().get("order_out", ""))
        self.status_var.set(self.READY)

        self.page_header("讀取 Order 條件", "讀商品資料夾裡的 (Order) 原信，輸出 Order 條件清單；只讀，不修改信件。")
        self.handed_off: list[str] | None = None  # 從每日匯入帶入的 TDCC
        self.hint = HintBar(self.frame, self._clear_handed_off)
        self.hint.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        self.hint.hide()
        card = self.card(2)
        self.tdcc_column(card)
        body = self.body(card, column=1)
        row = self.field(
            body,
            0,
            "輸出位置",
            self.out_var,
            lambda: _ask_dir("選擇 Order 條件清單要存在哪裡", self.out_var),
            "Order 條件清單存在這裡",
        )
        self.result_area(body, row)
        self.start_button = ttk.Button(self.buttons, text="讀取 Order", command=self.start, style="Primary.TButton")
        self.start_button.pack(side=tk.LEFT)
        self.open_button = ttk.Button(self.buttons, text="打開條件清單", command=self.open_list, state=tk.DISABLED)
        self.open_button.pack(side=tk.LEFT, padx=8)
        self.export_button = ttk.Button(
            self.buttons, text="匯出問題信件", command=self.export_problems, state=tk.DISABLED
        )
        self.export_button.pack(side=tk.LEFT)
        self.place_table(3)

    def receive_tdccs(self, tdccs: Sequence[str]) -> bool:
        """每日匯入完成後帶入交易明細的 FCN TDCC（#90）；回傳是否帶入。不切頁、不自動讀 Order。

        正在讀 Order 或匯出時不帶入；已經貼了不同的 TDCC（不看順序）時先問（#93）。
        """
        if self.working:
            messagebox.showinfo(
                TITLE,
                "「2 Order 條件」正在處理，這次每日匯入的 FCN TDCC 沒有帶入；處理完後請自己貼。",
                parent=self.master,
            )
            return False
        current = parse_tdccs(self.tdcc_text.get("1.0", tk.END))
        if (
            current
            and set(current) != set(tdccs)
            and not messagebox.askyesno(
                TITLE,
                f"「2 Order 條件」已經有 {len(current)} 筆 TDCC，和這次每日匯入的 FCN TDCC（{len(tdccs)} 筆）不同。"
                "\n\n要換成每日匯入的嗎？",
                parent=self.master,
            )
        ):
            return False
        self._clear_results()  # 換了一批要讀的，上一次的結果收起來
        self.handed_off = list(tdccs)
        self._set_tdccs("\n".join(tdccs))
        self.hint.show(f"已帶入每日匯入的 FCN TDCC {len(tdccs)} 筆，確認後按「讀取 Order」。")
        return True

    def _set_tdccs(self, text: str) -> None:
        self.tdcc_text.delete("1.0", tk.END)
        self.tdcc_text.insert("1.0", text)
        self.count_var.set(f"已貼 {len(parse_tdccs(text))} 筆")

    def _tdcc_changed(self, _event: object = None) -> None:
        """自己改了帶入的 TDCC，提示條就收起來。"""
        super()._tdcc_changed(_event)
        if self.handed_off is not None and parse_tdccs(self.tdcc_text.get("1.0", tk.END)) != self.handed_off:
            self._forget_handed_off()

    def _clear_handed_off(self) -> None:
        """提示條的「改選…」：清掉帶入的 TDCC，讓作業人員自己貼。"""
        self._forget_handed_off()
        self._set_tdccs("")
        self.tdcc_text.focus_set()

    def _forget_handed_off(self) -> None:
        self.handed_off = None
        self.hint.hide()

    def _clear_results(self) -> None:
        """回到還沒讀取的樣子：清掉結果表與重點數字，狀態列回到說明，條件清單與匯出不能按。"""
        self.restart()
        self.table.delete(*self.table.get_children())
        self.status_var.set(self.READY)
        self.list_path = self.receipt = None
        self.open_button.configure(state=tk.DISABLED)
        self.export_button.configure(state=tk.DISABLED)

    def start(self) -> None:
        text = self.tdcc_text.get("1.0", tk.END)
        paths = self.root_folder.with_output(self.out_var, "輸出位置")
        if paths is None:
            return
        root, out = paths
        self.memory.remember(order_out=str(out))
        self.receipt = None
        self.working = True
        self.start_button.configure(state=tk.DISABLED)
        self.open_button.configure(state=tk.DISABLED)
        self.export_button.configure(state=tk.DISABLED)
        self.restart()
        self.run_in_background(
            lambda: read_orders(text, root, out),
            "讀取中…（在根目錄底下搜尋商品資料夾，再用 Outlook 開信件）",
            self._done,
        )

    def _done(self, result: object) -> None:
        self.working = False
        self.start_button.configure(state=tk.NORMAL)
        if not isinstance(result, OrderReceipt):
            return
        for row in result.rows:
            self.add_row((row.tdcc, row.status, row.issuer, "；".join(row.reasons)), problem=row.status != ORDER_READ)
        read = sum(row.status == ORDER_READ for row in result.rows)
        unsure = len(result.rows) - read
        status = f"完成：{read} 檔已讀取、{unsure} 檔要確認。條件清單：{result.list_path}"
        if result.unreadable_dirs:
            status += f"\n注意：搜尋時有 {len(result.unreadable_dirs)} 個資料夾讀不到（例如 {result.unreadable_dirs[0]}），裡面的商品資料夾找不到。"
        self.finish((("已讀取（檔）", read, False), ("要確認（檔）", unsure, unsure > 0)), status)
        self.list_path = result.list_path
        self.receipt = result
        self.open_button.configure(state=tk.NORMAL)
        self.export_button.configure(state=tk.NORMAL if can_export(result) else tk.DISABLED)

    def open_list(self) -> None:
        if self.list_path is not None:
            _open_folder(self.list_path)

    def export_problems(self) -> None:
        """把有問題商品的 .msg 複製到輸出位置的新資料夾（#42），結果表保留不清空。"""
        receipt = self.receipt
        if receipt is None:
            return
        out = receipt.list_path.parent
        self.working = True
        self.export_button.configure(state=tk.DISABLED)
        self.mark_done(False)
        self.run_in_background(
            lambda: export_problem_mails(receipt, out),
            "匯出問題信件中…（從商品資料夾複製 .msg）",
            self._exported,
            clear=False,
        )

    def _exported(self, result: object) -> None:
        self.working = False
        if self.receipt is not None and can_export(self.receipt):
            self.export_button.configure(state=tk.NORMAL)
        if not isinstance(result, ExportReceipt):
            return
        status = f"已匯出 {len(result.products)} 檔有問題商品的信件，共 {result.copied} 封：{result.folder}"
        if result.failures:
            status += f"\n有 {len(result.failures)} 個檔案沒複製成功，例如 {result.failures[0]}"
        status += "\n提醒：信件含真實交易與客戶資料，帶出公司請依公司的資料規定。"
        self.status_var.set(status)
        self.mark_done(True)
        _open_folder(result.folder)


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
    """左側步驟欄（#83）：照每天的順序列出 1～6，按了切到那一步；目前所在的那一步用淺藍底標出。

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
        env_path: Path | None = None,
        *,
        session: PanelSession | None = None,
        start_on_check: bool = False,
    ) -> None:
        """session 是核對的工作階段；沒給時用執行目錄的 config（開發時從專案根目錄執行）。"""
        self.master = master
        self.session = session if session is not None else PanelSession()
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
        db = _DatabaseLogin(master, env_path or default_env_path())
        # 分頁照每天的作業順序建立，前面自動加上 1～6 的編號
        self.daily = DailyImportTab(
            self.notebook,
            memory,
            db,
            on_imported=lambda tdccs: self.orders.receive_tdccs(tdccs),
            on_next=lambda: self.notebook.select(self.orders.frame),
        )
        self.orders = OrderTermsTab(self.notebook, memory, self.root_folder)
        self.prepare = PrepareTab(
            self.notebook,
            memory,
            self.root_folder,
            on_prepared=self._prepared,
            on_next=lambda: self.notebook.select(self.check),
        )
        self.check = CheckTab(self.notebook, self.session, self.pixels, on_saved=self._check_saved)
        self.check.add_next_step("下一步：放回 →", lambda: self.notebook.select(self.returning.frame))
        self.notebook.add(self.check, text="核對")
        self.returning = ReturnTab(self.notebook, memory)
        self.mail = MailDraftTab(self.notebook, memory, db, self.root_folder)
        tabs = self.notebook.tabs()
        for number, tab in enumerate(tabs, 1):
            self.notebook.tab(tab, text=f"{number} {self.notebook.tab(tab, 'text')}")
        pages = (self.daily, self.orders, self.prepare, self.returning, self.mail)
        for page, page_header in [(p.frame, p.header) for p in pages] + [(self.check, self.check.header)]:
            page_header.step.set(f"步驟 {self.notebook.index(page) + 1}／{len(tabs)}")
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
        return bool(pdfs) and self.check.receive_pdfs(
            pdfs,
            f"已帶入「3 準備」的批次（{len(pdfs)} 份 PDF），選好參考條件表後按「載入／重新載入預覽」：\n{receipt.batch_dir}",
        )

    def _check_saved(self, result: Path) -> None:
        """核對存檔：放回頁帶入剛存的核對結果檔；PDF 都在同一個有準備清單的資料夾時，那就是這批的批次資料夾（#76）。"""
        folders = {pdf.parent for pdf in self.check.pdf_paths}
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

    def _close_warning(self) -> str | None:
        if self.check.working:
            return "核對頁還在處理（讀檔、核對或存檔），關閉後要重新來。"
        if self.session.has_unsaved_result:
            return "核對結果還沒儲存，關閉後要重新核對。"
        return None

    def close(self) -> None:
        """核對頁還在處理或有還沒存檔的核對結果時先問一次；關閉前記住視窗大小。"""
        warning = self._close_warning()
        if warning and not messagebox.askyesno(
            TITLE,
            f"{warning}\n\n確定要關閉嗎？",
            icon=messagebox.WARNING,
            default=messagebox.NO,
            parent=self.master,
        ):
            self.notebook.select(self.check)
            return
        self.size.remember(self.master)
        self.check.close()
        self.master.destroy()


def _ask_dir(title: str, current: tk.StringVar) -> str:
    return filedialog.askdirectory(title=title, initialdir=current.get() or None, mustexist=True)


def _set_if_chosen(var: tk.StringVar, choose: Callable[[], str]) -> None:
    """打開選擇視窗；有選才換掉，按取消不動。"""
    chosen = choose()
    if chosen:
        var.set(chosen)


def _import_text(info: ImportInfo) -> str:
    return f"{info.imported_at:%Y-%m-%d %H:%M}（第 {info.run_id} 次，{info.file_name}）"


def _ask_excel(title: str, current: tk.StringVar) -> str:
    start = Path(current.get()).parent if current.get() else None
    return filedialog.askopenfilename(
        title=title, initialdir=start, filetypes=[("Excel", "*.xlsx"), ("全部檔案", "*.*")]
    )


def _open_folder(folder: Path) -> None:
    if sys.platform == "win32":
        os.startfile(folder)  # Windows 檔案總管
    else:
        subprocess.Popen(["xdg-open", str(folder)])


def folder_memory(argv: list[str] | None = None) -> FolderMemory:
    """依啟動參數決定上次選的資料夾記在哪：有 --install-root 就記在它底下的 .local/。"""
    return _memory_under(parse_args(argv).install_root)


def _memory_under(install_root: Path | None) -> FolderMemory:
    return FolderMemory.under(install_root) if install_root else FolderMemory()


def main(argv: list[str] | None = None, *, start_on_check: bool = False) -> None:
    """開 FCN Workflow 視窗；參數和核對 PANEL 相同（`fcn_checker.panel.parse_args`），舊的啟動方式照舊有效。"""
    args = parse_args(argv)
    enable_windows_dpi_awareness()
    set_windows_app_id()
    master = tk.Tk()
    window = PanelWindow(
        master,
        _memory_under(args.install_root),
        default_env_path(args.install_root),
        session=session_from_args(args),
        start_on_check=start_on_check,
    )
    window.restore_size()
    apply_window_icon(master)
    master.mainloop()


if __name__ == "__main__":
    main()
