"""本機 Natixis DACN 真實樣本（workflow #371）；交易資料不進 Git，CI 自動略過。測試碼不含真實商品代號或數值。

樣本為說明書＋投資人須知成對（`real_data.DACN`）。斷言與探勘結論一致（docs/checker/templates/natixis-zh-dacn.md §4、
docs/checker/rules/natixis-dacn-check-rules.md §5）：7 份說明書與 7 份投資人須知都是現行版、辨識成 Natixis DACN、該讀的欄位都讀得到；
沒有一份被認成 Natixis FCN。月配息率 = 年利率 ÷ 12（half-up 4 位）、配息門檻 = 執行價 %、保證期數 = Non-Call = 1、各情境第 1 列
保證期算式、其餘計息期算式；投資人須知配息界限價格 = 同商品說明書配息門檻。日圓那份情境配息算式的月配息率和第 13 項不同
（本機疑慮記錄），核對時判不一致，這裡只斷言讀得到。參考條件表要另外準備，整批核對的試跑結果記在 PR。
"""

from collections import Counter
from decimal import ROUND_HALF_UP, Decimal

import fitz
import pytest

from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import NATIXIS, NATIXIS_DACN, detect, detect_iis
from fcn_checker.schema import CheckStatus as S
from fcn_checker.schema import FieldStatus
from real_data import DACN, requires

pytestmark = [pytest.mark.real_samples, requires(DACN / "328*_TS.pdf")]
READ = (FieldStatus.PRESENT, FieldStatus.NOT_APPLICABLE)
NO_KI = {"ki_day_rule", "ki_def_pct"}  # 無 KI 的說明書沒有觸及事件判定日與界限價格定義（同 Natixis FCN）


def _lines(path):
    with fitz.open(path) as doc:
        return extract_lines(doc)


@pytest.fixture(scope="module")
def documents():
    """每份樣本：（商品代號, 是否投資人須知, 辨識到的上手, 辨識結果, 讀出結果或 None）。"""
    out = []
    for path in sorted(DACN.glob("328*.pdf")):
        iis = path.stem.endswith("_IIS")
        lines = _lines(path)
        issuer, result = (detect_iis if iis else detect)(lines)
        sheet = None
        if issuer is NATIXIS_DACN and result.status == S.PASS:
            sheet = (NATIXIS_DACN.iis.read if iis else NATIXIS_DACN.read)(lines)
        out.append((path.stem[:12], iis, issuer, result, sheet))
    return out


def test_samples_are_current_natixis_dacn(documents):
    seen = Counter((iis, r.actual) for _, iis, _, r, _ in documents)
    assert seen == Counter({(False, NATIXIS_DACN.template_id): 7, (True, NATIXIS_DACN.iis.template_id): 7})
    assert all(issuer is not NATIXIS for _, _, issuer, _, _ in documents), "沒有一份被認成 Natixis FCN"


def test_every_field_of_the_samples_is_read(documents):
    unread = Counter(
        (iis, name)
        for _, iis, _, _, sheet in documents
        if sheet is not None
        for name, pf in sheet.fields.items()
        if pf.status not in READ and not (sheet.f("ki_type").value == "none" and name in NO_KI)
    )
    assert unread == Counter()


def test_samples_follow_the_sampled_dacn_terms(documents):
    """KO 每日觀察＋記憶式、多標的、KI 到期 1 份／每日 5 份／無 1 份、美元 6 份／日圓 1 份；保證期數 = Non-Call = 1；
    配息門檻 = 執行價 %；月配息率 = 年利率 ÷ 12；各情境第 1 列保證期算式、其餘計息期；情境一年化 = 年利率。"""
    term_sheets = [s for _, iis, _, _, s in documents if s is not None and not iis]
    assert len(term_sheets) == 7
    ki, currency = Counter(), Counter()
    for ts in term_sheets:
        f = ts.f
        assert (f("ko_observation").value, f("ko_memory").value) == ("D", True)
        assert len(f("underlyings").value) >= 3
        assert f("guaranteed_periods").value == f("first_callable_period").value == 1
        assert f("coupon_barrier_pct").value == f("strike_pct").value
        annual = f("coupon_pa_pct").value
        assert f("monthly_coupon_pct").value == (annual / 12).quantize(Decimal("0.0001"), ROUND_HALF_UP)
        tenor = f("tenor_months").value
        rows = f("scenario_coupon_rows").value
        assert [r.kinds for r in rows] == [("固定",)] + [("固定",) + ("變動",) * (tenor - 1)] * (len(rows) - 1)
        annualized = [p.value.value for p in f("scenario_params").value if p.code == "scenario_annualized_return"]
        assert annualized == [annual, annual]
        ki[f("ki_type").value] += 1
        currency[f("currency_zh").value] += 1
    assert ki == Counter({"D": 5, "AM": 1, "none": 1})
    assert currency == Counter({"美元": 6, "日圓": 1})


def test_investor_sheets_agree_with_their_term_sheets(documents):
    by_code = {(code, iis): s for code, iis, _, _, s in documents if s is not None}
    pairs = [(by_code[(code, False)], s) for (code, iis), s in by_code.items() if iis]
    assert len(pairs) == 7
    for ts, iis in pairs:
        for name in ("coupon_pa_pct", "coupon_barrier_pct", "issue_price_pct"):
            assert iis.f(name).value == ts.f(name).value, name
        assert [o.value.value for o in iis.f("monthly_coupons").value] == [ts.f("monthly_coupon_pct").value]
