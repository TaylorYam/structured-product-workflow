"""連線設定與資料表版本（#32）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from db_synth import inventory, seed_products
from sp_workflow import database
from sp_workflow.database import DatabaseError, apply_migrations, load_env_file, settings_from_env
from sp_workflow.product_record import REQUIRED_COLUMNS

A = "029199990001"
IDENTITY_COLUMNS = ("tdcc", "product", "issuer", "status_event", "source")  # TDCC、商品種類、上手、狀態、來源


def test_env_file_is_read_without_exposing_values(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "# 註解\nPGHOST=db.local\nexport PGDATABASE=\"spw\"\nPGUSER=app\nPGPASSWORD='s3cret'\n", encoding="utf-8"
    )

    settings = settings_from_env(env)

    assert (settings.host, settings.port, settings.dbname, settings.user, settings.password) == (
        "db.local",
        "5432",
        "spw",
        "app",
        "s3cret",
    )


def test_missing_password_is_left_for_the_caller_to_ask(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("PGDATABASE=spw\nPGUSER=app\n", encoding="utf-8")

    assert settings_from_env(env).password is None


def test_bad_env_line_reports_only_the_line_number(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("PGDATABASE=spw\nthis-has-secret-but-no-equals\n", encoding="utf-8")

    with pytest.raises(DatabaseError) as info:
        load_env_file(env)

    assert "第 2 行" in str(info.value) and "secret" not in str(info.value)


def test_missing_settings_are_named(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("PGHOST=localhost\n", encoding="utf-8")

    with pytest.raises(DatabaseError, match="PGDATABASE, PGUSER"):
        settings_from_env(env)


def test_install_root_decides_where_env_lives(tmp_path: Path) -> None:
    assert database.default_env_path(tmp_path) == tmp_path / ".env"


def test_migration_files_are_packaged_in_order() -> None:
    files = database.migration_files()

    assert files[0][:2] == (1, "0001_minimal_fcn.sql")
    assert [v for v, _, _ in files] == sorted(v for v, _, _ in files)


@pytest.mark.db
def test_migrations_apply_once_from_an_empty_database(pg) -> None:
    assert apply_migrations(pg) == []  # pg fixture 已從空的資料庫套用過一次
    tables = pg.execute(
        "SELECT table_schema || '.' || table_name FROM information_schema.tables"
        " WHERE table_schema IN ('mailbox', 'workflow') ORDER BY 1"
    ).fetchall()
    assert [t for (t,) in tables] == [
        "mailbox.alias",
        "mailbox.person",
        "workflow.autocall_date",
        "workflow.backfill_log",
        "workflow.branch_allocation",
        "workflow.check_result",
        "workflow.check_terms",
        "workflow.cross_check_release",
        "workflow.daily_price",
        "workflow.import_run",
        "workflow.order_import",
        "workflow.order_import_line",
        "workflow.other_product",
        "workflow.price_import",
        "workflow.product",
        "workflow.step_record",
        "workflow.term_correction",
        "workflow.trade_row",
        "workflow.trade_row_correction",
        "workflow.underlying_price",
    ]


@pytest.mark.db
def test_database_on_0001_upgrades_to_0002_keeping_data(pg) -> None:
    """筆電的開發資料庫已套用 0001；升級後既有人員保留，比對鍵改由資料庫產生（#35）。"""
    pg.execute("DROP SCHEMA mailbox, workflow CASCADE")
    pg.execute(f"DELETE FROM public.{database.VERSION_TABLE}")
    version, name, text = database.migration_files()[0]
    pg.execute(text)
    pg.execute(f"INSERT INTO public.{database.VERSION_TABLE} (version, name) VALUES (%s, %s)", (version, name))
    pg.execute("INSERT INTO mailbox.person (name, name_key, email) VALUES ('Amy', 'amy', 'amy@example.com')")

    applied = apply_migrations(pg)

    assert applied == [n for _, n, _ in database.migration_files()[1:]]
    assert pg.execute("SELECT name, name_key, email FROM mailbox.person").fetchall() == [
        ("Amy", "amy", "amy@example.com")
    ]


def _nullable(pg) -> dict[str, bool]:
    return dict(
        pg.execute(
            "SELECT column_name, is_nullable = 'YES' FROM information_schema.columns"
            " WHERE table_schema = 'workflow' AND table_name = 'product'"
        ).fetchall()
    )


def _database_before(pg, version: int) -> None:
    """把測試資料庫重建成只套用到 `version` 之前的資料表版本（模擬還沒升級的資料庫）。"""
    pg.execute("DROP SCHEMA mailbox, workflow CASCADE")
    pg.execute(f"DELETE FROM public.{database.VERSION_TABLE}")
    for applied, name, text in database.migration_files():
        if applied >= version:
            break
        pg.execute(text)
        pg.execute(f"INSERT INTO public.{database.VERSION_TABLE} (version, name) VALUES (%s, %s)", (applied, name))


@pytest.mark.db
def test_upgrading_to_0012_lets_required_terms_be_blank_and_keeps_existing_products(pg) -> None:
    """#114：起單資料必填欄對應的欄可以空白，識別欄照舊不可空白；migration 前已有的商品不變。"""
    _database_before(pg, 12)
    seed_products(pg, [inventory(A, [("台北", 100000)])])
    before = pg.execute("SELECT * FROM workflow.product").fetchall()

    applied = apply_migrations(pg)

    assert applied[0] == "0012_partial_product_terms.sql"
    assert pg.execute("SELECT * FROM workflow.product").fetchall() == before
    nullable = _nullable(pg)
    blank = [c for c in REQUIRED_COLUMNS if c not in IDENTITY_COLUMNS]  # 含 KO Type／Barrier Type 換算的代碼欄
    assert [c for c in blank if not nullable[c]] == []
    assert [c for c in IDENTITY_COLUMNS if nullable[c]] == []


@pytest.mark.db
def test_upgrading_to_0013_keeps_corrections_and_allows_manual_products(pg) -> None:
    """#115：以前的更正紀錄都是已鎖住的商品；人工維護建立的商品沒有匯入批次，其他來源照舊要有。"""
    import psycopg

    _database_before(pg, 13)
    seed_products(pg, [inventory(A, [("台北", 100000)])])
    pg.execute(
        "INSERT INTO workflow.term_correction (tdcc, corrected_by, changes, previous) VALUES (%s, 'op', '[]', '{}')",
        (A,),
    )

    applied = apply_migrations(pg)

    assert applied[0] == "0013_manual_maintenance.sql"
    assert pg.execute("SELECT locked, previous FROM workflow.term_correction").fetchall() == [(True, {})]
    insert = "INSERT INTO workflow.product (tdcc, product, issuer, status_event, source) VALUES (%s, 'FCN', 'SG', '在庫', %s)"
    pg.execute(insert, ("037199990002", "人工維護"))
    pg.execute(
        "INSERT INTO workflow.term_correction (tdcc, corrected_by, changes, locked) VALUES (%s, 'op', '[]', false)",
        ("037199990002",),
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        pg.execute(insert, ("037199990003", "Order"))  # 起單資料匯入的商品照舊要有匯入批次


@pytest.mark.db
def test_a_product_can_be_stored_with_only_some_required_terms(pg) -> None:
    """#114：人工維護可以先存一部分商品條件；上手這類識別欄照舊不可空白。"""
    import psycopg

    (import_id,) = pg.execute(
        "INSERT INTO workflow.order_import (imported_by, summary) VALUES ('tester', '{}') RETURNING import_id"
    ).fetchone()
    insert = (
        "INSERT INTO workflow.product (tdcc, product, issuer, currency, status_event, source, order_import_id)"
        " VALUES (%s, 'FCN', %s, 'USD', '在庫', 'Order', %s)"
    )

    pg.execute(insert, (A, "Barclays", import_id))

    assert pg.execute("SELECT currency, maturity_date, strike_pct FROM workflow.product").fetchall() == [
        ("USD", None, None)
    ]
    with pytest.raises(psycopg.errors.NotNullViolation):
        pg.execute(insert, ("029199990002", None, import_id))  # 上手照舊不可空白


@pytest.mark.db
def test_step_records_migration_keeps_products(pg) -> None:
    """資料表版本 0015 只新增步驟紀錄表（#151），既有的商品條件不動。"""
    _database_before(pg, 15)
    pg.execute(
        "INSERT INTO workflow.product (tdcc, product, issuer, status_event, source) VALUES (%s, 'FCN', 'SG', '在庫', '人工維護')",
        ("147000000001",),
    )

    apply_migrations(pg)

    assert pg.execute("SELECT tdcc, issuer, source FROM workflow.product").fetchall() == [
        ("147000000001", "SG", "人工維護")
    ]
    assert pg.execute("SELECT count(*) FROM workflow.step_record").fetchone()[0] == 0
