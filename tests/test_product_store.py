"""讀商品條件（`sp_workflow.product_store`，#437）：`Stored` 的有效期初價格、有效執行價、原始執行價，
`read_many` 帶出的價格基準日與核對條件的差異，和這個 module 不 import 寫入端。

`read_many` 其餘的讀法（指定 TDCC、全部、鎖）在 test_product_terms。資料全部虛構。
"""

from __future__ import annotations

import ast
import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pytest

from db_synth import check_terms, inventory, seed_products
from sp_workflow.product_record import UnderlyingPrice
from sp_workflow.product_store import CheckTermsState, Difference, Stored, read

A = "000000000001"
SRC = Path(__file__).resolve().parents[1] / "src" / "sp_workflow"


def stored(*prices: UnderlyingPrice, strike_pct: Decimal | None = Decimal(80)) -> Stored:
    return Stored(
        tdcc=A,
        columns={"strike_pct": strike_pct},
        source="Order",
        isin=None,
        locked_at=None,
        locked_reason="",
        check=None,
        prices=prices,
        autocall=None,
        branches=(),
    )


def answers(s: Stored, seq: int) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    return s.effective_initial(seq), s.effective_strike(seq), s.original_strike(seq)


# ---------- 有效期初價格、有效執行價、原始執行價 ----------


def test_plain_product_uses_the_initial_and_stored_strike() -> None:
    s = stored(UnderlyingPrice(1, "AAA UW", Decimal(100), Decimal(80), Decimal(60), Decimal(100)))

    assert answers(s, 1) == (Decimal(100), Decimal(80), Decimal(80))


def test_lookback_uses_the_lookback_price_and_keeps_the_original_strike_for_the_check() -> None:
    # 比價寫過回顧期初價格 90：執行價已改寫成 90 × 80% = 72；核對比的是 TS 的期初執行價 100 × 80%
    s = stored(
        UnderlyingPrice(1, "AAA UW", Decimal(100), Decimal("72.0000"), None, None, lookback=Decimal(90)),
        UnderlyingPrice(2, "BBB UW", Decimal(50), Decimal(40)),  # 還沒寫回顧期初價格的標的照一般
    )

    assert answers(s, 1) == (Decimal(90), Decimal("72.0000"), Decimal("80.0000"))
    assert answers(s, 2) == (Decimal(50), Decimal(40), Decimal(40))


def test_lookback_without_strike_pct_falls_back_to_the_stored_strike() -> None:
    s = stored(UnderlyingPrice(1, "AAA UW", Decimal(100), Decimal(72), lookback=Decimal(90)), strike_pct=None)

    assert answers(s, 1) == (Decimal(90), Decimal(72), Decimal(72))


def test_vwap_before_and_after_the_ts_backfill() -> None:
    waiting = stored(UnderlyingPrice(1, "AAA UW"))  # VWAP：TS 回填之前四個價格都空白
    filled = stored(UnderlyingPrice(1, "AAA UW", Decimal("101.2345"), Decimal("80.9876")))  # TS 回填的價格

    assert answers(waiting, 1) == (None, None, None)
    assert answers(filled, 1) == (Decimal("101.2345"), Decimal("80.9876"), Decimal("80.9876"))


def test_missing_underlying_has_no_prices() -> None:
    s = stored(UnderlyingPrice(1, "AAA UW", Decimal(100), Decimal(80)))

    assert s.price(2) is None
    assert answers(s, 2) == (None, None, None)


# ---------- read_many 帶出的欄 ----------


@pytest.mark.db
def test_read_carries_the_price_basis_date_and_the_differences(pg) -> None:
    seed_products(pg, [inventory(A, [("台北", 100)])])
    check_terms(pg, A, "待確認", "幣別不一致")
    pg.execute(
        "UPDATE workflow.check_terms SET differences = %s::jsonb WHERE tdcc = %s",
        (json.dumps([["幣別", "USD", "JPY", False], ["UF", "1.5", "0.02", True]], ensure_ascii=False), A),
    )
    pg.execute("DELETE FROM workflow.underlying_price WHERE tdcc = %s", (A,))
    pg.execute(
        "INSERT INTO workflow.underlying_price (tdcc, seq, code, initial_price, strike_price, price_basis_date)"
        " VALUES (%s, 1, 'AAA UW', 100, 80, %s)",
        (A, dt.date(2026, 10, 20)),
    )

    found = read(pg, A)

    assert found is not None and found.check is not None
    assert found.prices == (UnderlyingPrice(1, "AAA UW", Decimal(100), Decimal(80), basis=dt.date(2026, 10, 20)),)
    assert found.check.differences == (Difference("幣別", "USD", "JPY"), Difference("UF", "1.5", "0.02", True))
    assert found.check.differences[1].describe() == "UF不一致：起單資料 1.5、交易明細 0.02（換算 2）"


def test_check_terms_state_without_differences_defaults_to_none() -> None:
    state = CheckTermsState("完成", "", None, dt.datetime(2026, 10, 20, 9, 0))

    assert state.differences == ()


# ---------- 不 import 寫入端 ----------


def test_product_store_imports_no_writer() -> None:
    tree = ast.parse((SRC / "product_store.py").read_text(encoding="utf-8"))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imported |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}

    assert not imported & {"sp_workflow.product_terms", "sp_workflow.check_terms", "sp_workflow.risk_level"}


@pytest.mark.parametrize("name", ["check_terms.py", "risk_level.py"])
def test_no_function_level_imports_of_the_writer_or_risk_level(name: str) -> None:
    tree = ast.parse((SRC / name).read_text(encoding="utf-8"))
    nested = [
        node.module
        for func in ast.walk(tree)
        if isinstance(func, ast.FunctionDef)
        for node in ast.walk(func)
        if isinstance(node, ast.ImportFrom)
    ]

    assert not set(nested) & {"sp_workflow.product_terms", "sp_workflow.risk_level"}
