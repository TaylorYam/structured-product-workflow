"""Natixis DACN 中文產品說明書（docs/checker/templates/natixis-zh-dacn.md）：版面、封面、價格表、日期表、第二章以後同 Natixis FCN
說明書（parsers/natixis.py），用 DACN 的讀法（`VARIANT`：價格表欄的關鍵字、情境配息算式）照它讀出，再另讀配息門檻、日期表的
保證期數與各情境配息算式的保證期／計息期。

依第 13 項的「變動配息金額」公式與 n 定義（配息界限價格）辨識商品家族（不看標題，workflow #366 決定 10）；只支援現行版範本：
認得出是 Natixis DACN、但現行版特徵任一項不成立或交易日早於 2026-06-01 時，辨識交出「不支援的範本版本」（範本規格 §1、§8）。
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal
from typing import NamedTuple

from ..schema import DetectionResult, Evidence, Line, ParsedField
from ..standard_fields import ScenarioParam
from ..text import squash
from . import natixis, price_table
from .common import Mention
from .layout import TextIndex, parse_date

TEMPLATE_ID = "natixis-zh-dacn-pd"
PARSER_VERSION = "1"
OLD_VERSION = "Natixis DACN 說明書的寫法和現行版（2026-06 以後）不同"
FIRST_SUPPORTED = dt.date(2026, 6, 1)  # 交易日早於這天的是舊版範本（沒有樣本，範本規格 §1；待使用者確認）
NAME_EN_MARK = "Autocallable Daily Range Accrual Note Linked to Basket of Equities"
NAME_ZH_MARK = "區間計息(自動提前到期)"
# 商品家族（範本規格 §8 條件 2）：第 13 項的變動配息金額公式與 n 定義
FAMILY = ("「變動配息金額」=", "所有連結標的收盤價皆高於或等於配息界限價格之配息觀察日天數")
_FIXED = re.escape("「固定配息金額」=配息觀察期間結束日持有之商品單位數x每單位商品面額x月配息率")
_N_TOTAL = re.escape("N係指相關配息觀察期間內的總配息觀察日天數；n係指該相關配息觀察期間內截至")
# 範本規格 §2.1 的七段固定寫法（去空白後依序出現）；第 6 段現行版寫「配息界線價格」，更正成「界限」也收（待使用者確認）
WORDINGS = (
    ("第 13 項 a. 固定配息金額", _FIXED),
    (
        "第 13 項 a. 變動配息金額",
        re.escape("「變動配息金額」=配息觀察期間結束日持有之商品單位數x每單位商品面額x月配息率x(n/N)"),
    ),
    (
        "第 13 項 a. n、N 定義",
        _N_TOTAL + re.escape("該配息觀察期間結束日(含)，所有連結標的收盤價皆高於或等於配息界限價格之配息觀察日天數。"),
    ),
    ("第 13 項 b. 固定配息金額", re.escape("b.若該配息觀察期間發生自動提前到期事件：") + ".*?" + _FIXED),
    (
        "第 13 項 b. 變動配息金額",
        re.escape(
            "「變動配息金額」=發生自動提前到期事件之自動提前到期觀察日持有之商品單位數x每單位商品面額x月配息率x(n/N)，其中："
        ),
    ),
    (
        "第 13 項 b. n、N 定義",
        _N_TOTAL
        + re.escape("自動提前到期事件發生之自動提前到期觀察日(含)，所有連結標的收盤價皆高於或等於配息界")
        + "[限線]"
        + re.escape("價格之配息觀察日天數。"),
    ),
    ("第 13 項配息觀察日定義", re.escape("「配息觀察日」係指於配息觀察期間之各預定交易日，並依「中斷日」進行調整。")),
)
BARRIER_HEAD = "配息界限價格(每日觀察)/執行價格"  # 第 9 項價格表執行價格欄的欄頭（一欄兩用）
DATE_HEAD = "配息觀察期間(t)配息觀察期間開始日配息觀察期間結束日配息日配息金額"
TRADE_DATE = r"交易日/期初進場日：" + natixis.D
# 日期表每列：期別、三個日期、配息金額種類（保證期「固定」、計息期「變動」）
KIND_ROW = re.compile(r"(\d{1,2})" + natixis.D * 3 + r"(固定|變動)配息金額")
GUARANTEED, ACCRUAL = "固定", "變動"
# 第 9 項價格表與第 16 項情境價格表：界限價格（KI）欄要用「界限價格(期初」認（執行價格欄的欄頭也含「界限價格」）
TABLE = replace(
    natixis.PRICE_TABLE,
    columns=(
        price_table.Column("ko", "自動提前到期"),
        price_table.Column("ki", "界限價格(期初", required=False),
        price_table.Column("strike", "執行價格"),
    ),
)
# 第 16 項情境配息算式「面額 幣別 x X% = 」（保證期）或「面額 幣別 x X% x (n/N) = 」（計息期），在原文行上讀
COUPON_ROW = re.compile(
    rf"(?:^|\s)({natixis.NUMBER})\s*{natixis.CCY}\s*x\s*(\d+(?:\.\d+)?)%(\s*x\s*\(\s*\d+\s*/\s*\d+\s*\))?\s*="
)


def coupon_terms(ti: TextIndex) -> list[ScenarioParam]:
    """各情境配息算式的 X（共用情境規則和第 13 項月配息率比）：每個情境同一個值一筆；找不到情境或算式時一筆讀不到。"""
    code, out = "scenario_monthly_coupon", []
    for case, a, b in natixis.scenario_spans(ti):
        found: dict[Decimal, list[Line]] = {}
        for ln in ti.lines_for(a, b):
            if m := COUPON_ROW.search(ln.text):
                found.setdefault(Decimal(m[2]), []).append(ln)
        for value, lns in found.items():
            mention = Mention(f"第 16 項{case}配息算式", value, tuple(map(Evidence.of, lns)))
            out.append(ScenarioParam(code, f"第 16 項{case}配息算式的", mention.parsed(code)))
    return out or [
        ScenarioParam(code, "第 16 項情境配息算式的", ParsedField.missing(code, "情境找不到配息算式「面額 x X% =」"))
    ]


# 情境一以外的平均年化報酬率是假設計息一半天數的結果，不是年利率（範本規格 §4.3）
VARIANT = natixis.Variant(TABLE, COUPON_ROW, coupon_terms, annualized_cases=1)


# ---------------------------------------------------------------- 辨識


def is_accrual(art13: str) -> bool:
    """商品家族（區間計息，範本規格 §8 條件 2）。"""
    return all(x in art13 for x in FAMILY)


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §8）：Natixis 骨架（`natixis.skeleton`）與區間計息（條件 2）都成立後，現行版特徵全部成立才是本範本；
    缺任一項 → 不支援的範本版本。"""
    sk = natixis.skeleton(lines)
    doc = sk.document
    arts = doc.articles(1)

    def text(n: int) -> str:
        return TextIndex(doc.span_lines(arts.get(n))).text

    art13 = text(13)
    failed = list(sk.failed)
    if not is_accrual(art13):
        failed.append(f"第 13 項沒有區間計息的「{FAMILY[0]}」公式與「{FAMILY[1]}」定義")
    if failed:
        return DetectionResult(False, failed, sk.evidence)
    table = natixis.underlying_table("underlying_table", doc.span_lines(arts.get(9)), TABLE)
    missing = version_problems(sk, art13, table, text(12))
    if missing:
        return DetectionResult(False, missing, sk.evidence, unsupported=f"{OLD_VERSION}：{'；'.join(missing)}")
    return DetectionResult(True, [], sk.evidence)


def version_problems(sk: natixis.Skeleton, art13: str, table: ParsedField, art12: str) -> list[str]:
    """現行版特徵（範本規格 §8）中不成立的項目。第 9 項價格表讀不到時不在這裡判（由讀出結果轉人工覆核）。"""
    out, at = [], 0
    for where, pattern in WORDINGS:
        m = re.compile(pattern).search(art13, at)
        if m is None:
            out.append(f"{where}的寫法和現行版不同")
        else:
            at = m.end()
    if NAME_ZH_MARK not in sk.zh:
        out.append(f"封面商品中文名稱沒有「{NAME_ZH_MARK}」")
    if not (sk.en.startswith("Natixis–") and squash(NAME_EN_MARK) in sk.en):
        out.append(f"封面商品英文名稱沒有「Natixis –」與「{NAME_EN_MARK}」")
    if table.ok and BARRIER_HEAD not in table.value.heads.get("strike", ""):
        out.append(f"第 9 項價格表執行價格欄的欄頭沒有「{BARRIER_HEAD}」")
    if DATE_HEAD not in art12:
        out.append("第 12 項日期表沒有「配息金額」欄")
    m = re.search(TRADE_DATE, art12)
    trade = parse_date(m[1]) if m else None
    if trade is None:
        out.append("第 12 項讀不到交易日")
    elif trade < FIRST_SUPPORTED:
        out.append(f"交易日 {trade} 早於 {FIRST_SUPPORTED}（只支援現行版範本）")
    return out


# ---------------------------------------------------------------- 讀出


class ScenarioRows(NamedTuple):
    """第 16 項一個情境的配息算式：每列是保證期（`固定`）或計息期（`變動`，有「x (n/N)」），依序。"""

    case: str
    kinds: tuple[str, ...]
    lines: tuple[Line, ...]


def read(lines: Sequence[Line]) -> natixis.NatixisTermSheet:
    """用 DACN 的讀法照 Natixis FCN 讀出（`VARIANT`），另交配息門檻、保證期數與各期配息種類、各情境配息算式的種類。"""
    base = natixis.read(lines, VARIANT)
    doc = base.document
    arts = doc.articles(1)
    fields = dict(base.fields)
    fields["coupon_barrier_pct"] = _coupon_barrier(fields["underlying_table"])
    fields["guaranteed_periods"], fields["coupon_kinds"] = _coupon_kinds(doc.span_lines(arts.get(12)))
    fields["scenario_coupon_rows"] = _scenario_rows(doc.span_lines(arts.get(16)))
    return natixis.NatixisTermSheet(fields, base.full_text, doc)


def _coupon_barrier(table: ParsedField) -> ParsedField:
    """配息門檻 = 第 9 項價格表「配息界限價格(每日觀察)/執行價格」欄頭 %（範本規格 §4.1；說明書沒有配息界限價格的定義句）。"""
    name = "coupon_barrier_pct"
    if not table.ok:
        return ParsedField(name, table.status, None, list(table.evidence), note=table.note)
    value = table.value
    head, pct = value.heads.get("strike", ""), value.pcts.get("strike")
    lines = list(value.head_lines.get("strike", ()))
    if BARRIER_HEAD not in head:
        return ParsedField.invalid(name, lines, f"第 9 項價格表執行價格欄的欄頭沒有「{BARRIER_HEAD}」，讀不到配息門檻")
    if pct is None:
        return ParsedField.invalid(name, lines, f"第 9 項價格表「{BARRIER_HEAD}」欄頭沒有寫 %")
    return ParsedField.present(name, pct, lines)


def _coupon_kinds(art12: list[Line]) -> tuple[ParsedField, ParsedField]:
    """第 12 項日期表每列的配息金額種類（`固定`／`變動`）與保證期數 g（從第 1 期起連續 `固定` 的列數；範本規格 §4.2）。
    期別不是從 1 連續、讀不到，或 `固定` 不是只在開頭 → 讀不到（轉人工覆核）。"""
    ti = TextIndex(art12)
    a = ti.text.find(DATE_HEAD)
    rows = []
    for m in KIND_ROW.finditer(ti.text, a if a >= 0 else 0):
        if int(m[1]) != len(rows) + 1:
            break
        rows.append(m)
    if a < 0 or not rows:
        note = "第 12 項日期表找不到「配息金額」欄或每列的「固定配息金額」「變動配息金額」"
        return ParsedField.missing("guaranteed_periods", note), ParsedField.missing("coupon_kinds", note)
    kinds = tuple(m[5] for m in rows)
    evidence = ti.lines_for(rows[0].start(), rows[-1].end())
    g = next((n for n, k in enumerate(kinds) if k != GUARANTEED), len(kinds))
    if any(k == GUARANTEED for k in kinds[g:]):
        note = "第 12 項日期表的「固定配息金額」不是從第 1 期起連續（中間夾著「變動配息金額」）"
        return ParsedField.invalid("guaranteed_periods", evidence, note), ParsedField.present(
            "coupon_kinds", kinds, evidence
        )
    return ParsedField.present("guaranteed_periods", g, evidence), ParsedField.present("coupon_kinds", kinds, evidence)


def _scenario_rows(art16: list[Line]) -> ParsedField:
    """第 16 項每個情境的配息算式種類（範本規格 §4.3）。"""
    ti = TextIndex(art16)
    spans = natixis.scenario_spans(ti)
    if not spans:
        return ParsedField.missing("scenario_coupon_rows", "第 16 項找不到情境一～三（或四）的段落")
    out, evidence = [], []
    for case, a, b in spans:
        hits = [(ln, m) for ln in ti.lines_for(a, b) if (m := COUPON_ROW.search(ln.text))]
        kinds = tuple(ACCRUAL if m[3] else GUARANTEED for _, m in hits)
        out.append(ScenarioRows(case, kinds, tuple(ln for ln, _ in hits)))
        evidence += [ln for ln, _ in hits[:1]]
    return ParsedField.present("scenario_coupon_rows", tuple(out), evidence)
