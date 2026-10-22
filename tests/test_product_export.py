"""匯出資料庫的商品條件（#122）：`workflow.product` 整張寫成 Excel。資料庫測試對真的 PostgreSQL；資料全部虛構。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest

from order_synth import CONFIG, A, B, order_row
from sp_workflow import product_export
from sp_workflow.order_import import drafts_from, import_drafts
from sp_workflow.product_export import ProductExportError, export_name, export_products

UF_215 = {"Upfront / NotePrice (%)": Decimal("97.85")}


def import_order(pg, tdcc: str) -> None:
    receipt = import_drafts(drafts_from([order_row(tdcc, UF_215)], pg), pg, CONFIG, user="tester")
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


def test_every_product_and_column_is_exported(pg, tmp_path: Path) -> None:
    import_order(pg, B)
    import_order(pg, A)
    pg.execute("UPDATE workflow.product SET locked_at = now() WHERE tdcc = %s", (A,))
    path = tmp_path / "商品條件.xlsx"

    receipt = export_products(pg, path)

    rows = read_back(path)
    columns = product_columns(pg)
    assert rows[0] == columns
    assert receipt.count == 2 and len(rows) == 3
    first = dict(zip(columns, rows[1], strict=True))
    assert [row[0] for row in rows[1:]] == [A, B], "依 TDCC 排序，前導零保留"
    stored = pg.execute("SELECT issuer, currency FROM workflow.product WHERE tdcc = %s", (A,)).fetchone()
    assert (first["issuer"], first["currency"]) == stored
    assert isinstance(first["trade_date"], dt.datetime), "日期照日期寫"
    assert isinstance(first["coupon_pct"], int | float), "數字照數字寫"
    assert isinstance(first["locked_at"], dt.datetime) and first["locked_at"].tzinfo is None, "時間換成本機時間"
    assert list(tmp_path.iterdir()) == [path], "不留暫存檔"


def test_an_empty_table_still_writes_the_header(pg, tmp_path: Path) -> None:
    receipt = export_products(pg, tmp_path / "x.xlsx")

    assert receipt.count == 0 and read_back(tmp_path / "x.xlsx") == [product_columns(pg)]


def test_text_that_looks_like_a_formula_stays_text(tmp_path: Path) -> None:
    path = tmp_path / "x.xlsx"

    product_export._save(path, ("tdcc", "status_raw"), [("029199990001", "=1+1")])

    assert read_back(path) == [["tdcc", "status_raw"], ["029199990001", "=1+1"]]


def test_control_characters_are_dropped(tmp_path: Path) -> None:
    path = tmp_path / "x.xlsx"

    product_export._save(path, ("tdcc", "status_raw"), [("029199990001", "KO\x01 2026")])

    assert read_back(path)[1] == ["029199990001", "KO 2026"]


def test_a_failed_write_keeps_the_old_file_and_leaves_no_temp_file(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "x.xlsx"
    path.write_bytes(b"old")

    def locked(_source, _target):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "replace", locked)

    with pytest.raises(ProductExportError, match="同名檔開在 Excel 時請先關掉"):
        product_export._save(path, ("tdcc",), [("029199990001",)])

    assert path.read_bytes() == b"old" and list(tmp_path.iterdir()) == [path]


def test_an_unexpected_error_leaves_no_temp_file(tmp_path: Path, monkeypatch) -> None:
    def broken(self, filename):
        Path(filename).write_bytes(b"half")
        raise ValueError("壞掉")

    monkeypatch.setattr(product_export.openpyxl.Workbook, "save", broken)

    with pytest.raises(ValueError):
        product_export._save(tmp_path / "x.xlsx", ("tdcc",), [])

    assert list(tmp_path.iterdir()) == []


def test_a_missing_folder_says_where(tmp_path: Path) -> None:
    with pytest.raises(ProductExportError, match="寫不進"):
        product_export._save(tmp_path / "沒有這個資料夾" / "x.xlsx", ("tdcc",), [])
