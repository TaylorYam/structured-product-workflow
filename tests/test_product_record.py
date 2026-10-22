"""商品紀錄：畫面的欄 ↔ `workflow.product` 的欄的轉換（#126），不需要資料庫。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from sp_workflow.product_record import (
    COLUMN_FIELDS,
    DERIVED_COLUMNS,
    FIELDS,
    PRODUCT_COLUMNS,
    REQUIRED,
    REQUIRED_COLUMNS,
    UPFRONT_FIELD,
    columns_of,
    fields_of,
    invert_upfront,
    missing_fields,
)

DENOMINATIONS = {"USD": Decimal(10000), "JPY": Decimal(1000000)}
VALUES: dict[str, object] = {
    "Product": "FCN",
    "Currency": "USD",
    "Guaranteed Periods (m)": 3,
    "BBG Code 1": "AAA UW",
    "BBG Code 2": "BBB UW",
    "BBG Code 3": None,
    "BBG Code 4": None,
    "BBG Code 5": None,
    "Strike (%)": Decimal("80"),
    "KO Type": "Period End Memory",
    "KO Barrier (%)": Decimal("100"),
    "Coupon p.a. (%)": Decimal("12.5"),
    UPFRONT_FIELD: Decimal("97.14"),
    "Tenor (m)": 12,
    "Barrier Type": "AKI",
    "KI Barrier (%)": Decimal("60"),
    "Trade date": dt.date(2026, 10, 15),
    "Strike date": dt.date(2026, 10, 16),
    "Issue date": dt.date(2026, 10, 22),
    "Final valuation date": dt.date(2027, 10, 15),
    "Maturity date": dt.date(2027, 10, 22),
    "期初價格": "開盤價",
}


def test_every_field_and_derived_column_is_a_product_column() -> None:
    assert tuple(COLUMN_FIELDS.values()) == FIELDS
    assert set(COLUMN_FIELDS) | set(DERIVED_COLUMNS) == set(PRODUCT_COLUMNS)
    assert set(REQUIRED) <= set(FIELDS)
    assert set(REQUIRED_COLUMNS) <= set(PRODUCT_COLUMNS)


def test_upfront_is_inverted_the_same_way_in_both_directions() -> None:
    assert invert_upfront(Decimal("97.14")) == Decimal("2.86")
    assert invert_upfront(Decimal("2.860000")) == Decimal("97.140000")


def test_columns_of_derives_codes_inverts_upfront_and_fills_the_denomination() -> None:
    columns = columns_of(VALUES, "Barclays", DENOMINATIONS)

    assert tuple(columns) == PRODUCT_COLUMNS
    assert columns["product"] == "FCN"
    assert columns["issuer"] == "Barclays"
    assert (columns["ko_freq"], columns["ko_memory"], columns["ki_freq"]) == ("P", "Y", "D")
    assert columns["upfront_pct"] == Decimal("2.860000")
    assert str(columns["strike_pct"]) == "80.000000"
    assert str(columns["denomination"]) == "10000.0000"
    assert columns["non_call_months"] == 3
    assert columns["underlying_2"] == "BBB UW"
    assert columns["strike_date"] == dt.date(2026, 10, 16)


@pytest.mark.parametrize(
    ("ko_type", "barrier", "codes"),
    [
        ("Daily", "NONE", ("D", "N", "-")),
        ("Daily Memory", "EKI", ("D", "Y", "AM")),
        ("Period End", "AKI", ("P", "N", "D")),
    ],
)
def test_ko_and_ki_codes(ko_type, barrier, codes) -> None:
    columns = columns_of({**VALUES, "KO Type": ko_type, "Barrier Type": barrier}, "Barclays", DENOMINATIONS)

    assert (columns["ko_freq"], columns["ko_memory"], columns["ki_freq"]) == codes


def test_blank_fields_leave_their_columns_codes_and_denomination_blank() -> None:
    columns = columns_of(dict.fromkeys(FIELDS), None, DENOMINATIONS)

    assert columns["product"] == "FCN"  # 商品種類不可空白（ADR 0016）
    assert all(value is None for column, value in columns.items() if column != "product")


def test_fields_of_reads_back_what_columns_of_wrote() -> None:
    fields = fields_of(columns_of(VALUES, "Barclays", DENOMINATIONS))

    assert fields == VALUES
    assert str(fields[UPFRONT_FIELD]) == "97.14"  # 資料庫的小數 6 位去掉
    assert str(fields["Strike (%)"]) == "80"


def test_missing_fields_name_blank_required_fields_and_their_derived_codes() -> None:
    complete = columns_of(VALUES, "Barclays", DENOMINATIONS)

    assert missing_fields(complete) == []
    assert missing_fields({**complete, "maturity_date": None, "strike_pct": None}) == ["Strike (%)", "Maturity date"]
    assert missing_fields({**complete, "ko_memory": None, "ki_freq": None}) == ["KO Type", "Barrier Type"]
    assert missing_fields({**complete, "currency": None, "strike_date": None}, ("Currency",)) == ["Currency"]
    assert missing_fields(columns_of(dict.fromkeys(FIELDS), None, DENOMINATIONS)) == [
        name for name in REQUIRED if name != "Product"
    ]
