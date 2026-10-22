"""商品紀錄：`workflow.product` 的欄與畫面欄位的對照（#126）。

讀寫 `workflow.product` 的地方（起單資料讀取 `order_terms`、寫入 `product_write`、人工維護 `order_import`、
核對條件 `check_terms`、核對讀資料庫 `check_source`、寄信草稿 `mail_drafts`）都從這裡拿欄位與轉換，不各自維護。對外：
- `PRODUCT_COLUMNS`：資料庫的商品欄（TDCC、ISIN、來源、鎖住時間這類不是商品條件的欄不在內）。
- `FIELDS`／`KINDS`／`CHOICES`：畫面的欄（依起單建檔的順序）、型別與選項；`REQUIRED`：必填欄。
- `COLUMN_FIELDS`：資料庫的欄 → 畫面的欄；`DERIVED_COLUMNS`：沒有對應畫面欄的資料庫欄 → 說明用的名稱。
- `columns_of`／`fields_of`：畫面的值 ↔ 資料庫的欄（KO／KI 代碼、UF 反轉、單位面額預設）。
- `missing_fields`：資料庫商品條件缺哪幾個必填欄（#114）。

`REQUIRED` 與欄位對照放在這裡、不放在 `order_import` 或 `product_write`：`check_terms` 也要用，
而它被 `product_write` import，放在寫入那邊會循環 import（ADR 0016 當時因此搬到 `order_terms`，#126 再收到這裡）。
這個 module 只 import `daily_import`（商品種類 FCN），不 import 上面那些 module。

畫面上的 Upfront / NotePrice (%) 是價格，資料庫存 UF = 100 − 價格（`invert_upfront`，兩個方向一樣）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

from sp_workflow.daily_import import FCN

UPFRONT_FIELD = "Upfront / NotePrice (%)"
TRADE_FIELD = "Trade date"
STRIKE_FIELD = "Strike date"
PRICING_FIELD = "期初價格"


class FieldKind(Enum):
    TEXT = "文字"
    CHOICE = "選項"
    PERCENT = "百分比"
    INT = "整數"
    DATE = "日期"


KO_TYPES = ("Daily", "Daily Memory", "Period End", "Period End Memory")
BARRIER_TYPES = ("NONE", "EKI", "AKI")
OPEN = "開盤價"
INITIAL_PRICINGS = (OPEN, "收盤價", "VWAP")
UNDERLYINGS = tuple(f"BBG Code {n}" for n in range(1, 6))
CHOICES: dict[str, tuple[str, ...]] = {
    "KO Type": KO_TYPES,
    "Barrier Type": BARRIER_TYPES,
    PRICING_FIELD: INITIAL_PRICINGS,
}
# 畫面上可以改的欄（依起單建檔的順序）→ 型別
KINDS: dict[str, FieldKind] = {
    "Product": FieldKind.TEXT,
    "Currency": FieldKind.TEXT,
    "Guaranteed Periods (m)": FieldKind.INT,
    **dict.fromkeys(UNDERLYINGS, FieldKind.TEXT),
    "Strike (%)": FieldKind.PERCENT,
    "KO Type": FieldKind.CHOICE,
    "KO Barrier (%)": FieldKind.PERCENT,
    "Coupon p.a. (%)": FieldKind.PERCENT,
    UPFRONT_FIELD: FieldKind.PERCENT,
    "Tenor (m)": FieldKind.INT,
    "Barrier Type": FieldKind.CHOICE,
    "KI Barrier (%)": FieldKind.PERCENT,
    TRADE_FIELD: FieldKind.DATE,
    STRIKE_FIELD: FieldKind.DATE,
    "Issue date": FieldKind.DATE,
    "Final valuation date": FieldKind.DATE,
    "Maturity date": FieldKind.DATE,
    PRICING_FIELD: FieldKind.CHOICE,
}
FIELDS = tuple(KINDS)
# 必填欄：起單資料匯入時空白就擋下（#86）。資料庫的商品條件可以先存一部分（#114、ADR 0016），
# 缺這幾欄的商品核對條件停在待確認，不能核對、寄信（`missing_fields`）
REQUIRED = (
    "Product",
    "Currency",
    "Guaranteed Periods (m)",
    "BBG Code 1",
    "Strike (%)",
    "KO Type",
    "KO Barrier (%)",
    "Coupon p.a. (%)",
    UPFRONT_FIELD,
    "Tenor (m)",
    "Barrier Type",
    TRADE_FIELD,
    STRIKE_FIELD,
    "Final valuation date",
    "Maturity date",
    PRICING_FIELD,
)

# 資料庫商品欄位
PRODUCT_COLUMNS = (
    "product", "issuer", "currency", "trade_date", "issue_date", "final_valuation_date", "maturity_date",
    "initial_pricing", "non_call_months", "underlying_1", "underlying_2", "underlying_3", "underlying_4",
    "underlying_5", "strike_pct", "ko_freq", "ko_memory", "ko_type", "ko_pct", "coupon_pct", "upfront_pct",
    "tenor_months", "ki_freq", "barrier_type", "ki_pct", "denomination", "strike_date",
)  # fmt: skip
# 資料庫 workflow.product 的欄 ↔ 畫面的欄（畫面上的 Upfront / NotePrice = 100 − UF）
COLUMN_FIELDS = {
    "product": "Product",
    "currency": "Currency",
    "non_call_months": "Guaranteed Periods (m)",
    **{f"underlying_{n}": name for n, name in enumerate(UNDERLYINGS, 1)},
    "strike_pct": "Strike (%)",
    "ko_type": "KO Type",
    "ko_pct": "KO Barrier (%)",
    "coupon_pct": "Coupon p.a. (%)",
    "upfront_pct": UPFRONT_FIELD,
    "tenor_months": "Tenor (m)",
    "barrier_type": "Barrier Type",
    "ki_pct": "KI Barrier (%)",
    "trade_date": TRADE_FIELD,
    "strike_date": STRIKE_FIELD,
    "issue_date": "Issue date",
    "final_valuation_date": "Final valuation date",
    "maturity_date": "Maturity date",
    "initial_pricing": PRICING_FIELD,
}
# 由 KO Type／Barrier Type 換算的代碼欄、發行機構與面額：沒有對應的畫面欄，說明時用的名稱
DERIVED_COLUMNS = {
    "ko_freq": "KO Type",
    "ko_memory": "KO Type",
    "ki_freq": "Barrier Type",
    "issuer": "上手",
    "denomination": "單位面額",
}
# KO Type → 資料庫的 (ko_freq, ko_memory)；Barrier Type → ki_freq（同清單更新的代碼）
KO_CODES = dict(zip(KO_TYPES, (("D", "N"), ("D", "Y"), ("P", "N"), ("P", "Y")), strict=True))
KI_CODES = dict(zip(BARRIER_TYPES, ("-", "AM", "D"), strict=True))  # 每期觀察 MKI 目前不支援，讀到時標紅

_FIELD_COLUMNS = {name: column for column, name in COLUMN_FIELDS.items()}
# KO Type、Barrier Type 另外存換算的代碼（`KO_CODES`／`KI_CODES`）
_CODE_COLUMNS = {"KO Type": ("ko_freq", "ko_memory"), "Barrier Type": ("ki_freq",)}
# 必填欄 → 資料庫裡存它的欄
_REQUIRED_STORED = {name: (_FIELD_COLUMNS[name], *_CODE_COLUMNS.get(name, ())) for name in REQUIRED}
REQUIRED_COLUMNS = tuple(column for columns in _REQUIRED_STORED.values() for column in columns)

_PCT = Decimal("0.000001")
_AMOUNT = Decimal("0.0001")
_HUNDRED = Decimal(100)


def columns_of(
    values: Mapping[str, Any], issuer: str | None, denominations: Mapping[str, Decimal]
) -> dict[str, object]:
    """畫面的值 → 資料庫商品欄位（依 `PRODUCT_COLUMNS` 的順序）。

    `issuer` 是發行機構的寫法；`denominations` 是幣別 → 單位面額預設值。空白的欄（人工維護，#115）寫空白，
    換算的代碼與單位面額也是空白；商品種類一律 FCN。
    """
    columns: dict[str, object] = {}
    for column, name in COLUMN_FIELDS.items():
        value = values.get(name)
        if column == "upfront_pct" and value is not None:
            value = invert_upfront(Decimal(value))
        columns[column] = _pct(value) if KINDS[name] is FieldKind.PERCENT else value
    currency = values.get("Currency")
    columns["ko_freq"], columns["ko_memory"] = KO_CODES.get(values.get("KO Type"), (None, None))
    columns.update(
        product=FCN,
        issuer=issuer,
        ki_freq=KI_CODES.get(values.get("Barrier Type")),
        denomination=None if currency is None else Decimal(denominations[currency]).quantize(_AMOUNT),
    )
    return {column: columns[column] for column in PRODUCT_COLUMNS}


def fields_of(columns: Mapping[str, Any]) -> dict[str, object]:
    """資料庫商品欄位 → 畫面的欄（Upfront / NotePrice = 100 − UF；百分比去掉多餘的 0）。"""
    fields: dict[str, object] = {}
    for column, name in COLUMN_FIELDS.items():
        value = columns[column]
        if column == "upfront_pct" and value is not None:
            value = invert_upfront(value)
        fields[name] = plain(value) if isinstance(value, Decimal) else value
    return fields


def invert_upfront(value: Decimal) -> Decimal:
    """UF ↔ Upfront / NotePrice（100 − 值；兩個方向一樣）。"""
    return _HUNDRED - value


def missing_fields(product: Mapping[str, object], names: Sequence[str] = REQUIRED) -> list[str]:
    """資料庫商品條件的一列（`workflow.product` 的欄 → 值）裡空白的必填欄，依 `names` 的順序回傳畫面的欄名。

    換算的代碼欄空白也算那一欄缺（沒有存齊）。`product` 要有 `REQUIRED_COLUMNS`（讀 `PRODUCT_COLUMNS` 就有）。
    """
    return [name for name in names if any(product[column] is None for column in _REQUIRED_STORED[name])]


def plain(value: Decimal) -> Decimal:
    """去掉多餘的 0，但不變成科學記號（例：80.000000 → 80、2.150000 → 2.15）。"""
    if value == value.to_integral_value():
        return value.quantize(Decimal(1))
    return value.normalize()


def _pct(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value).quantize(_PCT)  # type: ignore[arg-type]
    except InvalidOperation as exc:
        raise ValueError(f"「{value}」不是數字") from exc
