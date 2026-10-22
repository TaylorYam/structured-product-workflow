"""比價（`sp_workflow.observation`，#155、ADR 0020）：KO、KI、到期判定、缺價格停住與比價狀態。

商品、價格與假日全部虛構；假日表用測試自己組的 `TradingCalendar`（美國、日本，2026～2027）。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterable
from decimal import Decimal

import pytest

from db_synth import inventory, seed_products
from fcn_checker.trading_calendar import Market, TradingCalendar
from sp_workflow.lifecycle import DELIVERY, KO, MATURITY, AutoEvent, current_events, record_redemption
from sp_workflow.observation import (
    AKI,
    EKI,
    EXITED,
    MKI,
    MKI_REASON,
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
    assert run(terms("Daily"), both) == Outcome(EXITED, f"KO {day2}", day2, (AutoEvent(KO, day2),))


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
    ("barrier", "a_final", "a_path", "event"),
    [
        (NO_KI, "79", {}, AutoEvent(DELIVERY, FINAL, "AAA UW")),  # 沒有 KI：低於執行價就接股
        (NO_KI, "80", {}, AutoEvent(MATURITY, FINAL)),  # 等於執行價不接股
        (EKI, "64", {}, AutoEvent(DELIVERY, FINAL, "AAA UW")),  # 到期觀察：最終收盤價 < 下限價
        (EKI, "70", {dt.date(2026, 11, 20): "50"}, AutoEvent(MATURITY, FINAL)),  # 期間跌破不算
        (AKI, "70", {dt.date(2026, 11, 3): "60"}, AutoEvent(DELIVERY, FINAL, "AAA UW")),  # Strike date 隔天就觀察
        (AKI, "70", {STRIKE: "60"}, AutoEvent(MATURITY, FINAL)),  # Strike date 當天不算
        (AKI, "70", {}, AutoEvent(MATURITY, FINAL)),  # 沒觸及
    ],
)
def test_maturity(barrier: str, a_final: str, a_path: dict, event: AutoEvent) -> None:
    prices = closes(on({FINAL: a_final, **a_path}), on({FINAL: "85"}))

    assert run(terms("Daily", barrier), prices) == Outcome(EXITED, _text(event), FINAL, (event,))


def test_delivery_names_the_worst_performer() -> None:
    worst = ul(2, "7203 JT", "2000")
    prices = closes(on({FINAL: "60"}))
    prices[worst.code] = {day: D("1100") for day in trading_days(worst.code)}  # 55%

    outcome = run(terms("Daily", EKI, underlyings=(A, worst)), prices)

    assert outcome.events == (AutoEvent(DELIVERY, FINAL, "7203 JT"),)


def test_aki_is_watched_on_each_market_own_trading_days() -> None:
    prices = closes(on({FINAL: "85"}), on({FINAL: "1000"}))
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
    assert run(terms("Daily", MKI), closes()) == Outcome(
        TO_CONFIRM, f"{MKI_REASON}（最終比價日 {FINAL} 沒有 KO，要人工判定接股或到期還本）", FINAL
    )
    through = dt.date(2026, 11, 20)
    assert run(terms("Daily", MKI), closes(), through) == Outcome(TO_CONFIRM, MKI_REASON, through)
    day = dt.date(2026, 11, 18)
    assert run(terms("Daily", MKI), closes(on({day: "100"}), on({day: "100"}))).events == (AutoEvent(KO, day),)


# ---------------------------------------------------------------- 缺價格、待確認、比到哪裡


def test_a_missing_price_stops_on_that_day() -> None:
    gap = dt.date(2026, 11, 19)
    prices = closes()
    del prices[B.code][gap]
    later = dt.date(2026, 11, 20)
    prices[A.code][later] = prices[B.code][later] = D("120")  # 缺價格之後的 KO 不看

    assert run(terms(), prices) == Outcome(WAIT_PRICES, f"UL_2 7203 JT：{gap} 沒有收盤價", gap - dt.timedelta(days=1))


def test_holidays_are_skipped_without_waiting_for_prices() -> None:
    prices = closes()
    assert US_HOLIDAY not in prices[A.code] and JP_HOLIDAY not in prices[B.code]

    assert run(terms("Daily", AKI), prices).status == EXITED


def test_aki_needs_prices_between_strike_and_the_first_autocall_date() -> None:
    gap = dt.date(2026, 11, 5)
    prices = closes()
    del prices[A.code][gap]

    assert run(terms("Daily", AKI), prices).status == WAIT_PRICES
    assert run(terms("Daily", NO_KI), prices).status == EXITED, "沒有每日 KI 時保證期內的價格用不到"


def test_through_the_latest_close_stays_on_book() -> None:
    through = dt.date(2026, 11, 20)

    assert run(terms(), closes(), through) == Outcome(ON_BOOK, "", through)
    assert run(terms(), closes(), dt.date(2026, 11, 10)) == Outcome(ON_BOOK, "還沒到第一個觀察日")


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
    assert run(t, prices or closes()) == Outcome(TO_CONFIRM, reason)


def test_dates_beyond_the_calendar_need_confirmation() -> None:
    t = terms(final_date=dt.date(2028, 1, 4), autocall=(START, dt.date(2028, 1, 4)))

    outcome = run(t, closes(), dt.date(2028, 1, 4))

    assert outcome.status == TO_CONFIRM and "超出美國假日表的涵蓋範圍（2026-01-01～2027-12-31）" in outcome.reason


def test_a_closed_autocall_date_needs_confirmation() -> None:
    dates = (START, JP_HOLIDAY, FINAL)

    outcome = run(terms("Period End", autocall=dates), closes())

    assert outcome == Outcome(
        TO_CONFIRM, f"比價日 {JP_HOLIDAY} 是 UL_2 7203 JT 市場的休市日", JP_HOLIDAY - dt.timedelta(days=1)
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
def test_a_corrected_close_replaces_the_events(pg) -> None:
    seed(pg, T1)
    write_closes(pg, us_closes(dt.date(2026, 11, 20), {KO_DAY: "100"}))
    observe_all(pg, CALENDAR)

    write_closes(pg, {"AAA UW": {KO_DAY: "99"}})
    receipt = observe_all(pg, CALENDAR)

    assert status(pg, T1) == (ON_BOOK, None, dt.date(2026, 11, 20), True)
    assert events(pg, T1) == []
    assert receipt.changes[0].previous == ((KO, KO_DAY.isoformat(), None),) and receipt.changes[0].current == ()


@pytest.mark.db
def test_existing_events_are_kept_when_prices_go_missing(pg) -> None:
    seed(pg, T1)
    write_closes(pg, us_closes(dt.date(2026, 11, 20), {KO_DAY: "100"}))
    observe_all(pg, CALENDAR)
    pg.execute(
        "UPDATE workflow.underlying_price SET code = 'CCC UN' WHERE tdcc = %s AND seq = 2", (T1,)
    )  # 例如更正了代碼、收盤價表還沒有

    observe_all(pg, CALENDAR)

    assert events(pg, T1) == [(KO, KO_DAY, None, "比價")]
    assert status(pg, T1)[0] == TO_CONFIRM and "原本的 KO 2026-11-18 先保留" in status(pg, T1)[1]


@pytest.mark.db
def test_redeemed_products_are_not_observed(pg) -> None:
    seed(pg, T1)
    record_redemption(pg, T1, dt.date(2026, 11, 10), user="tester")
    write_closes(pg, us_closes(dt.date(2026, 11, 20), {KO_DAY: "100"}))

    observe_all(pg, CALENDAR)

    assert status(pg, T1) == (EXITED, "顧客贖回 2026-11-10", None, True)
    assert events(pg, T1) == [("顧客贖回", dt.date(2026, 11, 10), None, "人工")]


@pytest.mark.db
def test_legacy_events_are_compared_not_replaced(pg) -> None:
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

    assert status(pg, T1) == (EXITED, f"同舊清單：KO {KO_DAY}", KO_DAY, True)
    assert status(pg, T2) == (TO_CONFIRM, f"和舊清單不同：舊清單 KO 2026-11-19；比價 KO {KO_DAY}", KO_DAY, True)
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

    assert run(terms(), prices, dt.date(2026, 11, 10)) == Outcome(ON_BOOK, "還沒到第一個觀察日")
    assert run(terms(), prices) == Outcome(TO_CONFIRM, "UL_2 7203 JT：收盤價表沒有這個代碼")


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
