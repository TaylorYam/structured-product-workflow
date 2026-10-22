"""舊商品補價格與比價日（#154、ADR 0020「舊商品」）。

以前由清單更新建立、資料表版本 0006 時鎖住的商品沒有各標的的四個價格與比價日，比不了價。「維護」選單讀一份
參考條件表格式的 Excel（例如回填後的庫存總表），只補這些商品缺的資料：
- 範圍：資料庫已鎖住、還沒有 `underlying_price` 或 `autocall_date` 的商品；缺哪一樣補哪一樣，其他欄一律不碰。
- 價格：UL_1～UL_5 的進場價、執行價、下限價、KO 價寫進 `workflow.underlying_price`（代碼用資料庫的，表上的代碼要一樣）；
  它掛在核對條件上，還沒有核對條件的商品先寫一筆「完成」的核對條件（之後比價就會比這檔）。
- 比價日 1～12 寫進 `workflow.autocall_date`，空值寫法存成空白。
- 表上的「庫存狀態」：「已於 Y/M/D KO」寫成 KO 事件（事件日是那一天）、「到期還本」寫成到期還本事件（沒有事件日），
  來源「舊清單」（`lifecycle.record_legacy`）；已有同種事件的不重複寫，已有別種事件的那一檔不匯入。
- 表上沒有、資料庫沒有、還沒鎖住、表上有兩列、代碼和資料庫不同、價格不是數字、比價日不是日期的：那一檔不匯入並
  寫出原因，其他照常；不匯入的另外寫一份錯誤清單放在來源檔旁邊。
- 整次匯入一個 transaction；重跑同一份，補過的都是「已存在」，不重寫。
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import openpyxl
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from fcn_checker.ingestion import IngestionError
from fcn_checker.orders.reference import (
    autocall_date_key,
    header_columns,
    load_reference_sheet,
    locate,
    price_key,
    underlying_key,
)
from sp_workflow.check_terms import DONE
from sp_workflow.lifecycle import KO, MATURITY, LifecycleError, current_events_of, record_legacy

if TYPE_CHECKING:
    import psycopg

    from fcn_checker.config import ReferenceFormat
    from fcn_checker.orders.reference import OrderRecord

STATUS_COLUMN = "庫存狀態"
SLOTS = 12
UNDERLYINGS = 5
PRICES = (("initial", "initial_price", "進場價"), ("strike", "strike_price", "執行價"),
          ("ki", "ki_price", "下限價"), ("ko", "ko_price", "KO 價"))  # fmt: skip
REQUIRED_PRICES = frozenset(("進場價", "執行價", "KO 價"))  # 下限價在沒有 KI 時本來就空白
FILLED, EXISTING, SKIPPED = "已補", "已存在", "不匯入"
ERROR_BOOK = "舊商品補資料錯誤_{stamp}.xlsx"
ERROR_BOOK_COLUMNS = (("TDCC", 16), ("原因", 80), ("表上的列", 10))
_KO = re.compile(r"已於\s*(\d{4})\s*/\s*(\d{1,2})\s*/\s*(\d{1,2})\s*KO")


class LegacyBackfillError(Exception):
    """整份不匯入（檔案讀不了、缺必要欄、錯誤清單寫不出來）；訊息是給作業人員看的中文。"""


@dataclass(frozen=True)
class BackfillLine:
    """一檔的結果：已補、已存在或不匯入，與說明。"""

    tdcc: str
    result: str
    detail: str
    rows: tuple[int, ...] = ()  # 表上的 Excel 列號；表上沒有時空的


@dataclass(frozen=True)
class BackfillReceipt:
    lines: tuple[BackfillLine, ...]
    has_status: bool  # 表上有沒有「庫存狀態」欄；沒有時不寫事件
    events: int  # 這次寫了幾筆舊清單事件
    error_book_path: Path | None = None

    def count(self, result: str) -> int:
        return sum(line.result == result for line in self.lines)


@dataclass(frozen=True)
class _Product:
    locked: bool
    codes: tuple[str | None, ...]  # UL_1～UL_5
    has_prices: bool
    has_dates: bool
    terms_status: str | None  # 核對條件的狀態；沒有時 None


@dataclass(frozen=True)
class _LegacyEvent:
    """庫存狀態要寫成的舊清單事件。"""

    event: str
    event_date: dt.date | None

    def text(self) -> str:
        return (
            f"{self.event}事件（{self.event_date:%Y-%m-%d}）" if self.event_date else f"{self.event}事件（沒有事件日）"
        )


def backfill_legacy(
    conn: psycopg.Connection,
    path: Path,
    fmt: ReferenceFormat,
    *,
    user: str | None = None,
    now: dt.datetime | None = None,
) -> BackfillReceipt:
    """讀一份參考條件表格式的 Excel，補舊商品的價格與比價日、寫舊清單事件；一個 transaction，失敗時什麼都不寫。"""
    records, statuses, has_status = _read(path, fmt)
    note = f"舊商品匯入：{path.name}"
    lines: list[BackfillLine] = []
    events = 0
    error_book = None
    try:
        with conn.transaction():
            products = _products(conn, sorted({r.product_code.value for r in records}))
            grouped: dict[str, list[OrderRecord]] = {}
            for record in records:
                grouped.setdefault(record.product_code.value, []).append(record)
            existing = current_events_of(conn, list(grouped))
            for tdcc, rows in grouped.items():
                numbers = tuple(r.row for r in rows)
                found = products.get(tdcc)
                problem = _scope_problem(found, rows)
                if problem:
                    lines.append(BackfillLine(tdcc, SKIPPED, problem, numbers))
                    continue
                if found.has_prices and found.has_dates:
                    lines.append(BackfillLine(tdcc, EXISTING, "價格與比價日都有了，沒有動", numbers))
                    continue
                (record,) = rows
                status = statuses.get(record.row)
                problems = [
                    *([] if found.has_prices else _price_problems(record, found, fmt)),
                    *([] if found.has_dates else _date_problems(record, fmt)),
                ]
                event, event_problem = _status_event(status)
                if event_problem:
                    problems.append(event_problem)
                stored = existing.get(tdcc, [])
                other = next((e for e in stored if event and e.event != event.event), None)
                if other is not None:
                    problems.append(f"已有存續事件「{other.event}」（{other.source}），和表上的「{status}」不同")
                if problems:
                    lines.append(BackfillLine(tdcc, SKIPPED, "；".join(problems), numbers))
                    continue
                done = []
                if not found.has_prices:
                    if found.terms_status is None:
                        conn.execute(
                            "INSERT INTO workflow.check_terms (tdcc, status, reason) VALUES (%s, %s, %s)",
                            (tdcc, DONE, f"{note}（舊商品，價格由表上補入）"),
                        )
                    _write_prices(conn, tdcc, record, found, fmt)
                    done.append("價格")
                if not found.has_dates:
                    _write_dates(conn, tdcc, record, fmt)
                    done.append("比價日 1～12")
                detail = f"補了{'、'.join(done)}"
                if found.terms_status not in (None, DONE):
                    detail += f"；核對條件是「{found.terms_status}」，比價不會比這檔，要先處理核對條件"
                if event:
                    same = next((e for e in stored if e.event == event.event), None)
                    if same is not None:
                        detail += f"；已有{event.event}事件，沒有再寫"
                        if event.event_date and same.event_date != event.event_date:
                            when = same.event_date or "沒有事件日"
                            detail += f"（日期不同：資料庫 {when}，表上 {event.event_date}，請確認）"
                    else:
                        try:
                            record_legacy(
                                conn, tdcc, event.event, event.event_date, f"{note}，庫存狀態「{status}」", user=user
                            )
                        except LifecycleError as exc:
                            raise LegacyBackfillError(f"{tdcc} 舊清單事件寫不進去，整份不匯入：{exc}") from exc
                        events += 1
                        detail += f"；寫了{event.text()}"
                lines.append(BackfillLine(tdcc, FILLED, detail, numbers))
            for tdcc, found in sorted(products.items()):
                if tdcc not in grouped and found.locked and not (found.has_prices and found.has_dates):
                    lines.append(BackfillLine(tdcc, SKIPPED, "表上沒有這檔（已鎖住，還缺價格或比價日）"))
            problems = [line for line in lines if line.result == SKIPPED]
            if problems:
                error_book = path.parent / ERROR_BOOK.format(stamp=f"{(now or dt.datetime.now()):%Y%m%d-%H%M%S}")
                _write_error_book(error_book, problems)
    except BaseException:
        if error_book is not None:  # 沒有匯入就不留錯誤清單
            error_book.unlink(missing_ok=True)
        raise
    return BackfillReceipt(tuple(lines), has_status, events, error_book)


# ---------- 讀表 ----------


def _read(path: Path, fmt: ReferenceFormat) -> tuple[list[OrderRecord], dict[int, str], bool]:
    """參考條件表的每一列、「庫存狀態」欄（列號 → 文字）與表上有沒有這一欄。"""
    try:
        sheet = load_reference_sheet(path, fmt)
        book = openpyxl.load_workbook(path, data_only=True)
        layout = locate(book, fmt)
    except IngestionError as exc:
        raise LegacyBackfillError(f"{str(exc).removesuffix('，整批不核對')}，整份不匯入") from exc
    ws = book[layout.sheet]
    columns = header_columns(ws, layout).get(STATUS_COLUMN)
    statuses: dict[int, str] = {}
    if columns:
        for record in sheet.rows:
            value = ws.cell(row=record.row, column=columns[0]).value
            if value is not None and str(value).strip():
                statuses[record.row] = str(value).strip()
    return sheet.rows, statuses, bool(columns)


def _products(conn: psycopg.Connection, tdccs: Sequence[str]) -> dict[str, _Product]:
    """表上的商品與資料庫裡所有已鎖住、還缺價格或比價日的商品；鎖住列，避免同時有人改。"""
    codes = ", ".join(f"p.underlying_{i}" for i in range(1, UNDERLYINGS + 1))
    rows = conn.execute(
        f"SELECT p.tdcc, p.locked_at IS NOT NULL, {codes},"
        " EXISTS (SELECT 1 FROM workflow.underlying_price u WHERE u.tdcc = p.tdcc),"
        " EXISTS (SELECT 1 FROM workflow.autocall_date a WHERE a.tdcc = p.tdcc),"
        " (SELECT c.status FROM workflow.check_terms c WHERE c.tdcc = p.tdcc)"
        " FROM workflow.product p WHERE p.tdcc = ANY(%s) OR (p.locked_at IS NOT NULL AND ("
        " NOT EXISTS (SELECT 1 FROM workflow.underlying_price u WHERE u.tdcc = p.tdcc)"
        " OR NOT EXISTS (SELECT 1 FROM workflow.autocall_date a WHERE a.tdcc = p.tdcc)))"
        " ORDER BY p.tdcc FOR UPDATE OF p",
        (list(tdccs),),
    ).fetchall()
    return {row[0]: _Product(row[1], tuple(row[2 : 2 + UNDERLYINGS]), *row[2 + UNDERLYINGS :]) for row in rows}


# ---------- 檢查 ----------


def _scope_problem(found: _Product | None, rows: Sequence[OrderRecord]) -> str:
    if found is None:
        return "資料庫沒有這檔"
    if not found.locked:
        return "還沒鎖住：價格與比價日由核對寫回，不用這個匯入"
    if len(rows) > 1:
        return f"表上有 {len(rows)} 列（第 {'、'.join(str(r.row) for r in rows)} 列），不知道用哪一列"
    if not found.has_prices and not any(found.codes):
        return "資料庫這檔沒有任何標的，補不了價格"
    return ""


def _value(record: OrderRecord, key: str, fmt: ReferenceFormat) -> Any:
    """一格的值；空白與空值寫法都是 None。"""
    found = record.fields.get(key)
    value = None if found is None else found.value
    return None if value is None or value == fmt.empty_value else value


def _price_problems(record: OrderRecord, found: _Product, fmt: ReferenceFormat) -> list[str]:
    problems = []
    for i, code in enumerate(found.codes, 1):
        listed = _value(record, underlying_key(i), fmt)
        values = {label: _value(record, price_key(i, col), fmt) for col, _, label in PRICES}
        if code is None:
            if listed is not None or any(v is not None for v in values.values()):
                problems.append(f"資料庫沒有 UL_{i}，表上卻有代碼或價格")
            continue
        if listed is None:
            problems.append(f"表上沒有 UL_{i}（資料庫是 {code}）")
        elif _code(str(listed)) != _code(code):
            problems.append(f"UL_{i} 代碼和資料庫不同（表上 {listed}，資料庫 {code}）")
        for label, value in values.items():
            if value is None and label in REQUIRED_PRICES:
                problems.append(f"UL_{i} {label}是空白")
            elif value is not None and not isinstance(value, Decimal):
                problems.append(f"UL_{i} {label}不是數字（{value}）")
    return problems


def _date_problems(record: OrderRecord, fmt: ReferenceFormat) -> list[str]:
    problems = []
    for n in range(1, SLOTS + 1):
        value = _value(record, autocall_date_key(n), fmt)
        if value is not None and not isinstance(value, dt.date):
            problems.append(f"比價日_{n} 不是日期（{value}）")
    return problems


def _status_event(status: str | None) -> tuple[_LegacyEvent | None, str]:
    """庫存狀態 → (事件, 事件日)；看不懂的 KO 寫法是錯誤，其他狀態（在庫、接股等）不寫事件。"""
    if not status:
        return None, ""
    match = _KO.search(status)
    if match:
        try:
            return _LegacyEvent(KO, dt.date(*(int(part) for part in match.groups()))), ""
        except ValueError:
            return None, f"庫存狀態「{status}」的 KO 日期不是有效的日期"
    if "KO" in status.upper():
        return None, f"庫存狀態「{status}」看不出 KO 的日期（要寫成「已於 Y/M/D KO」）"
    if "到期還本" in status:
        return _LegacyEvent(MATURITY, None), ""
    return None, ""


def _code(code: str) -> str:
    """比對代碼用：不分大小寫、空白，結尾的「Equity」不算。"""
    text = " ".join(code.upper().split())
    return text.removesuffix(" EQUITY")


# ---------- 寫入 ----------


def _write_prices(
    conn: psycopg.Connection, tdcc: str, record: OrderRecord, found: _Product, fmt: ReferenceFormat
) -> None:
    for i, code in enumerate(found.codes, 1):
        if code is None:
            continue
        values = [_value(record, price_key(i, col), fmt) for col, _, _ in PRICES]
        conn.execute(
            "INSERT INTO workflow.underlying_price (tdcc, seq, code, initial_price, strike_price, ki_price, ko_price)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (tdcc, i, code, *values),
        )


def _write_dates(conn: psycopg.Connection, tdcc: str, record: OrderRecord, fmt: ReferenceFormat) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO workflow.autocall_date (tdcc, slot, value) VALUES (%s, %s, %s)",
            [(tdcc, n, _value(record, autocall_date_key(n), fmt)) for n in range(1, SLOTS + 1)],
        )


def _write_error_book(path: Path, lines: Sequence[BackfillLine]) -> None:
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "舊商品補資料錯誤"
    sheet.append([name for name, _ in ERROR_BOOK_COLUMNS])
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for line in lines:
        sheet.append([line.tdcc, line.detail, "、".join(map(str, line.rows))])
    for i, (_, width) in enumerate(ERROR_BOOK_COLUMNS, start=1):
        sheet.column_dimensions[get_column_letter(i)].width = width
    sheet.freeze_panes = "A2"
    try:
        book.save(path)
    except OSError as exc:
        raise LegacyBackfillError(f"錯誤清單寫不進去，整份不匯入：{path}（{exc.strerror or exc}）") from exc
