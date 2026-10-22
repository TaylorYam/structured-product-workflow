"""步驟紀錄（#151、ADR 0019）：準備、放回、建草稿每檔在 `workflow.step_record` 留一筆，進度頁靠它判斷走到哪一步。

只新增、不修改：同一檔重做多一筆。哪些檔要寫由各步驟的結果決定（`prepared_records`、`returned_records`、
`drafted_records`），寫入一個 transaction（`write_records`）。Excel 清單照舊先產出，寫紀錄失敗不影響清單（`window_actions`）。
"""

from __future__ import annotations

import getpass
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sp_workflow.mail_drafts import DRAFTED, DraftReceipt
from sp_workflow.preparation import PREPARED, PrepareReceipt
from sp_workflow.returning import ReturnReceipt

if TYPE_CHECKING:
    import psycopg

PREPARED_STEP = "已準備"
RETURNED_STEP = "已放回"
DRAFTED_STEP = "草稿已建立"
SENT_STEP = "已寄出"


@dataclass(frozen=True)
class StepRecord:
    tdcc: str
    step: str
    result: str
    related_file: str | None = None


def prepared_records(receipt: PrepareReceipt) -> list[StepRecord]:
    """「3 準備」：已準備的每檔一筆（跳過的不寫），關聯準備清單。"""
    return [
        StepRecord(o.tdcc, PREPARED_STEP, PREPARED, str(receipt.list_path))
        for o in receipt.outcomes
        if o.status == PREPARED
    ]


def returned_records(receipt: ReturnReceipt) -> list[StepRecord]:
    """「5 放回」：放回（或已在）的每檔一筆，含放回待確認檔的；不放回的不寫。關聯放回清單。"""
    return [
        StepRecord(
            o.tdcc,
            RETURNED_STEP,
            f"{'含待確認：' if o.pending else ''}TS {o.ts}、IIS {o.iis}",
            str(receipt.list_path),
        )
        for o in receipt.outcomes
        if not o.blocked
    ]


def drafted_records(receipt: DraftReceipt) -> list[StepRecord]:
    """「6 寄信草稿」：建了草稿的每檔一筆（待處理、不需寄送、建立失敗不寫），關聯寄信草稿清單。"""
    return [
        StepRecord(o.tdcc, DRAFTED_STEP, DRAFTED, str(receipt.list_path))
        for o in receipt.outcomes
        if o.status == DRAFTED
    ]


def write_records(conn: psycopg.Connection, records: Sequence[StepRecord], *, user: str | None = None) -> int:
    """一個 transaction 寫入全部紀錄，回傳寫了幾筆；`user` 預設是 Windows 使用者名稱。"""
    if not records:
        return 0
    by = user or getpass.getuser()
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO workflow.step_record (tdcc, step, recorded_by, result, related_file)"
            " VALUES (%s, %s, %s, %s, %s)",
            [(r.tdcc, r.step, by, r.result, r.related_file) for r in records],
        )
    return len(records)
