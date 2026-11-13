"""CA FCN_Stepdown 中文產品說明書（docs/checker/templates/ca-zh-stepdown.md，workflow #546）。

名稱、骨架和 CA FCN（`parsers/ca.py`）相同，差別在 KO：第 9 項 (1)(b) 自動提前出場事件後多一張每期一列的自動提前出場價表
（`期初訂價之N.NN%`，每期固定往下降）與觀察期定義句；第 9 項 (2) 價格表與第 16 項情境價格表沒有 KO 欄；記憶式兩句另有
「相關自動提前出場期所適用之」「無須於同一預定交易日或同一自動提前出場觀察期」的寫法。讀出沿用 `ca.read`（價格表宣告拿掉
KO 欄、記憶式收新寫法），再換掉 KO%、各標的 KO 價與每日型的比價日（共用讀法 `parsers/stepdown.py`）。每期 KO 表的 `i` 須從
1 起連續、列數 = 配息表列數（對不上是範本不支援）；同一期結束日和配息表不同由規則 `doc.ko_table_dates` 判不一致。
投資人須知沒有可靠的 Stepdown 寫法，用 CA FCN 的投資人須知範本（`parsers/ca_iis.py`）。
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from ..schema import DetectionResult, Line, ParsedField
from . import ca, price_table, stepdown
from .common import derived
from .fields_builder import FieldsBuilder
from .layout import Document, TextIndex, parse_date

TEMPLATE_ID = "ca-zh-stepdown-pd"
PARSER_VERSION = "1"
UNSUPPORTED = "CA FCN_Stepdown 的寫法和範本不同"

B, C = "(b)「自動提前出場贖回金額」", "(c)「到期贖回金額」"
# 商品家族標記（範本規格 §4 條件 2，去空白比對）：第 9 項 (1)(b) 有百分比；CA FCN 的這一段沒有任何百分比
FAMILY = re.compile(r"期初訂價之\d+(?:\.\d+)?%")
KO_WHERE = "第 9 項 (1)(b) 每期 KO 表"
# 每期 KO 表表頭（範本規格 §1）：第一欄「自動提前出場觀察期 i」拆行、擷取順序不固定，只看後兩欄；舊範本是 P 型觀察日表多一欄
HEAD = ("自動提前出場觀察期結束日", "自動提前出場價")
OLD_HEAD = ca.KO_TABLE_HEAD + "自動提前出場價"  # i｜自動提前出場觀察日｜自動提前出場日｜自動提前出場價
PERIOD_DEF = "自動提前出場觀察期：自動提前出場觀察期結束日「i-1」"  # 表後的觀察期定義句（現行版面）
KO_CELL = "期初訂價之"
ROW = re.compile(ca.ROW + ca.D + r"\|?" + KO_CELL + r"(\d+\.\d{2})%")  # i＋結束日＋N.NN%（範本規格 §4）

# 第 9 項與第 16 項情境價格表沒有 KO 欄；記憶式第二句另收 Stepdown 的寫法（範本規格 §1、§2.2）
PRICE_TABLE = replace(ca.PRICE_TABLE, columns=tuple(c for c in ca.PRICE_TABLE.columns if c.key != "ko"))
VARIANT = ca.Variant(PRICE_TABLE, memory_same=ca.MEMORY_SAME_WORDINGS)


@dataclass(frozen=True)
class KoRow:
    """每期 KO 表的一列：期別 `i`、自動提前出場觀察期結束日、KO%。"""

    i: int
    end: dt.date
    pct: Decimal
    lines: tuple[Line, ...]


@dataclass(frozen=True)
class KoSection:
    """第 9 項 (1)(b) 從自動提前出場事件之後到觀察期定義句（沒有時到 (c)）之前：表頭與每期 KO 表都在這段。"""

    text: str
    start: int
    index: TextIndex


def _article9(doc: Document) -> list[Line]:
    return doc.span_lines(doc.articles(1).get(9)) if 1 in doc.chapters else []


def _region(art9: Sequence[Line]) -> tuple[TextIndex, int, int]:
    """第 9 項 (1)(b) 的位置（找不到時 -1）。"""
    ti = TextIndex(list(art9), separate_digits=True)
    b = ti.text.find(B)
    c = ti.text.find(C, b) if b >= 0 else -1
    return ti, b, c


def has_family_mark(art9: Sequence[Line]) -> bool:
    """第 9 項 (1)(b) 有 `期初訂價之N%`（商品家族標記）。"""
    ti, b, c = _region(art9)
    return b >= 0 and c >= 0 and FAMILY.search(ti.text, b, c) is not None


def _section(art9: Sequence[Line]) -> KoSection | None:
    ti, b, c = _region(art9)
    if b < 0 or c < 0:
        return None
    events = list(re.finditer(ca.KO_EVENT, ti.text[b:c]))
    start = b + (events[0].end() if len(events) == 1 else 0)
    end = ti.text.find(PERIOD_DEF, start, c)
    return KoSection(ti.text[start : end if end >= 0 else c], start, ti)


def ko_table(art9: Sequence[Line]) -> ParsedField:
    """每期 KO 表：表頭（`HEAD`）後每列「i＋自動提前出場觀察期結束日＋期初訂價之N.NN%」，`i` 須從 1 起連續；表內還有讀不到的
    KO 格 → 不合法。"""
    sec = _section(art9)
    if sec is None:
        return ParsedField.missing("ko_table", "第 9 項找不到 (b) 自動提前出場贖回金額或 (c) 到期贖回金額")
    ev = sec.index.lines_for(sec.start, sec.start + len(sec.text))
    first = ROW.search(sec.text)
    head = sec.text[: first.start()] if first else sec.text
    if not all(h in head for h in HEAD):
        return ParsedField.missing("ko_table", f"{KO_WHERE}的表頭讀不到（要有「{'」「'.join(HEAD)}」）")
    matches = list(ROW.finditer(sec.text))
    if not matches:
        return ParsedField.invalid("ko_table", ev, f"{KO_WHERE}讀不到任何一列")
    if sec.text.count(KO_CELL) != len(matches):
        note = f"{KO_WHERE}有讀不到的列（每列要有期別、結束日與「期初訂價之N.NN%」）"
        return ParsedField.invalid("ko_table", ev, note)
    rows: list[KoRow] = []
    for m in matches:
        if int(m[1]) != len(rows) + 1:
            note = f"{KO_WHERE}的期別不是從 1 起連續：第 {len(rows) + 1} 列寫 {m[1]}"
            return ParsedField.invalid("ko_table", ev, note)
        end = parse_date(m[2])
        if end is None:
            return ParsedField.invalid("ko_table", ev, f"{KO_WHERE}第 {m[1]} 列的結束日讀不到")
        lines = sec.index.lines_for(sec.start + m.start(), sec.start + m.end())
        rows.append(KoRow(int(m[1]), end, Decimal(m[3]), tuple(lines)))
    return ParsedField.present("ko_table", tuple(rows), ev)


def _ko_terms(table: ParsedField) -> dict[str, ParsedField]:
    if not table.ok:
        return {key: derived(key, table) for key in stepdown.KEYS}
    return stepdown.ko_terms([(r.pct, list(r.lines)) for r in table.value], KO_WHERE)


def _head_problem(art9: Sequence[Line]) -> str:
    """每期 KO 表表頭與觀察期定義句不是現行版面時的說明；是現行版面時空字串。"""
    sec = _section(art9)
    if sec is None:
        return f"第 9 項找不到 {B} 或 {C}"
    first = ROW.search(sec.text)
    head = sec.text[: first.start()] if first else sec.text
    if not all(h in head for h in HEAD):
        if OLD_HEAD in head:
            return f"{KO_WHERE}是舊範本的表頭（i｜自動提前出場觀察日｜自動提前出場日｜自動提前出場價）"
        return f"{KO_WHERE}的表頭讀不到（要有「{'」「'.join(HEAD)}」）"
    ti, b, c = _region(art9)
    if PERIOD_DEF not in ti.text[b:c]:
        return (
            "第 9 項 (1)(b) 沒有自動提前出場觀察期的定義句（「自動提前出場觀察期：自動提前出場觀察期結束日「i-1」…」）"
        )
    return ""


def _alignment_problem(table: ParsedField, coupon: ParsedField) -> str:
    """每期 KO 表的期別要是配息表第 1 期到末期（範本規格 §2.1）；對不上時的說明，對得上時空字串。"""
    if not coupon.ok:
        return f"第 9 項配息表讀不到（{coupon.note or coupon.status.value}），無法對齊{KO_WHERE}"
    n = len(coupon.value["rows"])
    got = len(table.value)
    if got != n:
        return f"{KO_WHERE}有 {got} 期，配息表有 {n} 期，期別對不上"
    return ""


def version_problems(doc: Document) -> list[str]:
    """商品家族標記成立後，現行寫法（範本規格 §4）中不成立的項目（每期 KO 表讀不到也算）。"""
    art9 = _article9(doc)
    out = []
    if problem := _head_problem(art9):
        out.append(problem)
    fields = FieldsBuilder()
    ca.article9(fields, art9, VARIANT)
    wording, memory = fields["ko_wording"], fields["ko_memory"]
    if not (wording.ok and wording.value[0] == "D" and wording.value[1] == 1):
        out.append(
            "第 9 項 (1)(b) 自動提前出場觀察日不是「自第1 個配息期結束日(含)…的每一個預定交易日」"
            "（每期觀察、Non-Call ≥ 2 沒有樣本）"
        )
    if not (memory.ok and memory.value is True):
        out.append("第 9 項 (1)(b) 自動提前出場事件不是範本內的記憶式寫法（非記憶式沒有樣本）")
    region = ca.table_region(art9, "(2)連結標的：", "投資人請注意：本商品之收益")
    if price_table.read("ko_column", region, ca.PRICE_TABLE).ok:  # 照 FCN 的表頭（有 KO 欄）讀得出來
        out.append("第 9 項 (2) 價格表有自動提前出場價欄")
    if out:
        return out
    table = ko_table(art9)
    if not table.ok:
        return [f"讀不到{KO_WHERE}（{table.note or table.status.value}）"]
    if problem := _alignment_problem(table, fields["coupon_table"]):
        return [problem]
    if problem := stepdown.step_problem(_ko_terms(table)):
        return [problem]
    return []


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §4）：CA 說明書骨架（`ca.detect`）與 Stepdown 標記都成立，現行寫法全部成立才是本範本；缺任一項
    → 不支援的範本版本。有 Stepdown 標記、但骨架不成立的文件也停在本範本（不退回 BEN、Super Bull、FCN 範本）。"""
    sk = ca.detect(lines)
    doc = ca.document(lines)
    if not has_family_mark(_article9(doc)):
        mark = f"第 9 項 (1)(b) 沒有「{KO_CELL}N.NN%」（不是 Stepdown）"
        return DetectionResult(False, [*sk.failed, mark], sk.evidence)
    if sk.failed:
        return DetectionResult(False, list(sk.failed), sk.evidence, claimed=True)
    if problems := version_problems(doc):
        return DetectionResult(False, problems, sk.evidence, unsupported=f"{UNSUPPORTED}：{'；'.join(problems)}")
    return DetectionResult(True, [], sk.evidence)


def read(lines: Sequence[Line]) -> ca.CaTermSheet:
    """照 CA FCN 讀出（價格表沒有 KO 欄、記憶式收新寫法），再換掉 KO%、各標的 KO 價（第 1 期）與每日型的比價日。"""
    ts = ca.read(lines, VARIANT)
    fields = dict(ts.fields)
    fields["ko_table"] = ko_table(_article9(ts.document))
    stepdown.apply_ko_terms(fields, _ko_terms(fields["ko_table"]))
    return replace(ts, fields=fields)
