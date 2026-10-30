"""Natixis DACN 投資人須知專屬規則（docs/checker/rules/natixis-dacn-check-rules.md §3.9）：配息界限價格 % = 同商品說明書的配息門檻；
KO 定義寫法、KO 每日到最終評價日含不含、受託機構商品代號照 Natixis FCN（rules/natixis_iis.py）。年利率 = 參考條件表年利率
（`field.coupon_pa_pct`）、月配息率 = 年利率 ÷ 12（`iis.monthly_coupon`）是共用規則。
"""

from __future__ import annotations

from ..investor_sheet import read_iis
from ..schema import CheckResult, Item
from . import natixis_iis
from .iis import NO_TERM_SHEET, IisIssuerContext, ts_unavailable
from .kit import Check


def coupon_barrier(ctx: IisIssuerContext) -> CheckResult:
    """貳 2.「配息界限價格」係指…之X% 的 X = 同商品說明書配息門檻（第 9 項欄頭）；說明書讀不到時轉人工覆核。"""
    rid, field, name = "iis.coupon_barrier_term_sheet", "coupon_barrier_pct", "配息界限價格 %"
    pf = read_iis(ctx.sheet, field)
    check = Check(rid, field, Item.term_sheet(name), ctx.document).needs(pf)
    if (problem := check.blocked) is not None:
        return problem
    if ctx.term_sheet is None:
        return ts_unavailable(rid, field, name, NO_TERM_SHEET, pf.value, document=ctx.document)
    src = ctx.term_sheet.f(field)
    if not src.ok:
        why = "同商品說明書讀不到配息門檻（第 9 項「配息界限價格(每日觀察)/執行價格」欄頭 %），無法比對"
        return ts_unavailable(rid, field, name, why, pf.value, document=ctx.document)
    return check.compare(src.value, pf.value, message="須等於說明書第 9 項配息界限價格欄頭 %")


def run_all(ctx: IisIssuerContext) -> list[CheckResult]:
    return [coupon_barrier(ctx), *natixis_iis.run_all(ctx)]
