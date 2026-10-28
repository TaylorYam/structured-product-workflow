"""本機 CA Super Bull 真實樣本（Issue #312）；交易資料不進 Git，CI 自動略過。測試碼不含真實商品代號或數值。

樣本為說明書＋投資人須知成對（`real_data.SUPER_BULL`，和 BNP 放同一個資料夾）。斷言與探勘結論一致
（docs/checker/templates/ca-zh-super-bull.md §1、ca-zh-super-bull-iis.md §1）：現行版兩份都辨識成 CA Super Bull、每個欄位
都讀得到；舊版認得是 Super Bull 舊版、轉人工（不支援的範本版本），兩份舊版投資人須知有錯字、認不出範本；兩份投資人須知
是現行版寫法、但同商品說明書是舊版（批量核對時照說明書轉人工）；沒有一份被認成 CA FCN。參考條件表要另外準備，整批核對的試跑結果記在 PR。
"""

from collections import Counter

import fitz
import pytest

from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import CA, CA_SUPER_BULL, detect, detect_iis
from fcn_checker.schema import CheckStatus as S
from fcn_checker.schema import FieldStatus
from real_data import SUPER_BULL, requires

pytestmark = [pytest.mark.real_samples, requires(SUPER_BULL / "086*_TS.pdf")]
READ = (FieldStatus.PRESENT, FieldStatus.NOT_APPLICABLE)


def _lines(path):
    with fitz.open(path) as doc:
        return extract_lines(doc)


@pytest.fixture(scope="module")
def documents():
    """每份樣本：（是否投資人須知, 辨識到的上手, 辨識結果, 讀出結果或 None）。"""
    out = []
    for path in sorted(SUPER_BULL.glob("086*.pdf")):
        iis = path.stem.endswith("_IIS")
        lines = _lines(path)
        issuer, result = (detect_iis if iis else detect)(lines)
        sheet = None
        if issuer is CA_SUPER_BULL and result.status == S.PASS:
            sheet = (CA_SUPER_BULL.iis.read if iis else CA_SUPER_BULL.read)(lines)
        out.append((iis, issuer, result, sheet))
    return out


def test_current_samples_are_super_bull_and_old_ones_are_unsupported(documents):
    seen = Counter(
        (iis, issuer.code if issuer else None, result.status, result.reason_code)
        for iis, issuer, result, _ in documents
    )
    assert seen == Counter(
        {
            (False, "CA", S.PASS, ""): 32,
            (True, "CA", S.PASS, ""): 34,  # 另兩份投資人須知是現行版寫法、說明書是舊版（範本規格 §1）
            (False, None, S.REVIEW_REQUIRED, "template_unsupported"): 9,
            (True, None, S.REVIEW_REQUIRED, "template_unsupported"): 5,
            (True, None, S.REVIEW_REQUIRED, "template_unknown"): 2,
        }
    )
    assert all(issuer is not CA for _, issuer, _, _ in documents), "沒有一份被認成 CA FCN"
    assert all("舊版" in r.message for _, _, r, _ in documents if r.reason_code == "template_unsupported")


def test_every_field_of_the_current_samples_is_read(documents):
    unread = Counter(
        (iis, name)
        for iis, _, _, sheet in documents
        if sheet is not None
        for name, pf in sheet.fields.items()
        if pf.status not in READ
    )
    assert unread == Counter()


def test_current_samples_follow_the_super_bull_terms(documents):
    """每期觀察、非記憶式、無 KI；比價日只填觀察日月份與最後一期；Non-Call = 第一個觀察日的月數。"""
    term_sheets = [s for iis, _, _, s in documents if s is not None and not iis]
    assert term_sheets
    for ts in term_sheets:
        f = ts.f
        assert (f("ko_observation").value, f("ko_memory").value, f("ki_type").value) == ("P", False, "none")
        sched, months = f("autocall_schedule").value, list(f("observation_months").value)
        assert sorted(sched.dates) == [*months, f("tenor_months").value]
        assert f("first_callable_period").value == months[0]
