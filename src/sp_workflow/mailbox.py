"""信箱維護（#158）：列出、新增、修改、刪除人員（`mailbox.person`）與別名（`mailbox.alias`），不必寫 SQL。

每個動作一個 transaction：鎖住那一列、確認打開後沒被改過，再寫入並留一筆 `mailbox.change_log`（誰、何時、改前改後）。
名字不分大小寫不重複、信箱格式、別名指到存在的人、別名不分大小寫不重複都由資料庫檢查（0002）；
檢查失敗或帳號沒有寫入權限時轉成作業人員看得懂的 `MailboxError`。改名時資料庫讓別名跟著改、刪除人員時連帶刪掉
他的別名，這兩種的紀錄另外寫出那些別名。
畫面在 `sp_workflow.tabs.mailbox`。
人員型別 `Person`、名字比對 `Mailbox.match`、PB 判斷 `is_pb` 與讀取 `load_mailbox` 也在這裡（#201）：
每日匯入、寄信草稿與交易明細都從這裡拿，比對規則（不分大小寫、先查別名、不模糊比對）只有這一份。
"""

from __future__ import annotations

import getpass
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from sp_workflow.product_record import to_json

if TYPE_CHECKING:
    import psycopg

PB = "PB"
PERSON, ALIAS = "人員", "別名"
ADD, EDIT, DELETE = "新增", "修改", "刪除"


# ---------- 信箱的型別、名字比對與讀取（#201 從 daily_import 搬來；每日匯入、寄信草稿、交易明細共用） ----------


@dataclass(frozen=True)
class Person:
    name: str
    email: str
    unit: str | None
    fixed_cc: bool


@dataclass(frozen=True)
class Mailbox:
    people: tuple[Person, ...]
    aliases: Mapping[str, str]  # 別名（小寫）→ 使用者名稱

    def match(self, name: str) -> Person | None:
        """大小寫不同視為同一人；先查別名。不做模糊比對。"""
        key = name_key(name)
        target = name_key(self.aliases.get(key, name))
        return next((p for p in self.people if name_key(p.name) == target), None)


def name_key(name: str) -> str:
    """比對名字用的鍵：去頭尾空白、不分大小寫。"""
    return name.strip().lower()


def is_pb(name: str) -> bool:
    """交易列的 FC/PB 是 PB（私銀）：不寄信，但交易列照存。"""
    return name.strip().upper() == PB


def load_mailbox(conn: psycopg.Connection) -> Mailbox:
    """讀資料庫裡的人員與別名（信箱由資料庫直接維護，#35）。"""
    people = tuple(
        Person(*row) for row in conn.execute("SELECT name, email, unit, fixed_cc FROM mailbox.person ORDER BY name")
    )
    aliases = dict(conn.execute("SELECT alias_key, person_name FROM mailbox.alias").fetchall())
    return Mailbox(people, aliases)


# ---------- 信箱維護（#158） ----------


class MailboxError(Exception):
    """信箱維護寫不進去；訊息直接給作業人員看。"""


@dataclass(frozen=True)
class Alias:
    alias: str  # 交易清單上的寫法
    person_name: str  # 信箱裡的人員名字


@dataclass(frozen=True)
class MailboxRows:
    people: tuple[Person, ...]
    aliases: tuple[Alias, ...]

    def aliases_of(self, name: str) -> tuple[Alias, ...]:
        return tuple(a for a in self.aliases if a.person_name == name)


def person_from(name: str, email: str, unit: str, fixed_cc: bool) -> Person:
    """畫面輸入的人員：去頭尾空白，單位空白存成沒有；名字、信箱必填。"""
    person = Person(name.strip(), email.strip(), unit.strip() or None, fixed_cc)
    if not person.name:
        raise MailboxError("請填名字。")
    if not person.email:
        raise MailboxError("請填信箱。")
    return person


def alias_from(alias: str, person_name: str) -> Alias:
    """畫面輸入的別名：去頭尾空白；別名與人員必填。"""
    found = Alias(alias.strip(), person_name.strip())
    if not found.alias:
        raise MailboxError("請填別名（交易清單上的寫法）。")
    if not found.person_name:
        raise MailboxError("請選這個別名是哪一個人。")
    return found


def load_rows(conn: psycopg.Connection) -> MailboxRows:
    """全部人員與別名，依名字（不分大小寫）排序。"""
    people = tuple(
        Person(*row)
        for row in conn.execute("SELECT name, email, unit, fixed_cc FROM mailbox.person ORDER BY name_key, name")
    )
    aliases = tuple(
        Alias(*row) for row in conn.execute("SELECT alias, person_name FROM mailbox.alias ORDER BY alias_key")
    )
    return MailboxRows(people, aliases)


def save_person(conn: psycopg.Connection, before: Person | None, after: Person, *, user: str | None = None) -> bool:
    """新增（before 是 None）或修改一個人；沒有改任何欄位時不寫，回傳 False。改名時紀錄寫出跟著改的別名。"""
    if before == after:
        return False
    with _explained(conn, after.name, email=after.email):
        if before is None:
            conn.execute(
                "INSERT INTO mailbox.person (name, email, unit, fixed_cc) VALUES (%s, %s, %s, %s)",
                (after.name, after.email, after.unit, after.fixed_cc),
            )
            _log(conn, PERSON, ADD, None, asdict(after), user)
            return True
        _lock_person(conn, before)
        moved = {"aliases": _aliases_of(conn, before.name)} if after.name != before.name else {}
        conn.execute(
            "UPDATE mailbox.person SET name = %s, email = %s, unit = %s, fixed_cc = %s WHERE name = %s",
            (after.name, after.email, after.unit, after.fixed_cc, before.name),
        )
        _log(conn, PERSON, EDIT, {**asdict(before), **moved}, {**asdict(after), **moved}, user)
    return True


def delete_person(conn: psycopg.Connection, before: Person, *, user: str | None = None) -> tuple[Alias, ...]:
    """刪除一個人，資料庫連帶刪掉他的別名；回傳刪掉的別名，改前紀錄也寫出它們。"""
    with _explained(conn, before.name):
        _lock_person(conn, before)
        aliases = _aliases_of(conn, before.name)
        conn.execute("DELETE FROM mailbox.person WHERE name = %s", (before.name,))
        _log(conn, PERSON, DELETE, {**asdict(before), "aliases": aliases}, None, user)
    return tuple(Alias(alias, before.name) for alias in aliases)


def save_alias(conn: psycopg.Connection, before: Alias | None, after: Alias, *, user: str | None = None) -> bool:
    """新增（before 是 None）或修改一個別名；沒有改任何欄位時不寫，回傳 False。"""
    if before == after:
        return False
    with _explained(conn, after.person_name, alias=after.alias):
        if before is None:
            conn.execute(
                "INSERT INTO mailbox.alias (alias, person_name) VALUES (%s, %s)", (after.alias, after.person_name)
            )
            _log(conn, ALIAS, ADD, None, asdict(after), user)
            return True
        _lock_alias(conn, before)
        conn.execute(
            "UPDATE mailbox.alias SET alias = %s, person_name = %s WHERE alias_key = lower(btrim(%s))",
            (after.alias, after.person_name, before.alias),
        )
        _log(conn, ALIAS, EDIT, asdict(before), asdict(after), user)
    return True


def delete_alias(conn: psycopg.Connection, before: Alias, *, user: str | None = None) -> None:
    with _explained(conn, before.person_name, alias=before.alias):
        _lock_alias(conn, before)
        conn.execute("DELETE FROM mailbox.alias WHERE alias_key = lower(btrim(%s))", (before.alias,))
        _log(conn, ALIAS, DELETE, asdict(before), None, user)


_REASONS = {  # 資料庫檢查（0001、0002 的 constraint）→ 給作業人員看的原因
    "person_pkey": "名字「{name}」已經有了（不分大小寫）",
    "person_name_key_unique": "名字「{name}」已經有了（不分大小寫）",
    "person_email_format": "信箱「{email}」格式不對（要像 name@example.com）",
    "alias_key_unique": "別名「{alias}」已經有了（不分大小寫）",
    "alias_person_name_fkey": "別名要指到信箱裡的人：沒有「{name}」",
}


@contextmanager
def _explained(conn: psycopg.Connection, name: str, *, email: str = "", alias: str = "") -> Iterator[None]:
    """一個 transaction；資料庫的檢查失敗或沒有權限時整個不寫，改丟 `MailboxError` 寫出原因。"""
    from psycopg import errors

    try:
        with conn.transaction():
            yield
    except errors.InsufficientPrivilege as exc:
        raise MailboxError("這個資料庫帳號沒有修改信箱的權限，請找管理者開權限。") from exc
    except errors.IntegrityError as exc:
        reason = _REASONS.get(exc.diag.constraint_name or "")
        if reason is None:
            first = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            raise MailboxError(f"資料庫拒絕這次存檔：{first}") from exc
        raise MailboxError(reason.format(name=name, email=email, alias=alias)) from exc


def _aliases_of(conn: psycopg.Connection, name: str) -> list[str]:
    """這個人的別名，鎖住：改名、刪除人員時會跟著改、跟著刪的那些。"""
    rows = conn.execute("SELECT alias FROM mailbox.alias WHERE person_name = %s ORDER BY alias_key FOR UPDATE", (name,))
    return [alias for (alias,) in rows]


def _lock_person(conn: psycopg.Connection, before: Person) -> None:
    row = conn.execute(
        "SELECT name, email, unit, fixed_cc FROM mailbox.person WHERE name = %s FOR UPDATE", (before.name,)
    ).fetchone()
    if row is None or Person(*row) != before:
        raise MailboxError(f"人員「{before.name}」在打開之後被改過或刪掉了，請按「重新整理」再改。")


def _lock_alias(conn: psycopg.Connection, before: Alias) -> None:
    row = conn.execute(
        "SELECT alias, person_name FROM mailbox.alias WHERE alias_key = lower(btrim(%s)) FOR UPDATE", (before.alias,)
    ).fetchone()
    if row is None or Alias(*row) != before:
        raise MailboxError(f"別名「{before.alias}」在打開之後被改過或刪掉了，請按「重新整理」再改。")


def _log(
    conn: psycopg.Connection, kind: str, action: str, before: dict | None, after: dict | None, user: str | None
) -> None:
    conn.execute(
        "INSERT INTO mailbox.change_log (changed_by, kind, action, before, after)"
        " VALUES (%s, %s, %s, %s::jsonb, %s::jsonb)",
        (user or getpass.getuser(), kind, action, to_json(before), to_json(after)),
    )
