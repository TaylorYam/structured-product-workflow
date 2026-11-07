"""寫標的價格（#492）：`workflow.underlying_price` 一列只由這裡寫。

同一列的欄由三方各自擁有，照舊（ADR 0012、0025、0029）：
- 四個價格與代碼：核對條件重算（`check_terms`，整檔重寫 `write_prices`）、舊商品補資料與歷史回補（新建列 `add_prices`）、
  核對寫回 TS 回填的單一價格（`product_terms.backfill` → `write_price`）。
- 回顧期初價格與回顧執行價：比價在回顧期間結束後寫（`observation` → `write_lookback`，#374）。
- 價格基準日：比價前依拆股登記推（`stock_splits.refresh_basis` → `write_basis`，ADR 0025）。

重寫四個價格時別人的欄怎麼保留只在 `carry_over` 一處決定：同一位置同一標的保留回顧期初價格（執行價照它重算）與價格基準日。
讀在 `product_store`（`Stored.prices`）。寫入都在呼叫端的 transaction 裡執行。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING

from fcn_checker.fields import PRICE_COLUMNS, PRICE_SQL
from sp_workflow.database import require_in_transaction
from sp_workflow.product_record import UnderlyingPrice, price_at

if TYPE_CHECKING:
    import psycopg

_COLUMNS = f"tdcc, seq, code, {PRICE_SQL}, lookback_price, price_basis_date"
_INSERT = f"INSERT INTO workflow.underlying_price ({_COLUMNS}) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"


def carry_over(
    prices: Sequence[UnderlyingPrice], old: Sequence[UnderlyingPrice], strike_pct: Decimal | None
) -> tuple[UnderlyingPrice, ...]:
    """核對條件重算出的價格 `prices` 加上舊列 `old` 裡別人擁有、要保留的欄，同一位置同一標的才保留：

    - 價格基準日（拆股登記推的，#491）。
    - 比價寫過的回顧期初價格（#374），執行價 = 回顧期初價格 × 執行價(%)（執行價(%) 空白時照算出的）。重算後進場價比
      回顧期初價格低的不保留（回顧期初價格不會高於進場價），由下一次比價重算（回顧結束日空白卻有回顧期初價格時比價待確認，
      `observation`）。

    標的改了的位置兩個都不保留，由下一次比價推。
    """
    was_at = {p.seq: p for p in old}
    result = []
    for price in prices:
        was = was_at.get(price.seq)
        if was is None or was.code != price.code:
            result.append(price)
            continue
        price = replace(price, basis=was.basis)
        if was.lookback is not None and (price.initial is None or price.initial >= was.lookback):
            strike = price.strike if strike_pct is None else price_at(was.lookback, strike_pct)
            price = replace(price, strike=strike, lookback=was.lookback)
        result.append(price)
    return tuple(result)


def write_prices(conn: psycopg.Connection, tdcc: str, prices: Sequence[UnderlyingPrice]) -> None:
    """整檔重寫這檔的標的價格（核對條件重算）：每一列照 `prices` 寫，含回顧期初價格與價格基準日（先用 `carry_over` 帶上舊的）。"""
    require_in_transaction(conn)
    conn.execute("DELETE FROM workflow.underlying_price WHERE tdcc = %s", (tdcc,))
    _insert(conn, tdcc, prices)


def add_prices(conn: psycopg.Connection, tdcc: str, prices: Sequence[UnderlyingPrice]) -> None:
    """新建這檔的標的價格列（舊商品補資料、歷史回補）；已經有列時照資料庫的主鍵報錯。"""
    require_in_transaction(conn)
    _insert(conn, tdcc, prices)


def write_price(
    conn: psycopg.Connection, tdcc: str, seq: int, column: str, value: Decimal | None
) -> tuple[bool, Decimal | None]:
    """改一個標的的一個價格（核對寫回 TS 回填的值，`product_terms.backfill`）；回傳 (有沒有這個標的, 原值)，
    沒有這個標的時不寫。`column` 只接受四個價格欄（`fcn_checker.fields.PRICE_COLUMNS`），欄名才放進 SQL。"""
    require_in_transaction(conn)
    if column not in PRICE_COLUMNS.values():
        raise ValueError(f"不是標的價格欄：{column}")
    found = conn.execute(
        f"SELECT {column} FROM workflow.underlying_price WHERE tdcc = %s AND seq = %s", (tdcc, seq)
    ).fetchone()
    if found is None:
        return False, None
    conn.execute(f"UPDATE workflow.underlying_price SET {column} = %s WHERE tdcc = %s AND seq = %s", (value, tdcc, seq))
    return True, found[0]


def write_lookback(
    conn: psycopg.Connection, tdcc: str, seq: int, lookback: Decimal | None, strike: Decimal | None
) -> None:
    """比價算出的回顧期初價格與回顧執行價（#374）。"""
    require_in_transaction(conn)
    conn.execute(
        "UPDATE workflow.underlying_price SET lookback_price = %s, strike_price = %s WHERE tdcc = %s AND seq = %s",
        (lookback, strike, tdcc, seq),
    )


def write_basis(conn: psycopg.Connection, tdcc: str, seq: int, basis: dt.date | None) -> None:
    """拆股登記推出的價格基準日（ADR 0025）。"""
    require_in_transaction(conn)
    conn.execute(
        "UPDATE workflow.underlying_price SET price_basis_date = %s WHERE tdcc = %s AND seq = %s", (basis, tdcc, seq)
    )


def _insert(conn: psycopg.Connection, tdcc: str, prices: Sequence[UnderlyingPrice]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            _INSERT, [(tdcc, p.seq, p.code, p.initial, p.strike, p.ki, p.ko, p.lookback, p.basis) for p in prices]
        )
