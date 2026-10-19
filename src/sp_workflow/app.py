"""本機視窗：貼 TDCC、選根目錄與輸出位置，準備批次並顯示逐商品結果。

視窗只收輸入、在背景執行 `prepare_batch` 並顯示結果，不含判斷邏輯。
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from sp_workflow.local_settings import load_folders, save_folders
from sp_workflow.preparation import PREPARED, PreparationError, PrepareReceipt, prepare_batch

TITLE = "TS／IIS 批次準備"


class PrepareWindow:
    def __init__(self, master: tk.Tk) -> None:
        self.master = master
        self.results: queue.Queue[PrepareReceipt | Exception] = queue.Queue()
        self.batch_dir: Path | None = None
        folders = load_folders()
        self.root_var = tk.StringVar(value=folders.get("root", ""))
        self.out_var = tk.StringVar(value=folders.get("out", ""))
        self.status_var = tk.StringVar(value="貼上 TDCC，選好根目錄與輸出位置後按「開始準備」。")

        master.title(TITLE)
        master.minsize(760, 560)
        frame = ttk.Frame(master, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(5, weight=1)

        ttk.Label(frame, text="TDCC（可貼多筆）").grid(row=0, column=0, sticky="nw", pady=4)
        self.tdcc_text = tk.Text(frame, height=6, width=40)
        self.tdcc_text.grid(row=0, column=1, columnspan=2, sticky="nsew", pady=4)

        self._folder_row(frame, 1, "根目錄", self.root_var, "選擇商品資料夾所在的根目錄")
        self._folder_row(frame, 2, "輸出位置", self.out_var, "選擇批次資料夾要開在哪裡")

        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, columnspan=3, sticky="w", pady=8)
        self.start_button = ttk.Button(buttons, text="開始準備", command=self.start)
        self.start_button.pack(side=tk.LEFT)
        self.open_button = ttk.Button(buttons, text="打開批次資料夾", command=self.open_batch, state=tk.DISABLED)
        self.open_button.pack(side=tk.LEFT, padx=8)

        ttk.Label(frame, textvariable=self.status_var, wraplength=720).grid(row=4, column=0, columnspan=3, sticky="w")

        self.table = ttk.Treeview(frame, columns=("tdcc", "status", "reason"), show="headings")
        for col, label, width in (("tdcc", "TDCC", 130), ("status", "狀態", 70), ("reason", "原因", 520)):
            self.table.heading(col, text=label)
            self.table.column(col, width=width, stretch=col == "reason")
        self.table.tag_configure("skipped", foreground="#b00020")
        self.table.grid(row=5, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
        scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.table.yview)
        scroll.grid(row=5, column=3, sticky="ns", pady=(8, 0))
        self.table.configure(yscrollcommand=scroll.set)

    def _folder_row(self, frame: ttk.Frame, row: int, label: str, var: tk.StringVar, title: str) -> None:
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=var).grid(row=row, column=1, sticky="ew", pady=4)

        def choose() -> None:
            chosen = filedialog.askdirectory(title=title, initialdir=var.get() or None, mustexist=True)
            if chosen:
                var.set(chosen)

        ttk.Button(frame, text="選擇…", command=choose).grid(row=row, column=2, padx=(8, 0), pady=4)

    def start(self) -> None:
        text = self.tdcc_text.get("1.0", tk.END)
        root_text, out_text = self.root_var.get().strip(), self.out_var.get().strip()
        if not root_text or not out_text:
            messagebox.showerror(TITLE, "請先選擇根目錄與輸出位置。")
            return
        root, out = Path(root_text), Path(out_text)
        save_folders({"root": str(root), "out": str(out)})
        self.start_button.configure(state=tk.DISABLED)
        self.open_button.configure(state=tk.DISABLED)
        self.table.delete(*self.table.get_children())
        self.status_var.set("處理中…（在根目錄底下搜尋商品資料夾，公槽上可能要一點時間）")
        threading.Thread(target=self._work, args=(text, root, out), daemon=True).start()
        self.master.after(200, self._poll)

    def _work(self, text: str, root: Path, out: Path) -> None:
        try:
            self.results.put(prepare_batch(text, root, out))
        except Exception as exc:  # 背景執行緒的錯誤一律交回主執行緒顯示
            self.results.put(exc)

    def _poll(self) -> None:
        try:
            result = self.results.get_nowait()
        except queue.Empty:
            self.master.after(200, self._poll)
            return
        self.start_button.configure(state=tk.NORMAL)
        if isinstance(result, PreparationError):
            self.status_var.set(str(result))
            messagebox.showerror(TITLE, str(result))
        elif isinstance(result, Exception):
            self.status_var.set(f"執行失敗：{result}")
            messagebox.showerror(TITLE, f"執行失敗：{result!r}")
        else:
            self._show(result)

    def _show(self, receipt: PrepareReceipt) -> None:
        for o in receipt.outcomes:
            self.table.insert(
                "", tk.END, values=(o.tdcc, o.status, o.reason), tags=() if o.status == PREPARED else ("skipped",)
            )
        prepared = sum(o.status == PREPARED for o in receipt.outcomes)
        skipped = len(receipt.outcomes) - prepared
        self.batch_dir = receipt.batch_dir
        self.open_button.configure(state=tk.NORMAL)
        self.status_var.set(f"完成：{prepared} 個商品已準備、{skipped} 個跳過。批次資料夾：{receipt.batch_dir}")

    def open_batch(self) -> None:
        if self.batch_dir is None:
            return
        if sys.platform == "win32":
            os.startfile(self.batch_dir)  # Windows 檔案總管
        else:
            subprocess.Popen(["xdg-open", str(self.batch_dir)])


def main() -> None:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    master = tk.Tk()
    PrepareWindow(master)
    master.mainloop()


if __name__ == "__main__":
    main()
