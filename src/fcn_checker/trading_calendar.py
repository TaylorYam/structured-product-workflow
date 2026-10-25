"""交易所假日表與一籃子預定交易日（Issue #208）。

說明書的自動提前出場觀察期是期始日到期末日之間的「一籃子預定交易日」：所有標的交易所都開市的日子。
所以期始日 = 前一期期末日（第 1 期為發行日）後的下一個一籃子預定交易日，跳過週末，也跳過任一標的交易所的休市日。

標的的市場由彭博代號最後的交易所碼決定（`config/exchange_holidays.toml` 的 `bloomberg_codes`）。
有標的的交易所不在假日表、或要推算的日期不在假日表涵蓋範圍時，結果帶著「不確定」的原因：
規則怎麼用這個原因見 `rules/kit.period_start`：只有期始日比推算結果晚時才轉人工覆核。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass


def exchange_code(ticker: str) -> str | None:
    """彭博代號（例：GOOGL UW、ABC UN Equity）最後的交易所碼（大寫）；只有代號、沒有交易所碼時為 None。"""
    parts = [p for p in ticker.upper().split() if p != "EQUITY"]
    return parts[-1] if len(parts) >= 2 else None


@dataclass(frozen=True)
class Market:
    """一個市場（例：美國）：彭博交易所碼、全天休市日、假日表涵蓋範圍與交易幣別（ISO 代碼；沒寫時為 None）。"""

    code: str
    name: str
    bloomberg_codes: frozenset[str]
    covered_from: dt.date
    covered_through: dt.date
    holidays: frozenset[dt.date]
    currency: str | None = None

    def covers(self, first: dt.date, last: dt.date) -> bool:
        return self.covered_from <= first and last <= self.covered_through


@dataclass(frozen=True)
class NextTradingDay:
    """推算結果：下一個一籃子預定交易日，以及無法確定的原因（空字串表示確定）。"""

    day: dt.date
    uncertain: str = ""


@dataclass(frozen=True)
class TradingCalendar:
    """交易所假日表（`config/exchange_holidays.toml`）。"""

    version: int
    markets: tuple[Market, ...]

    def market_of(self, ticker: str) -> Market | None:
        """彭博代號（例：GOOGL UW、ABC UN Equity）最後的交易所碼對到的市場；對不到為 None。"""
        code = exchange_code(ticker)
        return next((m for m in self.markets if code in m.bloomberg_codes), None)

    def currency_of(self, ticker: str) -> str | None:
        """彭博代號的交易所交易幣別（價格表的標的交易幣別，Issue #227）；交易所不在假日表或沒寫幣別時為 None。"""
        m = self.market_of(ticker)
        return m.currency if m is not None else None

    def market(self, code: str) -> Market | None:
        """市場代號（假日表 [markets.<代號>]，例：TW 臺北營業日）對到的市場；假日表沒有時為 None。"""
        return next((m for m in self.markets if m.code == code), None)

    def closed(self, day: dt.date, tickers: Sequence[str] | None) -> bool:
        """週末，或籃子裡已知市場的休市日（不在假日表的市場不算）。"""
        markets = {self.market_of(t) for t in tickers or ()} - {None}
        return day.weekday() >= 5 or any(day in m.holidays for m in markets)

    def next_trading_day(
        self, after: dt.date, tickers: Sequence[str] | None, *, also: Sequence[Market] = ()
    ) -> NextTradingDay:
        """`after` 之後的下一個一籃子預定交易日（`also` 的市場也要開市，例：並須為臺北營業日）。讀不到標的、標的交易所
        不在假日表或超出涵蓋範圍時另附原因。"""
        markets: list[Market] = list(also)
        unknown: list[str] = []
        for t in tickers or ():
            m = self.market_of(t)
            if m is None:
                if t not in unknown:
                    unknown.append(t)
            elif m not in markets:
                markets.append(m)
        reasons = []
        if not tickers:
            reasons.append("讀不到標的，無法確定標的交易所的假日")
        if unknown:
            reasons.append(f"標的 {'、'.join(unknown)} 的交易所不在交易所假日表")
        return self._next_open(after, markets, reasons)

    def next_business_day(self, after: dt.date, market: Market) -> NextTradingDay:
        """`after` 之後 `market` 的下一個營業日（例：臺北營業日）；超出涵蓋範圍時另附原因。"""
        return self._next_open(after, [market], [])

    @staticmethod
    def _next_open(after: dt.date, markets: Sequence[Market], reasons: list[str]) -> NextTradingDay:
        day = after + dt.timedelta(days=1)
        while day.weekday() >= 5 or any(day in m.holidays for m in markets):
            day += dt.timedelta(days=1)
        first = after + dt.timedelta(days=1)
        reasons.extend(
            f"{first}～{day} 不在{m.name}假日表的涵蓋範圍（{m.covered_from}～{m.covered_through}）"
            for m in markets
            if not m.covers(first, day)
        )
        return NextTradingDay(day, "；".join(reasons))
