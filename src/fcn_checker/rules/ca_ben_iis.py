"""CA BEN 投資人須知專屬規則（docs/checker/rules/ca-ben-check-rules.md §3.6）：有無 KI（同 CA FCN）、紅利與參與率、沒有配息、
到期公式。只用投資人須知與同商品 BEN 說明書的讀出結果，不碰參考條件表；其他檢查點是各上手共用的投資人須知規則。
"""

from __future__ import annotations

from ..schema import CheckResult, Item
from . import super_bull
from .ca_iis import has_ki
from .iis import IisIssuerContext
from .kit import Check


def bonus(ctx: IisIssuerContext) -> list[CheckResult]:
    """二、2.「參與率：x%。紅利率：y%。」的 x、y = 說明書第 9 項的參與率、紅利率（比數值）。"""
    return [
        super_bull.iis_exit_bonus(
            ctx, "須等於說明書第 9 項的紅利率", rid="iis.bonus", name="紅利", field="bonus", ts_field="coupon_pa_pct"
        ),
        super_bull.iis_exit_bonus(
            ctx,
            "須等於說明書第 9 項的參與率",
            rid="iis.participation",
            name="參與率",
            field="participation",
            ts_field="participation_pct",
        ),
    ]


def no_coupon(ctx: IisIssuerContext) -> list[CheckResult]:
    """二、1.(15) 配息日與二、2. 開頭「本商品主要給付項目為「到期贖回金額」，持有期間並無配息金額。」：找不到 → 轉人工覆核。"""
    return [
        Check("iis.no_coupon", field, Item.expected(name), ctx.document)
        .needs(ctx.sheet.f(field))
        .compare(None, None, ok=True, message="本商品持有期間並無配息金額")
        for field, name in (("no_coupon_summary", "二、1.(15) 配息日"), ("no_coupon_payments", "二、2. 收益分配事項"))
    ]


def redemption_formula(ctx: IisIssuerContext) -> list[CheckResult]:
    """二、2. 到期（1）交股句、（2）領回句與 Bonus 公式：固定寫法（同說明書第 9 項），找不到 → 不一致。"""
    return [
        super_bull.fixed("iis.redemption_formula", field, name, ctx.sheet.f(field), ctx.document)
        for field, name in (
            ("delivery_formula", "二、2. 到期（1）小於執行價交股"),
            ("maturity_formula", "二、2. 到期（2）與 Bonus 公式"),
        )
    ]


def run_all(ctx: IisIssuerContext) -> list[CheckResult]:
    return [has_ki(ctx), *bonus(ctx), *no_coupon(ctx), *redemption_formula(ctx)]
