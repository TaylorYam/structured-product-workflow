"""BNP DACN 投資人須知專屬規則（docs/checker/rules/bnp-dacn-check-rules.md §3.9）：配息寫法「第1個配息日之配息金額」推得的
保證期數 = 同商品說明書第 15 項（`dacn.iis_guaranteed_periods`）；標的表定界價格列的 % = 說明書配息門檻、每檔價格 = 說明書
第 11 項同檔。月配息率（`其中C為X%`）、名稱、價格、KO／KI 等是各上手共用的投資人須知規則。
"""

from __future__ import annotations

from ..schema import CheckResult, Item
from ..schema import CheckStatus as S
from . import dacn
from .iis import NO_TERM_SHEET, IisIssuerContext, ts_unavailable
from .kit import Check


def coupon_barrier(ctx: IisIssuerContext) -> list[CheckResult]:
    """標的表定界價格列：% = 說明書配息門檻 %；每檔定界價格 = 說明書第 11 項標的表同檔（同一列順序）。"""
    rid = "iis.coupon_barrier"
    pct, rows = ctx.sheet.f("coupon_barrier_pct"), ctx.sheet.f("underlying_prices")
    ts = ctx.term_sheet
    name = "標的表定界價格 %"
    out = []
    check = Check(rid, "coupon_barrier_pct", Item.term_sheet(name), ctx.document).needs(pct)
    ts_pct = ts.f("coupon_barrier_pct") if ts is not None else None
    if (problem := check.blocked) is not None:
        out.append(problem)
    elif ts_pct is None or not ts_pct.ok:
        why = NO_TERM_SHEET if ts_pct is None else "同商品說明書讀不到配息門檻，無法比對"
        out.append(ts_unavailable(rid, "coupon_barrier_pct", name, why, pct.value, document=ctx.document))
    else:
        out.append(check.compare(ts_pct.value, pct.value, message="須等於同商品說明書標的表的定界價格 %"))
    whole = Check(rid, "coupon_barrier_prices", Item.term_sheet("標的表定界價格"), ctx.document).needs(rows)
    if (problem := whole.blocked) is not None:
        return [*out, problem]
    ts_rows = ts.f("underlying_prices") if ts is not None else None
    for i, row in enumerate(rows.value, start=1):
        field = item = f"UL_{i} 定界價格"
        actual = row.prices.get("barrier")
        cell = Check(rid, field, Item.term_sheet(item), ctx.document)
        if actual is None:
            message = f"標的表第 {i} 檔讀不到定界價格"
            out.append(
                cell.result(S.REVIEW_REQUIRED, reason="document_missing", message=message, evidence=list(row.evidence))
            )
            continue
        if ts_rows is None or not ts_rows.ok or i > len(ts_rows.value) or "barrier" not in ts_rows.value[i - 1].prices:
            why = NO_TERM_SHEET if ts_rows is None else "同商品說明書標的表沒有對應的定界價格，無法比對"
            out.append(ts_unavailable(rid, field, item, why, actual, document=ctx.document))
            continue
        out.append(cell.compare(ts_rows.value[i - 1].prices["barrier"], actual, evidence=list(row.evidence)))
    return out


def run_all(ctx: IisIssuerContext) -> list[CheckResult]:
    return [dacn.iis_guaranteed_periods(ctx), *coupon_barrier(ctx)]
