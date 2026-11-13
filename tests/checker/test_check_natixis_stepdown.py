"""Natixis FCN_Stepdown 黑箱回歸（workflow #545）：合成說明書與投資人須知（natixis_synth.StepdownSpec）經批量入口核對。

反例依 docs/checker/rules/natixis-stepdown-check-rules.md §7。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import fitz
import pytest

from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import NATIXIS, NATIXIS_DACN, NATIXIS_STEPDOWN
from fcn_checker.schema import CheckStatus as S
from harness import check_all, flagged, iis_path
from harness import field_problems as problems
from harness import results as rule
from natixis_synth import CHECKS, STEPDOWN_CHECKS, DacnSpec, Spec, StepdownSpec, build_pdf, row
from pdf_writer import Edit
from reference_synth import STEPDOWN_HEADERS, STEPDOWN_PRODUCT, build_reference_sheet

S0 = STEPDOWN_CHECKS.default()  # D 型、記憶式、6 個月、Non-Call 1、起始 KO 100%、每期降 2 個百分點、KI 到期觀察
STEP_RULES = ("field.ko_step_pct", "field.ko_levels")
check, check_pair, check_iis = STEPDOWN_CHECKS.check, STEPDOWN_CHECKS.check_pair, STEPDOWN_CHECKS.check_iis


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
        StepdownSpec(product_code="328199990004", ko_obs="P", tenor=12, ko=Decimal("102.00"), step=Decimal("2.00")),
        StepdownSpec(product_code="328199990005", first_callable=2, ko=Decimal("98.00"), ki="none"),
        StepdownSpec(
            product_code="328199990006",
            market="JT",
            currency_zh="日圓",
            tenor=9,
            ko=Decimal("97.00"),
            step=Decimal("4.00"),
        ),
        StepdownSpec(product_code="328199990007", ko_final_day="含", step=Decimal("11.00"), tenor=3, ki="none"),
    ],
    ids=["D", "P-12m-KO102", "D-NC2-KO98-no-KI", "D-JPY-9m-KO97-step4", "D-final-day-step11"],
)
def test_supported_stepdown_pairs_pass_and_fill(tmp_path, spec):
    ts, iis = check_pair(tmp_path, spec)
    assert ts.report.template == NATIXIS_STEPDOWN.template_id
    assert iis.report.template == NATIXIS_STEPDOWN.iis.template_id
    assert ts.report.status == S.PASS, problems(ts.report)
    assert iis.report.status == S.PASS, problems(iis.report)
    assert all(r.rule_id != "template.product_family" for r in ts.report.results)
    for rid in (*STEP_RULES, "field.ko_pct", "field.ko_memory", "field.underlying_prices", "doc.ko_table_dates"):
        statuses = {x.status for x in rule(ts.report, rid)}
        assert S.PASS in statuses and statuses <= {S.PASS, S.NOT_APPLICABLE}, rid  # 沒有標的的格子不適用
    levels = rule(ts.report, "field.ko_levels")[0]
    assert f"共 {spec.tenor - spec.first_callable + 1} 期" in levels.message
    assert {x.status for x in rule(iis.report, "iis.ko_definition")} == {S.PASS}
    # 比價日：第一個可提前到期期到最後一期每一格都填（D 型也是，ADR 0033），之前的格子 `-`，最後一格 = 最終評價日
    assert ts.fills_sheet
    slots = compare_slots(ts.report)
    k, n = spec.first_callable, spec.tenor
    filled = [slots[f"比價日_{p}"] for p in range(k, n + 1)]
    assert "-" not in filled and filled == sorted(filled) and len(set(filled)) == len(filled), slots
    assert filled[-1] == spec.final_date
    assert {slots[f"比價日_{p}"] for p in range(1, k)} <= {"-"}


def test_fcn_daily_still_fills_two_compare_dates(tmp_path):
    """回歸：Natixis FCN D 型（KO 不降）比價日仍只填第一個可提前到期期與最後一期，中間 `-`。"""
    spec = Spec()
    report = CHECKS.check(tmp_path, spec)
    assert report.template == NATIXIS.template_id and report.status == S.PASS, problems(report)
    slots = compare_slots(report)
    first, last = f"比價日_{spec.first_callable}", f"比價日_{spec.tenor}"
    assert slots[last] == spec.final_date and slots[first] not in ("-", None)
    assert {v for c, v in slots.items() if c not in (first, last)} == {"-"}


def test_daily_compare_dates_fill_the_blank_middle_slots(tmp_path):
    """D 型 Stepdown 表上只填開始與最後（FCN 的填法）：開始、最後照常比對，中間空白的格子回填每一期期末日。"""
    full = compare_slots(check(tmp_path / "full"))
    k, n = S0.first_callable, S0.tenor
    ends = {f"比價日_{p}": full[f"比價日_{p}"] for p in (k, n)}
    report = check(tmp_path / "ends", **ends)
    assert report.status == S.PASS, problems(report)
    slots = compare_slots(report)
    assert all(slots[f"比價日_{p}"] == full[f"比價日_{p}"] for p in range(k + 1, n))


def test_compare_dates_on_sheet_are_checked_not_overwritten(tmp_path):
    """D 型 Stepdown 表上已填的中間比價日照常比對：和說明書不同 → 不一致、保留原值。"""
    ok = compare_slots(check(tmp_path / "ok"))["比價日_3"]
    report = check(tmp_path / "bad", **{"比價日_3": "2026/01/02"})
    assert flagged(report, "backfill.compare_dates", S.MISMATCH), problems(report)
    assert ok != "-"


def test_ko_step_differs_from_sheet(tmp_path):
    report = check(tmp_path, **{"KO降幅(%)": 3.0})
    only(report, STEP_RULES)
    assert "第 2 期" in rule(report, "field.ko_levels")[0].message


@pytest.mark.parametrize("blank", [None, "-"], ids=["empty", "dash"])
def test_blank_ko_step_goes_to_review(tmp_path, blank):
    only(check(tmp_path, **{"KO降幅(%)": blank}), STEP_RULES, S.REVIEW_REQUIRED)


@pytest.mark.parametrize("value", [0.0, -2.0, "兩點"], ids=["zero", "negative", "text"])
def test_ko_step_that_is_not_a_positive_number_goes_to_review(tmp_path, value):
    report = check(tmp_path, **{"KO降幅(%)": value})
    only(report, STEP_RULES, S.REVIEW_REQUIRED)
    assert {r.reason_code for r in report.results if r.rule_id in STEP_RULES} == {"order_invalid"}


def test_sheet_without_ko_step_column_goes_to_review(tmp_path):
    report = check(tmp_path, headers=[h for h in STEPDOWN_HEADERS if h != "KO降幅(%)"])
    only(report, STEP_RULES, S.REVIEW_REQUIRED)


def test_first_period_ko_differs_from_sheet(tmp_path):
    """表上 KO(%) 不是第 1 期：KO(%) 與每期 KO% 都不一致，降幅本身沒錯。"""
    report = check(tmp_path, **{"KO(%)": 98.0})
    assert flagged(report, "field.ko_pct", S.MISMATCH) and flagged(report, "field.ko_levels", S.MISMATCH)
    assert not flagged(report, "field.ko_step_pct", S.MISMATCH)


def test_ko_price_is_initial_times_first_period_ko(tmp_path):
    spec = StepdownSpec(product_code="328199990008", step=Decimal("1.00"), ko=Decimal("95.00"))
    ok = check(tmp_path / "ok", spec)
    assert ok.status == S.PASS, problems(ok)
    wrong = float(spec.underlyings[0].initial)  # 用 100% 的價格（不是第 1 期 95%）
    only(check(tmp_path / "bad", spec, **{"UL_1_KO價": wrong}), "field.underlying_prices")


def test_periodic_ko_table_date_differs_from_article_12(tmp_path):
    """P 型：每期 KO 表某一期的自動提前到期觀察日和第 12 項 (7) 同一個 `t` 不同 → `doc.ko_table_dates` 不一致。"""
    spec = StepdownSpec(product_code="328199990023", ko_obs="P")
    end = spec.ends[2]
    edit = Edit(spec.date(end), spec.date(end + dt.timedelta(days=1)), "ts.art14.table")
    report = check(tmp_path, spec, edits=[edit])
    only(report, "doc.ko_table_dates")
    assert "第 3 期的自動提前到期觀察日" in rule(report, "doc.ko_table_dates")[0].message


def test_ko_table_date_differs_from_article_12(tmp_path):
    """第 14 項每期 KO 表某一期的結束日和第 12 項不同 → `doc.ko_table_dates` 不一致（同一個日期也是下一期開始日）。"""
    end = S0.ends[2]
    edit = Edit(S0.date(end), S0.date(end + dt.timedelta(days=1)), "ts.art14.table")
    report = check(tmp_path, edits=[edit])
    only(report, "doc.ko_table_dates")
    message = rule(report, "doc.ko_table_dates")[0].message
    assert "第 3 期" in message and "第 4 期" in message


FIXED_DEF = "「自動提前到期贖回價格」，係指就任一連結標的而言，自動提前到期贖回價格為期初進場價格之100.00%。"


@pytest.mark.parametrize(
    ("spec", "edit", "reason"),
    [
        (S0, Edit("96.00%期初進場價格", "97.00%期初進場價格", "ts.art14.table"), "不是每期固定往下降"),
        (StepdownSpec(product_code="328199990009", step=Decimal("0.00")), None, "不是每期固定往下降"),
        (StepdownSpec(product_code="328199990010", step=Decimal("-1.00")), None, "不是每期固定往下降"),
        (StepdownSpec(product_code="328199990011", memory=False), None, "不是記憶式"),
        (S0, Edit("視同發生。", "視同發生。" + FIXED_DEF, "ts.art14"), "固定 % 的 KO 定義句"),
        (
            StepdownSpec(product_code="328199990021", art9_ko_column=True),
            None,
            "第 9 項連結標的表有自動提前到期贖回價格欄",
        ),
        (S0, Edit("自動提前到期贖回價格", "贖回價格", "ts.art14.table"), "讀不到第 14 項每期 KO 表"),
        (
            StepdownSpec(product_code="328199990012", first_callable=2, ko_rows_from=1),
            None,
            "和第 12 項應有的期別",
        ),
        (StepdownSpec(product_code="328199990013", tenor=2, first_callable=2), None, "只有 1 期"),
        (
            StepdownSpec(product_code="328199990022", ko_obs="P", ko_table_kind="D"),
            None,
            "是 D 型表頭，第 12 項卻是 P 型",
        ),
    ],
    ids=[
        "uneven",
        "flat",
        "rising",
        "no-memory",
        "fixed-ko-definition",
        "ko-in-article-9",
        "no-ko-table",
        "rows-before-non-call",
        "single-period",
        "table-kind-differs",
    ],
)
def test_stepdown_variants_outside_the_template_are_unsupported(tmp_path, spec, edit, reason):
    report = check(tmp_path, spec, edits=[edit] if edit else [])
    assert reason in unsupported(report)


def test_fcn_document_with_the_stepdown_mark_stays_in_the_stepdown_template(tmp_path):
    """Natixis FCN 說明書加上 Stepdown 標記：停在 Stepdown 範本、判不支援的範本版本，不退回 FCN 範本（範本規格 §4）。"""
    edit = Edit("視同發生。", "視同發生。自動提前到期贖回價格為下表所列之期初進場價格。", "ts.art14")
    report = CHECKS.check(tmp_path, Spec(product_code="328199990014"), edits=[edit])
    assert "固定 % 的 KO 定義句" in unsupported(report)


def test_product_fcn_with_stepdown_document_goes_to_review(tmp_path):
    report = check(tmp_path, Product="FCN")
    assert report.template == NATIXIS_STEPDOWN.template_id
    assert flagged(report, "template.product_family", S.REVIEW_REQUIRED), problems(report)


def test_product_stepdown_with_fcn_document_goes_to_review(tmp_path):
    report = CHECKS.check(tmp_path, Spec(), headers=STEPDOWN_HEADERS, **{"Product": STEPDOWN_PRODUCT, "KO降幅(%)": 2.0})
    assert report.template == NATIXIS.template_id
    assert flagged(report, "template.product_family", S.REVIEW_REQUIRED), problems(report)


def test_fcn_dacn_and_stepdown_templates_are_never_confused(tmp_path):
    fcn, dacn = Spec(product_code="328199990015"), DacnSpec(product_code="328199990016")
    specs = (fcn, dacn, S0)
    pdfs = [build_pdf(tmp_path / f"{s.product_code}_TS.pdf", s) for s in specs]
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [row(s) for s in specs], STEPDOWN_HEADERS)
    outcome = check_all(sheet, [*pdfs, *map(iis_path, pdfs)])
    detected = {i.term_sheet.name: rule(i.report, "template.detect")[0].actual for i in outcome.items}
    expected = {}
    for pdf, template in zip(pdfs, (NATIXIS, NATIXIS_DACN, NATIXIS_STEPDOWN), strict=True):
        expected[pdf.name] = template.template_id
        expected[iis_path(pdf).name] = template.iis.template_id
    assert detected == expected
    assert all(i.report.status == S.PASS for i in outcome.items), [problems(i.report) for i in outcome.items]


def test_fcn_and_dacn_documents_are_not_stepdown(tmp_path):
    """Natixis FCN（每日、每期）與 DACN 的說明書、投資人須知都不被 Stepdown 範本認走，也不停在 Stepdown（不是 claimed）。"""
    for spec in (
        Spec(product_code="328199990017"),
        Spec(product_code="328199990018", ko_obs="P", memory=False),
        DacnSpec(product_code="328199990019"),
    ):
        pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
        ts = NATIXIS_STEPDOWN.detect(lines_of(pdf))
        iis = NATIXIS_STEPDOWN.iis.detect(lines_of(iis_path(pdf)))
        assert not ts.matched and not ts.claimed and not ts.unsupported
        assert not iis.matched and not iis.claimed and not iis.unsupported


def test_term_sheet_and_investor_sheet_are_not_taken_for_each_other(tmp_path):
    pdf = build_pdf(tmp_path / f"{S0.product_code}_TS.pdf", S0)
    ts_lines, iis_lines = lines_of(pdf), lines_of(iis_path(pdf))
    assert NATIXIS_STEPDOWN.detect(ts_lines).matched
    assert NATIXIS_STEPDOWN.iis.detect(iis_lines).matched
    assert not NATIXIS_STEPDOWN.iis.detect(ts_lines).matched
    assert not NATIXIS_STEPDOWN.detect(iis_lines).matched


def test_investor_sheet_with_a_fixed_ko_percentage_is_a_mismatch(tmp_path):
    """投資人須知 KO 價格定義寫固定 %（FCN 的寫法）：和說明書每期 KO 表矛盾 → 不一致（核對規則 §3.6）。"""
    edit = Edit("中文產品說明書第一章第14 項所列之期初進場價格", "期初進場價格之100.00%", "iis")
    only(check_iis(tmp_path, edits=[edit]).report, "iis.ko_definition")


def test_investor_sheet_with_another_english_name_stops_at_stepdown(tmp_path):
    """投資人須知有 Stepdown 標記、英文名稱卻不是 FCN 的名稱：停在 Stepdown 範本、判範本不符（不是範本不支援，範本規格 §2）。"""
    edit = Edit("Autocallable Fixed Coupon Note", "Autocallable Floating Coupon Note", "iis")
    pdf = build_pdf(tmp_path / f"{S0.product_code}_TS.pdf", S0, edits=[edit])
    result = NATIXIS_STEPDOWN.iis.detect(lines_of(iis_path(pdf)))
    assert not result.matched and result.claimed and not result.unsupported
    assert any("英文名稱" in f for f in result.failed)


def test_investor_sheet_with_two_ko_definitions_goes_to_review(tmp_path):
    """投資人須知兩句 KO 價格定義（「下表所列」與參照說明書）：轉人工覆核（待使用者決定）。"""
    spec = StepdownSpec(product_code="328199990020", iis_table_def=True)
    report = check_iis(tmp_path, spec).report
    only(report, "iis.ko_definition", S.REVIEW_REQUIRED)
    assert "投資人須知沒有這張表" in rule(report, "iis.ko_definition")[0].message


def test_compatibility_ideographs_are_read_as_standard_characters(tmp_path):
    """說明書用 CJK 相容字（早期版面）：轉成標準字後照常辨識與讀取。合成字型畫不出相容字，改在抽出的文字行上換字。"""
    pdf = build_pdf(tmp_path / f"{S0.product_code}_TS.pdf", S0, iis=False)
    lines = [replace(ln, text=ln.text.replace("列", "列").replace("料", "料")) for ln in lines_of(pdf)]
    assert any("列" in ln.text for ln in lines)
    assert NATIXIS_STEPDOWN.detect(lines).matched
    ts = NATIXIS_STEPDOWN.read(lines)
    assert (ts.f("ko_pct").value, ts.f("ko_step_pct").value) == (S0.ko, S0.step)


@pytest.mark.parametrize("checks", [STEPDOWN_CHECKS, CHECKS], ids=["stepdown", "fcn"])
def test_best_case_scenario_without_a_bullet_still_passes(tmp_path, checks):
    """情境一「在較佳情況下…」句前沒有列項符號（Stepdown 早期版面）也照常讀；Natixis FCN 共用同一讀法。"""
    report = checks.check(tmp_path, edits=[Edit("• 在較佳情況下", "在較佳情況下", "ts.art16")])
    assert report.status == S.PASS, problems(report)
