"""標的定價（#442）：一個標的的有效期初價格、執行價、下限價、KO 價與換算後收盤價 → 表現、跌破了沒、最差一檔。

純 function、不碰資料庫。比價的到期判定（`observation`）與曝險分析（`exposure.assess`）都是它的呼叫端，同一套算法
只寫一次。對外：
- `Leg`：一個標的的價格與收盤價；`performance`（收盤價 ÷ 有效期初價格 − 1）、`to_strike`（距執行價）、`below`。
- `worst`：最差表現的一檔（同表現取序號小的）；任一檔缺收盤價或期初價格時 None。
- `converted`：收盤價換回價格基準（拆股，ADR 0025）。
- `KiTouch`：KI 第一次觸及的日期與代碼（比價存進 `workflow.pricing_status`，曝險讀它，不解析顯示文字）。

有效期初價格：lookback（#374）寫過回顧期初價格就用它，否則進場價；執行價是資料庫存的（lookback 之後是回顧執行價）。
這兩個由呼叫端從 `product_store.Stored`（或比價的 `Underlying`）帶進來。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sp_workflow.stock_splits import SplitBook


@dataclass(frozen=True)
class Leg:
    """一檔商品的一個標的：條件價格與換回價格基準的收盤價（或試算價）。"""

    seq: int
    code: str
    initial: Decimal | None  # 有效期初價格（lookback 用回顧期初價格）
    strike: Decimal | None  # 執行價
    ki: Decimal | None  # 下限價；沒有 KI 時 None
    close: Decimal | None  # 換回價格基準的收盤價；缺時 None
    ko: Decimal | None = None  # KO 價

    @property
    def performance(self) -> Decimal | None:
        """收盤價 ÷ 有效期初價格 − 1；缺價格時 None。"""
        if self.close is None or not self.initial:
            return None
        return self.close / self.initial - 1

    @property
    def to_strike(self) -> Decimal | None:
        """距執行價：收盤價 ÷ 執行價 − 1（負的是已低於執行價）；缺價格時 None。"""
        return None if self.close is None or not self.strike else self.close / self.strike - 1

    def below(self, level: Decimal | None) -> bool:
        """收盤價跌破 `level`（嚴格低於；`level` 或收盤價空白時不算）。"""
        return self.close is not None and level is not None and self.close < level

    @property
    def below_strike(self) -> bool:
        return self.below(self.strike)

    @property
    def below_ki(self) -> bool:
        return self.below(self.ki)


def worst(legs: Sequence[Leg]) -> Leg | None:
    """最差表現的一檔（收盤價 ÷ 有效期初價格最小，同表現取序號小的）；沒有標的或任一檔算不出表現時 None。"""
    if not legs or any(leg.performance is None for leg in legs):
        return None
    return min(legs, key=lambda leg: (leg.close / leg.initial, leg.seq))  # type: ignore[operator]


def converted(book: SplitBook, code: str, close: Decimal, basis: dt.date, day: dt.date) -> Decimal:
    """`day` 的收盤價換回價格基準日 `basis` 的基準（`SplitBook.factor`，#275、ADR 0025）。"""
    return close * book.factor(code, basis, day)


@dataclass(frozen=True)
class KiTouch:
    """KI 第一次觸及：日期與標的代碼（AKI 每日觀察；EKI 在最終比價日）。"""

    day: dt.date
    code: str
