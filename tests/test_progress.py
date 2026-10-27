"""作業進度（`sp_workflow.progress`，#156、#186、ADR 0019）：五個階段的推導、篩選與排序、批次登記已寄出的範圍、
從資料庫讀、匯出 Excel。

商品、交易明細與紀錄全部虛構。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest

from db_synth import (
    STEP_AT,
    check_result,
    check_terms,
    inventory,
    seed_products,
    seed_waiting_for_mail,
    step,
    trade_row,
)
from order_synth import CONFIG
from sp_workflow.lifecycle import KO, record_redemption
from sp_workflow.progress import (
    CHECK_TERMS_DONE,
    CHECKED,
    DATE_COLUMNS,
    EXPORT_COLUMNS,
    FINISHED,
    LOCKED,
    PREPARING,
    PRICING_COLUMNS,
    PROGRESS,
    READY_COLUMNS,
    SENT_STEP,
    TERMS_IMPORTED,
    TO_CHECK,
    TO_FIX,
    TO_MAIL,
    TRADES_IMPORTED,
    Facts,
    ProgressRow,
    SentBatch,
    by_trade_date,
    export_progress,
    filter_rows,
    load_progress,
    progress_of,
    ready_of,
    sent_batch,
    still_pending_mail,
    to_confirm_tdccs,
)
from sp_workflow.step_records import DRAFTED_STEP, PREPARED_STEP, RETURNED_STEP

T = STEP_AT
READY = {"has_trades": True, "has_terms": True, "terms_status": "完成"}


# ---------------------------------------------------------------- 推導（不碰資料庫）


@pytest.mark.parametrize(
    ("facts", "progress", "reason"),
    [
        (Facts("1"), PREPARING, "還沒有交易明細；還沒有商品條件；還沒算核對條件"),
        (Facts("1", **{**READY, "has_trades": False}), PREPARING, "還沒有交易明細"),
        (Facts("1", **READY, missing=("Strike date", "KO Type")), PREPARING, "缺：Strike date、KO Type"),
        (Facts("1", **{**READY, "terms_status": None}), PREPARING, "還沒算核對條件"),
        (
            Facts("1", **{**READY, "terms_status": "待確認"}, terms_reason="UL_1 AAA UW：沒有股價"),
            PREPARING,
            "核對條件待確認：UL_1 AAA UW：沒有股價",
        ),
        (Facts("1", **{**READY, "terms_status": "等股價"}), PREPARING, "核對條件等股價"),
        (Facts("1", **READY), TO_CHECK, ""),
        (Facts("1", **READY, dates={PREPARED_STEP: T, RETURNED_STEP: T}), TO_CHECK, ""),  # 步驟日期不決定階段
        (
            Facts("1", **READY, checks={"說明書": "不一致", "投資人須知": "通過"}),
            TO_FIX,
            "投資人須知：通過、說明書：不一致",
        ),
        (Facts("1", **READY, checks={"說明書": "通過"}, dates={LOCKED: T}), TO_MAIL, ""),
        (Facts("1", **READY, checks={"說明書": "通過"}, dates={LOCKED: T, DRAFTED_STEP: T}), TO_MAIL, ""),
        (Facts("1", dates={SENT_STEP: T}), FINISHED, ""),  # 有已寄出一律完成
        (Facts("1", locked_inventory=True), FINISHED, ""),  # 以前在 Excel 走完的舊商品（#184）
        (Facts("1", locked_inventory=True, **READY, dates={PREPARED_STEP: T}), TO_CHECK, ""),  # 走過新流程的照順序
        (
            Facts("1", locked_inventory=True, has_trades=True, dates={RETURNED_STEP: T}),
            PREPARING,
            "還沒有商品條件；還沒算核對條件",
        ),
    ],
)
def test_progress_is_the_first_stage_that_applies(facts: Facts, progress: str, reason: str) -> None:
    assert progress_of(facts) == (progress, reason)


@pytest.mark.parametrize(
    ("facts", "ready"),
    [
        (Facts("1"), (False, False, False)),
        (Facts("1", **READY), (True, True, True)),
        (Facts("1", **READY, missing=("KO Type",)), (True, False, True)),
        (Facts("1", **{**READY, "terms_status": "等股價", "has_trades": False}), (False, True, False)),
    ],
)
def test_the_three_preparations_are_ticked_independently(facts: Facts, ready: tuple[bool, ...]) -> None:
    assert READY_COLUMNS == ("交易明細", "商品條件", "股價")
    assert ready_of(facts) == ready


def test_the_five_stages_are_in_order() -> None:
    assert PROGRESS == (PREPARING, TO_CHECK, TO_FIX, TO_MAIL, FINISHED)
    assert PROGRESS == ("資料準備中", "待核對", "待修正", "待寄信", "完成")


def test_a_batch_of_sent_takes_only_products_waiting_for_mail_up_to_the_day() -> None:
    day = dt.date(2026, 10, 22)
    rows = [
        row("5", progress=TO_MAIL, trade_date=day),
        row("1", progress=TO_MAIL, trade_date=dt.date(2026, 10, 1)),
        row("2", progress=TO_MAIL, trade_date=dt.date(2026, 10, 23)),  # 選的日期之後
        row("3", progress=TO_MAIL, trade_date=None, days=None),
        row("4", progress=FINISHED, trade_date=day),
        row("6", progress=TO_FIX, trade_date=day),
        row("7", progress=TO_CHECK, trade_date=dt.date(2026, 9, 1)),
        row("8", progress=PREPARING, trade_date=day),
        row("9", progress=PREPARING, trade_date=dt.date(2026, 10, 23)),
    ]

    assert sent_batch(rows, day) == SentBatch(day, ("1", "5"), 3), "還沒核對完：資料準備中、待核對、待修正"


def row(tdcc: str, **changes) -> ProgressRow:
    values = {
        "tdcc": tdcc,
        "issuer": "SG",
        "trade_date": dt.date(2026, 10, 15),
        "days": 7,
        "progress": TO_CHECK,
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
    assert tdccs(progress={FINISHED, TO_CHECK}) == ["1", "2", "3", "4", "5", "6"]
    assert tdccs(progress={FINISHED}) == ["4"]
    assert tdccs(lifecycle={KO}) == ["5"]
    assert tdccs(issuer="BARC") == ["6"]


def test_rows_are_filtered_by_pricing_status() -> None:
    rows = [row("1", pricing="在庫"), row("2", pricing="等股價"), row("3"), row("4", pricing="待確認")]

    def tdccs(**filters) -> list[str]:
        return [r.tdcc for r in filter_rows(rows, **filters)]

    assert tdccs() == ["1", "2", "3", "4"]
    assert tdccs(pricing={"等股價", "待確認"}) == ["2", "4"], "選了比價狀態時還沒比過的不列"


def test_tdccs_to_confirm_are_only_the_pricing_to_confirm_rows_in_order() -> None:
    """複製待確認 TDCC（#289）：只取比價狀態是待確認的，照列的順序、重複的只取一次；未鎖住的也算。"""
    rows = [
        row("4", pricing="待確認"),
        row("1", pricing="在庫"),
        row("2", pricing="等股價"),
        row("3", reason="核對條件待確認"),
        row("5", pricing="待確認", pricing_unlocked=True),
        row("4", pricing="待確認"),
    ]

    assert to_confirm_tdccs(rows) == ["4", "5"]
    assert to_confirm_tdccs([row("1", pricing="在庫")]) == []


def test_pricing_text_marks_unlocked_products() -> None:
    assert row("1", pricing="在庫", pricing_unlocked=True).pricing_text == "在庫（未鎖住）"
    assert row("1", pricing="在庫").pricing_text == "在庫"
    assert row("1", pricing_unlocked=True).pricing_text == "", "還沒比過的空白"


def test_default_order_puts_the_newest_trade_first() -> None:
    rows = [
        row("3", trade_date=None),
        row("1", trade_date=dt.date(2026, 10, 20)),
        row("2", trade_date=dt.date(2026, 9, 1)),
        row("0", trade_date=dt.date(2026, 10, 20)),
    ]

    assert [r.tdcc for r in by_trade_date(rows)] == ["0", "1", "2", "3"]


def test_export_writes_the_rows_as_shown(tmp_path: Path) -> None:
    dates = (T, None, None, None, None, None, None, None, None)
    path = tmp_path / "進度.xlsx"

    shown = row(
        "037199990002",
        dates=dates,
        reason="還沒有商品條件",
        ready=(True, False, False),
        pricing="等股價",
        pricing_reason="UL_1 AAA UW：2026-10-21 沒有收盤價",
        observed_through=dt.date(2026, 10, 20),
        pricing_unlocked=True,
        ki="AKI",
    )
    receipt = export_progress([shown], path)

    assert (receipt.path, receipt.count) == (path, 1)
    sheet = openpyxl.load_workbook(path).active
    rows = list(sheet.iter_rows(values_only=True))
    assert rows[0] == EXPORT_COLUMNS
    assert rows[1][:9] == ("037199990002", "SG", dt.datetime(2026, 10, 15), 7, TO_CHECK, "✓", None, None, "在庫")
    assert rows[0][5:8] == READY_COLUMNS, "三件打勾接在作業進度後面"
    pricing = rows[0].index(PRICING_COLUMNS[0])
    assert rows[0][pricing : pricing + 4] == PRICING_COLUMNS
    assert rows[1][pricing : pricing + 4] == (
        "等股價（未鎖住）",
        dt.datetime(2026, 10, 20),
        "UL_1 AAA UW：2026-10-21 沒有收盤價",
        "AKI",
    )
    assert rows[1][-1] == "還沒有商品條件"
    assert list(tmp_path.iterdir()) == [path], "暫存檔換名後不留"


# ---------------------------------------------------------------- 從資料庫讀


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
    assert (rows[B].progress, rows[B].reason) == (PREPARING, "還沒有交易明細；核對條件待確認：UL_1 AAA UW：沒有股價")
    assert (rows[C].progress, rows[C].reason) == (PREPARING, "還沒有交易明細；缺：Strike date；還沒算核對條件")
    assert {t: r.ready for t, r in rows.items()} == {
        A: (True, True, True),
        B: (False, True, False),
        C: (False, False, False),
        D: (True, False, False),
    }
    d = rows[D]
    assert (d.issuer, d.trade_date, d.days, d.progress, d.reason) == (
        "MS",
        dt.date(2026, 10, 1),
        21,
        PREPARING,
        "還沒有商品條件；還沒算核對條件",
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
    for tdcc in (A, B, C, E):
        trade_row(pg, tdcc, dt.date(2026, 10, 15))
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
        A: (TO_CHECK, ""),
        B: (PREPARING, "缺：期初價格"),
        C: (PREPARING, "缺：Maturity date"),
        E: (PREPARING, "缺：期初價格"),
    }, "進場價不齊（沒有或是 0）時照舊算缺；其他必填欄照舊"


@pytest.mark.db
def test_locked_legacy_products_without_step_records_are_finished(pg) -> None:
    """#184：以前清單更新建立、已鎖住的舊商品，以前在 Excel 走完；走過新流程的、沒鎖住的、人工維護的照順序。"""
    seed_products(pg, [inventory(t, [("台北", 10000)]) for t in (A, B, C, D)])
    pg.execute("UPDATE workflow.product SET strike_date = trade_date")
    pg.execute("UPDATE workflow.product SET locked_at = %s WHERE tdcc IN (%s, %s, %s)", (T, A, B, D))
    pg.execute("UPDATE workflow.product SET source = '人工維護' WHERE tdcc = %s", (D,))
    for tdcc in (A, B, C, D):
        check_terms(pg, tdcc)
        trade_row(pg, tdcc, dt.date(2026, 10, 15))
    step(pg, B, PREPARED_STEP, T)

    rows = {r.tdcc: r for r in load_progress(pg, CONFIG, dt.date(2026, 10, 22))}

    assert {t: (r.progress, r.reason) for t, r in rows.items()} == {
        A: (FINISHED, ""),
        B: (TO_CHECK, ""),
        C: (TO_CHECK, ""),
        D: (TO_CHECK, ""),
    }
    assert dict(zip(DATE_COLUMNS, rows[A].dates, strict=True))[LOCKED] == T, "各步日期照實際有的顯示"


@pytest.mark.db
def test_a_sent_record_finishes_the_product(pg) -> None:
    seed_products(pg, [inventory(A, [("台北", 10000)])])
    step(pg, A, SENT_STEP, T)

    (only,) = load_progress(pg, CONFIG, dt.date(2026, 10, 22))

    assert only.progress == FINISHED and only.reason == ""


@pytest.mark.db
def test_still_pending_mail_rereads_the_same_facts_as_the_progress(pg) -> None:
    """#205：鎖住未寄的還是待寄信；鎖住已寄的、還沒鎖住的、更正解鎖的、核對條件不再是完成的不是。依傳入的順序、不重複。"""
    e = "147199990005"
    seed_waiting_for_mail(pg, [A, B, C, D, e])
    pg.execute("UPDATE workflow.check_terms SET status = '待確認' WHERE tdcc = %s", (e,))  # 鎖住的更正後重算不是完成
    step(pg, B, SENT_STEP, T)  # 鎖住、已寄出
    pg.execute("DELETE FROM workflow.check_result WHERE tdcc = %s", (C,))  # 還沒核對、還沒鎖住
    pg.execute("UPDATE workflow.product SET locked_at = NULL WHERE tdcc IN (%s, %s)", (C, D))  # D：核對過、被更正解鎖
    rows = {r.tdcc: r.progress for r in load_progress(pg, CONFIG, dt.date(2026, 10, 22))}

    found = still_pending_mail(pg, [e, D, C, B, A, A, "999999999999"])

    assert found == (A,)
    assert [t for t, progress in rows.items() if progress == TO_MAIL] == [A], "和進度頁同一個判斷"
