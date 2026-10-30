"""「分析」選單的「重災區掃描…」（#362）：另開一個小視窗，選最終比價日截止日，匯出全部在庫商品裡目前跌破的。

不用挑標的，也沒有試算價；格式同標的曝險分析的「全部商品」。
分析規則在 `sp_workflow.exposure`，這裡只收截止日、在背景呼叫 `window_actions`。
"""

from __future__ import annotations

import datetime as dt
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from fcn_checker.date_picker import DateEntry, parse_day
from fcn_checker.ui import Background, WrapLabel
from sp_workflow.exposure import BROKEN, HotspotReceipt, hotspot_name
from sp_workflow.local_settings import FolderMemory
from sp_workflow.tabs.base import TITLE, ActionRunner, BackgroundFactory, DatabaseLogin
from sp_workflow.tabs.exposure import MEMORY_KEY, follow_width, one_month_later
from sp_workflow.window_actions import export_hotspot_scan


class HotspotWindow(tk.Toplevel):
    """重災區掃描視窗；處理中按鈕不能按。"""

    def __init__(
        self,
        parent: tk.Misc,
        db: DatabaseLogin,
        memory: FolderMemory,
        *,
        background: BackgroundFactory = Background,
        today: dt.date | None = None,
    ) -> None:
        super().__init__(parent)
        self.title(f"{TITLE} — 重災區掃描")
        self.memory = memory
        self.background = background(self)
        frame = ttk.Frame(self, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(0, weight=1)
        self.intro = intro = WrapLabel(
            frame,
            text="不用挑標的：用收盤價表最新的收盤價，掃過最終比價日不晚於截止日的全部在庫商品，"
            "只列出目前跌破執行價（跌破K）或下限價（跌破KI）的，連同分公司、FC 與面額匯出 Excel，"
            "格式同標的曝險分析。請先匯入最新的收盤價再掃描。這裡只讀資料庫，不改資料。",
            wraplength=500,
            justify="left",
        )
        intro.grid(row=0, column=0, sticky="ew")
        follow_width(intro)
        bottom = ttk.Frame(frame)
        bottom.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        ttk.Label(bottom, text="最終比價日截止").pack(side=tk.LEFT, padx=(0, 8))
        self.until_var = tk.StringVar(value=one_month_later(today or dt.date.today()).isoformat())
        DateEntry(bottom, self.until_var, width=14).pack(side=tk.LEFT)
        self.export_button = ttk.Button(bottom, text="匯出 Excel…", command=self.export)
        self.export_button.pack(side=tk.RIGHT)
        self.status_var = tk.StringVar()
        self.runner = ActionRunner(db, self.background, self.status_var, self._busy, parent=self)
        status = WrapLabel(frame, textvariable=self.status_var, wraplength=500, justify="left")
        status.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        follow_width(status)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.geometry("600x220")

    @property
    def working(self) -> bool:
        return self.runner.working

    def export(self) -> None:
        if self.working:
            return
        until = parse_day(self.until_var.get())
        if until is None:
            messagebox.showinfo(TITLE, "最終比價日截止請填 YYYY-MM-DD（可以點右邊的小月曆）。", parent=self)
            return
        chosen = filedialog.asksaveasfilename(
            title="匯出重災區掃描",
            initialdir=self.memory.load().get(MEMORY_KEY) or None,  # 跟標的曝險分析存在同一個資料夾
            initialfile=hotspot_name(),
            defaultextension=".xlsx",
            filetypes=[("Excel 活頁簿", "*.xlsx")],
            parent=self,
        )
        if not chosen:
            return
        path = Path(chosen)
        self.memory.remember(**{MEMORY_KEY: str(path.parent)})
        self.runner.run_action(
            lambda settings: export_hotspot_scan(settings, until, path), "掃描並匯出中…", self._exported
        )

    def _exported(self, receipt: HotspotReceipt) -> None:
        counts = "、".join(f"{status} {receipt.counts[status]}" for status in BROKEN if receipt.counts.get(status))
        self.status_var.set(
            f"已匯出 {receipt.count} 檔（{counts or '沒有跌破的商品'}；截止日內在庫 {receipt.scanned} 檔）：{receipt.path}"
        )

    def _busy(self, busy: bool) -> None:
        self.export_button.configure(state=tk.DISABLED if busy else tk.NORMAL)

    def close(self) -> None:
        self.background.close()
        self.destroy()
