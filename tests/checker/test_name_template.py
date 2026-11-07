"""測試切點：商品名稱樣板 `name_template`（Issue #488）——載入與語法檢查、（樣板, 事實）→ 預期名稱、（預期, 實際）→ 判定與
容差說明。不經 PDF；各上手實際樣板由 `test_check_*` 的合成說明書覆蓋。
"""

from __future__ import annotations

import pytest

from fcn_checker.ingestion import IngestionError
from fcn_checker.name_template import load


def tpl(**kw):
    return load({"zh": "甲{tenor}個月{ccy_zh}{memory_zh}乙", "en": "{tenor} Months {ccy} {memory_en}Note", **kw}, "t")


BASE = {"memory_zh": "記憶式", "memory_en": "Memory "}


def values(t, *, tenor=6, flags=None):
    return t.values(tenor=tenor, ccy_zh="美元", iso="USD", flags={"memory": False, **(flags or {})})


@pytest.mark.parametrize(
    ("kw", "lang", "tenor", "flags", "want"),
    [
        ({}, "zh", 6, {}, ("甲6個月美元乙", "")),
        ({}, "zh", 6, {"memory": True}, ("甲6個月美元記憶式乙", "")),
        ({}, "en", 1, {}, ("1 Month USD Note", "")),  # 天期 1 個月改單數
        ({"ccy_aliases": {"USD": "US$"}}, "en", 6, {}, ("6 Months US$ Note", "")),
        ({"en": "*{tenor}M Note"}, "en", 6, {}, ("6M Note", "")),
        ({"en": "{tenor}M Note[ (Unsecured)]"}, "en", 6, {}, ("6M Note", " (Unsecured)")),
        (
            {"en": "{underlying_en} Note", "single_en": "Share", "basket_en": "Basket"},
            "en",
            6,
            {"maxi": True},
            ("Basket Note", ""),
        ),
        (
            {"en": "{ko_observation_en} Note", "daily_en": "Daily", "periodic_en": "Periodic"},
            "en",
            6,
            {"daily": False},
            ("Periodic Note", ""),
        ),
        (
            {"en": "{autocall_en}Note", "autocall_en": "Callable ", "autocall_memory_en": "Memory Callable "},
            "en",
            6,
            {"autocall": True, "memory": True},
            ("Memory Callable Note", ""),
        ),
        (
            {"en": "{payoff_en}", "digital_en": "Digital", "enhanced_en": "Bonus"},
            "en",
            6,
            {"digital": False},
            ("Bonus", ""),
        ),
    ],
)
def test_template_and_facts_give_the_expected_name(kw, lang, tenor, flags, want):
    t = tpl(**{**BASE, **kw})
    assert t.expected(lang, values(t, tenor=tenor, flags=flags)) == want


@pytest.mark.parametrize(
    ("kw", "lang", "actual", "ok", "tol"),
    [
        ({}, "zh", "甲 6 個月美元乙", True, "忽略空白；全形／半形括號不計"),
        ({"zh": "甲{tenor}（個月）"}, "zh", "甲6(個月)", True, "忽略空白；全形／半形括號不計"),
        ({"zh": "甲{tenor}（個月）", "normalize_zh": []}, "zh", "甲6(個月)", False, "忽略空白"),
        ({}, "en", "6  Months USD  Note ", True, "連續空白視為一個"),
        ({}, "en", "6Months USD Note", False, "連續空白視為一個"),
        ({"normalize_en": ["whitespace"]}, "en", "6MonthsUSDNote", True, "忽略所有空白與換行"),
        ({"en": "*{tenor} Months Note"}, "en", "Maxi 6 Months Note", True, "連續空白視為一個；名稱前面多的字不核對"),
        ({"en": "*{tenor} Months Note"}, "en", "6 Months Note X", False, "連續空白視為一個；名稱前面多的字不核對"),
        (
            {"en": "{tenor} Months Note[ (U)]"},
            "en",
            "6 Months Note (U)",
            True,
            "連續空白視為一個；結尾的「 (U)」可有可無",
        ),
        ({"en": "{tenor} Months Note[ (U)]"}, "en", "6 Months Note", True, "連續空白視為一個；結尾的「 (U)」可有可無"),
        (
            {"en": "{tenor} Months Note[ (U)]"},
            "en",
            "6 Months Note (V)",
            False,
            "連續空白視為一個；結尾的「 (U)」可有可無",
        ),
    ],
)
def test_expected_and_actual_give_the_verdict_and_tolerance(kw, lang, actual, ok, tol):
    t = tpl(**{**BASE, **kw})
    assert t.matches(lang, t.expected(lang, values(t)), actual) == (ok, tol)


@pytest.mark.parametrize(
    ("kw", "message"),
    [
        ({"ignore_prefix_en": True}, "ignore_prefix_en 請改成 英文樣板開頭加 *"),
        ({"optional_suffix_en": "X"}, "optional_suffix_en 請改成 英文樣板結尾加 ["),
        ({"normalize_brackets": True}, 'normalize_brackets 請改成 normalize_zh = ["brackets"]'),
        ({"ignore_whitespace_en": True}, 'ignore_whitespace_en 請改成 normalize_en = ["whitespace"]'),
        ({"en": "A *B"}, "「*」只能放在開頭"),
        ({"en": "A [B] C"}, "「[…]」只能有一段、放在結尾"),
        ({"en": "A [B [C]]"}, "「[…]」只能有一段、放在結尾"),
        ({"normalize_en": ["brackets"]}, "normalize_en 須是清單，可用的值：whitespace"),
        ({"normalize_zh": "brackets"}, "normalize_zh 須是清單"),
    ],
)
def test_loader_rejects_legacy_keys_and_misplaced_syntax(kw, message):
    with pytest.raises(IngestionError) as e:
        tpl(**{**BASE, **kw})
    assert message in str(e.value)
