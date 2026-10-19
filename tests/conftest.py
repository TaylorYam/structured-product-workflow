"""共用 fixture。`pg`：測試用 PostgreSQL（設定 SPW_TEST_DSN 才執行，CI 由 PostgreSQL 服務提供）。"""

from __future__ import annotations

import os

import pytest

TEST_DSN_ENV = "SPW_TEST_DSN"


@pytest.fixture
def pg():
    """每個測試從空的 schema 開始：刪掉 mailbox、workflow 與版本表，再套用全部版本檔。

    會清空資料，所以只接受名稱以 `_test` 結尾的資料庫。
    """
    dsn = os.environ.get(TEST_DSN_ENV)
    if not dsn:
        pytest.skip(f"沒有設定 {TEST_DSN_ENV}")
    import psycopg

    from sp_workflow.database import VERSION_TABLE, apply_migrations

    conn = psycopg.connect(dsn, autocommit=True)
    if not conn.info.dbname.endswith("_test"):
        conn.close()
        pytest.fail(f"{TEST_DSN_ENV} 指向的資料庫名稱要以 _test 結尾（測試會清空資料）")
    conn.execute("DROP SCHEMA IF EXISTS mailbox, workflow CASCADE")
    conn.execute(f"DROP TABLE IF EXISTS public.{VERSION_TABLE}")
    apply_migrations(conn)
    yield conn
    conn.close()
