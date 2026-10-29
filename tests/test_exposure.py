"""標的曝險分析（#345、#347、#352）：分類規則、依分公司與理專加總、匯出的 Excel。資料庫測試對真的 PostgreSQL；資料全部虛構。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest

from db_synth import inventory, seed_products
from sp_workflow.exposure import (
    CROSSED,
    DELIVER,
    FIXED,
    MANUAL,
    NEAR,
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
    ("barrier", "close", "ki_text", "category"),
    [
        ("NONE", "72", "-", DELIVER),  # 沒有 KI，低於執行價就接股
        ("EKI", "60", "EKI", DELIVER),  # 破下限價
        ("EKI", "70", "EKI", WATCH),  # 低於執行價、還在下限價之上
        ("AKI", "70", "AKI hit 2026-10-01 UL_1 AAA UW", DELIVER),  # 比價已記 AKI 觸及
        ("AKI", "70", "AKI", WATCH),
        ("MKI", "60", "MKI (manual)", MANUAL),
        ("NONE", "80", "-", NEAR),  # 等於執行價不接股，但距執行價 10% 以內
        ("NONE", "88", "-", NEAR),  # 剛好高 10% 還算以內
        ("NONE", "88.1", "-", SAFE),
    ],
)
def test_the_worst_underlying_decides(barrier: str, close: str, ki_text: str, category: str) -> None:
    result = assess(position(barrier, leg(1, "AAA UW", close), leg(2, "BBB UN", "95"), ki_text=ki_text))

    assert result.category == category
    assert result.worst is not None and result.worst.code == "AAA UW"
    assert result.notional == D(100000)


@pytest.mark.parametrize(
    ("barrier", "ki_text", "close", "basis", "group"),
    [
        ("EKI", "EKI", "72", "下限價", ">10%"),  # 72 ÷ 65 − 1 = 10.8%，不是距執行價的 −10%
        ("EKI", "EKI", "70", "下限價", "3–10%"),
        ("EKI", "EKI", "66", "下限價", "<3%"),
        ("EKI", "EKI", "60", "下限價", CROSSED),
        ("AKI", "AKI", "70", "下限價", "3–10%"),  # 還沒觸及，下限價還要看
        ("AKI", "AKI hit 2026-10-01 UL_1 AAA UW", "70", "執行價", CROSSED),  # 已觸及，只剩執行價
        ("MKI", "MKI (manual)", "66", "下限價", "<3%"),
        ("NONE", "-", "79", "執行價", CROSSED),
        ("NONE", "-", "82", "執行價", "<3%"),
        ("NONE", "-", "82.4", "執行價", "3–10%"),  # 剛好 3%
        ("NONE", "-", "88", "執行價", "3–10%"),  # 剛好 10%
        ("NONE", "-", "88.1", "執行價", ">10%"),
    ],
)
def test_buffer_is_the_distance_to_the_trigger(barrier: str, ki_text: str, close: str, basis: str, group: str) -> None:
    result = assess(position(barrier, leg(1, "AAA UW", close), ki_text=ki_text))

    assert (result.buffer_basis, result.buffer_group) == (basis, group)
    assert result.buffer == D(close) / (65 if basis == "下限價" else 80) - 1


def test_distances_to_strike_and_ki() -> None:
    worst = assess(position("EKI", leg(1, "AAA UW", "72"), ki_text="EKI")).worst

    assert worst is not None and (worst.to_strike, worst.to_ki) == (D("-0.1"), D(72) / 65 - 1)


def test_products_past_the_final_valuation_are_fixed() -> None:
    p = position("NONE", leg(1, "AAA UW", "72"))

    fixed = assess(p, UNTIL + dt.timedelta(days=1))
    assert (fixed.category, fixed.buffer_group) == (FIXED, FIXED), "不用最新收盤價判斷會不會接股"
    assert fixed.worst is not None and fixed.worst.code == "AAA UW"
    assert assess(p, UNTIL).category == DELIVER, "最終比價日當天還用最新收盤價判斷"


def test_worst_is_lowest_performance_not_lowest_price() -> None:
    result = assess(position("NONE", leg(1, "AAA UW", "90"), leg(2, "BBB UN", "150", initial="200", strike="160")))

    assert result.worst is not None and result.worst.code == "BBB UN", "BBB −25% 比 AAA −10% 差"
    assert result.category == DELIVER


def test_missing_prices_cannot_be_assessed() -> None:
    assert assess(position("NONE")).category == UNKNOWN
    missing = assess(position("NONE", leg(1, "AAA UW", None), leg(2, "BBB UN", "95")))
    assert (missing.category, missing.reason) == (UNKNOWN, "缺進場價、執行價或收盤價：AAA UW")
    assert (missing.buffer, missing.buffer_group) == (None, None)


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
def test_products_near_the_strike_are_on_the_notice_list(seeded, tmp_path: Path) -> None:
    seed(seeded, OTHER_EXCHANGE, ["AAA UQ", "BBB UN"])
    closes(seeded, {"AAA UQ": "85"})
    path = tmp_path / "曝險.xlsx"

    receipt = export_exposure(seeded, ["AAA UQ"], UNTIL, path, today=DAY)

    assert receipt.counts[NEAR] == 1
    [row] = sheet_rows(path, "通知明細")
    assert (row["分類"], row["緩衝分組"], row["緩衝基準"]) == (NEAR, "3–10%", "執行價")
    assert row["緩衝"] == row["距執行價"] == pytest.approx(85 / 80 - 1)


@pytest.mark.db
def test_products_past_the_final_valuation_are_listed_apart(seeded, tmp_path: Path) -> None:
    seed(seeded, OTHER_EXCHANGE, ["AAA UW", "BBB UN"], final=DAY - dt.timedelta(days=3))  # 等股價
    path = tmp_path / "曝險.xlsx"

    receipt = export_exposure(seeded, ["AAA UW"], UNTIL, path, today=DAY)

    assert (receipt.counts[FIXED], receipt.counts[DELIVER]) == (1, 2), "AAA 72 也不算會接股"
    every = {row["TDCC"]: row for row in sheet_rows(path, "全部商品")}
    assert (every[OTHER_EXCHANGE]["分類"], every[OTHER_EXCHANGE]["緩衝分組"]) == (FIXED, FIXED)
    assert every[OTHER_EXCHANGE]["剩餘天數"] == -3
    assert OTHER_EXCHANGE not in {row["TDCC"] for row in sheet_rows(path, "通知明細")}
    overview = dict(openpyxl.load_workbook(path)["總覽"].iter_rows(min_row=2, max_col=2, values_only=True))
    assert overview[FIXED] == "1 檔，面額 USD 1"


@pytest.mark.db
def test_export_sums_by_branch_and_by_fc(seeded, tmp_path: Path) -> None:
    path = tmp_path / "曝險.xlsx"

    receipt = export_exposure(seeded, ["AAA UW"], UNTIL, path, today=DAY)

    assert receipt.count == 4, "截止日之後、已出場、沒連結的不列"
    assert (receipt.counts[DELIVER], receipt.counts[WATCH], receipt.counts[UNKNOWN]) == (2, 1, 1)
    book = openpyxl.load_workbook(path)
    assert book.sheetnames == ["總覽", "依分公司", "依理專", "通知明細", "全部商品"]
    for sheet in book:
        texts = {str(cell.value) for row in sheet.iter_rows() for cell in row if cell.value is not None}
        assert not [t for t in texts if any(word in t for word in ("估損", "配息", "淨損益"))], "只呈現現況（#352）"

    fcs = sheet_rows(path, "依理專")
    assert [row["理專"] for row in fcs] == ["Amy", "Cara", "Ben", "-"], "依會接股面額，再看 KI 未破下限面額"
    fc = {row["理專"]: row for row in fcs}
    assert fc["Amy"]["會接股檔數"] == 1 and fc["Amy"]["會接股面額(USD)"] == 150000, "跨分公司合併、一檔只算一次"
    assert fc["Amy"]["會接股面額：選定標的造成(USD)"] == 150000
    assert fc["Amy"]["服務的分公司"] == "台中、台北"
    assert (fc["Ben"]["會接股面額(USD)"], fc["Ben"]["KI 未破下限面額(USD)"]) == (30000, 200000)
    assert fc["Cara"]["會接股面額(USD)"] == fc["Cara"]["會接股面額：其他標的造成(USD)"] == pytest.approx(10000000 / 150)
    assert fc["Cara"]["會接股面額：選定標的造成(USD)"] == 0, "最差是 DDD，不是選定標的造成"

    branches = sheet_rows(path, "依分公司")
    assert [row["分公司"] for row in branches] == ["台北", "台中", "新竹"]
    branch = {row["分公司"]: row for row in branches}
    assert branch["台中"]["會接股面額(USD)"] == 80000 and branch["台中"]["理專"] == "Amy、Ben"
    assert branch["台中"]["連結檔數"] == 2

    detail = sheet_rows(path, "通知明細")
    assert [(r["TDCC"], r["分類"], r["理專"]) for r in detail] == [
        (A, DELIVER, "Amy"),  # 剩餘天數都是 23，緩衝 −10% 最小
        (A, DELIVER, "Amy"),
        (A, DELIVER, "Ben"),
        (C, DELIVER, "Cara"),  # DDD 距下限價 −7.7%
        (B, WATCH, "Ben"),  # AAA 距下限價 +10.8%
    ]
    assert detail[3]["最差標的"] == "DDD JT" and detail[3]["最差是選定標的"] == "否"
    assert (detail[0]["剩餘天數"], detail[0]["緩衝分組"], detail[0]["緩衝基準"]) == (23, CROSSED, "執行價")
    assert (detail[0]["緩衝"], detail[0]["最差表現"], detail[0]["執行價(%)"]) == (pytest.approx(-0.1), -0.28, 0.8)
    assert (detail[4]["緩衝基準"], detail[4]["緩衝分組"]) == ("下限價", ">10%"), "B 是 EKI，看距下限價"
    assert detail[4]["緩衝"] == detail[4]["距下限價"] == pytest.approx(72 / 65 - 1)

    every = {row["TDCC"]: row for row in sheet_rows(path, "全部商品")}
    assert every[A]["各標的表現"] == "AAA UW -28.0% / BBB UN -5.0%"
    assert (every[PENDING]["分類"], every[PENDING]["說明"]) == (UNKNOWN, "核對條件待確認：幣別不同")
    rows = list(book["總覽"].iter_rows(min_row=2, max_col=2, values_only=True))
    assert rows[0] == ("判斷基準", "以 10/28 收盤價判斷"), "最上面寫用哪天的收盤價"
    overview = dict(rows)
    assert overview["選定標的 AAA UW"] == "最新收盤 72（2026-10-28）"
    assert overview[f"{DELIVER}｜選定標的造成"] == "1 檔，面額 USD 180,000"
    assert overview[f"{DELIVER}｜其他標的造成"] == "1 檔，面額 USD 66,667"
    assert overview[f"{WATCH}｜選定標的造成"] == "1 檔，面額 USD 200,000"


@pytest.mark.db
def test_every_analysis_has_a_chart(seeded, tmp_path: Path) -> None:
    path = tmp_path / "曝險.xlsx"

    export_exposure(seeded, ["AAA UW"], UNTIL, path, today=DAY)

    book = openpyxl.load_workbook(path)
    assert [len(book[title]._charts) for title in book.sheetnames] == [3, 2, 2, 0, 0]
    data = [row for row in book["總覽"].iter_rows(min_col=4, max_col=6, values_only=True) if any(row)]
    assert data[:4] == [
        ("分類", "面額：選定標的造成(USD)", "面額：其他標的造成(USD)"),
        (DELIVER, 180000, pytest.approx(10000000 / 150)),
        (WATCH, 200000, 0),
        (NEAR, 0, 0),
    ]
    assert data[4:10] == [
        ("緩衝分組（距下限價或執行價）", "檔數", "面額(USD)"),
        (CROSSED, 2, pytest.approx(180000 + 10000000 / 150)),  # PENDING 沒分類，不算
        ("<3%", 0, 0),
        ("3–10%", 0, 0),
        (">10%", 1, 200000),
        (FIXED, 0, 0),
    ]
    assert data[10:] == [
        ("會接股的最差標的", "會接股面額(USD)", None),
        ("AAA UW", 180000, None),
        ("DDD JT", pytest.approx(10000000 / 150), None),
    ]
    charts = [chart for title in book.sheetnames for chart in book[title]._charts]
    assert all(c.title.overlay is False and c.legend.position == "b" for c in charts), "標題不蓋到圖（#350）"
    assert all(c.y_axis.crosses == "max" for c in charts if c.type == "bar"), "橫條圖數值軸在下面"
    assert all((c.x_axis.delete, c.y_axis.delete) == (False, False) for c in charts), "Excel 才會顯示座標軸"


@pytest.mark.db
def test_products_without_trade_rows_use_the_branch_allocation(seeded, tmp_path: Path) -> None:
    seeded.execute("DELETE FROM workflow.trade_row WHERE tdcc = %s", (B,))
    path = tmp_path / "曝險.xlsx"

    export_exposure(seeded, ["AAA UW"], UNTIL, path, today=DAY)

    rows = [r for r in sheet_rows(path, "通知明細") if r["TDCC"] == B]
    assert [(r["分公司"], r["理專"], r["金額"]) for r in rows] == [("台北", "-", 1)]


@pytest.mark.db
def test_no_delivery_chart_when_nothing_delivers(seeded, tmp_path: Path) -> None:
    path = tmp_path / "曝險.xlsx"

    export_exposure(seeded, ["CCC UN"], UNTIL, path, today=DAY)

    book = openpyxl.load_workbook(path)
    assert [len(book[title]._charts) for title in book.sheetnames] == [3, 1, 1, 0, 0], "只剩面額組成"
