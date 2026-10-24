"""Excel 格子（或畫面打的字）的解讀（#207）：每日匯入、人工維護的交易明細、收盤價共用，純 function、不碰資料庫。

規則取自每日匯入（#87、#116），一字不改：
- `text`：去頭尾空白；`None`、空字串、`-`（`dash_is_none=False` 時 `-` 照留）是 None；整數的 float 去掉 `.0`。
- `tdcc`：12 碼以內的數字，補前導零到 12 碼；不是時丟 `ValueError`。`tdcc_or_raw`：認得的補零，不認得的照原字。
- `decimal`：空白是 None；千分位逗號去掉；`bool`、不是數字、NaN、Infinity 丟 `ValueError`。
  `whole_number` 只收整數；`pct` 取到小數 6 位、`amount` 取到 4 位（`Decimal.quantize`，預設的四捨六入五成雙）。
- `date`：`datetime`、`date` 或 `2026/10/15`、`2026-10-15` 的字；不是時丟 `ValueError`。
- `bbg_code`：去掉彭博代碼最後的類別字（`Equity`、`Curncy`、`Index`、`Comdty`），其餘一字不差。

錯誤訊息是給作業人員看的中文，不含欄名（呼叫端補）。其他 module 讀格子的規則和這裡不同的（起單建檔的
`order_terms.parse_field`、收盤價只收正數的 `close_prices._price` 等）留在原處，各自註明差在哪裡。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
from typing import Any

TDCC_DIGITS = 12
CLASS_WORDS = frozenset(("EQUITY", "CURNCY", "INDEX", "COMDTY"))  # 彭博代碼最後的類別字
_PCT = Decimal("0.000001")
_AMOUNT = Decimal("0.0001")


def text(value: Any, *, dash_is_none: bool = True) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    found = str(value).strip()
    if not found or (dash_is_none and found == "-"):
        return None
    return found


def tdcc(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    found = text(value)
    if found is None or not found.isdigit() or len(found) > TDCC_DIGITS:
        raise ValueError(f"TDCC「{found or '空白'}」不是 12 碼以內的數字")
    return found.zfill(TDCC_DIGITS)


def tdcc_or_raw(value: Any) -> str:
    try:
        return tdcc(value)
    except ValueError:
        return text(value) or "（空白）"


def decimal(value: Any) -> Decimal | None:
    if text(value) is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"「{value}」不是數字")
    try:
        number = Decimal(str(value).strip().replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"「{value}」不是數字") from exc
    if not number.is_finite():  # NaN、Infinity 算價格時會中斷
        raise ValueError(f"「{value}」不是數字")
    return number


def whole_number(value: Any) -> int | None:
    number = decimal(value)
    if number is not None and number != number.to_integral_value():
        raise ValueError(f"「{number}」不是整數")
    return None if number is None else int(number)


def pct(value: Any) -> Decimal | None:
    number = decimal(value)
    return None if number is None else number.quantize(_PCT)


def amount(value: Any) -> Decimal | None:
    number = decimal(value)
    return None if number is None else number.quantize(_AMOUNT)


def date(value: Any) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    found = text(value)
    if found is None:
        return None
    for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(found, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"「{found}」不是日期")


def bbg_code(raw: str) -> str:
    """去掉彭博代碼最後的類別字（例：`AA UN Equity` → `AA UN`），其餘一字不差；只去頭尾空白。"""
    code = raw.strip()
    head, _, last = code.rpartition(" ")
    return head.rstrip() if head.strip() and last.upper() in CLASS_WORDS else code
