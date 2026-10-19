"""每日匯入（#32、#35）：虛構的每日更新，對真的 PostgreSQL 匯入；信箱直接寫進資料庫。資料全部虛構。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest

from db_synth import inventory, seed_mailbox, trade, write_daily
from sp_workflow import daily_import
from sp_workflow.daily_import import (
    BLOCKED,
    DIFFERENT,
    INVENTORY_MISSING,
    MAILBOX_EMPTY,
    NAME_UNMATCHED,
    NEW_PRODUCT,
    NEW_TRADES,
    RECONCILE,
    TRADE_HEADERS,
    TRADES_MISSING,
    UNCHANGED,
    DailyImportError,
    import_daily,
    is_pb,
    latest_import,
)
from sp_workflow.database import DatabaseError, require_current_schema

pytestmark = pytest.mark.db

A = "029199990001"
B = "037199990002"
C = "076199990003"
D = "325199990004"


@pytest.fixture
def mailbox(pg) -> None:
    seed_mailbox(pg)


def kinds(receipt, tdcc: str) -> list[str]:
    return [line.kind for line in receipt.lines if line.tdcc == tdcc]


def count(pg, table: str) -> int:
    return pg.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_new_products_trades_and_people_are_written(pg, tmp_path: Path, mailbox) -> None:
    daily = write_daily(
        tmp_path / "每日更新.xlsx",
        [
            trade(A, "Amy", "北區", 300000),
            trade(A, "Ben", "南區", 100000, number=None),
            trade(B, "Ben", "南區", 500000),
        ],
        [inventory(A, [("北區", 300000), ("南區", 100000)]), inventory(B, [("南區", 500000)])],
    )

    receipt = import_daily(daily, pg)

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
    summary = pg.execute("SELECT daily_file_name, summary FROM workflow.import_run").fetchone()
    assert summary[0] == "每日更新.xlsx" and summary[1]["counts"][NEW_PRODUCT] == 2


def test_tdcc_loses_no_leading_zero(pg, tmp_path: Path, mailbox) -> None:
    daily = write_daily(tmp_path / "d.xlsx", [trade(int(A), "Amy", "北區", 1)], [inventory(int(A), [("北區", 1)])])

    import_daily(daily, pg)

    assert pg.execute("SELECT tdcc FROM workflow.product").fetchall() == [(A,)]
    assert pg.execute("SELECT DISTINCT tdcc FROM workflow.trade_row").fetchall() == [(A,)]


def test_other_products_are_skipped_and_counted(pg, tmp_path: Path, mailbox) -> None:
    daily = write_daily(
        tmp_path / "d.xlsx",
        [trade(A, "Amy", "北區", 1), trade(D, "Amy", "北區", 1, product="BEN")],
        [inventory(A, [("北區", 1)]), inventory(D, [("北區", 1)], Product="BEN")],
    )

    receipt = import_daily(daily, pg)

    assert (receipt.skipped_trade_rows, receipt.skipped_inventory_rows) == (1, 1)
    assert count(pg, "workflow.product") == 1 and kinds(receipt, D) == []
    assert pg.execute("SELECT tdcc, product FROM workflow.other_product").fetchall() == [(D, "BEN")]


def test_latest_import_describes_the_newest_run(pg, tmp_path: Path, mailbox) -> None:
    assert latest_import(pg) is None
    import_daily(write_daily(tmp_path / "1.xlsx", [], []), pg)
    import_daily(write_daily(tmp_path / "2.xlsx", [], []), pg)

    info = latest_import(pg)

    assert (info.run_id, info.file_name, len(info.sha256)) == (2, "2.xlsx", 64)


@pytest.mark.parametrize("name", ["PB", " pb ", "Pb"])
def test_pb_is_recognized_however_it_is_typed(name) -> None:
    assert is_pb(name)


def test_importing_the_same_file_again_adds_nothing(pg, tmp_path: Path, mailbox) -> None:
    daily = write_daily(tmp_path / "d.xlsx", [trade(A, "Amy", "北區", 1)], [inventory(A, [("北區", 1)])])
    import_daily(daily, pg)

    receipt = import_daily(daily, pg)

    assert kinds(receipt, A) == [UNCHANGED, UNCHANGED]
    assert (count(pg, "workflow.product"), count(pg, "workflow.trade_row"), count(pg, "workflow.import_run")) == (
        1,
        1,
        2,
    )


def test_changed_terms_are_reported_not_overwritten(pg, tmp_path: Path, mailbox) -> None:
    import_daily(write_daily(tmp_path / "1.xlsx", [trade(A, "Amy", "北區", 1)], [inventory(A, [("北區", 1)])]), pg)
    changed = write_daily(
        tmp_path / "2.xlsx",
        [trade(A, "Amy", "北區", 2)],
        [inventory(A, [("北區", 1)], **{"Coupon p.a. (%)": 13, "庫存狀態": "已於2026/11/2 KO"})],
    )

    receipt = import_daily(changed, pg)

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
def test_inventory_status_is_split_into_event_date_and_underlying(pg, tmp_path: Path, mailbox, raw, expected) -> None:
    daily = write_daily(tmp_path / "d.xlsx", [], [inventory(A, [("北區", 1)], 庫存狀態=raw)])

    import_daily(daily, pg)

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
def test_bad_inventory_rows_are_blocked_with_a_reason(pg, tmp_path: Path, mailbox, overrides, reason) -> None:
    daily = write_daily(
        tmp_path / "d.xlsx", [], [inventory(A, [("北區", 1)], **overrides), inventory(B, [("北區", 1)])]
    )

    receipt = import_daily(daily, pg)

    blocked = [line for line in receipt.lines if line.kind == BLOCKED]
    assert len(blocked) == 1 and blocked[0].tdcc == A and reason in blocked[0].detail
    assert pg.execute("SELECT tdcc FROM workflow.product").fetchall() == [(B,)]


def test_no_ki_is_stored_as_none_barrier(pg, tmp_path: Path, mailbox) -> None:
    daily = write_daily(tmp_path / "d.xlsx", [], [inventory(A, [("北區", 1)], **{"KI(Freq)": "-", "KI(%)": "-"})])

    import_daily(daily, pg)

    assert pg.execute("SELECT barrier_type, ki_pct FROM workflow.product").fetchone() == ("NONE", None)


def test_a_bad_trade_row_blocks_all_rows_of_that_product(pg, tmp_path: Path, mailbox) -> None:
    bad = trade(A, "Ben", "南區", 1, number=None)
    bad["金額"] = None
    daily = write_daily(tmp_path / "d.xlsx", [trade(A, "Amy", "北區", 1), bad, trade(B, "Amy", "北區", 1)], [])

    receipt = import_daily(daily, pg)

    assert kinds(receipt, A) == [BLOCKED] and "金額 沒有值" in receipt.lines[0].detail
    assert pg.execute("SELECT DISTINCT tdcc FROM workflow.trade_row").fetchall() == [(B,)]


def test_branch_amounts_that_do_not_reconcile_are_reported_only(pg, tmp_path: Path, mailbox) -> None:
    daily = write_daily(
        tmp_path / "d.xlsx",
        [trade(A, "Amy", "北區", 300000), trade(A, "PB", "南區", 50000, number=None)],
        [inventory(A, [("北區", 300000), ("南區", 100000)])],
    )

    receipt = import_daily(daily, pg)

    assert [line.detail for line in receipt.lines if line.kind == RECONCILE] == [
        "南區：庫存 100,000、交易 50,000、差額 -50,000"
    ]
    assert count(pg, "workflow.product") == 1 and count(pg, "workflow.trade_row") == 2


def test_trades_before_inventory_are_kept_until_inventory_arrives(pg, tmp_path: Path, mailbox) -> None:
    first = import_daily(write_daily(tmp_path / "1.xlsx", [trade(A, "Amy", "北區", 1)], []), pg)
    second = import_daily(write_daily(tmp_path / "2.xlsx", [], [inventory(A, [("北區", 1)])]), pg)

    assert INVENTORY_MISSING in kinds(first, A)
    assert kinds(second, A) == [NEW_PRODUCT]


def test_inventory_without_trades_is_reported(pg, tmp_path: Path, mailbox) -> None:
    receipt = import_daily(write_daily(tmp_path / "d.xlsx", [], [inventory(A, [("北區", 1)])]), pg)

    assert kinds(receipt, A) == [NEW_PRODUCT, TRADES_MISSING]


def test_fc_names_match_ignoring_case_and_through_aliases(pg, tmp_path: Path) -> None:
    seed_mailbox(pg, aliases=[("Amie", "Amy")])
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

    receipt = import_daily(daily, pg)

    assert [(line.tdcc, line.detail) for line in receipt.lines if line.kind == NAME_UNMATCHED] == [(C, "Zoe")]


def test_import_does_not_change_the_mailbox(pg, tmp_path: Path, mailbox) -> None:
    before = pg.execute("SELECT name, email, unit, fixed_cc FROM mailbox.person ORDER BY name").fetchall()

    receipt = import_daily(write_daily(tmp_path / "d.xlsx", [trade(A, "Amy", "北區", 1)], []), pg)

    assert pg.execute("SELECT name, email, unit, fixed_cc FROM mailbox.person ORDER BY name").fetchall() == before
    assert receipt.people == 3


def test_empty_mailbox_is_reported_once(pg, tmp_path: Path) -> None:
    receipt = import_daily(write_daily(tmp_path / "d.xlsx", [trade(A, "Amy", "北區", 1)], []), pg)

    assert [line.kind for line in receipt.lines if line.problem] == [MAILBOX_EMPTY, INVENTORY_MISSING]
    assert count(pg, "workflow.trade_row") == 1


def test_mailbox_keys_are_generated_by_the_database(pg) -> None:
    seed_mailbox(pg, [(" Amy ", "amy@example.com", "FC", False)], aliases=[("Amie", " Amy ")])

    assert pg.execute("SELECT name_key FROM mailbox.person").fetchone() == ("amy",)
    assert pg.execute("SELECT alias_key FROM mailbox.alias").fetchone() == ("amie",)


@pytest.mark.parametrize(
    ("sql", "params"),
    [
        ("INSERT INTO mailbox.person (name, email) VALUES (%s, %s)", ("amy", "other@example.com")),
        ("INSERT INTO mailbox.person (name, email) VALUES (%s, %s)", ("Cat", "not-an-email")),
        ("INSERT INTO mailbox.alias (alias, person_name) VALUES (%s, %s)", ("Zed", "Nobody")),
        ("INSERT INTO mailbox.alias (alias, person_name) VALUES (%s, %s)", ("AMIE", "Ben")),
    ],
)
def test_database_rejects_bad_mailbox_rows(pg, sql, params) -> None:
    import psycopg

    seed_mailbox(pg, aliases=[("Amie", "Amy")])

    with pytest.raises(psycopg.errors.IntegrityError):
        pg.execute(sql, params)


def test_renaming_a_person_keeps_their_aliases(pg) -> None:
    seed_mailbox(pg, aliases=[("Amie", "Amy")])

    pg.execute("UPDATE mailbox.person SET name = 'Amelia' WHERE name = 'Amy'")

    assert pg.execute("SELECT alias, person_name FROM mailbox.alias").fetchall() == [("Amie", "Amelia")]


def test_wrong_header_rejects_the_whole_file(pg, tmp_path: Path, mailbox) -> None:
    headers = list(TRADE_HEADERS)
    headers[3], headers[4] = headers[4], headers[3]
    daily = write_daily(tmp_path / "d.xlsx", [trade(A, "Amy", "北區", 1)], [], trade_headers=headers)

    with pytest.raises(DailyImportError, match="D 欄應為「TDCC」，實際是「幣別」"):
        import_daily(daily, pg)

    assert count(pg, "workflow.import_run") == 0


def test_missing_sheet_rejects_the_whole_file(pg, tmp_path: Path, mailbox) -> None:
    wb = openpyxl.Workbook()
    wb.active.title = daily_import.TRADE_SHEET
    wb.save(tmp_path / "d.xlsx")

    with pytest.raises(DailyImportError, match="缺少工作表「清單更新」"):
        import_daily(tmp_path / "d.xlsx", pg)


def test_failure_halfway_leaves_nothing_behind(pg, tmp_path: Path, mailbox, monkeypatch) -> None:
    daily = write_daily(tmp_path / "d.xlsx", [trade(A, "Amy", "北區", 1)], [inventory(A, [("北區", 1)])])

    def broken(*_args):
        raise RuntimeError("中途失敗")

    monkeypatch.setattr(daily_import, "_reconcile", broken)
    with pytest.raises(RuntimeError):
        import_daily(daily, pg)

    assert [count(pg, t) for t in ("workflow.product", "workflow.trade_row", "workflow.import_run")] == [0, 0, 0]


def test_import_requires_the_latest_schema(pg, tmp_path: Path, mailbox) -> None:
    pg.execute("DELETE FROM public.spw_schema_version")

    with pytest.raises(DatabaseError):
        require_current_schema(pg)
    with pytest.raises(DailyImportError, match="spw-db-migrate"):
        import_daily(write_daily(tmp_path / "d.xlsx", [], []), pg)
