"""匯入後重算核對條件（#88）在畫面上的部分：「放行差異…」與完成訊息裡的核對條件一句；每日匯入與起單資料兩頁共用。"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Iterable
from tkinter import messagebox, ttk

from sp_workflow.check_terms import DONE, STATUSES, CalcLine
from sp_workflow.product_terms import ImportConfig
from sp_workflow.tabs.base import TITLE, ActionRunner, DatabaseLogin, Tab
from sp_workflow.window_actions import Recalc, release_line


class Releaser:
    """「放行差異…」（#88）：選到的商品有還沒放行的交叉比對差異時可以按；確認後記下放行人與時間，再重算那一檔。

    `idle` 是分頁現在能不能做別的事；`busy` 在放行前後通知分頁停用、恢復自己的按鈕（同一分頁一次只跑一件背景工作）。
    放行用自己的 `ActionRunner`，在分頁的背景執行器上跑，處理中的說明與錯誤寫在分頁的狀態列（#206）。
    """

    def __init__(
        self,
        tab: Tab,
        db: DatabaseLogin,
        selected: Callable[[], str | None],
        on_released: Callable[[CalcLine], None],
        *,
        idle: Callable[[], bool] = lambda: True,
        busy: Callable[[bool], None] = lambda _busy: None,
    ) -> None:
        self.tab = tab
        self.selected = selected
        self.on_released = on_released
        self.idle = idle
        self.busy = busy
        self.lines: dict[str, CalcLine] = {}
        self.config: ImportConfig | None = None
        self.runner = ActionRunner(db, tab.background, tab.status_var, self._busy)  # 和分頁共用狀態列；按鈕由 idle 擋
        self.button = ttk.Button(tab.buttons, text="放行差異…", command=self.release, state=tk.DISABLED)
        self.button.pack(side=tk.LEFT, padx=(8, 0))
        tab.table.bind("<<TreeviewSelect>>", lambda _event: self.refresh(), add="+")

    def show(self, recalc: Recalc) -> None:
        """換成這次重算的結果（空的 `Recalc` 是清掉）。"""
        self.lines = {} if recalc.receipt is None else {line.tdcc: line for line in recalc.receipt.lines}
        self.config = recalc.config
        self.refresh()

    def line(self) -> CalcLine | None:
        tdcc = self.selected()
        return None if tdcc is None else self.lines.get(tdcc)

    def refresh(self) -> None:
        line = self.line()
        ready = line is not None and bool(line.unreleased) and self.config is not None
        self.button.configure(state=tk.NORMAL if ready and not self.runner.working and self.idle() else tk.DISABLED)

    def release(self) -> None:
        line, config = self.line(), self.config
        if line is None or not line.unreleased or config is None:
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
        tdcc, differences = line.tdcc, line.unreleased
        self.runner.run_action(
            lambda settings: release_line(settings, tdcc, differences, config), f"放行中…（{tdcc}）", self._done
        )

    def _busy(self, busy: bool) -> None:
        self.busy(busy)
        self.refresh()

    def _done(self, result: CalcLine) -> None:
        self.lines[result.tdcc] = result
        self.on_released(result)
        self.tab.status_var.set(f"已放行 {result.tdcc} 的差異，重算後：{calc_status(result)}。")


def calc_status(line: CalcLine) -> str:
    return line.status if line.status == DONE or not line.reason else f"{line.status}（{line.reason}）"


def status_counts(lines: Iterable[CalcLine]) -> str:
    statuses = [line.status for line in lines]
    return "、".join(f"{status} {statuses.count(status)}" for status in STATUSES)


def recalc_text(recalc: Recalc) -> str:
    """完成訊息裡的核對條件一句（#88）。"""
    if recalc.receipt is None:
        return f"注意：匯入已寫進資料庫，但核對條件沒有重算（{recalc.error}）；排除後重新匯入一次就會重算。"
    receipt = recalc.receipt
    return (
        f"核對條件（還沒鎖住的 {len(receipt.lines)} 檔，這次有變 {receipt.changed} 檔）："
        f"{status_counts(receipt.lines)}。"
    )
