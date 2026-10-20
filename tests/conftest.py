"""共用 fixture 與選項。

- `pg`：測試用 PostgreSQL（設定 SPW_TEST_DSN 才執行，CI 由 PostgreSQL 服務提供）。
- `--shard=i/n`：只跑第 i 份（依測試檔分組，見 shard.py；CI 用來把核對測試分給幾台機器同時跑，#69）。
"""

from __future__ import annotations

import os

import pytest

from shard import assign_shards, parse_shard

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


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--shard", default=None, help="i/n：只跑第 i 份（共 n 份，同一個測試檔在同一份）")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    spec = config.getoption("--shard")
    if not spec:
        return
    try:
        index, total = parse_shard(spec)
    except ValueError as exc:
        raise pytest.UsageError(str(exc)) from None
    counts: dict[str, int] = {}
    for item in items:
        path = item.nodeid.split("::", 1)[0]
        counts[path] = counts.get(path, 0) + 1
    keep = assign_shards(counts, total)[index - 1]
    selected = [item for item in items if item.nodeid.split("::", 1)[0] in keep]
    deselected = [item for item in items if item.nodeid.split("::", 1)[0] not in keep]
    if deselected:
        config.hook.pytest_deselected(items=deselected)
    items[:] = selected
