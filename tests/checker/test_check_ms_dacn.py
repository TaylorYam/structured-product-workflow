"""MS DACN 黑箱回歸（workflow #550；情境二～四區間計息驗算 #578）：合成說明書與投資人須知（ms_synth.DacnSpec）經批量入口核對。

反例依 docs/checker/rules/ms-dacn-check-rules.md §7：正式條款正確、但其他出處錯時只判那一處。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import fitz
import pytest

from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import MS, MS_BEN, MS_DACN
from fcn_checker.schema import CheckStatus as S
from harness import check_all, flagged, iis_path
from harness import field_problems as problems
from harness import results as rule
from ms_ben_synth import BenSpec
from ms_ben_synth import build_pdf as build_ben_pdf
from ms_synth import CHECKS, DacnSpec, Spec, build_pdf
from pdf_writer import Edit, zh_date
from reference_synth import DACN_PRODUCT, REFERENCE_HEADERS, build_reference_sheet

# 2 檔美股、4 個月、KO 每日＋記憶式、KI 到期、第 15 項保證 1 期 = Non-Call 1、年利率 12%（固定配息率 1.0000%、每期 100.00
# 美元、區間計息算式 ×（10/20）= 50.00 美元；情境二～四年化報酬率 9.00%、7.50%、-117.00%）、執行價 = 配息門檻 70%
D0 = DacnSpec()
WITH_BARRIER = [*REFERENCE_HEADERS, "配息門檻(%)"]
# D0 情境二區間計息算式（前半段左邊是文字、最後一步才是數字）與損益算式
ACCRUAL_2 = "x（10/20） = 10,000.00 美元×1.0000%×（10/20） = 50.00 美元"
PNL_2 = "= 10,000.00 美元+100.00 美元x1+50.00 美元-10,000.00 美元 = 150.00 美元"

FAMILY = CHECKS.family(D0)  # 單份核對殼（harness.SynthChecks）：預設 D0、參考條件表預設沒有配息門檻欄
check_pair, check = FAMILY.check_pair, FAMILY.check


def lines_of(path: Path):
    with fitz.open(path) as doc:
        return extract_lines(doc)


def scenario_problems(report) -> dict[str, S]:
    """`doc.scenario_calculations` 不是 PASS 的欄位與狀態（其他規則要全部通過）。"""
    assert {r for r, _, _ in problems(report)} <= {"doc.scenario_calculations"}, problems(report)
    return {x.field: x.status for x in rule(report, "doc.scenario_calculations") if x.status != S.PASS}


def only(report, rule_id: str, status: S = S.MISMATCH) -> None:
    """只有 `rule_id` 判錯（狀態 `status`）。"""
    assert flagged(report, rule_id, status), problems(report)
    assert {r for r, _, _ in problems(report)} == {rule_id}, problems(report)


# ---------------------------------------------------------------- 支援的型態


@pytest.mark.parametrize(
    ("spec", "headers"),
    [
        (D0, None),
        (D0, WITH_BARRIER),
        (DacnSpec(annual=Decimal("10.50"), count=4, tenor=12), WITH_BARRIER),
        (DacnSpec(count=3, tenor=6, strike=Decimal("67.00")), None),
    ],
    ids=["no-column", "with-column", "annual-10.5-4ul-12m", "3ul-6m-strike-67"],
)
def test_supported_dacn_pairs_pass_and_fill(tmp_path, spec, headers):
    ts, iis = check_pair(tmp_path, spec, headers=headers)
    assert ts.report.template == MS_DACN.template_id
    assert iis.report.template == MS_DACN.iis.template_id
    assert ts.report.status == S.PASS, problems(ts.report)
    assert iis.report.status == S.PASS, problems(iis.report)
    assert ts.fills_sheet
    # 比價日回填照 MS FCN D 型：比價日_1 = 第 1 期終止日、最後一格 = 期末定價日，中間 `-`
    slots = {d.column: d.expected for d in ts.report.backfill if d.column.startswith("比價日_")}
    last = f"比價日_{spec.tenor}"
    assert (slots["比價日_1"], slots[last]) == (spec.ends[0], spec.final_date)
    assert {v for k, v in slots.items() if k not in ("比價日_1", last)} == {"-"}
    barrier = rule(ts.report, "field.coupon_barrier_pct")[0]
    assert barrier.status == S.PASS and barrier.actual == spec.strike
    assert ("沒有配息門檻欄" in barrier.message) == (headers is None)
    for rid in (
        "doc.guaranteed_periods",
        "doc.scenario_guaranteed_periods",
        "doc.template_variant",
        "doc.coupon_periods",
        "doc.scenario_calculations",
        "derive.monthly_coupon",
        "scenario.params",
        "scenario.reprint",
        "scenario.strike",
        "standard.product_name",
    ):
        assert rule(ts.report, rid) and all(x.status == S.PASS for x in rule(ts.report, rid)), rid
    assert rule(iis.report, "iis.guaranteed_periods")[0].status == S.PASS
    # 第 15 項的出處：固定配息率與 {X%×n(j)/N(j)} 各一筆
    assert len(rule(ts.report, "derive.monthly_coupon")) == 2
    # 情境四個（不含無法履約）各有 g；有區間計息的情境二～四另驗算區間計息每期配息與損益加總（#578），年化報酬率不核
    assert len(rule(ts.report, "doc.scenario_guaranteed_periods")) == 4
    calcs = {x.field: x for x in rule(ts.report, "doc.scenario_calculations")}
    accrual = {f"情境{n}_{k}" for n in "二三四" for k in ("coupon", "accrual", "pnl")}
    assert set(calcs) == {"情境一_coupon", "情境一_pnl", *accrual}
    assert calcs["情境二_accrual"].expected == calcs["情境三_accrual"].expected == spec.accrued
    assert calcs["情境四_accrual"].expected == 0
    annualized = [x for x in ts.report.results if x.field.startswith("scenario_annualized_return")]
    assert len(annualized) == 1 and "情境一" in annualized[0].item.name


def test_fcn_ben_and_dacn_templates_are_never_confused(tmp_path):
    """同一批：MS FCN（D 型也有 {X%×n(j)/N(j)}）與 DACN 各自辨識成自己的範本（由專到泛：DACN → BEN → FCN）。"""
    specs = (Spec(), D0)
    pdfs = [build_pdf(tmp_path / f"{s.product_code}_TS.pdf", s) for s in specs]
    rows = [CHECKS.row(s) for s in specs]  # DACN 的 Product 填 `DACN`
    outcome = check_all(build_reference_sheet(tmp_path / "FCN參考條件.xlsx", rows), [*pdfs, *map(iis_path, pdfs)])
    detected = {i.term_sheet.name: rule(i.report, "template.detect")[0].actual for i in outcome.items}
    expected = {}
    for pdf, template in zip(pdfs, (MS, MS_DACN), strict=True):
        expected[pdf.name] = template.template_id
        expected[iis_path(pdf).name] = template.iis.template_id
    assert detected == expected
    assert all(i.report.status == S.PASS for i in outcome.items), [problems(i.report) for i in outcome.items]


def test_term_sheet_and_investor_sheet_are_not_taken_for_each_other(tmp_path):
    pdf = build_pdf(tmp_path / f"{D0.product_code}_TS.pdf", D0)
    ts_lines, iis_lines = (lines_of(x) for x in (pdf, iis_path(pdf)))
    assert MS_DACN.detect(ts_lines).matched
    assert MS_DACN.iis.detect(iis_lines).matched
    # FCN（固定配息）、BEN 範本不收 DACN；說明書與投資人須知不互認
    for detect in (MS.detect, MS_BEN.detect, MS_DACN.iis.detect, MS.iis.detect, MS_BEN.iis.detect):
        assert not detect(ts_lines).matched
    for detect in (MS.iis.detect, MS_BEN.iis.detect, MS_DACN.detect, MS.detect, MS_BEN.detect):
        assert not detect(iis_lines).matched


@pytest.mark.parametrize("ko_obs", ["D", "P"])
def test_fcn_documents_are_not_claimed_by_dacn(tmp_path, ko_obs):
    """MS FCN（D 型也有 {X%×n(j)/N(j)}）全文沒有「區間累積配息」：DACN 範本不收、也不攔下（範本規格 §8 負面測試）。"""
    spec = Spec(ko_obs=ko_obs)
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    for detect, lines in ((MS_DACN.detect, lines_of(pdf)), (MS_DACN.iis.detect, lines_of(iis_path(pdf)))):
        result = detect(lines)
        assert not result.matched and not result.claimed and not result.unsupported


def test_ben_documents_are_not_claimed_by_dacn(tmp_path):
    """MS BEN 說明書與投資人須知不被 DACN 範本收、也不攔下（範本規格 §8 負面測試）。"""
    spec = BenSpec()
    pdf = build_ben_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    for detect, lines in ((MS_DACN.detect, lines_of(pdf)), (MS_DACN.iis.detect, lines_of(iis_path(pdf)))):
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
        [Edit("N(j) = 於第 j 個配息觀察期間的配息觀察日總數", "N(j) = 於第 j 個配息觀察期間的交易日總數", "ts.art15")],
        [Edit("小數點後第 2 位", "小數點後第 4 位", "ts.art15")],
        [Edit("孰先，籃子中表現最差", "孰後，籃子中表現最差", "ts.art15")],
        [Edit("持有之商品單位數×每單位配息金額(j)", "持有之商品單位數×每單位配息金額", "ts.art15")],
        [Edit("每單位商品面額× 固定配息率", "每單位商品面額× 首期配息率", "ts.art15")],
        [Edit("即同時為所有連結標的之預定交易日之日", "即任一連結標的之預定交易日", "ts.art15")],
        [Edit(f"交易日：{zh_date(D0.trade_date)}", "交易日：另行公告", "ts.art14")],
    ],
    ids=["Nj", "rounding", "nj-wording", "monthly-amount", "fixed-amount", "observation-day", "trade-date-unreadable"],
)
def test_other_term_sheet_wording_is_not_supported(tmp_path, edits):
    ts, _ = check_pair(tmp_path, edits=edits)
    unsupported(ts)


def test_other_investor_sheet_wording_is_not_supported(tmp_path):
    edit = Edit(
        "N(j) = 於第 j 個配息觀察期間的配息觀察日總數", "N(j) = 於第 j 個配息觀察期間的交易日總數", "iis.coupon"
    )
    ts, iis = check_pair(tmp_path, edits=[edit])
    assert ts.report.status == S.PASS, problems(ts.report)
    unsupported(iis)


def test_accrual_definition_against_another_price_is_not_dacn(tmp_path):
    """n(j) 定義改成比下限價格：不是區間累積配息（商品家族條件不成立）；全文仍有「區間累積配息」→ 停在 DACN 範本、範本未知
    （不退回 FCN 範本）。"""
    edit = Edit("等於或大於其執行價的配息觀察日總數", "等於或大於其下限價格的配息觀察日總數", "ts.art15")
    ts, _ = check_pair(tmp_path, edits=[edit])
    detect = rule(ts.report, "template.detect")[0]
    assert detect.status == S.REVIEW_REQUIRED and detect.reason_code == "template_unknown"
    assert "區間累積配息" in detect.message


def test_trade_date_before_june_2026_with_current_wording_is_checked(tmp_path):
    """交易日早於 2026-06-01、寫法和現行版相同 → 照現行版範本辨識（版本只看寫法，workflow #409）。"""
    spec = D0.with_(trade_date=dt.date(2026, 3, 2), issue_date=dt.date(2026, 3, 9))
    ts, iis = check_pair(tmp_path, spec)
    for item, template in ((ts, MS_DACN.template_id), (iis, MS_DACN.iis.template_id)):
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


# ---------------------------------------------------------------- 配息門檻（= 執行價）


def test_coupon_barrier_column_differs(tmp_path):
    """表上有配息門檻欄：第 16 項執行價表頭 % 和它比；不同時判不一致。"""
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


def test_barrier_evidence_cites_the_accrual_definition(tmp_path):
    """配息門檻的證據附第 15 項 n(j) 定義（說明為什麼是執行價）與第 16 項執行價表頭。"""
    r = check(tmp_path, headers=WITH_BARRIER)
    texts = "".join(e.text for e in rule(r, "field.coupon_barrier_pct")[0].document_evidence).replace(" ", "")
    assert "等於或大於其執行價" in texts and "執行價（期" in texts


# ---------------------------------------------------------------- 配息率


@pytest.mark.parametrize(
    ("edit", "rule_id"),
    [
        (Edit("{1.0000%×n(j)/N(j)}", "{1.1000%×n(j)/N(j)}", "ts.art15"), "derive.monthly_coupon"),
        (Edit("1.0000%x（10/20）", "1.1000%x（10/20）", "ts.scen.二"), "scenario.params"),
        (Edit("面額×1.0000%×（10/20）", "面額×1.1000%×（10/20）", "ts.scen.三"), "scenario.params"),
        (Edit("浮動配息率（1.0000%）", "浮動配息率（1.1000%）", "ts.scen.四"), "scenario.params"),
        (Edit("固定配息率（1.0000%）", "固定配息率（1.1000%）", "ts.scen.二"), "scenario.params"),
    ],
    ids=["art15-formula", "scenario2-accrual", "scenario3-accrual", "scenario4-floating", "scenario2-fixed"],
)
def test_other_places_of_the_rate_must_match_the_fixed_rate(tmp_path, edit, rule_id):
    """第 15 項 (2) 的固定配息率正確（= 參考條件表推算），其他出處錯：只有那一處判錯（投資人須知不受影響）。"""
    ts, iis = check_pair(tmp_path, edits=[edit])
    only(ts.report, rule_id)
    assert iis.report.status == S.PASS, problems(iis.report)


def test_investor_sheet_formula_rate_must_match(tmp_path):
    """投資人須知收益分配事項的 {X%×n(j)/N(j)} 錯：只有投資人須知那一處判錯（同 FCN D 型）。"""
    ts, iis = check_pair(tmp_path, edits=[Edit("{1.0000%×n(j)/N(j)}", "{1.1000%×n(j)/N(j)}", "iis.coupon")])
    assert ts.report.status == S.PASS, problems(ts.report)
    assert {r for r, _, _ in problems(iis.report)} == {"iis.monthly_coupon"}, problems(iis.report)


@pytest.mark.parametrize("scenario", ["二", "三"])
def test_fixed_period_coupon_of_accrual_scenarios_is_checked(tmp_path, scenario):
    """情境二、三第 1 期固定配息算錯（面額 × 月配息率）：該情境的每期配息判錯；區間計息算式不算每期配息算式。"""
    edit = Edit("×1.0000% = 100.00 美元", "×1.0000% = 101.00 美元", f"ts.scen.{scenario}")
    r = check(tmp_path, edits=[edit])
    only(r, "doc.scenario_calculations")
    mismatched = [x.field for x in rule(r, "doc.scenario_calculations") if x.status == S.MISMATCH]
    assert mismatched == [f"情境{scenario}_coupon"]


def test_annualized_returns_and_assumed_days_are_not_checked(tmp_path):
    """情境二～四的年化報酬率依假設算出、不核；n/M 本身也不核——改成 （5/10） 算出的區間計息配息仍是 50.00，不判錯（#578）。"""
    r = check(
        tmp_path,
        edits=[
            Edit(ACCRUAL_2, "x（5/10） = 10,000.00 美元×1.0000%×（5/10） = 50.00 美元", "ts.scen.二"),
            Edit("9.00%", "1.23%", "ts.scen.二"),
            Edit("7.50%", "4.56%", "ts.scen.三"),
            Edit("-117.00%", "-1.00%", "ts.scen.四"),
        ],
    )
    assert r.status == S.PASS, problems(r)


def test_accrual_scenario_without_the_final_formula_needs_review(tmp_path):
    """情境四只寫 `面額×浮動配息率（X%）×（0/M） = 0`、沒有最後一步的數字算式：仍是區間計息情境（損益不照 FCN 核），
    區間計息每期配息讀不到、損益無法驗算 → 都轉人工（#578）。"""
    edit = Edit("（0/20） = 10,000.00 美元×1.0000%×（0/20） = 0 美元", "（0/20） = 0 美元", "ts.scen.四")
    r = check(tmp_path, edits=[edit])
    assert scenario_problems(r) == {"情境四_accrual": S.REVIEW_REQUIRED, "情境四_pnl": S.REVIEW_REQUIRED}


def test_accrual_coupon_must_follow_its_own_rate(tmp_path):
    """最後一步寫 `面額×1.1000%×（10/20） = 50.00`：50.00 是月配息率算的值、但不等於算式自己的 X → 區間計息每期配息判錯
    （X 本身另由 `scenario.params` 判錯），損益照寫的金額加總、不受影響（核對規則 §3.7 ②）。"""
    edit = Edit(
        "= 10,000.00 美元×1.0000%×（10/20） = 50.00", "= 10,000.00 美元×1.1000%×（10/20） = 50.00", "ts.scen.二"
    )
    r = check(tmp_path, edits=[edit])
    assert {rid for rid, _, _ in problems(r)} == {"scenario.params", "doc.scenario_calculations"}, problems(r)
    assert [x.field for x in rule(r, "doc.scenario_calculations") if x.status != S.PASS] == ["情境二_accrual"]


@pytest.mark.parametrize(("written", "expected"), [("3.13", {}), ("3.12", {"情境二_accrual": S.MISMATCH})])
def test_accrual_coupon_rounds_half_up(tmp_path, written, expected):
    """（1/32）：10,000.00 × 1.0000% × 1/32 = 3.125，第 15 項 (2) 四捨五入到 2 位 → 3.13；寫 3.12（half-even）判錯。
    損益用文件寫的金額加總（同 FCN），不受影響。"""
    pnl = Decimal("100.00") + Decimal(written)
    edits = [
        Edit(ACCRUAL_2, f"x（1/32） = 10,000.00 美元×1.0000%×（1/32） = {written} 美元", "ts.scen.二"),
        Edit(PNL_2, f"= 10,000.00 美元+100.00 美元x1+{written} 美元-10,000.00 美元 = {pnl} 美元", "ts.scen.二"),
    ]
    assert scenario_problems(check(tmp_path, edits=edits)) == expected


@pytest.mark.parametrize(
    ("edits", "expected"),
    [
        # 區間計息每期配息算錯（損益照寫的金額一致加總）
        (
            [
                Edit("（10/20） = 50.00 美元", "（10/20） = 50.01 美元", "ts.scen.二"),
                Edit("+50.00 美元-10,000.00 美元 = 150.00", "+50.01 美元-10,000.00 美元 = 150.01", "ts.scen.二"),
            ],
            {"情境二_accrual": S.MISMATCH},
        ),
        (
            [Edit("= 10,000.00 美元×1.0000%×（10/20）", "= 1,000.00 美元×1.0000%×（10/20）", "ts.scen.二")],
            {"情境二_accrual": S.MISMATCH},
        ),
        # M 是 0 算不出 → 人工；損益照寫的金額照樣驗算
        (
            [Edit("×1.0000%×（10/20） = 50.00", "×1.0000%×（10/0） = 50.00", "ts.scen.三")],
            {"情境三_accrual": S.REVIEW_REQUIRED},
        ),
        # 損益裡的區間計息項對不到區間計息算式的金額 → 人工（不猜）
        (
            [Edit("+50.00 美元-10,000.00 美元 = 150.00", "+51.00 美元-10,000.00 美元 = 151.00", "ts.scen.二")],
            {"情境二_pnl": S.REVIEW_REQUIRED},
        ),
        # 同一情境兩個不同的區間計息金額（兩個算式本身都對）→ 損益人工
        (
            [
                Edit(
                    "×（10/20） = 50.00 美元",
                    "×（10/20） = 50.00 美元；10,000.00 美元×1.0000%×（9/20） = 45.00 美元",
                    "ts.scen.三",
                )
            ],
            {"情境三_pnl": S.REVIEW_REQUIRED},
        ),
        # 區間計息次數合計 ≠ 配息次數 − 保證期數
        (
            [Edit("50.00x3-10,000.00 美元 = 250.00", "50.00x2-10,000.00 美元 = 200.00", "ts.scen.三")],
            {"情境三_pnl": S.MISMATCH},
        ),
        # 固定配息項次數 ≠ 保證期數（獲利、較差情境）
        (
            [
                Edit(
                    "100.00 美元x1+50.00 美元-10,000.00 美元 = 150.00",
                    "100.00 美元x2+50.00 美元-10,000.00 美元 = 250.00",
                    "ts.scen.二",
                )
            ],
            {"情境二_pnl": S.MISMATCH},
        ),
        (
            [
                Edit(
                    "100.00 美元x1+6,000.00 美元-10,000.00 美元 = -3,900.00",
                    "100.00 美元x2+6,000.00 美元-10,000.00 美元 = -3,800.00",
                    "ts.scen.四",
                )
            ],
            {"情境四_pnl": S.MISMATCH},
        ),
        # 加總算錯
        (
            [Edit("-10,000.00 美元 = 250.00 美元", "-10,000.00 美元 = 251.00 美元", "ts.scen.三")],
            {"情境三_pnl": S.MISMATCH},
        ),
        # 拿回的本金、減的面額寫錯（加總照寫的數一致）
        (
            [Edit(PNL_2, "= 10,100.00 美元+100.00 美元x1+50.00 美元-10,000.00 美元 = 250.00 美元", "ts.scen.二")],
            {"情境二_pnl": S.MISMATCH},
        ),
        (
            [Edit("+50.00 美元-10,000.00 美元 = 150.00", "+50.00 美元-9,900.00 美元 = 250.00", "ts.scen.二")],
            {"情境二_pnl": S.MISMATCH},
        ),
        # 區間計息配息不是 0 卻沒列進損益
        (
            [Edit("x1+50.00 美元-10,000.00 美元 = 150.00", "x1-10,000.00 美元 = 100.00", "ts.scen.二")],
            {"情境二_pnl": S.MISMATCH},
        ),
    ],
    ids=[
        "accrual-wrong",
        "accrual-denomination",
        "zero-total-days",
        "pnl-term-unmatched",
        "two-accrual-amounts",
        "accrual-count",
        "fixed-times",
        "worse-fixed-times",
        "pnl-sum",
        "principal",
        "denomination-subtracted",
        "missing-accrual",
    ],
)
def test_accrual_scenario_counterexamples(tmp_path, edits, expected):
    """核對規則 §7（#578）：只有那一個情境的那一項判錯或轉人工，其他規則照樣通過。"""
    assert scenario_problems(check(tmp_path, edits=edits)) == expected


def test_accrual_formula_is_still_counted_for_fcn(tmp_path):
    """MS FCN 的情境若出現區間計息算式（FCN 沒有這種寫法）：照 FCN 當成讀不出來的每期配息算式、轉人工（FCN 結果不變）。"""
    edit = Edit("=10,000.00 美元×1.0000%=100.00 美元", "=10,000.00 美元×1.0000%×（10/20）=50.00 美元", "ts.scen.二")
    r = CHECKS.check(tmp_path, Spec(), edits=[edit])
    assert flagged(r, "doc.scenario_calculations", S.REVIEW_REQUIRED), problems(r)


# ---------------------------------------------------------------- 保證期數


def test_guaranteed_periods_must_equal_non_call(tmp_path):
    """Non-Call 2，但第 15 項只保證 1 期（`每單位配息金額(1)`）：保證期數 ≠ Non-Call（沒有樣本的型態另轉人工）。"""
    r = check(tmp_path, DacnSpec(first_callable=2))
    assert flagged(r, "doc.guaranteed_periods", S.MISMATCH), problems(r)
    assert flagged(r, "doc.template_variant", S.REVIEW_REQUIRED)
    assert not flagged(r, "doc.coupon_periods"), problems(r)


def test_coupon_periods_must_start_after_non_call(tmp_path):
    """第 15 項 `j 係為` 起點 ≠ Non-Call + 1：只有期別範圍判錯（同 MS FCN D 型）。"""
    edit = Edit("j 係為2 至4 的數字，代表第2 至4", "j 係為3 至4 的數字，代表第3 至4", "ts.art15")
    r = check(tmp_path, edits=[edit])
    only(r, "doc.coupon_periods")


def test_other_guaranteed_period_wordings_are_not_read(tmp_path):
    """第 15 項與投資人須知寫「每單位配息金額(1～2)」：沒有樣本的寫法，讀不到、轉人工（不判成某一個值，也不是範本版本不支援）。"""
    edits = [
        Edit("每單位配息金額(1) = 每單位商品面額× 固定", "每單位配息金額(1～2) = 每單位商品面額× 固定", "ts.art15"),
        Edit("每單位配息金額(1) = 每單位商品面額 × 固定", "每單位配息金額(1～2) = 每單位商品面額 × 固定", "iis.coupon"),
    ]
    ts, iis = check_pair(tmp_path, edits=edits)
    assert ts.report.template == MS_DACN.template_id and iis.report.template == MS_DACN.iis.template_id
    assert flagged(ts.report, "doc.guaranteed_periods", S.REVIEW_REQUIRED), problems(ts.report)
    assert not [p for p in problems(ts.report) if p[2] == S.MISMATCH], problems(ts.report)
    only(iis.report, "iis.guaranteed_periods", S.REVIEW_REQUIRED)


def test_investor_sheet_guaranteed_periods_must_match(tmp_path):
    """說明書第 15 項讀得到 g = 1、投資人須知寫成沒有樣本的期別：只有投資人須知的保證期數轉人工。"""
    edit = Edit("每單位配息金額(1) = 每單位商品面額 × 固定", "每單位配息金額(2) = 每單位商品面額 × 固定", "iis.coupon")
    ts, iis = check_pair(tmp_path, edits=[edit])
    assert ts.report.status == S.PASS, problems(ts.report)
    only(iis.report, "iis.guaranteed_periods", S.REVIEW_REQUIRED)


def test_scenario_guaranteed_periods_must_match_article15(tmp_path):
    """情境三寫「本商品第2 個月發放固定配息」（第 15 項保證 1 期）：只有情境三那一處判不一致。"""
    edit = Edit("本商品第1 個月發放固定配息，而後", "本商品第2 個月發放固定配息，而後", "ts.scen.三")
    r = check(tmp_path, edits=[edit])
    only(r, "doc.scenario_guaranteed_periods")
    bad = [x.field for x in rule(r, "doc.scenario_guaranteed_periods") if x.status == S.MISMATCH]
    assert bad == ["情境三_guaranteed_periods"]


def test_scenario_without_the_guaranteed_sentence_needs_review(tmp_path):
    edit = Edit("本商品第1 個月發放固定配息，故", "本商品首月發放固定配息，故", "ts.scen.一")
    r = check(tmp_path, edits=[edit])
    only(r, "doc.scenario_guaranteed_periods", S.REVIEW_REQUIRED)


# ---------------------------------------------------------------- 情境、名稱、型態


def test_worse_scenario_strike_must_match_article16(tmp_path):
    """情境四 `籃子中表現最差之連結標的（代碼）收盤價 = … 小於其執行價 = K`（DACN 句型，沒有「的」）：K ≠ 第 16 項 → 判錯。"""
    r = check(tmp_path, edits=[Edit("小於其執行價 = 70.0000", "小於其執行價 = 71.0000", "ts.scen.四")])
    only(r, "scenario.strike")


def test_article1_name_with_fixed_coupon_is_mismatched(tmp_path):
    """第一章第 1 項名稱寫成「固定配息」（封面正確）：名稱一致判錯。"""
    r = check(tmp_path, edits=[Edit("區間累積配息", "固定配息", "ts.art1")])
    only(r, "doc.name_consistency")


def test_fixed_coupon_name_on_the_cover_is_not_dacn(tmp_path):
    """說明書封面與第一章名稱都寫「固定配息」：DACN 名稱條件不成立、FCN 英文名稱也不符 → 範本未知、轉人工。"""
    edits = [Edit("區間累積配息", "固定配息", s) for s in ("ts.cover", "ts.art1")]
    ts, _ = check_pair(tmp_path, edits=edits)
    detect = rule(ts.report, "template.detect")[0]
    assert (detect.status, detect.reason_code) == (S.REVIEW_REQUIRED, "template_unknown"), detect.message


@pytest.mark.parametrize(
    "spec",
    [
        DacnSpec(currency_zh="澳幣"),
        DacnSpec(count=1),
        DacnSpec(count=5),
        DacnSpec(memory=False),
        DacnSpec(ko_obs="P"),
        DacnSpec(ki="none"),
        DacnSpec(ki="D"),
        DacnSpec(first_callable=2),
    ],
    ids=["aud", "single", "five", "not-memory", "ko-periodic", "no-ki", "ki-daily", "non-call-2"],
)
def test_variants_without_samples_need_review(tmp_path, spec):
    r = check(tmp_path, spec)
    result = rule(r, "doc.template_variant")[0]
    assert (result.status, result.reason_code) == (S.REVIEW_REQUIRED, "template_variant_unverified")
    assert "待樣本驗證" in result.message


def test_monthly_ki_is_an_unsupported_variant(tmp_path):
    """KI 每期觀察（MKI）：MS 上手支援樣態表本來就不支援（照 FCN），在型態檢查前就轉人工、不能人工放行（#386 也維持人工）。"""
    item = FAMILY.check_item(tmp_path, DacnSpec(ki="P"))
    result = rule(item.report, "batch.variant")[0]
    assert result.status == S.REVIEW_REQUIRED and "KI 每期觀察" in result.message
    assert item.unsupported_variant and item.release_problem
