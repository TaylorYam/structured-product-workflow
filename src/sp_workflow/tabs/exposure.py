"""「分析」選單的「標的曝險分析…」（#345）：另開一個視窗，選一檔或多檔標的與最終比價日截止日，匯出曝險分析的 Excel。

打開時讀一次在庫商品連結的標的，一列一個股票代號（合併各交易所的代碼、最新收盤價、連結檔數）；
可以搜尋，搜尋時已選的保留；匯出時把選的代號展開成全部交易所的代碼。
分析規則在 `sp_workflow.exposure`，這裡只收輸入、在背景呼叫 `window_actions`。
"""

from __future__ import annotations

import calendar
import datetime as dt
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from fcn_checker.date_picker import DateEntry, parse_day
from fcn_checker.ui import Background, FitTreeview, Scrollbar, WrapLabel
from sp_workflow.exposure import CATEGORIES, Choice, ExposureReceipt, export_name
from sp_workflow.local_settings import FolderMemory
from sp_workflow.tabs.base import TITLE, ActionRunner, BackgroundFactory, DatabaseLogin
from sp_workflow.window_actions import export_underlying_exposure, load_exposure_choices

COLUMNS = (
    ("ticker", "代號", 90),
    ("codes", "交易所代碼", 180),
    ("close", "最新收盤", 100),
    ("day", "收盤日", 100),
    ("count", "在庫檔數", 80),
)
MEMORY_KEY = "exposure_export"


def one_month_later(day: dt.date) -> dt.date:
    """下個月的同一天；下個月沒有這一天時取月底（例：1/31 → 2/28）。"""
    year, month = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
    return dt.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


class ExposureWindow(tk.Toplevel):
    """標的曝險分析視窗：打開時讀一次可選的標的；處理中按鈕不能按。"""

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
        self.title(f"{TITLE} — 標的曝險分析")
        self.memory = memory
        self.background = background(self)
        self.choices: tuple[Choice, ...] = ()
        self.chosen: list[str] = []  # 選的股票代號，依選的先後
        self._showing = False  # 重畫表時不把畫面上的選取當成使用者改選
        frame = ttk.Frame(self, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        top = ttk.Frame(frame)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        WrapLabel(
            top,
            text="選一檔或多檔標的（按住 Ctrl 複選），用收盤價表最新的收盤價，找出最終比價日不晚於截止日、"
            "連結這些標的的在庫商品，判斷哪些會接股，依分公司與理專統計面額與估損，匯出 Excel。"
            "一列是一個股票代號，已包含它所有交易所的代碼（例：GOOG 含 GOOG UW、GOOG UQ）。"
            "請先匯入最新的收盤價再分析；這裡只讀資料庫，不改資料。",
            wraplength=760,
            justify="left",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.refresh_button = ttk.Button(top, text="重新整理", command=self.refresh)
        self.refresh_button.pack(side=tk.RIGHT)
        search = ttk.Frame(frame)
        search.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(search, text="搜尋代號").pack(side=tk.LEFT, padx=(0, 8))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._show())
        ttk.Entry(search, textvariable=self.search_var, width=20).pack(side=tk.LEFT)
        box = ttk.LabelFrame(frame, text="在庫商品連結的標的", padding=8)
        box.grid(row=2, column=0, sticky="nsew")
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        self.table = FitTreeview(box, columns=[c for c, _, _ in COLUMNS], show="headings", selectmode="extended")
        for col, label, width in COLUMNS:
            self.table.heading(col, text=label)
            self.table.column(col, width=width, stretch=col == "count")
        self.table.grid(row=0, column=0, sticky="nsew")
        scroll = Scrollbar(box, orient=tk.VERTICAL, command=self.table.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.table.configure(yscrollcommand=scroll.set)
        self.table.bind("<<TreeviewSelect>>", self._picked)
        self.chosen_var = tk.StringVar()
        chosen = ttk.Frame(box)
        chosen.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        WrapLabel(chosen, textvariable=self.chosen_var, wraplength=700, justify="left").pack(
            side=tk.LEFT, fill=tk.X, expand=True
        )
        self.clear_button = ttk.Button(chosen, text="清除已選", command=self.clear)
        self.clear_button.pack(side=tk.RIGHT)
        bottom = ttk.Frame(frame)
        bottom.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(bottom, text="最終比價日截止").pack(side=tk.LEFT, padx=(0, 8))
        self.until_var = tk.StringVar(value=one_month_later(today or dt.date.today()).isoformat())
        DateEntry(bottom, self.until_var, width=14).pack(side=tk.LEFT)
        self.export_button = ttk.Button(bottom, text="匯出 Excel…", command=self.export)
        self.export_button.pack(side=tk.RIGHT)
        self.buttons = [self.refresh_button, self.clear_button, self.export_button]
        self.status_var = tk.StringVar()
        self.runner = ActionRunner(db, self.background, self.status_var, self._busy, parent=self)
        WrapLabel(frame, textvariable=self.status_var, wraplength=900, justify="left").grid(
            row=4, column=0, sticky="ew", pady=(10, 0)
        )
        self._show_chosen()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.geometry("820x620")

    # ---------- 讀與顯示 ----------

    @property
    def working(self) -> bool:
        return self.runner.working

    def refresh(self) -> None:
        self.runner.run_action(load_exposure_choices, "讀取在庫商品連結的標的中…", self._loaded)

    def _loaded(self, choices: list[Choice]) -> None:
        self.choices = tuple(choices)
        known = {c.ticker for c in choices}
        self.chosen = [code for code in self.chosen if code in known]
        self._show()
        self._show_chosen()
        self.status_var.set(f"在庫商品連結的標的 {len(choices)} 個。")

    def visible(self) -> list[Choice]:
        """符合搜尋的標的（不分大小寫，比對代號與交易所代碼）。"""
        words = self.search_var.get().upper().split()
        return [c for c in self.choices if all(word in " ".join((c.ticker, *c.codes)).upper() for word in words)]

    def _show(self) -> None:
        self._showing = True
        try:
            self.table.delete(*self.table.get_children())
            for choice in self.visible():
                close = "" if choice.close is None else f"{choice.close:,f}"
                day = "" if choice.close_date is None else choice.close_date.isoformat()
                values = (choice.ticker, "、".join(choice.codes), close, day, choice.products)
                self.table.insert("", tk.END, iid=choice.ticker, values=values)
            shown = [code for code in self.chosen if self.table.exists(code)]
            self.table.selection_set(shown)
        finally:
            self._showing = False

    def _picked(self, _event: object = None) -> None:
        """畫面上的選取改了：畫面上有的照選取更新，搜尋藏起來的保留。"""
        if self._showing:
            return
        on_screen = set(self.table.get_children())
        selected = set(self.table.selection())
        kept = [code for code in self.chosen if code not in on_screen or code in selected]
        self.chosen = kept + [code for code in self.table.selection() if code not in kept]
        self._show_chosen()

    def _show_chosen(self) -> None:
        self.chosen_var.set(f"已選：{'、'.join(self.chosen)}" if self.chosen else "已選：（還沒選標的）")

    def clear(self) -> None:
        self.chosen = []
        self._show()
        self._show_chosen()

    # ---------- 匯出 ----------

    def export(self) -> None:
        if self.working:
            return
        if not self.chosen:
            messagebox.showinfo(TITLE, "請先在表上選一檔或多檔標的（按住 Ctrl 複選）。", parent=self)
            return
        until = parse_day(self.until_var.get())
        if until is None:
            messagebox.showinfo(TITLE, "最終比價日截止請填 YYYY-MM-DD（可以點右邊的小月曆）。", parent=self)
            return
        by_ticker = {c.ticker: c.codes for c in self.choices}
        codes = [code for ticker in self.chosen for code in by_ticker.get(ticker, ())]
        chosen = filedialog.asksaveasfilename(
            title="匯出標的曝險分析",
            initialdir=self.memory.load().get(MEMORY_KEY) or None,
            initialfile=export_name(self.chosen),
            defaultextension=".xlsx",
            filetypes=[("Excel 活頁簿", "*.xlsx")],
            parent=self,
        )
        if not chosen:
            return
        path = Path(chosen)
        self.memory.remember(**{MEMORY_KEY: str(path.parent)})
        self.runner.run_action(
            lambda settings: export_underlying_exposure(settings, codes, until, path),
            "分析並匯出中…",
            self._exported,
        )

    def _exported(self, receipt: ExposureReceipt) -> None:
        counts = "、".join(f"{c} {receipt.counts[c]}" for c in CATEGORIES if receipt.counts.get(c))
        self.status_var.set(f"已匯出 {receipt.count} 檔（{counts or '沒有符合的商品'}）：{receipt.path}")

    def _busy(self, busy: bool) -> None:
        for button in self.buttons:
            button.configure(state=tk.DISABLED if busy else tk.NORMAL)

    def close(self) -> None:
        self.background.close()
        self.destroy()
