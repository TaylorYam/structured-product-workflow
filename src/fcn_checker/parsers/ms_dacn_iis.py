"""MS DACN 中文投資人須知（docs/checker/templates/ms-zh-dacn-iis.md）：版面、警語、相關機構、商品簡介、贖回價金之計算、費用表
同 MS FCN 投資人須知（parsers/ms_iis.py），先照它讀出，再加讀收益分配事項的保證期數 g（由專屬規則和說明書比）。

名稱的商品標記改成 `區間累積配息`／`Range Accrual Notes`（同說明書）；收益分配事項有 `{X%×n(j)/N(j)}` 與比執行價的 n(j)
定義才是本家族。只支援現行版範本：認得出是 MS DACN、但收益分配事項的寫法不同時，辨識交出「不支援的範本版本」（範本規格
§3）。投資人須知沒有價格與 %，配息門檻只核說明書。
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from ..schema import DetectionResult, Evidence, Line
from ..text import squash
from . import iis, ms_dacn, ms_iis
from .layout import TextIndex

TEMPLATE_ID = "ms-zh-dacn-iis"
PARSER_VERSION = "1"
OLD_VERSION = "MS DACN 投資人須知的寫法和現行版不同"
# 收益分配事項（範本規格 §1.1）：同說明書第 15 項 (2) 的寫法接成一段，j 範圍多一個「第」
WORDINGS = ms_dacn.wordings(r"j係為\d+至第\d+的數字，代表第\d+至第\d+個配息觀察期間")


def _income(lines: Sequence[Line]) -> TextIndex:
    """收益分配事項（到「贖回價金之計算：」為止；去掉頁尾與刊印日期、括號全形）；找不到時為空。"""
    body = [x for x in ms_iis.body(lines) if not squash(x.text).startswith(ms_iis.PRINT_DATE)]
    ti = TextIndex(body, unify_brackets=True)
    start, end = ms_iis.COUPON_SECTION
    m = re.search(re.escape(start) + ".*?" + re.escape(end), ti.text)
    return TextIndex(ti.lines_for(m.start(), m.end()), unify_brackets=True) if m else TextIndex([])


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §3）：MS 投資人須知版面（條件 1）、DACN 名稱（條件 2）與區間累積配息（條件 3）都成立後，收益分配
    事項的固定寫法依序成立才是本範本；不成立 → 不支援的範本版本。名稱有 `區間累積配息` 的文件停在本範本。"""
    text = TextIndex(ms_iis.body(lines)).text
    evidence = [Evidence.of(x) for x in ms_iis.body(lines)[:2]]
    failed = ms_iis.layout_problems(text)
    zh, en = ms_iis.names(text)
    if not (zh and ms_dacn.MARK in zh and en and ms_dacn.NAME_EN_MARK in en):
        failed.append("商品名稱沒有「區間累積配息」或「Range Accrual Notes」")
    income = _income(lines).text
    if not ms_dacn.is_accrual(income):
        failed.append("收益分配事項沒有區間累積配息的 {X%×n(j)/N(j)} 與比「執行價」的 n(j) 定義")
    if failed:
        return DetectionResult(False, failed, evidence, claimed=bool(zh) and ms_dacn.MARK in zh)
    missing = ms_dacn.missing_wordings(income, WORDINGS, "收益分配事項")
    if missing:
        return DetectionResult(False, missing, evidence, unsupported=f"{OLD_VERSION}：{'；'.join(missing)}")
    return DetectionResult(True, [], evidence)


def read(lines: Sequence[Line]) -> iis.IisSheet:
    """照 MS FCN 投資人須知讀出後，加讀保證期數。"""
    base = ms_iis.read(lines)
    fields = dict(base.fields)
    fields["guaranteed_periods"] = ms_dacn.guaranteed(_income(lines), "guaranteed_periods", "收益分配事項")
    return iis.IisSheet(fields, base.full_text, base.provided | {"guaranteed_periods"})
