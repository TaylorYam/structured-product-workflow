"""CA Super Bull 中文產品說明書（docs/checker/templates/ca-zh-super-bull.md）：版面、封面、價格表、標的表與第二章以後
同 CA FCN（parsers/ca.py），先照 FCN 讀出，再改讀 Super Bull 不同的條目（第 9 項沒有配息、四欄觀察日表、無 KI 與到期
公式；13.(6)、15.(4)(5) 沒有配息；16.(3) 出場紅利與情境一～三）。第 16 項基本假設、情境價格表與匯率行、情境三交股算式和 CA BEN
共用 parsers/ca_variants.py，Super Bull 的用字宣告在 `VARIANT`（workflow #441）。

只支援現行版範本；認得出是 CA Super Bull、但現行版特徵不齊時辨識交出「不支援的範本版本」（範本規格 §1、§8）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import NamedTuple

from .. import standard_fields
from ..schema import DetectionResult, Evidence, FieldStatus, Line, ParsedField
from ..standard_fields import Money, ScenarioPrice
from ..text import squash
from . import ca, ca_variants, price_table
from .ca_variants import EQ, MUL, NUM, SPCT, Assumed, RemainderCalc, SharesCalc, U, WorseProfitReturn, worst_row
from .common import Hit, Mention, dec, derived, exactly_one
from .layout import TextIndex, parse_date
from .super_bull import ProfitReturn, Wording, fill_schedule

TEMPLATE_ID = "ca-zh-sb-pd"
PARSER_VERSION = "1"
OLD_VERSION = "CA Super Bull 舊版說明書（範本文字和現行版不同）"
NAME_ZH_MARK = "計價連結表現自動提前出場結構型商品(無擔保)(不保本)"
NAME_EN_MARK = "Performance Autocall Note"
NO_COUPON = "本商品持有期間並無配息金額。"
# 第 9 項 (1) 主要給付項目（沒有「配息金額」）
PAYMENTS = "本商品主要給付項目為「自動提前出場贖回金額」或「到期贖回金額」。" + NO_COUPON
UNIT = "[（(]張[）)]"
# 第 9 項 (a) 自動提前出場贖回金額：面額 ×（100% + 出場紅利）；舊版 x100% 漏紅利（範本規格 §1）
KO_REDEMPTION = (
    rf"「自動提前出場贖回金額」：自動提前出場觀察日時持有之商品單位{UNIT}數x每單位{UNIT}商品面額x"
    r"\(100%\+自動提前出場紅利\)。其後無任何金額給付。"
)
TABLE_HEAD = "自動提前出場觀察日自動提前出場價自動提前出場日自動提前出場紅利"
TABLE_NOTE = r"(?:註[:：]自動提前出場價以期初訂價的百分比表示。?)?"
# 多標的或單一標的的非記憶式事件句（範本規格 §3；多標的同 CA FCN #344）
NON_MEMORY = (ca.NON_MEMORY_ALL, ca.NON_MEMORY_ALL.removeprefix("所有"))
NO_KI = r"\(i\)若(?:最差表現之)?連結標的期末訂價低於執行價："
MATURITY_FORMULA = (
    r"\(ii\)若(?:最差表現之)?連結標的期末訂價高於或等於執行價：[^。]*?每單位"
    + UNIT
    + r"商品面額x\[100%\+100%\*\((?:最差表現連結標的期末訂價/最差表現連結標的執行價|連結標的期末訂價/連結標的執行價)"
    r"-100%\)\]。"
)
NO_COUPON_DATE = "(6)配息日：" + NO_COUPON
MIN_RATE = "(4)最低保證配息率：不適用。" + NO_COUPON
PARTICIPATION = "(5)參與率：不適用。"
MONTH_TOLERANCE = 7  # 觀察日和「發行日 + m 個月」前後最多差幾個日曆天（範本規格 §4.1；樣本 ±3）
WORDS = Wording("CA", "觀察日", "自動提前出場日", "期末訂價日")
TABLE_NAME = "第 9 項觀察日表"
# CA FCN 才有的欄位（配息表、配息率、FCN 贖回 %、情境配息月數等）：Super Bull 讀不到，也不交給規則
FCN_ONLY = (
    "coupon_table",
    "coupon_note_pct",
    "first_period_full_coupon",
    "ko_redemption_pct",
    "maturity_redemption_pct",
    "guaranteed_coupon_months",
    "guaranteed_coupon_pct",
    "ko_wording",
    "scenario_first_months",
    "scenario_ko_redemption_mentions",
    "scenario_maturity_redemption_mentions",
)


# ---------------------------------------------------------------- 辨識


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §8）：CA 版面與 Super Bull 名稱（條件 1～3）都成立後，現行版特徵（條件 4）全部成立才是現行版；
    缺任一項 → Super Bull 舊版（不支援，錯訊列出缺的特徵）。"""
    doc = ca.document(lines)
    cover = doc.before_chapter1()
    ti = TextIndex(cover)
    head = [squash(x.text) for x in cover[:2]]
    zh = re.search(r"●商品中文名稱：(.+?)\(ISIN：", ti.text)
    en = re.search(r"●商品英文名稱：(.+?)\(ISIN：", ti.text)
    issuer = squash(ca.ISSUER)
    arts = doc.articles(1)
    rest = TextIndex(doc.lines[doc.chapters[6].start :]).text if 6 in doc.chapters else ""
    evidence = [Evidence.of(x) for x in cover[:2]]
    family = (
        (
            len(head) == 2 and head[0] == "中文產品說明書" and head[1].startswith("●TDCC商品代號："),
            "前兩行不是「中文產品說明書」「● TDCC 商品代號：…」",
        ),
        (
            zh is not None and zh[1].startswith(issuer) and NAME_ZH_MARK in zh[1] and "固定配息" not in zh[1],
            f"封面商品中文名稱不是以「{ca.ISSUER}」開頭、沒有「{NAME_ZH_MARK}」或有「固定配息」",
        ),
        (
            en is not None and en[1].startswith(issuer) and squash(NAME_EN_MARK) in en[1],
            f"封面商品英文名稱不是以「{ca.ISSUER}」開頭或沒有「{NAME_EN_MARK}」",
        ),
        (
            all(n in doc.chapters for n in ca.CHAPTER_NAMES) and ca.CHAPTER7 in rest,
            "章名不是「第一章、商品基本資料」…「第七章、股權連結商品的額外條款及條件」或七章不完整",
        ),
        (set(range(1, ca.ARTICLES + 1)) <= set(arts), f"第一章條號 1–{ca.ARTICLES} 不連續"),
    )
    failed = [message for ok, message in family if not ok]
    if failed:
        return DetectionResult(False, failed, evidence)

    def text(n: int) -> str:
        return TextIndex(doc.span_lines(arts.get(n))).text

    art9 = text(9)
    current = (
        (PAYMENTS in art9, f"第 9 項 (1) 沒有「{PAYMENTS}」"),
        (
            re.search(KO_REDEMPTION, art9) is not None,
            "第 9 項 (a) 自動提前出場贖回金額不是「面額 x (100%+自動提前出場紅利)」",
        ),
        (TABLE_HEAD in art9, "第 9 項觀察日表不是「觀察日／出場價／出場日／紅利」四欄"),
        ("■" not in art9, "第 9 項用「■」條列"),
        (NO_COUPON_DATE in text(13), f"第 13 項沒有「{NO_COUPON_DATE}」"),
        (PARTICIPATION in text(15), f"第 15 項沒有「{PARTICIPATION}」"),
    )
    missing = [message for ok, message in current if not ok]
    if missing:
        return DetectionResult(False, missing, evidence, unsupported=OLD_VERSION)
    return DetectionResult(True, [], evidence)


# ---------------------------------------------------------------- 第 9 項


def _ko_table(ti: TextIndex, a: int, b: int) -> ParsedField:
    """(a) 四欄觀察日表（範本規格 §4.1）：表頭之後每列「觀察日、出場價 %、出場日、紅利 %」，沒有列號；表後只能接「註:…」。"""
    name = "ko_table"
    text = ti.text[a:b]
    h = text.find(TABLE_HEAD)
    if h < 0:
        return ParsedField.missing(name, "第 9 項 (a) 找不到四欄觀察日表")
    row = re.compile(ca.D + ca.PCT + ca.D + ca.PCT)
    rows, pos = [], h + len(TABLE_HEAD)
    while (m := row.match(text, pos)) is not None:
        rows.append(
            {"observation": parse_date(m[1]), "ko": Decimal(m[2]), "exit": parse_date(m[3]), "bonus": Decimal(m[4])}
        )
        pos = m.end()
    lns = ti.lines_for(a + h, a + pos)
    if not rows or any(r["observation"] is None or r["exit"] is None for r in rows):
        return ParsedField.invalid(name, lns, "第 9 項觀察日表的列讀不到")
    if not re.fullmatch(TABLE_NOTE, text[pos:]):
        return ParsedField.invalid(name, ti.lines_for(a + h, b), "第 9 項觀察日表後有表格以外的文字")
    return ParsedField.present(name, rows, lns)


def _ko_memory(fields: dict[str, ParsedField], ti: TextIndex, a: int, b: int) -> None:
    """(a) 自動提前出場事件句：非記憶式句（多標的、單一標的）→ False；有 FCN 記憶式兩句 → 照 CA FCN 判；其他 → 不合法。"""
    text = ti.text[a:b]
    events = list(re.finditer(ca.KO_EVENT, text))
    if len(events) != 1:
        fields["ko_memory"] = ParsedField.invalid(
            "ko_memory", ti.lines_for(a, b), "第 9 項 (a) 自動提前出場事件的句型缺漏或重複"
        )
        return
    m = events[0]
    lns = ti.lines_for(a + m.start(), a + m.end())
    wording = ca.memory_wording(m[1], text[m.end() : m.end() + 40])
    fields["ko_memory_wording"] = ParsedField.present("ko_memory_wording", wording, lns)
    if wording.prior or wording.same:
        fields["ko_memory"] = ca.memory_of(fields["ko_memory_wording"])
    elif m[1].rstrip("，,") in NON_MEMORY:
        fields["ko_memory"] = ParsedField.present("ko_memory", False, lns)
    else:
        fields["ko_memory"] = ParsedField.invalid("ko_memory", lns, "第 9 項 (a) 自動提前出場事件的句型不在範本規格內")


def _ki_type(ti: TextIndex, b: int, e: int, table: ParsedField, whole: TextIndex) -> ParsedField:
    """無 KI（範本規格 §3）：說明書全文沒有「界限事件」、價格表沒有下限價欄、(b)(i) 直接比執行價，三者都成立。"""
    problems = []
    if "界限事件" in whole.text:
        problems.append("說明書有界限事件")
    if table.ok and "ki" in table.value.columns:
        problems.append("價格表有下限價欄")
    if re.search(NO_KI, ti.text[b:e]) is None:
        problems.append("(b)(i) 不是「若…期末訂價低於執行價」")
    if problems:
        return ParsedField.invalid("ki_type", ti.lines_for(b, e), "無法判定無 KI：" + "；".join(problems))
    return ParsedField.present("ki_type", "none", ti.lines_for(b, e))


def _article9(fields: dict[str, ParsedField], art9: list[Line], whole: TextIndex) -> None:
    ti = TextIndex(art9)
    text = ti.text
    a = text.find("(a)「自動提前出場贖回金額」")
    b = text.find("(b)「到期贖回金額」", a)
    e = text.find("(2)連結標的：", b)
    if min(a, b, e) < 0:
        note = "第 9 項找不到 (a) 自動提前出場贖回金額、(b) 到期贖回金額或 (2) 連結標的"
        for name in ("ko_table", "ko_memory", "ki_type", "underlying_table", "main_price_table", "maturity_formula"):
            fields[name] = ParsedField.missing(name, note)
        return
    fields["ko_table"] = _ko_table(ti, a, b)
    _ko_memory(fields, ti, a, b)
    table = price_table.read(
        "underlying_table", ca.table_region(art9, "(2)連結標的：", "投資人請注意：本商品之收益"), ca.PRICE_TABLE
    )
    fields["underlying_table"] = table
    fields["main_price_table"] = price_table.at("main_price_table", table, ca.PRICE_TABLE)
    fields["ki_type"] = _ki_type(ti, b, e, table, whole)
    fields["ko_redemption_formula"] = exactly_one(
        "ko_redemption_formula",
        ti,
        KO_REDEMPTION,
        "第 9 項 (a) 自動提前出場贖回金額不是「面額 x (100%+自動提前出場紅利)」",
    )
    fields["maturity_formula"] = exactly_one(
        "maturity_formula",
        ti,
        MATURITY_FORMULA,
        "第 9 項 (b)(ii) 不是「面額 x [100%+100%* (…期末訂價/…執行價-100%)]」",
    )
    fields["no_coupon_art9"] = exactly_one("no_coupon_art9", ti, re.escape(PAYMENTS), f"第 9 項 (1) 沒有「{PAYMENTS}」")


def _row_bonus(table: ParsedField) -> ParsedField:
    """出場紅利 = 觀察日表各列的紅利（每列相同才交出；不同 → 歧義，轉人工覆核）。"""
    name = "coupon_pa_pct"
    if not table.ok:
        return derived(name, table)
    bonuses = sorted({r["bonus"] for r in table.value})
    if len(bonuses) > 1:
        return ParsedField(
            name, FieldStatus.AMBIGUOUS, None, list(table.evidence), bonuses, "第 9 項觀察日表各列出場紅利不同"
        )
    return ParsedField(name, FieldStatus.PRESENT, bonuses[0], list(table.evidence))


def _schedule(fields: dict[str, ParsedField]) -> None:
    """觀察日表只列觀察日（範本規格 §4.1）：觀察日和「發行日 + m 個月」前後差 7 個日曆天內。"""
    table = fields["ko_table"]
    fill_schedule(
        fields,
        table,
        lambda: [(n, r["observation"]) for n, r in enumerate(table.value, start=1)],
        table_name=TABLE_NAME,
        words=WORDS,
        early=MONTH_TOLERANCE,
        late=MONTH_TOLERANCE,
    )


# ---------------------------------------------------------------- 第 16 項情境（範本規格 §5）


WHO = "(?:最差表現之)?連結標的"
S1_ASSUMED = r"假設於第(\d+)個自動提前出場觀察日發生自動提前出場事件。"
S1_AMOUNT = rf"每單位商品面額×\(100%\+自動提前出場紅利\){EQ}{NUM}{U}{MUL}\(100%\+{ca.PCT}\){EQ}{NUM}{U}"
# 「(自動提前出場贖回金額 / 總投資金額) - 1」也有寫成「[(自動提前出場贖回金額)/總投資金額]-1」的樣本
S1_RETURN = (
    rf"總報酬率{EQ}\[?\(自動提前出場贖回金額\)?/總投資金額[\])]-1{EQ}\({NUM}/{NUM}\)-1{EQ}{SPCT}"
    rf"（平均年化報酬率[:：]{SPCT}）"
)
# 單一標的有寫名稱（「連結標的為X公司，」）和沒寫兩種
S2_ASSUMED = rf"假設於期末訂價日(?:{WHO}為(.+?)，|連結標的)收盤價為{NUM}{U}。"
S2_AMOUNT = (
    rf"每單位面額{MUL}\[100%\+100%{MUL}\({WHO}期末訂價/{WHO}執行價-100%\)\]。{EQ}{NUM}{U}{MUL}\[100%\+100%{MUL}"
    rf"\({NUM}{U}/{NUM}{U}-100%\)\]{EQ}{NUM}{U}{MUL}\(100%\+{SPCT}\){EQ}{NUM}{U}"
)
S2_RETURN = (
    rf"報酬率計算為：{EQ}\(每單位面額到期給付金額/每單位面額初始投資金額\)-1{EQ}\({NUM}/{NUM}\)-1{EQ}{SPCT}"
    rf"（平均年化報酬率[:：]{SPCT}）"
)
# 第 16 項基本假設與情境三（算式在 parsers/ca_variants.py）：紅利標籤「自動提前出場紅利：」；情境三假設句單一標的有寫名稱
# 和沒寫兩種、收盤價後寫「，低於其執行價」；股數「每單位面額/（最差表現之）連結標的（的）執行價*」；報酬率「-1」、半形括號
VARIANT = ca_variants.Variant(
    bonus_label="自動提前出場紅利：",
    scenario_table=ca.SCENARIO_TABLE,
    worst="(?:(?:表現最差的)?連結標的為(.+?)，|連結標的)",
    below_strike="，低於其執行價",
    strike_of=f"{WHO}的?",
    minus="-1",
    annualized_parens=(r"\(", r"\)"),
)


class AutocallCalc(NamedTuple):
    """情境一「= N × (100% + x%) = A」。"""

    denomination: Decimal
    bonus: Decimal
    amount: Decimal


class BestPayout(NamedTuple):
    """情境二「＝N × [100% + 100% x (P / K - 100%)]＝N x (100% + r%) = A」。`currencies` 是 N、N、A 旁的幣別字（計價幣別
    的金額；P、K 是標的報價幣別的股價，不算金額，workflow #424）。"""

    denomination: Decimal
    close: Decimal
    strike: Decimal
    denomination_again: Decimal  # 「＝N x (100% + r%)」再寫一次的面額
    pct: Decimal
    amount: Decimal
    currencies: tuple[str, str, str]


@dataclass
class SbScenarios:
    """情境一～三的算式；找不到、或在情境裡有多處時為 None（由規則轉人工覆核）。`titles` 為三個情境標題的原文行
    （找不到標題時三個情境都空）。"""

    titles: tuple[list[Line], list[Line], list[Line]]
    first: Hit[int] | None = None
    autocall: Hit[AutocallCalc] | None = None
    autocall_return: Hit[ProfitReturn] | None = None
    best_assumed: Hit[Assumed] | None = None
    best: Hit[BestPayout] | None = None
    best_return: Hit[ProfitReturn] | None = None
    worse_assumed: Hit[Assumed] | None = None
    shares: Hit[SharesCalc] | None = None
    remainder: Hit[RemainderCalc] | None = None
    worse_return: Hit[WorseProfitReturn] | None = None

    @property
    def found(self) -> bool:
        return all(self.titles)


def _return(m: re.Match[str]) -> ProfitReturn:
    return ProfitReturn(dec(m[1]), dec(m[2]), Decimal(m[3]), Decimal(m[4]))


def _scenarios(ti: TextIndex) -> SbScenarios:
    """以標題切出情境一～三（`ca_variants.sections`），在各自範圍內找算式；情境三的算式由 `ca_variants` 依 `VARIANT` 讀。"""
    sec = ca_variants.sections(ti)
    if sec is None:
        return SbScenarios(([], [], []))
    out = SbScenarios(sec.titles)
    find = sec.find
    out.first = find(1, S1_ASSUMED, lambda m: int(m[1]))
    out.autocall = find(1, S1_AMOUNT, lambda m: AutocallCalc(dec(m[1]), Decimal(m[3]), dec(m[4])))
    out.autocall_return = find(1, S1_RETURN, _return)
    out.best_assumed = find(2, S2_ASSUMED, lambda m: Assumed(m[1], dec(m[2])))
    out.best = find(
        2,
        S2_AMOUNT,
        lambda m: BestPayout(
            dec(m[1]), dec(m[3]), dec(m[5]), dec(m[7]), Decimal(m[9]), dec(m[10]), (m[2], m[8], m[11])
        ),
    )
    out.best_return = find(2, S2_RETURN, _return)
    out.worse_assumed, out.shares, out.remainder, out.worse_return = sec.worse(VARIANT)
    return out


def _article16(fields: dict[str, ParsedField], art16: list[Line]) -> SbScenarios:
    """基本假設（天期、面額、初始投資金額、出場紅利）與情境價格表（`ca_variants.basics`）、情境一～三的算式（範本規格 §5）。"""
    sc = _scenarios(ca_variants.basics(fields, art16, VARIANT))
    prices = fields["underlying_prices"]
    place = f"第一章第 16 項情境分析{ca.SCENARIOS[1]}到期給付金額算式的"
    strike = Mention.of_optional_hit(place, sc.best, lambda v: v.strike, sc.titles[1]).parsed("scenario2_strike")
    row = worst_row(sc.best_assumed.value.name, prices) if sc.best_assumed is not None else None
    worse = ca_variants.Worse(sc.worse_assumed, sc.shares, sc.remainder, sc.worse_return)
    fields["scenario_prices"] = standard_fields.occurrences(
        "scenario_prices",
        [ScenarioPrice(place, row, "strike", strike), ca_variants.worse_price(worse, sc.titles[2], prices)],
    )
    return sc


def _best_amounts(best: Hit[BestPayout]) -> ParsedField:
    """情境二以計價幣別寫的金額（面額、再寫一次的面額、到期給付金額）各自的幣別字。"""
    b, evidence = best.value, tuple(Evidence.of(ln) for ln in best.lines)
    amounts = (b.denomination, b.denomination_again, b.amount)
    found = tuple(Money(f"{x:,} {c}", c, evidence) for x, c in zip(amounts, b.currencies, strict=True))
    return ParsedField.present("scenario2_currency", found, list(best.lines))


# ---------------------------------------------------------------- 讀出


@dataclass
class CaSuperBullTermSheet(ca.CaTermSheet):
    scenarios: SbScenarios
    bonus_mentions: list[Mention]  # 出場紅利的其他出處（基本假設、情境一；正式值是觀察日表的 `coupon_pa_pct`）


def read(lines: Sequence[Line]) -> CaSuperBullTermSheet:
    """先照 CA FCN 讀出，再改讀 Super Bull 不同的條目（範本辨識另由 `detect` 負責）。"""
    base = ca.read(lines)
    doc, fields = base.document, dict(base.fields)
    arts = doc.articles(1)

    def article(n: int) -> list[Line]:
        return doc.span_lines(arts.get(n))

    for name in FCN_ONLY:
        fields.pop(name, None)
    fields["monthly_coupon_pct"] = standard_fields.absent("monthly_coupon_pct", "月配息率（Super Bull 沒有配息）")

    # 第 9 項、價格表與標的、觀察日表
    _article9(fields, article(9), base.full_text)
    ca.table_fields(fields)
    fields["coupon_pa_pct"] = _row_bonus(fields["ko_table"])
    _schedule(fields)

    # 13.(6)、15.(4)(5)：沒有配息、參與率
    fields["no_coupon_art13"] = exactly_one(
        "no_coupon_art13", TextIndex(article(13)), re.escape(NO_COUPON_DATE), f"第 13 項沒有「{NO_COUPON_DATE}」"
    )
    art15 = TextIndex(article(15))
    fields["no_coupon_art15"] = exactly_one(
        "no_coupon_art15", art15, re.escape(MIN_RATE), f"第 15 項沒有「{MIN_RATE}」"
    )
    fields["participation_rate"] = exactly_one(
        "participation_rate", art15, re.escape(PARTICIPATION), f"第 15 項沒有「{PARTICIPATION}」"
    )

    # 第 16 項情境
    scenarios = _article16(fields, article(16))
    mentions = [
        Mention.of_field("第 16 項基本假設「自動提前出場紅利：x%」", fields["bonus_assumed"]),
        Mention.of_optional_hit(
            "第 16 項情境分析一「(100% + x%)」", scenarios.autocall, lambda v: v.bonus, scenarios.titles[0]
        ),
    ]

    # 幣別出處：第 9 項 (b)(i) 寫「以計價幣別支付剩餘金額」（沒有幣別字）；基本假設初始投資金額後接出場紅利；情境三
    # 只有剩餘金額是計價幣別的金額（CA FCN 不列入剩餘金額那行），改取它的幣別字；情境二只取面額與到期給付金額（算式
    # 裡的期末訂價、執行價是標的報價幣別的股價，跨幣別時和計價幣別不同，workflow #424）
    remainder, best = scenarios.remainder, scenarios.best
    replaced = {
        "scenario_investment_currency": fields["scenario_investment_currency"],
        "scenario2_currency": _best_amounts(best)
        if best is not None
        else ParsedField.missing("scenario2_currency", "第 16 項情境分析二找不到到期給付金額算式"),
        "scenario3_currency": ca_variants.worse_currency(remainder),
    }
    others = [
        replace(occ, value=replaced[occ.field]) if occ.field in replaced else occ
        for occ in fields["currency_others"].value
        if occ.field != "redemption_currency"
    ]
    fields["currency_others"] = standard_fields.occurrences("currency_others", others)

    return CaSuperBullTermSheet(fields, base.full_text, doc, scenarios, mentions)
