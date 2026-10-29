"""標的曝險分析（#345）：分類規則、依分公司與理專加總、匯出的 Excel。資料庫測試對真的 PostgreSQL；資料全部虛構。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest

from db_synth import inventory, seed_products
from sp_workflow.exposure import (
    DELIVER,
    MANUAL,
    SAFE,
    UNKNOWN,
    WATCH,
    Holding,
    Leg,
    Position,
    assess,
    export_exposure,
    export_name,
    load_choices,
)
from sp_workflow.lifecycle import KO, AutoEvent, replace_auto_events
from sp_workflow.tabs.exposure import one_month_later

D = Decimal
DAY = dt.date(2026, 10, 28)
UNTIL = dt.date(2026, 11, 30)


def leg(seq: int, code: str, close: str | None, *, initial: str = "100", strike: str = "80", ki: str | None = "65"):
    return Leg(
        seq, code, D(initial), D(strike), None if ki is None else D(ki), None if close is None else D(close), DAY
    )


def position(barrier: str | None, *legs: Leg, ki_text: str = "", terms: str | None = "完成") -> Position:
    holdings = (Holding("台北", "Amy", D(100000)),)
    return Position("147199990001", "FCN", "XBANK", "USD", UNTIL, barrier, ki_text, terms, None, legs, holdings, D(1))


@pytest.mark.parametrize(
    ("barrier", "close", "ki_text", "category", "loss"),
    [
        ("NONE", "72", "-", DELIVER, D("0.1")),  # 沒有 KI，低於執行價就接股：1 − 72 ÷ 80
        ("EKI", "60", "EKI", DELIVER, D("0.25")),  # 破下限價
        ("EKI", "70", "EKI", WATCH, D(0)),  # 低於執行價、還在下限價之上
        ("AKI", "70", "AKI hit 2026-10-01 UL_1 AAA UW", DELIVER, D("0.125")),  # 比價已記 AKI 觸及
        ("AKI", "70", "AKI", WATCH, D(0)),
        ("MKI", "60", "MKI (manual)", MANUAL, D(0)),
        ("NONE", "80", "-", SAFE, D(0)),  # 等於執行價不接股
    ],
)
def test_the_worst_underlying_decides(barrier: str, close: str, ki_text: str, category: str, loss: Decimal) -> None:
    result = assess(position(barrier, leg(1, "AAA UW", close), leg(2, "BBB UN", "95"), ki_text=ki_text))

    assert (result.category, result.loss_ratio) == (category, loss)
    assert result.worst is not None and result.worst.code == "AAA UW"
    assert result.notional == D(100000)


def test_worst_is_lowest_performance_not_lowest_price() -> None:
    result = assess(position("NONE", leg(1, "AAA UW", "90"), leg(2, "BBB UN", "150", initial="200", strike="160")))

    assert result.worst is not None and result.worst.code == "BBB UN", "BBB −25% 比 AAA −10% 差"
    assert result.category == DELIVER


def test_missing_prices_cannot_be_assessed() -> None:
    assert assess(position("NONE")).category == UNKNOWN
    missing = assess(position("NONE", leg(1, "AAA UW", None), leg(2, "BBB UN", "95")))
    assert (missing.category, missing.reason) == (UNKNOWN, "缺進場價、執行價或收盤價：AAA UW")


@pytest.mark.parametrize(
    ("p", "reason"),
    [
        (position("NONE", leg(1, "AAA UW", "60"), terms="待確認"), "核對條件待確認"),  # 價格算得出來也不分類
        (position("NONE", leg(1, "AAA UW", "60"), terms=None), "核對條件還沒算"),
        (position(None, leg(1, "AAA UW", "60")), "沒有 KI 型態"),
        (position("EKI", leg(1, "AAA UW", "60", ki=None)), "EKI 沒有下限價：AAA UW"),
    ],
)
def test_incomplete_terms_cannot_be_assessed(p: Position, reason: str) -> None:
    assert (assess(p).category, assess(p).reason) == (UNKNOWN, reason)


def test_mki_is_listed_for_manual_review_even_without_prices() -> None:
    assert assess(position("MKI", leg(1, "AAA UW", None))).category == MANUAL


def test_one_month_later_clips_to_the_month_end() -> None:
    assert one_month_later(dt.date(2026, 10, 29)) == dt.date(2026, 11, 29)
    assert one_month_later(dt.date(2027, 1, 31)) == dt.date(2027, 2, 28)
    assert one_month_later(dt.date(2026, 12, 15)) == dt.date(2027, 1, 15)


def test_default_name_lists_the_tickers() -> None:
    name = export_name(["GOOG UW", "GOOG UQ", "GOOGL UW"], dt.datetime(2026, 10, 30, 9, 5))
    assert name == "標的曝險分析_GOOG_GOOGL_20261030_0905.xlsx"


# ---------------------------------------------------------------- 資料庫

A, B, C, LATE, GONE, OTHER, PENDING, OTHER_EXCHANGE = (f"14719999000{n}" for n in range(1, 9))


def seed(pg, tdcc: str, codes: list[str], *, barrier: str = "NONE", final=dt.date(2026, 11, 20), currency="USD"):
    seed_products(pg, [inventory(tdcc, [("台北", 1)], UL_1=codes[0], UL_2=codes[1], 承作幣別=currency)])
    pg.execute(
        "UPDATE workflow.product SET barrier_type = %s, final_valuation_date = %s, strike_date = '2026-05-20'"
        " WHERE tdcc = %s",
        (barrier, final, tdcc),
    )
    pg.execute("INSERT INTO workflow.check_terms (tdcc, status) VALUES (%s, '完成')", (tdcc,))
    for seq, code in enumerate(codes, start=1):
        pg.execute(
            "INSERT INTO workflow.underlying_price (tdcc, seq, code, initial_price, strike_price, ki_price, ko_price)"
            " VALUES (%s, %s, %s, 100, 80, %s, 100)",
            (tdcc, seq, code, None if barrier == "NONE" else 65),
        )


def trades(pg, tdcc: str, rows: list[tuple[str, str, int]]) -> None:
    (run_id,) = pg.execute(
        "INSERT INTO workflow.import_run (daily_file_name, daily_sha256, summary) VALUES ('每日更新.xlsx', 'x', '{}')"
        " RETURNING run_id"
    ).fetchone()
    for seq, (branch, fc, amount) in enumerate(rows, start=1):
        pg.execute(
            "INSERT INTO workflow.trade_row (tdcc, row_seq, trade_date, product, currency, amount, fc_name, branch,"
            " first_run_id) VALUES (%s, %s, '2026-05-20', 'FCN', 'USD', %s, %s, %s, %s)",
            (tdcc, seq, amount, fc, branch, run_id),
        )


def closes(pg, prices: dict[str, str]) -> None:
    (import_id,) = pg.execute(
        "INSERT INTO workflow.close_price_import (source, file_name, file_sha256, imported_by, summary)"
        " VALUES ('歷史收盤價', 'HP.xlsx', 'x', 'tester', '{}') RETURNING import_id"
    ).fetchone()
    for code, close in prices.items():
        for day, value in ((DAY - dt.timedelta(days=1), "100"), (DAY, close)):
            pg.execute(
                "INSERT INTO workflow.close_price (close_date, code, close, import_id) VALUES (%s, %s, %s, %s)",
                (day, code, value, import_id),
            )


def sheet_rows(path: Path, title: str) -> list[dict[str, object]]:
    header, *rows = openpyxl.load_workbook(path)[title].iter_rows(values_only=True)
    return [dict(zip(header, row, strict=True)) for row in rows]


@pytest.fixture
def seeded(pg):
    seed(pg, A, ["AAA UW", "BBB UN"])  # 沒有 KI：AAA −28% 低於執行價 → 會接股
    trades(pg, A, [("台北", "Amy", 100000), ("台中", "Amy", 50000), ("台中", "Ben", 30000)])
    seed(pg, B, ["CCC UN", "AAA UW"], barrier="EKI")  # EKI：AAA −28% 還在下限價之上 → 觀察
    trades(pg, B, [("台中", "Ben", 200000)])
    seed(pg, C, ["AAA UW", "DDD JT"], barrier="EKI", currency="JPY")  # DDD −40% 破下限 → 會接股，最差不是選定標的
    trades(pg, C, [("新竹", "Cara", 10000000)])
    seed(pg, LATE, ["AAA UW", "BBB UN"], final=dt.date(2026, 12, 31))  # 最終比價日晚於截止日
    seed(pg, GONE, ["AAA UW", "BBB UN"])  # 已出場
    replace_auto_events(pg, GONE, [AutoEvent(KO, dt.date(2026, 10, 1))], user="tester")
    seed(pg, OTHER, ["BBB UN", "CCC UN"])  # 沒有連結 AAA
    seed(pg, PENDING, ["AAA UW", "BBB UN"])  # 核對條件待確認：價格有，但不分類
    pg.execute("UPDATE workflow.check_terms SET status = '待確認', reason = '幣別不同' WHERE tdcc = %s", (PENDING,))
    closes(pg, {"AAA UW": "72", "BBB UN": "95", "CCC UN": "110", "DDD JT": "60", "USDJPY": "150"})
    return pg


@pytest.mark.db
def test_choices_merge_exchange_codes_by_ticker(seeded) -> None:
    seed(seeded, OTHER_EXCHANGE, ["AAA UQ", "BBB UN"])
    choices = {c.ticker: c for c in load_choices(seeded)}

    assert sorted(choices) == ["AAA", "BBB", "CCC", "DDD"]
    assert choices["AAA"].codes == ("AAA UW", "AAA UQ"), "連結多的代碼在前"
    assert choices["AAA"].products == 6, "兩個代碼的在庫檔數合計，已出場的不算"
    assert (choices["AAA"].close_date, choices["AAA"].close) == (DAY, D(72)), "收盤價取連結最多的代碼"


@pytest.mark.db
def test_every_exchange_code_of_a_ticker_is_analysed(seeded, tmp_path: Path) -> None:
    seed(seeded, OTHER_EXCHANGE, ["AAA UQ", "BBB UN"])
    closes(seeded, {"AAA UQ": "72"})
    path = tmp_path / "曝險.xlsx"

    export_exposure(seeded, ["AAA UW", "AAA UQ"], UNTIL, path, today=DAY)

    every = {row["TDCC"]: row for row in sheet_rows(path, "全部商品")}
    assert (every[OTHER_EXCHANGE]["分類"], every[OTHER_EXCHANGE]["最差是選定標的"]) == (DELIVER, "是")
    branches = {row["分公司"]: row for row in sheet_rows(path, "依分公司")}
    assert branches["台北"]["會接股檔數"] == 2, "AAA UW 的 A 與 AAA UQ 的那一檔"


@pytest.mark.db
def test_export_sums_by_branch_and_by_fc(seeded, tmp_path: Path) -> None:
    path = tmp_path / "曝險.xlsx"

    receipt = export_exposure(seeded, ["AAA UW"], UNTIL, path, today=DAY)

    assert receipt.count == 4, "截止日之後、已出場、沒連結的不列"
    assert (receipt.counts[DELIVER], receipt.counts[WATCH], receipt.counts[UNKNOWN]) == (2, 1, 1)
    assert openpyxl.load_workbook(path).sheetnames == ["總覽", "依分公司", "依理專", "通知明細", "全部商品"]

    fcs = {row["理專"]: row for row in sheet_rows(path, "依理專")}
    assert fcs["Amy"]["會接股檔數"] == 1 and fcs["Amy"]["會接股面額(USD)"] == 150000, "跨分公司合併、一檔只算一次"
    assert fcs["Amy"]["估損(USD)"] == pytest.approx(150000 * 0.1)
    assert fcs["Amy"]["服務的分公司"] == "台中、台北"
    assert (fcs["Ben"]["會接股面額(USD)"], fcs["Ben"]["觀察面額(USD)"]) == (30000, 200000)
    assert fcs["Cara"]["會接股面額(USD)"] == pytest.approx(10000000 / 150), "JPY 用 USDJPY 換算"
    assert fcs["Cara"]["估損(USD)"] == pytest.approx(10000000 / 150 * 0.25)

    branches = {row["分公司"]: row for row in sheet_rows(path, "依分公司")}
    assert branches["台中"]["會接股面額(USD)"] == 80000 and branches["台中"]["理專"] == "Amy、Ben"
    assert branches["台中"]["連結檔數"] == 2

    detail = sheet_rows(path, "通知明細")
    assert [(r["TDCC"], r["分類"], r["理專"]) for r in detail] == [
        (A, DELIVER, "Amy"),
        (A, DELIVER, "Amy"),
        (A, DELIVER, "Ben"),
        (B, WATCH, "Ben"),
        (C, DELIVER, "Cara"),
    ]
    assert detail[4]["最差標的"] == "DDD JT" and detail[4]["最差是選定標的"] == "否"
    assert detail[0]["最差是選定標的"] == "是" and detail[0]["估損"] == pytest.approx(10000)

    every = {row["TDCC"]: row for row in sheet_rows(path, "全部商品")}
    assert every[A]["各標的表現"] == "AAA UW -28.0% / BBB UN -5.0%"
    assert (every[PENDING]["分類"], every[PENDING]["說明"]) == (UNKNOWN, "核對條件待確認：幣別不同")
    overview = dict(openpyxl.load_workbook(path)["總覽"].iter_rows(min_row=2, values_only=True))
    assert overview["選定標的 AAA UW"] == "最新收盤 72（2026-10-28）"
    assert overview[DELIVER].startswith("2 檔，面額 USD 246,667")


@pytest.mark.db
def test_products_without_trade_rows_use_the_branch_allocation(seeded, tmp_path: Path) -> None:
    seeded.execute("DELETE FROM workflow.trade_row WHERE tdcc = %s", (B,))
    path = tmp_path / "曝險.xlsx"

    export_exposure(seeded, ["AAA UW"], UNTIL, path, today=DAY)

    rows = [r for r in sheet_rows(path, "通知明細") if r["TDCC"] == B]
    assert [(r["分公司"], r["理專"], r["金額"]) for r in rows] == [("台北", "-", 1)]
