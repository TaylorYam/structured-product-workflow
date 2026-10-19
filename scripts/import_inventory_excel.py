"""Validate and import the confirmed inventory Excel layout into PostgreSQL.

The default mode is a read-only dry-run. Database writes require --write.
The importer reads a local .env file itself and never prints its values.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from getpass import getpass
from pathlib import Path
from typing import Any

import openpyxl
import psycopg
from openpyxl.utils import column_index_from_string

SHEET_NAME = "工作表1"
HEADER_ROW = 3
DATA_START_ROW = 4
CONFIG_VERSION = "inventory-excel-v1-confirmed-2026-10-18"

EXPECTED_HEADERS = {
    "A": "庫存狀態",
    "B": "當日比價",
    "C": "Product",
    "D": "發行機構",
    "E": "TDCC Code",
    "F": "ISIN Code",
    "G": "私銀註記",
    "H": "單位面額",
    "I": "承作幣別",
    "X": "交易日",
    "Y": "發行日",
    "AM": "最終比價日",
    "AN": "到期日",
    "AO": "評價日",
    "AP": "期初定價",
    "AQ": "KO(%)",
    "AT": "K(%)",
    "AW": "KI(%)",
    "AZ": "Coupon p.a. (%)",
    "BA": "天期(月)",
    "BB": "Non-Call(月)",
}

BRANCH_COLUMNS = [
    ("J", "K"),
    ("L", "M"),
    ("N", "O"),
    ("P", "Q"),
    ("R", "S"),
    ("T", "U"),
    ("V", "W"),
]

UNDERLYING_COLUMNS = [
    ("BD", "BE", "BF", "BG", "BH", "BI"),
    ("BJ", "BK", "BL", "BM", "BN", "BO"),
    ("BP", "BQ", "BR", "BS", "BT", "BU"),
    ("BV", "BW", "BX", "BY", "BZ", "CA"),
    ("CB", "CC", "CD", "CE", "CF", "CG"),
]

PRICING_METHODS = {
    "收盤價": "CLOSE",
    "開盤價": "OPEN",
    "VWAP": "VWAP",
}


DDL = """
CREATE TABLE IF NOT EXISTS inventory_import_batch (
    batch_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_file_name text NOT NULL,
    source_sha256 text NOT NULL,
    sheet_name text NOT NULL,
    config_sha256 text NOT NULL,
    source_row_count integer NOT NULL,
    imported_row_count integer NOT NULL DEFAULT 0,
    excluded_row_count integer NOT NULL DEFAULT 0,
    status text NOT NULL CHECK (status IN ('completed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_sha256, sheet_name, config_sha256)
);

CREATE TABLE IF NOT EXISTS inventory_import_raw_row (
    batch_id bigint NOT NULL REFERENCES inventory_import_batch(batch_id) ON DELETE RESTRICT,
    source_row_number integer NOT NULL,
    tdcc_raw text,
    row_status text NOT NULL CHECK (row_status IN ('imported', 'excluded')),
    exclusion_reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
    raw_values jsonb NOT NULL,
    normalized_values jsonb,
    PRIMARY KEY (batch_id, source_row_number)
);

CREATE TABLE IF NOT EXISTS inventory_product_snapshot (
    product_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id bigint NOT NULL REFERENCES inventory_import_batch(batch_id) ON DELETE RESTRICT,
    tdcc text NOT NULL CHECK (tdcc ~ '^[0-9]{12}$'),
    isin text NOT NULL,
    product_type text NOT NULL,
    issuer_raw text NOT NULL,
    denomination numeric(20, 4) NOT NULL,
    currency_code text NOT NULL,
    trade_date date NOT NULL,
    issue_date date NOT NULL,
    final_pricing_date date NOT NULL,
    maturity_date date NOT NULL,
    status_code text,
    status_date date,
    status_raw text,
    pricing_method text,
    ko_percent numeric(12, 6),
    k_percent numeric(12, 6),
    ki_percent numeric(12, 6),
    coupon_percent numeric(12, 6),
    tenor_months integer NOT NULL,
    non_call_months integer NOT NULL,
    UNIQUE (batch_id, tdcc)
);

CREATE TABLE IF NOT EXISTS inventory_branch_allocation (
    allocation_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id bigint NOT NULL REFERENCES inventory_import_batch(batch_id) ON DELETE RESTRICT,
    tdcc text NOT NULL CHECK (tdcc ~ '^[0-9]{12}$'),
    source_row_number integer NOT NULL,
    branch_sequence integer NOT NULL CHECK (branch_sequence BETWEEN 1 AND 7),
    branch_name text NOT NULL,
    allocated_amount numeric(20, 4) NOT NULL,
    UNIQUE (batch_id, source_row_number, branch_sequence)
);

CREATE TABLE IF NOT EXISTS inventory_underlying (
    underlying_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id bigint NOT NULL REFERENCES inventory_import_batch(batch_id) ON DELETE RESTRICT,
    tdcc text NOT NULL CHECK (tdcc ~ '^[0-9]{12}$'),
    source_row_number integer NOT NULL,
    underlying_sequence integer NOT NULL CHECK (underlying_sequence BETWEEN 1 AND 5),
    ticker text NOT NULL,
    entry_price numeric(20, 8),
    execution_price numeric(20, 8),
    lower_barrier_price numeric(20, 8),
    ko_price numeric(20, 8),
    memo text,
    UNIQUE (batch_id, source_row_number, underlying_sequence)
);
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--excel", required=True, type=Path)
    parser.add_argument("--sheet", default=SHEET_NAME)
    parser.add_argument("--env-file", default=Path(".env"), type=Path)
    parser.add_argument("--check-connection", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--write", action="store_true")
    return parser.parse_args()


def load_env_file(path: Path) -> dict[str, str]:
    """Load a simple .env file without printing or logging any values."""

    if not path.is_file():
        raise RuntimeError(f"Environment file not found: {path}")

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise RuntimeError(f"Invalid .env entry at line {line_number}")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise RuntimeError(f"Invalid .env key at line {line_number}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def connection_kwargs(env: dict[str, str]) -> dict[str, Any]:
    required = ("PGDATABASE", "PGUSER")
    missing = [key for key in required if not env.get(key)]
    if missing:
        raise RuntimeError("Missing required database setting(s): " + ", ".join(missing))
    host = env.get("PGHOST", "localhost")
    port = env.get("PGPORT", "5432")
    # PGPASSWORD may live only in the local, Git-ignored .env; otherwise prompt without echoing.
    password = env.get("PGPASSWORD") or getpass(f"PostgreSQL password for {env['PGUSER']}@{host}:{port} (hidden): ")
    return {
        "host": host,
        "port": port,
        "dbname": env["PGDATABASE"],
        "user": env["PGUSER"],
        "password": password,
        "connect_timeout": 5,
    }


def source_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def config_sha256(sheet_name: str) -> str:
    config = {
        "version": CONFIG_VERSION,
        "sheet": sheet_name,
        "header_row": HEADER_ROW,
        "data_start_row": DATA_START_ROW,
        "tdcc_is_unique_product_key": True,
        "dash_means_not_applicable": True,
        "percentage_unit": "percent_points",
    }
    encoded = json.dumps(config, ensure_ascii=True, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def cell_value(sheet: Any, row_number: int, column: str) -> Any:
    return sheet.cell(row_number, column_index_from_string(column)).value


def text_value(value: Any, *, allow_dash: bool = True) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("expected text")
    cleaned = value.replace("\u00a0", " ").strip()
    if allow_dash and cleaned == "-":
        return None
    return cleaned


def decimal_value(value: Any, *, allow_dash: bool = True) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("expected numeric value")
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    if isinstance(value, str):
        cleaned = value.replace("\u00a0", " ").strip()
        if allow_dash and cleaned == "-":
            return None
        try:
            return Decimal(cleaned)
        except InvalidOperation as exc:
            raise ValueError("expected numeric value") from exc
    raise ValueError("expected numeric value")


def integer_value(value: Any, *, allow_dash: bool = False) -> int | None:
    parsed = decimal_value(value, allow_dash=allow_dash)
    if parsed is None:
        return None
    if parsed != parsed.to_integral_value():
        raise ValueError("expected integer value")
    return int(parsed)


def date_value(value: Any, *, allow_dash: bool = True) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and allow_dash and value.replace("\u00a0", " ").strip() == "-":
        return None
    raise ValueError("expected Excel date or '-'")


def parse_status(value: Any) -> tuple[str | None, date | None, str | None]:
    raw = text_value(value)
    if raw is None:
        return None, None, None
    match = re.fullmatch(r"已於(\d{4})/(\d{1,2})/(\d{1,2})\s*KO", raw)
    if match:
        try:
            status_date = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError as exc:
            raise ValueError("invalid KO status date") from exc
        return "KO", status_date, raw
    if raw == "到期還本":
        return "MATURITY_REPAID", None, raw
    raise ValueError("unmapped inventory status")


def parse_pricing_method(value: Any) -> str | None:
    raw = text_value(value)
    if raw is None:
        return None
    if raw not in PRICING_METHODS:
        raise ValueError("unmapped initial pricing method")
    return PRICING_METHODS[raw]


def raw_row(sheet: Any, headers: dict[str, str], row_number: int) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for column, header in headers.items():
        result[f"{column}:{header}"] = json_value(cell_value(sheet, row_number, column))
    return result


def parse_row(sheet: Any, headers: dict[str, str], row_number: int) -> dict[str, Any]:
    errors: list[str] = []

    def capture(label: str, function: Any, value: Any, **kwargs: Any) -> Any:
        try:
            return function(value, **kwargs)
        except ValueError as exc:
            errors.append(f"{label}: {exc}")
            return None

    raw = raw_row(sheet, headers, row_number)
    tdcc_raw = cell_value(sheet, row_number, "E")
    tdcc = None
    if not isinstance(tdcc_raw, str) or not re.fullmatch(r"\d{12}", tdcc_raw):
        errors.append("TDCC Code: expected 12-digit text")
    else:
        tdcc = tdcc_raw

    status_code, status_date, status_raw = capture("庫存狀態", parse_status, cell_value(sheet, row_number, "A")) or (None, None, None)
    pricing_method = capture("期初定價", parse_pricing_method, cell_value(sheet, row_number, "AP"))
    product_type = capture("Product", text_value, cell_value(sheet, row_number, "C"), allow_dash=False)
    issuer = capture("發行機構", text_value, cell_value(sheet, row_number, "D"), allow_dash=False)
    isin = capture("ISIN Code", text_value, cell_value(sheet, row_number, "F"), allow_dash=False)
    currency = capture("承作幣別", text_value, cell_value(sheet, row_number, "I"), allow_dash=False)
    denomination = capture("單位面額", decimal_value, cell_value(sheet, row_number, "H"), allow_dash=False)
    trade_date = capture("交易日", date_value, cell_value(sheet, row_number, "X"), allow_dash=False)
    issue_date = capture("發行日", date_value, cell_value(sheet, row_number, "Y"), allow_dash=False)
    final_pricing_date = capture("最終比價日", date_value, cell_value(sheet, row_number, "AM"), allow_dash=False)
    maturity_date = capture("到期日", date_value, cell_value(sheet, row_number, "AN"), allow_dash=False)
    ko_percent = capture("KO(%)", decimal_value, cell_value(sheet, row_number, "AQ"))
    k_percent = capture("K(%)", decimal_value, cell_value(sheet, row_number, "AT"))
    ki_percent = capture("KI(%)", decimal_value, cell_value(sheet, row_number, "AW"))
    coupon_percent = capture("Coupon p.a. (%)", decimal_value, cell_value(sheet, row_number, "AZ"))
    tenor_months = capture("天期(月)", integer_value, cell_value(sheet, row_number, "BA"), allow_dash=False)
    non_call_months = capture("Non-Call(月)", integer_value, cell_value(sheet, row_number, "BB"), allow_dash=False)

    for column in ("Z", "AA", "AB", "AC", "AD", "AE", "AF", "AG", "AH", "AI", "AJ", "AK", "AL"):
        capture(headers[column], date_value, cell_value(sheet, row_number, column))

    allocations: list[dict[str, Any]] = []
    for sequence, (branch_column, amount_column) in enumerate(BRANCH_COLUMNS, 1):
        branch_raw = cell_value(sheet, row_number, branch_column)
        amount_raw = cell_value(sheet, row_number, amount_column)
        branch_name = capture(f"{branch_column} 分公司", text_value, branch_raw)
        amount = capture(f"{amount_column} 承作金額", decimal_value, amount_raw)
        if branch_name is None and amount is not None:
            errors.append(f"{branch_column}:{amount_column}: amount without branch")
        if branch_name is not None and amount is None:
            errors.append(f"{branch_column}:{amount_column}: branch without amount")
        if branch_name is not None and amount is not None:
            allocations.append(
                {
                    "sequence": sequence,
                    "branch_name": branch_name,
                    "allocated_amount": amount,
                }
            )

    underlyings: list[dict[str, Any]] = []
    for sequence, columns in enumerate(UNDERLYING_COLUMNS, 1):
        ticker_column, entry_column, execution_column, barrier_column, ko_column, memo_column = columns
        ticker = capture(f"{ticker_column} 標的", text_value, cell_value(sheet, row_number, ticker_column))
        entry_price = capture(f"{entry_column} 進場價", decimal_value, cell_value(sheet, row_number, entry_column))
        execution_price = capture(f"{execution_column} 執行價", decimal_value, cell_value(sheet, row_number, execution_column))
        barrier_price = capture(f"{barrier_column} 下限價", decimal_value, cell_value(sheet, row_number, barrier_column))
        ko_price = capture(f"{ko_column} KO價", decimal_value, cell_value(sheet, row_number, ko_column))
        memo = capture(f"{memo_column} Memo", text_value, cell_value(sheet, row_number, memo_column))
        dependent_present = any(value is not None for value in (entry_price, execution_price, barrier_price, ko_price, memo))
        if ticker is None and dependent_present:
            errors.append(f"{ticker_column}:{memo_column}: underlying values without ticker")
        if ticker is not None:
            underlyings.append(
                {
                    "sequence": sequence,
                    "ticker": ticker,
                    "entry_price": entry_price,
                    "execution_price": execution_price,
                    "lower_barrier_price": barrier_price,
                    "ko_price": ko_price,
                    "memo": memo,
                }
            )

    normalized = {
        "tdcc": tdcc,
        "isin": isin,
        "product_type": product_type,
        "issuer_raw": issuer,
        "denomination": denomination,
        "currency_code": currency,
        "trade_date": trade_date,
        "issue_date": issue_date,
        "final_pricing_date": final_pricing_date,
        "maturity_date": maturity_date,
        "status_code": status_code,
        "status_date": status_date,
        "status_raw": status_raw,
        "pricing_method": pricing_method,
        "ko_percent": ko_percent,
        "k_percent": k_percent,
        "ki_percent": ki_percent,
        "coupon_percent": coupon_percent,
        "tenor_months": tenor_months,
        "non_call_months": non_call_months,
    }
    return {
        "source_row_number": row_number,
        "raw_values": raw,
        "normalized": normalized,
        "allocations": allocations,
        "underlyings": underlyings,
        "errors": errors,
    }


def validate_headers(sheet: Any) -> dict[str, str]:
    headers: dict[str, str] = {}
    for column_index in range(1, 91):
        value = sheet.cell(HEADER_ROW, column_index).value
        if value is not None:
            column = openpyxl.utils.get_column_letter(column_index)
            headers[column] = str(value)
    for column, expected in EXPECTED_HEADERS.items():
        if headers.get(column) != expected:
            raise RuntimeError(f"Unexpected header at {column}{HEADER_ROW}")
    return headers


def mark_duplicate_conflicts(rows_by_tdcc: dict[str, list[dict[str, Any]]]) -> None:
    consistency_fields = (
        "isin",
        "product_type",
        "issuer_raw",
        "denomination",
        "currency_code",
        "trade_date",
        "issue_date",
        "final_pricing_date",
        "maturity_date",
        "status_code",
        "status_date",
        "pricing_method",
        "ko_percent",
        "k_percent",
        "ki_percent",
        "coupon_percent",
        "tenor_months",
        "non_call_months",
    )
    for rows in rows_by_tdcc.values():
        reference = rows[0]["normalized"]
        for row in rows[1:]:
            differences = [field for field in consistency_fields if row["normalized"].get(field) != reference.get(field)]
            if differences:
                reason = "TDCC duplicate has conflicting product fields: " + ", ".join(differences)
                for conflicted in rows:
                    conflicted["errors"].append(reason)


def prepare_workbook(path: Path, sheet_name: str) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f"Excel file not found: {path}")
    workbook = openpyxl.load_workbook(path, data_only=False, read_only=False)
    if sheet_name not in workbook.sheetnames:
        raise RuntimeError(f"Worksheet not found: {sheet_name}")
    sheet = workbook[sheet_name]
    headers = validate_headers(sheet)
    rows = [parse_row(sheet, headers, row_number) for row_number in range(DATA_START_ROW, sheet.max_row + 1)]
    rows_by_tdcc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        tdcc = row["normalized"].get("tdcc")
        if tdcc is not None and not row["errors"]:
            rows_by_tdcc[tdcc].append(row)
    mark_duplicate_conflicts(rows_by_tdcc)
    valid_rows = [row for row in rows if not row["errors"]]
    excluded_rows = [row for row in rows if row["errors"]]
    valid_by_tdcc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in valid_rows:
        valid_by_tdcc[row["normalized"]["tdcc"]].append(row)
    return {
        "headers": headers,
        "rows": rows,
        "valid_rows": valid_rows,
        "excluded_rows": excluded_rows,
        "valid_by_tdcc": valid_by_tdcc,
        "source_row_count": len(rows),
        "imported_row_count": len(valid_rows),
        "excluded_row_count": len(excluded_rows),
        "product_count": len(valid_by_tdcc),
        "allocation_count": sum(len(row["allocations"]) for row in valid_rows),
        "underlying_count": sum(len(row["underlyings"]) for row in valid_rows),
    }


def table_exists(connection: psycopg.Connection[Any]) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('public.inventory_import_batch') IS NOT NULL")
        return bool(cursor.fetchone()[0])


def existing_batch(connection: psycopg.Connection[Any], source_hash: str, sheet_name: str, config_hash: str) -> bool:
    if not table_exists(connection):
        return False
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT 1
            FROM inventory_import_batch
            WHERE source_sha256 = %s AND sheet_name = %s AND config_sha256 = %s
            """,
            (source_hash, sheet_name, config_hash),
        )
        return cursor.fetchone() is not None


def serializable(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: serializable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [serializable(item) for item in value]
    return json_value(value)


def write_import(
    connection: psycopg.Connection[Any],
    path: Path,
    sheet_name: str,
    source_hash: str,
    config_hash: str,
    prepared: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute(DDL)
            cursor.execute(
                """
                SELECT 1
                FROM inventory_import_batch
                WHERE source_sha256 = %s AND sheet_name = %s AND config_sha256 = %s
                """,
                (source_hash, sheet_name, config_hash),
            )
            if cursor.fetchone() is not None:
                raise RuntimeError("identical source, worksheet, and import configuration already imported")

            cursor.execute(
                """
                INSERT INTO inventory_import_batch
                    (source_file_name, source_sha256, sheet_name, config_sha256, source_row_count,
                     imported_row_count, excluded_row_count, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'completed')
                RETURNING batch_id
                """,
                (
                    path.name,
                    source_hash,
                    sheet_name,
                    config_hash,
                    prepared["source_row_count"],
                    prepared["imported_row_count"],
                    prepared["excluded_row_count"],
                ),
            )
            batch_id = cursor.fetchone()[0]

            for row in prepared["rows"]:
                normalized = row["normalized"] if not row["errors"] else None
                cursor.execute(
                    """
                    INSERT INTO inventory_import_raw_row
                        (batch_id, source_row_number, tdcc_raw, row_status, exclusion_reasons,
                         raw_values, normalized_values)
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb)
                    """,
                    (
                        batch_id,
                        row["source_row_number"],
                        row["normalized"].get("tdcc"),
                        "excluded" if row["errors"] else "imported",
                        json.dumps(row["errors"], ensure_ascii=False),
                        json.dumps(serializable(row["raw_values"]), ensure_ascii=False),
                        json.dumps(serializable(normalized), ensure_ascii=False) if normalized is not None else None,
                    ),
                )

            for tdcc, rows in prepared["valid_by_tdcc"].items():
                product = rows[0]["normalized"]
                cursor.execute(
                    """
                    INSERT INTO inventory_product_snapshot
                        (batch_id, tdcc, isin, product_type, issuer_raw, denomination, currency_code,
                         trade_date, issue_date, final_pricing_date, maturity_date, status_code, status_date,
                         status_raw, pricing_method, ko_percent, k_percent, ki_percent, coupon_percent,
                         tenor_months, non_call_months)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        batch_id,
                        tdcc,
                        product["isin"],
                        product["product_type"],
                        product["issuer_raw"],
                        product["denomination"],
                        product["currency_code"],
                        product["trade_date"],
                        product["issue_date"],
                        product["final_pricing_date"],
                        product["maturity_date"],
                        product["status_code"],
                        product["status_date"],
                        product["status_raw"],
                        product["pricing_method"],
                        product["ko_percent"],
                        product["k_percent"],
                        product["ki_percent"],
                        product["coupon_percent"],
                        product["tenor_months"],
                        product["non_call_months"],
                    ),
                )
                for row in rows:
                    for allocation in row["allocations"]:
                        cursor.execute(
                            """
                            INSERT INTO inventory_branch_allocation
                                (batch_id, tdcc, source_row_number, branch_sequence, branch_name, allocated_amount)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            """,
                            (
                                batch_id,
                                tdcc,
                                row["source_row_number"],
                                allocation["sequence"],
                                allocation["branch_name"],
                                allocation["allocated_amount"],
                            ),
                        )
                    for underlying in row["underlyings"]:
                        cursor.execute(
                            """
                            INSERT INTO inventory_underlying
                                (batch_id, tdcc, source_row_number, underlying_sequence, ticker,
                                 entry_price, execution_price, lower_barrier_price, ko_price, memo)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                batch_id,
                                tdcc,
                                row["source_row_number"],
                                underlying["sequence"],
                                underlying["ticker"],
                                underlying["entry_price"],
                                underlying["execution_price"],
                                underlying["lower_barrier_price"],
                                underlying["ko_price"],
                                underlying["memo"],
                            ),
                        )

        # Verify before the transaction commits so a failed reconciliation rolls back the whole batch.
        verification = verify_import(connection, batch_id, prepared)

    return batch_id, verification


def verify_import(connection: psycopg.Connection[Any], batch_id: int, prepared: dict[str, Any]) -> dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT source_row_count, imported_row_count, excluded_row_count, status
            FROM inventory_import_batch
            WHERE batch_id = %s
            """,
            (batch_id,),
        )
        batch = cursor.fetchone()
        if batch is None:
            raise RuntimeError("post-import batch verification failed: batch not found")

        cursor.execute(
            """
            SELECT
                count(*) FILTER (WHERE row_status = 'imported'),
                count(*) FILTER (WHERE row_status = 'excluded')
            FROM inventory_import_raw_row
            WHERE batch_id = %s
            """,
            (batch_id,),
        )
        raw_counts = cursor.fetchone()

        cursor.execute(
            "SELECT count(*) FROM inventory_product_snapshot WHERE batch_id = %s",
            (batch_id,),
        )
        product_count = cursor.fetchone()[0]
        cursor.execute(
            "SELECT count(*) FROM inventory_branch_allocation WHERE batch_id = %s",
            (batch_id,),
        )
        allocation_count = cursor.fetchone()[0]
        cursor.execute(
            "SELECT count(*) FROM inventory_underlying WHERE batch_id = %s",
            (batch_id,),
        )
        underlying_count = cursor.fetchone()[0]

        # Samples are picked from this batch's data; no real product code is hard-coded.
        cursor.execute(
            """
            SELECT tdcc, trade_date, maturity_date, ko_percent, coupon_percent
            FROM inventory_product_snapshot
            WHERE batch_id = %s AND tdcc LIKE '0%%'
            ORDER BY tdcc
            LIMIT 1
            """,
            (batch_id,),
        )
        leading_zero_sample = cursor.fetchone()

        cursor.execute(
            """
            SELECT tdcc, count(*)
            FROM inventory_branch_allocation
            WHERE batch_id = %s
            GROUP BY tdcc
            HAVING count(DISTINCT source_row_number) > 1
            ORDER BY tdcc
            LIMIT 1
            """,
            (batch_id,),
        )
        duplicate_tdcc_allocation_sample = cursor.fetchone()

    expected = {
        "source_rows": batch[0],
        "batch_imported_rows": batch[1],
        "batch_excluded_rows": batch[2],
        "batch_status": batch[3],
        "raw_imported_rows": raw_counts[0],
        "raw_excluded_rows": raw_counts[1],
        "product_count": product_count,
        "allocation_count": allocation_count,
        "underlying_count": underlying_count,
        "leading_zero_sample": leading_zero_sample,
        "duplicate_tdcc_allocation_sample": duplicate_tdcc_allocation_sample,
    }
    if expected["source_rows"] != expected["batch_imported_rows"] + expected["batch_excluded_rows"]:
        raise RuntimeError("post-import verification failed: batch row totals do not reconcile")
    if expected["raw_imported_rows"] != expected["batch_imported_rows"]:
        raise RuntimeError("post-import verification failed: raw imported rows do not reconcile")
    if expected["raw_excluded_rows"] != expected["batch_excluded_rows"]:
        raise RuntimeError("post-import verification failed: raw excluded rows do not reconcile")
    if expected["product_count"] != prepared["product_count"]:
        raise RuntimeError("post-import verification failed: product count does not reconcile")
    if expected["allocation_count"] != prepared["allocation_count"]:
        raise RuntimeError("post-import verification failed: allocation count does not reconcile")
    if expected["underlying_count"] != prepared["underlying_count"]:
        raise RuntimeError("post-import verification failed: underlying count does not reconcile")
    return expected


def print_summary(
    *,
    env_loaded: bool,
    connection_ok: bool,
    dry_run: bool,
    source_hash: str,
    config_hash: str,
    prepared: dict[str, Any] | None = None,
    schema_exists: bool | None = None,
    rerun_detected: bool | None = None,
) -> None:
    print(f"env_loaded={env_loaded}")
    print(f"connection_ok={connection_ok}")
    if prepared is not None:
        print(f"mode={'dry-run' if dry_run else 'write'}")
        print(f"source_sha256_prefix={source_hash[:12]}")
        print(f"config_sha256_prefix={config_hash[:12]}")
        if schema_exists is not None:
            print(f"import_schema_exists={schema_exists}")
        if rerun_detected is not None:
            print(f"identical_import_detected={rerun_detected}")
        print(f"source_rows={prepared['source_row_count']}")
        print(f"planned_success_rows={prepared['imported_row_count']}")
        print(f"planned_excluded_rows={prepared['excluded_row_count']}")
        print(f"planned_unique_tdcc_products={prepared['product_count']}")
        print(f"planned_branch_allocations={prepared['allocation_count']}")
        print(f"planned_underlyings={prepared['underlying_count']}")
        if prepared["excluded_rows"]:
            print("excluded_rows:")
            for row in prepared["excluded_rows"]:
                print(f"  row={row['source_row_number']} reasons={'; '.join(row['errors'])}")


def main() -> int:
    args = parse_args()
    if args.check_connection and (args.dry_run or args.write):
        raise RuntimeError("--check-connection cannot be combined with --dry-run or --write")
    if args.dry_run and args.write:
        raise RuntimeError("--dry-run and --write are mutually exclusive")
    if not args.check_connection and not args.dry_run and not args.write:
        args.dry_run = True

    env = load_env_file(args.env_file)
    kwargs = connection_kwargs(env)
    if args.check_connection:
        with psycopg.connect(**kwargs) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        print_summary(env_loaded=True, connection_ok=True, dry_run=True, source_hash="", config_hash="")
        return 0

    source_hash = source_sha256(args.excel)
    config_hash = config_sha256(args.sheet)
    prepared = prepare_workbook(args.excel, args.sheet)
    with psycopg.connect(**kwargs) as connection:
        schema_present = table_exists(connection)
        rerun = existing_batch(connection, source_hash, args.sheet, config_hash)
        if args.dry_run:
            print_summary(
                env_loaded=True,
                connection_ok=True,
                dry_run=True,
                source_hash=source_hash,
                config_hash=config_hash,
                prepared=prepared,
                schema_exists=schema_present,
                rerun_detected=rerun,
            )
            return 0
        if rerun:
            print("identical_import_detected=True")
            print("write_skipped=True")
            return 0
        connection.commit()  # end the read-only checks' implicit transaction so the write runs as its own
        batch_id, verification = write_import(connection, args.excel, args.sheet, source_hash, config_hash, prepared)
        print_summary(
            env_loaded=True,
            connection_ok=True,
            dry_run=False,
            source_hash=source_hash,
            config_hash=config_hash,
            prepared=prepared,
            schema_exists=True,
            rerun_detected=False,
        )
        print(f"batch_id={batch_id}")
        print(f"verified_source_rows={verification['source_rows']}")
        print(f"verified_imported_rows={verification['raw_imported_rows']}")
        print(f"verified_excluded_rows={verification['raw_excluded_rows']}")
        print(f"verified_unique_tdcc_products={verification['product_count']}")
        print(f"verified_branch_allocations={verification['allocation_count']}")
        print(f"verified_underlyings={verification['underlying_count']}")
        if verification["duplicate_tdcc_allocation_sample"] is not None:
            sample_tdcc, sample_allocation_count = verification["duplicate_tdcc_allocation_sample"]
            print(f"verified_duplicate_tdcc={sample_tdcc}")
            print(f"verified_duplicate_tdcc_allocation_rows={sample_allocation_count}")
        if verification["leading_zero_sample"] is not None:
            sample_tdcc, sample_trade_date, sample_maturity_date, sample_ko, sample_coupon = verification["leading_zero_sample"]
            print(f"verified_leading_zero_tdcc={sample_tdcc}")
            print(f"verified_sample_trade_date={sample_trade_date.isoformat()}")
            print(f"verified_sample_maturity_date={sample_maturity_date.isoformat()}")
            print(f"verified_sample_ko_percent={sample_ko}")
            print(f"verified_sample_coupon_percent={sample_coupon}")
        print("write_committed=True")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, psycopg.Error) as exc:
        print(f"import_failed: {type(exc).__name__}: {str(exc).splitlines()[0]}", file=sys.stderr)
        raise SystemExit(1) from None
