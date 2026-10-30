"""連線設定與資料表版本（#32）。"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pytest

from db_synth import inventory, seed_products
from sp_workflow import database
from sp_workflow.database import (
    VERSION_TABLE,
    DatabaseError,
    OperatorError,
    SchemaOutdatedError,
    apply_migrations,
    connect,
    connect_raw,
    load_env_file,
    migrate_main,
    require_in_transaction,
    settings_from_env,
)
from sp_workflow.product_record import REQUIRED_COLUMNS

A = "029199990001"
IDENTITY_COLUMNS = ("tdcc", "product", "issuer", "source")  # TDCC、商品種類、上手、來源（舊狀態欄 0020 起移除，#172）


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


OUTDATED = "資料庫的資料表還沒建立或不是最新版本，請先執行 spw-db-migrate。"


def test_operator_errors_share_one_base() -> None:
    assert issubclass(SchemaOutdatedError, DatabaseError) and issubclass(DatabaseError, OperatorError)


@pytest.mark.db
def test_connect_checks_the_schema_version_once(pg, pg_settings) -> None:
    with connect(pg_settings) as conn:
        assert conn.execute("SELECT 1").fetchone() == (1,), "最新版本：連得上、可以直接用"

    pg.execute(f"DELETE FROM public.{VERSION_TABLE} WHERE version = (SELECT max(version) FROM public.{VERSION_TABLE})")

    with pytest.raises(SchemaOutdatedError) as info:
        connect(pg_settings)
    assert str(info.value) == OUTDATED
    with connect_raw(pg_settings) as conn:
        assert conn.execute("SELECT 1").fetchone() == (1,), "遷移工具用的連線不檢查版本"


@pytest.mark.db
def test_functions_in_the_callers_transaction_refuse_to_run_outside_one(pg_settings) -> None:
    """#205：沒有進行中的 transaction 時丟 `RuntimeError`（用 `if`，`python -O` 也照樣檢查）。"""
    with connect(pg_settings) as conn:
        with pytest.raises(RuntimeError, match="IDLE"):
            require_in_transaction(conn)
        with conn.transaction():
            require_in_transaction(conn)


@pytest.mark.db
def test_broken_migration_files_are_not_a_login_problem(pg, pg_settings, monkeypatch) -> None:
    def duplicated():
        raise DatabaseError("資料表版本檔的編號重複")

    monkeypatch.setattr(database, "migration_files", duplicated)

    with pytest.raises(SchemaOutdatedError, match="編號重複"):
        connect(pg_settings)


@pytest.mark.db
def test_connect_without_any_schema_is_outdated(pg, pg_settings) -> None:
    pg.execute("DROP SCHEMA mailbox, workflow CASCADE")
    pg.execute(f"DROP TABLE public.{VERSION_TABLE}")

    with pytest.raises(SchemaOutdatedError, match="spw-db-migrate"):
        connect(pg_settings)


@pytest.mark.db
def test_migrate_builds_the_schema_on_an_empty_database(pg, pg_settings, tmp_path: Path, capsys, monkeypatch) -> None:
    pg.execute("DROP SCHEMA mailbox, workflow CASCADE")
    pg.execute(f"DROP TABLE public.{VERSION_TABLE}")
    env = tmp_path / ".env"
    env.write_text(
        f"PGHOST={pg_settings.host}\nPGPORT={pg_settings.port}\nPGDATABASE={pg_settings.dbname}\nPGUSER={pg_settings.user}\n",
        encoding="utf-8",
    )
    password = os.environ.get("PGPASSWORD") or pg_settings.password or "trust"  # CI 的 PostgreSQL 不驗密碼
    monkeypatch.setattr(database, "getpass", lambda prompt: password)

    assert migrate_main(["--env-file", str(env), "--admin-user", pg_settings.user]) == 0
    assert "已套用" in capsys.readouterr().out
    connect(pg_settings).close()  # 套用後日常連線通過版本檢查


@pytest.mark.db
def test_migrations_apply_once_from_an_empty_database(pg) -> None:
    assert apply_migrations(pg) == []  # pg fixture 已從空的資料庫套用過一次
    tables = pg.execute(
        "SELECT table_schema || '.' || table_name FROM information_schema.tables"
        " WHERE table_schema IN ('mailbox', 'workflow') ORDER BY 1"
    ).fetchall()
    assert [t for (t,) in tables] == [
        "mailbox.alias",
        "mailbox.change_log",
        "mailbox.person",
        "workflow.autocall_date",
        "workflow.backfill_log",
        "workflow.branch_allocation",
        "workflow.check_result",
        "workflow.check_terms",
        "workflow.close_price",
        "workflow.close_price_change",
        "workflow.close_price_import",
        "workflow.credit_rating",
        "workflow.credit_rating_log",
        "workflow.cross_check_release",
        "workflow.daily_price",
        "workflow.eln_whitelist",
        "workflow.eln_whitelist_import",
        "workflow.event_objection",
        "workflow.import_run",
        "workflow.order_import",
        "workflow.order_import_line",
        "workflow.other_product",
        "workflow.price_import",
        "workflow.pricing_status",
        "workflow.product",
        "workflow.product_event",
        "workflow.product_event_change",
        "workflow.rated_entity",
        "workflow.risk_level",
        "workflow.step_record",
        "workflow.stock_split",
        "workflow.stock_split_log",
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


STATUS_COLUMNS = {"status_event", "status_date", "status_underlying", "status_raw"}


def _products(pg) -> list[dict]:
    """全部商品（舊狀態欄除外：資料表版本 0020 會移除，#172）。"""
    cursor = pg.execute("SELECT * FROM workflow.product ORDER BY tdcc")
    names = [column.name for column in cursor.description]
    return [{k: v for k, v in zip(names, row, strict=True) if k not in STATUS_COLUMNS} for row in cursor.fetchall()]


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
    before = _products(pg)

    applied = apply_migrations(pg)

    assert applied[0] == "0012_partial_product_terms.sql"
    assert [
        {k: row[k] for k in before[0]} for row in _products(pg)
    ] == before  # 之後的 migration 新增的欄不比（例：0032～0034）
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
    insert = "INSERT INTO workflow.product (tdcc, product, issuer, source) VALUES (%s, 'FCN', 'SG', %s)"
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
        "INSERT INTO workflow.product (tdcc, product, issuer, currency, source, order_import_id)"
        " VALUES (%s, 'FCN', %s, 'USD', 'Order', %s)"
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


@pytest.mark.db
def test_old_statuses_become_old_list_events(pg) -> None:
    """資料表版本 0016（#152）：不是在庫的舊狀態轉成來源「舊清單」的事件，原欄位保留、之後新商品不再寫（版本 0020 再移除）。"""
    _database_before(pg, 16)
    insert = (
        "INSERT INTO workflow.product (tdcc, product, issuer, source, status_event, status_date, status_underlying,"
        " status_raw) VALUES (%s, 'FCN', 'SG', '人工維護', %s, %s, %s, %s)"
    )
    pg.execute(insert, ("147000000001", "KO", dt.date(2026, 9, 1), None, "已於 2026/9/1 KO"))
    pg.execute(insert, ("147000000002", "接股", dt.date(2026, 8, 3), "AAA UW", "接股 AAA"))
    pg.execute(insert, ("147000000003", "在庫", None, None, None))
    pg.execute(insert, ("147000000004", "到期還本", None, None, "到期還本"))

    apply_migrations(pg)

    events = pg.execute(
        "SELECT tdcc, event, event_date, underlying, source, note, superseded_at FROM workflow.product_event"
        " ORDER BY tdcc"
    ).fetchall()
    assert events == [
        ("147000000001", "KO", dt.date(2026, 9, 1), None, "舊清單", "已於 2026/9/1 KO", None),
        ("147000000002", "接股", dt.date(2026, 8, 3), "AAA UW", "舊清單", "接股 AAA", None),
        ("147000000004", "到期還本", None, None, "舊清單", "到期還本", None),
    ], "在庫的不轉；沒有日期的照樣轉、事件日空白"
    assert not STATUS_COLUMNS & set(_nullable(pg)), "資料表版本 0020（#172）把舊狀態欄移除"


@pytest.mark.db
def test_close_prices_migration_keeps_products(pg) -> None:
    """資料表版本 0017 只新增收盤價表（#153），既有的商品條件不動。"""
    _database_before(pg, 17)
    pg.execute(
        "INSERT INTO workflow.product (tdcc, product, issuer, status_event, source) VALUES (%s, 'FCN', 'SG', '在庫', '人工維護')",
        ("147000000001",),
    )

    apply_migrations(pg)

    assert pg.execute("SELECT tdcc, issuer, source FROM workflow.product").fetchall() == [
        ("147000000001", "SG", "人工維護")
    ]
    assert pg.execute("SELECT count(*) FROM workflow.close_price").fetchone()[0] == 0


@pytest.mark.db
def test_pricing_status_migration_keeps_products(pg) -> None:
    """資料表版本 0018 只新增比價狀態表（#155），既有的商品條件與存續事件不動。"""
    _database_before(pg, 18)
    pg.execute(
        "INSERT INTO workflow.product (tdcc, product, issuer, status_event, source) VALUES (%s, 'FCN', 'SG', '在庫', '人工維護')",
        ("147000000001",),
    )

    apply_migrations(pg)

    assert pg.execute("SELECT tdcc, issuer, source FROM workflow.product").fetchall() == [
        ("147000000001", "SG", "人工維護")
    ]
    assert pg.execute("SELECT count(*) FROM workflow.pricing_status").fetchone()[0] == 0
    assert pg.execute("SELECT count(*) FROM workflow.product_event").fetchone()[0] == 0


def _old_status(pg, tdcc: str, status: str | None, day: dt.date | None = None) -> None:
    """0016 之後、0020 之前的商品：舊狀態欄還在（新商品是空白）。"""
    pg.execute(
        "INSERT INTO workflow.product (tdcc, product, issuer, source, status_event, status_date, status_raw)"
        " VALUES (%s, 'FCN', 'SG', '人工維護', %s, %s, %s)",
        (tdcc, status, day, status),
    )


def _old_list_event(pg, tdcc: str, event: str, day: dt.date | None) -> None:
    pg.execute(
        "INSERT INTO workflow.product_event (tdcc, event, event_date, source, recorded_by)"
        " VALUES (%s, %s, %s, '舊清單', '資料表版本 0016')",
        (tdcc, event, day),
    )


@pytest.mark.db
def test_old_status_columns_are_dropped_and_everything_else_is_kept(pg) -> None:
    """資料表版本 0020（#172）：每個舊狀態都有舊清單事件時，刪掉四個舊狀態欄，商品與事件不動。"""
    _database_before(pg, 20)
    _old_status(pg, "147000000001", "KO", dt.date(2026, 9, 1))
    _old_list_event(pg, "147000000001", "KO", dt.date(2026, 9, 1))
    _old_status(pg, "147000000002", "到期還本")
    _old_list_event(pg, "147000000002", "到期還本", None)
    _old_status(pg, "147000000003", "在庫")
    _old_status(pg, "147000000004", None)  # 0016 之後建立的商品不寫舊狀態

    assert apply_migrations(pg)[0] == "0020_drop_product_status.sql"

    assert not STATUS_COLUMNS & set(_nullable(pg))
    assert pg.execute("SELECT tdcc, issuer, source FROM workflow.product ORDER BY tdcc").fetchall() == [
        (f"14700000000{i}", "SG", "人工維護") for i in range(1, 5)
    ]
    assert pg.execute("SELECT tdcc, event, event_date FROM workflow.product_event ORDER BY tdcc").fetchall() == [
        ("147000000001", "KO", dt.date(2026, 9, 1)),
        ("147000000002", "到期還本", None),
    ]


@pytest.mark.db
def test_an_old_status_without_its_old_list_event_stops_the_migration(pg) -> None:
    """舊狀態沒有對應（同一種事件、同一個事件日）的舊清單事件時整個版本失敗、寫出 TDCC，什麼都不改。"""
    import psycopg

    _database_before(pg, 20)
    _old_status(pg, "147000000001", "KO", dt.date(2026, 9, 1))
    _old_list_event(pg, "147000000001", "KO", dt.date(2026, 9, 1))
    _old_status(pg, "147000000002", "KO", dt.date(2026, 9, 2))
    _old_list_event(pg, "147000000002", "KO", dt.date(2026, 9, 3))  # 日期不同
    _old_status(pg, "147000000003", "接股", dt.date(2026, 8, 3))  # 沒有事件

    with pytest.raises(psycopg.errors.RaiseException, match="147000000002、147000000003"):
        apply_migrations(pg)

    assert STATUS_COLUMNS <= set(_nullable(pg)), "舊狀態欄照舊在"
    assert 20 not in database.applied_versions(pg)


@pytest.mark.db
def test_old_list_maturity_gets_the_final_valuation_date(pg) -> None:
    """資料表版本 0021（#189）：舊清單到期還本沒有事件日的補成商品的最終比價日，其他事件不動，事件照樣不能改。"""
    import psycopg

    _database_before(pg, 21)
    tdccs = [f"02919999000{i}" for i in range(1, 6)]
    seed_products(pg, [inventory(t, [("台北", 100000)]) for t in tdccs])  # 最終比價日 2027-04-22
    pg.execute("UPDATE workflow.product SET final_valuation_date = NULL WHERE tdcc = %s", (tdccs[1],))
    insert = (
        "INSERT INTO workflow.product_event (tdcc, event, event_date, source, note, recorded_by)"
        " VALUES (%s, %s, %s, %s, %s, '資料表版本 0016')"
    )
    pg.execute(insert, (tdccs[0], "到期還本", None, "舊清單", "到期還本"))
    pg.execute(insert, (tdccs[1], "到期還本", None, "舊清單", None))  # 商品沒有最終比價日
    pg.execute(insert, (tdccs[2], "KO", None, "舊清單", None))
    pg.execute(insert, (tdccs[3], "到期還本", dt.date(2027, 4, 20), "舊清單", None))  # 已有日期
    pg.execute(insert, (tdccs[4], "到期還本", dt.date(2027, 4, 22), "比價", None))

    assert apply_migrations(pg)[0] == "0021_legacy_maturity_date.sql"

    added = "事件日由資料表版本 0021 補成最終比價日（#189）"
    assert pg.execute("SELECT tdcc, event, event_date, note FROM workflow.product_event ORDER BY tdcc").fetchall() == [
        (tdccs[0], "到期還本", dt.date(2027, 4, 22), f"到期還本；{added}"),
        (tdccs[1], "到期還本", None, None),
        (tdccs[2], "KO", None, None),
        (tdccs[3], "到期還本", dt.date(2027, 4, 20), None),
        (tdccs[4], "到期還本", dt.date(2027, 4, 22), None),
    ]
    with pytest.raises(psycopg.errors.RaiseException, match="存續事件不能修改"):
        pg.execute("UPDATE workflow.product_event SET event_date = NULL WHERE tdcc = %s", (tdccs[0],))


@pytest.mark.db
def test_pricing_status_gets_a_blank_ki_column(pg) -> None:
    """資料表版本 0022（#196）：比價狀態加 KI 欄，既有的比價狀態不動、KI 空白，下一次比價補上。"""
    _database_before(pg, 22)
    seed_products(pg, [inventory(A, [("台北", 100000)])])
    pg.execute(
        "INSERT INTO workflow.pricing_status (tdcc, status, reason, observed_through, locked)"
        " VALUES (%s, '在庫', '還沒到第一個觀察日', NULL, true)",
        (A,),
    )

    assert apply_migrations(pg)[0] == "0022_pricing_ki.sql"

    assert pg.execute("SELECT status, reason, ki FROM workflow.pricing_status").fetchall() == [
        ("在庫", "還沒到第一個觀察日", None)
    ]


@pytest.mark.db
def test_chinese_ki_is_cleared_for_the_english_codes(pg) -> None:
    """資料表版本 0023（#198）：清掉中文 KI，下一次比價改寫英文代碼；比價狀態其他欄不動。"""
    _database_before(pg, 23)
    seed_products(pg, [inventory(A, [("台北", 100000)])])
    pg.execute(
        "INSERT INTO workflow.pricing_status (tdcc, status, reason, observed_through, locked, ki)"
        " VALUES (%s, '在庫', 'Non-call period', NULL, true, '未觸及')",
        (A,),
    )

    assert apply_migrations(pg) == [name for version, name, _ in database.migration_files() if version >= 23]

    assert pg.execute("SELECT status, reason, ki FROM workflow.pricing_status").fetchall() == [
        ("在庫", "Non-call period", None)
    ]
