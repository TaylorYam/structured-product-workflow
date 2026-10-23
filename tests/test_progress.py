"""作業進度（`sp_workflow.progress`，#156、ADR 0019）：九個進度的推導、篩選與排序、從資料庫讀、匯出 Excel。

商品、交易明細與紀錄全部虛構。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest

from db_synth import inventory, seed_products
from order_synth import CONFIG
from sp_workflow.lifecycle import KO, record_redemption
from sp_workflow.progress import (
    CHECK_FAILED,
    CHECK_TERMS_DONE,
    CHECKED,
    DATE_COLUMNS,
    EXPORT_COLUMNS,
    FINISHED,
    LOCKED,
    PRICING_COLUMNS,
    PROGRESS,
    SENT_STEP,
    TERMS_IMPORTED,
    TO_CHECK,
    TO_MAIL,
    TO_PREPARE,
    TO_RETURN,
    TO_SEND,
    TRADES_IMPORTED,
    WAIT_CHECK_TERMS,
    WAIT_TERMS,
    Facts,
    ProgressRow,
    by_days,
    export_progress,
    filter_rows,
    load_progress,
    progress_of,
)
from sp_workflow.step_records import DRAFTED_STEP, PREPARED_STEP, RETURNED_STEP

T = dt.datetime(2026, 10, 20, 9, 0, tzinfo=dt.UTC)
READY = {"has_terms": True, "terms_status": "完成"}


# ---------------------------------------------------------------- 推導（不碰資料庫）


@pytest.mark.parametrize(
    ("facts", "progress", "reason"),
    [
        (Facts("1"), WAIT_TERMS, "還沒有商品條件"),
        (Facts("1", has_terms=True, missing=("Strike date", "KO Type")), WAIT_TERMS, "缺：Strike date、KO Type"),
        (Facts("1", has_terms=True), WAIT_CHECK_TERMS, "還沒算核對條件"),
        (
            Facts("1", has_terms=True, terms_status="待確認", terms_reason="UL_1 AAA UW：沒有股價"),
            WAIT_CHECK_TERMS,
            "核對條件待確認：UL_1 AAA UW：沒有股價",
        ),
        (Facts("1", has_terms=True, terms_status="等股價"), WAIT_CHECK_TERMS, "核對條件等股價"),
        (Facts("1", **READY), TO_PREPARE, ""),
        (Facts("1", **READY, dates={PREPARED_STEP: T}), TO_CHECK, ""),
        (
            Facts("1", **READY, checks={"說明書": "不一致", "投資人須知": "通過"}, dates={PREPARED_STEP: T}),
            CHECK_FAILED,
            "投資人須知：通過、說明書：不一致",
        ),
        (Facts("1", **READY, checks={"說明書": "通過"}, dates={PREPARED_STEP: T, LOCKED: T}), TO_RETURN, ""),
        (
            Facts("1", **READY, checks={"說明書": "通過"}, dates={PREPARED_STEP: T, LOCKED: T, RETURNED_STEP: T}),
            TO_MAIL,
            "",
        ),
        (
            Facts(
                "1",
                **READY,
                checks={"說明書": "通過"},
                dates={PREPARED_STEP: T, LOCKED: T, RETURNED_STEP: T, DRAFTED_STEP: T},
            ),
            TO_SEND,
            "",
        ),
        (Facts("1", dates={SENT_STEP: T}), FINISHED, ""),  # 有已寄出一律完成
    ],
)
def test_progress_is_the_first_step_not_yet_done(facts: Facts, progress: str, reason: str) -> None:
    assert progress_of(facts) == (progress, reason)


def test_the_nine_steps_are_in_order() -> None:
    assert PROGRESS == (
        WAIT_TERMS,
        WAIT_CHECK_TERMS,
        TO_PREPARE,
        TO_CHECK,
        CHECK_FAILED,
        TO_RETURN,
        TO_MAIL,
        TO_SEND,
        FINISHED,
    )


def row(tdcc: str, **changes) -> ProgressRow:
    values = {
        "tdcc": tdcc,
        "issuer": "SG",
        "trade_date": dt.date(2026, 10, 15),
        "days": 7,
        "progress": TO_PREPARE,
        "lifecycle": "在庫",
        "dates": (None,) * len(DATE_COLUMNS),
        "reason": "",
    }
    return ProgressRow(**{**values, **changes})


def test_rows_are_filtered_by_trade_date_progress_lifecycle_and_issuer() -> None:
    rows = [
        row("1"),
        row("2", trade_date=dt.date(2026, 9, 30), days=22),
        row("3", trade_date=None, days=None),
        row("4", progress=FINISHED),
        row("5", lifecycle=KO),
        row("6", issuer="BARC"),
    ]

    def tdccs(**filters) -> list[str]:
        return [r.tdcc for r in filter_rows(rows, **filters)]

    assert tdccs() == ["1", "2", "3", "4", "5", "6"]
    assert tdccs(first=dt.date(2026, 10, 1)) == ["1", "4", "5", "6"], "有區間時沒有交易日的不列"
    assert tdccs(last=dt.date(2026, 10, 1)) == ["2"]
    assert tdccs(first=dt.date(2026, 10, 15), last=dt.date(2026, 10, 15)) == ["1", "4", "5", "6"]
    assert tdccs(progress={FINISHED, TO_PREPARE}) == ["1", "2", "3", "4", "5", "6"]
    assert tdccs(progress={FINISHED}) == ["4"]
    assert tdccs(lifecycle={KO}) == ["5"]
    assert tdccs(issuer="BARC") == ["6"]


def test_rows_are_filtered_by_pricing_status() -> None:
    rows = [row("1", pricing="在庫"), row("2", pricing="等股價"), row("3"), row("4", pricing="待確認")]

    def tdccs(**filters) -> list[str]:
        return [r.tdcc for r in filter_rows(rows, **filters)]

    assert tdccs() == ["1", "2", "3", "4"]
    assert tdccs(pricing={"等股價", "待確認"}) == ["2", "4"], "選了比價狀態時還沒比過的不列"


def test_pricing_text_marks_unlocked_products() -> None:
    assert row("1", pricing="在庫", pricing_unlocked=True).pricing_text == "在庫（未鎖住）"
    assert row("1", pricing="在庫").pricing_text == "在庫"
    assert row("1", pricing_unlocked=True).pricing_text == "", "還沒比過的空白"


def test_default_order_puts_the_most_behind_first() -> None:
    rows = [row("3", days=None), row("1", days=3), row("2", days=30), row("0", days=3)]

    assert [r.tdcc for r in by_days(rows)] == ["2", "0", "1", "3"]


def test_export_writes_the_rows_as_shown(tmp_path: Path) -> None:
    dates = (T, None, None, None, None, None, None, None, None)
    path = tmp_path / "進度.xlsx"

    shown = row(
        "037199990002",
        dates=dates,
        reason="還沒有商品條件",
        pricing="等股價",
        pricing_reason="UL_1 AAA UW：2026-10-21 沒有收盤價",
        observed_through=dt.date(2026, 10, 20),
        pricing_unlocked=True,
    )
    receipt = export_progress([shown], path)

    assert (receipt.path, receipt.count) == (path, 1)
    sheet = openpyxl.load_workbook(path).active
    rows = list(sheet.iter_rows(values_only=True))
    assert rows[0] == EXPORT_COLUMNS
    assert rows[1][:6] == ("037199990002", "SG", dt.datetime(2026, 10, 15), 7, TO_PREPARE, "在庫")
    pricing = rows[0].index(PRICING_COLUMNS[0])
    assert rows[0][pricing : pricing + 3] == PRICING_COLUMNS
    assert rows[1][pricing : pricing + 3] == (
        "等股價（未鎖住）",
        dt.datetime(2026, 10, 20),
        "UL_1 AAA UW：2026-10-21 沒有收盤價",
    )
    assert rows[1][-1] == "還沒有商品條件"
    assert list(tmp_path.iterdir()) == [path], "暫存檔換名後不留"


# ---------------------------------------------------------------- 從資料庫讀


def trade_row(pg, tdcc: str, day: dt.date) -> None:
    (run_id,) = pg.execute(
        "INSERT INTO workflow.import_run (daily_file_name, daily_sha256, summary) VALUES ('每日更新.xlsx', 'x', '{}')"
        " RETURNING run_id"
    ).fetchone()
    pg.execute(
        "INSERT INTO workflow.trade_row (tdcc, row_seq, trade_date, product, currency, amount, fc_name, branch,"
        " first_run_id) VALUES (%s, 1, %s, 'FCN', 'USD', 10000, 'Amy', '台北', %s)",
        (tdcc, day, run_id),
    )


def check_terms(pg, tdcc: str, status: str = "完成", reason: str | None = None) -> None:
    pg.execute(
        "INSERT INTO workflow.check_terms (tdcc, status, reason, calculated_at) VALUES (%s, %s, %s, %s)",
        (tdcc, status, reason, T),
    )


def check_result(pg, tdcc: str, kind: str, category: str, at: dt.datetime = T) -> None:
    pg.execute(
        "INSERT INTO workflow.check_result (tdcc, kind, file_name, category, status, released, filled, result_file,"
        " checked_by, checked_at) VALUES (%s, %s, 'x.pdf', %s, 'PASS', false, false, '核對結果.xlsx', 'tester', %s)",
        (tdcc, kind, category, at),
    )


def step(pg, tdcc: str, name: str, at: dt.datetime) -> None:
    pg.execute(
        "INSERT INTO workflow.step_record (tdcc, step, recorded_at, recorded_by, result) VALUES (%s, %s, %s, 'tester', '')",
        (tdcc, name, at),
    )


A, B, C, D = "029199990001", "037199990002", "092199990003", "147199990004"


@pytest.mark.db
def test_progress_is_read_from_the_database(pg) -> None:
    seed_products(pg, [inventory(t, [("台北", 10000)], 發行機構="SG") for t in (A, B, C)])
    pg.execute("UPDATE workflow.product SET strike_date = trade_date WHERE tdcc IN (%s, %s)", (A, B))
    trade_row(pg, A, dt.date(2026, 10, 15))
    trade_row(pg, D, dt.date(2026, 10, 1))  # 只有交易明細
    check_terms(pg, A)
    check_terms(pg, B, "待確認", "UL_1 AAA UW：沒有股價")
    later = T + dt.timedelta(days=1)
    step(pg, A, PREPARED_STEP, T)
    step(pg, A, PREPARED_STEP, later)
    check_result(pg, A, "說明書", "通過")
    check_result(pg, A, "投資人須知", "通過", later)
    pg.execute("UPDATE workflow.product SET locked_at = %s WHERE tdcc = %s", (later, A))
    step(pg, A, RETURNED_STEP, later)
    step(pg, "999999999999", PREPARED_STEP, T)  # 交易明細與商品條件都沒有：不列
    record_redemption(pg, A, dt.date(2026, 10, 21), user="tester")
    for pricing in (
        (A, "已出場", "顧客贖回 2026-10-21", dt.date(2026, 10, 20), True),
        (B, "等股價", None, None, False),
    ):
        pg.execute(
            "INSERT INTO workflow.pricing_status (tdcc, status, reason, observed_through, locked)"
            " VALUES (%s, %s, %s, %s, %s)",
            pricing,
        )

    rows = {r.tdcc: r for r in load_progress(pg, CONFIG, dt.date(2026, 10, 22))}

    assert list(rows) == [A, B, C, D]
    a = rows[A]
    assert (a.issuer, a.trade_date, a.days, a.progress, a.lifecycle) == (
        "BARC",
        dt.date(2026, 10, 15),
        7,
        TO_MAIL,
        "顧客贖回",
    )
    dates = dict(zip(DATE_COLUMNS, a.dates, strict=True))
    assert dates[PREPARED_STEP] == dates[CHECKED] == dates[LOCKED] == dates[RETURNED_STEP] == later, "同一步取最新"
    assert dates[TRADES_IMPORTED] is not None and dates[TERMS_IMPORTED] is not None
    assert dates[CHECK_TERMS_DONE] == T and dates[DRAFTED_STEP] is None and dates[SENT_STEP] is None
    assert (rows[B].progress, rows[B].reason) == (WAIT_CHECK_TERMS, "核對條件待確認：UL_1 AAA UW：沒有股價")
    assert (rows[C].progress, rows[C].reason) == (WAIT_TERMS, "缺：Strike date")
    d = rows[D]
    assert (d.issuer, d.trade_date, d.days, d.progress, d.reason) == (
        "MS",
        dt.date(2026, 10, 1),
        21,
        WAIT_TERMS,
        "還沒有商品條件",
    )
    assert d.lifecycle == "在庫"
    pricing = {t: (r.pricing_text, r.pricing_reason, r.observed_through) for t, r in rows.items()}
    assert pricing == {
        A: ("已出場", "顧客贖回 2026-10-21", dt.date(2026, 10, 20)),
        B: ("等股價（未鎖住）", "", None),
        C: ("", "", None),
        D: ("", "", None),
    }, "沒有比價狀態的空白"


@pytest.mark.db
def test_blank_initial_pricing_counts_only_until_every_underlying_has_an_initial_price(pg) -> None:
    """#182：期初價格（定價方式）只是用來取進場價；每個標的都有進場價時空白不算缺。"""
    E = "148199990005"
    seed_products(pg, [inventory(t, [("台北", 10000)]) for t in (A, B, C, E)])
    pg.execute("UPDATE workflow.product SET strike_date = trade_date, initial_pricing = NULL")
    pg.execute("UPDATE workflow.product SET maturity_date = NULL WHERE tdcc = %s", (C,))
    for tdcc, prices in (
        (A, {1: "100", 2: "50"}),
        (B, {1: "100"}),  # UL_2 沒有進場價
        (C, {1: "100", 2: "50"}),
        (E, {1: "100", 2: "0"}),
    ):
        check_terms(pg, tdcc)
        for seq, price in prices.items():
            pg.execute(
                "INSERT INTO workflow.underlying_price (tdcc, seq, code, initial_price) VALUES (%s, %s, %s, %s)",
                (tdcc, seq, ("AAA UW", "BBB UN")[seq - 1], Decimal(price)),
            )

    rows = {r.tdcc: (r.progress, r.reason) for r in load_progress(pg, CONFIG, dt.date(2026, 10, 22))}

    assert rows == {
        A: (TO_PREPARE, ""),
        B: (WAIT_TERMS, "缺：期初價格"),
        C: (WAIT_TERMS, "缺：Maturity date"),
        E: (WAIT_TERMS, "缺：期初價格"),
    }, "進場價不齊（沒有或是 0）時照舊算缺；其他必填欄照舊"


@pytest.mark.db
def test_a_sent_record_finishes_the_product(pg) -> None:
    seed_products(pg, [inventory(A, [("台北", 10000)])])
    step(pg, A, SENT_STEP, T)

    (only,) = load_progress(pg, CONFIG, dt.date(2026, 10, 22))

    assert only.progress == FINISHED and only.reason == ""
