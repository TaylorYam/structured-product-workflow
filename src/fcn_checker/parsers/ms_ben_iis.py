"""MS BEN 中文投資人須知（docs/checker/templates/ms-zh-ben-iis.md）：版面、警語、相關機構、商品簡介與費用表同 MS FCN 投資人須知
（parsers/ms_iis.py），先照它讀出，再改讀收益分配事項（沒有定期配息；紅利、最低保證贖回率、參與率）與贖回價金之計算 1) 的到期公式。

只支援現行版範本；認得出是 MS BEN、但現行版特徵不成立時辨識交出「不支援的範本版本」（範本規格 §1、§4）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import Decimal

from ..schema import DetectionResult, Evidence, Line, ParsedField
from ..text import squash
from . import iis, ms_ben, ms_iis
from .common import derived, exactly_one
from .layout import TextIndex

TEMPLATE_ID = "ms-zh-ben-iis"
PARSER_VERSION = "1"
# 待使用者確認 BEN-376-05：同說明書，現行版特徵不成立 → 不支援的範本版本
OLD_VERSION = "MS BEN 投資人須知的寫法和現行版不同"
# 收益分配事項開頭（沒有定期配息的明確句）；Digital 也寫「籃子中表現最差的連結標的」（待使用者確認 BEN-376-04：當固定句）
NO_COUPON = "收益分配事項：於到期日，視期末定價日時籃子中表現最差的連結標的之收盤價而定。"
PAYOUT = r"紅利配息率：" + ms_ben.PCT + "；" + re.escape(ms_ben.MIN_RATE) + r"；參與率：" + ms_ben.PCT
# 待使用者確認 BEN-376-09：(b) 股數一句投資人須知寫句號、說明書寫逗號（18 份都是）；當投資人須知範本寫法，只認這一處
DELIVERY_PUNCTUATION = ("無條件捨去至整數位後之數，非整數", "無條件捨去至整數位後之數。非整數")
# FCN 投資人須知才有的欄位：BEN 沒有定期配息、KO 與 KI
FCN_ONLY = frozenset({"monthly_coupons", "ko_observation", "ko_memory", "first_callable_period", "ki_type"})


def _text(lines: Sequence[Line]) -> TextIndex:
    """內文（去掉頁尾與刊印日期：收益分配事項可能跨頁，中間夾著第 2 頁的刊印日期與頁尾）；括號統一全形。"""
    body = [x for x in ms_iis.body(lines) if not squash(x.text).startswith(ms_iis.PRINT_DATE)]
    return TextIndex(body, unify_brackets=True)


def _redemption(ti: TextIndex) -> str:
    """贖回價金之計算 1) 到 2) 之前的文字（沒有時為空字串）。"""
    m = re.search(r"贖回價金之計算：1）到期贖回：(.+?)2）", ti.text)
    return m[1] if m else ""


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §4）：MS 投資人須知版面（贖回價金之計算沒有觸及下限、自動提前出場事件）與 BEN 名稱（條件 1～3）都
    成立後，現行版特徵（條件 4）成立才是現行版；不成立 → 不支援的範本版本。"""
    ti = _text(lines)
    text = TextIndex(ms_iis.body(lines)).text
    evidence = [Evidence.of(x) for x in ms_iis.body(lines)[:2]]
    kind, problems = ms_ben.kind_of(*ms_iis.names(text))
    failed = [*ms_iis.layout_problems(text), *problems]
    redemption = _redemption(ti)
    if any(w in redemption for w in ("觸及下限事件", "自動提前出場事件")):
        failed.append("贖回價金之計算有觸及下限事件或自動提前出場事件")
    if failed or kind is None:
        return DetectionResult(False, failed, evidence)
    payout = re.search(PAYOUT, ti.text)
    checks = (
        (NO_COUPON in ti.text, f"沒有「{NO_COUPON}」"),
        (
            payout is not None and Decimal(payout[2]) == ms_ben.PARTICIPATION[kind],
            f"收益分配事項沒有「紅利配息率…；參與率：{ms_ben.PARTICIPATION[kind]}%」",
        ),
        (
            [k for k, _ in ms_ben.formula_of(redemption)] == [kind],
            f"贖回價金之計算 1) 不是 {ms_ben.KIND_LABEL[kind]} 的到期公式",
        ),
    )
    missing = [message for ok, message in checks if not ok]
    if missing:
        return DetectionResult(False, missing, evidence, unsupported=OLD_VERSION)
    return DetectionResult(True, [], evidence)


def read(lines: Sequence[Line]) -> iis.IisSheet:
    base = ms_iis.read(lines)
    fields = {k: v for k, v in base.fields.items() if k not in FCN_ONLY}
    ti = _text(lines)
    kind, _ = ms_ben.kind_of(*ms_iis.names(TextIndex(ms_iis.body(lines)).text))
    fields["no_coupon"] = exactly_one("no_coupon", ti, re.escape(NO_COUPON), f"找不到「{NO_COUPON}」")
    payout = exactly_one(
        "payout",
        ti,
        PAYOUT,
        "收益分配事項找不到「紅利配息率：X%；最低保證贖回率：本商品不適用。…；參與率：X%」",
        lambda m: m,
    )
    for name, group in (("bonus", 1), ("participation", 2)):
        fields[name] = derived(name, payout, Decimal(payout.value[group]) if payout.ok else None)
    # 到期公式：(a)(b) 整句 = 說明書（(b) 照 `DELIVERY_PUNCTUATION` 換一個標點；說明書 (b) 後面的 * 交割說明投資人須知沒有）
    found = ms_ben.formula_of(_redemption(ti))
    kinds = {k for k, _ in found}
    if len(found) != 1 or (kind is not None and kinds != {kind}):
        note = "贖回價金之計算 1) 找不到該型態的 (a) 現金交割公式，或寫了好幾種"
        fields["maturity_formula"] = ParsedField.missing("maturity_formula", note)
    else:
        k, lookback = found[0]
        clause = ms_ben.cash_clause(k, lookback) + ms_ben.delivery_clause(k, lookback).replace(*DELIVERY_PUNCTUATION)
        fields["maturity_formula"] = exactly_one(
            "maturity_formula",
            ti,
            re.escape(clause),
            "贖回價金之計算 1) (a)(b) 不是說明書的寫法",
            lambda m: (k, lookback),
        )
    return iis.IisSheet(fields, base.full_text, base.provided - FCN_ONLY)
