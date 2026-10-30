"""DBS DACN 黑箱回歸（workflow #370）：合成說明書與投資人須知（dbs_synth.DacnSpec）經批量入口核對。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import fitz
import pytest

from dbs_synth import DACN_PRODUCT, DacnSpec, Spec, build_pdf, row
from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import DBS, DBS_DACN
from fcn_checker.schema import CheckStatus as S
from harness import check_all, flagged, iis_path
from harness import field_problems as problems
from harness import results as rule
from pdf_writer import Edit
from reference_synth import REFERENCE_HEADERS, build_reference_sheet

D0 = (
    DacnSpec()
)  # 3 檔、9 個月、KO 每日＋記憶式、無 KI、保證 1 期 = Non-Call 1、年利率 10%（最大利率值無條件進位 0.8334%）
AM = DacnSpec(ki="AM", tenor=12, count=4, strike=Decimal("55.00"), ko=Decimal("80.00"), ki_pct=Decimal("50.00"))
D1 = DacnSpec(barrier=Decimal("65.00"))  # 配息門檻 ≠ 執行價：UL_1 區間下限價格 65.2925、執行價格 70.3150
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


def passed(report, rule_id: str) -> bool:
    return bool(rule(report, rule_id)) and all(x.status == S.PASS for x in rule(report, rule_id))


@pytest.mark.parametrize(
    ("spec", "headers"),
    [
        (D0, None),
        (D0, WITH_BARRIER),
        (AM, WITH_BARRIER),
        (AM, None),
        (D1, WITH_BARRIER),
        (DacnSpec(count=4, tenor=12, annual=Decimal("13.20"), split_iis_table=True), None),
    ],
    ids=["3UL-no-column", "3UL-column", "AM-4UL-column", "AM-4UL-no-column", "barrier-not-strike", "iis-table-split"],
)
def test_supported_dacn_pairs_pass_and_fill(tmp_path, spec, headers):
    ts, iis = check_pair(tmp_path, spec, headers=headers)
    assert ts.report.template == DBS_DACN.template_id
    assert iis.report.template == DBS_DACN.iis.template_id
    assert ts.report.status == S.PASS, problems(ts.report)
    assert iis.report.status == S.PASS, problems(iis.report)
    assert ts.fills_sheet
    for rid in (
        "field.coupon_barrier_pct",
        "doc.coupon_barrier",
        "doc.scenario_periods",
        "doc.template_variant",
        "scenario.reprint",
        "standard.product_name",
        "derive.monthly_coupon",
    ):
        assert passed(ts.report, rid), rid
    for rid in ("iis.coupon_barrier", "field.ki_type", "iis.monthly_coupon"):
        assert passed(iis.report, rid), rid


def test_fcn_and_dacn_templates_are_never_confused(tmp_path):
    fcn, dacn = Spec(product_code="078199990001"), DacnSpec(product_code="078199990002")
    pdfs = [build_pdf(tmp_path / f"{s.product_code}_TS.pdf", s) for s in (fcn, dacn)]
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [row(fcn), row(dacn)])
    outcome = check_all(sheet, [*pdfs, *map(iis_path, pdfs)])
    detected = {i.term_sheet.name: rule(i.report, "template.detect")[0].actual for i in outcome.items}
    assert detected == {
        pdfs[0].name: DBS.template_id,
        iis_path(pdfs[0]).name: DBS.iis.template_id,
        pdfs[1].name: DBS_DACN.template_id,
        iis_path(pdfs[1]).name: DBS_DACN.iis.template_id,
    }
    assert all(i.report.status == S.PASS for i in outcome.items), [problems(i.report) for i in outcome.items]


def test_term_sheet_and_investor_sheet_are_not_taken_for_each_other(tmp_path):
    pdf = build_pdf(tmp_path / f"{D0.product_code}_TS.pdf", D0)
    ts_lines, iis_lines = (lines_of(x) for x in (pdf, iis_path(pdf)))
    assert DBS_DACN.detect(ts_lines).matched
    assert DBS_DACN.iis.detect(iis_lines).matched
    for detect in (DBS_DACN.iis.detect, DBS.iis.detect, DBS.detect):
        assert not detect(ts_lines).matched
    for detect in (DBS_DACN.detect, DBS.detect, DBS.iis.detect):
        assert not detect(iis_lines).matched


@pytest.mark.parametrize(
    "edit",
    [
        Edit("利率將根據以下公式決定：", "利率將依以下公式決定：", "ts.basic.coupon"),
        Edit("截至(包含)自動提前出場事件日為止", "截至(不含)自動提前出場事件日為止", "ts.basic.coupon"),
        Edit("該觀察期間內觀察日的總數。", "該觀察期間內交易日的總數。", "ts.basic.coupon"),
        Edit("區間上限", "上限", "ts.basic.table"),
        Edit("區間累計配息記憶型", "區間累積配息記憶型", "ts.cover"),
        Edit("Range Accrual Memory", "Range Accrual Plus Memory", "ts.cover"),
    ],
    ids=["rate-wording", "accrued-days", "total-days", "no-upper-column", "zh-name", "en-name"],
)
def test_other_term_sheet_wording_is_not_supported(tmp_path, edit):
    ts, _ = check_pair(tmp_path, edits=[edit])
    detect = rule(ts.report, "template.detect")[0]
    assert (detect.status, detect.reason_code) == (S.REVIEW_REQUIRED, "template_unsupported")
    assert "不能人工放行" in detect.message
    assert ts.status_label == "不支援的範本版本" and ts.release_problem


def test_trade_date_before_june_2026_is_not_supported(tmp_path):
    ts, iis = check_pair(tmp_path, DacnSpec(trade_date=dt.date(2026, 5, 4), issue_date=dt.date(2026, 5, 11)))
    for item in (ts, iis):
        detect = rule(item.report, "template.detect")[0]
        assert (detect.status, detect.reason_code) == (S.REVIEW_REQUIRED, "template_unsupported")
        assert "2026-06-01" in detect.message


def test_other_investor_sheet_wording_is_not_supported(tmp_path):
    edit = Edit("為該觀察期間內觀察日的總數。", "為該觀察期間內交易日的總數。", "iis.terms")
    ts, iis = check_pair(tmp_path, edits=[edit])
    assert ts.report.status == S.PASS, problems(ts.report)
    detect = rule(iis.report, "template.detect")[0]
    assert (detect.status, detect.reason_code) == (S.REVIEW_REQUIRED, "template_unsupported")


def test_accrual_not_counting_the_barrier_is_read_as_fcn(tmp_path):
    """累積日數不比其區間下限價格（FCN 寫法）：不是 DACN，由 DBS FCN 範本認走，再因參考條件表 Product = DACN 轉人工。"""
    edits = [
        Edit("皆等於或高於其區間下限價格", "皆等於或高於其執行價格", "ts.basic.coupon"),
        Edit("皆等於或高於其區間下限價格", "皆等於或高於其執行價格", "iis.terms"),
    ]
    ts, iis = check_pair(tmp_path, edits=edits)
    assert ts.report.template == DBS.template_id
    assert iis.report.template == DBS.iis.template_id
    for item in (ts, iis):
        assert flagged(item.report, "template.product_family", S.REVIEW_REQUIRED), problems(item.report)


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
    """表上有配息門檻欄：說明書配息門檻（表1 區間下限價格的 %）和它比；空白或空值寫法轉人工覆核（DACN 一定要有值）。"""
    r = check(tmp_path, headers=WITH_BARRIER, **{"配息門檻(%)": value})
    only(r, "field.coupon_barrier_pct", status)


def test_without_the_column_the_barrier_is_compared_with_the_strike(tmp_path):
    """舊 Excel 沒有配息門檻欄：和 K(%) 比；配息門檻 ≠ 執行價的商品要有配息門檻欄才會過。"""
    assert flagged(check(tmp_path / "a", D1), "field.coupon_barrier_pct", S.MISMATCH)
    r = check(tmp_path / "b", **{"K(%)": 75.0})
    assert flagged(r, "field.coupon_barrier_pct", S.MISMATCH), problems(r)
    assert flagged(r, "field.strike_pct", S.MISMATCH)


def test_fcn_rows_get_no_coupon_barrier_result(tmp_path):
    r = check(tmp_path, Spec(), headers=WITH_BARRIER, **{"配息門檻(%)": "-"})
    assert r.status == S.PASS, problems(r)
    assert not [x for x in r.results if x.rule_id == "field.coupon_barrier_pct"]


def test_barrier_prices_in_table1_must_follow_the_percentage(tmp_path):
    """表1 區間下限價格 ≠ 期初價格 × %：配息門檻價格判錯；情境價格表照抄的那格也和表1 不同。"""
    r = check(tmp_path, D1, headers=WITH_BARRIER, edits=[Edit("65.2925", "65.2926", "ts.basic.table")])
    assert flagged(r, "doc.coupon_barrier", S.MISMATCH), problems(r)
    assert {x for x, _, _ in problems(r)} == {"doc.coupon_barrier", "scenario.reprint"}, problems(r)


def test_scenario_barrier_cell_must_equal_table1(tmp_path):
    r = check(tmp_path, D1, headers=WITH_BARRIER, edits=[Edit("65.2925", "65.2926", "ts.scenario.table")])
    only(r, "scenario.reprint")


@pytest.mark.parametrize(
    "edit",
    [
        Edit("於第2~9個觀察期間", "於第2~8個觀察期間", "ts.scenario"),
        Edit(
            "於第2~9個觀察期間，每一個觀察期間之累積日數皆為0",
            "於第3~9個觀察期間，每一個觀察期間之累積日數皆為0",
            "ts.scenario",
        ),
    ],
    ids=["end", "start"],
)
def test_scenario_ranges_follow_guaranteed_periods_and_tenor(tmp_path, edit):
    only(check(tmp_path, edits=[edit]), "doc.scenario_periods")


def test_scenario_one_exits_after_non_call(tmp_path):
    edit = Edit("於第2個觀察期間結束日發生", "於第3個觀察期間結束日發生", "ts.scenario")
    only(check(tmp_path, edits=[edit]), "doc.autocall_terms")


@pytest.mark.parametrize(
    ("spec", "edit"),
    [
        (AM, Edit("於到期較佳情況：", "於到期次佳情況：", "ts.scenario")),
        (D0, Edit("2. 於到期最佳情況：", "2. 於到期較佳情況：", "ts.scenario")),
    ],
    ids=["ki-without-better-case", "no-ki-with-better-case"],
)
def test_scenario_titles_follow_ki(tmp_path, spec, edit):
    r = check(tmp_path, spec, edits=[edit])
    assert flagged(r, "doc.scenario_periods", S.REVIEW_REQUIRED), problems(r)


@pytest.mark.parametrize(
    "spec",
    [
        DacnSpec(currency_zh="澳幣"),
        DacnSpec(count=1),
        DacnSpec(first_callable=2),
        DacnSpec(upper="150.0000"),
    ],
    ids=["aud-quanto", "single", "guaranteed-2", "upper-applies"],
)
def test_variants_without_samples_need_review(tmp_path, spec):
    r = check(tmp_path, spec)
    result = rule(r, "doc.template_variant")[0]
    assert (result.status, result.reason_code) == (S.REVIEW_REQUIRED, "template_variant_unverified")
    assert "待樣本驗證" in result.message


def test_periodic_ki_wording_is_not_read(tmp_path):
    """KI 每期觀察（MKI，#386 待決定）：DBS 範本只認「於定價日」的下限觸發事件，改成每期觀察的寫法讀不到 KI 型態，
    轉人工覆核，不會自動通過。"""
    edit = Edit("於定價日的參考價格", "於任一觀察期間結束日的參考價格", "ts.basic.maturity")
    r = check(tmp_path, AM, edits=[edit])
    assert r.status != S.PASS
    assert flagged(r, "field.ki_type", S.REVIEW_REQUIRED), problems(r)


def test_periodic_ki_on_the_sheet_is_an_unsupported_variant(tmp_path):
    """參考條件表 KI(Freq) 寫 P（KI 每期觀察）：DBS 的不支援樣態（config/issuer_prefixes.toml），不核對、不能人工放行。"""
    ts, iis = check_pair(tmp_path, AM, **{"KI(Freq)": "P"})
    for item in (ts, iis):
        assert item.unsupported_variant and item.release_problem


def test_guaranteed_periods_must_equal_non_call(tmp_path):
    """強制贖回 Non-Call 2，但配息資料只保證第 1 期（情境範圍照公式）：保證期數 ≠ Non-Call。"""
    r = check(tmp_path, DacnSpec(first_callable=2), pdf_spec=DacnSpec(first_callable=2, guaranteed=1))
    assert flagged(r, "doc.autocall_terms", S.MISMATCH), problems(r)
    assert flagged(r, "doc.template_variant", S.REVIEW_REQUIRED)


@pytest.mark.parametrize(
    ("edit", "status"),
    [
        (Edit("65.00%)", "66.00%)", "iis.summary.table"), S.MISMATCH),
        (Edit("65.2925", "65.2926", "iis.summary.table"), S.MISMATCH),
        (Edit("不適用", "150.0000", "iis.summary.table"), S.REVIEW_REQUIRED),
    ],
    ids=["percentage", "price", "upper"],
)
def test_investor_sheet_barrier_must_match_the_term_sheet(tmp_path, edit, status):
    ts, iis = check_pair(tmp_path, D1, headers=WITH_BARRIER, edits=[edit])
    assert ts.report.status == S.PASS, problems(ts.report)
    assert flagged(iis.report, "iis.coupon_barrier", status), problems(iis.report)


def test_investor_sheet_ki_maturity_may_use_letters(tmp_path):
    """投資人須知 KI 到期觀察的到期贖回：樣本用 `1.`，FCN 寫法 `(a)` 也收。"""
    edit = Edit("1. 如果未發生下限觸發事件", "(a) 如果未發生下限觸發事件", "iis.terms")
    _, iis = check_pair(tmp_path, AM, edits=[edit])
    assert iis.report.status == S.PASS, problems(iis.report)
    assert passed(iis.report, "field.ki_type")
