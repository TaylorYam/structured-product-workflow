"""Nomura DACN 中文投資人須知（docs/checker/templates/nomura-zh-dacn-iis.md）：版面、相關機構、商品簡介、KO／KI 句型、費用表同
Nomura FCN 投資人須知（parsers/nomura_iis.py），先照它讀出，再改讀「第三 收益分配事項」的每日計息配息公式與年化配息率。

依收益分配事項的「×實際日數/總日數」辨識商品家族；只支援現行版範本：認得出是 Nomura DACN、但現行版特徵不成立時辨識交出
「不支援的範本版本」（範本規格 §3）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import Decimal

from .. import standard_fields
from ..schema import DetectionResult, Line, ParsedField
from . import iis, nomura, nomura_dacn, nomura_iis
from .layout import TextIndex

TEMPLATE_ID = "nomura-zh-dacn-iis"
PARSER_VERSION = "1"
OLD_VERSION = "Nomura DACN 投資人須知的寫法和現行版不同"
ISSUED_BY = f"issuedby{nomura_iis.ISSUER}(unsecured)"
PROVIDED = (nomura_iis.PROVIDED - {"coupon_per_period_pct"}) | {"coupon_pa_pct"}


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §3）：Nomura 投資人須知骨架（條件 1）與每日計息（條件 2）都成立後，現行版特徵全部成立才是本範本。"""
    ti, zh, en, failed, evidence = nomura_iis.skeleton(lines)
    coupon = nomura_iis.coupon_text(ti)
    if nomura_iis.ACCRUAL not in coupon:
        failed.append(f"收益分配事項沒有每日計息的「{nomura_iis.ACCRUAL}」")
    if failed:
        return DetectionResult(False, failed, evidence)
    missing = []
    if nomura_dacn.FORMULA not in coupon:
        missing.append("收益分配事項的配息公式 (i)(ii) 和現行版不同")
    if len(re.findall(nomura_dacn.ANNUAL, coupon)) != 1:
        missing.append("收益分配事項「年化配息率：X%」不是剛好一處")
    if not ("AutocallDailyRangeAccrualNotes" in en and en.endswith(ISSUED_BY)):
        missing.append(
            f"封面英文名稱沒有「Autocall Daily Range Accrual Notes」或不是以「issued by {nomura.ISSUER_EN} (unsecured)」結尾"
        )
    if nomura_dacn.NAME_ZH_MARK not in zh:
        missing.append(f"封面中文名稱沒有「{nomura_dacn.NAME_ZH_MARK}」")
    if missing:
        return DetectionResult(False, missing, evidence, unsupported=f"{OLD_VERSION}：{'；'.join(missing)}")
    return DetectionResult(True, [], evidence)


def read(lines: Sequence[Line]) -> iis.IisSheet:
    """先照 Nomura FCN 投資人須知讀出，再改讀年化配息率（沒有每期配息率）。"""
    base = nomura_iis.read(lines)
    fields = dict(base.fields)
    sec = iis.section(base.full_text, nomura_iis.COUPON)
    fields["coupon_per_period_pct"] = standard_fields.absent(
        "coupon_per_period_pct", "每期配息率（範本只寫公式「年化配息率/12」）"
    )
    fields["coupon_pa_pct"] = (
        iis.capture("coupon_pa_pct", TextIndex(sec), nomura_dacn.ANNUAL, Decimal)
        if sec is not None
        else ParsedField.missing("coupon_pa_pct", "找不到「第三 收益分配事項」到「第四 贖回價金之計算」段落")
    )
    return iis.IisSheet(fields, base.full_text, PROVIDED)
