"""「8 進度」分頁（#156、#186、ADR 0019）：每檔商品在五個階段的哪一個、核對前三件齊了沒、各步日期、存續狀態與
比價狀態（#170）；篩選、排序、匯出 Excel。

按「重新整理」在背景讀資料庫（`window_actions.load_progress_rows`），篩選與排序只在畫面上做、不再連資料庫；
「匯出 Excel…」把目前篩選、排序後的整張表寫成一份 Excel（`progress.export_progress`）；
結果表右上方的「複製待確認 TDCC」只把表上比價待確認的 TDCC 一行一筆放進剪貼簿，貼回「3 準備」重新核對（#289）。
人工能做的三件事只在這一頁登記（#157）：對選中的商品「登記已寄出」「登記顧客贖回…」、對一檔的自動事件
「提出異議…」與「解除異議…」；都在背景寫資料庫，一檔失敗只影響那一檔，寫完重新整理。
「批次登記已寄出…」（#186）選一個交易日，重新讀進度、列出交易日在這天以前的待寄信商品，確認後各寫一筆「已寄出」。
"""

from __future__ import annotations

import datetime as dt
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from fcn_checker.check_config import ConfigPaths
from fcn_checker.date_picker import DateEntry
from fcn_checker.ui import Background, to_clipboard
from sp_workflow.lifecycle import DELIVERY, IN_STOCK, KO, MATURITY, REDEMPTION
from sp_workflow.local_settings import FolderMemory
from sp_workflow.observation import STATUSES as PRICING_STATUSES
from sp_workflow.progress import (
    DATE_COLUMNS,
    FINISHED,
    PROGRESS,
    READY_COLUMNS,
    ProgressExport,
    ProgressRow,
    SentBatch,
    by_trade_date,
    export_name,
    export_progress,
    filter_rows,
    to_confirm_tdccs,
)
from sp_workflow.tabs.base import TITLE, BackgroundFactory, DatabaseLogin, Tab
from sp_workflow.tabs.progress_dialogs import (
    ask_objection,
    ask_redemption,
    ask_resolution,
    ask_sent_through,
    tdcc_list,
)
from sp_workflow.window_actions import (
    EventChoices,
    Registered,
    event_choices,
    find_sent_batch,
    load_progress_rows,
    mark_sent,
    mark_sent_batch,
    object_to_event,
    register_redemptions,
    resolve_event_objection,
    sent_before,
)

LIFECYCLES = (IN_STOCK, KO, DELIVERY, MATURITY, REDEMPTION)
ALL_ISSUERS = "全部"
_READY_KEYS = tuple(f"ready_{i}" for i in range(len(READY_COLUMNS)))
_DATE_KEYS = tuple(f"date_{i}" for i in range(len(DATE_COLUMNS)))
_DESCENDING_FIRST = frozenset(("trade_date", "days", "observed_through", *_DATE_KEYS))  # 第一次點就由大到小的欄
COLUMNS = (
    ("tdcc", "TDCC", 120),
    ("issuer", "上手", 60),
    ("trade_date", "交易日", 90),
    ("days", "距今天數", 70),
    ("progress", "作業進度", 90),
    *((key, name, 70) for key, name in zip(_READY_KEYS, READY_COLUMNS, strict=True)),
    ("lifecycle", "存續狀態", 80),
    ("pricing", "比價狀態", 110),
    ("observed_through", "KO比價", 90),
    ("pricing_reason", "比價原因", 220),
    ("ki", "KI", 160),  # #196
    ("objection", "異議", 160),
    *((key, name, 95) for key, name in zip(_DATE_KEYS, DATE_COLUMNS, strict=True)),
    ("reason", "卡住的原因", 320),
)


class ProgressTab(Tab):
    def __init__(
        self,
        notebook: ttk.Notebook,
        memory: FolderMemory,
        db: DatabaseLogin,
        config_paths: Callable[[], ConfigPaths],
        *,
        background: BackgroundFactory = Background,
    ) -> None:
        super().__init__(notebook, "進度", COLUMNS, db, background)
        self.memory = memory
        self.config_paths = config_paths
        self.rows: tuple[ProgressRow, ...] | None = None  # 最近一次重新整理讀到的全部商品；還沒讀過是 None
        self.shown: list[ProgressRow] = []  # 篩選、排序後畫面上的列
        self.sort: tuple[str, bool] | None = None  # 點欄位排序：(欄, 由大到小)；None 是預設（交易日由新至舊）
        self.notice = ""  # 剛做完的登記（#157），重新整理後寫在狀態列最前面
        self.status_var.set("按「重新整理」讀資料庫，列出每檔商品的作業進度；篩選與排序不會再讀資料庫。")

        self.page_header(
            "作業進度",
            "每檔商品在哪個階段、核對前三件齊了沒、各步的日期、距交易日幾天、存續狀態與比價狀態；落後的依距今天數排在上面。",
        )
        body = self.body(self.card(1))
        filters = ttk.Frame(body, style="CardBody.TFrame")
        filters.grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(filters, text="交易日", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        self.first_var, self.last_var = tk.StringVar(), tk.StringVar()
        dates = ttk.Frame(filters, style="CardBody.TFrame")
        dates.grid(row=0, column=1, sticky="w", padx=(8, 0))
        for i, var in enumerate((self.first_var, self.last_var)):
            field = DateEntry(dates, var, on_pick=self.refilter, style="CardBody.TFrame")  # 小日曆（#192）
            field.grid(row=0, column=i * 2)
            field.entry.bind("<Return>", lambda _event: self.refilter())
            field.entry.bind("<FocusOut>", lambda _event: self.refilter())
        ttk.Label(dates, text="～", style="Card.TLabel").grid(row=0, column=1, padx=4)
        ttk.Label(dates, text="YYYY-MM-DD，空白是不限", style="CardMuted.TLabel").grid(row=0, column=3, padx=(8, 0))
        ttk.Label(filters, text="上手", style="Card.TLabel").grid(row=0, column=2, sticky="w", padx=(24, 0))
        self.issuer_var = tk.StringVar(value=ALL_ISSUERS)
        self.issuer_box = ttk.Combobox(
            filters, textvariable=self.issuer_var, values=(ALL_ISSUERS,), state="readonly", width=10
        )
        self.issuer_box.grid(row=0, column=3, sticky="w", padx=(8, 0))
        self.issuer_box.bind("<<ComboboxSelected>>", lambda _event: self.refilter())
        self.progress_vars = self._checks(filters, 1, "作業進度", PROGRESS)
        self.lifecycle_vars = self._checks(filters, 2, "存續狀態", LIFECYCLES)
        self.pricing_vars = self._checks(filters, 3, "比價狀態", PRICING_STATUSES)
        self.result_area(body, 1)
        self.refresh_button = ttk.Button(self.buttons, text="重新整理", command=self.refresh, style="Primary.TButton")
        self.refresh_button.pack(side=tk.LEFT)
        self.export_button = ttk.Button(self.buttons, text="匯出 Excel…", command=self.export)
        self.export_button.pack(side=tk.LEFT, padx=(8, 0))
        self.sent_button = ttk.Button(self.buttons, text="登記已寄出", command=self.record_sent)
        self.batch_sent_button = ttk.Button(self.buttons, text="批次登記已寄出…", command=self.record_sent_batch)
        self.redeem_button = ttk.Button(self.buttons, text="登記顧客贖回…", command=self.register_redemption)
        self.object_button = ttk.Button(self.buttons, text="提出異議…", command=self.raise_objection)
        self.resolve_button = ttk.Button(self.buttons, text="解除異議…", command=self.resolve_objection)
        for button in self._actions():
            button.pack(side=tk.LEFT, padx=(8, 0))
        self.place_table(2)
        self.copy_to_confirm_button = self.table_area.add_button(
            "複製待確認 TDCC",
            self.copy_to_confirm,
            copied_text="已複製 {} 筆",
            empty_text="表上沒有待確認",
            width=16,
        )  # #289
        for key, _, _ in COLUMNS:
            self.table.heading(key, command=lambda k=key: self.sort_by(k))
        self._busy(False)

    def _checks(self, parent: ttk.Frame, row: int, label: str, names: tuple[str, ...]) -> dict[str, tk.BooleanVar]:
        """一列多選的勾選框；都不勾等於全部。"""
        ttk.Label(parent, text=label, style="Card.TLabel").grid(row=row, column=0, sticky="nw", pady=(10, 0))
        box = ttk.Frame(parent, style="CardBody.TFrame")
        box.grid(row=row, column=1, columnspan=3, sticky="w", padx=(8, 0), pady=(10, 0))
        found = {}
        for i, name in enumerate(names):
            var = tk.BooleanVar(value=False)
            ttk.Checkbutton(box, text=name, variable=var, command=self.refilter, style="Card.TCheckbutton").grid(
                row=0, column=i, sticky="w", padx=(0, 12)
            )
            found[name] = var
        ttk.Label(box, text="都不勾是全部", style="CardMuted.TLabel").grid(row=0, column=len(names), sticky="w")
        return found

    def _busy(self, busy: bool) -> None:
        """處理中：按鈕不能按、狀態列恢復一般顏色；不在處理時，匯出與登記要畫面上有列才能按。"""
        if busy:
            self.mark_done(False)
        self.refresh_button.configure(state=tk.DISABLED if busy else tk.NORMAL)
        self.export_button.configure(state=tk.DISABLED if busy or not self.shown else tk.NORMAL)
        for button in self._actions():
            button.configure(state=tk.DISABLED if busy or not self.shown else tk.NORMAL)

    def _reset(self, clear: bool) -> None:
        """結果表由 `refilter` 重畫，開始動作時不清（重新整理在開始前 restart，登記與匯出留著上一次的表）。"""

    def _actions(self) -> tuple[ttk.Button, ...]:
        """登記用的按鈕：重新整理過、沒有在背景做事時才能按。"""
        return (self.sent_button, self.batch_sent_button, self.redeem_button, self.object_button, self.resolve_button)

    # ---------- 重新整理 ----------

    def refresh(self, notice: str = "") -> None:
        """重新讀資料庫；`notice` 是剛做完的登記，寫在狀態列最前面。"""
        if self.working:
            return
        paths = self.config_paths()

        def starting() -> None:
            self.notice = notice
            self.restart()

        self.runner.run_action(
            lambda settings: load_progress_rows(settings, paths), "讀取作業進度中…", self._loaded, before=starting
        )

    def _loaded(self, result: tuple[ProgressRow, ...]) -> None:
        self.rows = result
        issuers = sorted({row.issuer for row in result if row.issuer})
        self.issuer_box.configure(values=(ALL_ISSUERS, *issuers))
        if self.issuer_var.get() not in (ALL_ISSUERS, *issuers):
            self.issuer_var.set(ALL_ISSUERS)
        self.refilter()

    # ---------- 篩選、排序 ----------

    def _date(self, var: tk.StringVar, label: str) -> tuple[dt.date | None, str]:
        text = var.get().strip()
        if not text:
            return None, ""
        try:
            return dt.date.fromisoformat(text), ""
        except ValueError:
            return None, f"{label}「{text}」不是 YYYY-MM-DD，這一邊先不限。"

    def refilter(self) -> None:
        """依目前的篩選條件與排序重畫結果表；還沒重新整理過時不動。"""
        if self.rows is None:
            return
        first, first_error = self._date(self.first_var, "交易日起")
        last, last_error = self._date(self.last_var, "交易日迄")
        issuer = self.issuer_var.get()
        rows = filter_rows(
            self.rows,
            first=first,
            last=last,
            progress={name for name, var in self.progress_vars.items() if var.get()},
            lifecycle={name for name, var in self.lifecycle_vars.items() if var.get()},
            pricing={name for name, var in self.pricing_vars.items() if var.get()},
            issuer="" if issuer == ALL_ISSUERS else issuer,
        )
        self.shown = self._sorted(rows)
        selected = {self.table.set(item, "tdcc") for item in self.table.selection()}  # 重畫後還在表上的照樣選著
        self.table.delete(*self.table.get_children())
        keep = []
        for row in self.shown:
            item = self.table.insert("", tk.END, values=_cells(row), tags=("problem",) if row.problem else ())
            if row.tdcc in selected:
                keep.append(item)
        self.table.selection_set(keep)
        unfinished = sum(row.progress != FINISHED for row in self.shown)
        notes = " ".join(error for error in (first_error, last_error) if error)
        self.finish(
            (("顯示（檔）", len(self.shown), False), ("還沒完成（檔）", unfinished, False)),
            f"{self.notice}共 {len(self.rows)} 檔，篩選後 {len(self.shown)} 檔；依{self._sort_text()}排序。{notes}".rstrip(),
        )
        self.notice = ""  # 登記結果只在寫完後那一次顯示
        self._busy(self.working)

    def copy_to_confirm(self) -> int:
        """表上比價待確認的 TDCC 一行一筆放進剪貼簿，貼回「3 準備」就能用（#289）；回傳幾筆，沒有時不動剪貼簿。"""
        tdccs = to_confirm_tdccs(self.shown)
        if tdccs:
            to_clipboard(self.table, "\n".join(tdccs))
        return len(tdccs)

    def sort_by(self, key: str) -> None:
        """點欄位排序：第一次由大到小（距今天數、日期）或由小到大（文字），再點一次反過來。"""
        current, descending = self.sort or ("trade_date", True)  # 預設就是交易日由新至舊
        self.sort = (key, not descending if key == current else key in _DESCENDING_FIRST)
        self.refilter()

    def _sorted(self, rows: list[ProgressRow]) -> list[ProgressRow]:
        if self.sort is None:
            return by_trade_date(rows)
        key, descending = self.sort
        index = [k for k, _, _ in COLUMNS].index(key)
        present = [row for row in rows if _sort_value(row, index) is not None]
        blank = [row for row in rows if _sort_value(row, index) is None]
        present.sort(key=lambda row: _sort_value(row, index), reverse=descending)
        return present + sorted(blank, key=lambda row: row.tdcc)  # 空白的一律放最後

    def _sort_text(self) -> str:
        if self.sort is None:
            return "交易日由新至舊"
        key, descending = self.sort
        name = next(label for k, label, _ in COLUMNS if k == key)
        return f"{name}{'由大到小' if descending else '由小到大'}"

    # ---------- 登記（#157） ----------

    def _selected(self, *, one: bool = False) -> list[str]:
        """表上選中的 TDCC；沒選或要一檔卻選了多檔時說明，回傳空的。"""
        tdccs = [self.table.set(item, "tdcc") for item in self.table.selection()]
        if not tdccs:
            messagebox.showinfo(TITLE, "請先在表上選商品（可以按住 Ctrl 或 Shift 多選）。", parent=self.master)
        elif one and len(tdccs) > 1:
            messagebox.showinfo(TITLE, "這個動作一次只能選一檔。", parent=self.master)
            return []
        return tdccs

    def record_sent(self) -> None:
        """登記已寄出：先在背景查選中的哪幾檔已經登記過，問過才寫；每檔寫一筆「已寄出」。"""
        if self.working:
            return
        tdccs = self._selected()
        if not tdccs:
            return
        self.runner.run_action(
            lambda settings: sent_before(settings, tdccs),
            "檢查是否登記過已寄出中…",
            lambda result: self._sent_checked(tdccs, result),
        )

    def _sent_checked(self, tdccs: list[str], result: tuple[str, ...]) -> None:
        again = [t for t in tdccs if t in result]
        if again:
            fresh = len(tdccs) - len(again)
            answer = messagebox.askyesnocancel(
                TITLE,
                f"{tdcc_list(again)} 已經登記過已寄出。\n"
                f"是：這幾檔也再寫一筆；否：只寫還沒登記過的 {fresh} 檔；取消：都不寫。",
                parent=self.master,
            )
            if answer is None:
                self.refilter()
                return
            if not answer:
                tdccs = [t for t in tdccs if t not in again]
                if not tdccs:
                    messagebox.showinfo(TITLE, "選中的都已經登記過已寄出，這次沒有寫。", parent=self.master)
                    self.refilter()
                    return
        elif not messagebox.askokcancel(TITLE, f"登記 {len(tdccs)} 檔已寄出：{tdcc_list(tdccs)}？", parent=self.master):
            self.refilter()
            return
        self.runner.run_action(lambda settings: mark_sent(settings, tdccs), "登記已寄出中…", self._registered("已寄出"))

    def record_sent_batch(self) -> None:
        """批次登記已寄出（#186）：選交易日，在背景重新讀進度找出這批，列出來確認後才寫。"""
        if self.working:
            return
        through = ask_sent_through(self.master)
        if through is None:
            return
        paths = self.config_paths()
        self.runner.run_action(
            lambda settings: find_sent_batch(settings, paths, through), "找出要登記已寄出的商品中…", self._batch_found
        )

    def _batch_found(self, result: SentBatch) -> None:
        skipped_note = f"交易日 {result.through} 以前還沒核對完、這次沒有登記的 {result.unchecked} 檔"
        if not result.tdccs:
            messagebox.showinfo(
                TITLE,
                f"交易日 {result.through} 以前沒有待寄信的商品，這次沒有寫。\n{skipped_note}。",
                parent=self.master,
            )
            self.refilter()
            return
        if not messagebox.askokcancel(
            TITLE,
            f"登記 {len(result.tdccs)} 檔已寄出（交易日 {result.through} 以前、核對通過、還沒登記已寄出）："
            f"{tdcc_list(result.tdccs)}？\n{skipped_note}。",
            parent=self.master,
        ):
            self.refilter()
            return
        self.runner.run_action(
            lambda settings: mark_sent_batch(settings, result),
            "批次登記已寄出中…",
            self._registered("已寄出", f"；{skipped_note}"),
        )

    def register_redemption(self) -> None:
        """登記顧客贖回：填事件日與備註，選中的每檔寫一筆來源「人工」的事件。"""
        if self.working:
            return
        tdccs = self._selected()
        if not tdccs:
            return
        found = ask_redemption(self.master, tdccs)
        if found is None:
            return
        day, note = found
        self.runner.run_action(
            lambda settings: register_redemptions(settings, tdccs, day, note),
            "登記顧客贖回中…",
            self._registered("顧客贖回"),
        )

    def _registered(self, what: str, note: str = "") -> Callable[[Registered], None]:
        """寫完的處理：有寫不進去的跳警告；重新整理，狀態列最前面寫登記結果（`note` 接在最後）。"""

        def done(result: Registered) -> None:
            text = f"登記{what}：寫了 {len(result.done)} 檔"
            if result.failed:
                failed = result.failed[:10]
                text += f"；寫不進去 {len(result.failed)} 檔：" + "；".join(f"{t} {why}" for t, why in failed)
                if len(result.failed) > len(failed):
                    text += f"；…另外 {len(result.failed) - len(failed)} 檔"
                messagebox.showwarning(TITLE, text, parent=self.master)
            self.refresh(text + note + "。\n")

        return done

    def raise_objection(self) -> None:
        """提出異議：選一檔，讀它比價產生的事件，選一筆、寫備註。"""
        self._choose_event(self._objection_chosen)

    def resolve_objection(self) -> None:
        """解除異議：選一檔，讀它還沒解除的異議，選一筆解除。"""
        self._choose_event(self._resolution_chosen)

    def _choose_event(self, on_choices: Callable[[EventChoices], None]) -> None:
        if self.working:
            return
        tdccs = self._selected(one=True)
        if not tdccs:
            return
        tdcc = tdccs[0]
        self.runner.run_action(lambda settings: event_choices(settings, tdcc), "讀取存續事件中…", on_choices)

    def _objection_chosen(self, choices: EventChoices) -> None:
        if not choices.events:
            messagebox.showinfo(TITLE, f"{choices.tdcc} 沒有比價產生的自動事件，不能提出異議。", parent=self.master)
            self.refilter()
            return
        found = ask_objection(self.master, choices.tdcc, choices.events)
        if found is None:
            self.refilter()
            return
        event_id, note = found
        self.runner.run_action(
            lambda settings: object_to_event(settings, event_id, note),
            "提出異議中…",
            self._changed(f"{choices.tdcc} 已提出異議（待確認）。\n"),
        )

    def _resolution_chosen(self, choices: EventChoices) -> None:
        if not choices.objections:
            messagebox.showinfo(TITLE, f"{choices.tdcc} 沒有還沒解除的異議。", parent=self.master)
            self.refilter()
            return
        objection_id = ask_resolution(self.master, choices.tdcc, choices.objections)
        if objection_id is None:
            self.refilter()
            return
        self.runner.run_action(
            lambda settings: resolve_event_objection(settings, objection_id),
            "解除異議中…",
            self._changed(f"{choices.tdcc} 已解除異議。\n"),
        )

    def _changed(self, notice: str) -> Callable[[int], None]:
        return lambda _count: self.refresh(notice)

    # ---------- 匯出 ----------

    def export(self) -> None:
        if self.working or not self.shown:
            return
        chosen = filedialog.asksaveasfilename(
            title="匯出作業進度",
            initialdir=self.memory.load().get("progress_export") or None,
            initialfile=export_name(),
            defaultextension=".xlsx",
            filetypes=[("Excel 活頁簿", "*.xlsx")],
            parent=self.master,
        )
        if not chosen:
            return
        path = Path(chosen)
        self.memory.remember(progress_export=str(path.parent))
        rows = list(self.shown)
        self.runner.run_local(lambda: export_progress(rows, path), "匯出作業進度中…", self._exported)

    def _exported(self, result: ProgressExport) -> None:
        self.status_var.set(f"已匯出作業進度 {result.count} 檔（目前的篩選與排序）：{result.path}")
        self.mark_done(True)


def _cells(row: ProgressRow) -> tuple[str, ...]:
    trade_date = "" if row.trade_date is None else f"{row.trade_date:%Y-%m-%d}"
    days = "" if row.days is None else str(row.days)
    through = "" if row.observed_through is None else f"{row.observed_through:%Y-%m-%d}"
    dates = tuple("" if value is None else f"{value.astimezone():%Y-%m-%d}" for value in row.dates)
    return (
        row.tdcc, row.issuer, trade_date, days, row.progress, *row.ready_marks, row.lifecycle, row.pricing_text,
        through, row.pricing_reason, row.ki, row.objection, *dates, row.reason,
    )  # fmt: skip


def _sort_value(row: ProgressRow, index: int) -> object:
    """排序用的值（依 `COLUMNS` 的位置）；作業進度、比價狀態依定義的順序，三件打勾的齊了排後面，空白是 None。"""
    values = (
        row.tdcc,
        row.issuer or None,
        row.trade_date,
        row.days,
        PROGRESS.index(row.progress),
        *row.ready,
        LIFECYCLES.index(row.lifecycle) if row.lifecycle in LIFECYCLES else len(LIFECYCLES),
        PRICING_STATUSES.index(row.pricing) if row.pricing in PRICING_STATUSES else None,
        row.observed_through,
        row.pricing_reason or None,
        row.ki or None,
        row.objection or None,
        *row.dates,
        row.reason or None,
    )
    return values[index]
