"""各步驟共用的小工具（#139）：檔案與位元組的 SHA-256、寫一張簡單的 Excel 表。

簡單的表：第 1 列粗體表頭、凍結第一列，欄寬與格子格式由呼叫端決定；存檔時先寫暫存檔再換名，
寫不進去時丟 `OSError`、暫存檔刪掉、原本的同名檔不動。核對結果檔與回報包的 Excel 有自己的版面，不用這裡。
給作業人員看的「檔案寫不進去」是 `FileWriteError`（#207，例：`import_run.error_book`）。
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

import openpyxl
from openpyxl.cell.cell import Cell
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from sp_workflow.database import OperatorError


class FileWriteError(OperatorError):
    """檔案寫不進去（#207）；訊息是給作業人員看的中文，`path` 是那個檔，`error` 是原本的 `OSError`。"""

    def __init__(self, message: str, path: Path, error: OSError) -> None:
        super().__init__(message)
        self.path = path
        self.error = error


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def tdcc_as_text(cell: Cell) -> None:
    """第 1 欄（TDCC）存成文字格式，保留前導零。"""
    if cell.column == 1:
        cell.number_format = "@"


def table_book(
    title: str,
    headers: Sequence[str],
    rows: Iterable[Sequence[object]],
    *,
    widths: Sequence[float] = (),
    style: Callable[[Cell], None] | None = None,
    auto_filter: bool = False,
) -> openpyxl.Workbook:
    """一張工作表（`title`）的活頁簿：粗體表頭、每列寫完交給 `style` 設格式、依序設欄寬、凍結第一列。"""
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = title
    sheet.append(list(headers))
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in rows:
        sheet.append(list(row))
        if style is not None:
            for cell in sheet[sheet.max_row]:
                style(cell)
    for i, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(i)].width = width
    sheet.freeze_panes = "A2"
    if auto_filter:
        sheet.auto_filter.ref = sheet.dimensions
    return book


def save_book(book: openpyxl.Workbook, path: Path, *, overwrite: bool = True) -> None:
    """先寫 `<檔名>.tmp` 再換名。`overwrite=False` 時同名檔已存在就失敗（Windows 的 `os.rename`），不覆蓋。"""
    temp = path.with_name(path.name + ".tmp")
    try:
        book.save(temp)
        if overwrite:
            temp.replace(path)
        else:
            os.rename(temp, path)
    finally:
        temp.unlink(missing_ok=True)  # 換名成功時已經不在
