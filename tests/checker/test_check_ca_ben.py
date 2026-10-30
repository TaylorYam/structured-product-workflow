"""CA BEN 黑箱回歸（Issue #377）：合成說明書與投資人須知（ca_synth.BenSpec，現行版 Bonus）經批量入口核對。"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import fitz
import pytest

from ca_synth import BEN_PRODUCT, SB_PRODUCT, BenSpec, SbSpec, Spec, build_pdf, row
from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import CA, CA_BEN, CA_SUPER_BULL
from fcn_checker.parsers import ca_ben, ca_ben_iis
from fcn_checker.rules import ca_ben as ca_ben_rules
from fcn_checker.schema import CheckStatus as S
from harness import check_all, flagged, iis_path
from harness import field_problems as problems
from harness import results as rule
from pdf_writer import Edit
from reference_synth import REFERENCE_HEADERS, as_headers, build_reference_sheet

B0 = BenSpec()  # 2 檔、3 個月、K% 100、紅利 20%、參與率 100%、美元
HEADERS = [*REFERENCE_HEADERS, "參與率(%)"]
NO_KO = "提前出場"


def check_pair(tmp_path: Path, spec: Spec = B0, *, edits=(), headers=HEADERS, **overrides):
    """合成說明書＋投資人須知＋參考條件表一列，經批量入口核對，回傳（說明書, 投資人須知）。"""
    tmp_path.mkdir(parents=True, exist_ok=True)
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec, edits=edits)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [row(spec, **overrides)], headers)
    outcome = check_all(sheet, [pdf, iis_path(pdf)])
    ts = next(i for i in outcome.items if i.term_sheet == pdf)
    return ts, next(i for i in outcome.items if i.term_sheet != pdf)


def check(tmp_path: Path, spec: Spec = B0, **kw):
    return check_pair(tmp_path, spec, **kw)[0].report


def lines_of(path: Path):
    with fitz.open(path) as doc:
        return extract_lines(doc)


def compare_dates(report) -> dict:
    return {d.column: d.expected for d in report.backfill if d.column.startswith("比價日_") and d.expected != "-"}


def statuses(report, rid: str) -> set[S]:
    return {x.status for x in rule(report, rid)}


# ---------------------------------------------------------------- 現行版 Bonus 通過


@pytest.mark.parametrize(
    "spec",
    [
        B0,
        BenSpec(tenor=1),
        BenSpec(tenor=12, annual=Decimal("8.50"), below=Decimal("105")),
        BenSpec(tenor=6, participation=Decimal("120.00"), above=Decimal("140"), below=Decimal("105")),
    ],
    ids=["3m", "1m", "12m", "participation-120"],
)
def test_supported_ben_pairs_pass_and_fill_the_final_compare_date(tmp_path, spec):
    ts, iis = check_pair(tmp_path, spec)
    assert ts.report.template == CA_BEN.template_id
    assert iis.report.template == CA_BEN.iis.template_id
    assert ts.report.status == S.PASS, problems(ts.report)
    assert iis.report.status == S.PASS, problems(iis.report)
    assert ts.fills_sheet
    # 紅利不年化、和 Coupon p.a. (%) 相等；參與率和「參與率(%)」相等；Non-Call = 天期
    for rid in ("field.coupon_pa_pct", "field.participation_pct"):
        assert statuses(ts.report, rid) == {S.PASS}, rid
    assert rule(ts.report, "field.first_callable_period")[0].actual == spec.tenor
    for rid in (
        "doc.scenario_calculations",
        "doc.bonus",
        "doc.participation",
        "doc.no_autocall",
        "doc.no_coupon",
        "doc.redemption_formula",
        "doc.ki_consistency",
        "doc.template_variant",
    ):
        assert statuses(ts.report, rid) == {S.PASS}, rid
    for rid in ("iis.bonus", "iis.participation", "iis.no_coupon", "iis.redemption_formula", "iis.has_ki"):
        assert statuses(iis.report, rid) == {S.PASS}, rid
    # 比價日只有 `比價日_{天期}` = 期末訂價日
    assert compare_dates(ts.report) == {f"比價日_{spec.tenor}": spec.final_date}


def test_ben_and_other_ca_families_are_never_confused(tmp_path):
    fcn = Spec(product_code="086199990001")
    sb = SbSpec(product_code="086199990002")
    ben = BenSpec(product_code="086199990003")
    pdfs = [build_pdf(tmp_path / f"{s.product_code}_TS.pdf", s) for s in (fcn, sb, ben)]
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [row(fcn), row(sb), row(ben)], HEADERS)
    outcome = check_all(sheet, [*pdfs, *map(iis_path, pdfs)])
    detected = {i.term_sheet.name: rule(i.report, "template.detect")[0].actual for i in outcome.items}
    expected = {}
    for pdf, issuer in zip(pdfs, (CA, CA_SUPER_BULL, CA_BEN), strict=True):
        expected[pdf.name] = issuer.template_id
        expected[iis_path(pdf).name] = issuer.iis.template_id
    assert detected == expected


def test_ben_documents_are_not_read_as_each_other(tmp_path):
    """BEN 說明書不是投資人須知、投資人須知不是說明書；CA FCN、Super Bull 的兩份都不是 BEN。"""
    ben = build_pdf(tmp_path / "086199990003_TS.pdf", B0)
    assert not ca_ben_iis.detect(lines_of(ben)).matched
    assert not ca_ben.detect(lines_of(iis_path(ben))).matched
    for spec in (Spec(product_code="086199990001"), SbSpec(product_code="086199990002")):
        pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
        for detect, path in ((ca_ben.detect, pdf), (ca_ben_iis.detect, iis_path(pdf))):
            result = detect(lines_of(path))
            assert not result.matched and not result.unsupported, path.name


# ---------------------------------------------------------------- 範本版本、型態（不支援，不能人工放行）


def _unsupported(item, message: str) -> None:
    detect = rule(item.report, "template.detect")[0]
    assert (detect.status, detect.reason_code) == (S.REVIEW_REQUIRED, "template_unsupported")
    assert message in detect.message and "不能人工放行" in detect.message
    assert item.status_label == "不支援的範本版本" and item.release_problem


@pytest.mark.parametrize(
    ("spec", "edits", "message"),
    [
        (BenSpec(ko_column=True), (), ca_ben.OLD_VERSION),
        (B0, (Edit("相關。", f"相關，不會{NO_KO}。", "ts.art9"),), ca_ben.OLD_VERSION),
        (B0, (Edit("(5) 到期日：", "(5) 自動提前出場觀察日：無。(5) 到期日：", "ts.art13"),), ca_ben.OLD_VERSION),
        (B0, (Edit("參與率：100.00%。", "", "ts.art9"), Edit("紅利率： 20.00%。", "", "ts.art9")), ca_ben.OLD_VERSION),
        (B0, (Edit("不適用，本商品持有期間", "0%，本商品持有期間", "ts.art15"),), ca_ben.OLD_VERSION),
        (B0, (Edit("期初訂價)-100%))]", "期初訂價)-90%))]", "ts.art9"),), ca_ben.OLD_VERSION),
        (
            B0,
            (
                Edit("Bonus Note", "Digital Coupon Note", "ts.cover"),
                Edit("x[100%+最大值", "x紅利水準。x[100%+最大值", "ts.art9"),
            ),
            ca_ben.DIGITAL,
        ),
        (B0, (Edit("Bonus Note", "Digital Coupon Note", "ts.cover"),), ca_ben.MIXED),
    ],
    ids=["ko-column", "ko-word", "observation-date", "no-rate-lines", "min-rate", "formula", "digital", "mixed"],
)
def test_other_versions_and_shapes_are_unsupported(tmp_path, spec, edits, message):
    ts, _ = check_pair(tmp_path, spec, edits=edits)
    _unsupported(ts, message)


def test_digital_iis_and_old_iis_are_unsupported(tmp_path):
    digital = check_pair(tmp_path / "d", edits=(Edit("Bonus Note", "Digital Coupon Note", "iis"),))[1]
    _unsupported(digital, ca_ben.DIGITAL)
    old = check_pair(tmp_path / "o", edits=(Edit("參與率：100.00%。紅利率：20.00%。", "", "iis.coupon"),))[1]
    _unsupported(old, ca_ben_iis.OLD_VERSION)


@pytest.mark.parametrize(("edit", "name"), [("固定配息紅利", "name_zh"), ("Bonus Autocall Note", "name_en")])
def test_misnamed_iis_is_still_ben_and_the_name_mismatches(tmp_path, edit, name):
    """BEN-377-04：名稱多「固定配息」或「Autocall」的投資人須知仍辨識成 CA BEN，名稱判不一致（可人工放行）。"""
    old = "紅利" if name == "name_zh" else "Bonus Note"
    _, iis = check_pair(tmp_path, edits=(Edit(old, edit, "iis.cover"),))
    assert iis.report.template == CA_BEN.iis.template_id
    assert flagged(iis.report, f"iis.{name}", S.MISMATCH), problems(iis.report)
    assert not iis.release_problem


# ---------------------------------------------------------------- CA BEN 限定（BEN-377-07、CNH、匯率行）


@pytest.mark.parametrize(
    ("spec", "words"),
    [
        (BenSpec(count=1), "有 1 檔標的"),
        (BenSpec(count=3), "有 3 檔標的"),
        (BenSpec(strike=Decimal("90.00"), ko=Decimal("90.00")), "執行價 90.00%"),
        (BenSpec(currency_zh="日圓", market="JT"), "計價幣別是日圓"),
        (BenSpec(currency_zh="人民幣", currency_iso="CNH"), "境外人民幣"),
        (BenSpec(fx="美元/日圓=150.00"), "第 16 項有匯率"),
    ],
    ids=["single", "three", "k90", "jpy", "cnh", "fx"],
)
def test_unsampled_shapes_need_review(tmp_path, spec, words):
    report = check(tmp_path, spec)
    variant = rule(report, "doc.template_variant")[0]
    assert variant.status == S.REVIEW_REQUIRED, problems(report)
    assert words in variant.message
    assert report.status == S.REVIEW_REQUIRED


def test_sampled_shape_constants_are_the_bonus_samples():
    assert list(ca_ben_rules.SAMPLED_UNDERLYINGS) == [2]
    assert ca_ben_rules.SAMPLED_STRIKE_PCTS == {Decimal(100)}
    assert ca_ben_rules.SAMPLED_CURRENCIES == {"USD"}


# ---------------------------------------------------------------- 樣態與 Product（共用規則）


@pytest.mark.parametrize(
    ("spec", "product"),
    [(B0, "FCN"), (B0, SB_PRODUCT), (B0, None), (Spec(), BEN_PRODUCT)],
    ids=["ben-booked-as-fcn", "ben-booked-as-sb", "ben-blank-product", "fcn-booked-as-ben"],
)
def test_product_must_match_the_template_family(tmp_path, spec, product):
    for item in check_pair(tmp_path, spec, Product=product):
        assert flagged(item.report, "template.product_family", S.REVIEW_REQUIRED), problems(item.report)
        assert item.unsupported_variant and item.release_problem


@pytest.mark.parametrize(
    "column",
    [{"ko_memory": "Y"}, {"ki_type": "AM"}, {"first_callable_period": 2}, {"ko_observation": "D"}],
    ids=["memory", "ki", "non-call", "ko-daily"],
)
def test_ben_terms_on_the_sheet_must_be_the_supported_variant(tmp_path, column):
    for item in check_pair(tmp_path, B0, **as_headers(column)):
        assert flagged(item.report, "batch.variant", S.REVIEW_REQUIRED), problems(item.report)
        assert item.unsupported_variant and item.release_problem


# ---------------------------------------------------------------- 紅利、參與率：正式條款對、其他出處錯


@pytest.mark.parametrize(
    ("edits", "rid"),
    [
        ((Edit("紅利率：20.00%", "紅利率：21.00%", "ts.art16"),), "doc.bonus"),
        ((Edit("最大值（20.00%，", "最大值（21.00%，", "ts.art16.s1"),), "doc.bonus"),
        ((Edit("(5)參與率：100.00%。", "(5)參與率：90.00%。", "ts.art15"),), "doc.participation"),
        ((Edit("100.00% x（", "90.00% x（", "ts.art16.s2"),), "doc.participation"),
    ],
    ids=["assumption-bonus", "scenario-bonus", "art15-participation", "scenario-participation"],
)
def test_other_mentions_must_equal_article9(tmp_path, edits, rid):
    report = check(tmp_path, edits=edits)
    assert S.MISMATCH in statuses(report, rid), problems(report)


@pytest.mark.parametrize(
    ("old", "new", "rid"),
    [
        ("紅利率：20.00%。詳細", "紅利率：21.00%。詳細", "iis.bonus"),
        ("參與率：100.00%。紅利", "參與率：90.00%。紅利", "iis.participation"),
    ],
    ids=["bonus", "participation"],
)
def test_iis_bonus_and_participation_must_equal_the_term_sheet(tmp_path, old, new, rid):
    _, iis = check_pair(tmp_path, edits=(Edit(old, new, "iis.coupon"),))
    assert statuses(iis.report, rid) == {S.MISMATCH}, problems(iis.report)


def test_participation_must_equal_the_reference_sheet(tmp_path):
    report = check(tmp_path, **{"參與率(%)": 90})
    assert statuses(report, "field.participation_pct") == {S.MISMATCH}


def test_missing_participation_column_needs_review(tmp_path):
    report = check(tmp_path, headers=REFERENCE_HEADERS)
    assert statuses(report, "field.participation_pct") == {S.REVIEW_REQUIRED}


# ---------------------------------------------------------------- 沒有配息、到期公式


@pytest.mark.parametrize(
    ("old", "new", "where", "rid"),
    [
        ("(6) 配息日：本商品持有期間並無配息金額。", "(6) 配息日：每月。", "ts.art13", "doc.no_coupon"),
        ("則投資人可領回：", "則投資人可取回：", "ts.art9", "doc.redemption_formula"),
        ("股票之股數，", "股票，", "ts.art9", "doc.redemption_formula"),
    ],
    ids=["art13", "maturity", "delivery"],
)
def test_fixed_wordings_mismatch(tmp_path, old, new, where, rid):
    report = check(tmp_path, edits=(Edit(old, new, where),))
    assert S.MISMATCH in statuses(report, rid), problems(report)


def test_iis_formula_mismatch(tmp_path):
    _, iis = check_pair(tmp_path, edits=(Edit("則投資人可領回：", "則投資人可取回：", "iis.coupon"),))
    assert S.MISMATCH in statuses(iis.report, "iis.redemption_formula"), problems(iis.report)


# ---------------------------------------------------------------- 情境


@pytest.mark.parametrize(
    ("spec", "edits", "field"),
    [
        (B0, (Edit("＝15,000.00 美元  /", "＝14,000.00 美元  /", "ts.art16.s1"),), "情境一_return"),  # BEN-377-05
        (BenSpec(above=Decimal("115")), (), "情境一_assumed"),  # 漲幅沒有大於紅利
        (B0, (Edit("= 15,000.00 美元", "= 15,100.00 美元", "ts.art16.s1"),), "情境一_redemption"),
        (
            B0,
            (Edit("＝50.00%（平均年化報酬率：200.00%）", "＝50.00%（平均年化報酬率：100.00%）", "ts.art16.s1"),),
            "情境一_annualized",
        ),
        (B0, (Edit("= 99.552 股", "= 99.553 股", "ts.art16.s3"),), "情境三_shares"),
    ],
    ids=["return-amount", "gain-not-above-bonus", "amount", "annualized", "shares-rounded"],
)
def test_scenario_errors_mismatch(tmp_path, spec, edits, field):
    report = check(tmp_path, spec, edits=edits)
    assert rule(report, "doc.scenario_calculations", field)[0].status == S.MISMATCH, problems(report)


def test_return_amount_status_is_a_mismatch():
    assert ca_ben_rules.RETURN_AMOUNT_STATUS == S.MISMATCH
