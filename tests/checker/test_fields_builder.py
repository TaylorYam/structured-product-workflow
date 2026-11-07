"""測試切點：說明書 parser 的欄位累積器 `parsers.fields_builder`（Issue #487）——出處依代號分組、範本可無欄位補 absent、
其他沒交出的仍是 not_provided。不經 PDF；各家 parser 的實際讀法由 `test_check_*` 的合成說明書覆蓋。
"""

from __future__ import annotations

import pytest

from fcn_checker import standard_fields
from fcn_checker.parsers.fields_builder import FieldsBuilder, ParsedTermSheet
from fcn_checker.parsers.layout import TextIndex
from fcn_checker.schema import FieldStatus, Line, ParsedField
from fcn_checker.standard_fields import (
    MAY_BE_ABSENT,
    OCCURRENCE_LISTS,
    OCCURRENCE_NAMES,
    Occurrence,
    absent,
    occurrence_list,
)


def line(text: str, y: float = 0) -> Line:
    return Line(1, 0, y, 100, y + 10, text)


def present(name: str, value: object = "x") -> ParsedField:
    return ParsedField.present(name, value, [])


def test_occurrence_lists_are_one_list_per_code_and_known_codes():
    assert set(OCCURRENCE_LISTS) <= set(OCCURRENCE_NAMES)
    codes = [code for codes in standard_fields._LIST_CODES.values() for code in codes]
    assert len(codes) == len(set(codes)) == len(OCCURRENCE_LISTS)  # 一個代號只屬於一張清單
    assert set(OCCURRENCE_LISTS.values()) == {
        "print_dates",
        "subscription_dates",
        "min_amounts",
        "issue_price_others",
        "currency_others",
    }
    assert occurrence_list("print_date_final") == "print_dates"
    assert occurrence_list("wording_currency") == "currency_others"
    with pytest.raises(ValueError, match="distributor_name"):
        occurrence_list("distributor_name")


def test_put_reads_the_whole_match_and_returns_the_field():
    fields = FieldsBuilder()
    pf = fields.put("tenor_months", [line("天期：6個月")], r"天期：(\d+)個月", int)
    assert (pf.status, pf.value) == (FieldStatus.PRESENT, 6)
    assert fields["tenor_months"] is pf
    assert fields.put("isin", [line("沒有")], r"ISIN：(\w+)").status == FieldStatus.MISSING


def test_occurrences_are_grouped_by_code_in_registration_order_with_the_value_at_registration():
    fields = FieldsBuilder()
    fields["print_date_review"] = present("print_date_review", "a")
    fields["print_date_ch3"] = present("print_date_ch3", "b")
    fields["minimum_trade"] = present("minimum_trade", 1)
    fields.occur("print_date_review", "封面")
    fields.occur("minimum_trade", "第 7 條")
    fields.occur("print_date_ch3", "第三章", code="print_date_review", qualifier="甲")
    fields["print_date_review"] = present("print_date_review", "later")  # 登記之後改的值不影響已登記的出處
    helper = Occurrence("wording", "wording_currency", "說明文字", present("wording", "美元"))
    fields.add(helper)
    built = fields.build()
    prints = built["print_dates"].value
    assert [(o.field, o.code, o.where, o.value.value, o.qualifier) for o in prints] == [
        ("print_date_review", "print_date_review", "封面", "a", ""),
        ("print_date_ch3", "print_date_review", "第三章", "b", "甲"),
    ]
    assert [o.field for o in built["min_amounts"].value] == ["minimum_trade"]
    assert built["currency_others"].value == (helper,)
    assert "subscription_dates" not in built  # 沒登記過的清單不交出


def test_may_be_absent_fields_default_to_absent_and_others_stay_not_provided():
    fields = FieldsBuilder()
    fields["issuer_name_ch1"] = absent("issuer_name_ch1", "別的說法")  # 上手自己交出的說法優先
    fields["initial_pricing"] = present("initial_pricing", "close")
    sheet = ParsedTermSheet(fields.build(), TextIndex([]), None)
    assert sheet.f("issuer_name_ch1").note == "範本沒有別的說法"
    assert sheet.f("initial_pricing").value == "close"
    for name in set(MAY_BE_ABSENT) - {"issuer_name_ch1", "initial_pricing"}:
        pf = sheet.f(name)
        assert (pf.status, pf.note) == (FieldStatus.NOT_APPLICABLE, f"範本沒有{MAY_BE_ABSENT[name]}")
    assert sheet.f("isin") == standard_fields.not_provided("isin")


def test_a_list_cannot_be_both_delivered_and_registered():
    fields = FieldsBuilder()
    fields["minimum_trade"] = present("minimum_trade", 1)
    fields["min_amounts"] = standard_fields.occurrences("min_amounts", [])
    fields.occur("minimum_trade", "第 7 條")
    with pytest.raises(ValueError, match="min_amounts"):
        fields.build()
