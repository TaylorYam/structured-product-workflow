"""HSBC BEN 黑箱回歸（Issue #375）：合成說明書與投資人須知（hsbc_synth.BenSpec，Maxi 與 Digital）經批量入口核對。"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import fitz
import pytest

from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import HSBC, HSBC_BEN, HSBC_SUPER_BULL
from fcn_checker.schema import CheckStatus as S
from harness import check_all, flagged, iis_path
from harness import field_problems as problems
from harness import results as rule
from hsbc_synth import BEN_PRODUCT, SB_PRODUCT, BenSpec, SbSpec, Spec, build_pdf
from pdf_writer import Edit
from reference_synth import REFERENCE_HEADERS, as_headers, build_reference_sheet, reference_row

M0 = BenSpec()  # Maxi：2 檔、6 個月、K% 90、紅利 12%
D0 = BenSpec(kind="digital", count=1, strike=Decimal("100.0000"), ko=Decimal("100.0000"))  # Digital：1 檔、K% 100
HEADERS = [*REFERENCE_HEADERS, "參與率(%)"]


def row(spec: Spec, **overrides):
    """與 `spec` 一致的參考條件表列；BEN 的 Product 填 `BEN`、參與率 Maxi 100、Digital 0。`overrides` 以 Excel 欄名覆寫。"""
    out = reference_row(spec)
    if isinstance(spec, BenSpec):
        out["Product"] = BEN_PRODUCT
        out["參與率(%)"] = 0 if spec.digital else 100
    elif isinstance(spec, SbSpec):
        out["Product"] = SB_PRODUCT
    out.update(overrides)
    return out


def check_pair(tmp_path: Path, spec: Spec = M0, *, edits=(), sheet_row=None, headers=HEADERS, **overrides):
    """合成說明書＋投資人須知＋參考條件表一列，經批量入口核對，回傳（說明書, 投資人須知）。"""
    tmp_path.mkdir(parents=True, exist_ok=True)
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec, edits=edits)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [sheet_row or row(spec, **overrides)], headers)
    outcome = check_all(sheet, [pdf, iis_path(pdf)])
    ts = next(i for i in outcome.items if i.term_sheet == pdf)
    return ts, next(i for i in outcome.items if i.term_sheet != pdf)


def check(tmp_path: Path, spec: Spec = M0, **kw):
    return check_pair(tmp_path, spec, **kw)[0].report


def lines_of(path: Path):
    with fitz.open(path) as doc:
        return extract_lines(doc)


def compare_dates(report) -> dict:
    return {d.column: d.expected for d in report.backfill if d.column.startswith("比價日_") and d.expected != "-"}


@pytest.mark.parametrize(
    "spec",
    [
        M0,
        BenSpec(count=3, strike=Decimal("100.0000"), ko=Decimal("100.0000"), tenor=3),
        BenSpec(inline_articles_from=1),
        D0,
        BenSpec(kind="digital", count=1, tenor=4),
    ],
    ids=["maxi-2UL", "maxi-3UL-K100", "maxi-inline", "digital-K100", "digital-K90"],
)
def test_supported_ben_pairs_pass_and_fill_the_final_compare_date(tmp_path, spec):
    ts, iis = check_pair(tmp_path, spec)
    assert ts.report.template == HSBC_BEN.template_id
    assert iis.report.template == HSBC_BEN.iis.template_id
    assert ts.report.status == S.PASS, problems(ts.report)
    assert iis.report.status == S.PASS, problems(iis.report)
    assert ts.fills_sheet
    # 參與率由到期公式推得；紅利不年化，和 Coupon p.a. (%) 相等；Non-Call = 天期
    participation = rule(ts.report, "field.participation_pct")[0]
    assert (participation.status, participation.actual) == (S.PASS, 0 if spec.digital else 100)
    assert rule(ts.report, "field.coupon_pa_pct")[0].status == S.PASS
    assert rule(ts.report, "field.first_callable_period")[0].actual == spec.tenor
    for rid in (
        "doc.scenario_calculations",
        "doc.bonus",
        "doc.no_autocall",
        "doc.ki_consistency",
        "doc.redemption_formula",
        "standard.product_name",
    ):
        assert rule(ts.report, rid) and all(x.status == S.PASS for x in rule(ts.report, rid)), rid
    assert rule(iis.report, "iis.no_coupon")[0].status == S.PASS
    # 比價日只有 `比價日_{天期}` = 最後評價日
    assert compare_dates(ts.report) == {f"比價日_{spec.tenor}": spec.final_date}


def test_ben_fcn_and_super_bull_templates_are_never_confused(tmp_path):
    fcn = Spec(product_code="325199990001")
    sb = SbSpec(product_code="325199990002")
    maxi = BenSpec(product_code="325199990003")
    digital = BenSpec(product_code="325199990004", kind="digital", count=1)
    specs = (fcn, sb, maxi, digital)
    pdfs = [build_pdf(tmp_path / f"{s.product_code}_TS.pdf", s) for s in specs]
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [row(s) for s in specs], HEADERS)
    outcome = check_all(sheet, [*pdfs, *map(iis_path, pdfs)])
    detected = {i.term_sheet.name: rule(i.report, "template.detect")[0].actual for i in outcome.items}
    issuers = (HSBC, HSBC_SUPER_BULL, HSBC_BEN, HSBC_BEN)
    assert detected == {
        **{p.name: i.template_id for p, i in zip(pdfs, issuers, strict=True)},
        **{iis_path(p).name: i.iis.template_id for p, i in zip(pdfs, issuers, strict=True)},
    }


def test_term_sheet_and_investor_sheet_are_not_taken_for_each_other(tmp_path):
    pdf = build_pdf(tmp_path / f"{M0.product_code}_TS.pdf", M0)
    ts_lines, iis_lines = (lines_of(x) for x in (pdf, iis_path(pdf)))
    assert not HSBC_BEN.iis.detect(ts_lines).matched
    assert not HSBC_BEN.detect(iis_lines).matched
    for other in (HSBC, HSBC_SUPER_BULL):
        assert not other.detect(ts_lines).matched
        assert not other.iis.detect(iis_lines).matched


def test_super_bull_with_a_maxi_bonus_english_name_is_not_ben(tmp_path):
    """Super Bull 英文名稱誤寫成 `Maxi Bonus Enhanced Notes`（範本規格 §7）：中文名稱與第 11 條仍是 Super Bull，不是 BEN。"""
    sb = SbSpec()
    edit = Edit("SuperBull Notes", "Maxi Bonus Enhanced Notes", "ts.cover")
    pdf = build_pdf(tmp_path / f"{sb.product_code}_TS.pdf", sb, edits=[edit], iis=False)
    assert not HSBC_BEN.detect(lines_of(pdf)).matched


def test_chinese_and_english_kinds_must_agree(tmp_path):
    """Maxi 中文名稱配 Digital 英文名稱：不辨識成 BEN（轉人工）。"""
    edit = Edit("Maxi Bonus Enhanced Notes", "Reverse Convertible with Digital Coupon Notes", "ts.cover")
    pdf = build_pdf(tmp_path / f"{M0.product_code}_TS.pdf", M0, edits=[edit], iis=False)
    result = HSBC_BEN.detect(lines_of(pdf))
    assert not result.matched and any("型態不同" in x for x in result.failed)


@pytest.mark.parametrize(
    ("spec", "product"),
    [(M0, "FCN"), (D0, SB_PRODUCT), (M0, None), (Spec(), BEN_PRODUCT)],
    ids=["ben-booked-as-fcn", "ben-booked-as-sb", "ben-blank-product", "fcn-booked-as-ben"],
)
def test_product_must_match_the_template_family(tmp_path, spec, product):
    ts, iis = check_pair(tmp_path, spec, Product=product)
    for item in (ts, iis):
        assert flagged(item.report, "template.product_family", S.REVIEW_REQUIRED)
        assert item.unsupported_variant and item.release_problem


@pytest.mark.parametrize(
    "column",
    [{"ko_memory": "Y"}, {"ki_type": "AM"}, {"first_callable_period": 2}],
    ids=["memory", "ki", "non-call"],
)
def test_ben_terms_on_the_sheet_must_be_the_supported_variant(tmp_path, column):
    ts, iis = check_pair(tmp_path, M0, **as_headers(column))
    for item in (ts, iis):
        assert flagged(item.report, "batch.variant", S.REVIEW_REQUIRED)
        assert item.unsupported_variant and item.release_problem


MAXI_FORMULA = "最大值 [紅利配息率，"


@pytest.mark.parametrize(
    ("spec", "edits"),
    [
        (M0, [Edit("為商品到期贖回金額", "為自動提前贖回金額或商品到期贖回金額", "ts.art11")]),
        (M0, [Edit(MAXI_FORMULA, "最大值 [紅利配息率 × 50%，", "ts.art11")]),  # 到期公式改了：參與率推不出
        (M0, [Edit("本商品不適用", "請見第 11 項", "ts.art16")]),
        (M0, [Edit("參與率：本商品不適用", "參與率：100%", "ts.art17")]),
        (D0, [Edit("並無因配息致投資標的價值而相對降低之情事", "每月支付配息", "ts.art16")]),
        (D0, [Edit("則無紅利配息。", "則支付最低配息。", "ts.art11")]),
    ],
    ids=["autocall-opening", "formula", "maxi-art16", "participation", "digital-art16", "digital-coupon"],
)
def test_other_term_sheet_wording_is_not_supported(tmp_path, spec, edits):
    ts, _ = check_pair(tmp_path, spec, edits=edits)
    detect = rule(ts.report, "template.detect")[0]
    assert (detect.status, detect.reason_code) == (S.REVIEW_REQUIRED, "template_unsupported")
    assert "寫法和現行版不同" in detect.message and "不能人工放行" in detect.message
    assert ts.status_label == "不支援的範本版本" and ts.release_problem


def test_other_investor_sheet_wording_is_not_supported(tmp_path):
    """第二寫成 FCN 的配息句（核對規則 §7 反例）：認得出是 HSBC BEN 投資人須知、但不是現行版。"""
    edit = Edit("收益分配事項：請見產品說明書第一章第11 項之說明。", "收益分配事項：本商品支付固定配息。", "iis.p2")
    _, iis = check_pair(tmp_path, M0, edits=[edit])
    detect = rule(iis.report, "template.detect")[0]
    assert (detect.status, detect.reason_code) == (S.REVIEW_REQUIRED, "template_unsupported")
    assert iis.status_label == "不支援的範本版本" and iis.release_problem


def test_decision_date_in_article_15_means_ko_cannot_be_ruled_out(tmp_path):
    """第 15 條有自動提前贖回決定日（核對規則 §3.5）：無法判定沒有 KO → 人工覆核。"""
    r = check(tmp_path, M0, edits=[Edit("日期說明", "自動提前贖回決定日：不適用", "ts.art15")])
    assert flagged(r, "doc.no_autocall", S.REVIEW_REQUIRED), problems(r)


def test_ki_column_means_no_ki_cannot_be_ruled_out(tmp_path):
    """價格表有觸及不保本價格欄、全文有「觸及」：無法判定無 KI → 人工覆核。"""
    r = check(tmp_path, M0, edits=[Edit("期初股價", "期初股價 觸及不保本價格", "ts.art12.table")])
    assert flagged(r, "doc.ki_consistency", S.REVIEW_REQUIRED), problems(r)


@pytest.mark.parametrize(
    ("spec", "headers", "value", "status"),
    [
        (M0, HEADERS, 0, S.MISMATCH),
        (D0, HEADERS, 100, S.MISMATCH),
        (M0, HEADERS, None, S.REVIEW_REQUIRED),
        (M0, REFERENCE_HEADERS, 100, S.REVIEW_REQUIRED),
    ],
    ids=["maxi-0", "digital-100", "blank", "no-column"],
)
def test_participation_on_the_sheet_must_match_the_formula(tmp_path, spec, headers, value, status):
    r = check(tmp_path, spec, headers=headers, **{"參與率(%)": value})
    assert flagged(r, "field.participation_pct", status), problems(r)


def test_participation_is_not_checked_for_other_products(tmp_path):
    ts, _ = check_pair(tmp_path, Spec(), **{"參與率(%)": 55})
    assert all(x.rule_id != "field.participation_pct" for x in ts.report.results)


@pytest.mark.parametrize(
    "spec",
    [
        BenSpec(count=1),
        BenSpec(count=4),
        BenSpec(kind="digital", count=2),
        BenSpec(currency_zh="日圓"),
    ],
    ids=["maxi-single", "maxi-4UL", "digital-2UL", "jpy"],
)
def test_shapes_without_samples_need_review(tmp_path, spec):
    r = check(tmp_path, spec)
    result = rule(r, "doc.template_variant")[0]
    assert (result.status, result.reason_code) == (S.REVIEW_REQUIRED, "template_variant_unverified")
    assert "待樣本驗證" in result.message


def test_residue_note_after_the_digital_table_is_a_mismatch(tmp_path):
    """Digital 標的表後的方括號殘留註記（待使用者確認，暫定判不一致、人工放行）；價格表照常讀到。"""
    r = check(tmp_path, BenSpec(kind="digital", count=1, residue=True))
    assert flagged(r, "doc.template_residue", S.MISMATCH), problems(r)
    assert not flagged(r, "field.underlyings")


def test_word_layout_without_final_edition_is_a_mismatch(tmp_path):
    """Word 排版（條號與標籤同一行、標題沒有 (最終版)）：條款照常讀到；少 (最終版) 兩份都判不一致（待使用者確認）。"""
    ts, iis = check_pair(tmp_path, BenSpec(word=True, inline_articles_from=1))
    assert flagged(ts.report, "doc.final_edition", S.MISMATCH), problems(ts.report)
    assert flagged(iis.report, "iis.final_edition", S.MISMATCH), problems(iis.report)
    others = [x for x in ts.report.results if x.status != S.PASS and x.rule_id != "doc.final_edition"]
    assert all(x.status == S.NOT_APPLICABLE for x in others), problems(ts.report)


@pytest.mark.parametrize(
    ("spec", "where", "old", "new", "rule_id"),
    [
        # 紅利其他出處（核對規則 §3.3）：§11 正確、其他出處錯
        (M0, "ts.art18", "紅利配息率為12.0000%", "紅利配息率為12.5000%", "doc.bonus"),
        (M0, "ts.art18", "最大值 [12.0000%，(150", "最大值 [12.5000%，(150", "doc.bonus"),
        (D0, "ts.art18", "= 美元 10,000 × (100% + 12.0000%%)", "= 美元 10,000 × (100% + 12.5000%%)", "doc.bonus"),
        # 價格表表頭 % 和第 11 條 K% 不同
        (M0, "ts.art12.table", "(即期初股價的90%)", "(即期初股價的95%)", "doc.price_header_pct"),
        # 情境重算（核對規則 §3.7）
        (M0, "ts.art18", "= 美元15,000.00", "= 美元15,100.00", "doc.scenario_calculations"),
        (M0, "ts.art18", "{100% + 50.00%}", "{100% + 12.0000%}", "doc.scenario_calculations"),
        (M0, "ts.art18", "= 美元 5,000.00", "= 美元 5,100.00", "doc.scenario_calculations"),
        (M0, "ts.art18", "為:100%", "為:90%", "doc.scenario_calculations"),
        (M0, "ts.art18", "在此例中為111 股", "在此例中為112 股", "doc.scenario_calculations"),  # 四捨五入而不是捨去
        (M0, "ts.art18", "= 111.1111 股", "= 111.1121 股", "doc.scenario_calculations"),
        (D0, "ts.art18", "= 美元 11,200.00", "= 美元 11,300.00", "doc.scenario_calculations"),
        (D0, "ts.art18", "/ 6 × 12 = -80%", "/ 6 × 12 = -70%", "doc.scenario_calculations"),
        # Maxi 情境以執行價算漲幅（應以期初股價）
        (M0, "ts.art18", "(150.0000/100.0000-1)", "(150.0000/90.0000-1)", "scenario.strike"),
        (M0, "ts.art18", "期末匯率為1 美元 = 美元 1.00", "期末匯率為1 日圓 = 日圓 1.00", "field.currency"),
    ],
)
def test_term_sheet_difference_is_reported(tmp_path, spec, where, old, new, rule_id):
    r = check(tmp_path, spec, edits=[Edit(old, new, where)])
    assert flagged(r, rule_id, S.MISMATCH), problems(r)


def test_extra_percent_sign_and_trailing_zeros_are_not_differences(tmp_path):
    """Digital 紅利多一個 %、年化少寫尾數 0 只比數值（範本規格 §4.2）。"""
    edits = [Edit("紅利配息率：12.0000% %", "紅利配息率：12.00%", "ts.art11"), Edit("= -80%", "= -80.00%", "ts.art18")]
    r = check(tmp_path, D0, edits=edits)
    assert r.status == S.PASS, problems(r)


def test_english_name_prefix_is_not_checked_but_the_rest_is(tmp_path):
    """英文名稱前面的 `Maxi ` 不核對（範本規格 §3.6）；後面的字要相同。"""
    other = Edit("Maxi Bonus Enhanced Notes", "Bonus Enhanced Notes", "ts.cover")
    assert not flagged(check(tmp_path / "prefix", M0, edits=[other]), "standard.product_name")
    wrong = Edit("Notes (Non Guaranteed, Unsecured)", "Notes (Unsecured)", "ts.cover")
    assert flagged(check(tmp_path / "suffix", M0, edits=[wrong]), "standard.product_name", S.MISMATCH)
