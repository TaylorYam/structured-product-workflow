"""本機 CA FCN_Stepdown 真實樣本（workflow #546）；交易資料不進 Git，CI 自動略過。測試碼不含真實商品代號或數值。

樣本為說明書＋投資人須知成對（`real_data.STEPDOWN`，商品代號前 3 碼 086 是 CA）。斷言與探勘結論一致
（docs/checker/templates/ca-zh-stepdown.md §3～§5）：3 份說明書都辨識成 CA FCN_Stepdown，沒有一份被認成 CA FCN、BEN 或 Super Bull；
都是 KO 每日觀察、記憶式、Non-Call 1；每期 KO 表期別從 1 起連續、和配息表同期數，同一期結束日相同；每期 KO% 從第 1 期起固定
往下降；各標的 KO 價 = 期初訂價 × 第 1 期 KO%（half-up 4 位）；每日型比價日每一期都列。投資人須知由 CA FCN 的投資人須知範本認，
記憶式兩句（Stepdown 的寫法）讀得出來。舊範本樣本不在這個資料夾，不在這裡測；實作 PR 的本機回歸已確認 5 份都停在本範本、
判範本不支援（探勘時還沒有本範本，當時被 CA FCN 範本認走，規格 §5）。
參考條件表要另外準備，整批核對的試跑結果記在 PR。
"""

from collections import Counter
from decimal import ROUND_HALF_UP, Decimal

import fitz
import pytest

from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import CA, CA_STEPDOWN, detect, detect_iis
from fcn_checker.schema import CheckStatus as S
from real_data import STEPDOWN, requires

pytestmark = [pytest.mark.real_samples, requires(STEPDOWN / "086*_TS.pdf")]
Q4 = Decimal("0.0001")


def _lines(path):
    with fitz.open(path) as doc:
        return extract_lines(doc)


@pytest.fixture(scope="module")
def documents():
    """每份樣本：（是否投資人須知, 辨識到的範本, 辨識結果, 讀出結果或 None）。"""
    out = []
    for path in sorted(STEPDOWN.glob("086*.pdf")):
        iis = path.stem.endswith("_IIS")
        lines = _lines(path)
        template, result = (detect_iis if iis else detect)(lines)
        sheet = None
        if template is not None and result.status == S.PASS:
            sheet = (template.iis.read if iis else template.read)(lines)
        out.append((iis, template, result, sheet))
    return out


def test_term_sheets_are_ca_stepdown(documents):
    seen = Counter((t.template_id if t else None) for iis, t, _, _ in documents if not iis)
    assert seen == Counter({CA_STEPDOWN.template_id: 3})


def test_investor_sheets_use_the_ca_fcn_template_and_read_the_memory_wording(documents):
    sheets = [(t, r, s) for iis, t, r, s in documents if iis]
    assert len(sheets) == 3
    for template, result, sheet in sheets:
        assert template is CA and result.status == S.PASS
        assert sheet.fields["ko_memory"].value is True


def test_every_field_reads(documents):
    """說明書每個欄位都讀得到（不適用、沒有的除外）。"""
    for iis, _, _, sheet in documents:
        if sheet is None or iis:
            continue
        bad = {
            k: v.status.value
            for k, v in sheet.fields.items()
            if not v.ok and v.status.value not in ("not_applicable", "absent")
        }
        assert bad == {}


def test_ko_table_lines_up_with_the_coupon_table(documents):
    for iis, _, _, ts in documents:
        if ts is None or iis:
            continue
        table, coupon = ts.f("ko_table").value, ts.f("coupon_table").value["rows"]
        assert [r.i for r in table] == [r["period"] for r in coupon] == list(range(1, len(coupon) + 1))
        assert [r.end for r in table] == [r.get("end") for r in coupon]


def test_ko_levels_step_down_by_a_fixed_step(documents):
    term_sheets = [s for iis, _, _, s in documents if s is not None and not iis]
    assert len(term_sheets) == 3
    for ts in term_sheets:
        f = ts.f
        assert f("ko_observation").value == "D" and f("ko_memory").value is True
        ko, step, levels = f("ko_pct").value, f("ko_step_pct").value, f("ko_levels").value
        assert step > 0
        assert [lv.period for lv in levels] == list(range(1, len(levels) + 1))
        assert [lv.pct for lv in levels] == [ko - step * (n - 1) for n in range(1, len(levels) + 1)]
        schedule = f("autocall_schedule").value
        assert schedule.first_callable == 1 and len(levels) == schedule.periods
        for row in f("underlying_prices").value:
            assert row.prices["ko"] == (row.prices["initial"] * ko / 100).quantize(Q4, ROUND_HALF_UP)


def test_daily_compare_dates_list_every_period(documents):
    """每日型比價日：第 1 期到最後一期每一期都列（ADR 0033），最後一期 = 最終評價日。"""
    for iis, _, _, ts in documents:
        if ts is None or iis:
            continue
        schedule = ts.f("autocall_schedule").value
        assert schedule.listed
        assert sorted(schedule.dates) == list(range(1, schedule.periods + 1))
        dates = [schedule.dates[p] for p in sorted(schedule.dates)]
        assert dates == sorted(dates) and len(set(dates)) == len(dates)
        assert dates[-1] == ts.f("final_valuation_date").value
