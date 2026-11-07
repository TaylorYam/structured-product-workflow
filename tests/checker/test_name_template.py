"""測試切點：商品名稱樣板 `name_template`（Issue #488）——載入與語法檢查、（樣板, 事實）→ 預期名稱、（預期, 實際）→ 判定與
容差說明。不經 PDF；各上手實際樣板由 `test_check_*` 的合成說明書覆蓋。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fcn_checker.ingestion import IngestionError
from fcn_checker.name_template import load
from fcn_checker.rules import product_name
from fcn_checker.schema import CheckStatus as S
from fcn_checker.schema import DocKind, FieldStatus, ParsedField
from fcn_checker.standard_fields import PriceRow


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
        ({"en": "{tenor}M Note[ ({tenor}M)]"}, "en", 1, {}, ("1M Note", " (1M)")),  # 可有可無那段也填佔位符
        ({"zh": "*甲{tenor}個月[（乙）]"}, "zh", 6, {}, ("甲6個月", "（乙）")),  # 中文樣板也能用語法
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
    assert t.expected(lang, values(t, tenor=tenor, flags=flags))[:2] == want


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
        (
            {"en": "*{tenor} Months Note[ (U)]"},
            "en",
            "Maxi 6 Months Note (U)",
            True,
            "連續空白視為一個；結尾的「 (U)」可有可無；名稱前面多的字不核對",
        ),
        (
            {"en": "*{tenor} Months Note[ (U)]"},
            "en",
            "Maxi 6 Months Note",
            True,
            "連續空白視為一個；結尾的「 (U)」可有可無；名稱前面多的字不核對",
        ),
        (
            {"en": "*{tenor} Months Note[ (U)]"},
            "en",
            "Maxi 6 Months Note (V)",
            False,
            "連續空白視為一個；結尾的「 (U)」可有可無；名稱前面多的字不核對",
        ),
        (
            {"zh": "*甲{tenor}個月[（乙）]"},
            "zh",
            "前甲6個月(乙)",
            True,
            "忽略空白；全形／半形括號不計；結尾的「（乙）」可有可無；名稱前面多的字不核對",
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
        ({"en": "A[*B]"}, "「*」只能放在開頭"),
        ({"zh": "甲*乙"}, "「*」只能放在開頭"),
        ({"normalize_en": ["brackets"]}, "normalize_en 須是清單，可用的值：whitespace"),
        ({"normalize_zh": "brackets"}, "normalize_zh 須是清單"),
    ],
)
def test_loader_rejects_legacy_keys_and_misplaced_syntax(kw, message):
    with pytest.raises(IngestionError) as e:
        tpl(**{**BASE, **kw})
    assert message in str(e.value)


# 規則層：說明書事實 → 樣板旗標 → 結果（`rules.product_name.check`），用最小的假 ctx，不經 PDF。


class _TS:
    def __init__(self, fields):
        self.fields = fields

    def f(self, name):
        return self.fields.get(name) or ParsedField.missing(name, "x")


def _run(t, **facts):
    pf = {k: v if isinstance(v, ParsedField) else ParsedField.present(k, v, []) for k, v in facts.items()}
    base = {"tenor_months": 6, "currency_zh": "美元", "ko_memory": False, "name_zh": "X", "name_en": "X"}
    fields = {k: ParsedField.present(k, v, []) for k, v in base.items()} | pf
    ctx = SimpleNamespace(
        issuer="T",
        order={},
        issuer_std=SimpleNamespace(product_name_for=lambda product: t),
        std=SimpleNamespace(currency_zh_to_iso={"美元": "USD", "日圓": "JPY"}),
        document=DocKind.TERM_SHEET,
        ts=_TS(fields),
    )
    return {r.field: r for r in product_name.check(ctx)}


def test_ki_not_applicable_counts_as_no_ki():
    t = tpl(**BASE, en="{tenor}M {ki_en}Note", ki_en="KI ")
    r = _run(t, ki_type=ParsedField("ki_type", FieldStatus.NOT_APPLICABLE), name_en="6M Note")["name_en"]
    assert (r.status, r.expected) == (S.PASS, "6M Note")
    assert r.message == "依審查標準名稱樣板與說明書天期、幣別、是否記憶式、有無 KI組出"


def test_undecidable_flag_goes_to_manual_review():
    t = tpl(**BASE, en="{tenor}M {quanto_en}Note", quanto_en="Quanto ")
    rows = (PriceRow(None, {}, (), currency=None),)  # 讀不到報價幣別
    r = _run(t, underlying_prices=rows)["name_en"]
    assert r.status == S.REVIEW_REQUIRED
    assert "判斷不出名稱樣板的「quanto」旗標" in r.message


def test_quanto_flag_from_quote_currency():
    t = tpl(**BASE, en="{tenor}M {quanto_en}Note", quanto_en="Quanto ")
    rows = (PriceRow(None, {}, (), currency="日圓"),)
    r = _run(t, underlying_prices=rows, name_en="6M Quanto Note")["name_en"]
    assert (r.status, r.expected) == (S.PASS, "6M Quanto Note")


def test_single_underlying_without_sample_goes_to_manual_review():
    t = tpl(**BASE, en="{underlying_en} Note", basket_en="Basket", single_unsupported=True)
    r = _run(t, underlyings=("A US",))["name_en"]
    assert (r.status, r.reason_code) == (S.REVIEW_REQUIRED, "variant_not_covered")
    r = _run(t, underlyings=("A US", "B US"), name_en="Basket Note")["name_en"]
    assert (r.status, r.message) == (S.PASS, "依審查標準名稱樣板與說明書天期、幣別、是否記憶式、標的數組出")
