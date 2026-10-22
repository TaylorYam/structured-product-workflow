"""「6 寄信草稿」分頁（`sp_workflow.tabs.mail`）：做完後的重點數字（#85）。根目錄與記住位置的測試在 test_app.py。"""

from __future__ import annotations

from pathlib import Path

from sp_workflow.mail_drafts import DRAFTED, NOT_NEEDED, DraftOutcome, DraftReceipt
from window_synth import new_window


def test_mail_page_shows_key_figures_when_done(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    outcomes = (DraftOutcome("147000000001", DRAFTED), DraftOutcome("147000000002", NOT_NEEDED))

    window.mail._done(DraftReceipt(tmp_path / "寄信草稿清單.xlsx", outcomes, last_import=None))

    assert window.mail.kpis.values == (("已建草稿（檔）", 1, False), ("沒有建立（檔）", 1, False)), "不需寄送不算問題"
    assert window.mail.status_label.cget("style") == "Done.TLabel"
