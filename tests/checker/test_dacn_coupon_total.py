"""DACN 情境試算共用的 n/M 配息算法（rules/dacn.py，路線圖 #575）：區間計息每期配息與累積配息加總。

數值都是虛構的。
"""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, ROUND_UP
from decimal import Decimal as D

import pytest

from fcn_checker.rules import dacn

CENT = D("0.01")


@pytest.mark.parametrize(
    ("days", "total", "rounding", "expected"),
    [
        (17, 17, ROUND_HALF_UP, D("123.45")),  # (n ÷ M) = 1：和保證期同額
        (0, 17, ROUND_HALF_UP, D("0.00")),  # 沒有合格營業日
        (11, 17, ROUND_HALF_UP, D("79.88")),  # 79.879…
        (11, 17, ROUND_DOWN, D("79.87")),
        (11, 17, ROUND_UP, D("79.88")),
        (3, 17, ROUND_UP, D("21.79")),  # 21.785…
        (3, 17, ROUND_DOWN, D("21.78")),
    ],
)
def test_accrual_coupon_follows_the_written_rounding(days, total, rounding, expected):
    assert dacn.accrual_coupon(D("10000"), D("1.2345"), days, total, CENT, rounding) == expected


def test_accrual_coupon_rounds_half_up_at_the_midpoint():
    """剛好落在進位點（12.345）：四捨五入是 12.35；改成浮點數或銀行家進位（12.34）時這裡會擋下。"""
    assert dacn.accrual_coupon(D("1000"), D("1.2345"), 7, 7, CENT) == D("12.35")
    assert dacn.accrual_coupon(D("1000"), D("1.2345"), 7, 7, CENT, ROUND_DOWN) == D("12.34")


def test_accrual_coupon_rejects_unknown_rounding():
    with pytest.raises(ValueError, match="不支援的進位方式"):
        dacn.accrual_coupon(D("1000"), D("1.2345"), 7, 7, CENT, ROUND_HALF_EVEN)


def test_accrual_coupon_rounds_to_the_written_places():
    assert dacn.accrual_coupon(D("5000"), D("0.8765"), 9, 21, D(1)) == D("19")  # 18.78…，四捨五入到整數


def test_accrual_coupon_without_days_is_unknown():
    assert dacn.accrual_coupon(D("10000"), D("1.2345"), 5, 0, CENT) is None


def test_accrual_coupon_issues_explain_the_formula():
    args = (D("10000"), D("1.2345"), 11, 17, CENT)
    assert dacn.accrual_coupon_issues("第2~6 期配息", D("79.88"), *args) == []
    (issue,) = dacn.accrual_coupon_issues("第2~6 期配息", D("79.87"), *args)
    assert issue == "第2~6 期配息 79.87 應為 10000 × 1.2345% × (11 ÷ 17) = 79.88（四捨五入到小數點以下 2 位）"
    (issue,) = dacn.accrual_coupon_issues("配息", D("79.88"), *args, ROUND_DOWN)
    assert issue.endswith("= 79.87（無條件捨去到小數點以下 2 位）")
    (issue,) = dacn.accrual_coupon_issues("配息", D("1"), D("10000"), D("1.2345"), 5, 0, CENT)
    assert "M 是 0" in issue


def test_coupon_total_adds_rounded_coupons_times_periods():
    legs = [dacn.CouponLeg(D("123.45"), 1), dacn.CouponLeg(D("79.88"), 5)]
    assert dacn.coupon_total(legs) == D("522.85")
    assert dacn.coupon_total([]) == D(0)
    assert dacn.coupon_total_issues("累積配息", D("522.85"), legs) == []
    (issue,) = dacn.coupon_total_issues("累積配息", D("522.84"), legs)
    assert issue == "累積配息 522.84 應為 123.45 × 1 期 + 79.88 × 5 期 = 522.85"
