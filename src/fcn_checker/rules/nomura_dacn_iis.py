"""Nomura DACN 投資人須知專屬規則（docs/checker/rules/nomura-dacn-check-rules.md §3.9）：年化配息率 = 同商品說明書第 7 項；
D 型 KO 起日、標的英文名稱、受理贖回日期文字照 Nomura FCN（rules/nomura_iis.py）。年化配息率 = 參考條件表年利率是共用的
`field.coupon_pa_pct`；DACN 沒有每期配息率。
"""

from __future__ import annotations

from ..schema import CheckResult
from . import nomura_iis
from .iis import IisIssuerContext


def coupon_pa(ctx: IisIssuerContext) -> CheckResult:
    """收益分配事項年化配息率 = 說明書第 7 項年化配息率。"""
    return nomura_iis.vs_term_sheet(
        ctx,
        "iis.coupon_pa_term_sheet",
        "coupon_pa_pct",
        "年化配息率 %",
        lambda v: v,
        "coupon_pa_pct",
    )


def run_all(ctx: IisIssuerContext) -> list[CheckResult]:
    return [
        coupon_pa(ctx),
        *nomura_iis.ko_start(ctx),
        nomura_iis.underlying_names(ctx),
        *nomura_iis.redemption_dates(ctx),
    ]
