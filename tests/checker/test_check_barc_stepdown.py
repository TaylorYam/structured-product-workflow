"""Barclays FCN_Stepdown 黑箱回歸（workflow #544）：合成說明書與投資人須知（barc_synth.Spec(step=…)）經批量入口核對。

反例依 docs/checker/rules/barc-stepdown-check-rules.md §7。
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import fitz
import pytest

from barc_synth import CHECKS, STEPDOWN_CHECKS, STEPDOWN_HEADERS, STEPDOWN_PRODUCT, Spec, build_pdf, stepdown_row
from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import BARC, BARC_STEPDOWN
from fcn_checker.schema import CheckStatus as S
from harness import check_all, flagged, iis_path
from harness import field_problems as problems
from harness import results as rule
from pdf_writer import Edit
from reference_synth import build_reference_sheet, reference_row

S0 = STEPDOWN_CHECKS.default()  # D 型、記憶式、6 個月、保證配息 1 期（Non-Call 1）、起始 KO 100%、每期降 2 個百分點
STEP_RULES = ("field.ko_step_pct", "field.ko_levels")
check, check_pair = STEPDOWN_CHECKS.check, STEPDOWN_CHECKS.check_pair


def lines_of(path: Path):
    with fitz.open(path) as doc:
        return extract_lines(doc)


def only(report, rule_ids: tuple[str, ...] | str, status: S = S.MISMATCH) -> None:
    """只有 `rule_ids` 判錯（狀態 `status`）。"""
    ids = (rule_ids,) if isinstance(rule_ids, str) else rule_ids
    for rid in ids:
        assert flagged(report, rid, status), (rid, problems(report))
    assert {r for r, _, _ in problems(report)} == set(ids), problems(report)


def unsupported(report) -> str:
    """說明書判成不支援的 Stepdown 範本版本，回傳錯訊。"""
    detect = rule(report, "template.detect")[0]
    assert (detect.status, detect.reason_code) == (S.REVIEW_REQUIRED, "template_unsupported"), problems(report)
    return detect.message


def compare_slots(report) -> dict[str, object]:
    return {d.column: d.expected for d in report.backfill if d.column.startswith("比價日_")}


@pytest.mark.parametrize(
    "spec",
    [
        S0,
        Spec(product_code="029199990003", step=Decimal("1.00"), ko=Decimal("95.00"), ki="AM", tenor=9),
        Spec(product_code="029199990004", step=Decimal("2.00"), ko_obs="P", tenor=12),
        Spec(product_code="029199990005", step=Decimal("1.00"), ko_obs="P", ko=Decimal("98.00"), ki="AM"),
        Spec(product_code="029199990006", step=Decimal("2.00"), guaranteed=2, ki="AM"),
    ],
    ids=["D", "D-KI-AM-KO95", "P-12m", "P-KI-AM-KO98", "D-G2"],
)
def test_supported_stepdown_pairs_pass_and_fill(tmp_path, spec):
    ts, iis = check_pair(tmp_path, spec)
    assert ts.report.template == BARC_STEPDOWN.template_id
    assert iis.report.template == BARC_STEPDOWN.iis.template_id
    assert ts.report.status == S.PASS, problems(ts.report)
    assert iis.report.status == S.PASS, problems(iis.report)
    assert all(r.rule_id != "template.product_family" for r in ts.report.results)
    for rid in (*STEP_RULES, "field.ko_pct", "field.ko_memory", "field.underlying_prices"):
        statuses = {x.status for x in rule(ts.report, rid)}
        assert S.PASS in statuses and statuses <= {S.PASS, S.NOT_APPLICABLE}, rid  # 沒有標的的格子不適用
    levels = rule(ts.report, "field.ko_levels")[0]
    assert f"共 {spec.tenor - spec.first_callable + 1} 期" in levels.message
    # 說明書內部「每期觸發百分比 = §15 定義句」不適用（Stepdown 沒有定義句）
    trigger = rule(ts.report, "doc.autocall_trigger_per_period")
    assert trigger and {x.status for x in trigger} == {S.NOT_APPLICABLE}
    # 比價日：第一個可提前出場期到最後一期每一格都填（D 型也是，ADR 0033），之前的格子 `-`，最後一格 = 最終評價日
    assert ts.fills_sheet
    slots = compare_slots(ts.report)
    k, n = spec.first_callable, spec.tenor
    filled = [slots[f"比價日_{p}"] for p in range(k, n + 1)]
    assert "-" not in filled and filled == sorted(filled) and len(set(filled)) == len(filled), slots
    assert filled[-1] == spec.final_date
    assert {slots[f"比價日_{p}"] for p in range(1, k)} <= {"-"}


def test_fcn_daily_still_fills_two_compare_dates(tmp_path):
    """回歸：BARC FCN D 型（KO 不降）比價日仍只填第一個可提前出場期與最後一期，中間 `-`。"""
    spec = Spec()
    report = CHECKS.check(tmp_path, spec)
    assert report.template == BARC.template_id and report.status == S.PASS, problems(report)
    slots = compare_slots(report)
    first, last = f"比價日_{spec.first_callable}", f"比價日_{spec.tenor}"
    assert slots[last] == spec.final_date and slots[first] not in ("-", None)
    assert {v for c, v in slots.items() if c not in (first, last)} == {"-"}


def test_compare_dates_on_sheet_are_checked_not_overwritten(tmp_path):
    """D 型 Stepdown 表上已填的中間比價日照常比對：和說明書不同 → 不一致、保留原值。"""
    ok = compare_slots(check(tmp_path / "ok"))["比價日_3"]
    report = check(tmp_path / "bad", **{"比價日_3": "2026/01/02"})
    assert flagged(report, "backfill.compare_dates", S.MISMATCH), problems(report)
    assert ok != "-"


def test_ko_step_differs_from_sheet(tmp_path):
    report = check(tmp_path, **{"KO降幅(%)": 3.0})
    only(report, STEP_RULES)
    levels = rule(report, "field.ko_levels")[0]
    assert "第 2 期" in levels.message


@pytest.mark.parametrize("blank", [None, "-"], ids=["empty", "dash"])
def test_blank_ko_step_goes_to_review(tmp_path, blank):
    report = check(tmp_path, **{"KO降幅(%)": blank})
    only(report, STEP_RULES, S.REVIEW_REQUIRED)


def test_sheet_without_ko_step_column_goes_to_review(tmp_path):
    report = check(tmp_path, headers=[h for h in STEPDOWN_HEADERS if h != "KO降幅(%)"])
    only(report, STEP_RULES, S.REVIEW_REQUIRED)


def test_first_period_ko_differs_from_sheet(tmp_path):
    """表上 KO(%) 不是第 1 期：KO(%) 與每期 KO% 都不一致（KO 價照表上第 1 期，另改成一致的值）。"""
    report = check(tmp_path, **{"KO(%)": 98.0})
    assert flagged(report, "field.ko_pct", S.MISMATCH) and flagged(report, "field.ko_levels", S.MISMATCH)
    assert not flagged(report, "field.ko_step_pct", S.MISMATCH)


def test_ko_price_is_initial_times_first_period_ko(tmp_path):
    spec = Spec(product_code="029199990007", step=Decimal("1.00"), ko=Decimal("95.00"))
    ok = check(tmp_path / "ok", spec)
    assert ok.status == S.PASS, problems(ok)
    initial = spec.underlyings[0].initial
    wrong = float(initial)  # 用 100% 的價格（不是第 1 期 95%）
    report = check(tmp_path / "bad", spec, **{"UL_1_KO價": wrong})
    only(report, "field.underlying_prices")


@pytest.mark.parametrize(
    ("spec", "edit", "reason"),
    [
        (S0, Edit("96.00%", "97.00%", "ts.art13.ko.3.trigger"), "不是每期固定往下降"),
        (Spec(product_code="029199990008", step=Decimal("0.00")), None, "不是每期固定往下降"),
        (Spec(product_code="029199990009", step=Decimal("-1.00")), None, "不是每期固定往下降"),
        (S0, Edit("則該標的資產應視為已發生「記憶事件」。", "。", "ts.art13"), "記憶事件句"),
        (
            S0,
            Edit(
                "「最初價格」詳見下表所示；",
                "「自動提前出場觸發價格」詳見下表所示（為最初價格的100.00%）；",
                "ts.art15",
            ),
            "第 15 條仍有 KO 定義句",
        ),
        (
            S0,
            Edit("乘以相對應之「自動提前出場觸發百分比」", "乘以「自動提前出場觸發百分比」", "ts.art13"),
            "觸發價格",
        ),
        (Spec(product_code="029199990010", step=Decimal("2.00"), guaranteed=0), None, "第 1 期期始日就有日期"),
    ],
    ids=["uneven", "flat", "rising", "no-memory", "art15-ko-definition", "no-ko-price-definition", "D-G0"],
)
def test_stepdown_variants_outside_the_template_are_unsupported(tmp_path, spec, edit, reason):
    report = check(tmp_path, spec, edits=[edit] if edit else [])
    assert reason in unsupported(report)


def test_product_fcn_with_stepdown_document_goes_to_review(tmp_path):
    report = check(tmp_path, Product="FCN")
    assert report.template == BARC_STEPDOWN.template_id
    assert flagged(report, "template.product_family", S.REVIEW_REQUIRED), problems(report)


def test_product_stepdown_with_fcn_document_goes_to_review(tmp_path):
    report = CHECKS.check(tmp_path, Spec(), headers=STEPDOWN_HEADERS, **{"Product": STEPDOWN_PRODUCT, "KO降幅(%)": 2.0})
    assert report.template == BARC.template_id
    assert flagged(report, "template.product_family", S.REVIEW_REQUIRED), problems(report)


def test_fcn_and_stepdown_templates_are_never_confused(tmp_path):
    fcn = Spec(product_code="029199990011")
    pdfs = [build_pdf(tmp_path / f"{s.product_code}_TS.pdf", s) for s in (fcn, S0)]
    rows = [reference_row(fcn), stepdown_row(S0)]
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", rows, STEPDOWN_HEADERS)
    outcome = check_all(sheet, [*pdfs, *map(iis_path, pdfs)])
    detected = {i.term_sheet.name: rule(i.report, "template.detect")[0].actual for i in outcome.items}
    assert detected == {
        pdfs[0].name: BARC.template_id,
        iis_path(pdfs[0]).name: BARC.iis.template_id,
        pdfs[1].name: BARC_STEPDOWN.template_id,
        iis_path(pdfs[1]).name: BARC_STEPDOWN.iis.template_id,
    }
    assert all(i.report.status == S.PASS for i in outcome.items), [problems(i.report) for i in outcome.items]


def test_fcn_documents_are_not_stepdown(tmp_path):
    """BARC FCN 說明書與投資人須知（記憶式、非記憶式）都不被 Stepdown 範本認走，也不停在 Stepdown（不是 claimed）。"""
    for spec in (Spec(product_code="029199990012"), Spec(product_code="029199990013", memory=False, ko_obs="P")):
        pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
        ts, iis = BARC_STEPDOWN.detect(lines_of(pdf)), BARC_STEPDOWN.iis.detect(lines_of(iis_path(pdf)))
        assert not ts.matched and not ts.claimed and not ts.unsupported
        assert not iis.matched


def test_investor_sheet_listing_ko_price_is_not_stepdown(tmp_path):
    """投資人須知表頭沒有 KO 欄、表前說明句卻列了觸發價格：兩項矛盾，不是 Stepdown 投資人須知。"""
    edit = Edit("「最初價格」與「執行價格」", "「最初價格」、「執行價格」與「自動提前出場觸發價格」", "iis.p2")
    pdf = build_pdf(tmp_path / f"{S0.product_code}_TS.pdf", S0, edits=[edit])
    result = BARC_STEPDOWN.iis.detect(lines_of(iis_path(pdf)))
    assert not result.matched
    assert any("表前說明句卻列了觸發價格" in f for f in result.failed)


def test_term_sheet_and_investor_sheet_are_not_taken_for_each_other(tmp_path):
    pdf = build_pdf(tmp_path / f"{S0.product_code}_TS.pdf", S0)
    ts_lines, iis_lines = lines_of(pdf), lines_of(iis_path(pdf))
    assert BARC_STEPDOWN.detect(ts_lines).matched
    assert BARC_STEPDOWN.iis.detect(iis_lines).matched
    assert not BARC_STEPDOWN.iis.detect(ts_lines).matched
    assert not BARC_STEPDOWN.detect(iis_lines).matched
