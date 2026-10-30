"""CA BEN 中文投資人須知（docs/checker/templates/ca-zh-ben-iis.md）：版面、警語、相關機構、商品簡介、標的表、費用表同 CA FCN
投資人須知（parsers/ca_iis.py），先照它讀出，再改讀二、1.(15)、二、2. 不同的段落（沒有配息、沒有 KO、無 KI、到期公式、
參與率與紅利率）。

只支援現行版 Bonus 範本；認得出是 CA BEN、但是 Digital 或現行版特徵不齊時辨識交出「不支援的範本版本」（範本規格 §1、§4）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import Decimal

from ..schema import DetectionResult, Evidence, Line, ParsedField
from ..text import squash
from . import ca, ca_ben, ca_iis, iis
from .common import exactly_one
from .layout import TextIndex

TEMPLATE_ID = "ca-zh-ben-iis"
PARSER_VERSION = "1"
OLD_VERSION = "CA BEN 投資人須知寫法和現行版不同"
# BEN-377-04（待使用者確認）：名稱寫錯（中文多「固定配息」、英文多「Autocall」）仍辨識成 CA BEN，再由名稱規則判不一致；
# 所以中文只要有 `NAME_ZH_MARK`、沒有 `NAME_ZH_NOT`，英文只要有 Bonus 與 Note（或 Digital Coupon Note）
NAME_ZH_MARK = "紅利結構型商品(無擔保)(不保本)"
NAME_ZH_NOT = ("自動提前出場", "連結表現")
NO_COUPON_DATE = "(15)配息日：" + ca_ben.NO_COUPON
FCN_ONLY = ("coupon_pa_pct", "monthly_coupons", "redemption_pcts", "ko_memory")


def _names(text: str) -> tuple[str, str]:
    m = re.search(r"^中文投資人須知(.+?)\(ISIN：[A-Z0-9]+\)\(下稱「商品」或「本商品」\)(.+?)\(ISIN：", text)
    return (m[1], m[2]) if m else ("", "")


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §4）：CA 投資人須知版面與 BEN 名稱（條件 1～3）都成立後，Digital → 不支援；現行版特徵（條件 4）
    全部成立才是現行版，缺任一項 → 不支援的範本版本（錯訊列出缺的特徵）。"""
    body = ca_iis.body(lines)
    text = TextIndex(body).text
    head = squash(body[0].text) if body else ""
    issuer = squash(ca.ISSUER)
    zh, en = _names(text)
    digital_name = squash(ca_ben.DIGITAL_EN) in en
    evidence = [Evidence.of(x) for x in body[:2]]
    family = (
        (head == "中文投資人須知", "第一行不是「中文投資人須知」"),
        (
            zh.startswith(issuer) and NAME_ZH_MARK in zh and not any(x in zh for x in NAME_ZH_NOT),
            f"中文名稱不是以「{ca.ISSUER}」開頭、沒有「{NAME_ZH_MARK}」或有「{'」「'.join(NAME_ZH_NOT)}」",
        ),
        (
            en.startswith(issuer) and (("Bonus" in en and "Note" in en) or digital_name),
            f"英文名稱不是以「{ca.ISSUER}」開頭或沒有「Bonus…Note」「{ca_ben.DIGITAL_EN}」",
        ),
        ("(種類：" in text and "(TDCC商品代號：" in text, "找不到「(種類：…)(TDCC 商品代號：…)」"),
        (
            f"一、相關機構事業概況1.發行機構：{issuer}" in text and "3.總代理人：" in text,
            f"相關機構第 1 點的發行機構不是 {ca.ISSUER} 或沒有第 3 點總代理人",
        ),
        (iis.in_order(text, ca_iis.SECTIONS), "段落標題不完整或順序不符：" + "、".join(ca_iis.SECTIONS)),
    )
    failed = [message for ok, message in family if not ok]
    if failed:
        return DetectionResult(False, failed, evidence)
    if digital_name or ca_ben.DIGITAL_FORMULA in text:
        why = "英文名稱是 Digital Coupon Note" if digital_name else "二、2. 是 Digital 公式"
        return DetectionResult(False, [why], evidence, unsupported=ca_ben.DIGITAL)
    formula = text.find(ca_ben.FORMULA)
    after = text[formula:] if formula >= 0 else ""
    current = (
        (ca_ben.PAYMENTS in text, f"二、2. 沒有「{ca_ben.PAYMENTS}」"),
        (formula >= 0, "二、2. 沒有 Bonus 公式「每單位面額 x[100%+最大值(紅利率，參與率x(…)-100%))]」"),
        (
            re.search(ca_ben.PARTICIPATION + ca_ben.BONUS, after) is not None,
            "二、2. 公式後沒有「參與率：x%。紅利率：x%。」",
        ),
        (NO_COUPON_DATE in text, f"二、1. 沒有「{NO_COUPON_DATE}」"),
        (ca_ben.NO_KO not in text, f"投資人須知有「{ca_ben.NO_KO}」"),
    )
    missing = [message for ok, message in current if not ok]
    if missing:
        return DetectionResult(False, missing, evidence, unsupported=OLD_VERSION)
    return DetectionResult(True, [], evidence)


def read(lines: Sequence[Line]) -> iis.IisSheet:
    base = ca_iis.read(lines)
    ti, fields = base.full_text, dict(base.fields)
    for name in FCN_ONLY:
        fields.pop(name, None)
    for name, pattern, label in (("participation", ca_ben.PARTICIPATION, "參與率"), ("bonus", ca_ben.BONUS, "紅利率")):
        fields[name] = exactly_one(name, ti, pattern, f"二、2. 找不到「{label}：x%。」", lambda m: Decimal(m[1]))
    phrases = (
        ("no_coupon_summary", re.escape(NO_COUPON_DATE), f"二、1. 沒有「{NO_COUPON_DATE}」"),
        ("no_coupon_payments", re.escape(ca_ben.PAYMENTS), f"二、2. 沒有「{ca_ben.PAYMENTS}」"),
        (
            "delivery_formula",
            ca_ben.DELIVERY,
            "二、2. 到期（1）不是「若最差表現之連結標的期末訂價小於執行價…交付…股數」的寫法",
        ),
        ("maturity_formula", re.escape(ca_ben.MATURITY), "二、2. 到期（2）與 Bonus 公式不是範本固定寫法"),
    )
    for name, pattern, missing in phrases:
        fields[name] = exactly_one(name, ti, pattern, missing)
    # 無 KI（範本規格 §3）：全文沒有界限事件、（1）直接比執行價；CA FCN 的讀法看「期末訂價低於執行價」，BEN 寫「小於」
    problems = ["有界限事件"] if "界限事件" in ti.text else []
    if not fields["delivery_formula"].ok:
        problems.append("到期（1）不是「若最差表現之連結標的期末訂價小於執行價」")
    fields["has_ki"] = (
        ParsedField.missing("has_ki", "判斷不出有無 KI：" + "；".join(problems))
        if problems
        else ParsedField("has_ki", fields["delivery_formula"].status, False, fields["delivery_formula"].evidence)
    )
    return iis.IisSheet(fields, ti, frozenset(set(base.provided) - set(FCN_ONLY)))
