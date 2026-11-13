"""本機 Barclays FCN_Stepdown 真實樣本（workflow #544）；交易資料不進 Git，CI 自動略過。測試碼不含真實商品代號或數值。

樣本為說明書＋投資人須知成對（`real_data.STEPDOWN`，商品代號前 3 碼 029 是 Barclays）。斷言與探勘結論一致
（docs/checker/templates/barc-zh-stepdown.md §3、docs/checker/rules/barc-stepdown-check-rules.md §5）：6 份說明書與 6 份投資人須知
都辨識成 Barclays FCN_Stepdown，沒有一份被認成 Barclays FCN；KO 每日 4 份、每期 2 份，都是記憶式；每期 KO% 從第 1 期起固定往下降
（整數百分點）；各標的 KO 價 = 最初價格 × 第 1 期 KO%（half-up 4 位）；每日型比價日每一期都列。參考條件表要另外準備，整批核對的
試跑結果記在 PR。
"""

from collections import Counter
from decimal import ROUND_HALF_UP, Decimal

import fitz
import pytest

from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import BARC, BARC_STEPDOWN, detect, detect_iis
from fcn_checker.schema import CheckStatus as S
from real_data import STEPDOWN, requires

pytestmark = [pytest.mark.real_samples, requires(STEPDOWN / "029*_TS.pdf")]
Q4 = Decimal("0.0001")


def _lines(path):
    with fitz.open(path) as doc:
        return extract_lines(doc)


@pytest.fixture(scope="module")
def documents():
    """每份樣本：（是否投資人須知, 辨識到的範本, 辨識結果, 讀出結果或 None）。"""
    out = []
    for path in sorted(STEPDOWN.glob("029*.pdf")):
        iis = path.stem.endswith("_IIS")
        lines = _lines(path)
        template, result = (detect_iis if iis else detect)(lines)
        sheet = None
        if template is BARC_STEPDOWN and result.status == S.PASS:
            sheet = (BARC_STEPDOWN.iis.read if iis else BARC_STEPDOWN.read)(lines)
        out.append((iis, template, result, sheet))
    return out


def test_samples_are_barc_stepdown(documents):
    seen = Counter((iis, r.actual) for iis, _, r, _ in documents)
    assert seen == Counter({(False, BARC_STEPDOWN.template_id): 6, (True, BARC_STEPDOWN.iis.template_id): 6})
    assert all(t is not BARC for _, t, _, _ in documents), "沒有一份被認成 Barclays FCN"


def test_ko_levels_step_down_by_a_fixed_whole_point(documents):
    term_sheets = [s for iis, _, _, s in documents if s is not None and not iis]
    assert len(term_sheets) == 6
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
    assert observation == Counter({"D": 4, "P": 2})


def test_daily_compare_dates_list_every_period(documents):
    """每日型比價日：第一個可提前出場期到最後一期每一期都列（ADR 0033），最後一期 = 最終評價日。"""
    daily = [s for iis, _, _, s in documents if s is not None and not iis and s.f("ko_observation").value == "D"]
    assert len(daily) == 4
    for ts in daily:
        schedule = ts.f("autocall_schedule").value
        assert schedule.listed
        assert sorted(schedule.dates) == list(range(schedule.first_callable, schedule.periods + 1))
        dates = [schedule.dates[p] for p in sorted(schedule.dates)]
        assert dates == sorted(dates) and len(set(dates)) == len(dates)
        assert dates[-1] == ts.f("final_valuation_date").value


def test_investor_sheets_read_prices_without_ko(documents):
    sheets = [s for iis, _, _, s in documents if s is not None and iis]
    assert len(sheets) == 6
    for iis in sheets:
        prices = iis.f("underlying_prices")
        assert prices.ok and all("ko" not in row.prices for row in prices.value)
