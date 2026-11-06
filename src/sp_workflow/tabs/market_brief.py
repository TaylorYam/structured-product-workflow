"""「分析」選單的「盤勢速報…」（#467）：另開一個視窗，選要畫熱圖的標的，匯出給主管看的盤勢速報 Excel。

打開時讀一次全部在庫且存續商品連結的標的，依連結面額（折 USD）大到小，預設選前 5 個畫熱圖；
可以搜尋、按住 Ctrl 加選或取消，搜尋時已選的保留。缺匯率的幣別在狀態列提示（面額不計入）。
分析規則在 `sp_workflow.market_brief`，這裡只收輸入、在背景呼叫 `window_actions`。
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from fcn_checker.ui import Background, FitTreeview, Scrollbar, WrapLabel
from sp_workflow.local_settings import FolderMemory
from sp_workflow.market_brief import MILLION, Brief, BriefReceipt, TickerRow, brief_name
from sp_workflow.tabs.base import TITLE, ActionRunner, BackgroundFactory, DatabaseLogin
from sp_workflow.tabs.exposure import MEMORY_KEY, follow_width
from sp_workflow.window_actions import export_market_brief, load_market_brief

COLUMNS = (
    ("ticker", "代號", 90),
    ("close", "最新收盤", 100),
    ("change", "漲跌%", 80),
    ("count", "在庫檔數", 80),
    ("notional", "連結面額（M USD）", 140),
)


def missing_fx_text(missing: frozenset[str]) -> str:
    return f"；缺 {'、'.join(sorted(missing))} 對美元的匯率，這些幣別的面額沒有算進去" if missing else ""


class MarketBriefWindow(tk.Toplevel):
    """盤勢速報視窗：打開時讀一次標的；處理中按鈕不能按。"""

    def __init__(
        self,
        parent: tk.Misc,
        db: DatabaseLogin,
        memory: FolderMemory,
        *,
        background: BackgroundFactory = Background,
    ) -> None:
        super().__init__(parent)
        self.title(f"{TITLE} — 盤勢速報")
        self.memory = memory
        self.background = background(self)
        self.rows: tuple[TickerRow, ...] = ()
        self.chosen: list[str] = []  # 要畫熱圖的股票代號，依選的先後
        self._showing = False  # 重畫表時不把畫面上的選取當成使用者改選
        frame = ttk.Frame(self, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        top = ttk.Frame(frame)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.refresh_button = ttk.Button(top, text="重新整理", command=self.refresh)
        self.refresh_button.pack(side=tk.RIGHT, padx=(12, 0))
        intro = WrapLabel(
            top,
            text="用收盤價表最新的收盤價，看全部在庫且存續的商品，匯出給主管看的盤勢速報 Excel："
            "總覽、大跌標的（近 1／2／3 個月表現）、重災區，與選定標的的「執行價 × 最終比價月份」熱圖（一個標的一頁）。"
            "面額依幣別折成 USD。預設選連結面額前 5 個標的畫熱圖，可以按住 Ctrl 加選或取消。"
            "每個分頁的表格可以直接複製到 PPT。請先匯入最新的收盤價。這裡只讀資料庫，不改資料。",
            wraplength=600,
            justify="left",
        )
        intro.pack(side=tk.LEFT, fill=tk.X, expand=True)
        follow_width(intro)
        search = ttk.Frame(frame)
        search.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(search, text="搜尋代號").pack(side=tk.LEFT, padx=(0, 8))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._show())
        ttk.Entry(search, textvariable=self.search_var, width=20).pack(side=tk.LEFT)
        box = ttk.LabelFrame(frame, text="在庫商品連結的標的（依連結面額）", padding=8)
        box.grid(row=2, column=0, sticky="nsew")
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        self.table = FitTreeview(box, columns=[c for c, _, _ in COLUMNS], show="headings", selectmode="extended")
        for col, label, width in COLUMNS:
            self.table.heading(col, text=label)
            self.table.column(col, width=width, stretch=col == "notional")
        self.table.grid(row=0, column=0, sticky="nsew")
        scroll = Scrollbar(box, orient=tk.VERTICAL, command=self.table.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.table.configure(yscrollcommand=scroll.set)
        self.table.bind("<<TreeviewSelect>>", self._picked)
        chosen = ttk.Frame(box)
        chosen.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.clear_button = ttk.Button(chosen, text="清除已選", command=self.clear)
        self.clear_button.pack(side=tk.RIGHT, padx=(12, 0))
        self.chosen_var = tk.StringVar()
        label = WrapLabel(chosen, textvariable=self.chosen_var, wraplength=600, justify="left")
        label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        follow_width(label)
        bottom = ttk.Frame(frame)
        bottom.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        self.export_button = ttk.Button(bottom, text="匯出 Excel…", command=self.export)
        self.export_button.pack(side=tk.RIGHT)
        self.buttons = [self.refresh_button, self.clear_button, self.export_button]
        self.status_var = tk.StringVar()
        self.runner = ActionRunner(db, self.background, self.status_var, self._busy, parent=self)
        status = WrapLabel(frame, textvariable=self.status_var, wraplength=600, justify="left")
        status.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        follow_width(status)
        self._show_chosen()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.geometry("760x600")

    @property
    def working(self) -> bool:
        return self.runner.working

    def refresh(self) -> None:
        self.runner.run_action(load_market_brief, "讀取在庫商品與收盤價中…", self._loaded)

    def _loaded(self, brief: Brief) -> None:
        first = not self.rows
        self.rows = brief.rows
        known = {r.ticker for r in brief.rows}
        self.chosen = brief.default_heatmaps() if first else [t for t in self.chosen if t in known]
        self._show()
        self._show_chosen()
        self.status_var.set(
            f"在庫且存續 {len(brief.exposures)} 檔、連結 {len(brief.rows)} 個標的{missing_fx_text(brief.missing_fx)}。"
        )

    def visible(self) -> list[TickerRow]:
        words = self.search_var.get().upper().split()
        return [r for r in self.rows if all(word in " ".join((r.ticker, *r.codes)).upper() for word in words)]

    def _show(self) -> None:
        self._showing = True
        try:
            self.table.delete(*self.table.get_children())
            for row in self.visible():
                q = row.quote
                values = (
                    row.ticker,
                    "" if q is None else f"{q.close:,f}",
                    "" if q is None or q.change is None else f"{q.change:+.1%}",
                    row.products,
                    f"{row.notional / MILLION:,.1f}",
                )
                self.table.insert("", tk.END, iid=row.ticker, values=values)
            self.table.selection_set([t for t in self.chosen if self.table.exists(t)])
        finally:
            self._showing = False

    def _picked(self, _event: object = None) -> None:
        """畫面上的選取改了：畫面上有的照選取更新，搜尋藏起來的保留。"""
        if self._showing:
            return
        on_screen = set(self.table.get_children())
        selected = set(self.table.selection())
        kept = [t for t in self.chosen if t not in on_screen or t in selected]
        self.chosen = kept + [t for t in self.table.selection() if t not in kept]
        self._show_chosen()

    def _show_chosen(self) -> None:
        self.chosen_var.set(
            f"畫熱圖：{'、'.join(self.chosen)}" if self.chosen else "畫熱圖：（沒有選，只匯出總覽與重災區）"
        )

    def clear(self) -> None:
        self.chosen = []
        self._show()
        self._show_chosen()

    def export(self) -> None:
        if self.working:
            return
        if not self.rows:
            messagebox.showinfo(TITLE, "還沒讀到在庫商品，請先按「重新整理」。", parent=self)
            return
        chosen = filedialog.asksaveasfilename(
            title="匯出盤勢速報",
            initialdir=self.memory.load().get(MEMORY_KEY) or None,  # 跟標的曝險分析存在同一個資料夾
            initialfile=brief_name(),
            defaultextension=".xlsx",
            filetypes=[("Excel 活頁簿", "*.xlsx")],
            parent=self,
        )
        if not chosen:
            return
        path = Path(chosen)
        self.memory.remember(**{MEMORY_KEY: str(path.parent)})
        tickers = list(self.chosen)
        self.runner.run_action(
            lambda settings: export_market_brief(settings, tickers, path), "分析並匯出中…", self._exported
        )

    def _exported(self, receipt: BriefReceipt) -> None:
        maps = "、".join(receipt.heatmaps) or "無"
        self.status_var.set(
            f"已匯出（在庫 {receipt.products} 檔、跌破K {receipt.broken} 檔；熱圖 {maps}"
            f"{missing_fx_text(receipt.missing_fx)}）：{receipt.path}"
        )

    def _busy(self, busy: bool) -> None:
        for button in self.buttons:
            button.configure(state=tk.DISABLED if busy else tk.NORMAL)

    def close(self) -> None:
        self.background.close()
        self.destroy()
