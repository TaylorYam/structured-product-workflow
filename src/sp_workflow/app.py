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

商品資料夾所在的根目錄在視窗最上方設一次，3、6 兩頁共用（#75）。
做完一步，下一頁先帶入結果並用藍色提示條寫出來，按「下一步 →」才切頁，不自動執行（#76）：
每日匯入完成 → 起單資料頁帶入交易明細的 FCN TDCC（#90）；
準備完成 → 核對頁帶入這批 PDF；核對存檔 → 放回頁帶入批次資料夾與核對結果檔。
審查通過日期、匯出回報包與使用的設定檔不是每天的步驟，收在視窗上方的「維護」選單。
視窗只收輸入、在背景執行 `collect_orders`／`import_drafts`／`missing_products`／`open_maintenance`／`load_trade_rows`／`save_maintenance`／
`import_daily`／`recalculate`／`release_differences`／`prepare_batch`／核對／`return_to_folders`／`create_drafts`
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
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter import font as tkfont
from typing import TYPE_CHECKING

from fcn_checker.check_config import ConfigPaths
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
    TEXT,
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
from fcn_checker.trading_calendar import TradingCalendar
from sp_workflow.check_source import EXPORT_FOLDER, WorkflowDatabase
from sp_workflow.check_terms import (
    DONE,
    STATUSES,
    TO_CONFIRM,
    CalcLine,
    CalcReceipt,
    CheckTermsError,
    load_calendar,
    recalculate,
    release_differences,
)
from sp_workflow.daily_import import (
    DailyImportError,
    ImportInfo,
    ImportReceipt,
    PriceCsvReceipt,
    PriceSummary,
    import_daily,
    import_price_csv,
    latest_import,
    trade_value,
)
from sp_workflow.database import (
    ConnectionSettings,
    DatabaseError,
    connect,
    default_env_path,
    settings_from_env,
)
from sp_workflow.local_settings import FolderMemory
from sp_workflow.mail_drafts import (
    DRAFTED,
    NOT_NEEDED,
    DraftReceipt,
    MailDraftError,
    create_drafts,
    mail_template_from_env,
)
from sp_workflow.order_import import (
    BLOCKED,
    FIXED_IN_CORRECTION,
    IMPORTABLE,
    LOCKED,
    MAINTAIN_LOCKED,
    MAINTAIN_NEW,
    MAINTAIN_OPEN,
    NEW,
    OVERWRITTEN,
    READY,
    SAME,
    SKIPPED,
    TDCC_FIELD,
    TO_CHECK,
    CorrectionError,
    CorrectionReceipt,
    ImportConfig,
    MissingProduct,
    OrderDraft,
    OrderImportError,
    OrderImportLine,
    OrderImportReceipt,
    check_draft,
    describe,
    drafts_from,
    import_drafts,
    load_import_config,
    maintenance_problems,
    missing_products,
    open_maintenance,
    reload_stored,
    review_status,
    save_maintenance,
)
from sp_workflow.order_terms import (
    CHOICES,
    FIELDS,
    REQUIRED,
    STRIKE_FIELD,
    OrderRow,
    OrderTermsError,
    collect_orders,
    format_field,
    parse_field,
)
from sp_workflow.preparation import (
    LIST_NAME,
    PREPARED,
    PreparationError,
    PrepareReceipt,
    parse_tdccs,
    prepare_batch,
)
from sp_workflow.returning import PENDING_SUFFIX, ReturnError, ReturnReceipt, return_to_folders
from sp_workflow.trade_rows import FIELDS as TRADE_ROW_FIELDS
from sp_workflow.trade_rows import TradeRowsDraft, load_trade_rows, trade_notes, trade_problems

if TYPE_CHECKING:
    import psycopg

TITLE = "FCN Workflow"
_EXPECTED_ERRORS = (
    PreparationError,
    ReturnError,
    DailyImportError,
    DatabaseError,
    MailDraftError,
    OrderTermsError,
    OrderImportError,
    CheckTermsError,
    CorrectionError,
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
        """第 0 列：步驟 N／總數（由視窗填）、標題（只寫功能）與一行說明。"""
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
    """商品資料夾所在的根目錄：整個視窗只在最上方設一次，準備、寄信草稿兩頁共用（#75）。

    記在上次選的資料夾紀錄的 `root`；以前幾頁本來就共用這一筆，升級後直接沿用，不用重選。
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
        config_paths: Callable[[], ConfigPaths],
        on_imported: Callable[[Sequence[str]], bool],
        on_next: Callable[[], None],
    ) -> None:
        super().__init__(notebook, "每日匯入", (("tdcc", "TDCC", 130), ("kind", "結果", 150), ("detail", "說明", 440)))
        self.memory = memory
        self.db = db
        self.config_paths = config_paths
        self.on_imported = on_imported
        folders = memory.load()
        self.daily_var = tk.StringVar(value=folders.get("daily", ""))
        self.trades_only_var = tk.BooleanVar(value=False)
        self.csv_var = tk.StringVar(value=folders.get("price_csv", ""))
        self.status_var.set(f"選每日更新，按「匯入」。資料庫連線設定：{db.env_path}")

        self.page_header(
            "匯入每日更新",
            "把每日更新的 FCN 交易明細與每日股價匯入資料庫，再重算還沒鎖住的商品的核對條件；信箱在資料庫直接維護。",
        )
        body = self.body(self.card(1))
        row = self.field(body, 0, "每日更新", self.daily_var, lambda: _ask_excel("選擇每日更新", self.daily_var))
        ttk.Checkbutton(
            body,
            text="只匯交易明細（回補時用：不讀 BBG_Data、不動股價）",
            variable=self.trades_only_var,
            style="Card.TCheckbutton",
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(6, 0))
        row = self.field(
            body,
            row + 1,
            "回補股價",
            self.csv_var,
            lambda: _ask_csv("選擇股價 CSV", self.csv_var),
            "放假或請假後回補用：MarketData_YYYYMMDD.csv，一次一個",
        )
        self.result_area(body, row)
        self.start_button = ttk.Button(self.buttons, text="匯入", command=self.start, style="Primary.TButton")
        self.start_button.pack(side=tk.LEFT)
        self.csv_button = ttk.Button(self.buttons, text="回補股價", command=self.start_csv)
        self.csv_button.pack(side=tk.LEFT, padx=(8, 0))
        self.releaser = _Releaser(self, db, self._selected_tdcc, self._released, busy=self._busy)
        self.next_button = NextButton(self.buttons, "下一步：起單資料 →", on_next)
        self.place_table(2)

    def _busy(self, busy: bool) -> None:
        for button in (self.start_button, self.csv_button):
            button.configure(state=tk.DISABLED if busy else tk.NORMAL)

    def _selected_tdcc(self) -> str | None:
        selection = self.table.selection()
        return self.table.set(selection[0], "tdcc") if selection else None

    def _released(self, line: CalcLine) -> None:
        iid = f"calc-{line.tdcc}"
        if self.table.exists(iid):
            self.table.item(iid, values=_calc_row(line), tags=("problem",) if line.problem else ())

    def start(self) -> None:
        daily_text = self.daily_var.get().strip()
        if not daily_text:
            messagebox.showerror(TITLE, "請先選擇每日更新。")
            return
        settings = self.db.settings()
        if settings is None:
            return
        daily = Path(daily_text)
        prices = not self.trades_only_var.get()
        self.memory.remember(daily=str(daily))
        self._busy(True)
        self.next_button.withdraw()
        self.releaser.show(_Recalc())
        self.restart()
        paths = self.config_paths()

        def run() -> _DailyResult:
            with connect(settings) as conn:
                receipt = import_daily(daily, conn, prices=prices)
                return _DailyResult(receipt, _recalculate(conn, lambda: load_calendar(paths.exchange_holidays)))

        self.run_in_background(run, "匯入中…", self._done)

    def start_csv(self) -> None:
        """回補股價（#112）：匯入一個 MarketData CSV，再重算核對條件；交易明細不動。"""
        csv_text = self.csv_var.get().strip()
        if not csv_text:
            messagebox.showerror(TITLE, "請先選擇股價 CSV。")
            return
        settings = self.db.settings()
        if settings is None:
            return
        source = Path(csv_text)
        self.memory.remember(price_csv=str(source))
        self._busy(True)
        self.next_button.withdraw()
        self.releaser.show(_Recalc())
        self.restart()
        paths = self.config_paths()

        def run() -> _PriceCsvResult:
            with connect(settings) as conn:
                receipt = import_price_csv(source, conn)
                return _PriceCsvResult(receipt, _recalculate(conn, lambda: load_calendar(paths.exchange_holidays)))

        self.run_in_background(run, "回補股價中…", self._csv_done)

    def _csv_done(self, result: object) -> None:
        self._busy(False)
        self.db.after_run(result)
        if not isinstance(result, _PriceCsvResult):
            return
        receipt, recalc = result.receipt, result.recalc
        for line in receipt.lines:
            self.add_row((line.tdcc, line.kind, line.detail), problem=line.problem)
        self._show_recalc(recalc)
        problems = len(receipt.lines)
        kpis = [("要注意（項）", problems, problems > 0), ("股價（筆）", receipt.prices.count, False)]
        self.finish(
            kpis + self._recalc_kpis(recalc),
            f"完成（第 {receipt.run_id} 次匯入，回補股價）：{receipt.close_date:%Y-%m-%d} 收盤，"
            f"存成取價日 {receipt.prices.price_date:%Y-%m-%d}（等同隔天早上取價）。\n"
            f"{_price_text(receipt.prices)}\n{_recalc_text(recalc)}\n錯誤清單：{receipt.error_book_path}",
        )
        self.releaser.show(recalc)

    def _show_recalc(self, recalc: _Recalc) -> None:
        for calc in () if recalc.receipt is None else recalc.receipt.to_show():
            tags = ("problem",) if calc.problem else ()
            self.table.insert("", tk.END, iid=f"calc-{calc.tdcc}", values=_calc_row(calc), tags=tags)

    @staticmethod
    def _recalc_kpis(recalc: _Recalc) -> list[tuple[str, int, bool]]:
        if recalc.receipt is None:
            return []
        to_confirm = recalc.receipt.count(TO_CONFIRM)
        return [("核對條件待確認（檔）", to_confirm, to_confirm > 0)]

    def _done(self, result: object) -> None:
        self._busy(False)
        self.db.after_run(result)
        if not isinstance(result, _DailyResult):
            return
        receipt, recalc = result.receipt, result.recalc
        for line in receipt.lines:
            self.add_row((line.tdcc, line.kind, line.detail), problem=line.problem)
        self._show_recalc(recalc)
        problems = sum(line.problem for line in receipt.lines)
        normal = len(receipt.lines) - problems
        prices = receipt.prices
        kpis = [("正常（項）", normal, False), ("要注意（項）", problems, problems > 0)]
        if prices is not None:
            kpis.append(("股價（筆）", prices.count, False))
        price_text = "股價：只匯交易明細，股價沒有變動。" if prices is None else _price_text(prices)
        self.finish(
            kpis + self._recalc_kpis(recalc),
            f"完成（第 {receipt.run_id} 次匯入）：{normal} 項正常、{problems} 項要注意；"
            f"略過非 FCN 交易 {receipt.skipped_trade_rows} 列；取消交易 {receipt.cancelled_trade_rows} 列；"
            f"信箱 {receipt.people} 人。\n{price_text}\n{_recalc_text(recalc)}"
            f"\n錯誤清單：{receipt.error_book_path}\n{self._hand_off(receipt)}",
        )
        self.releaser.show(recalc)

    def _hand_off(self, result: ImportReceipt) -> str:
        """把交易明細的 FCN TDCC 帶到起單資料頁（#90）；真的帶入了才有下一步。回傳寫在完成訊息的一句。"""
        if not result.fcn_trade_rows:
            return "這份交易明細沒有 FCN，起單資料頁沒有帶入。"
        if not result.fcn_tdccs:
            return "這份交易明細的 FCN 都已取消或 TDCC 不是 12 碼，起單資料頁沒有帶入。"
        if not self.on_imported(result.fcn_tdccs):
            return "起單資料頁沒有帶入（正在處理，或保留原本的 TDCC）。"
        self.next_button.offer()
        return f"已把 FCN TDCC {len(result.fcn_tdccs)} 筆帶到「2 起單資料」。"


class _DatabaseLogin:
    """各分頁共用的資料庫連線設定；`.env` 沒有密碼時詢問一次，只留在記憶體。"""

    def __init__(self, master: tk.Misc, env_path: Path) -> None:
        self.master = master
        self.env_path = env_path
        self.password: str | None = None

    def settings(self, *, quiet: bool = False) -> ConnectionSettings | None:
        """連線設定；沒有密碼時詢問一次，取消就是 None。`quiet`：沒有連線設定檔或格式不對時不跳錯誤（讀 Order 時用）。"""
        try:
            settings = settings_from_env(self.env_path)
        except DatabaseError as exc:
            if not quiet:
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
            self.forget_password()

    def forget_password(self) -> None:
        """連不上資料庫：可能是密碼錯，下次重新詢問。"""
        self.password = None


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


_PROBLEM_BG = "#FFC7CE"  # 同 Excel 的淺紅色填滿：擋匯入，或讀起單建檔時有問題
_DIFFERENT_BG = "#FFEB9C"  # 同 Excel 的淺黃色：和資料庫的值不同
# 起單建檔的資料夾（#97）；本機沒有選過時「選擇…」先打開這裡
ORDER_SHEET_DIR = Path(r"G:\Bond Data\PM事項\0.常用工具\ELN每日起單建檔")


@dataclass(frozen=True)
class _ReadResult:
    rows: tuple[OrderRow, ...]
    drafts: list[OrderDraft]
    config: ImportConfig
    database_note: str = ""  # 沒有帶入資料庫的值時的說明
    database_error: DatabaseError | None = None


@dataclass(frozen=True)
class _Recalc:
    """匯入後重算核對條件（#88）的結果；重算失敗時只有 error（匯入已經寫進資料庫）。"""

    receipt: CalcReceipt | None = None
    calendar: TradingCalendar | None = None  # 放行後重算要用
    error: str = ""


def _recalculate(conn: psycopg.Connection, calendar: Callable[[], TradingCalendar]) -> _Recalc:
    """匯入之後重算；失敗時不丟錯誤（匯入已經完成），說明放在結果裡。"""
    import psycopg

    try:
        found = calendar()
        return _Recalc(recalculate(conn, found), found)
    except (CheckTermsError, DatabaseError, psycopg.Error) as exc:
        return _Recalc(error=_first_line(exc))


@dataclass(frozen=True)
class _DailyResult:
    receipt: ImportReceipt
    recalc: _Recalc = _Recalc()


@dataclass(frozen=True)
class _PriceCsvResult:
    receipt: PriceCsvReceipt
    recalc: _Recalc = _Recalc()


@dataclass(frozen=True)
class _Checked:
    """人工維護「檢查」的結果（#115）。"""

    products: tuple[MissingProduct, ...]
    config: ImportConfig


@dataclass(frozen=True)
class _Opened:
    """人工維護打開的一檔（#115）與它的交易明細（#116）。"""

    draft: OrderDraft
    config: ImportConfig
    trades: TradeRowsDraft | None = None


@dataclass(frozen=True)
class _SaveResult:
    receipt: OrderImportReceipt
    reload_error: str = ""  # 匯入成功但重讀資料庫狀態失敗
    recalc: _Recalc = _Recalc()


class _Releaser:
    """「放行差異…」（#88）：選到的商品有還沒放行的交叉比對差異時可以按；確認後記下放行人與時間，再重算那一檔。

    `idle` 是分頁現在能不能做別的事；`busy` 在放行前後通知分頁停用、恢復自己的按鈕（同一分頁一次只跑一件背景工作）。
    """

    def __init__(
        self,
        tab: _Tab,
        db: _DatabaseLogin,
        selected: Callable[[], str | None],
        on_released: Callable[[CalcLine], None],
        *,
        idle: Callable[[], bool] = lambda: True,
        busy: Callable[[bool], None] = lambda _busy: None,
    ) -> None:
        self.tab = tab
        self.db = db
        self.selected = selected
        self.on_released = on_released
        self.idle = idle
        self.busy = busy
        self.lines: dict[str, CalcLine] = {}
        self.calendar: TradingCalendar | None = None
        self.running = False
        self.button = ttk.Button(tab.buttons, text="放行差異…", command=self.release, state=tk.DISABLED)
        self.button.pack(side=tk.LEFT, padx=(8, 0))
        tab.table.bind("<<TreeviewSelect>>", lambda _event: self.refresh(), add="+")

    def show(self, recalc: _Recalc) -> None:
        """換成這次重算的結果（空的 `_Recalc` 是清掉）。"""
        self.lines = {} if recalc.receipt is None else {line.tdcc: line for line in recalc.receipt.lines}
        self.calendar = recalc.calendar
        self.refresh()

    def line(self) -> CalcLine | None:
        tdcc = self.selected()
        return None if tdcc is None else self.lines.get(tdcc)

    def refresh(self) -> None:
        line = self.line()
        ready = line is not None and bool(line.unreleased) and self.calendar is not None
        self.button.configure(state=tk.NORMAL if ready and not self.running and self.idle() else tk.DISABLED)

    def release(self) -> None:
        line, calendar = self.line(), self.calendar
        if line is None or not line.unreleased or calendar is None:
            return
        listing = "\n".join(f"・{difference.describe()}" for difference in line.unreleased)
        if not messagebox.askyesno(
            TITLE,
            f"{line.tdcc} 的交易明細和起單資料不一致：\n\n{listing}\n\n確認看過、要放行嗎？\n"
            "放行會記下你的 Windows 使用者名稱與時間；之後資料改變、差異跟著變時要重新放行。",
            icon=messagebox.WARNING,
            default=messagebox.NO,
            parent=self.tab.master,
        ):
            return
        settings = self.db.settings()
        if settings is None:
            return
        tdcc, differences = line.tdcc, line.unreleased
        self.running = True
        self.busy(True)
        self.refresh()

        def run() -> CalcLine:
            with connect(settings) as conn:
                return release_differences(conn, tdcc, differences, calendar)

        self.tab.run_in_background(run, f"放行中…（{tdcc}）", self._done, clear=False)

    def _done(self, result: object) -> None:
        self.running = False
        self.busy(False)
        self.db.after_run(result)
        if isinstance(result, CalcLine):
            self.lines[result.tdcc] = result
            self.on_released(result)
            self.tab.status_var.set(f"已放行 {result.tdcc} 的差異，重算後：{_calc_status(result)}。")
        self.refresh()


@dataclass(frozen=True)
class _CloseWarning:
    message: str
    page: tk.Misc  # 不關閉時切去哪一頁


class _FieldRow:
    """編輯區的一欄：欄名、輸入框（選項用下拉）、和資料庫不同時的「用資料庫的值」；說明放在下一行，有才顯示。

    `follow_width` 是 False 時說明的換行寬度由呼叫端更新（會拿掉重畫的欄，不能在 parent 上留自己的事件）。
    """

    def __init__(
        self,
        parent: tk.Frame,
        row: int,
        name: str,
        on_commit: Callable[[str], object],
        on_database: Callable[[str], None],
        *,
        follow_width: bool = True,
    ) -> None:
        self.var = tk.StringVar()
        row *= 2  # 每欄兩行：輸入一行、說明一行
        self.label = tk.Label(parent, text=name, anchor="w", background=SURFACE, font=(FONT, 10), padx=6)
        self.label.grid(row=row, column=0, sticky="nsew", pady=(1, 0))
        if name in CHOICES:
            self.input: ttk.Entry = ttk.Combobox(parent, textvariable=self.var, values=CHOICES[name], width=18)
            self.input.bind("<<ComboboxSelected>>", lambda _e: on_commit(name))
        else:
            self.input = ttk.Entry(parent, textvariable=self.var, width=20)
        self.input.grid(row=row, column=1, sticky="ew", pady=(1, 0))
        for event in ("<FocusOut>", "<Return>"):
            self.input.bind(event, lambda _e: on_commit(name))
        self.database = ttk.Button(parent, text="用資料庫的值", command=lambda: on_database(name))
        self.database.grid(row=row, column=2, sticky="w", padx=(6, 0), pady=(1, 0))
        self.database.grid_remove()
        self.note = tk.Label(parent, anchor="w", justify=tk.LEFT, background=SURFACE, font=(FONT, 9), padx=6)
        self.note.grid(row=row + 1, column=0, columnspan=3, sticky="ew", pady=(0, 2))
        self.note.grid_remove()
        if follow_width:
            parent.bind("<Configure>", lambda event: self.wrap(event.width), add="+")

    @property
    def widgets(self) -> tuple[tk.Misc, ...]:
        return self.label, self.input, self.database, self.note

    def wrap(self, width: int) -> None:
        """說明依欄位區的寬度換行。"""
        self.note.configure(wraplength=max(240, width - 20))

    def mark(self, background: str, note: str, *, offer_database: bool = False) -> None:
        for widget in (self.label, self.note):
            widget.configure(background=background)
        self.note.configure(text=note, foreground=DANGER if background == _PROBLEM_BG else TEXT_SECONDARY)
        if note:
            self.note.grid()
        else:
            self.note.grid_remove()
        if offer_database:
            self.database.grid()
        else:
            self.database.grid_remove()


class OrderEditor(ttk.Frame):
    """起單資料頁右邊的編輯區：一次一檔，欄位是 `order_terms.FIELDS`（#86、#97、ADR 0009、0010）。

    擋匯入的欄與讀起單建檔時有提醒的欄淺紅底並寫原因；和資料庫不同的欄淺黃底，寫出資料庫的值，可以一鍵改回。
    離開欄位、按 Enter 或選了下拉時才解讀；寫錯的欄保留打的字並標紅（說明記在 `OrderDraft.input_errors`，
    匯入時那一檔不匯入）。值真的變了才通知 `on_change`。`fixed` 的欄只顯示、不能改（已鎖住時的 Issue date，#55）。
    `check` 決定哪些欄標紅（起單資料用匯入的檢查，人工維護用 `maintenance_problems`，#115）。
    """

    PLACEHOLDER = "點左邊的商品來確認、修改"
    FIXED_NOTE = "TS 回填的欄位，不能更正"

    def __init__(
        self,
        parent: tk.Misc,
        config: Callable[[], ImportConfig | None],
        on_change: Callable[[OrderDraft], None],
        *,
        fixed: Collection[str] = (),
        check: Callable[[OrderDraft, ImportConfig], dict[str, str]] = check_draft,
        placeholder: str = PLACEHOLDER,
    ) -> None:
        super().__init__(parent, style="Card.TFrame", padding=(14, 12))
        self.config = config
        self.on_change = on_change
        self.fixed = frozenset(fixed)
        self.check = check
        self.placeholder = placeholder
        self.draft: OrderDraft | None = None
        self.typed: dict[str, dict[str, str]] = {}  # TDCC → {欄位: 寫錯時打的字}
        self.frozen = False  # 存檔中整個編輯區不能改
        self.title_var = tk.StringVar(value=placeholder)
        self.note_var = tk.StringVar()
        ttk.Label(self, textvariable=self.title_var, style="Card.TLabel").pack(anchor="w")
        self.note = ttk.Label(self, textvariable=self.note_var, style="CardMuted.TLabel", justify=tk.LEFT)
        self.note.pack(anchor="w", fill=tk.X, pady=(4, 8))
        self.bind("<Configure>", lambda event: self.note.configure(wraplength=max(240, event.width - 40)))
        area = ttk.Frame(self, style="CardBody.TFrame")
        area.pack(fill=tk.BOTH, expand=True)
        canvas = tk.Canvas(area, highlightthickness=0, background=SURFACE)
        scroll = ttk.Scrollbar(area, orient=tk.VERTICAL, command=canvas.yview)
        self.fields = tk.Frame(canvas, background=SURFACE)
        self.fields.columnconfigure(2, weight=1)  # 「用資料庫的值」右邊的空白吃掉多的寬度
        self.fields.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        window = canvas.create_window((0, 0), window=self.fields, anchor="nw")
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.LEFT, fill=tk.Y)
        self.rows = {
            name: _FieldRow(self.fields, i, name, self._commit, self._use_database) for i, name in enumerate(FIELDS)
        }
        self.show(None)

    @property
    def editable(self) -> bool:
        return self.draft is not None and not self.draft.locked and not self.frozen

    def show(self, draft: OrderDraft | None, row: OrderRow | None = None) -> None:
        """顯示一檔；`draft` 是 None 時（起單建檔裡沒有這檔）只顯示 `row` 的原因，欄位不能改。"""
        self.draft = draft
        typed = self.typed.get(draft.tdcc, {}) if draft else {}
        for name, field_row in self.rows.items():
            field_row.var.set("" if draft is None else typed.get(name, format_field(draft.values.get(name))))
        self._apply_state()
        if draft is None:
            self.title_var.set(self.placeholder if row is None else f"{row.tdcc}　{row.issuer}　{row.status}")
            self.note_var.set("" if row is None else "沒有可以改的條件：" + describe(row.notes))
            for field_row in self.rows.values():
                field_row.mark(SURFACE, "")
            return
        self.refresh()

    def set_frozen(self, frozen: bool) -> None:
        """存檔時整個編輯區不能改，存完再打開。"""
        self.frozen = frozen
        self._apply_state()

    def _apply_state(self) -> None:
        state = tk.NORMAL if self.editable else tk.DISABLED
        for name, field_row in self.rows.items():
            field_row.input.configure(state=tk.DISABLED if name in self.fixed else state)

    def refresh(self) -> None:
        """依目前的值重新標紅、標黃與寫說明。讀起單建檔時有問題的欄就算和資料庫不同也維持紅色，兩個說明都寫。"""
        draft = self.draft
        if draft is None:
            return
        config = self.config()
        problems = self.check(draft, config) if config else dict(draft.input_errors)
        notes = draft.notes
        differences = draft.differences()
        for name, field_row in self.rows.items():
            reading = f"起單建檔：{notes.get(name, '')}"
            if name in problems:
                field_row.mark(_PROBLEM_BG, problems[name])
            elif name in differences:
                database = f"資料庫是 {format_field(differences[name]) or '空白'}"
                if name in notes:
                    field_row.mark(_PROBLEM_BG, f"{reading}；{database}", offer_database=True)
                else:
                    field_row.mark(_DIFFERENT_BG, database, offer_database=True)
            elif name in notes:
                field_row.mark(_PROBLEM_BG, reading)
            else:
                field_row.mark(SURFACE, self.FIXED_NOTE if name in self.fixed else "")
        self.title_var.set(f"{draft.tdcc}　{draft.issuer}")
        row_notes = [problems.get(TDCC_FIELD, ""), notes.get(TDCC_FIELD, "")]
        self.note_var.set("；".join(filter(None, dict.fromkeys(row_notes))))

    def commit(self) -> bool:
        """把目前這檔畫面上的輸入都解讀一次（換檔、存檔、關閉前用）；有改到才通知，回傳有沒有改到。"""
        if not self.editable:
            return False
        changed = [self._commit(name, notify=False) for name in self.rows]
        if any(changed):
            self.refresh()
            self.on_change(self.draft)
        return any(changed)

    def _commit(self, name: str, *, notify: bool = True) -> bool:
        draft = self.draft
        if draft is None or not self.editable or name in self.fixed:
            return False
        text = self.rows[name].var.get()
        typed = self.typed.setdefault(draft.tdcc, {})
        try:
            value = parse_field(name, text)
        except ValueError as exc:
            changed = typed.get(name) != text
            typed[name] = text
            draft.input_errors[name] = str(exc)
        else:
            changed = name in typed or format_field(draft.values.get(name)) != format_field(value)
            typed.pop(name, None)
            draft.input_errors.pop(name, None)
            strike = self.rows[STRIKE_FIELD]
            follow = strike.var.get() == format_field(draft.values.get(STRIKE_FIELD))
            for other in draft.set_value(name, value, follow=follow):  # 例：Strike date 跟著 Trade date
                self.rows[other].var.set(format_field(draft.values.get(other)))
            self.rows[name].var.set(format_field(value))
        if not typed:
            self.typed.pop(draft.tdcc, None)
        if changed and notify:
            self.refresh()
            self.on_change(draft)
        return changed

    def _use_database(self, name: str) -> None:
        draft = self.draft
        if draft is None or draft.stored is None:
            return
        self.rows[name].var.set(format_field(draft.stored.get(name)))
        self._commit(name)


class TradeRowsEditor:
    """人工維護編輯區裡、商品條件底下的交易明細（#116）：一列一段，欄位是交易明細的表頭（`trade_rows.FIELDS`）。

    畫在 `OrderEditor` 的欄位區、接在商品條件後面（同一個 grid，欄寬對齊），換檔時整段重畫。只改值，不新增、
    不刪除列。和起單資料的欄一樣：離開欄位、按 Enter 才解讀（規則同每日匯入），寫錯的保留打的字並標紅；
    必填空白、備註改成取消標紅（擋存檔）；改過的標黃、寫出資料庫的值，可以一鍵改回；FC 名字對不到信箱標紅提示（不擋）。
    """

    TITLE = "交易明細"
    NOTE = (
        "只改值，不新增、不刪除列；存檔時和上面的商品條件一起寫入。"
        "改到交易日期、幣別、金額、分公司、UF 時重算核對條件（分公司配置、交叉比對）。"
    )
    EMPTY = "資料庫還沒有這檔的交易明細。"

    def __init__(self, editor: OrderEditor, on_change: Callable[[TradeRowsDraft], None]) -> None:
        self.parent = editor.fields
        self.first_row = len(FIELDS)  # 接在商品條件的欄後面（`_FieldRow` 的列號）
        self.on_change = on_change
        self.trades: TradeRowsDraft | None = None
        self.rows: dict[tuple[int, str], _FieldRow] = {}  # (row_seq, 欄) → 那一欄
        self.typed: dict[tuple[int, str], str] = {}  # 寫錯時打的字
        self.frozen = False
        self.widgets: list[tk.Misc] = []  # 這一段所有的元件（換檔時拿掉）
        self.note: tk.Label | None = None  # 標題下的說明
        self.empty: tk.Label | None = None  # 沒有交易明細時的說明
        self.parent.bind("<Configure>", lambda event: self._wrap(event.width), add="+")

    @property
    def editable(self) -> bool:
        return self.trades is not None and not self.frozen

    def show(self, trades: TradeRowsDraft | None) -> None:
        """換成這檔的交易明細；None 時（還沒打開任何一檔）整段不顯示。"""
        for widget in self.widgets:
            widget.destroy()
        self.widgets, self.rows, self.typed = [], {}, {}
        self.note = self.empty = None
        self.trades = trades
        if trades is None:
            return
        row = self.first_row
        self._label(row, self.TITLE, (FONT, 11, "bold"), pady=(14, 0))
        self.note = self._label(row, self.NOTE, (FONT, 9), foreground=TEXT_SECONDARY, second=True)
        if not trades.rows:
            self.empty = self._label(row + 1, self.EMPTY, (FONT, 10))
        for trade in trades.rows:
            row += 1
            self._label(row, f"第 {trade.seq} 列", (FONT, 10, "bold"), pady=(8, 0))
            for name in TRADE_ROW_FIELDS:
                row += 1
                key = (trade.seq, name)
                field_row = _FieldRow(
                    self.parent,
                    row,
                    name,
                    lambda _name, key=key: self._commit(key),
                    lambda _name, key=key: self._use_database(key),
                    follow_width=False,
                )
                field_row.var.set(format_field(trade.values.get(name)))
                self.rows[key] = field_row
                self.widgets += field_row.widgets
        self._wrap(self.parent.winfo_width())
        self._apply_state()
        self.refresh()

    def set_frozen(self, frozen: bool) -> None:
        """存檔時不能改，存完再打開。"""
        self.frozen = frozen
        self._apply_state()

    def refresh(self) -> None:
        """依目前的值重新標紅、標黃與寫說明。"""
        trades = self.trades
        if trades is None:
            return
        problems, notes = trade_problems(trades), trade_notes(trades)
        rows = {trade.seq: trade for trade in trades.rows}
        for key, field_row in self.rows.items():
            seq, name = key
            stored = rows[seq].stored.get(name)
            database = f"資料庫是 {format_field(stored) or '空白'}"
            if key in problems:
                field_row.mark(_PROBLEM_BG, problems[key])
            elif stored != rows[seq].values.get(name):
                if key in notes:
                    field_row.mark(_PROBLEM_BG, f"{notes[key]}；{database}", offer_database=True)
                else:
                    field_row.mark(_DIFFERENT_BG, database, offer_database=True)
            elif key in notes:
                field_row.mark(_PROBLEM_BG, notes[key])
            else:
                field_row.mark(SURFACE, "")

    def commit(self) -> bool:
        """把畫面上的輸入都解讀一次（換檔、存檔、關閉前用）；有改到才通知，回傳有沒有改到。"""
        if not self.editable:
            return False
        changed = [self._commit(key, notify=False) for key in self.rows]
        if any(changed):
            self.refresh()
            self._notify()
        return any(changed)

    def _commit(self, key: tuple[int, str], *, notify: bool = True) -> bool:
        trades = self.trades
        if trades is None or not self.editable:
            return False
        seq, name = key
        trade = next(trade for trade in trades.rows if trade.seq == seq)
        text = self.rows[key].var.get()
        try:
            value = trade_value(name, text)
        except ValueError as exc:
            changed = self.typed.get(key) != text
            self.typed[key] = text
            trade.input_errors[name] = str(exc)
        else:
            changed = key in self.typed or format_field(trade.values.get(name)) != format_field(value)
            self.typed.pop(key, None)
            trade.input_errors.pop(name, None)
            if changed:
                trade.values[name] = value
            self.rows[key].var.set(format_field(trade.values.get(name)))
        if changed and notify:
            self.refresh()
            self._notify()
        return changed

    def _use_database(self, key: tuple[int, str]) -> None:
        trades = self.trades
        if trades is None:
            return
        seq, name = key
        trade = next(trade for trade in trades.rows if trade.seq == seq)
        self.rows[key].var.set(format_field(trade.stored.get(name)))
        self._commit(key)

    def _notify(self) -> None:
        if self.trades is not None:
            self.on_change(self.trades)

    def _apply_state(self) -> None:
        state = tk.NORMAL if self.editable else tk.DISABLED
        for field_row in self.rows.values():
            field_row.input.configure(state=state)

    def _wrap(self, width: int) -> None:
        if self.note is not None:
            self.note.configure(wraplength=max(240, width - 20))
        for field_row in self.rows.values():
            field_row.wrap(width)

    def _label(
        self,
        row: int,
        text: str,
        font: tuple[object, ...],
        *,
        foreground: str = TEXT,
        second: bool = False,
        pady: tuple[int, int] = (0, 0),
    ) -> tk.Label:
        """這一段的標題或說明，佔一整行（`_FieldRow` 的列號；`second` 放在那一列的第二行）。"""
        label = tk.Label(
            self.parent, text=text, anchor="w", justify=tk.LEFT, background=SURFACE, foreground=foreground, font=font
        )
        label.grid(row=row * 2 + second, column=0, columnspan=3, sticky="ew", padx=6, pady=pady)
        self.widgets.append(label)
        return label


class OrderTermsTab(_Tab):
    READY_TEXT = "貼上 TDCC，選一份起單建檔，按「讀取」。只讀起單建檔，不會修改它。"

    def __init__(
        self,
        notebook: ttk.Notebook,
        memory: FolderMemory,
        db: _DatabaseLogin,
        config_paths: Callable[[], ConfigPaths],
    ) -> None:
        super().__init__(
            notebook,
            "起單資料",
            (
                ("tdcc", "TDCC", 130),
                ("status", "狀態", 110),
                ("issuer", "上手", 85),
                ("calc", "核對條件", 90),
                ("reason", "原因", 260),
            ),
        )
        self.memory = memory
        self.db = db
        self.config_paths = config_paths
        self.read: _ReadResult | None = None  # 最近一次讀取的結果
        self.items: dict[str, tuple[OrderRow, OrderDraft | None]] = {}  # 結果表的列 → (讀取結果, 確認資料)
        self.unsaved = False  # 有改過、還沒匯入資料庫
        self.working = False  # 正在讀取或匯入
        self.sheet_var = tk.StringVar(value=memory.load().get("order_sheet", ""))
        self.status_var.set(self.READY_TEXT)

        self.page_header(
            "確認起單資料",
            "讀作業人員的起單建檔，在右邊逐檔確認、修改後匯入資料庫；不修改起單建檔。",
        )
        self.header.grid_configure(columnspan=3)
        self.handed_off: list[str] | None = None  # 從每日匯入帶入的 TDCC
        self.hint = HintBar(self.frame, self._clear_handed_off)
        self.hint.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 12))
        self.hint.hide()
        card = self.card(2)  # 只佔左邊，和結果表同寬；右邊讓給確認區（#106）
        self.tdcc_column(card)
        self.tdcc_text.configure(height=6)  # 讓出高度給下方的結果表
        body = self.body(card, column=1)
        row = self.field(body, 0, "起單建檔", self.sheet_var, self._ask_sheet, "每天的 起單建檔_<日期>.xlsm")
        self.result_area(body, row)
        self.start_button = ttk.Button(self.buttons, text="讀取", command=self.start, style="Primary.TButton")
        self.start_button.pack(side=tk.LEFT)
        self.import_button = ttk.Button(self.buttons, text="匯入資料庫", command=self.import_now, state=tk.DISABLED)
        self.import_button.pack(side=tk.LEFT, padx=(8, 0))
        self.place_table(3)
        self.frame.columnconfigure(2, weight=1)
        self.editor = OrderEditor(self.frame, lambda: self.read.config if self.read else None, self._edited)
        self.editor.grid(row=2, rowspan=2, column=2, sticky="nsew", padx=(16, 0))  # 從卡片那一列拉到底
        self.table.bind("<<TreeviewSelect>>", self._selected)
        self.imported: dict[str, OrderImportLine] = {}  # 最近一次匯入每檔的結果（放行後重寫那一列）
        self.releaser = _Releaser(
            self, db, self._selected_tdcc, self._released, idle=lambda: not self.working, busy=self._busy
        )

    def _selected_tdcc(self) -> str | None:
        selection = self.table.selection()
        found = self.items.get(selection[0]) if selection else None
        return None if found is None else found[0].tdcc

    def _released(self, calc: CalcLine) -> None:
        for iid, (row, draft) in self.items.items():
            line = self.imported.get(row.tdcc)
            if draft is not None and line is not None and row.tdcc == calc.tdcc:
                self._show_imported(iid, row, line, calc)

    def _show_imported(self, iid: str, row: OrderRow, line: OrderImportLine, calc: CalcLine | None) -> None:
        """匯入後的一列：狀態是匯入結果，核對條件是重算後的狀態，原因寫兩邊的說明。"""
        calc_reason = f"核對條件：{calc.reason}" if calc is not None and calc.reason else ""
        problem = line.result == SKIPPED or (calc is not None and calc.problem)
        reason = "；".join(filter(None, (line.detail, calc_reason)))
        self._show_row(iid, row, line.result, reason, problem, calc.status if calc is not None else "")

    def _ask_sheet(self) -> str:
        """選起單建檔；有選就記住（沒按讀取就關視窗，下次也打開這個資料夾）。"""
        current = self.sheet_var.get().strip()
        folder = Path(current).parent if current else ORDER_SHEET_DIR
        chosen = filedialog.askopenfilename(
            title="選擇起單建檔",
            initialdir=str(folder) if folder.is_dir() else None,
            filetypes=[("Excel", "*.xlsm *.xlsx"), ("全部檔案", "*.*")],
        )
        if chosen:
            self.memory.remember(order_sheet=str(Path(chosen)))
        return chosen

    def receive_tdccs(self, tdccs: Sequence[str]) -> bool:
        """每日匯入完成後帶入交易明細的 FCN TDCC（#90）；回傳是否帶入。不切頁、不自動讀取。

        正在處理時不帶入；已經貼了不同的 TDCC（不看順序）時先問（#93）；有改過還沒匯入的內容也先問（#86）。
        """
        if self.working:
            messagebox.showinfo(
                TITLE,
                "「2 起單資料」正在處理，這次每日匯入的 FCN TDCC 沒有帶入；處理完後請自己貼。",
                parent=self.master,
            )
            return False
        current = parse_tdccs(self.tdcc_text.get("1.0", tk.END))
        if (
            current
            and set(current) != set(tdccs)
            and not messagebox.askyesno(
                TITLE,
                f"「2 起單資料」已經有 {len(current)} 筆 TDCC，和這次每日匯入的 FCN TDCC（{len(tdccs)} 筆）不同。"
                "\n\n要換成每日匯入的嗎？",
                parent=self.master,
            )
        ):
            return False
        if not self._may_discard("帶入每日匯入的 TDCC"):
            return False
        self._clear_results()  # 換了一批要讀的，上一次的結果收起來
        self.handed_off = list(tdccs)
        self._set_tdccs("\n".join(tdccs))
        self.hint.show(f"已帶入每日匯入的 FCN TDCC {len(tdccs)} 筆，選好起單建檔後按「讀取」。")
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

    def has_unsaved(self) -> bool:
        """有改過、還沒匯入資料庫的內容（先把編輯區還沒解讀的輸入解讀掉）。"""
        self.editor.commit()
        return self.unsaved

    def _may_discard(self, action: str) -> bool:
        """有改過、還沒匯入資料庫的內容時先問。"""
        return not self.has_unsaved() or messagebox.askyesno(
            TITLE,
            f"起單資料頁有改過、還沒匯入資料庫的內容，{action}後會不見。\n\n確定要繼續嗎？",
            icon=messagebox.WARNING,
            default=messagebox.NO,
            parent=self.master,
        )

    def _clear_results(self) -> None:
        """回到還沒讀取的樣子：清掉結果表、編輯區與重點數字，狀態列回到說明，按鈕不能按。"""
        self.restart()
        self.table.delete(*self.table.get_children())
        self.status_var.set(self.READY_TEXT)
        self.read = None
        self.items = {}
        self.imported = {}
        self.releaser.show(_Recalc())
        self.unsaved = False
        self.editor.typed = {}
        self.editor.show(None)
        self.import_button.configure(state=tk.DISABLED)

    def _busy(self, busy: bool) -> None:
        """處理中：按鈕與編輯區都不能用，結果表點了也不換檔。"""
        self.working = busy
        self.editor.set_frozen(busy)
        state = tk.DISABLED if busy else tk.NORMAL
        self.start_button.configure(state=state)
        has_drafts = self.read is not None and bool(self.read.drafts)
        self.import_button.configure(state=state if has_drafts else tk.DISABLED)
        self.releaser.refresh()

    def _show_row(self, iid: str, row: OrderRow, status: str, reason: str, problem: bool, calc: str = "") -> None:
        values = (row.tdcc, status, row.issuer, calc, reason)
        tags = ("problem",) if problem else ()
        if self.table.exists(iid):
            self.table.item(iid, values=values, tags=tags)
        else:
            self.table.insert("", tk.END, iid=iid, values=values, tags=tags)

    # ---------- 讀取 ----------

    def start(self) -> None:
        text = self.tdcc_text.get("1.0", tk.END)
        sheet_text = self.sheet_var.get().strip()
        if not sheet_text:
            messagebox.showerror(TITLE, "請先選擇起單建檔。", parent=self.master)
            return
        if not self._may_discard("重新讀取"):
            return
        sheet = Path(sheet_text)
        self.memory.remember(order_sheet=str(sheet))
        self._clear_results()
        settings = self.db.settings(quiet=True)  # 用來帶入資料庫的值；沒有連線設定或取消時照樣讀
        config_paths = self.config_paths()
        self._busy(True)

        def run() -> _ReadResult:
            config = load_import_config(config_paths)
            rows = collect_orders(text, sheet, config.issuer_prefixes, config.calendar)
            if settings is None:
                return _ReadResult(rows, drafts_from(rows), config, "沒有登入資料庫，沒有帶入資料庫的值")
            try:
                with connect(settings) as conn:
                    return _ReadResult(rows, drafts_from(rows, conn), config)
            except DatabaseError as exc:
                note = f"資料庫沒有連上，沒有帶入資料庫的值（{exc}）"
                return _ReadResult(rows, drafts_from(rows), config, note, exc)

        self.run_in_background(run, f"讀取中…（{sheet.name}）", self._done)

    def _done(self, result: object) -> None:
        if isinstance(result, _ReadResult):
            self.db.after_run(result.database_error)
            self.read = result
            self._fill_table(result)
        self._busy(False)
        if not isinstance(result, _ReadResult):
            return
        statuses = [self.table.set(iid, "status") for iid in self.items]
        ready = sum(status in IMPORTABLE for status in statuses)
        locked = statuses.count(LOCKED)
        todo = len(statuses) - ready - locked
        status = (
            f"讀完了：{ready} 檔可以匯入、{todo} 檔要處理、{locked} 檔已鎖住。點商品就能在右邊確認、修改，"
            "好了按「匯入資料庫」。"
        )
        if result.database_note:
            status += f"\n注意：{result.database_note}。"
        self.finish((("可匯入（檔）", ready, False), ("要處理（檔）", todo, todo > 0)), status)

    def _fill_table(self, result: _ReadResult) -> None:
        """有問題的排前面：待確認（含找不到）、請確認、可匯入、已鎖住。"""
        by_tdcc = {draft.tdcc: draft for draft in result.drafts}
        order = {BLOCKED: 0, TO_CHECK: 1, READY: 2, LOCKED: 3}
        listed = []
        for row in result.rows:
            draft = by_tdcc.get(row.tdcc) if row.found else None
            listed.append((row, draft, review_status(row, draft, result.config)))
        listed.sort(key=lambda item: order.get(item[2][0], 0))
        for i, (row, draft, (status, reason, problem)) in enumerate(listed):
            self.items[str(i)] = (row, draft)
            self._show_row(str(i), row, status, reason, problem)
        if listed:
            self.table.selection_set("0")
            self._selected()

    def _selected(self, _event: object = None) -> None:
        selection = self.table.selection()
        if self.working or not selection or selection[0] not in self.items:
            return
        row, draft = self.items[selection[0]]
        if draft is self.editor.draft and draft is not None:
            return  # 同一檔（例如 selection_set 之後又收到一次通知）
        self.editor.commit()  # 換檔前把上一檔還沒解讀的輸入解讀掉
        self.editor.show(draft, row)

    def _edited(self, draft: OrderDraft) -> None:
        """編輯區改了一檔：標成還沒匯入，結果表那一列改回匯入前的狀態。"""
        self.unsaved = True
        for iid, (row, item) in self.items.items():
            if item is draft and self.read is not None:
                self._show_row(iid, row, *review_status(row, draft, self.read.config))

    # ---------- 匯入 ----------

    def import_now(self) -> None:
        """寫錯的欄不擋整批：那一檔不匯入（OrderDraft.input_errors），其他照常匯入。"""
        read = self.read
        if read is None or not read.drafts:
            return
        self.editor.commit()
        settings = self.db.settings()
        if settings is None:
            return
        self._busy(True)
        self.mark_done(False)

        def run() -> _SaveResult:
            import psycopg

            with connect(settings) as conn:
                receipt = import_drafts(read.drafts, conn, read.config)
                recalc = _recalculate(conn, lambda: read.config.calendar)
                try:
                    reload_stored(read.drafts, conn)
                except (DatabaseError, psycopg.Error) as exc:
                    return _SaveResult(receipt, _first_line(exc), recalc)
            return _SaveResult(receipt, recalc=recalc)

        self.run_in_background(run, "匯入資料庫中…", self._saved, clear=False)

    def _saved(self, result: object) -> None:
        self.db.after_run(result)
        if isinstance(result, _SaveResult):
            self.unsaved = False
        self._busy(False)
        if not isinstance(result, _SaveResult):
            return
        receipt, calcs = result.receipt, result.recalc.receipt
        self.imported = {line.tdcc: line for line in receipt.lines}
        batch: dict[str, CalcLine] = {}
        for iid, (row, draft) in self.items.items():
            line = self.imported.get(row.tdcc) if draft is not None else None
            if line is not None:
                calc = None if calcs is None or not line.imported else calcs.line_of(row.tdcc)
                if calc is not None:
                    batch[row.tdcc] = calc
                self._show_imported(iid, row, line, calc)
        if self.editor.draft is not None:
            self.editor.show(self.editor.draft)  # 資料庫的值更新了，黃色的差異與帶入的值跟著重算
        imported = sum(line.imported for line in receipt.lines)
        not_imported = len(receipt.lines) - imported
        counts = "、".join(f"{kind} {receipt.count(kind)}" for kind in (NEW, OVERWRITTEN, SAME, SKIPPED, LOCKED))
        status = f"完成（第 {receipt.import_id} 次起單資料匯入）：{counts}。"
        if calcs is not None:
            status += f"\n核對條件（這批匯入的 {len(batch)} 檔）：{_status_counts(batch.values())}。"
        elif result.recalc.error:
            status += f"\n{_recalc_text(result.recalc)}"
        if result.reload_error:
            status += f"\n注意：已寫進資料庫，但重新讀取資料庫的值失敗（{result.reload_error}）；再匯入前請重新讀取。"
        kpis = [("已匯入（檔）", imported, False), ("沒匯入（檔）", not_imported, receipt.count(SKIPPED) > 0)]
        if calcs is not None:
            to_confirm = sum(calc.problem for calc in batch.values())
            kpis.append(("核對條件待確認（檔）", to_confirm, to_confirm > 0))
        self.finish(kpis, status)
        self.releaser.show(result.recalc)


class CellTable(ttk.Frame):
    """人工維護的檢查結果表（#115）：一列一檔，可以替每一格上色（缺的格子淺紅底）。

    `ttk.Treeview` 只能整列上色，所以用 Canvas 自己畫：第一欄（TDCC）固定在左邊，其他欄可以左右捲動，
    表頭跟著左右捲、不跟著上下捲；上下捲時左右兩塊一起動。點一列就選它並通知 `on_select`（傳那一列的 key）。
    """

    PAD = 8  # 格子左右的留白
    MIN_WIDTH = 48

    def __init__(
        self,
        parent: tk.Misc,
        first: tuple[str, str],
        columns: Sequence[tuple[str, str]],
        on_select: Callable[[str], None],
    ) -> None:
        super().__init__(parent, style="Card.TFrame")
        self.first = first  # (欄, 表頭)：固定在左邊
        self.columns = tuple(columns)  # [(欄, 表頭)]：可以左右捲動
        self.on_select = on_select
        self.font = tkfont.Font(family=FONT, size=10)
        self.header_font = tkfont.Font(family=FONT, size=10, weight="bold")
        self.row_height = self.font.metrics("linespace") + 10
        self.keys: list[str] = []
        self.selected: str | None = None
        self._rects: dict[tuple[str, str], tuple[tk.Canvas, int]] = {}  # (key, 欄) → 格子的方塊
        self._labels: dict[tuple[str, str], tuple[tk.Canvas, int]] = {}  # (key, 欄) → 格子的字
        self._missing: dict[str, frozenset[str]] = {}

        def canvas() -> tk.Canvas:
            return tk.Canvas(self, background=SURFACE, highlightthickness=0, borderwidth=0)

        self.corner, self.header, self.side, self.body = canvas(), canvas(), canvas(), canvas()
        for scrolling in (self.side, self.body):
            scrolling.configure(yscrollincrement=self.row_height)
        self.vbar = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self._yview)
        self.hbar = ttk.Scrollbar(self, orient=tk.HORIZONTAL, command=self._xview)
        self.body.configure(yscrollcommand=self.vbar.set, xscrollcommand=self.hbar.set)
        self.corner.grid(row=0, column=0, sticky="nsew")
        self.header.grid(row=0, column=1, sticky="ew")
        self.side.grid(row=1, column=0, sticky="ns")
        self.body.grid(row=1, column=1, sticky="nsew")
        self.vbar.grid(row=1, column=2, sticky="ns")
        self.hbar.grid(row=2, column=1, sticky="ew")
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)
        for widget in (self.side, self.body):
            widget.bind("<Button-1>", self._click)
            widget.bind("<MouseWheel>", lambda event: self._yview("scroll", self._steps(event), "units"))
            widget.bind("<Shift-MouseWheel>", lambda event: self._xview("scroll", self._steps(event) * 3, "units"))
        self.set_rows(())

    def set_rows(self, rows: Sequence[tuple[str, Mapping[str, str], Collection[str]]]) -> None:
        """換成這些列：(key, {欄: 顯示的字}, 缺的欄)；key 也是第一欄顯示的字。選中的那列還在就照樣選著。"""
        for widget in (self.corner, self.header, self.side, self.body):
            widget.delete("all")
        self._rects, self._labels = {}, {}
        self.keys = [key for key, _, _ in rows]
        self._missing = {key: frozenset(gone) for key, _, gone in rows}
        first, first_label = self.first
        first_width = self._width(first_label, self.keys)
        widths = [self._width(label, [cells.get(name, "") for _, cells, _ in rows]) for name, label in self.columns]
        height = self.row_height
        self._header(self.corner, 0, first_width, first_label)
        x = 0
        for (_, label), width in zip(self.columns, widths, strict=True):
            self._header(self.header, x, width, label)
            x += width
        for i, (key, cells, _) in enumerate(rows):
            y = i * height
            self._cell(self.side, key, first, 0, y, first_width, key)
            x = 0
            for (name, _), width in zip(self.columns, widths, strict=True):
                self._cell(self.body, key, name, x, y, width, cells.get(name, ""))
                x += width
        total = len(rows) * height
        self.corner.configure(width=first_width, height=height, scrollregion=(0, 0, first_width, height))
        self.side.configure(width=first_width, scrollregion=(0, 0, first_width, total))
        self.header.configure(height=height, scrollregion=(0, 0, x, height))
        self.body.configure(scrollregion=(0, 0, x, total))
        if self.selected not in self._missing:
            self.selected = None
        self._paint(self.selected, selected=True)

    def select(self, key: str | None) -> None:
        """選這一列（淺藍底，缺的格子照樣淺紅）；不在表上或 None 時不選任何一列。不通知 `on_select`。"""
        self._paint(self.selected, selected=False)
        self.selected = key if key in self._missing else None
        self._paint(self.selected, selected=True)

    def background(self, key: str, column: str) -> str:
        canvas, item = self._rects[(key, column)]
        return str(canvas.itemcget(item, "fill"))

    def text(self, key: str, column: str) -> str:
        canvas, item = self._labels[(key, column)]
        return str(canvas.itemcget(item, "text"))

    def _choose(self, key: str) -> None:
        """點了一列：選它，再通知 `on_select`（分頁決定要不要換檔，不換時用 `select` 改回去）。"""
        self.select(key)
        self.on_select(key)

    def _click(self, event: tk.Event) -> None:
        index = int(event.widget.canvasy(event.y) // self.row_height)
        if 0 <= index < len(self.keys):
            self._choose(self.keys[index])

    def _yview(self, *args: object) -> None:
        self.side.yview(*args)
        self.body.yview(*args)

    def _xview(self, *args: object) -> None:
        self.header.xview(*args)
        self.body.xview(*args)

    @staticmethod
    def _steps(event: tk.Event) -> int:
        """滾輪轉一格（Windows 是 ±120）→ 捲一列；方向和 Windows 一樣，往下轉是往下捲。"""
        return -1 if event.delta > 0 else 1

    def _width(self, label: str, texts: Iterable[str]) -> int:
        measured = [self.header_font.measure(label), *(self.font.measure(text) for text in texts)]
        return max(self.MIN_WIDTH, max(measured) + 2 * self.PAD)

    def _header(self, canvas: tk.Canvas, x: int, width: int, label: str) -> None:
        canvas.create_rectangle(x, 0, x + width, self.row_height, fill=BACKGROUND, outline=BORDER)
        canvas.create_text(
            x + self.PAD, self.row_height // 2, text=label, anchor="w", font=self.header_font, fill=TEXT_SECONDARY
        )

    def _cell(self, canvas: tk.Canvas, key: str, name: str, x: int, y: int, width: int, text: str) -> None:
        fill = _PROBLEM_BG if name in self._missing[key] else SURFACE
        rect = canvas.create_rectangle(x, y, x + width, y + self.row_height, fill=fill, outline=BORDER)
        label = canvas.create_text(
            x + self.PAD, y + self.row_height // 2, text=text, anchor="w", font=self.font, fill=TEXT
        )
        self._rects[(key, name)] = (canvas, rect)
        self._labels[(key, name)] = (canvas, label)

    def _paint(self, key: str | None, *, selected: bool) -> None:
        if key is None:
            return
        for (row, name), (canvas, rect) in self._rects.items():
            if row == key and name not in self._missing[key]:
                canvas.itemconfigure(rect, fill=ACCENT_SOFT if selected else SURFACE)


class MaintenanceTab(_Tab):
    """「7 人工維護」（#115、ADR 0017）：檢查缺資料的商品，手動補齊、修改資料庫裡的商品條件。

    左邊是「檢查」的結果（`CellTable`，缺的格子淺紅底），也可以輸入 TDCC 打開任何一檔；右邊是和起單資料頁
    同一套的編輯區。已鎖住的照更正的規則存檔（ADR 0014），還沒鎖住的可以留空白；每次存檔都留紀錄。
    """

    READY_TEXT = "按「檢查」列出還沒鎖住、缺資料的商品；或輸入 TDCC 按「打開」，改任何一檔（包括已鎖住的）。"
    INTROS = {
        MAINTAIN_LOCKED: (
            "已核對通過、條件已鎖住：存檔照更正的規則，商品維持鎖住、核對結果不動，算過核對條件的商品會重算價格。"
            "不能留空白；Issue date 由 TS 回填，不能改。"
        ),
        MAINTAIN_OPEN: (
            "還沒鎖住：存檔寫進資料庫並重算核對條件。必填欄可以先空著（淺紅底），缺欄時核對條件停在待確認、不能核對；"
            "重新匯入起單資料時照舊整檔覆蓋。"
        ),
        MAINTAIN_NEW: (
            "資料庫還沒有這檔的商品條件（只有交易明細）：存檔時新增，上手依上手編號對照。"
            "必填欄可以先空著，缺欄時核對條件停在待確認、不能核對。"
        ),
    }
    COLUMNS = (("issuer", "上手"), ("status", "狀態"), *((name, name) for name in REQUIRED))

    def __init__(self, notebook: ttk.Notebook, db: _DatabaseLogin, config_paths: Callable[[], ConfigPaths]) -> None:
        super().__init__(notebook, "人工維護", ())
        self.db = db
        self.config_paths = config_paths
        self.config: ImportConfig | None = None  # 最近一次檢查或打開時讀的設定
        self.working = False  # 正在檢查、讀取或存檔
        self.checked: dict[str, MissingProduct] = {}  # 最近一次檢查的結果（依 TDCC 排）
        self.status_var.set(self.READY_TEXT)

        self.page_header(
            "維護商品條件",
            "檢查還缺資料的商品，手動補齊或修改資料庫裡的商品條件；已鎖住的照更正的規則，每次存檔都留紀錄。",
        )
        self.header.grid_configure(columnspan=3)
        body = self.body(self.card(1))
        title = ttk.Frame(body, style="CardBody.TFrame")
        title.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))
        ttk.Label(title, text="TDCC", style="Card.TLabel").pack(side=tk.LEFT)
        ttk.Label(title, text="直接打開任何一檔，包括已鎖住、沒缺資料的", style="CardMuted.TLabel").pack(
            side=tk.LEFT, padx=(10, 0)
        )
        self.tdcc_var = tk.StringVar()
        entry = ttk.Entry(body, textvariable=self.tdcc_var, width=18, font=(NUMBER_FONT, 11))
        entry.grid(row=1, column=0, sticky="w")
        entry.bind("<Return>", lambda _event: self.open_typed())
        self.open_button = ttk.Button(body, text="打開", command=self.open_typed)
        self.open_button.grid(row=1, column=1, sticky="w", padx=(8, 0))
        body.columnconfigure(0, weight=0)
        body.columnconfigure(1, weight=1)
        self.result_area(body, 2)
        self.check_button = ttk.Button(self.buttons, text="檢查", command=self.check, style="Primary.TButton")
        self.check_button.pack(side=tk.LEFT)

        self.cells = CellTable(self.frame, ("tdcc", "TDCC"), self.COLUMNS, self._selected)
        self.cells.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(16, 0))
        self.frame.rowconfigure(2, weight=1)
        self.frame.columnconfigure(2, weight=1)
        side = ttk.Frame(self.frame)
        side.grid(row=1, rowspan=2, column=2, sticky="nsew", padx=(16, 0))  # 從卡片那一列拉到底
        self.intro_var = tk.StringVar()
        intro = ttk.Label(side, textvariable=self.intro_var, style="Muted.TLabel", justify=tk.LEFT)
        intro.pack(anchor="w", fill=tk.X)
        side.bind("<Configure>", lambda event: intro.configure(wraplength=max(240, event.width - 20)))
        self.editor = OrderEditor(
            side,
            lambda: self.config,
            self._edited,
            check=maintenance_problems,
            placeholder="點左邊的商品，或輸入 TDCC 打開",
        )
        self.editor.pack(fill=tk.BOTH, expand=True, pady=(6, 8))
        self.trade_editor = TradeRowsEditor(self.editor, lambda trades: self._edited_tdcc(trades.tdcc))
        actions = ttk.Frame(side, style="Card.TFrame", padding=(14, 10))
        actions.pack(fill=tk.X)
        actions.columnconfigure(1, weight=1)
        ttk.Label(actions, text="原因（選填）", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        self.reason_var = tk.StringVar()
        self.reason_entry = ttk.Entry(actions, textvariable=self.reason_var)
        self.reason_entry.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        buttons = ttk.Frame(actions, style="CardBody.TFrame")
        buttons.grid(row=1, column=0, columnspan=2, sticky="e", pady=(10, 0))
        self.save_button = ttk.Button(buttons, text="存檔", command=self.save, style="Primary.TButton")
        self.save_button.pack(side=tk.LEFT)
        self.reload_button = ttk.Button(buttons, text="重新載入", command=self.reload)
        self.reload_button.pack(side=tk.LEFT, padx=(8, 0))
        self._busy(False)

    def _busy(self, busy: bool) -> None:
        """處理中：按鈕與編輯區都不能用；還沒打開任何一檔時不能存檔、重新載入。"""
        self.working = busy
        self.editor.set_frozen(busy)
        self.trade_editor.set_frozen(busy)
        state = tk.DISABLED if busy else tk.NORMAL
        for widget in (self.check_button, self.open_button):
            widget.configure(state=state)
        has_draft = state if self.editor.draft is not None else tk.DISABLED
        for widget in (self.save_button, self.reload_button, self.reason_entry):
            widget.configure(state=has_draft)

    def has_unsaved(self) -> bool:
        """有改過、還沒存檔的內容（先把編輯區還沒解讀的輸入解讀掉），包括交易明細。"""
        self.editor.commit()
        self.trade_editor.commit()
        draft, trades = self.editor.draft, self.trade_editor.trades
        terms = draft is not None and bool(draft.edits() or draft.input_errors)
        return terms or (trades is not None and bool(trades.edits() or trades.has_input_errors()))

    def _may_discard(self, action: str) -> bool:
        return not self.has_unsaved() or messagebox.askyesno(
            TITLE,
            f"人工維護頁有改過、還沒存檔的內容，{action}後會不見。\n\n確定要繼續嗎？",
            icon=messagebox.WARNING,
            default=messagebox.NO,
            parent=self.master,
        )

    def _current_tdcc(self) -> str | None:
        return None if self.editor.draft is None else self.editor.draft.tdcc

    # ---------- 檢查 ----------

    def check(self) -> None:
        if self.working:
            return
        settings = self.db.settings()
        if settings is None:
            return
        paths = self.config_paths()
        self._busy(True)
        self.restart()

        def run() -> _Checked:
            config = load_import_config(paths)
            with connect(settings) as conn:
                return _Checked(missing_products(conn, config), config)

        self.run_in_background(run, "檢查中…", self._checked, clear=False)

    def _checked(self, result: object) -> None:
        self.db.after_run(result)
        self._busy(False)
        if not isinstance(result, _Checked):
            return
        self.config = result.config
        self.checked = {product.tdcc: product for product in result.products}
        self._fill_cells()
        found = len(self.checked)
        new = sum(product.status == MAINTAIN_NEW for product in self.checked.values())
        if found:
            status = (
                f"檢查完了：{found} 檔還沒鎖住而且缺資料（其中 {new} 檔只有交易明細）。"
                "缺的格子是淺紅底，點一列在右邊補齊。"
            )
        else:
            status = "檢查完了：沒有還沒鎖住、缺資料的商品。"
        self.finish((("缺資料（檔）", found, found > 0), ("只有交易明細（檔）", new, False)), status)

    def _fill_cells(self) -> None:
        rows = [
            (
                product.tdcc,
                {
                    "issuer": product.issuer,
                    "status": product.status,
                    **{name: format_field(product.values.get(name)) for name in REQUIRED},
                },
                product.missing,
            )
            for product in self.checked.values()
        ]
        self.cells.set_rows(rows)
        self.cells.select(self._current_tdcc())

    # ---------- 打開 ----------

    def _selected(self, tdcc: str) -> None:
        if self.working or tdcc == self._current_tdcc():
            self.cells.select(self._current_tdcc())
            return
        self.open(tdcc)

    def open_typed(self) -> None:
        tdccs = parse_tdccs(self.tdcc_var.get())
        if len(tdccs) != 1:
            messagebox.showerror(TITLE, "請輸入一筆 TDCC。", parent=self.master)
            return
        self.open(tdccs[0])

    def reload(self) -> None:
        """重新讀資料庫目前的值（例如存檔時說打開之後被改過）。"""
        tdcc = self._current_tdcc()
        if tdcc is not None:
            self.open(tdcc, action="重新載入")

    def open(self, tdcc: str, *, action: str = "") -> None:
        """讀這檔目前的條件到右邊；有改過、還沒存檔的先問。"""
        if self.working or not self._may_discard(action or f"打開 {tdcc}"):
            self.cells.select(self._current_tdcc())
            return
        settings = self.db.settings()
        if settings is None:
            self.cells.select(self._current_tdcc())
            return
        paths = self.config_paths()
        self._busy(True)

        def run() -> _Opened:
            config = load_import_config(paths)
            with connect(settings) as conn:
                return _Opened(open_maintenance(conn, tdcc, config), config, load_trade_rows(conn, tdcc))

        self.run_in_background(run, f"讀取 {tdcc} 的條件中…", self._opened, clear=False)

    def _opened(self, result: object) -> None:
        self.db.after_run(result)
        if isinstance(result, _Opened):
            self.config = result.config
            self._show(result.draft, result.trades)
            self.status_var.set(f"已打開 {result.draft.tdcc}（{result.draft.row.status}），改好按「存檔」。")
        self._busy(False)
        self.cells.select(self._current_tdcc())

    def _show(self, draft: OrderDraft, trades: TradeRowsDraft | None) -> None:
        self.editor.fixed = frozenset(FIXED_IN_CORRECTION if draft.row.status == MAINTAIN_LOCKED else ())
        self.editor.typed = {}
        self.editor.show(draft)
        self.trade_editor.show(trades)
        self.intro_var.set(self.INTROS[draft.row.status])
        self.reason_var.set("")

    def _edited(self, draft: OrderDraft) -> None:
        self._edited_tdcc(draft.tdcc)

    def _edited_tdcc(self, tdcc: str) -> None:
        self.mark_done(False)
        self.status_var.set(f"{tdcc} 有改過、還沒存檔。")

    # ---------- 存檔 ----------

    def save(self) -> None:
        draft, trades, config = self.editor.draft, self.trade_editor.trades, self.config
        if self.working or draft is None or config is None:
            return
        self.editor.commit()
        self.trade_editor.commit()
        settings = self.db.settings()
        if settings is None:
            return
        reason = self.reason_var.get()
        self._busy(True)
        self.mark_done(False)

        def run() -> CorrectionReceipt:
            with connect(settings) as conn:
                return save_maintenance(conn, draft, config, trades=trades, reason=reason)

        self.run_in_background(run, f"存檔中…（{draft.tdcc}）", self._saved, clear=False)

    def _saved(self, result: object) -> None:
        self.db.after_run(result)
        self._busy(False)
        if not isinstance(result, CorrectionReceipt):
            return  # 錯誤已顯示；畫面上的值不動，可以改好再存或重新載入
        draft = self.editor.draft
        if result.saved and draft is not None:
            # 已換成資料庫存的值：黃色的差異清掉，只有交易明細的新增了商品條件時變成未鎖住
            self._show(draft, self.trade_editor.trades)
        if result.correction_id is not None and draft is not None:
            self._update_checked(result, draft)
        self.status_var.set(_correction_text(result))
        self.mark_done(result.saved)

    def _update_checked(self, receipt: CorrectionReceipt, draft: OrderDraft) -> None:
        """檢查結果裡有這檔時跟著更新：補齊了就拿掉，還缺的寫入後的值與缺的欄。"""
        if receipt.tdcc not in self.checked:
            return
        if receipt.missing:
            listed = self.checked[receipt.tdcc]
            self.checked[receipt.tdcc] = MissingProduct(
                receipt.tdcc, listed.issuer, MAINTAIN_OPEN, dict(draft.values), receipt.missing
            )
        else:
            del self.checked[receipt.tdcc]
        self._fill_cells()


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
        if self.session.database is None:  # 核對分頁可以改從資料庫讀條件（#68）
            self.session.database = WorkflowDatabase(
                db.settings,
                self.session.install_root / "runtime" / EXPORT_FOLDER,
                on_failure=db.forget_password,
            )
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
        self.check = CheckTab(self.notebook, self.session, self.pixels, on_saved=self._check_saved)
        self.check.add_next_step("下一步：放回 →", lambda: self.notebook.select(self.returning.frame))
        self.notebook.add(self.check, text="核對")
        self.returning = ReturnTab(self.notebook, memory)
        self.mail = MailDraftTab(self.notebook, memory, db, self.root_folder)
        self.manual = MaintenanceTab(self.notebook, db, lambda: self.session.paths)
        tabs = self.notebook.tabs()
        for number, tab in enumerate(tabs, 1):
            self.notebook.tab(tab, text=f"{number} {self.notebook.tab(tab, 'text')}")
        pages = (self.daily, self.orders, self.prepare, self.returning, self.mail, self.manual)
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
        then = "" if self.check.uses_database else "選好參考條件表後"
        return bool(pdfs) and self.check.receive_pdfs(
            pdfs,
            f"已帶入「3 準備」的批次（{len(pdfs)} 份 PDF），{then}按「載入／重新載入預覽」：\n{receipt.batch_dir}",
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
        self.master.destroy()


def _ask_dir(title: str, current: tk.StringVar) -> str:
    return filedialog.askdirectory(title=title, initialdir=current.get() or None, mustexist=True)


def _set_if_chosen(var: tk.StringVar, choose: Callable[[], str]) -> None:
    """打開選擇視窗；有選才換掉，按取消不動。"""
    chosen = choose()
    if chosen:
        var.set(chosen)


def _price_text(prices: PriceSummary) -> str:
    """完成訊息裡的股價一句（#87）：取價日、筆數、取代了幾筆、衝突的代碼。"""
    text = f"股價：取價日 {prices.price_date:%Y-%m-%d}，{prices.count} 筆"
    if prices.replaced:
        text += f"（取代了這一天原本的 {prices.replaced} 筆）"
    if prices.conflicts:
        text += f"；值不一樣的重複代碼 {len(prices.conflicts)} 個，價格沒有存：{'、'.join(prices.conflicts)}"
    return text + "。"


def _calc_status(line: CalcLine) -> str:
    return line.status if line.status == DONE or not line.reason else f"{line.status}（{line.reason}）"


def _calc_row(line: CalcLine) -> tuple[str, str, str]:
    """每日匯入結果表的一列：核對條件重算後的狀態。"""
    return line.tdcc, f"核對條件：{line.status}", line.reason


def _correction_text(receipt: CorrectionReceipt) -> str:
    """人工維護存檔（已鎖住的是更正）的結果：改了什麼（前 → 後）、重算後的核對條件、對不到信箱的 FC 名字。"""
    if not receipt.saved:
        return f"{receipt.tdcc} 沒有改任何欄位，沒有寫入。"
    tdcc, text = receipt.tdcc, ""
    changes = _changes_text(receipt.changes)
    if receipt.correction_id is not None and receipt.locked:
        text += f"已更正 {tdcc}（第 {receipt.correction_id} 次更正）：{changes}。"
    elif receipt.correction_id is not None:
        action = "新增" if receipt.created else "存檔"
        text += f"已{action} {tdcc} 的商品條件（第 {receipt.correction_id} 筆紀錄）：{changes}。"
    if receipt.trade_correction_id is not None:
        rows = _changes_text(
            (f"第 {seq} 列 {name}", before, after) for seq, name, before, after in receipt.trade_changes
        )
        text += f"已改 {tdcc} 的交易明細（第 {receipt.trade_correction_id} 筆紀錄）：{rows}。"
        if receipt.correction_id is None and receipt.changes:
            text += f"重算後改變的價格：{changes}。"
    if receipt.locked:
        text += "商品維持鎖住，核對結果沒有變。"
    if receipt.calc is not None:
        text += f"\n核對條件重算後：{_calc_status(receipt.calc)}。"
    if receipt.unmatched:
        names = "、".join(receipt.unmatched)
        text += f"\n注意：FC 名字對不到信箱：{names}（寄信草稿找不到收件人；請確認名字或在信箱表補上）。"
    return text


def _changes_text(changes: Iterable[tuple[str, object, object]]) -> str:
    return "、".join(
        f"{name} {format_field(before) or '空白'} → {format_field(after) or '空白'}" for name, before, after in changes
    )


def _status_counts(lines: Iterable[CalcLine]) -> str:
    statuses = [line.status for line in lines]
    return "、".join(f"{status} {statuses.count(status)}" for status in STATUSES)


def _recalc_text(recalc: _Recalc) -> str:
    """完成訊息裡的核對條件一句（#88）。"""
    if recalc.receipt is None:
        return f"注意：匯入已寫進資料庫，但核對條件沒有重算（{recalc.error}）；排除後重新匯入一次就會重算。"
    receipt = recalc.receipt
    return (
        f"核對條件（還沒鎖住的 {len(receipt.lines)} 檔，這次有變 {receipt.changed} 檔）："
        f"{_status_counts(receipt.lines)}。"
    )


def _first_line(exc: Exception) -> str:
    return str(exc).splitlines()[0] if str(exc) else type(exc).__name__


def _import_text(info: ImportInfo) -> str:
    return f"{info.imported_at:%Y-%m-%d %H:%M}（第 {info.run_id} 次，{info.file_name}）"


def _ask_excel(title: str, current: tk.StringVar) -> str:
    start = Path(current.get()).parent if current.get() else None
    return filedialog.askopenfilename(
        title=title, initialdir=start, filetypes=[("Excel", "*.xlsx"), ("全部檔案", "*.*")]
    )


def _ask_csv(title: str, current: tk.StringVar) -> str:
    start = Path(current.get()).parent if current.get() else None
    return filedialog.askopenfilename(title=title, initialdir=start, filetypes=[("CSV", "*.csv"), ("全部檔案", "*.*")])


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
