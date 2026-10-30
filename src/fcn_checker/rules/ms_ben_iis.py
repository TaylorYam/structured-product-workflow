"""MS BEN 投資人須知專屬規則（docs/checker/rules/ms-ben-check-rules.md §3.7）：沒有定期配息、紅利與參與率 = 說明書、到期公式
= 說明書（型態與 lookback 與否），另照 MS FCN 核對開始受理贖回日期與商品種類。只用投資人須知與同商品說明書的讀出結果，不碰
參考條件表；其他檢查點是各上手共用的投資人須知規則。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..parsers import ms_ben
from ..schema import CheckResult, Item
from . import super_bull
from .iis import NO_TERM_SHEET, IisIssuerContext, ts_unavailable
from .kit import Check
from .ms_iis import product_type, redemption_start


def _formula(value: tuple[str, bool]) -> str:
    kind, lookback = value
    return ms_ben.KIND_LABEL.get(kind, str(kind)) + ("（lookback）" if lookback else "")


def _vs_term_sheet(
    ctx: IisIssuerContext,
    rid: str,
    field: str,
    name: str,
    ts_fields: tuple[str, ...],
    shown: Callable[[Any], Any] = lambda v: v,
    fail_message: str = "",
) -> CheckResult:
    """投資人須知的 `field` = 同商品說明書 `ts_fields` 的值（多個時合成 tuple）；`shown` 把兩邊轉成結果上的寫法。"""
    pf = ctx.sheet.f(field)
    check = Check(rid, field, Item.term_sheet(name), ctx.document).needs(pf)
    if (problem := check.blocked) is not None:
        return problem
    if ctx.term_sheet is None:
        return ts_unavailable(rid, field, name, NO_TERM_SHEET, shown(pf.value), document=ctx.document)
    found = [ctx.term_sheet.f(n) for n in ts_fields]
    if not all(x.ok for x in found):
        why = f"同商品說明書讀不到「{name}」，無法比對"
        return ts_unavailable(rid, field, name, why, shown(pf.value), document=ctx.document)
    expected = found[0].value if len(found) == 1 else tuple(x.value for x in found)
    return check.compare(shown(expected), shown(pf.value), fail_message=fail_message)


def run_all(ctx: IisIssuerContext) -> list[CheckResult]:
    return [
        redemption_start(ctx),
        product_type(ctx),
        super_bull.fixed("iis.no_coupon", "no_coupon", "收益分配事項開頭", ctx.sheet.f("no_coupon"), ctx.document),
        _vs_term_sheet(ctx, "doc.bonus", "bonus", "收益分配事項紅利配息率", ("coupon_pa_pct",)),
        _vs_term_sheet(ctx, "doc.participation", "participation", "收益分配事項參與率", ("participation_pct",)),
        # 到期公式：投資人須知 (a)(b) 的型態與 lookback 與否 = 說明書（說明書的 `ben_kind`、`lookback`）
        _vs_term_sheet(
            ctx,
            "doc.redemption_formula",
            "maturity_formula",
            "贖回價金之計算 1) 到期公式",
            ("ben_kind", "lookback"),
            _formula,
            "投資人須知的到期公式須和說明書 §15(1) 同一型態、同樣有沒有 lookback",
        ),
    ]
