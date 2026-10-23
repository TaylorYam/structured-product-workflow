"""「9 出場清單」分頁（#171）：某一個收盤日比價算出的 KO、接股、到期還本，一檔一列；匯出 Excel。

收盤日空白時按「重新整理」用收盤價表最新的收盤日，讀到後把日期填回去；改日期再按一次就看那一天
（`window_actions.load_exit_list`）。每日匯入（回補股價、歷史收盤價）比價完成後由視窗呼叫 `receive_day`
帶入比到的那一天並顯示提示條，不自動讀、不切頁（#76）。
"""

from __future__ import annotations

import datetime as dt
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, ttk

from fcn_checker.check_config import ConfigPaths
from fcn_checker.ui import HintBar
from sp_workflow.exits import LOCKED_TEXT, ExitExport, ExitList, ExitRow, export_exits, export_name
from sp_workflow.lifecycle import AUTO_EVENTS
from sp_workflow.local_settings import FolderMemory
from sp_workflow.tabs.base import DatabaseLogin, Tab
from sp_workflow.window_actions import load_exit_list

COLUMNS = (
    ("tdcc", "TDCC", 120),
    ("issuer", "上手", 60),
    ("product", "商品", 80),
    ("event", "事件", 80),
    ("event_date", "事件日", 90),
    ("underlying", "標的", 120),
    ("locked", "鎖住", 70),
    ("recorded_at", "記錄時間", 140),
)


class ExitListTab(Tab):
    def __init__(
        self, notebook: ttk.Notebook, memory: FolderMemory, db: DatabaseLogin, config_paths: Callable[[], ConfigPaths]
    ) -> None:
        super().__init__(notebook, "出場清單", COLUMNS)
        self.memory = memory
        self.db = db
        self.config_paths = config_paths
        self.working = False
        self.shown: ExitList | None = None  # 最近一次讀到的那一天；還沒讀過是 None
        self.status_var.set("按「重新整理」列出那一天比價算出的 KO、接股、到期還本；收盤日空白是收盤價表最新的一天。")

        self.page_header(
            "出場清單",
            "某一個收盤日比價算出的 KO、接股、到期還本；早上匯入昨日收盤價後看昨天有哪些商品出場。",
        )
        self.hint = HintBar(self.frame, self.hint_dismissed)
        self.hint.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        self.hint.hide()
        body = self.body(self.card(2))
        line = ttk.Frame(body, style="CardBody.TFrame")
        line.grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(line, text="收盤日", style="Card.TLabel").pack(side=tk.LEFT)
        self.day_var = tk.StringVar()
        entry = ttk.Entry(line, textvariable=self.day_var, width=12)
        entry.pack(side=tk.LEFT, padx=(8, 0))
        entry.bind("<Return>", lambda _event: self.refresh())
        ttk.Label(line, text="YYYY-MM-DD，空白是收盤價表最新的一天", style="CardMuted.TLabel").pack(
            side=tk.LEFT, padx=(8, 0)
        )
        self.result_area(body, 1)
        self.refresh_button = ttk.Button(self.buttons, text="重新整理", command=self.refresh, style="Primary.TButton")
        self.refresh_button.pack(side=tk.LEFT)
        self.export_button = ttk.Button(self.buttons, text="匯出 Excel…", command=self.export)
        self.export_button.pack(side=tk.LEFT, padx=(8, 0))
        self.place_table(3)
        self._busy(False)

    def _busy(self, busy: bool) -> None:
        self.working = busy
        self.refresh_button.configure(state=tk.DISABLED if busy else tk.NORMAL)
        has_rows = self.shown is not None and bool(self.shown.rows)
        self.export_button.configure(state=tk.DISABLED if busy or not has_rows else tk.NORMAL)

    def receive_day(self, day: dt.date) -> None:
        """每日匯入比價完成後帶入比到的那一天（#171）；正在讀時不換。不自動讀、不切頁。"""
        if self.working:
            return
        self.day_var.set(f"{day:%Y-%m-%d}")
        self.hint.show(f"比價已比到 {day:%Y-%m-%d}，已帶入這一天；按「重新整理」列出那天出場的商品。")

    def hint_dismissed(self) -> None:
        """提示條的「改選…」：收起提示條，自己改收盤日。"""
        self.hint.hide()

    # ---------- 重新整理 ----------

    def refresh(self) -> None:
        if self.working:
            return
        text = self.day_var.get().strip()
        try:
            day = dt.date.fromisoformat(text) if text else None
        except ValueError:
            self.status_var.set(f"收盤日「{text}」不是 YYYY-MM-DD；改好再按「重新整理」，空白是收盤價表最新的一天。")
            self.mark_done(False)
            return
        settings = self.db.settings()
        if settings is None:
            return
        paths = self.config_paths()
        self._busy(True)
        self.restart()

        def run() -> ExitList:
            return load_exit_list(settings, paths, day)

        self.run_in_background(run, "讀取出場清單中…", self._loaded)

    def _loaded(self, result: object) -> None:
        self.db.after_run(result)
        if isinstance(result, ExitList):
            self.shown = result
            self.hint.hide()
            for row in result.rows:
                self.add_row(_cells(row), problem=False)
            if result.day is None:
                self.status_var.set("收盤價表還沒有資料；先匯入收盤價，或填一個收盤日。")
            else:
                self.day_var.set(f"{result.day:%Y-%m-%d}")
                counts = [
                    (f"{event}（檔）", sum(r.event == event for r in result.rows), False) for event in AUTO_EVENTS
                ]
                text = f"{result.day:%Y-%m-%d} 出場 {len(result.rows)} 檔。" if result.rows else "沒有出場的商品。"
                self.finish(counts, f"收盤日 {result.day:%Y-%m-%d}：{text}")
        self._busy(False)

    # ---------- 匯出 ----------

    def export(self) -> None:
        if self.working or self.shown is None or self.shown.day is None or not self.shown.rows:
            return
        chosen = filedialog.asksaveasfilename(
            title="匯出出場清單",
            initialdir=self.memory.load().get("exit_export") or None,
            initialfile=export_name(self.shown.day),
            defaultextension=".xlsx",
            filetypes=[("Excel 活頁簿", "*.xlsx")],
            parent=self.master,
        )
        if not chosen:
            return
        path = Path(chosen)
        self.memory.remember(exit_export=str(path.parent))
        rows = self.shown.rows
        self._busy(True)
        self.mark_done(False)

        def run() -> ExitExport:
            return export_exits(rows, path)

        self.run_in_background(run, "匯出出場清單中…", self._exported, clear=False)

    def _exported(self, result: object) -> None:
        self._busy(False)
        if not isinstance(result, ExitExport):
            return
        self.status_var.set(f"已匯出出場清單 {result.count} 檔：{result.path}")
        self.mark_done(True)


def _cells(row: ExitRow) -> tuple[str, ...]:
    return (
        row.tdcc,
        row.issuer,
        row.product,
        row.event,
        f"{row.event_date:%Y-%m-%d}",
        row.underlying,
        LOCKED_TEXT[row.locked],
        f"{row.recorded_at.astimezone():%Y-%m-%d %H:%M}",
    )
