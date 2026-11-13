"""BARC DACN 中文產品說明書（docs/checker/templates/barc-zh-dacn.md）：版面、封面、§13、§16 情境、第二章以後同 BARC FCN
說明書（parsers/barc.py），先照它讀出，再改讀 §14 月配息率、配息公式保證期數、配息門檻與配息觀察期表、§15 價格表（多配息門檻
欄、列靠上對齊）與月配息率的其他出處（§9 段末、§16 `× X% × (n/N)`）；範本沒有年利率。§16 一般、最差情況另讀兩段配息算式、
累積配息、最終交割金額（等值）與總報酬率算式（`scenario_general_calc`、`scenario_worst_calc`，workflow #577）。

名稱和 FCN 相同，依 §14 的配息率 t 公式與 nt 定義（比配息門檻）辨識商品家族（不看名稱，workflow #366 決定 10）；只支援
現行版範本：認得出是 BARC DACN、但現行版特徵任一項不成立時，辨識交出「不支援的範本版本」（範本規格 §1、§8）。版本只看寫法、
不看交易日早晚（同第一波 DACN，workflow #409）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import NamedTuple

from .. import standard_fields
from ..schema import DetectionResult, Line, ParsedField
from . import barc, price_table
from . import barc_schedule as schedule
from .common import NUM, Hit, dec, hits, one
from .layout import Document, TextIndex

TEMPLATE_ID = "barc-zh-dacn-pd"
PARSER_VERSION = "1"
OLD_VERSION = "BARC DACN 說明書的寫法和現行版不同"
# 本家族的標記（範本規格 §7、§8）：全文有這幾個字就停在 DACN 範本、不退回 FCN 範本（#436）；BARC FCN 全文沒有
MARK = "配息門檻"
N = r"\d+"
X = r"[\d.]+"
# §14 配息公式（範本規格 §2.1 第 3 段）：第 1～g 期「配息率t=1(到g)=X%」、其後「配息率t=g+1到N=X%×nt/Nt」
RATE = re.compile(rf"「配息率」\(i\)配息率t=1(?:到({N}))?=({X})%;及\(ii\)配息率t=({N})到({N})=({X})%×nt/Nt;")
NT = re.compile(r"「nt」指.+?其配息門檻之配息評價日天數")  # 計息天數比的是配息門檻（範本規格 §8 條件 2）
BARRIER = re.compile(rf"「配息門檻」詳見本章第15條之表格所示（為最初價格的({X})%）。")
# §14 六段固定寫法（範本規格 §2.1，去空白後依序出現）
WORDINGS = (
    (
        "第 14 條配息條件",
        re.escape(
            "除非於相關配息觀察期內之所有配息評價日，一籃子標的資產中任一標的之相關價格小於其配息門檻，以至於配息率為零之"
            "情況外，發行機構將於每一個配息支付日支付依下列公式計算之配息金額。相關配息率t將依配息觀察期t內各標的資產之相關"
            "價格均大於或等於其配息門檻之配息評價日天數而定。"
        ),
    ),
    (
        "第 14 條配息公式與 t 定義",
        re.escape("每單位商品面額×配息率t其中：「t」係自1至")
        + N
        + re.escape("的數字，每個數字代表一個配息支付日及相對應的配息觀察期以及配息率;"),
    ),
    ("第 14 條配息率 t", RATE.pattern),
    (
        "第 14 條 nt 定義",
        re.escape(
            "「nt」指與配息支付日t相對應之配息觀察期內，各標的資產之相關價格均高於或等於其配息門檻之配息評價日天數。"
        ),
    ),
    (
        "第 14 條 Nt 定義",
        re.escape("「Nt」指與配息支付日t相對應之配息觀察期內之一籃子預定交易日天數-如以下「配息觀察期」表格所示。"),
    ),
    ("第 14 條配息門檻定義", BARRIER.pattern),
)
T_RANGE = re.compile(rf"「t」係自1至({N})的數字")
WHERE_14 = "第 14 條"
# §15 價格表：執行價格欄右邊多一欄配息門檻（欄頭有 %）；每列的字靠上對齊，長名稱折成三行時第三行跟著上一列（範本規格 §5）
PRICE_TABLE = replace(
    barc.PRICE_TABLE,
    columns=(*barc.PRICE_TABLE.columns, price_table.Column("barrier", MARK)),
    align="top",
)
# 月配息率的出處（範本規格 §6）：§14 第 1 段、§15(2) 最低保證配息率、§9 段末（§9(3) 相關配息率另由 `repeat` 核對）。
# §14 第 2 段的 X 由 `_rate` 和第 1 段比，不同時月配息率讀不到、轉人工（核對規則 §3.3），不再另判不一致
MONTHLY_PATTERNS = {
    "第14條(i)": rf"「配息率」\(i\)配息率t=1(?:到{N})?=({X})%",
    "第15條(2)": rf"商品面額×({X})%\(顯示",
    "第9條": rf"遠低於月配息率({X})%",
}
# §16 情境試算的月配息率：FCN 的寫法，加上一般、最差情況第 2 期起的「× X% × (n/N)」
SCENARIO_MONTHLY = (*barc.SCENARIO_MONTHLY, rf"[×xX]({X})%(?=[×xX]\(\d+/\d+\))")


# ---------------------------------------------------------------- 辨識


def is_accrual(art14: str) -> bool:
    """商品家族（區間計息，範本規格 §8 條件 2）：§14 有配息率 t 公式與比配息門檻的 nt 定義。"""
    return "配息率t=1" in art14 and "×nt/Nt" in art14 and NT.search(art14) is not None


def _art14(doc: Document) -> TextIndex:
    return TextIndex(doc.span_lines(doc.articles(1).get(14)))


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §8）：BARC 說明書骨架（條件 1，同 FCN）與區間計息（條件 2）都成立後，現行版特徵全部成立才是本
    範本；缺任一項 → 不支援的範本版本。全文有 `MARK` 的文件停在本範本（不退回 FCN 範本）。"""
    doc = barc.document(lines)
    base = barc.detect(doc)
    failed = list(base.failed)
    art14 = _art14(doc)
    if not is_accrual(art14.text):
        failed.append("第 14 條沒有區間計息的「配息率t=1」「×nt/Nt」公式與比配息門檻的「nt」定義")
    if failed:
        claimed = MARK in TextIndex(doc.lines).text
        return DetectionResult(False, failed, base.evidence, claimed=claimed)
    missing = version_problems(doc, art14)
    if missing:
        return DetectionResult(False, missing, base.evidence, unsupported=f"{OLD_VERSION}：{'；'.join(missing)}")
    return DetectionResult(True, [], base.evidence)


def version_problems(doc: Document, art14: TextIndex) -> list[str]:
    """現行版特徵（範本規格 §8）中不成立的項目。"""
    out, at = [], 0
    for where, pattern in WORDINGS:
        m = re.compile(pattern).search(art14.text, at)
        if m is None:
            out.append(f"{where}的寫法和現行版不同")
        else:
            at = m.end()
    if not any("days" in t.columns for t in _tables(art14)):
        out.append("第 14 條沒有配息觀察期表（「預定交易日日數(Nt)」欄）")
    s15 = TextIndex(doc.span_lines(doc.articles(1).get(15)))
    if not re.search(MARK + r"（為最初價格的", s15.text):
        out.append(f"第 15 條價格表表頭沒有「{MARK}（為最初價格的X%）」")
    s13 = doc.subitems(doc.articles(1).get(13))
    trade = barc._s13_date(doc, s13, 2, "trade_date")  # 只看寫法，不看早晚
    if not trade.ok:
        out.append("第 13 條讀不到交易日")
    return out


def _tables(art14: TextIndex) -> list[schedule.Table]:
    return schedule.tables_in(list(art14.lines), schedule.DACN_KEYS, schedule.DACN_HEADER_DY)


# ---------------------------------------------------------------- 讀出


def read(lines: Sequence[Line]) -> barc.BarcTermSheet:
    """照 BARC FCN 讀出後，改讀 DACN 不同的欄位（範本規格 §3）。"""
    base = barc.read(lines)
    doc = base.document
    arts = doc.articles(1)
    f = dict(base.fields)
    art14 = _art14(doc)
    f["coupon_table"] = schedule.coupon_table(_tables(art14), WHERE_14)
    f["monthly_coupon_pct"], f["coupon_guaranteed_periods"] = _rate(art14, f["coupon_table"])
    f["coupon_pa_pct"] = standard_fields.absent("coupon_pa_pct", "年利率（BARC DACN 改由月配息率推算核對）")
    f["coupon_barrier_pct"] = barc._distinct(
        "coupon_barrier_pct",
        [(Decimal(m[1]), art14.lines_for(m.start(), m.end())) for m in art14.finditer(BARRIER)],
        f"{WHERE_14}找不到「「配息門檻」詳見本章第15條之表格所示（為最初價格的Y%）」",
    )
    s15 = doc.span_lines(arts.get(15))
    table = barc.price_table_at(s15, "price_table", PRICE_TABLE.where, barc.PRICE_START, barc.PRICE_END, PRICE_TABLE)
    f["price_table"] = table
    f["underlying_prices"] = price_table.price_rows(table)
    f["main_price_table"] = price_table.at("main_price_table", price_table.without(table, "barrier"), barc.PRICE_TABLE)
    f["schedule_table"] = schedule.date_table(
        f["coupon_table"], f["ko_table"], f["ko_observation"], f["guaranteed_periods"]
    )
    f["autocall_schedule"] = schedule.autocall_schedule(lambda k: f[k])
    # Non-Call（同 FCN 由 §13(7) 推得）另交欄位：配息公式保證期數與沒有樣本的型態規則要和它比（核對規則 §3.1、§3.5）
    f["first_callable_period"] = schedule.first_callable(lambda k: f[k])
    s16 = TextIndex(doc.span_lines(arts.get(16)))
    repeats = barc.repeat_mentions(doc, arts, s16, SCENARIO_MONTHLY)
    f["scenario_params"] = standard_fields.occurrences(
        "scenario_params", barc.scenario_params(f, s16, repeats, annualized=False)
    )
    mentions = {
        "monthly": barc._mentions(doc, arts, MONTHLY_PATTERNS),
        "annual": [],  # 範本沒有年利率
        "repeat": [m for m in repeats if m.article != barc.ARTICLE_16],
    }
    f.update(scenario_calcs(s16, f["currency_zh"]))
    pcts = barc.header_pcts(table, "第15條") + barc.scenario_header_pcts(doc.span_lines(arts.get(16)))
    return barc.BarcTermSheet(f, base.full_text, doc, coupon_mentions=mentions, header_pcts=pcts)


def _rate(art14: TextIndex, table: ParsedField) -> tuple[ParsedField, ParsedField]:
    """§14 配息率 t 公式 → 月配息率 X 與保證期數 g（範本規格 §3、§4.2）：兩個 X 須相同、第二段從 g+1 起、兩處 N 與
    「t」係自1至N 相同（配息觀察期表讀得到時也須等於表的期數）；不成立時兩個欄位都讀不到。"""
    monthly, g_name = "monthly_coupon_pct", "coupon_guaranteed_periods"
    hits = list(art14.finditer(RATE))
    if len(hits) != 1:
        note = (
            f"{WHERE_14}配息率 t 公式出現多次"
            if hits
            else f"{WHERE_14}找不到「「配息率」(i)配息率t=1=X%;及(ii)配息率t=2到N=X%×nt/Nt;」"
        )
        return ParsedField.missing(monthly, note), ParsedField.missing(g_name, note)
    m = hits[0]
    lines = art14.lines_for(m.start(), m.end())
    g = int(m[1] or 1)
    first, rest_from, last, x2 = Decimal(m[2]), int(m[3]), int(m[4]), Decimal(m[5])
    span = T_RANGE.search(art14.text)
    problems = []
    if first != x2:
        problems.append(f"兩段的 X 不同（{first}%、{x2}%）")
    if rest_from != g + 1:
        problems.append(f"第二段從 t={rest_from} 起，不是第 {g + 1} 期")
    if span is None or int(span[1]) != last:
        problems.append(f"「t」係自1至N 的 N 和配息率 t=…到{last} 不同")
    if table.ok and len(table.value.rows) != last:
        problems.append(f"配息率 t=…到{last} 和配息觀察期表 {len(table.value.rows)} 期不同")
    if problems:
        note = f"{WHERE_14}配息率 t 公式：" + "；".join(problems)
        return ParsedField.invalid(monthly, lines, note), ParsedField.invalid(g_name, lines, note)
    return ParsedField.present(monthly, first, lines), ParsedField.present(g_name, g, lines)


# ---------------------------------------------------------------- §16 一般、最差情況依假設天數的算式（workflow #577）

AMOUNT = r"[\d,]+(?:\.\d+)?"  # 同 NUM、不分組（累積算式各段）
ROUND = r"\(四捨五入至小數點後第(\d+)位\)"
_, GENERAL, WORST = barc._CASES
# 情境（欄位, 開頭, 下一個情境的開頭；最差情況到第 16 條結尾）
CALC_CASES = (("scenario_general_calc", GENERAL, WORST), ("scenario_worst_calc", WORST, None))


class Coupon(NamedTuple):
    """「第a(到b)個配息支付日：每單位配息金額 = 面額 × X% (× (n/N)) (四捨五入至小數點後第k位) = 金額」；第 1～g 期沒有 n/N。"""

    first: int
    last: int
    denomination: Decimal
    rate: Decimal
    days: int | None  # n
    total_days: int | None  # N
    amount: Decimal
    decimals: int


class Leg(NamedTuple):
    """累積配息算式的一段「金額 × 期數」；沒寫期數時 `periods` 為 None（最差情況樣本 0 那段）。"""

    amount: Decimal
    periods: int | None


class Cumulative(NamedTuple):
    legs: tuple[Leg, ...]
    total: Decimal


class Final(NamedTuple):
    """一般情況「每單位最終現金交割金額 = 每單位商品面額 × 100% = 面額 × 100% = 金額」（`denomination` 是算式的面額）；
    最差情況「每單位最終實物贖回權益等值 = … = 金額」（`denomination` 為 None）。"""

    denomination: Decimal | None
    amount: Decimal


class TotalReturn(NamedTuple):
    """「= [(加數 + 加數) / 面額] - 1 = r%」。"""

    terms: tuple[Decimal, Decimal]
    divisor: Decimal
    pct: Decimal


@dataclass(frozen=True)
class ScenarioCalc:
    """§16(3) 一般或最差情況的算式（範本規格 §4.3）；找不到、寫法不同（例：進位方式不是四捨五入）或出現多次的部分為
    None（規則轉人工）。`title` 是情境開頭的原文行。"""

    title: tuple[Line, ...]
    coupon: Hit[Coupon] | None
    accrual: Hit[Coupon] | None
    cumulative: Hit[Cumulative] | None
    final: Hit[Final] | None
    total_return: Hit[TotalReturn] | None


def scenario_calcs(s16: TextIndex, currency: ParsedField) -> dict[str, ParsedField]:
    """一般、最差情況各一個欄位（值為 `ScenarioCalc`）；讀不到情境開頭或幣別時缺漏。幣別字用說明書讀出的計價幣別。"""
    if not currency.ok:
        why = "讀不到計價幣別，無法讀第 16 條情境算式"
        return {name: ParsedField.missing(name, why) for name, _, _ in CALC_CASES}
    u = re.escape(currency.value)
    coupon = (
        rf"第(\d+)(?:到(\d+))?個配息支付日[:：]每單位配息金額={NUM}{u}[×xX]({X})%(?:[×xX]\((\d+)/(\d+)\))?{ROUND}"
        rf"={NUM}{u}"
    )
    cumulative = rf"每單位累積配息金額=((?:{AMOUNT}{u}(?:[×xX]\d+)?\+)*{AMOUNT}{u}(?:[×xX]\d+)?)={NUM}{u}"
    cash = rf"每單位最終現金交割金額=每單位商品面額[×xX]100%={NUM}{u}[×xX]100%={NUM}{u}"
    equivalent = rf"每單位最終實物贖回權益等值=[^總]*?={NUM}{u}{ROUND}"
    total_return = rf"=\[\({NUM}{u}\+{NUM}{u}\)/{NUM}{u}\]-1=(-?[\d.]+)%"
    text, out = s16.text, {}
    for name, start_mark, end_mark in CALC_CASES:
        start = text.find(start_mark)
        end = len(text) if end_mark is None else text.find(end_mark)
        if start < 0 or end < start:
            out[name] = ParsedField.missing(name, f"第 16 條找不到「{start_mark}」")
            continue
        span = (start, end)
        coupons = hits(s16, coupon, _coupon, span)
        final = (
            hits(s16, cash, lambda m: Final(dec(m[1]), dec(m[2])), span)
            if end_mark is not None
            else hits(s16, equivalent, lambda m: Final(None, dec(m[1])), span)
        )
        calc = ScenarioCalc(
            title=tuple(s16.lines_for(start, start + len(start_mark))),
            coupon=one([h for h in coupons if h.value.days is None]),
            accrual=one([h for h in coupons if h.value.days is not None]),
            cumulative=one(hits(s16, cumulative, lambda m: _cumulative(m, u), span)),
            final=one(final),
            total_return=one(hits(s16, total_return, _total_return, span)),
        )
        out[name] = ParsedField.present(name, calc, list(calc.title))
    return out


def _coupon(m: re.Match[str]) -> Coupon:
    first = int(m[1])
    days, total = (int(m[5]), int(m[6])) if m[5] else (None, None)
    return Coupon(first, int(m[2]) if m[2] else first, dec(m[3]), Decimal(m[4]), days, total, dec(m[8]), int(m[7]))


def _cumulative(m: re.Match[str], currency: str) -> Cumulative:
    leg = rf"({AMOUNT}){currency}(?:[×xX](\d+))?"
    legs = tuple(Leg(dec(a), int(n) if n else None) for a, n in re.findall(leg, m[1]))
    return Cumulative(legs, dec(m[2]))


def _total_return(m: re.Match[str]) -> TotalReturn:
    return TotalReturn((dec(m[1]), dec(m[2])), dec(m[3]), Decimal(m[4]))
