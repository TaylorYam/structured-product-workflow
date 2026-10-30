"""Nomura DACN 黑箱回歸（workflow #368）：合成說明書與投資人須知（nomura_synth.DacnSpec）經批量入口核對。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import fitz
import pytest

from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import NOMURA, NOMURA_DACN
from fcn_checker.schema import CheckStatus as S
from harness import check_all, flagged, iis_path
from harness import field_problems as problems
from harness import results as rule
from nomura_synth import DACN_PRODUCT, DacnSpec, Spec, build_pdf, row
from pdf_writer import Edit
from reference_synth import REFERENCE_HEADERS, build_reference_sheet

D0 = DacnSpec()  # 3 檔、6 個月、KO 每日＋記憶式、無 KI、Non-Call 1、年化配息率 12.00%、情境二 P = 50%
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
        (DacnSpec(ki="AM", count=4, tenor=9, ko=Decimal("95.00"), annual=Decimal("20.00")), WITH_BARRIER),
        (DacnSpec(count=5, tenor=12, strike=Decimal("66.85"), accrual=Decimal("40")), None),
    ],
    ids=["3UL-no-column", "3UL-column", "4UL-AM-9m", "5UL-12m"],
)
def test_supported_dacn_pairs_pass_and_fill(tmp_path, spec, headers):
    ts, iis = check_pair(tmp_path, spec, headers=headers)
    assert ts.report.template == NOMURA_DACN.template_id
    assert iis.report.template == NOMURA_DACN.iis.template_id
    assert ts.report.status == S.PASS, problems(ts.report)
    assert iis.report.status == S.PASS, problems(iis.report)
    assert ts.fills_sheet
    barrier = rule(ts.report, "field.coupon_barrier_pct")[0]
    assert barrier.status == S.PASS
    assert ("沒有配息門檻欄" in barrier.message) == (headers is None)
    for rid in ("doc.scenario_return", "doc.guaranteed_periods", "doc.template_variant", "standard.product_name"):
        assert all(x.status == S.PASS for x in rule(ts.report, rid)), rid
    assert [x.status for x in rule(ts.report, "doc.coupon_mentions")] == [S.PASS, S.PASS]
    assert rule(iis.report, "iis.coupon_pa_term_sheet")[0].status == S.PASS
    assert rule(iis.report, "field.coupon_pa_pct")[0].status == S.PASS


def test_fcn_and_dacn_templates_are_never_confused(tmp_path):
    fcn, dacn = Spec(product_code="156199990001", ko_obs="D"), DacnSpec(product_code="156199990002")
    pdfs = [build_pdf(tmp_path / f"{s.product_code}_TS.pdf", s) for s in (fcn, dacn)]
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [row(fcn), row(dacn)])
    outcome = check_all(sheet, [*pdfs, *map(iis_path, pdfs)])
    detected = {i.term_sheet.name: rule(i.report, "template.detect")[0].actual for i in outcome.items}
    assert detected == {
        pdfs[0].name: NOMURA.template_id,
        iis_path(pdfs[0]).name: NOMURA.iis.template_id,
        pdfs[1].name: NOMURA_DACN.template_id,
        iis_path(pdfs[1]).name: NOMURA_DACN.iis.template_id,
    }
    assert all(i.report.status == S.PASS for i in outcome.items), [problems(i.report) for i in outcome.items]


def test_term_sheet_and_investor_sheet_are_not_taken_for_each_other(tmp_path):
    pdf = build_pdf(tmp_path / f"{D0.product_code}_TS.pdf", D0)
    ts_lines, iis_lines = (lines_of(x) for x in (pdf, iis_path(pdf)))
    assert not NOMURA_DACN.iis.detect(ts_lines).matched
    assert not NOMURA.iis.detect(ts_lines).matched
    assert not NOMURA_DACN.detect(iis_lines).matched
    assert not NOMURA.detect(iis_lines).matched


def test_dacn_named_like_fcn_is_not_taken_by_the_fcn_template(tmp_path):
    """DACN 說明書英文名稱誤寫成 FCN：FCN 範本因每日計息定義不收，DACN 範本認得出但不是現行版（不支援的範本版本）。"""
    edit = Edit("Autocall Daily Range Accrual Notes", "Autocall Fixed Coupon Notes", "ts.cover")
    ts, _ = check_pair(tmp_path, edits=[edit])
    detect = rule(ts.report, "template.detect")[0]
    assert (detect.status, detect.reason_code) == (S.REVIEW_REQUIRED, "template_unsupported")
    assert "Autocall Daily Range Accrual Notes" in detect.message


def test_fcn_named_like_dacn_matches_neither_template(tmp_path):
    """FCN 說明書英文名稱誤寫成 DACN：FCN 名稱條件不成立、DACN 沒有每日計息定義 → 範本不符（不是 DACN 也不是 FCN）。"""
    fcn = Spec(ko_obs="D")
    edit = Edit("Autocall Fixed Coupon Notes", "Autocall Daily Range Accrual Notes", "ts.cover")
    r = check(tmp_path, fcn, edits=[edit])
    detect = rule(r, "template.detect")[0]
    assert detect.status == S.REVIEW_REQUIRED and detect.reason_code != "template_unsupported"
    assert "每日計息" in detect.message


@pytest.mark.parametrize(
    "edits",
    [
        [Edit("等於或大於其各自執行價", "等於或大於其各自期初價格", "ts.art7")],
        [Edit("(i)就第一個配息日而言", "(i)就第一個及第二個配息日而言", "ts.art7")],
        [Edit("觀察期間結束日止", "觀察期間終止日止", "ts.art7")],
        [Edit("年化報酬率：就第一個配息日而言:", "年化報酬率：", "ts.art15")],
    ],
    ids=["barrier-price", "two-guaranteed", "total-days", "art15"],
)
def test_other_term_sheet_wording_is_not_supported(tmp_path, edits):
    ts, _ = check_pair(tmp_path, edits=edits)
    detect = rule(ts.report, "template.detect")[0]
    assert (detect.status, detect.reason_code) == (S.REVIEW_REQUIRED, "template_unsupported")
    assert "寫法和現行版" in detect.message and "不能人工放行" in detect.message
    assert ts.status_label == "不支援的範本版本" and ts.release_problem


def test_trade_date_before_june_2026_is_not_supported(tmp_path):
    ts, _ = check_pair(tmp_path, DacnSpec(trade_date=dt.date(2026, 5, 4), issue_date=dt.date(2026, 5, 11)))
    detect = rule(ts.report, "template.detect")[0]
    assert (detect.status, detect.reason_code) == (S.REVIEW_REQUIRED, "template_unsupported")
    assert "2026-06-01" in detect.message


def test_other_investor_sheet_wording_is_not_supported(tmp_path):
    edit = Edit("(i)就第一個配息日而言", "(i)就第一個及第二個配息日而言", "iis.coupon")
    _, iis = check_pair(tmp_path, edits=[edit], pdf_spec=D0)
    detect = rule(iis.report, "template.detect")[0]
    assert (detect.status, detect.reason_code) == (S.REVIEW_REQUIRED, "template_unsupported")


@pytest.mark.parametrize(
    ("spec", "product"),
    [(D0, "FCN"), (D0, None), (Spec(ko_obs="D"), DACN_PRODUCT)],
    ids=["dacn-booked-as-fcn", "dacn-blank-product", "fcn-booked-as-dacn"],
)
def test_product_must_match_the_template_family(tmp_path, spec, product):
    ts, iis = check_pair(tmp_path, spec, Product=product)
    for item in (ts, iis):
        assert flagged(item.report, "template.product_family", S.REVIEW_REQUIRED)
        assert item.unsupported_variant and item.release_problem


@pytest.mark.parametrize(
    ("value", "status"),
    [(75.0, S.MISMATCH), (None, S.REVIEW_REQUIRED), ("-", S.REVIEW_REQUIRED)],
    ids=["differs", "blank", "empty-value"],
)
def test_coupon_barrier_column(tmp_path, value, status):
    """表上有配息門檻欄：說明書配息門檻（= 執行價 %）和它比；空白或空值寫法轉人工覆核（DACN 一定要有值）。"""
    r = check(tmp_path, headers=WITH_BARRIER, **{"配息門檻(%)": value})
    only(r, "field.coupon_barrier_pct", status)


def test_without_the_column_the_barrier_is_compared_with_the_strike(tmp_path):
    """舊 Excel 沒有配息門檻欄：和 K(%) 比；K(%) 和說明書不同時配息門檻與執行價一起判錯。"""
    r = check(tmp_path, **{"K(%)": 75.0})
    assert flagged(r, "field.coupon_barrier_pct", S.MISMATCH), problems(r)
    assert flagged(r, "field.strike_pct", S.MISMATCH)


def test_fcn_rows_get_no_coupon_barrier_result(tmp_path):
    r = check(tmp_path, Spec(ko_obs="D"), headers=WITH_BARRIER, **{"配息門檻(%)": "-"})
    assert r.status == S.PASS, problems(r)
    assert not [x for x in r.results if x.rule_id == "field.coupon_barrier_pct"]


@pytest.mark.parametrize(
    ("edit", "rule_id"),
    [
        (Edit("本情境中年化報酬率為7.00%", "本情境中年化報酬率為7.01%", "ts.art14"), "doc.scenario_return"),
        (Edit("就其他配息日而言: 12.00%", "就其他配息日而言: 12.50%", "ts.art15"), "doc.coupon_mentions"),
        (Edit("就第一個配息日而言: 12.00%", "就第一個配息日而言: 12.50%", "ts.art15"), "doc.coupon_mentions"),
        (Edit("12.00%", "12.50%", "ts.art14.table"), "scenario.params"),
        (Edit("本情境中年化報酬率為12.00%", "本情境中年化報酬率為12.50%", "ts.art14"), "scenario.params"),
    ],
    ids=["scenario2", "art15-other", "art15-first", "setting-param", "scenario1"],
)
def test_other_places_of_the_annual_rate_must_match_article7(tmp_path, edit, rule_id):
    """第 7 項年化配息率正確（= 參考條件表），其他出處錯：只有那一處判錯。"""
    ts, iis = check_pair(tmp_path, edits=[edit])
    only(ts.report, rule_id)
    assert iis.report.status == S.PASS, problems(iis.report)


def test_annual_rate_differs_from_the_sheet(tmp_path):
    ts, iis = check_pair(tmp_path, **{"Coupon p.a. (%)": 12.5})
    only(ts.report, "field.coupon_pa_pct")
    only(iis.report, "field.coupon_pa_pct")


def test_investor_sheet_annual_rate_must_match_the_term_sheet(tmp_path):
    edit = Edit("年化配息率：12.00%", "年化配息率：12.50%", "iis.coupon")
    ts, iis = check_pair(tmp_path, edits=[edit])
    assert ts.report.status == S.PASS, problems(ts.report)
    assert flagged(iis.report, "iis.coupon_pa_term_sheet", S.MISMATCH)
    assert flagged(iis.report, "field.coupon_pa_pct", S.MISMATCH)


def test_guaranteed_periods_must_equal_non_call(tmp_path):
    """KO 起日在第 2 期（Non-Call 2）但配息公式只保證第 1 期：保證期數 ≠ Non-Call；Non-Call 2 也沒有樣本。"""
    r = check(tmp_path, DacnSpec(first_callable=2))
    assert flagged(r, "doc.guaranteed_periods", S.MISMATCH), problems(r)
    assert flagged(r, "doc.template_variant", S.REVIEW_REQUIRED)


@pytest.mark.parametrize(
    "spec",
    [DacnSpec(ki="D"), DacnSpec(memory=False), DacnSpec(ko_obs="P"), DacnSpec(currency_zh="澳幣")],
    ids=["ki-daily", "non-memory", "ko-periodic", "aud"],
)
def test_variants_without_samples_need_review(tmp_path, spec):
    r = check(tmp_path, spec)
    result = rule(r, "doc.template_variant")[0]
    assert (result.status, result.reason_code) == (S.REVIEW_REQUIRED, "template_variant_unverified")
    assert "待樣本驗證" in result.message
