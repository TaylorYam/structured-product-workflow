"""回填值（#444）：回填決策 → 型別化的回填值；人工放行的單位面額由核對端交出。只用虛構值。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from fcn_checker import backfill
from fcn_checker.backfill import BackfillAction, CellDecision
from fcn_checker.backfill_values import (
    CHECKED_MARKS,
    DENOMINATION,
    ISIN,
    ROW_FIELDS,
    AutocallSlot,
    BackfillValue,
    UnderlyingPrice,
    released_denomination,
)
from fcn_checker.schema import CheckReport, CheckResult, CheckStatus, Item

A = BackfillAction


def decision(column: str, cell: str, expected, action: BackfillAction, target) -> CellDecision:
    return CellDecision(column, cell, None, expected, action, target)


def test_written_decisions_become_values_in_order_without_the_checked_marks() -> None:
    day = dt.date(2030, 1, 9)
    decisions = [
        decision("TS", "A4", "V", A.FILL, CHECKED_MARKS[0]),
        decision("ISIN Code", "F4", "XS0000000001", A.FILL, ISIN),
        decision("比價日_1", "X4", "-", A.FILL, AutocallSlot(1)),
        decision("比價日_2", "Y4", day, A.MATCH, AutocallSlot(2)),
        decision("比價日_3", "Z4", day, A.MISMATCH, AutocallSlot(3)),
        decision("UL_2_KO價", "BZ4", Decimal("12.5"), A.OVERWRITE, UnderlyingPrice(2, "ko")),
    ]

    assert backfill.values(decisions, "-") == (
        BackfillValue(ISIN, "XS0000000001", "ISIN Code"),
        BackfillValue(AutocallSlot(1), None, "比價日_1"),  # 空值寫法 → None
        BackfillValue(UnderlyingPrice(2, "ko"), Decimal("12.5"), "UL_2_KO價"),
    )


def test_targets_name_their_standard_fields() -> None:
    assert (AutocallSlot(3).field, UnderlyingPrice(2, "initial").field) == (
        "autocall_date_3",
        "underlying_2_initial_price",
    )
    assert {t.field for t in ROW_FIELDS} == {
        "checked_term_sheet",
        "checked_iis",
        "isin",
        "issue_date",
        "lookback_end_date",
        *(f"autocall_date_{n}" for n in range(1, 13)),
    }


def report(*results: CheckResult) -> CheckReport:
    return CheckReport(CheckStatus.MISMATCH, None, list(results), [])


def denomination(actual, status=CheckStatus.MISMATCH) -> CheckResult:
    return CheckResult(
        rule_id="field.denomination", field="denomination", status=status, actual=actual, item=Item.note("單位面額")
    )


def test_released_denomination_is_the_single_term_sheet_value() -> None:
    assert released_denomination(report(denomination(10000))) == BackfillValue(
        DENOMINATION, Decimal("10000"), "單位面額"
    )
    assert released_denomination(report(denomination(10000, CheckStatus.PASS))) is None
    assert released_denomination(report(denomination(10000), denomination(5000))) is None
    assert released_denomination(report(denomination("不是數字"))) is None
