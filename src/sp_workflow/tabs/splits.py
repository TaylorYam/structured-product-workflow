"""「維護」選單的「拆股登記…」（#270、ADR 0024）：另開一個視窗，列出、新增、刪除標的的拆股登記。

一筆是一個代碼在一個生效日（第一個用新股數交易的日子）的拆股前、拆股後股數；前後都是 1 是「確認不是拆股」。
要改就刪掉重登。每次按確定就存進資料庫、留紀錄、比價全部（受影響、由比價判定出場的商品重新比），再重讀全部顯示。
規則在 `sp_workflow.stock_splits`，這裡只收輸入、在背景呼叫 `window_actions`。
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk

from fcn_checker.check_config import ConfigPaths
from fcn_checker.ui import Background, FitTreeview, Scrollbar, WrapLabel
from sp_workflow.credit_ratings import CreditRatingError
from sp_workflow.observation import EventChange
from sp_workflow.stock_splits import Split, StockSplitError, split_from
from sp_workflow.tabs.base import TITLE, ActionRunner, BackgroundFactory, DatabaseLogin
from sp_workflow.tabs.daily import observation_text
from sp_workflow.tabs.ratings import _ask, _Form, read_day
from sp_workflow.window_actions import SplitResult, add_stock_split, delete_stock_split, load_split_rows

COLUMNS = (
    ("code", "代碼", 110),
    ("day", "生效日", 100),
    ("label", "比例", 120),
    ("note", "備註", 260),
    ("by", "登記人", 90),
    ("at", "登記時間", 140),
)


class SplitsWindow(tk.Toplevel):
    """拆股登記視窗：打開時讀一次，之後每次存檔後重讀；處理中按鈕不能按。"""

    def __init__(
        self,
        parent: tk.Misc,
        db: DatabaseLogin,
        config_paths: Callable[[], ConfigPaths],
        *,
        background: BackgroundFactory = Background,
    ) -> None:
        super().__init__(parent)
        self.title(f"{TITLE} — 拆股登記")
        self.config_paths = config_paths
        self.background = background(self)
        self.splits: tuple[Split, ...] = ()
        frame = ttk.Frame(self, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        top = ttk.Frame(frame)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        WrapLabel(
            top,
            text="標的拆股或合股後，比價會把生效日起的收盤價換回交易時的基準再比（商品的進場價、執行價等不動）。"
            "比價遇到沒登記的價格斷層會停下來標待確認：是拆股就在這裡登記；是真的大漲大跌就登記「確認不是拆股」"
            "（前後股數都填 1）；價格錯了請重匯收盤價。每次存檔都會重新比價，並記下誰、何時、內容。",
            wraplength=760,
            justify="left",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.refresh_button = ttk.Button(top, text="重新整理", command=self.refresh)
        self.refresh_button.pack(side=tk.RIGHT)
        box = ttk.LabelFrame(frame, text="拆股登記", padding=8)
        box.grid(row=1, column=0, sticky="nsew")
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        self.table = FitTreeview(box, columns=[c for c, _, _ in COLUMNS], show="headings", selectmode="browse")
        for col, label, width in COLUMNS:
            self.table.heading(col, text=label)
            self.table.column(col, width=width, stretch=col == "note")
        self.table.grid(row=0, column=0, sticky="nsew")
        scroll = Scrollbar(box, orient=tk.VERTICAL, command=self.table.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.table.configure(yscrollcommand=scroll.set)
        buttons = ttk.Frame(box)
        buttons.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.buttons = [self.refresh_button]
        for text, command in (("新增…", self.add), ("刪除…", self.delete)):
            button = ttk.Button(buttons, text=text, command=command)
            button.pack(side=tk.LEFT, padx=(0, 8))
            self.buttons.append(button)
        self.status_var = tk.StringVar()
        self.runner = ActionRunner(db, self.background, self.status_var, self._busy, parent=self)
        WrapLabel(frame, textvariable=self.status_var, wraplength=900, justify="left").grid(
            row=2, column=0, sticky="ew", pady=(10, 0)
        )
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.geometry("980x520")

    # ---------- 讀與顯示 ----------

    @property
    def working(self) -> bool:
        return self.runner.working

    def refresh(self) -> None:
        self.runner.run_action(load_split_rows, "讀取拆股登記中…", self._show)

    def _show(self, splits: tuple[Split, ...], notice: str = "") -> None:
        self.splits = splits
        self.table.delete(*self.table.get_children())
        for index, split in enumerate(splits):
            at = f"{split.recorded_at:%Y-%m-%d %H:%M}" if split.recorded_at else ""
            values = (
                split.code,
                split.effective_date.isoformat(),
                split.label,
                split.note or "",
                split.recorded_by,
                at,
            )
            self.table.insert("", tk.END, iid=f"s{index}", values=values)
        self.status_var.set(f"{notice}拆股登記 {len(splits)} 筆。")

    def _saved(self, result: SplitResult, done: str) -> None:
        self._show(result.splits)
        receipt = result.observation.receipt
        changes = "" if receipt is None else "".join(f"\n{_change_text(c)}" for c in receipt.changes)
        self.status_var.set(f"已存：{done}。{observation_text(result.observation)}{changes}")

    def _selected(self) -> Split | None:
        selection = self.table.selection()
        if not selection:
            messagebox.showinfo(TITLE, "請先在表上點一筆登記。", parent=self)
            return None
        return self.splits[int(selection[0][1:])]

    # ---------- 動作 ----------

    def add(self) -> None:
        if self.working:
            return
        split = ask_split(self)
        if split is None:
            return
        paths = self.config_paths()
        done = f"新增 {split.code} {split.effective_date} {split.label}"
        self.runner.run_action(
            lambda settings: add_stock_split(settings, split, paths),
            "存檔並重新比價中…",
            lambda r: self._saved(r, done),
        )

    def delete(self) -> None:
        if self.working:
            return
        split = self._selected()
        if split is None:
            return
        if not messagebox.askyesno(
            TITLE,
            f"刪除 {split.code} 在 {split.effective_date} 的拆股登記（{split.label}）？刪除後會重新比價。",
            icon=messagebox.WARNING,
            default=messagebox.NO,
            parent=self,
        ):
            return
        paths = self.config_paths()
        done = f"刪除 {split.code} {split.effective_date} {split.label}"
        self.runner.run_action(
            lambda settings: delete_stock_split(settings, split, paths),
            "刪除並重新比價中…",
            lambda r: self._saved(r, done),
        )

    def _busy(self, busy: bool) -> None:
        for button in self.buttons:
            button.configure(state=tk.DISABLED if busy else tk.NORMAL)

    def close(self) -> None:
        self.background.close()
        self.destroy()


def _change_text(change: EventChange) -> str:
    def text(events: tuple[tuple[str, str, str | None], ...]) -> str:
        return (
            "、".join(" ".join(p for p in (event, underlying, day) if p) for event, day, underlying in events) or "沒有"
        )

    return f"{change.tdcc} 存續事件：{text(change.previous)} → {text(change.current)}"


class SplitForm(_Form):
    """新增一筆拆股登記：代碼、生效日、拆股前後股數、備註。"""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent, "新增拆股登記")
        self.code_var = self.entry("代碼", "", width=16)
        self.day_var = self.day_field("生效日", "")
        self.before_var = self.entry("拆股前股數", "1", width=8)
        self.after_var = self.entry("拆股後股數", "", width=8)
        self.note_var = self.entry("備註", "")
        self.note(
            "代碼同收盤價表（例：1321 JT，可以帶 Equity）。生效日是第一個用新股數交易的日子。"
            "1 拆 100 填 1、100；10 合 1 填 10、1；確認不是拆股填 1、1。"
        )
        self.finish_layout()

    def read(self) -> Split:
        try:
            day = read_day(self.day_var.get())
        except CreditRatingError as exc:  # 生效日欄和評等視窗共用，訊息一樣適用
            raise StockSplitError(str(exc)) from exc
        return split_from(self.code_var.get(), day, self.before_var.get(), self.after_var.get(), self.note_var.get())

    def confirm(self) -> None:
        try:
            self.result = self.read()
        except StockSplitError as exc:
            self.error_var.set(str(exc))
            return
        self.destroy()


def ask_split(parent: tk.Misc) -> Split | None:
    """新增拆股登記的輸入；取消時 None。"""
    found = _ask(SplitForm(parent))
    return found if isinstance(found, Split) else None
