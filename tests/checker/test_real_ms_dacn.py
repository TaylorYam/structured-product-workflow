"""本機 MS DACN 真實樣本（workflow #550、#578）；交易資料不進 Git，CI 自動略過。測試碼不含真實商品代號或數值。

樣本為說明書＋投資人須知成對（`real_data.DACN`）。斷言與探勘結論一致（docs/checker/templates/ms-zh-dacn.md §4、
docs/checker/rules/ms-dacn-check-rules.md §5）：2 份說明書與 2 份投資人須知都是現行版、辨識成 MS DACN；沒有一份被認成
MS FCN 或 BEN；每個欄位都讀得到。全部美元、多標的、KO 每日＋記憶式、KI 到期觀察、第 15 項保證期數 = Non-Call = 1、
第 18 項每個情境的保證期數相同、配息門檻 = 執行價 %；有區間計息算式的情境不交年化報酬率，最後一步的區間計息算式與損益
算式都讀得出來（#578）。投資人須知的保證期數 = 同商品
說明書。參考條件表要另外準備，整批核對的試跑結果記在 PR。
"""

from collections import Counter

import fitz
import pytest

from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import MS_DACN, detect, detect_iis
from fcn_checker.schema import CheckStatus as S
from fcn_checker.schema import FieldStatus
from real_data import DACN, requires

pytestmark = [pytest.mark.real_samples, requires(DACN / "147*_TS.pdf")]
READ = (FieldStatus.PRESENT, FieldStatus.NOT_APPLICABLE)


def _lines(path):
    with fitz.open(path) as doc:
        return extract_lines(doc)


@pytest.fixture(scope="module")
def documents():
    """每份樣本：（商品代號, 是否投資人須知, 辨識到的範本, 辨識結果, 讀出結果或 None）。"""
    out = []
    for path in sorted(DACN.glob("147*.pdf")):
        iis = path.stem.endswith("_IIS")
        lines = _lines(path)
        template, result = (detect_iis if iis else detect)(lines)
        sheet = None
        if template is MS_DACN and result.status == S.PASS:
            sheet = (MS_DACN.iis.read if iis else MS_DACN.read)(lines)
        out.append((path.stem[:12], iis, template, result, sheet))
    return out


def test_samples_are_current_ms_dacn(documents):
    seen = Counter((iis, r.actual) for _, iis, _, r, _ in documents)
    assert seen == Counter({(False, MS_DACN.template_id): 2, (True, MS_DACN.iis.template_id): 2})
    assert all(template is MS_DACN for _, _, template, _, _ in documents), "沒有一份被認成 MS FCN 或 BEN"


def test_every_field_of_the_samples_is_read(documents):
    unread = Counter(
        (iis, name)
        for _, iis, _, _, sheet in documents
        if sheet is not None
        for name, pf in sheet.fields.items()
        if pf.status not in READ
    )
    assert unread == Counter()


def test_samples_follow_the_sampled_dacn_terms(documents):
    term_sheets = [s for _, iis, _, _, s in documents if s is not None and not iis]
    assert len(term_sheets) == 2
    for ts in term_sheets:
        f = ts.f
        assert (f("ko_observation").value, f("ki_type").value, f("currency_zh").value) == ("D", "AM", "美元")
        assert f("ko_memory").value is True
        assert len(f("underlyings").value) >= 2
        assert f("coupon_guaranteed_periods").value == f("first_callable_period").value == 1
        assert f("coupon_barrier_pct").value == f("strike_pct").value
        scenarios = [s for s in ts.scenarios.scenarios if s.kind != "default"]
        assert [h.value for s in scenarios for h in s.guaranteed] == [1] * len(scenarios)
        # 情境一沒有區間計息；其他情境都有，且區間計息算式以外的每期配息算式都讀得出來
        assert [bool(s.accrual) for s in scenarios] == [False] + [True] * (len(scenarios) - 1)
        assert all(len(s.coupons) == s.coupon_formulas - s.accrual_formulas for s in scenarios)
        assert len([s for s in scenarios if s.kind == "worse" and len(s.strike) == 1]) == 1
        # 有區間計息的情境（#578）：最後一步的區間計息算式全部讀得出、M 不是 0，每個情境一個區間計息金額與一個損益算式，
        # 獲利情境讀得到假設的配息次數
        for s in scenarios[1:]:
            assert s.accruals and len(s.accruals) == s.accrual_formulas
            assert all(h.value.total_days for h in s.accruals)
            assert len({h.value.amount for h in s.accruals}) == 1 and len(s.pnl_sums) == 1
            assert s.kind == "worse" or s.paid is not None


def test_investor_sheets_agree_with_their_term_sheets(documents):
    by_code = {(code, iis): s for code, iis, _, _, s in documents if s is not None}
    pairs = [(by_code[(code, False)], s) for (code, iis), s in by_code.items() if iis]
    assert len(pairs) == 2
    for ts, iis in pairs:
        assert iis.f("guaranteed_periods").value == ts.f("coupon_guaranteed_periods").value
        assert iis.f("name_zh").value.replace(" ", "") == ts.f("name_zh").value.replace(" ", "")
