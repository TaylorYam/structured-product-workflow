"""PostgreSQL 連線與資料表版本（#32、ADR 0003）。

- 連線設定讀本機 `.env`（`PGHOST`、`PGPORT`、`PGDATABASE`、`PGUSER`、選填 `PGPASSWORD`）；值不印出、不寫 log。
- 資料表版本是套件裡 `migrations/NNNN_*.sql`，`apply_migrations` 依序套用沒套過的版本，
  記在 `public.spw_schema_version`。建立 schema 需要有權限的帳號（`spw-db-migrate --admin-user`），
  套用後把讀寫權限給日常使用的帳號（`PGUSER`）。
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from getpass import getpass
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import psycopg

ENV_FILE_NAME = ".env"
VERSION_TABLE = "spw_schema_version"
SCHEMAS = ("mailbox", "workflow")
_MIGRATION_NAME = re.compile(r"(\d{4})_[A-Za-z0-9_]+\.sql")


class DatabaseError(Exception):
    """連線設定或資料表版本有問題；訊息是給作業人員看的中文。"""


@dataclass(frozen=True)
class ConnectionSettings:
    host: str
    port: str
    dbname: str
    user: str
    password: str | None = None  # 沒有時由呼叫端詢問（隱藏輸入）

    def with_password(self, password: str) -> ConnectionSettings:
        return replace(self, password=password)


def default_env_path(install_root: Path | None = None) -> Path:
    """`.env` 的位置：有安裝資料夾就在它底下；在 repo 裡開發用 repo 根目錄；否則用使用者家目錄。"""
    if install_root is not None:
        return install_root / ENV_FILE_NAME
    repo = Path(__file__).resolve().parents[2]
    base = repo if (repo / "pyproject.toml").is_file() else Path.home() / ".sp_workflow"
    return base / ENV_FILE_NAME


def load_env_file(path: Path) -> dict[str, str]:
    """讀簡單的 `.env`（`KEY=value`，可加引號、`#` 註解）。錯誤訊息只提行號，不帶值。"""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise DatabaseError(f"讀不到連線設定檔：{path}") from exc
    values: dict[str, str] = {}
    for number, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise DatabaseError(f"連線設定檔第 {number} 行格式不對：{path}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def settings_from_env(path: Path) -> ConnectionSettings:
    env = load_env_file(path)
    missing = [key for key in ("PGDATABASE", "PGUSER") if not env.get(key)]
    if missing:
        raise DatabaseError(f"連線設定檔缺少 {', '.join(missing)}：{path}")
    return ConnectionSettings(
        host=env.get("PGHOST") or "localhost",
        port=env.get("PGPORT") or "5432",
        dbname=env["PGDATABASE"],
        user=env["PGUSER"],
        password=env.get("PGPASSWORD") or None,
    )


def connect(settings: ConnectionSettings) -> psycopg.Connection:
    """連上資料庫；失敗時只回報主機、資料庫與帳號，不帶密碼。"""
    import psycopg

    try:
        return psycopg.connect(
            host=settings.host,
            port=settings.port,
            dbname=settings.dbname,
            user=settings.user,
            password=settings.password,
            connect_timeout=5,
        )
    except psycopg.Error as exc:
        first_line = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        raise DatabaseError(
            f"連不上資料庫 {settings.dbname}（{settings.user}@{settings.host}:{settings.port}）：{first_line}"
        ) from exc


def migration_files() -> list[tuple[int, str, str]]:
    """套件內的版本檔，依編號排序：(版本, 檔名, SQL)。"""
    folder = resources.files("sp_workflow") / "migrations"
    found = []
    for entry in folder.iterdir():
        match = _MIGRATION_NAME.fullmatch(entry.name)
        if match:
            found.append((int(match.group(1)), entry.name, entry.read_text(encoding="utf-8")))
    found.sort()
    versions = [v for v, _, _ in found]
    if len(set(versions)) != len(versions):
        raise DatabaseError("資料表版本檔的編號重複")
    return found


def applied_versions(conn: psycopg.Connection) -> set[int]:
    with conn.transaction():
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS public.{VERSION_TABLE} ("
            "version integer PRIMARY KEY, name text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        return {row[0] for row in conn.execute(f"SELECT version FROM public.{VERSION_TABLE}")}


def apply_migrations(conn: psycopg.Connection, *, grant_to: str | None = None) -> list[str]:
    """套用還沒套過的版本檔（每個版本一個 transaction），回傳這次套用的檔名。

    `grant_to` 有值時，最後把兩個 schema 的使用與資料表讀寫權限給這個帳號（日常匯入用的帳號）。
    """
    from psycopg import sql

    done = applied_versions(conn)
    applied = []
    for version, name, text in migration_files():
        if version in done:
            continue
        with conn.transaction():
            conn.execute(text)
            conn.execute(f"INSERT INTO public.{VERSION_TABLE} (version, name) VALUES (%s, %s)", (version, name))
        applied.append(name)
    if grant_to:
        role = sql.Identifier(grant_to)
        with conn.transaction():
            for schema in SCHEMAS:
                ident = sql.Identifier(schema)
                conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(ident, role))
                conn.execute(
                    sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {} TO {}").format(ident, role)
                )
                conn.execute(sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {} TO {}").format(ident, role))
            conn.execute(sql.SQL("GRANT SELECT ON public.{} TO {}").format(sql.Identifier(VERSION_TABLE), role))
    return applied


def require_current_schema(conn: psycopg.Connection) -> None:
    """日常使用前確認資料表已經建到最新版本。"""
    from psycopg import errors

    latest = {v for v, _, _ in migration_files()}
    try:
        with conn.transaction():
            done = {row[0] for row in conn.execute(f"SELECT version FROM public.{VERSION_TABLE}")}
    except errors.UndefinedTable:
        done = set()
    if not latest <= done:
        raise DatabaseError("資料庫的資料表還沒建立或不是最新版本，請先執行 spw-db-migrate。")


def migrate_main(argv: Sequence[str] | None = None) -> int:
    """`spw-db-migrate`：用有權限的帳號建立／更新資料表，並把讀寫權限給 `.env` 的 PGUSER。"""
    parser = argparse.ArgumentParser(description="建立或更新流程資料庫的資料表")
    parser.add_argument("--env-file", type=Path, default=default_env_path(), help="連線設定檔（預設 repo 的 .env）")
    parser.add_argument("--admin-user", default="postgres", help="有建立 schema 權限的帳號（預設 postgres）")
    parser.add_argument("--no-grant", action="store_true", help="不把權限給 .env 的 PGUSER")
    args = parser.parse_args(argv)
    import psycopg

    try:
        settings = settings_from_env(args.env_file)
        app_user = settings.user
        admin = replace(settings, user=args.admin_user, password=None)
        if args.admin_user == app_user:
            admin = settings
        if admin.password is None:
            admin = admin.with_password(getpass(f"{admin.user}@{admin.host}:{admin.port} 的密碼（不顯示）："))
        with connect(admin) as conn:
            conn.autocommit = True
            applied = apply_migrations(conn, grant_to=None if args.no_grant or admin is settings else app_user)
    except (DatabaseError, psycopg.Error) as exc:
        print(str(exc).splitlines()[0] if str(exc) else type(exc).__name__, file=sys.stderr)
        return 1
    print("已是最新版本" if not applied else "已套用：" + "、".join(applied))
    return 0


if __name__ == "__main__":
    raise SystemExit(migrate_main())
