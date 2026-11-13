"""UBS DACN 中文投資人須知（docs/checker/templates/ubs-zh-dacn-iis.md）：版面、警語、相關機構、商品簡介、費用表同 UBS FCN
投資人須知（parsers/ubs_iis.py），先照它讀出，再加讀收益分配事項的保證期數 g（由專屬規則和說明書比）。

名稱的商品標記改成 `區間累計配息`／`Range Accrual`（同說明書）；只支援現行版範本：名稱認得出是 UBS DACN、但收益分配事項的
寫法不同時，辨識交出「不支援的範本版本」（範本規格 §3）。投資人須知沒有價格表與配息率，配息門檻只核說明書。
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from ..schema import DetectionResult, Line, ParsedField
from . import iis, ubs_dacn, ubs_iis
from .layout import TextIndex

TEMPLATE_ID = "ubs-zh-dacn-iis"
PARSER_VERSION = "1"
OLD_VERSION = "UBS DACN 投資人須知的寫法和現行版不同"
MARK = "區間累計配息"  # 本家族的標記（名稱）：有就停在本範本，不退回 FCN 投資人須知範本（#436）
# 收益分配事項（範本規格 §1.1，去空白）：前 g 個月固定配息、之後潛在配息；g 只有 `第一個月` 有樣本
INCOME = r"收益分配事項[:：]本商品(.{1,4}?)個月支付固定配息，之後每月係給付潛在配息。"
MONTHS = ubs_dacn.MONTHS


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §3）：UBS 投資人須知骨架與 DACN 名稱都成立後，收益分配事項的寫法成立才是本範本。"""
    failed, evidence = ubs_iis.skeleton(lines, ubs_dacn.NAME_ZH_MARK, ubs_dacn.NAME_EN_MARK)
    text = TextIndex(ubs_iis.body(lines)).text
    if failed:
        return DetectionResult(False, failed, evidence, claimed=MARK in text)
    if re.search(INCOME, text) is None:
        problem = "收益分配事項沒有「本商品…個月支付固定配息，之後每月係給付潛在配息」"
        return DetectionResult(False, [problem], evidence, unsupported=f"{OLD_VERSION}：{problem}")
    return DetectionResult(True, [], evidence)


def read(lines: Sequence[Line]) -> iis.IisSheet:
    """照 UBS FCN 投資人須知讀出後，加讀保證期數。"""
    base = ubs_iis.read(lines)
    fields = dict(base.fields)
    fields["guaranteed_periods"] = guaranteed(base.full_text)
    return iis.IisSheet(fields, base.full_text, base.provided | {"guaranteed_periods"})


def guaranteed(ti: TextIndex) -> ParsedField:
    """收益分配事項的保證期數 g（`第一個月` → 1）；其他寫法沒有樣本，讀不到、轉人工（範本規格 §1.1）。"""
    name = "guaranteed_periods"
    hits = list(ti.finditer(INCOME))
    if len(hits) != 1:
        return ParsedField.missing(name, "收益分配事項的配息寫法出現多次" if hits else "收益分配事項找不到配息寫法")
    m = hits[0]
    lines = ti.lines_for(m.start(), m.end())
    if m[1] not in MONTHS:
        return ParsedField.invalid(name, lines, f"收益分配事項「本商品{m[1]}個月支付固定配息」的寫法沒有樣本")
    return ParsedField.present(name, MONTHS[m[1]], lines)
