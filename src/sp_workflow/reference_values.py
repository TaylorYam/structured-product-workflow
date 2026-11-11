"""參考條件表一列的值 ⇄ 資料庫的欄（#498）：兩個方向的值對映只寫在這裡。

- 資料庫 → 表：`sheet_values`（核對讀資料庫，`check_source._row` 再加上評等、風險等級、打勾欄與 UF、分公司欄）。
- 表 → 資料庫：`cell_value`（空白與空值寫法都是 None）、`sheet_prices`／`sheet_underlying`（一檔標的的四個價格）、
  `sheet_dates`（比價日）、`ko_type`／`barrier_type`（KO(Freq)＋KO(memo)、KI(Freq) 的代碼 → 型態）、`upfront_field`
  （UF → 畫面的 Upfront / NotePrice）。舊商品補資料（`legacy_backfill`）、回補 FCN 以外商品（`product_backfill`）用。

代碼表的來源仍是 `product_record.KO_CODES`／`KI_CODES`，UF 的換算是 `product_record.invert_upfront`；KI(Freq) 的
「-」（空值寫法）就是沒有 KI（Barrier Type NONE）。表的排法、`OrderRecord`、`Table` 不在這裡（ADR 0013、#445）。
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from fcn_checker.backfill_values import LOOKBACK_END_DATE, SLOTS
from fcn_checker.fields import PRICE_COLUMNS, PRICE_FIELDS, PriceField
from fcn_checker.orders.reference import UNDERLYING_SLOTS, autocall_date_key, price_key, underlying_key
from sp_workflow.product_record import KI_CODES, KO_CODES, NO_BARRIER, UnderlyingPrice, invert_upfront

if TYPE_CHECKING:
    from fcn_checker.config import ReferenceFormat
    from fcn_checker.orders.reference import OrderRecord
    from sp_workflow.product_store import Stored

NO_KI = NO_BARRIER  # Barrier Type：沒有 KI
PARTICIPATION_KEY = (
    "participation_pct"  # 參考條件表的標準欄位（不是畫面的欄名）：參與率（#373；只有 BEN 有，不在匯入格式裡）
)
COUPON_BARRIER_KEY = "coupon_barrier_pct"  # 參考條件表的配息門檻（#367；不在匯入格式裡）
KO_STEP_KEY = "ko_step_pct"  # 參考條件表的 KO 降幅（#537；只有 Stepdown、Snowball 有，不在匯入格式裡）
LOOKBACK_KEY = LOOKBACK_END_DATE.field  # 參考條件表的回顧結束日（#376；回填欄位，不在匯入格式裡）

_KO_TYPES = {codes: name for name, codes in KO_CODES.items()}  # (KO(Freq), KO(memo)) → KO Type
_BARRIER_TYPES = {code: name for name, code in KI_CODES.items()}  # KI(Freq) → Barrier Type


# ---------- 表 → 資料庫 ----------


def cell_value(record: OrderRecord, key: str, fmt: ReferenceFormat) -> Any:
    """一格的值；空白與空值寫法都是 None。"""
    found = record.fields.get(key)
    value = None if found is None else found.value
    return None if value is None or value == fmt.empty_value else value


def ko_type(observation: object, memory: object) -> str | None:
    """KO(Freq)、KO(memo) 的代碼 → KO Type（例：D、Y → Daily Memory）；看不懂時 None。"""
    return _KO_TYPES.get((observation, memory))  # type: ignore[arg-type]


def barrier_type(code: object, empty: str) -> str | None:
    """KI(Freq) 的代碼 → Barrier Type；空白與空值寫法 `empty`（「-」）是沒有 KI。目前不支援的代碼（例：每期觀察）None。"""
    return _BARRIER_TYPES.get(code or empty)  # type: ignore[arg-type]


def upfront_field(uf: object, empty: str) -> object:
    """表上的 UF（百分點）→ 畫面的 Upfront / NotePrice（100 − UF）；空白是 None，不是數字的照原樣留著（由檢查標出來）。"""
    if uf in (None, empty):
        return None
    return invert_upfront(uf) if isinstance(uf, Decimal) else uf


def sheet_prices(record: OrderRecord, seq: int, fmt: ReferenceFormat) -> dict[PriceField, Any]:
    """表上 UL_n 的四個價格（`cell_value`），依 `PRICE_FIELDS` 的順序。"""
    return {pf: cell_value(record, price_key(seq, pf.key), fmt) for pf in PRICE_FIELDS}


def sheet_underlying(
    record: OrderRecord, seq: int, code: str, fmt: ReferenceFormat, ignored: frozenset[str] = frozenset()
) -> UnderlyingPrice:
    """表上 UL_n 的價格 → 資料庫的一列標的價格（代碼用資料庫的寫法 `code`）；`ignored` 的價格（鍵）不讀，空白。"""
    prices = sheet_prices(record, seq, fmt)
    return UnderlyingPrice(seq, code, **{pf.key: None if pf.key in ignored else v for pf, v in prices.items()})


def sheet_dates(record: OrderRecord, fmt: ReferenceFormat) -> dict[int, Any]:
    """表上的比價日_1～比價日_12（`cell_value`）。"""
    return {n: cell_value(record, autocall_date_key(n), fmt) for n in range(1, SLOTS + 1)}


# ---------- 資料庫 → 表 ----------


def sheet_values(found: Stored, empty: str) -> dict[str, Any]:
    """資料庫裡的這檔 → {標準欄位: 值}：商品條件、比價日、標的與四個價格。空值寫法 `empty` 用在 KI NONE、BEN 預設沒有
    KO（#406）、沒有的標的與不需要填的比價日；執行價是核對比的原始執行價（lookback 寫過回顧期初價格時是期初的，#374）。
    VWAP 還沒回填的價格是 None。評等、風險等級、打勾欄、UF、分公司欄由 `check_source._row` 加。"""
    p = found.columns
    no_ki = p["barrier_type"] == NO_KI
    no_ko = p["ko_pct"] is None  # BEN 預設沒有 KO（#406）
    prices = {price.seq: price for price in found.prices}
    values: dict[str, Any] = {
        "product_code": found.tdcc,
        "product": p["product"],
        "isin": found.isin,
        "denomination": p["denomination"],
        "currency": p["currency"],
        "trade_date": p["trade_date"],
        "issue_date": p["issue_date"],
        "final_valuation_date": p["final_valuation_date"],
        "maturity_date": p["maturity_date"],
        "initial_pricing": p["initial_pricing"],
        "ko_pct": empty if no_ko else p["ko_pct"],
        "ko_observation": p["ko_freq"],
        "ko_memory": p["ko_memory"],
        "strike_pct": p["strike_pct"],
        "ki_pct": empty if no_ki else p["ki_pct"],
        "ki_type": p["ki_freq"],
        "coupon_pa_pct": p["coupon_pct"],
        COUPON_BARRIER_KEY: empty if p["coupon_barrier_pct"] is None else p["coupon_barrier_pct"],
        KO_STEP_KEY: empty if p["ko_step_pct"] is None else p["ko_step_pct"],
        "tenor_months": p["tenor_months"],
        "first_callable_period": p["non_call_months"],
        PARTICIPATION_KEY: p["participation_pct"],
        LOOKBACK_KEY: p["lookback_end_date"],
    }
    if found.autocall is not None:
        for slot in range(1, SLOTS + 1):
            values[autocall_date_key(slot)] = found.autocall.get(slot) or empty
    for i in range(1, UNDERLYING_SLOTS + 1):
        code = p[f"underlying_{i}"]
        values[underlying_key(i)] = code or empty
        price = prices.get(i)
        for col in PRICE_COLUMNS:
            key = price_key(i, col)
            if not code or (col == "ki" and no_ki) or (col == "ko" and no_ko):
                values[key] = empty
            elif col == "strike":
                values[key] = found.original_strike(i)
            else:
                values[key] = None if price is None else getattr(price, col)
    return values
