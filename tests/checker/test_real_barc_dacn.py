"""本機 Barclays DACN 真實樣本（workflow #547）；交易資料不進 Git，CI 自動略過。測試碼不含真實商品代號或數值。

樣本為說明書＋投資人須知成對（`real_data.DACN`）。斷言與探勘結論一致（docs/checker/templates/barc-zh-dacn.md §4、
docs/checker/rules/barc-dacn-check-rules.md §5）：5 份說明書與 5 份投資人須知都是現行版、辨識成 BARC DACN；沒有一份被認成
BARC FCN。讀不到的欄位和 BARC FCN 相同（說明書費用表沒有的費用項目、沒有 KI 的 KI %）。全部美元、KO 每日＋記憶式、配息公式
保證期數 = §13(7) 保證配息期 = Non-Call = 1、配息門檻 = 執行價 %、配息門檻價格 = 最初價格 × %、§14 配息表期數 = 天期；投資人須知
的月配息率、配息門檻、價格與名稱 = 同商品說明書（長名稱折三行的列也一樣）；§16 一般、最差情況的算式都讀得到（workflow #577）。
參考條件表要另外準備，整批核對的試跑結果記在 PR。
"""

from collections import Counter
from decimal import ROUND_HALF_UP, Decimal

import fitz
import pytest

from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import BARC, BARC_DACN, detect, detect_iis
from fcn_checker.schema import CheckStatus as S
from fcn_checker.schema import FieldStatus
from fcn_checker.text import squash
from real_data import DACN, requires

pytestmark = [pytest.mark.real_samples, requires(DACN / "029*_TS.pdf")]
READ = (FieldStatus.PRESENT, FieldStatus.NOT_APPLICABLE)
Q4 = Decimal("0.0001")
FEES = (
    "fee_管理費用",
    "fee_保費費用",
    "fee_解約費用",
    "fee_其他費用",
)  # BARC FCN 說明書同樣讀不到（費用表沒有這些項目）


def _lines(path):
    with fitz.open(path) as doc:
        return extract_lines(doc)


@pytest.fixture(scope="module")
def documents():
    """每份樣本：（商品代號, 是否投資人須知, 辨識到的上手, 辨識結果, 讀出結果或 None）。"""
    out = []
    for path in sorted(DACN.glob("029*.pdf")):
        iis = path.stem.endswith("_IIS")
        lines = _lines(path)
        issuer, result = (detect_iis if iis else detect)(lines)
        sheet = None
        if issuer is BARC_DACN and result.status == S.PASS:
            sheet = (BARC_DACN.iis.read if iis else BARC_DACN.read)(lines)
        out.append((path.stem[:12], iis, issuer, result, sheet))
    return out


def test_samples_are_current_barc_dacn(documents):
    seen = Counter((iis, r.actual) for _, iis, _, r, _ in documents)
    assert seen == Counter({(False, BARC_DACN.template_id): 5, (True, BARC_DACN.iis.template_id): 5})
    assert all(issuer is not BARC for _, _, issuer, _, _ in documents), "沒有一份被認成 BARC FCN"


def test_every_field_of_the_samples_is_read(documents):
    unread = Counter(
        (iis, name)
        for _, iis, _, _, sheet in documents
        if sheet is not None
        for name, pf in sheet.fields.items()
        if pf.status not in READ
    )
    assert unread == Counter({**{(False, fee): 5 for fee in FEES}, (False, "ki_pct"): 3})


def test_samples_follow_the_sampled_dacn_terms(documents):
    term_sheets = [s for _, iis, _, _, s in documents if s is not None and not iis]
    assert len(term_sheets) == 5
    ki = Counter()
    for ts in term_sheets:
        f = ts.f
        assert (f("ko_observation").value, f("ko_memory").value, f("currency_zh").value) == ("D", True, "美元")
        assert len(f("underlyings").value) >= 3
        assert f("coupon_guaranteed_periods").value == f("guaranteed_periods").value == f("first_callable_period").value
        assert f("coupon_guaranteed_periods").value == 1
        assert f("coupon_pa_pct").status == FieldStatus.NOT_APPLICABLE
        assert len(f("coupon_table").value.rows) == f("tenor_months").value
        assert f("coupon_barrier_pct").value == f("strike_pct").value
        pct = f("coupon_barrier_pct").value
        assert f("price_table").value.pcts["barrier"] == pct
        for row in f("underlying_prices").value:
            assert row.prices["barrier"] == (row.prices["initial"] * pct / 100).quantize(Q4, ROUND_HALF_UP)
        assert "barrier" not in f("main_price_table").value.table.columns
        ki[f("ki_type").value] += 1
    assert ki == Counter({"none": 3, "AM": 2})


def test_investor_sheets_agree_with_their_term_sheets(documents):
    by_code = {(code, iis): s for code, iis, _, _, s in documents if s is not None}
    pairs = [(by_code[(code, False)], s) for (code, iis), s in by_code.items() if iis]
    assert len(pairs) == 5
    for ts, iis in pairs:
        for name in ("monthly_coupon_pct", "coupon_barrier_pct", "strike_pct"):
            assert iis.f(name).value == ts.f(name).value, name
        assert [r.prices for r in iis.f("underlying_prices").value] == [
            r.prices for r in ts.f("underlying_prices").value
        ]
        assert iis.f("underlying_names").value == [squash(n) for n in ts.f("price_table").value.names]


def test_scenario_calculations_are_read(documents):
    """§16 一般、最差情況的兩行配息算式、累積、最終交割金額（等值）與總報酬率各讀到剛好一處（workflow #577）；最差情況
    累積第二段寫成不帶期數的 0（範本規格 §4.3）。驗算結果記在 PR 的試跑。"""
    term_sheets = [s for _, iis, _, _, s in documents if s is not None and not iis]
    assert len(term_sheets) == 5
    for ts in term_sheets:
        for name in ("scenario_general_calc", "scenario_worst_calc"):
            calc = ts.f(name).value
            assert None not in (calc.coupon, calc.accrual, calc.cumulative, calc.final, calc.total_return), name
            assert calc.accrual.value.total_days > 0
        legs = ts.f("scenario_worst_calc").value.cumulative.value.legs
        assert len(legs) == 2 and legs[1].periods is None and legs[1].amount == 0
