"""BNP Super Bull 中文產品說明書（docs/checker/templates/bnp-zh-super-bull.md）：版面、封面、標的表與共用段落同 BNP FCN
（parsers/bnp.py），先照 FCN 讀出，再改讀 Super Bull 不同的條目（§14 日期表、§15 沒有配息、§16 出場紅利、§17 無 KI 與
到期公式、§18 情境）。

只支援現行版範本（2026-06 起交易）；認得出是 Super Bull 舊版時辨識交出「不支援的範本版本」（範本規格 §1、§8）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import NamedTuple

from .. import standard_fields
from ..schema import DetectionResult, Evidence, Line, ParsedField
from ..standard_fields import ScenarioPrice
from ..text import squash
from . import bnp, bnp_scenario
from .bnp_scenario import NUM, PCT, ROUNDING, StrikeAssumption, U, WorseReturn
from .common import Hit, Mention, dec, exactly_one, hits, one
from .layout import TextIndex
from .super_bull import ProfitReturn, Wording, fill_schedule

TEMPLATE_ID = "bnp-zh-sb-pd"
PARSER_VERSION = "1"
OLD_VERSION = "BNP Super Bull 舊版說明書（2026-06 以前的範本），不支援自動核對"
NAME_EN = "Super Bull Early Redeemable Note"
NO_COUPON = "配息資料及其計算公式：不適用。"
# §16 出場紅利（現行版 `[100% + X%]`；舊版 `{100.00% + X% }` 不支援）
BONUS = r"自動提前贖回金額=商品面額×\[100%\+(\d+(?:\.\d+)?)%\]"
# 第二章交易架構「以商品面額之{100% + X%}贖回」
BONUS_CH2 = r"以商品面額之\{100%\+(\d+(?:\.\d+)?)%\}贖回"
PARTICIPATION = "參與率：100%。"
MIN_RATE = "最低保證配息率：無。"
# §17 到期贖回兩款（範本規格 §2；說明書 (1) 寫「表現最差連結標之執行價格」少一個「的」，兩種都收）
MATURITY_UP = (
    r"\(1\)若表現最差連結標的之收盤價格大於或等於其執行價格，則投資人就每單位商品可領回：商品面額×\[100%\+100%×"
    r"\(表現最差連結標的於期末評價日之收盤價格÷表現最差連結標的?之執行價格-1\)\]；或"
)
MATURITY_DOWN = (
    r"\(2\)若表現最差連結標的之收盤價格小於其執行價格，則投資人將會收到發行機構以實體交付表現最差連結標的股數"
    r"及以現金交付剩餘金額"
)
KO_SB = "自動提前贖回評價日：如下表所列之日期"
KO_EVENT = (
    r"「自動提前贖回事件」：(?:計算代理機構將依專業合理的方式釐定，)?若於任一自動提前贖回評價日，(.+?)，"
    r"則視為發生自動提前贖回事件"
)
MAX_DELAY = 7  # 評價日最多晚於「發行日 + m 個月」幾個日曆天（範本規格 §4.1；樣本 0～3）
WORDS = Wording("BNP", "評價日", "自動提前贖回日", "期末評價日")
# FCN 才有的 BNP 專屬欄位：Super Bull 讀不到，也不交給規則
FCN_ONLY = (
    "coupon_formula_pct",
    "coupon_decimals",
    "coupon_currency_art15",
    "min_coupon_rate",
    "autocall_redemption_pct",
    "maturity_redemption_pct",
)


# ---------------------------------------------------------------- 辨識


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §8）：封面是 BNP Super Bull（標題、發行機構、中英文名稱）且現行版特徵、五章、第一章 1～27 條、
    沒有配息全部成立才是 Super Bull 現行版。封面是 BNP Super Bull、現行版特徵一個都沒有 → 舊版（不支援；舊版的章名、
    頁首版面也不同，所以不看結構）。"""
    doc = bnp.document(lines)
    cover = doc.before_chapter1()
    items = bnp.cover_items(cover)
    zh, en = (squash(items[n].value) if n in items else "" for n in (2, 3))
    issuer = items.get(5)
    ch1 = bnp.chapter1(doc)

    def text(key: str) -> str:
        return TextIndex(doc.span_lines(ch1.spans.get(key))).text

    evidence = [Evidence.of(x) for x in cover[:2]]
    family = [
        (bnp.COVER_TITLE in TextIndex(cover).text, "封面標題不是「中文產品說明書(適用於專業投資人及OSU 客戶)」"),
        (
            issuer is not None and issuer.label == "發行機構名稱" and squash(issuer.value) == squash(bnp.ISSUER_EN),
            f"封面第 5 項發行機構名稱不是 {bnp.ISSUER_EN}",
        ),
        (
            zh.startswith("法國巴黎銀行") and "結構型商品(不保本)(無擔保)" in zh and "固定配息" not in zh,
            "商品中文名稱不是法國巴黎銀行…結構型商品(不保本)(無擔保)（不含固定配息）",
        ),
        (
            squash(NAME_EN) in en and squash(f"issued by {bnp.ISSUER_EN} (unsecured)") in en,
            f"商品英文名稱不含 {NAME_EN} 與 issued by {bnp.ISSUER_EN} (unsecured)",
        ),
    ]
    failed = [message for ok, message in family if not ok]
    if failed:
        return DetectionResult(False, failed, evidence)
    current = [
        (bool(bnp.page_headers(lines)), "頁首不是「第N頁，共M頁」"),
        (re.search(BONUS, text("autocall")) is not None, "自動提前贖回金額不是「商品面額 × [100% + X%]」"),
        (PARTICIPATION in text("maturity"), f"到期贖回計算公式沒有「{PARTICIPATION}」"),
    ]
    if not any(ok for ok, _ in current):
        return DetectionResult(False, [m for _, m in current], evidence, unsupported=OLD_VERSION)
    for ok, message in (
        *current,
        (all(n in doc.chapters for n in range(1, 6)), "章名不是「第X章、」寫法或五章不完整"),
        (set(range(1, 28)) <= set(doc.articles(1)), "第一章條號 1–27 不連續"),
        (NO_COUPON in text("coupon"), f"第一章配息資料不是「{NO_COUPON}」"),
    ):
        if not ok:
            failed.append(message)
    return DetectionResult(not failed, failed, evidence)


# ---------------------------------------------------------------- 情境（§18(3)，範本規格 §5）


class BonusCalc(NamedTuple):
    """情境一「自動提前贖回金額 = 1 單位商品 × 面額 × [100% + X%] = A」。"""

    denomination: Decimal
    currency: str
    bonus: Decimal
    amount: Decimal
    amount_currency: str


class BestCalc(NamedTuple):
    """情境二「到期贖回金額 = 1 單位商品 × 面額 × [100% + 100% × (…收盤價格 ÷ …執行價格 - 1)] = A(四捨五入…)」。"""

    denomination: Decimal
    currency: str
    amount: Decimal
    amount_currency: str
    decimals: int


@dataclass
class SbScenario:
    """Super Bull 情境的專屬算式（FCN 共用的假設句、股數、剩餘金額在 `bnp_scenario.Scenario`）。"""

    bonus: Hit[BonusCalc] | None = None  # 情境一
    best: Hit[BestCalc] | None = None  # 情境二
    best_assumed: Hit[StrikeAssumption] | None = None  # 情境二假設句「收盤價格 C 大於其執行價格 K」
    total_return: Hit[ProfitReturn] | None = None  # 情境一、二「總報酬率 = (贖回金額 ÷ 總投資金額) - 1 = …」


def _scenario(s: bnp_scenario.Scenario) -> SbScenario:
    """情境專屬算式；較差情境的總報酬率沒有累積配息，改填進 `s.worse_return`（累積為 0），沿用 FCN 的驗算。"""
    ti = TextIndex(s.lines, separate_digits=True)
    out = SbScenario()
    out.bonus = one(
        hits(
            ti,
            rf"自動提前贖回金額=1單位商品×{NUM}({U})×\[100%\+{PCT}\]={NUM}({U})",
            lambda m: BonusCalc(dec(m[1]), m[2], Decimal(m[3]), dec(m[4]), m[5]),
        )
    )
    out.best = one(
        hits(
            ti,
            rf"到期贖回金額=1單位商品×{NUM}({U})×\[100%\+100%×\(表現最差連結標的於期末評價日之收盤價格÷"
            rf"表現最差連結標的之執行價格-1\)\]={NUM}({U}){ROUNDING}",
            lambda m: BestCalc(dec(m[1]), m[2], dec(m[3]), m[4], bnp_scenario.DECIMALS[m[5]]),
        )
    )
    out.best_assumed = one(
        hits(
            ti,
            rf"於期末評價日之收盤價格{NUM}({U})大於(?:或等於)?其執行價格([\d,]+\.\d{{4}})({U})",
            lambda m: StrikeAssumption(dec(m[1]), m[2], dec(m[3]), m[4]),
        )
    )
    out.total_return = one(
        hits(
            ti,
            rf"總報酬率=\((?:自動提前贖回金額|到期贖回金額)÷總投資金額\)-1=\[?\({NUM}({U})÷{NUM}({U})\)-1="
            rf"{PCT}\(年化報酬率：{PCT}\)",
            lambda m: ProfitReturn(dec(m[1]), dec(m[3]), Decimal(m[5]), Decimal(m[6])),
        )
    )
    s.worse_return = one(
        hits(
            ti,
            rf"總報酬率=\[\((\d[\d,]*)股×.+?收盤價格{NUM}({U})(÷匯率)?\+現金交割{NUM}({U})\)"
            rf"÷總投資金額{NUM}({U})\]-1={PCT}",
            lambda m: WorseReturn(
                int(dec(m[1])),
                dec(m[2]),
                m[3],
                bool(m[4]),
                dec(m[5]),
                m[6],
                Decimal(0),
                "",
                dec(m[7]),
                m[8],
                Decimal(m[9]),
            ),
        )
    )
    return out


# ---------------------------------------------------------------- 讀出


@dataclass
class SuperBullTermSheet(bnp.BnpTermSheet):
    sb_scenarios: list[SbScenario]  # 各情境的專屬算式，順序同 `scenarios.scenarios`
    bonus_mentions: list[Mention]  # 出場紅利的其他出處（§18 情境一、第二章交易架構；正式值是 §16 的 `coupon_pa_pct`）


def _ko_wording(art14: list[Line], where: str) -> ParsedField:
    """§14(6)「自動提前贖回評價日：如下表所列之日期」剛好一處、沒有 FCN 的配息日或每日觀察寫法 → {"type": "SB"}。"""
    name = "ko_wording"
    ti = TextIndex(art14)
    found = [m for m in ti.finditer(re.escape(KO_SB))]
    fcn = re.search(bnp.KO_D, ti.text) or re.search(bnp.KO_P[0], ti.text)
    if len(found) == 1 and not fcn:
        m = found[0]
        return ParsedField.present(name, {"type": "SB", "k": None}, ti.lines_for(m.start(), m.end()))
    return ParsedField.invalid(name, art14, f"{where}(6) 自動提前贖回評價日的寫法缺漏或不在範本規格內")


def _schedule(fields: dict[str, ParsedField], where: str) -> None:
    """日期表只列評價日（範本規格 §4.1）：評價日當天到其後 7 個日曆天是「發行日 + m 個月」；表頭要是 Super Bull 的寫法。"""
    table = fields["date_table"]
    fill_schedule(
        fields,
        table,
        lambda: [(r["period"], r["pricing"]) for r in table.value["rows"]],
        table_name=f"{where}日期表",
        words=WORDS,
        early=0,
        late=MAX_DELAY,
        needs=(fields["ko_wording"],),
        evidence=fields["ko_wording"].evidence,
        problem=lambda: (
            "" if table.value["type"] == "SB" else f"{where}日期表表頭不是「j、自動提前贖回評價日、自動提前贖回日」"
        ),
    )


def ki_type(art17: list[Line], table: ParsedField) -> ParsedField:
    """無 KI：§17 沒有「觸及生效事件」、到期贖回是兩款寫法、§11 標的表沒有下限價格列（範本規格 §2、核對規則 §3.5）。"""
    name = "ki_type"
    ti = TextIndex(art17)
    problems = []
    if "觸及生效事件" in ti.text:
        problems.append("到期贖回計算公式有「觸及生效事件」")
    if not (re.search(MATURITY_UP, ti.text) and re.search(MATURITY_DOWN, ti.text)):
        problems.append("到期贖回計算公式不是範本的兩款寫法")
    if table.ok and "ki" in table.value["pcts"]:
        problems.append("標的表有下限價格列")
    if problems:
        return ParsedField.invalid(name, art17, "無法判定無 KI：" + "；".join(problems))
    return ParsedField.present(name, "none", art17)


def _bonus(m: re.Match[str]) -> Decimal:
    """出場紅利 X%（句型第 1 組）。"""
    return Decimal(m[1])


def read(lines: Sequence[Line]) -> SuperBullTermSheet:
    """先照 BNP FCN 讀出，再改讀 Super Bull 不同的條目（範本辨識另由 `detect` 負責）。"""
    base = bnp.read(lines)
    doc, ch1, fields = base.document, base.chapter1, dict(base.fields)
    art_label = ch1.label

    def article(key: str) -> list[Line]:
        return doc.span_lines(ch1.spans.get(key))

    for name in FCN_ONLY:
        fields.pop(name, None)
    fields["monthly_coupon_pct"] = standard_fields.absent("monthly_coupon_pct", "月配息率（Super Bull 沒有配息）")

    # §14 日期
    fields["ko_wording"] = _ko_wording(article("dates"), art_label("dates"))
    _schedule(fields, art_label("dates"))

    # §15～§17
    no_coupon = f"{art_label('coupon')}不是「{NO_COUPON}」"
    fields["no_coupon"] = exactly_one("no_coupon", TextIndex(article("coupon")), re.escape(NO_COUPON), no_coupon)
    art16 = article("autocall")
    fields["coupon_pa_pct"] = exactly_one(
        "coupon_pa_pct",
        TextIndex(art16),
        BONUS,
        f"找不到{art_label('autocall')}「自動提前贖回金額 = 商品面額 × [100% + X%]」",
        _bonus,
    )
    fields["ko_memory"] = bnp.ko_memory(
        "ko_memory", art16, KO_EVENT, f"找不到{art_label('autocall')}自動提前贖回事件定義句"
    )
    art17 = article("maturity")
    ti17 = TextIndex(art17)
    fields["ki_type"] = ki_type(art17, fields["underlying_table"])
    maturity = art_label("maturity")
    fields["maturity_formula"] = exactly_one(
        "maturity_formula", ti17, MATURITY_UP, f"{maturity}(1) 不是「商品面額 × [100% + 100% × (… ÷ … - 1)]」的寫法"
    )
    fields["participation_rate"] = exactly_one(
        "participation_rate", ti17, re.escape(PARTICIPATION), f"{maturity}沒有「{PARTICIPATION}」"
    )
    fields["min_guaranteed_rate"] = exactly_one(
        "min_guaranteed_rate", ti17, re.escape(MIN_RATE), f"{maturity}沒有「{MIN_RATE}」"
    )
    bnp.pct_fields(fields, art_label("underlying"))

    # 幣別出處：Super Bull 沒有配息款項
    fields["currency_others"] = standard_fields.occurrences(
        "currency_others", [o for o in fields["currency_others"].value if o.code != "coupon_currency"]
    )

    # §18 情境
    scenario_item = art_label("scenario")
    extras = [_scenario(s) for s in base.scenarios.scenarios]
    params = [p for p in fields["scenario_params"].value if p.code in ("scenario_denomination", "scenario_principal")]
    fields["scenario_params"] = standard_fields.occurrences("scenario_params", params)
    prices = list(fields["scenario_prices"].value)
    for s, x in zip(base.scenarios.scenarios, extras, strict=True):
        if s.kind == "best":
            where = f"{scenario_item}情境{s.number}"
            strike = Mention.of_optional_hit(
                f"{where}「收盤價格…大於其執行價格…」假設句", x.best_assumed, lambda v: v.strike, s.lines[:1]
            )
            row = bnp.worst_row(s, fields["underlying_prices"])
            prices.insert(0, ScenarioPrice(f"{where}假設句的", row, "strike", strike.parsed("scenario_strike")))
    fields["scenario_prices"] = standard_fields.occurrences("scenario_prices", prices)

    # 出場紅利的其他出處
    autocall = [(s, x) for s, x in zip(base.scenarios.scenarios, extras, strict=True) if s.kind == "autocall"]
    mentions = []
    for s, x in autocall[:1]:
        where = f"{scenario_item}情境{s.number}自動提前贖回金額算式"
        mentions.append(Mention.of_optional_hit(where, x.bonus, lambda v: v.bonus, s.lines[:1]))
    if not autocall:
        mentions.append(Mention.missing(f"{scenario_item}自動提前贖回情況", base.scenarios.lines[:1]))
    ch2 = exactly_one(
        "bonus_ch2",
        TextIndex(doc.chapter_lines(2)),
        BONUS_CH2,
        "第二章交易架構找不到「以商品面額之{100% + X%}贖回」",
        _bonus,
    )
    mentions.append(Mention.of_field("第二章交易架構提前贖回金額", ch2))

    return SuperBullTermSheet(
        fields,
        base.full_text,
        doc,
        base.scenarios,
        [],
        base.page_headers,
        ch1,
        extras,
        mentions,
    )
