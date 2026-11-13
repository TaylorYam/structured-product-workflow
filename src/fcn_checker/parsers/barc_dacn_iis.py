"""BARC DACN 中文投資人須知（docs/checker/templates/barc-zh-dacn-iis.md）：版面、封面、警語、相關機構、商品簡介、到期贖回、
費用表同 BARC FCN 投資人須知（parsers/barc_iis.py），先照它讀出，再改讀收益分配事項的配息率 t（月配息率；沒有年利率）與價格表
（多配息門檻欄、列靠上對齊）。

依收益分配事項的配息率 t 公式與 nt 定義（比配息門檻）辨識商品家族；只支援現行版範本：認得出是 BARC DACN、但現行版特徵不成立
時辨識交出「不支援的範本版本」（範本規格 §3）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal

from ..schema import DetectionResult, Line, ParsedField
from . import barc_dacn, barc_iis, dacn, iis
from .layout import TextIndex

TEMPLATE_ID = "barc-zh-dacn-iis"
PARSER_VERSION = "1"
OLD_VERSION = "BARC DACN 投資人須知的寫法和現行版不同"
N, X = barc_dacn.N, barc_dacn.X
# 配息率 t（範本規格 §1.1，去空白）：第 1～g 期 X%、其後 X%×nt/Nt
RATE = re.compile(rf"「配息率t」係指，\(i\)如t=1(?:到{N})?，為({X})%；及\(ii\)如t={N}到{N}，為({X})%×nt/Nt；")
WORDING = (
    RATE.pattern
    + re.escape("其中：「t」係自1至")
    + N
    + re.escape(
        "的數字，每個數字代表一個配息支付日及相對應的配息觀察期及配息率。就配息支付日t而言，「nt」指與配息支付日t相對應之"
        "配息觀察期內，各標的資產之相關價格高於或等於其配息門檻之配息評價日天數。「Nt」指與該配息支付日t相對應之配息觀察期內"
        "之一籃子預定交易日天數。"
    )
)
PRICE_TABLE = replace(barc_dacn.PRICE_TABLE, where=barc_iis.PRICE_TABLE.where)


def is_accrual(text: str) -> bool:
    """商品家族（區間計息，範本規格 §3 條件 2）。"""
    return "「配息率t」係指" in text and "×nt/Nt" in text and barc_dacn.NT.search(text) is not None


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §3）：BARC 投資人須知骨架（條件 1，同 FCN）與區間計息（條件 2）都成立後，現行版特徵全部成立才是
    本範本；全文有 `barc_dacn.MARK` 的文件停在本範本（不退回 FCN 範本）。"""
    base = barc_iis.detect(lines)
    text = TextIndex(lines).text
    failed = list(base.failed)
    if not is_accrual(text):
        failed.append("收益分配事項沒有區間計息的「「配息率t」係指」「×nt/Nt」公式與比配息門檻的「nt」定義")
    if failed:
        return DetectionResult(False, failed, base.evidence, claimed=barc_dacn.MARK in text)
    missing = []
    if not re.search(WORDING, text):
        missing.append("收益分配事項的配息率 t 寫法和現行版不同")
    if not re.search(barc_dacn.MARK + r"（為最初價格的", text):
        missing.append(f"價格表表頭沒有「{barc_dacn.MARK}（為最初價格的X%）」")
    if missing:
        return DetectionResult(False, missing, base.evidence, unsupported=f"{OLD_VERSION}：{'；'.join(missing)}")
    return DetectionResult(True, [], base.evidence)


def read(lines: Sequence[Line]) -> iis.IisSheet:
    """照 BARC FCN 投資人須知讀出後，改讀月配息率、價格表與配息門檻 %。"""
    base = barc_iis.read(lines)
    ti = base.full_text
    fields = dict(base.fields)
    fields.pop("coupon_pa_pct", None)
    fields["monthly_coupon_pct"] = _monthly(ti)
    table, prices = barc_iis.price_fields(lines, PRICE_TABLE)
    fields.update(prices)
    fields["coupon_barrier_pct"] = dacn.barrier_from_head(table, "barrier", "價格表配息門檻欄頭讀不到 %")
    # 沒有年利率：不交 `coupon_pa_pct`（投資人須知不核年利率）；配息門檻 % 由專屬規則和說明書比
    provided = (base.provided - {"coupon_pa_pct"}) | {"coupon_barrier_pct"}
    return iis.IisSheet(fields, ti, frozenset(provided))


def _monthly(ti: TextIndex) -> ParsedField:
    """配息率 t 的 X（兩個 X 須相同，否則讀不到）。"""
    name = "monthly_coupon_pct"
    hits = list(ti.finditer(RATE))
    if len(hits) != 1:
        note = "收益分配事項配息率 t 公式出現多次" if hits else "收益分配事項找不到「「配息率t」係指，(i)如t=1，為X%」"
        return ParsedField.missing(name, note)
    m = hits[0]
    lines = ti.lines_for(m.start(), m.end())
    if m[1] != m[2]:
        return ParsedField.invalid(name, lines, f"配息率 t 兩段的 X 不同（{m[1]}%、{m[2]}%）")
    return ParsedField.present(name, Decimal(m[1]), lines)
