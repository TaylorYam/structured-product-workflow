"""Natixis FCN_Stepdown 中文產品說明書（docs/checker/templates/natixis-zh-stepdown.md，workflow #545）。

和 Natixis FCN（`parsers/natixis.py`）只差 KO：第 14 項的 KO 價格定義寫「為下表所列之期初進場價格」，後面一張每期一列的
自動提前到期贖回價格表（每期固定往下降）；第 9 項連結標的表沒有 KO 欄。早期版面有大量 CJK 相容字，辨識與讀取前先轉成標準字
（只在本範本做）。讀出沿用 `natixis.read`（價格表宣告拿掉 KO 欄），再換掉 KO%、各標的 KO 價與每日型的比價日（共用讀法
`parsers/stepdown.py`）。每期 KO 表用期別 `t` 對齊第 12 項：`t` 對不上是範本不支援；同一期日期不同由規則
`doc.ko_table_dates` 判不一致。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from .. import standard_fields
from ..schema import DetectionResult, Line, ParsedField
from ..text import squash
from . import natixis, stepdown
from .common import compat_normalized, derived
from .layout import Document, TextIndex, parse_date

TEMPLATE_ID = "natixis-zh-stepdown-pd"
PARSER_VERSION = "1"
UNSUPPORTED = "Natixis FCN_Stepdown 的寫法和範本不同"

# 商品家族標記（範本規格 §4 條件 2，去空白比對）；FCN 是「自動提前到期贖回價格為期初進場價格之Y%」
FAMILY = "自動提前到期贖回價格為下表所列之期初進場價格"
KO_WHERE = "第 14 項每期 KO 表"
# 每期 KO 表的表頭（範本規格 §1）：D 型對第 12 項 (6) 配息觀察期間表，P 型對第 12 項 (7) 自動提前到期觀察日表
HEADS = {
    "D": "配息觀察期間(t)配息觀察期間開始日配息觀察期間結束日自動提前到期贖回價格",
    "P": "配息觀察期間(t)自動提前到期觀察日(t)自動提前到期贖回價格",
}
KO_CELL = r"(\d+(?:\.\d+)?)%期初進場價格"
ROWS = {
    "D": re.compile(r"(\d{1,2})" + natixis.D * 2 + KO_CELL),
    "P": re.compile(r"(\d{1,2})" + natixis.D + KO_CELL),
}
TABLE_END = "自動提前到期贖回金額"  # 表後的下一句

# 第 9 項與第 16 項情境價格表：沒有 KO 欄（範本規格 §1）
PRICE_TABLE = replace(natixis.PRICE_TABLE, columns=tuple(c for c in natixis.PRICE_TABLE.columns if c.key != "ko"))
VARIANT = replace(natixis.FCN, table=PRICE_TABLE)


@dataclass(frozen=True)
class KoRow:
    """每期 KO 表的一列：期別 `t`、日期（D：開始日、結束日；P：自動提前到期觀察日）、KO%。"""

    t: int
    dates: tuple
    pct: Decimal
    lines: tuple[Line, ...]


@dataclass(frozen=True)
class KoTable:
    kind: str  # D／P（依表頭）
    rows: tuple[KoRow, ...]


def _article(doc: Document, n: int) -> list[Line]:
    return doc.span_lines(doc.articles(1).get(n)) if 1 in doc.chapters else []


def ko_table(art14: Sequence[Line]) -> ParsedField:
    """第 14 項每期 KO 表：表頭決定 D／P，之後每列「t＋日期＋N.NN%期初進場價格」，到「自動提前到期贖回金額」為止。
    期別須連續；表內還有讀不到的 KO 格 → 不合法。"""
    ti = TextIndex(list(art14))
    text = ti.text
    a = text.find(FAMILY)
    if a < 0:
        return ParsedField.missing("ko_table", f"第 14 項沒有「{FAMILY}」")
    found = [(kind, text.find(head, a)) for kind, head in HEADS.items()]
    found = [(kind, i) for kind, i in found if i >= 0]
    if len(found) != 1:
        return ParsedField.missing(
            "ko_table", f"{KO_WHERE}的表頭讀不到（要有「配息觀察期間(t)…自動提前到期贖回價格」）"
        )
    kind, i = found[0]
    start = i + len(HEADS[kind])
    end = text.find(TABLE_END, start)
    end = len(text) if end < 0 else end
    matches = list(ROWS[kind].finditer(text, start, end))
    ev = ti.lines_for(a, end)
    rows = tuple(
        KoRow(
            int(m[1]),
            tuple(parse_date(x) for x in m.groups()[1:-1]),
            Decimal(m.groups()[-1]),
            tuple(ti.lines_for(m.start(), m.end())),
        )
        for m in matches
    )
    if not rows:
        return ParsedField.invalid("ko_table", ev, f"{KO_WHERE}讀不到任何一列")
    if text.count("期初進場價格", start, end) != len(rows) or any(None in r.dates for r in rows):
        return ParsedField.invalid(
            "ko_table", ev, f"{KO_WHERE}有讀不到的列（每列要有期別、日期與「N.NN%期初進場價格」）"
        )
    ts = [r.t for r in rows]
    if ts != list(range(ts[0], ts[0] + len(ts))):
        return ParsedField.invalid("ko_table", ev, f"{KO_WHERE}的期別不連續：" + "、".join(map(str, ts)))
    return ParsedField.present("ko_table", KoTable(kind, rows), ev)


def _ko_terms(table: ParsedField) -> dict[str, ParsedField]:
    if not table.ok:
        return {key: derived(key, table) for key in stepdown.KEYS}
    return stepdown.ko_terms([(r.pct, list(r.lines)) for r in table.value.rows], KO_WHERE)


def expected_periods(fields: dict[str, ParsedField]) -> tuple[str, list[int]] | str:
    """第 12 項的 KO 型態與每期 KO 表應有的期別（D：第 k 期到末期；P：自動提前到期觀察日表的 t）；讀不到時回傳說明。"""
    table, wording = fields.get("date_table"), fields.get("ko_wording")
    if table is None or wording is None or not (table.ok and wording.ok):
        return "第 12 項日期表或自動提前到期觀察日的寫法讀不到，無法對齊每期 KO 表"
    kind = table.value["kind"]
    if kind == "P":
        return kind, [r["t"] for r in table.value["ko_rows"]]
    k = wording.value[1]
    if not k:
        return "第 12 項自動提前到期觀察日的起始期讀不到，無法對齊每期 KO 表"
    return kind, list(range(k, len(table.value["rows"]) + 1))


def alignment_problem(table: KoTable, fields: dict[str, ParsedField]) -> str:
    """每期 KO 表的表頭型態、期別和第 12 項對不上時的說明（範本不支援）；對得上時空字串。"""
    expected = expected_periods(fields)
    if isinstance(expected, str):
        return expected
    kind, periods = expected
    if table.kind != kind:
        return f"{KO_WHERE}是{table.kind} 型表頭，第 12 項卻是 {kind} 型"
    ts = [r.t for r in table.rows]
    if ts != periods:
        shown = "、".join(map(str, ts))
        return f"{KO_WHERE}的期別（{shown}）和第 12 項應有的期別（" + "、".join(map(str, periods)) + "）對不上"
    return ""


def version_problems(doc: Document) -> list[str]:
    """商品家族標記成立後，現行寫法（範本規格 §4）中不成立的項目（每期 KO 表讀不到也算）。"""
    art14 = _article(doc, 14)
    text14 = TextIndex(art14).text
    out = []
    if re.search(natixis.KO_DEF, text14):
        out.append("第 14 項仍有固定 % 的 KO 定義句（「自動提前到期贖回價格為期初進場價格之Y%」）")
    if natixis.MEMORY not in text14:
        out.append("第 14 項不是記憶式寫法（非記憶式沒有樣本）")
    if "自動提前到期贖回價格" in TextIndex(_article(doc, 9)).text:
        out.append("第 9 項連結標的表有自動提前到期贖回價格欄")
    table = ko_table(art14)
    if not table.ok:
        out.append(f"讀不到{KO_WHERE}（{table.note or table.status.value}）")
        return out
    fields: dict[str, ParsedField] = {}
    natixis.article12(fields, _article(doc, 12))
    if problem := alignment_problem(table.value, fields):
        out.append(problem)
    elif problem := stepdown.step_problem(_ko_terms(table)):
        out.append(problem)
    return out


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §4）：相容字轉成標準字後，Natixis 說明書骨架、英文名稱與 Stepdown 標記都成立，現行寫法全部成立才是
    本範本；缺任一項 → 不支援的範本版本。有 Stepdown 標記、但骨架不成立的文件也停在本範本（不退回 DACN、FCN 範本）。"""
    sk = natixis.skeleton(compat_normalized(lines))
    failed = list(sk.failed)
    if not (sk.en.startswith("Natixis–") and squash(natixis.NAME_EN_MARK) in sk.en):
        failed.append(f"封面商品英文名稱沒有「Natixis –」與「{natixis.NAME_EN_MARK}」")
    if FAMILY not in TextIndex(_article(sk.document, 14)).text:
        return DetectionResult(False, [*failed, f"第 14 項沒有「{FAMILY}」（不是 Stepdown）"], sk.evidence)
    if failed:
        return DetectionResult(False, failed, sk.evidence, claimed=True)
    if problems := version_problems(sk.document):
        return DetectionResult(False, problems, sk.evidence, unsupported=f"{UNSUPPORTED}：{'；'.join(problems)}")
    return DetectionResult(True, [], sk.evidence)


def read(lines: Sequence[Line]) -> natixis.NatixisTermSheet:
    """相容字轉成標準字後照 Natixis FCN 讀出（價格表沒有 KO 欄），再換掉 KO%、各標的 KO 價（第 1 期）與每日型的比價日。"""
    ts = natixis.read(compat_normalized(lines), VARIANT)
    fields = dict(ts.fields)
    table = ko_table(_article(ts.document, 14))
    fields["ko_table"] = table
    fields.update(stepdown.matching_periods(_ko_terms(table), fields["autocall_schedule"]))
    fields["ko_def_pct"] = standard_fields.absent("ko_def_pct", "固定的 KO %（第 14 項是每期 KO 表）")
    fields["underlying_prices"] = stepdown.with_ko_prices(fields["underlying_prices"], fields["ko_pct"])
    fields["autocall_schedule"] = stepdown.every_period(fields["autocall_schedule"], fields["schedule_table"])
    return replace(ts, fields=fields)
