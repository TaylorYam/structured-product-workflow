"""連線設定與資料表版本（#32）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from sp_workflow import database
from sp_workflow.database import DatabaseError, apply_migrations, load_env_file, settings_from_env


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
        "workflow.term_correction",
        "workflow.trade_row",
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
