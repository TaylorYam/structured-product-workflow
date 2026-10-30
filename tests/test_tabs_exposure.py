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
    Choice("AAA", ("AAA UW", "AAA UQ"), 13, DAY, Decimal("101.5")),
    Choice("AAAB", ("AAAB UN",), 1, DAY, Decimal("7")),
    Choice("BBB", ("BBB UN",), 3, None, None),
]


@pytest.fixture
def opened(master, tmp_path: Path, monkeypatch, immediate):
    calls: list[tuple] = []

    def export(settings, codes, until, path, trial=None):
        calls.append((codes, until, path, trial))
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

    assert codes(exposure.table) == ["AAA", "AAAB", "BBB"]
    assert exposure.table.item("AAA", "values") == ("AAA", "AAA UW、AAA UQ", "101.5", "2030-03-04", "13")
    assert exposure.table.item("BBB", "values")[2:4] == ("", "")
    assert exposure.status_var.get() == "在庫商品連結的標的 3 個。"
    window.open_exposure()
    assert window.exposure is exposure, "已經開著就拉到前面，不重開"


def test_search_keeps_what_was_chosen(opened) -> None:
    _, exposure, _ = opened
    exposure.table.selection_set(["BBB"])
    exposure.update()

    exposure.search_var.set("aaa")
    assert codes(exposure.table) == ["AAA", "AAAB"], "只打代號就找得到，不分大小寫"
    exposure.table.selection_add(["AAA"])
    exposure.update()
    assert exposure.chosen == ["BBB", "AAA"], "搜尋藏起來的保留"
    assert exposure.chosen_var.get() == "已選：BBB、AAA"

    exposure.search_var.set("uq")
    assert codes(exposure.table) == ["AAA"], "交易所代碼也搜得到"
    exposure.search_var.set("")
    assert set(exposure.table.selection()) == {"BBB", "AAA"}
    exposure.clear()
    assert exposure.chosen == [] and exposure.table.selection() == ()


def test_export_asks_for_a_file_and_runs_the_analysis(opened, monkeypatch, tmp_path: Path) -> None:
    _, exposure, calls = opened
    infos: list[str] = []
    monkeypatch.setattr(messagebox, "showinfo", lambda title, message, **kw: infos.append(message))
    exposure.export()
    assert infos == ["請先在表上選一檔或多檔標的（按住 Ctrl 複選）。"] and not calls

    exposure.table.selection_set(["AAA", "BBB"])
    exposure.update()
    exposure.until_var.set("2030-04-04")
    target = tmp_path / "out" / "曝險.xlsx"
    asked: list[dict] = []
    monkeypatch.setattr(filedialog, "asksaveasfilename", lambda **kw: asked.append(kw) or str(target))

    exposure.export()

    assert calls == [(["AAA UW", "AAA UQ", "BBB UN"], dt.date(2030, 4, 4), target, {})], "代號展開成全部交易所代碼"
    assert asked[0]["initialfile"].startswith("標的曝險分析_AAA_BBB_")
    assert exposure.status_var.get() == f"已匯出 4 檔（會接股 3、KI 未破下限 1）：{target}"
    assert exposure.memory.load()[screen.MEMORY_KEY] == str(target.parent), "記住存檔的資料夾"


def test_a_bad_cutoff_date_is_refused(opened, monkeypatch) -> None:
    _, exposure, calls = opened
    exposure.table.selection_set(["AAA"])
    exposure.update()
    exposure.until_var.set("明天")
    infos: list[str] = []
    monkeypatch.setattr(messagebox, "showinfo", lambda title, message, **kw: infos.append(message))

    exposure.export()

    assert infos == ["最終比價日截止請填 YYYY-MM-DD（可以點右邊的小月曆）。"] and not calls


def test_text_wraps_beside_its_button(opened) -> None:
    _, exposure, _ = opened
    exposure.chosen_var.set("已選：" + "、".join(f"T{n:03d}" for n in range(80)))  # 選很多檔
    exposure.status_var.set("已匯出 " + "很長的路徑\\" * 40)
    exposure.update()

    for label, button in ((exposure.intro, exposure.refresh_button), (exposure.chosen_label, exposure.clear_button)):
        assert button.winfo_width() >= button.winfo_reqwidth(), "按鈕不能被文字擠扁（#349）"
        assert label.winfo_x() + label.winfo_reqwidth() <= button.winfo_x(), "文字照剩下的寬度換行，不被裁掉"


def test_trial_prices_apply_to_every_exchange_code(opened, monkeypatch, tmp_path: Path) -> None:
    _, exposure, calls = opened
    assert not exposure.trial_frame.winfo_children(), "還沒選標的時不顯示試算價"
    exposure.table.selection_set(["AAA", "BBB"])
    exposure.update()
    entries = [w for w in exposure.trial_frame.winfo_children() if w.winfo_class() == "TEntry"]
    assert len(entries) == 2, "已選的每個代號一格"
    infos: list[str] = []
    monkeypatch.setattr(messagebox, "showinfo", lambda title, message, **kw: infos.append(message))
    target = tmp_path / "曝險.xlsx"
    asked: list[dict] = []
    monkeypatch.setattr(filedialog, "asksaveasfilename", lambda **kw: asked.append(kw) or str(target))

    for bad in ("abc", "0", "-5", "NaN"):
        exposure.trial_vars["AAA"].set(bad)
        exposure.export()
    assert infos == ["AAA 的試算價請填大於 0 的數字（空白用最新收盤價）。"] * 4 and not calls and not asked

    exposure.trial_vars["AAA"].set(" 1,234.5 ")
    exposure.table.selection_set(["AAA"])  # 取消 BBB 再選回來，已填的保留
    exposure.update()
    exposure.table.selection_set(["AAA", "BBB"])
    exposure.update()
    assert exposure.trial_vars["AAA"].get() == " 1,234.5 "
    exposure.export()

    price = Decimal("1234.5")
    assert calls[0][0] == ["AAA UW", "AAA UQ", "BBB UN"]
    assert calls[0][3] == {"AAA UW": price, "AAA UQ": price}, "BBB 空白，用最新收盤價"
    assert "_試算_" in asked[0]["initialfile"]
    assert exposure.status_var.get() == f"已匯出 4 檔（會接股 3、KI 未破下限 1；試算價 AAA 1234.5）：{target}"
