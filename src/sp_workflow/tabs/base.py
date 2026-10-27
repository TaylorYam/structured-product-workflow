"""分頁共用的基底（#138 從 `sp_workflow.app` 搬出）：分頁版面 `Tab`、視窗頂端的根目錄、各分頁共用的資料庫登入與選檔對話框。

背景動作只有一種寫法 `ActionRunner`（#206）：分頁、放行差異與信箱維護各持有一個，登入、停用按鈕、背景執行、
顯示錯誤都在這裡；背景執行器由視窗傳入（`BackgroundFactory`，測試傳 `fcn_checker.ui.ImmediateBackground`）。
這裡不 import 其他分頁 module；分頁 module 與視窗外殼都從這裡 import。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import TypeVar

from fcn_checker.ui import (
    CODE_FONT,
    DANGER,
    SURFACE,
    TEXT,
    Background,
    CopyArea,
    FieldBox,
    FitTreeview,
    KpiRow,
    PageHeader,
    Scrollbar,
    TableCopy,
    WrapLabel,
    show_done,
)
from sp_workflow.database import (
    ConnectionSettings,
    DatabaseError,
    OperatorError,
    SchemaOutdatedError,
    settings_from_env,
)
from sp_workflow.local_settings import FolderMemory
from sp_workflow.preparation import parse_tdccs
from sp_workflow.step_records import Recorded

TITLE = "FCN Workflow"
R = TypeVar("R")
BackgroundFactory = Callable[[tk.Misc], Background]  # 一個擁有者一個；測試傳介面相同的 `ImmediateBackground`
COPY_HINT = "按右鍵可以只複製一格或一列；Ctrl+A 全選、Ctrl+C 複製選取的列"  # 結果表上方（#121）
CELL_COPY_HINT = "按右鍵可以只複製一格或一列"  # 人工維護的檢查結果表（Canvas，不能用 Ctrl+A／Ctrl+C）


class Tab:
    """一個分頁（#83、#85 版面）：頁首、白色輸入卡片（按鈕、完成後的重點數字與結果條）、下方結果表。

    子類別依序用 page_header、card（卡片裡用 tdcc_column、field、result_area 放內容）與 place_table 排版；
    動作用 `runner`（`ActionRunner`）在背景執行，處理中停用哪些按鈕寫在 `_busy`；做完用 finish 顯示重點數字並把
    狀態列變成綠色結果條，重新開始用 restart。
    """

    def __init__(
        self,
        notebook: ttk.Notebook,
        title: str,
        columns: Sequence[tuple[str, str, int]],
        db: DatabaseLogin,
        background: BackgroundFactory = Background,
    ) -> None:
        self.master = notebook.winfo_toplevel()
        self.db = db
        self.background = background(self.master)
        self.frame = ttk.Frame(notebook, padding=(32, 24))
        notebook.add(self.frame, text=title)
        self.frame.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar()
        self.runner = ActionRunner(db, self.background, self.status_var, self._busy, reset=self._reset)
        self.columns = columns
        self.table_area = CopyArea(self.frame, COPY_HINT, lambda: self.copier.copy_all())  # #121
        self.table = FitTreeview(self.table_area.card, columns=[c for c, _, _ in columns], show="headings")
        for col, label, width in columns:
            self.table.heading(col, text=label)
            # 最後一欄跟著視窗變寬，但不縮到比內容窄（窄了就左右捲動，#179）；欄寬依內容（#192）
            last = col == columns[-1][0]
            self.table.column(col, width=width, minwidth=width if last else 20, stretch=last)
        self.table.tag_configure("problem", foreground=DANGER)
        self.copier = TableCopy(self.table)

    def page_header(self, title: str, description: str) -> None:
        """第 0 列：步驟 N／總數（由視窗用 show_step 填）、標題（只寫功能）與一行說明。"""
        self.header = PageHeader(self.frame, title, description)
        self.header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 16))

    def show_step(self, step: str) -> None:
        self.header.step.set(step)

    def card(self, row: int) -> ttk.Frame:
        """第 row 列的白色輸入卡片。"""
        card = ttk.Frame(self.frame, style="Card.TFrame", padding=(20, 16))
        card.grid(row=row, column=0, columnspan=2, sticky="ew")
        return card

    def body(self, card: ttk.Frame, column: int = 0) -> ttk.Frame:
        """卡片裡的一欄；有直的 TDCC 欄位時放在它右邊。"""
        card.columnconfigure(column, weight=1)
        body = ttk.Frame(card, style="CardBody.TFrame")
        body.grid(row=0, column=column, sticky="nsew", padx=(24, 0) if column else 0)
        body.columnconfigure(0, weight=1)
        return body

    def tdcc_column(self, card: ttk.Frame) -> None:
        """卡片左邊直的 TDCC 欄位（#83）：一次貼很多筆時往下長，底下即時顯示貼了幾筆（重複只算一次）。"""
        column = ttk.Frame(card, style="CardBody.TFrame")
        column.grid(row=0, column=0, sticky="ns")
        ttk.Label(column, text="TDCC", style="CardSection.TLabel").pack(anchor="w")
        ttk.Label(column, text="可以一次貼很多筆，換行、空白或逗號分隔", style="CardMuted.TLabel").pack(
            anchor="w", pady=(2, 8)
        )
        box = FieldBox(column)
        box.pack(anchor="w")
        self.tdcc_text = tk.Text(
            box,
            height=12,
            width=16,
            font=CODE_FONT,
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            background=SURFACE,
            foreground=TEXT,
            insertbackground=TEXT,
            padx=4,
            pady=2,
            undo=True,
        )
        box.watch(self.tdcc_text)
        scroll = Scrollbar(box, command=self.tdcc_text.yview)
        self.tdcc_text.configure(yscrollcommand=scroll.set)
        self.tdcc_text.pack(side=tk.LEFT)
        scroll.pack(side=tk.LEFT, fill=tk.Y)
        self.count_var = tk.StringVar(value="已貼 0 筆")
        ttk.Label(column, textvariable=self.count_var, style="CardMuted.TLabel").pack(anchor="w", pady=(8, 0))
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
        title.grid(row=row, column=0, columnspan=2, sticky="w", pady=(12 if row else 0, 6))
        ttk.Label(title, text=label, style="CardSection.TLabel").pack(side=tk.LEFT)
        if note:
            ttk.Label(title, text=note, style="CardMuted.TLabel").pack(side=tk.LEFT, padx=(10, 0))
        ttk.Entry(body, textvariable=var).grid(row=row + 1, column=0, sticky="ew")
        ttk.Button(body, text="選擇…", command=lambda: set_if_chosen(var, choose)).grid(
            row=row + 1, column=1, padx=(8, 0)
        )
        return row + 2

    def result_area(self, body: ttk.Frame, row: int) -> None:
        """卡片裡按鈕列、完成後的重點數字與狀態列（做完時變成綠色結果條）。"""
        self.buttons = ttk.Frame(body, style="CardBody.TFrame")
        self.buttons.grid(row=row, column=0, columnspan=2, sticky="w", pady=(16, 0))  # 主要的動作放最左邊
        self.kpis = KpiRow(body)
        self.kpis.grid(row=row + 1, column=0, columnspan=2, sticky="w", pady=(16, 0))
        self.kpis.hide()
        self.status_label = WrapLabel(body, textvariable=self.status_var, style="Card.TLabel", wraplength=600)
        self.status_label.grid(row=row + 2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        body.bind("<Configure>", lambda event: self.status_label.configure(wraplength=max(300, event.width - 20)))

    def place_table(self, row: int) -> None:
        """結果表與上下、左右捲軸放在第 row 列，並讓它佔滿剩下的高度；表的右上方是「全部複製」（#121）。

        欄位比畫面寬時用下方的捲軸左右捲動，看得到最右邊的欄（#179）。
        """
        self.frame.rowconfigure(row, weight=1)
        self.table_area.grid(row=row, column=0, columnspan=2, sticky="nsew", pady=(20, 0))
        self.table.grid(row=0, column=0, sticky="nsew")
        scroll = Scrollbar(self.table_area.card, orient=tk.VERTICAL, command=self.table.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.hbar = Scrollbar(self.table_area.card, orient=tk.HORIZONTAL, command=self.table.xview)
        self.hbar.grid(row=1, column=0, sticky="ew")
        self.table.configure(yscrollcommand=scroll.set, xscrollcommand=self.hbar.set)

    def selected_item(self) -> str | None:
        """選取的列裡作業人員點的那一列；Ctrl+A 全選時不換（#121）。沒點過時是第一個選取的列。"""
        selection = self.table.selection()
        if not selection:
            return None
        focus = self.table.focus()
        return focus if focus in selection else selection[0]

    def mark_done(self, done: bool) -> None:
        """狀態列：做完時是綠色結果條（#83），處理中或出錯時是一般顏色。"""
        show_done(self.status_label, done, "Card.TLabel")

    def restart(self) -> None:
        """重新開始：收起上一次的重點數字，狀態列恢復一般顏色。"""
        self.kpis.hide()
        self.mark_done(False)

    @property
    def working(self) -> bool:
        """正在背景做事（一次只跑一件）。"""
        return self.runner.working

    def _busy(self, busy: bool) -> None:
        """處理中停用、做完恢復分頁自己的按鈕；子類別覆寫（基底沒有要停用的）。"""

    def _reset(self, clear: bool) -> None:
        """`clear`（預設）：開始前清掉結果表並 restart；不清時由呼叫端的 `before` 決定要不要收起上一次的結果。"""
        if clear:
            self.table.delete(*self.table.get_children())
            self.restart()

    def finish(self, kpis: Sequence[tuple[str, int, bool]], status: str) -> None:
        """做完了：顯示重點數字，狀態列變成綠色結果條。"""
        self.kpis.show(kpis)
        self.status_var.set(status)
        self.mark_done(True)

    def add_row(self, values: Sequence[str], problem: bool) -> None:
        self.table.insert("", tk.END, values=list(values), tags=("problem",) if problem else ())


class ActionRunner:
    """「跑一個背景動作」的唯一寫法（#206）；分頁、放行差異與信箱維護各持有一個，一次只跑一件。

    1. 登入：`run_action` 要連線設定（沒有或取消就不做）；`run_maybe` 可以沒有（準備、放回、讀起單建檔：
       沒有連線設定檔或取消時 action 拿到 None，照樣做）；`run_local` 不連資料庫（匯出畫面上的表）。
    2. 開始：`working`、擁有者的 `set_busy(True)`、`reset(clear)`、呼叫端的 `before`，狀態列寫處理中的說明（`busy`），
       背景執行。
    3. 做完（主執行緒）：`db.after_run`（連不上時忘記密碼；成功的結果裡記著的資料庫錯誤用 `database_error` 取出），
       `working` 結束；出錯時顯示原因（`OperatorError` 的訊息直接給作業人員看），成功才呼叫 `on_result`；
       最後擁有者的 `set_busy(False)`，`on_result` 又開始下一件時不呼叫。中間任何一步丟出例外，`working` 與按鈕
       照樣恢復，不會卡住。
    """

    def __init__(
        self,
        db: DatabaseLogin,
        background: Background,
        status: tk.StringVar,
        set_busy: Callable[[bool], None],
        *,
        reset: Callable[[bool], None] = lambda _clear: None,
        parent: tk.Misc | None = None,
    ) -> None:
        self.db = db
        self.background = background
        self.status = status
        self.set_busy = set_busy
        self.reset = reset
        self.parent = parent  # 錯誤訊息框掛在哪個視窗（None：主視窗）
        self.working = False

    def run_action(
        self,
        action: Callable[[ConnectionSettings], R],
        busy: str,
        on_result: Callable[[R], None],
        *,
        clear: bool = True,
        before: Callable[[], None] | None = None,
        database_error: Callable[[R], DatabaseError | None] | None = None,
    ) -> bool:
        """要連線設定的動作；回傳有沒有開始（正在做別的、沒有連線設定或取消登入時 False）。"""
        if self.working:
            return False
        settings = self.db.settings()
        if settings is None:
            return False
        return self._start(lambda: action(settings), busy, on_result, clear, before, database_error)

    def run_maybe(
        self,
        action: Callable[[ConnectionSettings | None], R],
        busy: str,
        on_result: Callable[[R], None],
        *,
        clear: bool = True,
        before: Callable[[], None] | None = None,
        database_error: Callable[[R], DatabaseError | None] | None = None,
    ) -> bool:
        """不一定要連線設定的動作：沒有連線設定檔時不跳錯誤、取消登入時照樣做，action 拿到 None。"""
        if self.working:
            return False
        settings = self.db.settings(quiet=True)
        return self._start(lambda: action(settings), busy, on_result, clear, before, database_error)

    def run_local(
        self,
        action: Callable[[], R],
        busy: str,
        on_result: Callable[[R], None],
        *,
        clear: bool = True,
        before: Callable[[], None] | None = None,
    ) -> bool:
        """不連資料庫的動作（例：匯出畫面上的表）：不登入。"""
        if self.working:
            return False
        return self._start(action, busy, on_result, clear, before, None)

    def _start(
        self,
        call: Callable[[], R],
        busy: str,
        on_result: Callable[[R], None],
        clear: bool,
        before: Callable[[], None] | None,
        database_error: Callable[[R], DatabaseError | None] | None,
    ) -> bool:
        self.working = True
        self.set_busy(True)
        try:
            self.reset(clear)
            if before is not None:
                before()
            self.status.set(busy)
            self.background.run(call, lambda future: self._done(future, on_result, database_error))
        except BaseException:
            self.working = False
            self.set_busy(False)
            raise
        return True

    def _done(
        self,
        future: Future[R],
        on_result: Callable[[R], None],
        database_error: Callable[[R], DatabaseError | None] | None,
    ) -> None:
        error = future.exception()  # 背景執行緒的錯誤一律交回主執行緒顯示
        try:
            try:
                if error is None:
                    result = future.result()
                    self.db.after_run(None if database_error is None else database_error(result))
                else:
                    self.db.after_run(error)
            finally:
                self.working = False
            if error is None:
                on_result(result)
            else:
                self._show_error(error)
        finally:
            if not self.working:  # on_result 接著開始下一件時，按鈕留給那一件恢復
                self.set_busy(False)

    def _show_error(self, error: BaseException) -> None:
        if isinstance(error, OperatorError):  # 訊息本身就是給作業人員看的
            status, message = str(error), str(error)
        else:
            status, message = f"執行失敗：{error}", f"執行失敗：{error!r}"
        self.status.set(status)
        if self.parent is None:
            messagebox.showerror(TITLE, message)
        else:
            messagebox.showerror(TITLE, message, parent=self.parent)


class RootFolder:
    """商品資料夾所在的根目錄：整個視窗只在最上方設一次，準備、寄信草稿兩頁共用（#75）。

    記在上次選的資料夾紀錄的 `root`；以前幾頁本來就共用這一筆，升級後直接沿用，不用重選。
    """

    def __init__(self, memory: FolderMemory) -> None:
        self.memory = memory
        self.var = tk.StringVar(value=memory.load().get("root", ""))

    def change(self) -> None:
        chosen = ask_dir("選擇商品資料夾所在的根目錄", self.var)
        if chosen:
            self.var.set(str(Path(chosen)))
            self.memory.remember(root=self.var.get())

    def build_bar(self, header: tk.Misc, pixels: Callable[[float], int]) -> None:
        """視窗頂端列固定顯示根目錄與「變更…」；只能用按鈕改，避免不小心打錯。還沒設定時旁邊用紅字提醒。"""
        ttk.Label(header, text="根目錄", style="Header.TLabel").pack(side=tk.LEFT)
        ttk.Entry(header, textvariable=self.var, state="readonly", style="Path.TEntry").pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=pixels(10)
        )
        ttk.Button(header, text="變更…", command=self.change).pack(side=tk.RIGHT)
        missing = ttk.Label(header, text="尚未設定：準備、寄信草稿要用", style="HeaderWarning.TLabel")

        def show_missing(*_args: object) -> None:
            if self.var.get().strip():
                missing.pack_forget()
            else:
                missing.pack(side=tk.RIGHT, padx=(0, pixels(10)))

        self.var.trace_add("write", show_missing)
        show_missing()

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


class DatabaseLogin:
    """各分頁共用的資料庫連線設定；`.env` 沒有密碼時詢問一次，只留在記憶體。

    `settings` 在主執行緒登入（需要時問密碼）；核對的資料庫來源在背景用 `current` 拿登入過的設定，不會問（#129）。
    """

    def __init__(self, master: tk.Misc, env_path: Path) -> None:
        self.master = master
        self.env_path = env_path
        self.password: str | None = None
        self._current: ConnectionSettings | None = None

    def settings(self, *, quiet: bool = False) -> ConnectionSettings | None:
        """連線設定；沒有密碼時詢問一次，取消就是 None。`quiet`：沒有連線設定檔或格式不對時不跳錯誤（讀 Order 時用）。"""
        settings = self._ask(quiet)
        if settings is not None:  # 沒拿到時不覆蓋：別的分頁取消或安靜地讀失敗，不會把核對頁登出
            self._current = settings
        return settings

    def log_in(self) -> bool:
        """在主執行緒登入（需要時問密碼）；取消或沒有連線設定時 False。"""
        return self.settings() is not None

    def current(self) -> ConnectionSettings | None:
        """最近一次 `settings` 拿到的連線設定，不詢問；還沒登入或忘記密碼後是 None。"""
        return self._current

    def _ask(self, quiet: bool) -> ConnectionSettings | None:
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
        """背景動作做完（`ActionRunner`）：連不上資料庫時忘記密碼，下次重新詢問；版本不對不是密碼錯。"""
        if isinstance(result, DatabaseError) and not isinstance(result, SchemaOutdatedError):
            self.forget_password()

    def forget_password(self) -> None:
        """連不上資料庫：可能是密碼錯，下次重新詢問。"""
        self.password = None
        self._current = None


def record_note(recorded: Recorded) -> str:
    """步驟紀錄（#151）沒有寫進資料庫時接在完成訊息後面的一句；寫進去了是空字串。"""
    if recorded.skipped:
        return "\n注意：沒有登入資料庫（沒有連線設定或取消登入），這次沒有留步驟紀錄（清單照常產出）。"
    if recorded.error:
        return f"\n注意：步驟紀錄沒有寫進資料庫：{recorded.error}（清單照常產出）。"
    return ""


def ask_dir(title: str, current: tk.StringVar) -> str:
    return filedialog.askdirectory(title=title, initialdir=current.get() or None, mustexist=True)


def set_if_chosen(var: tk.StringVar, choose: Callable[[], str]) -> None:
    """打開選擇視窗；有選才換掉，按取消不動。"""
    chosen = choose()
    if chosen:
        var.set(chosen)


def ask_excel(title: str, current: tk.StringVar, patterns: str = "*.xlsx") -> str:
    start = Path(current.get()).parent if current.get() else None
    return filedialog.askopenfilename(
        title=title, initialdir=start, filetypes=[("Excel", patterns), ("全部檔案", "*.*")]
    )


def ask_csv(title: str, current: tk.StringVar) -> str:
    start = Path(current.get()).parent if current.get() else None
    return filedialog.askopenfilename(title=title, initialdir=start, filetypes=[("CSV", "*.csv"), ("全部檔案", "*.*")])


def open_folder(folder: Path) -> None:
    if sys.platform == "win32":
        os.startfile(folder)  # Windows 檔案總管
    else:
        subprocess.Popen(["xdg-open", str(folder)])
