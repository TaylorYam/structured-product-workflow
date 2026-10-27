"""自算風險等級（#238、ADR 0023）：計算本身不需要資料庫；跟著核對條件重算、鎖住後不變對真的 PostgreSQL。資料全部虛構。"""

from __future__ import annotations

import dataclasses
import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from db_synth import trade, write_daily
from fcn_checker.ingestion import IngestionError
from order_synth import CONFIG, A, open_terms, order_row
from sp_workflow.check_terms import WAIT_TRADES, recalculate
from sp_workflow.credit_ratings import FITCH, GUARANTOR, ISSUER, MOODYS, SP, Entity, Rating, RatingRows
from sp_workflow.daily_import import import_daily
from sp_workflow.product_terms import load_risk_underlyings, write, write_batch
from sp_workflow.risk_level import (
    BACKFILLED,
    DONE,
    PENDING,
    RiskLevel,
    RiskTerms,
    Volatility,
    _detail,
    _detail_json,
    assess,
    count_score,
    level_of,
    rating_score,
    risk_level_of,
    tenor_score,
    underlying_key,
    volatility_score,
)
from sp_workflow.window_actions import _drafts

FIRST = dt.date(1900, 1, 1)
TRADE = dt.date(2026, 9, 4)  # 回填期間（< 2026-10-30）
LATER = dt.date(2026, 11, 2)  # 待算期間（>= 2026-10-30）
RATINGS = RatingRows(
    (
        Entity("BARC", ISSUER, "Barclays Bank PLC"),
        Entity("CITI", ISSUER, "Citigroup Global Markets Holdings Inc."),
        Entity("CITI", GUARANTOR, "Citigroup Inc."),
    ),
    (
        Rating("BARC", ISSUER, FIRST, "A+", "A1", "AA-"),
        Rating("CITI", ISSUER, FIRST, "AA", "Aa2", "AA"),
        Rating("CITI", GUARANTOR, FIRST, "BBB+", "A3", "A"),
    ),
)


def terms(**changes) -> RiskTerms:
    base = RiskTerms(
        tdcc=A,
        issuer="BARC",
        trade_date=TRADE,
        tenor_months=9,
        underlyings=("LITE UW", "NBIS UW", "MRVL UW", None, None),
    )
    return dataclasses.replace(base, **changes)


def vols(day: dt.date = TRADE, **values: str | None) -> dict[tuple[dt.date, str], Volatility]:
    """股價檔一天的 200 日波動率：代碼（底線代替空白）→ 值。"""
    return {(day, code.replace("_", " ")): Volatility(None if v is None else Decimal(v)) for code, v in values.items()}


_SHEET = {"LITE_UW": "100.98", "NBIS_UW": "113.92", "MRVL_UW": "84.04"}  # Generator 試算表的實例
SHEET_VOLS = vols(**_SHEET)


def assess_with(product: RiskTerms, found=SHEET_VOLS, dates=(TRADE,), ratings=RATINGS, indices=(), etfs=()):
    return assess(product, list(dates), found, ratings, frozenset(indices), frozenset(etfs))


# ---------- 各分數的門檻 ----------


@pytest.mark.parametrize(
    ("months", "score"), [(1, 1), (12, 1), (13, 2), (36, 2), (37, 3), (60, 3), (61, 4), (120, 4), (121, 5)]
)
def test_tenor_score_thresholds(months, score) -> None:
    assert tenor_score(months) == score


@pytest.mark.parametrize(("count", "score"), [(1, 1), (2, 2), (3, 3), (4, 4), (5, 4), (6, 5), (7, 5)])
def test_underlying_count_score(count, score) -> None:
    assert count_score(count) == score


@pytest.mark.parametrize(
    ("average", "score"),
    [
        ("0", 1),
        ("15", 1),
        ("15.0001", 2),
        ("30", 2),
        ("30.0001", 3),
        ("45", 3),
        ("45.0001", 4),
        ("59.9999", 4),
        ("60", 5),
        ("113.92", 5),
    ],
)
def test_volatility_score_thresholds(average, score) -> None:
    assert volatility_score(Decimal(average)) == score


@pytest.mark.parametrize(
    ("agency", "rating", "score"),
    [
        (SP, "AAA", 1),
        (SP, "AA+", 2),
        (FITCH, "AA", 2),
        (SP, "AA-", 3),
        (FITCH, "A+", 3),
        (SP, "A", 4),
        (FITCH, "A-", 4),
        (SP, "BBB+", 5),
        (FITCH, "BB", 5),
        (MOODYS, "Aaa", 1),
        (MOODYS, "Aa1", 2),
        (MOODYS, "Aa2", 2),
        (MOODYS, "Aa3", 3),
        (MOODYS, "A1", 3),
        (MOODYS, "A2", 4),
        (MOODYS, "A3", 4),
        (MOODYS, "Baa1", 5),
        (MOODYS, "Ba2", 5),
    ],
)
def test_rating_score_per_agency(agency, rating, score) -> None:
    assert rating_score(agency, rating) == score


@pytest.mark.parametrize(
    ("total", "level"),
    [
        ("1.79", "RR1"),
        ("1.8", "RR2"),
        ("2.59", "RR2"),
        ("2.6", "RR3"),
        ("3.39", "RR3"),
        ("3.4", "RR4"),
        ("4.19", "RR4"),
        ("4.2", "RR5"),
        ("5", "RR5"),
    ],
)
def test_level_thresholds_include_the_lower_bound(total, level) -> None:
    assert level_of(Decimal(total)) == level


# ---------- 一檔的計算 ----------


def test_the_generator_spreadsheet_example_is_rr4() -> None:
    """LITE／NBIS／MRVL、9 個月、Barclays（A+／A1／AA-）→ 2 + 0.2 + 1.5 + 0.26 = 3.96 → RR4。"""
    risk = assess_with(terms())

    assert (risk.status, risk.level, risk.total, risk.reason, risk.price_date) == (
        DONE,
        "RR4",
        Decimal("3.96"),
        "",
        TRADE,
    )
    detail = risk.detail
    assert detail is not None
    assert (detail.tenor_score, detail.underlying_type_score, detail.underlying_count_score) == (1, 4, 3)
    assert detail.average_volatility == Decimal("99.646667") and detail.volatility_score == 5
    assert detail.structure_score == Decimal("5")
    assert (detail.rating_role, detail.rating_score, detail.liquidity_score) == (ISSUER, 3, Decimal("2.6"))
    assert detail.ratings == ((SP, "A+"), (MOODYS, "A1"), (FITCH, "AA-"))
    assert detail.volatilities == (
        ("LITE UW", Decimal("100.98")),
        ("NBIS UW", Decimal("113.92")),
        ("MRVL UW", Decimal("84.04")),
    )
    assert risk.label == "RR4"
    text = risk.describe()
    assert "總分 3.96 = 0.4×保本率 5 + 0.2×年期 1 + 0.3×結構複雜度 5 + 0.1×流動性 2.6" in text
    assert "平均 99.6467（LITE UW 100.98、NBIS UW 113.92、MRVL UW 84.04），5 分" in text
    assert "發行機構 標準普爾 A+、穆迪 A1、惠譽 AA-，取最好的一家 3 分" in text
    assert "股價檔（取價日）：2026-09-04" in text


def test_a_total_of_exactly_3_4_is_rr4() -> None:
    """個股 4 + 3 檔 3 + 波動度 1、1 年內、AAA：2 + 0.2 + 1.1 + 0.1 = 3.4，十進位算剛好是 RR4（試算表的浮點誤差不重現）。"""
    rated = RatingRows((Entity("BARC", ISSUER, "x"),), (Rating("BARC", ISSUER, FIRST, "AAA", None, None),))

    risk = assess_with(terms(), vols(LITE_UW="10", NBIS_UW="12", MRVL_UW="14"), ratings=rated)

    assert (risk.total, risk.level) == (Decimal("3.4"), "RR4")


def test_one_quiet_index_with_a_good_rating_is_rr3() -> None:
    """UL_1 在指數清單上 3 分；單一低波動標的、AA → 2 + 0.2 + 0.8 + 0.18 = 3.18 → RR3。

    清單照彭博寫 `SPX Index`，資料庫存的代碼已去掉類別字（`SPX`）：比對時兩邊都去掉、不分大小寫。
    """
    rated = RatingRows((Entity("BARC", ISSUER, "x"),), (Rating("BARC", ISSUER, FIRST, "AA", None, None),))
    product = terms(underlyings=("SPX", None, None, None, None), tenor_months=6)

    risk = assess_with(product, vols(SPX="12.5"), ratings=rated, indices=["SPX Index"])

    assert risk.detail is not None
    assert (risk.detail.underlying_type, risk.detail.underlying_type_score) == ("指數", 3)
    assert (risk.total, risk.level) == (Decimal("3.18"), "RR3")
    assert "UL_1 是指數，3 分" in risk.describe()
    for listed in (" spx index ", "SPX", "spx"):
        same = assess_with(product, vols(SPX="12.5"), ratings=rated, indices=[listed])
        assert same.detail is not None and same.detail.underlying_type_score == 3, listed


def test_only_the_first_underlying_decides_index_or_stock() -> None:
    product = terms(underlyings=("LITE UW", "SPX", None, None, None))

    risk = assess_with(product, vols(LITE_UW="20", SPX="12"), indices=["SPX Index"])

    assert risk.detail is not None and risk.detail.underlying_type_score == 4


def test_an_etf_first_underlying_scores_like_an_index() -> None:
    """ETF 和指數一樣 3 分（#252）；明細寫 ETF。清單照白名單寫 `QQQ US Equity`（美國綜合代碼），
    資料庫存交易所代碼（`QQQ UQ`）：美國的交易所代碼都當 `US` 比對。"""
    product = terms(underlyings=("QQQ UQ", "NVDA UW", None, None, None))

    risk = assess_with(product, vols(QQQ_UQ="20", NVDA_UW="40"), etfs=["QQQ US Equity"])

    assert risk.detail is not None
    assert (risk.detail.underlying_type, risk.detail.underlying_type_score) == ("ETF", 3)
    assert "UL_1 是 ETF，3 分" in risk.describe()
    assert risk.total == Decimal("3.46"), "2 + 0.2 + (3 + 2 + 2 + 3) ÷ 10 + 0.26"


@pytest.mark.parametrize(
    ("code", "listed", "is_etf"),
    [
        ("SPY UP", "SPY US Equity", True),
        ("DRAM UF", "DRAM US Equity", True),
        ("spy up", "SPY US", True),
        ("SPY US", "SPY UP Equity", True),
        ("1321 JT", "1321 JT Equity", True),
        ("1321 HK", "1321 JT Equity", False),
        ("SPY JT", "SPY US Equity", False),
        ("SPYG UP", "SPY US Equity", False),
    ],
)
def test_etf_codes_match_across_us_exchanges_only(code, listed, is_etf) -> None:
    risk = assess_with(
        terms(underlyings=(code, None, None, None, None)), vols(**{code.replace(" ", "_"): "20"}), etfs=[listed]
    )

    assert risk.detail is not None and risk.detail.underlying_type == ("ETF" if is_etf else "個股")


def test_stored_details_from_before_the_etf_list_still_describe_the_type() -> None:
    """#252 以前存的明細沒有標的種類：讀回來照分數寫（4 個股、3 指數）；之後存的照存的寫。"""
    risk = assess_with(terms())
    assert risk.detail is not None
    stored = _detail_json(risk.detail)
    old = {key: value for key, value in stored.items() if key != "underlying_type"}

    assert _detail(stored) == risk.detail
    assert "UL_1 是個股，4 分" in RiskLevel(DONE, "RR4", "", TRADE, risk.total, _detail(old)).describe()


def test_the_guarantor_rating_wins_and_the_best_agency_counts() -> None:
    """CITI 有保證機構：用保證機構（BBB+／A3／A），最好的是 A3、A（4 分），不用發行機構的 AA。"""
    risk = assess_with(terms(issuer="CITI"))

    assert risk.detail is not None
    assert (risk.detail.rating_role, risk.detail.rating_score) == (GUARANTOR, 4)
    assert risk.detail.ratings == ((SP, "BBB+"), (MOODYS, "A3"), (FITCH, "A"))


def test_ratings_are_taken_on_the_trade_date() -> None:
    later = Rating("BARC", ISSUER, dt.date(2026, 9, 5), "AAA", None, None)
    rated = dataclasses.replace(RATINGS, ratings=(*RATINGS.ratings, later))

    before = assess_with(terms(), ratings=rated)
    sept_7 = dt.date(2026, 9, 7)
    after = assess_with(terms(trade_date=sept_7), vols(sept_7, **_SHEET), ratings=rated, dates=(sept_7,))

    assert before.detail is not None and before.detail.rating_score == 3
    assert after.detail is not None and after.detail.rating_score == 1


def test_the_price_file_is_the_trade_date_one_else_the_latest_before() -> None:
    sept_3, sept_8 = dt.date(2026, 9, 3), dt.date(2026, 9, 8)
    found = {**vols(sept_3, **_SHEET), **vols(sept_8, LITE_UW="1", NBIS_UW="1", MRVL_UW="1")}

    on_day = assess_with(terms(), {**found, **SHEET_VOLS}, dates=(sept_3, TRADE, sept_8))
    before = assess_with(terms(), found, dates=(sept_3, sept_8))

    assert on_day.price_date == TRADE
    assert before.price_date == sept_3 and before.level == "RR4", "取交易日以前最近的一份，不用之後的"


# ---------- 算不出來：回填與待算 ----------


def test_missing_data_before_the_cutover_is_backfilled_as_rr4() -> None:
    risk = assess_with(terms(trade_date=dt.date(2026, 10, 29)), {}, dates=())

    assert (risk.status, risk.level, risk.total, risk.detail) == (BACKFILLED, "RR4", None, None)
    assert risk.reason == "沒有取價日在交易日 2026-10-29 以前（含）的股價檔"
    assert risk.label == "RR4（回填）"
    assert risk.describe().startswith("風險等級：RR4（回填）\n原因：沒有取價日")


def test_missing_data_from_the_cutover_on_is_pending() -> None:
    found = vols(dt.date(2026, 10, 30), LITE_UW="30", NBIS_UW=None)

    risk = assess_with(
        terms(trade_date=dt.date(2026, 10, 30), tenor_months=None), found, dates=(dt.date(2026, 10, 30),)
    )

    assert (risk.status, risk.level, risk.price_date) == (PENDING, None, dt.date(2026, 10, 30))
    assert risk.reason == (
        "缺天期（Tenor (m)）；UL_2 NBIS UW：股價檔（取價日 2026-10-30）沒有 200 日波動率；"
        "UL_3 MRVL UW：股價檔（取價日 2026-10-30）沒有這個代碼"
    )
    assert risk.label == "待算"


def test_conflicting_prices_unknown_issuers_and_missing_ratings_are_named() -> None:
    conflict = {**SHEET_VOLS, (TRADE, "NBIS UW"): Volatility(None, conflict=True)}

    risk = assess_with(terms(issuer="HSBC", trade_date=LATER), conflict, dates=(TRADE,))
    unknown = assess_with(terms(issuer=""))

    assert risk.status == PENDING
    assert risk.reason == (
        "UL_2 NBIS UW：股價檔（取價日 2026-09-04）這個代碼有好幾列、值不同；評等表沒有 HSBC 的發行機構或保證機構"
    )
    assert (unknown.status, unknown.reason) == (BACKFILLED, "認不出上手，查不到評等")


def test_a_blank_first_underlying_is_missing_data() -> None:
    """標的種類照試算表只看 UL_1：空白時算缺資料，不改看下一檔。"""
    product = terms(underlyings=(None, "LITE UW", "NBIS UW", "MRVL UW", None))

    risk = assess_with(product, indices=["LITE UW"])
    pending = assess_with(dataclasses.replace(product, trade_date=LATER), vols(LATER, **_SHEET), dates=(LATER,))

    assert (risk.status, risk.level, risk.reason) == (BACKFILLED, "RR4", "缺 UL_1（標的種類看第一檔標的）")
    assert (pending.status, pending.reason) == (PENDING, "缺 UL_1（標的種類看第一檔標的）")


def test_no_trade_date_or_underlyings_is_pending() -> None:
    risk = assess_with(terms(trade_date=None, underlyings=(None,) * 5))

    assert (risk.status, risk.reason) == (PENDING, "缺交易日；缺標的")


# ---------- 設定檔 ----------


BUILTIN = Path(__file__).resolve().parents[1] / "config" / "risk_level.toml"


def test_the_index_and_etf_lists_are_read_from_the_config(tmp_path: Path) -> None:
    path = tmp_path / "risk_level.toml"
    path.write_text(
        'index_underlyings = ["SPX Index", " nky index "]\netf_underlyings = ["1321 JT Equity"]\n'
        'fallback_etf_underlyings = ["QQQ US Equity"]\n',
        encoding="utf-8",
    )

    assert load_risk_underlyings(path) == (
        frozenset({"SPX Index", "nky index"}),
        frozenset({"1321 JT Equity"}),
        frozenset({"QQQ US Equity"}),
    ), "照寫法，比對時才去類別字"

    for bad in (
        'index_underlyings = "SPX Index"\netf_underlyings = []\nfallback_etf_underlyings = []\n',
        "index_underlyings = []\netf_underlyings = []\n",
    ):
        path.write_text(bad, encoding="utf-8")
        with pytest.raises(IngestionError, match="_underlyings"):
            load_risk_underlyings(path)


def test_the_builtin_etf_list_matches_the_stored_underlying_codes() -> None:
    """抽查（#252）：白名單 ETF 工作表的寫法（`SPY US Equity`）要對得到資料庫存的代碼（交易所代碼，例 `SPY UP`）；
    白名單沒有的日本 ETF 與 UFO 在補充清單；個股不在清單上。指數本身不能連結，指數清單是空的。
    還沒匯入白名單時用的是備用清單加補充清單（#273）。"""
    indices, extra, fallback = load_risk_underlyings(BUILTIN)
    etfs = fallback | extra
    etf_codes = (
        "QQQ UQ",
        "SPY UP",
        "VOO UP",
        "IVV UP",
        "SMH UQ",
        "SOXX UQ",
        "GLD UP",
        "SLV UP",
        "XLK UP",
        "DRAM UF",
        "SCHD UP",
    )
    stocks = ("TSM UN", "NVDA UW", "7011 JT", "700 HK", "TSM US")

    assert indices == frozenset()
    assert len(etfs) > 1000
    assert all(len(underlying_key(code).split()) == 2 for code in etfs), "每一項都是「代碼 交易所」"
    for code in (*etf_codes, "1321 JT", "1306 JT", "UFO UQ", *stocks):
        risk = assess_with(
            terms(underlyings=(code, None, None, None, None)), vols(**{code.replace(" ", "_"): "20"}), etfs=etfs
        )
        assert risk.detail is not None and risk.detail.underlying_type == ("個股" if code in stocks else "ETF"), code


# ---------- 資料庫：跟著核對條件重算、鎖住後不變 ----------


def vol_price(code: str, volatility: float | None) -> tuple:
    """BBG_Data 的一列，200 日波動率可以指定。"""
    return (code, "虛構公司", 100.5, 99.25, 98.0, volatility, 12345.6)


def import_prices(pg, tmp_path, day: dt.date, prices, *, trades=()) -> None:
    path = tmp_path / f"d{day:%m%d}-{len(list(tmp_path.iterdir()))}.xlsx"
    import_daily(write_daily(path, list(trades), prices, price_date=dt.datetime.combine(day, dt.time())), pg)


def import_order(pg, tdcc=A, **changes) -> None:
    values = {"Upfront / NotePrice (%)": Decimal("97.85"), **changes}
    receipt = write_batch(pg, _drafts([order_row(tdcc, values)], CONFIG, pg), CONFIG, user="tester")
    assert receipt.lines[0].imported, receipt.lines[0].detail


def stored_row(pg, tdcc=A):
    return pg.execute(
        "SELECT status, risk_level, total_score, reason, price_date FROM workflow.risk_level WHERE tdcc = %s", (tdcc,)
    ).fetchone()


OCT_15 = dt.date(2026, 10, 15)  # order_synth 的交易日


@pytest.mark.db
def test_recalculation_stores_the_risk_level_and_rewrites_only_changes(pg, tmp_path) -> None:
    """AAA UW 20、BBB UN 40 → 平均 30（2 分）；6 個月、2 檔個股、Barclays：2 + 0.2 + 1.1 + 0.26 = 3.56 → RR4。"""
    import_prices(pg, tmp_path, OCT_15, [vol_price("AAA UW", 20), vol_price("BBB UN", 40)])
    import_order(pg)

    (line,) = recalculate(pg, CONFIG).lines

    assert line.risk is not None
    assert (line.risk.status, line.risk.level, line.risk.total) == (DONE, "RR4", Decimal("3.56"))
    assert stored_row(pg) == (DONE, "RR4", Decimal("3.56"), None, OCT_15)
    assert risk_level_of(pg, A) == line.risk, "讀回來的明細和算的一樣（#239 讀這個）"
    before = pg.execute("SELECT calculated_at FROM workflow.risk_level").fetchone()

    (again,) = recalculate(pg, CONFIG).lines

    assert again.risk == line.risk
    assert pg.execute("SELECT calculated_at FROM workflow.risk_level").fetchone() == before


@pytest.mark.db
def test_an_etf_list_change_reaches_unlocked_products_only(pg, tmp_path) -> None:
    """設定檔加了 ETF 清單（#252）：還沒鎖住的下一次重算改成 ETF 3 分（2 + 0.2 + 1 + 0.26 = 3.46），已鎖住的不變。"""
    other = "029199990005"
    import_prices(pg, tmp_path, OCT_15, [vol_price("AAA UW", 20), vol_price("BBB UN", 40)])
    import_order(pg)
    import_order(pg, other)
    recalculate(pg, CONFIG)
    pg.execute("UPDATE workflow.product SET locked_at = now() WHERE tdcc = %s", (other,))
    with_etfs = dataclasses.replace(CONFIG, etf_underlyings=frozenset({"AAA US Equity"}))

    recalculate(pg, with_etfs)

    risk = risk_level_of(pg, A)
    assert risk is not None and risk.detail is not None
    assert (risk.detail.underlying_type, risk.total) == ("ETF", Decimal("3.46"))
    assert "UL_1 是 ETF，3 分" in risk.describe()
    assert stored_row(pg, other)[2] == Decimal("3.56"), "已鎖住的凍結"


@pytest.mark.db
def test_pending_products_are_calculated_once_the_prices_arrive(pg, tmp_path) -> None:
    later = {"Trade date": LATER, "Strike date": LATER}
    import_order(pg, **later)

    (first,) = recalculate(pg, CONFIG).lines
    import_prices(pg, tmp_path, LATER, [vol_price("AAA UW", 20), vol_price("BBB UN", 40)])
    (second,) = recalculate(pg, CONFIG).lines

    assert first.risk is not None and (first.risk.status, first.risk.level) == (PENDING, None)
    assert first.risk.reason == "沒有取價日在交易日 2026-11-02 以前（含）的股價檔"
    assert second.risk is not None and second.risk.total == Decimal("3.56")
    assert stored_row(pg)[:2] == (DONE, "RR4")


@pytest.mark.db
def test_old_products_without_data_are_backfilled(pg, tmp_path) -> None:
    import_order(pg)  # 交易日 2026-10-15，還沒有任何股價檔

    (line,) = recalculate(pg, CONFIG).lines

    assert stored_row(pg) == (BACKFILLED, "RR4", None, "沒有取價日在交易日 2026-10-15 以前（含）的股價檔", None)
    assert line.risk is not None and line.risk.label == "RR4（回填）"


@pytest.mark.db
def test_locked_products_keep_their_risk_level(pg, tmp_path) -> None:
    import_prices(pg, tmp_path, OCT_15, [vol_price("AAA UW", 20), vol_price("BBB UN", 40)])
    import_order(pg)
    recalculate(pg, CONFIG)
    pg.execute("UPDATE workflow.product SET locked_at = now() WHERE tdcc = %s", (A,))
    import_prices(pg, tmp_path, OCT_15, [vol_price("AAA UW", 90), vol_price("BBB UN", 90)])  # 同一天整份取代

    receipt = recalculate(pg, CONFIG)

    assert receipt.lines == ()
    assert stored_row(pg) == (DONE, "RR4", Decimal("3.56"), None, OCT_15)


@pytest.mark.db
def test_locked_products_without_a_risk_level_get_one_once_then_freeze(pg, tmp_path) -> None:
    """本功能上線前就鎖住的商品：下一次重算補算一次（不在核對條件的結果裡），之後資料變了也不再改。"""
    import_prices(pg, tmp_path, OCT_15, [vol_price("AAA UW", 20), vol_price("BBB UN", 40)])
    import_order(pg)
    pg.execute("UPDATE workflow.product SET locked_at = now() WHERE tdcc = %s", (A,))

    first = recalculate(pg, CONFIG)
    assert first.lines == ()
    assert stored_row(pg) == (DONE, "RR4", Decimal("3.56"), None, OCT_15)
    before = pg.execute("SELECT calculated_at FROM workflow.risk_level").fetchone()

    import_prices(pg, tmp_path, OCT_15, [vol_price("AAA UW", 5), vol_price("BBB UN", 5)])  # 同一天整份取代
    recalculate(pg, CONFIG)
    recalculate(pg, CONFIG, tdccs=[A])

    assert stored_row(pg) == (DONE, "RR4", Decimal("3.56"), None, OCT_15), "有了之後凍結"
    assert pg.execute("SELECT calculated_at FROM workflow.risk_level").fetchone() == before


@pytest.mark.db
def test_only_the_named_locked_products_are_filled_when_recalculating_some(pg, tmp_path) -> None:
    import_order(pg)
    import_order(pg, "029199990005")
    pg.execute("UPDATE workflow.product SET locked_at = now()")

    recalculate(pg, CONFIG, tdccs=["029199990005"])

    assert stored_row(pg) is None and stored_row(pg, "029199990005")[:2] == (BACKFILLED, "RR4")


@pytest.mark.db
def test_maintenance_edits_to_underlyings_recalculate_the_risk_level(pg, tmp_path) -> None:
    import_prices(pg, tmp_path, OCT_15, [vol_price("AAA UW", 20), vol_price("BBB UN", 40), vol_price("CCC UN", 90)])
    import_order(pg)
    recalculate(pg, CONFIG)
    draft = open_terms(pg, A)
    draft.values["BBG Code 3"] = "CCC UN"

    receipt = write(pg, draft, CONFIG, user="operator1")

    assert receipt.calc is not None and receipt.calc.risk is not None
    # 平均 50（4 分）、3 檔（3 分）：2 + 0.2 + 1.4 + 0.26 = 3.86 → RR4
    assert stored_row(pg)[:3] == (DONE, "RR4", Decimal("3.86"))


@pytest.mark.db
def test_trade_rows_do_not_block_the_risk_level(pg, tmp_path) -> None:
    """核對條件還在等交易明細、等股價時，風險等級照樣算（兩者各自的資料）。"""
    other = [trade("037199990002", "Amy", "台北", 1)]
    import_prices(pg, tmp_path, OCT_15, [vol_price("AAA UW", 20), vol_price("BBB UN", 40)], trades=other)
    import_order(pg)

    (line,) = recalculate(pg, CONFIG).lines

    assert line.status == WAIT_TRADES and line.risk is not None and line.risk.status == DONE
