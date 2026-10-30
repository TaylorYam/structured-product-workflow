"""SG DACN 中文投資人須知（docs/checker/templates/sg-zh-dacn-iis.md）：版面、相關機構、商品簡介與價格表、KO／KI 句型、費用表同
SG FCN 投資人須知（parsers/sg_iis.py），先照它讀出，再改讀封面英文名稱與「收益分配事項」的區間累積配息公式、配息率與年配息率。

依收益分配事項的「×配息率×[n(i)/N(i)]」辨識商品家族；只支援現行版範本：認得出是 SG DACN、但現行版特徵不成立時辨識交出
「不支援的範本版本」（範本規格 §3）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import Decimal

from .. import standard_fields
from ..schema import DetectionResult, Line, ParsedField
from ..text import squash
from . import iis, sg_dacn, sg_iis
from .layout import TextIndex

TEMPLATE_ID = "sg-zh-dacn-iis"
PARSER_VERSION = "1"
OLD_VERSION = "SG DACN 投資人須知的寫法和現行版不同"
NAME_EN_TAIL = squash(sg_dacn.NAME_EN_TAIL)
# 範本規格 §1、§1.1：配息公式兩行（保證期 `第1個`／`第1~g個`、計息期 `第a~T個`）與 n(i)、N(i)、配息週期四段（和說明書寫法不同）
FORMULA = r"■第1(?:~(\d+))?個配息金額=每單位面額×配息率第(\d+)~(\d+)個配息金額=每單位面額×配息率×\[n\(i\)/N\(i\)\]"
ACCRUED = r"n\(i\)\(i=(\d+)-(\d+)\)=" + re.escape(
    "於第i個配息週期內，所有連結標的之收盤價皆大於或等於其執行價的預定交易日（應該視為評價日並受限於“連結標的中斷日之後果”條款）總日數"
)
KO_PERIOD = re.escape("若某一個配息週期發生自動提前出場事件，該配息週期之n(i)僅計算至該自動提前出場事件之日（含）")
WORDINGS = (
    ("收益分配事項配息公式", FORMULA),
    ("收益分配事項 n(i) 定義", ACCRUED),
    ("收益分配事項自動提前出場那一期的 n(i) 句", KO_PERIOD),
    ("收益分配事項 N(i) 定義", sg_dacn.TOTAL),
    ("收益分配事項配息週期定義", sg_dacn.PERIOD),
)
# 收益分配事項沒有「於配息日，發行機構將依…給付」那一行（配息公式直接接在「■」後），配息日的給付幣別不在投資人須知
NO_COUPON_CURRENCY = "payment_currency_coupon"


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §3）：SG 投資人須知骨架（`sg_iis.skeleton`）與區間累積配息（收益分配事項有「×配息率×[n(i)/N(i)]」）
    都成立後，現行版特徵全部成立才是本範本。"""
    ti, failed, evidence = sg_iis.skeleton(lines)
    payout = sg_iis.payout_text(ti)
    if sg_dacn.FAMILY not in payout:
        failed.append(f"收益分配事項沒有區間累積配息的「{sg_dacn.FAMILY}」")
    if failed:
        return DetectionResult(False, failed, evidence)
    missing, at = [], 0
    for where, pattern in WORDINGS:
        m = re.compile(pattern).search(payout, at)
        if m is None:
            missing.append(f"{where}和現行版不同")
        else:
            at = m.end()
    if len(re.findall(sg_dacn.RATE, payout)) != 1:
        missing.append("收益分配事項「配息率 = X%（相當於年配息率Y%）」不是剛好一處")
    names = re.match(sg_iis.names_pattern(NAME_EN_TAIL), ti.text)
    if names is None or sg_dacn.NAME_ZH_MARK not in names[0]:
        missing.append(
            f"封面名稱不是「SG ISSUER …{sg_dacn.NAME_ZH_MARK}…（不保本及無擔保）」與「SG ISSUER … {sg_dacn.NAME_EN_TAIL}」"
        )
    if missing:
        return DetectionResult(False, missing, evidence, unsupported=f"{OLD_VERSION}：{'；'.join(missing)}")
    return DetectionResult(True, [], evidence)


def read(lines: Sequence[Line]) -> iis.IisSheet:
    """先照 SG FCN 投資人須知讀出，再改讀英文名稱、年配息率與配息率，另交保證期數與計息範圍。"""
    base = sg_iis.read(lines)
    ti = base.full_text
    fields = dict(base.fields)
    fields["name_en"] = iis.capture(
        "name_en", ti, re.escape(sg_iis.NAME_ZH_TAIL) + r"(SGISSUER.+?" + re.escape(NAME_EN_TAIL) + ")商品種類："
    )
    sec = iis.section(ti, sg_iis.PAYOUT_SECTION)
    st = TextIndex(sec) if sec is not None else None
    if st is None:
        note = "找不到「收益分配事項，舉例如下：」到「贖回價金之計算：」段落"
        for name in ("coupon_pa_pct", "guaranteed_periods", "accrual_ranges"):
            fields[name] = ParsedField.missing(name, note)
    else:
        fields["coupon_pa_pct"] = iis.capture("coupon_pa_pct", st, sg_dacn.ANNUAL, Decimal)
        fields["guaranteed_periods"], fields["accrual_ranges"] = _periods(st)
    fields["monthly_coupons"] = iis.occurrences(
        "monthly_coupons",
        ti,
        (("monthly_coupon_rate", "monthly_coupon", "收益分配事項配息率", sg_dacn.MONTHLY),),
        iis.number,
    )
    currency = fields["currency_others"]
    if currency.ok:
        kept = [o for o in currency.value if o.field != NO_COUPON_CURRENCY]
        fields["currency_others"] = standard_fields.occurrences("currency_others", kept)
    return iis.IisSheet(fields, ti, base.provided)


def _periods(st: TextIndex) -> tuple[ParsedField, ParsedField]:
    """配息公式第一行的保證期數；配息公式第二行、n(i)、N(i) 的期別範圍（形狀同說明書 `accrual_ranges`）。"""
    formula = list(st.finditer(FORMULA))
    if len(formula) != 1:
        note = "收益分配事項配息公式（第1~g個配息金額、第a~T個配息金額）缺漏、重複或不在範本規格內"
        return ParsedField.missing("guaranteed_periods", note), ParsedField.missing("accrual_ranges", note)
    m = formula[0]
    lns = st.lines_for(m.start(), m.end())
    guaranteed = ParsedField.present("guaranteed_periods", int(m[1]) if m[1] else 1, lns)
    ranges, evidence = [("收益分配事項配息公式第二行", int(m[2]), int(m[3]))], list(lns)
    for where, pattern in (("收益分配事項 n(i)", ACCRUED), ("收益分配事項 N(i)", sg_dacn.TOTAL)):
        found = list(st.finditer(pattern))
        if len(found) != 1:
            return guaranteed, ParsedField.missing("accrual_ranges", f"{where}定義缺漏、重複或不在範本規格內")
        x = found[0]
        ranges.append((where, int(x[1]), int(x[2])))
        evidence += st.lines_for(x.start(), x.end())
    return guaranteed, ParsedField.present("accrual_ranges", tuple(ranges), evidence)
