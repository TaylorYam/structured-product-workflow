"""算出核對條件（#88、ADR 0012）：計算本身不需要資料庫；重算、放行與鎖住對真的 PostgreSQL。資料全部虛構。"""

from __future__ import annotations

import dataclasses
import datetime as dt
from decimal import Decimal

import pytest

from db_synth import price, trade, write_daily
from order_synth import CONFIG, A, B, order_row
from sp_workflow.check_terms import (
    DONE,
    TO_CONFIRM,
    WAIT_PRICES,
    WAIT_TRADES,
    CheckTermsError,
    Difference,
    ProductTerms,
    Quote,
    TradeRow,
    UnderlyingPrice,
    calculate,
    recalculate,
    release_differences,
)
from sp_workflow.daily_import import import_daily
from sp_workflow.product_terms import write_batch
from sp_workflow.window_actions import _drafts

CALENDAR = CONFIG.calendar
THU, FRI, MON, TUE = dt.date(2026, 10, 15), dt.date(2026, 10, 16), dt.date(2026, 10, 19), dt.date(2026, 10, 20)


def product(**changes) -> ProductTerms:
    terms = ProductTerms(
        tdcc=A,
        currency="USD",
        trade_date=THU,
        strike_date=THU,
        initial_pricing="收盤價",
        underlyings=("AAA UW", "BBB UN", None, None, None),
        strike_pct=Decimal(80),
        ko_pct=Decimal(100),
        barrier_type="EKI",
        ki_pct=Decimal(65),
        upfront_pct=Decimal("1.99"),
    )
    return dataclasses.replace(terms, **changes)


def row(branch: str = "台北", amount: int = 100000, **changes) -> TradeRow:
    values = {
        "trade_date": THU,
        "currency": "USD",
        "amount": Decimal(amount),
        "branch": branch,
        "uf": Decimal("0.0199"),
    }
    return TradeRow(**{**values, **changes})


def quotes(price_date: dt.date, **by_code: tuple[str, str | None]) -> dict:
    """(取價日, 代碼) → 價格；by_code 的值是 (最新收盤價, 最新開盤價)，代碼裡的空白寫成底線。"""
    return {
        (price_date, code.replace("_", " ")): Quote(
            None if c is None else Decimal(c), None if o is None else Decimal(o)
        )
        for code, (c, o) in by_code.items()
    }


def calc(terms: ProductTerms, trades=None, dates=(FRI,), found=None, released=()):
    trades = [row()] if trades is None else trades
    found = quotes(FRI, AAA_UW=("100.5", "99.25"), BBB_UN=("50", "49")) if found is None else found
    return calculate(terms, trades, list(dates), found, set(released), CALENDAR)


# ---------- 進場價與三個價格 ----------


def test_close_prices_for_five_underlyings_match_the_hand_calculation() -> None:
    codes = ("AAA UW", "BBB UN", "CCC UQ", "DDD JT", "EEE UN")
    found = quotes(
        FRI,
        AAA_UW=("12.34565", "1"),  # 第 5 位是 5：half-up 進位（銀行家捨入會變 12.3456）
        BBB_UN=("1.0001", "1"),  # 執行價 0.50005 → 0.5001
        CCC_UQ=("200", "1"),
        DDD_JT=("3456.78", "1"),
        EEE_UN=("0.00004", "1"),  # 進場價 0.0000（捨去）
    )

    result = calc(product(underlyings=codes, strike_pct=Decimal(50)), found=found)

    assert result.status == DONE and result.reason == "" and result.price_date == FRI
    assert result.prices == (
        # 執行價、下限價、KO 價用股價檔原值算：12.34565 × 50% = 6.172825 → 6.1728（用進場價 12.3457 算會是 6.1729）
        UnderlyingPrice(1, "AAA UW", Decimal("12.3457"), Decimal("6.1728"), Decimal("8.0247"), Decimal("12.3457")),
        UnderlyingPrice(2, "BBB UN", Decimal("1.0001"), Decimal("0.5001"), Decimal("0.6501"), Decimal("1.0001")),
        UnderlyingPrice(3, "CCC UQ", Decimal("200.0000"), Decimal("100.0000"), Decimal("130.0000"), Decimal("200")),
        UnderlyingPrice(4, "DDD JT", Decimal("3456.78"), Decimal("1728.39"), Decimal("2246.907"), Decimal("3456.78")),
        UnderlyingPrice(5, "EEE UN", Decimal("0.0000"), Decimal("0.0000"), Decimal("0.0000"), Decimal("0.0000")),
    )


def test_open_prices_use_the_latest_open() -> None:
    found = quotes(FRI, AAA_UW=("100.5", "99.25"), BBB_UN=("50", "48.12345"))

    result = calc(product(initial_pricing="開盤價", ko_pct=Decimal("102.5")), found=found)

    assert result.status == DONE
    assert result.prices == (
        UnderlyingPrice(1, "AAA UW", Decimal("99.25"), Decimal("79.4"), Decimal("64.5125"), Decimal("101.7313")),
        UnderlyingPrice(2, "BBB UN", Decimal("48.1235"), Decimal("38.4988"), Decimal("31.2802"), Decimal("49.3265")),
    )


def test_no_barrier_leaves_the_ki_price_blank() -> None:
    result = calc(product(barrier_type="NONE", ki_pct=None))

    assert result.status == DONE
    assert [p.ki for p in result.prices] == [None, None]
    assert [p.initial for p in result.prices] == [Decimal("100.5"), Decimal("50")]


def test_vwap_leaves_all_four_prices_blank_without_asking() -> None:
    result = calc(product(initial_pricing="VWAP"), dates=())

    assert result.status == DONE
    assert result.prices == (UnderlyingPrice(1, "AAA UW"), UnderlyingPrice(2, "BBB UN"))
    assert result.price_date is None
    assert "VWAP" in result.reason


# ---------- 股價檔與交易日 ----------


def test_a_friday_strike_uses_the_monday_file() -> None:
    found = quotes(MON, AAA_UW=("100.5", "1"), BBB_UN=("50", "1"))

    result = calc(product(trade_date=FRI, strike_date=FRI), trades=[row(trade_date=FRI)], dates=(THU, MON), found=found)

    assert result.status == DONE and result.price_date == MON


def test_a_missed_file_in_between_needs_confirmation() -> None:
    found = quotes(TUE, AAA_UW=("100.5", "1"), BBB_UN=("50", "1"))

    result = calc(product(trade_date=FRI, strike_date=FRI), trades=[row(trade_date=FRI)], dates=(TUE,), found=found)

    assert result.status == TO_CONFIRM and result.price_date == TUE
    assert "UL_1 AAA UW：Strike date 2026-10-16 和取價日 2026-10-20 之間還有美國" in result.reason
    assert "2026-10-19" in result.reason
    assert all(p.initial is None for p in result.prices), "日期對不上的價格不能用"


def test_an_exchange_not_in_the_calendar_needs_confirmation() -> None:
    found = quotes(FRI, AAA_UW=("100.5", "1"), **{"BMW_GY": ("400", "1")})

    result = calc(product(underlyings=("AAA UW", "BMW GY", None, None, None)), found=found)

    assert result.status == TO_CONFIRM
    assert result.reason.startswith("UL_2 BMW GY：交易所不在交易所假日表")
    assert result.prices[0].initial == Decimal("100.5") and result.prices[1].initial is None


def test_a_strike_date_on_a_market_holiday_needs_confirmation() -> None:
    holiday = dt.date(2026, 10, 12)  # 日本體育日，美國照常交易
    found = quotes(dt.date(2026, 10, 13), DDD_JT=("3000", "1"))
    terms = product(trade_date=holiday, strike_date=holiday, underlyings=("DDD JT", None, None, None, None))

    result = calc(terms, trades=[row(trade_date=holiday)], dates=(dt.date(2026, 10, 13),), found=found)

    assert result.status == TO_CONFIRM
    assert "UL_1 DDD JT：Strike date 2026-10-12 不是日本（東京證交所）的交易日" in result.reason


def test_dates_outside_the_calendar_need_confirmation() -> None:
    late, after = dt.date(2027, 12, 30), dt.date(2028, 1, 4)
    found = quotes(after, DDD_JT=("3000", "1"))
    terms = product(trade_date=late, strike_date=late, underlyings=("DDD JT", None, None, None, None))

    result = calc(terms, trades=[row(trade_date=late)], dates=(after,), found=found)

    assert result.status == TO_CONFIRM and "不在日本（東京證交所）假日表的涵蓋範圍" in result.reason


def test_without_a_later_price_file_it_waits_for_prices() -> None:
    result = calc(product(strike_date=FRI, trade_date=FRI), trades=[row(trade_date=FRI)], dates=(THU, FRI))

    assert result.status == WAIT_PRICES and result.price_date is None
    assert result.reason == "還沒有取價日比 Strike date 2026-10-16 晚的股價檔"


def test_next_day_open_uses_the_file_after_the_strike_date() -> None:
    """明日開盤價（#101）：交易日週四、Strike date 週五，用週一的檔。"""
    found = {
        **quotes(FRI, AAA_UW=("1", "1"), BBB_UN=("1", "1")),
        **quotes(MON, AAA_UW=("100.5", "99.25"), BBB_UN=("50", "49")),
    }

    result = calc(product(initial_pricing="開盤價", strike_date=FRI), dates=(FRI, MON), found=found)

    assert result.status == DONE and result.price_date == MON
    assert [p.initial for p in result.prices] == [Decimal("99.25"), Decimal("49")]


@pytest.mark.parametrize(
    ("found", "reason"),
    [
        (quotes(FRI, BBB_UN=("50", "49")), "UL_1 AAA UW：股價檔（取價日 2026-10-16）沒有這個代碼"),
        (
            quotes(FRI, AAA_UW=(None, "99"), BBB_UN=("50", "49")),
            "UL_1 AAA UW：股價檔（取價日 2026-10-16）沒有最新收盤價",
        ),
        (
            {(FRI, "AAA UW"): Quote(None, None, conflict=True), **quotes(FRI, BBB_UN=("50", "49"))},
            "UL_1 AAA UW：股價檔（取價日 2026-10-16）這個代碼有好幾列、值不同",
        ),
    ],
    ids=["沒有代碼", "沒有價格", "衝突"],
)
def test_missing_or_conflicting_prices_name_the_underlying_and_code(found, reason) -> None:
    result = calc(product(), found=found)

    assert result.status == TO_CONFIRM and result.reason == reason
    assert result.prices[0] == UnderlyingPrice(1, "AAA UW")
    assert result.prices[1].initial == Decimal("50"), "其他標的照算"


def test_an_unknown_initial_pricing_needs_confirmation() -> None:
    result = calc(product(initial_pricing="明日開盤價"))

    assert result.status == TO_CONFIRM and "期初定價「明日開盤價」" in result.reason


def test_missing_required_terms_need_confirmation_and_name_the_fields() -> None:
    """#114：商品條件缺必填欄時不算價格、不交叉比對，分公司配置照樣由交易明細算。"""
    terms = product(strike_pct=None, missing=("Strike (%)", "Maturity date"))

    with_trades = calc(terms, trades=[row("台北", 100000), row("台中", 5000, currency="JPY")])
    without = calc(terms, trades=[])

    assert (with_trades.status, with_trades.reason) == (TO_CONFIRM, "缺 Strike (%)、Maturity date")
    assert with_trades.prices == (UnderlyingPrice(1, "AAA UW"), UnderlyingPrice(2, "BBB UN"))
    assert with_trades.price_date is None and with_trades.differences == (), "缺欄時沒有可以放行的差異"
    assert with_trades.allocation == (("台北", Decimal(100000)), ("台中", Decimal(5000)))
    assert (without.status, without.reason) == (TO_CONFIRM, "缺 Strike (%)、Maturity date；交易明細還沒有這檔")
    assert without.allocation is None


# ---------- 分公司配置與交叉比對 ----------


def test_branch_allocation_sums_per_branch_in_order_of_appearance() -> None:
    trades = [row("台中", 30000), row("台北", 100000), row("台中", 20000), row("高雄", 5000)]

    result = calc(product(), trades=trades)

    assert result.allocation == (("台中", Decimal(50000)), ("台北", Decimal(100000)), ("高雄", Decimal(5000)))


def test_without_trade_rows_it_waits_and_keeps_the_allocation() -> None:
    result = calc(product(), trades=[])

    assert result.status == WAIT_TRADES and result.allocation is None and result.differences == ()
    assert result.reason == "交易明細還沒有這檔"


@pytest.mark.parametrize(
    ("trades", "reason"),
    [
        ([row(currency="JPY")], "幣別不一致：起單資料 USD、交易明細 JPY"),
        ([row(), row(currency="TWD")], "幣別不一致：起單資料 USD、交易明細 USD、TWD"),
        ([row(trade_date=FRI)], "交易日不一致：起單資料 2026-10-15、交易明細 2026-10-16"),
        ([row(uf=Decimal("0.0215"))], "UF不一致：起單資料 1.99、交易明細 0.0215（換算 2.15）"),
        ([row(), row(uf=Decimal("0.02"))], "UF不一致：起單資料 1.99、交易明細 0.0199、0.02（換算 1.99、2）"),
        ([row(uf=None)], "UF不一致：起單資料 1.99、交易明細 空白（換算 空白）"),
    ],
    ids=["幣別", "交易列之間幣別不同", "交易日", "UF", "交易列之間UF不同", "UF空白"],
)
def test_cross_check_shows_both_values(trades, reason) -> None:
    result = calc(product(), trades=trades)

    assert result.status == TO_CONFIRM and result.reason == reason


def test_matching_trade_rows_pass_the_cross_check() -> None:
    result = calc(product(), trades=[row(currency=" usd "), row("台中")])

    assert result.status == DONE and result.differences == ()


def test_a_released_difference_no_longer_blocks_until_it_changes() -> None:
    released = [("UF", "1.99", "0.0215")]

    passed = calc(product(), trades=[row(uf=Decimal("0.0215"))], released=released)
    changed = calc(product(), trades=[row(uf=Decimal("0.022"))], released=released)

    assert passed.status == DONE
    assert passed.differences == (Difference("UF", "1.99", "0.0215", released=True),)
    assert passed.reason == "已放行的差異：UF（起單資料 1.99、交易明細 0.0215（換算 2.15））"
    assert changed.status == TO_CONFIRM and changed.differences == (Difference("UF", "1.99", "0.022"),)


# ---------- 資料庫：重算、放行、鎖住 ----------


def import_trades_and_prices(pg, tmp_path, trades, prices=None, *, price_date=dt.datetime(2026, 10, 16), name="d"):
    path = tmp_path / f"{name}.xlsx"
    return import_daily(write_daily(path, trades, prices, price_date=price_date), pg)


def import_order(pg, tdcc=A, **changes):
    values = {"Upfront / NotePrice (%)": Decimal("97.85"), **changes}  # UF 2.15，和 db_synth 的交易列一樣
    receipt = write_batch(pg, _drafts([order_row(tdcc, values)], CONFIG, pg), CONFIG, user="tester")
    assert receipt.lines[0].imported, receipt.lines[0].detail


def stored(pg, tdcc=A):
    terms = pg.execute(
        "SELECT status, reason, price_date FROM workflow.check_terms WHERE tdcc = %s", (tdcc,)
    ).fetchone()
    prices = pg.execute(
        "SELECT seq, code, initial_price, strike_price, ki_price, ko_price FROM workflow.underlying_price"
        " WHERE tdcc = %s ORDER BY seq",
        (tdcc,),
    ).fetchall()
    branches = pg.execute(
        "SELECT seq, branch, amount FROM workflow.branch_allocation WHERE tdcc = %s ORDER BY seq", (tdcc,)
    ).fetchall()
    return terms, prices, branches


@pytest.mark.db
def test_recalculation_writes_prices_and_branches_from_trade_rows(pg, tmp_path) -> None:
    cancelled = {**trade(A, "Ben", "高雄", 999999, number=None), "備註": "Cancel"}
    trades = [trade(A, "Amy", "台北", 100000), trade(A, "PB", "台中", 50000, number=None), cancelled]
    trades.append(trade(A, "Ben", "台北", 30000, number=None))
    import_trades_and_prices(pg, tmp_path, trades, [price("AAA UW", 100.5), price("BBB UN", 50.00005)])
    import_order(pg)

    receipt = recalculate(pg, CONFIG)

    (line,) = receipt.lines
    assert (line.tdcc, line.status, line.changed) == (A, DONE, True)
    terms, prices, branches = stored(pg)
    assert terms == (DONE, None, dt.date(2026, 10, 16))
    assert prices == [
        (1, "AAA UW", Decimal("100.5000"), Decimal("80.4000"), Decimal("65.3250"), Decimal("100.5000")),
        (2, "BBB UN", Decimal("50.0001"), Decimal("40.0000"), Decimal("32.5000"), Decimal("50.0001")),
    ]
    assert branches == [(1, "台北", Decimal("130000.0000")), (2, "台中", Decimal("50000.0000"))], (
        "取消的列不算、PB 也算"
    )


@pytest.mark.db
def test_unchanged_products_are_not_rewritten(pg, tmp_path) -> None:
    import_trades_and_prices(pg, tmp_path, [trade(A, "Amy", "台北", 100000)], [price("AAA UW"), price("BBB UN")])
    import_order(pg)
    recalculate(pg, CONFIG)
    before = pg.execute("SELECT calculated_at FROM workflow.check_terms").fetchone()

    again = recalculate(pg, CONFIG)

    assert [line.changed for line in again.lines] == [False]
    assert again.to_show() == ()
    assert pg.execute("SELECT calculated_at FROM workflow.check_terms").fetchone() == before


@pytest.mark.db
def test_waiting_products_finish_when_the_data_arrives(pg, tmp_path) -> None:
    import_trades_and_prices(pg, tmp_path, [trade(B, "Amy", "台北", 1)], [price("AAA UW")])  # 只有別檔的交易
    import_order(pg, **{"Trade date": FRI, "Strike date": FRI})

    first = recalculate(pg, CONFIG).line_of(A)
    import_trades_and_prices(
        pg,
        tmp_path,
        [{**trade(A, "Amy", "台北", 100000), "交易日期": dt.datetime(2026, 10, 16)}],
        [price("AAA UW"), price("BBB UN")],
        price_date=dt.datetime(2026, 10, 19),
        name="e",
    )
    second = recalculate(pg, CONFIG).line_of(A)

    assert first is not None and second is not None
    assert (first.status, first.reason) == (
        WAIT_TRADES,
        "交易明細還沒有這檔；還沒有取價日比 Strike date 2026-10-16 晚的股價檔",
    )
    assert (second.status, second.changed) == (DONE, True)
    assert stored(pg)[0] == (DONE, None, dt.date(2026, 10, 19))


@pytest.mark.db
def test_a_release_records_who_and_lasts_until_the_difference_changes(pg, tmp_path) -> None:
    import_trades_and_prices(pg, tmp_path, [trade(A, "Amy", "台北", 100000)], [price("AAA UW"), price("BBB UN")])
    import_order(pg, Currency="USD", **{"Upfront / NotePrice (%)": Decimal("98.01")})
    line = recalculate(pg, CONFIG).line_of(A)
    assert line is not None and line.status == TO_CONFIRM
    assert line.unreleased == (Difference("UF", "1.99", "0.0215"),)

    released = release_differences(pg, A, line.unreleased, CONFIG, user="operator1")

    assert released.status == DONE and released.changed
    assert pg.execute(
        "SELECT item, order_value, trade_value, released_by FROM workflow.cross_check_release"
    ).fetchall() == [("UF", "1.99", "0.0215", "operator1")]
    assert recalculate(pg, CONFIG).line_of(A).status == DONE, "同樣的差異不再擋"

    import_order(pg, **{"Upfront / NotePrice (%)": Decimal("98.1")})
    again = recalculate(pg, CONFIG).line_of(A)
    assert again is not None and again.status == TO_CONFIRM
    assert again.unreleased == (Difference("UF", "1.9", "0.0215"),), "差異變了要重新放行"


@pytest.mark.db
def test_products_with_blank_required_terms_stay_to_confirm_until_filled(pg, tmp_path) -> None:
    """#114：人工維護先存一部分（這裡直接改資料庫）：重算不中斷，待確認並寫出缺的欄；補齊後照常算。"""
    import_trades_and_prices(pg, tmp_path, [trade(A, "Amy", "台北", 100000)], [price("AAA UW"), price("BBB UN")])
    import_order(pg)
    pg.execute("UPDATE workflow.product SET maturity_date = NULL, strike_pct = NULL WHERE tdcc = %s", (A,))
    (import_id,) = pg.execute("SELECT max(import_id) FROM workflow.order_import").fetchone()
    pg.execute(  # 只有識別欄的商品（例：只有交易明細、在人工維護從頭補）
        "INSERT INTO workflow.product (tdcc, product, issuer, source, order_import_id)"
        " VALUES (%s, 'FCN', 'SG', 'Order', %s)",
        (B, import_id),
    )

    receipt = recalculate(pg, CONFIG)

    partial, bare = receipt.line_of(A), receipt.line_of(B)
    assert partial is not None and bare is not None
    assert (partial.status, partial.reason, partial.unreleased) == (TO_CONFIRM, "缺 Strike (%)、Maturity date", ())
    assert stored(pg) == (
        (TO_CONFIRM, "缺 Strike (%)、Maturity date", None),
        [(1, "AAA UW", None, None, None, None), (2, "BBB UN", None, None, None, None)],
        [(1, "台北", Decimal("100000.0000"))],
    )
    assert bare.status == TO_CONFIRM
    assert bare.reason.startswith("缺 Currency、Guaranteed Periods (m)、BBG Code 1、Strike (%)、KO Type")
    assert bare.reason.endswith("Maturity date、期初價格；交易明細還沒有這檔")
    assert "Product" not in bare.reason

    pg.execute("UPDATE workflow.product SET maturity_date = '2027-04-22', strike_pct = 80 WHERE tdcc = %s", (A,))
    filled = recalculate(pg, CONFIG).line_of(A)
    assert filled is not None and (filled.status, filled.changed) == (DONE, True)


@pytest.mark.db
def test_locked_products_are_not_recalculated(pg, tmp_path) -> None:
    import_trades_and_prices(pg, tmp_path, [trade(A, "Amy", "台北", 100000)], [price("AAA UW"), price("BBB UN")])
    import_order(pg)
    pg.execute("INSERT INTO workflow.branch_allocation (tdcc, seq, branch, amount) VALUES (%s, 1, '原本', 1)", (A,))
    pg.execute("UPDATE workflow.product SET locked_at = now() WHERE tdcc = %s", (A,))

    receipt = recalculate(pg, CONFIG)

    assert receipt.lines == ()
    assert stored(pg) == (None, [], [(1, "原本", Decimal("1.0000"))])
    with pytest.raises(CheckTermsError, match="已鎖住"):
        release_differences(pg, A, [Difference("UF", "1", "2")], CONFIG, user="operator1")
