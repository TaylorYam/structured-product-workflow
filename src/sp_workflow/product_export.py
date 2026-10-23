"""匯出資料庫的商品條件（#122）：把 `workflow.product` 整張寫成一份 Excel，給作業人員留存或對照。

只讀資料庫，不改任何資料。第 1 列是資料庫的欄名，最後多一欄「存續狀態」（最新一筆存續事件，#152），依 TDCC 排序；值照資料庫的型別寫入（日期、數字、文字），
有時區的時間換成本機時間（Excel 不收時區），Excel 不收的控制字元拿掉。先寫到同一個資料夾的暫存檔，
寫好才換成選的檔名，寫不進去時不留半個檔、原本的同名檔不動。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE, Cell

from sp_workflow.file_tools import save_book, table_book
from sp_workflow.lifecycle import lifecycle_status

if TYPE_CHECKING:
    import psycopg

TABLE = "workflow.product"
LIFECYCLE_COLUMN = "存續狀態"
_MAX_WIDTH = 40


class ProductExportError(Exception):
    """寫不進匯出檔；訊息是給作業人員看的中文。"""


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


def _excel(value: object) -> object:
    """資料庫的值 → 寫進 Excel 的值。"""
    if isinstance(value, dt.datetime) and value.tzinfo is not None:
        return value.astimezone().replace(tzinfo=None)
    if isinstance(value, str):
        return ILLEGAL_CHARACTERS_RE.sub("", value)
    return value


def save_rows(path: Path, title: str, columns: Sequence[str], rows: Sequence[Sequence[object]]) -> None:
    """一張工作表（`title`）：第 1 列粗體欄名、凍結窗格、自動篩選；先寫暫存檔再換名（進度頁匯出也用，#156）。"""
    values = [[_excel(value) for value in row] for row in rows]
    widths = [len(name) for name in columns]
    for row in values:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], len(str(value or "")))
    book = table_book(
        title,
        columns,
        values,
        widths=[min(width + 2, _MAX_WIDTH) for width in widths],
        style=_text_cell,
        auto_filter=True,
    )
    try:
        save_book(book, path)
    except OSError as exc:
        raise ProductExportError(
            f"寫不進 {path}：{exc.strerror or exc}。同名檔開在 Excel 時請先關掉，或換一個檔名再匯出。"
        ) from exc


def _text_cell(cell: Cell) -> None:
    if isinstance(cell.value, str):
        cell.data_type = "s"  # 「=」開頭的文字照文字寫，不當公式
        cell.number_format = "@"  # TDCC 等文字保留前導零
