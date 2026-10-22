"""「1 每日匯入」分頁（`sp_workflow.tabs.daily`）：完成訊息、回補股價 CSV（#112）、把 FCN TDCC 帶到起單資料頁（#90）、放行差異（#88）。"""

from __future__ import annotations

import datetime as dt
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import messagebox

import pytest

import sp_workflow.tabs.daily
from order_synth import CONFIG, order_row
from sp_workflow.check_terms import DONE, WAIT_PRICES, CalcLine, CalcReceipt, Difference
from sp_workflow.close_prices import CloseSummary, HistoryProblem, HistoryReceipt
from sp_workflow.daily_import import (
    NEW_TRADES,
    PRICE_CONFLICT,
    DailyImportError,
    ImportLine,
    ImportReceipt,
    PriceCsvReceipt,
    PriceSummary,
)
from sp_workflow.legacy_backfill import EXISTING, FILLED, SKIPPED, BackfillLine, BackfillReceipt
from sp_workflow.local_settings import FolderMemory
from sp_workflow.observation import EventChange, ObservationReceipt, Observed
from sp_workflow.order_terms import PENDING as ORDER_PENDING
from sp_workflow.order_terms import OrderRow
from sp_workflow.window_actions import DailyResult, HistoryResult, LegacyResult, Observation, PriceCsvResult, Recalc
from window_synth import NEEDS_RELEASE, UF_DIFF, hint_change_button, new_window, read_result, release_without_database


def daily_receipt(
    tmp_path: Path, tdccs: tuple[str, ...] = (), fcn_rows: int | None = None, *, lines=(), prices=None, calcs=None
) -> DailyResult:
    """每日匯入的結果；`calcs` 是重算核對條件的結果（CalcLine），沒給時是空的重算。"""
    receipt = ImportReceipt(
        run_id=7,
        lines=lines,
        people=3,
        skipped_trade_rows=0,
        cancelled_trade_rows=0,
        error_book_path=tmp_path / "每日匯入錯誤.xlsx",
        prices=prices or PriceSummary(dt.date(2026, 10, 16), 1, 0, ()),
        fcn_trade_rows=len(tdccs) if fcn_rows is None else fcn_rows,
        fcn_tdccs=tdccs,
    )
    return DailyResult(receipt, Recalc(CalcReceipt(tuple(calcs or ())), CONFIG.calendar))


def test_daily_import_page_shows_key_figures_and_restarts_clean(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    lines = (ImportLine("147000000001", NEW_TRADES), ImportLine("", PRICE_CONFLICT, "AAA UW：值不同"))
    receipt = daily_receipt(tmp_path, lines=lines, prices=PriceSummary(dt.date(2026, 10, 16), 510, 0, ("AAA UW",)))

    window.daily._done(receipt)
    assert window.daily.kpis.values == (
        ("正常（項）", 1, False),
        ("要注意（項）", 1, True),
        ("股價（筆）", 510, False),
        ("核對條件待確認（檔）", 0, False),
    )
    assert (
        "股價：取價日 2026-10-16，510 筆；值不一樣的重複代碼 1 個，價格沒有存：AAA UW。"
        in window.daily.status_var.get()
    )

    window.daily.restart()
    assert window.daily.kpis.values == () and window.daily.status_label.cget("style") == "Card.TLabel"


def test_reimporting_a_price_date_says_how_many_prices_were_replaced(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)

    window.daily._done(daily_receipt(tmp_path, prices=PriceSummary(dt.date(2026, 10, 16), 3, 2, ())))

    assert "股價：取價日 2026-10-16，3 筆（取代了這一天原本的 2 筆）。" in window.daily.status_var.get()


# ---------------------------------------------------------------- 回補股價 CSV 與只匯交易明細（#112）


def test_trades_only_import_says_prices_were_left_alone(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    result = daily_receipt(tmp_path)

    window.daily._done(DailyResult(replace(result.receipt, prices=None), result.recalc))

    assert "股價：只匯交易明細，股價沒有變動。" in window.daily.status_var.get()
    assert [caption for caption, _, _ in window.daily.kpis.values] == [
        "正常（項）",
        "要注意（項）",
        "核對條件待確認（檔）",
    ]


def test_the_trades_only_box_is_passed_to_the_import(master, tmp_path: Path, monkeypatch) -> None:
    window = new_window(master, tmp_path)
    actions = []
    monkeypatch.setattr(window.daily.db, "settings", lambda: object())
    monkeypatch.setattr(window.daily, "run_in_background", lambda action, *a, **k: actions.append(action))
    calls = []
    monkeypatch.setattr(
        sp_workflow.tabs.daily, "run_daily_import", lambda settings, daily, paths, *, prices: calls.append(prices)
    )
    window.daily.daily_var.set(str(tmp_path / "每日更新.xlsx"))

    window.daily.trades_only_var.set(True)
    window.daily.start()
    actions[-1]()
    window.daily.trades_only_var.set(False)
    window.daily.start()
    actions[-1]()

    assert calls == [False, True]


def test_price_csv_import_shows_the_stored_price_date(master, tmp_path: Path, monkeypatch) -> None:
    window = new_window(master, tmp_path)
    actions = []
    monkeypatch.setattr(window.daily.db, "settings", lambda: object())
    monkeypatch.setattr(window.daily, "run_in_background", lambda action, *a, **k: actions.append(action))
    receipt = PriceCsvReceipt(
        run_id=9,
        close_date=dt.date(2026, 10, 16),
        prices=PriceSummary(dt.date(2026, 10, 17), 505, 504, ()),
        lines=(ImportLine("", PRICE_CONFLICT, "AAA UW：同一個代碼有 2 列、值不同，價格沒有存，算價格時不能用"),),
        error_book_path=tmp_path / "每日匯入錯誤.xlsx",
    )
    result = PriceCsvResult(receipt, Recalc(CalcReceipt(()), CONFIG.calendar))
    monkeypatch.setattr(sp_workflow.tabs.daily, "run_price_csv", lambda settings, source, paths: result)
    source = tmp_path / "MarketData_20261016.csv"
    window.daily.csv_var.set(str(source))

    window.daily.start_csv()
    window.daily._csv_done(actions[-1]())

    status = window.daily.status_var.get()
    assert "回補股價）：2026-10-16 收盤，存成取價日 2026-10-17" in status
    assert "股價：取價日 2026-10-17，505 筆（取代了這一天原本的 504 筆）。" in status
    assert "收盤價：讀不到交易所假日表，這次沒有存" in status
    assert window.daily.kpis.values == (
        ("要注意（項）", 1, True),
        ("股價（筆）", 505, False),
        ("核對條件待確認（檔）", 0, False),
    )
    assert [window.daily.table.item(i, "values")[1] for i in window.daily.table.get_children()] == [PRICE_CONFLICT]
    assert FolderMemory.under(tmp_path).load()["price_csv"] == str(source)
    assert str(window.daily.start_button.cget("state")) == "normal"


def test_price_csv_import_says_how_many_closes_were_stored(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    receipt = PriceCsvReceipt(
        run_id=9,
        close_date=dt.date(2026, 10, 16),
        prices=PriceSummary(dt.date(2026, 10, 17), 505, 0, ()),
        lines=(),
        error_book_path=tmp_path / "每日匯入錯誤.xlsx",
        closes=CloseSummary(3, dt.date(2026, 10, 16), dt.date(2026, 10, 16), 500, 500, 2, 1),
        closed=("7203 JT", "6758 JT"),
        close_conflicts=("AAA UW",),
    )

    window.daily._csv_done(PriceCsvResult(receipt, Recalc(CalcReceipt(()), CONFIG.calendar)))

    status = window.daily.status_var.get()
    assert (
        "收盤價：存了 500 筆（取代原本的 2 筆，其中 1 筆的值不同，舊值留在紀錄）；那一天休市、沒有存 2 個代碼；"
        "去掉 Equity 這類字尾後重複、值不同、沒有存：AAA UW。"
    ) in status
    assert ("收盤價（筆）", 500, False) in window.daily.kpis.values


def test_close_history_runs_on_this_page_and_lists_problems(master, tmp_path: Path, monkeypatch) -> None:
    window = new_window(master, tmp_path)
    actions = []
    source = tmp_path / "HP.xlsx"
    monkeypatch.setattr(sp_workflow.tabs.daily, "ask_excel", lambda *a: str(source))
    monkeypatch.setattr(window.daily.db, "settings", lambda: object())
    monkeypatch.setattr(window.daily, "run_in_background", lambda action, *a, **k: actions.append(action))
    day = dt.date(2026, 10, 15)
    receipt = HistoryReceipt(
        summary=CloseSummary(1, dt.date(2022, 7, 28), dt.date(2026, 10, 21), 507, 500000, 0, 0),
        first_date=dt.date(2022, 7, 28),
        last_date=dt.date(2026, 10, 21),
        codes=507,
        missing=12,
        closed=3,
        uncovered=0,
        problems=(HistoryProblem("AAA UW", "抓價未完成（#N/A Requesting Data...），請重抓再匯", day, day, 1),),
        error_book_path=tmp_path / "歷史收盤價錯誤.xlsx",
    )
    result = HistoryResult(receipt, Observation(error="讀不到交易所假日表，這次沒有比價"))
    monkeypatch.setattr(sp_workflow.tabs.daily, "import_close_history", lambda settings, path, paths: result)

    window._import_close_history()
    assert str(window.daily.start_button.cget("state")) == "disabled"
    window.daily._history_done(actions[-1]())

    assert window.notebook.select() == str(window.daily.frame)
    status = window.daily.status_var.get()
    assert "完成（歷史收盤價）：2022-07-28～2026-10-21，代碼 507 個，沒有價格 12 格（休市日不算）。" in status
    assert f"錯誤清單：{tmp_path / '歷史收盤價錯誤.xlsx'}" in status
    assert "比價：沒有跑，讀不到交易所假日表，這次沒有比價。匯入已經完成，修好後按「維護 → 重新比價全部」。" in status
    assert [window.daily.table.item(i, "values")[0] for i in window.daily.table.get_children()] == ["AAA UW"]
    assert window.daily.kpis.values == (
        ("收盤價（筆）", 500000, False),
        ("沒有價格（格）", 12, True),
        ("錯誤清單（項）", 1, True),
        ("比價沒有跑", 1, True),
    )
    assert FolderMemory.under(tmp_path).load()["close_history"] == str(source)
    assert str(window.daily.start_button.cget("state")) == "normal"


OBSERVED = ObservationReceipt(
    dt.date(2026, 10, 21),
    (
        Observed("147199990001", "已出場", "KO 2026-10-20", dt.date(2026, 10, 20), True),
        Observed("147199990002", "等股價", "UL_2 BBB UN：2026-10-21 沒有收盤價", dt.date(2026, 10, 20), False),
        Observed("147199990003", "在庫", "", dt.date(2026, 10, 21), True),
    ),
    (EventChange("147199990001", (), (("KO", "2026-10-20", None),)),),
    2,
)


def test_price_csv_import_lists_changed_events_and_unfinished_observation(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    receipt = PriceCsvReceipt(
        run_id=8,
        close_date=dt.date(2026, 10, 21),
        prices=PriceSummary(dt.date(2026, 10, 22), 2, 0, ()),
        lines=(),
        error_book_path=tmp_path / "錯誤.xlsx",
    )

    window.daily._csv_done(PriceCsvResult(receipt, Recalc(CalcReceipt(()), CONFIG.calendar), Observation(OBSERVED)))

    rows = [window.daily.table.item(i, "values") for i in window.daily.table.get_children()]
    assert [tuple(r) for r in rows] == [
        ("147199990001", "存續事件有變", "沒有 → KO 2026-10-20"),
        ("147199990002", "比價等股價", "UL_2 BBB UN：2026-10-21 沒有收盤價（已比到 2026-10-20）；未鎖住"),
    ]
    assert "比價：比到 2026-10-21，共 3 檔（在庫 1、已出場 1、等股價 1、待確認 0）；存續事件有變 1 檔。" in (
        window.daily.status_var.get()
    )
    assert ("存續事件有變（檔）", 1, False) in window.daily.kpis.values
    assert ("比價沒完成（檔）", 1, True) in window.daily.kpis.values


def test_reobserve_runs_on_this_page(master, tmp_path: Path, monkeypatch) -> None:
    window = new_window(master, tmp_path)
    actions = []
    monkeypatch.setattr(window.daily.db, "settings", lambda: object())
    monkeypatch.setattr(window.daily, "run_in_background", lambda action, *a, **k: actions.append((action, a[1])))
    monkeypatch.setattr(sp_workflow.tabs.daily, "observe_products", lambda settings, paths: OBSERVED)

    window._reobserve()
    action, done = actions.pop()
    done(action())

    assert window.notebook.select() == str(window.daily.frame)
    assert "完成（重新比價全部）。\n比價：比到 2026-10-21" in window.daily.status_var.get()
    assert len(window.daily.table.get_children()) == 2


def test_legacy_backfill_runs_on_this_page_and_lists_what_changed(master, tmp_path: Path, monkeypatch) -> None:
    window = new_window(master, tmp_path)
    actions = []
    source = tmp_path / "庫存總表.xlsx"
    monkeypatch.setattr(sp_workflow.tabs.daily, "ask_excel", lambda *a: str(source))
    monkeypatch.setattr(window.daily.db, "settings", lambda: object())
    monkeypatch.setattr(window.daily, "run_in_background", lambda action, *a, **k: actions.append(action))
    receipt = BackfillReceipt(
        (
            BackfillLine("147199990001", FILLED, "補了價格、比價日 1～12；寫了KO事件（2026-10-01）", (2,)),
            BackfillLine("147199990002", EXISTING, "價格與比價日都有了，沒有動", (3,)),
            BackfillLine("147199990003", SKIPPED, "資料庫沒有這檔", (4,)),
        ),
        has_status=True,
        events=1,
        error_book_path=tmp_path / "舊商品補資料錯誤.xlsx",
    )
    result = LegacyResult(receipt, Observation(error="讀不到交易所假日表，這次沒有比價"))
    monkeypatch.setattr(sp_workflow.tabs.daily, "import_legacy_products", lambda settings, path, paths: result)

    window._import_legacy()
    assert str(window.daily.start_button.cget("state")) == "disabled"
    window.daily._legacy_done(actions[-1]())

    assert window.notebook.select() == str(window.daily.frame)
    status = window.daily.status_var.get()
    assert "完成（舊商品的價格與比價日）：已補 1 檔、已存在 1 檔、不匯入 1 檔；寫了舊清單事件 1 筆。" in status
    assert f"錯誤清單：{tmp_path / '舊商品補資料錯誤.xlsx'}" in status
    rows = [window.daily.table.item(i, "values") for i in window.daily.table.get_children()]
    assert [r[:2] for r in rows] == [("147199990001", "舊商品已補"), ("147199990003", "舊商品不匯入")], "已存在的不列"
    assert window.daily.memory.load()["legacy_backfill"] == str(source)


def test_close_history_does_nothing_without_a_file(master, tmp_path: Path, monkeypatch) -> None:
    window = new_window(master, tmp_path)
    monkeypatch.setattr(sp_workflow.tabs.daily, "ask_excel", lambda *a: "")
    monkeypatch.setattr(window.daily, "run_in_background", lambda *a, **k: pytest.fail("沒選檔不匯入"))

    window.daily.import_history()


def test_price_csv_import_needs_a_file(master, tmp_path: Path, monkeypatch) -> None:
    window = new_window(master, tmp_path)
    errors = []
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: errors.append(a[1]))
    window.daily.csv_var.set("")

    window.daily.start_csv()

    assert errors == ["請先選擇股價 CSV。"]


# ---------------------------------------------------------------- 每日匯入在第 1 頁，帶入 起單資料（#90）


def pasted(tab) -> str:
    return tab.tdcc_text.get("1.0", tk.END).strip()


def test_daily_import_hands_fcn_tdccs_to_the_order_tab_without_switching(master, tmp_path: Path, monkeypatch) -> None:
    window = new_window(master, tmp_path)
    monkeypatch.setattr(window.orders, "run_in_background", lambda *a, **k: pytest.fail("不該自動讀取"))

    window.daily._done(daily_receipt(tmp_path, ("147000000002", "147000000001")))
    master.update()  # 帶入時改了 TDCC 欄位，處理完那次 <<Modified>> 提示條仍在

    assert pasted(window.orders) == "147000000002\n147000000001" and window.orders.count_var.get() == "已貼 2 筆"
    assert window.orders.hint.text.get() == "已帶入每日匯入的 FCN TDCC 2 筆，選好起單建檔後按「讀取」。"
    assert window.daily.status_var.get().endswith("已把 FCN TDCC 2 筆帶到「2 起單資料」。")
    assert window.notebook.select() == str(window.daily.frame), "不自動切頁"
    assert window.daily.next_button.winfo_manager() == "pack"

    window.daily.next_button.invoke()

    assert window.notebook.select() == str(window.orders.frame)


@pytest.mark.parametrize(
    ("receipt_args", "message"),
    [
        ((), "這份交易明細沒有 FCN，起單資料頁沒有帶入。"),
        (((), 2), "這份交易明細的 FCN 都已取消或 TDCC 不是 12 碼，起單資料頁沒有帶入。"),
    ],
)
def test_nothing_is_handed_over_without_fcn_tdccs(master, tmp_path: Path, receipt_args, message) -> None:
    window = new_window(master, tmp_path)
    window.orders.tdcc_text.insert("1.0", "147000000009")

    window.daily._done(daily_receipt(tmp_path, *receipt_args))

    assert window.daily.status_var.get().endswith(message)
    assert pasted(window.orders) == "147000000009" and not window.orders.hint.shown
    assert window.daily.next_button.winfo_manager() == ""


def test_a_rejected_daily_file_hands_nothing_over(master, tmp_path: Path, monkeypatch) -> None:
    window = new_window(master, tmp_path)
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: None)

    window.daily._done(DailyImportError("「交易明細」的欄名和來源表不同，整份不匯入"))

    assert pasted(window.orders) == "" and not window.orders.hint.shown
    assert window.daily.next_button.winfo_manager() == ""


@pytest.mark.parametrize("answer", [False, True])
def test_different_tdccs_on_the_order_tab_are_replaced_only_if_the_operator_agrees(
    master, tmp_path: Path, monkeypatch, answer
) -> None:
    window = new_window(master, tmp_path)
    window.orders.tdcc_text.insert("1.0", "147000000009")
    asked = []
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: asked.append(a[1]) or answer)

    window.daily._done(daily_receipt(tmp_path, ("147000000001",)))

    assert len(asked) == 1 and "已經有 1 筆 TDCC" in asked[0]
    assert pasted(window.orders) == ("147000000001" if answer else "147000000009")
    assert window.orders.hint.shown is answer
    assert window.daily.next_button.winfo_manager() == ("pack" if answer else ""), "沒帶入就沒有下一步"
    if not answer:
        assert window.daily.status_var.get().endswith("起單資料頁沒有帶入（正在處理，或保留原本的 TDCC）。")


def test_the_same_tdccs_are_handed_over_again_without_asking(master, tmp_path: Path, monkeypatch) -> None:
    window = new_window(master, tmp_path)
    window.daily._done(daily_receipt(tmp_path, ("147000000001",)))
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: pytest.fail("一樣的 TDCC 不用問"))

    window.daily._done(daily_receipt(tmp_path, ("147000000001",)))

    assert window.orders.hint.shown


def test_change_on_the_order_hint_clears_the_handed_over_tdccs(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    window.daily._done(daily_receipt(tmp_path, ("147000000001", "147000000002")))

    hint_change_button(window.orders.hint).invoke()

    assert pasted(window.orders) == "" and window.orders.count_var.get() == "已貼 0 筆"
    assert not window.orders.hint.shown


def test_editing_the_handed_over_tdccs_hides_the_hint(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    window.daily._done(daily_receipt(tmp_path, ("147000000001",)))
    master.update()
    assert window.orders.hint.shown, "帶入本身不算自己改"

    window.orders.tdcc_text.insert(tk.END, "\n147000000003")
    master.update()

    assert not window.orders.hint.shown and window.orders.count_var.get() == "已貼 2 筆"


@pytest.mark.parametrize("busy", ["reading", "importing"])
def test_a_busy_order_tab_does_not_take_the_tdccs(master, tmp_path: Path, monkeypatch, busy) -> None:
    """#93：正在讀取或匯入時不帶入，免得舊的結果蓋在新的 TDCC 底下。"""
    window = new_window(master, tmp_path)
    monkeypatch.setattr(window.orders, "run_in_background", lambda *a, **k: None)
    window.orders.sheet_var.set(str(tmp_path / "起單建檔_20261015.xlsm"))
    window.orders.tdcc_text.insert("1.0", "147000000009")
    if busy == "reading":
        window.orders.start()
    else:
        window.orders._done(read_result(order_row("147000000009")))
        monkeypatch.setattr(window.orders.db, "settings", lambda **k: object())
        window.orders.import_now()
    told = []
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: told.append(a[1]))

    window.daily._done(daily_receipt(tmp_path, ("147000000001",)))

    assert len(told) == 1 and "正在處理" in told[0]
    assert pasted(window.orders) == "147000000009" and not window.orders.hint.shown
    assert window.daily.next_button.winfo_manager() == ""


def test_handing_over_clears_the_last_order_results(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    window.orders._done(read_result(OrderRow("147000000009", ORDER_PENDING)))
    window.orders.tdcc_text.delete("1.0", tk.END)

    window.daily._done(daily_receipt(tmp_path, ("147000000001",)))

    orders = window.orders
    assert orders.table.get_children() == () and orders.kpis.values == ()
    assert orders.status_var.get() == orders.READY_TEXT and orders.status_label.cget("style") == "Card.TLabel"
    assert str(orders.import_button.cget("state")) == "disabled"


def test_the_same_tdccs_in_another_order_are_handed_over_without_asking(master, tmp_path: Path, monkeypatch) -> None:
    window = new_window(master, tmp_path)
    window.orders.tdcc_text.insert("1.0", "147000000002\n147000000001")
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: pytest.fail("只是順序不同，不用問"))

    window.daily._done(daily_receipt(tmp_path, ("147000000001", "147000000002")))

    assert pasted(window.orders) == "147000000001\n147000000002" and window.orders.hint.shown


def test_starting_another_daily_import_withdraws_the_next_step(master, tmp_path: Path, monkeypatch) -> None:
    """上一次成功、這次重新匯入：開始時先收起「下一步」，這次失敗就不會留著上一次的。"""
    window = new_window(master, tmp_path)
    window.daily._done(daily_receipt(tmp_path, ("147000000001",)))
    assert window.daily.next_button.winfo_manager() == "pack"
    monkeypatch.setattr(window.daily.db, "settings", lambda: object())
    monkeypatch.setattr(window.daily, "run_in_background", lambda *a, **k: None)
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: None)
    window.daily.daily_var.set(str(tmp_path / "每日更新.xlsx"))

    window.daily.start()
    window.daily._done(DailyImportError("每日更新缺少工作表「交易明細」"))

    assert window.daily.next_button.winfo_manager() == ""


# ---------------------------------------------------------------- 匯入後重算核對條件、放行差異（#88）


def test_daily_import_lists_check_terms_and_releases_a_difference(master, tmp_path: Path, monkeypatch) -> None:
    window = new_window(master, tmp_path)
    calcs = (
        NEEDS_RELEASE,
        CalcLine("029199990002", WAIT_PRICES, "還沒有取價日比 Strike date 2026-10-16 晚的股價檔", False),
        CalcLine("029199990003", DONE, "", False),  # 沒變、已完成的不列
    )

    window.daily._done(daily_receipt(tmp_path, calcs=calcs))

    rows = [tuple(window.daily.table.item(iid, "values")) for iid in window.daily.table.get_children()]
    assert rows == [
        ("029199990001", "核對條件：待確認", NEEDS_RELEASE.reason),
        ("029199990002", "核對條件：等股價", "還沒有取價日比 Strike date 2026-10-16 晚的股價檔"),
    ]
    assert window.daily.kpis.values[-1] == ("核對條件待確認（檔）", 1, True)
    assert "核對條件（還沒鎖住的 3 檔，這次有變 1 檔）：完成 1、等股價 1、等交易明細 0、待確認 1。" in (
        window.daily.status_var.get()
    )
    button = window.daily.releaser.button
    window.daily.table.selection_set("calc-029199990002")
    master.update()
    assert str(button.cget("state")) == "disabled", "沒有差異的不能放行"
    window.daily.table.selection_set("calc-029199990001")
    master.update()
    assert str(button.cget("state")) == "normal"

    released = CalcLine("029199990001", DONE, "已放行的差異：UF（…）", True, (Difference(*UF_DIFF.key, released=True),))
    asked = release_without_database(window.daily, monkeypatch, released)
    button.invoke()

    assert len(asked) == 1 and "UF不一致：起單資料 1.99、交易明細 0.0215（換算 2.15）" in asked[0]
    assert window.daily.table.item("calc-029199990001", "values")[1] == "核對條件：完成"
    assert window.daily.status_var.get().startswith("已放行 029199990001 的差異，重算後：完成")
    assert str(button.cget("state")) == "disabled"


def test_daily_import_says_when_check_terms_were_not_recalculated(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    result = daily_receipt(tmp_path)

    window.daily._done(DailyResult(result.receipt, Recalc(error="找不到交易所假日表設定檔")))

    assert "核對條件沒有重算（找不到交易所假日表設定檔）" in window.daily.status_var.get()
    assert [caption for caption, _, _ in window.daily.kpis.values] == ["正常（項）", "要注意（項）", "股價（筆）"]
