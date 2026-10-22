"""「3 準備」分頁（`sp_workflow.tabs.prepare`）：做完後的重點數字與結果條（#83）。帶入核對頁的銜接在 test_app.py。"""

from __future__ import annotations

from pathlib import Path

from window_synth import new_window, prepared_receipt


def test_prepare_shows_key_figures_when_done(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)

    window.prepare._done(prepared_receipt(tmp_path / "待核對_1"))

    assert window.prepare.kpis.values == (
        ("已準備（檔）", 1, False),
        ("跳過（檔）", 1, True),
        ("Word 轉檔（份）", 0, False),
    )
    assert window.prepare.status_label.cget("style") == "Done.TLabel", "做完了，狀態列變成綠色結果條"
