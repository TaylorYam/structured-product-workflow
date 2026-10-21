"""讀起單建檔（#97）：用 openpyxl 寫出虛構的起單建檔再讀。資料全部虛構。"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from pathlib import Path

import pytest

from order_synth import HEADERS, PREFIXES, VALUES, A, B, sheet_line, write_sheet
from sp_workflow.order_terms import (
    FIELDS,
    NOT_FOUND,
    PENDING,
    READ,
    TDCC_FIELD,
    OrderTermsError,
    collect_orders,
    format_field,
    parse_field,
)


def read(path: Path, text: str):
    return collect_orders(text, path, PREFIXES)


def test_reads_the_pasted_tdccs_in_order_with_typed_values(tmp_path: Path) -> None:
    other = sheet_line(B, {"Barrier Type": "NONE", "KI Barrier (%)": None, "Coupon p.a. (%)": Decimal("10.17")})
    path = write_sheet(tmp_path / "起單建檔_20261015.xlsx", [sheet_line(A), other, sheet_line("147199990003")])

    rows = read(path, f"{B}\n{A}")

    assert [(r.tdcc, r.status, r.issuer, r.lines) for r in rows] == [(B, READ, "SG", (4,)), (A, READ, "BARC", (3,))]
    assert rows[1].values == VALUES
    assert set(rows[1].values) == set(FIELDS)
    assert rows[0].values["Coupon p.a. (%)"] == Decimal("10.17")  # float 照 Excel 顯示的位數
    assert rows[0].values["KI Barrier (%)"] is None
    assert rows[1].source == path and rows[1].notes == {} and rows[1].conflicts == frozenset()


def test_the_old_upfront_header_and_any_column_order_are_accepted(tmp_path: Path) -> None:
    headers = ["Upfront / NotePrice (%)" if h.strip() == "UF (%)" else h for h in reversed(HEADERS)]
    line = sheet_line(A)
    line["Upfront / NotePrice (%)"] = line.pop("UF (%)")
    path = write_sheet(tmp_path / "old.xlsx", [line], headers=headers)

    (row,) = read(path, A)

    assert row.status == READ and row.values["Upfront / NotePrice (%)"] == Decimal("98.5")


def test_choices_ignore_case_and_unknown_values_are_left_blank(tmp_path: Path) -> None:
    lines = [
        sheet_line(A, {"KO Type": "DAILY MEMORY", "Barrier Type": "None", "KI Barrier (%)": None}),
        sheet_line(B, {"KO Type": 120, "Barrier Type": "MKI", "Tenor (m)": "15Y", "Trade date": "10/15"}),
    ]
    path = write_sheet(tmp_path / "s.xlsx", lines)

    first, second = read(path, f"{A} {B}")

    assert first.status == READ
    assert (first.values["KO Type"], first.values["Barrier Type"]) == ("Daily Memory", "NONE")
    assert second.status == PENDING
    assert {name: second.values[name] for name in second.notes} == dict.fromkeys(second.notes)
    assert second.notes == {
        "KO Type": "讀到「120」，要選 Daily／Daily Memory／Period End／Period End Memory",
        "Tenor (m)": "讀到「15Y」，不是整數",
        "Barrier Type": "讀到「MKI」，要選 NONE／EKI／AKI",
        "Trade date": "讀到「10/15」，日期要寫成 2026-10-21",
    }


def test_missing_and_malformed_tdccs(tmp_path: Path) -> None:
    path = write_sheet(tmp_path / "起單建檔_20261015.xlsx", [sheet_line(A)])

    missing, short, unknown = read(path, "037199990009, 12345, 999199990001")

    assert (missing.status, missing.issuer, missing.found) == (NOT_FOUND, "SG", False)
    assert missing.notes == {TDCC_FIELD: "起單建檔_20261015.xlsx 裡沒有這檔"}
    assert (short.status, short.notes) == (PENDING, {TDCC_FIELD: "TDCC 要剛好 12 碼數字"})
    assert unknown.status == NOT_FOUND


def test_an_unknown_issuer_prefix_is_noted(tmp_path: Path) -> None:
    path = write_sheet(tmp_path / "s.xlsx", [sheet_line("999199990001")])

    (row,) = read(path, "999199990001")

    assert (row.status, row.issuer) == (PENDING, "")
    assert row.notes == {TDCC_FIELD: "TDCC 前三碼 999 不在上手編號對照"}


def test_identical_duplicate_lines_are_used(tmp_path: Path) -> None:
    path = write_sheet(tmp_path / "s.xlsx", [sheet_line(A), sheet_line(B), sheet_line(A)])

    (row,) = read(path, A)

    assert (row.status, row.lines, row.values, row.notes) == (READ, (3, 5), VALUES, {})


def test_duplicate_lines_are_compared_by_their_read_values(tmp_path: Path) -> None:
    """寫法不同但解讀後一樣（大小寫、數字與文字）不算不同。"""
    same = sheet_line(A, {"KO Type": "DAILY MEMORY", "Barrier Type": "eki", "Tenor (m)": "6"})
    path = write_sheet(tmp_path / "s.xlsx", [sheet_line(A), same])

    (row,) = read(path, A)

    assert (row.status, row.conflicts, row.values) == (READ, frozenset(), VALUES)


def test_different_duplicate_lines_leave_those_fields_for_the_operator(tmp_path: Path) -> None:
    changed = sheet_line(A, {"Coupon p.a. (%)": Decimal(13), "BBG Code 3": "CCC UQ"})
    path = write_sheet(tmp_path / "s.xlsx", [sheet_line(A), changed])

    (row,) = read(path, A)

    assert row.status == PENDING and row.conflicts == {"Coupon p.a. (%)", "BBG Code 3"}
    assert row.values["Coupon p.a. (%)"] is None and row.values["Strike (%)"] == Decimal(80)
    assert row.notes == {
        "BBG Code 3": "這檔有 2 列，這欄不同：第 3 列「空白」、第 4 列「CCC UQ」；請填正確的值",
        "Coupon p.a. (%)": "這檔有 2 列，這欄不同：第 3 列「12.5」、第 4 列「13」；請填正確的值",
    }


@pytest.mark.parametrize(
    ("make", "message"),
    [
        (lambda p: write_sheet(p, [sheet_line()], sheets=2), "有 2 張工作表；請選每天的起單建檔"),
        (lambda p: write_sheet(p, [sheet_line()], headers=[h for h in HEADERS if h != "TDCC"]), "找不到表頭"),
        (
            lambda p: write_sheet(p, [sheet_line()], headers=[h for h in HEADERS if h not in ("期初價格", "KO Type")]),
            "表頭少了：KO Type、期初價格",
        ),
        (lambda p: write_sheet(p, [sheet_line()], headers=[*HEADERS, "Tenor (M)"]), "表頭有兩欄都是「Tenor (m)」"),
        (lambda p: p.write_bytes(b"not excel") and p, "起單建檔打不開"),
        (lambda p: p, "起單建檔不存在"),
    ],
)
def test_files_that_are_not_a_daily_sheet_stop_the_batch(tmp_path: Path, make, message) -> None:
    path = make(tmp_path / "s.xlsx")

    with pytest.raises(OrderTermsError, match=re.escape(message)):
        read(path, A)


def test_no_pasted_tdcc_stops_the_batch(tmp_path: Path) -> None:
    with pytest.raises(OrderTermsError, match="沒有輸入 TDCC"):
        read(tmp_path / "s.xlsx", " \n")


def test_parse_and_format_fields() -> None:
    assert parse_field("Strike (%)", " 80% ") == Decimal(80)
    assert parse_field("Strike (%)", 80.83) == Decimal("80.83")
    assert parse_field("Tenor (m)", "12") == 12
    assert parse_field("Tenor (m)", 6.0) == 6
    assert parse_field("KO Type", "daily memory") == "Daily Memory"
    assert parse_field("BBG Code 1", 2330) == "2330"
    assert parse_field("Issue date", "") is None
    assert parse_field("Issue date", "-") is None
    assert parse_field("Maturity date", "22-Apr-27") == dt.date(2027, 4, 22)
    assert parse_field("Maturity date", dt.datetime(2027, 4, 22)) == dt.date(2027, 4, 22)
    for name, raw, message in (
        ("Strike (%)", "八十", "不是數字"),
        ("Tenor (m)", "6.5", "不是整數"),
        ("Tenor (m)", True, "要填整數"),
        ("Trade date", "10/15", "日期要寫成 2026-10-21"),
        ("Trade date", dt.datetime(2026, 10, 15, 9, 30), "日期不能有時間"),
        ("Barrier Type", "MKI", "要選 NONE／EKI／AKI"),
    ):
        with pytest.raises(ValueError, match=message):
            parse_field(name, raw)
    assert format_field(Decimal("80.000000")) == "80"
    assert format_field(Decimal("2.150000")) == "2.15"
    assert format_field(dt.date(2026, 10, 15)) == "2026-10-15"
    assert format_field(None) == ""
