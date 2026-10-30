"""MS BEN 黑箱回歸（Issue #376）：合成說明書與投資人須知（ms_ben_synth.BenSpec：一般 BEN、Digital，各有 lookback）經批量入口
核對；反例照核對規則 docs/checker/rules/ms-ben-check-rules.md §8。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import fitz
import pytest

import ms_synth
from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import MS, MS_BEN
from fcn_checker.schema import CheckStatus as S
from harness import field_problems as problems
from harness import flagged, iis_path
from harness import results as rule
from ms_ben_synth import HEADERS, BenSpec, build_pdf, check_pair
from pdf_writer import Edit
from reference_synth import REFERENCE_HEADERS

M0 = BenSpec()  # 一般 BEN：2 檔、6 個月、K% 90、紅利 12.50%
D0 = BenSpec(kind="digital", count=1, strike=Decimal("75.00"), ko=Decimal("75.00"), annual=Decimal("3.00"), tenor=3)
ML = BenSpec(lookback=True)  # 一般 BEN lookback：回顧結束日 = 發行日
DL = BenSpec(
    kind="digital", lookback=True, count=1, strike=Decimal("100.00"), ko=Decimal("100.00"), worse=Decimal("60")
)


def lookback_fill(report):
    return [(d.column, d.action.value, d.expected) for d in report.backfill if d.column == "回顧結束日"]


@pytest.mark.parametrize(
    "spec",
    [M0, BenSpec(count=3, strike=Decimal("100.00"), ko=Decimal("100.00"), tenor=12), D0, ML, DL],
    ids=["maxi-2UL", "maxi-3UL-K100", "digital", "maxi-lookback", "digital-lookback"],
)
def test_supported_pairs_pass(tmp_path, spec):
    ts, iis = check_pair(tmp_path, spec)
    assert ts.report.template == MS_BEN.template_id
    assert iis.report.template == MS_BEN.iis.template_id
    assert ts.report.status == S.PASS, problems(ts.report)
    assert iis.report.status == S.PASS, problems(iis.report)
    # 回顧結束日：lookback 表上空白 → 核對通過後回填說明書的日期；不是 lookback → 沒有這格
    expected = [("回顧結束日", "fill", spec.lookback_end_date)] if spec.lookback else []
    assert lookback_fill(ts.report) == expected
    participation = rule(ts.report, "field.participation_pct")[0]
    assert (participation.status, participation.actual) == (S.PASS, spec.participation)


def lines_of(path):
    with fitz.open(path) as doc:
        return extract_lines(doc)


def test_ms_fcn_and_ben_are_never_confused(tmp_path):
    """MS FCN 說明書與投資人須知不會被認成 BEN，反之亦然（核對規則 §8）。"""
    for k in ("fcn", "maxi", "digital"):
        (tmp_path / k).mkdir()
    fcn = ms_synth.build_pdf(tmp_path / "fcn" / "147199990001_TS.pdf", ms_synth.Spec())
    ben = [build_pdf(tmp_path / k / "147199990001_TS.pdf", s) for k, s in (("maxi", ML), ("digital", D0))]
    assert MS.detect(lines_of(fcn)).matched and MS.iis.detect(lines_of(iis_path(fcn))).matched
    assert not MS_BEN.detect(lines_of(fcn)).matched and not MS_BEN.iis.detect(lines_of(iis_path(fcn))).matched
    for pdf in ben:
        ts, iis = lines_of(pdf), lines_of(iis_path(pdf))
        assert MS_BEN.detect(ts).matched and MS_BEN.iis.detect(iis).matched
        assert not MS.detect(ts).matched and not MS.iis.detect(iis).matched
        assert not MS_BEN.detect(iis).matched and not MS_BEN.iis.detect(ts).matched


def test_chinese_and_english_kinds_must_agree(tmp_path):
    """Digital 中文名稱配一般 BEN 英文名稱：不辨識成 BEN（轉人工）。"""
    edit = Edit("Equity Linked Notes", "Bonus Enhanced Notes", "ts.cover")
    pdf = build_pdf(tmp_path / f"{D0.product_code}_TS.pdf", D0, edits=[edit], iis=False)
    result = MS_BEN.detect(lines_of(pdf))
    assert not result.matched and any("型態不同" in x for x in result.failed)


@pytest.mark.parametrize(
    ("spec", "product"),
    [(M0, "FCN"), (D0, "FCN"), (M0, None)],
    ids=["maxi-booked-as-fcn", "digital-booked-as-fcn", "ben-blank-product"],
)
def test_product_must_match_the_template_family(tmp_path, spec, product):
    ts, iis = check_pair(tmp_path, spec, Product=product)
    for item in (ts, iis):
        assert flagged(item.report, "template.product_family", S.REVIEW_REQUIRED)
        assert item.unsupported_variant and item.release_problem


@pytest.mark.parametrize(
    ("spec", "edit"),
    [
        (M0, Edit("參與率：100.00%", "參與率：0%", "ts.art15")),  # 一般 BEN 名稱配 Digital 參與率
        (D0, Edit("參與率：0%", "參與率：100.00%", "ts.art15")),  # Digital 名稱配一般 BEN 參與率
        (M0, Edit("主要給付項目為到期贖回金額：", "主要給付項目為到期贖回金額或自動提前出場贖回金額：", "ts.art10")),
        (D0, Edit("每單位面額 x (100% +紅利配息率)", "每單位面額 x 100%", "ts.art15")),  # §15(1)(a) 不是 Digital 公式
    ],
    ids=["maxi-participation-0", "digital-participation-100", "autocall-opening", "digital-formula"],
)
def test_other_term_sheet_wording_is_not_supported(tmp_path, spec, edit):
    ts, _ = check_pair(tmp_path, spec, edits=[edit])
    detect = rule(ts.report, "template.detect")[0]
    assert (detect.status, detect.reason_code) == (S.REVIEW_REQUIRED, "template_unsupported")
    assert ts.status_label == "不支援的範本版本" and ts.release_problem


def test_fcn_coupon_sentence_in_the_investor_sheet_is_not_supported(tmp_path):
    """投資人須知收益分配事項寫成 FCN 固定配息句：認得出是 MS BEN 投資人須知、但不是現行版。"""
    old = "收益分配事項：於到期日，視期末定價日時籃子中表現最差的連結標的之收盤價而定。"
    edit = Edit(old, "收益分配事項：於每月配息日，發行機構將依下列計算公式給付配息金額。", "iis.coupon")
    _, iis = check_pair(tmp_path, M0, edits=[edit])
    detect = rule(iis.report, "template.detect")[0]
    assert (detect.status, detect.reason_code) == (S.REVIEW_REQUIRED, "template_unsupported")


def test_knock_in_wording_means_no_ki_cannot_be_ruled_out(tmp_path):
    """全文出現「觸及下限事件」：無法判定無 KI → 人工覆核。"""
    ts, _ = check_pair(tmp_path, M0, edits=[Edit("其中：", "其中：觸及下限事件：不適用。", "ts.art15")])
    assert flagged(ts.report, "doc.ki_consistency", S.REVIEW_REQUIRED), problems(ts.report)


def test_mixed_lookback_wording_needs_review(tmp_path):
    """lookback 只有回顧定義、沒有 §14(9)（混雜）：轉人工覆核。"""
    edit = Edit("回顧觀察期間：", "觀察期間說明：", "ts.art14")
    ts, _ = check_pair(tmp_path, ML, edits=[edit])
    assert flagged(ts.report, "doc.template_variant", S.REVIEW_REQUIRED), problems(ts.report)


@pytest.mark.parametrize(
    ("spec", "edits", "field"),
    [
        (ML, [Edit("自交易日（包含）起", "自發行日（包含）起", "ts.art14")], "lookback_start"),
        (ML.with_(lookback_end=ML.final_date + dt.timedelta(days=1)), [], "lookback_end_date"),
        (ML.with_(lookback_end=ML.trade_date), [], "lookback_end_date"),
        (ML.with_(lookback_end=dt.date(2027, 5, 31)), [], "lookback_end_trading_day"),  # 美國休市
        (ML.with_(lookback_end=dt.date(2027, 4, 24)), [], "lookback_end_trading_day"),  # 週六
    ],
    ids=["start", "after-final", "on-trade-date", "holiday", "weekend"],
)
def test_lookback_period_must_be_valid(tmp_path, spec, edits, field):
    ts, _ = check_pair(tmp_path, spec, edits=edits)
    bad = [r for r in rule(ts.report, "doc.lookback_period") if r.field == field]
    assert [r.status for r in bad] == [S.MISMATCH], problems(ts.report)


@pytest.mark.parametrize(
    ("spec", "headers", "value", "status", "action"),
    [
        (ML, HEADERS, dt.datetime(2027, 4, 19), S.PASS, "match"),
        (ML, HEADERS, dt.datetime(2027, 4, 20), S.MISMATCH, "mismatch"),
        (M0, HEADERS, dt.datetime(2027, 4, 20), S.MISMATCH, None),  # 不是 lookback 但表上有回顧結束日
        (ML, REFERENCE_HEADERS + ["參與率(%)"], None, S.REVIEW_REQUIRED, None),  # 表上沒有這欄
    ],
    ids=["same", "different", "not-lookback", "no-column"],
)
def test_lookback_end_date_on_the_sheet(tmp_path, spec, headers, value, status, action):
    ts, _ = check_pair(tmp_path, spec, headers=headers, **{"回顧結束日": value})
    result = rule(ts.report, "backfill.lookback_end_date")
    assert [r.status for r in result] == [status], problems(ts.report)
    assert [d[1] for d in lookback_fill(ts.report)] == ([action] if action else [])


def test_non_lookback_without_the_column_is_not_affected(tmp_path):
    ts, _ = check_pair(tmp_path, M0, headers=REFERENCE_HEADERS + ["參與率(%)"])
    assert ts.report.status == S.PASS, problems(ts.report)
    assert all(r.rule_id != "backfill.lookback_end_date" for r in ts.report.results)


@pytest.mark.parametrize(
    ("spec", "where", "old", "new", "rule_id"),
    [
        # 紅利、參與率其他出處（核對規則 §3.3）：§15 正確、其他出處錯
        (M0, "ts.art16", "紅利配息率：12.50%", "紅利配息率：12.00%", "doc.bonus"),
        (M0, "ts.scen.二", "(100%+12.50%)", "(100%+12.00%)", "doc.bonus"),
        (D0, "ts.scen.一", "(100%+3.00%)", "(100%+3.50%)", "doc.bonus"),
        (M0, "ts.art16", "參與率：100.00%", "參與率：90.00%", "doc.participation"),
        # 情境：一般 BEN 情境一用執行價算漲幅、年化寫錯、交股股數寫錯
        (M0, "ts.scen.一", "/100.0000 美元-100%", "/90.0000 美元-100%", "scenario.strike"),
        (M0, "ts.scen.三", "年化報酬率為-40.00%", "年化報酬率為-40.50%", "doc.scenario_calculations"),
        (D0, "ts.scen.三", "在此例中為 133 股", "在此例中為 134 股", "doc.scenario_calculations"),
        # 到期公式：§10 和 §15(1) 不同
        (M0, "ts.art10", "並以計價幣別支付。", "並以美元支付。", "doc.redemption_formula"),
    ],
)
def test_term_sheet_difference_is_reported(tmp_path, spec, where, old, new, rule_id):
    ts, _ = check_pair(tmp_path, spec, edits=[Edit(old, new, where)])
    assert flagged(ts.report, rule_id), problems(ts.report)


@pytest.mark.parametrize(
    ("where", "old", "new", "rule_id"),
    [
        ("iis.coupon", "紅利配息率：12.50%", "紅利配息率：12.00%", "doc.bonus"),
        ("iis.redeem", "回顧", "", "doc.redemption_formula"),  # 說明書是 lookback、投資人須知的到期公式不是
    ],
    ids=["bonus", "formula-lookback"],
)
def test_investor_sheet_difference_is_reported(tmp_path, where, old, new, rule_id):
    _, iis = check_pair(tmp_path, ML, edits=[Edit(old, new, where)])
    assert flagged(iis.report, rule_id), problems(iis.report)


def delivery(report):
    (r,) = [r for r in rule(report, "doc.redemption_formula") if r.field == "delivery_clause"]
    return r


@pytest.mark.parametrize(
    ("spec", "old", "new"),
    [
        (M0, None, None),  # 合成投資人須知照樣本寫句號
        (M0, "後之數。非整數", "後之數，非整數"),  # 和說明書一樣寫逗號
        (DL, None, None),
    ],
    ids=["full-stop", "comma-as-term-sheet", "digital-lookback"],
)
def test_investor_sheet_delivery_allows_only_the_known_full_stop(tmp_path, spec, old, new):
    """BEN-376-09：(b) 股數一句的句號／逗號不判錯。"""
    edits = [Edit(old, new, "iis.redeem")] if old else []
    _, iis = check_pair(tmp_path, spec, edits=edits)
    assert delivery(iis.report).status == S.PASS, problems(iis.report)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("後之數。非整數", "後之數；非整數"),  # 同一處換成其他標點
        ("連結標的。如該數目", "連結標的，如該數目"),  # 同樣的句號／逗號互換，但在 (b) 的別句
        ("轉換匯率），並以", "轉換匯率）。並以"),
        ("並以計價幣別支付", "並以美元支付"),
    ],
    ids=["other-mark-same-place", "swap-other-sentence", "swap-last-sentence", "wording"],
)
def test_investor_sheet_delivery_other_differences_are_mismatches(tmp_path, old, new):
    """BEN-376-09 的容許只限那一處：其他標點或文字不同 → 不一致。"""
    _, iis = check_pair(tmp_path, M0, edits=[Edit(old, new, "iis.redeem")])
    assert delivery(iis.report).status == S.MISMATCH, problems(iis.report)


@pytest.mark.parametrize(
    "spec",
    [BenSpec(count=1), BenSpec(count=4), BenSpec(kind="digital", count=2), BenSpec(currency_zh="日圓")],
    ids=["maxi-1UL", "maxi-4UL", "digital-2UL", "jpy"],
)
def test_shapes_without_samples_need_review(tmp_path, spec):
    ts, _ = check_pair(tmp_path, spec)
    result = [r for r in rule(ts.report, "doc.template_variant") if r.field == "template_variant"][0]
    assert (result.status, result.reason_code) == (S.REVIEW_REQUIRED, "template_variant_unverified")
    assert "待樣本驗證" in result.message
