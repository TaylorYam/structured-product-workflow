"""BNP DACN 中文投資人須知（docs/checker/templates/bnp-zh-dacn-iis.md）：版面、封面、警語、相關機構、段落、KO／KI、費用表同
BNP FCN 投資人須知（parsers/bnp_iis.py），先照它讀出（標的表多定界價格列），再改讀收益分配事項的配息寫法（保證期數 g、N）與
配息門檻（標的表定界價格列的 %）。月配息率 `其中C為X%` 同 FCN。

依收益分配事項的 `×C×(nj/Nj)` 公式與比定界價格的 nj 定義辨識商品家族，名稱另當條件（中文沒有 `固定配息`、英文有
`Daily Accrual`）；只支援現行版範本：認得出是 BNP DACN、但現行版特徵不成立時辨識交出「不支援的範本版本」（範本規格 §4）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from ..schema import DetectionResult, Evidence, Line, ParsedField
from . import bnp_dacn, bnp_iis, bnp_tables, iis
from .layout import TextIndex

TEMPLATE_ID = "bnp-zh-dacn-iis"
PARSER_VERSION = "1"
OLD_VERSION = "BNP DACN 投資人須知的寫法和現行版不同"
NAME_EN = "DailyAccrual"
# 收益分配事項「● 配息：」（範本規格 §1.1，去空白）：第 1～g 個配息日 C、第 g+1～N 個配息日 C × (nj/Nj)
COUPON = re.compile(
    re.escape("●配息：配息將於每個配息日支付(如有)。")
    + r"第1(?:(?:到|~)(\d+))?個配息日之配息金額為商品面額×C；第(\d+)~(\d+)個配息日之配息金額為商品面額×C×\(nj/Nj\)，"
    + r"其中C為\d+(?:\.\d+)?%。"
    + re.escape(
        "nj：指於相關第j個配息期內籃子內所有連結標的之收盤價格皆大於或等於其各自定界價格的合格營業日天數。"
        "Nj：指於相關第j個配息期內合格營業日總天數。合格營業日指任一預定交易日且籃子內所有連結標的在該日均沒有發生市場中斷事件。"
    )
)
NJ = re.compile(r"nj：指.+?其各自定界價格的合格營業日天數")


def is_accrual(text: str) -> bool:
    """商品家族（區間計息，範本規格 §4 條件 3）。"""
    return "×C×(nj/Nj)" in text and NJ.search(text) is not None


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §4）：BNP 投資人須知骨架（條件 1）、DACN 名稱（條件 2）與區間計息（條件 3）都成立後，現行版特徵
    全部成立才是本範本；全文有 `bnp_dacn.MARK` 的文件停在本範本（不退回 Super Bull 或 FCN 投資人須知範本）。"""
    body = bnp_iis.body(lines)
    text = TextIndex(body).text
    name = re.search(r"刊印日期[:：]\d{4}年\d{1,2}月\d{1,2}日(法國巴黎銀行.+?)" + bnp_iis.NAME_TAIL, text)
    evidence = [Evidence.of(x) for x in body[:2]]
    failed = [
        message
        for ok, message in (
            (bnp_iis.TITLE in text, "封面缺少「中文投資人須知 (適用於專業投資人及OSU客戶)」"),
            (
                name is not None and "固定配息" not in name[1],
                "商品中文名稱不是「法國巴黎銀行…(下稱「商品」)」（不含固定配息）",
            ),
            (bnp_iis.NAME_EN in text and NAME_EN in text, "商品英文名稱沒有「Daily Accrual」與發行機構"),
            (re.search(r"相關機構1?發行機構[:：]BNPParibas", text) is not None, "相關機構的發行機構不是BNP"),
            (iis.in_order(text, bnp_iis.SECTIONS), "段落標題不完整或順序不符：" + "、".join(bnp_iis.SECTIONS)),
            (is_accrual(text), "收益分配事項沒有區間計息的「×C×(nj/Nj)」公式與比定界價格的「nj」定義"),
        )
        if not ok
    ]
    if failed:
        return DetectionResult(False, failed, evidence, claimed=bnp_dacn.MARK in text)
    missing = []
    if COUPON.search(text) is None:
        missing.append("收益分配事項的配息寫法和現行版不同")
    if not re.search(bnp_tables.BARRIER_LABEL[1], text):
        missing.append("標的表沒有「定界價格(Barrier Price)」列")
    if missing:
        return DetectionResult(False, missing, evidence, unsupported=f"{OLD_VERSION}：{'；'.join(missing)}")
    return DetectionResult(True, [], evidence)


def read(lines: Sequence[Line]) -> iis.IisSheet:
    """照 BNP FCN 投資人須知讀出（標的表宣告定界價格列）後，加讀保證期數與配息門檻 %（由專屬規則和說明書比）。"""
    base = bnp_iis.read(lines, labels=bnp_tables.DACN_PRICE_LABELS)
    fields = dict(base.fields)
    fields["guaranteed_periods"] = _guaranteed(base.full_text, fields["tenor_months"])
    fields["coupon_barrier_pct"] = bnp_dacn.barrier_pct(fields["underlying_table"], "商品簡介標的表")
    provided = base.provided | {"guaranteed_periods", "coupon_barrier_pct"}
    return iis.IisSheet(fields, base.full_text, frozenset(provided))


def _guaranteed(ti: TextIndex, tenor: ParsedField) -> ParsedField:
    """配息寫法的保證期數 g（`第1個配息日` → 1）：第二段須從 g+1 起、到 N = 天期，否則讀不到（範本規格 §2）。g > 1 的
    `第1到g個`／`第1~g個` 沒有樣本，讀不到、轉人工（範本規格 §2）。"""
    name = "guaranteed_periods"
    hits = list(ti.finditer(COUPON))
    if len(hits) != 1:
        return ParsedField.missing(name, "收益分配事項的配息寫法出現多次" if hits else "收益分配事項找不到配息寫法")
    m = hits[0]
    lines = ti.lines_for(m.start(), m.end())
    g, start, last = int(m[1] or 1), int(m[2]), int(m[3])
    problems = []
    if g > 1:
        problems.append(f"第 1～{g} 個配息日的寫法沒有樣本")
    if start != g + 1:
        problems.append(f"第二段從第 {start} 個配息日起，不是第 {g + 1} 個")
    if tenor.ok and last != tenor.value:
        problems.append(f"第二段到第 {last} 個配息日，不等於天期 {tenor.value} 個月")
    if problems:
        return ParsedField.invalid(name, lines, "收益分配事項配息寫法：" + "；".join(problems))
    return ParsedField.present(name, g, lines)
