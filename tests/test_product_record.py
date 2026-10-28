"""商品紀錄：畫面的欄 ↔ `workflow.product` 的欄的轉換（#126），不需要資料庫。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from fcn_checker.products import FCN, FCN_EXPRESS, PRODUCT_RULES, product_name
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

    # 商品種類不可空白（ADR 0016）：寫入前由檢查擋下；不支援的商品種類同樣存空白，不猜成 FCN（#302）
    assert all(value is None for value in columns.values())
    assert columns_of({**VALUES, "Product": "BEN"}, "Barclays", DENOMINATIONS)["product"] is None


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
    assert missing_fields(columns_of(dict.fromkeys(FIELDS), None, DENOMINATIONS)) == list(REQUIRED)


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("FCN", FCN),
        (" fcn ", FCN),
        ("FCN_Express", FCN_EXPRESS),
        ("fcn_express", FCN_EXPRESS),
        ("BEN", None),
        (None, None),
    ],
)
def test_product_names_are_matched_without_case_or_surrounding_spaces(text, name) -> None:
    assert product_name(text) == name


def test_the_product_is_stored_the_way_it_is_named_in_the_rules() -> None:
    columns = columns_of({**VALUES, "Product": "fcn_express", "Guaranteed Periods (m)": 0}, "Barclays", DENOMINATIONS)

    assert (columns["product"], columns["non_call_months"]) == (FCN_EXPRESS, 0)


@pytest.mark.parametrize(
    ("product", "months", "problem"),
    [
        (FCN, 1, ""),
        (FCN, 12, ""),
        (FCN, 0, "至少 1"),
        (FCN_EXPRESS, 0, ""),
        (FCN_EXPRESS, 1, "FCN_Express 沒有 Non-Call，要填 0"),
    ],
)
def test_non_call_follows_the_product_rule(product, months, problem) -> None:
    assert PRODUCT_RULES[product].non_call_problem(months) == problem


@pytest.mark.parametrize("module", ["sp_workflow.product_record", "sp_workflow.mailbox"])
def test_loading_does_not_pull_in_the_daily_import(module: str) -> None:
    """`product_record`、`mailbox` 不依賴每日匯入（#201）：新的 Python 只 import 它，`daily_import` 不會被載入。"""
    import subprocess
    import sys

    probe = f"import sys, {module}; print('sp_workflow.daily_import' in sys.modules)"
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)

    assert result.stdout.strip() == "False"
