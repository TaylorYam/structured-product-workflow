"""支援的商品種類與各自的 Non-Call 規則（#302、#304、ADR 0027）。

流程（`sp_workflow`：交易明細、起單建檔、人工維護、歷史回補）與核對（說明書推得的 Non-Call 符不符合參考條件表的
商品種類，`rules/reference.non_call_product`）共用這一份；新增商品時在 `PRODUCT_RULES` 加一筆，訂它自己的規則。
"""

from __future__ import annotations

from dataclasses import dataclass

FCN = "FCN"  # 商品種類（#201）
FCN_EXPRESS = "FCN_Express"  # 沒有 Non-Call 的每日記憶式 FCN（#302、ADR 0027）


@dataclass(frozen=True)
class ProductRule:
    """一種商品種類的條件規則。"""

    name: str
    has_non_call: bool = True  # False：沒有 Non-Call，Guaranteed Periods (m) 一定是 0（FCN_Express）

    def non_call_problem(self, months: int) -> str:
        """Guaranteed Periods (m) 不符合這種商品時的說明；符合時空字串。"""
        if not self.has_non_call:
            return "" if months == 0 else f"{self.name} 沒有 Non-Call，要填 0"
        return "" if months >= 1 else "至少 1"


# 支援的商品種類（依加入的順序）
PRODUCT_RULES = {rule.name: rule for rule in (ProductRule(FCN), ProductRule(FCN_EXPRESS, has_non_call=False))}
_PRODUCT_NAMES = {name.upper(): name for name in PRODUCT_RULES}
SUPPORTED_PRODUCTS = "、".join(PRODUCT_RULES)  # 錯訊用：目前只做這些商品種類


def full_coupon_periods(non_call: int) -> int:
    """第一個可提前出場期出場時配整期息的期數：Non-Call 期間；沒有 Non-Call（FCN_Express）時第 1 期配整期息，為 1
    （workflow #305、#306）。"""
    return max(non_call, 1)


def product_name(text: object) -> str | None:
    """交易明細、起單建檔、參考條件表的 Product → 支援的商品種類的寫法（不分大小寫、不看頭尾空白）；不支援時 None。"""
    return _PRODUCT_NAMES.get(text.strip().upper()) if isinstance(text, str) else None
