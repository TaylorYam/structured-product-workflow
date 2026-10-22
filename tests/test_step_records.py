"""步驟紀錄（#151、ADR 0019）：準備、放回、建草稿每檔留一筆，只新增不修改。資料全部虛構。

哪些檔要寫看各步驟的結果（純函式）；寫入走視窗動作，準備與放回的本體換成虛構結果，
重點是清單照常產出、紀錄另寫：沒有連線設定不寫、連不上或寫入失敗時原因放在結果裡。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from sp_workflow.mail_drafts import DRAFTED, FAILED, NOT_NEEDED, PENDING, DraftOutcome, DraftReceipt
from sp_workflow.preparation import PREPARED, SKIPPED, PrepareReceipt, ProductOutcome
from sp_workflow.returning import (
    ALREADY_THERE,
    NOT_RETURNED,
    PENDING_RETURNED,
    RETURNED,
    ReturnOutcome,
    ReturnReceipt,
)
from sp_workflow.step_records import (
    DRAFTED_STEP,
    PREPARED_STEP,
    RETURNED_STEP,
    drafted_records,
    prepared_records,
    returned_records,
    write_records,
)
from sp_workflow.window_actions import make_mail_drafts, run_prepare, run_return

A, B, C = "147000000001", "147000000002", "147000000003"


def prepare_receipt(tmp_path: Path) -> PrepareReceipt:
    return PrepareReceipt(
        tmp_path, tmp_path / "準備清單.xlsx", (ProductOutcome(A, PREPARED), ProductOutcome(B, SKIPPED, "找不到說明書"))
    )


def return_receipt(tmp_path: Path) -> ReturnReceipt:
    outcomes = (
        ReturnOutcome(A, RETURNED, RETURNED),
        ReturnOutcome(B, RETURNED, PENDING_RETURNED, "IIS 沒通過"),
        ReturnOutcome(C, NOT_RETURNED, NOT_RETURNED, "核對結果檔沒有這個商品（還沒核對）"),
    )
    return ReturnReceipt(tmp_path / "放回清單_20261022-100000.xlsx", outcomes)


def draft_receipt(tmp_path: Path) -> DraftReceipt:
    outcomes = (
        DraftOutcome(A, DRAFTED),
        DraftOutcome(B, NOT_NEEDED),
        DraftOutcome(C, PENDING, reason="核對未通過"),
        DraftOutcome("147000000004", FAILED, reason="處理失敗"),
    )
    return DraftReceipt(tmp_path / "寄信草稿清單.xlsx", outcomes, last_import=None)


def records(pg) -> list[tuple]:
    return pg.execute(
        "SELECT tdcc, step, recorded_by, result, related_file FROM workflow.step_record ORDER BY record_id"
    ).fetchall()


# ---------- 哪些檔要寫 ----------


def test_prepare_records_only_prepared_products(tmp_path: Path) -> None:
    (record,) = prepared_records(prepare_receipt(tmp_path))

    assert (record.tdcc, record.step, record.related_file) == (A, PREPARED_STEP, str(tmp_path / "準備清單.xlsx"))


def test_return_records_returned_and_pending_but_not_blocked(tmp_path: Path) -> None:
    returned, pending = returned_records(return_receipt(tmp_path))

    assert (returned.tdcc, returned.step, returned.result) == (A, RETURNED_STEP, "TS 已放回、IIS 已放回")
    assert (pending.tdcc, pending.result) == (B, "含待確認：TS 已放回、IIS 已放回（待確認）"), "放回待確認檔也寫並標明"


def test_return_records_already_there(tmp_path: Path) -> None:
    receipt = ReturnReceipt(tmp_path / "放回清單.xlsx", (ReturnOutcome(A, ALREADY_THERE, ALREADY_THERE),))

    (record,) = returned_records(receipt)

    assert record.result == "TS 已在、IIS 已在"


def test_draft_records_only_drafted(tmp_path: Path) -> None:
    (record,) = drafted_records(draft_receipt(tmp_path))

    assert (record.tdcc, record.step) == (A, DRAFTED_STEP), "待處理、不需寄送、建立失敗不寫"


# ---------- 寫入 ----------


@pytest.mark.db
def test_each_step_writes_one_record(pg, pg_settings, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("sp_workflow.window_actions.prepare_batch", lambda text, root, out: prepare_receipt(tmp_path))
    monkeypatch.setattr("sp_workflow.window_actions.return_to_folders", lambda batch, result: return_receipt(tmp_path))
    monkeypatch.setattr("sp_workflow.window_actions.create_drafts", lambda *args, **kwargs: draft_receipt(tmp_path))
    monkeypatch.setattr("getpass.getuser", lambda: "tester")

    prepared = run_prepare(pg_settings, A, tmp_path, tmp_path)
    returned = run_return(pg_settings, tmp_path, tmp_path / "核對結果.xlsx")
    drafted = make_mail_drafts(pg_settings, A, tmp_path, tmp_path, template_path=None)

    assert (prepared.recorded.count, returned.recorded.count, drafted.recorded.count) == (1, 2, 1)
    assert records(pg) == [
        (A, "已準備", "tester", "已準備", str(tmp_path / "準備清單.xlsx")),
        (A, "已放回", "tester", "TS 已放回、IIS 已放回", str(tmp_path / "放回清單_20261022-100000.xlsx")),
        (
            B,
            "已放回",
            "tester",
            "含待確認：TS 已放回、IIS 已放回（待確認）",
            str(tmp_path / "放回清單_20261022-100000.xlsx"),
        ),
        (A, "草稿已建立", "tester", "已建草稿", str(tmp_path / "寄信草稿清單.xlsx")),
    ]


@pytest.mark.db
def test_redoing_a_step_adds_a_record(pg, tmp_path: Path) -> None:
    write_records(pg, prepared_records(prepare_receipt(tmp_path)), user="tester")
    again = replace(prepare_receipt(tmp_path), list_path=tmp_path / "第二批" / "準備清單.xlsx")

    write_records(pg, prepared_records(again), user="tester")

    assert [(r[0], r[1], r[4]) for r in records(pg)] == [
        (A, "已準備", str(tmp_path / "準備清單.xlsx")),
        (A, "已準備", str(tmp_path / "第二批" / "準備清單.xlsx")),
    ], "同一檔重做多一筆，不覆蓋"


@pytest.mark.db
def test_records_are_insert_only_with_known_steps(pg) -> None:
    import psycopg

    with pytest.raises(psycopg.errors.CheckViolation):
        pg.execute(
            "INSERT INTO workflow.step_record (tdcc, step, recorded_by, result) VALUES (%s, '核對', 'x', 'x')", (A,)
        )


def test_without_database_settings_no_records_and_the_list_is_kept(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("sp_workflow.window_actions.prepare_batch", lambda text, root, out: prepare_receipt(tmp_path))

    result = run_prepare(None, A, tmp_path, tmp_path)

    assert result.receipt == prepare_receipt(tmp_path) and result.recorded.skipped


@pytest.mark.db
def test_unreachable_database_keeps_the_list_and_says_why(pg_settings, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("sp_workflow.window_actions.return_to_folders", lambda batch, result: return_receipt(tmp_path))

    result = run_return(replace(pg_settings, dbname="no_such_db_test"), tmp_path, tmp_path / "核對結果.xlsx")

    assert result.receipt == return_receipt(tmp_path)
    assert "連不上資料庫 no_such_db_test" in result.recorded.error and result.recorded.database_error is not None


@pytest.mark.db
def test_write_failure_keeps_the_list_and_says_why(pg, pg_settings, tmp_path: Path, monkeypatch) -> None:
    pg.execute("DROP TABLE workflow.step_record")
    monkeypatch.setattr("sp_workflow.window_actions.prepare_batch", lambda text, root, out: prepare_receipt(tmp_path))
    monkeypatch.setattr("sp_workflow.window_actions.require_current_schema", lambda conn: None)

    result = run_prepare(pg_settings, A, tmp_path, tmp_path)

    assert result.receipt == prepare_receipt(tmp_path)
    assert "step_record" in result.recorded.error and result.recorded.database_error is None, (
        "不是連線問題，不用重問密碼"
    )
