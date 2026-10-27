"""「1 每日匯入」分頁：匯入每日更新的交易明細與股價、回補股價 CSV（#112），完成後把 FCN TDCC 帶到起單資料頁（#90）。

MarketData CSV 同時寫收盤價（#153）；維護選單的「匯入歷史收盤價…」「匯入舊商品的價格與比價日…」（#154）、
「匯入可連結標的白名單…」（#273）也在這一頁的背景執行、顯示結果。
匯入收盤價之後比價（#155），完成訊息列出這次有變的存續事件與還沒完成的比價狀態；維護選單的「重新比價全部」
也在這一頁執行。回補股價、歷史收盤價比價完成後把比到的那一天帶到「9 出場清單」，按「下一步」才切過去（#171）。
"""

from __future__ import annotations

import datetime as dt
import tkinter as tk
from collections.abc import Callable, Sequence
from pathlib import Path
from tkinter import messagebox, ttk

from fcn_checker.check_config import ConfigPaths
from fcn_checker.ui import Background, NextButton
from sp_workflow.check_terms import TO_CONFIRM, CalcLine
from sp_workflow.close_prices import CloseSummary
from sp_workflow.daily_import import ImportReceipt, PriceCsvReceipt, PriceSummary
from sp_workflow.eln_whitelist import WhitelistImport
from sp_workflow.legacy_backfill import EXISTING, FILLED, SKIPPED
from sp_workflow.local_settings import FolderMemory
from sp_workflow.observation import EXITED, ON_BOOK, UNLOCKED, WAIT_PRICES, ObservationReceipt, Observed
from sp_workflow.status import Pricing
from sp_workflow.tabs.base import TITLE, BackgroundFactory, DatabaseLogin, Tab, ask_csv, ask_excel
from sp_workflow.tabs.recalc import Releaser, recalc_text
from sp_workflow.window_actions import (
    DailyResult,
    HistoryResult,
    LegacyResult,
    Observation,
    PriceCsvResult,
    Recalc,
    WhitelistResult,
    import_close_history,
    import_eln_whitelist,
    import_legacy_products,
    observe_products,
    read_whitelist_import,
    run_daily_import,
    run_price_csv,
)


class DailyImportTab(Tab):
    def __init__(
        self,
        notebook: ttk.Notebook,
        memory: FolderMemory,
        db: DatabaseLogin,
        config_paths: Callable[[], ConfigPaths],
        on_imported: Callable[[Sequence[str]], bool],
        on_next: Callable[[], None],
        on_observed: Callable[[dt.date], bool],
        on_exits: Callable[[], None],
        *,
        background: BackgroundFactory = Background,
    ) -> None:
        columns = (("tdcc", "TDCC", 130), ("kind", "結果", 150), ("detail", "說明", 440))
        super().__init__(notebook, "每日匯入", columns, db, background)
        self.memory = memory
        self.config_paths = config_paths
        self.on_imported = on_imported
        self.on_observed = on_observed
        folders = memory.load()
        self.daily_var = tk.StringVar(value=folders.get("daily", ""))
        self.trades_only_var = tk.BooleanVar(value=False)
        self.csv_var = tk.StringVar(value=folders.get("price_csv", ""))
        self.history_var = tk.StringVar(value=folders.get("close_history", ""))
        self.legacy_var = tk.StringVar(value=folders.get("legacy_backfill", ""))
        self.whitelist_var = tk.StringVar(value=folders.get("eln_whitelist", ""))
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
            "MarketData_YYYYMMDD.csv，一次一個：每天匯（收盤價給比價用），放假或請假後也用它回補",
        )
        self.result_area(body, row)
        self.start_button = ttk.Button(self.buttons, text="匯入", command=self.start, style="Primary.TButton")
        self.start_button.pack(side=tk.LEFT)
        self.csv_button = ttk.Button(self.buttons, text="回補股價", command=self.start_csv)
        self.csv_button.pack(side=tk.LEFT, padx=(8, 0))
        self.releaser = Releaser(self, db, self._selected_tdcc, self._released, busy=self._busy)
        self.next_button = NextButton(self.buttons, "下一步：起單資料 →", on_next)
        self.exits_button = NextButton(self.buttons, "下一步：出場清單 →", on_exits)  # #171
        self.place_table(2)

    def _busy(self, busy: bool) -> None:
        for button in (self.start_button, self.csv_button):
            button.configure(state=tk.DISABLED if busy else tk.NORMAL)

    def _starting(self, **remember: str) -> None:
        """開始匯入（登入之後）：記住選的檔，收起下一步與上一次的放行差異。"""
        if remember:
            self.memory.remember(**remember)
        self.next_button.withdraw()
        self.exits_button.withdraw()
        self.releaser.show(Recalc())

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
        daily = Path(daily_text)
        prices = not self.trades_only_var.get()
        paths = self.config_paths()
        self.runner.run_action(
            lambda settings: run_daily_import(settings, daily, paths, prices=prices),
            "匯入中…",
            self._done,
            before=lambda: self._starting(daily=str(daily)),
        )

    def start_csv(self) -> None:
        """回補股價（#112）：匯入一個 MarketData CSV，再重算核對條件；交易明細不動。"""
        csv_text = self.csv_var.get().strip()
        if not csv_text:
            messagebox.showerror(TITLE, "請先選擇股價 CSV。")
            return
        source = Path(csv_text)
        paths = self.config_paths()
        self.runner.run_action(
            lambda settings: run_price_csv(settings, source, paths),
            "回補股價中…",
            self._csv_done,
            before=lambda: self._starting(price_csv=str(source)),
        )

    def _csv_done(self, result: PriceCsvResult) -> None:
        receipt, recalc = result.receipt, result.recalc
        for line in receipt.lines:
            self.add_row((line.tdcc, line.kind, line.detail), problem=line.problem)
        self._show_recalc(recalc)
        self._show_observation(result.observation.receipt)
        problems = len(receipt.lines)
        kpis = [("要注意（項）", problems, problems > 0), ("股價（筆）", receipt.prices.count, False)]
        if receipt.closes is not None:
            kpis.append(("收盤價（筆）", receipt.closes.stored, False))
        self.finish(
            kpis + self._recalc_kpis(recalc) + _observation_kpis(result.observation),
            f"完成（第 {receipt.run_id} 次匯入，回補股價）：{receipt.close_date:%Y-%m-%d} 收盤，"
            f"存成取價日 {receipt.prices.price_date:%Y-%m-%d}（等同隔天早上取價）。\n"
            f"{_price_text(receipt.prices)}\n{_close_text(receipt)}\n{recalc_text(recalc)}"
            f"\n{observation_text(result.observation)}{self._hand_off_exits(result.observation)}"
            f"\n錯誤清單：{receipt.error_book_path}",
        )
        self.releaser.show(recalc)

    def can_import_history(self) -> bool:
        """維護選單的「匯入歷史收盤價…」「重新比價全部」「匯入舊商品的價格與比價日…」「匯入可連結標的白名單…」：
        這一頁沒有在匯入時才能按。"""
        return str(self.start_button.cget("state")) != tk.DISABLED

    def import_history(self) -> None:
        """匯入歷史收盤價（#153，維護選單）：選一份 HP 格式的 Excel，整份檢查後寫入。"""
        chosen = ask_excel("選擇歷史收盤價", self.history_var)
        if not chosen:
            return
        source = Path(chosen)
        paths = self.config_paths()

        def starting() -> None:
            self.history_var.set(str(source))
            self._starting(close_history=str(source))

        self.runner.run_action(
            lambda settings: import_close_history(settings, source, paths),
            "匯入歷史收盤價中…（整份檢查完才寫入）",
            self._history_done,
            before=starting,
        )

    def _history_done(self, result: HistoryResult) -> None:
        observation, result = result.observation, result.receipt
        for p in result.problems:
            if p.first is None or p.last is None:
                when = "整列"
            elif p.first == p.last:
                when = f"{p.first:%Y-%m-%d}"
            else:
                when = f"{p.first:%Y-%m-%d}～{p.last:%Y-%m-%d}"
            self.add_row((p.code, p.reason, f"{when}，{p.cells} 格"), problem=True)
        self._show_observation(observation.receipt)
        listed = sum(p.cells for p in result.problems)
        kpis = [
            ("收盤價（筆）", result.summary.stored, False),
            ("沒有價格（格）", result.missing, result.missing > 0),
            ("錯誤清單（項）", len(result.problems), bool(result.problems)),
        ]
        book = f"錯誤清單：{result.error_book_path}" if result.error_book_path else "沒有問題，沒有寫錯誤清單。"
        self.finish(
            kpis + _observation_kpis(observation),
            f"完成（歷史收盤價）：{result.first_date:%Y-%m-%d}～{result.last_date:%Y-%m-%d}，"
            f"代碼 {result.codes} 個，沒有價格 {result.missing} 格（休市日不算）。\n"
            f"{_summary_text(result.summary)}休市日有值、沒有存 {result.closed} 格；"
            f"超出交易所假日表涵蓋範圍、沒有存 {result.uncovered} 格；"
            f"錯誤清單列了 {len(result.problems)} 項、{listed} 格。\n{book}\n{observation_text(observation)}"
            f"{self._hand_off_exits(observation)}",
        )

    def import_legacy(self) -> None:
        """匯入舊商品的價格與比價日（#154，維護選單）：選一份參考條件表格式的 Excel，只補已鎖住、還缺資料的商品。"""
        chosen = ask_excel("選擇舊商品的參考條件表（例如回填後的庫存總表）", self.legacy_var)
        if not chosen:
            return
        source = Path(chosen)
        paths = self.config_paths()

        def starting() -> None:
            self.legacy_var.set(str(source))
            self._starting(legacy_backfill=str(source))

        self.runner.run_action(
            lambda settings: import_legacy_products(settings, source, paths),
            "匯入舊商品的價格與比價日中…",
            self._legacy_done,
            before=starting,
        )

    def _legacy_done(self, result: LegacyResult) -> None:
        observation, receipt = result.observation, result.receipt
        for line in receipt.lines:
            if line.result != EXISTING:  # 已存在的不列，數字在上面
                self.add_row((line.tdcc, f"舊商品{line.result}", line.detail), problem=line.result == SKIPPED)
        self._show_observation(observation.receipt)
        filled, existing, skipped = (receipt.count(r) for r in (FILLED, EXISTING, SKIPPED))
        kpis = [
            ("已補（檔）", filled, False),
            ("已存在（檔）", existing, False),
            ("不匯入（檔）", skipped, skipped > 0),
        ]
        book = f"錯誤清單：{receipt.error_book_path}" if receipt.error_book_path else "沒有問題，沒有寫錯誤清單。"
        events = (
            f"寫了舊清單事件 {receipt.events} 筆。" if receipt.has_status else "表上沒有「庫存狀態」欄，沒有寫事件。"
        )
        self.finish(
            kpis + _observation_kpis(observation),
            f"完成（舊商品的價格與比價日）：已補 {filled} 檔、已存在 {existing} 檔、不匯入 {skipped} 檔；{events}\n"
            f"{book}\n{observation_text(observation)}",
        )

    def import_whitelist(self) -> None:
        """匯入可連結標的白名單（#273，維護選單）：先給作業人員看資料庫目前這一份，確認後選「ELN可連結標的白名單.xlsm」，
        整份檢查後取代資料庫的白名單，再重算。"""

        def confirm(current: WhitelistImport | None) -> None:
            now = (
                "資料庫還沒有匯入過白名單，風險等級暫用設定檔的備用 ETF 清單。"
                if current is None
                else f"資料庫目前的白名單：{whitelist_text(current)}"
            )
            self.status_var.set(now)
            if not messagebox.askyesno(TITLE, f"{now}\n\n要選新的一份匯入、整份取代嗎？", parent=self.frame):
                self.status_var.set(f"{now}\n這次沒有匯入。")
                return
            chosen = ask_excel("選擇可連結標的白名單（ELN可連結標的白名單.xlsm）", self.whitelist_var, "*.xlsm *.xlsx")
            if not chosen:
                return
            source = Path(chosen)
            paths = self.config_paths()

            def starting() -> None:
                self.whitelist_var.set(str(source))
                self._starting(eln_whitelist=str(source))

            self.runner.run_action(
                lambda settings: import_eln_whitelist(settings, source, paths),
                "匯入可連結標的白名單中…（整份檢查完才寫入）",
                self._whitelist_done,
                before=starting,
            )

        self.runner.run_action(read_whitelist_import, "讀取目前的白名單…", confirm)

    def _whitelist_done(self, result: WhitelistResult) -> None:
        receipt, recalc = result.receipt, result.recalc
        if receipt.previous is not None:  # 第一次匯入全部都是新增，不逐檔列
            for kind, rows in (("白名單新增", receipt.added), ("白名單移除", receipt.removed)):
                for row in rows:
                    self.add_row((row.code, kind, "、".join(row.groups)), problem=False)
            for before, after in receipt.regrouped:
                detail = f"{'、'.join(before.groups)} → {'、'.join(after.groups)}"
                self.add_row((after.code, "白名單分類變了", detail), problem=False)
        self._show_recalc(recalc)
        changes = (
            "第一次匯入。"
            if receipt.previous is None
            else f"和上一次匯入的那份（抓取日 {receipt.previous:%Y-%m-%d}）比：新增 {len(receipt.added)} 檔、"
            f"移除 {len(receipt.removed)} 檔、分類變了 {len(receipt.regrouped)} 檔。"
        )
        self.finish(
            [("白名單（檔）", receipt.code_count, False), ("ETF（檔）", receipt.etf_count, False)]
            + self._recalc_kpis(recalc),
            f"完成（可連結標的白名單）：抓取日 {receipt.fetched_on:%Y-%m-%d}，{receipt.code_count} 檔"
            f"（ETF {receipt.etf_count} 檔）；{changes}\n"
            f"風險等級的 ETF 清單改用這一份（加上設定檔的補充 ETF）；已鎖住的商品不變。\n{recalc_text(recalc)}",
        )
        self.releaser.show(recalc)

    def reobserve(self) -> None:
        """重新比價全部（#155，維護選單）：不匯入，直接用收盤價表現有的價格比一次。"""
        paths = self.config_paths()
        self.runner.run_action(
            lambda settings: observe_products(settings, paths),
            "重新比價全部中…",
            self._reobserved,
            before=self._starting,
        )

    def _reobserved(self, result: ObservationReceipt) -> None:
        observation = Observation(result)
        self._show_observation(result)
        self.finish(_observation_kpis(observation), f"完成（重新比價全部）。\n{observation_text(observation)}")

    def _show_observation(self, receipt: ObservationReceipt | None) -> None:
        """結果表列出這次有變的存續事件、寫不進去的與還沒完成的比價狀態（#155）。"""
        if receipt is None:
            return
        for change in receipt.changes:
            detail = f"{events_text(change.previous)} → {events_text(change.current)}"
            self.add_row((change.tdcc, "存續事件有變", detail), problem=False)
        for tdcc, reason in receipt.errors:
            self.add_row((tdcc, "比價寫不進去", reason), problem=True)
        for r in receipt.unfinished():
            self.add_row((r.tdcc, f"比價{r.status}", _observed_detail(r)), problem=True)

    def _hand_off_exits(self, observation: Observation) -> str:
        """比價比到某一天時帶到出場清單（#171），真的帶入了才有下一步；回傳接在完成訊息後面的一句。"""
        receipt = observation.receipt
        if receipt is None or receipt.through is None:
            return ""
        if not self.on_observed(receipt.through):
            return "\n「9 出場清單」正在處理，比到的那一天沒有帶入。"
        self.exits_button.offer()
        return f"\n已把 {receipt.through:%Y-%m-%d} 帶到「9 出場清單」。"

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

    def _done(self, result: DailyResult) -> None:
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


def _summary_text(closes: CloseSummary) -> str:
    text = f"收盤價：存了 {closes.stored} 筆"
    if closes.replaced:
        text += f"（取代原本的 {closes.replaced} 筆，其中 {closes.changed} 筆的值不同，舊值留在紀錄）"
    return text + "；"


def _close_text(receipt: PriceCsvReceipt) -> str:
    """回補股價完成訊息裡的收盤價一句（#153）。"""
    if receipt.closes is None:
        return "收盤價：讀不到交易所假日表，這次沒有存，修好假日表後再匯一次這個 CSV。"
    text = _summary_text(receipt.closes)
    if receipt.closed:
        text += f"那一天休市、沒有存 {len(receipt.closed)} 個代碼；"
    if receipt.close_uncovered:
        text += (
            f"那一天超出交易所假日表的涵蓋範圍、沒有存 {len(receipt.close_uncovered)} 個代碼"
            "（補了假日表後再匯一次這個 CSV）；"
        )
    if receipt.close_conflicts:
        text += f"去掉 Equity 這類字尾後重複、值不同、沒有存：{'、'.join(receipt.close_conflicts)}；"
    return text.rstrip("；") + "。"


def _observation_kpis(observation: Observation) -> list[tuple[str, int, bool]]:
    receipt = observation.receipt
    if receipt is None:
        return [("比價沒有跑", 1, True)] if observation.error else []
    unfinished = len(receipt.unfinished())
    return [("存續事件有變（檔）", len(receipt.changes), False), ("比價沒完成（檔）", unfinished, unfinished > 0)]


def observation_text(observation: Observation, saved: str = "匯入") -> str:
    """完成訊息裡的比價一句（#155）；`saved` 是比價前寫好的東西（拆股登記用「登記」）。"""
    receipt = observation.receipt
    if receipt is None and not observation.error:
        return "比價：這次沒有跑。"
    if receipt is None:
        return f"比價：沒有跑，{observation.error}。{saved}已經完成，修好後按「維護 → 重新比價全部」。"
    through = "收盤價表還沒有資料" if receipt.through is None else f"比到 {receipt.through:%Y-%m-%d}"
    counts = "、".join(
        f"{status} {receipt.count(status)}" for status in (ON_BOOK, EXITED, WAIT_PRICES, Pricing.TO_CONFIRM)
    )
    text = f"比價：{through}，共 {len(receipt.results)} 檔（{counts}）；存續事件有變 {len(receipt.changes)} 檔"
    if receipt.errors:
        text += f"；寫不進去 {len(receipt.errors)} 檔"
    return text + "。"


def events_text(events: Sequence[tuple[str, str, str | None]]) -> str:
    texts = [" ".join(part for part in (event, underlying, day) if part) for event, day, underlying in events]
    return "、".join(texts) or "沒有"


def _observed_detail(r: Observed) -> str:
    through = "" if r.observed_through is None else f"（已比到 {r.observed_through:%Y-%m-%d}）"
    return f"{r.reason}{through}" + ("" if r.locked else f"；{UNLOCKED}")


def _calc_row(line: CalcLine) -> tuple[str, str, str]:
    """每日匯入結果表的一列：核對條件重算後的狀態。"""
    return line.tdcc, f"核對條件：{line.status}", line.reason


def whitelist_text(current: WhitelistImport) -> str:
    """一份白名單的說明：抓取日、檔數、誰在何時從哪個檔匯入。"""
    return (
        f"抓取日 {current.fetched_on:%Y-%m-%d}，{current.code_count} 檔（ETF {current.etf_count} 檔）；"
        f"{current.imported_by} 在 {current.imported_at.astimezone():%Y-%m-%d %H:%M} 從 {current.source_file} 匯入。"
    )
