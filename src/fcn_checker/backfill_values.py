"""回填值（#444）：核對交給寫回端的型別化值，以標準欄位定址，不看 Excel 欄名或格子。

核對（`single_check`）建回填決策（`backfill.CellDecision`）時一次建好：每個決策帶一個目標（`Target`），
寫進「回填後」的決策（回填與 VWAP 覆寫）由 `backfill.values` 變成回填值（`BackfillValue`），放進
`CheckReport.backfill_values`。
- 資料庫 adapter（`sp_workflow.check_source.write_back` → `product_terms.backfill`）照目標的型別寫表，
  不必從欄名翻回標準欄位、也不必拆 `autocall_date_<n>` 這類字串。
- Excel adapter（`saving`／`result_file`）照決策的格子（`CellDecision.cell`）排版核對結果檔。

「哪些是回填欄位」只在這裡宣告（`ROW_FIELDS` 加上期初定價 VWAP 時的價格欄）；TS、IIS 打勾不是回填值
（資料庫那邊改成鎖住，Excel 那邊照決策打勾）。人工放行時的單位面額（ADR 0008）也由這裡從核對結果交出。
這個 module 不 import openpyxl。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .fields import PRICE_COLUMNS
from .schema import CheckReport, CheckStatus

DENOMINATION_RULE = "field.denomination"
DENOMINATION_LABEL = "單位面額"  # 回填紀錄的項目名稱（參考條件表欄名）


@dataclass(frozen=True)
class ProductTerm:
    """`workflow.product` 的一欄：ISIN、發行日、回顧結束日；人工放行時的單位面額。"""

    column: str

    @property
    def field(self) -> str:
        return self.column


@dataclass(frozen=True)
class AutocallSlot:
    """第 `slot` 期比價日（比價日_1～12，`workflow.autocall_date`）。"""

    slot: int

    @property
    def field(self) -> str:
        return f"autocall_date_{self.slot}"


@dataclass(frozen=True)
class UnderlyingPrice:
    """第 `seq` 檔標的的一個價格（`fields.PRICE_FIELDS` 的 key：initial／strike／ki／ko；`workflow.underlying_price`）。"""

    seq: int
    key: str

    def __post_init__(self) -> None:
        if self.key not in PRICE_COLUMNS:  # 欄名會放進 SQL：只接受四個價格
            raise ValueError(f"不認得的價格：{self.key}")

    @property
    def column(self) -> str:
        """`workflow.underlying_price` 的欄。"""
        return PRICE_COLUMNS[self.key]

    @property
    def field(self) -> str:
        return f"underlying_{self.seq}_{self.column}"


@dataclass(frozen=True)
class CheckedMark:
    """TS 或 IIS 打勾（`checked_term_sheet`、`checked_iis`）：Excel 打勾、資料庫改成鎖住，不是回填值。"""

    column: str

    @property
    def field(self) -> str:
        return self.column


Target = ProductTerm | AutocallSlot | UnderlyingPrice | CheckedMark

SLOTS = 12
ISIN = ProductTerm("isin")
ISSUE_DATE = ProductTerm("issue_date")
LOOKBACK_END_DATE = ProductTerm("lookback_end_date")
DENOMINATION = ProductTerm("denomination")
CHECKED_MARKS = (CheckedMark("checked_term_sheet"), CheckedMark("checked_iis"))  # TS、IIS 欄
AUTOCALL_SLOTS = tuple(AutocallSlot(n) for n in range(1, SLOTS + 1))
# 每檔商品都有的回填欄位（寫回前比對條件時不算）；期初定價 VWAP 時各標的價格也回填（沒回填前兩邊都空白，照比）
ROW_FIELDS: tuple[Target, ...] = (*CHECKED_MARKS, ISIN, ISSUE_DATE, *AUTOCALL_SLOTS, LOOKBACK_END_DATE)


@dataclass(frozen=True)
class BackfillValue:
    """一個回填值：寫到哪（`target`）、值（空值寫法已轉成 None）、回填紀錄的項目名稱（參考條件表欄名，給人看）。"""

    target: Target
    value: Any
    label: str


def released_denomination(report: CheckReport) -> BackfillValue | None:
    """人工放行的說明書：單位面額和條件不同時寫入 TS 的值（ADR 0008）；說明書交出的值不只一個或不是數字時沒有。"""
    found = {
        r.actual
        for r in report.results
        if r.rule_id == DENOMINATION_RULE and r.status != CheckStatus.PASS and r.actual is not None
    }
    if len(found) != 1:
        return None
    (value,) = found
    try:
        return BackfillValue(DENOMINATION, Decimal(str(value)), DENOMINATION_LABEL)
    except ArithmeticError:
        return None
