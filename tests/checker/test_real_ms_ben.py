"""本機 MS BEN 真實樣本（Issue #376）；交易資料不進 Git，CI 自動略過。測試碼不含真實商品代號或數值。

樣本為說明書＋投資人須知成對（`real_data.BEN`）。斷言與探勘結論一致（docs/checker/rules/ms-ben-check-rules.md §6）：18 份說明書
與 18 份投資人須知都是現行版、辨識成 MS BEN（一般 BEN 16、Digital 2；lookback 6）；每個欄位都讀得到；沒有一份被認成 MS FCN。
整批核對的試跑結果記在 PR。
"""

from collections import Counter

import fitz
import pytest

from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import MS_BEN, detect, detect_iis
from fcn_checker.parsers import ms_ben
from fcn_checker.parsers.ms_ben_iis import DELIVERY_PUNCTUATION, delivery_as_term_sheet
from fcn_checker.schema import CheckStatus as S
from fcn_checker.schema import FieldStatus
from real_data import BEN, requires

pytestmark = [pytest.mark.real_samples, requires(BEN / "147*_TS.pdf")]
READ = (FieldStatus.PRESENT, FieldStatus.NOT_APPLICABLE)


def _lines(path):
    with fitz.open(path) as doc:
        return extract_lines(doc)


@pytest.fixture(scope="module")
def documents():
    """每份樣本：（是否投資人須知, 辨識到的上手, 辨識結果, 讀出結果或 None）。"""
    out = []
    for path in sorted(BEN.glob("147*.pdf")):
        iis = path.stem.endswith("_IIS")
        lines = _lines(path)
        issuer, result = (detect_iis if iis else detect)(lines)
        sheet = None
        if issuer is MS_BEN and result.status == S.PASS:
            sheet = (MS_BEN.iis.read if iis else MS_BEN.read)(lines)
        out.append((iis, issuer, result, sheet))
    return out


def test_samples_are_current_ms_ben(documents):
    seen = Counter((iis, r.actual) for iis, _, r, _ in documents)
    assert seen == Counter({(False, MS_BEN.template_id): 18, (True, MS_BEN.iis.template_id): 18})
    assert all(issuer is MS_BEN for _, issuer, _, _ in documents), "沒有一份被認成 MS FCN"
    kinds = Counter((s.kind, s.f("lookback").value) for iis, _, _, s in documents if s is not None and not iis)
    assert kinds == Counter({("maxi", False): 11, ("maxi", True): 5, ("digital", False): 1, ("digital", True): 1})


def test_every_field_of_the_samples_is_read(documents):
    unread = [
        (iis, name)
        for iis, _, _, sheet in documents
        if sheet is not None
        for name, pf in sheet.fields.items()
        if pf.status not in READ
    ]
    assert unread == []


def test_samples_follow_the_ben_terms(documents):
    """沒有 KO、無 KI；參與率一般 BEN 100、Digital 0；lookback 才有回顧結束日；情境都讀得到；投資人須知到期公式 = 說明書。"""
    term_sheets = {}
    for iis, _, _, s in documents:
        if s is not None and not iis:
            term_sheets[s.f("isin").value] = s
    assert len(term_sheets) == 18
    for ts in term_sheets.values():
        f = ts.f
        assert (f("ko_observation").value, f("ko_memory").value, f("ki_type").value) == ("P", False, "none")
        assert f("first_callable_period").value == f("tenor_months").value
        assert f("participation_pct").value == (100 if ts.kind == "maxi" else 0)
        assert f("lookback_end_date").ok == f("lookback").value
        assert ts.ben_scenarios.found
    for iis, _, _, sheet in documents:
        if iis and sheet is not None:
            ts = term_sheets[sheet.f("isin").value]
            assert sheet.f("maturity_formula").value == (ts.kind, ts.f("lookback").value)
            text = sheet.f("delivery_clause").value
            assert DELIVERY_PUNCTUATION[1] in text  # BEN-376-09：樣本都寫句號
            assert delivery_as_term_sheet(text) == ms_ben.delivery_clause(ts.kind, ts.f("lookback").value)
            assert sheet.f("bonus").value == ts.f("coupon_pa_pct").value
