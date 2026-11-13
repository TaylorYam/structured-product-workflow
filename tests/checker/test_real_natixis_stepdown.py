"""本機 Natixis FCN_Stepdown 真實樣本（workflow #545）；交易資料不進 Git，CI 自動略過。測試碼不含真實商品代號或數值。

樣本為說明書＋投資人須知成對（`real_data.STEPDOWN`，商品代號前 3 碼 328 是 Natixis）。斷言與探勘結論一致
（docs/checker/templates/natixis-zh-stepdown.md §3、natixis-zh-stepdown-iis.md §3～§4）：7 份說明書都辨識成 Natixis FCN_Stepdown
（含 2 份早期版面，相容字轉成標準字後讀），沒有一份被認成 Natixis FCN 或 DACN；KO 每日 5 份、每期 2 份，都是記憶式；每期 KO%
從第 1 期起固定往下降（整數百分點）；各標的 KO 價 = 期初進場價格 × 第 1 期 KO%（half-up 4 位）；每日型比價日每一期都列。
投資人須知：現行版面 5 份辨識成 Stepdown 投資人須知（3 份一句 KO 定義、2 份兩句轉人工），早期版面 2 份停在 Stepdown、範本不符。
參考條件表要另外準備，整批核對的試跑結果記在 PR。
"""

from collections import Counter
from decimal import ROUND_HALF_UP, Decimal

import fitz
import pytest

from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import NATIXIS_STEPDOWN, detect, detect_iis
from fcn_checker.parsers.natixis_iis import KO_DEF_INITIAL
from fcn_checker.schema import CheckStatus as S
from fcn_checker.schema import FieldStatus
from real_data import STEPDOWN, requires

pytestmark = [pytest.mark.real_samples, requires(STEPDOWN / "328*_TS.pdf")]
Q4 = Decimal("0.0001")


def _lines(path):
    with fitz.open(path) as doc:
        return extract_lines(doc)


@pytest.fixture(scope="module")
def documents():
    """每份樣本：（是否投資人須知, 辨識到的範本, 辨識結果, 讀出結果或 None, 抽出的文字行）。"""
    out = []
    for path in sorted(STEPDOWN.glob("328*.pdf")):
        iis = path.stem.endswith("_IIS")
        lines = _lines(path)
        template, result = (detect_iis if iis else detect)(lines)
        sheet = None
        if template is NATIXIS_STEPDOWN and result.status == S.PASS:
            sheet = (NATIXIS_STEPDOWN.iis.read if iis else NATIXIS_STEPDOWN.read)(lines)
        out.append((iis, template, result, sheet, lines))
    return out


def test_term_sheets_are_natixis_stepdown(documents):
    seen = Counter((t.template_id if t else None) for iis, t, _, _, _ in documents if not iis)
    assert seen == Counter({NATIXIS_STEPDOWN.template_id: 7})


def test_investor_sheets_current_layout_is_stepdown_and_early_layout_stops_there(documents):
    """現行版面辨識成 Stepdown 投資人須知；早期版面停在 Stepdown（claimed）、不被任何範本認走。"""
    sheets = [(t, r, lines) for iis, t, r, _, lines in documents if iis]
    assert len(sheets) == 7
    matched = [r for t, r, _ in sheets if t is NATIXIS_STEPDOWN]
    assert len(matched) == 5 and all(r.status == S.PASS for r in matched)
    early = [(t, r, lines) for t, r, lines in sheets if t is not NATIXIS_STEPDOWN]
    assert len(early) == 2
    for t, r, lines in early:
        assert t is None and r.reason_code == "template_unknown"
        assert NATIXIS_STEPDOWN.iis.detect(lines).claimed


def test_investor_sheet_ko_definitions(documents):
    """KO 價格定義：3 份只參照說明書第 14 項（通過），2 份另有「下表所列」一句（歧義，轉人工覆核）。"""
    seen = Counter()
    for iis, _, _, sheet, _ in documents:
        if iis and sheet is not None:
            pf = sheet.fields["ko_definition"]
            seen[pf.status, pf.value if pf.status == FieldStatus.PRESENT else None] += 1
    assert seen == Counter({(FieldStatus.PRESENT, KO_DEF_INITIAL): 3, (FieldStatus.AMBIGUOUS, None): 2})


def test_ko_levels_step_down_by_a_fixed_whole_point(documents):
    term_sheets = [s for iis, _, _, s, _ in documents if s is not None and not iis]
    assert len(term_sheets) == 7
    observation = Counter()
    for ts in term_sheets:
        f = ts.f
        observation[f("ko_observation").value] += 1
        assert f("ko_memory").value is True
        ko, step, levels = f("ko_pct").value, f("ko_step_pct").value, f("ko_levels").value
        assert step > 0 and step == step.to_integral_value()
        assert [lv.period for lv in levels] == list(range(1, len(levels) + 1))
        assert [lv.pct for lv in levels] == [ko - step * (n - 1) for n in range(1, len(levels) + 1)]
        schedule = f("autocall_schedule").value
        assert len(levels) == schedule.periods - schedule.first_callable + 1
        for row in f("underlying_prices").value:
            assert row.prices["ko"] == (row.prices["initial"] * ko / 100).quantize(Q4, ROUND_HALF_UP)
    assert observation == Counter({"D": 5, "P": 2})


def test_daily_compare_dates_list_every_period(documents):
    """每日型比價日：第一個可提前到期期到最後一期每一期都列（ADR 0033），最後一期 = 最終評價日。"""
    daily = [s for iis, _, _, s, _ in documents if s is not None and not iis and s.f("ko_observation").value == "D"]
    assert len(daily) == 5
    for ts in daily:
        schedule = ts.f("autocall_schedule").value
        assert schedule.listed
        assert sorted(schedule.dates) == list(range(schedule.first_callable, schedule.periods + 1))
        dates = [schedule.dates[p] for p in sorted(schedule.dates)]
        assert dates == sorted(dates) and len(set(dates)) == len(dates)
        assert dates[-1] == ts.f("final_valuation_date").value
