"""讀商品條件（`sp_workflow.product_store`，#437、#438）：`Stored` 的有效期初價格、有效執行價、原始執行價與價格基準日，
`read_many` 帶出的價格基準日與核對條件的差異，比價與曝險換算收盤價用同一個基準日，和這個 module 不 import 寫入端。

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
from sp_workflow.exposure import load_positions
from sp_workflow.observation import _load_closes, _load_terms, observe
from sp_workflow.product_record import UnderlyingPrice
from sp_workflow.product_store import CheckTermsState, Difference, Stored, read
from sp_workflow.status import Pricing
from sp_workflow.stock_splits import Split, SplitBook, add_split, load_splits
from test_observation import CALENDAR

A = "000000000001"
SRC = Path(__file__).resolve().parents[1] / "src" / "sp_workflow"


def stored(
    *prices: UnderlyingPrice, strike_pct: Decimal | None = Decimal(80), strike_date: dt.date | None = None
) -> Stored:
    return Stored(
        tdcc=A,
        columns={"strike_pct": strike_pct, "strike_date": strike_date},
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


def test_price_basis_is_the_split_basis_else_the_strike_date() -> None:
    strike = dt.date(2026, 5, 20)
    split = dt.date(2026, 8, 3)
    s = stored(
        UnderlyingPrice(1, "AAA UW", Decimal(100), Decimal(80), basis=split),  # 價格已含 8/3 的拆股
        UnderlyingPrice(2, "BBB UW", Decimal(50), Decimal(40)),
        strike_date=strike,
    )

    assert (s.price_basis(1), s.price_basis(2), s.price_basis(3)) == (split, strike, strike)
    assert stored(UnderlyingPrice(1, "AAA UW")).price_basis(1) is None, "沒有基準日也沒有 Strike date"


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


# ---------- 比價與曝險的價格基準日 ----------


@pytest.mark.db
def test_observation_and_exposure_convert_closes_from_the_same_basis(pg) -> None:
    """沒有價格基準日（拆股登記沒調整過價格）時，比價與曝險都從 Strike date 換算收盤價（#438）。

    1 拆 2 之後收盤 50：換回 Strike date 的基準是 100，在執行價 80 之上 → 到期還本；沒換算就會變成接股。"""
    strike, split_day, day = dt.date(2026, 5, 20), dt.date(2026, 8, 3), dt.date(2026, 10, 28)
    seed_products(pg, [inventory(A, [("台北", 1)], UL_1="AAA UW", UL_2=None, **{"KI(Freq)": "-", "KO(memo)": "N"})])
    pg.execute(
        "UPDATE workflow.product SET barrier_type = 'NONE', ko_type = 'Period End', strike_date = %s,"
        " final_valuation_date = %s WHERE tdcc = %s",
        (strike, day, A),
    )
    check_terms(pg, A)
    pg.execute(
        "INSERT INTO workflow.underlying_price (tdcc, seq, code, initial_price, strike_price, ko_price)"
        " VALUES (%s, 1, 'AAA UW', 100, 80, 150)",
        (A,),
    )
    pg.execute("INSERT INTO workflow.autocall_date (tdcc, slot, value) VALUES (%s, 1, %s)", (A, day))
    add_split(pg, Split("AAA UW", split_day, 1, 2), user="tester")
    (import_id,) = pg.execute(
        "INSERT INTO workflow.close_price_import (source, file_name, file_sha256, imported_by, summary)"
        " VALUES ('歷史收盤價', 'x.xlsx', 'x', 'tester', '{}') RETURNING import_id"
    ).fetchone()
    for close_day, close in ((strike, 100), (day, 50)):
        pg.execute(
            "INSERT INTO workflow.close_price (close_date, code, close, import_id) VALUES (%s, 'AAA UW', %s, %s)",
            (close_day, close, import_id),
        )
    book = SplitBook(load_splits(pg))

    (position,) = load_positions(pg, ["AAA UW"], dt.date(2026, 11, 30))
    (terms,), _, _ = _load_terms(pg)
    outcome = observe(terms, _load_closes(pg, [terms]), CALENDAR, day, book)

    assert terms.underlyings[0].basis == strike
    assert position.legs[0].close == Decimal(50) * book.factor("AAA UW", strike, day) == Decimal(100)
    assert (outcome.status, outcome.reason) == (Pricing.EXITED, f"到期還本 {day}")
