"""本機 HSBC Super Bull 真實樣本（Issue #314）；交易資料不進 Git，CI 自動略過。測試碼不含真實商品代號或數值。

樣本為說明書＋投資人須知成對（`real_data.SUPER_BULL`，和 BNP、CA 放同一個資料夾）。斷言與探勘結論一致
（docs/checker/templates/hsbc-zh-super-bull.md §10、hsbc-zh-super-bull-iis.md §5）：5 份說明書與 5 份投資人須知都是現行版、
辨識成 HSBC Super Bull、每個欄位都讀得到；沒有一份被認成 HSBC FCN。參考條件表要另外準備，整批核對的試跑結果記在 PR。
"""

from collections import Counter

import fitz
import pytest

from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import HSBC, HSBC_SUPER_BULL, detect, detect_iis
from fcn_checker.schema import CheckStatus as S
from fcn_checker.schema import FieldStatus
from real_data import SUPER_BULL, requires

pytestmark = [pytest.mark.real_samples, requires(SUPER_BULL / "325*_TS.pdf")]
READ = (FieldStatus.PRESENT, FieldStatus.NOT_APPLICABLE)


def _lines(path):
    with fitz.open(path) as doc:
        return extract_lines(doc)


@pytest.fixture(scope="module")
def documents():
    """每份樣本：（是否投資人須知, 辨識到的上手, 辨識結果, 讀出結果或 None）。"""
    out = []
    for path in sorted(SUPER_BULL.glob("325*.pdf")):
        iis = path.stem.endswith("_IIS")
        lines = _lines(path)
        issuer, result = (detect_iis if iis else detect)(lines)
        sheet = None
        if issuer is HSBC_SUPER_BULL and result.status == S.PASS:
            sheet = (HSBC_SUPER_BULL.iis.read if iis else HSBC_SUPER_BULL.read)(lines)
        out.append((iis, issuer, result, sheet))
    return out


def test_samples_are_current_hsbc_super_bull(documents):
    seen = Counter((iis, r.actual) for iis, _, r, _ in documents)
    assert seen == Counter({(False, HSBC_SUPER_BULL.template_id): 5, (True, HSBC_SUPER_BULL.iis.template_id): 5})
    assert all(issuer is not HSBC for _, issuer, _, _ in documents), "沒有一份被認成 HSBC FCN"


def test_every_field_of_the_samples_is_read(documents):
    unread = Counter(
        (iis, name)
        for iis, _, _, sheet in documents
        if sheet is not None
        for name, pf in sheet.fields.items()
        if pf.status not in READ
    )
    assert unread == Counter()


def test_samples_follow_the_super_bull_terms(documents):
    """每期觀察、非記憶式、無 KI；比價日只填決定日月份與最後一期；Non-Call = 決定日的月數；情境三個都讀得到。"""
    term_sheets = [s for iis, _, _, s in documents if s is not None and not iis]
    assert len(term_sheets) == 5
    for ts in term_sheets:
        f = ts.f
        assert (f("ko_observation").value, f("ko_memory").value, f("ki_type").value) == ("P", False, "none")
        sched, months = f("autocall_schedule").value, list(f("observation_months").value)
        assert sorted(sched.dates) == [*months, f("tenor_months").value]
        assert f("first_callable_period").value == months[0]
        assert ts.sb_scenarios.found
