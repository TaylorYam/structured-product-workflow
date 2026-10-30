"""本機 Nomura DACN 真實樣本（workflow #368）；交易資料不進 Git，CI 自動略過。測試碼不含真實商品代號或數值。

樣本為說明書＋投資人須知成對（`real_data.DACN`）。斷言與探勘結論一致（docs/checker/templates/nomura-zh-dacn.md §4、
docs/checker/rules/nomura-dacn-check-rules.md §5）：11 份說明書與 11 份投資人須知都是現行版、辨識成 Nomura DACN、每個欄位都
讀得到；沒有一份被認成 Nomura FCN。年利率各出處相等、配息門檻 = 執行價 %、保證期數 = Non-Call = 1、情境二照公式重算相等。
參考條件表要另外準備，整批核對的試跑結果記在 PR。
"""

from collections import Counter
from decimal import ROUND_HALF_UP, Decimal

import fitz
import pytest

from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import NOMURA, NOMURA_DACN, detect, detect_iis
from fcn_checker.schema import CheckStatus as S
from fcn_checker.schema import FieldStatus
from real_data import DACN, requires

pytestmark = [pytest.mark.real_samples, requires(DACN / "156*_TS.pdf")]
READ = (FieldStatus.PRESENT, FieldStatus.NOT_APPLICABLE)


def _lines(path):
    with fitz.open(path) as doc:
        return extract_lines(doc)


@pytest.fixture(scope="module")
def documents():
    """每份樣本：（是否投資人須知, 辨識到的上手, 辨識結果, 讀出結果或 None）。"""
    out = []
    for path in sorted(DACN.glob("156*.pdf")):
        iis = path.stem.endswith("_IIS")
        lines = _lines(path)
        issuer, result = (detect_iis if iis else detect)(lines)
        sheet = None
        if issuer is NOMURA_DACN and result.status == S.PASS:
            sheet = (NOMURA_DACN.iis.read if iis else NOMURA_DACN.read)(lines)
        out.append((iis, issuer, result, sheet))
    return out


def test_samples_are_current_nomura_dacn(documents):
    seen = Counter((iis, r.actual) for iis, _, r, _ in documents)
    assert seen == Counter({(False, NOMURA_DACN.template_id): 11, (True, NOMURA_DACN.iis.template_id): 11})
    assert all(issuer is not NOMURA for _, issuer, _, _ in documents), "沒有一份被認成 Nomura FCN"


def test_every_field_of_the_samples_is_read(documents):
    unread = Counter(
        (iis, name)
        for iis, _, _, sheet in documents
        if sheet is not None
        for name, pf in sheet.fields.items()
        if pf.status not in READ
    )
    assert unread == Counter()


def test_samples_follow_the_sampled_dacn_terms(documents):
    """KO 每日觀察＋記憶式、多標的、無 KI 8 份／到期觀察 3 份、美元；保證期數 = Non-Call = 1；配息門檻 = 執行價 %。"""
    term_sheets = [s for iis, _, _, s in documents if s is not None and not iis]
    assert len(term_sheets) == 11
    ki = Counter()
    for ts in term_sheets:
        f = ts.f
        assert (f("ko_observation").value, f("ko_memory").value, f("currency_zh").value) == ("D", True, "美元")
        assert len(f("underlyings").value) >= 3
        assert f("guaranteed_periods").value == f("first_callable_period").value == 1
        assert f("coupon_barrier_pct").value == f("strike_pct").value
        ki[f("ki_type").value] += 1
    assert ki == Counter({"none": 8, "AM": 3})


def test_annual_rate_places_agree_and_scenario2_recomputes(documents):
    """年利率：第 7 項 = 第 14 項設定參數 = 情境一 = 第 15 項兩處 = 同商品投資人須知；情境二 = 公式重算（half-up 2 位）。"""
    term_sheets = [s for iis, _, _, s in documents if s is not None and not iis]
    investor = sorted(s.f("coupon_pa_pct").value for iis, _, _, s in documents if s is not None and iis)
    assert sorted(ts.f("coupon_pa_pct").value for ts in term_sheets) == investor
    for ts in term_sheets:
        f = ts.f
        annual = f("coupon_pa_pct").value
        assert f("coupon_art14").value == f("scenario1_return").value == annual
        assert [m.value for m in ts.coupon_mentions] == [annual, annual]
        t, g, p = (
            Decimal(f("tenor_months").value),
            Decimal(f("guaranteed_periods").value),
            f("accrual_scenario_pct").value,
        )
        expected = (annual * (g + p / 100 * (t - g)) / t).quantize(Decimal("0.01"), ROUND_HALF_UP)
        assert f("scenario2_return").value == expected
