"""CA BEN 中文產品說明書（docs/checker/templates/ca-zh-ben.md）：版面、封面、價格表、標的表與第二章以後同 CA FCN
（parsers/ca.py），先照 FCN 讀出，再改讀 BEN 不同的條目（第 9 項沒有配息、沒有 KO 與 KI、Bonus 公式、參與率與紅利率；
13.(6)、15.(4)(5) 沒有配息與參與率；16.(3) 紅利率與情境一～三）。

只支援現行版 Bonus 範本；認得出是 CA BEN、但現行版特徵不齊（KO 價欄版、早期版、更早沒有樣本的寫法）或是 Digital 時，辨識
交出「不支援的範本版本」（範本規格 §1、§8；暫定做法 BEN-377-01～03 只在這裡改）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import NamedTuple

from .. import standard_fields
from ..schema import DetectionResult, Evidence, FieldStatus, Line, ParsedField
from ..standard_fields import AutocallSchedule, ScenarioParam, ScenarioPrice
from ..text import squash
from . import ca, price_table
from .ca_super_bull import (
    S3_REMAINDER,
    Assumed,
    RemainderCalc,
    SharesCalc,
    WorseProfitReturn,
    worst_row,
)
from .common import Hit, Mention, capture_group, dec, derived, exactly_one, number, one
from .layout import TextIndex, capture
from .super_bull import ProfitReturn

TEMPLATE_ID = "ca-zh-ben-pd"
PARSER_VERSION = "1"

# ---------------------------------------------------------------- 待使用者確認的暫定做法（核對規則 §9.2，詳見本機疑慮記錄）
# BEN-377-01、BEN-377-02：認得出是 CA BEN、但現行版特徵不齊（KO 價欄版、早期版、2026-06 以前沒有樣本的寫法）→ 不支援的範本版本
OLD_VERSION = "CA BEN 說明書寫法和現行版不同"
# BEN-377-03：Digital（英文 Digital Coupon Note 或第 9 項 Digital 公式）→ 不支援，不跑規則
DIGITAL = "CA BEN Digital（目前不支援）"
# 英文名稱和第 9 項公式指向不同型態（範本規格 §8）→ 不支援，不跑規則
MIXED = "CA BEN 英文名稱和第 9 項公式的型態不同"

NAME_ZH_MARK = "計價紅利結構型商品(無擔保)(不保本)"
NAME_ZH_NOT = ("固定配息", "連結表現", "自動提前出場")
BONUS_EN = "Bonus Note"
DIGITAL_EN = "Digital Coupon Note"
NO_COUPON = "本商品持有期間並無配息金額。"
PAYMENTS = "本商品主要給付項目為「到期贖回金額」，持有期間並無配息金額。"
# 第 9 項 (1) 到期（1）交股、（2）領回與 Bonus 公式（範本規格 §2；括號不對稱照樣本，BEN-377-06）
DELIVERY = (
    rf"（1）若最差表現之連結標的期末訂價小於執行價，則投資人將會收到發行機構交付表現最差的連結標的股票之股數，"
    rf"及以({ca.CCY})支付剩餘金額（如有）;"
)
FORMULA = "每單位面額x[100%+最大值(紅利率，參與率x(最差表現之連結標的期末訂價/最差表現之連結標的期初訂價)-100%))]。"
MATURITY = "（2）若最差表現之連結標的期末訂價大於或等於執行價，則投資人可領回：" + FORMULA
DIGITAL_FORMULA = "每單位面額x紅利水準。"
PARTICIPATION = r"參與率：" + ca.PCT + "。"
BONUS = r"紅利率：" + ca.PCT + "。"
NO_KO = "提前出場"
# BEN-377-08（待使用者確認）：13.(6) 只收這句；1 份現行版樣本寫「詳見第9 項…」→ `doc.no_coupon` 不一致、人工放行
NO_COUPON_DATE = "(6)配息日：" + NO_COUPON
ART15_TITLE = "15.到期贖回計算公式，包含保證配息率及參與率："
MIN_RATE = "(4)最低保證配息率：不適用，" + NO_COUPON
ART15_PARTICIPATION = r"\(5\)參與率：" + ca.PCT + "。?"
PRICE_TABLE = replace(ca.PRICE_TABLE, columns=(price_table.Column("strike", "執行價"),))
SCENARIO_TABLE = replace(PRICE_TABLE, where="第 16 項情境價格表")
# CA FCN 才有的欄位（配息表、配息率、贖回 %、KO 表、情境配息等）：BEN 讀不到，也不交給規則
FCN_ONLY = (
    "coupon_table",
    "coupon_note_pct",
    "first_period_full_coupon",
    "ko_redemption_pct",
    "maturity_redemption_pct",
    "guaranteed_coupon_months",
    "guaranteed_coupon_pct",
    "ko_wording",
    "ko_memory_wording",
    "ko_table",
    "scenario_first_months",
    "scenario_monthly_pct",
    "scenario_coupon_mentions",
    "scenario_denomination_mentions",
    "scenario_months_mentions",
    "scenario_annualized_mentions",
    "scenario_ko_redemption_mentions",
    "scenario_maturity_redemption_mentions",
)


# ---------------------------------------------------------------- 辨識


def _names(text: str) -> tuple[str, str]:
    zh = re.search(r"●商品中文名稱：(.+?)\(ISIN：", text)
    en = re.search(r"●商品英文名稱：(.+?)\(ISIN：", text)
    return (zh[1] if zh else "", en[1] if en else "")


def family_problems(zh: str, en: str) -> list[str]:
    """CA BEN 名稱（範本規格 §8 條件 2、3）：中文含 `NAME_ZH_MARK`、不含 FCN／Super Bull 的字；英文含 Bonus 或 Digital。"""
    issuer = squash(ca.ISSUER)
    out = []
    if not (zh.startswith(issuer) and NAME_ZH_MARK in zh and not any(x in zh for x in NAME_ZH_NOT)):
        out.append(
            f"封面商品中文名稱不是以「{ca.ISSUER}」開頭、沒有「{NAME_ZH_MARK}」或有「{'」「'.join(NAME_ZH_NOT)}」"
        )
    if not (en.startswith(issuer) and (squash(BONUS_EN) in en or squash(DIGITAL_EN) in en)):
        out.append(f"封面商品英文名稱不是以「{ca.ISSUER}」開頭或沒有「{BONUS_EN}」「{DIGITAL_EN}」")
    return out


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §8）：CA 版面與 BEN 名稱（條件 1～3）都成立後，Digital 或型態對不上 → 不支援；現行版特徵（條件 4）
    全部成立才是現行版，缺任一項 → 不支援的範本版本（錯訊列出缺的特徵）。"""
    doc = ca.document(lines)
    cover = doc.before_chapter1()
    ti = TextIndex(cover)
    head = [squash(x.text) for x in cover[:2]]
    zh, en = _names(ti.text)
    arts = doc.articles(1)
    rest = TextIndex(doc.lines[doc.chapters[6].start :]).text if 6 in doc.chapters else ""
    evidence = [Evidence.of(x) for x in cover[:2]]
    failed = [
        message
        for ok, message in (
            (
                len(head) == 2 and head[0] == "中文產品說明書" and head[1].startswith("●TDCC商品代號："),
                "前兩行不是「中文產品說明書」「● TDCC 商品代號：…」",
            ),
            (
                all(n in doc.chapters for n in ca.CHAPTER_NAMES) and ca.CHAPTER7 in rest,
                "章名不是「第一章、商品基本資料」…「第七章、股權連結商品的額外條款及條件」或七章不完整",
            ),
            (set(range(1, ca.ARTICLES + 1)) <= set(arts), f"第一章條號 1–{ca.ARTICLES} 不連續"),
        )
        if not ok
    ]
    failed += family_problems(zh, en)
    if failed:
        return DetectionResult(False, failed, evidence)

    def text(n: int) -> str:
        return TextIndex(doc.span_lines(arts.get(n))).text

    art9 = text(9)
    digital_name, digital_formula = squash(DIGITAL_EN) in en, DIGITAL_FORMULA in art9
    if digital_name and digital_formula:
        return DetectionResult(
            False, ["英文名稱是 Digital Coupon Note、第 9 項是 Digital 公式"], evidence, unsupported=DIGITAL
        )
    if digital_name or digital_formula:
        why = "英文名稱是 Digital Coupon Note、第 9 項不是 Digital 公式" if digital_name else "第 9 項是 Digital 公式"
        return DetectionResult(False, [why], evidence, unsupported=MIXED)
    return _current(art9, text(13), text(15), TextIndex(doc.lines).text, evidence)


def _current(art9: str, art13: str, art15: str, whole: str, evidence: list[Evidence]) -> DetectionResult:
    """現行版特徵（範本規格 §1、§8 條件 4）；不齊 → 不支援的範本版本（BEN-377-01、BEN-377-02）。"""
    formula = art9.find(FORMULA)
    after = art9[formula:] if formula >= 0 else ""
    current = (
        (PAYMENTS in art9, f"第 9 項 (1) 沒有「{PAYMENTS}」"),
        (formula >= 0, "第 9 項沒有 Bonus 公式「每單位面額 x[100%+最大值(紅利率，參與率x(…)-100%))]」"),
        (
            re.search(PARTICIPATION, after) is not None and re.search(BONUS, after) is not None,
            "第 9 項公式後沒有「參與率：x%。」「紅利率：x%。」兩行",
        ),
        ("自動提前出場價" not in art9, "第 9 項價格表有自動提前出場價欄"),
        (NO_KO not in whole, f"說明書有「{NO_KO}」"),
        ("觀察日" not in art13, "第 13 項有觀察日條"),
        (
            ART15_TITLE in art15 and MIN_RATE in art15 and re.search(ART15_PARTICIPATION, art15) is not None,
            f"第 15 項不是「{ART15_TITLE}」「{MIN_RATE}」「(5)參與率：x%」的寫法",
        ),
    )
    missing = [message for ok, message in current if not ok]
    if missing:
        return DetectionResult(False, missing, evidence, unsupported=OLD_VERSION)
    return DetectionResult(True, [], evidence)


# ---------------------------------------------------------------- 第 9 項


def _table_fields(fields: dict[str, ParsedField]) -> None:
    """價格表（期初訂價、執行價兩欄）＋第 10 項代碼 → 標的、各標的價格、K%（表頭是唯一來源）；沒有 KO%（ADR 0029）。"""
    table, tickers = fields["underlying_table"], fields["art10_tickers"]
    if table.ok and tickers.ok and len(table.value.rows) == len(tickers.value):
        fields["underlyings"] = ParsedField(
            "underlyings", FieldStatus.PRESENT, list(tickers.value), list(tickers.evidence)
        )
        fields["underlying_prices"] = price_table.price_rows(table, tickers.value)
    elif table.ok and tickers.ok:
        note = f"價格表 {len(table.value.rows)} 列，第 10 項連結標的資產表 {len(tickers.value)} 檔，無法依列序對到代碼"
        ev = list(table.evidence) + list(tickers.evidence)
        for key in ("underlyings", "underlying_prices"):
            fields[key] = ParsedField(key, FieldStatus.INVALID, evidence=ev, note=note)
    else:
        bad = table if not table.ok else tickers
        fields["underlyings"] = derived("underlyings", bad)
        fields["underlying_prices"] = derived("underlying_prices", bad)
    fields["strike_pct"] = derived("strike_pct", table, table.value.pcts["strike"] if table.ok else None)


def _ki_type(fields: dict[str, ParsedField], art9: TextIndex, whole: TextIndex) -> None:
    """無 KI（範本規格 §3）：全文沒有「界限事件」、價格表沒有下限價欄、到期（1）直接比執行價，三者都成立。"""
    problems = []
    if "界限事件" in whole.text:
        problems.append("說明書有界限事件")
    table = fields["underlying_table"]
    if table.ok and "ki" in table.value.columns:
        problems.append("價格表有下限價欄")
    delivery = fields["delivery_formula"]
    if not delivery.ok:
        problems.append("到期（1）不是「若最差表現之連結標的期末訂價小於執行價」")
    if problems:
        note = "無法判定無 KI：" + "；".join(problems)
        fields["ki_type"] = ParsedField.invalid("ki_type", art9.lines[:3], note)
        fields["ki_pct"] = ParsedField.invalid("ki_pct", art9.lines[:3], note)
        return
    fields["ki_type"] = ParsedField("ki_type", FieldStatus.PRESENT, "none", list(delivery.evidence))
    fields["ki_pct"] = ParsedField(
        "ki_pct", FieldStatus.NOT_APPLICABLE, evidence=list(delivery.evidence), note="說明書無 KI（沒有界限事件）"
    )


def _no_autocall(fields: dict[str, ParsedField], art13: TextIndex, whole: TextIndex) -> None:
    """沒有 KO（範本規格 §3）：第 9 項 (1) 開頭句、全文沒有「提前出場」、第 13 項沒有觀察日條，三者都成立才判定；判定後
    KO 每期觀察、非記憶式、Non-Call = 天期，比價日只有期末訂價日（下單資料格式 §4）。"""
    problems = []
    opening = fields["no_coupon_art9"]
    if not opening.ok:
        problems.append(opening.note)
    if NO_KO in whole.text:
        problems.append(f"說明書有「{NO_KO}」")
    if "觀察日" in art13.text:
        problems.append("第 13 項有觀察日條")
    if problems:
        fields["no_autocall"] = ParsedField.invalid(
            "no_autocall", list(opening.evidence), "無法判定沒有 KO：" + "；".join(problems)
        )
    else:
        fields["no_autocall"] = ParsedField("no_autocall", FieldStatus.PRESENT, True, list(opening.evidence))
    no_ko = fields["no_autocall"]
    fields["ko_pct"] = (
        ParsedField("ko_pct", FieldStatus.NOT_APPLICABLE, evidence=list(no_ko.evidence), note="BEN 沒有 KO")
        if no_ko.ok
        else derived("ko_pct", no_ko)
    )
    fields["ko_observation"] = derived("ko_observation", no_ko, "P")
    fields["ko_memory"] = derived("ko_memory", no_ko, False)
    fields["schedule_table"] = standard_fields.absent("schedule_table", "日期表（BEN 不會提前出場）")
    tenor, final = fields["tenor_months"], fields["final_valuation_date"]
    bad = next((p for p in (no_ko, tenor, final) if not p.ok), None)
    if bad is not None:
        fields["first_callable_period"] = derived("first_callable_period", bad)
        fields["autocall_schedule"] = derived("autocall_schedule", bad)
        return
    ev = [*no_ko.evidence, *tenor.evidence, *final.evidence]
    fields["first_callable_period"] = ParsedField("first_callable_period", FieldStatus.PRESENT, tenor.value, ev)
    fields["autocall_schedule"] = ParsedField(
        "autocall_schedule",
        FieldStatus.PRESENT,
        AutocallSchedule("P", tenor.value, tenor.value, {tenor.value: final.value}, listed=True),
        ev,
    )


def _article9(fields: dict[str, ParsedField], art9: list[Line]) -> None:
    """(1) 開頭、到期（1）（2）與 Bonus 公式、公式下的參與率與紅利率（只取公式之後的標籤，範本規格 §7）、(2) 價格表。"""
    ti = TextIndex(art9)
    fields["no_coupon_art9"] = exactly_one("no_coupon_art9", ti, re.escape(PAYMENTS), f"第 9 項 (1) 沒有「{PAYMENTS}」")
    fields["delivery_formula"] = exactly_one(
        "delivery_formula",
        ti,
        DELIVERY,
        "第 9 項到期（1）不是「若最差表現之連結標的期末訂價小於執行價…交付…股數」的寫法",
    )
    fields["maturity_formula"] = exactly_one(
        "maturity_formula", ti, re.escape(MATURITY), "第 9 項到期（2）與 Bonus 公式不是範本固定寫法"
    )
    formula = ti.text.find(FORMULA)
    end = ti.text.find("(2)連結標的：")
    for name, pattern, what in (
        ("participation_pct", PARTICIPATION, "參與率"),
        ("coupon_pa_pct", BONUS, "紅利率"),
    ):
        found = [
            m for m in ti.finditer(pattern) if formula >= 0 and formula < m.start() and (end < 0 or m.start() < end)
        ]
        if len(found) != 1:
            note = f"第 9 項公式後找不到「{what}：x%。」" if not found else f"第 9 項公式後有多處「{what}：x%。」"
            fields[name] = ParsedField.missing(name, note)
        else:
            m = found[0]
            fields[name] = ParsedField.present(name, Decimal(m[1]), ti.lines_for(m.start(), m.end()))
    table = price_table.read(
        "underlying_table", ca.table_region(art9, "(2)連結標的：", "投資人請注意：本商品之收益"), PRICE_TABLE
    )
    fields["underlying_table"] = table
    fields["main_price_table"] = price_table.at("main_price_table", table, PRICE_TABLE)


# ---------------------------------------------------------------- 第 16 項情境（範本規格 §5）


EQ = ca.EQ
MUL = "[×xX*]"
NUM = r"([\d,]+(?:\.\d+)?)"
SPCT = r"(-?\d+(?:\.\d+)?)%"
U = f"({ca.CCY})"
L, R = "[（(]", "[）)]"
TITLES = ("情境分析一：較佳情況", "情境分析二：一般情況", "情境分析三：較差情況")
ASSUMED = rf"假設於期末訂價日，最差表現的連結標的為(.+?)，收盤價為{NUM}{U}，高於其執行價。"
# 「＝N × [100%+最大值（B%，Q% x（P / I - 100%))]」；「＝N × [100%+最大值（B%，r%) = A」
CALC = rf"{EQ}{NUM}{MUL}\[100%\+最大值{L}{ca.PCT}，{ca.PCT}{MUL}{L}{NUM}{U}/{NUM}{U}-100%{R}{R}\]"
AMOUNT = rf"{EQ}{NUM}{MUL}\[100%\+最大值{L}{ca.PCT}，{SPCT}{R}{EQ}{NUM}{U}"
# 「＝A / N ) - 100% ＝r%（平均年化報酬率：y%）」：左括號樣本缺一個，可有可無
RETURN = rf"{EQ}\(?{NUM}{U}/{NUM}\)-100%{EQ}{SPCT}{L}平均年化報酬率[:：]{SPCT}{R}"
S3_ASSUMED = rf"假設於期末訂價日表現最差的連結標的為(.+?)，收盤價為{NUM}{U}。"
S3_SHARES = rf"每單位面額/表現最差連結標的的執行價\*{EQ}{NUM}/{NUM}{EQ}{NUM}股"
S3_RETURN = rf"{EQ}\(\({NUM}{MUL}{NUM}\+{NUM}\)/{NUM}\)-100%{EQ}{SPCT}{L}平均年化報酬率[:：]{SPCT}{R}"


class BonusCalc(NamedTuple):
    """情境一、二代入行「＝N × [100%+最大值（B%，Q% x（P / I - 100%))]」。"""

    denomination: Decimal
    bonus: Decimal
    participation: Decimal
    close: Decimal
    initial: Decimal


class BonusAmount(NamedTuple):
    """情境一、二結果行「＝N × [100%+最大值（B%，r%) = A」。"""

    denomination: Decimal
    bonus: Decimal
    gain: Decimal
    amount: Decimal


@dataclass
class BenScenarios:
    """情境一～三的算式；找不到、或在情境裡有多處時為 None（由規則轉人工覆核）。`titles` 為三個情境標題的原文行（找不到
    標題時三個情境都空）。情境三的欄位名稱同 CA Super Bull（交股算式共用規則 `rules/ca_super_bull.worse`）。"""

    titles: tuple[list[Line], list[Line], list[Line]]
    assumed: dict[int, Hit[Assumed] | None]
    calc: dict[int, Hit[BonusCalc] | None]
    amount: dict[int, Hit[BonusAmount] | None]
    returns: dict[int, Hit[ProfitReturn] | None]  # 報酬率行「＝A / N ) - 100% ＝r%（平均年化報酬率：y%）」
    worse_assumed: Hit[Assumed] | None = None
    shares: Hit[SharesCalc] | None = None
    remainder: Hit[RemainderCalc] | None = None
    worse_return: Hit[WorseProfitReturn] | None = None

    @property
    def found(self) -> bool:
        return all(self.titles)


def _scenarios(ti: TextIndex) -> BenScenarios:
    """以標題切出情境一～三（到「※請注意」為止），在各自範圍內找算式；情境要剛好這三個（核對規則 §3.7）。"""
    starts = [ti.text.find(t) for t in TITLES]
    bounds = [*starts, ti.text.find("※請注意", max(starts))]
    empty = BenScenarios(([], [], []), {}, {}, {}, {})
    if not (all(x >= 0 for x in bounds) and bounds == sorted(bounds)):
        return empty
    if len(re.findall(r"情境分析[一二三四五六七八九十]+：", ti.text)) != len(TITLES):
        return empty
    titles = tuple(ti.lines_for(s, s + len(t)) for s, t in zip(starts, TITLES, strict=True))

    def find(n: int, pattern: str, convert):
        lo, hi = bounds[n - 1], bounds[n]
        return one(
            [
                Hit(convert(m), tuple(ti.lines_for(m.start(), m.end())))
                for m in ti.finditer(pattern)
                if lo <= m.start() < hi
            ]
        )

    out = BenScenarios(titles, {}, {}, {}, {})  # type: ignore[arg-type]
    for n in (1, 2):
        out.assumed[n] = find(n, ASSUMED, lambda m: Assumed(m[1], dec(m[2])))
        out.calc[n] = find(n, CALC, lambda m: BonusCalc(dec(m[1]), Decimal(m[2]), Decimal(m[3]), dec(m[4]), dec(m[6])))
        out.amount[n] = find(n, AMOUNT, lambda m: BonusAmount(dec(m[1]), Decimal(m[2]), Decimal(m[3]), dec(m[4])))
        out.returns[n] = find(n, RETURN, lambda m: ProfitReturn(dec(m[1]), dec(m[3]), Decimal(m[4]), Decimal(m[5])))
    out.worse_assumed = find(3, S3_ASSUMED, lambda m: Assumed(m[1], dec(m[2])))
    out.shares = find(3, S3_SHARES, lambda m: SharesCalc(dec(m[1]), dec(m[2]), dec(m[3])))
    out.remainder = find(
        3, S3_REMAINDER, lambda m: RemainderCalc(dec(m[1]), dec(m[2]), dec(m[3]), dec(m[4]), dec(m[5]), m[6])
    )
    out.worse_return = find(
        3,
        S3_RETURN,
        lambda m: WorseProfitReturn(dec(m[1]), dec(m[2]), dec(m[3]), dec(m[4]), Decimal(m[5]), Decimal(m[6])),
    )
    return out


def _article16(fields: dict[str, ParsedField], art16: list[Line]) -> BenScenarios:
    """基本假設（天期、面額、初始投資金額、紅利率）、情境價格表、情境一～三的算式（範本規格 §5）。"""
    ti = TextIndex(art16)
    where = "第 16 項基本假設"
    tenor = capture("scenario_tenor", ti, r"基本假設天期：(\d+)個月", int, whole_match=True)
    denom = rf"每單位面額：([\d,]+)({ca.CCY})初始投資金額"
    denomination = capture("scenario_denomination", ti, denom, number, whole_match=True)
    fields["scenario_denomination_currency"] = capture_group(art16, denom, 2, "scenario_denomination_currency")
    invest = rf"初始投資金額：([\d,]+)({ca.CCY})紅利率："
    investment = capture_group(art16, invest, 1, "scenario_initial_investment", number)
    fields["scenario_investment_currency"] = capture_group(art16, invest, 2, "scenario_investment_currency")
    fields["bonus_assumed"] = capture_group(art16, r"紅利率：" + ca.PCT, 1, "bonus_assumed", Decimal)
    scenario_table = price_table.read(
        "scenario_table", ca.table_region(art16, "紅利率：", "情境分析一"), SCENARIO_TABLE
    )
    fields["scenario_reprint_table"] = price_table.at("scenario_reprint_table", scenario_table, SCENARIO_TABLE)
    # 標的報價幣別和計價幣別不同時，情境價格表後多一行匯率（`美元/日圓=150.00`），由規則轉人工覆核（同 CA Super Bull）
    fx = [ln for ln in art16 if re.fullmatch(rf"{ca.CCY}/{ca.CCY}=[\d,.]+", squash(ln.text))]
    fields["scenario_fx"] = ParsedField.present("scenario_fx", [squash(ln.text) for ln in fx], fx)
    fields["scenario_params"] = standard_fields.occurrences(
        "scenario_params",
        [
            ScenarioParam("scenario_tenor", where, tenor),
            ScenarioParam("scenario_denomination", where, denomination),
            ScenarioParam("scenario_initial_investment", where, investment),
        ],
    )
    sc = _scenarios(ti)
    prices = fields["underlying_prices"]
    out = []
    for n in (1, 2):
        place = f"第一章第 16 項情境分析{ca.SCENARIOS[n - 1]}到期給付金額算式的"
        assumed = sc.assumed.get(n)
        row = worst_row(assumed.value.name, prices) if assumed is not None else None
        initial = Mention.of_optional_hit(place, sc.calc.get(n), lambda v: v.initial, sc.titles[n - 1])
        out.append(ScenarioPrice(place, row, "initial", initial.parsed(f"scenario{n}_initial")))
    place = "第一章第 16 項情境分析三股數算式的"
    row = worst_row(sc.worse_assumed.value.name, prices) if sc.worse_assumed is not None else None
    strike = Mention.of_optional_hit(place, sc.shares, lambda v: v.strike, sc.titles[2])
    out.append(ScenarioPrice(place, row, "strike", strike.parsed("scenario3_strike")))
    fields["scenario_prices"] = standard_fields.occurrences("scenario_prices", out)
    return sc


# ---------------------------------------------------------------- 讀出


@dataclass
class CaBenTermSheet(ca.CaTermSheet):
    scenarios: BenScenarios
    bonus_mentions: list[Mention]  # 紅利的其他出處（基本假設、情境一二；正式值是第 9 項的 `coupon_pa_pct`）
    participation_mentions: list[Mention]  # 參與率的其他出處（15.(5)、情境一二；正式值是第 9 項的 `participation_pct`）


def read(lines: Sequence[Line]) -> CaBenTermSheet:
    """先照 CA FCN 讀出，再改讀 BEN 不同的條目（範本辨識另由 `detect` 負責）。"""
    base = ca.read(lines)
    doc, fields = base.document, dict(base.fields)
    arts = doc.articles(1)

    def article(n: int) -> list[Line]:
        return doc.span_lines(arts.get(n))

    for name in FCN_ONLY:
        fields.pop(name, None)
    fields["monthly_coupon_pct"] = standard_fields.absent("monthly_coupon_pct", "月配息率（BEN 沒有配息）")

    # 第 9 項、價格表與標的、沒有 KO、無 KI
    _article9(fields, article(9))
    _table_fields(fields)
    art13 = TextIndex(article(13))
    _no_autocall(fields, art13, base.full_text)
    _ki_type(fields, TextIndex(article(9)), base.full_text)

    # 13.(6)、15.(4)(5)：沒有配息、參與率
    fields["no_coupon_art13"] = exactly_one(
        "no_coupon_art13", art13, re.escape(NO_COUPON_DATE), f"第 13 項沒有「{NO_COUPON_DATE}」"
    )
    art15 = TextIndex(article(15))
    fields["no_coupon_art15"] = exactly_one(
        "no_coupon_art15", art15, re.escape(MIN_RATE), f"第 15 項沒有「{MIN_RATE}」"
    )
    fields["participation_art15"] = exactly_one(
        "participation_art15", art15, ART15_PARTICIPATION, "第 15 項沒有「(5)參與率：x%」", lambda m: Decimal(m[1])
    )

    # 第 16 項情境
    scenarios = _article16(fields, article(16))
    bonus = [Mention.of_field("第 16 項基本假設「紅利率：x%」", fields["bonus_assumed"])]
    participation = [Mention.of_field("第 15 項 (5)「參與率：x%」", fields["participation_art15"])]
    for n in (1, 2):
        where = f"第 16 項情境分析{ca.SCENARIOS[n - 1]}"
        title = scenarios.titles[n - 1]
        bonus += [
            Mention.of_optional_hit(f"{where}代入行「最大值（x%，」", scenarios.calc.get(n), lambda v: v.bonus, title),
            Mention.of_optional_hit(
                f"{where}結果行「最大值（x%，」", scenarios.amount.get(n), lambda v: v.bonus, title
            ),
        ]
        participation.append(
            Mention.of_optional_hit(f"{where}代入行「x% x（」", scenarios.calc.get(n), lambda v: v.participation, title)
        )

    # 幣別出處：情境三只有剩餘金額是計價幣別的金額（CA FCN 不列入剩餘金額那行），改取它的幣別字（同 CA Super Bull）
    remainder = scenarios.remainder
    replaced = {
        "scenario_investment_currency": fields["scenario_investment_currency"],
        "scenario3_currency": ParsedField.present("scenario3_currency", remainder.value.currency, list(remainder.lines))
        if remainder is not None
        else ParsedField.missing("scenario3_currency", "第 16 項情境分析三找不到剩餘金額算式"),
    }
    fields["currency_others"] = standard_fields.occurrences(
        "currency_others",
        [
            replace(occ, value=replaced[occ.field]) if occ.field in replaced else occ
            for occ in fields["currency_others"].value
        ],
    )
    return CaBenTermSheet(fields, base.full_text, doc, scenarios, bonus, participation)
