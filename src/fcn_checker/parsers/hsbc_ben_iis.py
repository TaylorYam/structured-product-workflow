"""HSBC BEN 中文投資人須知（docs/checker/templates/hsbc-zh-ben-iis.md）：版面、警語、相關機構、商品簡介與費用表同 HSBC FCN
投資人須知（parsers/hsbc_iis.py），先照它讀出，再讀第二 收益分配事項（沒有定期配息）與標題的 (最終版)。

只支援現行版範本；認得出是 HSBC BEN、但現行版特徵不成立時辨識交出「不支援的範本版本」（範本規格 §1、§4）。
"""

from __future__ import annotations

from collections.abc import Sequence

from ..schema import DetectionResult, Evidence, Line
from . import hsbc_ben, hsbc_iis, iis
from .common import exactly_one
from .hsbc_super_bull_iis import NO_COUPON
from .layout import TextIndex

TEMPLATE_ID = "hsbc-zh-ben-iis"
PARSER_VERSION = "1"
OLD_VERSION = "HSBC BEN 投資人須知的寫法和現行版不同"
FINAL_EDITION = r"中文投資人須知\(最終版\)"


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §4）：HSBC 投資人須知版面與 BEN 名稱（條件 1～3）都成立後，現行版特徵（條件 4）成立才是現行版；
    不成立 → 不支援的範本版本。"""
    body = hsbc_iis.body(lines)
    text = TextIndex(body).text
    zh, en = hsbc_iis.names(text)
    evidence = [Evidence.of(x) for x in body[:2]]
    _, problems = hsbc_ben.kind_of(zh, en)
    failed = [*hsbc_iis.layout_problems(text), *problems]
    if failed:
        return DetectionResult(False, failed, evidence)
    if NO_COUPON not in text:
        return DetectionResult(False, [f"沒有「{NO_COUPON}」"], evidence, unsupported=OLD_VERSION)
    return DetectionResult(True, [], evidence)


def read(lines: Sequence[Line]) -> iis.IisSheet:
    base = hsbc_iis.read(lines)
    fields = dict(base.fields)
    fields["no_coupon"] = exactly_one("no_coupon", base.full_text, NO_COUPON, f"找不到「{NO_COUPON}」")
    fields["final_edition"] = exactly_one("final_edition", base.full_text, FINAL_EDITION, "標題沒有「(最終版)」")
    return iis.IisSheet(fields, base.full_text, base.provided)
