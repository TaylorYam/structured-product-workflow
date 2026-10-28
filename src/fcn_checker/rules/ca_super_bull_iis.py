"""CA Super Bull 投資人須知專屬規則（docs/checker/rules/ca-super-bull-check-rules.md §3.8）：有無 KI（同 CA FCN）、出場紅利、
沒有配息、贖回公式。只用投資人須知與同商品 Super Bull 說明書的讀出結果，不碰參考條件表；其他檢查點是各上手共用的投資人
須知規則。
"""

from __future__ import annotations

from ..schema import CheckResult, Item
from . import super_bull
from .ca_iis import has_ki
from .iis import IisIssuerContext
from .kit import Check


def exit_bonus(ctx: IisIssuerContext) -> CheckResult:
    """二、2.(a)「自動提前出場紅利：x%」（4 位）的 x = 說明書第 9 項觀察日表的出場紅利（比數值）。"""
    return super_bull.iis_exit_bonus(ctx, "須等於說明書第 9 項觀察日表的出場紅利")


def no_coupon(ctx: IisIssuerContext) -> list[CheckResult]:
    """二、1.(15) 與二、2. 開頭的「本商品持有期間並無配息金額。」：找不到 → 轉人工覆核。"""
    return [
        Check("iis.no_coupon", field, Item.expected(name), ctx.document)
        .needs(ctx.sheet.f(field))
        .compare(None, None, ok=True, message="本商品持有期間並無配息金額")
        for field, name in (("no_coupon_summary", "二、1.(15) 配息日"), ("no_coupon_payments", "二、2. 收益分配事項"))
    ]


def redemption_formula(ctx: IisIssuerContext) -> list[CheckResult]:
    """二、2.(a) 自動提前出場贖回金額、到期贖回 (ii) 公式：固定寫法（同說明書第 9 項），找不到 → 不一致。"""
    return [
        super_bull.fixed("iis.redemption_formula", field, name, ctx.sheet.f(field), ctx.document)
        for field, name in (
            ("ko_redemption_formula", "二、2.(a) 自動提前出場贖回金額"),
            ("maturity_formula", "二、2. 到期贖回公式"),
        )
    ]


def run_all(ctx: IisIssuerContext) -> list[CheckResult]:
    return [has_ki(ctx), exit_bonus(ctx), *no_coupon(ctx), *redemption_formula(ctx)]
