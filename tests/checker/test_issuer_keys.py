"""測試切點：審查標準上手表的鍵表 `config.ISSUER_KEYS`（Issue #490）——鍵表與 `IssuerStandard` 欄位一致、沒寫的鍵用預設或共用值、
`ISSUER_FIELDS`／`LEGACY_TABLES` 由鍵表導出。載入現有設定的結果另有各規則測試覆蓋。
"""

from __future__ import annotations

import dataclasses

from fcn_checker.config import ISSUER_FIELDS, ISSUER_KEYS, LEGACY_TABLES, SHARED, IssuerStandard
from harness import CONFIG

# IssuerStandard 不從上手表來的欄位：上手代號、由固定警語組成的寫法、所有上手共用的受託機構值
NOT_FROM_KEYS = {
    "issuer",
    "fixed_warnings",
    "distributor_name",
    "distributor_phone",
    "distributor_phone_equivalents",
    "distributor_address",
    "distributor_address_equivalents",
    "distributor_name_equivalents",
}


def test_every_issuer_key_has_one_issuer_standard_field_and_back():
    from_keys = [key.field(name) for name, key in ISSUER_KEYS.items() if key.attr is not None]
    assert len(from_keys) == len(set(from_keys))
    assert set(from_keys) | NOT_FROM_KEYS == {f.name for f in dataclasses.fields(IssuerStandard)}
    assert not set(from_keys) & NOT_FROM_KEYS


def test_derived_tables_follow_the_key_table():
    assert list(ISSUER_FIELDS) == list(ISSUER_KEYS)
    assert all(ISSUER_FIELDS[name] is key.read for name, key in ISSUER_KEYS.items())
    assert LEGACY_TABLES == {key.legacy: name for name, key in ISSUER_KEYS.items() if key.legacy}
    assert {"redemption_start_wordings", "product_names", "osu_clients", "distributor_name_exempt"}.isdisjoint(
        LEGACY_TABLES.values()
    )


def test_an_issuer_without_a_table_gets_the_defaults_and_shared_values():
    std = CONFIG.review_standard
    other = std.for_issuer("ZZZ")
    assert (other.fixed_warning, other.fixed_warning_occurrences) == (std.fixed_warning, std.fixed_warning_occurrences)
    assert other.fixed_warnings == (std.fixed_warning,)
    assert other.risk_level_formats == ("【{level}】",)
    assert (other.issuer_name, other.product_name, other.iis_fixed_warning_occurrences) == (None, None, None)
    assert (other.guarantor_names, other.agent_names, other.min_amount_multiples, other.product_names) == (
        {},
        {},
        {},
        {},
    )
    assert other.osu_clients is True and other.distributor_name_exempt == ()
    assert other.distributor_name == std.distributor_name
    for name, key in ISSUER_KEYS.items():
        if key.attr is not None and key.default is not SHARED:
            assert getattr(other, key.field(name)) == key.default, name


def test_table_values_are_copied_so_issuer_standards_do_not_share_dicts():
    std = CONFIG.review_standard
    a, b = std.for_issuer("ZZZ"), std.for_issuer("ZZZ")
    assert a.guarantor_names is not b.guarantor_names
    assert a.guarantor_names is not ISSUER_KEYS["guarantor_name"].default
