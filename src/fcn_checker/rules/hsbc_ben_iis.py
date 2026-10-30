"""HSBC BEN 投資人須知專屬規則（docs/checker/rules/hsbc-ben-check-rules.md §3.8）：沒有定期配息、標題的 (最終版)。只用投資人
須知的讀出結果，不碰參考條件表；其他檢查點是各上手共用的投資人須知規則。
"""

from __future__ import annotations

from ..schema import CheckResult, Item
from ..schema import CheckStatus as S
from . import super_bull
from .hsbc_ben import FINAL_EDITION_STATUS
from .iis import IisIssuerContext
from .kit import Check


def run_all(ctx: IisIssuerContext) -> list[CheckResult]:
    """第二「收益分配事項：請見產品說明書第一章第11 項之說明。」：固定寫法，找不到或不同 → 不一致；標題沒有「(最終版)」→
    `FINAL_EDITION_STATUS`（暫定做法，同說明書）。"""
    final = ctx.sheet.f("final_edition")
    check = Check("iis.final_edition", "final_edition", Item.expected("標題「(最終版)」"), ctx.document)
    return [
        super_bull.fixed("iis.no_coupon", "no_coupon", "第二 收益分配事項", ctx.sheet.f("no_coupon"), ctx.document),
        check.result(S.PASS, evidence=final.evidence, message="範本固定寫法")
        if final.ok
        else check.result(FINAL_EDITION_STATUS, evidence=final.evidence, reason="value_mismatch", message=final.note),
    ]
