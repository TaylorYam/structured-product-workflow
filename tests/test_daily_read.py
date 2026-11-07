"""每日匯入的讀取端（#484）：虛構的每日更新（交易明細＋BBG_Data）與回補股價 CSV 用 `read_daily`／`read_price_csv`
讀成 `DailyFile`／`DailyPrices`，只驗解讀規則與錯誤訊息，不連資料庫。寫入資料庫的端到端測試在 test_daily_import。
資料全部虛構。"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest

from db_synth import csv_price, legacy_trade, price, trade, write_daily, write_price_csv
from sp_workflow import daily_import
from sp_workflow.daily_import import (
    BLOCKED,
    CANCELLED,
    LEGACY_TRADE_HEADERS,
    PRICE_CONFLICT,
    PRICE_CSV_HEADERS,
    PRICE_HEADERS,
    PRICE_NOT_NUMBER,
    TRADE_HEADERS,
    DailyImportError,
    read_daily,
    read_price_csv,
)

A = "029199990001"
B = "037199990002"
C = "076199990003"
D = "325199990004"


def people(parsed) -> list[tuple]:
    """交易列裡和人有關的欄（`workflow.trade_row` 的欄名），依 TDCC 與檔案裡的順序。"""
    return [
        (tdcc, row["fc_name"], row["fc2_name"], row["singapore"], row["supervisor"])
        for tdcc, rows in sorted(parsed.trades.items())
        for row in rows
    ]


def problems(prices) -> list[tuple[str, str]]:
    return [(line.kind, line.detail) for line in prices.problems]


# ---------------------------------------------------------------- 交易明細


def test_tdcc_loses_no_leading_zero(tmp_path: Path) -> None:
    parsed = read_daily(write_daily(tmp_path / "d.xlsx", [trade(int(A), "Amy", "北區", 1)]), prices=True)

    assert list(parsed.trades) == [A]


def test_other_products_are_skipped_and_counted(tmp_path: Path) -> None:
    daily = write_daily(
        tmp_path / "d.xlsx", [trade(A, "Amy", "北區", 1), trade(D, "Amy", "北區", 1, product="FCN_Stepdown")]
    )

    parsed = read_daily(daily, prices=True)

    assert parsed.skipped_trade_rows == 1
    assert list(parsed.trades) == [A] and parsed.blocked == []
    assert parsed.other_products == {D: "FCN_Stepdown"}


def test_express_rows_are_imported_like_fcn(tmp_path: Path) -> None:
    """FCN_Express 和 FCN 一樣寫進交易明細列（#302）：商品種類照設定的寫法存，列給起單資料頁，不記成其他商品。"""
    daily = write_daily(
        tmp_path / "d.xlsx", [trade(A, "Amy", "北區", 1, product="fcn_express"), trade(B, "Amy", "北區", 1)]
    )

    parsed = read_daily(daily, prices=True)

    assert (parsed.skipped_trade_rows, parsed.fcn_trade_rows, parsed.fcn_tdccs) == (0, 2, {A: True, B: True})
    assert {tdcc: [row["product"] for row in rows] for tdcc, rows in parsed.trades.items()} == {
        A: ["FCN_Express"],
        B: ["FCN"],
    }
    assert parsed.other_products == {}


@pytest.mark.parametrize(("written", "stored"), [("SUPER BULL", "Super Bull"), (" ben ", "BEN")])
def test_super_bull_and_ben_rows_are_imported_like_fcn(tmp_path: Path, written, stored) -> None:
    """Super Bull（#310）、BEN（#373）和 FCN 一樣寫進交易明細列：大小寫、空白不同也照設定的寫法存，不記成其他商品。"""
    parsed = read_daily(write_daily(tmp_path / "d.xlsx", [trade(A, "Amy", "北區", 1, product=written)]), prices=True)

    assert (parsed.skipped_trade_rows, parsed.fcn_tdccs) == (0, {A: True})
    assert [row["product"] for row in parsed.trades[A]] == [stored]
    assert parsed.other_products == {}


def test_dacn_rows_are_imported_like_fcn(tmp_path: Path) -> None:
    """DACN 和 FCN 一樣寫進交易明細列（#367），不記成其他商品。"""
    parsed = read_daily(write_daily(tmp_path / "d.xlsx", [trade(A, "Amy", "北區", 1, product="dacn")]), prices=True)

    assert (parsed.skipped_trade_rows, parsed.fcn_tdccs) == (0, {A: True})
    assert [row["product"] for row in parsed.trades[A]] == ["DACN"]
    assert parsed.other_products == {}


def test_a_bad_trade_row_blocks_all_rows_of_that_product(tmp_path: Path) -> None:
    bad = trade(A, "Ben", "南區", 1, number=None)
    bad["金額"] = None
    daily = write_daily(tmp_path / "d.xlsx", [trade(A, "Amy", "北區", 1), bad, trade(B, "Amy", "北區", 1)])

    parsed = read_daily(daily, prices=True)

    assert [(line.tdcc, line.kind) for line in parsed.blocked] == [(A, BLOCKED)]
    assert "金額 沒有值" in parsed.blocked[0].detail
    assert list(parsed.trades) == [B]


def test_cancel_note_is_skipped(tmp_path: Path) -> None:
    cancelled = trade(A, "Ben", "南區", 100000, number=None)
    cancelled["備註"] = "此筆取消"
    daily = write_daily(tmp_path / "d.xlsx", [trade(A, "Amy", "北區", 300000), cancelled])

    parsed = read_daily(daily, prices=True)

    assert parsed.cancelled_trade_rows == 1
    assert [(line.tdcc, line.kind) for line in parsed.cancelled_lines] == [(A, CANCELLED)]
    assert [(row["fc_name"], row["amount"]) for row in parsed.trades[A]] == [("Amy", Decimal("300000"))]


def test_fcn_tdccs_are_listed_for_the_order_tab_in_first_seen_order(tmp_path: Path) -> None:
    """#90：依第一次出現的順序、不重複；非 FCN、整檔都取消的不列；有一列沒取消就列。
    #93：只列剛好 12 碼的 TDCC；存成數字、前面的 0 掉了的照樣匯入資料庫，但不列。"""
    cancelled_b = trade(B, "Amy", "北區", 1)
    cancelled_b["備註"] = "Cancel"
    cancelled_c = trade(C, "Amy", "北區", 1, number=None)
    cancelled_c["備註"] = "此筆取消"
    daily = write_daily(
        tmp_path / "d.xlsx",
        [
            trade(C, "Amy", "北區", 1),
            trade(D, "Amy", "北區", 1, product="FCN_Stepdown"),
            trade(A, "Amy", "北區", 1),
            cancelled_b,
            cancelled_c,
            trade("14700000000X", "Amy", "北區", 1),
            trade(A, "Ben", "南區", 1, number=None),
            trade(int(B), "Ben", "南區", 1),
        ],
    )

    parsed = read_daily(daily, prices=True)

    assert [tdcc for tdcc, live in parsed.fcn_tdccs.items() if live] == [C, A]
    assert parsed.fcn_trade_rows == 7, "非 FCN 不算，取消與 TDCC 不完整的列都算"
    assert B in parsed.trades, "前面的 0 掉了的 TDCC 匯入資料庫照舊"


def test_no_fcn_trades_leaves_nothing_to_hand_over(tmp_path: Path) -> None:
    daily = write_daily(tmp_path / "d.xlsx", [trade(D, "Amy", "北區", 1, product="FCN_Stepdown")])

    parsed = read_daily(daily, prices=True)

    assert (parsed.fcn_trade_rows, parsed.fcn_tdccs) == (0, {})


def test_new_layout_stores_fc2_and_singapore(tmp_path: Path) -> None:
    """#420：新版面的 FC1 存 fc_name、FC2 存 fc2_name、新加坡存 singapore；沒有前台主管/投專，留空白。"""
    daily = write_daily(
        tmp_path / "d.xlsx",
        [trade(A, "Amy", "北區", 1, fc2="Ben", singapore="Y"), trade(A, "Ben", "南區", 1, number=None)],
    )

    parsed = read_daily(daily, prices=True)

    assert parsed.layout.headers == TRADE_HEADERS and parsed.blocked == []
    assert people(parsed) == [(A, "Amy", "Ben", "Y", None), (A, "Ben", None, "N", None)]


def test_legacy_layout_still_imports(tmp_path: Path) -> None:
    """#420：舊版面（FC/PB、前台主管/投專）照樣可以匯入，回補舊的交易清單用；FC2、新加坡留空白。"""
    daily = write_daily(
        tmp_path / "d.xlsx",
        [legacy_trade(trade(A, "Amy", "北區", 1), supervisor="PB")],
        trade_headers=LEGACY_TRADE_HEADERS,
    )

    parsed = read_daily(daily, prices=False)

    assert parsed.layout.headers == LEGACY_TRADE_HEADERS and parsed.blocked == []
    assert people(parsed) == [(A, "Amy", None, None, "PB")]


def test_fc1_is_required(tmp_path: Path) -> None:
    parsed = read_daily(write_daily(tmp_path / "d.xlsx", [trade(A, None, "北區", 1, fc2="Ben")]), prices=True)

    assert [line.kind for line in parsed.blocked] == [BLOCKED] and "FC1 沒有值" in parsed.blocked[0].detail
    assert parsed.trades == {}


def test_a_header_matching_neither_layout_is_explained_against_the_new_one(tmp_path: Path) -> None:
    """#420：表頭不是新版也不是舊版時整份不匯入，訊息照新版指出哪一欄不同。"""
    headers = list(LEGACY_TRADE_HEADERS)
    headers[-1] = "建檔人員"
    daily = write_daily(tmp_path / "d.xlsx", [], trade_headers=headers)

    with pytest.raises(DailyImportError, match="G 欄應為「FC1」，實際是「FC/PB」"):
        read_daily(daily, prices=True)


@pytest.mark.parametrize("missing", [daily_import.TRADE_SHEET, daily_import.PRICE_SHEET])
def test_missing_sheet_rejects_the_whole_file(tmp_path: Path, missing) -> None:
    daily = write_daily(tmp_path / "d.xlsx", [trade(A, "Amy", "北區", 1)], [price("AAA UW")])
    wb = openpyxl.load_workbook(daily)
    del wb[missing]
    wb.save(daily)

    with pytest.raises(DailyImportError, match=f"缺少工作表「{missing}」"):
        read_daily(daily, prices=True)


def test_an_inventory_sheet_is_ignored(tmp_path: Path) -> None:
    """清單更新停用（#87）：檔案裡有也不讀。"""
    daily = write_daily(tmp_path / "d.xlsx", [trade(A, "Amy", "北區", 1)], [price("AAA UW")])
    wb = openpyxl.load_workbook(daily)
    wb.create_sheet("清單更新").append(["這張工作表不讀"])
    wb.save(daily)

    parsed = read_daily(daily, prices=True)

    assert list(parsed.trades) == [A] and parsed.blocked == []


# ---------------------------------------------------------------- 每日股價 BBG_Data（#87）


def test_codes_keep_their_case_and_exchange(tmp_path: Path) -> None:
    parsed = read_daily(write_daily(tmp_path / "d.xlsx", [], [price("aaa uw"), price("AAA UQ")]), prices=True)

    assert sorted(parsed.prices.rows) == ["AAA UQ", "aaa uw"]


def test_duplicate_codes_with_the_same_values_count_once(tmp_path: Path) -> None:
    parsed = read_daily(write_daily(tmp_path / "d.xlsx", [], [price("AAA UW"), price("AAA UW")]), prices=True)

    assert list(parsed.prices.rows) == ["AAA UW"] and parsed.prices.conflicts == {}
    assert parsed.prices.problems == []


def test_a_conflict_counts_every_row_of_the_code(tmp_path: Path) -> None:
    """三列以上：訊息寫實際的列數，衝突只記不同的值。"""
    rows = [price("AAA UW", 100), price("AAA UW", 101), price("AAA UW", 100), price("AAA UW", 102)]

    prices = read_daily(write_daily(tmp_path / "d.xlsx", [], rows), prices=True).prices

    assert problems(prices) == [(PRICE_CONFLICT, "AAA UW：同一個代碼有 4 列、值不同，價格沒有存，算價格時不能用")]
    assert prices.row_counts["AAA UW"] == 4 and "AAA UW" not in prices.rows
    assert [row.last_close for row in prices.conflicts["AAA UW"]] == [Decimal("100"), Decimal("101"), Decimal("102")]


def test_a_price_that_is_not_a_number_is_stored_blank_and_listed(tmp_path: Path) -> None:
    prices = read_daily(write_daily(tmp_path / "d.xlsx", [], [price("AAA UW", "#N/A N/A")]), prices=True).prices

    assert problems(prices) == [
        (PRICE_NOT_NUMBER, "BBG_Data第 4 列 AAA UW：最新收盤價(值)「#N/A N/A」不是數字，存成空白")
    ]
    assert prices.rows["AAA UW"].last_close is None


@pytest.mark.parametrize("value", [None, "2026/10/16", 20261016])
def test_a_price_date_that_is_not_a_date_rejects_the_whole_file(tmp_path: Path, value) -> None:
    daily = write_daily(tmp_path / "d.xlsx", [trade(A, "Amy", "北區", 1)], [price("AAA UW")], price_date=value)

    with pytest.raises(DailyImportError, match="C1 應為取價日（日期）"):
        read_daily(daily, prices=True)


def test_wrong_price_headers_reject_the_whole_file(tmp_path: Path) -> None:
    headers = list(PRICE_HEADERS)
    headers[2], headers[3] = headers[3], headers[2]
    daily = write_daily(tmp_path / "d.xlsx", [trade(A, "Amy", "北區", 1)], [price("AAA UW")], price_headers=headers)

    with pytest.raises(DailyImportError, match="D2 應為「最新收盤價\\(值\\)」，實際是「最新開盤價\\(值\\)」"):
        read_daily(daily, prices=True)


def test_a_price_sheet_without_headers_rejects_the_whole_file(tmp_path: Path) -> None:
    """只有 C1 的 BBG_Data：整份不匯入，不能當成「沒有股價」。"""
    daily = write_daily(tmp_path / "d.xlsx", [trade(A, "Amy", "北區", 1)], [price("AAA UW")])
    wb = openpyxl.load_workbook(daily)
    wb[daily_import.PRICE_SHEET].delete_rows(2, 10)
    wb.save(daily)

    with pytest.raises(DailyImportError, match="B2 應為「代碼\\(值\\)」，實際是「空白」"):
        read_daily(daily, prices=True)


def test_a_price_sheet_without_prices_rejects_the_whole_file(tmp_path: Path) -> None:
    """有欄名但沒有任何代碼：多半是取價沒有完成，整份不匯入。"""
    daily = write_daily(tmp_path / "d.xlsx", [trade(A, "Amy", "北區", 1)], [("", "代碼空白")])

    with pytest.raises(DailyImportError, match="沒有任何股價"):
        read_daily(daily, prices=True)


def test_trades_only_ignores_a_stale_price_sheet(tmp_path: Path) -> None:
    daily = write_daily(tmp_path / "d.xlsx", [trade(A, "Amy", "北區", 1)], [price("AAA UW")], price_date="壞掉的")

    parsed = read_daily(daily, prices=False)

    assert parsed.prices is None and list(parsed.trades) == [A]


# ---------------------------------------------------------------- 回補股價 CSV（#112）


def test_a_quoted_line_break_stays_in_one_price_csv_row(tmp_path: Path) -> None:
    source = tmp_path / "m.csv"
    lines = [",".join(PRICE_CSV_HEADERS), '2026-10-15,AAA UW,"虛構\n公司",101,100,31.5,12345.6,USD,12345.6']
    source.write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-8-sig"))

    _, prices = read_price_csv(source)

    assert list(prices.rows) == ["AAA UW"] and prices.problems == []
    assert (prices.rows["AAA UW"].company_name, prices.rows["AAA UW"].last_close) == ("虛構\n公司", Decimal("101"))


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-inf"])
def test_nan_and_infinity_are_not_prices(tmp_path: Path, value) -> None:
    """NaN、Infinity 會讓算價格中斷：當成不是數字，存成空白並列出來（CSV 與 BBG_Data 都一樣）。"""
    _, from_csv = read_price_csv(write_price_csv(tmp_path / "m.csv", [csv_price("AAA UW", value)]))
    from_daily = read_daily(write_daily(tmp_path / "d.xlsx", [], [price("BBB UN", value)]), prices=True).prices

    assert [line.kind for line in from_csv.problems + from_daily.problems] == [PRICE_NOT_NUMBER, PRICE_NOT_NUMBER]
    assert (from_csv.rows["AAA UW"].last_close, from_daily.rows["BBB UN"].last_close) == (None, None)


@pytest.mark.parametrize(
    ("rows", "headers", "message"),
    [
        ([csv_price("AAA UW")], ("取價日", "代碼", "公司名稱", "開盤價"), "第 4 欄應為「收盤價」，實際是「開盤價」"),
        ([csv_price("AAA UW")], (*PRICE_CSV_HEADERS, "備註"), "之後還有其他欄"),
        ([csv_price("AAA UW", day="2026/10/32")], PRICE_CSV_HEADERS, "第 2 列的取價日「2026/10/32」不是日期"),
        ([csv_price("AAA UW", day="")], PRICE_CSV_HEADERS, "第 2 列的取價日「空白」不是日期"),
        (
            [csv_price("AAA UW"), csv_price("BBB UN", day="2026-10-16")],
            PRICE_CSV_HEADERS,
            "取價日不只一天：2026-10-15（第 2 列起）、2026-10-16（第 3 列起）",
        ),
        ([["2026-10-15", ""]], PRICE_CSV_HEADERS, "沒有任何股價"),
    ],
)
def test_a_bad_price_csv_is_rejected(tmp_path: Path, rows, headers, message) -> None:
    source = write_price_csv(tmp_path / "m.csv", rows, headers=headers)

    with pytest.raises(DailyImportError, match=re.escape(message)):
        read_price_csv(source)


def test_a_price_csv_that_is_not_utf8_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "m.csv"
    source.write_bytes(",".join(PRICE_CSV_HEADERS).encode("big5"))

    with pytest.raises(DailyImportError, match="不是 UTF-8"):
        read_price_csv(source)
