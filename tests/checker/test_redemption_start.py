"""開始受理贖回日期（Issue #227；docs/checker/rules/shared-check-rules.md §6.2）：合成文件經批量入口核對。

寫日期的（Citi、UBS「即現預計為」）= 發行日的次一臺北營業日；寫文字的須是審查標準列出的寫法（說明書與投資人須知都核對）。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace

import pytest

import citi_synth
import sg_synth
import ubs_synth
from fcn_checker.schema import CheckStatus as S
from fcn_checker.trading_calendar import Market
from harness import CONFIG, with_markets
from harness import results as rule
from pdf_writer import Edit, zh_date

DATE, WORDING = "doc.redemption_start_date", "standard.redemption_start"


def closed_on(day: dt.date, code: str):
    """核對設定：假日表的 `code` 市場在 `day` 休市。"""

    def change(m: Market) -> Market:
        return replace(m, holidays=m.holidays | {day}) if m.code == code else m

    return with_markets(CONFIG, change)


def test_citi_date_must_be_the_next_business_day_after_issue(tmp_path):
    """Citi 開始受理贖回日期改成發行日當天 → 不一致，預期值是次一營業日（驗收條件）。"""
    spec = citi_synth.Spec()
    after = dt.date(2030, 1, 15)
    assert spec.issue_date == dt.date(2030, 1, 14)
    assert [x.status for x in rule(citi_synth.check(tmp_path, spec), DATE)] == [S.PASS]
    edit = Edit(f"開始受理贖回日期：{zh_date(after)}", f"開始受理贖回日期：{zh_date(spec.issue_date)}", "ts")
    (bad := tmp_path / "bad").mkdir()
    [x] = rule(citi_synth.check(bad, spec, edits=[edit]), DATE)
    assert (x.status, x.expected, x.actual) == (S.MISMATCH, after, spec.issue_date)


def test_taipei_holidays_are_skipped_but_exchange_holidays_are_not(tmp_path):
    """「營業日」是臺北營業日：臺北休假順延；只有美股休市不順延（真實樣本 Citi 有這種情形）。"""
    spec, day = citi_synth.Spec(), dt.date(2030, 1, 15)
    [x] = rule(citi_synth.check(tmp_path, spec, config=closed_on(day, "TW")), DATE)
    assert (x.status, x.expected) == (S.MISMATCH, dt.date(2030, 1, 16))
    (us := tmp_path / "us").mkdir()
    assert [x.status for x in rule(citi_synth.check(us, spec, config=closed_on(day, "US")), DATE)] == [S.PASS]


@pytest.mark.parametrize("code", ["TW", "US"])
def test_ubs_expected_date_also_needs_the_underlying_exchange_open(tmp_path, code):
    """UBS「預定交易日（並須為臺北營業日）(即現預計為 <日期>)」：臺北或標的交易所休市都要順延。"""
    spec = ubs_synth.Spec()
    report = ubs_synth.check(tmp_path, spec, config=closed_on(dt.date(2030, 1, 15), code))
    [x] = rule(report, DATE)
    assert (x.status, x.expected, x.actual) == (S.MISMATCH, dt.date(2030, 1, 16), dt.date(2030, 1, 15))
    assert [w.status for w in rule(report, WORDING)] == [S.PASS]


def test_listed_wording_passes_in_both_documents(tmp_path):
    ts, iis = sg_synth.check_pair(tmp_path)
    assert [x.status for x in rule(ts.report, WORDING)] == [S.PASS]
    assert [x.status for x in rule(iis.report, WORDING)] == [S.PASS]


@pytest.mark.parametrize("where", ["ts", "iis"])
def test_unlisted_wording_mismatches(tmp_path, where):
    """寫法不是審查標準列出的（例：發行日當日）→ 不一致；另一份文件不受影響。"""
    edit = Edit("開始受理贖回日期：發行日後第一個營業日", "開始受理贖回日期：發行日當日", where)
    ts, iis = sg_synth.check_pair(tmp_path, edits=[edit])
    bad, other = (ts, iis) if where == "ts" else (iis, ts)
    [x] = rule(bad.report, WORDING)
    assert x.status == S.MISMATCH and x.actual.startswith("發行日當日")
    assert [y.status for y in rule(other.report, WORDING)] == [S.PASS]
