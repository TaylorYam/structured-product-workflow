"""受託對象與能否承作 OSU 客戶（Issue #224）：不能承作的上手（DBS、Citi）全文不得出現 OSU；可以承作的上手（JPM）受託對象
要寫到 OSU 客戶。交易日（參考條件表）在審查標準 `osu.exempt_until`（含）以前，不一致特例通過（上手改版前的舊範本）。

合成文件經公開批量入口核對；商品、標的與價格皆虛構。
"""

from __future__ import annotations

import datetime as dt

import pytest

import citi_synth
import dbs_synth
import jpm_synth
from harness import MISMATCH, PASS, REVIEW_STANDARD, load_config
from pdf_writer import Edit

CUTOFF = dt.datetime(2026, 11, 30)  # 審查標準 osu.exempt_until
AFTER = dt.datetime(2026, 12, 1)
JPM_OLD = Edit("受託對象：專業投資人及OSU客戶", "受託對象：專業投資人", "iis.summary")


def osu(report) -> list:
    return [r for r in report.results if r.rule_id == "standard.osu_clients"]


def test_dbs_revised_template_passes(tmp_path):
    """合成文件是改版後的範本：只有固定警語與受託機構名稱「(含國際證券業務分公司)」寫到 OSU，兩者遮掉不算。"""
    item = dbs_synth.check_item(tmp_path)
    for report in (item.report, item.partner.report):
        [res] = osu(report)
        assert res.status == PASS and res.actual == 0


@pytest.mark.parametrize(("trade", "status"), [(CUTOFF, PASS), (AFTER, MISMATCH), (None, MISMATCH)])
def test_dbs_old_template_passes_until_the_cutoff(tmp_path, trade, status):
    """DBS 改版前的範本各處寫 OSU 客戶：交易日在特例日期（含）以前特例通過，之後或缺交易日判錯（說明書與投資人須知）。"""
    item = dbs_synth.check_item(tmp_path, edits=dbs_synth.OLD_OSU_EDITS, 交易日=trade)
    for report in (item.report, item.partner.report):
        [res] = osu(report)
        assert res.status == status and res.actual > 0 and res.expected == 0
        assert ("特例通過" in res.message) == (status == PASS)
        assert len(res.document_evidence) == res.actual  # 每處 OSU 一筆出處


@pytest.mark.parametrize(("trade", "status"), [(CUTOFF, PASS), (AFTER, MISMATCH)])
def test_jpm_clients_without_osu_pass_until_the_cutoff(tmp_path, trade, status):
    """JPM 可以承作 OSU 客戶，投資人須知受託對象只寫「專業投資人」：特例日期（含）以前特例通過，之後判錯。"""
    iis = jpm_synth.check_item(tmp_path, edits=[JPM_OLD], 交易日=trade).partner.report
    [res] = osu(iis)
    assert res.status == status and res.actual == "專業投資人"
    assert ("特例通過" in res.message) == (status == PASS)
    if status == PASS:
        assert "2026-11-30（含）以前" in res.tolerance


def test_jpm_clients_with_osu_pass(tmp_path):
    iis = jpm_synth.check_item(tmp_path).partner.report
    [res] = osu(iis)
    assert res.status == PASS and res.actual == "專業投資人及OSU客戶" and "特例" not in (res.message or "")


def test_osu_issuer_without_clients_field_has_no_result(tmp_path):
    """可以承作的上手，文件沒有「受託對象：」欄（JPM 說明書）時不產生結果。"""
    assert osu(jpm_synth.check(tmp_path)) == []


def test_citi_clients_with_osu_fail(tmp_path):
    """Citi 不能承作 OSU 客戶：投資人須知受託或銷售對象寫 OSU 客戶判錯（合成交易日在特例日期之後）；說明書沒寫 OSU。"""
    edit = Edit("受託或銷售對象：專業投資人", "受託或銷售對象：專業投資人及OSU客戶", "iis")
    item = citi_synth.check_item(tmp_path, edits=[edit])
    [ts] = osu(item.report)
    assert ts.status == PASS and ts.actual == 0
    [res] = osu(item.partner.report)
    assert res.status == MISMATCH and res.actual == 1 and res.reason_code == "osu_clients"
    assert "受託或銷售對象：專業投資人及OSU客戶" in res.document_evidence[0].text


def test_removing_the_osu_exemption_restores_the_rule(tmp_path):
    standard = tmp_path / "review_standard.toml"
    text = REVIEW_STANDARD.read_text(encoding="utf-8")
    assert text.count("exempt_until = 2026-11-30") == 1
    standard.write_text(text.replace("exempt_until = 2026-11-30", ""), encoding="utf-8")
    iis = jpm_synth.check_item(
        tmp_path, edits=[JPM_OLD], config=load_config(review_standard=standard), 交易日=CUTOFF
    ).partner.report
    [res] = osu(iis)
    assert res.status == MISMATCH and res.reason_code == "osu_clients"
