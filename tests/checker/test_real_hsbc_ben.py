"""本機 HSBC BEN 真實樣本（Issue #375）；交易資料不進 Git，CI 自動略過。測試碼不含真實商品代號或數值。

樣本為說明書＋投資人須知成對（`real_data.BEN`）。斷言與探勘結論一致（docs/checker/templates/hsbc-zh-ben.md §10、
hsbc-zh-ben-iis.md §5）：27 份說明書與 27 份投資人須知都是現行版、辨識成 HSBC BEN（Maxi 24、Digital 3）；除了 Digital 的殘留
註記與 Word 排版少的 (最終版)，每個欄位都讀得到；沒有一份被認成 HSBC FCN 或 Super Bull。整批核對的試跑結果記在 PR。
"""

from collections import Counter

import fitz
import pytest

from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import HSBC_BEN, detect, detect_iis
from fcn_checker.schema import CheckStatus as S
from fcn_checker.schema import FieldStatus
from real_data import BEN, requires

pytestmark = [pytest.mark.real_samples, requires(BEN / "325*_TS.pdf")]
READ = (FieldStatus.PRESENT, FieldStatus.NOT_APPLICABLE)


def _lines(path):
    with fitz.open(path) as doc:
        return extract_lines(doc)


@pytest.fixture(scope="module")
def documents():
    """每份樣本：（是否投資人須知, 辨識到的上手, 辨識結果, 讀出結果或 None）。"""
    out = []
    for path in sorted(BEN.glob("325*.pdf")):
        iis = path.stem.endswith("_IIS")
        lines = _lines(path)
        issuer, result = (detect_iis if iis else detect)(lines)
        sheet = None
        if issuer is HSBC_BEN and result.status == S.PASS:
            sheet = (HSBC_BEN.iis.read if iis else HSBC_BEN.read)(lines)
        out.append((iis, issuer, result, sheet))
    return out


def test_samples_are_current_hsbc_ben(documents):
    seen = Counter((iis, r.actual) for iis, _, r, _ in documents)
    assert seen == Counter({(False, HSBC_BEN.template_id): 27, (True, HSBC_BEN.iis.template_id): 27})
    assert all(issuer is HSBC_BEN for _, issuer, _, _ in documents), "沒有一份被認成 HSBC FCN 或 Super Bull"
    kinds = Counter(s.kind for iis, _, _, s in documents if s is not None and not iis)
    assert kinds == Counter({"maxi": 24, "digital": 3})


def test_every_field_of_the_samples_is_read(documents):
    """讀不到的只有已知的兩種（範本規格 §1、§2）：Digital 標的表後的殘留註記、Word 排版沒有 (最終版)。"""
    unread = Counter(
        (iis, name)
        for iis, _, _, sheet in documents
        if sheet is not None
        for name, pf in sheet.fields.items()
        if pf.status not in READ
    )
    assert unread == Counter({(False, "template_residue"): 3, (False, "final_edition"): 2, (True, "final_edition"): 2})


def test_samples_follow_the_ben_terms(documents):
    """沒有 KO（每期觀察、非記憶式、Non-Call = 天期、比價日只有最後評價日）、無 KI；參與率 Maxi 100、Digital 0；情境三個都讀得到。"""
    term_sheets = [s for iis, _, _, s in documents if s is not None and not iis]
    assert len(term_sheets) == 27
    for ts in term_sheets:
        f = ts.f
        assert (f("ko_observation").value, f("ko_memory").value, f("ki_type").value) == ("P", False, "none")
        assert f("first_callable_period").value == f("tenor_months").value
        assert list(f("autocall_schedule").value.dates) == [f("tenor_months").value]
        assert f("participation_pct").value == (100 if ts.kind == "maxi" else 0)
        assert ts.ben_scenarios.found
