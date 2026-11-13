"""自動核對的商品種類（#302）：上手編號對照設定檔的 `[auto_check_products]`，以及批量核對攔下沒開放的商品種類
（參考條件表 Product 欄；同不支援樣態：不跑規則、不能人工放行）；商品規則回答商品條件的問題（#434）、
比價政策與核對樣態的問題（#435）。"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from barc_synth import Spec, build_not_barc_pdf, build_pdf
from fcn_checker.fields import (
    COUPON_BARRIER_FIELD,
    KI_BARRIER_FIELD,
    KO_BARRIER_FIELD,
    KO_STEP_FIELD,
    LOOKBACK_FIELD,
    NON_CALL_FIELD,
    PARTICIPATION_FIELD,
)
from fcn_checker.ingestion import IngestionError
from fcn_checker.products import (
    BEN,
    DACN,
    FCN,
    FCN_EXPRESS,
    FCN_ONESTAR,
    FCN_STAR,
    FCN_STEPDOWN,
    PRODUCT_RULES,
    SNOWBALL,
    SNOWBALL_STEPDOWN,
    SUPER_BULL,
    rule_or_fcn,
)
from fcn_checker.saving import run_batch
from fcn_checker.schema import CheckStatus
from fcn_checker.variants import load_auto_check_products, product_terms_text
from harness import CONFIG, ISSUER_PREFIXES, check_all, load_config, with_iis
from reference_synth import build_reference_sheet, reference_row

REVIEW = CheckStatus.REVIEW_REQUIRED
EXPRESS_OPENED = '\nFCN_Express = ["BARC", "CA", "HSBC"]\n'


def prefixes_with(tmp_path: Path, old: str, new: str) -> Path:
    text = ISSUER_PREFIXES.read_text(encoding="utf-8")
    assert old in text
    p = tmp_path / "prefixes.toml"
    p.write_text(text.replace(old, new), encoding="utf-8")
    return p


# ---------------------------------------------------------------- 設定檔


def test_repo_table_opens_fcn_everywhere_and_other_products_for_their_issuers():
    issuers = frozenset(CONFIG.issuer_prefixes.values())

    expected = {
        "FCN": issuers,
        "FCN_Express": frozenset({"BARC", "CA", "HSBC"}),
        "Super Bull": frozenset({"BNP", "CA", "HSBC"}),
        "BEN": frozenset({"CA", "HSBC", "MS"}),
        "DACN": frozenset({"BARC", "BNP", "DBS", "NATIXIS", "NOMURA", "SG"}),
        "FCN_Stepdown": frozenset({"BARC", "CA", "NATIXIS"}),
    }  # #304～#306、#310～#312、#314、#367～#371、#373、#375～#377、#544～#548
    assert CONFIG.auto_check_products == expected


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (EXPRESS_OPENED, '\nFCN_Express = ["FAKE"]\n', "FCN_Express 有上手編號對照沒有的上手：FAKE"),
        (EXPRESS_OPENED, '\nFCN_Express = "BARC"\n', "FCN_Express 須是上手代號的清單"),
        ("[auto_check_products]", "[products]", "[auto_check_products]不存在"),
    ],
)
def test_table_problems_fail_config_load(tmp_path, old, new, message):
    path = prefixes_with(tmp_path, old, new)
    with pytest.raises(IngestionError) as e:
        load_auto_check_products(path, CONFIG.issuer_prefixes.values())
    assert message in str(e.value)
    with pytest.raises(IngestionError):
        load_config(issuer_prefixes=path)


# ---------------------------------------------------------------- 批量核對


def run(tmp_path: Path, row_changes: dict, config=CONFIG):
    spec = Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [reference_row(spec, **row_changes)])
    outcome, _ = run_batch(config, sheet, with_iis([pdf]), tmp_path / "reports", root=tmp_path, now=None)
    return outcome


def product_result(item):
    [r] = [r for r in item.report.results if r.rule_id == "batch.variant"]
    return r


@pytest.mark.parametrize("product", ["FCN_Express", "Range Accrual"])
def test_products_not_opened_for_the_issuer_stop_both_documents(tmp_path, product):
    config = load_config(issuer_prefixes=prefixes_with(tmp_path, EXPRESS_OPENED, "\nFCN_Express = []\n"))
    outcome = run(tmp_path, {"Product": product}, config)
    message = (
        f"目前樣態不支援自動核對：BARC 還沒開放「{product}」自動核對（參考條件表 Product）；請人工核對，不能人工放行"
    )

    for item in outcome.items:
        assert (product_result(item).status, product_result(item).reason_code) == (REVIEW, "variant_unsupported")
        assert item.problem_messages == (message,)
        assert not item.identification.checked
        assert not any(r.rule_id.startswith(("field.", "doc.", "iis.")) for r in item.report.results)
        with pytest.raises(IngestionError):
            outcome.release(item)


@pytest.mark.parametrize("product", ["Super Bull", "BEN"])
def test_products_not_opened_for_barc_go_to_manual_review_with_the_repo_table(tmp_path, product):
    """Super Bull 沒開放 BARC（#310）、BEN 只開放 HSBC、CA（#375、#377）：照不支援樣態轉人工，不跑規則。"""
    outcome = run(tmp_path, {"Product": product})
    message = (
        f"目前樣態不支援自動核對：BARC 還沒開放「{product}」自動核對（參考條件表 Product）；請人工核對，不能人工放行"
    )

    assert [item.problem_messages for item in outcome.items] == [(message,)] * len(outcome.items)


@pytest.mark.parametrize("product", ["FCN", " fcn ", None])
def test_fcn_and_a_blank_product_are_checked_as_usual(tmp_path, product):
    outcome = run(tmp_path, {"Product": product})

    assert all(i.status == CheckStatus.PASS for i in outcome.items), [i.problem_messages for i in outcome.items]


def test_opening_a_product_for_the_issuer_lets_it_through(tmp_path):
    outcome = run(tmp_path, {"Product": "FCN_Express"})  # 設定檔開放 BARC（#304）

    assert not any(r.rule_id == "batch.variant" for i in outcome.items for r in i.report.results)


# ---------------------------------------------------------------- 範本辨識不到（workflow #426）


def run_unknown_template(tmp_path: Path, rows: list[dict]):
    """檔名是 BARC 的商品代號、內容不是任何已支援範本的說明書，參考條件表放 `rows`。"""
    spec = Spec()
    pdf = build_not_barc_pdf(tmp_path / f"{spec.product_code}_TS.pdf")
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", rows)
    [item] = check_all(sheet, [pdf]).items
    return item


def test_an_unknown_template_whose_product_is_not_opened_is_an_unsupported_variant(tmp_path):
    """SG BEN 回報包：範本辨識不到，但參考條件表該列 Product 沒開放給這家上手，照不支援樣態報「還沒開放」，不報範本不符。"""
    item = run_unknown_template(tmp_path, [reference_row(Spec(), Product="BEN")])

    assert item.unsupported_variant
    assert item.problem_messages == (
        "目前樣態不支援自動核對：BARC 還沒開放「BEN」自動核對（參考條件表 Product）；請人工核對，不能人工放行",
    )
    assert not any(r.rule_id == "template.detect" for r in item.report.results)
    assert item.release_problem == "上手不支援這個樣態，沒有範本可以核對，不能人工放行"


@pytest.mark.parametrize("rows", [[{"Product": "FCN"}], [{"Product": None}], []], ids=["fcn", "blank", "no-row"])
def test_an_unknown_template_is_still_reported_when_the_product_is_opened_or_missing(tmp_path, rows):
    item = run_unknown_template(tmp_path, [reference_row(Spec(), **changes) for changes in rows])

    assert not item.unsupported_variant
    [r] = [r for r in item.report.results if r.rule_id == "template.detect"]
    assert (r.status, r.reason_code) == (REVIEW, "template_unknown")


# ---------------------------------------------------------------- 商品規則回答商品條件的問題（#434）

ALL = (FCN, FCN_EXPRESS, SUPER_BULL, BEN, DACN, FCN_STEPDOWN, SNOWBALL, SNOWBALL_STEPDOWN, FCN_STAR, FCN_ONESTAR)
STEPPING = (FCN_STEPDOWN, SNOWBALL, SNOWBALL_STEPDOWN)  # 有 KO 降幅（#537）
STEP_REQUIRED = (FCN_STEPDOWN, SNOWBALL_STEPDOWN)
CAPPED = (FCN_STAR, FCN_ONESTAR)  # 到期有上限價（#538）
BEN_CHOICES = {"KO Type": "Period End", "Barrier Type": "NONE"}


@pytest.mark.parametrize("name", ALL)
def test_fixed_terms(name):
    rule = PRODUCT_RULES[name]
    ben = name == BEN
    assert rule.fixed_choices() == (BEN_CHOICES if ben else {})
    assert rule.order_overrides(6) == ({NON_CALL_FIELD: 6, **BEN_CHOICES, KI_BARRIER_FIELD: None} if ben else {})
    assert rule.fixed_non_call() == (0 if name == FCN_EXPRESS else None)
    assert rule.fixed_choice_problem("KO Type", "Period End") == ""
    assert rule.fixed_choice_problem("KO Type", "Daily") == (
        "BEN 不會提前出場、沒有 KI，要選 Period End" if ben else ""
    )


@pytest.mark.parametrize("name", ALL)
def test_required_optional_and_blank_terms(name):
    rule = PRODUCT_RULES[name]
    ben, barrier = name == BEN, name in (BEN, DACN)
    assert rule.optional_fields() == ((KO_BARRIER_FIELD,) if ben else ())
    assert rule.extra_required() == (
        (COUPON_BARRIER_FIELD,) if barrier else (KO_STEP_FIELD,) if name in STEP_REQUIRED else ()
    )
    assert rule.required_fields(("A", KO_BARRIER_FIELD, "B")) == (
        ("A", "B", COUPON_BARRIER_FIELD) if ben else ("A", KO_BARRIER_FIELD, "B", *rule.extra_required())
    )
    assert rule.blank_problem(PARTICIPATION_FIELD) == ("" if ben else f"{name} 沒有參與率，要空白")
    assert rule.blank_problem(LOOKBACK_FIELD) == ("" if ben else f"{name} 沒有回顧期間，要空白")
    assert rule.blank_problem(COUPON_BARRIER_FIELD) == ("" if barrier else f"{name} 沒有配息門檻，要空白")
    assert rule.blank_problem(KO_STEP_FIELD) == ("" if name in STEPPING else f"{name} 沒有KO降幅，要空白")
    assert rule.has_term("Currency")


@pytest.mark.parametrize(
    ("name", "value", "problem"),
    [
        (BEN, None, "BEN 必填（一般 100、Digital 0）"),
        (BEN, Decimal(-1), "不能是負數"),
        (BEN, Decimal(0), ""),
        (FCN, None, ""),
        (DACN, Decimal(100), "DACN 沒有參與率，要空白"),
    ],
)
def test_participation_problem(name, value, problem):
    assert PRODUCT_RULES[name].participation_problem(value) == problem


@pytest.mark.parametrize(
    ("name", "value", "problem"),
    [
        (FCN_STEPDOWN, Decimal(3), ""),
        (FCN_STEPDOWN, None, ""),  # 必填由 extra_required 管
        (SNOWBALL, Decimal(0), ""),
        (SNOWBALL_STEPDOWN, Decimal(-1), "不能是負數"),
        (FCN, None, ""),
        (FCN, Decimal(3), "FCN 沒有KO降幅，要空白"),
    ],
)
def test_ko_step_problem(name, value, problem):
    assert PRODUCT_RULES[name].ko_step_problem(value) == problem


@pytest.mark.parametrize("name", ALL)
def test_defaults_and_moved_terms(name):
    rule = PRODUCT_RULES[name]
    ben, barrier = name == BEN, name in (BEN, DACN)
    assert rule.default_participation() == (Decimal(100) if ben else None)
    assert rule.default_coupon_barrier(Decimal(80)) == (Decimal(80) if barrier else None)
    assert rule.moved_fields() == ({KO_BARRIER_FIELD: COUPON_BARRIER_FIELD} if ben else {})
    assert rule.ignored_prices() == (frozenset({"ko"}) if ben else frozenset())


@pytest.mark.parametrize("name", ALL)
def test_observation_policy(name):
    rule = PRODUCT_RULES[name]
    ben = name == BEN
    assert rule.final_date_can_ko() == (name not in (SUPER_BULL, BEN, *CAPPED))
    assert rule.delivery_needs_review() == (name in CAPPED)
    assert rule.non_call_is_tenor() == ben
    assert rule.ko_needs_review(Decimal(100)) == ben
    assert not rule.ko_needs_review(None)
    # Non-Call 涵蓋整個天期就不會提前出場，任何商品都一樣；空白時當作會
    assert rule.no_early_exit(12, 12) and rule.no_early_exit(13, 12)
    assert not rule.no_early_exit(11, 12)
    assert not rule.no_early_exit(None, 12) and not rule.no_early_exit(12, None)


def test_a_tenor_non_call_product_must_match_the_tenor_and_then_never_exits_early():
    rule = PRODUCT_RULES[BEN]
    assert rule.non_call_problem(12, 12) == "" and rule.no_early_exit(12, 12)
    assert rule.non_call_problem(6, 12) == "BEN 不會提前出場，要等於天期（12）"


@pytest.mark.parametrize("name", ALL)
def test_check_terms(name):
    rule = PRODUCT_RULES[name]
    assert rule.blank_coupon_barrier_uses_strike() == (name == DACN)
    assert rule.only_periodic_no_ki() == (name in (SUPER_BULL, BEN))


@pytest.mark.parametrize(
    ("name", "text"),
    [
        (SUPER_BULL, "KO(Freq) P、KO(memo) N、KI(Freq) -"),
        (BEN, "KO(Freq) P、KO(memo) N、KI(Freq) -、Non-Call = 天期"),
        (FCN, ""),
        (DACN, ""),
        ("Range Accrual", ""),
    ],
)
def test_variant_terms_follow_the_rule(name, text):
    assert product_terms_text(name) == text


@pytest.mark.parametrize("product", ["Range Accrual", None, 3, ""])
def test_unsupported_products_follow_fcn(product):
    assert rule_or_fcn(product) is PRODUCT_RULES[FCN]


@pytest.mark.parametrize("name", ALL)
def test_supported_products_get_their_own_rule(name):
    assert rule_or_fcn(name) is PRODUCT_RULES[name]
