"""CA Super Bull 黑箱回歸（Issue #312）：合成說明書與投資人須知（ca_synth.SbSpec）經批量入口核對。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from ca_synth import SB_PRODUCT, SbSpec, Spec, build_pdf, check, check_pair, row
from fcn_checker.issuers import CA, CA_SUPER_BULL
from fcn_checker.schema import CheckStatus as S
from harness import check_all, flagged, iis_path
from harness import field_problems as problems
from harness import results as rule
from pdf_writer import Edit
from reference_synth import as_headers, build_reference_sheet

S0 = SbSpec()  # 3 檔、6 個月、第 3 個月觀察一次、出場價 80% ≠ 執行價 70%、出場紅利 5%


def compare_dates(report) -> dict:
    return {d.column: d.expected for d in report.backfill if d.column.startswith("比價日_") and d.expected != "-"}


@pytest.mark.parametrize(
    "spec",
    [
        S0,
        SbSpec(count=1),
        SbSpec(months=(1, 2, 3, 4, 5), count=2, annual=Decimal("1.25")),
        SbSpec(months=(3, 6, 9), tenor=12, note=True, shift=3),
        SbSpec(months=(6,), tenor=12, shift=-3),
        SbSpec(market="JT", currency_zh="日圓", count=4),
    ],
    ids=["3UL", "single", "monthly", "note-late", "early", "jpy"],
)
def test_supported_super_bull_pairs_pass_and_fill_compare_dates(tmp_path, spec):
    ts, iis = check_pair(tmp_path, spec)
    assert ts.report.template == CA_SUPER_BULL.template_id
    assert iis.report.template == CA_SUPER_BULL.iis.template_id
    assert ts.report.status == S.PASS, problems(ts.report)
    assert iis.report.status == S.PASS, problems(iis.report)
    assert ts.fills_sheet
    # 出場紅利不年化，和 Coupon p.a. (%) 相等；Non-Call = 第一個觀察日的月數
    assert rule(ts.report, "field.coupon_pa_pct")[0].status == S.PASS
    assert rule(ts.report, "field.first_callable_period")[0].actual == spec.months[0]
    for rid in ("doc.scenario_calculations", "doc.exit_bonus", "doc.redemption_formula", "schedule.observation_order"):
        assert all(x.status == S.PASS for x in rule(ts.report, rid)), rid
    assert rule(iis.report, "iis.exit_bonus")[0].status == S.PASS
    # 比價日：`比價日_m` = 觀察日、`比價日_{天期}` = 期末訂價日，其他格 `-`
    expected = dict(zip((f"比價日_{m}" for m in (*spec.months, spec.tenor)), spec.ends, strict=True))
    assert compare_dates(ts.report) == expected


def test_fcn_and_super_bull_templates_are_never_confused(tmp_path):
    fcn = Spec(product_code="086199990001")
    express = Spec(product_code="086199990003", first_callable=0)
    sb = SbSpec(product_code="086199990002")
    pdfs = [build_pdf(tmp_path / f"{s.product_code}_TS.pdf", s) for s in (fcn, express, sb)]
    rows = [row(fcn), row(express), row(sb)]
    outcome = check_all(build_reference_sheet(tmp_path / "FCN參考條件.xlsx", rows), [*pdfs, *map(iis_path, pdfs)])
    detected = {i.term_sheet.name: rule(i.report, "template.detect")[0].actual for i in outcome.items}
    assert detected == {
        **{p.name: CA.template_id for p in pdfs[:2]},
        **{iis_path(p).name: CA.iis.template_id for p in pdfs[:2]},
        pdfs[2].name: CA_SUPER_BULL.template_id,
        iis_path(pdfs[2]).name: CA_SUPER_BULL.iis.template_id,
    }


@pytest.mark.parametrize(
    ("spec", "product"),
    [(S0, "FCN"), (S0, None), (Spec(), SB_PRODUCT)],
    ids=["sb-booked-as-fcn", "sb-blank-product", "fcn-booked-as-sb"],
)
def test_product_must_match_the_template_family(tmp_path, spec, product):
    ts, iis = check_pair(tmp_path, spec, Product=product)
    for item in (ts, iis):
        assert flagged(item.report, "template.product_family", S.REVIEW_REQUIRED)
        assert item.unsupported_variant and item.release_problem


@pytest.mark.parametrize(
    "column",
    [{"ko_memory": "Y"}, {"ko_observation": "D"}, {"ki_type": "AM"}],
    ids=["memory", "daily", "ki"],
)
def test_super_bull_terms_on_the_sheet_must_be_the_supported_variant(tmp_path, column):
    ts, iis = check_pair(tmp_path, S0, **as_headers(column))
    for item in (ts, iis):
        assert flagged(item.report, "batch.variant", S.REVIEW_REQUIRED)
        assert item.unsupported_variant and item.release_problem


@pytest.mark.parametrize(
    "edits",
    [
        # 舊版：(a) 贖回金額只寫 x100%、紅利在別處
        [Edit("商品面額x(100%+自動提前出場紅利) 。", "商品面額x100%。", "ts.art9")],
        # 舊版：第 9 項 (1) 沒有「本商品持有期間並無配息金額。」
        [Edit("「到期贖回金額」。本商品持有期間並無配息金額。", "「到期贖回金額」。", "ts.art9")],
        # ■ 版：第 15 項參與率寫 100%
        [Edit("(5)參與率：不適用。", "(5)參與率：100%。", "ts.art15")],
        # 舊版：觀察日表少「自動提前出場紅利」欄
        [Edit("自動提前出場紅利", "", "ts.art9.ko"), Edit("5.00%", "", "ts.art9.ko")],
    ],
    ids=["x100", "no-coupon-sentence", "participation-100", "three-columns"],
)
def test_old_term_sheet_version_is_not_supported(tmp_path, edits):
    ts, _ = check_pair(tmp_path, S0, edits=edits)
    detect = rule(ts.report, "template.detect")[0]
    assert (detect.status, detect.reason_code) == (S.REVIEW_REQUIRED, "template_unsupported")
    assert "舊版" in detect.message and "不能人工放行" in detect.message
    assert ts.status_label == "不支援的範本版本" and ts.release_problem


def test_old_investor_sheet_version_is_not_supported(tmp_path):
    _, iis = check_pair(tmp_path, S0, edits=[Edit("自動提前出場紅利：5.0000%。", "", "iis.coupon")])
    detect = rule(iis.report, "template.detect")[0]
    assert (detect.status, detect.reason_code) == (S.REVIEW_REQUIRED, "template_unsupported")
    assert iis.status_label == "不支援的範本版本" and iis.release_problem


def test_offshore_renminbi_needs_review(tmp_path):
    r = check(tmp_path, SbSpec(currency_zh="人民幣"))
    assert flagged(r, "doc.template_variant", S.REVIEW_REQUIRED), problems(r)


def test_row_ko_price_different_from_the_header_needs_review(tmp_path):
    spec = SbSpec(months=(3, 6), tenor=9)
    r = check(tmp_path, spec, edits=[Edit("80.00%", "85.00%", "ts.art9.ko")])
    assert flagged(r, "doc.template_variant", S.REVIEW_REQUIRED), problems(r)


def test_row_bonus_different_needs_review(tmp_path):
    spec = SbSpec(months=(3, 6), tenor=9)
    r = check(tmp_path, spec, pdf_spec=SbSpec(months=(3, 6), tenor=9, row_bonus=(Decimal("5.00"), Decimal("6.00"))))
    assert r.status == S.REVIEW_REQUIRED, problems(r)
    assert flagged(r, "field.coupon_pa_pct", S.REVIEW_REQUIRED), problems(r)


def test_observation_date_too_far_from_the_month_needs_review(tmp_path):
    r = check(tmp_path, S0, pdf_spec=SbSpec(shift=9))
    assert r.status == S.REVIEW_REQUIRED
    assert flagged(r, "schedule.observation_months", S.REVIEW_REQUIRED), problems(r)


def test_exit_date_must_be_after_the_observation_date(tmp_path):
    obs, exit_ = (S0.date(d) for d in (S0.ends[0], S0.payments[0]))
    r = check(tmp_path, S0, edits=[Edit(exit_, obs, "ts.art9.ko")])
    assert flagged(r, "schedule.observation_order"), problems(r)


@pytest.mark.parametrize(
    ("where", "old", "new", "rule_id"),
    [
        # 出場紅利其他出處（核對規則 §3.3）
        ("ts.art16", "自動提前出場紅利：5.00%", "自動提前出場紅利：5.50%", "doc.exit_bonus"),
        ("ts.art16", "(100% + 5.00%)", "(100% + 5.50%)", "doc.exit_bonus"),
        # 情境重算（核對規則 §3.7）
        ("ts.art16", "= 10,500.00 美元", "= 10,600.00 美元", "doc.scenario_calculations"),
        ("ts.art16", "報酬率：20.00%）", "報酬率：10.00%）", "doc.scenario_calculations"),
        ("ts.art16", "= 11,000.00 美元", "= 11,100.00 美元", "doc.scenario_calculations"),
        ("ts.art16", "= 142.217 股", "= 142.218 股", "doc.scenario_calculations"),
        ("ts.art16", "＝9.16 美元", "＝9.26 美元", "doc.scenario_calculations"),
        ("ts.art16", "= -40.00% (", "= -41.00% (", "doc.scenario_calculations"),
        # 到期公式固定寫法（核對規則 §3.5；參與率寫 100% 是 ■ 版特徵，見舊版測試）
        ("ts.art9", "x [100%+100%*", "x [100%+90%*", "doc.redemption_formula"),
    ],
)
def test_term_sheet_difference_is_reported(tmp_path, where, old, new, rule_id):
    r = check(tmp_path, S0, edits=[Edit(old, new, where)])
    assert flagged(r, rule_id, S.MISMATCH), problems(r)


MEMORY = "於預定自動提前出場觀察日或任何先前的自動提前出場觀察日之"


@pytest.mark.parametrize(
    ("edit", "rule_id", "status"),
    [
        (
            Edit("自動提前出場紅利：5.0000%。", "自動提前出場紅利：5.5000%。", "iis.coupon"),
            "iis.exit_bonus",
            S.MISMATCH,
        ),
        (Edit("x[100%+100%*", "x[100%+90%*", "iis.coupon"), "iis.redemption_formula", S.MISMATCH),
        # 說明書非記憶式、投資人須知寫成記憶式：和參考條件表 KO(memo) 不同
        (Edit("於預定自動提前出場觀察日之", MEMORY, "iis.coupon"), "field.ko_memory", S.REVIEW_REQUIRED),
    ],
    ids=["bonus", "formula", "memory"],
)
def test_investor_sheet_difference_is_reported(tmp_path, edit, rule_id, status):
    _, iis = check_pair(tmp_path, S0, edits=[edit])
    assert flagged(iis.report, rule_id, status), problems(iis.report)
