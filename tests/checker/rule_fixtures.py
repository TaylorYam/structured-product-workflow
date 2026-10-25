"""直接打規則函式的輸入：不經 PDF 與 Excel，以 dict 建讀出結果、參考條件表列與規則的 Context。

`term_sheet(**欄位)`：值是 `ParsedField` 就照用，否則包成讀到的欄位；沒給的欄位是「上手沒交出」（缺漏）。
"""

from __future__ import annotations

from typing import Any

from fcn_checker.orders.reference import OrderRecord
from fcn_checker.parsers.layout import TextIndex
from fcn_checker.rules.kit import Context, IssuerContext
from fcn_checker.schema import DocKind, Line, OrderValue, ParsedField
from fcn_checker.standard_fields import lookup
from harness import CONFIG

SHEET_SOURCE = "參考條件表"


class TermSheetOf:
    """只交出指定欄位的讀出結果；全文索引為空。"""

    def __init__(self, fields: dict[str, ParsedField]):
        self.fields = fields
        self.full_text = TextIndex([])

    def f(self, name: str) -> ParsedField:
        return lookup(self.fields, name)


def term_sheet(**values: Any) -> TermSheetOf:
    return TermSheetOf(
        {name: v if isinstance(v, ParsedField) else ParsedField.present(name, v, []) for name, v in values.items()}
    )


def with_text(ts: TermSheetOf, *texts: str) -> TermSheetOf:
    """全文索引換成這幾行：第 n 行在第 n 頁（證據的頁碼即行序）。"""
    ts.full_text = TextIndex([Line(page, 0, 0, 100, 10, text) for page, text in enumerate(texts, 1)])
    return ts


def context(ts: TermSheetOf, issuer: str = "BARC", *, document: DocKind = DocKind.TERM_SHEET, **row: Any) -> Context:
    """共用規則的輸入：參考條件表的列以標準欄位值給（`row`），沒給的欄位是空白格。"""
    record = OrderRecord.offline(CONFIG.reference_format, {"product_code": "029199990001", **row})
    return Context(ts, record, CONFIG.review_standard, CONFIG.reference_format, issuer, document=document)


def issuer_context(ts: TermSheetOf, issuer: str = "BARC", **declared: Any) -> IssuerContext:
    """上手說明書內部規則的輸入：`declared` 為宣告過的參考條件表欄位 → 值。"""
    values = {key: OrderValue(v, f"{SHEET_SOURCE}!?4", key) for key, v in declared.items()}
    return IssuerContext(ts, CONFIG.review_standard, issuer, values, SHEET_SOURCE, CONFIG.exchange_calendar)
