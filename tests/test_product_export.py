"""匯出資料庫的商品條件（#122）：`workflow.product` 整張寫成 Excel。資料庫測試對真的 PostgreSQL；資料全部虛構。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest

from order_synth import CONFIG, A, B, drafts_of, order_row
from sp_workflow.lifecycle import KO, AutoEvent, replace_auto_events
from sp_workflow.product_export import export_name, export_products
from sp_workflow.product_terms import write_batch

UF_215 = {"Upfront / NotePrice (%)": Decimal("97.85")}


def import_order(pg, tdcc: str) -> None:
    receipt = write_batch(pg, drafts_of([order_row(tdcc, UF_215)], pg), CONFIG, user="tester")
    assert receipt.lines[0].imported, receipt.lines[0].detail


def product_columns(pg) -> list[str]:
    rows = pg.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema = 'workflow' AND table_name = 'product' ORDER BY ordinal_position"
    ).fetchall()
    return [name for (name,) in rows]


def read_back(path: Path) -> list[list[object]]:
    sheet = openpyxl.load_workbook(path).active
    return [list(row) for row in sheet.iter_rows(values_only=True)]


def test_default_name_has_the_date_and_time() -> None:
    assert export_name(dt.datetime(2026, 10, 22, 15, 4, 5)) == "workflow_product_20261022_150405.xlsx"


@pytest.mark.db
def test_every_product_and_column_is_exported(pg, tmp_path: Path) -> None:
    import_order(pg, B)
    import_order(pg, A)
    pg.execute("UPDATE workflow.product SET locked_at = now() WHERE tdcc = %s", (A,))
    path = tmp_path / "商品條件.xlsx"

    receipt = export_products(pg, path)

    rows = read_back(path)
    columns = [*product_columns(pg), "存續狀態"]
    assert rows[0] == columns
    assert not [c for c in columns if c.startswith("status_")], "舊狀態欄已移除（#172），只有存續狀態"
    assert receipt.count == 2 and len(rows) == 3
    first = dict(zip(columns, rows[1], strict=True))
    assert [row[0] for row in rows[1:]] == [A, B], "依 TDCC 排序，前導零保留"
    stored = pg.execute("SELECT issuer, currency FROM workflow.product WHERE tdcc = %s", (A,)).fetchone()
    assert (first["issuer"], first["currency"]) == stored
    assert isinstance(first["trade_date"], dt.datetime), "日期照日期寫"
    assert isinstance(first["coupon_pct"], int | float), "數字照數字寫"
    assert isinstance(first["locked_at"], dt.datetime) and first["locked_at"].tzinfo is None, "時間換成本機時間"
    assert list(tmp_path.iterdir()) == [path], "不留暫存檔"


@pytest.mark.db
def test_an_empty_table_still_writes_the_header(pg, tmp_path: Path) -> None:
    receipt = export_products(pg, tmp_path / "x.xlsx")

    assert receipt.count == 0 and read_back(tmp_path / "x.xlsx") == [[*product_columns(pg), "存續狀態"]]


@pytest.mark.db
def test_lifecycle_status_is_exported(pg, tmp_path: Path) -> None:
    import_order(pg, A)
    import_order(pg, B)
    replace_auto_events(pg, A, [AutoEvent(KO, dt.date(2026, 11, 2))], user="tester")
    path = tmp_path / "商品條件.xlsx"

    export_products(pg, path)

    rows = read_back(path)
    assert [(row[0], row[-1]) for row in rows[1:]] == [(A, "KO"), (B, "在庫")]
