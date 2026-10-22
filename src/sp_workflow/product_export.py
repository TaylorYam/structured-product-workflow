"""匯出資料庫的商品條件（#122）：把 `workflow.product` 整張寫成一份 Excel，給作業人員留存或對照。

只讀資料庫，不改任何資料。第 1 列是資料庫的欄名，依 TDCC 排序；值照資料庫的型別寫入（日期、數字、文字），
有時區的時間換成本機時間（Excel 不收時區）。先寫到同一個資料夾的暫存檔，寫好才換成選的檔名，
寫不進去時不留半個檔、原本的同名檔不動。
"""

from __future__ import annotations

import datetime as dt
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import openpyxl
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

if TYPE_CHECKING:
    import psycopg

TABLE = "workflow.product"
_QUERY = "SELECT * FROM workflow.product ORDER BY tdcc"
_MAX_WIDTH = 40


class ProductExportError(Exception):
    """寫不進匯出檔；訊息是給作業人員看的中文。"""


@dataclass(frozen=True)
class ExportReceipt:
    path: Path
    count: int  # 匯出幾檔
    columns: tuple[str, ...]


def export_name(now: dt.datetime | None = None) -> str:
    """存檔視窗預設的檔名。"""
    return f"workflow_product_{now or dt.datetime.now():%Y%m%d_%H%M%S}.xlsx"


def export_products(conn: psycopg.Connection, path: Path) -> ExportReceipt:
    with conn.cursor() as cur:
        cur.execute(_QUERY)
        columns = tuple(column.name for column in cur.description or ())
        rows = cur.fetchall()
    _save(path, columns, rows)
    return ExportReceipt(path, len(rows), columns)


def _excel(value: object) -> object:
    """資料庫的值 → 寫進 Excel 的值。"""
    if isinstance(value, dt.datetime) and value.tzinfo is not None:
        return value.astimezone().replace(tzinfo=None)
    if isinstance(value, list | tuple | dict):
        return str(value)
    return value


def _save(path: Path, columns: tuple[str, ...], rows: list[tuple[object, ...]]) -> None:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = TABLE
    sheet.append(list(columns))
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    widths = [len(name) for name in columns]
    for row in rows:
        sheet.append([_excel(value) for value in row])
        for i, cell in enumerate(sheet[sheet.max_row]):
            if isinstance(cell.value, str):
                cell.data_type = "s"  # 「=」開頭的文字照文字寫，不當公式
                cell.number_format = "@"  # TDCC 等文字保留前導零
            widths[i] = max(widths[i], len(str(cell.value or "")))
    for i, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(i)].width = min(width + 2, _MAX_WIDTH)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    _replace(workbook, path)


def _replace(workbook: openpyxl.Workbook, path: Path) -> None:
    """先存到同一個資料夾的暫存檔，再換成 `path`。"""
    try:
        handle, temp = tempfile.mkstemp(prefix=".workflow_product_", suffix=".xlsx", dir=path.parent)
    except OSError as exc:
        raise ProductExportError(f"寫不進 {path.parent}：{exc.strerror or exc}") from exc
    os.close(handle)
    try:
        workbook.save(temp)
        os.replace(temp, path)
    except OSError as exc:
        Path(temp).unlink(missing_ok=True)
        raise ProductExportError(
            f"寫不進 {path}：{exc.strerror or exc}。同名檔開在 Excel 時請先關掉，或換一個檔名再匯出。"
        ) from exc
