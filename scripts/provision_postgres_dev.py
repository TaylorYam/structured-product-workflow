"""Provision the local development database and verify its restricted role."""

from __future__ import annotations

import os
import sys

import psycopg
from psycopg import sql


DATABASE_NAME = "structured_product_workflow_dev"
ROLE_NAME = "workflow_dev"


def required_environment(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required local environment variable: {name}")
    return value


def main() -> int:
    host = os.getenv("PGHOST", "localhost")
    port = os.getenv("PGPORT", "5432")
    admin_database = os.getenv("PGADMINDB", "postgres")
    admin_user = os.getenv("PGADMINUSER", "postgres")
    admin_password = required_environment("PGADMINPASSWORD")
    developer_password = required_environment("PGDEVPASSWORD")

    admin_connection_kwargs = {
        "host": host,
        "port": port,
        "dbname": admin_database,
        "user": admin_user,
        "password": admin_password,
        "connect_timeout": 5,
        "autocommit": True,
    }

    try:
        with psycopg.connect(**admin_connection_kwargs) as admin_connection:
            with admin_connection.cursor() as cursor:
                cursor.execute("SELECT version(), current_database();")
                server_version, current_database = cursor.fetchone()
                print(f"admin_server_version: {server_version}")
                print(f"admin_current_database: {current_database}")

                cursor.execute(
                    """
                    SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
                           rolreplication, rolbypassrls
                    FROM pg_roles
                    WHERE rolname = %s;
                    """,
                    (ROLE_NAME,),
                )
                role_record = cursor.fetchone()

                if role_record is None:
                    cursor.execute(
                        sql.SQL(
                            "CREATE ROLE {} LOGIN PASSWORD {} "
                            "NOSUPERUSER NOCREATEDB NOCREATEROLE "
                            "NOREPLICATION NOBYPASSRLS"
                        ).format(
                            sql.Identifier(ROLE_NAME),
                            sql.Literal(developer_password),
                        )
                    )
                    print(f"role_action: created {ROLE_NAME}")
                else:
                    can_login, is_superuser, can_create_db, can_create_role, can_replicate, bypass_rls = role_record
                    print(f"role_action: existing {ROLE_NAME}")
                    if (
                        not can_login
                        or is_superuser
                        or can_create_db
                        or can_create_role
                        or can_replicate
                        or bypass_rls
                    ):
                        print(
                            "role configuration is not safely restricted; no role changes were made.",
                            file=sys.stderr,
                        )
                        return 3

                cursor.execute(
                    """
                    SELECT datallowconn, pg_get_userbyid(datdba)
                    FROM pg_database
                    WHERE datname = %s;
                    """,
                    (DATABASE_NAME,),
                )
                database_record = cursor.fetchone()

                database_created = database_record is None
                if database_created:
                    cursor.execute(
                        sql.SQL("CREATE DATABASE {}").format(sql.Identifier(DATABASE_NAME))
                    )
                    print(f"database_action: created {DATABASE_NAME}")
                else:
                    allow_connections, owner = database_record
                    print(f"database_action: existing {DATABASE_NAME}")
                    print(f"database_owner: {owner}")
                    if not allow_connections:
                        print("target database does not allow connections; no database changes were made.", file=sys.stderr)
                        return 3

                cursor.execute(
                    "SELECT has_database_privilege(%s, %s, 'CONNECT');",
                    (ROLE_NAME, DATABASE_NAME),
                )
                if not cursor.fetchone()[0]:
                    cursor.execute(
                        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                            sql.Identifier(DATABASE_NAME),
                            sql.Identifier(ROLE_NAME),
                        )
                    )
                    print("database_privilege: granted CONNECT")
                else:
                    print("database_privilege: CONNECT already present")

            target_connection_kwargs = dict(admin_connection_kwargs)
            target_connection_kwargs["dbname"] = DATABASE_NAME
            with psycopg.connect(**target_connection_kwargs) as target_connection:
                with target_connection.cursor() as cursor:
                    if database_created:
                        cursor.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC;")

                    cursor.execute(
                        "SELECT has_schema_privilege(%s, 'public', 'USAGE'), "
                        "has_schema_privilege(%s, 'public', 'CREATE');",
                        (ROLE_NAME, ROLE_NAME),
                    )
                    has_usage, has_create = cursor.fetchone()

                    if not has_usage:
                        cursor.execute(
                            sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
                                sql.Identifier(ROLE_NAME)
                            )
                        )
                        print("schema_privilege: granted USAGE on public")
                    if not has_create:
                        cursor.execute(
                            sql.SQL("GRANT CREATE ON SCHEMA public TO {}").format(
                                sql.Identifier(ROLE_NAME)
                            )
                        )
                        print("schema_privilege: granted CREATE on public")
                    if has_usage and has_create:
                        print("schema_privilege: USAGE and CREATE already present on public")

        developer_connection_kwargs = {
            "host": host,
            "port": port,
            "dbname": DATABASE_NAME,
            "user": ROLE_NAME,
            "password": developer_password,
            "connect_timeout": 5,
        }
        with psycopg.connect(**developer_connection_kwargs) as developer_connection:
            with developer_connection.cursor() as cursor:
                cursor.execute("SELECT current_database(), current_user;")
                connected_database, connected_user = cursor.fetchone()
                print(f"developer_current_database: {connected_database}")
                print(f"developer_current_user: {connected_user}")

    except psycopg.Error as exc:
        print(f"PostgreSQL provisioning failed: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
