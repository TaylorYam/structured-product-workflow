"""Natixis DACN 黑箱回歸（workflow #371）：合成說明書與投資人須知（natixis_synth.DacnSpec）經批量入口核對。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import fitz
import pytest

from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import NATIXIS, NATIXIS_DACN
from fcn_checker.schema import CheckStatus as S
from harness import check_all, flagged, iis_path
from harness import field_problems as problems
from harness import results as rule
from natixis_synth import DACN_PRODUCT, DacnSpec, Spec, build_pdf, row
from pdf_writer import Edit
from reference_synth import REFERENCE_HEADERS, build_reference_sheet

# 3 檔、6 個月、KO 每日＋記憶式、KI 每日、保證 1 期 = Non-Call 1、年利率 18%（月配息率 1.5000%）、執行價 75%
D0 = DacnSpec()
WITH_BARRIER = [*REFERENCE_HEADERS, "配息門檻(%)"]


def check_pair(tmp_path: Path, spec: Spec = D0, *, pdf_spec: Spec | None = None, edits=(), headers=None, **overrides):
    """合成說明書（`pdf_spec`，預設同 `spec`）＋投資人須知＋參考條件表一列（`headers` 預設沒有配息門檻欄），經批量入口核對，
    回傳（說明書, 投資人須知）的核對項目。"""
    tmp_path.mkdir(parents=True, exist_ok=True)
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", pdf_spec or spec, edits=edits)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [row(spec, **overrides)], headers)
    outcome = check_all(sheet, [pdf, iis_path(pdf)])
    ts = next(i for i in outcome.items if i.term_sheet == pdf)
    return ts, next(i for i in outcome.items if i.term_sheet != pdf)


def check(tmp_path: Path, spec: Spec = D0, **kw):
    return check_pair(tmp_path, spec, **kw)[0].report


def lines_of(path: Path):
    with fitz.open(path) as doc:
        return extract_lines(doc)


def only(report, rule_id: str, status: S = S.MISMATCH) -> None:
    """只有 `rule_id` 判錯（狀態 `status`）。"""
    assert flagged(report, rule_id, status), problems(report)
    assert {r for r, _, _ in problems(report)} == {rule_id}, problems(report)


@pytest.mark.parametrize(
    ("spec", "headers"),
    [
        (D0, None),
        (D0, WITH_BARRIER),
        (DacnSpec(ki="AM", tenor=12, ko=Decimal("95.00"), strike=Decimal("60.00"), ki_pct=Decimal("50.00")), None),
        (DacnSpec(ki="none", tenor=3, market="JT", currency_zh="日圓", annual=Decimal("29.00")), WITH_BARRIER),
        (DacnSpec(count=3, ko=Decimal("100.00"), iis_price_bullet="●"), None),
    ],
    ids=["3UL-no-column", "3UL-column", "KI-AM-12m", "JPY-no-KI", "iis-bullet"],
)
def test_supported_dacn_pairs_pass_and_fill(tmp_path, spec, headers):
    ts, iis = check_pair(tmp_path, spec, headers=headers)
    assert ts.report.template == NATIXIS_DACN.template_id
    assert iis.report.template == NATIXIS_DACN.iis.template_id
    assert ts.report.status == S.PASS, problems(ts.report)
    assert iis.report.status == S.PASS, problems(iis.report)
    assert ts.fills_sheet
    barrier = rule(ts.report, "field.coupon_barrier_pct")[0]
    assert barrier.status == S.PASS
    assert ("沒有配息門檻欄" in barrier.message) == (headers is None)
    for rid in (
        "doc.guaranteed_periods",
        "doc.scenario_terms",
        "doc.template_variant",
        "doc.table_header_pct",
        "standard.product_name",
        "derive.monthly_coupon",
        "scenario.params",
        "scenario.reprint",
    ):
        assert rule(ts.report, rid) and all(x.status == S.PASS for x in rule(ts.report, rid)), rid
    for rid in ("iis.coupon_barrier_term_sheet", "field.coupon_pa_pct"):
        assert rule(iis.report, rid) and all(x.status == S.PASS for x in rule(iis.report, rid)), rid


def test_fcn_and_dacn_templates_are_never_confused(tmp_path):
    fcn, dacn = Spec(product_code="328199990001"), DacnSpec(product_code="328199990002")
    pdfs = [build_pdf(tmp_path / f"{s.product_code}_TS.pdf", s) for s in (fcn, dacn)]
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [row(fcn), row(dacn)])
    outcome = check_all(sheet, [*pdfs, *map(iis_path, pdfs)])
    detected = {i.term_sheet.name: rule(i.report, "template.detect")[0].actual for i in outcome.items}
    assert detected == {
        pdfs[0].name: NATIXIS.template_id,
        iis_path(pdfs[0]).name: NATIXIS.iis.template_id,
        pdfs[1].name: NATIXIS_DACN.template_id,
        iis_path(pdfs[1]).name: NATIXIS_DACN.iis.template_id,
    }
    assert all(i.report.status == S.PASS for i in outcome.items), [problems(i.report) for i in outcome.items]


def test_term_sheet_and_investor_sheet_are_not_taken_for_each_other(tmp_path):
    pdf = build_pdf(tmp_path / f"{D0.product_code}_TS.pdf", D0)
    ts_lines, iis_lines = (lines_of(x) for x in (pdf, iis_path(pdf)))
    assert NATIXIS_DACN.detect(ts_lines).matched
    assert NATIXIS_DACN.iis.detect(iis_lines).matched
    for detect in (NATIXIS_DACN.iis.detect, NATIXIS.iis.detect, NATIXIS.detect):
        assert not detect(ts_lines).matched
    for detect in (NATIXIS_DACN.detect, NATIXIS.detect, NATIXIS.iis.detect):
        assert not detect(iis_lines).matched


def test_dacn_named_like_fcn_is_not_taken_by_the_fcn_template(tmp_path):
    """DACN 說明書英文名稱誤寫成 FCN：FCN 範本因區間計息寫法不收，DACN 範本認得出但不是現行版（不支援的範本版本）。"""
    edit = Edit("Autocallable Daily Range Accrual Note", "Autocallable Fixed Coupon Note", "ts.cover")
    ts, _ = check_pair(tmp_path, edits=[edit])
    detect = rule(ts.report, "template.detect")[0]
    assert (detect.status, detect.reason_code) == (S.REVIEW_REQUIRED, "template_unsupported")
    assert "Daily Range Accrual Note" in detect.message


def test_fcn_named_like_dacn_matches_neither_template(tmp_path):
    """FCN 說明書英文名稱誤寫成 DACN：FCN 名稱條件不成立、DACN 沒有區間計息公式 → 範本不符（不是 DACN 也不是 FCN）。"""
    edit = Edit("Autocallable Fixed Coupon Note", "Autocallable Daily Range Accrual Note", "ts.cover")
    r = check(tmp_path, Spec(), edits=[edit])
    detect = rule(r, "template.detect")[0]
    assert detect.status == S.REVIEW_REQUIRED and detect.reason_code != "template_unsupported"
    assert "變動配息金額" in detect.message


def test_fcn_ko_prorate_clause_is_not_taken_for_dacn(tmp_path):
    """Natixis FCN 的 KO 期按比例條款也寫「x (n/N)」：只要沒有「變動配息金額」與配息界限價格，仍是 FCN。"""
    edit = Edit(
        "配息金額 = 配息觀察期間結束日持有之商品單位數x 每單位商品面額 x 月配息率",
        "配息金額 = 配息觀察期間結束日持有之商品單位數x 每單位商品面額 x 月配息率x (n/N)",
        "ts.art13",
    )
    r = check(tmp_path, Spec(), edits=[edit])
    assert rule(r, "template.detect")[0].actual == NATIXIS.template_id
    assert r.status == S.PASS, problems(r)


def test_corrected_spelling_of_the_barrier_is_accepted(tmp_path):
    """第 13 項 b. 的「配息界線價格」（樣本的錯字）更正成「界限」也是現行版（待使用者確認）。"""
    ts, _ = check_pair(tmp_path, edits=[Edit("配息界線價格", "配息界限價格", "ts.art13")])
    assert ts.report.template == NATIXIS_DACN.template_id
    assert ts.report.status == S.PASS, problems(ts.report)


@pytest.mark.parametrize(
    "edits",
    [
        [Edit("期間內的總配息觀察日天數", "期間內的總預定交易日天數", "ts.art13")],
        [Edit("係指於配息觀察期間之各預定交易日", "係指於配息觀察期間之各交易日", "ts.art13")],
        [Edit("x 月配息率x (n/N)，其中：", "x 月配息率x (n/N)：", "ts.art13")],
        [Edit("區間計息", "固定配息", "ts.cover")],
        [Edit("格(每日觀", "格(期末觀", "ts.art9.table")],
    ],
    ids=["total-days", "observation-day", "ko-period", "zh-name", "table-head"],
)
def test_other_term_sheet_wording_is_not_supported(tmp_path, edits):
    ts, _ = check_pair(tmp_path, edits=edits)
    detect = rule(ts.report, "template.detect")[0]
    assert (detect.status, detect.reason_code) == (S.REVIEW_REQUIRED, "template_unsupported")
    assert "不能人工放行" in detect.message
    assert ts.status_label == "不支援的範本版本" and ts.release_problem


def test_trade_date_before_june_2026_is_not_supported(tmp_path):
    ts, _ = check_pair(tmp_path, DacnSpec(trade_date=dt.date(2026, 5, 4), issue_date=dt.date(2026, 5, 11)))
    detect = rule(ts.report, "template.detect")[0]
    assert (detect.status, detect.reason_code) == (S.REVIEW_REQUIRED, "template_unsupported")
    assert "2026-06-01" in detect.message


@pytest.mark.parametrize(
    "edit",
    [
        Edit("期間內的總配息觀察日天數", "期間內的總預定交易日天數", "iis.coupon"),
        Edit("「配息界限價格」係指", "「配息價格」係指", "iis.coupon"),
    ],
    ids=["total-days", "barrier-definition"],
)
def test_other_investor_sheet_wording_is_not_supported(tmp_path, edit):
    _, iis = check_pair(tmp_path, edits=[edit])
    detect = rule(iis.report, "template.detect")[0]
    assert (detect.status, detect.reason_code) == (S.REVIEW_REQUIRED, "template_unsupported")


def test_accrual_definition_against_another_price_is_not_dacn(tmp_path):
    """n 定義改成比別的價格：不是區間計息（商品家族條件不成立），也不是 FCN → 範本不符、轉人工覆核。"""
    edit = Edit("皆高於或等於配息界限價格之配息觀察日天數", "皆高於或等於期初進場價格之配息觀察日天數", "ts.art13")
    ts, _ = check_pair(tmp_path, edits=[edit])
    detect = rule(ts.report, "template.detect")[0]
    assert detect.status == S.REVIEW_REQUIRED and detect.reason_code != "template_unsupported"


@pytest.mark.parametrize(
    ("spec", "product"),
    [(D0, "FCN"), (D0, None), (Spec(), DACN_PRODUCT)],
    ids=["dacn-booked-as-fcn", "dacn-blank-product", "fcn-booked-as-dacn"],
)
def test_product_must_match_the_template_family(tmp_path, spec, product):
    ts, iis = check_pair(tmp_path, spec, Product=product)
    for item in (ts, iis):
        assert flagged(item.report, "template.product_family", S.REVIEW_REQUIRED)
        assert item.unsupported_variant and item.release_problem


@pytest.mark.parametrize(
    ("value", "status"),
    [(76.0, S.MISMATCH), (None, S.REVIEW_REQUIRED), ("-", S.REVIEW_REQUIRED)],
    ids=["differs", "blank", "empty-value"],
)
def test_coupon_barrier_column(tmp_path, value, status):
    """表上有配息門檻欄：說明書配息門檻（第 9 項欄頭 %）和它比；空白或空值寫法轉人工覆核（DACN 一定要有值）。"""
    r = check(tmp_path, headers=WITH_BARRIER, **{"配息門檻(%)": value})
    only(r, "field.coupon_barrier_pct", status)


def test_without_the_column_the_barrier_is_compared_with_the_strike(tmp_path):
    """舊 Excel 沒有配息門檻欄：和 K(%) 比；K(%) 和說明書不同時配息門檻與執行價一起判錯。"""
    r = check(tmp_path, **{"K(%)": 76.0})
    assert flagged(r, "field.coupon_barrier_pct", S.MISMATCH), problems(r)
    assert flagged(r, "field.strike_pct", S.MISMATCH)


def test_fcn_rows_get_no_coupon_barrier_result(tmp_path):
    r = check(tmp_path, Spec(), headers=WITH_BARRIER, **{"配息門檻(%)": "-"})
    assert r.status == S.PASS, problems(r)
    assert not [x for x in r.results if x.rule_id == "field.coupon_barrier_pct"]


def test_barrier_header_must_match_the_strike_definition(tmp_path):
    """第 9 項「配息界限價格(每日觀察)/執行價格」欄頭 % 和第 15 項執行價格定義不同：表頭 % 判錯，配息門檻也和 K(%) 不同。"""
    r = check(tmp_path, edits=[Edit("之75.00%)", "之76.00%)", "ts.art9.table")])
    assert flagged(r, "doc.table_header_pct", S.MISMATCH), problems(r)
    assert flagged(r, "field.coupon_barrier_pct", S.MISMATCH), problems(r)


def test_investor_sheet_barrier_must_match_the_term_sheet(tmp_path):
    edit = Edit("期初進場價格之75.00%。", "期初進場價格之76.00%。", "iis.coupon")
    ts, iis = check_pair(tmp_path, edits=[edit])
    assert ts.report.status == S.PASS, problems(ts.report)
    only(iis.report, "iis.coupon_barrier_term_sheet")


@pytest.mark.parametrize(
    ("edit", "rule_id"),
    [
        (Edit("月配息率：1.5000%", "月配息率：1.6000%", "ts.art13"), "derive.monthly_coupon"),
        (Edit("x 1.5000% x (10/20)", "x 1.6000% x (10/20)", "ts.art16.s3.r4"), "scenario.params"),
        (Edit("x 1.5000% =", "x 1.6000% =", "ts.art16.s1"), "scenario.params"),
        (Edit("x 12.00=18.00%", "x 12.00=18.10%", "ts.art16"), "scenario.params"),
    ],
    ids=["art13-monthly", "scenario3-accrual", "scenario1-guaranteed", "scenario1-annualized"],
)
def test_other_places_of_the_rate_must_match_article13(tmp_path, edit, rule_id):
    """第 13 項年利率正確（= 參考條件表），其他出處錯：只有那一處判錯（投資人須知不受影響）。"""
    ts, iis = check_pair(tmp_path, edits=[edit])
    assert flagged(ts.report, rule_id, S.MISMATCH), problems(ts.report)
    assert {r for r, _, _ in problems(ts.report)} <= {rule_id, "derive.monthly_coupon", "scenario.params"}
    assert iis.report.status == S.PASS, problems(iis.report)


def test_annualized_return_of_accrual_scenarios_is_not_checked(tmp_path):
    """情境二以後假設計息一半天數，平均年化報酬率不是年利率：不核對（改了也不判錯）。"""
    r = check(tmp_path, edits=[Edit("平均年化報酬率為", "平均年化報酬率約為", "ts.art16.s2")])
    assert r.status == S.PASS, problems(r)


def test_annual_rate_differs_from_the_sheet(tmp_path):
    ts, iis = check_pair(tmp_path, **{"Coupon p.a. (%)": 18.5})
    assert flagged(ts.report, "field.coupon_pa_pct", S.MISMATCH), problems(ts.report)
    assert flagged(iis.report, "field.coupon_pa_pct", S.MISMATCH), problems(iis.report)


def test_guaranteed_periods_must_equal_non_call(tmp_path):
    """日期表保證 2 期，但 Non-Call 1：保證期數 ≠ Non-Call（沒有樣本的型態另轉人工）。"""
    r = check(tmp_path, D0, pdf_spec=DacnSpec(guaranteed=2))
    assert flagged(r, "doc.guaranteed_periods", S.MISMATCH), problems(r)
    assert flagged(r, "doc.template_variant", S.REVIEW_REQUIRED)


def test_guaranteed_rows_must_come_first(tmp_path):
    """日期表的「固定配息金額」夾在「變動配息金額」中間：保證期數讀不到，轉人工覆核。"""
    r = check(tmp_path, D0, pdf_spec=DacnSpec(kinds=("固定", "變動", "固定", "變動", "變動", "變動")))
    assert flagged(r, "doc.guaranteed_periods", S.REVIEW_REQUIRED), problems(r)


@pytest.mark.parametrize(
    "edit",
    [
        Edit(" x (10/20) =", " =", "ts.art16.s2.r3"),
        Edit("x 1.5000% =", "x 1.5000% x (10/20) =", "ts.art16.s3.r1"),
    ],
    ids=["accrual-row-without-nN", "guaranteed-row-with-nN"],
)
def test_scenario_rows_must_follow_the_guaranteed_periods(tmp_path, edit):
    r = check(tmp_path, edits=[edit])
    assert flagged(r, "doc.scenario_terms", S.MISMATCH), problems(r)


@pytest.mark.parametrize(
    "spec",
    [
        DacnSpec(ki="P"),
        DacnSpec(memory=False),
        DacnSpec(currency_zh="澳幣"),
        DacnSpec(count=1),
        DacnSpec(first_callable=2),
    ],
    ids=["ki-periodic", "non-memory", "aud", "single", "guaranteed-2"],
)
def test_variants_without_samples_need_review(tmp_path, spec):
    r = check(tmp_path, spec)
    result = rule(r, "doc.template_variant")[0]
    assert (result.status, result.reason_code) == (S.REVIEW_REQUIRED, "template_variant_unverified")
    assert "待樣本驗證" in result.message


def test_periodic_ko_dacn_is_not_supported(tmp_path):
    """KO 每期觀察的 DACN（沒有樣本）：日期表不是現行版的配息觀察期間表（沒有「配息金額」欄）→ 不支援的範本版本。"""
    ts, _ = check_pair(tmp_path, DacnSpec(ko_obs="P"))
    detect = rule(ts.report, "template.detect")[0]
    assert (detect.status, detect.reason_code) == (S.REVIEW_REQUIRED, "template_unsupported")
    assert "配息金額" in detect.message
