"""參考條件表的值 ⇄ 資料庫的欄（#498）：代碼表的反查與 UF 換算只在 `reference_values`。資料全部虛構。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from sp_workflow.product_record import KI_CODES, KO_CODES, invert_upfront
from sp_workflow.reference_values import NO_KI, barrier_type, ko_type, upfront_field

EMPTY = "-"


@pytest.mark.parametrize("name", list(KO_CODES))
def test_ko_type_reverses_the_code_table(name):
    assert ko_type(*KO_CODES[name]) == name


def test_ko_type_unknown_codes_are_none():
    assert ko_type("X", "Y") is None
    assert ko_type(None, None) is None


@pytest.mark.parametrize("name", list(KI_CODES))
def test_barrier_type_reverses_the_code_table(name):
    assert barrier_type(KI_CODES[name], EMPTY) == name


def test_blank_ki_is_no_ki_and_unsupported_codes_are_none():
    assert barrier_type(None, EMPTY) == NO_KI  # 空白與空值寫法「-」都是沒有 KI
    assert barrier_type("", EMPTY) == NO_KI
    assert barrier_type("P", EMPTY) is None  # 每期觀察 MKI 目前不支援


def test_upfront_field_inverts_numbers_and_keeps_the_rest():
    assert upfront_field(Decimal("1.5"), EMPTY) == invert_upfront(Decimal("1.5"))
    assert upfront_field(None, EMPTY) is None
    assert upfront_field(EMPTY, EMPTY) is None
    assert upfront_field("abc", EMPTY) == "abc"  # 不是數字照原樣，由檢查標出來
