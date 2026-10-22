"""人工維護修改交易明細（#116、ADR 0018）。

「7 人工維護」打開一檔時一起讀這個 TDCC 的交易明細列，放在編輯區的商品條件底下逐列修改；存檔由
`product_write.save_terms` 和商品條件在同一個 transaction 裡寫入、重算核對條件、留紀錄。對外：
- `load_trade_rows`：讀這檔的交易明細列（依 row_seq）與信箱；畫面打的字由 `editor_cells.commit_trade_rows` 解讀進
  `TradeRowsDraft` 每列的值（用 `daily_import.trade_value`，同每日匯入），存進去的值才會和之後匯入的一樣。
  `TradeRowsDraft.differences`：改了的格子 → 資料庫的值（畫面標黃）。
- `trade_problems`：擋存檔的問題（改了的格子必填空白、備註改成取消；寫錯的格子）；`trade_notes`／`unmatched_names`：
  改了的 FC 名字對不到信箱（照每日匯入的規則），只提示、不擋存檔。沒改的格子照資料庫的值，不擋也不提示。
- `read_rows`、`write_trade_rows`：存檔時鎖住這檔的列（確認打開之後沒變在 `product_write`）、寫入改了的格子。
只改值，不新增、不刪除列；之後的每日匯入照舊以資料庫為準、不覆蓋，改到和 Excel 一樣時匯入結果是「已存在」。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sp_workflow.check_terms import CHECK_TRADE_COLUMNS
from sp_workflow.daily_import import TRADE_FIELDS, TRADE_REQUIRED, Mailbox, is_cancel_note, is_pb, load_mailbox

if TYPE_CHECKING:
    import psycopg

FIELDS = tuple(TRADE_FIELDS)  # 畫面的欄（交易明細的表頭），依交易明細的順序
FC_FIELD, NOTE_FIELD = "FC/PB", "備註"
# 核對條件會用到的欄（分公司配置、交叉比對）：已鎖住的商品只有這幾欄改了才重算
CHECK_TERM_FIELDS = frozenset(name for name, column in TRADE_FIELDS.items() if column in CHECK_TRADE_COLUMNS)
CANCEL_NOTE = "寫 Cancel 或「取消」的列每日匯入不存資料庫；這裡只改值，不能刪除列"
UNMATCHED_NOTE = "名字對不到信箱，寄信草稿找不到這位 FC；可以存檔，請確認名字或在信箱表補上"
MAILBOX_EMPTY_NOTE = "信箱還沒有任何人員，FC 名字無法比對"
TradeEdit = tuple[int, str, object, object]  # 改了的一格：(row_seq, 欄, 打開時的值, 改成)


@dataclass
class TradeRowDraft:
    """一列交易明細；欄名是交易明細的表頭。`values` 是畫面上目前的值，`stored` 是打開時資料庫的值。"""

    seq: int  # row_seq
    values: dict[str, object]
    stored: dict[str, object]
    input_errors: dict[str, str] = field(default_factory=dict)  # 畫面上寫錯、還沒解讀成值的欄 → 說明

    def edits(self) -> list[tuple[str, object, object]]:
        """改了的格子：(欄, 打開時的值, 改成)。"""
        return [
            (name, self.stored.get(name), self.values.get(name))
            for name in FIELDS
            if self.stored.get(name) != self.values.get(name)
        ]


@dataclass
class TradeRowsDraft:
    """一個 TDCC 的交易明細列（依 row_seq）；資料庫還沒有這檔的交易明細時 `rows` 是空的。"""

    tdcc: str
    rows: list[TradeRowDraft]
    mailbox: Mailbox  # 打開時的信箱，比對 FC 名字用

    def edits(self) -> list[TradeEdit]:
        """改了的格子：(row_seq, 欄, 打開時的值, 改成)。"""
        return [(row.seq, *edit) for row in self.rows for edit in row.edits()]

    def differences(self) -> dict[tuple[int, str], object]:
        """和打開時不同的格子 (row_seq, 欄) → 資料庫的值（畫面標黃）；同 `OrderDraft.differences`。"""
        return {(seq, name): stored for seq, name, stored, _ in self.edits()}

    def has_input_errors(self) -> bool:
        return any(row.input_errors for row in self.rows)

    def previous(self) -> list[dict[str, object]]:
        """打開時（存檔前）的列，欄名同 `workflow.trade_row`：留紀錄用。"""
        return [{"row_seq": row.seq, **{TRADE_FIELDS[name]: row.stored[name] for name in FIELDS}} for row in self.rows]

    def reopen(self, saved: Mapping[int, Mapping[str, object]]) -> None:
        """存檔後當成重新打開：畫面與打開時的值都換成資料庫存的值（`read_rows`）。"""
        for row in self.rows:
            row.values, row.stored = dict(saved[row.seq]), dict(saved[row.seq])


# ---------- 檢查 ----------


def trade_problems(trades: TradeRowsDraft) -> dict[tuple[int, str], str]:
    """擋存檔的問題：{(row_seq, 欄): 說明}。只看改了的格子：必填欄（同每日匯入）清空、備註改成取消；
    畫面上寫錯的欄也算。沒改的格子照資料庫的值（例如以前匯入、備註寫了取消的列），不擋其他欄存檔。"""
    problems: dict[tuple[int, str], str] = {}
    for row in trades.rows:
        for name, _, value in row.edits():
            if value is None and name in TRADE_REQUIRED:
                problems[(row.seq, name)] = "必填"
            elif name == NOTE_FIELD and is_cancel_note(value):
                problems[(row.seq, name)] = CANCEL_NOTE
        problems.update({(row.seq, name): text for name, text in row.input_errors.items()})
    return problems


def trade_notes(trades: TradeRowsDraft) -> dict[tuple[int, str], str]:
    """不擋存檔的提示：改了的 FC 名字對不到信箱（照每日匯入：大小寫不同算同一人、先查別名、PB 不比）。"""
    note = UNMATCHED_NOTE if trades.mailbox.people else MAILBOX_EMPTY_NOTE
    edited = ((seq, after) for seq, name, _, after in trades.edits() if name == FC_FIELD)
    return {(seq, FC_FIELD): note for seq, after in edited if _unmatched(trades.mailbox, after)}


def unmatched_names(mailbox: Mailbox, changes: Iterable[TradeEdit]) -> tuple[str, ...]:
    """改了的格子（`edits`）裡對不到信箱的 FC 名字，依名字排、不重複。"""
    names = (after for _, name, _, after in changes if name == FC_FIELD)
    return tuple(sorted({str(name) for name in names if _unmatched(mailbox, name)}))


def _unmatched(mailbox: Mailbox, name: object) -> bool:
    return isinstance(name, str) and not is_pb(name) and mailbox.match(name) is None


# ---------- 資料庫 ----------


def load_trade_rows(conn: psycopg.Connection, tdcc: str) -> TradeRowsDraft:
    """這檔的交易明細列（依 row_seq）與信箱；值、打開時的值都是資料庫目前的值。"""
    with conn.transaction():
        rows = read_rows(conn, tdcc)
        mailbox = load_mailbox(conn)
    return TradeRowsDraft(
        tdcc, [TradeRowDraft(seq, dict(values), dict(values)) for seq, values in rows.items()], mailbox
    )


def read_rows(conn: psycopg.Connection, tdcc: str, *, lock: bool = False) -> dict[int, dict[str, object]]:
    """資料庫裡這檔的交易明細列：{row_seq: {欄: 值}}；`lock` 時鎖住這些列（存檔用）。"""
    found = conn.execute(
        f"SELECT row_seq, {', '.join(TRADE_FIELDS.values())} FROM workflow.trade_row WHERE tdcc = %s ORDER BY row_seq"
        + (" FOR UPDATE" if lock else ""),
        (tdcc,),
    )
    return {seq: dict(zip(FIELDS, values, strict=True)) for seq, *values in found}


def write_trade_rows(conn: psycopg.Connection, trades: TradeRowsDraft) -> list[TradeEdit]:
    """把改了的格子寫進 `workflow.trade_row`（只改值，不新增、不刪除列），回傳改了的格子（同 `edits`）。"""
    for row in trades.rows:
        changed = [name for name, _, _ in row.edits()]
        if changed:
            assignments = ", ".join(f"{TRADE_FIELDS[name]} = %s" for name in changed)
            conn.execute(
                f"UPDATE workflow.trade_row SET {assignments} WHERE tdcc = %s AND row_seq = %s",
                (*(row.values[name] for name in changed), trades.tdcc, row.seq),
            )
    return trades.edits()
