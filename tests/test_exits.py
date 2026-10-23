"""出場清單（`sp_workflow.exits`，#171）：某一個收盤日比價算出的 KO、接股、到期還本，匯出 Excel。

商品、事件與收盤價全部虛構。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import openpyxl
import pytest

from db_synth import inventory, seed_products
from order_synth import CONFIG
from sp_workflow.close_prices import latest_close_date
from sp_workflow.exits import EXPORT_COLUMNS, ExitRow, export_exits, export_name, load_exits
from sp_workflow.lifecycle import LEGACY, MANUAL, OBSERVATION

DAY = dt.date(2026, 10, 21)
T = dt.datetime(2026, 10, 22, 8, 30, tzinfo=dt.UTC)
A, B, C, D, E, F = (f"0291999900{i:02d}" for i in range(1, 7))


def event(
    pg, tdcc: str, name: str, day: dt.date | None, source: str = OBSERVATION, underlying: str | None = None
) -> int:
    (event_id,) = pg.execute(
        "INSERT INTO workflow.product_event (tdcc, event, event_date, underlying, source, recorded_by, recorded_at)"
        " VALUES (%s, %s, %s, %s, %s, 'tester', %s) RETURNING event_id",
        (tdcc, name, day, underlying, source, T),
    ).fetchone()
    return event_id


def close(pg, day: dt.date) -> None:
    (import_id,) = pg.execute(
        "INSERT INTO workflow.close_price_import (source, file_name, file_sha256, imported_by, summary)"
        " VALUES ('歷史收盤價', 'HP.xlsx', 'x', 'tester', '{}') RETURNING import_id"
    ).fetchone()
    pg.execute(
        "INSERT INTO workflow.close_price (close_date, code, close, import_id) VALUES (%s, 'AAA UW', 10, %s)",
        (day, import_id),
    )


@pytest.mark.db
def test_the_latest_close_day_lists_exits_from_observation_only(pg) -> None:
    seed_products(pg, [inventory(t, [("台北", 10000)]) for t in (A, B, C, D, E, F)])
    pg.execute("UPDATE workflow.product SET locked_at = %s WHERE tdcc <> %s", (T, C))
    close(pg, DAY - dt.timedelta(days=1))
    close(pg, DAY)
    event(pg, C, "到期還本", DAY)
    event(pg, B, "接股", DAY, underlying="AAA UW")
    event(pg, A, "KO", DAY)
    event(pg, D, "KO", DAY - dt.timedelta(days=1))  # 別天
    old = event(pg, E, "KO", DAY)
    pg.execute("UPDATE workflow.product_event SET superseded_at = now() WHERE event_id = %s", (old,))  # 已被取代
    event(pg, F, "顧客贖回", DAY, source=MANUAL)
    event(pg, D, "KO", DAY, source=LEGACY)

    found = load_exits(pg, CONFIG)

    assert found.day == latest_close_date(pg) == DAY
    assert [(r.tdcc, r.event, r.event_date, r.underlying, r.locked) for r in found.rows] == [
        (A, "KO", DAY, "", True),
        (B, "接股", DAY, "AAA UW", True),
        (C, "到期還本", DAY, "", False),
    ], "依事件、TDCC 排；別天、已被取代、顧客贖回、舊清單都不列"
    assert {r.issuer for r in found.rows} == {"BARC"} and found.rows[0].recorded_at == T


@pytest.mark.db
def test_another_day_and_an_empty_close_table(pg) -> None:
    seed_products(pg, [inventory(A, [("台北", 10000)])])
    assert load_exits(pg, CONFIG).day is None, "收盤價表還沒有資料"

    event(pg, A, "KO", DAY - dt.timedelta(days=3))
    close(pg, DAY)

    assert load_exits(pg, CONFIG).rows == ()
    earlier = load_exits(pg, CONFIG, DAY - dt.timedelta(days=3))
    assert (earlier.day, [r.tdcc for r in earlier.rows]) == (DAY - dt.timedelta(days=3), [A])


def test_export_writes_the_rows_as_shown(tmp_path: Path) -> None:
    path = tmp_path / export_name(DAY)
    rows = [ExitRow(B, "BARC", "FCN", "接股", DAY, "AAA UW", False, T)]

    receipt = export_exits(rows, path)

    assert path.name == "出場清單_20261021.xlsx" and (receipt.path, receipt.count) == (path, 1)
    values = list(openpyxl.load_workbook(path).active.iter_rows(values_only=True))
    assert values[0] == EXPORT_COLUMNS
    recorded = T.astimezone().replace(tzinfo=None)  # Excel 不收時區，存成本機時間
    assert values[1] == (B, "BARC", "FCN", "接股", dt.datetime(2026, 10, 21), "AAA UW", "未鎖住", recorded)
