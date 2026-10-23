"""出場清單（#171）：某一個收盤日比價算出的出場事件（KO、接股、到期還本），一檔一列。

只列來源「比價」、還有效（沒有被取代）的自動事件，事件日等於選的那一天；人工的顧客贖回與舊清單事件不列。
沒有指定日期時用收盤價表最新的收盤日（早上匯入昨日收盤價後就是昨天）。依事件（KO、接股、到期還本）、TDCC 排序。
只讀資料庫；匯出 Excel 的存檔方式同商品匯出（`product_export.save_rows`）。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from sp_workflow.close_prices import latest_close_date
from sp_workflow.lifecycle import AUTO_EVENTS, observed_exits_on
from sp_workflow.observation import UNLOCKED
from sp_workflow.order_import import ImportConfig, issuer_code
from sp_workflow.product_export import save_rows
from sp_workflow.product_write import LOCKED

if TYPE_CHECKING:
    import psycopg

EXPORT_COLUMNS = ("TDCC", "上手", "商品", "事件", "事件日", "標的", "鎖住", "記錄時間")
LOCKED_TEXT = {True: LOCKED, False: UNLOCKED}


@dataclass(frozen=True)
class ExitRow:
    """出場清單的一列。"""

    tdcc: str
    issuer: str  # 上手代號
    product: str
    event: str  # KO、接股、到期還本
    event_date: dt.date
    underlying: str  # 接股時是最差表現的標的；其他事件空白
    locked: bool
    recorded_at: dt.datetime  # 比價寫入事件的時間


@dataclass(frozen=True)
class ExitList:
    day: dt.date | None  # 這次看的收盤日；收盤價表還沒有資料又沒有指定時是 None
    rows: tuple[ExitRow, ...]


@dataclass(frozen=True)
class ExitExport:
    path: Path
    count: int


def load_exits(conn: psycopg.Connection, config: ImportConfig, day: dt.date | None = None) -> ExitList:
    """`day`（沒有時用最新的收盤日）比價算出、還有效的出場事件。在同一個 transaction 裡讀。"""
    with conn.transaction():
        if day is None:
            day = latest_close_date(conn)
        if day is None:
            return ExitList(None, ())
        events = observed_exits_on(conn, day)
        products = {
            tdcc: (issuer, product, locked)
            for tdcc, issuer, product, locked in conn.execute(
                "SELECT tdcc, issuer, product, locked_at IS NOT NULL FROM workflow.product WHERE tdcc = ANY(%s)",
                ([e.tdcc for e in events],),
            )
        }
    rows = []
    for e in events:
        issuer, product, locked = products[e.tdcc]
        rows.append(
            ExitRow(
                e.tdcc,
                issuer_code(e.tdcc, issuer, config),
                product or "",
                e.event,
                day,
                e.underlying or "",
                locked,
                e.recorded_at,
            )
        )
    rows.sort(key=lambda row: (AUTO_EVENTS.index(row.event), row.tdcc))
    return ExitList(day, tuple(rows))


def export_name(day: dt.date) -> str:
    """存檔視窗預設的檔名（帶收盤日）。"""
    return f"出場清單_{day:%Y%m%d}.xlsx"


def export_exits(rows: Sequence[ExitRow], path: Path) -> ExitExport:
    """把出場清單寫成 Excel，欄位同畫面；寫不進去時丟 `ProductExportError`、原本的同名檔不動。"""
    save_rows(path, "出場清單", EXPORT_COLUMNS, [export_values(row) for row in rows])
    return ExitExport(path, len(rows))


def export_values(row: ExitRow) -> tuple[object, ...]:
    """一列的值，依 `EXPORT_COLUMNS`。"""
    return (
        row.tdcc, row.issuer, row.product, row.event, row.event_date, row.underlying, LOCKED_TEXT[row.locked],
        row.recorded_at,
    )  # fmt: skip
