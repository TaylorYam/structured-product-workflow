"""本機視窗：四個分頁。

- 準備批次：貼 TDCC、選根目錄與輸出位置，產生批次資料夾與準備清單。
- 放回資料夾：選批次資料夾與核對結果檔，把核對通過的 TS／IIS 複製回商品資料夾。
- 每日匯入：選每日更新，匯入最小 FCN 資料庫（#32）；信箱在資料庫直接維護（#35）。
- 寄信草稿：貼核對通過的 TDCC，在 Outlook 建 TS／IIS 草稿給承作的 FC（#29）。

視窗只收輸入、在背景執行 `prepare_batch`／`return_to_folders`／`import_daily`／`create_drafts` 並顯示結果，不含判斷邏輯。
"""

from __future__ import annotations

import argparse
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from collections.abc import Callable, Sequence
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from sp_workflow.daily_import import DailyImportError, ImportInfo, ImportReceipt, import_daily, latest_import
from sp_workflow.database import ConnectionSettings, DatabaseError, connect, default_env_path, settings_from_env
from sp_workflow.local_settings import FolderMemory
from sp_workflow.mail_drafts import DRAFTED, NOT_NEEDED, DraftReceipt, MailDraftError, create_drafts
from sp_workflow.preparation import PREPARED, PreparationError, PrepareReceipt, prepare_batch
from sp_workflow.returning import PENDING_SUFFIX, ReturnError, ReturnReceipt, return_to_folders

TITLE = "TS／IIS 準備與放回"
_EXPECTED_ERRORS = (
    PreparationError,
    ReturnError,
    DailyImportError,
    DatabaseError,
    MailDraftError,
)  # 訊息本身就是給作業人員看的


class _Tab:
    """一個分頁：上方輸入、按鈕、狀態列，下方結果表；動作在背景執行，結果回主執行緒顯示。"""

    def __init__(self, notebook: ttk.Notebook, title: str, columns: Sequence[tuple[str, str, int]]) -> None:
        self.master = notebook.winfo_toplevel()
        self.results: queue.Queue[object] = queue.Queue()
        self.frame = ttk.Frame(notebook, padding=12)
        notebook.add(self.frame, text=title)
        self.frame.columnconfigure(1, weight=1)
        self.status_var = tk.StringVar()
        self.columns = columns
        self.table = ttk.Treeview(self.frame, columns=[c for c, _, _ in columns], show="headings")
        for col, label, width in columns:
            self.table.heading(col, text=label)
            self.table.column(col, width=width, stretch=col == columns[-1][0])
        self.table.tag_configure("problem", foreground="#b00020")
        self.buttons = ttk.Frame(self.frame)

    def place_bottom(self, row: int) -> None:
        """按鈕列、狀態列與結果表放在第 row 列以下。"""
        self.buttons.grid(row=row, column=0, columnspan=3, sticky="w", pady=8)
        ttk.Label(self.frame, textvariable=self.status_var, wraplength=720).grid(
            row=row + 1, column=0, columnspan=3, sticky="w"
        )
        self.frame.rowconfigure(row + 2, weight=1)
        self.table.grid(row=row + 2, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
        scroll = ttk.Scrollbar(self.frame, orient=tk.VERTICAL, command=self.table.yview)
        scroll.grid(row=row + 2, column=3, sticky="ns", pady=(8, 0))
        self.table.configure(yscrollcommand=scroll.set)

    def path_row(self, row: int, label: str, var: tk.StringVar, choose: Callable[[], str]) -> None:
        ttk.Label(self.frame, text=label).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(self.frame, textvariable=var).grid(row=row, column=1, sticky="ew", pady=4)

        def on_click() -> None:
            chosen = choose()
            if chosen:
                var.set(chosen)

        ttk.Button(self.frame, text="選擇…", command=on_click).grid(row=row, column=2, padx=(8, 0), pady=4)

    def run_in_background(self, action: Callable[[], object], busy: str, on_done: Callable[[object], None]) -> None:
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


class PrepareTab(_Tab):
    def __init__(self, notebook: ttk.Notebook, memory: FolderMemory, on_prepared: Callable[[Path], None]) -> None:
        super().__init__(notebook, "準備批次", (("tdcc", "TDCC", 130), ("status", "狀態", 70), ("reason", "原因", 520)))
        self.on_prepared = on_prepared
        self.memory = memory
        folders = memory.load()
        self.batch_dir: Path | None = None
        self.root_var = tk.StringVar(value=folders.get("root", ""))
        self.out_var = tk.StringVar(value=folders.get("out", ""))
        self.status_var.set("貼上 TDCC，選好根目錄與輸出位置後按「開始準備」。")

        ttk.Label(self.frame, text="TDCC（可貼多筆）").grid(row=0, column=0, sticky="nw", pady=4)
        self.tdcc_text = tk.Text(self.frame, height=6, width=40)
        self.tdcc_text.grid(row=0, column=1, columnspan=2, sticky="nsew", pady=4)
        self.path_row(1, "根目錄", self.root_var, lambda: _ask_dir("選擇商品資料夾所在的根目錄", self.root_var))
        self.path_row(2, "輸出位置", self.out_var, lambda: _ask_dir("選擇批次資料夾要開在哪裡", self.out_var))
        self.start_button = ttk.Button(self.buttons, text="開始準備", command=self.start)
        self.start_button.pack(side=tk.LEFT)
        self.open_button = ttk.Button(self.buttons, text="打開批次資料夾", command=self.open_batch, state=tk.DISABLED)
        self.open_button.pack(side=tk.LEFT, padx=8)
        self.place_bottom(3)

    def start(self) -> None:
        text = self.tdcc_text.get("1.0", tk.END)
        root_text, out_text = self.root_var.get().strip(), self.out_var.get().strip()
        if not root_text or not out_text:
            messagebox.showerror(TITLE, "請先選擇根目錄與輸出位置。")
            return
        root, out = Path(root_text), Path(out_text)
        self.memory.remember(root=str(root), out=str(out))
        self.start_button.configure(state=tk.DISABLED)
        self.open_button.configure(state=tk.DISABLED)
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
        status = (
            f"完成：{prepared} 個商品已準備、{len(result.outcomes) - prepared} 個跳過。批次資料夾：{result.batch_dir}"
        )
        if result.unreadable_dirs:
            status += f"\n注意：搜尋時有 {len(result.unreadable_dirs)} 個資料夾讀不到（例如 {result.unreadable_dirs[0]}），裡面的商品資料夾找不到。"
        self.status_var.set(status)
        self.batch_dir = result.batch_dir
        self.open_button.configure(state=tk.NORMAL)
        self.on_prepared(result.batch_dir)

    def open_batch(self) -> None:
        if self.batch_dir is not None:
            _open_folder(self.batch_dir)


class ReturnTab(_Tab):
    def __init__(self, notebook: ttk.Notebook, memory: FolderMemory) -> None:
        super().__init__(
            notebook,
            "放回資料夾",
            (("tdcc", "TDCC", 130), ("ts", "TS", 70), ("iis", "IIS", 70), ("reason", "原因", 450)),
        )
        self.memory = memory
        self.batch_var = tk.StringVar(value=memory.load().get("batch", ""))
        self.result_var = tk.StringVar()
        self.status_var.set("選批次資料夾與核對 PANEL 存下的核對結果檔，按「放回資料夾」。")

        self.path_row(0, "批次資料夾", self.batch_var, lambda: _ask_dir("選擇批次資料夾", self.batch_var))
        self.path_row(1, "核對結果檔", self.result_var, self._ask_result)
        self.start_button = ttk.Button(self.buttons, text="放回資料夾", command=self.start)
        self.start_button.pack(side=tk.LEFT)
        self.place_bottom(2)

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
        self.run_in_background(lambda: return_to_folders(batch, result), "放回中…", self._done)

    def _done(self, result: object) -> None:
        self.start_button.configure(state=tk.NORMAL)
        if not isinstance(result, ReturnReceipt):
            return
        for o in result.outcomes:
            self.add_row((o.tdcc, o.ts, o.iis, o.reason), problem=o.blocked or o.pending or bool(o.reason))
        blocked = sum(o.blocked for o in result.outcomes)
        pending = sum(o.pending and not o.blocked for o in result.outcomes)
        self.status_var.set(
            f"完成：{len(result.outcomes) - blocked - pending} 個商品已放回或已在、{pending} 個含「{PENDING_SUFFIX}」檔、"
            f"{blocked} 個不放回。放回清單：{result.list_path}"
        )


class DailyImportTab(_Tab):
    def __init__(self, notebook: ttk.Notebook, memory: FolderMemory, db: _DatabaseLogin) -> None:
        super().__init__(notebook, "每日匯入", (("tdcc", "TDCC", 130), ("kind", "結果", 150), ("detail", "說明", 440)))
        self.memory = memory
        self.db = db
        folders = memory.load()
        self.daily_var = tk.StringVar(value=folders.get("daily", ""))
        self.status_var.set(f"選每日更新，按「匯入」。資料庫連線設定：{db.env_path}")

        self.path_row(0, "每日更新", self.daily_var, lambda: _ask_excel("選擇每日更新", self.daily_var))
        self.start_button = ttk.Button(self.buttons, text="匯入", command=self.start)
        self.start_button.pack(side=tk.LEFT)
        self.place_bottom(1)

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
        self.status_var.set(
            f"完成（第 {result.run_id} 次匯入）：{len(result.lines) - problems} 項正常、{problems} 項要注意；"
            f"略過非 FCN 交易 {result.skipped_trade_rows} 列、清單 {result.skipped_inventory_rows} 列；"
            f"信箱 {result.people} 人。"
        )


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
    def __init__(self, notebook: ttk.Notebook, memory: FolderMemory, db: _DatabaseLogin) -> None:
        super().__init__(
            notebook,
            "寄信草稿",
            (("tdcc", "TDCC", 130), ("status", "狀態", 80), ("to", "收件人", 160), ("reason", "原因", 380)),
        )
        self.memory = memory
        self.db = db
        folders = memory.load()
        self.root_var = tk.StringVar(value=folders.get("root", ""))
        self.out_var = tk.StringVar(value=folders.get("mail_out", ""))
        self.status_var.set("貼上核對通過的 TDCC，選根目錄與結果清單位置，按「建立草稿」。只建草稿，不會寄出。")

        ttk.Label(self.frame, text="TDCC（可貼多筆）").grid(row=0, column=0, sticky="nw", pady=4)
        self.tdcc_text = tk.Text(self.frame, height=6, width=40)
        self.tdcc_text.grid(row=0, column=1, columnspan=2, sticky="nsew", pady=4)
        self.path_row(1, "根目錄", self.root_var, lambda: _ask_dir("選擇商品資料夾所在的根目錄", self.root_var))
        self.path_row(2, "結果清單位置", self.out_var, lambda: _ask_dir("選擇寄信草稿清單要存在哪裡", self.out_var))
        self.start_button = ttk.Button(self.buttons, text="建立草稿", command=self.start)
        self.start_button.pack(side=tk.LEFT)
        self.place_bottom(3)

    def start(self) -> None:
        text = self.tdcc_text.get("1.0", tk.END)
        root_text, out_text = self.root_var.get().strip(), self.out_var.get().strip()
        if not root_text or not out_text:
            messagebox.showerror(TITLE, "請先選擇根目錄與結果清單位置。")
            return
        settings = self.db.settings()
        if settings is None:
            return
        root, out = Path(root_text), Path(out_text)
        self.memory.remember(root=str(root), mail_out=str(out))
        self.start_button.configure(state=tk.DISABLED)

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
                    return create_drafts(text, root, out, conn)

            self.run_in_background(run, "建立草稿中…（在根目錄底下搜尋商品資料夾）", self._done)

        self.run_in_background(check, "讀取最近一次每日匯入…", confirm)

    def _done(self, result: object) -> None:
        self.start_button.configure(state=tk.NORMAL)
        self.db.after_run(result)
        if not isinstance(result, DraftReceipt):
            return
        for o in result.outcomes:
            self.add_row((o.tdcc, o.status, "、".join(o.to), o.reason), problem=o.status not in (DRAFTED, NOT_NEEDED))
        drafted = sum(o.status == DRAFTED for o in result.outcomes)
        last = _import_text(result.last_import) if result.last_import else "沒有匯入紀錄"
        status = (
            f"完成：{drafted} 封草稿已建立（在 Outlook 草稿匣，請確認後再寄出）、"
            f"{len(result.outcomes) - drafted} 檔沒有建立。最近一次每日匯入：{last}。結果清單：{result.list_path}"
        )
        if result.unreadable_dirs:
            status += f"\n注意：搜尋時有 {len(result.unreadable_dirs)} 個資料夾讀不到（例如 {result.unreadable_dirs[0]}），裡面的商品資料夾找不到。"
        self.status_var.set(status)


class PanelWindow:
    def __init__(self, master: tk.Tk, memory: FolderMemory, env_path: Path | None = None) -> None:
        master.title(TITLE)
        master.minsize(760, 560)
        notebook = ttk.Notebook(master)
        notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self.returning = ReturnTab(notebook, memory)
        self.prepare = PrepareTab(notebook, memory, on_prepared=lambda batch: self.returning.batch_var.set(str(batch)))
        notebook.insert(0, self.prepare.frame)  # 準備批次排第一個分頁
        db = _DatabaseLogin(master, env_path or default_env_path())
        self.daily = DailyImportTab(notebook, memory, db)
        self.mail = MailDraftTab(notebook, memory, db)
        notebook.select(0)


def _ask_dir(title: str, current: tk.StringVar) -> str:
    return filedialog.askdirectory(title=title, initialdir=current.get() or None, mustexist=True)


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


def _install_root(argv: Sequence[str] | None) -> Path | None:
    parser = argparse.ArgumentParser(description=TITLE)
    parser.add_argument("--install-root", type=Path, help="安裝資料夾；上次選的資料夾與 .env 都在它底下")
    return parser.parse_args(argv).install_root


def folder_memory(argv: Sequence[str] | None = None) -> FolderMemory:
    """依啟動參數決定上次選的資料夾記在哪：有 --install-root 就記在它底下的 .local/。"""
    root = _install_root(argv)
    return FolderMemory.under(root) if root else FolderMemory()


def main(argv: Sequence[str] | None = None) -> None:
    memory = folder_memory(argv)
    env_path = default_env_path(_install_root(argv))
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    master = tk.Tk()
    PanelWindow(master, memory, env_path)
    master.mainloop()


if __name__ == "__main__":
    main()
