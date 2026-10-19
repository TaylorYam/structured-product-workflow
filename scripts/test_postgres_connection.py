"""Run a read-only PostgreSQL connectivity check for local development."""

import os
import sys
from getpass import getpass

import psycopg

QUERY = "SELECT version(), current_database();"


def main() -> int:
    host = os.getenv("PGHOST", "localhost")
    port = os.getenv("PGPORT", "5432")
    database = os.getenv("PGDATABASE")
    user = os.getenv("PGUSER")

    if not database or not user:
        print("PGDATABASE and PGUSER must be set in the local environment.", file=sys.stderr)
        return 2

    password = getpass(f"PostgreSQL password for {user}@{host}:{port} (hidden): ")

    try:
        with psycopg.connect(
            host=host,
            port=port,
            dbname=database,
            user=user,
            password=password,
            connect_timeout=5,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(QUERY)
                server_version, current_database = cursor.fetchone()
    except psycopg.Error as exc:
        print(f"PostgreSQL connection failed: {exc}", file=sys.stderr)
        return 1

    print(f"server_version: {server_version}")
    print(f"current_database: {current_database}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
