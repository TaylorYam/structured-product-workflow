"""CA Super Bull 中文投資人須知（docs/checker/templates/ca-zh-super-bull-iis.md）：版面、警語、相關機構、商品簡介、標的表、
費用表同 CA FCN 投資人須知（parsers/ca_iis.py），先照它讀出，再改讀二、1.(15)、二、2. 不同的段落（沒有配息、出場紅利、
贖回公式；記憶式與有無 KI 照 CA FCN 讀）。

只支援現行版範本；認得出是 CA Super Bull、但現行版特徵不齊時辨識交出「不支援的範本版本」（範本規格 §1、§4）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import Decimal

from ..schema import DetectionResult, Evidence, Line, ParsedField
from ..text import squash
from . import ca, ca_iis, ca_super_bull, iis
from .layout import TextIndex

TEMPLATE_ID = "ca-zh-sb-iis"
PARSER_VERSION = "1"
OLD_VERSION = "CA Super Bull 舊版投資人須知（範本文字和現行版不同），不支援自動核對"
BONUS = r"自動提前出場紅利：" + ca.PCT + "。"
NO_COUPON_DATE = "(15)配息日：" + ca_super_bull.NO_COUPON
KO_REDEMPTION = r"支付每單位金額=每單位商品面額×\(100%\+自動提前出場紅利\)。其後無任何金額給付。"
# (ii) 到期公式：樣本有 `x[100%+100%* (…-100%)]` 和少右括號的 `x(100%+100%* (…-100%)` 兩種，括號寫法不核對（範本規格 §3）
MATURITY_FORMULA = (
    r"到期時持有之商品單位數x每單位商品面額x[\[(]100%\+100%\*\("
    r"(?:最差表現連結標的期末訂價/最差表現連結標的執行價|連結標的期末訂價/連結標的執行價)-100%\)\]?。"
)
FCN_ONLY = ("coupon_pa_pct", "monthly_coupons", "redemption_pcts")


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §4）：CA 投資人須知版面與 Super Bull 名稱（條件 1～3）都成立後，現行版特徵（條件 4）全部成立
    才是現行版；缺任一項 → Super Bull 舊版（不支援，錯訊列出缺的特徵）。"""
    body = ca_iis.body(lines)
    text = TextIndex(body).text
    head = squash(body[0].text) if body else ""
    issuer = squash(ca.ISSUER)
    m = re.search(r"^中文投資人須知(.+?)\(ISIN：[A-Z0-9]+\)\(下稱「商品」或「本商品」\)(.+?)\(ISIN：", text)
    zh, en = (m[1], m[2]) if m else ("", "")
    evidence = [Evidence.of(x) for x in body[:2]]
    family = (
        (head == "中文投資人須知", "第一行不是「中文投資人須知」"),
        (
            zh.startswith(issuer) and ca_super_bull.NAME_ZH_MARK in zh and "固定配息" not in zh,
            f"中文名稱不是以「{ca.ISSUER}」開頭、沒有「{ca_super_bull.NAME_ZH_MARK}」或有「固定配息」",
        ),
        (
            en.startswith(issuer) and squash(ca_super_bull.NAME_EN_MARK) in en,
            f"英文名稱不是以「{ca.ISSUER}」開頭或沒有「{ca_super_bull.NAME_EN_MARK}」",
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
    current = (
        (ca_super_bull.PAYMENTS in text, f"二、2. 沒有「{ca_super_bull.PAYMENTS}」"),
        (re.search(BONUS, text) is not None, "二、2. 沒有「自動提前出場紅利：x%」"),
        (re.search(ca.KO_EVENT, text) is not None, "二、2. 沒有自動提前出場事件句"),
        ("▪" not in text, "用「▪」條列"),
    )
    missing = [message for ok, message in current if not ok]
    if missing:
        return DetectionResult(False, missing, evidence, unsupported=OLD_VERSION)
    return DetectionResult(True, [], evidence)


def _one(name: str, ti: TextIndex, pattern: str, convert, missing: str) -> ParsedField:
    found = list(ti.finditer(pattern))
    if len(found) != 1:
        return ParsedField.missing(name, missing if not found else f"{missing}（出現多次）")
    m = found[0]
    return ParsedField.present(name, convert(m), ti.lines_for(m.start(), m.end()))


def read(lines: Sequence[Line]) -> iis.IisSheet:
    base = ca_iis.read(lines)
    ti, fields = base.full_text, dict(base.fields)
    for name in FCN_ONLY:
        fields.pop(name, None)
    fields["exit_bonus"] = _one(
        "exit_bonus", ti, BONUS, lambda m: Decimal(m[1]), "二、2.(a) 找不到「自動提前出場紅利：x%」"
    )
    phrases = (
        ("no_coupon_summary", re.escape(NO_COUPON_DATE), f"二、1. 沒有「{NO_COUPON_DATE}」"),
        ("no_coupon_payments", re.escape(ca_super_bull.PAYMENTS), f"二、2. 沒有「{ca_super_bull.PAYMENTS}」"),
        (
            "ko_redemption_formula",
            KO_REDEMPTION,
            "二、2.(a) 自動提前出場贖回金額不是「面額 × (100%+自動提前出場紅利)」",
        ),
        (
            "maturity_formula",
            MATURITY_FORMULA,
            "二、2. 到期贖回 (ii) 不是「面額 x [100%+100%* (…期末訂價/…執行價-100%)]」",
        ),
    )
    for name, pattern, missing in phrases:
        fields[name] = _one(name, ti, pattern, lambda m: m[0], missing)
    # 無 KI（範本規格 §3）：全文沒有界限事件、(i) 直接比執行價；CA FCN 的讀法只看有沒有「期末訂價低於執行價」
    if not (fields["has_ki"].ok and fields["has_ki"].value):
        problems = ["有界限事件"] if "界限事件" in ti.text else []
        if re.search(ca_super_bull.NO_KI, ti.text) is None:
            problems.append("到期贖回 (i) 不是「若…期末訂價低於執行價：」")
        if problems:
            fields["has_ki"] = ParsedField.missing("has_ki", "判斷不出有無 KI：" + "；".join(problems))
    return iis.IisSheet(fields, ti, frozenset(set(base.provided) - set(FCN_ONLY)))
