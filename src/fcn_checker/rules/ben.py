"""BEN（Bonus Enhanced Notes）各上手共用的說明書內部規則（HSBC、MS、CA 的核對規則 §3）：範圍（沒有 KO、無 KI）、固定寫法、
紅利與參與率的其他出處（workflow #439）。

各上手只給項目名稱（「沒有 KO」「無 KI」是依哪幾處判定，由 parser 讀出）、要核對哪些固定寫法與出處、型態範圍與情境算式；
交股情境的算式在 rules/delivery.py（HSBC、MS 用 `delivery.HSBC`／`delivery.MS`，CA 用 `delivery.CA`），紅利門檻在
parsers/ben.py 推得、由共用的 `field.coupon_barrier_pct` 核對。
"""

from __future__ import annotations

from collections.abc import Sequence

from ..parsers.common import Mention
from ..schema import CheckResult
from . import kit, super_bull
from .kit import IssuerContext


def fixed(ctx: IssuerContext, rid: str, field: str, name: str) -> CheckResult:
    """固定寫法：讀到 → 通過；找不到或寫法不同 → 不一致。"""
    return super_bull.fixed(rid, field, name, ctx.ts.f(field), ctx.document)


def no_autocall(ctx: IssuerContext, name: str) -> CheckResult:
    """沒有 KO（由 parser 依開頭句等判定，`name` 寫依哪幾處；讀不到 → 人工覆核）：不會提前出場，Non-Call = 天期。"""
    pf = ctx.ts.f("no_autocall")
    check = kit.doc_check(ctx, "doc.no_autocall", "no_autocall", name, pf)
    return check.compare(None, None, ok=True, message="沒有 KO：不會提前出場，Non-Call = 天期")


def no_ki(ctx: IssuerContext, name: str) -> CheckResult:
    """無 KI（由 parser 依全文、價格表、交股句判定，`name` 寫依哪幾處；讀不到 → 人工覆核）。"""
    pf = ctx.ts.f("ki_type")
    check = kit.doc_check(ctx, "doc.ki_consistency", "ki_type", name, pf)
    return check.compare(None, None, ok=True, message="無 KI")


def bonus_mentions(ctx: IssuerContext, mentions: Sequence[Mention], fail_message: str) -> list[CheckResult]:
    """紅利的其他出處，每處一筆：數值 = 正式的紅利（`coupon_pa_pct`）。"""
    return super_bull.exit_bonus_mentions(ctx, mentions, fail_message, rid="doc.bonus", label="紅利", key="bonus")


def participation_mentions(ctx: IssuerContext, mentions: Sequence[Mention], fail_message: str) -> list[CheckResult]:
    """參與率的其他出處，每處一筆：數值 = 正式的參與率（`participation_pct`；和參考條件表比是共用的
    `field.participation_pct`）。"""
    return super_bull.exit_bonus_mentions(
        ctx,
        mentions,
        fail_message,
        rid="doc.participation",
        label="參與率",
        key="participation",
        main_field="participation_pct",
    )
