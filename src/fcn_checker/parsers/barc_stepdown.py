"""BARC FCN_Stepdown 中文產品說明書（docs/checker/templates/barc-zh-stepdown.md，workflow #544）。

和 BARC FCN（`parsers/barc.py`）只差 KO：KO% 寫在第 13 條 (7) 提前出場表的「自動提前出場觸發百分比」欄、每期固定往下降；
第 15 條沒有 KO 定義句與 KO 價欄。讀出沿用 `barc.read`（價格表宣告換成沒有 KO 欄），再換掉 KO%、記憶式、各標的 KO 價與
每日型的比價日（共用讀法 `parsers/stepdown.py`）。
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal

from ..schema import DetectionResult, Evidence, FieldStatus, Line, ParsedField
from ..text import squash
from . import barc, stepdown
from . import barc_schedule as schedule
from .common import derived
from .layout import Document, Span, TextIndex

TEMPLATE_ID = "barc-zh-stepdown-pd"
PARSER_VERSION = "1"

# 商品家族標記與現行寫法（範本規格 §4）：比對前去空白、冒號統一為半形
FAMILY = "自動提前出場觸發百分比:詳見下表所示"
KO_PRICE_DEF = (
    "自動提前出場觸發價格:就任一標的資產而言，指該標的資產之「最初價格」乘以相對應之「自動提前出場觸發百分比」"
)
MEMORY = "應視為已發生「記憶事件」"
KO_WHERE = "第 13 條 (7) 提前出場表"
UNSUPPORTED = "Barclays FCN_Stepdown 的寫法和範本不同"

# 第 15 條與第 16 條(3) 價格表：沒有 KO 欄（範本規格 §1）
PRICE_TABLE = replace(barc.PRICE_TABLE, columns=tuple(c for c in barc.PRICE_TABLE.columns if c.key != "ko"))


def _flat(lines: Sequence[Line]) -> str:
    return squash("".join(ln.text for ln in lines)).replace("：", ":")


def _art(doc: Document, n: int) -> Span | None:
    return doc.articles(1).get(n) if 1 in doc.chapters else None


def _ko_rows(table: schedule.Table) -> list[tuple[Decimal | None, list[Line]]]:
    """提前出場表每一列的觸發百分比（不是數字的為 None：不可提前出場的 N/A 或讀不到）。"""
    return [(v if isinstance(v := r.get("trigger"), Decimal) else None, r.lines) for r in table.rows]


def _dated_without_pct(table: schedule.Table) -> list[int]:
    """有提前出場日期（D 期末日、P 自動提前出場評價日）、觸發百分比卻不是數字的列：KO 期別會和比價日錯開。"""
    return [
        i
        for i, r in enumerate(table.rows, start=1)
        if not isinstance(r.get("trigger"), Decimal)
        and any(isinstance(r.get(k), dt.date) for k in ("end", "ko_valuation"))
    ]


def _ko_terms(ko_table: ParsedField) -> dict[str, ParsedField]:
    if not ko_table.ok:
        return {key: derived(key, ko_table) for key in stepdown.KEYS}
    return stepdown.ko_terms(_ko_rows(ko_table.value), KO_WHERE)


def version_problems(doc: Document) -> list[str]:
    """商品家族標記成立後，現行寫法（範本規格 §4）中不成立的項目（提前出場表讀不到也算）。"""
    art13 = _art(doc, 13)
    text13 = _flat(doc.span_lines(art13))
    out = []
    if KO_PRICE_DEF not in text13:
        out.append("第 13 條 (7) 沒有「自動提前出場觸發價格：…乘以相對應之「自動提前出場觸發百分比」」定義")
    if MEMORY not in text13:
        out.append("第 13 條 (7) 沒有記憶事件句（非記憶式沒有樣本）")
    s15 = doc.span_lines(_art(doc, 15))
    if any(
        barc.definition_pct("ko_pct", s15, term).status != FieldStatus.MISSING
        for term in ("自動提前出場觸發價格", "觸發水準")
    ):
        out.append("第 15 條仍有 KO 定義句")
    obs = barc.ko_observation(doc, art13)
    ko = schedule.ko_table(schedule.find_tables(doc, art13), obs)
    if not ko.ok:
        out.append(f"讀不到第 13 條 (7) 提前出場表（{ko.note or ko.status.value}）")
        return out
    if dated := _dated_without_pct(ko.value):
        out.append(f"{KO_WHERE}第 " + "、".join(map(str, dated)) + " 列有日期、觸發百分比卻不是數字")
    elif problem := stepdown.step_problem(_ko_terms(ko)):
        out.append(problem)
    g = schedule.guaranteed_periods(ko)
    if obs.ok and obs.value == "D" and g.ok and g.value == 0:
        # FCN_Express 的寫法（比價日_1 = 第 1 期期始日）：ADR 0033 的 KO 期別會錯一期（範本規格 §3）
        out.append("每日型提前出場表第 1 期期始日就有日期（沒有 Non-Call），KO 每期往下降沒有這個樣本")
    return out


def detect(doc: Document) -> DetectionResult:
    """範本辨識（範本規格 §4）：BARC 說明書骨架與 Stepdown 標記都成立後，現行寫法全部成立才是本範本；缺任一項 → 不支援的範本
    版本。有 Stepdown 標記、但骨架不成立的文件也停在本範本（不退回 BARC FCN 範本）。"""
    base = barc.detect(doc)
    art13 = _art(doc, 13)
    if FAMILY not in _flat(doc.span_lines(art13)):
        failed = [*base.failed, f"第 13 條沒有「{FAMILY}」（不是 Stepdown）"]
        return DetectionResult(False, failed, base.evidence)
    if not base.matched:
        return DetectionResult(False, base.failed, base.evidence, claimed=True)
    problems = version_problems(doc)
    if problems:
        return DetectionResult(False, problems, base.evidence, unsupported=f"{UNSUPPORTED}：{'；'.join(problems)}")
    return DetectionResult(True, [], base.evidence)


def _memory(doc: Document, name_zh: ParsedField) -> ParsedField:
    """記憶式：第 13 條 (7) 的記憶事件句（第 15 條沒有觸發價格名詞）；須和商品名稱含「記憶式」一致（同 BARC FCN）。"""
    ti = TextIndex(doc.span_lines(_art(doc, 13)))  # 和範本辨識同一種找法（整段去空白），句子斷行也找得到
    hits = [ln for m in ti.finditer(re.escape(MEMORY)) for ln in ti.lines_for(m.start(), m.end())]
    if not hits:
        return ParsedField.missing("ko_memory", "第 13 條 (7) 找不到記憶事件句（「應視為已發生「記憶事件」」）")
    if name_zh.ok and "記憶式" not in name_zh.value:
        pf = ParsedField.ambiguous(
            "ko_memory", [True, False], hits, "第 13 條 (7) 有記憶事件句，商品名稱卻不含「記憶式」"
        )
        pf.evidence += name_zh.evidence
        return pf
    return ParsedField(
        "ko_memory", FieldStatus.PRESENT, True, [Evidence.of(ln) for ln in hits] + list(name_zh.evidence)
    )


def read(lines: Sequence[Line]) -> barc.BarcTermSheet:
    ts = barc.read(lines, PRICE_TABLE)
    fields = dict(ts.fields)
    fields.update(_ko_terms(fields["ko_table"]))
    fields["ko_memory"] = _memory(ts.document, fields["name_zh"])
    fields["underlying_prices"] = stepdown.with_ko_prices(fields["underlying_prices"], fields["ko_pct"])
    fields["autocall_schedule"] = stepdown.every_period(fields["autocall_schedule"], fields["schedule_table"])
    return replace(ts, fields=fields)
