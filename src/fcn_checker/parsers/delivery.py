"""交股情境（期末股價低於執行價、以實物交付股票）HSBC 寫法的算式（HSBC Super Bull 情境三、HSBC BEN 與 MS BEN 的交股情境）：
「N × 匯率 / K = S 股」、交割股數、零股數、「P × F / 匯率 = R」、「(P × W / 匯率) + R = V」與損益「A - N = 損益」。

各上手 parser 用自己的 regex 讀，交出這裡的型別；重算在 rules/delivery.py（`worse`，HSBC 寫法 `delivery.HSBC`）。CA 寫法的
交股算式（股數 3 位無條件去位、剩餘金額與整數股數寫在同一句）在 parsers/ca_super_bull.py。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import NamedTuple

from .common import Hit, dec


class SharesCalc(NamedTuple):
    """情境三「= N × 匯率 / K = S 股」。"""

    denomination: Decimal
    fx: Decimal
    strike: Decimal
    shares: Decimal


class RemainderCalc(NamedTuple):
    """情境三「= P × F / 匯率 = R」。"""

    close: Decimal
    fraction: Decimal
    fx: Decimal
    amount: Decimal


class ValueCalc(NamedTuple):
    """情境三「= (P × W / 匯率) + R = V」。"""

    close: Decimal
    whole: Decimal
    fx: Decimal
    remainder: Decimal
    amount: Decimal


class Pnl(NamedTuple):
    """損益「= A - N = 損益」。"""

    amount: Decimal
    investment: Decimal
    pnl: Decimal


def pnl(m: re.Match[str]) -> Pnl:
    """損益 regex 的三組（A、N、損益）→ `Pnl`。"""
    return Pnl(dec(m[1]), dec(m[2]), dec(m[3]))


@dataclass
class Delivery:
    """一個交股情境的算式（BEN；HSBC Super Bull 情境三的同名欄位直接放在 `SbScenarios`），交給 `rules.delivery.worse`。"""

    shares: Hit[SharesCalc] | None = None
    whole: Hit[Decimal] | None = None
    fraction: Hit[Decimal] | None = None
    remainder: Hit[RemainderCalc] | None = None
    value: Hit[ValueCalc] | None = None
    worse_pnl: Hit[Pnl] | None = None
    worse_annualized: Hit[Decimal] | None = None
