"""KO% 每期固定往下降的共用讀法（parsers/stepdown.py，workflow #544、ADR 0033）：上手交出每期 KO% 後推得的標準欄位。

合成的表列與排程，數值都是虛構的。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal as D

from fcn_checker.parsers import stepdown
from fcn_checker.schema import FieldStatus, Line, ParsedField
from fcn_checker.standard_fields import AutocallSchedule, DateRow, DateTable

WHERE = "提前出場表"


def line(text: str) -> Line:
    return Line(1, 0.0, 0.0, 10.0, 10.0, text)


def rows(*pcts: str | None) -> list[tuple[D | None, list[Line]]]:
    return [(None if p is None else D(p), [line(p or "N/A")]) for p in pcts]


def test_leading_non_callable_rows_are_not_ko_periods():
    out = stepdown.ko_terms(rows(None, "100", "98", "96"), WHERE)
    assert [(lv.period, lv.pct) for lv in out["ko_levels"].value] == [(1, D(100)), (2, D(98)), (3, D(96))]
    assert (out["ko_pct"].value, out["ko_step_pct"].value) == (D(100), D(2))


def test_gap_after_the_first_callable_period_is_invalid():
    out = stepdown.ko_terms(rows("100", None, "96"), WHERE)
    assert {pf.status for pf in out.values()} == {FieldStatus.INVALID}
    assert "第 2 列沒有 KO%" in out["ko_step_pct"].note


def test_no_callable_period_is_missing():
    out = stepdown.ko_terms(rows(None, None), WHERE)
    assert {pf.status for pf in out.values()} == {FieldStatus.MISSING}


def test_uneven_flat_or_single_period_has_no_step():
    for pcts, note in (
        (("100", "98", "97"), "不是每期固定往下降"),
        (("100", "100"), "不是每期固定往下降"),
        (("100",), "只有 1 期"),
    ):
        out = stepdown.ko_terms(rows(*pcts), WHERE)
        assert out["ko_levels"].ok and out["ko_pct"].ok
        assert not out["ko_step_pct"].ok and note in stepdown.step_problem(out)


def _schedule(first_callable: int, ends: list[dt.date | None]) -> tuple[ParsedField, ParsedField]:
    n = len(ends)
    final = dt.date(2027, 1, 29)
    sched = AutocallSchedule("D", first_callable, n, {first_callable: ends[first_callable - 1] or final, n: final})
    table = DateTable(WHERE, tuple(DateRow(i, end=e) for i, e in enumerate(ends, start=1)))
    return (
        ParsedField("autocall_schedule", FieldStatus.PRESENT, sched, []),
        ParsedField("schedule_table", FieldStatus.PRESENT, table, []),
    )


def test_daily_schedule_lists_every_period_end():
    ends = [dt.date(2026, 7, 31), dt.date(2026, 8, 31), dt.date(2026, 9, 30), dt.date(2027, 1, 28)]
    sched, table = _schedule(1, ends)
    out = stepdown.every_period(sched, table)
    assert out.ok and out.value.listed
    # 末期仍用原排程的比價日（上手可能取最終評價日），其他期取期末日
    assert out.value.dates == {1: ends[0], 2: ends[1], 3: ends[2], 4: dt.date(2027, 1, 29)}


def test_daily_schedule_with_a_blank_period_end_is_invalid():
    sched, table = _schedule(1, [dt.date(2026, 7, 31), None, dt.date(2026, 9, 30), dt.date(2027, 1, 28)])
    out = stepdown.every_period(sched, table)
    assert out.status == FieldStatus.INVALID and "第 2 期" in out.note


def test_daily_schedule_without_non_call_is_invalid():
    sched, table = _schedule(1, [dt.date(2026, 7, 31), dt.date(2027, 1, 28)])
    sched = ParsedField(sched.name, sched.status, AutocallSchedule("D", 0, 2, {0: dt.date(2026, 7, 1)}), [])
    assert stepdown.every_period(sched, table).status == FieldStatus.INVALID


def test_period_end_schedule_is_unchanged():
    sched, table = _schedule(1, [dt.date(2026, 7, 31), dt.date(2027, 1, 28)])
    periodic = ParsedField(sched.name, sched.status, AutocallSchedule("P", 1, 2, {1: dt.date(2026, 7, 31)}), [])
    assert stepdown.every_period(periodic, table) is periodic
