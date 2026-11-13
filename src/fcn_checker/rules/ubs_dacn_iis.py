"""UBS DACN 投資人須知專屬規則（docs/checker/rules/ubs-dacn-check-rules.md §3.9）：發行分行要是倫敦分行（同 UBS FCN 投資人
須知，rules/ubs_iis.py）；收益分配事項「本商品第一個月支付固定配息」推得的保證期數 = 同商品說明書配息表的保證期數
（`dacn.iis_guaranteed_periods`）。名稱（= 說明書；名稱樣板只核說明書）、幣別、標的、日期、機構、費率等是各上手共用的
投資人須知規則；投資人須知沒有價格表與配息率，配息門檻只核說明書。
"""

from __future__ import annotations

from ..schema import CheckResult
from . import dacn, ubs_iis
from .iis import IisIssuerContext


def run_all(ctx: IisIssuerContext) -> list[CheckResult]:
    return [*ubs_iis.run_all(ctx), dacn.iis_guaranteed_periods(ctx)]
