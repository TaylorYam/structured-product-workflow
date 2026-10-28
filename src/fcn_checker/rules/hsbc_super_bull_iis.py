"""HSBC Super Bull 投資人須知專屬規則（docs/checker/rules/hsbc-super-bull-check-rules.md §3.8）：沒有配息。只用投資人須知的
讀出結果，不碰參考條件表；其他檢查點是各上手共用的投資人須知規則。
"""

from __future__ import annotations

from ..schema import CheckResult
from . import super_bull
from .iis import IisIssuerContext


def run_all(ctx: IisIssuerContext) -> list[CheckResult]:
    """第二「收益分配事項：請見產品說明書第一章第11 項之說明。」：固定寫法，找不到或不同 → 不一致。"""
    return [super_bull.fixed("iis.no_coupon", "no_coupon", "第二 收益分配事項", ctx.sheet.f("no_coupon"), ctx.document)]
