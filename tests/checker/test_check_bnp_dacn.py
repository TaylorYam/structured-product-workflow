"""BNP DACN 黑箱回歸（workflow #548）：合成說明書與投資人須知（bnp_synth.DacnSpec）經批量入口核對。

反例依 docs/checker/rules/bnp-dacn-check-rules.md §7：正式條款正確、但其他出處錯時只判那一處。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import fitz
import pytest

from bnp_synth import CHECKS, SB_PRODUCT, DacnSpec, SbSpec, Spec, _row, build_pdf
from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import BNP, BNP_DACN, BNP_SUPER_BULL
from fcn_checker.schema import CheckStatus as S
from harness import check_all, flagged, iis_path
from harness import field_problems as problems
from harness import results as rule
from pdf_writer import Edit, zh_date
from reference_synth import DACN_PRODUCT, REFERENCE_HEADERS, build_reference_sheet

# 3 檔、6 個月、KO 每日＋記憶式、KI 每日、配息公式保證 1 期 = Non-Call 1、年利率 12%（月配息率 1.0000%）、
# 執行價 = 配息門檻 70%
D0 = DacnSpec()
WITH_BARRIER = [*REFERENCE_HEADERS, "配息門檻(%)"]
B75 = DacnSpec(barrier=Decimal("75.00"))  # 配息門檻 ≠ 執行價：定界價格列的格和執行價格列不同，改字才分得開
DENOM = f"{D0.denom:,}"

FAMILY = CHECKS.family(D0)  # 單份核對殼（harness.SynthChecks）：預設 D0、參考條件表預設沒有配息門檻欄
check_pair, check = FAMILY.check_pair, FAMILY.check


def lines_of(path: Path):
    with fitz.open(path) as doc:
        return extract_lines(doc)


def only(report, rule_id: str, status: S = S.MISMATCH) -> None:
    """只有 `rule_id` 判錯（狀態 `status`）。"""
    assert flagged(report, rule_id, status), problems(report)
    assert {r for r, _, _ in problems(report)} == {rule_id}, problems(report)


def barrier_cells(spec: DacnSpec, sep: str) -> list[str]:
    """定界價格列每格的文字（`sep` 是價格與百分比之間的寫法：說明書 ` 即期初價格之`、投資人須知 `,即期初價格之`）。"""
    return [f"{u.initial * spec.barrier_value / 100:,.4f}{sep}{spec.barrier_value:.2f}%" for u in spec.underlyings]


# ---------------------------------------------------------------- 支援的型態


@pytest.mark.parametrize(
    ("spec", "headers"),
    [
        (D0, None),
        (D0, WITH_BARRIER),
        (DacnSpec(ki="AM", tenor=9, ko=Decimal("95.00")), WITH_BARRIER),
        (DacnSpec(annual=Decimal("10.50")), None),
        (B75, WITH_BARRIER),
    ],
    ids=["no-column", "with-column", "ki-at-maturity-9m", "annual-10.5", "barrier-75"],
)
def test_supported_dacn_pairs_pass_and_fill(tmp_path, spec, headers):
    ts, iis = check_pair(tmp_path, spec, headers=headers)
    assert ts.report.template == BNP_DACN.template_id
    assert iis.report.template == BNP_DACN.iis.template_id
    assert ts.report.status == S.PASS, problems(ts.report)
    assert iis.report.status == S.PASS, problems(iis.report)
    assert ts.fills_sheet
    # 比價日回填照 FCN D 型：比價日_1 = 第 1 期結束日、最後一格 = 期末評價日，中間 `-`
    slots = {d.column: d.expected for d in ts.report.backfill if d.column.startswith("比價日_")}
    last = f"比價日_{spec.tenor}"
    assert (slots["比價日_1"], slots[last]) == (spec.ends[0], spec.final_date)
    assert {v for k, v in slots.items() if k not in ("比價日_1", last)} == {"-"}
    barrier = rule(ts.report, "field.coupon_barrier_pct")[0]
    assert barrier.status == S.PASS
    assert ("沒有配息門檻欄" in barrier.message) == (headers is None)
    for rid in (
        "doc.coupon_barrier",
        "doc.guaranteed_periods",
        "doc.template_variant",
        "doc.min_coupon_rate",
        "doc.scenario_calculations",
        "derive.monthly_coupon",
        "scenario.params",
        "scenario.reprint",
        "standard.product_name",
        "field.first_callable_period",
    ):
        assert rule(ts.report, rid) and all(x.status == S.PASS for x in rule(ts.report, rid)), rid
    for rid in ("iis.coupon_barrier", "iis.guaranteed_periods", "iis.monthly_coupon", "iis.underlying_prices"):
        assert rule(iis.report, rid) and all(x.status == S.PASS for x in rule(iis.report, rid)), rid
    # 每檔一列定界價格（說明書第 11 項、投資人須知標的表）
    assert len([x for x in rule(ts.report, "doc.coupon_barrier") if "定界價格" in x.field]) == spec.count
    assert len([x for x in rule(iis.report, "iis.coupon_barrier") if "定界價格" in x.field]) == spec.count


def test_fcn_super_bull_and_dacn_templates_are_never_confused(tmp_path):
    """同一批：BNP FCN、Super Bull、DACN 各自辨識成自己的範本（由專到泛：DACN → Super Bull → FCN）。"""
    specs = (
        Spec(product_code="082199990001"),
        SbSpec(product_code="082199990002"),
        D0.with_(product_code="082199990003"),
    )
    pdfs = [build_pdf(tmp_path / f"{s.product_code}_TS.pdf", s) for s in specs]
    rows = [_row(s) for s in specs]
    rows[1]["Product"] = SB_PRODUCT
    outcome = check_all(build_reference_sheet(tmp_path / "FCN參考條件.xlsx", rows), [*pdfs, *map(iis_path, pdfs)])
    detected = {i.term_sheet.name: rule(i.report, "template.detect")[0].actual for i in outcome.items}
    expected = {}
    for pdf, template in zip(pdfs, (BNP, BNP_SUPER_BULL, BNP_DACN), strict=True):
        expected[pdf.name] = template.template_id
        expected[iis_path(pdf).name] = template.iis.template_id
    assert detected == expected
    assert all(i.report.status == S.PASS for i in outcome.items), [problems(i.report) for i in outcome.items]


def test_term_sheet_and_investor_sheet_are_not_taken_for_each_other(tmp_path):
    pdf = build_pdf(tmp_path / f"{D0.product_code}_TS.pdf", D0)
    ts_lines, iis_lines = (lines_of(x) for x in (pdf, iis_path(pdf)))
    assert BNP_DACN.detect(ts_lines).matched
    assert BNP_DACN.iis.detect(iis_lines).matched
    # FCN（固定配息）與 Super Bull 範本不收 DACN；說明書與投資人須知不互認
    for detect in (BNP.detect, BNP_SUPER_BULL.detect, BNP_DACN.iis.detect, BNP.iis.detect, BNP_SUPER_BULL.iis.detect):
        assert not detect(ts_lines).matched
    for detect in (BNP.iis.detect, BNP_SUPER_BULL.iis.detect, BNP_DACN.detect, BNP.detect, BNP_SUPER_BULL.detect):
        assert not detect(iis_lines).matched


@pytest.mark.parametrize("spec", [Spec(), SbSpec()], ids=["fcn", "super-bull"])
def test_fcn_and_super_bull_documents_are_not_claimed_by_dacn(tmp_path, spec):
    """BNP FCN、Super Bull 全文沒有「定界價格」：DACN 範本不收、也不攔下（範本規格 §8 負面測試）。"""
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    for detect, lines in ((BNP_DACN.detect, lines_of(pdf)), (BNP_DACN.iis.detect, lines_of(iis_path(pdf)))):
        result = detect(lines)
        assert not result.matched and not result.claimed and not result.unsupported


# ---------------------------------------------------------------- 範本版本


def unsupported(item) -> None:
    detect = rule(item.report, "template.detect")[0]
    assert (detect.status, detect.reason_code) == (S.REVIEW_REQUIRED, "template_unsupported"), detect.message
    assert "不能人工放行" in detect.message
    assert item.status_label == "不支援的範本版本" and item.release_problem


@pytest.mark.parametrize(
    "edits",
    [
        [Edit("惟實際配息金額將取決於連結標的之表現", "惟實際配息金額將視連結標的之表現", "ts.art15")],
        [Edit("Nj 指於相關第j 個配息期內合格營業日總天數", "Nj 指於相關第j 個配息期內營業日總天數", "ts.art15")],
        [Edit("最低配息率為1.0000%。", "最低配息率為1.0000%（年化）。", "ts.art17")],
        [Edit(f"期初訂價日：{zh_date(D0.trade_date)}", "期初訂價日：另行公告", "ts.art14")],
    ],
    ids=["art15-paragraph1", "art15-Nj", "art17-annualised", "trade-date-unreadable"],
)
def test_other_term_sheet_wording_is_not_supported(tmp_path, edits):
    ts, _ = check_pair(tmp_path, edits=edits)
    unsupported(ts)


def test_term_sheet_without_a_barrier_row_is_not_supported(tmp_path):
    edits = [Edit("定界價格(Barrier Price)：", "", "ts.art11.table")]
    edits += [Edit(cell, "", "ts.art11.table") for cell in barrier_cells(B75, " 即期初價格之")]
    ts, _ = check_pair(tmp_path, B75, edits=edits)
    unsupported(ts)
    assert "定界價格" in rule(ts.report, "template.detect")[0].message


@pytest.mark.parametrize(
    ("spec", "edits"),
    [
        (D0, [Edit("合格營業日指任一預定交易日", "合格營業日指任一交易日", "iis.coupon")]),
        (
            B75,
            [
                Edit("定界價格(Barrier Price)：", "", "iis.summary.table"),
                *(Edit(cell, "", "iis.summary.table") for cell in barrier_cells(B75, ",即期初價格之")),
            ],
        ),
    ],
    ids=["coupon-wording", "no-barrier-row"],
)
def test_other_investor_sheet_wording_is_not_supported(tmp_path, spec, edits):
    ts, iis = check_pair(tmp_path, spec, headers=WITH_BARRIER, edits=edits)
    assert ts.report.status == S.PASS, problems(ts.report)
    unsupported(iis)


def test_accrual_definition_against_another_price_is_not_dacn(tmp_path):
    """nj 定義改成比執行價格：不是區間計息（商品家族條件不成立）；全文仍有「定界價格」→ 停在 DACN 範本、範本未知。"""
    edit = Edit("皆大於或等於其各自定界價格的合格營業日天數", "皆大於或等於其各自執行價格的合格營業日天數", "ts.art15")
    ts, _ = check_pair(tmp_path, edits=[edit])
    detect = rule(ts.report, "template.detect")[0]
    assert detect.status == S.REVIEW_REQUIRED and detect.reason_code != "template_unsupported"
    assert "nj" in detect.message


def test_trade_date_before_june_2026_with_current_wording_is_checked(tmp_path):
    """交易日早於 2026-06-01、寫法和現行版相同 → 照現行版範本辨識（版本只看寫法，workflow #409）。"""
    ts, iis = check_pair(tmp_path, D0.with_(trade_date=dt.date(2026, 3, 2)))
    for item, template in ((ts, BNP_DACN.template_id), (iis, BNP_DACN.iis.template_id)):
        assert item.report.template == template
        assert rule(item.report, "template.detect")[0].status == S.PASS
    assert not flagged(ts.report, "template.detect") and not flagged(iis.report, "template.detect")


@pytest.mark.parametrize(
    ("spec", "product"),
    [(D0, "FCN"), (D0, SB_PRODUCT), (D0, None), (Spec(), DACN_PRODUCT), (SbSpec(), DACN_PRODUCT)],
    ids=["dacn-booked-as-fcn", "dacn-booked-as-super-bull", "dacn-blank-product", "fcn-as-dacn", "super-bull-as-dacn"],
)
def test_product_must_match_the_template_family(tmp_path, spec, product):
    ts, iis = check_pair(tmp_path, spec, Product=product)
    for item in (ts, iis):
        assert flagged(item.report, "template.product_family", S.REVIEW_REQUIRED)
        assert item.unsupported_variant and item.release_problem


# ---------------------------------------------------------------- 配息門檻（定界價格）


def test_coupon_barrier_column_differs(tmp_path):
    """表上有配息門檻欄：第 11 項定界價格 % 和它比；不同時判不一致。"""
    r = check(tmp_path, headers=WITH_BARRIER, **{"配息門檻(%)": 71.0})
    only(r, "field.coupon_barrier_pct")


@pytest.mark.parametrize("value", [None, "-"], ids=["blank", "empty-value"])
def test_a_blank_barrier_cell_is_compared_with_the_strike(tmp_path, value):
    r = check(tmp_path, headers=WITH_BARRIER, **{"配息門檻(%)": value})
    assert r.status == S.PASS, problems(r)
    assert "該列配息門檻空白，和 K(%) 比" in rule(r, "field.coupon_barrier_pct")[0].message


def test_without_the_column_the_barrier_is_compared_with_the_strike(tmp_path):
    r = check(tmp_path, **{"K(%)": 71.0})
    assert flagged(r, "field.coupon_barrier_pct", S.MISMATCH), problems(r)
    assert flagged(r, "field.strike_pct", S.MISMATCH)


def test_barrier_pct_must_be_the_same_for_every_underlying(tmp_path):
    """第 11 項某檔定界價格 % 和其他檔不同：配息門檻 % 歧義、轉人工（不判成某一個值；重印表和第 11 項不同另判錯）。"""
    cell = barrier_cells(B75, " 即期初價格之")[2]
    r = check(
        tmp_path, B75, headers=WITH_BARRIER, edits=[Edit(cell, cell.replace("75.00%", "76.00%"), "ts.art11.table")]
    )
    assert flagged(r, "field.coupon_barrier_pct", S.REVIEW_REQUIRED), problems(r)
    assert flagged(r, "doc.coupon_barrier", S.REVIEW_REQUIRED)
    assert {p[0] for p in problems(r) if p[2] == S.MISMATCH} == {"scenario.reprint"}, problems(r)


def test_barrier_price_must_be_initial_times_pct(tmp_path):
    """第 11 項某檔定界價格 ≠ 期初 × %：該檔判錯（重印表照常印對，和第 11 項不同另判錯）。"""
    cell = barrier_cells(B75, " 即期初價格之")[1]
    r = check(
        tmp_path, B75, headers=WITH_BARRIER, edits=[Edit(cell, cell.replace("150.0000", "150.0100"), "ts.art11.table")]
    )
    assert {r for r, _, _ in problems(r)} == {"doc.coupon_barrier", "scenario.reprint"}, problems(r)
    bad = [x for x in rule(r, "doc.coupon_barrier") if x.status == S.MISMATCH]
    assert len(bad) == 1 and "定界價格" in bad[0].field


def test_reprinted_barrier_row_must_match_article11(tmp_path):
    cell = barrier_cells(B75, "，即期初價格之")[1]
    r = check(
        tmp_path, B75, headers=WITH_BARRIER, edits=[Edit(cell, cell.replace("150.0000", "150.0100"), "ts.art18.table")]
    )
    only(r, "scenario.reprint")


def test_investor_sheet_barrier_pct_must_match_the_term_sheet(tmp_path):
    edits = [Edit(c, c.replace("75.00%", "76.00%"), "iis.summary.table") for c in barrier_cells(B75, ",即期初價格之")]
    ts, iis = check_pair(tmp_path, B75, headers=WITH_BARRIER, edits=edits)
    assert ts.report.status == S.PASS, problems(ts.report)
    assert flagged(iis.report, "iis.coupon_barrier", S.MISMATCH), problems(iis.report)
    bad = [x for x in rule(iis.report, "iis.coupon_barrier") if x.status == S.MISMATCH]
    assert [x.field for x in bad] == ["coupon_barrier_pct"]


def test_investor_sheet_barrier_price_must_match_the_term_sheet(tmp_path):
    cell = barrier_cells(B75, ",即期初價格之")[1]
    edits = [Edit(cell, cell.replace("150.0000", "150.0100"), "iis.summary.table")]
    ts, iis = check_pair(tmp_path, B75, headers=WITH_BARRIER, edits=edits)
    assert ts.report.status == S.PASS, problems(ts.report)
    assert flagged(iis.report, "iis.coupon_barrier", S.MISMATCH), problems(iis.report)
    bad = [x for x in rule(iis.report, "iis.coupon_barrier") if x.status == S.MISMATCH]
    assert [x.field for x in bad] == ["UL_2 定界價格"]


# ---------------------------------------------------------------- 月配息率


def test_annual_rate_differs_from_the_sheet(tmp_path):
    ts, iis = check_pair(tmp_path, **{"Coupon p.a. (%)": 12.5})
    assert flagged(ts.report, "derive.monthly_coupon", S.MISMATCH), problems(ts.report)
    assert flagged(iis.report, "iis.monthly_coupon", S.MISMATCH), problems(iis.report)


@pytest.mark.parametrize(
    "edit",
    [
        Edit("配息金額 = 商品面額 × 1.0000%。", "配息金額 = 商品面額 × 1.1000%。", "ts.art15"),
        Edit("配息金額 = 商品面額 × 1.0000% × (nj/Nj)", "配息金額 = 商品面額 × 1.1000% × (nj/Nj)", "ts.art15"),
    ],
    ids=["paragraph2", "paragraph3"],
)
def test_rates_of_the_formula_must_agree(tmp_path, edit):
    """§15 第 2、3 段的 X 和第一句不同：讀不到月配息率與保證期數，轉人工覆核（不判成某一個值）。"""
    r = check(tmp_path, edits=[edit])
    assert flagged(r, "derive.monthly_coupon", S.REVIEW_REQUIRED), problems(r)
    assert flagged(r, "doc.guaranteed_periods", S.REVIEW_REQUIRED), problems(r)
    assert not [p for p in problems(r) if p[2] == S.MISMATCH], problems(r)


@pytest.mark.parametrize(
    ("edit", "rule_ids"),
    [
        (Edit("最低配息率為1.0000%。", "最低配息率為1.1000%。", "ts.art17"), {"doc.min_coupon_rate"}),
        (Edit("每月配息率：1.0000%", "每月配息率：1.1000%", "ts.art18"), {"scenario.params"}),
        (  # 第 1 個月配息金額另照 FCN 核算（面額 × 月配息率），和算式不同也判錯
            Edit(
                f"第1 個月配息金額 = 1 單位商品 × {DENOM} 美元 × 1.0000%",
                f"第1 個月配息金額 = 1 單位商品 × {DENOM} 美元 × 1.1000%",
                "ts.scen.二",
            ),
            {"scenario.params", "doc.scenario_calculations"},
        ),
        (Edit("× 1.0000% × (20 ÷ 20)", "× 1.1000% × (20 ÷ 20)", "ts.scen.二"), {"scenario.params"}),
        (Edit("× 1.0000% × (0 ÷ 20)", "× 1.1000% × (0 ÷ 20)", "ts.scen.四"), {"scenario.params"}),
    ],
    ids=["art17", "assumption", "best-first-month", "best-accrual", "worse-accrual"],
)
def test_other_places_of_the_rate_must_match_article15(tmp_path, edit, rule_ids):
    """§15 月配息率正確（= 參考條件表推算），其他出處錯：只有那一處判錯（投資人須知不受影響）。"""
    ts, iis = check_pair(tmp_path, edits=[edit])
    assert {r for r, _, s in problems(ts.report) if s == S.MISMATCH} == rule_ids, problems(ts.report)
    assert {r for r, _, _ in problems(ts.report)} == rule_ids, problems(ts.report)
    assert iis.report.status == S.PASS, problems(iis.report)


def test_accrual_assumptions_of_scenarios_are_not_checked(tmp_path):
    """合格營業日總天數假設、(n ÷ M)、第 2 期起的配息金額、累積與總報酬率依假設算出：不核對（改了也不判錯）。"""
    r = check(
        tmp_path,
        edits=[
            Edit("每個配息期之合格營業日總天數=20", "每個配息期之合格營業日總天數=22", "ts.art18"),
            Edit("(20 ÷ 20)", "(18 ÷ 20)", "ts.scen.二"),
            Edit("(0 ÷ 20)", "(2 ÷ 20)", "ts.scen.四"),
        ],
    )
    assert r.status == S.PASS, problems(r)


def test_investor_sheet_rate_must_match_the_term_sheet(tmp_path):
    ts, iis = check_pair(tmp_path, edits=[Edit("其中C 為1.0000%", "其中C 為1.1000%", "iis.coupon")])
    assert ts.report.status == S.PASS, problems(ts.report)
    assert flagged(iis.report, "iis.monthly_coupon", S.MISMATCH), problems(iis.report)


# ---------------------------------------------------------------- 保證期數、型態


def test_guaranteed_periods_must_equal_non_call(tmp_path):
    """§15 保證 2 期（第1 到2 個配息日），但 Non-Call 1：保證期數 ≠ Non-Call（沒有樣本的型態另轉人工）。"""
    r = check(tmp_path, D0, pdf_spec=D0.with_(formula_g=2))
    assert flagged(r, "doc.guaranteed_periods", S.MISMATCH), problems(r)
    assert flagged(r, "doc.template_variant", S.REVIEW_REQUIRED)


def test_last_accrual_period_must_be_the_tenor(tmp_path):
    """§15 第 3 段到第 7 個配息日、天期 6 個月：讀不到月配息率與保證期數，轉人工覆核。"""
    r = check(tmp_path, edits=[Edit("第2 到6 個配息日", "第2 到7 個配息日", "ts.art15")])
    assert flagged(r, "doc.guaranteed_periods", S.REVIEW_REQUIRED), problems(r)
    assert flagged(r, "derive.monthly_coupon", S.REVIEW_REQUIRED)


def test_investor_sheet_guaranteed_periods_must_match_the_term_sheet(tmp_path):
    edits = [
        Edit("第1 個配息日之配息金額", "第1 到2 個配息日之配息金額", "iis.coupon"),
        Edit("第2~6 個配息日之配息金額", "第3~6 個配息日之配息金額", "iis.coupon"),
    ]
    ts, iis = check_pair(tmp_path, edits=edits)
    assert ts.report.status == S.PASS, problems(ts.report)
    only(iis.report, "iis.guaranteed_periods")


@pytest.mark.parametrize(
    "spec",
    [
        DacnSpec(currency_zh="澳幣"),
        DacnSpec(count=1),
        DacnSpec(memory=False),
        DacnSpec(ki="none"),
        DacnSpec(first_callable=2),
    ],
    ids=["aud", "single", "ko-not-memory", "no-ki", "non-call-2"],
)
def test_variants_without_samples_need_review(tmp_path, spec):
    r = check(tmp_path, spec)
    result = rule(r, "doc.template_variant")[0]
    assert (result.status, result.reason_code) == (S.REVIEW_REQUIRED, "template_variant_unverified")
    assert "待樣本驗證" in result.message


def test_monthly_ki_stays_manual(tmp_path):
    """KI 每期觀察（MKI）：照 workflow #386 維持人工，錯訊不寫「待樣本驗證」。"""
    r = check(tmp_path, DacnSpec(ki="P"))
    result = rule(r, "doc.template_variant")[0]
    assert result.status == S.REVIEW_REQUIRED
    assert "每期觀察 KI 維持人工處理（#386）" in result.message and "待樣本驗證" not in result.message
