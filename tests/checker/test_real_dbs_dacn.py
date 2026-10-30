"""本機 DBS DACN 真實樣本（workflow #370）；交易資料不進 Git，CI 自動略過。測試碼不含真實商品代號或數值。

樣本為說明書＋投資人須知成對（`real_data.DACN`）。斷言與探勘結論一致（docs/checker/templates/dbs-zh-dacn.md §4、
docs/checker/rules/dbs-dacn-check-rules.md §5）：7 份說明書與 7 份投資人須知都是現行版、辨識成 DBS DACN；沒有一份被認成
DBS FCN。除了 Microsoft Word 輸出的那份說明書的情境價格表（表頭「彭博代碼」「期初價格」擠成一行，轉人工覆核）以外，每個欄位
都讀得到。最大利率值 = 年利率 ÷ 12 無條件進位、配息門檻 = 執行價 %、區間下限價格 = 期初 × %、區間上限都「不適用」、保證期數 =
Non-Call = 1、情境範圍 = 第 2 期～天期；投資人須知的配息門檻、價格與保證期數 = 同商品說明書。參考條件表要另外準備，整批核對的
試跑結果記在 PR。
"""

from collections import Counter
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

import fitz
import pytest

from fcn_checker.extraction import extract_lines
from fcn_checker.issuers import DBS, DBS_DACN, detect, detect_iis
from fcn_checker.parsers import dbs_dacn
from fcn_checker.rules.dbs_dacn import TITLES
from fcn_checker.schema import CheckStatus as S
from fcn_checker.schema import FieldStatus
from real_data import DACN, requires

pytestmark = [pytest.mark.real_samples, requires(DACN / "078*_TS.pdf")]
READ = (FieldStatus.PRESENT, FieldStatus.NOT_APPLICABLE)
Q4 = Decimal("0.0001")


def _lines(path):
    with fitz.open(path) as doc:
        return extract_lines(doc)


def _producer(path) -> str:
    with fitz.open(path) as doc:
        return doc.metadata.get("producer") or ""


@pytest.fixture(scope="module")
def documents():
    """每份樣本：（商品代號, 是否投資人須知, 是否 Word 輸出, 辨識到的上手, 辨識結果, 讀出結果或 None）。"""
    out = []
    for path in sorted(DACN.glob("078*.pdf")):
        iis = path.stem.endswith("_IIS")
        lines = _lines(path)
        issuer, result = (detect_iis if iis else detect)(lines)
        sheet = None
        if issuer is DBS_DACN and result.status == S.PASS:
            sheet = (DBS_DACN.iis.read if iis else DBS_DACN.read)(lines)
        out.append((path.stem[:12], iis, "Word" in _producer(path), issuer, result, sheet))
    return out


def test_samples_are_current_dbs_dacn(documents):
    seen = Counter((iis, r.actual) for _, iis, _, _, r, _ in documents)
    assert seen == Counter({(False, DBS_DACN.template_id): 7, (True, DBS_DACN.iis.template_id): 7})
    assert all(issuer is not DBS for *_, issuer, _, _ in documents), "沒有一份被認成 DBS FCN"


def test_every_field_of_the_samples_is_read(documents):
    unread = Counter(
        (iis, word, name)
        for _, iis, word, _, _, sheet in documents
        if sheet is not None
        for name, pf in sheet.fields.items()
        if pf.status not in READ
    )
    assert unread == Counter({(False, True, "scenario_table"): 1, (False, True, "scenario_reprint_table"): 1})


def test_samples_follow_the_sampled_dacn_terms(documents):
    term_sheets = [s for _, iis, _, _, _, s in documents if s is not None and not iis]
    assert len(term_sheets) == 7
    ki = Counter()
    for ts in term_sheets:
        f = ts.f
        assert (f("ko_observation").value, f("ko_memory").value, f("currency_zh").value) == ("D", True, "美元")
        assert len(f("underlyings").value) >= 3
        tenor = f("tenor_months").value
        assert f("guaranteed_periods").value == f("first_callable_period").value == 1
        assert f("coupon_barrier_pct").value == f("strike_pct").value
        assert f("monthly_coupon_pct").value == (f("coupon_pa_pct").value / 12).quantize(Q4, ROUND_CEILING)
        pct = f("coupon_barrier_pct").value
        for row in f("underlying_prices").value:
            assert row.prices["barrier"] == (row.prices["initial"] * pct / 100).quantize(Q4, ROUND_HALF_UP)
        assert set(f("range_upper").value) == {dbs_dacn.NOT_APPLICABLE}
        assert {(a, b) for _, a, b in f("scenario_ranges").value} == {(2, tenor)}
        assert f("scenario_first_period").value == 2
        kind = f("ki_type").value
        assert tuple(t for t, _ in f("scenario_titles").value) == TITLES[kind]
        ki[kind] += 1
    assert ki == Counter({"none": 4, "AM": 3})


def test_investor_sheets_agree_with_their_term_sheets(documents):
    by_code = {(code, iis): s for code, iis, _, _, _, s in documents if s is not None}
    pairs = [(by_code[(code, False)], s) for (code, iis), s in by_code.items() if iis]
    assert len(pairs) == 7
    for ts, iis in pairs:
        for name in ("coupon_barrier_pct", "ki_type", "coupon_pa_pct", "range_upper"):
            assert iis.f(name).value == ts.f(name).value, name
        assert iis.f("first_callable_period").value == ts.f("guaranteed_periods").value
        assert [r.prices for r in iis.f("underlying_prices").value] == [
            r.prices for r in ts.f("underlying_prices").value
        ]
