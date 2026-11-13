"""BARC DACN 投資人須知專屬規則（docs/checker/rules/barc-dacn-check-rules.md §3.8）：價格表配息門檻欄的欄頭 % = 同商品說明書
配息門檻、各列配息門檻價格 = 最初價格 × %。名稱、月配息率、價格、KO／KI 等是各上手共用的投資人須知規則。
"""

from __future__ import annotations

from ..schema import CheckResult, Item
from . import dacn
from .iis import NO_TERM_SHEET, IisIssuerContext, ts_unavailable
from .kit import Check


def coupon_barrier(ctx: IisIssuerContext) -> list[CheckResult]:
    """價格表配息門檻欄：欄頭 % = 說明書配息門檻；每列 = 最初價格 × %（half-up 4 位）。"""
    rid = "iis.coupon_barrier"
    pct, rows = ctx.sheet.f("coupon_barrier_pct"), ctx.sheet.f("underlying_prices")
    name = "價格表配息門檻 %"
    out = []
    check = Check(rid, "coupon_barrier_pct", Item.term_sheet(name), ctx.document).needs(pct)
    ts = ctx.term_sheet.f("coupon_barrier_pct") if ctx.term_sheet is not None else None
    if (problem := check.blocked) is not None:
        out.append(problem)
    elif ts is None or not ts.ok:
        why = NO_TERM_SHEET if ts is None else "同商品說明書讀不到配息門檻，無法比對"
        out.append(ts_unavailable(rid, "coupon_barrier_pct", name, why, pct.value, document=ctx.document))
    else:
        out.append(check.compare(ts.value, pct.value, message="須等於說明書第 14 條配息門檻 %"))
    if not pct.ok:
        return out
    whole = Check(rid, "coupon_barrier_prices", Item.expected("價格表配息門檻價格"), ctx.document).needs(rows)
    if (problem := whole.blocked) is not None:
        return [*out, problem]
    for i, row in enumerate(rows.value, start=1):
        cell = Check(rid, f"UL_{i} 配息門檻價格", Item.expected(f"UL_{i} 配息門檻價格"), ctx.document)
        out.append(dacn.barrier_price(cell, row, pct, f"價格表第 {i} 列讀不到配息門檻價格", "最初價格"))
    return out


def run_all(ctx: IisIssuerContext) -> list[CheckResult]:
    return coupon_barrier(ctx)
