"""「8 進度」分頁（`sp_workflow.tabs.progress`，#156、#186）：重新整理、三件打勾、篩選、點欄位排序、匯出 Excel。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from tkinter import filedialog

import openpyxl

import sp_workflow.tabs.progress
from sp_workflow.lifecycle import KO
from sp_workflow.local_settings import FolderMemory
from sp_workflow.progress import DATE_COLUMNS, FINISHED, PREPARING, TO_CHECK, ProgressRow
from window_synth import HeldBackground, new_window

T = dt.datetime(2026, 10, 20, 9, 0, tzinfo=dt.UTC)


def row(tdcc: str, days: int | None, progress: str = TO_CHECK, **changes) -> ProgressRow:
    trade_date = None if days is None else dt.date(2026, 10, 22) - dt.timedelta(days=days)
    values = {
        "tdcc": tdcc,
        "issuer": "SG",
        "trade_date": trade_date,
        "days": days,
        "progress": progress,
        "lifecycle": "在庫",
        "dates": (T, *(None,) * (len(DATE_COLUMNS) - 1)),
        "reason": "",
    }
    return ProgressRow(**{**values, **changes})


ROWS = (
    row("037199990001", 3),
    row("037199990002", 30, PREPARING, reason="還沒有商品條件", ready=(True, False, True)),
    row("029199990003", 10, FINISHED, issuer="BARC"),
    row("037199990004", None, lifecycle=KO),
)


def loaded(master, tmp_path: Path, monkeypatch, rows=ROWS):
    window = new_window(master, tmp_path, background=HeldBackground, logged_in=True)
    tab = window.progress
    actions = tab.background.held
    monkeypatch.setattr(sp_workflow.tabs.progress, "load_progress_rows", lambda settings, paths: rows)
    tab.refresh()
    tab.background.run_all()
    return window, tab, actions


def shown(tab) -> list[str]:
    return [tab.table.set(item, "tdcc") for item in tab.table.get_children()]


def test_progress_comes_before_the_exit_list(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)

    assert window.notebook.tab(window.notebook.tabs()[-2], "text") == "8 進度"
    assert window.progress.header.step.get() == "步驟 8／9"


def test_refresh_lists_the_newest_trade_first(master, tmp_path: Path, monkeypatch) -> None:
    _, tab, _ = loaded(master, tmp_path, monkeypatch)

    assert shown(tab) == ["037199990001", "029199990003", "037199990002", "037199990004"]
    stuck = tab.table.get_children()[2]
    assert tab.table.set(stuck, "reason") == "還沒有商品條件" and tab.table.item(stuck, "tags") == ("problem",)
    assert tab.table.set(stuck, "date_0") == f"{T.astimezone():%Y-%m-%d}"
    assert [tab.table.set(stuck, f"ready_{i}") for i in range(3)] == ["✓", "", "✓"], "交易明細、商品條件、股價"
    assert [tab.table.heading(f"ready_{i}", "text") for i in range(3)] == ["交易明細", "商品條件", "股價"]
    assert tab.kpis.values == (("顯示（檔）", 4, False), ("還沒完成（檔）", 3, False))
    assert "共 4 檔，篩選後 4 檔；依交易日由新至舊排序。" in tab.status_var.get()
    assert tuple(tab.issuer_box.cget("values")) == ("全部", "BARC", "SG")
    assert str(tab.export_button.cget("state")) == "normal"


def test_filters_apply_without_reading_the_database_again(master, tmp_path: Path, monkeypatch) -> None:
    _, tab, actions = loaded(master, tmp_path, monkeypatch)

    tab.progress_vars[FINISHED].set(True)
    tab.progress_vars[TO_CHECK].set(True)
    tab.refilter()
    assert shown(tab) == ["037199990001", "029199990003", "037199990004"]

    tab.issuer_var.set("SG")
    tab.refilter()
    assert shown(tab) == ["037199990001", "037199990004"]

    tab.lifecycle_vars[KO].set(True)
    tab.refilter()
    assert shown(tab) == ["037199990004"]

    for var in (*tab.progress_vars.values(), *tab.lifecycle_vars.values()):
        var.set(False)
    tab.issuer_var.set("全部")
    tab.first_var.set("2026-10-01")
    tab.last_var.set("2026-10-20")
    tab.refilter()
    assert shown(tab) == ["037199990001", "029199990003"], "交易日 10/19、10/12 在區間內；沒有交易日的不列"
    assert actions == [], "篩選不再讀資料庫"


def test_pricing_status_is_shown_filtered_and_marked(master, tmp_path: Path, monkeypatch) -> None:
    """比價狀態（#170）：三欄、可篩選，等股價與待確認用問題色。"""
    rows = (
        row("037199990001", 3, pricing="等股價", pricing_reason="UL_1 AAA UW：2026-10-21 沒有收盤價",
            observed_through=dt.date(2026, 10, 20), pricing_unlocked=True),
        row("037199990002", 5, pricing="在庫", observed_through=dt.date(2026, 10, 21)),
        row("037199990003", 7),
    )  # fmt: skip
    _, tab, _ = loaded(master, tmp_path, monkeypatch, rows)

    cells = {
        tab.table.set(item, "tdcc"): (
            tab.table.set(item, "pricing"),
            tab.table.set(item, "observed_through"),
            tab.table.set(item, "pricing_reason"),
            tab.table.item(item, "tags"),
        )
        for item in tab.table.get_children()
    }
    assert cells == {
        "037199990001": ("等股價（未鎖住）", "2026-10-20", "UL_1 AAA UW：2026-10-21 沒有收盤價", ("problem",)),
        "037199990002": ("在庫", "2026-10-21", "", ""),
        "037199990003": ("", "", "", ""),
    }

    tab.pricing_vars["等股價"].set(True)
    tab.refilter()
    assert shown(tab) == ["037199990001"]


def test_copy_tdccs_to_confirm_puts_only_their_tdccs_on_the_clipboard(master, tmp_path: Path, monkeypatch) -> None:
    """複製待確認 TDCC（#289）：只放畫面上比價待確認的 TDCC、一行一筆，貼到「3 準備」每筆都認得。"""
    rows = (
        row("037001023110", 3, pricing="待確認", pricing_reason="沒有比價日"),
        row("082001154182", 5, pricing="在庫"),
        row("092001743359", 7, pricing="待確認", pricing_reason="沒有比價日", pricing_unlocked=True),
        row("147000030313", 9, pricing="等股價", issuer="BNP"),
    )
    window, tab, _ = loaded(master, tmp_path, monkeypatch, rows)
    button = tab.copy_to_confirm_button

    button.invoke()

    assert master.clipboard_get() == "037001023110\n092001743359", "不含表頭與其他欄，照畫面的順序"
    assert button.cget("text") == "已複製 2 筆"
    window.prepare.tdcc_text.insert("1.0", master.clipboard_get())
    master.update()
    assert window.prepare.count_var.get() == "已貼 2 筆"

    tab.issuer_var.set("BNP")
    tab.refilter()
    master.clipboard_clear()
    master.clipboard_append("原本的內容")
    button.invoke()
    assert master.clipboard_get() == "原本的內容", "畫面上沒有待確認的不動剪貼簿"
    assert button.cget("text") == "表上沒有待確認"

    tab.table_area.button.invoke()
    assert master.clipboard_get().split("\n")[0].startswith("TDCC\t上手\t交易日"), "全部複製照舊含表頭"


def test_a_bad_date_filter_is_ignored_and_explained(master, tmp_path: Path, monkeypatch) -> None:
    _, tab, _ = loaded(master, tmp_path, monkeypatch)

    tab.first_var.set("10/1")
    tab.refilter()

    assert len(shown(tab)) == 4
    assert "交易日起「10/1」不是 YYYY-MM-DD，這一邊先不限。" in tab.status_var.get()


def test_clicking_a_heading_sorts_and_clicking_again_reverses(master, tmp_path: Path, monkeypatch) -> None:
    _, tab, _ = loaded(master, tmp_path, monkeypatch)

    tab.sort_by("trade_date")
    assert shown(tab) == ["037199990002", "029199990003", "037199990001", "037199990004"], "空白一律放最後"
    assert "依交易日由小到大排序" in tab.status_var.get()

    tab.sort_by("days")
    assert shown(tab) == ["037199990002", "029199990003", "037199990001", "037199990004"], "換欄第一次由大到小"

    tab.sort_by("tdcc")
    assert shown(tab) == ["029199990003", "037199990001", "037199990002", "037199990004"]
    tab.sort_by("tdcc")
    assert shown(tab) == ["037199990004", "037199990002", "037199990001", "029199990003"]

    tab.sort_by("progress")
    assert shown(tab)[0] == "037199990002", "作業進度依五個階段的順序"


def test_export_writes_what_is_shown(master, tmp_path: Path, monkeypatch) -> None:
    _, tab, actions = loaded(master, tmp_path, monkeypatch)
    path = tmp_path / "out" / "進度.xlsx"
    path.parent.mkdir()
    monkeypatch.setattr(filedialog, "asksaveasfilename", lambda **k: str(path))
    tab.progress_vars[PREPARING].set(True)
    tab.refilter()

    tab.export()
    action, done = actions.pop()
    done(action())

    sheet = openpyxl.load_workbook(path).active
    assert [r[0] for r in sheet.iter_rows(min_row=2, values_only=True)] == ["037199990002"]
    assert "已匯出作業進度 1 檔" in tab.status_var.get()
    assert FolderMemory.under(tmp_path).load()["progress_export"] == str(path.parent)


def test_a_refresh_with_no_products_clears_the_table(master, tmp_path: Path, monkeypatch) -> None:
    _, tab, actions = loaded(master, tmp_path, monkeypatch)
    monkeypatch.setattr(sp_workflow.tabs.progress, "load_progress_rows", lambda settings, paths: ())

    tab.refresh()
    action, done = actions.pop()
    done(action())

    assert shown(tab) == [] and tab.shown == []
    assert "共 0 檔，篩選後 0 檔" in tab.status_var.get()
    assert str(tab.export_button.cget("state")) == "disabled", "沒有列時不能匯出舊資料"


def test_nothing_to_export_before_a_refresh(master, tmp_path: Path, monkeypatch) -> None:
    window = new_window(master, tmp_path)
    monkeypatch.setattr(filedialog, "asksaveasfilename", lambda **k: (_ for _ in ()).throw(AssertionError))

    window.progress.export()

    assert str(window.progress.export_button.cget("state")) == "disabled"
