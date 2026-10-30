"""Lookback BEN（#374、ADR 0029）：回顧期初價格、回顧執行價、到期判定與寫入。

商品、價格與假日全部虛構；假日表用測試自己組的 `TradingCalendar`（美國、日本，2026～2027）。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal

import pytest

from db_synth import inventory, seed_products
from fcn_checker.trading_calendar import Market, TradingCalendar
from order_synth import CONFIG, maintenance_draft, open_terms
from sp_workflow.check_terms import recalculate
from sp_workflow.lifecycle import DELIVERY, MATURITY, AutoEvent
from sp_workflow.observation import (
    NO_KI,
    ON_BOOK,
    TO_CONFIRM,
    WAIT_PRICES,
    Terms,
    Underlying,
    missing_close,
    observe,
    observe_all,
)
from sp_workflow.product_terms import TermsError, backfill, problems, write
from sp_workflow.status import ProductSource
from sp_workflow.stock_splits import Split, SplitBook

D = Decimal
COVERED = (dt.date(2026, 1, 1), dt.date(2027, 12, 31))
JP_HOLIDAY = dt.date(2026, 11, 24)  # 虛構的日本休市日（週二）：美國照常開市，但不是共同交易日
CALENDAR = TradingCalendar(
    1,
    (
        Market("US", "美國", frozenset({"UN", "UW"}), *COVERED, frozenset()),
        Market("JP", "日本", frozenset({"JT"}), *COVERED, frozenset({JP_HOLIDAY})),
    ),
)
TRADE = dt.date(2026, 11, 2)  # 交易日＝Strike date（週一）
END = dt.date(2026, 11, 25)  # 回顧結束日（週三）
MIDDLE = dt.date(2026, 11, 10)
FINAL = dt.date(2026, 12, 15)
A = Underlying(1, "AAA UW", D(100), D(80), None, D(100))
B = Underlying(2, "7203 JT", D(100), D(80), None, D(100))


def ben(**changes) -> Terms:
    """回顧結束日 END、執行價 80% 的兩檔 BEN：Non-Call = 天期，比價日只有最終比價日。"""
    values = {
        "strike_date": TRADE,
        "final_date": FINAL,
        "no_autocall": True,
        "final_date_callable": False,
        "trade_date": TRADE,
        "lookback_end": END,
        "strike_pct": D(80),
        "underlyings": (A, B),
    }
    return Terms("147199990001", "Period End", NO_KI, autocall=(FINAL,), **{**values, **changes})


def days(code: str, last: dt.date = FINAL) -> list[dt.date]:
    market = CALENDAR.market_of(code)
    found, day = [], TRADE
    while day <= last:
        if day.weekday() < 5 and day not in market.holidays:
            found.append(day)
        day += dt.timedelta(days=1)
    return found


def closes(a: dict[dt.date, str] | None = None, b: dict[dt.date, str] | None = None, default: str = "100") -> dict:
    """兩檔在各自市場的每個交易日都有價格；預設 100（等於進場價）。"""
    return {
        A.code: {day: D((a or {}).get(day, default)) for day in days(A.code)},
        B.code: {day: D((b or {}).get(day, default)) for day in days(B.code)},
    }


def run(t: Terms, prices: dict, through: dt.date = FINAL, splits: SplitBook | None = None):
    return observe(t, prices, CALENDAR, through, splits)


# ---------------------------------------------------------------- 計算（不連資料庫）


def test_during_the_lookback_period_the_product_waits() -> None:
    outcome = run(ben(), closes(), through=END - dt.timedelta(days=1))

    assert (outcome.status, outcome.reason, outcome.lookback) == (ON_BOOK, f"回顧期間（至 {END}）", ())


def test_a_low_below_the_initial_price_resets_the_strike() -> None:
    outcome = run(ben(), closes({MIDDLE: "70"}), through=END)

    ul1, ul2 = outcome.lookback
    assert (ul1.lookback, ul1.strike) == (D("70.0000"), D("56.0000"))
    assert (ul2.lookback, ul2.strike) == (D(100), D(80)), "期間內都比進場價高：維持進場價"
    assert outcome.status == ON_BOOK
    assert outcome.reason == f"還沒到最終比價日（{FINAL}）；回顧期初價格 UL_1 AAA UW 70、UL_2 7203 JT 100"


def test_only_common_trading_days_count() -> None:
    """日本休市那天美國的收盤價不算（共同預定交易日）；回顧結束日當天算。"""
    outcome = run(ben(), closes({JP_HOLIDAY: "65", END: "95"}), through=END)

    assert [u.lookback for u in outcome.lookback] == [D("95.0000"), D(100)]


def test_a_missing_close_in_the_period_waits_for_prices() -> None:
    prices = closes()
    del prices[B.code][MIDDLE]

    outcome = run(ben(), prices, through=FINAL)

    assert (outcome.status, outcome.reason) == (WAIT_PRICES, f"UL_2 7203 JT：{MIDDLE} 沒有收盤價（回顧期間）")
    assert missing_close(outcome.reason) == ("7203 JT", MIDDLE)
    assert outcome.lookback == ()


def test_a_split_in_the_period_is_converted_back() -> None:
    """期間內 1 拆 2：拆股後的收盤價乘 2 換回交易時的基準再比（ADR 0024）。"""
    split_day = dt.date(2026, 11, 16)
    after = {day: "45" for day in days(A.code) if day >= split_day}  # 換回基準是 90

    outcome = run(ben(), closes(after), through=END, splits=SplitBook([Split(A.code, split_day, 1, 2)]))

    assert outcome.lookback[0].lookback == D("90.0000")


@pytest.mark.parametrize(
    ("a_final", "event", "without"),
    [
        ("60", AutoEvent(MATURITY, FINAL), AutoEvent(DELIVERY, FINAL, A.code)),
        ("55", AutoEvent(DELIVERY, FINAL, A.code), AutoEvent(DELIVERY, FINAL, A.code)),
    ],
)
def test_maturity_uses_the_lookback_price_and_strike(a_final, event, without) -> None:
    """AAA 回顧期初價格 70、回顧執行價 56，兩種選法的最差都是 AAA（7203 是 95/100）：最終 60 ≥ 56 到期還本；
    沒有 lookback 時 60 < 執行價 80 會接股。最終 55 < 56 → 接股。"""
    before = FINAL - dt.timedelta(days=1)
    prices = closes({MIDDLE: "70", before: "80", FINAL: a_final}, {FINAL: "95"})

    outcome = run(ben(), prices)

    assert outcome.events == (event,)
    assert [u.strike for u in outcome.lookback] == [D(56), D(80)]
    assert run(ben(lookback_end=None), prices).events == (without,)


def test_a_different_worst_by_the_initial_price_is_to_confirm() -> None:
    """MS 說明書的最差標的用期初價格定義（#383 未決）：AAA 回顧期初 70、最終 66 → 回顧表現 94% 不是最差（7203 是 93%）；
    用進場價 100 選，AAA 66% 才是最差。兩種選法不同時不自動判定。"""
    prices = closes({MIDDLE: "70", FINAL: "66"}, {FINAL: "93"})

    outcome = run(ben(), prices)

    assert outcome.status == TO_CONFIRM
    assert outcome.reason == (
        f"最終比價日 {FINAL}：用回顧期初價格選的最差標的 7203 JT 和用進場價選的 AAA UW 不同，要人工判定接股或到期還本"
    )
    assert outcome.events == ()
    assert [u.lookback for u in outcome.lookback] == [D("70.0000"), D(100)], "回顧期初價格照樣寫入"


def test_a_backfilled_product_with_a_different_strike_is_not_overwritten() -> None:
    """歷史回補的已鎖住商品：算出的回顧執行價和資料庫的不同時待確認、寫出兩個值，不覆寫。"""
    prices = closes({MIDDLE: "70"})

    outcome = run(ben(keep_strike=True), prices, through=END)

    assert outcome.status == TO_CONFIRM
    assert outcome.reason == (
        "回顧執行價和資料庫的執行價不同，沒有覆寫：UL_1 AAA UW 算出 56、資料庫 80"
        "（資料庫的執行價才對時，清掉回顧結束日就照它比價）"
    )
    assert outcome.lookback == ()
    same = replace(ben(keep_strike=True), underlyings=(Underlying(1, A.code, D(100), D(56), None, D(100)), B))
    assert run(same, prices, through=END).status == ON_BOOK


def test_without_a_new_low_the_stored_strike_stays() -> None:
    """期間內沒有比進場價低：回顧期初價格 = 進場價，執行價照資料庫的（不因重算進位而改動）。"""
    stored = Underlying(1, A.code, D(100), D("79.9999"), None, D(100))

    outcome = run(ben(underlyings=(stored, B)), closes(), through=END)

    assert [(u.lookback, u.strike) for u in outcome.lookback] == [(D(100), D("79.9999")), (D(100), D(80))]


def test_a_cleared_lookback_end_with_lookback_prices_is_to_confirm() -> None:
    computed = replace(A, strike=D(56), lookback=D(70))

    outcome = run(ben(lookback_end=None, underlyings=(computed, B)), closes())

    assert outcome.status == TO_CONFIRM
    assert outcome.reason.startswith("沒有回顧結束日，但資料庫有回顧期初價格")
    assert outcome.events == ()


def test_a_lookback_end_outside_the_tenor_is_to_confirm() -> None:
    outcome = run(ben(lookback_end=FINAL), closes())

    assert (outcome.status, outcome.reason) == (
        TO_CONFIRM,
        f"回顧結束日 {FINAL} 要在 Trade date {TRADE} 之後、最終比價日 {FINAL} 之前",
    )


# ---------------------------------------------------------------- 人工維護的檢查


BEN = {
    "Product": "BEN",
    "Guaranteed Periods (m)": 6,
    "KO Type": "Period End",
    "Barrier Type": "NONE",
    "KI Barrier (%)": None,
    "Participation (%)": D(100),
}


def test_the_lookback_end_date_is_checked() -> None:
    draft = maintenance_draft(**BEN)
    trade, final = draft.values["Trade date"], draft.values["Final valuation date"]
    inside = trade + dt.timedelta(days=7)

    assert problems(maintenance_draft(**BEN, **{"Lookback end date": inside}), CONFIG) == {}
    assert problems(maintenance_draft(**BEN, **{"Lookback end date": trade}), CONFIG) == {
        "Lookback end date": "要晚於 Trade date"
    }
    assert problems(maintenance_draft(**BEN, **{"Lookback end date": final}), CONFIG) == {
        "Lookback end date": "要早於 Final valuation date"
    }
    assert problems(maintenance_draft(**{"Lookback end date": inside}), CONFIG) == {
        "Lookback end date": "FCN 沒有回顧期間，要空白"
    }


# ---------------------------------------------------------------- 資料庫

T1 = "147199990001"


def seed(pg, *, locked: bool = True, source: str = ProductSource.MANUAL, lookback: dt.date | None = END) -> None:
    """一檔美國兩檔標的的 lookback BEN：核對條件完成、比價日只有最終比價日、執行價 80%。"""
    seed_products(pg, [inventory(T1, [("台北", 10000)], **{"KI(Freq)": "-", "KO(memo)": "N"})])
    pg.execute(
        "UPDATE workflow.product SET product = 'BEN', trade_date = %s, strike_date = %s, final_valuation_date = %s,"
        " non_call_months = tenor_months, ko_type = 'Period End', ko_freq = 'P', strike_pct = 80,"
        " participation_pct = 100, lookback_end_date = %s, locked_at = %s, source = %s WHERE tdcc = %s",
        (TRADE, TRADE, FINAL, lookback, dt.datetime(2026, 11, 3, tzinfo=dt.UTC) if locked else None, source, T1),
    )
    pg.execute("INSERT INTO workflow.check_terms (tdcc, status) VALUES (%s, '完成')", (T1,))
    for seq, code in ((1, "AAA UW"), (2, "BBB UN")):
        pg.execute(
            "INSERT INTO workflow.underlying_price (tdcc, seq, code, initial_price, strike_price, ki_price, ko_price)"
            " VALUES (%s, %s, %s, 100, 80, NULL, 100)",
            (T1, seq, code),
        )
    pg.execute("INSERT INTO workflow.autocall_date (tdcc, slot, value) VALUES (%s, 1, %s)", (T1, FINAL))


def write_closes(pg, last: dt.date, special: dict[dt.date, str]) -> None:
    (import_id,) = pg.execute(
        "INSERT INTO workflow.close_price_import (source, file_name, file_sha256, imported_by, summary)"
        " VALUES ('歷史收盤價', 'HP.xlsx', 'x', 'tester', '{}') RETURNING import_id"
    ).fetchone()
    for code in ("AAA UW", "BBB UN"):
        for day in days(code, last):
            close = special.get(day, "100") if code == "AAA UW" else "100"
            pg.execute(
                "INSERT INTO workflow.close_price (close_date, code, close, import_id) VALUES (%s, %s, %s, %s)",
                (day, code, close, import_id),
            )


def prices(pg) -> list[tuple]:
    return pg.execute(
        "SELECT seq, strike_price, lookback_price FROM workflow.underlying_price WHERE tdcc = %s ORDER BY seq", (T1,)
    ).fetchall()


def logs(pg) -> list[tuple]:
    return pg.execute(
        "SELECT item, old_value, new_value, result_file FROM workflow.backfill_log WHERE tdcc = %s ORDER BY log_id",
        (T1,),
    ).fetchall()


def status(pg) -> tuple:
    return pg.execute("SELECT status, reason FROM workflow.pricing_status WHERE tdcc = %s", (T1,)).fetchone()


@pytest.mark.db
def test_observe_all_writes_the_lookback_prices_once_and_logs_them(pg) -> None:
    seed(pg)
    write_closes(pg, END, {MIDDLE: "70"})

    observe_all(pg, CALENDAR, user="tester")

    assert prices(pg) == [(1, D("56.0000"), D("70.0000")), (2, D(80), D(100))]
    note = f"比價：回顧期間至 {END}"
    assert logs(pg) == [
        ("UL_1_回顧期初價格", None, "70", note),
        ("UL_1_執行價", "80", "56", note),
        ("UL_2_回顧期初價格", None, "100", note),
    ]
    observe_all(pg, CALENDAR, user="tester")
    assert len(logs(pg)) == 3, "再比一次沒有改變，不再記"


@pytest.mark.db
def test_the_lookback_period_shows_on_the_progress_status(pg) -> None:
    seed(pg)
    write_closes(pg, MIDDLE, {})

    observe_all(pg, CALENDAR, user="tester")

    assert status(pg) == (ON_BOOK, f"回顧期間（至 {END}）")
    assert prices(pg) == [(1, D(80), None), (2, D(80), None)]


@pytest.mark.db
def test_a_backfilled_product_with_a_different_strike_waits_for_confirmation(pg) -> None:
    seed(pg, source=ProductSource.BACKFILL)
    write_closes(pg, END, {MIDDLE: "70"})

    observe_all(pg, CALENDAR, user="tester")

    assert status(pg)[0] == TO_CONFIRM
    assert "UL_1 AAA UW 算出 56、資料庫 80" in status(pg)[1]
    assert prices(pg) == [(1, D(80), None), (2, D(80), None)]
    assert logs(pg) == []


@pytest.mark.db
def test_a_backfilled_product_gets_its_lookback_end_later_and_is_computed(pg) -> None:
    """舊總表回補的 lookback 商品：作業人員之後補回顧結束日，算出的執行價和資料庫相同時照樣寫入回顧期初價格。"""
    seed(pg, source=ProductSource.BACKFILL, lookback=None)
    pg.execute("UPDATE workflow.underlying_price SET strike_price = 56 WHERE tdcc = %s AND seq = 1", (T1,))
    write_closes(pg, END, {MIDDLE: "70"})
    observe_all(pg, CALENDAR, user="tester")
    assert prices(pg) == [(1, D(56), None), (2, D(80), None)]

    pg.execute("UPDATE workflow.product SET lookback_end_date = %s WHERE tdcc = %s", (END, T1))
    observe_all(pg, CALENDAR, user="tester")

    assert prices(pg) == [(1, D(56), D("70.0000")), (2, D(80), D(100))]
    assert [item for item, *_ in logs(pg)] == ["UL_1_回顧期初價格", "UL_2_回顧期初價格"]


@pytest.mark.db
def test_recalculating_an_unlocked_product_keeps_the_lookback_strike(pg) -> None:
    seed(pg, locked=False)
    write_closes(pg, END, {MIDDLE: "70"})
    observe_all(pg, CALENDAR, user="tester")

    recalculate(pg, CONFIG, tdccs=[T1])

    assert prices(pg)[0][1:] == (D("56.0000"), D("70.0000"))


@pytest.mark.db
def test_the_lookback_end_cannot_change_after_it_was_used(pg) -> None:
    seed(pg, locked=False)
    write_closes(pg, END, {MIDDLE: "70"})
    observe_all(pg, CALENDAR, user="tester")
    draft = open_terms(pg, T1)
    draft.values["Lookback end date"] = END - dt.timedelta(days=1)

    with pytest.raises(TermsError, match="已經算出回顧期初價格、重算了執行價，不能再改"):
        write(pg, draft, CONFIG, user="tester")


@pytest.mark.db
def test_the_reference_sheet_keeps_the_term_sheet_strike_and_the_screen_shows_the_lookback(pg, tmp_path) -> None:
    """核對比的是 TS（期初價格與期初執行價）；回顧期初價格只在人工維護畫面顯示。"""
    import openpyxl

    from fcn_checker.check_config import DEFAULTS
    from fcn_checker.config import load_issuer_prefixes, load_reference_format
    from sp_workflow.check_source import export_reference
    from sp_workflow.product_terms import read
    from sp_workflow.window_actions import lookback_text

    seed(pg)
    write_closes(pg, END, {MIDDLE: "70"})
    observe_all(pg, CALENDAR, user="tester")
    path = tmp_path / "參考條件.xlsx"
    fmt = load_reference_format(DEFAULTS.reference_format)

    export_reference(pg, [T1], fmt, path, prefixes=load_issuer_prefixes(DEFAULTS.issuer_prefixes))

    rows = list(openpyxl.load_workbook(path).active.iter_rows(values_only=True))
    row = dict(zip(rows[0], rows[1], strict=True))
    assert (D(str(row["UL_1_進場價"])), D(str(row["UL_1_執行價"]))) == (D(100), D(80))
    assert row["回顧結束日"].date() == END  # #376：說明書的回顧結束日和它比
    assert lookback_text(read(pg, T1)) == "UL_1 AAA UW 70、UL_2 BBB UN 100"
    assert lookback_text(None) == ""


@pytest.mark.db
def test_only_ben_can_have_a_lookback_end_in_the_database(pg) -> None:
    import psycopg

    seed(pg)
    with pytest.raises(psycopg.errors.CheckViolation):
        pg.execute("UPDATE workflow.product SET product = 'FCN' WHERE tdcc = %s", (T1,))


def lookback_end(pg) -> dt.date | None:
    return pg.execute("SELECT lookback_end_date FROM workflow.product WHERE tdcc = %s", (T1,)).fetchone()[0]


@pytest.mark.db
def test_check_write_back_fills_a_blank_lookback_end_and_logs_it(pg) -> None:
    """#376：說明書的回顧結束日回填空白的資料庫值，原值記在 backfill_log。"""
    seed(pg, locked=False, lookback=None)
    with pg.transaction():
        changed = backfill(pg, T1, "lookback_end_date", END, item="回顧結束日", result_file="r.xlsx", user="tester")
    assert changed and lookback_end(pg) == END
    assert logs(pg) == [("回顧結束日", None, str(END), "r.xlsx")]


@pytest.mark.db
def test_check_write_back_does_not_move_a_lookback_end_already_used(pg) -> None:
    """#376：已經算出回顧期初價格的商品，回填不改回顧結束日（同人工維護，#374）。"""
    seed(pg, locked=False)
    write_closes(pg, END, {MIDDLE: "70"})
    observe_all(pg, CALENDAR, user="tester")
    before = logs(pg)
    with pg.transaction():
        changed = backfill(pg, T1, "lookback_end_date", MIDDLE, item="回顧結束日", result_file="r.xlsx", user="tester")
    assert not changed and lookback_end(pg) == END
    assert logs(pg) == before
