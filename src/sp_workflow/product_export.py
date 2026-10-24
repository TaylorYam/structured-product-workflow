"""匯出資料庫的商品條件（#122）：把 `workflow.product` 整張寫成一份 Excel，給作業人員留存或對照。

只讀資料庫，不改任何資料。第 1 列是資料庫的欄名，最後多一欄「存續狀態」（最新一筆存續事件，#152），依 TDCC 排序；值照資料庫的型別寫入（日期、數字、文字），
有時區的時間換成本機時間（Excel 不收時區），Excel 不收的控制字元拿掉（`file_tools.save_rows`）。先寫到同一個資料夾的暫存檔，
寫好才換成選的檔名，寫不進去時丟 `file_tools.FileWriteError`、不留半個檔、原本的同名檔不動。
傳入的 `conn` 由 `database.connect` 建立，資料表版本已確認（#200），這裡不再檢查。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from sp_workflow.file_tools import save_rows
from sp_workflow.lifecycle import lifecycle_status

if TYPE_CHECKING:
    import psycopg

TABLE = "workflow.product"
LIFECYCLE_COLUMN = "存續狀態"


@dataclass(frozen=True)
class ExportReceipt:
    path: Path
    count: int  # 匯出幾檔


def export_name(now: dt.datetime | None = None) -> str:
    """存檔視窗預設的檔名。"""
    return f"workflow_product_{now or dt.datetime.now():%Y%m%d_%H%M%S}.xlsx"


def export_products(conn: psycopg.Connection, path: Path) -> ExportReceipt:
    with conn.transaction(), conn.cursor() as cur:  # 商品與存續事件讀同一個時間點
        cur.execute(f"SELECT * FROM {TABLE} ORDER BY tdcc")
        columns = tuple(column.name for column in cur.description or ())
        rows = cur.fetchall()
        tdcc = columns.index("tdcc")
        statuses = lifecycle_status(conn, (row[tdcc] for row in rows))
    save_rows(path, TABLE, (*columns, LIFECYCLE_COLUMN), [(*row, statuses[row[tdcc]].status) for row in rows])
    return ExportReceipt(path, len(rows))
