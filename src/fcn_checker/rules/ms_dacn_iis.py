"""MS DACN 投資人須知專屬規則（docs/checker/rules/ms-dacn-check-rules.md §3.8）：開始受理贖回日期、商品種類同 MS FCN 投資人
須知（rules/ms_iis.py）；收益分配事項「每單位配息金額(1)」推得的保證期數 = 同商品說明書第 15 項 (2) 的保證期數
（`dacn.iis_guaranteed_periods`）。名稱（= 說明書）、月配息率、幣別、標的、日期、機構、費率等是各上手共用的投資人須知規則；
投資人須知沒有價格與 %，配息門檻只核說明書。
"""

from __future__ import annotations

from ..schema import CheckResult
from . import dacn, ms_iis
from .iis import IisIssuerContext


def run_all(ctx: IisIssuerContext) -> list[CheckResult]:
    return [*ms_iis.run_all(ctx), dacn.iis_guaranteed_periods(ctx)]
