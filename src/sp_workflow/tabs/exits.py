"""「9 出場清單」分頁（#171、#188）：某一個收盤日還有效的 KO、接股、到期還本、顧客贖回（比價、舊清單、人工都列，
「來源」欄寫出是哪一種），一筆一列；匯出 Excel。

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
from fcn_checker.date_picker import DateEntry
from fcn_checker.ui import Background, HintBar
from sp_workflow.exits import LOCKED_TEXT, ExitExport, ExitList, ExitRow, export_exits, export_name
from sp_workflow.lifecycle import EXIT_EVENTS, SOURCES
from sp_workflow.local_settings import FolderMemory
from sp_workflow.tabs.base import BackgroundFactory, DatabaseLogin, Tab
from sp_workflow.window_actions import load_exit_list

COLUMNS = (
    ("tdcc", "TDCC", 120),
    ("issuer", "上手", 60),
    ("product", "商品", 80),
    ("event", "事件", 80),
    ("event_date", "事件日", 90),
    ("underlying", "標的", 120),
    ("source", "來源", 70),
    ("locked", "鎖住", 70),
    ("recorded_at", "記錄時間", 140),
)


class ExitListTab(Tab):
    def __init__(
        self,
        notebook: ttk.Notebook,
        memory: FolderMemory,
        db: DatabaseLogin,
        config_paths: Callable[[], ConfigPaths],
        *,
        background: BackgroundFactory = Background,
    ) -> None:
        super().__init__(notebook, "出場清單", COLUMNS, db, background)
        self.memory = memory
        self.config_paths = config_paths
        self.shown: ExitList | None = None  # 最近一次讀到的那一天；還沒讀過是 None
        self.status_var.set(
            "按「重新整理」列出那一天出場的商品（比價、舊清單、顧客贖回）；收盤日空白是收盤價表最新的一天。"
        )

        self.page_header(
            "出場清單",
            "某一個收盤日的 KO、接股、到期還本、顧客贖回，比價、舊清單都列；早上匯入昨日收盤價後看昨天有哪些商品出場。",
        )
        self.hint = HintBar(self.frame, self._hint_closed)
        self.hint.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        self.hint.hide()
        body = self.body(self.card(2))
        line = ttk.Frame(body, style="CardBody.TFrame")
        line.grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(line, text="收盤日", style="Card.TLabel").pack(side=tk.LEFT)
        self.day_var = tk.StringVar()
        field = DateEntry(line, self.day_var, on_pick=self.refresh, style="CardBody.TFrame")  # 小日曆（#192）
        field.pack(side=tk.LEFT, padx=(8, 0))
        field.entry.bind("<Return>", lambda _event: self.refresh())
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
        self.refresh_button.configure(state=tk.DISABLED if busy else tk.NORMAL)
        has_rows = self.shown is not None and bool(self.shown.rows)
        self.export_button.configure(state=tk.DISABLED if busy or not has_rows else tk.NORMAL)

    def receive_day(self, day: dt.date) -> bool:
        """每日匯入比價完成後帶入比到的那一天（#171），回傳是否帶入；正在讀或匯出時不換。

        上一次讀到的表先清掉（那是別天的），不自動讀、不切頁。
        """
        if self.working:
            return False
        self.day_var.set(f"{day:%Y-%m-%d}")
        self.shown = None
        self.table.delete(*self.table.get_children())
        self.restart()
        self.status_var.set("按「重新整理」列出那天出場的商品（比價、舊清單、顧客贖回）。")
        self._busy(False)
        self.hint.show(f"比價已比到 {day:%Y-%m-%d}，已帶入這一天；按「重新整理」列出那天出場的商品。")
        return True

    def _hint_closed(self) -> None:
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
        paths = self.config_paths()
        self.runner.run_action(lambda settings: load_exit_list(settings, paths, day), "讀取出場清單中…", self._loaded)

    def _loaded(self, result: ExitList) -> None:
        self.shown = result
        self.hint.hide()
        for row in result.rows:
            self.add_row(_cells(row), problem=False)
        if result.day is None:
            self.status_var.set("收盤價表還沒有資料；先匯入收盤價，或填一個收盤日。")
            return
        self.day_var.set(f"{result.day:%Y-%m-%d}")
        counts = [(f"{event}（檔）", _products(result.rows, event=event), False) for event in EXIT_EVENTS]
        text = f"出場 {_products(result.rows)} 檔（{_by_source(result.rows)}）。" if result.rows else "沒有出場的商品。"
        self.finish(counts, f"收盤日 {result.day:%Y-%m-%d}：{text}")

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
        self.runner.run_local(
            lambda: export_exits(rows, path),
            "匯出出場清單中…",
            self._exported,
            clear=False,
            before=lambda: self.mark_done(False),
        )

    def _exported(self, result: ExitExport) -> None:
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
        row.source,
        LOCKED_TEXT[row.locked],
        f"{row.recorded_at.astimezone():%Y-%m-%d %H:%M}",
    )


def _products(rows: tuple[ExitRow, ...], *, event: str = "", source: str = "") -> int:
    """有這種事件、這個來源（空白是不限）的商品檔數；一檔同一天有兩筆事件時算一檔。"""
    return len({r.tdcc for r in rows if (not event or r.event == event) and (not source or r.source == source)})


def _by_source(rows: tuple[ExitRow, ...]) -> str:
    """完成訊息的各來源檔數，例如「比價 1、舊清單 2」；沒有的來源不寫。"""
    counts = [(source, _products(rows, source=source)) for source in SOURCES]
    return "、".join(f"{source} {n}" for source, n in counts if n)
