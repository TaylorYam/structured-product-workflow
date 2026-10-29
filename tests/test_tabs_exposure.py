"""「分析」選單的「標的曝險分析…」（`sp_workflow.tabs.exposure`，#345）：列出標的、搜尋、複選、匯出。

背景執行改成馬上做完（`immediate`）；資料庫動作換成記下呼叫、回傳虛構的標的與匯出結果。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path
from tkinter import filedialog, messagebox

import pytest

import sp_workflow.tabs.exposure as screen
from sp_workflow.exposure import DELIVER, WATCH, Choice, ExposureReceipt
from window_synth import new_window

DAY = dt.date(2030, 3, 4)
CHOICES = [
    Choice("AAA UQ", 2, DAY, Decimal("101.5")),
    Choice("AAA UW", 12, DAY, Decimal("101.5")),
    Choice("BBB UN", 3, None, None),
]


@pytest.fixture
def opened(master, tmp_path: Path, monkeypatch, immediate):
    calls: list[tuple] = []

    def export(settings, codes, until, path):
        calls.append((codes, until, path))
        return ExposureReceipt(path, 4, {DELIVER: 3, WATCH: 1})

    monkeypatch.setattr(screen, "export_underlying_exposure", export)
    monkeypatch.setattr(screen, "load_exposure_choices", lambda settings: CHOICES)
    window = new_window(master, tmp_path, background=immediate, logged_in=True)
    window.open_exposure()
    return window, window.exposure, calls


def codes(table) -> list[str]:
    return list(table.get_children())


def test_menu_opens_one_window_listing_choices(opened) -> None:
    window, exposure, _ = opened

    assert codes(exposure.table) == ["AAA UQ", "AAA UW", "BBB UN"]
    assert exposure.table.item("AAA UW", "values") == ("AAA UW", "101.5", "2030-03-04", "12")
    assert exposure.table.item("BBB UN", "values")[1:3] == ("", "")
    assert exposure.status_var.get() == "在庫商品連結的標的 3 個。"
    window.open_exposure()
    assert window.exposure is exposure, "已經開著就拉到前面，不重開"


def test_search_keeps_what_was_chosen(opened) -> None:
    _, exposure, _ = opened
    exposure.table.selection_set(["BBB UN"])
    exposure.update()

    exposure.search_var.set("aaa")
    assert codes(exposure.table) == ["AAA UQ", "AAA UW"], "不分大小寫"
    exposure.table.selection_add(["AAA UW"])
    exposure.update()
    assert exposure.chosen == ["BBB UN", "AAA UW"], "搜尋藏起來的保留"
    assert exposure.chosen_var.get() == "已選：BBB UN、AAA UW"

    exposure.search_var.set("")
    assert set(exposure.table.selection()) == {"BBB UN", "AAA UW"}
    exposure.clear()
    assert exposure.chosen == [] and exposure.table.selection() == ()


def test_export_asks_for_a_file_and_runs_the_analysis(opened, monkeypatch, tmp_path: Path) -> None:
    _, exposure, calls = opened
    infos: list[str] = []
    monkeypatch.setattr(messagebox, "showinfo", lambda title, message, **kw: infos.append(message))
    exposure.export()
    assert infos == ["請先在表上選一檔或多檔標的（按住 Ctrl 複選）。"] and not calls

    exposure.table.selection_set(["AAA UW", "AAA UQ"])
    exposure.update()
    exposure.until_var.set("2030-04-04")
    target = tmp_path / "out" / "曝險.xlsx"
    asked: list[dict] = []
    monkeypatch.setattr(filedialog, "asksaveasfilename", lambda **kw: asked.append(kw) or str(target))

    exposure.export()

    assert calls == [(["AAA UQ", "AAA UW"], dt.date(2030, 4, 4), target)]
    assert asked[0]["initialfile"].startswith("標的曝險分析_AAA_")
    assert exposure.status_var.get() == f"已匯出 4 檔（會接股 3、KI 未破下限 1）：{target}"
    assert exposure.memory.load()[screen.MEMORY_KEY] == str(target.parent), "記住存檔的資料夾"


def test_a_bad_cutoff_date_is_refused(opened, monkeypatch) -> None:
    _, exposure, calls = opened
    exposure.table.selection_set(["AAA UW"])
    exposure.update()
    exposure.until_var.set("明天")
    infos: list[str] = []
    monkeypatch.setattr(messagebox, "showinfo", lambda title, message, **kw: infos.append(message))

    exposure.export()

    assert infos == ["最終比價日截止請填 YYYY-MM-DD（可以點右邊的小月曆）。"] and not calls
