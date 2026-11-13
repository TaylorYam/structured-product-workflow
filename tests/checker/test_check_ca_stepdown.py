"""CA FCN_Stepdown 黑箱回歸（workflow #546）：合成說明書與投資人須知（ca_synth.StepdownSpec）經批量入口核對。

反例依 docs/checker/rules/ca-stepdown-check-rules.md §7、§8。投資人須知用 CA FCN 的 `ca-zh-iis`（商品種類由說明書把關）。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import fitz
import pytest

import barc_synth
from ca_synth import CHECKS, STEPDOWN_CHECKS, BenSpec, SbSpec, Spec, StepdownSpec, build_pdf, row
from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import CA, CA_BEN, CA_STEPDOWN, CA_SUPER_BULL
from fcn_checker.schema import CheckStatus as S
from harness import check_all, flagged, iis_path
from harness import field_problems as problems
from harness import results as rule
from pdf_writer import Edit
from reference_synth import STEPDOWN_HEADERS, STEPDOWN_PRODUCT, build_reference_sheet

S0 = STEPDOWN_CHECKS.default()  # D 型、記憶式（新寫法）、4 個月、Non-Call 1、起始 KO 100%、每期降 2 個百分點、無 KI
STEP_RULES = ("field.ko_step_pct", "field.ko_levels")
check, check_pair, check_iis = STEPDOWN_CHECKS.check, STEPDOWN_CHECKS.check_pair, STEPDOWN_CHECKS.check_iis
NEW_SAME = "無須於同一預定交易日或同一自動提前出場觀察期"  # 記憶式第二句的 Stepdown 寫法
FCN_SAME = "無須於同一自動提前出場觀察日"


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
        StepdownSpec(
            product_code="086199990005", wording="fcn", ki="AM", ko=Decimal("103.00"), step=Decimal("4.00"), tenor=6
        ),
        StepdownSpec(product_code="086199990006", tenor=3, step=Decimal("10.00"), iis_period_def=True),
        StepdownSpec(product_code="086199990007", ki="D", count=2, step=Decimal("5.00")),
    ],
    ids=["D-new-wording", "fcn-wording-KI-AM-KO103-step4-6m", "iis-period-def-step10-3m", "KI-D-two-underlyings-step5"],
)
def test_supported_stepdown_pairs_pass_and_fill(tmp_path, spec):
    ts, iis = check_pair(tmp_path, spec)
    assert ts.report.template == CA_STEPDOWN.template_id
    assert iis.report.template == CA.iis.template_id  # 投資人須知用 CA FCN 的範本
    assert ts.report.status == S.PASS, problems(ts.report)
    assert iis.report.status == S.PASS, problems(iis.report)
    for report in (ts.report, iis.report):
        assert all(r.rule_id != "template.product_family" for r in report.results)
    for rid in (*STEP_RULES, "field.ko_pct", "field.ko_memory", "field.underlying_prices", "doc.ko_table_dates"):
        statuses = {x.status for x in rule(ts.report, rid)}
        assert S.PASS in statuses and statuses <= {S.PASS, S.NOT_APPLICABLE}, rid  # 沒有標的的格子不適用
    assert {x.status for x in rule(iis.report, "field.ko_memory")} == {S.PASS}
    levels = rule(ts.report, "field.ko_levels")[0]
    assert f"共 {spec.tenor} 期" in levels.message
    # 比價日：第 1 期到最後一期每一格都填（D 型也是，ADR 0033），最後一格 = 期末訂價日
    assert ts.fills_sheet
    slots = compare_slots(ts.report)
    filled = [slots[f"比價日_{p}"] for p in range(1, spec.tenor + 1)]
    assert filled == spec.ends and filled[-1] == spec.final_date, slots


def test_fcn_daily_still_fills_two_compare_dates(tmp_path):
    """回歸：CA FCN D 型（KO 不降）比價日仍只填第一個可提前出場期與最後一期，中間 `-`。"""
    spec = Spec()
    report = CHECKS.check(tmp_path, spec)
    assert report.template == CA.template_id and report.status == S.PASS, problems(report)
    slots = compare_slots(report)
    first, last = f"比價日_{spec.first_callable}", f"比價日_{spec.tenor}"
    assert slots[last] == spec.final_date and slots[first] not in ("-", None)
    assert {v for c, v in slots.items() if c not in (first, last)} == {"-"}


def test_daily_compare_dates_fill_the_blank_middle_slots(tmp_path):
    """D 型 Stepdown 表上只填開始與最後（FCN 的填法）：開始、最後照常比對，中間空白的格子回填每一期期末日。"""
    n = S0.tenor
    ends = {"比價日_1": S0.ends[0], f"比價日_{n}": S0.ends[-1]}
    report = check(tmp_path, **ends)
    assert report.status == S.PASS, problems(report)
    slots = compare_slots(report)
    assert [slots[f"比價日_{p}"] for p in range(2, n)] == S0.ends[1:-1]


def test_compare_dates_on_sheet_are_checked_not_overwritten(tmp_path):
    """D 型 Stepdown 表上已填的中間比價日照常比對：和說明書不同 → 不一致、保留原值。"""
    report = check(tmp_path, **{"比價日_2": "2026/01/02"})
    assert flagged(report, "backfill.compare_dates", S.MISMATCH), problems(report)


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
    spec = StepdownSpec(product_code="086199990008", step=Decimal("1.00"), ko=Decimal("103.00"))
    ok = check(tmp_path / "ok", spec)
    assert ok.status == S.PASS, problems(ok)
    wrong = float(spec.underlyings[0].initial)  # 用 100% 的價格（不是第 1 期 103%）
    only(check(tmp_path / "bad", spec, **{"UL_1_KO價": wrong}), "field.underlying_prices")


def test_ko_table_date_differs_from_coupon_table(tmp_path):
    """每期 KO 表某一期的結束日和配息表同一期的配息期結束日不同 → `doc.ko_table_dates` 不一致。"""
    end = S0.ends[2]
    edit = Edit(S0.date(end), S0.date(end + dt.timedelta(days=1)), "ts.art9.kotable")
    report = check(tmp_path, edits=[edit])
    only(report, "doc.ko_table_dates")
    assert "第 3 期" in rule(report, "doc.ko_table_dates")[0].message


@pytest.mark.parametrize(
    ("spec", "edit", "reason"),
    [
        (S0, Edit("期初訂價之96.00%", "期初訂價之97.00%", "ts.art9.kotable"), "不是每期固定往下降"),
        (StepdownSpec(product_code="086199990009", step=Decimal("0.00")), None, "不是每期固定往下降"),
        (StepdownSpec(product_code="086199990010", step=Decimal("-1.00")), None, "不是每期固定往下降"),
        (StepdownSpec(product_code="086199990011", memory=False), None, "是非記憶式"),
        (StepdownSpec(product_code="086199990013", layout="old", ko_obs="P", wording="old"), None, "舊範本的表頭"),
        (StepdownSpec(product_code="086199990014", period_def=False), None, "沒有自動提前出場觀察期的定義句"),
        (StepdownSpec(product_code="086199990015", art9_ko_column=True), None, "價格表有自動提前出場價欄"),
        (StepdownSpec(product_code="086199990016", first_callable=2), None, "Non-Call ≥ 2"),
        (StepdownSpec(product_code="086199990017", ko_rows=3), None, "有 3 期，配息表有 4 期"),
        (StepdownSpec(product_code="086199990035", ko_numbers=(1, 3, 4, 5)), None, "期別不是從 1 起連續"),
        (S0, Edit("自動提前出場觀察期結束日", "結束日", "ts.art9.kotable"), "表頭讀不到"),
        (S0, Edit("期初訂價之98.00%", "期初訂價之98%", "ts.art9.kotable"), "有讀不到的列"),
    ],
    ids=[
        "uneven",
        "flat",
        "rising",
        "no-memory",
        "old-layout",
        "no-period-definition",
        "ko-column-in-price-table",
        "non-call-2",
        "fewer-rows-than-coupon-table",
        "period-skips-a-number",
        "no-table-head",
        "unreadable-cell",
    ],
)
def test_stepdown_variants_outside_the_template_are_unsupported(tmp_path, spec, edit, reason):
    report = check(tmp_path, spec, edits=[edit] if edit else [])
    assert reason in unsupported(report)


@pytest.mark.parametrize(
    ("spec", "reasons"),
    [
        (StepdownSpec(product_code="086199990036", first_callable=2, memory=False), ("Non-Call ≥ 2", "是非記憶式")),
        (
            StepdownSpec(product_code="086199990037", art9_ko_column=True, ko_rows=3),
            ("價格表有自動提前出場價欄", "有 3 期"),
        ),
    ],
    ids=["non-call-2-and-no-memory", "ko-column-and-fewer-rows"],
)
def test_unsupported_message_lists_every_failed_condition(tmp_path, spec, reasons):
    """範本不支援的錯訊列出所有不成立的條件（核對規則 §3.1），不是只列第一個。"""
    message = unsupported(check(tmp_path, spec))
    assert all(r in message for r in reasons), message


def test_term_sheet_with_only_one_memory_sentence_goes_to_review(tmp_path):
    """說明書記憶式只認得第一句（第二句是舊範本的寫法）→ 照 CA FCN 由 `field.ko_memory` 轉人工覆核，不判範本不支援
    （核對規則 §3.5；確定是非記憶式才判不支援）。"""
    report = check(tmp_path, StepdownSpec(product_code="086199990012", wording="old"))
    assert rule(report, "template.detect")[0].status == S.PASS, problems(report)
    assert flagged(report, "field.ko_memory", S.REVIEW_REQUIRED), problems(report)


def test_fcn_document_with_the_stepdown_mark_stays_in_the_stepdown_template(tmp_path):
    """CA FCN 說明書第 9 項 (1)(b) 多一個「期初訂價之N%」：停在 Stepdown 範本、判不支援的範本版本，不退回 FCN 範本。"""
    edit = Edit("則發生自動提前出場事件。", "則發生自動提前出場事件。自動提前出場價為期初訂價之100.00%。", "ts.art9")
    report = CHECKS.check(tmp_path, Spec(product_code="086199990018"), edits=[edit])
    assert "表頭讀不到" in unsupported(report)


def test_product_fcn_with_stepdown_document_goes_to_review(tmp_path):
    ts, iis = check_pair(tmp_path, Product="FCN")
    assert ts.report.template == CA_STEPDOWN.template_id
    assert flagged(ts.report, "template.product_family", S.REVIEW_REQUIRED), problems(ts.report)
    assert all(r.rule_id != "template.product_family" for r in iis.report.results)  # 投資人須知涵蓋 FCN


def test_product_stepdown_with_fcn_document_goes_to_review(tmp_path):
    """Product 是 FCN_Stepdown、說明書是 CA FCN：說明書轉人工；投資人須知（`ca-zh-iis` 涵蓋 FCN_Stepdown）不另外擋。"""
    overrides = {"Product": STEPDOWN_PRODUCT, "KO降幅(%)": 2.0}
    ts, iis = CHECKS.check_pair(tmp_path, Spec(), headers=STEPDOWN_HEADERS, **overrides)
    assert ts.report.template == CA.template_id
    assert flagged(ts.report, "template.product_family", S.REVIEW_REQUIRED), problems(ts.report)
    assert all(r.rule_id != "template.product_family" for r in iis.report.results)


@pytest.mark.parametrize(
    ("spec", "product", "family_review"),
    [
        (Spec(product_code="086199990019"), "FCN", False),
        (Spec(product_code="086199990020", first_callable=0), "FCN_Express", False),
        (Spec(product_code="086199990021"), STEPDOWN_PRODUCT, False),
        (Spec(product_code="086199990022"), "BEN", True),
        (Spec(product_code="086199990023"), "Super Bull", True),
    ],
    ids=["fcn", "fcn-express", "stepdown", "ben", "super-bull"],
)
def test_ca_investor_sheet_product_family(tmp_path, spec, product, family_review):
    """`ca-zh-iis` 宣告涵蓋 FCN、FCN_Express、FCN_Stepdown（`IisTemplate.products`）：其他商品種類仍轉人工。"""
    item = CHECKS.check_iis(tmp_path, spec, headers=STEPDOWN_HEADERS, Product=product, **{"KO降幅(%)": 2.0})
    assert item.report.template == CA.iis.template_id
    assert flagged(item.report, "template.product_family", S.REVIEW_REQUIRED) == family_review, problems(item.report)


def test_investor_sheet_of_an_issuer_without_declared_products_still_goes_to_review(tmp_path):
    """沒宣告商品種類的投資人須知範本照說明書範本（例：BARC FCN 投資人須知配 Product FCN_Stepdown → 轉人工）。"""
    spec = barc_synth.Spec()
    overrides = {"Product": STEPDOWN_PRODUCT, "KO降幅(%)": 2.0}
    item = barc_synth.CHECKS.check_iis(tmp_path, spec, headers=STEPDOWN_HEADERS, **overrides)
    assert flagged(item.report, "template.product_family", S.REVIEW_REQUIRED), problems(item.report)


def test_ca_templates_are_never_confused(tmp_path):
    fcn, sb, ben = (
        Spec(product_code="086199990024"),
        SbSpec(product_code="086199990025"),
        BenSpec(product_code="086199990026"),
    )
    specs = (fcn, sb, ben, S0)
    pdfs = [build_pdf(tmp_path / f"{s.product_code}_TS.pdf", s) for s in specs]
    headers = [*STEPDOWN_HEADERS, "參與率(%)"]  # BEN 多參與率欄
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [row(s) for s in specs], headers)
    outcome = check_all(sheet, [*pdfs, *map(iis_path, pdfs)])
    detected = {i.term_sheet.name: rule(i.report, "template.detect")[0].actual for i in outcome.items}
    expected = {}
    for pdf, template, iis in zip(
        pdfs, (CA, CA_SUPER_BULL, CA_BEN, CA_STEPDOWN), (CA.iis, CA_SUPER_BULL.iis, CA_BEN.iis, CA.iis), strict=True
    ):
        expected[pdf.name] = template.template_id
        expected[iis_path(pdf).name] = iis.template_id
    assert detected == expected
    assert all(i.report.status == S.PASS for i in outcome.items), [problems(i.report) for i in outcome.items]


@pytest.mark.parametrize(
    "spec",
    [
        Spec(product_code="086199990027"),
        Spec(product_code="086199990028", first_callable=0),
        Spec(product_code="086199990029", ko_obs="P", memory=False),
        Spec(product_code="086199990030", ko_obs="P", ki="AM"),
        SbSpec(product_code="086199990031"),
        BenSpec(product_code="086199990032"),
    ],
    ids=["fcn-daily", "fcn-express", "fcn-periodic-no-memory", "fcn-periodic-ki", "super-bull", "ben"],
)
def test_other_ca_documents_are_not_stepdown(tmp_path, spec):
    """CA FCN（每日、FCN_Express、每期）、Super Bull、BEN 說明書不被 Stepdown 範本認走，也不停在 Stepdown。"""
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec)
    result = CA_STEPDOWN.detect(lines_of(pdf))
    assert not result.matched and not result.claimed and not result.unsupported


def test_term_sheet_is_not_taken_for_an_investor_sheet(tmp_path):
    pdf = build_pdf(tmp_path / f"{S0.product_code}_TS.pdf", S0)
    ts_lines, iis_lines = lines_of(pdf), lines_of(iis_path(pdf))
    assert CA_STEPDOWN.detect(ts_lines).matched
    assert not CA_STEPDOWN.detect(iis_lines).matched
    assert CA.iis.detect(iis_lines).matched and not CA.iis.detect(ts_lines).matched


def test_investor_sheet_with_only_one_memory_sentence_goes_to_review(tmp_path):
    """投資人須知記憶式只有第一句（第二句拿掉）→ 轉人工覆核（照 CA FCN）。"""
    edit = Edit(f"為避免疑義，所有連結標的{NEW_SAME}符合上開條件。", "", "iis")
    item = check_iis(tmp_path, edits=[edit])
    assert flagged(item.report, "field.ko_memory", S.REVIEW_REQUIRED), problems(item.report)


def test_fcn_investor_sheet_also_accepts_the_stepdown_memory_wording(tmp_path):
    """投資人須知只有一個範本：CA FCN 投資人須知寫 Stepdown 的第二句也讀成記憶式（核對規則 §3.6）。"""
    item = CHECKS.check_iis(tmp_path, Spec(product_code="086199990033"), edits=[Edit(FCN_SAME, NEW_SAME, "iis")])
    assert item.report.status == S.PASS, problems(item.report)


def test_fcn_term_sheet_does_not_accept_the_stepdown_memory_wording(tmp_path):
    """說明書只有 Stepdown 範本收新寫法：CA FCN 說明書寫 Stepdown 的第二句 → 記憶式只認得一句，照舊轉人工覆核。"""
    report = CHECKS.check(tmp_path, Spec(product_code="086199990034"), edits=[Edit(FCN_SAME, NEW_SAME, "ts.art9")])
    assert report.template == CA.template_id
    assert flagged(report, "field.ko_memory", S.REVIEW_REQUIRED), problems(report)
