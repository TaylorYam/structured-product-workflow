"""CA Super Bull 與 CA BEN 說明書（parsers/ca_super_bull.py、parsers/ca_ben.py）共用的第 16 項讀法（workflow #441）：基本
假設、情境價格表與匯率行、以標題切出情境一～三，以及情境三（交股）的假設句、股數、剩餘金額、報酬率算式與跨幣別的匯率。

兩者寫法不同的地方由各自的 `Variant` 宣告（基本假設的紅利標籤、情境價格表的欄、情境三假設句與算式的用字），算式的
regex 只有這裡一份；第 9 項（Super Bull 四欄觀察日表、BEN Bonus 公式）與情境一、二兩者完全不同，留在各自的 parser。
重算在 rules/delivery.py（`delivery.CA`）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import NamedTuple

from .. import standard_fields
from ..schema import Line, ParsedField
from ..standard_fields import ScenarioParam, ScenarioPrice
from ..text import squash
from . import ca, price_table
from .common import Hit, Mention, capture_group, dec, number, one
from .layout import TextIndex, capture

EQ = ca.EQ
MUL = "[×xX*]"
NUM = r"([\d,]+(?:\.\d+)?)"
SPCT = r"(-?\d+(?:\.\d+)?)%"
U = f"({ca.CCY})"
L, R = "[（(]", "[）)]"
TITLES = ("情境分析一：較佳情況", "情境分析二：一般情況", "情境分析三：較差情況")
# 跨幣別（第 16 項有匯率行，workflow #424、#428）：股數「N / (K x R)」、剩餘金額「P x F x R」、報酬率「W x P x R」多乘匯率 R
FX = rf"(?:{MUL}{NUM})?"
FX_LINE = rf"({ca.CCY})/({ca.CCY})=([\d,.]+)"
S3_REMAINDER = rf"整數股數{EQ}{NUM}股，畸零股數{EQ}{NUM}股，剩餘金額{EQ}{NUM}{MUL}{NUM}{FX}{EQ}{NUM}{U}"


@dataclass(frozen=True)
class Variant:
    """CA Super Bull、CA BEN 第 16 項寫法不同的地方。情境三算式的 regex 由這些用字組出（`assumed`、`shares`、`worse_return`）。"""

    bonus_label: str  # 基本假設「初始投資金額：…」後接的紅利標籤，也是情境價格表的開頭
    scenario_table: price_table.TableSpec
    worst: str  # 情境三假設句「假設於期末訂價日…收盤價為」的最差標的寫法（第 1 組是名稱）
    below_strike: str  # 情境三假設句收盤價後的「，低於其執行價」（沒寫為空字串）
    strike_of: str  # 情境三股數算式「每單位面額/…執行價*」的標的寫法
    minus: str  # 情境三報酬率「((W x P + R) / N)」後減的「-1」或「-100%」
    annualized_parens: tuple[str, str]  # 情境三「(平均年化報酬率:y%)」的左右括號

    @property
    def assumed(self) -> str:
        return rf"假設於期末訂價日{self.worst}收盤價為{NUM}{U}{self.below_strike}。"

    @property
    def shares(self) -> str:
        return rf"每單位面額/{self.strike_of}執行價\*{EQ}{NUM}/(\()?{NUM}{FX}(?(2)\)){EQ}{NUM}股"

    @property
    def worse_return(self) -> str:
        left, right = self.annualized_parens
        return (
            rf"{EQ}\(\({NUM}{MUL}{NUM}{FX}\+{NUM}\)/{NUM}\){self.minus}{EQ}{SPCT}{left}平均年化報酬率[:：]{SPCT}{right}"
        )


class Assumed(NamedTuple):
    """情境的假設句：最差標的名稱（單一標的沒寫，為 None）、收盤價。"""

    name: str | None
    close: Decimal


class SharesCalc(NamedTuple):
    """情境三「每單位面額/執行價* = N / K = S 股」；跨幣別「N / (K x R)」的匯率 R（同幣別為 None）。"""

    denomination: Decimal
    strike: Decimal
    shares: Decimal
    fx: Decimal | None = None


class RemainderCalc(NamedTuple):
    """情境三「整數股數＝W 股，畸零股數＝F 股，剩餘金額＝P x F＝R」。"""

    whole: Decimal
    fraction: Decimal
    close: Decimal
    fraction_again: Decimal  # 「P x F」再寫一次的畸零股數
    amount: Decimal
    currency: str
    fx: Decimal | None = None  # 跨幣別「P x F x R」的匯率


class WorseProfitReturn(NamedTuple):
    """情境三「= ((W x P + R) / N) - 1 = r% (平均年化報酬率:y%)」；跨幣別「W x P x 匯率」的匯率在 `fx`。"""

    whole: Decimal
    close: Decimal
    remainder: Decimal
    divisor: Decimal
    pct: Decimal
    annualized: Decimal
    fx: Decimal | None = None


class Worse(NamedTuple):
    """情境三的假設句與算式；找不到、或在情境裡有多處時為 None（由規則轉人工覆核）。"""

    assumed: Hit[Assumed] | None
    shares: Hit[SharesCalc] | None
    remainder: Hit[RemainderCalc] | None
    worse_return: Hit[WorseProfitReturn] | None


def opt_dec(x: str | None) -> Decimal | None:
    """選填的數字（例：跨幣別算式的匯率；沒寫為 None）。"""
    return None if x is None else dec(x)


@dataclass(frozen=True)
class Sections:
    """情境一～三：三個標題的原文行、各情境的範圍（第 n 個情境 = `bounds[n-1]`～`bounds[n]`）。"""

    ti: TextIndex
    titles: tuple[list[Line], list[Line], list[Line]]
    bounds: tuple[int, ...]

    def find(self, n: int, pattern: str, convert):
        """情境 n 裡剛好一處符合 `pattern` 的算式（交給 `convert` 轉型）；沒有或多處為 None。"""
        lo, hi = self.bounds[n - 1], self.bounds[n]
        return one(
            [
                Hit(convert(m), tuple(self.ti.lines_for(m.start(), m.end())))
                for m in self.ti.finditer(pattern)
                if lo <= m.start() < hi
            ]
        )

    def worse(self, v: Variant) -> Worse:
        """情境三的假設句、股數、剩餘金額、報酬率算式（範本規格 §5）。"""
        return Worse(
            self.find(3, v.assumed, lambda m: Assumed(m[1], dec(m[2]))),
            self.find(3, v.shares, lambda m: SharesCalc(dec(m[1]), dec(m[3]), dec(m[5]), opt_dec(m[4]))),
            self.find(
                3,
                S3_REMAINDER,
                lambda m: RemainderCalc(dec(m[1]), dec(m[2]), dec(m[3]), dec(m[4]), dec(m[6]), m[7], opt_dec(m[5])),
            ),
            self.find(
                3,
                v.worse_return,
                lambda m: WorseProfitReturn(
                    dec(m[1]), dec(m[2]), dec(m[4]), dec(m[5]), Decimal(m[6]), Decimal(m[7]), opt_dec(m[3])
                ),
            ),
        )


def sections(ti: TextIndex) -> Sections | None:
    """以標題切出情境一～三（到「※請注意」為止）；情境要剛好這三個（核對規則 §3.7），標題缺漏、順序不對或多出其他
    「情境分析X：」標題就當找不到（None）。"""
    starts = [ti.text.find(t) for t in TITLES]
    bounds = [*starts, ti.text.find("※請注意", max(starts))]
    if not (all(x >= 0 for x in bounds) and bounds == sorted(bounds)):
        return None
    if len(re.findall(r"情境分析[一二三四五六七八九十]+：", ti.text)) != len(TITLES):
        return None
    titles = tuple(ti.lines_for(s, s + len(t)) for s, t in zip(starts, TITLES, strict=True))
    return Sections(ti, titles, tuple(bounds))  # type: ignore[arg-type]


def worst_row(name: str | None, prices: ParsedField) -> int | None:
    """情境假設句寫的最差標的 → 價格表第幾檔：單一標的為第 1 檔；多標的依名稱（表下的「註:…」可能併進最後一列的名稱，
    所以比開頭），對不到為 None（共用規則轉人工覆核）。"""
    if not prices.ok:
        return None
    if len(prices.value) == 1:
        return 1
    if name is None:
        return None
    return next(
        (n for n, r in enumerate(prices.value, start=1) if r.name and squash(r.name).startswith(squash(name))), None
    )


def basics(fields: dict[str, ParsedField], art16: list[Line], v: Variant) -> None:
    """基本假設（天期、面額、初始投資金額、紅利）、情境價格表與其後的匯率行（範本規格 §5）。"""
    ti = TextIndex(art16)
    where = "第 16 項基本假設"
    tenor = capture("scenario_tenor", ti, r"基本假設天期：(\d+)個月", int, whole_match=True)
    denom = rf"每單位面額：([\d,]+)({ca.CCY})初始投資金額"
    denomination = capture("scenario_denomination", ti, denom, number, whole_match=True)
    fields["scenario_denomination_currency"] = capture_group(art16, denom, 2, "scenario_denomination_currency")
    invest = rf"初始投資金額：([\d,]+)({ca.CCY}){v.bonus_label}"
    investment = capture_group(art16, invest, 1, "scenario_initial_investment", number)
    fields["scenario_investment_currency"] = capture_group(art16, invest, 2, "scenario_investment_currency")
    fields["bonus_assumed"] = capture_group(art16, v.bonus_label + ca.PCT, 1, "bonus_assumed", Decimal)
    scenario_table = price_table.read(
        "scenario_table", ca.table_region(art16, v.bonus_label, "情境分析一"), v.scenario_table
    )
    fields["scenario_reprint_table"] = price_table.at("scenario_reprint_table", scenario_table, v.scenario_table)
    # 標的報價幣別和計價幣別不同時，情境價格表後多一行匯率（`美元/日圓=150.00`；規則核對幣別，workflow #335、#424、#428）
    fx = [ln for ln in art16 if re.fullmatch(FX_LINE, squash(ln.text))]
    fields["scenario_fx"] = ParsedField.present("scenario_fx", [squash(ln.text) for ln in fx], fx)
    fields["scenario_params"] = standard_fields.occurrences(
        "scenario_params",
        [
            ScenarioParam("scenario_tenor", where, tenor),
            ScenarioParam("scenario_denomination", where, denomination),
            ScenarioParam("scenario_initial_investment", where, investment),
        ],
    )


def worse_price(worse: Worse, title: list[Line], prices: ParsedField) -> ScenarioPrice:
    """情境三股數算式寫的執行價 → 價格表最差標的那一列的執行價（依假設句的名稱對列）。"""
    place = f"第一章第 16 項情境分析{ca.SCENARIOS[2]}股數算式的"
    strike = Mention.of_optional_hit(place, worse.shares, lambda v: v.strike, title).parsed("scenario3_strike")
    row = worst_row(worse.assumed.value.name, prices) if worse.assumed is not None else None
    return ScenarioPrice(place, row, "strike", strike)


def worse_currency(remainder: Hit[RemainderCalc] | None) -> ParsedField:
    """情境三的幣別出處：只有剩餘金額是計價幣別的金額（CA FCN 不列入剩餘金額那行），取它的幣別字。"""
    if remainder is None:
        return ParsedField.missing("scenario3_currency", "第 16 項情境分析三找不到剩餘金額算式")
    return ParsedField.present("scenario3_currency", remainder.value.currency, list(remainder.lines))
