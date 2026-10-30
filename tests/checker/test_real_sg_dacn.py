"""本機 SG DACN 真實樣本（workflow #369）；交易資料不進 Git，CI 自動略過。測試碼不含真實商品代號或數值。

樣本為說明書＋投資人須知成對（`real_data.DACN`）。斷言與探勘結論一致（docs/checker/templates/sg-zh-dacn.md §4、
docs/checker/rules/sg-dacn-check-rules.md §5）：10 份說明書與 10 份投資人須知都是現行版、辨識成 SG DACN、每個欄位都讀得到；
沒有一份被認成 SG FCN。配息率 = 年配息率 ÷ 12（half-up 4 位）、配息門檻 = 執行價 %、保證期數 = Non-Call（1 期 9 份、2 期 1 份）、
計息範圍 = 第 g+1 期～天期、五個情境的期別與範本規格相同；投資人須知的年配息率、保證期數與計息範圍 = 同商品說明書。
參考條件表要另外準備，整批核對的試跑結果記在 PR。
"""

from collections import Counter
from decimal import ROUND_HALF_UP, Decimal

import fitz
import pytest

from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import SG, SG_DACN, detect, detect_iis
from fcn_checker.rules.sg_dacn import SCENARIOS
from fcn_checker.schema import CheckStatus as S
from fcn_checker.schema import FieldStatus
from real_data import DACN, requires

pytestmark = [pytest.mark.real_samples, requires(DACN / "037*_TS.pdf")]
READ = (FieldStatus.PRESENT, FieldStatus.NOT_APPLICABLE)


def _lines(path):
    with fitz.open(path) as doc:
        return extract_lines(doc)


@pytest.fixture(scope="module")
def documents():
    """每份樣本：（商品代號, 是否投資人須知, 辨識到的上手, 辨識結果, 讀出結果或 None）。"""
    out = []
    for path in sorted(DACN.glob("037*.pdf")):
        iis = path.stem.endswith("_IIS")
        lines = _lines(path)
        issuer, result = (detect_iis if iis else detect)(lines)
        sheet = None
        if issuer is SG_DACN and result.status == S.PASS:
            sheet = (SG_DACN.iis.read if iis else SG_DACN.read)(lines)
        out.append((path.stem[:12], iis, issuer, result, sheet))
    return out


def test_samples_are_current_sg_dacn(documents):
    seen = Counter((iis, r.actual) for _, iis, _, r, _ in documents)
    assert seen == Counter({(False, SG_DACN.template_id): 10, (True, SG_DACN.iis.template_id): 10})
    assert all(issuer is not SG for _, _, issuer, _, _ in documents), "沒有一份被認成 SG FCN"


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
    """KO 每日觀察＋記憶式、多標的、無 KI 8 份／到期觀察 2 份、美元；保證期數 = Non-Call（1 期 9 份、2 期 1 份）；配息門檻 =
    執行價 %；配息率 = 年配息率 ÷ 12；計息範圍與情境期別照範本規格。"""
    term_sheets = [s for _, iis, _, _, s in documents if s is not None and not iis]
    assert len(term_sheets) == 10
    ki, guaranteed = Counter(), Counter()
    for ts in term_sheets:
        f = ts.f
        assert (f("ko_observation").value, f("ko_memory").value, f("currency_zh").value) == ("D", True, "美元")
        assert len(f("underlyings").value) >= 3
        g, tenor = f("guaranteed_periods").value, f("tenor_months").value
        assert g == f("first_callable_period").value
        assert f("coupon_barrier_pct").value == f("strike_pct").value
        annual = f("coupon_pa_pct").value
        assert f("monthly_coupon_pct").value == (annual / 12).quantize(Decimal("0.0001"), ROUND_HALF_UP)
        assert {(a, b) for _, a, b in f("accrual_ranges").value} == {(g + 1, tenor)}
        accrual = {"none": (), "next": ((g + 1, g + 1),), "rest": ((g + 1, tenor),)}
        assert [(t.number, t.title, t.guaranteed, t.accrual) for t in f("scenario_terms").value] == [
            (n, title, ((1, g),), accrual[kind]) for n, title, kind in SCENARIOS
        ]
        ki[f("ki_type").value] += 1
        guaranteed[g] += 1
    assert ki == Counter({"none": 8, "AM": 2})
    assert guaranteed == Counter({1: 9, 2: 1})


def test_investor_sheets_agree_with_their_term_sheets(documents):
    by_code = {(code, iis): s for code, iis, _, _, s in documents if s is not None}
    pairs = [(by_code[(code, False)], s) for (code, iis), s in by_code.items() if iis]
    assert len(pairs) == 10
    for ts, iis in pairs:
        for name in ("coupon_pa_pct", "guaranteed_periods"):
            assert iis.f(name).value == ts.f(name).value, name
        assert [r[1:] for r in iis.f("accrual_ranges").value] == [r[1:] for r in ts.f("accrual_ranges").value]
        assert [o.value.value for o in iis.f("monthly_coupons").value] == [ts.f("monthly_coupon_pct").value]
