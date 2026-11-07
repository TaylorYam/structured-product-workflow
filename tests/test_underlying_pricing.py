"""標的定價（`sp_workflow.underlying_pricing`，#442）：一般、lookback、拆股三種情況的表現與最差一檔，
和比價把 KI 觸及存成結構、曝險讀結構（不解析 KI 欄的字）。資料全部虛構。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from db_synth import seed_closes
from sp_workflow.exposure import KI_BROKEN, assess, load_positions
from sp_workflow.observation import observe_all
from sp_workflow.stock_splits import Split, SplitBook
from sp_workflow.underlying_pricing import KiTouch, Leg, converted, worst
from test_observation import CALENDAR, FINAL, T1, seed, us_closes

D = Decimal


def leg(seq: int, code: str, close: str | None, initial: str | None = "100", strike: str = "80", ki: str = "65") -> Leg:
    return Leg(
        seq, code, None if initial is None else D(initial), D(strike), D(ki), None if close is None else D(close)
    )


# ---------- 一般 ----------


def test_performance_and_distance_to_strike() -> None:
    a = leg(1, "AAA UW", "72")

    assert (a.performance, a.to_strike) == (D("-0.28"), D("-0.1"))
    assert (a.below_strike, a.below_ki) == (True, False)


def test_breaking_a_level_is_strictly_below_it() -> None:
    assert not leg(1, "AAA UW", "80").below_strike, "等於執行價不算跌破"
    assert not leg(1, "AAA UW", "65").below_ki, "等於下限價不算跌破"
    assert leg(1, "AAA UW", "64.99").below_ki
    assert not leg(1, "AAA UW", None).below_strike, "缺收盤價不算跌破"
    assert not Leg(1, "AAA UW", D(100), D(80), None, D(10)).below_ki, "沒有 KI 不算跌破"


def test_worst_is_the_lowest_performance_and_the_smaller_seq_on_a_tie() -> None:
    a, b, c = leg(1, "AAA UW", "90"), leg(2, "BBB UN", "70"), leg(3, "CCC UN", "70")

    assert worst([a, b, c]) == b
    assert worst([c, a]) == c


def test_no_worst_without_every_performance() -> None:
    assert worst([]) is None
    assert worst([leg(1, "AAA UW", "90"), leg(2, "BBB UN", None)]) is None, "缺收盤價"
    assert worst([leg(1, "AAA UW", "90"), leg(2, "BBB UN", "70", initial=None)]) is None, "VWAP 還沒回填期初價格"


# ---------- lookback ----------


def test_lookback_measures_against_the_lookback_initial() -> None:
    # 進場價 100、回顧期初價格 80（執行價已改成回顧執行價 64）：收盤 72 對進場價 −28%、對回顧期初價格 −10%
    looked_back = Leg(1, "AAA UW", D(80), D(64), None, D(72))
    plain = Leg(2, "BBB UN", D(100), D(80), None, D(85))

    assert looked_back.performance == D("-0.1")
    assert (looked_back.below_strike, plain.below_strike) == (False, False)
    assert worst([looked_back, plain]) == plain, "用進場價算的話會是 AAA"


# ---------- 拆股 ----------


def test_split_close_is_converted_back_to_the_price_basis() -> None:
    basis, split_day, day = dt.date(2026, 5, 20), dt.date(2026, 8, 3), dt.date(2026, 10, 28)
    book = SplitBook([Split("AAA UW", split_day, 1, 2)])

    close = converted(book, "AAA UW", D(45), basis, day)
    a, b = leg(1, "AAA UW", str(close)), leg(2, "BBB UN", "85")

    assert close == D(90), "1 拆 2 之後收盤 45，換回基準是 90"
    assert worst([a, b]) == b, "沒換算的話 AAA 45 會是最差、而且跌破下限價"
    assert converted(book, "AAA UW", D(90), basis, split_day - dt.timedelta(days=1)) == D(90), "生效日之前不換算"


# ---------- KI 觸及：比價存結構、曝險讀結構 ----------


@pytest.mark.db
def test_aki_touch_is_stored_by_observation_and_read_by_exposure(pg) -> None:
    seed(pg, T1)
    pg.execute("UPDATE workflow.product SET barrier_type = 'AKI' WHERE tdcc = %s", (T1,))
    touched, today = dt.date(2026, 11, 5), dt.date(2026, 11, 10)
    seed_closes(pg, us_closes(today, {touched: "60"}))  # 11/5 跌破下限價 65，之後回到 90

    observe_all(pg, CALENDAR)

    assert pg.execute(
        "SELECT ki, ki_touch_date, ki_touch_code FROM workflow.pricing_status WHERE tdcc = %s", (T1,)
    ).fetchone() == (f"AKI hit {touched} UL_1 AAA UW", touched, "AAA UW")
    (position,) = load_positions(pg, ["AAA UW"], FINAL, today=today)
    assert position.ki_touch == KiTouch(touched, "AAA UW")
    assert assess(position, today).status == KI_BROKEN, "現在 90 在執行價之上，但 AKI 已觸及"

    pg.execute("UPDATE workflow.pricing_status SET ki_touch_date = NULL, ki_touch_code = NULL WHERE tdcc = %s", (T1,))
    (untouched,) = load_positions(pg, ["AAA UW"], FINAL, today=today)
    assert assess(untouched, today).status != KI_BROKEN, "曝險看結構，不解析 KI 欄的字"
