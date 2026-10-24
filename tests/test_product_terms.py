"""讀資料庫裡的商品條件（`product_terms.read_many`，#204）：指定 TDCC 與全部、鎖與不鎖、關聯表沒有資料時的預設值。

資料庫測試對真的 PostgreSQL；資料全部虛構。
"""

from __future__ import annotations

import datetime as dt
import os
from decimal import Decimal

import psycopg
import pytest

from order_synth import CONFIG, A, B, drafts_of, order_row
from sp_workflow.check_terms import UnderlyingPrice
from sp_workflow.product_record import fields_of
from sp_workflow.product_terms import locked, read, read_many, write_batch
from sp_workflow.status import CheckTerms

pytestmark = pytest.mark.db


def imported(pg, *tdccs: str) -> None:
    write_batch(pg, drafts_of([order_row(t) for t in tdccs], pg), CONFIG, user="tester")


def test_given_tdccs_are_read_in_order_and_missing_ones_left_out(pg) -> None:
    imported(pg, B, A)

    found = read_many(pg, [B, "000000000000", A])

    assert list(found) == [A, B], "依 TDCC 排；資料庫沒有的檔不在結果裡"
    assert found[A].tdcc == A and found[A].columns["issuer"] == "Barclays"
    assert found[A].fields == fields_of(found[A].columns)
    assert found[A].source == "Order" and not locked(found[A])


def test_none_reads_every_product(pg) -> None:
    imported(pg, A, B)

    assert list(read_many(pg)) == [A, B]
    assert read_many(pg, []) == {}


def test_related_tables_are_read_with_the_product(pg) -> None:
    imported(pg, A)
    pg.execute("UPDATE workflow.product SET isin = 'XS0000000001', locked_at = now() WHERE tdcc = %s", (A,))
    pg.execute(
        "INSERT INTO workflow.check_terms (tdcc, status, reason, price_date) VALUES (%s, %s, 'x', %s)"
        " ON CONFLICT (tdcc) DO UPDATE SET status = EXCLUDED.status, reason = EXCLUDED.reason,"
        " price_date = EXCLUDED.price_date",
        (A, CheckTerms.DONE, dt.date(2026, 10, 16)),
    )
    pg.execute("DELETE FROM workflow.underlying_price WHERE tdcc = %s", (A,))
    pg.execute(
        "INSERT INTO workflow.underlying_price (tdcc, seq, code, initial_price, strike_price, ki_price, ko_price)"
        " VALUES (%s, 2, 'BBB UW', 50, 40, 30, 50), (%s, 1, 'AAA UW', 100, 80, NULL, 100)",
        (A, A),
    )
    pg.execute("DELETE FROM workflow.branch_allocation WHERE tdcc = %s", (A,))
    pg.execute(
        "INSERT INTO workflow.branch_allocation (tdcc, seq, branch, amount) VALUES (%s, 2, '台中', 200), (%s, 1, '台北', 100)",
        (A, A),
    )
    pg.execute(
        "INSERT INTO workflow.autocall_date (tdcc, slot, value) VALUES (%s, 1, %s), (%s, 2, NULL)",
        (A, dt.date(2026, 11, 16), A),
    )

    (stored,) = read_many(pg, [A]).values()

    assert stored.isin == "XS0000000001" and locked(stored)
    assert stored.locked_reason == "已核對通過，條件已鎖住，要改請到「7 人工維護」"
    assert (stored.check.status, stored.check.reason, stored.check.price_date) == (
        CheckTerms.DONE,
        "x",
        dt.date(2026, 10, 16),
    )
    assert stored.prices == (
        UnderlyingPrice(1, "AAA UW", Decimal(100), Decimal(80), None, Decimal(100)),
        UnderlyingPrice(2, "BBB UW", Decimal(50), Decimal(40), Decimal(30), Decimal(50)),
    ), "依標的序號"
    assert stored.branches == (("台北", Decimal(100)), ("台中", Decimal(200))), "依分公司配置的順序"
    assert stored.autocall == {1: dt.date(2026, 11, 16), 2: None}


def test_empty_related_tables_give_the_defaults(pg) -> None:
    imported(pg, A)
    pg.execute("DELETE FROM workflow.check_terms WHERE tdcc = %s", (A,))  # 標的價格跟著刪
    pg.execute("DELETE FROM workflow.branch_allocation WHERE tdcc = %s", (A,))

    stored = read(pg, A)

    assert stored is not None
    assert (stored.check, stored.prices, stored.autocall, stored.branches) == (None, (), None, ())
    assert stored.isin is None and stored.locked_at is None and stored.locked_reason == ""


def test_lock_holds_the_product_rows_until_the_transaction_ends(pg) -> None:
    imported(pg, A, B)
    other = psycopg.connect(os.environ["SPW_TEST_DSN"], autocommit=True)
    try:
        with pg.transaction():
            assert list(read_many(pg, [A], lock=True)) == [A]
            with pytest.raises(psycopg.errors.LockNotAvailable):
                other.execute("SELECT 1 FROM workflow.product WHERE tdcc = %s FOR UPDATE NOWAIT", (A,))
            other.execute("SELECT 1 FROM workflow.product WHERE tdcc = %s FOR UPDATE NOWAIT", (B,))  # 沒鎖別檔
        other.execute("SELECT 1 FROM workflow.product WHERE tdcc = %s FOR UPDATE NOWAIT", (A,))  # 結束就放開
    finally:
        other.close()


def test_without_lock_nothing_is_held(pg) -> None:
    imported(pg, A)
    other = psycopg.connect(os.environ["SPW_TEST_DSN"], autocommit=True)
    try:
        with pg.transaction():
            read_many(pg, [A])
            other.execute("SELECT 1 FROM workflow.product WHERE tdcc = %s FOR UPDATE NOWAIT", (A,))
    finally:
        other.close()


def test_lock_needs_the_callers_transaction(pg) -> None:
    with pytest.raises(RuntimeError):
        read_many(pg, [A], lock=True)
