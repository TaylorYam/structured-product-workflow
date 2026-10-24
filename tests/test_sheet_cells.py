"""Excel 格子的解讀（#207）：空白、破折號、錯的型別、邊界；不用資料庫。值全部虛構。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from sp_workflow import sheet_cells


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), ("", None), ("  ", None), ("-", None), (" -  ", None), (" Amy ", "Amy"), (12.0, "12"), (1.5, "1.5")],
)
def test_text_trims_and_treats_blank_and_dash_as_none(value, expected) -> None:
    assert sheet_cells.text(value) == expected


def test_text_can_keep_a_dash() -> None:
    assert sheet_cells.text("-", dash_is_none=False) == "-"
    assert sheet_cells.text("  ", dash_is_none=False) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("029199990001", "029199990001"),
        (29199990001, "029199990001"),
        (29199990001.0, "029199990001"),
        ("7", "000000000007"),
    ],
)
def test_tdcc_pads_up_to_twelve_digits(value, expected) -> None:
    assert sheet_cells.tdcc(value) == expected


@pytest.mark.parametrize("value", [None, "", "-", "1234567890123", "02919999000A", 1.5])
def test_tdcc_refuses_what_is_not_twelve_digits_or_fewer(value) -> None:
    with pytest.raises(ValueError, match="不是 12 碼以內的數字"):
        sheet_cells.tdcc(value)


def test_tdcc_or_raw_keeps_what_it_cannot_read() -> None:
    assert sheet_cells.tdcc_or_raw(29199990001) == "029199990001"
    assert sheet_cells.tdcc_or_raw(" ABC ") == "ABC"
    assert sheet_cells.tdcc_or_raw(None) == "（空白）"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("-", None),
        ("1,234.5", Decimal("1234.5")),
        (" 7 ", Decimal("7")),
        (2.15, Decimal("2.15")),
    ],
)
def test_decimal_reads_numbers_and_blanks(value, expected) -> None:
    assert sheet_cells.decimal(value) == expected


@pytest.mark.parametrize("value", [True, "abc", "NaN", "Infinity", "1.2.3"])
def test_decimal_refuses_what_is_not_a_number(value) -> None:
    with pytest.raises(ValueError, match="不是數字"):
        sheet_cells.decimal(value)


def test_whole_number_takes_only_integers() -> None:
    assert sheet_cells.whole_number("3") == 3 and sheet_cells.whole_number(4.0) == 4
    assert sheet_cells.whole_number("-") is None
    with pytest.raises(ValueError, match="不是整數"):
        sheet_cells.whole_number("3.5")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2.15", Decimal("2.150000")),
        ("0.0000015", Decimal("0.000002")),  # 五成雙：前一位是奇數進位
        ("0.0000025", Decimal("0.000002")),  # 前一位是偶數捨去
        ("-", None),
    ],
)
def test_pct_keeps_six_decimal_places(value, expected) -> None:
    found = sheet_cells.pct(value)
    assert found == expected and (found is None or found.as_tuple().exponent == -6)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("10,000", Decimal("10000.0000")), ("0.00005", Decimal("0.0000")), ("0.00015", Decimal("0.0002")), ("", None)],
)
def test_amount_keeps_four_decimal_places(value, expected) -> None:
    found = sheet_cells.amount(value)
    assert found == expected and (found is None or found.as_tuple().exponent == -4)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (dt.datetime(2026, 10, 15, 13, 30), dt.date(2026, 10, 15)),
        (dt.date(2026, 10, 15), dt.date(2026, 10, 15)),
        ("2026/10/15", dt.date(2026, 10, 15)),
        (" 2026-10-15 ", dt.date(2026, 10, 15)),
        ("-", None),
        (None, None),
    ],
)
def test_date_reads_dates_and_the_two_text_forms(value, expected) -> None:
    assert sheet_cells.date(value) == expected


@pytest.mark.parametrize("value", ["15-Oct-26", "2026/13/01", "20261015", 46310])
def test_date_refuses_other_forms(value) -> None:
    with pytest.raises(ValueError, match="不是日期"):
        sheet_cells.date(value)


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (" AA UN Equity ", "AA UN"),
        ("USDJPY Curncy", "USDJPY"),
        ("SPX index", "SPX"),
        ("AAA UW", "AAA UW"),
        ("Equity", "Equity"),
    ],
)
def test_bbg_code_drops_only_the_trailing_class_word(raw: str, code: str) -> None:
    assert sheet_cells.bbg_code(raw) == code
