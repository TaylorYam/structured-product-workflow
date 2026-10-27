"""「維護」選單的「拆股登記…」（`sp_workflow.tabs.splits`，#270）：列出、新增、刪除登記，存檔後顯示比價結果。

背景執行改成馬上做完（`immediate`）；資料庫動作換成記下呼叫、回傳虛構的登記與比價結果。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from tkinter import messagebox

import pytest

import sp_workflow.tabs.splits as screen
from sp_workflow.observation import EventChange, ObservationReceipt
from sp_workflow.stock_splits import Split
from sp_workflow.tabs.splits import SplitForm
from sp_workflow.window_actions import Observation, SplitResult
from window_synth import new_window

DAY = dt.date(2030, 3, 4)
AAA = Split("AAA UW", DAY, 1, 100, "公告", "tester", dt.datetime(2030, 3, 5, 9, 30))
BBB = Split("BBB UN", DAY, 1, 1)
CHANGE = EventChange("147199990001", (("接股", "2030-06-03", "AAA UW"),), (("到期還本", "2030-06-03", None),))
RESULT = SplitResult((AAA, BBB), Observation(ObservationReceipt(DAY, (), (CHANGE,))))


@pytest.fixture
def opened(master, tmp_path: Path, monkeypatch, immediate):
    calls: list[tuple] = []
    for name in ("add_stock_split", "delete_stock_split"):
        monkeypatch.setattr(screen, name, lambda settings, *args, name=name: calls.append((name, *args)) or RESULT)
    monkeypatch.setattr(screen, "load_split_rows", lambda settings: (AAA, BBB))
    window = new_window(master, tmp_path, background=immediate, logged_in=True)
    window.open_splits()
    return window, window.splits, calls


def values(table) -> list[tuple]:
    return [tuple(table.item(item, "values")) for item in table.get_children()]


def test_menu_opens_one_window_listing_splits(opened) -> None:
    window, splits, _ = opened

    assert values(splits.table) == [
        ("AAA UW", "2030-03-04", "1 拆 100", "公告", "tester", "2030-03-05 09:30"),
        ("BBB UN", "2030-03-04", "確認不是拆股", "", "", ""),
    ]
    assert splits.status_var.get() == "拆股登記 2 筆。"
    window.open_splits()
    assert window.splits is splits, "已經開著就拉到前面，不重開"


def test_add_and_delete_save_and_show_the_observation(opened, monkeypatch) -> None:
    window, splits, calls = opened
    added = Split("CCC UN", DAY, 10, 1)
    monkeypatch.setattr(screen, "ask_split", lambda parent: added)

    splits.add()

    assert calls[-1] == ("add_stock_split", added, window.session.paths)
    status = splits.status_var.get()
    assert status.startswith("已存：新增 CCC UN 2030-03-04 10 合 1。比價：比到 2030-03-04")
    assert status.endswith("147199990001 存續事件：接股 AAA UW 2030-06-03 → 到期還本 2030-06-03")

    infos, asked = [], []
    monkeypatch.setattr(messagebox, "showinfo", lambda title, message, **kw: infos.append(message))
    splits.table.selection_remove(*splits.table.selection())
    splits.delete()
    assert infos == ["請先在表上點一筆登記。"]

    monkeypatch.setattr(messagebox, "askyesno", lambda title, message, **kw: asked.append(message) or True)
    splits.table.selection_set("s0")
    splits.delete()
    assert calls[-1] == ("delete_stock_split", AAA, window.session.paths)
    assert asked == ["刪除 AAA UW 在 2030-03-04 的拆股登記（1 拆 100）？刪除後會重新比價。"]


def test_split_form_reads_and_checks_the_input(master) -> None:
    form = SplitForm(master)
    form.code_var.set("AAA UW Equity")
    form.day_var.set("2030-03-04")
    form.after_var.set("100")
    form.confirm()
    assert form.result == Split("AAA UW", DAY, 1, 100)

    form = SplitForm(master)
    form.code_var.set("AAA UW")
    form.day_var.set("2030/03/04")
    form.after_var.set("2")
    form.confirm()
    assert form.result is None and "YYYY-MM-DD" in form.error_var.get()
    form.day_var.set("2030-03-04")
    form.after_var.set("1.5")
    form.confirm()
    assert form.result is None and "拆股後股數請填正整數" in form.error_var.get()
    form.destroy()
