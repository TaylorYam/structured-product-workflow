"""每日匯入（#32）：虛構的每日更新與信箱表，對真的 PostgreSQL 匯入。資料全部虛構。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest

from sp_workflow import daily_import
from sp_workflow.daily_import import (
    BLOCKED,
    DIFFERENT,
    INVENTORY_HEADERS,
    INVENTORY_MISSING,
    NAME_UNMATCHED,
    NEW_PRODUCT,
    NEW_TRADES,
    RECONCILE,
    TRADE_HEADERS,
    TRADES_MISSING,
    UNCHANGED,
    DailyImportError,
    import_daily,
    read_mailbox,
)
from sp_workflow.database import DatabaseError, require_current_schema

pytestmark = pytest.mark.db

A = "029199990001"
B = "037199990002"
C = "076199990003"
D = "325199990004"


def trade(tdcc: str, fc: str, branch: str, amount: int, *, number: int | None = 1, product: str = "FCN") -> dict:
    return {
        "編號": number,
        "交易日期": dt.datetime(2026, 10, 15),
        "Product": product,
        "TDCC": tdcc,
        "幣別": "USD",
        "金額": amount,
        "FC/PB": fc,
        "分公司": branch,
        "UF": 0.0215,
        "期初價格": "收盤價",
        "募集": "N",
        "起單人員": "A",
    }


def inventory(tdcc: str, branches: list[tuple[str, int]], **overrides) -> dict:
    row = {name: "-" for name in INVENTORY_HEADERS}
    row.update(
        {
            "庫存狀態": None,
            "Product": "FCN",
            "發行機構": "XBANK",
            "TDCC Code": tdcc,
            "ISIN Code": "XS0000000001",
            "承作幣別": "USD",
            "交易日": dt.datetime(2026, 10, 15),
            "發行日": dt.datetime(2026, 10, 22),
            "最終比價日": dt.datetime(2027, 4, 22),
            "到期日": dt.datetime(2027, 4, 27),
            "期初定價": "收盤價",
            "KO(%)": 100,
            "KO(Freq)": "D",
            "KO(memo)": "Y",
            "K(%)": 80,
            "KI(%)": 65,
            "KI(Freq)": "AM",
            "UF": 2.1500000000000057,
            "Coupon p.a. (%)": 12.5,
            "天期(月)": 6,
            "Non-Call(月)": 1,
            "UL_1": "AAA UW",
            "UL_2": "BBB UN",
        }
    )
    for i, (branch, amount) in enumerate(branches, 1):
        row[f"分公司_{i}"], row[f"分公司_{i}_承作金額"] = branch, amount
    row.update(overrides)
    return row


def write_daily(path: Path, trades: list[dict], inventories: list[dict], *, trade_headers=TRADE_HEADERS) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = daily_import.TRADE_SHEET
    ws.append(list(trade_headers))
    for t in trades:
        ws.append([t.get(h) for h in TRADE_HEADERS])
    inv = wb.create_sheet(daily_import.INVENTORY_SHEET)
    inv.append(list(INVENTORY_HEADERS))
    for row in inventories:
        inv.append([row.get(h) for h in INVENTORY_HEADERS])
    wb.save(path)
    return path


def write_mailbox(path: Path, people=None, aliases=None, *, fixed_cc_column: bool = True) -> Path:
    people = people or [
        ("Amy", "amy@example.com", "FC", ""),
        ("Ben", "ben@example.com", "FC", ""),
        ("Pat", "pat@example.com", "PM", "Y"),
    ]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "信箱"
    ws.append(["使用者名稱", "公司信箱", "單位", *(["固定副本"] if fixed_cc_column else [])])
    for name, email, unit, cc in people:
        ws.append([name, email, unit, *([cc or None] if fixed_cc_column else [])])
    if aliases is not None:
        al = wb.create_sheet("別名")
        al.append(["交易清單寫法", "使用者名稱"])
        for row in aliases:
            al.append(list(row))
    wb.save(path)
    return path


@pytest.fixture
def mailbox(tmp_path: Path) -> Path:
    return write_mailbox(tmp_path / "信箱.xlsx")


def kinds(receipt, tdcc: str) -> list[str]:
    return [line.kind for line in receipt.lines if line.tdcc == tdcc]


def count(pg, table: str) -> int:
    return pg.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_new_products_trades_and_people_are_written(pg, tmp_path: Path, mailbox: Path) -> None:
    daily = write_daily(
        tmp_path / "每日更新.xlsx",
        [
            trade(A, "Amy", "北區", 300000),
            trade(A, "Ben", "南區", 100000, number=None),
            trade(B, "Ben", "南區", 500000),
        ],
        [inventory(A, [("北區", 300000), ("南區", 100000)]), inventory(B, [("南區", 500000)])],
    )

    receipt = import_daily(daily, mailbox, pg)

    assert kinds(receipt, A) == [NEW_PRODUCT, NEW_TRADES]
    assert kinds(receipt, B) == [NEW_PRODUCT, NEW_TRADES]
    assert not [line for line in receipt.lines if line.problem]
    product = pg.execute(
        "SELECT issuer, isin, ko_type, barrier_type, ki_pct, upfront_pct, status_event, underlying_2, underlying_3"
        " FROM workflow.product WHERE tdcc = %s",
        (A,),
    ).fetchone()
    assert product == (
        "XBANK",
        "XS0000000001",
        "Daily Memory",
        "EKI",
        Decimal("65"),
        Decimal("2.15"),
        "在庫",
        "BBB UN",
        None,
    )
    assert pg.execute(
        "SELECT seq, branch, amount FROM workflow.branch_allocation WHERE tdcc = %s ORDER BY seq", (A,)
    ).fetchall() == [(1, "北區", Decimal("300000")), (2, "南區", Decimal("100000"))]
    assert pg.execute(
        "SELECT row_seq, number, fc_name, amount FROM workflow.trade_row WHERE tdcc = %s ORDER BY row_seq", (A,)
    ).fetchall() == [(1, 1, "Amy", Decimal("300000")), (2, None, "Ben", Decimal("100000"))]
    assert pg.execute("SELECT name, fixed_cc FROM mailbox.person ORDER BY name").fetchall() == [
        ("Amy", False),
        ("Ben", False),
        ("Pat", True),
    ]
    summary = pg.execute("SELECT daily_file_name, summary FROM workflow.import_run").fetchone()
    assert summary[0] == "每日更新.xlsx" and summary[1]["counts"][NEW_PRODUCT] == 2


def test_tdcc_loses_no_leading_zero(pg, tmp_path: Path, mailbox: Path) -> None:
    daily = write_daily(tmp_path / "d.xlsx", [trade(int(A), "Amy", "北區", 1)], [inventory(int(A), [("北區", 1)])])

    import_daily(daily, mailbox, pg)

    assert pg.execute("SELECT tdcc FROM workflow.product").fetchall() == [(A,)]
    assert pg.execute("SELECT DISTINCT tdcc FROM workflow.trade_row").fetchall() == [(A,)]


def test_other_products_are_skipped_and_counted(pg, tmp_path: Path, mailbox: Path) -> None:
    daily = write_daily(
        tmp_path / "d.xlsx",
        [trade(A, "Amy", "北區", 1), trade(D, "Amy", "北區", 1, product="BEN")],
        [inventory(A, [("北區", 1)]), inventory(D, [("北區", 1)], Product="BEN")],
    )

    receipt = import_daily(daily, mailbox, pg)

    assert (receipt.skipped_trade_rows, receipt.skipped_inventory_rows) == (1, 1)
    assert count(pg, "workflow.product") == 1 and kinds(receipt, D) == []


def test_importing_the_same_file_again_adds_nothing(pg, tmp_path: Path, mailbox: Path) -> None:
    daily = write_daily(tmp_path / "d.xlsx", [trade(A, "Amy", "北區", 1)], [inventory(A, [("北區", 1)])])
    import_daily(daily, mailbox, pg)

    receipt = import_daily(daily, mailbox, pg)

    assert kinds(receipt, A) == [UNCHANGED, UNCHANGED]
    assert (count(pg, "workflow.product"), count(pg, "workflow.trade_row"), count(pg, "workflow.import_run")) == (
        1,
        1,
        2,
    )


def test_changed_terms_are_reported_not_overwritten(pg, tmp_path: Path, mailbox: Path) -> None:
    import_daily(
        write_daily(tmp_path / "1.xlsx", [trade(A, "Amy", "北區", 1)], [inventory(A, [("北區", 1)])]), mailbox, pg
    )
    changed = write_daily(
        tmp_path / "2.xlsx",
        [trade(A, "Amy", "北區", 2)],
        [inventory(A, [("北區", 1)], **{"Coupon p.a. (%)": 13, "庫存狀態": "已於2026/11/2 KO"})],
    )

    receipt = import_daily(changed, mailbox, pg)

    assert [(line.kind, line.detail) for line in receipt.lines if line.tdcc == A and line.kind == DIFFERENT] == [
        (DIFFERENT, "商品條件和資料庫不同：coupon_pct"),
        (DIFFERENT, "交易列和資料庫不同（資料庫 1 列、這次 1 列）"),
    ]
    assert pg.execute("SELECT coupon_pct, status_event FROM workflow.product").fetchone() == (Decimal("12.5"), "在庫")
    assert pg.execute("SELECT amount FROM workflow.trade_row").fetchone() == (Decimal("1"),)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, ("在庫", None, None)),
        ("已於2026/7/16 KO", ("KO", dt.date(2026, 7, 16), None)),
        ("已於2026/1/6KO", ("KO", dt.date(2026, 1, 6), None)),
        ("到期還本", ("到期還本", None, None)),
        ("已於2026/4/21 承接 TSLA UW", ("接股", dt.date(2026, 4, 21), "TSLA UW")),
        ("接SMCI UW股票", ("接股", None, "SMCI UW")),
        ("顧客贖回", ("顧客贖回", None, None)),
    ],
)
def test_inventory_status_is_split_into_event_date_and_underlying(
    pg, tmp_path: Path, mailbox: Path, raw, expected
) -> None:
    daily = write_daily(tmp_path / "d.xlsx", [], [inventory(A, [("北區", 1)], 庫存狀態=raw)])

    import_daily(daily, mailbox, pg)

    assert pg.execute(
        "SELECT status_event, status_date, status_underlying, status_raw FROM workflow.product"
    ).fetchone() == (
        *expected,
        raw,
    )


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"庫存狀態": "提前出場"}, "庫存狀態的寫法不認得"),
        ({"KO(Freq)": "W"}, "KO(Freq)／KO(memo) 的代碼「W／Y」不在對照表"),
        ({"KI(Freq)": "X"}, "KI(Freq) 的代碼「X」不在對照表"),
        ({"KI(%)": "-"}, "KI(%) 沒有值"),
        ({"到期日": "-"}, "到期日 沒有值"),
        ({"分公司_2": "南區"}, "分公司_2 與承作金額只有一邊有值"),
    ],
)
def test_bad_inventory_rows_are_blocked_with_a_reason(pg, tmp_path: Path, mailbox: Path, overrides, reason) -> None:
    daily = write_daily(
        tmp_path / "d.xlsx", [], [inventory(A, [("北區", 1)], **overrides), inventory(B, [("北區", 1)])]
    )

    receipt = import_daily(daily, mailbox, pg)

    blocked = [line for line in receipt.lines if line.kind == BLOCKED]
    assert len(blocked) == 1 and blocked[0].tdcc == A and reason in blocked[0].detail
    assert pg.execute("SELECT tdcc FROM workflow.product").fetchall() == [(B,)]


def test_no_ki_is_stored_as_none_barrier(pg, tmp_path: Path, mailbox: Path) -> None:
    daily = write_daily(tmp_path / "d.xlsx", [], [inventory(A, [("北區", 1)], **{"KI(Freq)": "-", "KI(%)": "-"})])

    import_daily(daily, mailbox, pg)

    assert pg.execute("SELECT barrier_type, ki_pct FROM workflow.product").fetchone() == ("NONE", None)


def test_a_bad_trade_row_blocks_all_rows_of_that_product(pg, tmp_path: Path, mailbox: Path) -> None:
    bad = trade(A, "Ben", "南區", 1, number=None)
    bad["金額"] = None
    daily = write_daily(tmp_path / "d.xlsx", [trade(A, "Amy", "北區", 1), bad, trade(B, "Amy", "北區", 1)], [])

    receipt = import_daily(daily, mailbox, pg)

    assert kinds(receipt, A) == [BLOCKED] and "金額 沒有值" in receipt.lines[0].detail
    assert pg.execute("SELECT DISTINCT tdcc FROM workflow.trade_row").fetchall() == [(B,)]


def test_branch_amounts_that_do_not_reconcile_are_reported_only(pg, tmp_path: Path, mailbox: Path) -> None:
    daily = write_daily(
        tmp_path / "d.xlsx",
        [trade(A, "Amy", "北區", 300000), trade(A, "PB", "南區", 50000, number=None)],
        [inventory(A, [("北區", 300000), ("南區", 100000)])],
    )

    receipt = import_daily(daily, mailbox, pg)

    assert [line.detail for line in receipt.lines if line.kind == RECONCILE] == [
        "南區：庫存 100,000、交易 50,000、差額 -50,000"
    ]
    assert count(pg, "workflow.product") == 1 and count(pg, "workflow.trade_row") == 2


def test_trades_before_inventory_are_kept_until_inventory_arrives(pg, tmp_path: Path, mailbox: Path) -> None:
    first = import_daily(write_daily(tmp_path / "1.xlsx", [trade(A, "Amy", "北區", 1)], []), mailbox, pg)
    second = import_daily(write_daily(tmp_path / "2.xlsx", [], [inventory(A, [("北區", 1)])]), mailbox, pg)

    assert INVENTORY_MISSING in kinds(first, A)
    assert kinds(second, A) == [NEW_PRODUCT]


def test_inventory_without_trades_is_reported(pg, tmp_path: Path, mailbox: Path) -> None:
    receipt = import_daily(write_daily(tmp_path / "d.xlsx", [], [inventory(A, [("北區", 1)])]), mailbox, pg)

    assert kinds(receipt, A) == [NEW_PRODUCT, TRADES_MISSING]


def test_fc_names_match_ignoring_case_and_through_aliases(pg, tmp_path: Path) -> None:
    mailbox = write_mailbox(tmp_path / "m.xlsx", aliases=[("Amie", "Amy")])
    daily = write_daily(
        tmp_path / "d.xlsx",
        [
            trade(A, "AMY", "北區", 1),
            trade(B, "Amie", "北區", 1),
            trade(C, "Zoe", "北區", 1),
            trade(D, "PB", "北區", 1),
        ],
        [],
    )

    receipt = import_daily(daily, mailbox, pg)

    assert [(line.tdcc, line.detail) for line in receipt.lines if line.kind == NAME_UNMATCHED] == [(C, "Zoe")]
    assert pg.execute("SELECT alias_key, person_name FROM mailbox.alias").fetchall() == [("amie", "Amy")]


def test_mailbox_replaces_previous_people(pg, tmp_path: Path, mailbox: Path) -> None:
    daily = write_daily(tmp_path / "d.xlsx", [], [])
    import_daily(daily, mailbox, pg)

    import_daily(daily, write_mailbox(tmp_path / "m2.xlsx", [("Cat", "cat@example.com", "FC", "")]), pg)

    assert pg.execute("SELECT name FROM mailbox.person").fetchall() == [("Cat",)]


def test_mailbox_without_fixed_cc_column_is_accepted(tmp_path: Path) -> None:
    box = read_mailbox(write_mailbox(tmp_path / "m.xlsx", fixed_cc_column=False))

    assert [p.fixed_cc for p in box.people] == [False, False, False]


def test_identical_duplicate_people_count_once(tmp_path: Path) -> None:
    people = [("Amy", "amy@example.com", "FC", ""), ("Amy", "amy@example.com", "FC", "")]

    assert len(read_mailbox(write_mailbox(tmp_path / "m.xlsx", people)).people) == 1


@pytest.mark.parametrize(
    ("people", "aliases", "message"),
    [
        ([("Amy", "not-an-email", "FC", "")], None, "格式不對"),
        ([("Amy", "a@example.com", "FC", ""), ("amy", "b@example.com", "FC", "")], None, "出現多次但內容不同"),
        ([("Amy", "a@example.com", "FC", "X")], None, "只能填 Y 或留白"),
        ([("Amy", "a@example.com", "FC", "")], [("Amie", "Nobody")], "對不到信箱表的使用者名稱"),
    ],
)
def test_mailbox_problems_stop_the_whole_import(pg, tmp_path: Path, people, aliases, message) -> None:
    daily = write_daily(tmp_path / "d.xlsx", [trade(A, "Amy", "北區", 1)], [inventory(A, [("北區", 1)])])

    with pytest.raises(DailyImportError, match=message):
        import_daily(daily, write_mailbox(tmp_path / "m.xlsx", people, aliases), pg)

    assert count(pg, "workflow.import_run") == 0 and count(pg, "workflow.trade_row") == 0


def test_wrong_header_rejects_the_whole_file(pg, tmp_path: Path, mailbox: Path) -> None:
    headers = list(TRADE_HEADERS)
    headers[3], headers[4] = headers[4], headers[3]
    daily = write_daily(tmp_path / "d.xlsx", [trade(A, "Amy", "北區", 1)], [], trade_headers=headers)

    with pytest.raises(DailyImportError, match="D 欄應為「TDCC」，實際是「幣別」"):
        import_daily(daily, mailbox, pg)

    assert count(pg, "workflow.import_run") == 0


def test_missing_sheet_rejects_the_whole_file(pg, tmp_path: Path, mailbox: Path) -> None:
    wb = openpyxl.Workbook()
    wb.active.title = daily_import.TRADE_SHEET
    wb.save(tmp_path / "d.xlsx")

    with pytest.raises(DailyImportError, match="缺少工作表「清單更新」"):
        import_daily(tmp_path / "d.xlsx", mailbox, pg)


def test_failure_halfway_leaves_nothing_behind(pg, tmp_path: Path, mailbox: Path, monkeypatch) -> None:
    daily = write_daily(tmp_path / "d.xlsx", [trade(A, "Amy", "北區", 1)], [inventory(A, [("北區", 1)])])

    def broken(*_args):
        raise RuntimeError("中途失敗")

    monkeypatch.setattr(daily_import, "_reconcile", broken)
    with pytest.raises(RuntimeError):
        import_daily(daily, mailbox, pg)

    assert [
        count(pg, t) for t in ("workflow.product", "workflow.trade_row", "workflow.import_run", "mailbox.person")
    ] == [0, 0, 0, 0]


def test_import_requires_the_latest_schema(pg, tmp_path: Path, mailbox: Path) -> None:
    pg.execute("DELETE FROM public.spw_schema_version")

    with pytest.raises(DatabaseError):
        require_current_schema(pg)
    with pytest.raises(DailyImportError, match="spw-db-migrate"):
        import_daily(write_daily(tmp_path / "d.xlsx", [], []), mailbox, pg)
