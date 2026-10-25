"""「6 寄信草稿」分頁（`sp_workflow.tabs.mail`）：做完後的重點數字（#85）、步驟紀錄沒寫進去的提示（#151）。根目錄與記住位置的測試在 test_app.py。"""

from __future__ import annotations

from pathlib import Path

from sp_workflow.mail_drafts import DRAFTED, NOT_NEEDED, DraftOutcome, DraftReceipt
from sp_workflow.step_records import Recorded
from sp_workflow.window_actions import DraftResult
from window_synth import new_window


def test_mail_page_shows_key_figures_when_done(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    outcomes = (DraftOutcome("147000000001", DRAFTED), DraftOutcome("147000000002", NOT_NEEDED))

    window.mail._done(DraftResult(DraftReceipt(tmp_path / "寄信草稿清單.xlsx", outcomes, last_import=None)))

    assert window.mail.kpis.values == (("已建草稿（檔）", 1, False), ("沒有建立（檔）", 1, False)), "不需寄送不算問題"
    assert window.mail.status_label.cget("style") == "Done.TLabel"


def test_mail_page_says_when_step_records_were_not_written(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    receipt = DraftReceipt(tmp_path / "寄信草稿清單.xlsx", (DraftOutcome("147000000001", DRAFTED),), last_import=None)

    window.mail._done(DraftResult(receipt, Recorded(error="寫入失敗")))

    assert "步驟紀錄沒有寫進資料庫：寫入失敗（清單照常產出）" in window.mail.status_var.get()


def test_drafts_whose_risk_level_needs_confirming_are_marked(master, tmp_path: Path) -> None:
    """#239：建了草稿、但風險等級待確認的那一列標出來；不算「沒有建立」。"""
    window = new_window(master, tmp_path)
    note = "資料庫還沒有風險等級，草稿寫「待確認」，寄出前請補上風險等級"
    outcomes = (
        DraftOutcome("147000000001", DRAFTED),
        DraftOutcome("147000000002", DRAFTED, reason=note, to_confirm=True),
    )

    window.mail._done(DraftResult(DraftReceipt(tmp_path / "寄信草稿清單.xlsx", outcomes, last_import=None)))

    table = window.mail.table
    assert [table.item(iid, "tags") for iid in table.get_children()] == ["", ("problem",)]
    assert window.mail.kpis.values == (("已建草稿（檔）", 2, False), ("沒有建立（檔）", 0, False))
