"""DBS DACN 投資人須知專屬規則（docs/checker/rules/dbs-dacn-check-rules.md §3.8）：商品簡介價格表的區間下限價格表頭 % =
同商品說明書配息門檻、各列區間下限價格 = 期初價格 × %、區間上限價格每列 `不適用`；刊印日期與最大利率值照 DBS FCN
（rules/dbs_iis.py）。名稱、年利率、價格、KO／KI、保證期數等是各上手共用的投資人須知規則。
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP

from ..parsers import dbs_dacn
from ..schema import CheckResult, Item
from ..schema import CheckStatus as S
from . import dbs_iis
from .iis import NO_TERM_SHEET, IisIssuerContext, ts_unavailable
from .kit import Q4, Check


def coupon_barrier(ctx: IisIssuerContext) -> list[CheckResult]:
    """價格表區間下限價格：表頭 % = 說明書配息門檻；每列 = 期初價格 × %（half-up 4 位）；區間上限每列 `不適用`。"""
    rid = "iis.coupon_barrier"
    pct, rows, upper = (ctx.sheet.f(x) for x in ("coupon_barrier_pct", "underlying_prices", "range_upper"))
    out = []
    name = "價格表區間下限價格 %（配息門檻）"
    check = Check(rid, "coupon_barrier_pct", Item.term_sheet(name), ctx.document).needs(pct)
    ts = ctx.term_sheet.f("coupon_barrier_pct") if ctx.term_sheet is not None else None
    if (problem := check.blocked) is not None:
        out.append(problem)
    elif ts is None or not ts.ok:
        why = NO_TERM_SHEET if ts is None else "同商品說明書讀不到配息門檻，無法比對"
        out.append(ts_unavailable(rid, "coupon_barrier_pct", name, why, pct.value, document=ctx.document))
    else:
        out.append(check.compare(ts.value, pct.value, message="須等於說明書表1 區間下限價格的 %"))
    if pct.ok:
        whole = Check(rid, "coupon_barrier_prices", Item.expected("價格表區間下限價格"), ctx.document).needs(rows)
        if (problem := whole.blocked) is not None:
            out.append(problem)
        else:
            for i, row in enumerate(rows.value, start=1):
                actual = row.prices.get("barrier")
                cell = Check(rid, f"UL_{i} 區間下限價格", Item.expected(f"UL_{i} 區間下限價格"), ctx.document)
                if actual is None:
                    out.append(
                        cell.result(
                            S.REVIEW_REQUIRED,
                            reason="document_missing",
                            message=f"價格表第 {i} 列讀不到區間下限價格",
                            evidence=list(row.evidence),
                        )
                    )
                    continue
                initial = row.prices["initial"]
                out.append(
                    cell.compare(
                        (initial * pct.value / 100).quantize(Q4, ROUND_HALF_UP),
                        actual,
                        evidence=[*row.evidence, *pct.evidence],
                        tolerance="四捨五入（half-up）到 4 位",
                        message=f"期初價格 {initial} × {pct.value}%",
                    )
                )
    up = Check(rid, "range_upper", Item.expected("價格表區間上限價格"), ctx.document).needs(upper)
    out.append(
        up.blocked
        or up.compare(
            dbs_dacn.NOT_APPLICABLE,
            "、".join(upper.value),
            ok=all(x == dbs_dacn.NOT_APPLICABLE for x in upper.value),
            fail=S.REVIEW_REQUIRED,
            reason="template_variant_unverified",
            fail_message="區間上限價格適用的 DACN 沒有樣本，待樣本驗證，請人工核對",
        )
    )
    return out


def run_all(ctx: IisIssuerContext) -> list[CheckResult]:
    return [*coupon_barrier(ctx), *dbs_iis.run_all(ctx)]
