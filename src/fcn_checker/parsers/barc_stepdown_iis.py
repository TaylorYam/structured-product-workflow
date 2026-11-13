"""BARC FCN_Stepdown 中文投資人須知（docs/checker/templates/barc-zh-stepdown-iis.md，workflow #544）。

和 BARC 投資人須知（`parsers/barc_iis.py`）只差收益分配事項的價格表沒有 KO 欄（表前說明句也不列觸發價格）；讀出沿用
`barc_iis.read`（價格表宣告換成沒有 KO 欄）。投資人須知沒有 KO%、降幅或 KO 價。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from ..schema import DetectionResult, Line
from ..text import squash
from . import barc_iis, barc_stepdown, iis

TEMPLATE_ID = "barc-zh-stepdown-iis"
PARSER_VERSION = "1"

PRICE_TABLE = replace(barc_stepdown.PRICE_TABLE, where=barc_iis.PRICE_TABLE.where)  # 同說明書：沒有 KO 欄
KO_HEADS = ("自動提前出場觸發價", "觸發水準")  # BARC FCN 投資人須知價格表 KO 欄頭的開頭（記憶式／非記憶式）
INTRO_END = "請詳見下表所示"  # 價格表前的說明句：而「最初價格」、「執行價格」與「…」，請詳見下表所示
INTRO_LINES = 8  # 往表頭前看幾行找說明句


def _intro(lines: Sequence[Line], region: Sequence[Line]) -> str | None:
    """價格表前說明句列出的價格名詞那一段（`而「最初價格」…` 到 `請詳見下表所示` 之前）；讀不到時 None。"""
    start = next(i for i, ln in enumerate(lines) if ln is region[0])
    text = squash("".join(ln.text for ln in lines[max(start - INTRO_LINES, 0) : start]))
    end = text.rfind(INTRO_END)
    if end < 0:
        return None
    begin = max(text.rfind(c, 0, end) for c in "；。") + 1
    return text[begin:end]


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §2）：BARC 投資人須知的條件成立，收益分配事項價格表讀得到、表頭沒有 KO 欄，且表前說明句也不列觸發
    價格，兩項都成立才是本範本（只看表頭時，KO 欄頭讀不到的 FCN 投資人須知會被認成 Stepdown）。兩項矛盾或說明句讀不到 →
    不是本範本，退回 BARC FCN 投資人須知範本（KO 欄讀不到時那邊轉人工覆核）。BARC 投資人須知的條件不成立時 FCN 範本也不成立，
    不必另外攔。"""
    base = barc_iis.detect(lines)
    region = barc_iis.price_region(lines)
    if region is None:
        return DetectionResult(False, [*base.failed, "找不到收益分配事項價格表"], base.evidence)
    text = squash("".join(ln.text for ln in region))
    if any(head in text for head in KO_HEADS):
        return DetectionResult(False, [*base.failed, "收益分配事項價格表有 KO 欄（不是 Stepdown）"], base.evidence)
    if "最初價格" not in text or "執行價格" not in text:
        return DetectionResult(False, [*base.failed, "收益分配事項價格表沒有最初價格、執行價格欄"], base.evidence)
    intro = _intro(lines, region)
    if intro is None or "最初價格" not in intro:
        failed = [*base.failed, f"價格表前沒有「…「最初價格」…{INTRO_END}」說明句"]
        return DetectionResult(False, failed, base.evidence)
    if any(head in intro for head in KO_HEADS):
        failed = [*base.failed, "價格表沒有 KO 欄，表前說明句卻列了觸發價格（不是 Stepdown）"]
        return DetectionResult(False, failed, base.evidence)
    return base


def read(lines: Sequence[Line]) -> iis.IisSheet:
    return barc_iis.read(lines, PRICE_TABLE)
