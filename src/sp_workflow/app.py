"""本機視窗：兩個分頁。

- 準備批次：貼 TDCC、選根目錄與輸出位置，產生批次資料夾與準備清單。
- 放回資料夾：選批次資料夾與核對結果檔，把核對通過的 TS／IIS 複製回商品資料夾。

視窗只收輸入、在背景執行 `prepare_batch`／`return_to_folders` 並顯示結果，不含判斷邏輯。
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
from tkinter import filedialog, messagebox, ttk

from sp_workflow.local_settings import FolderMemory
from sp_workflow.preparation import PREPARED, PreparationError, PrepareReceipt, prepare_batch
from sp_workflow.returning import NOT_RETURNED, ReturnError, ReturnReceipt, return_to_folders

TITLE = "TS／IIS 準備與放回"
_EXPECTED_ERRORS = (PreparationError, ReturnError)  # 訊息本身就是給作業人員看的


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
            self.add_row((o.tdcc, o.ts, o.iis, o.reason), problem=NOT_RETURNED in (o.ts, o.iis))
        blocked = sum(NOT_RETURNED in (o.ts, o.iis) for o in result.outcomes)
        self.status_var.set(
            f"完成：{len(result.outcomes) - blocked} 個商品已放回或已在、{blocked} 個不放回。放回清單：{result.list_path}"
        )


class PanelWindow:
    def __init__(self, master: tk.Tk, memory: FolderMemory) -> None:
        master.title(TITLE)
        master.minsize(760, 560)
        notebook = ttk.Notebook(master)
        notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self.returning = ReturnTab(notebook, memory)
        self.prepare = PrepareTab(notebook, memory, on_prepared=lambda batch: self.returning.batch_var.set(str(batch)))
        notebook.insert(0, self.prepare.frame)  # 準備批次排第一個分頁
        notebook.select(0)


def _ask_dir(title: str, current: tk.StringVar) -> str:
    return filedialog.askdirectory(title=title, initialdir=current.get() or None, mustexist=True)


def _open_folder(folder: Path) -> None:
    if sys.platform == "win32":
        os.startfile(folder)  # Windows 檔案總管
    else:
        subprocess.Popen(["xdg-open", str(folder)])


def folder_memory(argv: Sequence[str] | None = None) -> FolderMemory:
    """依啟動參數決定上次選的資料夾記在哪：有 --install-root 就記在它底下的 .local/。"""
    parser = argparse.ArgumentParser(description=TITLE)
    parser.add_argument("--install-root", type=Path, help="安裝資料夾；上次選的資料夾記在它底下的 .local/")
    args = parser.parse_args(argv)
    return FolderMemory.under(args.install_root) if args.install_root else FolderMemory()


def main(argv: Sequence[str] | None = None) -> None:
    memory = folder_memory(argv)
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    master = tk.Tk()
    PanelWindow(master, memory)
    master.mainloop()


if __name__ == "__main__":
    main()
