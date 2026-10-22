"""「1 每日匯入」分頁：匯入每日更新的交易明細與股價、回補股價 CSV（#112），完成後把 FCN TDCC 帶到起單資料頁（#90）。"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Sequence
from pathlib import Path
from tkinter import messagebox, ttk

from fcn_checker.check_config import ConfigPaths
from fcn_checker.ui import NextButton
from sp_workflow.check_terms import TO_CONFIRM, CalcLine
from sp_workflow.daily_import import ImportReceipt, PriceSummary
from sp_workflow.local_settings import FolderMemory
from sp_workflow.tabs.base import TITLE, DatabaseLogin, Tab, ask_csv, ask_excel
from sp_workflow.tabs.recalc import Releaser, recalc_text
from sp_workflow.window_actions import DailyResult, PriceCsvResult, Recalc, run_daily_import, run_price_csv


class DailyImportTab(Tab):
    def __init__(
        self,
        notebook: ttk.Notebook,
        memory: FolderMemory,
        db: DatabaseLogin,
        config_paths: Callable[[], ConfigPaths],
        on_imported: Callable[[Sequence[str]], bool],
        on_next: Callable[[], None],
    ) -> None:
        super().__init__(notebook, "每日匯入", (("tdcc", "TDCC", 130), ("kind", "結果", 150), ("detail", "說明", 440)))
        self.memory = memory
        self.db = db
        self.config_paths = config_paths
        self.on_imported = on_imported
        folders = memory.load()
        self.daily_var = tk.StringVar(value=folders.get("daily", ""))
        self.trades_only_var = tk.BooleanVar(value=False)
        self.csv_var = tk.StringVar(value=folders.get("price_csv", ""))
        self.status_var.set(f"選每日更新，按「匯入」。資料庫連線設定：{db.env_path}")

        self.page_header(
            "匯入每日更新",
            "把每日更新的 FCN 交易明細與每日股價匯入資料庫，再重算還沒鎖住的商品的核對條件；信箱在資料庫直接維護。",
        )
        body = self.body(self.card(1))
        row = self.field(body, 0, "每日更新", self.daily_var, lambda: ask_excel("選擇每日更新", self.daily_var))
        ttk.Checkbutton(
            body,
            text="只匯交易明細（回補時用：不讀 BBG_Data、不動股價）",
            variable=self.trades_only_var,
            style="Card.TCheckbutton",
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(6, 0))
        row = self.field(
            body,
            row + 1,
            "回補股價",
            self.csv_var,
            lambda: ask_csv("選擇股價 CSV", self.csv_var),
            "放假或請假後回補用：MarketData_YYYYMMDD.csv，一次一個",
        )
        self.result_area(body, row)
        self.start_button = ttk.Button(self.buttons, text="匯入", command=self.start, style="Primary.TButton")
        self.start_button.pack(side=tk.LEFT)
        self.csv_button = ttk.Button(self.buttons, text="回補股價", command=self.start_csv)
        self.csv_button.pack(side=tk.LEFT, padx=(8, 0))
        self.releaser = Releaser(self, db, self._selected_tdcc, self._released, busy=self._busy)
        self.next_button = NextButton(self.buttons, "下一步：起單資料 →", on_next)
        self.place_table(2)

    def _busy(self, busy: bool) -> None:
        for button in (self.start_button, self.csv_button):
            button.configure(state=tk.DISABLED if busy else tk.NORMAL)

    def _selected_tdcc(self) -> str | None:
        item = self.selected_item()
        return None if item is None else self.table.set(item, "tdcc")

    def _released(self, line: CalcLine) -> None:
        iid = f"calc-{line.tdcc}"
        if self.table.exists(iid):
            self.table.item(iid, values=_calc_row(line), tags=("problem",) if line.problem else ())

    def start(self) -> None:
        daily_text = self.daily_var.get().strip()
        if not daily_text:
            messagebox.showerror(TITLE, "請先選擇每日更新。")
            return
        settings = self.db.settings()
        if settings is None:
            return
        daily = Path(daily_text)
        prices = not self.trades_only_var.get()
        self.memory.remember(daily=str(daily))
        self._busy(True)
        self.next_button.withdraw()
        self.releaser.show(Recalc())
        self.restart()
        paths = self.config_paths()

        def run() -> DailyResult:
            return run_daily_import(settings, daily, paths, prices=prices)

        self.run_in_background(run, "匯入中…", self._done)

    def start_csv(self) -> None:
        """回補股價（#112）：匯入一個 MarketData CSV，再重算核對條件；交易明細不動。"""
        csv_text = self.csv_var.get().strip()
        if not csv_text:
            messagebox.showerror(TITLE, "請先選擇股價 CSV。")
            return
        settings = self.db.settings()
        if settings is None:
            return
        source = Path(csv_text)
        self.memory.remember(price_csv=str(source))
        self._busy(True)
        self.next_button.withdraw()
        self.releaser.show(Recalc())
        self.restart()
        paths = self.config_paths()

        def run() -> PriceCsvResult:
            return run_price_csv(settings, source, paths)

        self.run_in_background(run, "回補股價中…", self._csv_done)

    def _csv_done(self, result: object) -> None:
        self._busy(False)
        self.db.after_run(result)
        if not isinstance(result, PriceCsvResult):
            return
        receipt, recalc = result.receipt, result.recalc
        for line in receipt.lines:
            self.add_row((line.tdcc, line.kind, line.detail), problem=line.problem)
        self._show_recalc(recalc)
        problems = len(receipt.lines)
        kpis = [("要注意（項）", problems, problems > 0), ("股價（筆）", receipt.prices.count, False)]
        self.finish(
            kpis + self._recalc_kpis(recalc),
            f"完成（第 {receipt.run_id} 次匯入，回補股價）：{receipt.close_date:%Y-%m-%d} 收盤，"
            f"存成取價日 {receipt.prices.price_date:%Y-%m-%d}（等同隔天早上取價）。\n"
            f"{_price_text(receipt.prices)}\n{recalc_text(recalc)}\n錯誤清單：{receipt.error_book_path}",
        )
        self.releaser.show(recalc)

    def _show_recalc(self, recalc: Recalc) -> None:
        for calc in () if recalc.receipt is None else recalc.receipt.to_show():
            tags = ("problem",) if calc.problem else ()
            self.table.insert("", tk.END, iid=f"calc-{calc.tdcc}", values=_calc_row(calc), tags=tags)

    @staticmethod
    def _recalc_kpis(recalc: Recalc) -> list[tuple[str, int, bool]]:
        if recalc.receipt is None:
            return []
        to_confirm = recalc.receipt.count(TO_CONFIRM)
        return [("核對條件待確認（檔）", to_confirm, to_confirm > 0)]

    def _done(self, result: object) -> None:
        self._busy(False)
        self.db.after_run(result)
        if not isinstance(result, DailyResult):
            return
        receipt, recalc = result.receipt, result.recalc
        for line in receipt.lines:
            self.add_row((line.tdcc, line.kind, line.detail), problem=line.problem)
        self._show_recalc(recalc)
        problems = sum(line.problem for line in receipt.lines)
        normal = len(receipt.lines) - problems
        prices = receipt.prices
        kpis = [("正常（項）", normal, False), ("要注意（項）", problems, problems > 0)]
        if prices is not None:
            kpis.append(("股價（筆）", prices.count, False))
        price_text = "股價：只匯交易明細，股價沒有變動。" if prices is None else _price_text(prices)
        self.finish(
            kpis + self._recalc_kpis(recalc),
            f"完成（第 {receipt.run_id} 次匯入）：{normal} 項正常、{problems} 項要注意；"
            f"略過非 FCN 交易 {receipt.skipped_trade_rows} 列；取消交易 {receipt.cancelled_trade_rows} 列；"
            f"信箱 {receipt.people} 人。\n{price_text}\n{recalc_text(recalc)}"
            f"\n錯誤清單：{receipt.error_book_path}\n{self._hand_off(receipt)}",
        )
        self.releaser.show(recalc)

    def _hand_off(self, result: ImportReceipt) -> str:
        """把交易明細的 FCN TDCC 帶到起單資料頁（#90）；真的帶入了才有下一步。回傳寫在完成訊息的一句。"""
        if not result.fcn_trade_rows:
            return "這份交易明細沒有 FCN，起單資料頁沒有帶入。"
        if not result.fcn_tdccs:
            return "這份交易明細的 FCN 都已取消或 TDCC 不是 12 碼，起單資料頁沒有帶入。"
        if not self.on_imported(result.fcn_tdccs):
            return "起單資料頁沒有帶入（正在處理，或保留原本的 TDCC）。"
        self.next_button.offer()
        return f"已把 FCN TDCC {len(result.fcn_tdccs)} 筆帶到「2 起單資料」。"


def _price_text(prices: PriceSummary) -> str:
    """完成訊息裡的股價一句（#87）：取價日、筆數、取代了幾筆、衝突的代碼。"""
    text = f"股價：取價日 {prices.price_date:%Y-%m-%d}，{prices.count} 筆"
    if prices.replaced:
        text += f"（取代了這一天原本的 {prices.replaced} 筆）"
    if prices.conflicts:
        text += f"；值不一樣的重複代碼 {len(prices.conflicts)} 個，價格沒有存：{'、'.join(prices.conflicts)}"
    return text + "。"


def _calc_row(line: CalcLine) -> tuple[str, str, str]:
    """每日匯入結果表的一列：核對條件重算後的狀態。"""
    return line.tdcc, f"核對條件：{line.status}", line.reason
