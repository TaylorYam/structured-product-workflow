"""「9 出場清單」分頁（`sp_workflow.tabs.exits`，#171、#188）：重新整理、改日期、帶入比到的那一天、匯出 Excel。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from tkinter import filedialog

import openpyxl

import sp_workflow.tabs.exits
from sp_workflow.exits import ExitList, ExitRow
from window_synth import new_window

DAY = dt.date(2026, 10, 21)
T = dt.datetime(2026, 10, 22, 8, 30, tzinfo=dt.UTC)
ROWS = (
    ExitRow("029199990001", "BARC", "FCN", "KO", DAY, "", "比價", True, T),
    ExitRow("037199990002", "SG", "FCN", "接股", DAY, "AAA UW", "舊清單", False, T),
    ExitRow("037199990003", "SG", "FCN", "KO", DAY, "", "舊清單", True, T),
)


FOUND = ExitList(DAY, ROWS)


def loaded(master, tmp_path: Path, monkeypatch, immediate, found=FOUND):
    window = new_window(master, tmp_path, background=immediate, logged_in=True)
    tab = window.exits
    asked = []

    def load(settings, paths, day):
        asked.append(day)
        return found

    monkeypatch.setattr(sp_workflow.tabs.exits, "load_exit_list", load)
    tab.refresh()
    return window, tab, asked


def cells(tab) -> list[tuple[str, ...]]:
    return [tuple(tab.table.item(item, "values")) for item in tab.table.get_children()]


def test_exit_list_is_the_last_tab(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)

    assert window.notebook.tab(window.notebook.tabs()[-1], "text") == "9 出場清單"
    assert window.exits.header.step.get() == "步驟 9／9"


def test_a_blank_day_reads_the_latest_close_day(master, tmp_path: Path, monkeypatch, immediate) -> None:
    _, tab, asked = loaded(master, tmp_path, monkeypatch, immediate)

    assert asked == [None] and tab.day_var.get() == "2026-10-21", "讀到後把日期填回去"
    at = f"{T.astimezone():%Y-%m-%d %H:%M}"
    assert cells(tab) == [
        ("029199990001", "BARC", "FCN", "KO", "2026-10-21", "", "比價", "已鎖住", at),
        ("037199990002", "SG", "FCN", "接股", "2026-10-21", "AAA UW", "舊清單", "未鎖住", at),
        ("037199990003", "SG", "FCN", "KO", "2026-10-21", "", "舊清單", "已鎖住", at),
    ]
    assert tab.kpis.values == (
        ("KO（檔）", 2, False),
        ("接股（檔）", 1, False),
        ("到期還本（檔）", 0, False),
        ("顧客贖回（檔）", 0, False),
    )
    assert tab.status_var.get() == "收盤日 2026-10-21：出場 3 檔（比價 1、舊清單 2）。", "寫出各來源的檔數"


def test_two_events_of_one_product_count_as_one(master, tmp_path: Path, monkeypatch, immediate) -> None:
    rows = (*ROWS, ExitRow("037199990003", "SG", "FCN", "顧客贖回", DAY, "", "人工", True, T))
    _, tab, _ = loaded(master, tmp_path, monkeypatch, immediate, ExitList(DAY, rows))

    assert len(cells(tab)) == 4, "一筆一列"
    assert tab.kpis.values[0] == ("KO（檔）", 2, False) and tab.kpis.values[3] == ("顧客贖回（檔）", 1, False)
    assert tab.status_var.get() == "收盤日 2026-10-21：出場 3 檔（比價 1、舊清單 2、人工 1）。", "檔數依 TDCC 算"
    assert str(tab.export_button.cget("state")) == "normal"


def test_another_day_without_exits(master, tmp_path: Path, monkeypatch, immediate) -> None:
    _, tab, asked = loaded(master, tmp_path, monkeypatch, immediate, ExitList(dt.date(2026, 10, 15), ()))
    tab.day_var.set("2026-10-15")
    tab.refresh()

    assert asked[-1] == dt.date(2026, 10, 15)
    assert cells(tab) == [] and tab.status_var.get() == "收盤日 2026-10-15：沒有出場的商品。"
    assert str(tab.export_button.cget("state")) == "disabled"


def test_a_bad_day_is_explained_without_reading(master, tmp_path: Path, monkeypatch, immediate) -> None:
    _, tab, asked = loaded(master, tmp_path, monkeypatch, immediate)
    tab.day_var.set("10/15")
    tab.refresh()

    assert asked == [None], "日期不對時不讀資料庫"
    assert "收盤日「10/15」不是 YYYY-MM-DD" in tab.status_var.get()


def test_an_empty_close_table_is_explained(master, tmp_path: Path, monkeypatch, immediate) -> None:
    _, tab, _ = loaded(master, tmp_path, monkeypatch, immediate, ExitList(None, ()))

    assert tab.day_var.get() == "" and "收盤價表還沒有資料" in tab.status_var.get()


def test_received_day_shows_a_hint_until_refreshed(master, tmp_path: Path, monkeypatch, immediate) -> None:
    window, tab, asked = loaded(master, tmp_path, monkeypatch, immediate)
    tab.day_var.set("")

    assert tab.receive_day(DAY)

    assert tab.day_var.get() == "2026-10-21" and tab.hint.shown
    assert cells(tab) == [] and str(tab.export_button.cget("state")) == "disabled", "上一次讀到的是別天，先清掉"
    assert window.notebook.select() == str(window.daily.frame), "不自動切頁"
    assert asked == [None], "帶入時不自動讀"
    tab.refresh()
    assert not tab.hint.shown and asked[-1] == DAY
    tab.runner.working = True
    assert not tab.receive_day(DAY), "正在讀或匯出時不帶入"


def test_export_writes_what_is_shown(master, tmp_path: Path, monkeypatch, immediate) -> None:
    _, tab, _ = loaded(master, tmp_path, monkeypatch, immediate)
    path = tmp_path / "出場.xlsx"
    offered = {}

    def ask(**options):
        offered.update(options)
        return str(path)

    monkeypatch.setattr(filedialog, "asksaveasfilename", ask)
    tab.export()

    assert offered["initialfile"] == "出場清單_20261021.xlsx"
    rows = list(openpyxl.load_workbook(path).active.iter_rows(values_only=True))
    assert [r[:8] for r in rows[1:]] == [
        ("029199990001", "BARC", "FCN", "KO", dt.datetime(2026, 10, 21), None, "比價", "已鎖住"),
        ("037199990002", "SG", "FCN", "接股", dt.datetime(2026, 10, 21), "AAA UW", "舊清單", "未鎖住"),
        ("037199990003", "SG", "FCN", "KO", dt.datetime(2026, 10, 21), None, "舊清單", "已鎖住"),
    ]
    assert tab.status_var.get() == f"已匯出出場清單 3 檔：{path}"
