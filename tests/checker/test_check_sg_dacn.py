"""SG DACN 黑箱回歸（workflow #369）：合成說明書與投資人須知（sg_synth.DacnSpec）經批量入口核對。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import fitz
import pytest

from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import SG, SG_DACN
from fcn_checker.schema import CheckStatus as S
from harness import check_all, flagged, iis_path
from harness import field_problems as problems
from harness import results as rule
from pdf_writer import Edit
from reference_synth import REFERENCE_HEADERS, build_reference_sheet
from sg_synth import DACN_PRODUCT, DacnSpec, Spec, build_pdf, row

D0 = DacnSpec()  # 3 檔、9 個月、KO 每日＋記憶式、無 KI、保證 1 期 = Non-Call 1、年利率 13.2%（配息率 1.1000%）
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
        (DacnSpec(ki="AM", tenor=7, first_callable=2, annual=Decimal("16.58")), WITH_BARRIER),
        (DacnSpec(count=4, tenor=12, ko=Decimal("80.00"), strike=Decimal("65.50")), None),
        (DacnSpec(tenor=3, annual=Decimal("24.00")), None),
    ],
    ids=["3UL-no-column", "3UL-column", "AM-guaranteed-2", "4UL-12m-ko80", "3m"],
)
def test_supported_dacn_pairs_pass_and_fill(tmp_path, spec, headers):
    ts, iis = check_pair(tmp_path, spec, headers=headers)
    assert ts.report.template == SG_DACN.template_id
    assert iis.report.template == SG_DACN.iis.template_id
    assert ts.report.status == S.PASS, problems(ts.report)
    assert iis.report.status == S.PASS, problems(iis.report)
    assert ts.fills_sheet
    barrier = rule(ts.report, "field.coupon_barrier_pct")[0]
    assert barrier.status == S.PASS
    assert ("沒有配息門檻欄" in barrier.message) == (headers is None)
    for rid in (
        "doc.guaranteed_periods",
        "doc.accrual_range",
        "doc.scenario_terms",
        "doc.template_variant",
        "standard.product_name",
        "derive.monthly_coupon",
        "scenario.params",
    ):
        assert rule(ts.report, rid) and all(x.status == S.PASS for x in rule(ts.report, rid)), rid
    for rid in ("iis.coupon_pa_term_sheet", "iis.guaranteed_periods", "field.coupon_pa_pct"):
        assert rule(iis.report, rid) and all(x.status == S.PASS for x in rule(iis.report, rid)), rid


def test_fcn_and_dacn_templates_are_never_confused(tmp_path):
    fcn, dacn = Spec(product_code="037199990001"), DacnSpec(product_code="037199990002")
    pdfs = [build_pdf(tmp_path / f"{s.product_code}_TS.pdf", s) for s in (fcn, dacn)]
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [row(fcn), row(dacn)])
    outcome = check_all(sheet, [*pdfs, *map(iis_path, pdfs)])
    detected = {i.term_sheet.name: rule(i.report, "template.detect")[0].actual for i in outcome.items}
    assert detected == {
        pdfs[0].name: SG.template_id,
        iis_path(pdfs[0]).name: SG.iis.template_id,
        pdfs[1].name: SG_DACN.template_id,
        iis_path(pdfs[1]).name: SG_DACN.iis.template_id,
    }
    assert all(i.report.status == S.PASS for i in outcome.items), [problems(i.report) for i in outcome.items]


def test_term_sheet_and_investor_sheet_are_not_taken_for_each_other(tmp_path):
    pdf = build_pdf(tmp_path / f"{D0.product_code}_TS.pdf", D0)
    ts_lines, iis_lines = (lines_of(x) for x in (pdf, iis_path(pdf)))
    assert SG_DACN.detect(ts_lines).matched
    assert SG_DACN.iis.detect(iis_lines).matched
    for detect in (SG_DACN.iis.detect, SG.iis.detect, SG.detect):
        assert not detect(ts_lines).matched
    for detect in (SG_DACN.detect, SG.detect, SG.iis.detect):
        assert not detect(iis_lines).matched


def test_dacn_named_like_fcn_is_not_taken_by_the_fcn_template(tmp_path):
    """DACN 說明書英文名稱誤寫成 FCN：FCN 範本因 n(i)／N(i) 定義不收，DACN 範本認得出但不是現行版（不支援的範本版本）。"""
    edit = Edit("Range Accrual Equity Linked Notes", "Fixed Coupon Equity Linked Notes", "ts.cover")
    ts, _ = check_pair(tmp_path, edits=[edit])
    detect = rule(ts.report, "template.detect")[0]
    assert (detect.status, detect.reason_code) == (S.REVIEW_REQUIRED, "template_unsupported")
    assert "Range Accrual Equity Linked Notes" in detect.message


def test_fcn_named_like_dacn_matches_neither_template(tmp_path):
    """FCN 說明書英文名稱誤寫成 DACN：FCN 名稱條件不成立、DACN 沒有 n(i)／N(i) 定義 → 範本不符（不是 DACN 也不是 FCN）。"""
    edit = Edit("Fixed Coupon Equity Linked Notes", "Range Accrual Equity Linked Notes", "ts.cover")
    r = check(tmp_path, Spec(), edits=[edit])
    detect = rule(r, "template.detect")[0]
    assert detect.status == S.REVIEW_REQUIRED and detect.reason_code != "template_unsupported"
    assert "n(i)" in detect.message


@pytest.mark.parametrize(
    "edits",
    [
        [Edit("收盤價皆大於或等於其執行價", "收盤價皆大於或等於其期初價格", "ts.art8")],
        [Edit("均為預定交易日之總日數", "均為交易日之總日數", "ts.art8")],
        [Edit("並於自動提前出場日支付該配息週期之配息金額。", "。", "ts.art8")],
        [Edit("（相當於年配息率", "（年率", "ts.art8")],
        [Edit("區間累積配息", "固定配息", "ts.cover")],
    ],
    ids=["barrier-price", "total-days", "ko-period", "rate-wording", "zh-name"],
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


def test_other_investor_sheet_wording_is_not_supported(tmp_path):
    edit = Edit("收盤價皆大於或等於其執行價", "收盤價皆大於或等於其期初價格", "iis.coupon")
    _, iis = check_pair(tmp_path, edits=[edit])
    detect = rule(iis.report, "template.detect")[0]
    assert (detect.status, detect.reason_code) == (S.REVIEW_REQUIRED, "template_unsupported")


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
    r = check(tmp_path, Spec(), headers=WITH_BARRIER, **{"配息門檻(%)": "-"})
    assert r.status == S.PASS, problems(r)
    assert not [x for x in r.results if x.rule_id == "field.coupon_barrier_pct"]


@pytest.mark.parametrize(
    ("edit", "rule_id"),
    [
        (Edit("配息率 = 1.1000%（", "配息率 = 1.2000%（", "ts.art8"), "derive.monthly_coupon"),
        (Edit("配息率 = 1.1000 %", "配息率 = 1.2000 %", "ts.art15"), "scenario.params"),
        (Edit("x1.1000%x(10/20)", "x1.2000%x(10/20)", "ts.scen.三"), "scenario.params"),
        (Edit("x1.1000%", "x1.2000%", "ts.scen.一"), "scenario.params"),
        (Edit("平均年化報酬率：13.20%", "平均年化報酬率：13.21%", "ts.scen.二"), "scenario.params"),
    ],
    ids=["art8-monthly", "assumption", "scenario3-accrual", "scenario1-guaranteed", "scenario2-annualized"],
)
def test_other_places_of_the_rate_must_match_article8(tmp_path, edit, rule_id):
    """第 8 項年配息率正確（= 參考條件表），其他出處錯：只有那一處判錯（投資人須知不受影響）。"""
    ts, iis = check_pair(tmp_path, edits=[edit])
    assert flagged(ts.report, rule_id, S.MISMATCH), problems(ts.report)
    assert {r for r, _, _ in problems(ts.report)} <= {rule_id, "derive.monthly_coupon", "scenario.params"}
    assert iis.report.status == S.PASS, problems(iis.report)


def test_annual_rate_differs_from_the_sheet(tmp_path):
    ts, iis = check_pair(tmp_path, **{"Coupon p.a. (%)": 13.5})
    assert flagged(ts.report, "field.coupon_pa_pct", S.MISMATCH), problems(ts.report)
    assert flagged(iis.report, "field.coupon_pa_pct", S.MISMATCH), problems(iis.report)


def test_investor_sheet_rate_and_periods_must_match_the_term_sheet(tmp_path):
    edits = [
        Edit("相當於年配息率13.2000%", "相當於年配息率13.5000%", "iis.coupon"),
        Edit("■第1個配息金額", "■第1~2個配息金額", "iis.coupon"),
    ]
    ts, iis = check_pair(tmp_path, edits=edits)
    assert ts.report.status == S.PASS, problems(ts.report)
    assert flagged(iis.report, "iis.coupon_pa_term_sheet", S.MISMATCH)
    assert flagged(iis.report, "field.coupon_pa_pct", S.MISMATCH)
    assert flagged(iis.report, "iis.guaranteed_periods", S.MISMATCH)


def test_guaranteed_periods_must_equal_non_call(tmp_path):
    """日期表 Non-Call 2，但配息公式只保證第 1 期：保證期數 ≠ Non-Call（計息範圍、情境期別仍和公式一致）。"""
    r = check(tmp_path, DacnSpec(first_callable=2), pdf_spec=DacnSpec(first_callable=2, guaranteed=1))
    only(r, "doc.guaranteed_periods")


@pytest.mark.parametrize(
    "edit",
    [Edit("n(i)(i=2-9)", "n(i)(i=2-8)", "ts.art8"), Edit("N(i)(i=2-9)", "N(i)(i=3-9)", "ts.art8")],
    ids=["n-range", "N-range"],
)
def test_accrual_ranges_must_follow_the_formula(tmp_path, edit):
    r = check(tmp_path, edits=[edit])
    only(r, "doc.accrual_range")


@pytest.mark.parametrize(
    ("edit", "status"),
    [
        (Edit("情境三：一般情況", "情境三：中間情況", "ts.scen.三"), S.REVIEW_REQUIRED),
        (Edit("第2~9個配息金額", "第3~9個配息金額", "ts.scen.四"), S.MISMATCH),
        (Edit("第2個配息金額", "第3個配息金額", "ts.scen.二"), S.MISMATCH),
        (Edit("x1.1000%x(10/20)", "x1.1000%x10/20", "ts.scen.二"), S.REVIEW_REQUIRED),
    ],
    ids=["title", "scenario4-range", "scenario2-period", "scenario2-calc-unknown"],
)
def test_scenario_titles_and_periods(tmp_path, edit, status):
    r = check(tmp_path, edits=[edit])
    assert flagged(r, "doc.scenario_terms", status), problems(r)


@pytest.mark.parametrize(
    "spec",
    [
        DacnSpec(ki="D"),
        DacnSpec(ki="P"),  # KI 每期觀察（MKI）：流程各階段都不支援、人工處理，待 #386 決定
        DacnSpec(memory=False),
        DacnSpec(ko_obs="P"),
        DacnSpec(currency_zh="澳幣"),
        DacnSpec(count=1),
        DacnSpec(first_callable=3),
    ],
    ids=["ki-daily", "ki-periodic", "non-memory", "ko-periodic", "aud", "single", "guaranteed-3"],
)
def test_variants_without_samples_need_review(tmp_path, spec):
    r = check(tmp_path, spec)
    result = rule(r, "doc.template_variant")[0]
    assert (result.status, result.reason_code) == (S.REVIEW_REQUIRED, "template_variant_unverified")
    assert "待樣本驗證" in result.message
