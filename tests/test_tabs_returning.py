"""「5 放回」分頁（`sp_workflow.tabs.returning`）：做完後的重點數字（#85）、帶入新的核對結果時收起上一次的結果。"""

from __future__ import annotations

from pathlib import Path

from sp_workflow.returning import NOT_RETURNED, RETURNED, ReturnOutcome, ReturnReceipt
from window_synth import new_window


def test_return_page_shows_key_figures_when_done(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    outcomes = (
        ReturnOutcome("147000000001", RETURNED, RETURNED),
        ReturnOutcome("147000000002", NOT_RETURNED, NOT_RETURNED, "核對結果檔沒有這個商品"),
    )

    window.returning._done(ReturnReceipt(tmp_path / "放回清單.xlsx", outcomes))

    assert window.returning.kpis.values == (
        ("已放回或已在（檔）", 1, False),
        ("含待確認（檔）", 0, False),
        ("不放回（檔）", 1, True),
    )
    assert window.returning.status_label.cget("style") == "Done.TLabel"


def test_a_new_check_result_clears_the_last_return(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    window.returning._done(
        ReturnReceipt(tmp_path / "放回清單.xlsx", (ReturnOutcome("147000000001", RETURNED, RETURNED),))
    )

    window.returning.receive(tmp_path / "核對結果.xlsx", None)

    assert window.returning.kpis.values == () and window.returning.status_label.cget("style") == "Card.TLabel"
