"""Natixis DACN 中文投資人須知（docs/checker/templates/natixis-zh-dacn-iis.md）：版面、相關機構、商品簡介、KO／KI 句型、費用表同
Natixis FCN 投資人須知（parsers/natixis_iis.py），先照它讀出，再另讀配息界限價格（配息門檻）與發行價格（列項符號也可能是「●」）。

依貳、商品事項的「變動配息金額」公式與 n 定義辨識商品家族；只支援現行版範本：認得出是 Natixis DACN、但現行版特徵不成立時辨識
交出「不支援的範本版本」（範本規格 §3）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import Decimal

from ..schema import DetectionResult, Line
from ..text import squash
from . import iis, natixis, natixis_dacn, natixis_iis

TEMPLATE_ID = "natixis-zh-dacn-iis"
PARSER_VERSION = "1"
OLD_VERSION = "Natixis DACN 投資人須知的寫法和現行版不同"
_FIXED = re.escape("「固定配息金額」=配息觀察期間結束日持有之商品單位數x每單位商品面額x月配息率")
_N_TOTAL = re.escape("N係指相關配息觀察期間內的總配息觀察日天數；n係指該相關配息觀察期間內截至")
BARRIER = r"「配息界限價格」係指就任一連結標的而言，各該連結標的之期初進場價格之" + natixis.PCT
# 範本規格 §1.1 的八段固定寫法（去空白後依序出現；和說明書第 13 項的寫法不同，各自比對）
WORDINGS = (
    ("貳 2. (a) 固定配息金額", _FIXED),
    (
        "貳 2. (a) 變動配息金額",
        re.escape("「變動配息金額」=配息觀察期間結束日持有之商品單位數x每單位商品面額x月配息率x(n/N)"),
    ),
    (
        "貳 2. (a) n、N 定義",
        _N_TOTAL + re.escape("該配息觀察期間結束日(含)，所有連結標的收盤價皆高於或等於配息界限價格之配息觀察日天數。"),
    ),
    ("貳 2. (b) 固定配息金額", re.escape("(b)若該配息觀察期間發生自動提前到期事件：") + ".*?" + _FIXED),
    (
        "貳 2. (b) 變動配息金額",
        re.escape(
            "「變動配息金額」=發生自動提前到期事件之自動提前到期觀察日持有之商品單位數x每單位商品面額x月配息率x(n/N)"
        ),
    ),
    (
        "貳 2. (b) n、N 定義",
        "其中："
        + _N_TOTAL
        + re.escape(
            "自動提前到期事件發生之自動提前到期觀察日(含)，所有連結標的收盤價皆高於或等於配息界限價格之配息觀察日天數。"
        ),
    ),
    ("貳 2. 配息觀察日定義", re.escape("「配息觀察日」係指於配息觀察期間之各預定交易日，並依「中斷日」進行調整。")),
    ("貳 2. 配息界限價格定義", BARRIER),
)


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §3）：Natixis 投資人須知骨架（`natixis_iis.skeleton`）與區間計息（貳、商品事項有「變動配息金額」
    公式與 n 定義）都成立後，現行版特徵全部成立才是本範本。"""
    sk = natixis_iis.skeleton(lines)
    product = natixis_iis.product_text(sk.text)
    failed = list(sk.failed)
    if not natixis_dacn.is_accrual(product):
        family = natixis_dacn.FAMILY
        failed.append(f"貳、商品事項沒有區間計息的「{family[0]}」公式與「{family[1]}」定義")
    if failed:
        return DetectionResult(False, failed, sk.evidence)
    missing, at = [], 0
    for where, pattern in WORDINGS:
        m = re.compile(pattern).search(product, at)
        if m is None:
            missing.append(f"{where}的寫法和現行版不同")
        else:
            at = m.end()
    if len(re.findall(BARRIER, product)) != 1:
        missing.append("貳 2.「配息界限價格」定義不是剛好一處")
    if natixis_dacn.NAME_ZH_MARK not in sk.zh:
        missing.append(f"中文名稱沒有「{natixis_dacn.NAME_ZH_MARK}」")
    if not (sk.en.startswith("Natixis–") and squash(natixis_dacn.NAME_EN_MARK) in sk.en):
        missing.append(f"英文名稱沒有「Natixis –」與「{natixis_dacn.NAME_EN_MARK}」")
    if missing:
        return DetectionResult(False, missing, sk.evidence, unsupported=f"{OLD_VERSION}：{'；'.join(missing)}")
    return DetectionResult(True, [], sk.evidence)


def read(lines: Sequence[Line]) -> iis.IisSheet:
    """先照 Natixis FCN 投資人須知讀出，再改讀發行價格（列項符號 `•`／`‧`／`●`），另交配息界限價格 %（配息門檻）。"""
    base = natixis_iis.read(lines)
    ti = base.full_text
    fields = dict(base.fields)
    fields["issue_price_pct"] = iis.capture("issue_price_pct", ti, r"[•‧●]發行價格：" + natixis.PCT, Decimal)
    fields["coupon_barrier_pct"] = iis.capture("coupon_barrier_pct", ti, BARRIER, Decimal)
    return iis.IisSheet(fields, ti, base.provided)
