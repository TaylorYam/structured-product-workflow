"""BNP Super Bull 投資人須知專屬規則（docs/checker/rules/bnp-super-bull-check-rules.md §3.8）：出場紅利、評價日間隔、
沒有配息。只用投資人須知與同商品 Super Bull 說明書的讀出結果，不碰參考條件表；其他檢查點是各上手共用的投資人須知規則。
"""

from __future__ import annotations

from ..schema import CheckResult, Item
from ..schema import CheckStatus as S
from .iis import NO_TERM_SHEET, IisIssuerContext, ts_unavailable
from .kit import Check


def exit_bonus(ctx: IisIssuerContext) -> CheckResult:
    """收益分配事項 `{100% + X%}` 的 X = 說明書第 16 項出場紅利。"""
    rid, name = "iis.exit_bonus", "出場紅利"
    pf = ctx.sheet.f("exit_bonus")
    check = Check(rid, "exit_bonus", Item.term_sheet(name), ctx.document).needs(pf)
    if (problem := check.blocked) is not None:
        return problem
    if ctx.term_sheet is None:
        return ts_unavailable(rid, "exit_bonus", name, NO_TERM_SHEET, pf.value, document=ctx.document)
    expected = ctx.term_sheet.f("coupon_pa_pct")
    if not expected.ok:
        return ts_unavailable(
            rid, "exit_bonus", name, "同商品說明書讀不到出場紅利，無法比對", pf.value, document=ctx.document
        )
    return check.compare(expected.value, pf.value, fail_message="須等於說明書第 16 項出場紅利")


def observation_interval(ctx: IisIssuerContext) -> CheckResult:
    """「每N 個月一次」的 N = 說明書日期表的每個間隔（發行日 → 第 1 個評價日、相鄰評價日）；不符轉人工覆核（#309 決策）。"""
    rid, name = "iis.observation_interval", "評價日間隔"
    pf = ctx.sheet.f("observation_interval")
    check = Check(rid, "observation_interval", Item.term_sheet(name), ctx.document).needs(pf)
    if (problem := check.blocked) is not None:
        return problem
    if ctx.term_sheet is None:
        return ts_unavailable(rid, "observation_interval", name, NO_TERM_SHEET, pf.value, document=ctx.document)
    months = ctx.term_sheet.f("observation_months")
    if not months.ok:
        why = "同商品說明書讀不到評價日月數，無法比對"
        return ts_unavailable(rid, "observation_interval", name, why, pf.value, document=ctx.document)
    gaps = [b - a for a, b in zip((0, *months.value), months.value, strict=False)]
    return check.compare(
        pf.value,
        pf.value,
        ok=all(g == pf.value for g in gaps),
        fail=S.REVIEW_REQUIRED,
        reason="document_inconsistent",
        message=f"說明書評價日在第 {'、'.join(map(str, months.value))} 個月",
        fail_message=f"，間隔不全是 {pf.value} 個月；請人工核對",
    )


def no_coupon(ctx: IisIssuerContext) -> CheckResult:
    pf = ctx.sheet.f("no_coupon")
    return (
        Check("iis.no_coupon", "no_coupon", Item.expected("沒有配息"), ctx.document)
        .needs(pf)
        .compare(None, None, ok=True, message="● 配息：不適用")
    )


def run_all(ctx: IisIssuerContext) -> list[CheckResult]:
    return [exit_bonus(ctx), observation_interval(ctx), no_coupon(ctx)]
