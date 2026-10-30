"""SG DACN 中文產品說明書（docs/checker/templates/sg-zh-dacn.md）：版面、封面、標的表、日期表、第二章以後同 SG FCN 說明書
（parsers/sg.py），先照它讀出，再改讀區間累積配息的配息段落（第 8 項配息公式、配息率與年配息率、n(i)／N(i) 定義）與第 15 項
各情境的期別。

依第 8 項的 n(i)／N(i) 定義與「×配息率×[n(i)/N(i)]」配息公式辨識商品家族（不看標題，workflow #366 決定 10）；只支援現行版範本：
認得出是 SG DACN、但現行版的固定寫法任一段不同或訂價日早於 2026-06-01 時，辨識交出「不支援的範本版本」（範本規格 §1、§8）。
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Sequence
from decimal import Decimal
from typing import NamedTuple

from .. import standard_fields
from ..schema import DetectionResult, Evidence, Line, ParsedField
from ..standard_fields import ScenarioParam
from ..text import squash
from . import money, sg
from .common import Mention, derived
from .layout import TextIndex, capture, join_text, parse_date

TEMPLATE_ID = "sg-zh-dacn-pd"
PARSER_VERSION = "1"
OLD_VERSION = "SG DACN 說明書的寫法和現行版（2026-06 以後）不同"
FIRST_SUPPORTED = dt.date(2026, 6, 1)  # 訂價日早於這天的是舊版範本（沒有樣本，範本規格 §1；待使用者確認）
NAME_EN_TAIL = "Range Accrual Equity Linked Notes (Non Principal Guaranteed and Unsecured)"
NAME_ZH_MARK = "區間累積配息"
FAMILY = "×配息率×[n(i)/N(i)]"  # 商品家族：計息期的配息公式（範本規格 §8 條件 5）
# 範本規格 §2.1 的五段固定寫法（去空白後依序出現；a、b、g、T 是數字）
FORMULA = r"第1(?:~(\d+))?個配息金額＝每單位面額×配息率第(\d+)~(\d+)個配息金額＝每單位面額×配息率×\[n\(i\)/N\(i\)\]"
ACCRUED = r"n\(i\)\(i=(\d+)-(\d+)\)=" + re.escape(
    "於第i個配息週期內對所有連結標的而言均為預定交易日之日數內，所有連結標的收盤價皆大於或等於其執行價的預定交易日"
    "（應該視為評價日並受限於“連結標的中斷日之後果”條款(定義如後述)）總日數"
)
KO_PERIOD = re.escape(
    "若某一個配息週期發生自動提前出場事件，該配息週期之n(i)僅計算至發生該自動提前出場事件之日（含），"
    "並於自動提前出場日支付該配息週期之配息金額。"
)
TOTAL = r"N\(i\)\(i=(\d+)-(\d+)\)=" + re.escape("於第i個配息週期內對所有連結標的而言均為預定交易日之總日數")
PERIOD = re.escape("第i個配息週期=自第i個配息週期起始日（不含）至第i個配息週期終止日（含）")
WORDINGS = (
    ("第 8 項配息公式", FORMULA),
    ("第 8 項 n(i) 定義", ACCRUED),
    ("第 8 項自動提前出場那一期的 n(i) 句", KO_PERIOD),
    ("第 8 項 N(i) 定義", TOTAL),
    ("第 8 項配息週期定義", PERIOD),
)
RATE = r"配息率=\d+(?:\.\d+)?%（相當於年配息率\d+(?:\.\d+)?%）"
MONTHLY = r"配息率=(\d+(?:\.\d+)?)%（相當於年配息率"
ANNUAL = r"（相當於年配息率(\d+(?:\.\d+)?)%）"
TRADE_DATE = r"訂價日：" + sg.D
# 第 15 項情境的配息算式：保證期「第a~b個配息金額 = U單位面額 × 面額 × X% =」、計息期另乘「×(n/N)」（範本規格 §4.3）
U = money.CURRENCY
_CALC = rf"第(\d+)(?:~(\d+))?個配息金額=\d+單位面額[xX×][\d,]+(?:\.\d+)?{U}[xX×](\d+(?:\.\d+)?)%"
GUARANTEED_CALC = _CALC + "="
ACCRUAL_CALC = _CALC + r"[xX×]\(\d+/\d+\)="


# ---------------------------------------------------------------- 辨識


def is_accrual(art8: str) -> bool:
    """商品家族（區間累積配息，範本規格 §8 條件 5）：第 8 項有 n(i)、N(i) 兩個定義，且配息公式有「×配息率×[n(i)/N(i)]」。"""
    return all(x in art8 for x in sg.ACCRUAL_DEFINITIONS) and FAMILY in art8


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §8）：SG 骨架（條件 1～4）與區間累積配息（條件 5）都成立後，現行版特徵全部成立才是本範本；
    缺任一項 → 不支援的範本版本。"""
    sk = sg.skeleton(lines)
    art8 = TextIndex(sk.article(8)).text
    failed = list(sk.failed)
    if not is_accrual(art8):
        failed.append(f"第 8 項沒有區間累積配息的「n(i)(i=」「N(i)(i=」定義與「{FAMILY}」配息公式")
    if failed:
        return DetectionResult(False, failed, sk.evidence)
    missing = version_problems(sk, art8)
    if missing:
        return DetectionResult(False, missing, sk.evidence, unsupported=f"{OLD_VERSION}：{'；'.join(missing)}")
    return DetectionResult(True, [], sk.evidence)


def version_problems(sk: sg.Skeleton, art8: str) -> list[str]:
    """現行版特徵（範本規格 §8）中不成立的項目。"""
    out, at = [], 0
    for where, pattern in WORDINGS:
        m = re.compile(pattern).search(art8, at)
        if m is None:
            out.append(f"{where}和現行版不同")
        else:
            at = m.end()
    if len(re.findall(RATE, art8)) != 1:
        out.append("第 8 項「配息率 = X%（相當於年配息率Y%）」不是剛好一處")
    en = sk.en
    if not (en.startswith(squash(sg.ISSUER_EN)) and squash(NAME_EN_TAIL) in en):
        out.append(f"商品英文名稱不是 SG ISSUER … {NAME_EN_TAIL}")
    if NAME_ZH_MARK not in sk.zh:
        out.append(f"商品中文名稱沒有「{NAME_ZH_MARK}」")
    m = re.search(TRADE_DATE, TextIndex(sk.article(12)).text)
    trade = parse_date(m[1]) if m else None
    if trade is None:
        out.append("第 12 項讀不到訂價日")
    elif trade < FIRST_SUPPORTED:
        out.append(f"訂價日 {trade} 早於 {FIRST_SUPPORTED}（只支援現行版範本）")
    return out


# ---------------------------------------------------------------- 讀出


class ScenarioTerms(NamedTuple):
    """第 15 項一個情境的標題與配息算式期別（`(第一期, 最後一期)`；只寫一期時兩者相同）。"""

    number: str
    title: str
    guaranteed: tuple[tuple[int, int], ...]  # 保證期配息算式
    accrual: tuple[tuple[int, int], ...]  # 計息期配息算式（×(n/N)）
    lines: tuple[Line, ...]


def read(lines: Sequence[Line]) -> sg.SgTermSheet:
    """先照 SG FCN 讀出，再改讀配息段落（範本規格 §3）：年利率與配息率改讀「配息率 = X%（相當於年配息率Y%）」；另交配息門檻、
    保證期數、計息範圍與情境期別，按比例條款（範本沒有）交不適用。"""
    base = sg.read(lines)
    doc = base.document
    fields = dict(base.fields)
    fields["cover_title"] = _cover_title(doc.before_chapter1())
    art8 = doc.span_lines(doc.articles(1).get(8))
    t8 = TextIndex(art8)
    fields["monthly_coupon_pct"] = capture("monthly_coupon_pct", t8, MONTHLY, Decimal, whole_match=True)
    fields["coupon_pa_pct"] = capture("coupon_pa_pct", t8, ANNUAL, Decimal, whole_match=True)
    fields["prorate_range"] = standard_fields.absent("prorate_range", "配息按比例條款（DACN 改寫 n(i) 只算到出場當日）")
    fields["coupon_barrier_pct"] = _coupon_barrier(t8, fields["strike_pct"])
    fields["guaranteed_periods"], fields["accrual_ranges"] = _periods(t8)
    _scenarios(fields, base.scenarios)
    mentions = [Mention.of_field("第 8 項配息率", fields["monthly_coupon_pct"])]
    return sg.SgTermSheet(fields, base.full_text, doc, base.scenarios, mentions)


def _cover_title(cover: list[Line]) -> ParsedField:
    """封面標題名稱：「中文產品說明書」之後到代號句「TDCC商品代號…」之前的行（DACN 名稱較長，封面標題換成兩行）。"""
    name = "cover_title"
    start = next((i for i, ln in enumerate(cover) if squash(ln.text) == sg.COVER_TITLE), None)
    if start is None:
        return ParsedField.missing(name, f"找不到封面「{sg.COVER_TITLE}」")
    title = []
    for ln in cover[start + 1 :]:
        if squash(ln.text).startswith("TDCC"):
            break
        if ln.text.strip():
            title.append(ln)
    if not title:
        return ParsedField.missing(name, f"找不到封面標題名稱（「{sg.COVER_TITLE}」到代號句之間）")
    return ParsedField.present(name, join_text(title).strip(), title)


def _coupon_barrier(t8: TextIndex, strike: ParsedField) -> ParsedField:
    """n(i) 比的是「其執行價」→ 配息門檻 = 執行價 %（範本規格 §4.1）；證據掛定義句與「執行價：」行。"""
    name = "coupon_barrier_pct"
    m = next(iter(t8.finditer(ACCRUED)), None)
    if m is None:
        return ParsedField.missing(name, "第 8 項找不到 n(i) 定義的現行版寫法")
    pf = derived(name, strike, strike.value)
    lns = t8.lines_for(m.start(), m.end())
    return ParsedField(name, pf.status, pf.value, [*map(Evidence.of, lns), *pf.evidence], note=pf.note)


def _periods(t8: TextIndex) -> tuple[ParsedField, ParsedField]:
    """配息公式第一行的保證期數 g（`第1個` → 1、`第1~g個` → g）；配息公式第二行、n(i)、N(i) 的期別範圍（範本規格 §4.2）。"""
    formula = list(t8.finditer(FORMULA))
    if len(formula) != 1:
        note = "第 8 項配息公式（第1~g個配息金額、第a~T個配息金額）缺漏、重複或不在範本規格內"
        return ParsedField.missing("guaranteed_periods", note), ParsedField.missing("accrual_ranges", note)
    m = formula[0]
    lns = t8.lines_for(m.start(), m.end())
    guaranteed = ParsedField.present("guaranteed_periods", int(m[1]) if m[1] else 1, lns)
    ranges, evidence = [("第 8 項配息公式第二行", int(m[2]), int(m[3]))], list(lns)
    for where, pattern in (("第 8 項 n(i)", ACCRUED), ("第 8 項 N(i)", TOTAL)):
        found = list(t8.finditer(pattern))
        if len(found) != 1:
            return guaranteed, ParsedField.missing("accrual_ranges", f"{where}定義缺漏、重複或不在範本規格內")
        x = found[0]
        ranges.append((where, int(x[1]), int(x[2])))
        evidence += t8.lines_for(x.start(), x.end())
    return guaranteed, ParsedField.present("accrual_ranges", tuple(ranges), evidence)


def _span(m: re.Match) -> tuple[int, int]:
    first = int(m[1])
    return first, int(m[2]) if m[2] else first


def _scenarios(fields: dict[str, ParsedField], sec) -> None:
    """第 15 項每個情境的標題與保證期、計息期配息算式期別（`scenario_terms`）；計息期算式的配息率加進共用情境規則的
    `scenario_params`（保證期算式與情境一、二年化已由 SG FCN 讀法交出）。"""
    terms, params = [], list(fields["scenario_params"].value) if fields["scenario_params"].ok else None
    for s in sec.scenarios:
        ti = TextIndex(s.lines)
        guaranteed = tuple(_span(m) for m in ti.finditer(GUARANTEED_CALC))
        accrual = list(ti.finditer(ACCRUAL_CALC))
        terms.append(ScenarioTerms(s.number, squash(s.title), guaranteed, tuple(map(_span, accrual)), tuple(s.lines)))
        if params is None:
            continue
        where = f"第 15 項情境{s.number}計息期配息算式"
        for m in accrual:
            mention = Mention(where, Decimal(m[3]), tuple(map(Evidence.of, ti.lines_for(m.start(), m.end()))))
            params.append(
                ScenarioParam("scenario_monthly_coupon", f"{where}的", mention.parsed("scenario_monthly_coupon"))
            )
    if params is not None:
        fields["scenario_params"] = standard_fields.occurrences("scenario_params", params)
    fields["scenario_terms"] = (
        ParsedField.present("scenario_terms", tuple(terms), [s.lines[0] for s in sec.scenarios])
        if terms
        else ParsedField.missing("scenario_terms", "第 15 項找不到「情境X：」段落")
    )
