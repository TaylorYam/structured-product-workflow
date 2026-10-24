"""共用 fixture 與選項。

- `pg`：測試用 PostgreSQL（設定 SPW_TEST_DSN 才執行，CI 由 PostgreSQL 服務提供）。
- `pg_settings`：同一個測試資料庫的連線設定（自己開連線的視窗動作用）。
- `tk_root`、`master`：開視窗的測試用的 Tk（整個測試程序一個）與每個測試自己的 Toplevel；沒有顯示器（CI）時略過（#138）。
- `immediate`：建視窗時注入的同步背景執行器（#206），`new_window(..., background=immediate)`。
- `--shard=i/n`：只跑第 i 份（依測試檔分組，見 shard.py；CI 用來把核對測試分給幾台機器同時跑，#69）。
"""

from __future__ import annotations

import os
import tkinter as tk

import pytest

from shard import assign_shards, parse_shard

TEST_DSN_ENV = "SPW_TEST_DSN"


@pytest.fixture(scope="session")
def tk_root():
    """整個測試程序共用一個 Tk：在 Windows 上一再重建 Tk 偶爾會找不到 tk.tcl（拆成多個測試檔後一個 module 一個也會，#138）。"""
    try:
        root = tk.Tk()
    except tk.TclError as error:
        pytest.skip(f"沒有顯示器：{error}")
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def master(tk_root):
    """每個測試一個新的視窗；共用同一個 Tk，因為在 Windows 上一再重建 Tk 偶爾會找不到 tk.tcl。"""
    window = tk.Toplevel(tk_root)
    window.withdraw()
    yield window
    if window.winfo_exists():  # 關閉的測試已經關掉視窗
        window.destroy()


@pytest.fixture
def immediate():
    """`fcn_checker.ui.ImmediateBackground`：分頁的背景動作在同一個執行緒馬上做完、直接回呼（#206）。"""
    from fcn_checker.ui import ImmediateBackground

    return ImmediateBackground


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


@pytest.fixture
def pg_settings(pg):
    """連到 `pg` 同一個測試資料庫的 `ConnectionSettings`：給自己開連線的視窗動作用（#125）。"""
    from sp_workflow.database import ConnectionSettings

    info = pg.info
    return ConnectionSettings(info.host, str(info.port), info.dbname, info.user, info.password or None)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--shard", default=None, help="i/n：只跑第 i 份（共 n 份，同一個測試檔在同一份）")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    # 用到 `pg` 的測試一律算 db：漏標時本機 `-m "not db" -n auto` 也不會平行碰 spw_test（#142）
    for item in items:
        if "pg" in getattr(item, "fixturenames", ()):
            item.add_marker("db")
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
