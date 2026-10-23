"""存續事件（#152、ADR 0019）：存續狀態 = 最新一筆還有效的存續事件，沒有事件就是在庫。

`workflow.product_event` 只新增：
- 自動事件（KO、接股、到期還本）只由比價寫，`replace_auto_events` 收這檔算出的整組，和資料庫現有的
  不同才整組取代（舊的記取代時間、新增新的一組），差異寫進 `workflow.product_event_change`；人工不能新增、修改或刪除。
  已出場的商品不再比價（#169、ADR 0021），所以比價只在第一次算出事件時呼叫它。
- 人工事件只有顧客贖回（`record_redemption`），已有存續事件的商品不能登記。
- 舊清單的狀態在資料表版本 0016 轉成來源「舊清單」的事件，舊商品匯入（`record_legacy`，#154）也寫這種；比價不取代它們。
- 對自動事件有意見只能提出異議（`raise_objection`／`resolve_objection`），異議不改事件。
`workflow.product` 舊的 `status_event` 等四欄已由資料表版本 0020 移除（#172）。
"""

from __future__ import annotations

import datetime as dt
import getpass
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import psycopg

IN_STOCK = "在庫"
KO = "KO"
DELIVERY = "接股"
MATURITY = "到期還本"
REDEMPTION = "顧客贖回"
AUTO_EVENTS = (KO, DELIVERY, MATURITY)
EXIT_EVENTS = (*AUTO_EVENTS, REDEMPTION)  # 會讓商品出場的事件：自動事件加顧客贖回（#188）

OBSERVATION = "比價"  # 事件的來源
MANUAL = "人工"
LEGACY = "舊清單"  # 資料表版本 0016 轉換的舊狀態
SOURCES = (OBSERVATION, LEGACY, MANUAL)

_EVENT_COLUMNS = "event_id, tdcc, event, event_date, underlying, source, note, recorded_by, recorded_at"


class LifecycleError(Exception):
    """存續事件不能寫（例如已有存續事件、不是自動事件）；訊息是給作業人員看的中文。"""


@dataclass(frozen=True)
class AutoEvent:
    """比價算出的一筆自動事件。"""

    event: str
    event_date: dt.date
    underlying: str | None = None

    def key(self) -> tuple[str, str, str | None]:
        """比對與取代紀錄用的 [事件, 事件日, 標的]。"""
        return _key(self.event, self.event_date, self.underlying)


@dataclass(frozen=True)
class StoredEvent:
    event_id: int
    tdcc: str
    event: str
    event_date: dt.date | None
    underlying: str | None
    source: str
    note: str | None
    recorded_by: str
    recorded_at: dt.datetime


@dataclass(frozen=True)
class Objection:
    """一筆還沒解除的異議與它針對的事件。"""

    objection_id: int
    event_id: int
    tdcc: str
    event: str
    event_date: dt.date | None
    note: str
    raised_by: str
    raised_at: dt.datetime


@dataclass(frozen=True)
class Lifecycle:
    """一檔的存續狀態與決定它的那一筆事件（在庫時沒有）。"""

    status: str
    latest: StoredEvent | None = None


def lifecycle_status(conn: psycopg.Connection, tdccs: Iterable[str]) -> dict[str, Lifecycle]:
    """每個 TDCC 的存續狀態（依輸入順序）：最新一筆還有效的事件（事件日最晚，同一天取後寫的），沒有就是在庫。"""
    wanted = list(dict.fromkeys(tdccs))
    rows = conn.execute(
        f"SELECT DISTINCT ON (tdcc) {_EVENT_COLUMNS} FROM workflow.product_event"
        " WHERE tdcc = ANY(%s) AND superseded_at IS NULL"
        " ORDER BY tdcc, event_date DESC NULLS LAST, event_id DESC",
        (wanted,),
    ).fetchall()
    latest = {row[1]: StoredEvent(*row) for row in rows}
    return {t: Lifecycle(latest[t].event, latest[t]) if t in latest else Lifecycle(IN_STOCK) for t in wanted}


def current_events_of(conn: psycopg.Connection, tdccs: Iterable[str]) -> dict[str, list[StoredEvent]]:
    """多檔一次讀：每個 TDCC 還有效的事件，依事件日、寫入先後排序；沒有事件的不放。"""
    found: dict[str, list[StoredEvent]] = {}
    for row in conn.execute(
        f"SELECT {_EVENT_COLUMNS} FROM workflow.product_event WHERE tdcc = ANY(%s) AND superseded_at IS NULL"
        " ORDER BY tdcc, event_date NULLS FIRST, event_id",
        (list(dict.fromkeys(tdccs)),),
    ):
        found.setdefault(row[1], []).append(StoredEvent(*row))
    return found


def current_events(conn: psycopg.Connection, tdcc: str) -> list[StoredEvent]:
    """這檔還有效的事件，依事件日、寫入先後排序。"""
    rows = conn.execute(
        f"SELECT {_EVENT_COLUMNS} FROM workflow.product_event WHERE tdcc = %s AND superseded_at IS NULL"
        " ORDER BY event_date NULLS FIRST, event_id",
        (tdcc,),
    ).fetchall()
    return [StoredEvent(*row) for row in rows]


def exits_on(conn: psycopg.Connection, day: dt.date) -> list[StoredEvent]:
    """出場清單（#171、#188）：事件日是 `day`、還有效的出場事件，來源不限（比價、舊清單、人工），依 TDCC、寫入先後排序。"""
    rows = conn.execute(
        f"SELECT {_EVENT_COLUMNS} FROM workflow.product_event WHERE event_date = %s"
        " AND event = ANY(%s) AND superseded_at IS NULL ORDER BY tdcc, event_id",
        (day, list(EXIT_EVENTS)),
    ).fetchall()
    return [StoredEvent(*row) for row in rows]


def open_objections(conn: psycopg.Connection, tdccs: Iterable[str]) -> dict[str, list[Objection]]:
    """每個 TDCC 還沒解除的異議（含針對已被重新比價取代的事件），依提出先後；沒有的不放。"""
    found: dict[str, list[Objection]] = {}
    for row in conn.execute(
        "SELECT o.objection_id, o.event_id, e.tdcc, e.event, e.event_date, o.note, o.raised_by, o.raised_at"
        " FROM workflow.event_objection o JOIN workflow.product_event e ON e.event_id = o.event_id"
        " WHERE e.tdcc = ANY(%s) AND o.resolved_at IS NULL ORDER BY o.raised_at, o.objection_id",
        (list(dict.fromkeys(tdccs)),),
    ):
        found.setdefault(row[2], []).append(Objection(*row))
    return found


def replace_auto_events(
    conn: psycopg.Connection, tdcc: str, events: Sequence[AutoEvent], *, user: str | None = None
) -> bool:
    """比價算出這檔的整組自動事件；和資料庫現有的不同才整組取代並留紀錄，回傳有沒有改。

    已登記顧客贖回的商品停止比價，給了也不寫。一組依事件日寫入（同一天的後寫，存續狀態取後寫的那筆）。
    """
    wrong = [e.event for e in events if e.event not in AUTO_EVENTS]
    if wrong:
        raise LifecycleError(f"{tdcc} 的 {'、'.join(wrong)} 不是自動事件（只有 KO、接股、到期還本）")
    by = user or getpass.getuser()
    ordered = sorted(events, key=lambda e: (e.event_date, AUTO_EVENTS.index(e.event), e.underlying or ""))
    new = [e.key() for e in ordered]
    with conn.transaction():
        _lock_product(conn, tdcc)
        current = current_events(conn, tdcc)
        if any(e.source == MANUAL for e in current):
            raise LifecycleError(f"{tdcc} 已登記顧客贖回，停止比價，不寫自動事件")
        old = [_key(e.event, e.event_date, e.underlying) for e in current if e.source == OBSERVATION]
        if sorted(old) == sorted(new):
            return False
        conn.execute(
            "UPDATE workflow.product_event SET superseded_at = now()"
            " WHERE tdcc = %s AND source = %s AND superseded_at IS NULL",
            (tdcc, OBSERVATION),
        )
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO workflow.product_event (tdcc, event, event_date, underlying, source, recorded_by)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                [(tdcc, e.event, e.event_date, e.underlying, OBSERVATION, by) for e in ordered],
            )
        conn.execute(
            "INSERT INTO workflow.product_event_change (tdcc, changed_by, previous, current) VALUES (%s, %s, %s, %s)",
            (tdcc, by, json.dumps(old, ensure_ascii=False), json.dumps(new, ensure_ascii=False)),
        )
    return True


def record_redemption(
    conn: psycopg.Connection, tdcc: str, event_date: dt.date, note: str = "", *, user: str | None = None
) -> StoredEvent:
    """人工登記顧客贖回；已有存續事件（任何來源）的商品不能登記。登記後這檔停止比價。"""
    with conn.transaction():
        _lock_product(conn, tdcc)
        existing = current_events(conn, tdcc)
        if existing:
            last = existing[-1]
            raise LifecycleError(
                f"{tdcc} 已有存續事件「{last.event}」（{_date_text(last.event_date)}，{last.source}），不能登記顧客贖回"
            )
        row = conn.execute(
            "INSERT INTO workflow.product_event (tdcc, event, event_date, source, note, recorded_by)"
            f" VALUES (%s, %s, %s, %s, %s, %s) RETURNING {_EVENT_COLUMNS}",
            (tdcc, REDEMPTION, event_date, MANUAL, note.strip() or None, user or getpass.getuser()),
        ).fetchone()
    return StoredEvent(*row)


def record_legacy(
    conn: psycopg.Connection, tdcc: str, event: str, event_date: dt.date | None, note: str, *, user: str | None = None
) -> StoredEvent:
    """舊商品匯入（#154）寫一筆來源「舊清單」的事件（KO 或到期還本）；已有存續事件（任何來源）的商品不能寫。"""
    if event not in (KO, MATURITY):
        raise LifecycleError(f"舊清單事件只能是 KO 或到期還本，不能是「{event}」")
    with conn.transaction():
        _lock_product(conn, tdcc)
        existing = current_events(conn, tdcc)
        if existing:
            last = existing[-1]
            raise LifecycleError(
                f"{tdcc} 已有存續事件「{last.event}」（{_date_text(last.event_date)}，{last.source}），不能再寫舊清單事件"
            )
        row = conn.execute(
            "INSERT INTO workflow.product_event (tdcc, event, event_date, source, note, recorded_by)"
            f" VALUES (%s, %s, %s, %s, %s, %s) RETURNING {_EVENT_COLUMNS}",
            (tdcc, event, event_date, LEGACY, note.strip() or None, user or getpass.getuser()),
        ).fetchone()
    return StoredEvent(*row)


def raise_objection(conn: psycopg.Connection, event_id: int, note: str, *, user: str | None = None) -> int:
    """對一筆還有效的自動事件（來源比價）提出異議，回傳異議編號；事件不改。"""
    if not note.strip():
        raise LifecycleError("提出異議要寫備註（上手通知的內容或哪裡不同）")
    with conn.transaction():
        row = conn.execute(
            "SELECT tdcc, source, superseded_at FROM workflow.product_event WHERE event_id = %s", (event_id,)
        ).fetchone()
        if row is None:
            raise LifecycleError(f"找不到存續事件 {event_id}")
        if row[1] != OBSERVATION:
            raise LifecycleError(f"{row[0]} 的這筆事件來源是{row[1]}，只有比價產生的自動事件可以提出異議")
        if row[2] is not None:
            raise LifecycleError(f"{row[0]} 的這筆事件已被重新比價取代，請對目前的事件提出異議")
        (objection_id,) = conn.execute(
            "INSERT INTO workflow.event_objection (event_id, note, raised_by) VALUES (%s, %s, %s)"
            " RETURNING objection_id",
            (event_id, note.strip(), user or getpass.getuser()),
        ).fetchone()
    return objection_id


def resolve_objection(conn: psycopg.Connection, objection_id: int, *, user: str | None = None) -> None:
    """解除異議：記下解除的人與時間；已解除的不能再解除。"""
    with conn.transaction():
        row = conn.execute(
            "UPDATE workflow.event_objection SET resolved_at = now(), resolved_by = %s"
            " WHERE objection_id = %s AND resolved_at IS NULL RETURNING objection_id",
            (user or getpass.getuser(), objection_id),
        ).fetchone()
        if row is None:
            raise LifecycleError(f"異議 {objection_id} 不存在或已經解除")


def _lock_product(conn: psycopg.Connection, tdcc: str) -> None:
    """鎖住這檔的商品列，同時寫同一檔的事件時一個一個來。"""
    if conn.execute("SELECT 1 FROM workflow.product WHERE tdcc = %s FOR UPDATE", (tdcc,)).fetchone() is None:
        raise LifecycleError(f"資料庫沒有 {tdcc} 的商品條件")


def _key(event: str, day: dt.date | None, underlying: str | None) -> tuple[str, str, str | None]:
    return (event, day.isoformat() if day else "", underlying)


def _date_text(day: dt.date | None) -> str:
    return day.isoformat() if day else "沒有事件日"
