"""UBS DACN 黑箱回歸（workflow #549）：合成說明書與投資人須知（ubs_synth.DacnSpec）經批量入口核對。

反例依 docs/checker/rules/ubs-dacn-check-rules.md §7：正式條款正確、但其他出處錯時只判那一處。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import fitz
import pytest

from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import UBS, UBS_DACN
from fcn_checker.schema import CheckStatus as S
from harness import check_all, flagged, iis_path
from harness import field_problems as problems
from harness import results as rule
from pdf_writer import Edit
from reference_synth import DACN_PRODUCT, REFERENCE_HEADERS, build_reference_sheet, price
from ubs_synth import CHECKS, DacnSpec, Spec, build_pdf

# 3 檔美股、6 個月、KO 每日＋記憶式、無 KI、配息表保證 1 期 = Non-Call 1、年利率 12%（固定配息率 1.0000%）、
# 轉換價 = 區間下限價 70%
D0 = DacnSpec()
WITH_BARRIER = [*REFERENCE_HEADERS, "配息門檻(%)"]
B75 = DacnSpec(barrier=Decimal("75.00"))  # 配息門檻 ≠ 轉換價：區間下限價欄的格和轉換價欄不同，改字才分得開

FAMILY = CHECKS.family(D0)  # 單份核對殼（harness.SynthChecks）：預設 D0、參考條件表預設沒有配息門檻欄
check_pair, check = FAMILY.check_pair, FAMILY.check


def lines_of(path: Path):
    with fitz.open(path) as doc:
        return extract_lines(doc)


def only(report, rule_id: str, status: S = S.MISMATCH) -> None:
    """只有 `rule_id` 判錯（狀態 `status`）。"""
    assert flagged(report, rule_id, status), problems(report)
    assert {r for r, _, _ in problems(report)} == {rule_id}, problems(report)


def barrier_cell(spec: DacnSpec, i: int) -> str:
    """價格表第 `i` 檔（1 起）區間下限價那一格的文字。"""
    return f"{price(spec.underlyings[i - 1].initial, spec.barrier_value):,.4f} {spec.quote}"


def sample_names(spec: DacnSpec) -> list[Edit]:
    """把名稱改成真實樣本的寫法（KO 每日卻寫 `（定期觀察）`／`Periodic`）：說明書第 1 頁、商品基本資料一、投資人須知第 1 頁。"""
    zh, en = "瑞銀每日觀察", "UBS Daily"
    return [
        Edit(zh, "瑞銀（定期觀察）", "ts.cover"),
        Edit(en, "UBS Periodic", "ts.cover"),
        Edit(zh, "瑞銀（定期觀察）", "ts.art1"),
        Edit(zh, "瑞銀（定期觀察）", "iis.cover"),
        Edit(en, "UBS Periodic", "iis.cover"),
    ]


# ---------------------------------------------------------------- 支援的型態


@pytest.mark.parametrize(
    ("spec", "headers"),
    [
        (D0, None),
        (D0, WITH_BARRIER),
        (DacnSpec(memory=False, tenor=4), WITH_BARRIER),
        (DacnSpec(annual=Decimal("10.50"), count=4, tenor=12), None),
        (B75, WITH_BARRIER),
    ],
    ids=["no-column", "with-column", "not-memory-4m", "annual-10.5-4ul-12m", "barrier-75"],
)
def test_supported_dacn_pairs_pass_and_fill(tmp_path, spec, headers):
    ts, iis = check_pair(tmp_path, spec, headers=headers)
    assert ts.report.template == UBS_DACN.template_id
    assert iis.report.template == UBS_DACN.iis.template_id
    assert ts.report.status == S.PASS, problems(ts.report)
    assert iis.report.status == S.PASS, problems(iis.report)
    assert ts.fills_sheet
    # 比價日回填照 FCN D 型：比價日_1 = 第 1 期結束日、最後一格 = 期末訂價日，中間 `-`
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
        "doc.min_coupon_guarantee",
        "doc.accrual_scope",
        "doc.template_variant",
        "doc.autocall_terms",
        "doc.coupon_mentions",
        "scenario.params",
        "scenario.reprint",
        "standard.product_name",
        "field.first_callable_period",
    ):
        assert rule(ts.report, rid) and all(x.status == S.PASS for x in rule(ts.report, rid)), rid
    assert rule(iis.report, "iis.guaranteed_periods")[0].status == S.PASS
    # 每檔一列區間下限價；情境的平均年化報酬率依假設算出，不讀
    assert len([x for x in rule(ts.report, "doc.coupon_barrier") if "區間下限價" in x.field]) == spec.count
    assert not [x for x in ts.report.results if x.field == "scenario_annualized_return"]


def test_fcn_and_dacn_templates_are_never_confused(tmp_path):
    """同一批：UBS FCN 與 DACN 各自辨識成自己的範本（由專到泛：DACN → FCN）。"""
    specs = (Spec(), D0)
    pdfs = [build_pdf(tmp_path / f"{s.product_code}_TS.pdf", s) for s in specs]
    rows = [CHECKS.row(s) for s in specs]  # DACN 的 Product 填 `DACN`
    outcome = check_all(build_reference_sheet(tmp_path / "FCN參考條件.xlsx", rows), [*pdfs, *map(iis_path, pdfs)])
    detected = {i.term_sheet.name: rule(i.report, "template.detect")[0].actual for i in outcome.items}
    expected = {}
    for pdf, template in zip(pdfs, (UBS, UBS_DACN), strict=True):
        expected[pdf.name] = template.template_id
        expected[iis_path(pdf).name] = template.iis.template_id
    assert detected == expected
    assert all(i.report.status == S.PASS for i in outcome.items), [problems(i.report) for i in outcome.items]


def test_term_sheet_and_investor_sheet_are_not_taken_for_each_other(tmp_path):
    pdf = build_pdf(tmp_path / f"{D0.product_code}_TS.pdf", D0)
    ts_lines, iis_lines = (lines_of(x) for x in (pdf, iis_path(pdf)))
    assert UBS_DACN.detect(ts_lines).matched
    assert UBS_DACN.iis.detect(iis_lines).matched
    # FCN（固定配息）範本不收 DACN；說明書與投資人須知不互認
    for detect in (UBS.detect, UBS_DACN.iis.detect, UBS.iis.detect):
        assert not detect(ts_lines).matched
    for detect in (UBS.iis.detect, UBS_DACN.detect, UBS.detect):
        assert not detect(iis_lines).matched


def test_fcn_documents_are_not_claimed_by_dacn(tmp_path):
    """UBS FCN 全文沒有「區間下限價」、名稱沒有「區間累計配息」：DACN 範本不收、也不攔下（範本規格 §8 負面測試）。"""
    spec = Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    for detect, lines in ((UBS_DACN.detect, lines_of(pdf)), (UBS_DACN.iis.detect, lines_of(iis_path(pdf)))):
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
        [Edit("（年化配息率為12.00%X（nt/Nt））", "（年化配息率為12.00%）", "ts.art8")],
        [Edit("不適用中斷日之規定", "適用中斷日之規定", "ts.art8")],
        [Edit("固定配息率 x 每單位面額", "每單位面額 x 固定配息率", "ts.art8.coupon")],
        [Edit("區間下限價（即期初價", "配息門檻價（即期初價", "ts.art8.table")],
        [Edit(f"交易日/期初訂價日：{D0.date(D0.trade_date)}", "交易日/期初訂價日：另行公告", "ts.art12")],
    ],
    ids=["variable-annual", "Nt", "formula-order", "no-barrier-column", "trade-date-unreadable"],
)
def test_other_term_sheet_wording_is_not_supported(tmp_path, edits):
    ts, _ = check_pair(tmp_path, edits=edits)
    unsupported(ts)


def test_periodic_ko_has_no_dacn_coupon_table(tmp_path):
    """KO 每期觀察：UBS 的 P 型配息表是配息觀察日表，沒有 DACN 的配息表公式 → 不支援的範本版本（不走待樣本驗證）。"""
    ts, _ = check_pair(tmp_path, DacnSpec(ko_obs="P"))
    unsupported(ts)
    assert "配息表公式" in rule(ts.report, "template.detect")[0].message


def test_other_investor_sheet_wording_is_not_supported(tmp_path):
    ts, iis = check_pair(tmp_path, edits=[Edit("之後每月係給付潛在配息", "之後每月給付潛在配息", "iis.summary")])
    assert ts.report.status == S.PASS, problems(ts.report)
    unsupported(iis)


def test_accrual_definition_against_another_price_is_not_dacn(tmp_path):
    """nt 定義改成比轉換價：不是區間累計配息（商品家族條件不成立）；全文仍有「區間下限價」→ 停在 DACN 範本、範本未知。"""
    edit = Edit("高於或等於區間下限價的交易所營業日天數", "高於或等於轉換價的交易所營業日天數", "ts.art8")
    ts, _ = check_pair(tmp_path, edits=[edit])
    detect = rule(ts.report, "template.detect")[0]
    assert detect.status == S.REVIEW_REQUIRED and detect.reason_code != "template_unsupported"
    assert "區間累計配息" in detect.message


def test_trade_date_before_june_2026_with_current_wording_is_checked(tmp_path):
    """交易日早於 2026-06-01、寫法和現行版相同 → 照現行版範本辨識（版本只看寫法，workflow #409）。"""
    ts, iis = check_pair(tmp_path, D0.with_(trade_date=dt.date(2026, 3, 2)))
    for item, template in ((ts, UBS_DACN.template_id), (iis, UBS_DACN.iis.template_id)):
        assert item.report.template == template
        assert rule(item.report, "template.detect")[0].status == S.PASS
    assert not flagged(ts.report, "template.detect") and not flagged(iis.report, "template.detect")


@pytest.mark.parametrize(
    ("spec", "product"),
    [(D0, "FCN"), (D0, None), (Spec(), DACN_PRODUCT)],
    ids=["dacn-booked-as-fcn", "dacn-blank-product", "fcn-as-dacn"],
)
def test_product_must_match_the_template_family(tmp_path, spec, product):
    ts, iis = check_pair(tmp_path, spec, Product=product)
    for item in (ts, iis):
        assert flagged(item.report, "template.product_family", S.REVIEW_REQUIRED)
        assert item.unsupported_variant and item.release_problem


# ---------------------------------------------------------------- 配息門檻（區間下限價）


def test_coupon_barrier_column_differs(tmp_path):
    """表上有配息門檻欄：八價格表區間下限價 % 和它比；不同時判不一致。"""
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


def test_barrier_price_must_be_initial_times_pct(tmp_path):
    """八價格表某檔區間下限價 ≠ 期初 × %：該檔判錯（十五情境價格表照常印對，和八不同另判錯）。"""
    cell = barrier_cell(B75, 2)
    r = check(tmp_path, B75, headers=WITH_BARRIER, edits=[Edit(cell, cell.replace(".3375", ".3475"), "ts.art8.table")])
    assert {r for r, _, _ in problems(r)} == {"doc.coupon_barrier", "scenario.reprint"}, problems(r)
    bad = [x for x in rule(r, "doc.coupon_barrier") if x.status == S.MISMATCH]
    assert len(bad) == 1 and "區間下限價" in bad[0].field


def test_reprinted_barrier_price_must_match_article8(tmp_path):
    cell = barrier_cell(B75, 2)
    r = check(tmp_path, B75, headers=WITH_BARRIER, edits=[Edit(cell, cell.replace(".3375", ".3475"), "ts.art15.table")])
    only(r, "scenario.reprint")


def test_reprinted_barrier_head_must_match_article8(tmp_path):
    """十五情境價格表區間下限價表頭 % 和八不同：只有情境價格表判錯（配息門檻 % 取八）。"""
    r = check(tmp_path, B75, headers=WITH_BARRIER, edits=[Edit("格的75.00%）", "格的76.00%）", "ts.art15.table")])
    only(r, "scenario.reprint")


# ---------------------------------------------------------------- 配息率


def test_annual_rate_differs_from_the_sheet(tmp_path):
    r = check(tmp_path, **{"Coupon p.a. (%)": 12.5})
    only(r, "field.coupon_pa_pct")


@pytest.mark.parametrize(
    ("edit", "rule_id"),
    [
        (Edit("變動配息率：1.0000%X", "變動配息率：1.1000%X", "ts.art8"), "doc.coupon_mentions"),
        (
            Edit("（年化配息率為12.00%X（nt/Nt））", "（年化配息率為12.50%X（nt/Nt））", "ts.art8"),
            "doc.coupon_mentions",
        ),
        (Edit("變動配息率：1.0000%X", "變動配息率：1.1000%X", "ts.art15"), "scenario.params"),
        (Edit("1.0000%X15/21", "1.1000%X15/21", "ts.art15"), "scenario.params"),
    ],
    ids=["variable-rate", "variable-annual", "art15-variable-rate", "scenario-variable"],
)
def test_other_places_of_the_rate_must_match_the_fixed_rate(tmp_path, edit, rule_id):
    """八的固定配息率正確（= 參考條件表推算），其他出處錯：只有那一處判錯（投資人須知不受影響）。"""
    ts, iis = check_pair(tmp_path, edits=[edit])
    only(ts.report, rule_id)
    assert iis.report.status == S.PASS, problems(iis.report)


def test_accrual_assumptions_of_scenarios_are_not_checked(tmp_path):
    """十五的假設天數 M、算式的 n/M 與平均年化報酬率依假設算出：不核對（改了也不判錯）。"""
    r = check(
        tmp_path,
        edits=[
            Edit("皆為21 個交易所營業日", "皆為22 個交易所營業日", "ts.art15"),
            Edit("X15/21", "X16/22", "ts.art15"),
            Edit("9.87%", "1.23%", "ts.art15"),
            Edit("6.54%", "4.56%", "ts.art15"),
        ],
    )
    assert r.status == S.PASS, problems(r)


# ---------------------------------------------------------------- 保證期數


def test_guaranteed_periods_must_equal_non_call(tmp_path):
    """配息表保證 2 期，但 Non-Call 1：保證期數 ≠ Non-Call、十四「第一個月」≠ 配息表（沒有樣本的型態另轉人工）。"""
    r = check(tmp_path, D0, pdf_spec=D0.with_(fixed_periods=2))
    assert flagged(r, "doc.guaranteed_periods", S.MISMATCH), problems(r)
    assert flagged(r, "doc.min_coupon_guarantee", S.MISMATCH)
    assert flagged(r, "doc.template_variant", S.REVIEW_REQUIRED)


@pytest.mark.parametrize("section", ["ts.art8", "ts.art15"])
def test_variable_periods_must_start_after_the_guaranteed_periods(tmp_path, section):
    """八或十五的 t 範圍起點 ≠ g + 1：只有那一處判錯（同 UBS FCN `doc.autocall_terms`）。"""
    edit = Edit("t 係為2 至6 的數字，代表第2 至第6", "t 係為3 至6 的數字，代表第3 至第6", section)
    r = check(tmp_path, edits=[edit])
    only(r, "doc.autocall_terms")


def test_article14_must_match_the_coupon_table(tmp_path):
    """Non-Call 2、配息表保證 2 期，十四與投資人須知仍寫「第一個月」：說明書十四與投資人須知判不一致。"""
    ts, iis = check_pair(tmp_path, DacnSpec(first_callable=2))
    assert flagged(ts.report, "doc.min_coupon_guarantee", S.MISMATCH), problems(ts.report)
    assert not flagged(ts.report, "doc.guaranteed_periods"), problems(ts.report)
    only(iis.report, "iis.guaranteed_periods")


def test_other_guaranteed_period_wordings_are_not_read(tmp_path):
    """十四與投資人須知寫「第二個月」：沒有樣本的寫法，讀不到、轉人工（不判成某一個值）。"""
    ts, iis = check_pair(tmp_path, D0.with_(art14="第二"))
    assert flagged(ts.report, "doc.min_coupon_guarantee", S.REVIEW_REQUIRED), problems(ts.report)
    assert not [p for p in problems(ts.report) if p[2] == S.MISMATCH], problems(ts.report)
    only(iis.report, "iis.guaranteed_periods", S.REVIEW_REQUIRED)


def test_coupon_table_without_fixed_periods_is_not_read(tmp_path):
    """配息表每期都是「變動配息率」：保證期數讀不到、轉人工（不判成 0；配息表期別另照 FCN 判錯）。"""
    r = check(tmp_path, D0, pdf_spec=D0.with_(fixed_periods=0))
    assert flagged(r, "doc.guaranteed_periods", S.REVIEW_REQUIRED), problems(r)
    assert flagged(r, "doc.min_coupon_guarantee", S.REVIEW_REQUIRED)
    assert flagged(r, "doc.autocall_terms", S.MISMATCH)


# ---------------------------------------------------------------- 計息範圍（nt 定義）


def test_single_underlying_wording_of_a_basket_needs_review(tmp_path):
    """3 檔標的，nt 定義卻是單一標的的寫法（樣本 UD3）：計息看哪幾檔不明確，只有計息範圍轉人工（暫定做法，§3.6）。"""
    ts, iis = check_pair(tmp_path, DacnSpec(nt_scope="single"))
    only(ts.report, "doc.accrual_scope", S.REVIEW_REQUIRED)
    result = rule(ts.report, "doc.accrual_scope")[0]
    assert result.reason_code == "document_inconsistent"
    assert "請向上手確認" in result.message and "所有連結標的股票收盤價皆高於或等於區間下限價" in result.message
    assert iis.report.status == S.PASS, problems(iis.report)


def test_article15_scope_must_match_article8(tmp_path):
    """十五設定參數重複的 nt 定義改成單一標的寫法（八正確）：只有十五那一處判不一致。"""
    r = check(tmp_path, edits=[Edit("所有連結標的股票收盤價皆高於", "連結標的股票收盤價高於", "ts.art15")])
    only(r, "doc.accrual_scope")
    assert [x.field for x in rule(r, "doc.accrual_scope") if x.status == S.MISMATCH] == ["accrual_scope_art15"]


def test_half_of_the_basket_wording_is_not_read(tmp_path):
    """nt 定義只寫「所有」沒寫「皆」：計息範圍讀不到、轉人工。"""
    edit = Edit("所有連結標的股票收盤價皆高於", "所有連結標的股票收盤價高於", "ts.art8")
    r = check(tmp_path, edits=[edit])
    only(r, "doc.accrual_scope", S.REVIEW_REQUIRED)


# ---------------------------------------------------------------- 名稱、型態


def test_sample_names_with_periodic_wording_are_mismatched(tmp_path):
    """KO 每日、名稱卻寫 `（定期觀察）`／`Periodic`（3 份樣本都是）：說明書名稱判不一致（暫定做法，§3.8）；投資人須知名稱
    和說明書比，兩份相同所以通過。"""
    ts, iis = check_pair(tmp_path, edits=sample_names(D0))
    assert {(r, s) for r, _, s in problems(ts.report)} == {("standard.product_name", S.MISMATCH)}, problems(ts.report)
    assert iis.report.status == S.PASS, problems(iis.report)


ZH_BASKET = [Edit("連結一籃子股票", "連結股票", s) for s in ("ts.cover", "ts.art1", "iis.cover")]
EN_LAGGARD = [Edit("Linked（Laggard） Notes", "Linked Notes", s) for s in ("ts.cover", "iis.cover")]


@pytest.mark.parametrize(
    ("edits", "fields"),
    [
        ([*ZH_BASKET, *EN_LAGGARD], {"name_zh", "name_en"}),
        (ZH_BASKET, {"name_zh"}),
        (EN_LAGGARD, {"name_en"}),
    ],
    ids=["both", "zh-basket-only", "en-laggard-only"],
)
def test_basket_name_without_laggard_is_mismatched(tmp_path, edits, fields):
    """3 檔標的、名稱沒有 `一籃子`／`（Laggard）`（樣本 UD3 兩個都沒有）：缺哪個就那個名稱判不一致（暫定依標的數，§3.8）。"""
    ts, _ = check_pair(tmp_path, edits=edits)
    assert {(r, s) for r, _, s in problems(ts.report)} == {("standard.product_name", S.MISMATCH)}, problems(ts.report)
    assert {x.field for x in rule(ts.report, "standard.product_name") if x.status == S.MISMATCH} == fields


@pytest.mark.parametrize(
    "spec",
    [
        DacnSpec(currency_zh="澳幣"),
        DacnSpec(count=1),
        DacnSpec(ki="D"),
        DacnSpec(first_callable=2),
    ],
    ids=["aud", "single", "ki-daily", "non-call-2"],
)
def test_variants_without_samples_need_review(tmp_path, spec):
    r = check(tmp_path, spec)
    result = rule(r, "doc.template_variant")[0]
    assert (result.status, result.reason_code) == (S.REVIEW_REQUIRED, "template_variant_unverified")
    assert "待樣本驗證" in result.message


def test_single_underlying_name_needs_review(tmp_path):
    r = check(tmp_path, DacnSpec(count=1))
    assert {x.status for x in rule(r, "standard.product_name")} == {S.REVIEW_REQUIRED}


def test_monthly_ki_stays_manual(tmp_path):
    """KI 每期觀察（MKI）：照 workflow #386 維持人工，錯訊不寫「待樣本驗證」。"""
    r = check(tmp_path, DacnSpec(ki="P"))
    result = rule(r, "doc.template_variant")[0]
    assert result.status == S.REVIEW_REQUIRED
    assert "每期觀察 KI 維持人工處理（#386）" in result.message and "待樣本驗證" not in result.message
