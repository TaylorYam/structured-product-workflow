"""比價（`sp_workflow.observation`，#155、ADR 0020）：KO、KI、到期判定、缺價格停住與比價狀態。

商品、價格與假日全部虛構；假日表用測試自己組的 `TradingCalendar`（美國、日本，2026～2027）。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterable
from dataclasses import replace
from decimal import Decimal

import pytest

from db_synth import inventory, seed_products
from fcn_checker.trading_calendar import Market, TradingCalendar
from sp_workflow.lifecycle import (
    DELIVERY,
    KO,
    MATURITY,
    AutoEvent,
    current_events,
    record_redemption,
    replace_auto_events,
)
from sp_workflow.observation import (
    AKI,
    EKI,
    EXITED,
    MKI,
    MKI_KI,
    NO_KI,
    ON_BOOK,
    TO_CONFIRM,
    WAIT_PRICES,
    Outcome,
    Terms,
    Underlying,
    observe,
    observe_all,
)
from sp_workflow.stock_splits import Split, SplitBook, add_split, affected_exits, delete_split

COVERED = (dt.date(2026, 1, 1), dt.date(2027, 12, 31))
US_HOLIDAY = dt.date(2026, 11, 26)  # 虛構的休市日（週四）
JP_HOLIDAY = dt.date(2026, 11, 24)  # 虛構的休市日（週二）
CALENDAR = TradingCalendar(
    1,
    (
        Market("US", "美國", frozenset({"UN", "UW"}), *COVERED, frozenset({US_HOLIDAY})),
        Market("JP", "日本", frozenset({"JT"}), *COVERED, frozenset({JP_HOLIDAY})),
    ),
)
STRIKE = dt.date(2026, 11, 2)  # 週一
START = dt.date(2026, 11, 16)  # 第一個比價日（週一）
FINAL = dt.date(2026, 12, 15)  # 最終比價日（週二）
NON_CALL = f"Non-call period（第一個比價日 {START}）"
NO_KI_TEXT = "-"
D = Decimal


def ul(seq: int, code: str, initial: str = "100") -> Underlying:
    """進場價 100：執行價 80、下限價 65、KO 價 100（同比例）。"""
    base = D(initial)
    return Underlying(seq, code, base, base * D("0.8"), base * D("0.65"), base)


A, B = ul(1, "AAA UW"), ul(2, "7203 JT")


def terms(
    ko_type: str = "Daily",
    barrier: str = NO_KI,
    autocall: tuple[dt.date, ...] = (START, FINAL),
    underlyings: tuple[Underlying, ...] = (A, B),
    **changes,
) -> Terms:
    values = {"strike_date": STRIKE, "final_date": FINAL, "no_autocall": False}
    return Terms("147199990001", ko_type, barrier, autocall=autocall, underlyings=underlyings, **{**values, **changes})


def trading_days(code: str, first: dt.date = STRIKE, last: dt.date = FINAL) -> Iterable[dt.date]:
    market = CALENDAR.market_of(code)
    day = first
    while day <= last:
        if day.weekday() < 5 and day not in market.holidays:
            yield day
        day += dt.timedelta(days=1)


def closes(
    a: Callable[[dt.date], str] = lambda day: "90", b: Callable[[dt.date], str] = lambda day: "90"
) -> dict[str, dict[dt.date, Decimal]]:
    """每個標的在它市場的每個交易日都有價格；預設 90（低於 KO 價、高於執行價）。"""
    return {
        A.code: {day: D(a(day)) for day in trading_days(A.code)},
        B.code: {day: D(b(day)) for day in trading_days(B.code)},
    }


def on(days: dict[dt.date, str], default: str = "90") -> Callable[[dt.date], str]:
    return lambda day: days.get(day, default)


def run(t: Terms, prices: dict, through: dt.date = FINAL) -> Outcome:
    return observe(t, prices, CALENDAR, through)


# ---------------------------------------------------------------- KO


def test_non_memory_ko_needs_every_underlying_on_the_same_day() -> None:
    day1, day2 = dt.date(2026, 11, 17), dt.date(2026, 11, 18)
    prices = closes(on({day1: "101"}), on({day2: "100"}))

    assert run(terms("Daily"), prices).events == (AutoEvent(MATURITY, FINAL),), "不同天達標不算"
    both = closes(on({day1: "101", day2: "101"}), on({day2: "100"}))
    assert run(terms("Daily"), both) == Outcome(EXITED, f"KO {day2}", day2, (AutoEvent(KO, day2),), NO_KI_TEXT)


def test_memory_ko_remembers_each_underlying() -> None:
    day1, day2 = dt.date(2026, 11, 17), dt.date(2026, 11, 18)
    prices = closes(on({day1: "101"}), on({day2: "100"}))

    assert run(terms("Daily Memory"), prices).events == (AutoEvent(KO, day2),)


def test_daily_ko_starts_on_the_first_autocall_date() -> None:
    before = dt.date(2026, 11, 13)  # 第一個比價日之前（保證期）
    prices = closes(on({before: "120"}), on({before: "120"}))

    assert run(terms("Daily"), prices).events == (AutoEvent(MATURITY, FINAL),)


def test_daily_ko_only_on_days_every_market_is_open() -> None:
    prices = closes(on({JP_HOLIDAY: "120"}), on({JP_HOLIDAY - dt.timedelta(days=1): "120"}))

    assert run(terms("Daily Memory"), prices).events == (AutoEvent(MATURITY, FINAL),), "日本休市那天美股達標不算"


def test_period_end_ko_only_on_autocall_dates() -> None:
    dates = (START, dt.date(2026, 11, 30), FINAL)
    between = dt.date(2026, 11, 20)
    prices = closes(on({between: "120", dates[1]: "100"}), on({between: "120", dates[1]: "100"}))

    assert run(terms("Period End", autocall=dates), prices).events == (AutoEvent(KO, dates[1]),)
    memory = closes(on({START: "120"}), on({dates[1]: "120"}))
    assert run(terms("Period End Memory", autocall=dates), memory).events == (AutoEvent(KO, dates[1]),)
    assert run(terms("Period End", autocall=dates), memory).events == (AutoEvent(MATURITY, FINAL),)


def test_no_autocall_products_never_knock_out() -> None:
    prices = closes(lambda day: "120", lambda day: "120")

    assert run(terms(autocall=(FINAL,), no_autocall=True), prices).events == (AutoEvent(MATURITY, FINAL),)


# ---------------------------------------------------------------- KI 與到期


@pytest.mark.parametrize(
    ("barrier", "a_final", "a_path", "event", "ki"),
    [
        (NO_KI, "79", {}, AutoEvent(DELIVERY, FINAL, "AAA UW"), NO_KI_TEXT),  # 沒有 KI：低於執行價就接股
        (NO_KI, "80", {}, AutoEvent(MATURITY, FINAL), NO_KI_TEXT),  # 等於執行價不接股
        (EKI, "64", {}, AutoEvent(DELIVERY, FINAL, "AAA UW"), f"EKI hit {FINAL} UL_1 AAA UW"),  # 最終收盤價 < 下限價
        (EKI, "70", {dt.date(2026, 11, 20): "55"}, AutoEvent(MATURITY, FINAL), "EKI"),  # 期間跌破不算
        (
            AKI,
            "70",
            {dt.date(2026, 11, 3): "60"},
            AutoEvent(DELIVERY, FINAL, "AAA UW"),
            "AKI hit 2026-11-03 UL_1 AAA UW",
        ),  # Strike date 隔天就觀察
        (AKI, "70", {STRIKE: "60"}, AutoEvent(MATURITY, FINAL), "AKI"),  # Strike date 當天不算
        (AKI, "70", {}, AutoEvent(MATURITY, FINAL), "AKI"),  # 沒觸及
    ],
)
def test_maturity(barrier: str, a_final: str, a_path: dict, event: AutoEvent, ki: str) -> None:
    prices = closes(on({FINAL: a_final, **a_path}), on({FINAL: "85"}))

    assert run(terms("Daily", barrier), prices) == Outcome(EXITED, _text(event), FINAL, (event,), ki)


def test_delivery_names_the_worst_performer() -> None:
    worst = ul(2, "7203 JT", "2000")
    prices = closes(on({FINAL: "60"}))
    prices[worst.code] = {day: D("1100") for day in trading_days(worst.code)}  # 55%

    outcome = run(terms("Daily", EKI, underlyings=(A, worst)), prices)

    assert outcome.events == (AutoEvent(DELIVERY, FINAL, "7203 JT"),)


def test_aki_is_watched_on_each_market_own_trading_days() -> None:
    prices = closes(on({FINAL: "85"}), on({FINAL: "140"}))
    prices[A.code][US_HOLIDAY] = D("10")  # 美國休市那天的價格不會被讀（收盤價表本來就不會有）
    prices[B.code][US_HOLIDAY] = D("60")  # 日本那天開市：觸及

    assert run(terms("Daily", AKI), prices).events == (AutoEvent(MATURITY, FINAL),), (
        "觸及的是 B，最差表現的 A 沒跌破執行價"
    )
    prices[A.code][FINAL] = D("70")
    prices[B.code][FINAL] = D("60")
    assert run(terms("Daily", AKI), prices).events == (AutoEvent(DELIVERY, FINAL, "7203 JT"),)


def test_ko_on_the_final_date_wins_over_maturity() -> None:
    prices = closes(on({FINAL: "100"}), on({FINAL: "100"}))

    assert run(terms("Daily", EKI), prices).events == (AutoEvent(KO, FINAL),)


def test_mki_is_not_watched_but_ko_is() -> None:
    """#196：每期觀察 KI 不比，比價狀態照 KO 算（在庫），KI 欄提醒；最終比價日沒有 KO 才待確認。"""
    assert run(terms("Daily", MKI), closes()) == Outcome(
        TO_CONFIRM, f"每期觀察 KI，最終比價日 {FINAL} 沒有 KO，要人工判定接股或到期還本", FINAL, ki=MKI_KI
    )
    through = dt.date(2026, 11, 20)
    assert run(terms("Daily", MKI), closes(), through) == Outcome(ON_BOOK, "", through, ki=MKI_KI)
    assert MKI_KI == "MKI (manual)"
    day = dt.date(2026, 11, 18)
    assert run(terms("Daily", MKI), closes(on({day: "100"}), on({day: "100"}))).events == (AutoEvent(KO, day),)


# ---------------------------------------------------------------- 缺價格、待確認、比到哪裡


def test_a_missing_price_stops_on_that_day() -> None:
    gap = dt.date(2026, 11, 19)
    prices = closes()
    del prices[B.code][gap]
    later = dt.date(2026, 11, 20)
    prices[A.code][later] = prices[B.code][later] = D("120")  # 缺價格之後的 KO 不看

    assert run(terms(), prices) == Outcome(
        WAIT_PRICES, f"UL_2 7203 JT：{gap} 沒有收盤價", gap - dt.timedelta(days=1), ki=NO_KI_TEXT
    )


def test_holidays_are_skipped_without_waiting_for_prices() -> None:
    prices = closes()
    assert US_HOLIDAY not in prices[A.code] and JP_HOLIDAY not in prices[B.code]

    assert run(terms("Daily", AKI), prices).status == EXITED


def test_aki_needs_prices_between_strike_and_the_first_autocall_date() -> None:
    gap = dt.date(2026, 11, 5)
    prices = closes()
    del prices[A.code][gap]

    assert run(terms("Daily", AKI), prices) == Outcome(
        WAIT_PRICES, f"UL_1 AAA UW：{gap} 沒有收盤價（每日觀察 KI）", ki="AKI"
    ), "#196：Non-call 期間缺的是 KI 要的價格，已比到照樣只講 KO"
    assert run(terms("Daily", NO_KI), prices).status == EXITED, "沒有每日 KI 時保證期內的價格用不到"


def test_through_the_latest_close_stays_on_book() -> None:
    through = dt.date(2026, 11, 20)

    assert run(terms(), closes(), through) == Outcome(ON_BOOK, "", through, ki=NO_KI_TEXT)
    assert run(terms(), closes(), dt.date(2026, 11, 10)) == Outcome(ON_BOOK, NON_CALL, ki=NO_KI_TEXT)
    assert run(terms(), closes(), None) == Outcome(ON_BOOK, "收盤價表還沒有資料", ki=NO_KI_TEXT)


def test_the_non_call_period_is_the_reason_until_the_first_autocall_date() -> None:
    """#194：第一個比價日之前是 Non-call 期間；每日觀察 KI 已開始比的也寫。#196：已比到只講 KO，這段期間空白。"""
    through = dt.date(2026, 11, 10)

    assert run(terms("Daily", EKI), closes(), through) == Outcome(ON_BOOK, NON_CALL, ki="EKI"), "還沒開始比"
    assert run(terms("Period End", NO_KI), closes(), through) == Outcome(ON_BOOK, NON_CALL, ki=NO_KI_TEXT)
    assert run(terms("Daily", AKI), closes(), through) == Outcome(ON_BOOK, NON_CALL, ki="AKI"), "KI 照比、不顯示"
    assert run(terms("Daily", AKI), closes(), START) == Outcome(ON_BOOK, "", START, ki="AKI"), "到了第一個比價日"
    assert run(terms("Daily", AKI), closes(), STRIKE) == Outcome(ON_BOOK, NON_CALL, ki="AKI")
    no_call = terms(autocall=(FINAL,), no_autocall=True)
    assert run(no_call, closes(), through) == Outcome(ON_BOOK, f"還沒到最終比價日（{FINAL}）", ki=NO_KI_TEXT)
    assert run(no_call, closes(), dt.date(2026, 12, 1)) == Outcome(
        ON_BOOK, f"還沒到最終比價日（{FINAL}）", ki=NO_KI_TEXT
    ), "不觀察 KO：到最終比價日之前已比到都空白"
    assert run(terms(barrier=MKI), closes(), through) == Outcome(ON_BOOK, NON_CALL, ki=MKI_KI)


def test_aki_touch_shows_the_first_day_and_underlying() -> None:
    """#196：KI 欄寫第一次觸及的日期與標的；觸及不影響比價狀態與已比到。"""
    first, later = dt.date(2026, 11, 5), dt.date(2026, 11, 18)
    prices = closes(on({later: "60"}), on({first: "60", later: "60"}))

    assert run(terms("Daily", AKI), prices, dt.date(2026, 11, 10)) == Outcome(
        ON_BOOK, NON_CALL, ki=f"AKI hit {first} UL_2 7203 JT"
    )
    assert run(terms("Daily", AKI), prices, dt.date(2026, 11, 20)) == Outcome(
        ON_BOOK, "", dt.date(2026, 11, 20), ki=f"AKI hit {first} UL_2 7203 JT"
    )


@pytest.mark.parametrize(
    ("t", "prices", "reason"),
    [
        (terms(underlyings=(A, ul(2, "BMW GY"))), None, "UL_2 BMW GY：交易所不在假日表"),
        (terms(underlyings=(A, ul(2, "BBB UN"))), None, "UL_2 BBB UN：收盤價表沒有這個代碼"),
        (terms(underlyings=(A, Underlying(2, "7203 JT", D(100), D(80), None, None))), None, "UL_2 7203 JT 缺KO 價"),
        (
            terms(barrier=EKI, underlyings=(A, Underlying(2, "7203 JT", D(100), D(80), None, D(100)))),
            None,
            "UL_2 7203 JT 缺下限價",
        ),
        (terms(autocall=()), None, "沒有比價日"),
        (terms(autocall=(START,)), None, f"最後一個比價日 {START} 和最終比價日 {FINAL} 不同"),
        (terms(ko_type=None), None, "KO Type「」不認得"),
        (terms(barrier=AKI, strike_date=None), None, "每日觀察 KI 要有 Strike date"),
    ],
)
def test_incomplete_terms_need_confirmation(t: Terms, prices, reason: str) -> None:
    outcome = run(t, prices or closes())
    assert (outcome.status, outcome.reason, outcome.observed_through) == (TO_CONFIRM, reason, None)


def test_dates_beyond_the_calendar_need_confirmation() -> None:
    t = terms(final_date=dt.date(2028, 1, 4), autocall=(START, dt.date(2028, 1, 4)))

    outcome = run(t, closes(), dt.date(2028, 1, 4))

    assert outcome.status == TO_CONFIRM and "超出美國假日表的涵蓋範圍（2026-01-01～2027-12-31）" in outcome.reason


def test_a_closed_autocall_date_needs_confirmation() -> None:
    dates = (START, JP_HOLIDAY, FINAL)

    outcome = run(terms("Period End", autocall=dates), closes())

    assert outcome == Outcome(
        TO_CONFIRM,
        f"比價日 {JP_HOLIDAY} 是 UL_2 7203 JT 市場的休市日",
        JP_HOLIDAY - dt.timedelta(days=1),
        ki=NO_KI_TEXT,
    )


def _text(event: AutoEvent) -> str:
    if event.event == DELIVERY:
        return f"接股 {event.underlying} {event.event_date}"
    return f"{event.event} {event.event_date}"


# ---------------------------------------------------------------- 資料庫


T1, T2, T3 = "147199990001", "147199990002", "147199990003"


def seed(pg, tdcc: str, *, locked: bool = True, status: str = "完成") -> None:
    """一檔 D 型非記憶式、沒有 KI 的商品：核對條件完成、兩個比價日。"""
    seed_products(pg, [inventory(tdcc, [("台北", 10000)], **{"KI(Freq)": "-", "KO(memo)": "N"})])
    pg.execute(
        "UPDATE workflow.product SET strike_date = %s, final_valuation_date = %s, locked_at = %s WHERE tdcc = %s",
        (STRIKE, FINAL, dt.datetime(2026, 11, 3, tzinfo=dt.UTC) if locked else None, tdcc),
    )
    pg.execute("INSERT INTO workflow.check_terms (tdcc, status) VALUES (%s, %s)", (tdcc, status))
    for u in (ul(1, "AAA UW"), ul(2, "BBB UN")):
        pg.execute(
            "INSERT INTO workflow.underlying_price (tdcc, seq, code, initial_price, strike_price, ki_price, ko_price)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (tdcc, u.seq, u.code, u.initial, u.strike, u.ki, u.ko),
        )
    for slot, value in ((1, START), (6, FINAL), (2, None)):
        pg.execute("INSERT INTO workflow.autocall_date (tdcc, slot, value) VALUES (%s, %s, %s)", (tdcc, slot, value))


def write_closes(pg, prices: dict[str, dict[dt.date, str]]) -> None:
    (import_id,) = pg.execute(
        "INSERT INTO workflow.close_price_import (source, file_name, file_sha256, imported_by, summary)"
        " VALUES ('歷史收盤價', 'HP.xlsx', 'x', 'tester', '{}') RETURNING import_id"
    ).fetchone()
    for code, days in prices.items():
        for day, close in days.items():
            pg.execute(
                "INSERT INTO workflow.close_price (close_date, code, close, import_id) VALUES (%s, %s, %s, %s)"
                " ON CONFLICT (close_date, code) DO UPDATE SET close = EXCLUDED.close",
                (day, code, close, import_id),
            )


def us_closes(last: dt.date, special: dict[dt.date, str] | None = None) -> dict[str, dict[dt.date, str]]:
    days = {day: (special or {}).get(day, "90") for day in trading_days("AAA UW", STRIKE, last)}
    return {"AAA UW": days, "BBB UN": dict(days)}


def status(pg, tdcc: str) -> tuple:
    return pg.execute(
        "SELECT status, reason, observed_through, locked FROM workflow.pricing_status WHERE tdcc = %s", (tdcc,)
    ).fetchone()


def with_ki(pg, tdcc: str) -> tuple:
    return pg.execute(
        "SELECT status, reason, observed_through, ki FROM workflow.pricing_status WHERE tdcc = %s", (tdcc,)
    ).fetchone()


def events(pg, tdcc: str) -> list[tuple]:
    return [(e.event, e.event_date, e.underlying, e.source) for e in current_events(pg, tdcc)]


KO_DAY = dt.date(2026, 11, 18)


@pytest.mark.db
def test_observe_all_writes_events_and_status_only_when_changed(pg) -> None:
    seed(pg, T1)
    seed(pg, T2, locked=False)
    write_closes(pg, us_closes(dt.date(2026, 11, 20), {KO_DAY: "100"}))
    pg.execute("DELETE FROM workflow.close_price WHERE code = 'BBB UN' AND close_date = %s", (KO_DAY,))  # 先缺一天

    first = observe_all(pg, CALENDAR, user="tester")

    assert first.through == dt.date(2026, 11, 20) and first.changes == ()
    assert status(pg, T1) == (WAIT_PRICES, f"UL_2 BBB UN：{KO_DAY} 沒有收盤價", KO_DAY - dt.timedelta(days=1), True)
    assert status(pg, T2)[3] is False, "未鎖住的也比，標未鎖住"
    assert [r.tdcc for r in first.unfinished()] == [T1, T2]

    write_closes(pg, {"BBB UN": {KO_DAY: "100"}})
    second = observe_all(pg, CALENDAR, user="tester")

    assert status(pg, T1) == (EXITED, f"KO {KO_DAY}", KO_DAY, True)
    assert events(pg, T1) == [(KO, KO_DAY, None, "比價")]
    assert [(c.tdcc, c.previous, c.current) for c in second.changes] == [
        (T1, (), ((KO, KO_DAY.isoformat(), None),)),
        (T2, (), ((KO, KO_DAY.isoformat(), None),)),
    ]
    (at,) = pg.execute("SELECT calculated_at FROM workflow.pricing_status WHERE tdcc = %s", (T1,)).fetchone()

    third = observe_all(pg, CALENDAR, user="tester")

    assert third.changes == () and third.written == 0, "算一遍、有變才寫"
    assert pg.execute("SELECT calculated_at FROM workflow.pricing_status WHERE tdcc = %s", (T1,)).fetchone() == (at,)


@pytest.mark.db
def test_ki_is_stored_and_kept_after_exit(pg) -> None:
    """#196：KI 欄存進比價狀態；Non-call 期間已比到空白，出場後 KI 沿用上一次。"""
    seed(pg, T1)
    pg.execute("UPDATE workflow.product SET barrier_type = 'AKI' WHERE tdcc = %s", (T1,))
    touched = dt.date(2026, 11, 5)
    write_closes(pg, us_closes(dt.date(2026, 11, 10), {touched: "60"}))
    observe_all(pg, CALENDAR)

    ki = f"AKI hit {touched} UL_1 AAA UW"
    assert with_ki(pg, T1) == (ON_BOOK, NON_CALL, None, ki)

    write_closes(pg, us_closes(dt.date(2026, 11, 20), {touched: "60", KO_DAY: "100"}))
    observe_all(pg, CALENDAR)
    assert with_ki(pg, T1) == (EXITED, f"KO {KO_DAY}", KO_DAY, ki)

    assert observe_all(pg, CALENDAR).written == 0, "出場後 KI 沿用，不重寫"

    pg.execute("UPDATE workflow.pricing_status SET ki = NULL WHERE tdcc = %s", (T1,))
    assert observe_all(pg, CALENDAR).written == 1
    assert with_ki(pg, T1) == (EXITED, f"KO {KO_DAY}", KO_DAY, "AKI"), "#198：出場時沒存過 KI 的寫 KI 種類"


@pytest.mark.db
def test_exited_products_are_not_observed_again(pg) -> None:
    """已出場的不再比價（#169）：先假設收盤價不會事後更正，改了價格、條件或代碼事件都不動。"""
    seed(pg, T1)
    write_closes(pg, us_closes(dt.date(2026, 11, 20), {KO_DAY: "100"}))
    observe_all(pg, CALENDAR)

    write_closes(pg, {**us_closes(dt.date(2026, 11, 27)), "AAA UW": {KO_DAY: "99"}})
    pg.execute("UPDATE workflow.underlying_price SET code = 'CCC UN' WHERE tdcc = %s AND seq = 2", (T1,))
    receipt = observe_all(pg, CALENDAR)

    assert events(pg, T1) == [(KO, KO_DAY, None, "比價")]
    assert status(pg, T1) == (EXITED, f"KO {KO_DAY}", KO_DAY, True), "已比到的日期沿用上一次"
    assert receipt.changes == () and receipt.written == 0


@pytest.mark.db
def test_exited_products_stay_exited_when_check_terms_change(pg) -> None:
    seed(pg, T1)
    write_closes(pg, us_closes(dt.date(2026, 11, 20), {KO_DAY: "100"}))
    observe_all(pg, CALENDAR)
    pg.execute("UPDATE workflow.check_terms SET status = '待確認', reason = '更正後重算' WHERE tdcc = %s", (T1,))

    observe_all(pg, CALENDAR)

    assert status(pg, T1) == (EXITED, f"KO {KO_DAY}", KO_DAY, True)
    assert events(pg, T1) == [(KO, KO_DAY, None, "比價")]


@pytest.mark.db
def test_redeemed_products_are_not_observed(pg) -> None:
    seed(pg, T1)
    record_redemption(pg, T1, dt.date(2026, 11, 10), user="tester")
    write_closes(pg, us_closes(dt.date(2026, 11, 20), {KO_DAY: "100"}))

    observe_all(pg, CALENDAR)

    assert status(pg, T1) == (EXITED, "顧客贖回 2026-11-10", None, True)
    assert events(pg, T1) == [("顧客贖回", dt.date(2026, 11, 10), None, "人工")]


@pytest.mark.db
def test_legacy_events_stop_observation(pg) -> None:
    """舊清單的 KO、到期還本當作已確認（#169）：不再比對，比價狀態是已出場。"""
    seed(pg, T1)
    seed(pg, T2)
    for tdcc, day in ((T1, KO_DAY), (T2, dt.date(2026, 11, 19))):
        pg.execute(
            "INSERT INTO workflow.product_event (tdcc, event, event_date, source, recorded_by)"
            " VALUES (%s, 'KO', %s, '舊清單', '資料表版本 0016')",
            (tdcc, day),
        )
    write_closes(pg, us_closes(dt.date(2026, 11, 20), {KO_DAY: "100"}))

    observe_all(pg, CALENDAR)

    assert status(pg, T1) == (EXITED, f"舊清單：KO {KO_DAY}", None, True)
    assert status(pg, T2) == (EXITED, "舊清單：KO 2026-11-19", None, True), "和比價結果不同也不再比對"
    assert events(pg, T1) == [(KO, KO_DAY, None, "舊清單")] and events(pg, T2) == [
        (KO, dt.date(2026, 11, 19), None, "舊清單")
    ]


@pytest.mark.db
def test_products_whose_check_terms_are_no_longer_done(pg) -> None:
    seed(pg, T1)
    seed(pg, T3, status="待確認")
    write_closes(pg, us_closes(dt.date(2026, 11, 20)))
    observe_all(pg, CALENDAR)
    assert status(pg, T3) is None, "核對條件不是完成的不比"

    pg.execute(
        "UPDATE workflow.check_terms SET status = '待確認', reason = 'UL_1 AAA UW：沒有股價' WHERE tdcc = %s", (T1,)
    )
    observe_all(pg, CALENDAR)

    assert status(pg, T1) == (
        TO_CONFIRM,
        "核對條件待確認，這次沒有比價：UL_1 AAA UW：沒有股價",
        dt.date(2026, 11, 20),
        True,
    )


def test_a_code_without_closes_waits_until_its_first_observation_day() -> None:
    prices = {A.code: closes()[A.code]}

    assert run(terms(), prices, dt.date(2026, 11, 10)) == Outcome(ON_BOOK, NON_CALL, ki=NO_KI_TEXT)
    assert run(terms(), prices) == Outcome(TO_CONFIRM, "UL_2 7203 JT：收盤價表沒有這個代碼", ki=NO_KI_TEXT)


@pytest.mark.db
def test_an_event_that_cannot_be_written_needs_confirmation(pg, monkeypatch) -> None:
    import sp_workflow.observation
    from sp_workflow.lifecycle import LifecycleError

    seed(pg, T1)
    write_closes(pg, us_closes(dt.date(2026, 11, 20), {KO_DAY: "100"}))

    def refused(conn, tdcc, events, *, user=None):
        raise LifecycleError(f"{tdcc} 已登記顧客贖回，停止比價，不寫自動事件")

    monkeypatch.setattr(sp_workflow.observation, "replace_auto_events", refused)

    receipt = observe_all(pg, CALENDAR)

    assert receipt.errors == ((T1, f"{T1} 已登記顧客贖回，停止比價，不寫自動事件"),)
    assert status(pg, T1)[:2] == (TO_CONFIRM, f"自動事件寫不進去：{T1} 已登記顧客贖回，停止比價，不寫自動事件")


# ---------------------------------------------------------------- 拆股（#270、ADR 0024）


SPLIT_DAY = dt.date(2026, 11, 10)  # 生效日（週二）：Strike date 之後、第一個比價日之前
SPLIT = Split("AAA UW", SPLIT_DAY, 1, 10)
JUMP_REASON = (
    f"UL_1 AAA UW：{SPLIT_DAY} 收盤價是 {SPLIT_DAY - dt.timedelta(days=1)} 的 0.1 倍，疑似拆股或收盤價有誤"
    "（拆股請到「維護 → 拆股登記…」登記，價格錯了請重匯）"
)


def split_prices(after: str = "9", special: dict[dt.date, str] | None = None) -> Callable[[dt.date], str]:
    """AAA UW 拆股前 90、生效日起是 `after`（原始收盤價）；`special` 指定某幾天。"""
    return lambda day: (special or {}).get(day, "90" if day < SPLIT_DAY else after)


def test_registered_splits_convert_closes_back_to_the_trade_basis() -> None:
    prices = closes(split_prices())

    assert observe(terms(), prices, CALENDAR, FINAL) == Outcome(TO_CONFIRM, JUMP_REASON, ki=NO_KI_TEXT), "沒登記"
    assert observe(terms(), prices, CALENDAR, FINAL, SplitBook([SPLIT])) == Outcome(
        EXITED, f"到期還本 {FINAL}", FINAL, (AutoEvent(MATURITY, FINAL),), NO_KI_TEXT
    ), "9 換回 90：沒有 KO、不接股"
    ko = closes(split_prices(special={KO_DAY: "10.1"}), on({KO_DAY: "100"}))
    assert observe(terms(), ko, CALENDAR, FINAL, SplitBook([SPLIT])).events == (AutoEvent(KO, KO_DAY),)


def test_reverse_splits_and_splits_before_the_strike_date() -> None:
    merged = closes(split_prices("900"))
    assert observe(terms(), merged, CALENDAR, FINAL, SplitBook([Split("AAA UW", SPLIT_DAY, 10, 1)])).events == (
        AutoEvent(MATURITY, FINAL),
    ), "10 合 1：900 換回 90"

    before_strike = SplitBook([Split("AAA UW", STRIKE, 1, 10)])
    assert observe(terms(), closes(), CALENDAR, FINAL, before_strike).events == (AutoEvent(MATURITY, FINAL),), (
        "生效日在 Strike date 當天（含）以前：進場價已經是新基準，不換算"
    )


def test_an_unregistered_jump_stops_the_day_before_but_an_earlier_ko_stands() -> None:
    late = dt.date(2026, 11, 20)
    jump = closes(lambda day: "90" if day < late else "9")
    outcome = observe(terms(), jump, CALENDAR, FINAL)
    assert (outcome.status, outcome.observed_through) == (TO_CONFIRM, late - dt.timedelta(days=1))
    assert outcome.reason.startswith(f"UL_1 AAA UW：{late} 收盤價是 {late - dt.timedelta(days=1)} 的 0.1 倍")

    ko_first = closes(lambda day: "101" if day == KO_DAY else ("90" if day < late else "9"), on({KO_DAY: "100"}))
    assert observe(terms(), ko_first, CALENDAR, FINAL).events == (AutoEvent(KO, KO_DAY),)


def test_a_confirmed_real_drop_is_observed() -> None:
    drop = dt.date(2026, 11, 20)
    prices = closes(lambda day: "90" if day < drop else "50")
    t = terms(barrier=AKI)

    assert observe(t, prices, CALENDAR, FINAL).status == TO_CONFIRM
    assert observe(t, prices, CALENDAR, FINAL, SplitBook([Split("AAA UW", drop, 1, 1)])) == Outcome(
        EXITED,
        f"接股 AAA UW {FINAL}",
        FINAL,
        (AutoEvent(DELIVERY, FINAL, "AAA UW"),),
        f"AKI hit {drop} UL_1 AAA UW",
    )


@pytest.mark.db
def test_changing_a_split_reobserves_affected_observation_exits(pg) -> None:
    seed(pg, T1)
    write_closes(pg, us_closes(FINAL, {day: "9" for day in trading_days("AAA UW", SPLIT_DAY, FINAL)}))
    pg.execute("UPDATE workflow.close_price SET close = 90 WHERE code = 'BBB UN'")
    replace_auto_events(pg, T1, [AutoEvent(DELIVERY, FINAL, "AAA UW")], user="tester")  # 沒有換算時判錯的接股

    assert observe_all(pg, CALENDAR).changes == (), "已出場的平常不再比價"
    add_split(pg, SPLIT, user="tester")
    receipt = observe_all(pg, CALENDAR, user="tester", revisit=affected_exits(pg, SPLIT))

    assert events(pg, T1) == [(MATURITY, FINAL, None, "比價")]
    assert [(c.tdcc, c.previous, c.current) for c in receipt.changes] == [
        (T1, ((DELIVERY, FINAL.isoformat(), "AAA UW"),), ((MATURITY, FINAL.isoformat(), None),))
    ]
    assert status(pg, T1) == (EXITED, f"到期還本 {FINAL}", FINAL, True)

    delete_split(pg, SPLIT, user="tester")
    observe_all(pg, CALENDAR, user="tester", revisit=affected_exits(pg, SPLIT))

    assert events(pg, T1) == [], "不再出場就清掉事件"
    assert status(pg, T1) == (TO_CONFIRM, JUMP_REASON, None, True)
    (previous, current) = pg.execute(
        "SELECT previous, current FROM workflow.product_event_change WHERE tdcc = %s ORDER BY change_id DESC LIMIT 1",
        (T1,),
    ).fetchone()
    assert (previous, current) == ([[MATURITY, FINAL.isoformat(), None]], [])


# ---------------------------------------------------------------- 價格基準日（#275、ADR 0025）


ADJUSTED = replace(ul(1, "AAA UW", "9"), basis=SPLIT_DAY)  # 上手依 1 拆 10 調整過：進場價 9、KO 價 9


def test_prices_already_adjusted_by_the_issuer_are_not_converted_twice() -> None:
    prices = closes(split_prices("8.5"))
    t = terms(underlyings=(ADJUSTED, B))

    assert observe(t, prices, CALENDAR, FINAL, SplitBook([SPLIT])) == Outcome(
        EXITED, f"到期還本 {FINAL}", FINAL, (AutoEvent(MATURITY, FINAL),), NO_KI_TEXT
    ), "拆股前的 90 換成 9、之後的 8.5 不換：都沒到 KO 價"
    ko = closes(split_prices("8.5", {KO_DAY: "9"}), on({KO_DAY: "100"}))
    assert observe(t, ko, CALENDAR, FINAL, SplitBook([SPLIT])).events == (AutoEvent(KO, KO_DAY),)


def test_a_basis_that_does_not_match_the_strike_close_needs_confirmation() -> None:
    t = terms(underlyings=(replace(ADJUSTED, basis=None), B))  # 調整過的價格、卻當成交易時的基準

    assert observe(t, closes(split_prices("8.5")), CALENDAR, FINAL, SplitBook([SPLIT])) == Outcome(
        TO_CONFIRM,
        f"UL_1 AAA UW：進場價 9 跟 {STRIKE} 的收盤價（換到價格基準後 90）對不起來，拆股登記的比例或商品的價格可能有誤",
        ki=NO_KI_TEXT,
    )
    assert observe(t, closes(), CALENDAR, FINAL).status == EXITED, "沒有拆股的標的不檢查"


@pytest.mark.db
def test_observe_all_stores_the_basis_from_the_registered_splits(pg) -> None:
    seed(pg, T1)
    pg.execute("UPDATE workflow.underlying_price SET initial_price = 9, strike_price = 7.2, ko_price = 9 WHERE seq = 1")
    write_closes(pg, us_closes(FINAL, {day: "8.5" for day in trading_days("AAA UW", SPLIT_DAY, FINAL)}))
    pg.execute("UPDATE workflow.close_price SET close = 90 WHERE code = 'BBB UN'")
    add_split(pg, SPLIT, user="tester")

    observe_all(pg, CALENDAR, user="tester")

    assert pg.execute("SELECT seq, price_basis_date FROM workflow.underlying_price ORDER BY seq").fetchall() == [
        (1, SPLIT_DAY),
        (2, None),
    ]
    assert events(pg, T1) == [(MATURITY, FINAL, None, "比價")]
