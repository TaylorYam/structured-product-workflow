"""日期欄位的小日曆（#192、#198）：月曆的格子、開啟時停的月份、選了一天填入欄位並呼叫 on_pick。"""

from __future__ import annotations

import datetime as dt
import tkinter as tk

from fcn_checker.date_picker import DateEntry, DatePopup, month_grid, parse_day, shift_month

TODAY = dt.date(2026, 10, 24)


def test_month_grid_is_six_weeks_from_sunday() -> None:
    days = month_grid(2026, 10)
    assert len(days) == 42 and days[0] == dt.date(2026, 9, 27) and days[0].weekday() == 6
    assert days[4] == dt.date(2026, 10, 1) and days[-1] == dt.date(2026, 11, 7)
    assert [d for d in days if d.month == 10] == [dt.date(2026, 10, d) for d in range(1, 32)]
    assert month_grid(2026, 11)[0] == dt.date(2026, 11, 1), "1 號是星期日就從 1 號開始"


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
    fill = popup.canvas.itemcget
    assert fill(popup.cells[dt.date(2026, 3, 5)], "fill") != fill(popup.cells[dt.date(2026, 3, 6)], "fill"), (
        "欄位裡的日期標出來"
    )
    assert fill(popup.cells[dt.date(2026, 4, 2)], "fill") != fill(popup.cells[dt.date(2026, 3, 6)], "fill"), (
        "下一個月的日子淡灰"
    )
    x, y = popup.canvas.coords(popup.cells[dt.date(2026, 3, 18)])
    assert popup.day_at(x, y) == dt.date(2026, 3, 18) and popup.day_at(x, 1) is None, "星期列不是日子"
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


def test_date_entry_is_one_box_with_a_calendar_icon(master) -> None:
    """#198：輸入框和月曆圖示同一格，點圖示跳出月曆。"""
    field = DateEntry(master, tk.StringVar(master, "2026-02-03"))

    assert field.entry.master is field.box and field.button.master is field.box
    popup = field.open()
    assert popup.winfo_exists() and (popup.year, popup.month) == (2026, 2)
    popup.close()
