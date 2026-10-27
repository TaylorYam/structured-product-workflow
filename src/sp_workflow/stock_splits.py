"""拆股登記（#270、ADR 0024）：標的拆股、合股的生效日與前後股數，比價時把收盤價換回交易時的基準。

- 一筆登記是一個代碼在一個生效日（第一個用新股數交易的日子）的拆股前、拆股後股數（`workflow.stock_split`）；
  代碼同收盤價表（去掉 Equity 這類類別字）。前後股數都是 1 是「確認不是拆股」：那天的大漲大跌是真的。
- 換算（`SplitBook.factor`）：生效日在基準日（Strike date）之後、不晚於觀察日的拆股，收盤價乘上「拆股後 ÷ 拆股前」。
- 提醒（`SplitBook.jumps`）：同一個代碼相鄰兩個收盤日，換算後的價格比例低於 `JUMP_LOW` 倍或高於它的倒數時是
  疑似拆股或收盤價有誤；這段期間有「確認不是拆股」的登記就不算。
- 維護只有新增、刪除（改就刪掉重登）；每個動作一個 transaction，並留一筆 `workflow.stock_split_log`。
  新增、刪除之後，受影響、由比價判定出場的商品要重新比價（`affected_exits`，由 `window_actions` 接著比價）。
畫面在 `sp_workflow.tabs.splits`。傳入的 `conn` 由 `database.connect` 建立。
"""

from __future__ import annotations

import datetime as dt
import getpass
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

from sp_workflow import sheet_cells
from sp_workflow.database import OperatorError
from sp_workflow.lifecycle import OBSERVATION
from sp_workflow.product_record import to_json

if TYPE_CHECKING:
    import psycopg

ADD, DELETE = "新增", "刪除"
JUMP_LOW = Decimal("0.6")  # 相鄰兩個收盤日的價格比例低於這個、或高於它的倒數（約 1.67），算疑似拆股；1 拆 2 約 0.5
NOT_A_SPLIT = "確認不是拆股"


class StockSplitError(OperatorError):
    """拆股登記寫不進去或輸入有錯；訊息直接給作業人員看。"""


@dataclass(frozen=True)
class Split:
    code: str  # 同收盤價表，例：AAA UW
    effective_date: dt.date  # 第一個用新股數交易的日子
    shares_before: int
    shares_after: int
    note: str | None = None
    recorded_by: str = field(default="", compare=False)
    recorded_at: dt.datetime | None = field(default=None, compare=False)

    @property
    def factor(self) -> Decimal:
        """生效日起的收盤價乘上這個，換回拆股前的基準。"""
        return Decimal(self.shares_after) / Decimal(self.shares_before)

    @property
    def not_a_split(self) -> bool:
        return self.shares_before == self.shares_after == 1

    @property
    def label(self) -> str:
        """畫面上的比例：1 拆 100、10 合 1、確認不是拆股。"""
        if self.not_a_split:
            return NOT_A_SPLIT
        verb = "拆" if self.shares_after > self.shares_before else "合"
        return f"{self.shares_before} {verb} {self.shares_after}"

    def record(self) -> dict[str, object]:
        return {
            "code": self.code,
            "effective_date": self.effective_date,
            "shares_before": self.shares_before,
            "shares_after": self.shares_after,
            "note": self.note,
        }


@dataclass(frozen=True)
class Jump:
    """相鄰兩個收盤日（`before` → `day`）換算後的價格比例超出範圍：疑似拆股或收盤價有誤。"""

    code: str
    before: dt.date
    day: dt.date
    ratio: Decimal  # `day` 的收盤價 ÷ `before` 的收盤價（已套用登記的拆股）


class SplitBook:
    """全部拆股登記，依代碼查：比價換算收盤價、找疑似拆股的價格斷層。"""

    def __init__(self, splits: Iterable[Split] = ()) -> None:
        self.by_code: dict[str, list[Split]] = {}
        for split in sorted(splits, key=lambda s: (s.code, s.effective_date)):
            self.by_code.setdefault(split.code, []).append(split)

    def factor(self, code: str, base: dt.date, day: dt.date) -> Decimal:
        """`day` 的收盤價換回 `base`（Strike date）的基準要乘的數：生效日在 (`base`, `day`] 的拆股相乘。"""
        result = Decimal(1)
        for split in self.by_code.get(code, ()):
            if base < split.effective_date <= day:
                result *= split.factor
        return result

    def jumps(self, code: str, closes: Mapping[dt.date, Decimal]) -> tuple[Jump, ...]:
        """這個代碼的價格斷層：相鄰兩個收盤日換算後的比例 < `JUMP_LOW` 或 > 1 ÷ `JUMP_LOW`，期間沒有確認不是拆股。"""
        found = []
        days = sorted(closes)
        for before, day in zip(days, days[1:], strict=False):
            if closes[before] <= 0:
                continue
            ratio = closes[day] * self.factor(code, before, day) / closes[before]
            if JUMP_LOW <= ratio <= 1 / JUMP_LOW:
                continue
            if any(s.not_a_split and before < s.effective_date <= day for s in self.by_code.get(code, ())):
                continue
            found.append(Jump(code, before, day, ratio))
        return tuple(found)


# ---------- 畫面輸入 ----------


def split_from(code: str, effective_date: dt.date, before: str, after: str, note: str = "") -> Split:
    """畫面輸入的一筆：代碼去掉 Equity 這類字尾，前後股數是正整數。"""
    normalized = sheet_cells.bbg_code(code)
    if not normalized:
        raise StockSplitError("請填代碼（例：AAA UW，可以帶 Equity）。")
    return Split(
        normalized, effective_date, _shares("拆股前股數", before), _shares("拆股後股數", after), note.strip() or None
    )


def _shares(label: str, text: str) -> int:
    value = text.strip().replace(",", "")
    if not value.isdigit() or int(value) <= 0:
        raise StockSplitError(f"{label}請填正整數（例：1 拆 100 填 1 和 100；確認不是拆股填 1 和 1）。")
    return int(value)


# ---------- 讀取 ----------


def load_splits(conn: psycopg.Connection) -> tuple[Split, ...]:
    """全部拆股登記，依代碼、生效日排序。"""
    return tuple(
        Split(*row)
        for row in conn.execute(
            "SELECT code, effective_date, shares_before, shares_after, note, recorded_by, recorded_at"
            " FROM workflow.stock_split ORDER BY code, effective_date"
        )
    )


def affected_exits(conn: psycopg.Connection, split: Split) -> list[str]:
    """這筆登記改了之後要重新比價的已出場商品：用到這個代碼、生效日在 Strike date 之後、
    還有效的事件全部來自比價、而且最晚的事件不早於生效日（之前就出場的不受影響）。"""
    rows = conn.execute(
        "SELECT p.tdcc FROM workflow.product p"
        " WHERE p.strike_date < %(day)s"
        " AND EXISTS (SELECT 1 FROM workflow.underlying_price u WHERE u.tdcc = p.tdcc AND u.code = %(code)s)"
        " AND EXISTS (SELECT 1 FROM workflow.product_event e WHERE e.tdcc = p.tdcc AND e.superseded_at IS NULL"
        "             AND e.event_date >= %(day)s)"
        " AND NOT EXISTS (SELECT 1 FROM workflow.product_event e WHERE e.tdcc = p.tdcc AND e.superseded_at IS NULL"
        "                 AND e.source <> %(auto)s)"
        " ORDER BY p.tdcc",
        {"day": split.effective_date, "code": split.code, "auto": OBSERVATION},
    )
    return [tdcc for (tdcc,) in rows]


# ---------- 維護 ----------


def add_split(conn: psycopg.Connection, split: Split, *, user: str | None = None) -> None:
    """新增一筆；收盤價表與商品都沒有這個代碼時不寫（多半是打錯）。"""
    with _explained(conn, split):
        known = conn.execute(
            "SELECT EXISTS (SELECT 1 FROM workflow.close_price WHERE code = %(code)s)"
            " OR EXISTS (SELECT 1 FROM workflow.underlying_price WHERE code = %(code)s)",
            {"code": split.code},
        ).fetchone()
        if not (known and known[0]):
            raise StockSplitError(f"收盤價表和商品都沒有「{split.code}」這個代碼，請確認代碼（例：AAA UW）。")
        conn.execute(
            "INSERT INTO workflow.stock_split (code, effective_date, shares_before, shares_after, note, recorded_by)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (
                split.code,
                split.effective_date,
                split.shares_before,
                split.shares_after,
                split.note,
                user or getpass.getuser(),
            ),
        )
        _log(conn, ADD, split, user)


def delete_split(conn: psycopg.Connection, split: Split, *, user: str | None = None) -> None:
    """刪除一筆；打開之後被改過或刪掉了就不刪。"""
    with _explained(conn, split):
        deleted = conn.execute(
            "DELETE FROM workflow.stock_split WHERE code = %s AND effective_date = %s"
            " AND shares_before = %s AND shares_after = %s AND note IS NOT DISTINCT FROM %s",
            (split.code, split.effective_date, split.shares_before, split.shares_after, split.note),
        ).rowcount
        if deleted == 0:
            raise StockSplitError(
                f"{split.code} 在 {split.effective_date} 的拆股登記在打開之後被改過或刪掉了，請按「重新整理」再看。"
            )
        _log(conn, DELETE, split, user)


@contextmanager
def _explained(conn: psycopg.Connection, split: Split) -> Iterator[None]:
    """一個 transaction；資料庫的檢查失敗或沒有權限時整個不寫，改丟 `StockSplitError` 寫出原因。"""
    from psycopg import errors

    try:
        with conn.transaction():
            yield
    except errors.InsufficientPrivilege as exc:
        raise StockSplitError("這個資料庫帳號沒有修改拆股登記的權限，請找管理者開權限。") from exc
    except errors.UniqueViolation as exc:
        raise StockSplitError(
            f"{split.code} 在 {split.effective_date} 已經有一筆拆股登記；要改請先刪掉那一筆再新增。"
        ) from exc
    except errors.IntegrityError as exc:
        first = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        raise StockSplitError(f"資料庫拒絕這次存檔：{first}") from exc


def _log(conn: psycopg.Connection, action: str, split: Split, user: str | None) -> None:
    conn.execute(
        "INSERT INTO workflow.stock_split_log (changed_by, action, split) VALUES (%s, %s, %s::jsonb)",
        (user or getpass.getuser(), action, to_json(split.record())),
    )
