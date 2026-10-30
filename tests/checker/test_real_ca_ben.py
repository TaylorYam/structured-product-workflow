"""本機 CA BEN 真實樣本（Issue #377）；交易資料不進 Git，CI 自動略過。測試碼不含真實商品代號或數值。

樣本為說明書＋投資人須知成對（`real_data.BEN`）。斷言與探勘結論一致（docs/checker/templates/ca-zh-ben.md §1、
ca-zh-ben-iis.md §5）：5 份說明書是現行版 2、KO 價欄版與早期版各 1（不支援的範本版本）、Digital 1；投資人須知 Bonus 4 份都是
現行版、Digital 1。現行版每個欄位都讀得到，只有 1 份 13.(6) 配息日改寫成參照第 9 項（待使用者確認，詳見本機疑慮記錄）；
沒有一份被認成 CA FCN 或 Super Bull。整批核對的試跑結果記在 PR。
"""

from collections import Counter

import fitz
import pytest

from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import CA_BEN, detect, detect_iis
from fcn_checker.parsers import ca_ben
from fcn_checker.schema import CheckStatus as S
from fcn_checker.schema import FieldStatus
from real_data import BEN, requires

pytestmark = [pytest.mark.real_samples, requires(BEN / "086*_TS.pdf")]
READ = (FieldStatus.PRESENT, FieldStatus.NOT_APPLICABLE)


def _lines(path):
    with fitz.open(path) as doc:
        return extract_lines(doc)


@pytest.fixture(scope="module")
def documents():
    """每份樣本：（是否投資人須知, 辨識到的上手, 辨識結果, 讀出結果或 None）。"""
    out = []
    for path in sorted(BEN.glob("086*.pdf")):
        iis = path.stem.endswith("_IIS")
        lines = _lines(path)
        issuer, result = (detect_iis if iis else detect)(lines)
        sheet = None
        if issuer is CA_BEN and result.status == S.PASS:
            sheet = (CA_BEN.iis.read if iis else CA_BEN.read)(lines)
        out.append((iis, issuer, result, sheet))
    return out


def _kind(result) -> str:
    if result.status == S.PASS:
        return "current"
    return next(k for k, m in (("digital", ca_ben.DIGITAL), ("old", "寫法和現行版不同")) if m in result.message)


def test_samples_are_ca_ben_current_old_or_digital(documents):
    seen = Counter((iis, _kind(r)) for iis, _, r, _ in documents)
    assert seen == Counter(
        {(False, "current"): 2, (False, "old"): 2, (False, "digital"): 1, (True, "current"): 4, (True, "digital"): 1}
    )
    assert all(r.status == S.PASS or r.reason_code == "template_unsupported" for _, _, r, _ in documents)
    assert all(issuer in (CA_BEN, None) for _, issuer, _, _ in documents), "沒有一份被認成 CA FCN 或 Super Bull"
    assert all(
        r.actual in (CA_BEN.template_id, CA_BEN.iis.template_id) for _, _, r, _ in documents if r.status == S.PASS
    )


def test_every_field_of_the_current_samples_is_read(documents):
    """讀不到的只有 1 份說明書 13.(6) 配息日改寫成參照第 9 項（待使用者確認，詳見本機疑慮記錄）。"""
    unread = Counter(
        (iis, name)
        for iis, _, _, sheet in documents
        if sheet is not None
        for name, pf in sheet.fields.items()
        if pf.status not in READ
    )
    assert unread == Counter({(False, "no_coupon_art13"): 1})


def test_current_samples_follow_the_ben_terms(documents):
    """沒有 KO（每期觀察、非記憶式、Non-Call = 天期、比價日只有期末訂價日）、無 KI；參與率 100；情境三個都讀得到。"""
    term_sheets = [s for iis, _, _, s in documents if s is not None and not iis]
    assert len(term_sheets) == 2
    for ts in term_sheets:
        f = ts.f
        assert (f("ko_observation").value, f("ko_memory").value, f("ki_type").value) == ("P", False, "none")
        assert f("first_callable_period").value == f("tenor_months").value
        assert list(f("autocall_schedule").value.dates) == [f("tenor_months").value]
        assert f("participation_pct").value == 100
        assert ts.scenarios.found
        assert {m.parsed("x").value for m in ts.bonus_mentions} == {f("coupon_pa_pct").value}
        assert {m.parsed("x").value for m in ts.participation_mentions} == {f("participation_pct").value}
    sheets = [s for iis, _, _, s in documents if s is not None and iis]
    assert len(sheets) == 4
    assert all(s.f("has_ki").value is False for s in sheets)
