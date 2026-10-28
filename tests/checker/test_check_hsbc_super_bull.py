"""HSBC Super Bull 黑箱回歸（Issue #314）：合成說明書與投資人須知（hsbc_synth.SbSpec）經批量入口核對。"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from fcn_checker.issuers import HSBC, HSBC_SUPER_BULL
from fcn_checker.schema import CheckStatus as S
from harness import check_all, flagged, iis_path
from harness import field_problems as problems
from harness import results as rule
from hsbc_synth import SB_PRODUCT, SbSpec, Spec, build_pdf
from pdf_writer import Edit
from reference_synth import as_headers, build_reference_sheet, reference_row

S0 = SbSpec()  # 3 檔、6 個月、第 3 個月決定一次、KO% = K% = 85、出場紅利 6%


def row(spec: Spec, **overrides):
    """與 `spec` 一致的參考條件表列；Super Bull 的 Product 填 `Super Bull`。`overrides` 以 Excel 欄名覆寫。"""
    out = reference_row(spec)
    if isinstance(spec, SbSpec):
        out["Product"] = SB_PRODUCT
    out.update(overrides)
    return out


def check_pair(tmp_path: Path, spec: Spec = S0, *, pdf_spec: Spec | None = None, edits=(), sheet_row=None, **overrides):
    """合成說明書（`pdf_spec`，預設同 `spec`）＋投資人須知＋參考條件表一列，經批量入口核對，回傳（說明書, 投資人須知）。"""
    tmp_path.mkdir(parents=True, exist_ok=True)
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", pdf_spec or spec, edits=edits)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [sheet_row or row(spec, **overrides)])
    outcome = check_all(sheet, [pdf, iis_path(pdf)])
    ts = next(i for i in outcome.items if i.term_sheet == pdf)
    return ts, next(i for i in outcome.items if i.term_sheet != pdf)


def check(tmp_path: Path, spec: Spec = S0, **kw):
    return check_pair(tmp_path, spec, **kw)[0].report


def compare_dates(report) -> dict:
    return {d.column: d.expected for d in report.backfill if d.column.startswith("比價日_") and d.expected != "-"}


def swapped(r: dict, a: int, b: int) -> dict:
    """參考條件表列的第 a、b 檔標的（代號與各價格欄）對調。"""
    out = dict(r)
    for key in r:
        for x, y in ((a, b), (b, a)):
            if key == f"UL_{x}" or key.startswith(f"UL_{x}_"):
                out[f"UL_{y}" + key[len(f"UL_{x}") :]] = r[key]
    return out


@pytest.mark.parametrize(
    "spec",
    [
        S0,
        SbSpec(
            count=2, tenor=4, months=(2,), annual=Decimal("26.0000"), ko=Decimal("90.0000"), strike=Decimal("90.0000")
        ),
        SbSpec(count=4, maxi=True),
        SbSpec(months=(3,), tenor=12, shift=-4),
    ],
    ids=["3UL", "4m-2UL", "4UL-maxi", "12m-early"],
)
def test_supported_super_bull_pairs_pass_and_fill_compare_dates(tmp_path, spec):
    ts, iis = check_pair(tmp_path, spec)
    assert ts.report.template == HSBC_SUPER_BULL.template_id
    assert iis.report.template == HSBC_SUPER_BULL.iis.template_id
    assert ts.report.status == S.PASS, problems(ts.report)
    assert iis.report.status == S.PASS, problems(iis.report)
    assert ts.fills_sheet
    # 出場紅利不年化，和 Coupon p.a. (%) 相等；Non-Call = 決定日的月數
    assert rule(ts.report, "field.coupon_pa_pct")[0].status == S.PASS
    assert rule(ts.report, "field.first_callable_period")[0].actual == spec.months[0]
    for rid in ("doc.scenario_calculations", "doc.exit_bonus", "schedule.observation_order", "standard.product_name"):
        assert all(x.status == S.PASS for x in rule(ts.report, rid)), rid
    assert rule(iis.report, "iis.no_coupon")[0].status == S.PASS
    # 比價日：`比價日_m` = 決定日、`比價日_{天期}` = 最後評價日，其他格 `-`
    expected = dict(zip((f"比價日_{m}" for m in (*spec.months, spec.tenor)), spec.ends, strict=True))
    assert compare_dates(ts.report) == expected


def test_sheet_underlying_order_is_matched_by_code_for_super_bull_only(tmp_path):
    """HSBC Super Bull 參考條件表的標的順序和說明書不同時以代號配對（#314）；HSBC FCN 照舊依順序。"""
    ts, _ = check_pair(tmp_path / "sb", S0, sheet_row=swapped(row(S0), 1, 3))
    assert ts.report.status == S.PASS, problems(ts.report)
    fcn = Spec(product_code="325199990002", count=3)
    ts, _ = check_pair(tmp_path / "fcn", fcn, sheet_row=swapped(row(fcn), 1, 3))
    assert flagged(ts.report, "field.underlyings", S.MISMATCH), problems(ts.report)


def test_fcn_and_super_bull_templates_are_never_confused(tmp_path):
    fcn = Spec(product_code="325199990001")
    express = Spec(product_code="325199990003", first_callable=0)
    sb = SbSpec(product_code="325199990002")
    pdfs = [build_pdf(tmp_path / f"{s.product_code}_TS.pdf", s) for s in (fcn, express, sb)]
    rows = [row(fcn), row(express, Product="FCN_Express"), row(sb)]
    outcome = check_all(build_reference_sheet(tmp_path / "FCN參考條件.xlsx", rows), [*pdfs, *map(iis_path, pdfs)])
    detected = {i.term_sheet.name: rule(i.report, "template.detect")[0].actual for i in outcome.items}
    assert detected == {
        **{p.name: HSBC.template_id for p in pdfs[:2]},
        **{iis_path(p).name: HSBC.iis.template_id for p in pdfs[:2]},
        pdfs[2].name: HSBC_SUPER_BULL.template_id,
        iis_path(pdfs[2]).name: HSBC_SUPER_BULL.iis.template_id,
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
        [Edit("支付贖回金額 = 每單位面額 × (100% + 自動提前贖回配息)", "支付贖回金額 = 每單位面額 × 100%", "ts.art11")],
        [Edit("回付款日", "", "ts.art11.ko"), Edit("自動提前贖", "", "ts.art11.ko")],  # 表頭不是四欄
        [Edit("以現金給付", "以現金給付 每單位面額 x 100%", "ts.art11"), Edit("每單位面額x (", "(", "ts.art11")],
        [Edit("本商品不適用", "請見第 11 項", "ts.art16")],
        [Edit("參與率：本商品不適用", "參與率：100%", "ts.art17")],
    ],
    ids=["x100", "three-columns", "formula", "art16", "participation"],
)
def test_other_term_sheet_wording_is_not_supported(tmp_path, edits):
    ts, _ = check_pair(tmp_path, S0, edits=edits)
    detect = rule(ts.report, "template.detect")[0]
    assert (detect.status, detect.reason_code) == (S.REVIEW_REQUIRED, "template_unsupported")
    assert "寫法和現行版不同" in detect.message and "不能人工放行" in detect.message
    assert ts.status_label == "不支援的範本版本" and ts.release_problem


def test_other_investor_sheet_wording_is_not_supported(tmp_path):
    """第二寫成 FCN 的配息句（核對規則 §7 反例）：認得出是 HSBC Super Bull 投資人須知、但不是現行版。"""
    edit = Edit("收益分配事項：請見產品說明書第一章第11 項之說明。", "收益分配事項：本商品支付固定配息。", "iis.p2")
    _, iis = check_pair(tmp_path, S0, edits=[edit])
    detect = rule(iis.report, "template.detect")[0]
    assert (detect.status, detect.reason_code) == (S.REVIEW_REQUIRED, "template_unsupported")
    assert iis.status_label == "不支援的範本版本" and iis.release_problem


@pytest.mark.parametrize(
    "spec",
    [
        SbSpec(count=1),
        SbSpec(currency_zh="日圓"),
        SbSpec(ko=Decimal("90.0000")),
        SbSpec(months=(2, 4)),
    ],
    ids=["single", "jpy", "ko-not-strike", "two-decisions"],
)
def test_variants_without_samples_need_review(tmp_path, spec):
    r = check(tmp_path, spec)
    result = rule(r, "doc.template_variant")[0]
    assert (result.status, result.reason_code) == (S.REVIEW_REQUIRED, "template_variant_unverified")
    assert "待樣本驗證" in result.message


def test_decision_date_too_far_from_the_month_needs_review(tmp_path):
    r = check(tmp_path, S0, pdf_spec=SbSpec(shift=9))
    assert r.status == S.REVIEW_REQUIRED
    assert flagged(r, "schedule.observation_months", S.REVIEW_REQUIRED), problems(r)


def test_payment_date_must_be_after_the_decision_date(tmp_path):
    decision, payment = (f"{d.year}/{d.month}/{d.day}" for d in (S0.ends[0], S0.payments[0]))
    r = check(tmp_path, S0, edits=[Edit(payment, decision, "ts.art11.ko")])
    assert flagged(r, "schedule.observation_order"), problems(r)


def test_row_ko_different_from_the_price_table_header_is_reported(tmp_path):
    """提前贖回表 KO% 和表頭不同（核對規則 §7 反例）。"""
    r = check(tmp_path, S0, edits=[Edit("85.0000%", "86.0000%", "ts.art11.ko")])
    assert flagged(r, "doc.price_header_pct", S.MISMATCH), problems(r)


def test_ko_header_must_be_read_by_column_name(tmp_path):
    """KO 欄在執行價欄前面：表頭 % 依欄名對（KO% ≠ K% 時 KO 欄表頭寫成執行價 % → 不一致）。"""
    spec = SbSpec(ko=Decimal("90.0000"))
    r = check(tmp_path, spec, edits=[Edit("(即期初股價的90%)", "(即期初股價的85%)", "ts.art12.table")])
    assert flagged(r, "doc.price_header_pct", S.MISMATCH), problems(r)


@pytest.mark.parametrize(
    ("where", "old", "new", "rule_id"),
    [
        # 出場紅利其他出處（核對規則 §3.3）：§11(1) 正確、其他出處錯
        ("ts.art11.ko", "106.0000%", "106.5000%", "doc.exit_bonus"),
        ("ts.art18", "自動提前贖回配息為6.0000%", "自動提前贖回配息為6.5000%", "doc.exit_bonus"),
        ("ts.art18", "自動提前贖回配息為6.0000%", "自動提前贖回配息：8.5900%為6.0000%", "doc.exit_bonus"),  # 殘留數字
        ("ts.art18", "(100% + 6.0000%)", "(100% + 6.5000%)", "doc.exit_bonus"),
        # 情境重算（核對規則 §3.7）
        ("ts.art18", "= 美元10,600.00", "= 美元10,700.00", "doc.scenario_calculations"),
        ("ts.art18", "= 美元 600.00", "= 美元 700.00", "doc.scenario_calculations"),
        ("ts.art18", "計算): 24% (", "計算): 25% (", "doc.scenario_calculations"),
        ("ts.art18", "=美元15,294.12", "=美元15,394.12", "doc.scenario_calculations"),
        ("ts.art18", "= 117.6471 股", "= 117.6481 股", "doc.scenario_calculations"),
        ("ts.art18", "在此例中為117 股", "在此例中為118 股", "doc.scenario_calculations"),  # 四捨五入而不是捨去
        ("ts.art18", "=美元 38.83", "=美元 38.93", "doc.scenario_calculations"),
        ("ts.art18", "= 美元 7,058.83 (", "= 美元 7,068.83 (", "doc.scenario_calculations"),
        ("ts.art18", "為: -58.82%", "為: -57.82%", "doc.scenario_calculations"),
        ("ts.art18", "期末匯率為1 美元 = 美元 1.00", "期末匯率為1 美元 = 美元 1.10", "doc.scenario_calculations"),
    ],
)
def test_term_sheet_difference_is_reported(tmp_path, where, old, new, rule_id):
    r = check(tmp_path, S0, edits=[Edit(old, new, where)])
    assert flagged(r, rule_id, S.MISMATCH), problems(r)


def test_rounding_and_trailing_zeros_are_not_differences(tmp_path):
    """贖回價、紅利、年化報酬率只比數值（#314 已確認）：少寫尾數 0 不算錯。"""
    edits = [
        Edit("106.0000%", "106.00%", "ts.art11.ko"),
        Edit("(100% + 6.0000%)", "(100% + 6.000%)", "ts.art18"),
        Edit("為: -58.82%", "為: -58.820%", "ts.art18"),
    ]
    r = check(tmp_path, S0, edits=edits)
    assert r.status == S.PASS, problems(r)


def test_frequency_sentence_is_not_checked(tmp_path):
    edit = Edit("每半年一次", "每2 個月一次", "ts.art11")
    assert check(tmp_path, S0, edits=[edit]).status == S.PASS


def test_english_name_prefix_is_not_checked_but_the_rest_is(tmp_path):
    """英文名稱只核對 `SuperBull Notes (Non Guaranteed, Unsecured)`，前面多的字不核對（#314 已確認）。"""
    other = Edit("SuperBull Notes", "Bonus SuperBull Notes", "ts.cover")
    assert not flagged(check(tmp_path / "prefix", S0, edits=[other]), "standard.product_name")
    wrong = Edit("SuperBull Notes (Non Guaranteed, Unsecured)", "SuperBull Notes (Unsecured)", "ts.cover")
    assert flagged(check(tmp_path / "suffix", S0, edits=[wrong]), "standard.product_name", S.MISMATCH)


def test_scenario_strike_must_be_the_worst_underlying_strike(tmp_path):
    r = check(tmp_path, S0, edits=[Edit("(130.0000/85.0000)", "(130.0000/86.0000)", "ts.art18")])
    assert flagged(r, "scenario.strike", S.MISMATCH), problems(r)
