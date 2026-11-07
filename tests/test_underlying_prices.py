"""寫標的價格（`sp_workflow.underlying_prices`，#492）：重寫四個價格時保留比價寫的欄，和寫入只在這個 module。資料全部虛構。"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from pathlib import Path

from sp_workflow.product_record import UnderlyingPrice
from sp_workflow.underlying_prices import carry_over

D = Decimal
BASIS = dt.date(2026, 11, 9)
SRC = Path(__file__).resolve().parents[1] / "src"


def test_the_same_underlying_keeps_the_lookback_and_basis_with_the_strike_from_the_lookback() -> None:
    old = (UnderlyingPrice(1, "AAA UW", D(100), D(72), lookback=D(90), basis=BASIS),)
    new = (UnderlyingPrice(1, "AAA UW", D(101), D("80.8")),)

    assert carry_over(new, old, D(80)) == (
        UnderlyingPrice(1, "AAA UW", D(101), D("72.0000"), lookback=D(90), basis=BASIS),
    )
    assert carry_over(new, old, None)[0].strike == D("80.8"), "執行價(%) 空白：照算出的"
    waiting = (UnderlyingPrice(1, "AAA UW"),)  # VWAP 等 TS 回填：進場價空白也保留
    assert carry_over(waiting, old, D(80))[0].lookback == D(90)


def test_a_lower_initial_drops_the_lookback_but_keeps_the_basis() -> None:
    old = (UnderlyingPrice(1, "AAA UW", D(100), D(72), lookback=D(90), basis=BASIS),)
    new = (UnderlyingPrice(1, "AAA UW", D(89), D("71.2")),)

    assert carry_over(new, old, D(80)) == (UnderlyingPrice(1, "AAA UW", D(89), D("71.2"), basis=BASIS),)


def test_a_changed_or_new_underlying_keeps_nothing() -> None:
    old = (
        UnderlyingPrice(1, "AAA UW", D(100), D(72), lookback=D(90), basis=BASIS),
        UnderlyingPrice(2, "BBB UN", D(50), D(40), basis=BASIS),
    )
    new = (
        UnderlyingPrice(1, "BBB UN", D(50), D(40)),
        UnderlyingPrice(2, "AAA UW", D(100), D(80)),
        UnderlyingPrice(3, "CCC UN", D(10), D(8)),
    )

    assert carry_over(new, old, D(80)) == new


def test_only_this_module_writes_underlying_prices() -> None:
    writes = re.compile(r"(INSERT INTO|UPDATE|DELETE FROM)\s+workflow\.underlying_price\b")
    found = sorted(
        str(path.relative_to(SRC)).replace("\\", "/")
        for path in SRC.rglob("*.py")
        if writes.search(path.read_text(encoding="utf-8"))
    )

    assert found == ["sp_workflow/underlying_prices.py"]
