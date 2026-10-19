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
        "workflow.branch_allocation",
        "workflow.import_run",
        "workflow.product",
        "workflow.trade_row",
    ]
