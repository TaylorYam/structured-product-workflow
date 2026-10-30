"""「5 放回」分頁（`sp_workflow.tabs.returning`）：做完後的重點數字（#85）、帶入新的核對結果時收起上一次的結果、步驟紀錄沒寫進去的提示（#151）、放回完成帶入寄信草稿（#281）、回填通用（#364）。"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

import pytest

import sp_workflow.tabs.returning
from sp_workflow.generic_backfill import NOT_FOUND, GenericLine, GenericReceipt
from sp_workflow.mail_drafts import DRAFTED, DraftOutcome, DraftReceipt
from sp_workflow.returning import (
    ALREADY_THERE,
    NOT_CHECKED_REASON,
    NOT_RETURNED,
    PENDING_RETURNED,
    RETURNED,
    ReturnOutcome,
    ReturnReceipt,
)
from sp_workflow.step_records import Recorded
from sp_workflow.window_actions import DraftResult, ReturnResult
from window_synth import new_window


def test_return_page_shows_key_figures_when_done(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    outcomes = (
        ReturnOutcome("147000000001", RETURNED, RETURNED),
        ReturnOutcome("147000000002", NOT_RETURNED, NOT_RETURNED, "核對結果檔沒有這個商品"),
    )

    window.returning._done(ReturnResult(ReturnReceipt(tmp_path / "放回清單.xlsx", outcomes)))

    assert window.returning.kpis.values == (
        ("已放回或已在（檔）", 1, False),
        ("含待確認（檔）", 0, False),
        ("不放回（檔）", 1, True),
    )
    assert window.returning.status_label.cget("style") == "Done.TLabel"


def test_a_new_check_result_clears_the_last_return(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    window.returning._done(
        ReturnResult(ReturnReceipt(tmp_path / "放回清單.xlsx", (ReturnOutcome("147000000001", RETURNED, RETURNED),)))
    )

    window.returning.receive(tmp_path / "核對結果.xlsx", None)

    assert window.returning.kpis.values == () and window.returning.status_label.cget("style") == "Card.TLabel"


def test_return_page_says_when_step_records_were_not_written(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    receipt = ReturnReceipt(tmp_path / "放回清單.xlsx", (ReturnOutcome("147000000001", RETURNED, RETURNED),))

    window.returning._done(ReturnResult(receipt, Recorded(error="資料表還沒建立")))

    assert "步驟紀錄沒有寫進資料庫：資料表還沒建立（清單照常產出）" in window.returning.status_var.get()


# ---------- 放回完成，帶入寄信草稿（#281） ----------


def pasted(tab) -> str:
    return tab.tdcc_text.get("1.0", tk.END).strip()


def returned(*outcomes: ReturnOutcome) -> ReturnResult:
    return ReturnResult(ReturnReceipt(Path("D:/out/放回清單.xlsx"), outcomes))


PASSED = ReturnOutcome("147000000001", RETURNED, RETURNED, "商品資料夾還有舊的待確認檔")  # 只提醒，仍算通過
ALREADY = ReturnOutcome("147000000002", ALREADY_THERE, RETURNED)
PENDING = ReturnOutcome("147000000003", RETURNED, PENDING_RETURNED)
BLOCKED = ReturnOutcome("147000000004", NOT_RETURNED, NOT_RETURNED, NOT_CHECKED_REASON)


def test_return_hands_passed_tdccs_to_the_mail_tab(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)

    window.returning._done(returned(PENDING, PASSED, BLOCKED, ALREADY))
    master.update()  # 帶入時改了 TDCC 欄位，處理完那次 <<Modified>> 提示條仍在

    assert pasted(window.mail) == "147000000001\n147000000002", "待確認、不放回的不帶，照結果表順序"
    assert window.mail.hint.text.get() == (
        "已帶入「5 放回」通過的 TDCC 2 筆（TS、IIS 都用標準檔名放回或已在），選好結果清單位置後按「建立草稿」。"
    )
    assert window.returning.status_var.get().endswith("已把通過的 TDCC 2 筆帶到「6 寄信草稿」。")
    assert window.notebook.select() != str(window.mail.frame), "不自動切頁"
    assert window.returning.next_button.winfo_manager() == "pack"

    window.returning.next_button.invoke()

    assert window.notebook.select() == str(window.mail.frame)


def test_nothing_is_handed_to_the_mail_tab_without_a_passed_tdcc(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    window.mail.tdcc_text.insert("1.0", "147000000009")

    window.returning._done(returned(PENDING, BLOCKED))

    assert window.returning.status_var.get().endswith(
        "沒有通過的 TDCC（TS、IIS 都用標準檔名放回或已在），寄信草稿頁沒有帶入。"
    )
    assert pasted(window.mail) == "147000000009" and not window.mail.hint.shown
    assert window.returning.next_button.winfo_manager() == ""


@pytest.mark.parametrize("answer", [False, True])
def test_different_tdccs_on_the_mail_tab_are_replaced_only_if_the_operator_agrees(
    master, tmp_path: Path, monkeypatch, answer
) -> None:
    window = new_window(master, tmp_path)
    window.mail.tdcc_text.insert("1.0", "147000000009")
    asked = []
    monkeypatch.setattr(
        messagebox, "askyesno", lambda *a, **k: asked.append((a[1], window.returning.kpis.values)) or answer
    )

    window.returning._done(returned(PASSED))

    assert len(asked) == 1 and asked[0][0].startswith("「6 寄信草稿」已經有 1 筆 TDCC")
    assert asked[0][1][0] == ("已放回或已在（檔）", 1, False), "問的時候放回頁已經顯示這次的結果（#286）"
    assert pasted(window.mail) == ("147000000001" if answer else "147000000009")
    assert window.returning.next_button.winfo_manager() == ("pack" if answer else ""), "沒帶入就沒有下一步"
    if not answer:
        assert window.returning.status_var.get().endswith("寄信草稿頁沒有帶入（正在處理，或保留原本的 TDCC）。")


def test_the_same_tdccs_are_handed_to_the_mail_tab_again_without_asking(master, tmp_path, monkeypatch) -> None:
    window = new_window(master, tmp_path)
    window.returning._done(returned(PASSED, ALREADY))
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: pytest.fail("一樣的 TDCC 不用問"))

    window.returning._done(returned(ALREADY, PASSED))

    assert window.mail.hint.shown


def test_editing_the_tdccs_handed_to_the_mail_tab_hides_the_hint(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    window.returning._done(returned(PASSED))
    master.update()

    window.mail.tdcc_text.insert(tk.END, "\n147000000009")
    master.update()

    assert not window.mail.hint.shown


def test_a_new_check_result_withdraws_the_next_step_to_mail(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    window.returning._done(returned(PASSED))

    window.returning.receive(tmp_path / "核對結果.xlsx", None)

    assert window.returning.next_button.winfo_manager() == ""


def test_returning_again_withdraws_the_next_step_to_mail(master, tmp_path: Path, monkeypatch, immediate) -> None:
    window = new_window(master, tmp_path, background=immediate)
    window.returning._done(returned(PASSED))
    assert window.returning.next_button.winfo_manager() == "pack"
    window.returning.batch_var.set(str(tmp_path / "待核對_1"))
    window.returning.result_var.set(str(tmp_path / "核對結果.xlsx"))
    monkeypatch.setattr(sp_workflow.tabs.returning, "run_return", lambda *a: returned(BLOCKED))

    window.returning.start()

    assert window.returning.next_button.winfo_manager() == "", "這次沒有通過的，上一次的下一步收起來"


def test_handing_over_clears_the_last_mail_results(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    receipt = DraftReceipt(tmp_path / "寄信草稿清單.xlsx", (DraftOutcome("147000000001", DRAFTED),), last_import=None)
    window.mail._done(DraftResult(receipt))

    window.returning._done(returned(PASSED))

    mail = window.mail
    assert mail.table.get_children() == () and mail.kpis.values == ()
    assert mail.status_var.get() == mail.READY_TEXT, "狀態列不留上一次的完成訊息（#286）"


def test_generic_backfill_asks_for_files_and_shows_its_own_columns(
    master, tmp_path: Path, monkeypatch, immediate
) -> None:
    window = new_window(master, tmp_path, background=immediate, logged_in=True)
    page = window.returning
    page._done(returned(PASSED))
    source, target = tmp_path / "來源" / "ELN通用.xlsx", tmp_path / "來源" / "ELN通用_回填.xlsx"
    saving: list[dict] = []
    monkeypatch.setattr(filedialog, "askopenfilename", lambda **kw: str(source))
    monkeypatch.setattr(filedialog, "asksaveasfilename", lambda **kw: saving.append(kw) or str(target))
    calls: list[tuple] = []

    def fill(settings, src, out, paths):
        calls.append((src, out))
        lines = (
            GenericLine("147000000001", "FCN", 13, ()),
            GenericLine("147000000002", "BEN", 0, (NOT_FOUND,)),
            GenericLine("147000000003", "FCN", 11, ("ISIN Code 表上是 X、資料庫是 Y，保留原值",)),
        )
        return GenericReceipt(out, lines)

    monkeypatch.setattr(sp_workflow.tabs.returning, "fill_generic", fill)

    page.start_generic()

    assert calls == [(source, target)]
    assert saving[0]["initialdir"] == str(source.parent) and saving[0]["initialfile"] == "ELN通用_回填.xlsx"
    assert [page.table.heading(c, "text") for c in page.table["columns"]] == ["TDCC", "Product", "回填（格）", "原因"]
    assert page.copier.rows()[1] == ["147000000002", "BEN", "0", NOT_FOUND]
    assert page.kpis.values == (
        ("回填（格）", 24, False),
        ("資料庫沒有（檔）", 1, True),
        ("有格子沒填或不一致（檔）", 1, True),
    )
    assert page.next_button.winfo_manager() == "", "回填通用不是放回，收起帶到寄信草稿的下一步"
    assert page.memory.load()["generic_backfill"] == str(source.parent), "記住選檔的資料夾"

    page.batch_var.set(str(tmp_path / "待核對_1"))
    page.result_var.set(str(tmp_path / "核對結果.xlsx"))
    monkeypatch.setattr(sp_workflow.tabs.returning, "run_return", lambda *a: returned(BLOCKED))
    page.start()
    assert [page.table.heading(c, "text") for c in page.table["columns"]] == ["TDCC", "TS", "IIS", "原因"], (
        "放回時換回來"
    )


def test_a_handed_check_result_switches_back_to_return_columns(master, tmp_path: Path) -> None:
    window = new_window(master, tmp_path)
    page = window.returning
    page.use_columns(sp_workflow.tabs.returning.GENERIC_COLUMNS)

    page.receive(tmp_path / "核對結果.xlsx", None)

    assert page.table["columns"] == ("tdcc", "ts", "iis", "reason")


def test_cancelling_the_save_dialog_does_nothing(master, tmp_path: Path, monkeypatch, immediate) -> None:
    window = new_window(master, tmp_path, background=immediate, logged_in=True)
    monkeypatch.setattr(filedialog, "askopenfilename", lambda **kw: str(tmp_path / "ELN通用.xlsx"))
    monkeypatch.setattr(filedialog, "asksaveasfilename", lambda **kw: "")
    monkeypatch.setattr(sp_workflow.tabs.returning, "fill_generic", lambda *a: pytest.fail("取消存檔就不回填"))

    window.returning.start_generic()

    assert window.returning.table["columns"] == ("tdcc", "ts", "iis", "reason")
