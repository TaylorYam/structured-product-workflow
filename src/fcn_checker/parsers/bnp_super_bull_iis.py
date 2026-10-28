"""BNP Super Bull 中文投資人須知（docs/checker/templates/bnp-zh-super-bull-iis.md）：版面、封面、警語、標的表、費用表同
BNP FCN 投資人須知（parsers/bnp_iis.py），先照它讀出，再改讀不同的段落（段名、沒有配息、出場紅利、評價日間隔、無 KI）。

只支援現行版範本（2026-06 起交易）；認得出是 Super Bull 舊版時辨識交出「不支援的範本版本」（範本規格 §1、§4）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import Decimal

from ..schema import DetectionResult, Evidence, FieldStatus, Line, ParsedField
from . import bnp_iis, iis
from .layout import TextIndex

TEMPLATE_ID = "bnp-zh-sb-iis"
PARSER_VERSION = "1"
OLD_VERSION = "BNP Super Bull 舊版投資人須知（2026-06 以前的範本），不支援自動核對"
NAME_EN = "SuperBullEarlyRedeemableNote"
REDEMPTION_HEAD = "收益分配事項及到期贖回金額之計算"
SECTIONS = tuple(REDEMPTION_HEAD if s == bnp_iis.REDEMPTION_SECTION[0] else s for s in bnp_iis.SECTIONS)
REDEMPTION_SECTION = (REDEMPTION_HEAD, "發行不成立之處理")
NO_COUPON = "●配息：不適用"
BONUS = r"自動提前贖回金額=商品面額×\{100%\+(\d+(?:\.\d+)?)%\}"
# 商品簡介評價日間隔：「每N 個月一次」或「每月一次」（範本規格 §3）
INTERVAL = r"自動提前贖回評價日[:：](?:每(\d+)個月一次|每月一次)[，,]詳細日期請參閱中文產品說明書第一章第14項之說明"
MATURITY_UP = (
    r"\(1\)若於期末評價日，表現最差連結標的之收盤價格大於或等於其執行價格，則投資人就每單位商品可領回：商品面額×"
    r"\[100%\+100%×\(表現最差連結標的於期末評價日之收盤價格÷表現最差連結標的之執行價格-1\)\]；或"
)
MATURITY_DOWN = r"\(2\)若於期末評價日，表現最差連結標的之收盤價格小於其執行價格"


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §4）：條件全部成立才是現行版；標題與名稱是 BNP Super Bull、段名是 FCN 的寫法 → 舊版（不支援；
    舊版相關機構的版面也不同，所以先看名稱與段名）。"""
    text = TextIndex(bnp_iis.body(lines)).text
    name = re.search(r"刊印日期[:：]\d{4}年\d{1,2}月\d{1,2}日(法國巴黎銀行.+?)" + bnp_iis.NAME_TAIL, text)
    failed = []
    for ok, message in (
        (bnp_iis.TITLE in text, "封面缺少「中文投資人須知 (適用於專業投資人及OSU客戶)」"),
        (
            name is not None and "固定配息" not in name[1],
            "商品中文名稱不是「法國巴黎銀行…(下稱「商品」)」（不含固定配息）",
        ),
        (bnp_iis.NAME_EN in text and NAME_EN in text, "商品英文名稱沒有「Super Bull Early Redeemable Note」與發行機構"),
    ):
        if not ok:
            failed.append(message)
    evidence = [Evidence.of(x) for x in bnp_iis.body(lines)[:2]]
    if failed:
        return DetectionResult(False, failed, evidence)
    if iis.in_order(text, bnp_iis.SECTIONS):  # 舊版段名和 FCN 相同
        return DetectionResult(False, ["段名是 FCN 的寫法（Super Bull 舊版）"], evidence, unsupported=OLD_VERSION)
    for ok, message in (
        (re.search(r"相關機構1?發行機構[:：]BNPParibas", text) is not None, "相關機構的發行機構不是BNP"),
        (iis.in_order(text, SECTIONS), "段落標題不完整或順序不符：" + "、".join(SECTIONS)),
        (NO_COUPON in text, "收益分配事項沒有「● 配息：不適用」"),
    ):
        if not ok:
            failed.append(message)
    return DetectionResult(not failed, failed, evidence)


def _one(name: str, ti: TextIndex, pattern: str, convert, missing: str) -> ParsedField:
    found = list(ti.finditer(pattern))
    if len(found) != 1:
        return ParsedField.missing(name, missing if not found else f"{missing}（出現多次）")
    m = found[0]
    return ParsedField.present(name, convert(m), ti.lines_for(m.start(), m.end()))


def _ki_type(lines: list[Line] | None, table: ParsedField) -> ParsedField:
    """無 KI：到期贖回金額段沒有「觸及生效事件」、是範本的兩款寫法，標的表沒有下限價格列。"""
    name = "ki_type"
    if lines is None:
        return ParsedField.missing(name, f"找不到「{REDEMPTION_HEAD}」段落")
    text = TextIndex(lines).text
    ok = (
        "觸及生效事件" not in text
        and re.search(MATURITY_UP, text) is not None
        and re.search(MATURITY_DOWN, text) is not None
        and not (table.ok and "ki" in table.value["pcts"])
    )
    if not ok:
        return ParsedField.invalid(name, lines, "無法判定無 KI：到期贖回金額段或標的表的寫法不在範本規格內")
    return ParsedField.present(name, "none", lines)


def read(lines: Sequence[Line]) -> iis.IisSheet:
    base = bnp_iis.read(lines, REDEMPTION_SECTION)
    ti, fields = base.full_text, dict(base.fields)
    fields.pop("monthly_coupon_shown_pct", None)
    redemption = iis.section(ti, REDEMPTION_SECTION)
    fields["ki_type"] = _ki_type(redemption, fields["underlying_table"])
    interval = _one(
        "observation_interval",
        ti,
        INTERVAL,
        lambda m: int(m[1]) if m[1] else 1,
        "商品簡介「自動提前贖回評價日：每N 個月一次」的寫法缺漏或不在範本規格內",
    )
    fields["observation_interval"] = interval
    fields["ko_observation"] = (
        ParsedField("ko_observation", FieldStatus.PRESENT, "P", list(interval.evidence))
        if interval.ok
        else ParsedField("ko_observation", interval.status, evidence=list(interval.evidence), note=interval.note)
    )
    fields.pop("first_callable_period", None)
    fields["exit_bonus"] = _one(
        "exit_bonus",
        ti,
        BONUS,
        lambda m: Decimal(m[1]),
        "收益分配事項找不到「自動提前贖回金額 = 商品面額 × {100% + X%}」",
    )
    fields["no_coupon"] = _one("no_coupon", ti, re.escape(NO_COUPON), lambda m: m[0], f"找不到「{NO_COUPON}」")
    provided = set(base.provided) - {"monthly_coupon_shown_pct", "first_callable_period"}
    return iis.IisSheet(fields, ti, frozenset(provided))
