"""拆股登記（`sp_workflow.stock_splits`，#270、ADR 0024）：輸入、換算、價格斷層、新增刪除與受影響的已出場商品。

資料庫的部分對真的 PostgreSQL 跑；代碼、價格與商品全部虛構。
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import pytest

from db_synth import inventory, seed_products
from sp_workflow.lifecycle import KO, MATURITY, AutoEvent, record_legacy, replace_auto_events
from sp_workflow.stock_splits import (
    NOT_A_SPLIT,
    Jump,
    Split,
    SplitBook,
    StockSplitError,
    add_split,
    affected_exits,
    delete_split,
    load_splits,
    refresh_basis,
    split_from,
)

D = Decimal
DAY = dt.date(2030, 3, 4)  # 生效日（週一）


# ---------- 輸入、換算、斷層（不需資料庫） ----------


def test_input_drops_the_class_word_and_needs_positive_whole_shares() -> None:
    assert split_from(" AAA UW Equity ", DAY, "1", "100", " 1 拆 100 ") == Split("AAA UW", DAY, 1, 100, "1 拆 100")
    assert split_from("AAA UW", DAY, "10", "1").note is None
    for before, after in (("0", "2"), ("1", "1.5"), ("", "2"), ("1", "-2")):
        with pytest.raises(StockSplitError, match="請填正整數"):
            split_from("AAA UW", DAY, before, after)
    with pytest.raises(StockSplitError, match="請填代碼"):
        split_from("  ", DAY, "1", "2")


@pytest.mark.parametrize(
    ("before", "after", "label", "factor"),
    [(1, 100, "1 拆 100", D(100)), (2, 3, "2 拆 3", D("1.5")), (10, 1, "10 合 1", D("0.1")), (1, 1, NOT_A_SPLIT, D(1))],
)
def test_label_and_factor(before: int, after: int, label: str, factor: Decimal) -> None:
    split = Split("AAA UW", DAY, before, after)
    assert (split.label, split.factor) == (label, factor)


def test_factor_counts_splits_after_the_base_up_to_the_day() -> None:
    later = dt.date(2030, 6, 3)
    book = SplitBook([Split("AAA UW", later, 1, 2), Split("AAA UW", DAY, 1, 10), Split("BBB UN", DAY, 1, 4)])

    assert book.factor("AAA UW", DAY - dt.timedelta(days=1), DAY - dt.timedelta(days=1)) == 1, "生效日前不換算"
    assert book.factor("AAA UW", DAY - dt.timedelta(days=1), DAY) == 10, "生效日當天起換算"
    assert book.factor("AAA UW", dt.date(2030, 1, 2), later) == 20, "兩次拆股相乘"
    assert book.factor("AAA UW", DAY, later) == 2, "基準日當天（含）以前生效的不算：進場價已經是新基準"
    assert book.factor("CCC UN", dt.date(2030, 1, 2), later) == 1
    assert book.factor("AAA UW", later, DAY - dt.timedelta(days=1)) == D("0.05"), "基準日之前的收盤價反過來除掉"
    assert book.factor("AAA UW", DAY, DAY - dt.timedelta(days=1)) == D("0.1")


def test_the_basis_is_the_last_split_the_initial_price_already_includes() -> None:
    strike, later = dt.date(2030, 1, 2), dt.date(2030, 6, 3)
    book = SplitBook(
        [Split("AAA UW", DAY, 1, 10), Split("AAA UW", later, 1, 2), Split("AAA UW", later.replace(day=10), 1, 1)]
    )

    assert book.basis("AAA UW", strike, D(100), D(101)) is None, "交易時的基準"
    assert book.basis("AAA UW", strike, D(10), D(101)) == DAY, "上手調整過第一次拆股"
    assert book.basis("AAA UW", strike, D("5.1"), D(101)) == later, "兩次都調整過；確認不是拆股不算"
    assert book.basis("AAA UW", strike, D(30), D(101)) is None, "都對不起來：交給比價標待確認"
    assert book.basis("AAA UW", DAY, D(10), D(101)) is None, "Strike date 當天（含）以前生效的不算"
    assert book.basis("AAA UW", strike, None, D(101)) is None and book.basis("AAA UW", strike, D(10), None) is None
    assert book.basis("BBB UN", strike, D(10), D(101)) is None, "沒有拆股"


def test_jumps_are_gaps_beyond_the_range_after_registered_splits() -> None:
    before = DAY - dt.timedelta(days=3)  # 上一個收盤日（週五）
    closes = {before: D(500), DAY: D(5), DAY + dt.timedelta(days=1): D("5.1")}
    book = SplitBook()

    assert book.jumps("AAA UW", closes) == (Jump("AAA UW", before, DAY, D("0.01")),)
    assert SplitBook([Split("AAA UW", DAY, 1, 100)]).jumps("AAA UW", closes) == (), "登記後換算回來就沒有斷層"
    assert SplitBook([Split("AAA UW", DAY, 1, 10)]).jumps("AAA UW", closes)[0].ratio == D("0.1"), "比例登記錯還是斷層"
    gap = {before: D(100), DAY + dt.timedelta(days=1): D(50)}  # 中間缺一天，生效日落在兩個收盤日之間
    assert SplitBook([Split("AAA UW", DAY, 1, 1)]).jumps("AAA UW", gap) == (), "確認不是拆股"
    assert book.jumps("AAA UW", {before: D(100), DAY: D(61)}) == (), "0.6 倍以內不算"
    assert book.jumps("AAA UW", {before: D(100), DAY: D(166)}) == ()
    assert book.jumps("AAA UW", {before: D(100), DAY: D(170)})[0].ratio == D("1.7"), "合股：大漲"


# ---------- 資料庫 ----------


def close(pg, code: str, day: dt.date = DAY) -> None:
    (import_id,) = pg.execute(
        "INSERT INTO workflow.close_price_import (source, file_name, file_sha256, imported_by, summary)"
        " VALUES ('歷史收盤價', 'HP.xlsx', 'x', 'tester', '{}') RETURNING import_id"
    ).fetchone()
    pg.execute(
        "INSERT INTO workflow.close_price (close_date, code, close, import_id) VALUES (%s, %s, 10, %s)",
        (day, code, import_id),
    )


def log(pg) -> list[tuple]:
    return [
        (by, action, row["code"], row["shares_after"])
        for by, action, row in pg.execute(
            "SELECT changed_by, action, split FROM workflow.stock_split_log ORDER BY change_id"
        )
    ]


@pytest.mark.db
def test_add_and_delete_keep_an_insert_only_log(pg) -> None:
    close(pg, "AAA UW")
    split = Split("AAA UW", DAY, 1, 100, "公告")
    add_split(pg, split, user="tester")

    (stored,) = load_splits(pg)
    assert stored == split and stored.recorded_by == "tester" and stored.recorded_at is not None
    with pytest.raises(StockSplitError, match="已經有一筆拆股登記；要改請先刪掉那一筆再新增"):
        add_split(pg, Split("AAA UW", DAY, 1, 50), user="tester")
    with pytest.raises(StockSplitError, match="在打開之後被改過或刪掉了"):
        delete_split(pg, Split("AAA UW", DAY, 1, 50), user="tester")

    delete_split(pg, split, user="tester")

    assert load_splits(pg) == ()
    assert log(pg) == [("tester", "新增", "AAA UW", 100), ("tester", "刪除", "AAA UW", 100)]
    (row,) = pg.execute("SELECT split FROM workflow.stock_split_log WHERE action = '新增'").fetchone()
    assert row == json.loads(
        '{"code": "AAA UW", "effective_date": "2030-03-04", "shares_before": 1, "shares_after": 100, "note": "公告"}'
    )
    with pytest.raises(Exception, match="拆股登記紀錄只新增"):
        pg.execute("DELETE FROM workflow.stock_split_log")


@pytest.mark.db
def test_unknown_codes_are_refused(pg) -> None:
    with pytest.raises(StockSplitError, match="收盤價表和商品都沒有「ZZZ UW」這個代碼"):
        add_split(pg, Split("ZZZ UW", DAY, 1, 2), user="tester")
    assert load_splits(pg) == () and log(pg) == []


T1, T2, T3, T4 = "147199990001", "147199990002", "147199990003", "147199990004"


def product(pg, tdcc: str, code: str, strike: dt.date) -> None:
    seed_products(pg, [inventory(tdcc, [("台北", 10000)])])
    pg.execute("UPDATE workflow.product SET strike_date = %s WHERE tdcc = %s", (strike, tdcc))
    pg.execute("INSERT INTO workflow.check_terms (tdcc, status) VALUES (%s, '完成')", (tdcc,))
    pg.execute(
        "INSERT INTO workflow.underlying_price (tdcc, seq, code, initial_price, strike_price, ki_price, ko_price)"
        " VALUES (%s, 1, %s, 100, 80, 65, 100)",
        (tdcc, code),
    )


@pytest.mark.db
def test_affected_exits_are_observation_exits_on_or_after_the_split(pg) -> None:
    early = dt.date(2030, 1, 2)
    for tdcc in (T1, T2, T3, T4):
        product(pg, tdcc, "AAA UW", early)
    product(pg, "147199990005", "BBB UN", early)  # 別的代碼
    product(pg, "147199990006", "AAA UW", DAY)  # 生效日當天交易：進場價已經是新基準
    replace_auto_events(pg, T1, [AutoEvent(MATURITY, dt.date(2030, 6, 3))], user="tester")
    replace_auto_events(pg, T2, [AutoEvent(KO, DAY - dt.timedelta(days=1))], user="tester")  # 拆股前出場也重比（#275）
    record_legacy(pg, T3, KO, dt.date(2030, 6, 3), "", user="tester")  # 舊清單不重算
    replace_auto_events(pg, "147199990005", [AutoEvent(MATURITY, dt.date(2030, 6, 3))], user="tester")
    replace_auto_events(pg, "147199990006", [AutoEvent(MATURITY, dt.date(2030, 6, 3))], user="tester")

    assert affected_exits(pg, Split("AAA UW", DAY, 1, 2)) == [T1, T2], "T4 在庫，比價全部時本來就會比"


@pytest.mark.db
def test_refresh_basis_writes_only_changes_and_clears_codes_without_splits(pg) -> None:
    early = dt.date(2030, 1, 2)
    product(pg, T1, "AAA UW", early)  # 進場價 100：交易時的基準
    product(pg, T2, "AAA UW", early)
    pg.execute("UPDATE workflow.underlying_price SET initial_price = 1 WHERE tdcc = %s", (T2,))  # 上手調整過 1 拆 100
    close(pg, "AAA UW", early)
    pg.execute("UPDATE workflow.close_price SET close = 100")

    book = SplitBook([Split("AAA UW", DAY, 1, 100)])
    assert refresh_basis(pg, book) == 1
    assert refresh_basis(pg, book) == 0, "沒變就不寫"
    basis = "SELECT tdcc, price_basis_date FROM workflow.underlying_price ORDER BY tdcc"
    assert pg.execute(basis).fetchall() == [(T1, None), (T2, DAY)]

    assert refresh_basis(pg, SplitBook()) == 1, "拆股刪掉了：清成空白"
    assert pg.execute(basis).fetchall() == [(T1, None), (T2, None)]
