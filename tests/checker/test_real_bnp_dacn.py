"""本機 BNP DACN 真實樣本（workflow #548）；交易資料不進 Git，CI 自動略過。測試碼不含真實商品代號或數值。

樣本為說明書＋投資人須知成對（`real_data.DACN`）。斷言與探勘結論一致（docs/checker/templates/bnp-zh-dacn.md §4、
docs/checker/rules/bnp-dacn-check-rules.md §5）：3 份說明書與 3 份投資人須知都是現行版、辨識成 BNP DACN；沒有一份被認成
BNP FCN 或 Super Bull；每個欄位都讀得到。全部美元、多標的、KO 每日＋記憶式、配息公式保證期數 = Non-Call = 1、配息門檻 =
執行價 %、定界價格 = 期初價格 × %、§17 最低配息率 = 月配息率，KI 每日、每期、到期各一份；自動提前贖回情況以外的情境都讀到
區間計息算式。投資人須知的月配息率、保證期數、配息門檻與價格 = 同商品說明書。參考條件表要另外準備，整批核對的試跑結果記在 PR。
"""

from collections import Counter
from decimal import ROUND_HALF_UP, Decimal

import fitz
import pytest

from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import BNP_DACN, detect, detect_iis
from fcn_checker.schema import CheckStatus as S
from fcn_checker.schema import FieldStatus
from real_data import DACN, requires

pytestmark = [pytest.mark.real_samples, requires(DACN / "082*_TS.pdf")]
READ = (FieldStatus.PRESENT, FieldStatus.NOT_APPLICABLE)
Q4 = Decimal("0.0001")


def _lines(path):
    with fitz.open(path) as doc:
        return extract_lines(doc)


@pytest.fixture(scope="module")
def documents():
    """每份樣本：（商品代號, 是否投資人須知, 辨識到的範本, 辨識結果, 讀出結果或 None）。"""
    out = []
    for path in sorted(DACN.glob("082*.pdf")):
        iis = path.stem.endswith("_IIS")
        lines = _lines(path)
        template, result = (detect_iis if iis else detect)(lines)
        sheet = None
        if template is BNP_DACN and result.status == S.PASS:
            sheet = (BNP_DACN.iis.read if iis else BNP_DACN.read)(lines)
        out.append((path.stem[:12], iis, template, result, sheet))
    return out


def test_samples_are_current_bnp_dacn(documents):
    seen = Counter((iis, r.actual) for _, iis, _, r, _ in documents)
    assert seen == Counter({(False, BNP_DACN.template_id): 3, (True, BNP_DACN.iis.template_id): 3})
    assert all(template is BNP_DACN for _, _, template, _, _ in documents), "沒有一份被認成 BNP FCN 或 Super Bull"


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
    assert len(term_sheets) == 3
    ki = Counter()
    for ts in term_sheets:
        f = ts.f
        assert (f("ko_observation").value, f("ko_memory").value, f("currency_zh").value) == ("D", True, "美元")
        assert len(f("underlyings").value) >= 3
        assert f("coupon_guaranteed_periods").value == f("first_callable_period").value == 1
        assert f("min_coupon_rate").value == f("monthly_coupon_pct").value
        pct = f("coupon_barrier_pct").value
        assert pct == f("strike_pct").value
        for row in f("underlying_prices").value:
            assert row.prices["barrier"] == (row.prices["initial"] * pct / 100).quantize(Q4, ROUND_HALF_UP)
        for s in ts.scenarios.scenarios:
            assert (s.accrual is not None) == (s.kind in ("best", "worse", "worst")), s.title
        ki[f("ki_type").value] += 1
    assert ki == Counter({"D": 1, "P": 1, "AM": 1})


def test_investor_sheets_agree_with_their_term_sheets(documents):
    by_code = {(code, iis): s for code, iis, _, _, s in documents if s is not None}
    pairs = [(by_code[(code, False)], s) for (code, iis), s in by_code.items() if iis]
    assert len(pairs) == 3
    for ts, iis in pairs:
        assert iis.f("guaranteed_periods").value == ts.f("coupon_guaranteed_periods").value
        assert iis.f("monthly_coupon_shown_pct").value == ts.f("monthly_coupon_pct").value
        assert iis.f("coupon_barrier_pct").value == ts.f("coupon_barrier_pct").value
        assert [r.prices["barrier"] for r in iis.f("underlying_prices").value] == [
            r.prices["barrier"] for r in ts.f("underlying_prices").value
        ]
