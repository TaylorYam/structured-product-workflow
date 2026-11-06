"""「分析」選單的「盤勢速報…」（`sp_workflow.tabs.market_brief`，#467）：列出標的、預設選前 5、加選、匯出。

背景執行改成馬上做完（`immediate`）；資料庫動作換成記下呼叫、回傳虛構的盤勢資料與匯出結果。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path
from tkinter import filedialog

import pytest

import sp_workflow.tabs.market_brief as screen
from sp_workflow.market_brief import Brief, BriefReceipt, Quote, TickerRow
from sp_workflow.tabs.exposure import MEMORY_KEY
from window_synth import new_window

DAY = dt.date(2030, 3, 4)
TICKERS = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG"]
ROWS = tuple(
    TickerRow(t, (f"{t} UW",), Quote(DAY, Decimal(90), Decimal(100)), 10 - i, Decimal((7 - i) * 1_000_000))
    for i, t in enumerate(TICKERS)
)
BRIEF = Brief(DAY, (), ROWS, {}, frozenset({"ZAR"}))


@pytest.fixture
def opened(master, tmp_path: Path, monkeypatch, immediate):
    calls: list[tuple] = []

    def export(settings, tickers, path):
        calls.append((tickers, path))
        return BriefReceipt(path, 120, 30, tuple(tickers), frozenset())

    monkeypatch.setattr(screen, "export_market_brief", export)
    monkeypatch.setattr(screen, "load_market_brief", lambda settings: BRIEF)
    window = new_window(master, tmp_path, background=immediate, logged_in=True)
    window.open_market_brief()
    return window, window.market_brief, calls


def test_menu_opens_one_window_with_the_top_five_chosen(opened) -> None:
    window, brief, _ = opened

    assert list(brief.table.get_children()) == TICKERS
    assert brief.table.item("AAA", "values") == ("AAA", "90", "-10.0%", "10", "7.0")
    assert brief.chosen == TICKERS[:5]
    assert list(brief.table.selection()) == TICKERS[:5]
    assert "缺 ZAR 對美元的匯率" in brief.status_var.get()
    window.open_market_brief()
    assert window.market_brief is brief, "已經開著就拉到前面，不重開"


def test_search_keeps_what_was_chosen_and_adds_more(opened) -> None:
    _, brief, _ = opened
    brief.search_var.set("GGG")
    brief.table.selection_set(["GGG"])
    brief.update()
    brief.search_var.set("")

    assert brief.chosen == [*TICKERS[:5], "GGG"]
    assert brief.chosen_var.get() == f"畫熱圖：{'、'.join([*TICKERS[:5], 'GGG'])}"


def test_export_passes_the_chosen_tickers(opened, monkeypatch, tmp_path: Path) -> None:
    _, brief, calls = opened
    brief.clear()
    target = tmp_path / "out" / "盤勢速報.xlsx"
    asked: list[dict] = []
    monkeypatch.setattr(filedialog, "asksaveasfilename", lambda **kw: asked.append(kw) or str(target))

    brief.export()

    assert calls == [([], target)], "沒選熱圖也能匯出總覽與重災區"
    assert asked[0]["initialfile"].startswith("盤勢速報_")
    assert brief.status_var.get() == f"已匯出（在庫 120 檔、跌破K 30 檔；熱圖 無）：{target}"
    assert brief.memory.load()[MEMORY_KEY] == str(target.parent), "跟標的曝險分析記同一個資料夾"


def test_cancelled_save_does_nothing(opened, monkeypatch) -> None:
    _, brief, calls = opened
    monkeypatch.setattr(filedialog, "asksaveasfilename", lambda **kw: "")

    brief.export()

    assert not calls


def test_refresh_keeps_the_current_choice(opened) -> None:
    _, brief, _ = opened
    brief.clear()
    brief.table.selection_set(["CCC"])
    brief.update()

    brief.refresh()

    assert brief.chosen == ["CCC"], "重新整理不把選擇改回預設"


def test_closing_the_main_window_closes_it(opened) -> None:
    window, brief, _ = opened

    window.close()

    assert not brief.winfo_exists()
