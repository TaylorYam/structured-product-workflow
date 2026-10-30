"""「分析」選單的「重災區掃描…」（`sp_workflow.tabs.hotspot`，#362）：截止日、匯出、狀態列。

背景執行改成馬上做完（`immediate`）；資料庫動作換成記下呼叫、回傳虛構的匯出結果。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from tkinter import filedialog, messagebox

import pytest

import sp_workflow.tabs.hotspot as screen
from sp_workflow.exposure import HotspotReceipt
from sp_workflow.tabs.exposure import MEMORY_KEY
from window_synth import new_window


@pytest.fixture
def opened(master, tmp_path: Path, monkeypatch, immediate):
    calls: list[tuple] = []

    def export(settings, until, path):
        calls.append((until, path))
        return HotspotReceipt(path, 155, {"跌破K": 31, "跌破KI": 6})

    monkeypatch.setattr(screen, "export_hotspot_scan", export)
    window = new_window(master, tmp_path, background=immediate, logged_in=True)
    window.open_hotspots()
    return window, window.hotspots, calls


def test_menu_opens_one_window(opened) -> None:
    window, hotspots, _ = opened

    assert dt.date.fromisoformat(hotspots.until_var.get()) > dt.date.today(), "預設今天起一個月"
    window.open_hotspots()
    assert window.hotspots is hotspots, "已經開著就拉到前面，不重開"


def test_export_asks_for_a_file_and_scans(opened, monkeypatch, tmp_path: Path) -> None:
    _, hotspots, calls = opened
    hotspots.until_var.set("2030-04-04")
    target = tmp_path / "out" / "重災區.xlsx"
    asked: list[dict] = []
    monkeypatch.setattr(filedialog, "asksaveasfilename", lambda **kw: asked.append(kw) or str(target))

    hotspots.export()

    assert calls == [(dt.date(2030, 4, 4), target)]
    assert asked[0]["initialfile"].startswith("重災區掃描_")
    assert hotspots.status_var.get() == f"已匯出 37 檔（跌破K 31、跌破KI 6；截止日內在庫 155 檔）：{target}"
    assert hotspots.memory.load()[MEMORY_KEY] == str(target.parent), "跟標的曝險分析記同一個資料夾"


def test_cancelled_save_does_nothing(opened, monkeypatch) -> None:
    _, hotspots, calls = opened
    monkeypatch.setattr(filedialog, "asksaveasfilename", lambda **kw: "")

    hotspots.export()

    assert not calls


def test_a_bad_cutoff_date_is_refused(opened, monkeypatch) -> None:
    _, hotspots, calls = opened
    hotspots.until_var.set("明天")
    infos: list[str] = []
    monkeypatch.setattr(messagebox, "showinfo", lambda title, message, **kw: infos.append(message))

    hotspots.export()

    assert infos == ["最終比價日截止請填 YYYY-MM-DD（可以點右邊的小月曆）。"] and not calls


def test_closing_the_main_window_closes_it(opened) -> None:
    window, hotspots, _ = opened

    window.close()

    assert not hotspots.winfo_exists()
