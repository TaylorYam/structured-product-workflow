"""「3 準備」分頁（`sp_workflow.tabs.prepare`）：做完後的重點數字與結果條（#83）、步驟紀錄沒寫進去的提示（#151）。帶入核對頁的銜接在 test_app.py。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from sp_workflow.step_records import Recorded
from window_synth import new_window, prepared_result


def test_prepare_shows_key_figures_when_done(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)

    window.prepare._done(prepared_result(tmp_path / "待核對_1"))

    assert window.prepare.kpis.values == (
        ("已準備（檔）", 1, False),
        ("跳過（檔）", 1, True),
        ("Word 轉檔（份）", 0, False),
    )
    assert window.prepare.status_label.cget("style") == "Done.TLabel", "做完了，狀態列變成綠色結果條"


def test_prepare_says_when_step_records_were_not_written(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    result = prepared_result(tmp_path / "待核對_1")

    window.prepare._done(replace(result, recorded=Recorded(error="連不上資料庫 spw")))

    status = window.prepare.status_var.get()
    assert "步驟紀錄沒有寫進資料庫：連不上資料庫 spw（清單照常產出）" in status
    assert window.prepare.kpis.values[0] == ("已準備（檔）", 1, False), "準備結果照常顯示"


def test_prepare_without_database_says_no_step_records(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    result = prepared_result(tmp_path / "待核對_1")

    window.prepare._done(replace(result, recorded=Recorded(skipped=True)))

    assert "沒有登入資料庫（沒有連線設定或取消登入），這次沒有留步驟紀錄" in window.prepare.status_var.get()
