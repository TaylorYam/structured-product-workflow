"""核對條件來源（#445）：資料庫來源不寫檔，排好的表直接讀成列，讀到的和「寫成 Excel 再讀」一樣；
核對結果檔、回報包與核對紀錄用來源的名稱與憑證。只用合成資料。"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
from decimal import Decimal

import openpyxl
import pytest

import barc_synth
from fcn_checker.batch import check_batch, preview_batch
from fcn_checker.condition_source import DATABASE, DATABASE_STEM, ConditionSource
from fcn_checker.orders.reference import Table, as_read, load_reference_sheet
from fcn_checker.saving import save_batch
from harness import CONFIG, with_iis
from reference_synth import REFERENCE_HEADERS, reference_row

FMT = CONFIG.reference_format
NOW = dt.datetime(2030, 2, 3, 4, 5, 6)

TRICKY = [
    None,
    "",
    "  文字  ",
    True,
    0,
    7,
    12345678901234567890,  # 超過 16 位有效數字
    0.1 + 0.2,
    1 / 3,
    Decimal("1.10"),
    Decimal("99.999999999999999"),
    Decimal("0.0000123"),
    Decimal("1E+21"),
    dt.date(2030, 1, 7),
    dt.datetime(2030, 1, 7, 15, 30),
]


@pytest.mark.parametrize("value", TRICKY, ids=repr)
def test_as_read_matches_an_excel_round_trip(tmp_path, value) -> None:
    wb = openpyxl.Workbook()
    wb.active["A1"] = value
    path = tmp_path / "x.xlsx"
    wb.save(path)

    read = openpyxl.load_workbook(path, data_only=True).active["A1"].value

    assert as_read(value) == read and type(as_read(value)) is type(read)


def database_source(*rows: dict[str, object], absent=None) -> ConditionSource:
    table = Table(tuple(REFERENCE_HEADERS), tuple(tuple(r.get(h) for h in REFERENCE_HEADERS) for r in rows))
    return ConditionSource.database(FMT, table, absent or {})


def test_table_rows_equal_the_rows_read_from_the_saved_workbook(tmp_path) -> None:
    """值、欄名與儲存格位置（含「資料庫!」工作表名）都和寫成檔案再讀相同。"""
    specs = (barc_synth.Spec(), barc_synth.Spec(product_code="029199990002"))
    source = database_source(*(reference_row(s) for s in specs))
    path = tmp_path / "資料庫.xlsx"
    source.workbook().save(path)

    assert source.sheet.rows == load_reference_sheet(path, FMT).rows
    assert source.sheet.sheet == DATABASE and source.sheet.rows[0].product_code.source.startswith(f"{DATABASE}!")


def test_database_source_has_no_file_and_its_digest_follows_the_content() -> None:
    spec = barc_synth.Spec()
    source = database_source(reference_row(spec), absent={"029199990009": "資料庫沒有這檔商品"})
    same = database_source(reference_row(spec), absent={"029199990009": "資料庫沒有這檔商品"})
    other = database_source(reference_row(spec.with_(annual=spec.annual + 1)))

    assert source.files == () and source.path is None
    assert source.sheet.absent == {"029199990009": "資料庫沒有這檔商品"}
    assert source.digest == same.digest != other.digest


def test_database_check_names_the_result_file_and_records_the_digest(tmp_path) -> None:
    """結果檔檔名開頭是 `參考條件表_資料庫`；核對紀錄記來源名稱與表內容的 sha256，不是檔案。"""
    spec = barc_synth.Spec()
    pdfs = with_iis([barc_synth.build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)])
    source = database_source(reference_row(spec))

    outcome = check_batch(preview_batch(CONFIG, source, pdfs))
    receipt = save_batch(outcome, tmp_path / "out", root=tmp_path, now=NOW)

    assert receipt.output is not None and receipt.output.name == f"{DATABASE_STEM}_核對結果_20300203-040506.xlsx"
    assert outcome.metadata["inputs"]["reference_sheet"] == {"source": DATABASE, "sha256": source.digest}
    assert outcome.reference_sheet is None and outcome.source is source


def extracted(source: ConditionSource, members) -> list[tuple]:
    from fcn_checker.report_bundle import _reference_extract

    book = openpyxl.load_workbook(io.BytesIO(_reference_extract(source, FMT, members)))
    return [tuple((c.value, c.number_format) for c in row) for row in book.active.iter_rows()]


def test_bundle_extract_from_the_table_equals_the_extract_from_the_saved_workbook(tmp_path) -> None:
    """回報包的「參考條件表_未通過」：從記憶體的表擷取，和從寫成的檔案擷取相同（值與數字格式）。"""
    spec, other = barc_synth.Spec(), barc_synth.Spec(product_code="029199990002")
    pdfs = with_iis([barc_synth.build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)])
    source = database_source(reference_row(other), reference_row(spec.with_(annual=spec.annual + 1)))
    path = tmp_path / "資料庫.xlsx"
    source.workbook().save(path)
    members = check_batch(preview_batch(CONFIG, source, pdfs)).items

    assert extracted(source, members) == extracted(ConditionSource.excel(path, FMT), members)


def test_digest_is_a_fixed_serialisation_of_the_table() -> None:
    """憑證的 sha256 依 data-contract §5 的固定序列：同樣的值不同型別寫法（Decimal 的小數位）算不同內容。"""
    table = Table(("A", "B"), ((Decimal("1.10"), dt.date(2030, 1, 7)),))
    from fcn_checker.condition_source import _digest

    expected = '{"absent":{"9":"x"},"headers":["A","B"],"rows":[["1.10","2030-01-07"]]}'
    assert _digest(table, {"9": "x"}) == hashlib.sha256(expected.encode("utf-8")).hexdigest()
