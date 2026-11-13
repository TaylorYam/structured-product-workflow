"""MS 第一章第 18 項情境分析：假設、重印價格表與各情境的算式（docs/checker/templates/ms-zh-product-description.md §6、§7）。

只擷取文件上寫的值與原文行；核對在 rules/ms_scenario.py。抓不到的值為 None，由規則轉人工覆核。
DACN（docs/checker/templates/ms-zh-dacn.md §4.3，`accrual=True`）另看每個情境有沒有區間計息 `× （n/M）`、數展開成
`金額 × X% × （n/M）` 的算式、多讀 `固定配息率（X%）`／`浮動配息率（X%）` 的配息率；較差情境執行價多認
`連結標的（代碼）收盤價`（少「的」）的句型。有區間計息的情境另讀最後一步的區間計息算式、損益算式各項與假設的配息次數
（workflow #578）；左邊是文字的中間步驟不讀。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import NamedTuple

from ..schema import Line, ParsedField
from ..standard_fields import Money
from ..text import squash
from . import money, ms_tables
from .common import SCENARIO_HEAD, Hit, dec, hits, one
from .layout import TextIndex

N = r"([\d,]+(?:\.\d{2})?)"  # 金額：2 位小數，或整數（日圓樣本寫到整數位，Issue #181）
PNL = r"(-?[\d,]+(?:\.\d{2})?)"  # 損益（可為負）
TIMES = "[×xX]"  # 乘號：全形 ×，也有樣本寫半形 x（Issue #181）
UNIT = money.CURRENCY  # 金額後的幣別字（任一已知寫法；寫哪一個才對由共用規則核對，Issue #170）
TERM_AMOUNT = r"[\d,]+(?:\.\d+)?"  # DACN 損益算式一項的金額（到期贖回價值的位數同 FCN 較差情境不限）
TERM = rf"{TERM_AMOUNT}(?:{UNIT})?(?:{TIMES}\d+)?"  # DACN 損益算式的一項：金額（幣別）（× 次數）


class CouponCalc(NamedTuple):
    """每期配息算式「面額 × 月配息率 = 每期配息」。"""

    denomination: Decimal
    rate: Decimal
    amount: Decimal


class ProfitPnl(NamedTuple):
    """獲利情境損益算式「= 面額 + 每期配息 × k − 面額 = 損益」。"""

    denomination: Decimal
    coupon: Decimal
    periods: int
    principal: Decimal
    pnl: Decimal


class WorsePnl(NamedTuple):
    """較差情境損益算式「= 每期配息 × k + 到期贖回價值 − 面額 = 損益」。"""

    coupon: Decimal
    periods: int
    redemption: Decimal
    denomination: Decimal
    pnl: Decimal


class AccrualCalc(NamedTuple):
    """DACN 區間計息算式的最後一步「面額 × X% × （n/M） = 每期配息」（左邊是文字的中間步驟不讀）。"""

    denomination: Decimal
    rate: Decimal
    days: int  # n
    total_days: int  # M
    amount: Decimal


class PnlTerm(NamedTuple):
    """DACN 損益算式的一項「金額 × 次數」；沒寫次數是 1 次。"""

    amount: Decimal
    times: int


class PnlSum(NamedTuple):
    """DACN 損益算式的最後一步「= 項 + 項 + … − 面額 = 損益」：獲利情境第一項是拿回的本金、較差情境最後一項是到期贖回價值，
    其餘是配息項。"""

    terms: tuple[PnlTerm, ...]
    denomination: Decimal
    pnl: Decimal

    def split(self, profit: bool) -> tuple[Decimal, tuple[PnlTerm, ...]]:
        """（拿回的本金或到期贖回價值, 配息項）：獲利情境取第一項、較差情境取最後一項，其餘依序是配息項。"""
        return (self.terms[0].amount, self.terms[1:]) if profit else (self.terms[-1].amount, self.terms[:-1])


class WorseStrike(NamedTuple):
    """較差情境「連結標的(代碼)的收盤價 = … 小於其執行價 = N」。"""

    ticker: str  # 彭博代碼去空白
    strike: Decimal


@dataclass
class Scenario:
    number: str  # 情境編號（一、二…）
    title: str  # 例：較佳情況 A、一般情況、較差情況
    kind: str  # profit 獲利情境／worse 較差情境／default 發行機構無法履約（不核對）
    lines: list[Line]
    assumed: Hit[int | None] | None = None  # 假設的配息次數 k；「所有配息觀察期間」為 None
    coupons: list[Hit[CouponCalc]] = field(default_factory=list)
    coupon_formulas: int = 0  # 文件上「N 美元×X%」的處數（讀得出的每期配息算式應與它一樣多）
    rates: list[Hit[Decimal]] = field(default_factory=list)  # 算式中的每個 ×X%
    pnl: list[Hit[ProfitPnl | WorsePnl]] = field(default_factory=list)  # 損益算式（依情境種類）
    annualized: list[Hit[Decimal]] = field(default_factory=list)  # 年化報酬率 Y%
    strike: list[Hit[WorseStrike]] = field(default_factory=list)
    accrual: bool = False  # 有區間計息「× （n/M）」（DACN；FCN 沒有）
    accrual_formulas: int = 0  # 展開成「金額 × X% × （n/M）」的區間計息算式處數（`coupon_formulas` 也數到它們）
    guaranteed: list[Hit[int]] = field(default_factory=list)  # 「本商品第 g 個月發放固定配息」的 g（DACN）
    # 以下只在 DACN 有區間計息的情境讀（workflow #578）
    accruals: list[Hit[AccrualCalc]] = field(default_factory=list)
    pnl_sums: list[Hit[PnlSum]] = field(default_factory=list)  # 損益算式的最後一步
    paid: Hit[int] | None = None  # 假設的配息次數：「假設在第k 個配息觀察期間」的 k、「假設在第a 至第N 個…」的 N
    # 各金額旁的幣別字（商品幣別的出處）；股價與實物交割算式所在的行跟著標的走，不列入（money.stock_price_line）
    money: tuple[Money, ...] = ()


@dataclass
class ScenarioSection:
    lines: list[Line]
    denomination: Hit[Decimal] | None  # 總投資金額 = 1 單位商品面額 = N
    denomination_currency: Hit[str] | None  # 總投資金額旁的幣別字
    tenor: Hit[int] | None  # 年期：N 個月
    monthly: list[Hit[Decimal]]  # 月配息率=X%
    table: ParsedField  # 重印價格表
    scenarios: list[Scenario]


def _scenario(number: str, title: str, lines: list[Line], *, accrual: bool = False) -> Scenario:
    """一個情境；`accrual`（DACN）時配息率另讀 `固定配息率（X%）`、`浮動配息率（X%）`，較差情境執行價多認少「的」的句型
    （FCN 照舊）。"""
    unit = UNIT
    kind = "default" if "無法履約" in title else "worse" if "較差" in title else "profit"
    s = Scenario(number, title, kind, lines)
    ti = TextIndex(lines, separate_digits=True)
    s.money = money.amounts(ti, money.AMOUNT_THEN_CURRENCY, currency_group=2)
    assumed = [
        *hits(ti, r"假設在第(\d+)個(?:配息週期終止日|定價日)", lambda m: int(m[1])),
        *hits(ti, r"假設在第1至第(\d+)個(?:配息觀察期間|定價日)", lambda m: int(m[1])),
        *hits(ti, r"假設在所有配息觀察期間內", lambda m: None),
    ]
    s.assumed = one(assumed)
    s.coupons = hits(
        ti,
        rf"{N}{unit}{TIMES}(\d+(?:\.\d+)?)%(?:{TIMES}100%)?={N}{unit}",
        lambda m: CouponCalc(dec(m[1]), Decimal(m[2]), dec(m[3])),
    )
    s.coupon_formulas = len(list(ti.finditer(rf"\d{unit}{TIMES}\d+(?:\.\d+)?%")))
    s.accrual = re.search(rf"{TIMES}[（(]\d+/\d+[)）]", ti.text) is not None
    s.accrual_formulas = len(list(ti.finditer(rf"\d{unit}{TIMES}\d+(?:\.\d+)?%{TIMES}[（(]\d+/\d+[)）]")))
    s.guaranteed = hits(ti, r"本商品第(\d+)個月發放固定配息", lambda m: int(m[1]))
    if accrual and s.accrual:
        _accrual_terms(s, ti)
    rate = rf"(?:(?<!%){TIMES}|(?:固定|浮動)配息率[（(])" if accrual else rf"(?<!%){TIMES}"
    s.rates = hits(ti, rate + r"(\d+(?:\.\d+)?)%", lambda m: Decimal(m[1]))
    if kind == "profit":
        s.pnl = hits(
            ti,
            rf"={N}{unit}\+{N}{unit}(?:{TIMES}(\d+))?-{N}{unit}={PNL}{unit}",
            lambda m: ProfitPnl(dec(m[1]), dec(m[2]), int(m[3] or 1), dec(m[4]), dec(m[5])),
        )
    elif kind == "worse":
        s.pnl = hits(
            ti,
            rf"={N}{unit}{TIMES}(\d+)\+([\d,]+(?:\.\d+)?){unit}-{N}{unit}={PNL}{unit}",
            lambda m: WorsePnl(dec(m[1]), int(m[2]), dec(m[3]), dec(m[4]), dec(m[5])),
        )
        of = "的?" if accrual else "的"
        s.strike = hits(
            ti,
            rf"連結標的[（(]([A-Z0-9./-]+)[）)]{of}收盤價=[\d,]+(?:\.\d+)?{unit}小於其執行價=([\d,]+\.\d{{4}}){unit}",
            lambda m: WorseStrike(m[1], dec(m[2])),
        )
    s.annualized = hits(ti, r"年化報酬率為(-?\d+(?:\.\d+)?)%", lambda m: Decimal(m[1]))
    return s


def _accrual_terms(s: Scenario, ti: TextIndex) -> None:
    """DACN 有區間計息的情境（範本規格 ms-zh-dacn.md §4.3，workflow #578）：只讀最後一步的純數字算式——區間計息
    `面額 美元×X%×（n/M） = B 美元`、損益 `= 項+項+…-面額 美元 = 損益 美元`（項的幣別字可省略，例 `Bx11`）——與假設段的
    配息次數。"""
    unit = UNIT
    s.accruals = hits(
        ti,
        rf"{N}{unit}{TIMES}(\d+(?:\.\d+)?)%{TIMES}[（(](\d+)/(\d+)[)）]={N}{unit}",
        lambda m: AccrualCalc(dec(m[1]), Decimal(m[2]), int(m[3]), int(m[4]), dec(m[5])),
    )
    s.pnl_sums = hits(
        ti,
        rf"=({TERM}(?:\+{TERM})+)-{N}{unit}={PNL}{unit}",
        lambda m: PnlSum(tuple(_term(t) for t in m[1].split("+")), dec(m[2]), dec(m[3])),
    )
    s.paid = one(
        [
            *hits(ti, r"假設在第(\d+)個配息觀察期間", lambda m: int(m[1])),
            *hits(ti, r"假設在第\d+至第(\d+)個配息觀察期間", lambda m: int(m[1])),
        ]
    )


def _term(text: str) -> PnlTerm:
    m = re.fullmatch(rf"({TERM_AMOUNT})(?:{UNIT})?(?:{TIMES}(\d+))?", text)
    assert m is not None  # TERM 已比對過
    return PnlTerm(dec(m[1]), int(m[2] or 1))


def section(lines: list[Line], *, accrual: bool = False) -> ScenarioSection:
    """第 18 項：假設（到第一個情境前）、重印價格表（月配息率之後到情境一）、各情境（依「情境X：」切開）；`accrual` 見
    `_scenario`。"""
    unit = UNIT
    heads = [i for i, ln in enumerate(lines) if SCENARIO_HEAD.match(squash(ln.text))]
    head_end = heads[0] if heads else len(lines)
    ti = TextIndex(lines[:head_end])
    monthly = hits(ti, r"月配息率=(\d+(?:\.\d+)?)%", lambda m: Decimal(m[1]))
    if len(monthly) == 1 and heads:
        last = monthly[0].lines[-1]
        region = lines[lines.index(last) + 1 : head_end]
        table = ms_tables.price_table("scenario_table", region, ms_tables.SCENARIO_TABLE)
    else:
        table = ParsedField.missing("scenario_table", "找不到第 18 項重印價格表（月配息率之後、情境一之前）")
    scenarios = []
    for k, i in enumerate(heads):
        m = SCENARIO_HEAD.match(squash(lines[i].text))
        end = heads[k + 1] if k + 1 < len(heads) else len(lines)
        scenarios.append(_scenario(m[1], m[2], lines[i:end], accrual=accrual))
    notional = rf"總投資金額=1單位商品面額=([\d,]+(?:\.\d+)?)({unit})"
    return ScenarioSection(
        lines=lines,
        denomination=one(hits(ti, notional, lambda m: dec(m[1]))),
        denomination_currency=one(hits(ti, notional, lambda m: m[2])),
        tenor=one(hits(ti, r"年期[:：](\d+)個月", lambda m: int(m[1]))),
        monthly=monthly,
        table=table,
        scenarios=scenarios,
    )
