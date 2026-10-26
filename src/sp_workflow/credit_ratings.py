"""發行機構評等（#237）：各上手的發行機構與保證機構在標準普爾、穆迪、惠譽的長期債務信用評等，保留生效日歷史。

- 機構以「上手代碼＋角色」識別（`workflow.rated_entity`）；上手代碼同 `workflow.product.issuer`。
- 一筆評等是一個機構在某個生效日的三家評等（`workflow.credit_rating`）；沒有該家評等時是 `None`。
- 某日的評等＝生效日 <= 那天的最新一筆（`RatingRows.on`、`rating_on`），比照審查通過日期（§2.5）。
  第一批評等的生效日是 `INITIAL`（畫面寫「最初」）：不設起始日，任何交易日都查得到。
- 維護規則比照審查通過日期：新增的生效日要比該機構最新一筆晚；只能修改、刪除最新一筆，而且至少留一筆。
  每個動作一個 transaction，鎖住該機構、確認打開後沒被改過再寫，並留一筆 `workflow.credit_rating_log`。
- 評等格式由資料庫檢查（0024），畫面輸入先在 `rating_from` 檢查、轉成作業人員看得懂的 `CreditRatingError`。
畫面在 `sp_workflow.tabs.ratings`。傳入的 `conn` 由 `database.connect` 建立。
"""

from __future__ import annotations

import datetime as dt
import getpass
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from fcn_checker.rating_formats import (
    AGENCIES,
    EXAMPLES,
    FITCH,
    FORMATS,
    GUARANTOR,
    MOODYS,
    ROLES,
    SP,
    canonical,
)
from fcn_checker.rating_formats import ISSUER as ISSUER  # 這裡沒用到：risk_level、畫面沿用舊的 import
from sp_workflow.database import OperatorError
from sp_workflow.product_record import to_json

if TYPE_CHECKING:
    import psycopg


INITIAL = dt.date(1900, 1, 1)  # 第一批評等：不設起始日
ADD, EDIT, DELETE = "新增", "修改", "刪除"

_ISSUER_FORMAT = re.compile(r"[A-Z]+")


class CreditRatingError(OperatorError):
    """評等維護寫不進去或輸入有錯；訊息直接給作業人員看。"""


@dataclass(frozen=True)
class Entity:
    issuer: str  # 上手代碼（BARC、CITI…）
    role: str  # 發行機構／保證機構
    name: str  # 顯示名稱

    @property
    def key(self) -> tuple[str, str]:
        return self.issuer, self.role


@dataclass(frozen=True)
class Rating:
    issuer: str
    role: str
    effective_date: dt.date
    sp: str | None
    moodys: str | None
    fitch: str | None

    @property
    def key(self) -> tuple[str, str]:
        return self.issuer, self.role

    def by_agency(self) -> dict[str, str]:
        """有評等的評等機構 → 評等（標準普爾、穆迪、惠譽的順序）。"""
        pairs = zip(AGENCIES, (self.sp, self.moodys, self.fitch), strict=True)
        return {agency: value for agency, value in pairs if value is not None}


@dataclass(frozen=True)
class RatingRows:
    entities: tuple[Entity, ...]  # 依上手代碼、角色（發行機構在前）排序
    ratings: tuple[Rating, ...]  # 依機構、生效日排序

    def history(self, issuer: str, role: str) -> tuple[Rating, ...]:
        """一個機構的全部評等，生效日由舊到新。"""
        return tuple(r for r in self.ratings if r.key == (issuer, role))

    def latest(self, issuer: str, role: str) -> Rating | None:
        found = self.history(issuer, role)
        return found[-1] if found else None

    def on(self, issuer: str, role: str, day: dt.date) -> Rating | None:
        """那天的評等：生效日 <= 那天的最新一筆；沒有就 `None`。"""
        return next((r for r in reversed(self.history(issuer, role)) if r.effective_date <= day), None)


def effective_label(day: dt.date) -> str:
    """畫面上的生效日：第一批寫「最初」。"""
    return "最初" if day == INITIAL else day.isoformat()


# ---------- 畫面輸入 ----------


def normalize_rating(agency: str, text: str) -> str | None:
    """一家評等機構的評等：寫法統一（`rating_formats.canonical`，同核對時參考條件表那邊），空白是沒有評等；格式不對丟錯誤。"""
    value = canonical(agency, text)
    if not value:
        return None
    if not FORMATS[agency].fullmatch(value):
        raise CreditRatingError(f"{agency}評等「{text.strip()}」格式不對（要像 {EXAMPLES[agency]}）。")
    return value


def rating_from(issuer: str, role: str, effective_date: dt.date, sp: str, moodys: str, fitch: str) -> Rating:
    """畫面輸入的一筆評等：三家至少要有一家。"""
    rating = Rating(
        issuer,
        role,
        effective_date,
        normalize_rating(SP, sp),
        normalize_rating(MOODYS, moodys),
        normalize_rating(FITCH, fitch),
    )
    if not rating.by_agency():
        raise CreditRatingError("三家評等機構至少要填一家。")
    return rating


def entity_from(issuer: str, role: str, name: str) -> Entity:
    """畫面輸入的新機構：上手代碼大寫英文字母、角色是發行機構或保證機構、名稱必填。"""
    entity = Entity(issuer.strip().upper(), role.strip(), name.strip())
    if not _ISSUER_FORMAT.fullmatch(entity.issuer):
        raise CreditRatingError("上手代碼請填英文字母（例：BARC、CITI）。")
    if entity.role not in ROLES:
        raise CreditRatingError("請選發行機構或保證機構。")
    if not entity.name:
        raise CreditRatingError("請填機構名稱。")
    return entity


# ---------- 讀取 ----------


def load_rows(conn: psycopg.Connection) -> RatingRows:
    """全部機構與評等。"""
    entities = tuple(
        Entity(*row)
        for row in conn.execute(
            "SELECT issuer, role, name FROM workflow.rated_entity ORDER BY issuer, role = %s, role", (GUARANTOR,)
        )
    )
    ratings = tuple(
        Rating(*row)
        for row in conn.execute(
            "SELECT issuer, role, effective_date, sp, moodys, fitch FROM workflow.credit_rating"
            " ORDER BY issuer, role = %s, role, effective_date",
            (GUARANTOR,),
        )
    )
    return RatingRows(entities, ratings)


def rating_on(conn: psycopg.Connection, issuer: str, role: str, day: dt.date) -> Rating | None:
    """一個機構在那天的評等：生效日 <= 那天的最新一筆；沒有就 `None`。"""
    row = conn.execute(
        "SELECT issuer, role, effective_date, sp, moodys, fitch FROM workflow.credit_rating"
        " WHERE issuer = %s AND role = %s AND effective_date <= %s ORDER BY effective_date DESC LIMIT 1",
        (issuer, role, day),
    ).fetchone()
    return None if row is None else Rating(*row)


# ---------- 維護 ----------


def add_entity(conn: psycopg.Connection, entity: Entity, first: Rating, *, user: str | None = None) -> None:
    """新增一個機構和它的第一筆評等（新上手用）；`first` 的機構要和 `entity` 相同。"""
    if first.key != entity.key:
        raise ValueError("第一筆評等要是這個機構的")
    with _explained(conn, entity.issuer, entity.role):
        conn.execute(
            "INSERT INTO workflow.rated_entity (issuer, role, name) VALUES (%s, %s, %s)",
            (entity.issuer, entity.role, entity.name),
        )
        _insert(conn, first)
        _log(conn, ADD, None, {**asdict(entity), **asdict(first)}, user)


def add_rating(conn: psycopg.Connection, latest: Rating | None, after: Rating, *, user: str | None = None) -> None:
    """新增一筆評等；`latest` 是打開時看到的最新一筆，生效日要比它晚。"""
    with _explained(conn, after.issuer, after.role):
        current = _lock_history(conn, after.issuer, after.role)
        if (current[-1] if current else None) != latest:
            raise _changed(after)
        if latest is not None and after.effective_date <= latest.effective_date:
            raise CreditRatingError(
                f"生效日要比最新一筆（{effective_label(latest.effective_date)}）晚；要改最新一筆請按「修改最新一筆」。"
            )
        _insert(conn, after)
        _log(conn, ADD, None, asdict(after), user)


def edit_latest(conn: psycopg.Connection, before: Rating, after: Rating, *, user: str | None = None) -> bool:
    """修改最新一筆（評等或生效日）；生效日仍要比前一筆晚。沒有改任何欄位時不寫，回傳 False。"""
    if before == after:
        return False
    if before.key != after.key:
        raise ValueError("只能改同一個機構的評等")
    with _explained(conn, after.issuer, after.role):
        current = _lock_history(conn, before.issuer, before.role)
        if not current or current[-1] != before:
            raise _changed(before)
        if before.effective_date == INITIAL and after.effective_date != INITIAL:
            raise CreditRatingError("第一批評等（最初）的生效日不能改；評等變了請新增一筆。")
        if len(current) > 1 and after.effective_date <= current[-2].effective_date:
            raise CreditRatingError(f"生效日要比前一筆（{effective_label(current[-2].effective_date)}）晚。")
        conn.execute(
            "UPDATE workflow.credit_rating SET effective_date = %s, sp = %s, moodys = %s, fitch = %s"
            " WHERE issuer = %s AND role = %s AND effective_date = %s",
            (after.effective_date, after.sp, after.moodys, after.fitch, *before.key, before.effective_date),
        )
        _log(conn, EDIT, asdict(before), asdict(after), user)
    return True


def delete_latest(conn: psycopg.Connection, before: Rating, *, user: str | None = None) -> None:
    """刪除最新一筆；只剩一筆時不能刪。"""
    with _explained(conn, before.issuer, before.role):
        current = _lock_history(conn, before.issuer, before.role)
        if not current or current[-1] != before:
            raise _changed(before)
        if len(current) == 1:
            raise CreditRatingError("每個機構至少要留一筆評等，不能刪掉唯一的一筆。")
        conn.execute(
            "DELETE FROM workflow.credit_rating WHERE issuer = %s AND role = %s AND effective_date = %s",
            (*before.key, before.effective_date),
        )
        _log(conn, DELETE, asdict(before), None, user)


_REASONS = {  # 資料庫檢查（0024 的 constraint）→ 給作業人員看的原因
    "rated_entity_pkey": "{issuer} 的{role}已經有了",
    "credit_rating_pkey": "{issuer} 的{role}在這個生效日已經有評等",
    "credit_rating_sp_format": "標準普爾評等格式不對",
    "credit_rating_moodys_format": "穆迪評等格式不對",
    "credit_rating_fitch_format": "惠譽評等格式不對",
    "credit_rating_any": "三家評等機構至少要填一家",
}


@contextmanager
def _explained(conn: psycopg.Connection, issuer: str, role: str) -> Iterator[None]:
    """一個 transaction；資料庫的檢查失敗或沒有權限時整個不寫，改丟 `CreditRatingError` 寫出原因。"""
    from psycopg import errors

    try:
        with conn.transaction():
            yield
    except errors.InsufficientPrivilege as exc:
        raise CreditRatingError("這個資料庫帳號沒有修改評等的權限，請找管理者開權限。") from exc
    except errors.IntegrityError as exc:
        reason = _REASONS.get(exc.diag.constraint_name or "")
        if reason is None:
            first = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            raise CreditRatingError(f"資料庫拒絕這次存檔：{first}") from exc
        raise CreditRatingError(reason.format(issuer=issuer, role=role)) from exc


def _lock_history(conn: psycopg.Connection, issuer: str, role: str) -> list[Rating]:
    """鎖住這個機構（同一機構的存檔排隊），回傳它的評等（生效日由舊到新）。"""
    if (
        conn.execute(
            "SELECT 1 FROM workflow.rated_entity WHERE issuer = %s AND role = %s FOR UPDATE", (issuer, role)
        ).fetchone()
        is None
    ):
        raise CreditRatingError(f"評等表沒有 {issuer} 的{role}；請先新增機構。")
    return [
        Rating(*row)
        for row in conn.execute(
            "SELECT issuer, role, effective_date, sp, moodys, fitch FROM workflow.credit_rating"
            " WHERE issuer = %s AND role = %s ORDER BY effective_date",
            (issuer, role),
        )
    ]


def _changed(rating: Rating) -> CreditRatingError:
    return CreditRatingError(f"{rating.issuer} 的{rating.role}評等在打開之後被改過，請按「重新整理」再改。")


def _insert(conn: psycopg.Connection, rating: Rating) -> None:
    conn.execute(
        "INSERT INTO workflow.credit_rating (issuer, role, effective_date, sp, moodys, fitch)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (rating.issuer, rating.role, rating.effective_date, rating.sp, rating.moodys, rating.fitch),
    )


def _log(conn: psycopg.Connection, action: str, before: dict | None, after: dict | None, user: str | None) -> None:
    conn.execute(
        "INSERT INTO workflow.credit_rating_log (changed_by, action, before, after) VALUES (%s, %s, %s::jsonb, %s::jsonb)",
        (user or getpass.getuser(), action, to_json(before), to_json(after)),
    )
