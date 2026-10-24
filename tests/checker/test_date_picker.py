"""日期欄位旁的小日曆（#192）：月曆的格子、開啟時停的月份、選了一天填入欄位並呼叫 on_pick。"""

from __future__ import annotations

import datetime as dt
import tkinter as tk

from fcn_checker.date_picker import DateEntry, DatePopup, month_weeks, parse_day, shift_month

TODAY = dt.date(2026, 10, 24)


def test_month_weeks_start_on_sunday() -> None:
    weeks = month_weeks(2026, 10)
    assert weeks[0] == [None, None, None, None, dt.date(2026, 10, 1), dt.date(2026, 10, 2), dt.date(2026, 10, 3)]
    assert weeks[-1][-1] is None or weeks[-1][-1] == dt.date(2026, 10, 31)
    assert [day for week in weeks for day in week if day] == [dt.date(2026, 10, d) for d in range(1, 32)]


def test_shift_month_crosses_years() -> None:
    assert shift_month(2026, 12, 1) == (2027, 1)
    assert shift_month(2026, 1, -1) == (2025, 12)
    assert shift_month(2026, 5, 0) == (2026, 5)


def test_parse_day() -> None:
    assert parse_day(" 2026-03-05 ") == dt.date(2026, 3, 5)
    assert parse_day("10/15") is None and parse_day("") is None


def test_popup_opens_on_the_field_month_and_picks_a_day(master) -> None:
    var = tk.StringVar(master, "2026-03-05")
    picked = []
    entry = tk.Entry(master, textvariable=var)
    popup = DatePopup(entry, var, lambda: picked.append(var.get()), today=TODAY)

    assert (popup.year, popup.month) == (2026, 3) and popup.title_var.get() == "2026 年 3 月"
    assert (
        str(popup.cells[dt.date(2026, 3, 5)].cget("background")).upper()
        != str(popup.cells[dt.date(2026, 3, 6)].cget("background")).upper()
    ), "欄位裡的日期標出來"
    popup.show_month(1)
    assert popup.title_var.get() == "2026 年 4 月" and dt.date(2026, 4, 30) in popup.cells

    popup.pick(dt.date(2026, 4, 30))

    assert var.get() == "2026-04-30" and picked == ["2026-04-30"] and not popup.winfo_exists()


def test_popup_without_a_valid_date_opens_on_today_and_can_clear(master) -> None:
    var = tk.StringVar(master, "10/15")
    picked = []
    popup = DatePopup(tk.Entry(master), var, lambda: picked.append(var.get()), today=TODAY)

    assert (popup.year, popup.month) == (2026, 10)
    popup.pick(None)
    assert var.get() == "" and picked == [""], "清除也呼叫 on_pick（篩選欄空白是不限）"


def test_closing_leaves_the_field_alone(master) -> None:
    var = tk.StringVar(master, "2026-03-05")
    picked = []
    popup = DatePopup(tk.Entry(master), var, lambda: picked.append(1), today=TODAY)

    popup.close()

    assert var.get() == "2026-03-05" and picked == [] and not popup.winfo_exists()


def test_date_entry_keeps_one_popup(master) -> None:
    var = tk.StringVar(master)
    field = DateEntry(master, var, width=12)
    field.entry.insert(0, "2026-01-02")

    first = field.open()
    second = field.open()

    assert not first.winfo_exists() and second.winfo_exists() and (second.year, second.month) == (2026, 1)
    second.pick(dt.date(2026, 1, 9))
    assert field.entry.get() == "2026-01-09"
