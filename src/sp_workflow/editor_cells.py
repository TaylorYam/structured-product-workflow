"""編輯區格子的規則（#127、ADR 0009、0017、0018）：起單資料、人工維護的商品條件與交易明細共用，不碰 Tk。

畫面（`app.OrderEditor`、`app.TradeRowsEditor`）只照這裡的結果套顏色、寫字。對外：
- `cell_states`：每格的底色（`Tone`）、說明、要不要提供「用資料庫的值」。優先序：擋存檔的問題 ＞ 和資料庫不同
  （有備註時維持紅色、兩個說明都寫）＞ 備註（讀起單建檔時的提醒、FC 名字對不到信箱）＞ 固定欄的說明。
  `order_cell_states`／`trade_cell_states` 是兩個編輯區各自的組法。
- `commit_order`／`commit_trade_rows`：畫面上打的字解讀進 draft（規則同 `order_terms.parse_field`／
  `daily_import.trade_value`），回傳有沒有改到。寫錯的格子保留打的字（`typed`）並記在 `input_errors`，值不動。
  `order_texts`／`trade_texts` 是解讀後每格該顯示的字。
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Hashable, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum, auto
from typing import Protocol, TypeVar

from sp_workflow.daily_import import trade_value
from sp_workflow.order_import import OrderDraft
from sp_workflow.order_terms import FIELDS, STRIKE_FIELD, format_field, parse_field
from sp_workflow.trade_rows import FIELDS as TRADE_FIELDS
from sp_workflow.trade_rows import TradeRowsDraft, trade_notes, trade_problems

K = TypeVar("K", bound=Hashable)
TradeKey = tuple[int, str]  # (row_seq, 欄)
_READING_PREFIX = "起單建檔："  # 讀起單建檔時的提醒寫在說明前面


class Tone(Enum):
    PLAIN = auto()  # 一般
    DIFFERENT = auto()  # 淺黃：和資料庫的值不同
    PROBLEM = auto()  # 淺紅：擋存檔，或有要注意的備註


@dataclass(frozen=True)
class CellState:
    tone: Tone = Tone.PLAIN
    note: str = ""
    offer_database: bool = False  # 顯示「用資料庫的值」


# ---------- 格子狀態 ----------


def cell_states(
    keys: Iterable[K],
    *,
    problems: Mapping[K, str],
    notes: Mapping[K, str],
    differences: Mapping[K, object],
    fixed: Collection[K] = (),
    fixed_note: str = "",
) -> dict[K, CellState]:
    """每格的狀態。`differences` 是和資料庫不同的格 → 資料庫的值（draft 的 `differences()`）。"""
    return {key: _state(key, problems, notes, differences, fixed_note if key in fixed else "") for key in keys}


def _state(
    key: K, problems: Mapping[K, str], notes: Mapping[K, str], differences: Mapping[K, object], plain_note: str
) -> CellState:
    if key in problems:
        return CellState(Tone.PROBLEM, problems[key])
    if key in differences:
        database = f"資料庫是 {format_field(differences[key]) or '空白'}"
        if key in notes:
            return CellState(Tone.PROBLEM, f"{notes[key]}；{database}", offer_database=True)
        return CellState(Tone.DIFFERENT, database, offer_database=True)
    if key in notes:
        return CellState(Tone.PROBLEM, notes[key])
    return CellState(Tone.PLAIN, plain_note)


def order_cell_states(
    draft: OrderDraft, problems: Mapping[str, str], *, fixed: Collection[str] = (), fixed_note: str = ""
) -> dict[str, CellState]:
    """商品條件每欄的狀態；`problems` 由呼叫端檢查（起單資料用匯入的檢查，人工維護用 `maintenance_problems`）。"""
    notes = {name: f"{_READING_PREFIX}{text}" for name, text in draft.notes.items()}
    return cell_states(
        FIELDS, problems=problems, notes=notes, differences=draft.differences(), fixed=fixed, fixed_note=fixed_note
    )


def trade_cell_states(trades: TradeRowsDraft) -> dict[TradeKey, CellState]:
    """交易明細每格的狀態：擋存檔看 `trade_problems`，提示看 `trade_notes`。"""
    keys = [(row.seq, name) for row in trades.rows for name in TRADE_FIELDS]
    return cell_states(
        keys, problems=trade_problems(trades), notes=trade_notes(trades), differences=trades.differences()
    )


# ---------- 打的字解讀進 draft ----------

_WRONG = object()  # 寫錯、沒解讀成值


def commit_order(
    draft: OrderDraft, typed: dict[str, str], texts: Mapping[str, str], names: Iterable[str] | None = None
) -> bool:
    """畫面上打的字（{欄: 字}，不能改的欄不放）解讀進 `draft`，回傳有沒有改到。

    `names` 是要解讀的欄（離開一欄時只解讀那一欄）；None 是 `texts` 的每一欄（換檔、存檔、關閉前）。
    Trade date 解讀後 Strike date 照 `OrderDraft.set_value` 跟著改；但 Strike date 打了還沒解讀、或寫錯的字
    （和目前的值不同）時不跟，以作業人員打的為準。
    """
    texts = dict(texts)
    strike = texts.get(STRIKE_FIELD)
    follow = strike is None or strike == format_field(draft.values.get(STRIKE_FIELD))
    wanted = set(texts if names is None else names)
    changed = False
    for name in FIELDS:
        if name not in wanted or name not in texts:
            continue
        cell_changed, value = _read(draft, name, texts[name], parse_field, typed, name)
        changed |= cell_changed
        if value is not _WRONG:
            for other in draft.set_value(name, value, follow=follow):  # 例：Strike date 跟著 Trade date
                texts[other] = format_field(draft.values.get(other))
    return changed


def order_texts(draft: OrderDraft, typed: Mapping[str, str]) -> dict[str, str]:
    """商品條件每欄該顯示的字：寫錯的欄是打的字，其他是目前的值。"""
    return {name: typed.get(name, format_field(draft.values.get(name))) for name in FIELDS}


def commit_trade_rows(trades: TradeRowsDraft, typed: dict[TradeKey, str], texts: Mapping[TradeKey, str]) -> bool:
    """畫面上打的字（{(row_seq, 欄): 字}）解讀進每列的 `values`，回傳有沒有改到。"""
    rows = {row.seq: row for row in trades.rows}
    changed = False
    for (seq, name), text in texts.items():
        row = rows[seq]
        cell_changed, value = _read(row, name, text, trade_value, typed, (seq, name))
        changed |= cell_changed
        if cell_changed and value is not _WRONG:
            row.values[name] = value
    return changed


def trade_texts(trades: TradeRowsDraft, typed: Mapping[TradeKey, str]) -> dict[TradeKey, str]:
    """交易明細每格該顯示的字：寫錯的格是打的字，其他是目前的值。"""
    return {
        (row.seq, name): typed.get((row.seq, name), format_field(row.values.get(name)))
        for row in trades.rows
        for name in TRADE_FIELDS
    }


class _Cells(Protocol):
    """解讀進去的地方：`OrderDraft` 或一列交易明細（`TradeRowDraft`）。"""

    values: dict[str, object]
    input_errors: dict[str, str]


def _read(
    cells: _Cells, name: str, text: str, parse: Callable[[str, str], object], typed: dict[K, str], key: K
) -> tuple[bool, object]:
    """解讀一格：(有沒有改到, 值；寫錯時 `_WRONG`)。寫錯的字和上次一樣不算改到；改對了就算值相同也算改到。"""
    try:
        value = parse(name, text)
    except ValueError as exc:
        changed = typed.get(key) != text
        typed[key] = text
        cells.input_errors[name] = str(exc)
        return changed, _WRONG
    changed = key in typed or format_field(cells.values.get(name)) != format_field(value)
    typed.pop(key, None)
    cells.input_errors.pop(name, None)
    return changed, value
