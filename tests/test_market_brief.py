"""盤勢速報（#467）：熱圖的區間與月份、標的彙整、折 USD、匯出的分頁。資料庫測試對真的 PostgreSQL；資料全部虛構。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest

from sp_workflow.exposure import KI_BROKEN, Holding, Leg, Position, assess
from sp_workflow.lifecycle import KO, AutoEvent, replace_auto_events
from sp_workflow.market_brief import (
    ABOVE_STRIKE_BUCKET,
    MAX_MONTHS,
    Band,
    Brief,
    Exposure,
    Quote,
    brief_name,
    broken,
    distance_bucket,
    due_bucket,
    export_brief,
    heat_fill,
    heatmap,
    month_columns,
    months_before,
    nice_step,
    overview_row,
    plan_bands,
    ticker_rows,
    trailing_returns,
)
from sp_workflow.stock_splits import Split, SplitBook
from test_exposure import closes, seed, trades

D = Decimal
DAY = dt.date(2026, 10, 28)  # 同 test_exposure 的收盤日


def labels(bands: list[Band]) -> list[str]:
    return [b.label() for b in bands]


# ---------------------------------------------------------------- 熱圖版面


@pytest.mark.parametrize(("price", "step"), [("353.1", "20"), ("181.6", "10"), ("52.25", "2.5"), ("100", "5")])
def test_band_width_is_a_round_number_near_six_percent(price: str, step: str) -> None:
    assert nice_step(D(price)) == D(step)


def test_bands_are_round_and_the_close_is_on_an_edge() -> None:
    bands, line = plan_bands([D(90), D(95), D(110), D(130)], D(100))

    assert labels(bands) == [
        "130-135",
        "125-130",
        "120-125",
        "115-120",
        "110-115",
        "105-110",
        "100-105",
        "95-100",
        "90-95",
    ], "高價在上、寬 5"
    assert line == D(100)


def test_the_band_holding_the_close_is_split_at_the_close() -> None:
    bands, line = plan_bands([D(80), D("102.5")], D("102.5"))

    assert labels(bands) == ["102.50-105", "100-102.50", "95-100", "90-95", "85-90", "80-85"]
    assert line == D("102.5"), "線在 102.50-105 那格的下緣"


def test_far_strikes_fall_into_open_bands() -> None:
    bands, _ = plan_bands([D(10), D(100), D(400)], D(100))

    assert len(bands) == 2 + 7 + 1 + 7, "上下各 7 格，再外面併成以上、以下"
    assert bands[0].label() == "140 以上" and bands[-1].label() == "65 以下"
    assert bands[0].holds(D(400)) and bands[-1].holds(D(10)) and not bands[-1].holds(D(65))


def test_without_a_close_bands_center_on_the_median_and_no_line() -> None:
    bands, line = plan_bands([D(48), D(50), D(53)], None)

    assert line is None
    assert labels(bands) == ["52.50-55", "50-52.50", "47.50-50"]


def test_months_run_continuously_and_spill_into_later() -> None:
    assert month_columns([(2026, 11), (2027, 1)]) == ([(2026, 11), (2026, 12), (2027, 1)], False)
    months = [(2026, 11), (2028, 3)]
    columns, later = month_columns(months)
    assert later and len(columns) == MAX_MONTHS - 1 and columns[0] == (2026, 11)


def test_heat_fill_deepens_with_the_amount() -> None:
    assert heat_fill(D(0), D(10)) is None, "0 不填色"
    light = heat_fill(D(1), D(100))
    deep = heat_fill(D(100), D(100))
    assert light is not None and deep is not None
    assert deep.fgColor.rgb.endswith("E05A50")
    assert light.fgColor.rgb != deep.fgColor.rgb


# ---------------------------------------------------------------- 彙整


def leg(seq: int, code: str, close: str | None, *, strike: str = "80", ki: str | None = "65") -> Leg:
    return Leg(seq, code, D(100), D(strike), None if ki is None else D(ki), None if close is None else D(close))


def exposure(
    tdcc: str, *legs: Leg, usd: str = "1000000", final=dt.date(2026, 11, 20), barrier: str = "EKI"
) -> Exposure:
    p = Position(tdcc, "FCN", "XBANK", "USD", final, barrier, "", "完成", None, legs, (Holding("台北", "Amy", D(usd)),))
    return Exposure(assess(p, DAY), D(usd))


def test_ticker_rows_merge_exchange_codes_and_look_at_their_own_leg() -> None:
    exposures = [
        exposure("1", leg(1, "AAA UW", "72"), leg(2, "BBB UN", "60")),  # AAA 跌破K、BBB 跌破KI
        exposure("2", leg(1, "AAA UQ", "90"), usd="3000000"),  # 同一個代號、另一個交易所
        exposure("3", leg(1, "AAA UW", "90"), usd="0"),  # AAA UW 連結較多，昨收用它
    ]
    quotes = {"AAA UW": Quote(DAY, D(72), D(80)), "AAA UQ": Quote(DAY, D(90))}

    rows = {r.ticker: r for r in ticker_rows(exposures, quotes)}

    aaa = rows["AAA"]
    assert aaa.codes == ("AAA UW", "AAA UQ")
    assert (aaa.products, aaa.notional) == (3, D(4000000))
    assert (aaa.k_count, aaa.k_notional, aaa.ki_count) == (1, D(1000000), 0), "只看 AAA 本身，BBB 跌破KI 不算 AAA 的"
    assert aaa.quote == Quote(DAY, D(72), D(80)) and aaa.quote.change == D("-0.1")
    assert (rows["BBB"].ki_count, rows["BBB"].ki_notional) == (1, D(1000000))
    assert list(rows) == ["AAA", "BBB"], "依面額大到小"


def test_overview_rows_and_hotspot_buckets() -> None:
    near = exposure("1", leg(1, "AAA UW", "85"))
    broke_k = exposure("2", leg(1, "AAA UW", "70"))
    broke_ki = exposure("3", leg(1, "AAA UW", "50"))

    assert overview_row(near) == "距K 10% 內"
    assert overview_row(broke_k) == "跌破K（未破KI）"
    assert overview_row(broke_ki) == KI_BROKEN
    assert distance_bucket(broke_k) == "距K -10～-20%", "70 ÷ 80 − 1 = −12.5%"
    assert distance_bucket(broke_ki) == "距K -30% 以下"
    assert distance_bucket(near) == ABOVE_STRIKE_BUCKET
    assert due_bucket(DAY + dt.timedelta(days=30), DAY) == "1個月內"
    assert due_bucket(DAY + dt.timedelta(days=31), DAY) == "1–3個月"
    assert due_bucket(DAY + dt.timedelta(days=400), DAY) == "12個月以上"


def test_heatmap_puts_strikes_in_todays_price_basis() -> None:
    # 拆股 1 拆 2：原始收盤價 36，換回價格基準是 72；執行價 80 換到目前是 40
    e = exposure("1", leg(1, "AAA UW", "72"))
    later = exposure("2", leg(1, "AAA UW", "72"), final=dt.date(2027, 1, 5), usd="500000")
    past = exposure("3", leg(1, "AAA UW", "72"), final=DAY - dt.timedelta(days=1))  # 已比價，不畫
    rows = ticker_rows([e, later, past], {"AAA UW": Quote(DAY, D(36), D(40))})
    brief = Brief(DAY, (e, later, past), tuple(rows), {"AAA UW": D(36)})

    h = heatmap(brief, "AAA")

    assert h.line == D(36)
    assert h.months == [(2026, 11), (2026, 12), (2027, 1)]
    row = next(i for i, band in enumerate(h.bands) if band.holds(D(40)))
    assert h.cells == {(row, 0): D(1000000), (row, 2): D(500000)}
    assert h.bands[row].low > h.line, "執行價高於昨收，在線之上"


def test_mki_is_left_for_manual_review_not_judged_by_the_latest_close() -> None:
    mki = exposure("1", leg(1, "AAA UW", "50"), barrier="MKI")  # 收盤價低於下限價，但 MKI 系統不比
    pending = Exposure(
        assess(
            Position(
                "2",
                "FCN",
                "XBANK",
                "USD",
                dt.date(2026, 11, 20),
                "EKI",
                "",
                "待確認",
                None,
                (leg(1, "AAA UW", "50"),),
                (Holding("台北", "Amy", D(1)),),
            ),
            DAY,
        ),
        D(1),
    )

    assert overview_row(mki) == "MKI 人工確認"
    assert broken([mki, pending]) == [], "MKI 與無法計算都不列重災區"
    row = ticker_rows([mki, pending], {})[0]
    assert (row.products, row.k_count, row.ki_count) == (2, 0, 0), "檔數照算，跌破不用最新收盤價判斷"


def test_other_exchange_strikes_are_scaled_to_the_main_close() -> None:
    # 主代碼 AAA UW 昨收 90；另一個交易所 AAA GY 價格尺度不同（收盤 45、執行價 40，距執行價 +12.5%）
    main = exposure("1", leg(1, "AAA UW", "90"))
    other = exposure("2", leg(1, "AAA GY", "45", strike="40"))
    third = exposure("3", leg(1, "AAA UW", "90"))
    quotes = {"AAA UW": Quote(DAY, D(90)), "AAA GY": Quote(DAY, D(45))}
    brief = Brief(
        DAY, (main, other, third), tuple(ticker_rows([main, other, third], quotes)), {"AAA UW": D(90), "AAA GY": D(45)}
    )

    h = heatmap(brief, "AAA")

    strike_80 = next(i for i, band in enumerate(h.bands) if band.holds(D(80)))
    assert h.cells == {(strike_80, 0): D(3000000)}, "AAA GY 的執行價換成 40 × 90 ÷ 45 = 80，跟 AAA UW 同一格"


def test_heatmap_title_counts_only_what_is_drawn() -> None:
    drawn = exposure("1", leg(1, "AAA UW", "72"))
    past = exposure("2", leg(1, "AAA UW", "72"), final=DAY - dt.timedelta(days=1), usd="5000000")
    brief = Brief(DAY, (drawn, past), tuple(ticker_rows([drawn, past], {"AAA UW": Quote(DAY, D(72))})), {})

    h = heatmap(brief, "AAA")

    assert (h.products, h.notional, h.k_count, h.k_notional) == (1, D(1000000), 1, D(1000000))
    assert h.row.products == 2, "大跌標的的連結檔數仍含已比價"


def test_months_before_clips_to_the_month_end() -> None:
    assert months_before(dt.date(2026, 5, 31), 3) == dt.date(2026, 2, 28)
    assert months_before(dt.date(2026, 1, 15), 2) == dt.date(2025, 11, 15)


def test_trailing_returns_use_the_close_on_or_before_each_month_back() -> None:
    quote = Quote(dt.date(2026, 11, 5), D(90))
    history = [
        (dt.date(2026, 8, 5), D(60)),  # 3 個月前
        (dt.date(2026, 9, 30), D(100)),
        (dt.date(2026, 10, 2), D(120)),  # 10/5 之前最近一筆 → 近 1 月
    ]

    one, two, three = trailing_returns("AAA UW", quote, history, SplitBook())

    assert one == D("-0.25"), "10/5 之前最近一筆是 10/2 的 120"
    assert two is None, "9/5 之前最近一筆是 8/5，超過 14 天，不用"
    assert three == D("0.5")


def test_trailing_returns_convert_old_closes_across_a_split() -> None:
    # 10/1 起 1 拆 2：8/5 的 200 換到目前的基準是 100
    book = SplitBook([Split("AAA UW", dt.date(2026, 10, 1), 1, 2)])
    quote = Quote(dt.date(2026, 11, 5), D(90))

    *_, three = trailing_returns("AAA UW", quote, [(dt.date(2026, 8, 5), D(200))], book)

    assert three == D("-0.1")


def test_default_name() -> None:
    assert brief_name(dt.datetime(2026, 11, 6, 8, 30)) == "盤勢速報_20261106_0830.xlsx"


# ---------------------------------------------------------------- 資料庫

A, C, LATE, GONE, AUD = (f"14719998000{n}" for n in range(1, 6))


@pytest.fixture
def seeded(pg):
    seed(pg, A, ["AAA UW", "BBB UN"])  # 沒有 KI：AAA 72 < 80 → 跌破K
    trades(pg, A, [("台北", "Amy", 1000000)])
    seed(pg, C, ["AAA UW", "DDD JT"], barrier="EKI", currency="JPY")  # DDD 60 < 65 → 跌破KI
    trades(pg, C, [("新竹", "Cara", 100000000)])
    seed(pg, LATE, ["BBB UN", "CCC UN"], final=dt.date(2027, 3, 15))  # 高於K 10% 以上
    trades(pg, LATE, [("台中", "Ben", 2000000)])
    seed(pg, GONE, ["AAA UW", "BBB UN"])  # 已出場，不算
    replace_auto_events(pg, GONE, [AutoEvent(KO, dt.date(2026, 10, 1))], user="tester")
    closes(pg, {"AAA UW": "72", "BBB UN": "95", "CCC UN": "110", "DDD JT": "60", "JPYUSD": "0.0065"})
    return pg


def table(sheet, width: int) -> list[tuple[object, ...]]:
    """第 3 列起的表格（表頭 + 資料），只取前 `width` 欄。"""
    return [row[:width] for row in sheet.iter_rows(min_row=3, values_only=True)]


@pytest.mark.db
def test_export_writes_one_table_per_sheet(seeded, tmp_path: Path) -> None:
    path = tmp_path / "盤勢速報.xlsx"

    (import_id,) = seeded.execute("SELECT max(import_id) FROM workflow.close_price_import").fetchone()
    seeded.execute(  # 往回 1 個月是 9/28，沒有收盤價；往前最近一筆 9/25
        "INSERT INTO workflow.close_price (close_date, code, close, import_id) VALUES ('2026-09-25', 'AAA UW', 90, %s)",
        (import_id,),
    )

    receipt = export_brief(seeded, ["AAA", "不存在"], path, today=DAY)

    assert (receipt.products, receipt.broken, receipt.heatmaps) == (3, 2, ("AAA",))
    assert receipt.missing_fx == frozenset()
    book = openpyxl.load_workbook(path)
    assert book.sheetnames == ["總覽", "大跌標的", "重災區", "熱圖_AAA"], "沒有重災區前 15 檔（#469）"
    overview = book["總覽"]
    assert overview["A1"].value.startswith("盤勢總覽｜2026/10/28 收盤｜在庫 3 檔、連結 4 個標的")
    rows = {row[0]: row[1:3] for row in table(overview, 3)[1:]}
    assert rows[KI_BROKEN] == (1, pytest.approx(0.65)), "1 億日圓 × 0.0065 = 65 萬美元"
    assert rows["跌破K（未破KI）"] == (1, pytest.approx(1.0))
    assert rows["高於K 10% 以上"] == (1, pytest.approx(2.0))
    assert [row[0] for row in table(overview, 1)[1:]] == [
        "高於K 10% 以上",
        "距K 10% 內",
        "跌破K（未破KI）",
        KI_BROKEN,
    ], "四列，跌破KI 在最下面（#469）"
    assert table(overview, 4)[4][3] == pytest.approx(0.65 / 3.65), "佔比的分母是全部在庫面額"
    tickers = table(book["大跌標的"], 11)
    assert tickers[0][2:5] == ("近1月", "近2月", "近3月")
    assert [row[0] for row in tickers[1:]] == ["BBB", "CCC", "AAA", "DDD"], "依連結面額大到小"
    aaa = tickers[3]
    assert aaa[1:6] == (72, pytest.approx(-0.2), None, None, 2), "近 1 月 72 ÷ 90 − 1；更早沒有收盤價留空"
    assert aaa[7:10] == (2, pytest.approx(1.65), 0), "AAA 本身在兩檔都跌破K，都還在下限價之上"
    for sheet in book.worksheets:
        assert not sheet.merged_cells.ranges and not sheet.conditional_formatting, "貼到 PPT 不走樣"
        assert sheet.sheet_view.showGridLines is False


@pytest.mark.db
def test_heatmap_sheet_draws_the_close_line(seeded, tmp_path: Path) -> None:
    path = tmp_path / "盤勢速報.xlsx"
    export_brief(seeded, ["AAA"], path, today=DAY)

    sheet = openpyxl.load_workbook(path)["熱圖_AAA"]

    assert sheet["A1"].value.startswith("價格 x 到期月份 熱圖：AAA｜昨收 72.00（-28.0%）｜連結 2 檔 1.7M")
    grid = table(sheet, 2)
    assert grid[0] == ("執行價區間", "2026/11")
    assert grid[1:] == [("80-85", pytest.approx(1.65)), ("75-80", 0), ("72-75", 0), ("70-72", 0)]
    line_row = 3 + 3  # 表頭第 3 列，72-75 是第 3 格資料
    assert sheet.cell(line_row, 1).border.bottom.style == "thick"
    assert sheet.cell(line_row - 1, 1).border.bottom.style != "thick"


@pytest.mark.db
def test_currencies_without_a_usd_rate_are_reported(seeded, tmp_path: Path) -> None:
    seed(seeded, AUD, ["CCC UN", "BBB UN"], currency="AUD")
    trades(seeded, AUD, [("台北", "Amy", 500000)])

    receipt = export_brief(seeded, [], tmp_path / "盤勢速報.xlsx", today=DAY)

    assert receipt.missing_fx == frozenset({"AUD"})
    assert receipt.products == 4, "檔數照算，只是面額不計入"
