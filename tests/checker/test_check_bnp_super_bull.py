"""BNP Super Bull 黑箱回歸（Issue #311）：合成說明書與投資人須知（bnp_synth.SbSpec）經批量入口核對。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from bnp_synth import SB_PRODUCT, SbSpec, Spec, build_pdf, check, check_pair
from fcn_checker.issuers import BNP, BNP_SUPER_BULL
from fcn_checker.schema import CheckStatus as S
from harness import check_all, flagged, iis_path
from harness import field_problems as problems
from harness import results as rule
from pdf_writer import Edit, zh_date
from reference_synth import as_headers, build_reference_sheet, reference_row

S0 = SbSpec()  # 3 檔、6 個月、第 3 個月評價一次、出場紅利 9%


def compare_dates(report) -> dict:
    return {d.column: d.expected for d in report.backfill if d.column.startswith("比價日_") and d.expected != "-"}


@pytest.mark.parametrize(
    "spec",
    [
        S0,
        SbSpec(months=(3, 6), tenor=9, count=4, annual=Decimal("10.50")),
        SbSpec(months=(2, 4)),
        SbSpec(months=(1, 2, 3, 4, 5), count=2),  # 投資人須知寫「每月一次」
    ],
    ids=lambda s: f"{s.count}UL-{s.tenor}m-at{'-'.join(map(str, s.months))}",
)
def test_supported_super_bull_pairs_pass_and_fill_compare_dates(tmp_path, spec):
    ts, iis = check_pair(tmp_path, spec)
    assert ts.report.template == BNP_SUPER_BULL.template_id
    assert ts.report.status == S.PASS, problems(ts.report)
    assert iis.report.status == S.PASS, problems(iis.report)
    assert ts.fills_sheet
    # 出場紅利不年化，和 Coupon p.a. (%) 相等；Non-Call = 第一個評價日的月數
    assert rule(ts.report, "field.coupon_pa_pct")[0].status == S.PASS
    assert rule(ts.report, "field.first_callable_period")[0].actual == spec.months[0]
    # 比價日：`比價日_m` = 評價日、`比價日_{天期}` = 期末評價日，其他格 `-`
    expected = dict(zip((f"比價日_{m}" for m in (*spec.months, spec.tenor)), spec.ends, strict=True))
    assert compare_dates(ts.report) == expected


def test_fcn_and_super_bull_templates_are_never_confused(tmp_path):
    fcn = Spec(product_code="082199990001")
    sb = SbSpec(product_code="082199990002")
    pdfs = [build_pdf(tmp_path / f"{s.product_code}_TS.pdf", s) for s in (fcn, sb)]
    rows = [reference_row(fcn), reference_row(sb, Product=SB_PRODUCT)]
    outcome = check_all(build_reference_sheet(tmp_path / "FCN參考條件.xlsx", rows), pdfs)
    detected = {i.term_sheet.name: rule(i.report, "template.detect")[0].actual for i in outcome.items}
    assert detected == {
        pdfs[0].name: BNP.template_id,
        iis_path(pdfs[0]).name: BNP.iis.template_id,
        pdfs[1].name: BNP_SUPER_BULL.template_id,
        iis_path(pdfs[1]).name: BNP_SUPER_BULL.iis.template_id,
    }
    assert all(i.report.status == S.PASS for i in outcome.items), [problems(i.report) for i in outcome.items]


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


def test_old_template_version_is_not_supported(tmp_path):
    """舊版（2026-06 以前）：頁首「第N頁 / 共M頁」、§16 `{100.00% + X% }`、沒有參與率句、投資人須知段名同 FCN。"""
    old = SbSpec(header="第{n}頁 / 共{total}頁")
    edits = [
        Edit("商品面額 × [100% + 9.00%]", "商品面額 × {100.00% + 9.00% }", "ts.art16"),
        Edit("參與率：100%。", "", "ts.art17"),
        Edit("收益分配事項及到期贖回金額之計算", "收益分配事項、自動提前贖回金額及到期贖回金額之計算", "iis.coupon"),
    ]
    ts, iis = check_pair(tmp_path, S0, pdf_spec=old, edits=edits)
    for item in (ts, iis):
        detect = rule(item.report, "template.detect")[0]
        assert (detect.status, detect.reason_code) == (S.REVIEW_REQUIRED, "template_unsupported")
        assert "舊版" in detect.message and "不能人工放行" in detect.message
        assert item.problem_messages == (f"目前樣態不支援自動核對：{detect.message}",)  # Issue #340
        assert item.status_label == "不支援的範本版本" and item.release_problem


@pytest.mark.parametrize(
    "spec",
    [SbSpec(count=1), SbSpec(ko=Decimal("100.00")), SbSpec(currency_zh="日圓")],
    ids=["single", "ko-not-strike", "jpy"],
)
def test_shapes_without_current_samples_need_review(tmp_path, spec):
    r = check(tmp_path, spec)
    assert flagged(r, "doc.template_variant", S.REVIEW_REQUIRED), problems(r)


@pytest.mark.parametrize(
    ("where", "old", "new", "rule_id"),
    [
        # 正式條款（§16）正確、其他出處錯誤也要抓到（核對規則 §3.3）
        ("ts.scen.一", "[100% + 9.00%]", "[100% + 9.50%]", "doc.exit_bonus"),
        ("ts.ch二", "{100% + 9.00%}", "{100% + 9.50%}", "doc.exit_bonus"),
        ("ts.art16", "[100% + 9.00%]", "[100% + 9.50%]", "field.coupon_pa_pct"),
        # 情境重算（核對規則 §3.7）
        ("ts.scen.一", "年化報酬率：36.000%", "年化報酬率：18.000%", "doc.scenario_calculations"),
        ("ts.scen.二", "= 11,500.00 美元 (", "= 11,600.00 美元 (", "doc.scenario_calculations"),
        # 到期公式固定寫法（核對規則 §3.5）
        ("ts.art17", "最低保證配息率：無。", "最低保證配息率：0%。", "doc.redemption_formula"),
    ],
)
def test_term_sheet_difference_is_reported(tmp_path, where, old, new, rule_id):
    r = check(tmp_path, S0, edits=[Edit(old, new, where)])
    assert flagged(r, rule_id, S.MISMATCH), problems(r)


def test_memory_wording_in_article_20_is_a_mismatch(tmp_path):
    memory = (
        "每一連結標的皆曾經於該自動提前贖回評價日或之前任一自動提前贖回評價日之收盤價格大於或等於其各自自動提前贖回價格"
    )
    plain = "每一連結標的之收盤價格皆大於或等於其各自自動提前贖回價格"
    r = check(tmp_path, S0, edits=[Edit(plain, memory, "ts.art20")])
    assert r.status != S.PASS and flagged(r, "doc.ko_memory"), problems(r)


def test_observation_date_too_far_after_the_month_needs_review(tmp_path):
    # 第 3 個月評價日（發行日 + 3 個月遇週末順延）往後挪到 8 個日曆天以上：對不到月數
    late = S0.ends[0].replace(day=S0.ends[0].day + 9)
    r = check(tmp_path, S0, edits=[Edit(zh_date(S0.ends[0]), zh_date(late), "ts.art14.table")])
    assert r.status == S.REVIEW_REQUIRED
    assert flagged(r, "schedule.observation_months", S.REVIEW_REQUIRED), problems(r)


def test_investor_sheet_exit_bonus_must_match_the_term_sheet(tmp_path):
    _, iis = check_pair(tmp_path, S0, edits=[Edit("{100% + 9.00%}", "{100% + 9.50%}", "iis.coupon")])
    assert flagged(iis.report, "iis.exit_bonus", S.MISMATCH), problems(iis.report)


@pytest.mark.parametrize(
    "spec",
    [SbSpec(months=(3, 6), tenor=9, interval=2), SbSpec(months=(3, 5), tenor=9)],
    ids=["stated-2-actual-3", "uneven-gaps"],
)
def test_investor_sheet_interval_must_match_every_gap(tmp_path, spec):
    _, iis = check_pair(tmp_path, spec)
    assert flagged(iis.report, "iis.observation_interval", S.REVIEW_REQUIRED), problems(iis.report)
