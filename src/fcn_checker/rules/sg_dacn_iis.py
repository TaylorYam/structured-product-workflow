"""SG DACN 投資人須知專屬規則（docs/checker/rules/sg-dacn-check-rules.md §3.9）：年配息率、保證期數與計息範圍 = 同商品說明書
第 8 項；有無自動提前出場與 D 型觀察起日照 SG FCN（rules/sg_iis.py）。年配息率 = 參考條件表年利率（`field.coupon_pa_pct`）、
配息率 = 年利率 ÷ 12（`iis.monthly_coupon`）是共用規則。
"""

from __future__ import annotations

from typing import Any

from ..investor_sheet import read_iis
from ..schema import CheckResult, Item
from . import sg_iis
from .iis import NO_TERM_SHEET, IisIssuerContext, ts_unavailable
from .kit import Check


def _vs_term_sheet(ctx: IisIssuerContext, rid: str, field: str, name: str, show=lambda v: v) -> CheckResult:
    """投資人須知欄位 `field` = 同商品說明書同名欄位（`show` 轉成顯示值）；說明書讀不到時轉人工覆核。"""
    pf = read_iis(ctx.sheet, field)
    check = Check(rid, field, Item.term_sheet(name), ctx.document).needs(pf)
    if (problem := check.blocked) is not None:
        return problem
    if ctx.term_sheet is None:
        return ts_unavailable(rid, field, name, NO_TERM_SHEET, show(pf.value), document=ctx.document)
    src = ctx.term_sheet.f(field)
    if not src.ok:
        why = f"同商品說明書讀不到「{name}」，無法比對"
        return ts_unavailable(rid, field, name, why, show(pf.value), document=ctx.document)
    return check.compare(show(src.value), show(pf.value), message="須等於說明書第 8 項")


def _ranges(value: Any) -> str:
    """計息範圍（配息公式第二行、n(i)、N(i)）的顯示值；出處名稱不比。"""
    return "、".join(f"{a}-{b}" for _, a, b in value)


def run_all(ctx: IisIssuerContext) -> list[CheckResult]:
    return [
        _vs_term_sheet(ctx, "iis.coupon_pa_term_sheet", "coupon_pa_pct", "年配息率 %"),
        _vs_term_sheet(ctx, "iis.guaranteed_periods", "guaranteed_periods", "配息公式保證期數"),
        _vs_term_sheet(ctx, "iis.guaranteed_periods", "accrual_ranges", "區間累積配息期別範圍", _ranges),
        *sg_iis.run_all(ctx),
    ]
