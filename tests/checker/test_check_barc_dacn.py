"""Barclays DACN 黑箱回歸（workflow #547）：合成說明書與投資人須知（barc_synth.DacnSpec）經批量入口核對。

反例依 docs/checker/rules/barc-dacn-check-rules.md §7：正式條款正確、但其他出處錯時只判那一處。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import fitz
import pytest

from barc_synth import CHECKS, DacnSpec, Spec, build_pdf, row, schedule_rows
from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import BARC, BARC_DACN
from fcn_checker.schema import CheckStatus as S
from harness import check_all, flagged, iis_path
from harness import field_problems as problems
from harness import results as rule
from pdf_writer import Edit, zh_date
from reference_synth import DACN_PRODUCT, DEFAULT_ULS, REFERENCE_HEADERS, UL, build_reference_sheet

# 3 檔（第 1 檔名稱在價格表折成三行）、6 個月、KO 每日＋記憶式、KI 無、配息公式保證 1 期 = Non-Call 1、年利率 12%
# （月配息率 1.0000%）、執行價 = 配息門檻 70%
D0 = DacnSpec()
WITH_BARRIER = [*REFERENCE_HEADERS, "配息門檻(%)"]
B75 = DacnSpec(barrier=Decimal("75.00"))  # 配息門檻 ≠ 執行價：欄頭 % 與價格和執行價格欄不同，改字才分得開

FAMILY = CHECKS.family(D0)  # 單份核對殼（harness.SynthChecks）：預設 D0、參考條件表預設沒有配息門檻欄
check_pair, check = FAMILY.check_pair, FAMILY.check


def lines_of(path: Path):
    with fitz.open(path) as doc:
        return extract_lines(doc)


def only(report, rule_id: str, status: S = S.MISMATCH) -> None:
    """只有 `rule_id` 判錯（狀態 `status`）。"""
    assert flagged(report, rule_id, status), problems(report)
    assert {r for r, _, _ in problems(report)} == {rule_id}, problems(report)


FOUR = (*DEFAULT_ULS, UL("己庚辛半導體製造股份有限公司", "那斯達克證券交易所", "JGX UW", Decimal("45.6700")))


@pytest.mark.parametrize(
    ("spec", "headers"),
    [
        (D0, None),
        (D0, WITH_BARRIER),
        (DacnSpec(ki="AM", tenor=9, strike=Decimal("65.00"), ki_pct=Decimal("55.00")), WITH_BARRIER),
        (DacnSpec(tenor=12, ko=Decimal("95.00"), underlyings=FOUR), None),
        (DacnSpec(tenor=3, annual=Decimal("48.22")), None),
        (B75, WITH_BARRIER),
    ],
    ids=["3UL-no-column", "3UL-column", "KI-AM-9m", "4UL-KO95-12m", "3m", "barrier-75"],
)
def test_supported_dacn_pairs_pass_and_fill(tmp_path, spec, headers):
    ts, iis = check_pair(tmp_path, spec, headers=headers)
    assert ts.report.template == BARC_DACN.template_id
    assert iis.report.template == BARC_DACN.iis.template_id
    assert ts.report.status == S.PASS, problems(ts.report)
    assert iis.report.status == S.PASS, problems(iis.report)
    assert ts.fills_sheet
    # 比價日回填照 FCN D 型：比價日_1 = 第 1 期期末日、最後一格 = 最終評價日，中間 `-`
    slots = {d.column: d.expected for d in ts.report.backfill if d.column.startswith("比價日_")}
    last = f"比價日_{spec.tenor}"
    assert (slots["比價日_1"], slots[last]) == (schedule_rows(spec)[0]["valuation"], spec.final_date)
    assert {v for k, v in slots.items() if k not in ("比價日_1", last)} == {"-"}
    barrier = rule(ts.report, "field.coupon_barrier_pct")[0]
    assert barrier.status == S.PASS
    assert ("沒有配息門檻欄" in barrier.message) == (headers is None)
    for rid in (
        "doc.coupon_barrier",
        "schedule.accrual_starts",
        "doc.guaranteed_periods",
        "doc.template_variant",
        "doc.coupon_consistency",
        "doc.coupon_repeats",
        "doc.scenario_returns",
        "derive.monthly_coupon",
        "scenario.params",
        "scenario.reprint",
        "standard.product_name",
        "field.first_callable_period",
    ):
        assert rule(ts.report, rid) and all(x.status == S.PASS for x in rule(ts.report, rid)), rid
    for rid in ("iis.coupon_barrier", "iis.monthly_coupon", "iis.underlying_names"):
        assert rule(iis.report, rid) and all(x.status == S.PASS for x in rule(iis.report, rid)), rid
    # 每檔一列配息門檻價格（第 1 檔名稱在價格表折成三行，第三行仍跟著第 1 列）
    assert len([x for x in rule(ts.report, "doc.coupon_barrier") if "配息門檻價格" in x.field]) == len(spec.underlyings)


def test_long_names_folded_into_three_lines_stay_in_their_row(tmp_path):
    """價格表列靠上對齊：長名稱折成三行時第三行離下一列較近，仍歸到自己那一列（範本規格 §5）。"""
    from fcn_checker.parsers import barc_dacn

    pdf = build_pdf(tmp_path / f"{D0.product_code}_TS.pdf", D0)
    table = barc_dacn.read(lines_of(pdf)).fields["price_table"]
    assert table.ok, table.note
    assert [n.replace(" ", "") for n in table.value.names] == [u.name.replace(" ", "") for u in D0.underlyings]
    assert set(table.value.columns) == {"initial", "strike", "barrier", "ko"}


def test_fcn_and_dacn_templates_are_never_confused(tmp_path):
    fcn, dacn = Spec(product_code="029199990001"), DacnSpec(product_code="029199990002")
    pdfs = [build_pdf(tmp_path / f"{s.product_code}_TS.pdf", s) for s in (fcn, dacn)]
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [row(fcn), row(dacn)])
    outcome = check_all(sheet, [*pdfs, *map(iis_path, pdfs)])
    detected = {i.term_sheet.name: rule(i.report, "template.detect")[0].actual for i in outcome.items}
    assert detected == {
        pdfs[0].name: BARC.template_id,
        iis_path(pdfs[0]).name: BARC.iis.template_id,
        pdfs[1].name: BARC_DACN.template_id,
        iis_path(pdfs[1]).name: BARC_DACN.iis.template_id,
    }
    assert all(i.report.status == S.PASS for i in outcome.items), [problems(i.report) for i in outcome.items]


def test_term_sheet_and_investor_sheet_are_not_taken_for_each_other(tmp_path):
    pdf = build_pdf(tmp_path / f"{D0.product_code}_TS.pdf", D0)
    ts_lines, iis_lines = (lines_of(x) for x in (pdf, iis_path(pdf)))
    assert BARC_DACN.detect(ts_lines).matched
    assert BARC_DACN.iis.detect(iis_lines).matched
    # FCN 範本單獨看也收 DACN 文件（名稱相同）：由專到泛先試 DACN 範本（上一個測試），這裡只看說明書與投資人須知不互認
    for detect in (BARC_DACN.iis.detect, BARC.iis.detect):
        assert not detect(ts_lines).matched
    for detect in (BARC_DACN.detect, BARC.detect):
        assert not detect(iis_lines).matched


@pytest.mark.parametrize(
    "edits",
    [
        [Edit("小於其配息門檻，以至於", "小於其配息門檻，以致於", "ts.art14")],
        [Edit("每個數字代表一個配息支付日", "每個數字代表一個支付日", "ts.art14")],
        [Edit("之一籃子預定交易日天數-如以下", "之預定交易日天數-如以下", "ts.art14")],
        [Edit("詳見本章第15 條之表格所示", "詳見下表所示", "ts.art14")],
        [Edit("預定交易日日數(Nt)", "交易日日數(Nt)", "ts.art14")],
        [Edit("配息門檻（為最初價", "配息水準（為最初價", "ts.art15.price")],
    ],
    ids=["condition", "t-definition", "Nt-definition", "barrier-definition", "table-head", "price-table-head"],
)
def test_other_term_sheet_wording_is_not_supported(tmp_path, edits):
    ts, _ = check_pair(tmp_path, edits=edits)
    detect = rule(ts.report, "template.detect")[0]
    assert (detect.status, detect.reason_code) == (S.REVIEW_REQUIRED, "template_unsupported")
    assert "不能人工放行" in detect.message
    assert ts.status_label == "不支援的範本版本" and ts.release_problem


def test_trade_date_must_be_readable(tmp_path):
    """§13(2) 讀不到交易日（另行公告）：現行版特徵不成立 → 不支援的範本版本。"""
    ts, _ = check_pair(tmp_path, D0, pdf_spec=D0.with_(omit=frozenset({"trade_date"})))
    detect = rule(ts.report, "template.detect")[0]
    assert (detect.status, detect.reason_code) == (S.REVIEW_REQUIRED, "template_unsupported")
    assert "交易日" in detect.message


def test_investor_sheet_wording_is_not_supported(tmp_path):
    _, iis = check_pair(tmp_path, edits=[Edit("之一籃子預定交易日天數。", "之預定交易日天數。", "iis.p2")])
    detect = rule(iis.report, "template.detect")[0]
    assert (detect.status, detect.reason_code) == (S.REVIEW_REQUIRED, "template_unsupported")


def test_accrual_definition_against_another_price_is_not_dacn(tmp_path):
    """nt 定義改成比執行價格：不是區間計息（商品家族條件不成立）；全文仍有「配息門檻」→ 停在 DACN 範本、範本不符。"""
    edit = Edit("均高於或等於其配息門檻之配息評價", "均高於或等於其執行價格之配息評價", "ts.art14")
    ts, _ = check_pair(tmp_path, edits=[edit])
    detect = rule(ts.report, "template.detect")[0]
    assert detect.status == S.REVIEW_REQUIRED and detect.reason_code != "template_unsupported"
    assert "配息門檻" in detect.message


def test_trade_date_before_june_2026_with_current_wording_is_checked(tmp_path):
    """交易日早於 2026-06-01、寫法和現行版相同 → 照現行版範本辨識、照常核對（版本只看寫法，workflow #409）。"""
    old = DacnSpec(
        trade_date=dt.date(2026, 3, 2),
        issue_date=dt.date(2026, 3, 9),
        final_date=dt.date(2026, 9, 2),
        maturity_date=dt.date(2026, 9, 7),
    )
    ts, iis = check_pair(tmp_path, old)
    for item, template in ((ts, BARC_DACN.template_id), (iis, BARC_DACN.iis.template_id)):
        assert item.report.template == template
        assert rule(item.report, "template.detect")[0].status == S.PASS
    assert not flagged(ts.report, "template.detect") and not flagged(iis.report, "template.detect")


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


# ---------------------------------------------------------------- 配息門檻


def test_coupon_barrier_column_differs(tmp_path):
    """表上有配息門檻欄：說明書 §14 配息門檻和它比；不同時判不一致。"""
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


def test_price_table_barrier_header_must_match_the_definition(tmp_path):
    """§15 配息門檻欄頭 % 和 §14 定義句不同：只有欄頭判錯（每列價格照 §14 的 % 核對，仍一致）。"""
    r = check(
        tmp_path, B75, headers=WITH_BARRIER, edits=[Edit("格的75.00%）(四捨", "格的76.00%）(四捨", "ts.art15.price")]
    )
    only(r, "doc.coupon_barrier")
    head = [x for x in rule(r, "doc.coupon_barrier") if x.status == S.MISMATCH]
    assert [x.field for x in head] == ["coupon_barrier_head"]


def test_price_table_barrier_price_must_be_initial_times_pct(tmp_path):
    r = check(tmp_path, edits=[Edit("61.0400", "61.0500", "ts.art15.price.2.barrier")])
    only(r, "doc.coupon_barrier")
    bad = [x for x in rule(r, "doc.coupon_barrier") if x.status == S.MISMATCH]
    assert len(bad) == 1 and "配息門檻價格" in bad[0].field


def test_missing_barrier_price_needs_review(tmp_path):
    r = check(tmp_path, edits=[Edit("61.0400", "", "ts.art15.price.2.barrier")])
    assert flagged(r, "doc.coupon_barrier", S.REVIEW_REQUIRED), problems(r)


def test_investor_sheet_barrier_must_match_the_term_sheet(tmp_path):
    ts, iis = check_pair(
        tmp_path, B75, headers=WITH_BARRIER, edits=[Edit("初價格的75.00%", "初價格的76.00%", "iis.p2")]
    )
    assert ts.report.status == S.PASS, problems(ts.report)
    only(iis.report, "iis.coupon_barrier")


def test_investor_sheet_barrier_price_must_be_initial_times_pct(tmp_path):
    ts, iis = check_pair(tmp_path, B75, headers=WITH_BARRIER, edits=[Edit("65.4000", "65.5000", "iis.p2")])
    assert ts.report.status == S.PASS, problems(ts.report)
    only(iis.report, "iis.coupon_barrier")


# ---------------------------------------------------------------- 月配息率


def test_annual_rate_differs_from_the_sheet(tmp_path):
    """範本沒有年利率：參考條件表年利率由月配息率推算核對（說明書）、÷ 12 核對（投資人須知）。"""
    ts, iis = check_pair(tmp_path, **{"Coupon p.a. (%)": 12.5})
    assert flagged(ts.report, "derive.monthly_coupon", S.MISMATCH), problems(ts.report)
    assert flagged(iis.report, "iis.monthly_coupon", S.MISMATCH), problems(iis.report)
    assert rule(ts.report, "field.coupon_pa_pct")[0].status == S.NOT_APPLICABLE


def test_two_rates_of_the_formula_must_agree(tmp_path):
    """§14 配息率 t 公式兩段的 X 不同：讀不到月配息率，轉人工覆核。"""
    r = check(tmp_path, edits=[Edit("=1.0000% × nt/Nt;", "=1.1000% × nt/Nt;", "ts.art14")])
    assert flagged(r, "derive.monthly_coupon", S.REVIEW_REQUIRED), problems(r)
    assert not [p for p in problems(r) if p[2] == S.MISMATCH], problems(r)


def test_investor_sheet_two_rates_must_agree(tmp_path):
    ts, iis = check_pair(tmp_path, edits=[Edit("為1.0000% × nt/Nt", "為1.1000% × nt/Nt", "iis.p2")])
    assert ts.report.status == S.PASS, problems(ts.report)
    assert flagged(iis.report, "iis.monthly_coupon", S.REVIEW_REQUIRED), problems(iis.report)


@pytest.mark.parametrize(
    ("edit", "rule_id"),
    [
        (Edit("每單位商品面額 × 1.0000%(顯示", "每單位商品面額 × 1.1000%(顯示", "ts.art15"), "doc.coupon_consistency"),
        (Edit("遠低於月配息率1.0000%", "遠低於月配息率1.1000%", "ts.art9"), "doc.coupon_consistency"),
        (Edit("1.0000%；或", "1.1000%；或", "ts.art9"), "doc.coupon_repeats"),
        (Edit("相關配息率為：1.0000% × dt/Dt", "相關配息率為：1.1000% × dt/Dt", "ts.art9"), "doc.coupon_repeats"),
        (Edit("(100% + 1.0000%)", "(100% + 1.1000%)", "ts.art16.i"), "scenario.params"),
        (Edit("× 1.0000% × (9/21)", "× 1.1000% × (9/21)", "ts.art16.ii"), "scenario.params"),
        (Edit("× 1.0000% × (0/21)", "× 1.1000% × (0/21)", "ts.art16.iii"), "scenario.params"),
        (Edit("× 1.0000% (四捨五入", "× 1.1000% (四捨五入", "ts.art16.iii"), "scenario.params"),
    ],
    ids=[
        "art15-2",
        "art9-tail",
        "art9-3-i",
        "art9-3-ii",
        "favourable",
        "general-accrual",
        "worst-accrual",
        "worst-first",
    ],
)
def test_other_places_of_the_rate_must_match_article14(tmp_path, edit, rule_id):
    """§14 月配息率正確（= 參考條件表推算），其他出處錯：只有那一處判錯（投資人須知不受影響）。"""
    ts, iis = check_pair(tmp_path, edits=[edit])
    assert flagged(ts.report, rule_id, S.MISMATCH), problems(ts.report)
    assert {r for r, _, _ in problems(ts.report)} <= {rule_id, "doc.scenario_returns"}, problems(ts.report)
    assert iis.report.status == S.PASS, problems(iis.report)


# ---------------------------------------------------------------- §16 一般、最差情況依假設天數的算式（workflow #577）

GENERAL, WORST, CALC = "ts.art16.ii", "ts.art16.iii", "doc.scenario_calculations"
# D0：面額 10,000、月配息率 1.0000%、6 期、g = 1；一般情況 (9/21) → 42.86、累積 314.30、3.14%；最差情況 (0/21) → 0.00、
# 累積 100.00、等值 289.30、-96.11%（合成值，見 barc_synth._dacn_scenarios）
GENERAL_CUMULATIVE = "100.00 美元 × 1 + 42.86 美元 × 5 = 314.30 美元"
# 第二行區間計息算式（不留空白才放得進一行）
SECOND_ACCRUAL = "第2到6個配息支付日：每單位配息金額=10,000美元×1.0000%×(9/21)(四捨五入至小數點後第2位)=42.86美元"
PAYS = "發行機構將於每一個「配息支付日t」支付商品面額乘以各配息觀察期之配息率所計算之配息金額。"  # 情境開頭的說明句


def calc_problems(report) -> set[tuple[str, S]]:
    return {(field, status) for rid, field, status in problems(report) if rid == CALC}


@pytest.mark.parametrize("spec", [DacnSpec(days=22, general_days=13), DacnSpec(days=19, general_days=17, worst_days=3)])
def test_scenario_calculations_use_the_written_days(tmp_path, spec):
    """n、N 本身不核對：換成別的假設、配息、累積與總報酬率跟著算對 → 通過；平均年化報酬率不核對（改字也不判錯）；
    最差情況累積第二段是 0、沒寫期數（樣本寫法）也照常核對。"""
    r = check(tmp_path, spec, edits=[Edit("(平均年化報酬率：", "(平均年化報酬率約：", GENERAL)])
    assert r.status == S.PASS, problems(r)
    fields = {x.field for x in rule(r, CALC)}
    assert fields == {
        f"{k}_{p}" for k in ("general", "worst") for p in ("coupon", "accrual", "cumulative", "return")
    } | {"general_final"}


def test_worst_cumulative_without_count_is_checked(tmp_path):
    assert rule(check(tmp_path), CALC, "worst_cumulative")[0].status == S.PASS
    r = check(tmp_path, edits=[Edit("+ 0.00 美元 = 100.00 美元", "+ 0.00 美元 = 100.10 美元", WORST)])
    assert ("worst_cumulative", S.MISMATCH) in calc_problems(r), problems(r)


@pytest.mark.parametrize(
    ("edits", "expected"),
    [
        ([Edit("= 100.00 美元", "= 100.01 美元", GENERAL)], {"general_coupon", "general_cumulative"}),
        ([Edit("= 0.00 美元", "= 0.01 美元", WORST)], {"worst_accrual", "worst_cumulative"}),
        (
            [
                Edit(GENERAL_CUMULATIVE, "100.01 美元 × 1 + 42.86 美元 × 5 = 314.31 美元", GENERAL),
                Edit("= 100.00 美元", "= 100.01 美元", GENERAL),
                Edit("+ 314.30 美元)", "+ 314.31 美元)", GENERAL),
            ],
            {"general_coupon"},
        ),
        (
            [
                Edit(GENERAL_CUMULATIVE, "100.00 美元 × 1 + 42.87 美元 × 5 = 314.35 美元", GENERAL),
                Edit("= 42.86 美元", "= 42.87 美元", GENERAL),
                Edit("+ 314.30 美元)", "+ 314.35 美元)", GENERAL),
            ],
            {"general_accrual"},
        ),
        ([Edit("× (9/21)", "× (10/21)", GENERAL)], {"general_accrual"}),
        ([Edit("第2 到6 個配息支付日", "第2 到5 個配息支付日", GENERAL)], {"general_accrual"}),
        ([Edit("= 10,000 美元 × 1.0000% × (9/21)", "= 1,000 美元 × 1.0000% × (9/21)", GENERAL)], {"general_accrual"}),
        ([Edit("第1 個配息支付日：", "第1 到2 個配息支付日：", WORST)], {"worst_coupon"}),
        ([Edit("= 10,000 美元 × 1.0000% (四捨五入", "= 1,000 美元 × 1.0000% (四捨五入", WORST)], {"worst_coupon"}),
        ([Edit("+ 42.86 美元 × 5", "+ 42.68 美元 × 5", GENERAL)], {"general_cumulative"}),
        ([Edit("42.86 美元 × 5 =", "42.86 美元 × 4 =", GENERAL)], {"general_cumulative"}),
        (
            [
                Edit("× 5 = 314.30 美元", "× 5 = 314.03 美元", GENERAL),
                Edit("+ 314.30 美元)", "+ 314.03 美元)", GENERAL),
            ],
            {"general_cumulative"},
        ),
        ([Edit("× 100% = 10,000 美元", "× 100% = 10,100 美元", GENERAL)], {"general_final", "general_return"}),
        ([Edit("= 10,000 美元 × 100%", "= 1,000 美元 × 100%", GENERAL)], {"general_final"}),
        ([Edit("- 1 = 3.14%", "- 1 = 3.41%", GENERAL)], {"general_return"}),
        ([Edit("- 1 = -96.11%", "- 1 = -96.12%", WORST)], {"worst_return"}),
        ([Edit("+ 314.30 美元)", "+ 341.30 美元)", GENERAL)], {"general_return"}),
        ([Edit("+ 289.30 美元)", "+ 298.30 美元)", WORST)], {"worst_return"}),
        ([Edit(") / 10,000 美元] - 1 = 3.14%", ") / 1,000 美元] - 1 = 3.14%", GENERAL)], {"general_return"}),
    ],
    ids=[
        "general-first-coupon",
        "worst-accrual",
        "coupon-carried-into-cumulative",
        "accrual-carried-into-cumulative",
        "days-without-amount",
        "accrual-periods",
        "accrual-denomination",
        "coupon-periods",
        "coupon-denomination",
        "cumulative-leg-amount",
        "cumulative-leg-periods",
        "cumulative-total",
        "general-final",
        "final-denomination",
        "general-return",
        "worst-return",
        "return-term-not-cumulative",
        "return-term-not-equivalent",
        "return-divisor",
    ],
)
def test_scenario_calculations_must_follow_the_written_days(tmp_path, edits, expected):
    """§3.9 反例：只有算錯的那幾處判不一致；每期配息算錯、累積照錯的配息加總時只報每期配息。"""
    r = check(tmp_path, edits=edits)
    assert calc_problems(r) == {(f, S.MISMATCH) for f in expected}, problems(r)
    assert {rid for rid, _, _ in problems(r)} == {CALC}, problems(r)


@pytest.mark.parametrize(
    ("edits", "expected"),
    [
        ([Edit("× (9/21)", "× (9/0)", GENERAL)], {"general_accrual"}),
        (
            [Edit("× 1.0000% (四捨五入", "× 1.0000% × (9/21) (四捨五入", GENERAL)],
            {"general_coupon", "general_accrual", "general_cumulative"},
        ),
        ([Edit(PAYS, SECOND_ACCRUAL, GENERAL)], {"general_accrual", "general_cumulative"}),
        ([Edit("+ 42.86 美元 × 5", "+ 42.86 美元", GENERAL)], {"general_cumulative"}),
    ],
    ids=["zero-total-days", "first-coupon-as-accrual", "two-accrual-formulas", "count-omitted-for-nonzero"],
)
def test_unreadable_scenario_calculations_go_to_review(tmp_path, edits, expected):
    """N = 0 算不出、第 1～g 期那行也寫成區間計息、同一情境兩行區間計息算式、金額不是 0 的一段沒寫期數（分不出是每期
    還是合計）：轉人工覆核。"""
    r = check(tmp_path, edits=edits)
    assert calc_problems(r) == {(f, S.REVIEW_REQUIRED) for f in expected}, problems(r)


def test_accrual_rounding_is_half_up_at_the_midpoint(tmp_path):
    """10,000 × 1% × 5 ÷ 32 = 15.625：四捨五入是 15.63（銀行家進位的 15.62 判不一致）。"""
    spec = DacnSpec(days=32, general_days=5)
    assert check(tmp_path / "up", spec).status == S.PASS
    r = check(tmp_path / "even", spec, edits=[Edit("= 15.63 美元", "= 15.62 美元", GENERAL)])
    found = rule(r, CALC, "general_accrual")[0]
    assert found.status == S.MISMATCH and found.expected == Decimal("15.63"), problems(r)


def test_favourable_total_return_is_checked(tmp_path):
    r = check(tmp_path, edits=[Edit("- 1 = 1.0000%(平均", "- 1 = 1.1000%(平均", "ts.art16.i")])
    assert flagged(r, "doc.scenario_returns", S.MISMATCH), problems(r)


# ---------------------------------------------------------------- 保證期數、日期表、型態


def test_guaranteed_periods_must_equal_non_call(tmp_path):
    """§14 配息公式保證 2 期（配息率t=1到2），但 Non-Call 1：保證期數 ≠ Non-Call（沒有樣本的型態另轉人工）。"""
    r = check(tmp_path, D0, pdf_spec=DacnSpec(formula_g=2))
    assert flagged(r, "doc.guaranteed_periods", S.MISMATCH), problems(r)
    assert flagged(r, "doc.template_variant", S.REVIEW_REQUIRED)


def test_accrual_table_start_must_match_the_autocall_table(tmp_path):
    start = schedule_rows(D0)[2]["start"]
    r = check(tmp_path, edits=[Edit(start, "2030 年1 月2 日", "ts.art14.coupon.3.start")])
    assert flagged(r, "schedule.accrual_starts", S.MISMATCH), problems(r)
    assert "第 3 期" in [x for x in rule(r, "schedule.accrual_starts") if x.status == S.MISMATCH][0].actual


def test_last_accrual_period_must_end_on_the_final_valuation_date(tmp_path):
    end = zh_date(D0.final_date)
    r = check(tmp_path, edits=[Edit(end, "2030 年7 月9 日", "ts.art14.coupon.6.end")])
    assert flagged(r, "schedule.last_observation", S.MISMATCH), problems(r)


def test_a_missing_accrual_period_is_not_passed(tmp_path):
    """§14 配息觀察期表少一期：期數和配息率 t 公式、第 13 條表不同 → 月配息率與保證期數讀不到、期始日判期數不同。"""
    r3 = schedule_rows(D0)[2]
    cells = {"t": "3", "start": r3["start"], "end": r3["end"], "days": "20", "payment": zh_date(r3["payment"])}
    r = check(tmp_path, edits=[Edit(text, "", f"ts.art14.coupon.3.{key}") for key, text in cells.items()])
    assert flagged(r, "derive.monthly_coupon", S.REVIEW_REQUIRED), problems(r)
    assert flagged(r, "schedule.accrual_starts", S.MISMATCH), problems(r)


@pytest.mark.parametrize(
    "spec",
    [
        DacnSpec(currency_zh="澳幣"),
        DacnSpec(underlyings=DEFAULT_ULS[:1]),
        DacnSpec(memory=False),
        DacnSpec(guaranteed=2),
        DacnSpec(ki="D"),
        DacnSpec(ki="M"),
    ],
    ids=["aud", "single", "ko-not-memory", "non-call-2", "ki-daily", "ki-monthly"],
)
def test_variants_without_samples_need_review(tmp_path, spec):
    r = check(tmp_path, spec)
    result = rule(r, "doc.template_variant")[0]
    assert (result.status, result.reason_code) == (S.REVIEW_REQUIRED, "template_variant_unverified")
    assert "待樣本驗證" in result.message
