"""匯入的 run 紀錄與錯誤清單（#207）：每日匯入、回補股價、收盤價、起單資料匯入與舊商品補資料共用。

- run 紀錄：`open_run` 先記一筆（summary 是空的 `{}`）拿到編號，寫完再用 `finish_run` 補上 summary。
  三張 run 表照舊：`workflow.import_run`（每日匯入、回補股價）、`workflow.close_price_import`（收盤價）、
  `workflow.order_import`（起單資料）。在呼叫端的 transaction 裡執行。
- `error_book`：寫一份錯誤清單（簡單的 Excel 表，`file_tools.table_book`）；寫不進去時丟 `file_tools.FileWriteError`，
  暫存檔刪掉、原本的同名檔不動。錯誤清單的欄、工作表名與檔名由各 pipeline 決定。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from sp_workflow.file_tools import FileWriteError, save_book, table_book

if TYPE_CHECKING:
    import psycopg
    from openpyxl.cell.cell import Cell

_RUN_IDS = {"import_run": "run_id", "close_price_import": "import_id", "order_import": "import_id"}  # 表 → 編號欄


def open_run(conn: psycopg.Connection, table: str, columns: Mapping[str, object]) -> int:
    """在 `workflow.<table>` 記一筆 run（`columns` 是欄 → 值，summary 先是 `{}`），回傳它的編號。"""
    run_id = _RUN_IDS[table]
    names = ", ".join([*columns, "summary"])
    values = ", ".join(["%s"] * len(columns) + ["'{}'"])
    (found,) = conn.execute(
        f"INSERT INTO workflow.{table} ({names}) VALUES ({values}) RETURNING {run_id}", tuple(columns.values())
    ).fetchone()
    return found


def finish_run(conn: psycopg.Connection, table: str, run_id: int, summary: Mapping[str, object]) -> None:
    """補上這筆 run 的 summary（JSON，中文不跳脫）。"""
    conn.execute(
        f"UPDATE workflow.{table} SET summary = %s::jsonb WHERE {_RUN_IDS[table]} = %s",
        (json.dumps(summary, ensure_ascii=False), run_id),
    )


def error_book(
    path: Path,
    title: str,
    columns: Sequence[str],
    rows: Iterable[Sequence[object]],
    *,
    widths: Sequence[float],
    style: Callable[[Cell], None] | None = None,
    overwrite: bool = True,
    auto_filter: bool = False,
) -> None:
    """寫一份錯誤清單；寫不進去（含 `overwrite=False` 時已經有同名檔）丟 `FileWriteError`，不留半成品。"""
    book = table_book(title, columns, rows, widths=widths, style=style, auto_filter=auto_filter)
    try:
        save_book(book, path, overwrite=overwrite)
    except OSError as exc:
        raise FileWriteError(f"錯誤清單寫不進去，整份不匯入：{path}（{exc.strerror or exc}）", path, exc) from exc
