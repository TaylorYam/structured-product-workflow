"""起單資料讀取：從作業人員每天填的「起單建檔」讀出條件（#97，取代 #40 讀 Order 原信）。

對外是 `collect_orders`（讀選的那份起單建檔，依貼上的 TDCC 回傳逐檔結果）；`parse_field`／`format_field`
給畫面輸入與顯示用，和讀起單建檔是同一套解讀。起單建檔只讀、不修改。

起單建檔（`起單建檔_<日期>.xlsm`）只有一張工作表：表頭在前幾列（目前是第 2 列、從 B 欄開始），一檔一列。
欄位依表頭文字對（不分大小寫、不看位置）；畫面上可以改的欄是 `KINDS`，起單建檔其他的欄
（No.、Observation Frequency、OTC、Funding Spread、Effective Date Offset、起單FC）沒有對應的資料庫欄位，不讀。

規則式、不猜值：
- 格子的型別不對、選項不認得時那一格留空，`OrderRow.notes` 寫出讀到的值。
- 選項不分大小寫（`DAILY MEMORY` 就是 Daily Memory，Barrier Type 的 `None` 就是 NONE）。
- `UF (%)` 欄放的是價格（例：97.14；舊表頭叫 `Upfront / NotePrice (%)`），畫面上叫 Upfront / NotePrice (%)，
  寫進資料庫時才換成 UF = 100 − 價格（`order_import`）。
- 同一檔 TDCC 有好幾列：內容一樣就用；不一樣的欄留空並寫出各列的值（`OrderRow.conflicts`），由作業人員填好才能匯入。
- 上手依 TDCC 前三碼（上手編號對照 `config/issuer_prefixes.toml`）。
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from pathlib import Path

import openpyxl

from sp_workflow.preparation import is_tdcc, parse_tdccs

READ = "已讀取"
PENDING = "待確認"
NOT_FOUND = "找不到"

TDCC_FIELD = "TDCC"
UPFRONT_FIELD = "Upfront / NotePrice (%)"


class FieldKind(Enum):
    TEXT = "文字"
    CHOICE = "選項"
    PERCENT = "百分比"
    INT = "整數"
    DATE = "日期"


KO_TYPES = ("Daily", "Daily Memory", "Period End", "Period End Memory")
BARRIER_TYPES = ("NONE", "EKI", "AKI")
INITIAL_PRICINGS = ("開盤價", "收盤價", "VWAP")
CHOICES: dict[str, tuple[str, ...]] = {
    "KO Type": KO_TYPES,
    "Barrier Type": BARRIER_TYPES,
    "期初價格": INITIAL_PRICINGS,
}
# 畫面上可以改的欄（依起單建檔的順序）→ 型別
KINDS: dict[str, FieldKind] = {
    "Product": FieldKind.TEXT,
    "Currency": FieldKind.TEXT,
    "Guaranteed Periods (m)": FieldKind.INT,
    **{f"BBG Code {n}": FieldKind.TEXT for n in range(1, 6)},
    "Strike (%)": FieldKind.PERCENT,
    "KO Type": FieldKind.CHOICE,
    "KO Barrier (%)": FieldKind.PERCENT,
    "Coupon p.a. (%)": FieldKind.PERCENT,
    UPFRONT_FIELD: FieldKind.PERCENT,
    "Tenor (m)": FieldKind.INT,
    "Barrier Type": FieldKind.CHOICE,
    "KI Barrier (%)": FieldKind.PERCENT,
    "Trade date": FieldKind.DATE,
    "Issue date": FieldKind.DATE,
    "Final valuation date": FieldKind.DATE,
    "Maturity date": FieldKind.DATE,
    "期初價格": FieldKind.CHOICE,
}
FIELDS = tuple(KINDS)
# 起單建檔的表頭（不分大小寫）→ 欄位；UF (%) 是 2026 年起的表頭
_HEADERS = {name.casefold(): name for name in (TDCC_FIELD, *FIELDS)} | {"uf (%)": UPFRONT_FIELD}
_HEADER_ROWS = 5  # 表頭在前幾列找
_EMPTY = frozenset({"", "-"})
_MONTHS = {
    m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)
}


class OrderTermsError(Exception):
    """整批無法開始（沒有 TDCC、起單建檔打不開或格式不對）；訊息是給作業人員看的中文。"""


@dataclass(frozen=True)
class OrderRow:
    """一檔 TDCC 的讀取結果。`notes` 的整檔問題放在 TDCC；`lines` 是在起單建檔的第幾列（Excel 列號）。"""

    tdcc: str
    status: str  # 已讀取／待確認／找不到
    issuer: str = ""
    values: dict[str, object] = field(default_factory=dict)  # 欄位 → 值；空白或讀不到的是 None
    notes: dict[str, str] = field(default_factory=dict)  # 欄位 → 讀檔時的問題
    conflicts: frozenset[str] = frozenset()  # 好幾列不同、留空要由作業人員填的欄
    source: Path | None = None
    lines: tuple[int, ...] = ()

    @property
    def found(self) -> bool:
        return bool(self.lines)


def collect_orders(text: str, path: Path, issuer_prefixes: Mapping[str, str]) -> tuple[OrderRow, ...]:
    """讀起單建檔，依貼上的順序回傳每檔的結果（重複的 TDCC 只算一次）。

    issuer_prefixes 是上手編號對照（三碼 → 上手代號）。
    """
    tokens = parse_tdccs(text)
    if not tokens:
        raise OrderTermsError("沒有輸入 TDCC")
    found = read_sheet(path)
    return tuple(_row_for(token, found.get(token, []), path, issuer_prefixes) for token in tokens)


def read_sheet(path: Path) -> dict[str, list[tuple[int, dict[str, object]]]]:
    """起單建檔每列的原始格子：TDCC → [(Excel 列號, {欄位: 格子的值})]。格式不對時丟 `OrderTermsError`。"""
    if not path.is_file():
        raise OrderTermsError(f"起單建檔不存在：{path}")
    try:
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:  # openpyxl 對壞檔會丟各種錯誤
        raise OrderTermsError(f"起單建檔打不開：{path.name}（{exc}）") from exc
    try:
        if len(workbook.worksheets) != 1:
            raise OrderTermsError(
                f"{path.name} 有 {len(workbook.worksheets)} 張工作表；請選每天的起單建檔（只有一張工作表）"
            )
        rows = list(workbook.worksheets[0].iter_rows(values_only=True))
    finally:
        workbook.close()
    header_at, columns = _find_header(rows, path)
    found: dict[str, list[tuple[int, dict[str, object]]]] = {}
    for number, row in enumerate(rows[header_at + 1 :], header_at + 2):
        cells = {name: row[i] if i < len(row) else None for name, i in columns.items()}
        tdcc = cells.pop(TDCC_FIELD)
        if tdcc is None or str(tdcc).strip() == "":
            continue
        found.setdefault(str(tdcc).strip(), []).append((number, cells))
    return found


def _find_header(rows: Sequence[tuple[object, ...]], path: Path) -> tuple[int, dict[str, int]]:
    """前幾列裡有 TDCC 的那列是表頭；回傳 (列的位置, {欄位: 欄的位置})。少欄或同一欄出現兩次時丟錯。"""
    for at, row in enumerate(rows[:_HEADER_ROWS]):
        headers = [str(cell).strip().casefold() if cell is not None else "" for cell in row]
        if TDCC_FIELD.casefold() not in headers:
            continue
        columns: dict[str, int] = {}
        for i, header in enumerate(headers):
            name = _HEADERS.get(header)
            if name is None:
                continue
            if name in columns:
                raise OrderTermsError(f"{path.name} 的表頭有兩欄都是「{name}」")
            columns[name] = i
        missing = [name for name in (TDCC_FIELD, *FIELDS) if name not in columns]
        if missing:
            raise OrderTermsError(f"{path.name} 的表頭少了：{'、'.join(missing)}")
        return at, columns
    raise OrderTermsError(f"{path.name} 前 {_HEADER_ROWS} 列找不到表頭（TDCC）；請選每天的起單建檔")


def _row_for(
    tdcc: str, lines: list[tuple[int, dict[str, object]]], path: Path, issuer_prefixes: Mapping[str, str]
) -> OrderRow:
    if not is_tdcc(tdcc):
        return OrderRow(tdcc, PENDING, notes={TDCC_FIELD: "TDCC 要剛好 12 碼數字"})
    issuer = issuer_prefixes.get(tdcc[:3], "")
    if not lines:
        return OrderRow(tdcc, NOT_FOUND, issuer, notes={TDCC_FIELD: f"{path.name} 裡沒有這檔"}, source=path)
    numbers = tuple(number for number, _ in lines)
    parsed = [_parse_line(cells) for _, cells in lines]
    values: dict[str, object] = {}
    notes: dict[str, str] = {}
    conflicts: set[str] = set()
    for name in FIELDS:
        # 比解讀後的值（`DAILY MEMORY` 和 `Daily Memory` 一樣）；讀不到的比原本的字
        keys = {
            line[name][0] if not line[name][1] else ("", _shown(cells.get(name)))
            for line, (_, cells) in zip(parsed, lines, strict=True)
        }
        if len(keys) > 1:
            conflicts.add(name)
            values[name] = None
            shown = [_shown(cells.get(name)) for _, cells in lines]
            listed = "、".join(f"第 {n} 列「{s or '空白'}」" for n, s in zip(numbers, shown, strict=True))
            notes[name] = f"這檔有 {len(lines)} 列，這欄不同：{listed}；請填正確的值"
            continue
        values[name], note = parsed[0][name]
        if note:
            notes[name] = note
    if not issuer:
        notes[TDCC_FIELD] = unknown_issuer(tdcc)
    status = PENDING if notes else READ
    return OrderRow(tdcc, status, issuer, values, notes, frozenset(conflicts), path, numbers)


def unknown_issuer(tdcc: str) -> str:
    """TDCC 前三碼不在上手編號對照時的說明（讀取與匯入檢查共用）。"""
    return f"TDCC 前三碼 {tdcc[:3]} 不在上手編號對照"


def _parse_line(cells: Mapping[str, object]) -> dict[str, tuple[object, str]]:
    """一列的每一欄 → (值, 讀不到時的說明)。"""
    parsed: dict[str, tuple[object, str]] = {}
    for name in FIELDS:
        raw = cells.get(name)
        try:
            parsed[name] = (parse_field(name, raw), "")
        except ValueError as exc:
            parsed[name] = (None, f"讀到「{_shown(raw)}」，{exc}")
    return parsed


def _shown(raw: object) -> str:
    """格子的值寫成說明裡的字（日期不帶時間）。"""
    if raw is None:
        return ""
    if isinstance(raw, dt.datetime) and raw.time() == dt.time():
        return raw.date().isoformat()
    if isinstance(raw, float) and raw.is_integer():
        return str(int(raw))
    return str(raw).strip()


# ---------- 欄位的值 ----------


def parse_field(name: str, raw: object) -> object:
    """起單建檔的格子或畫面打的字 → 欄位的值；空白（含 `-`）回傳 None，不認得時丟 ValueError（中文說明）。"""
    kind = KINDS[name]
    if raw is None or isinstance(raw, str) and raw.strip() in _EMPTY:
        return None
    if isinstance(raw, bool):
        raise ValueError(f"要填{kind.value}")
    if kind is FieldKind.DATE:
        if isinstance(raw, dt.datetime):
            if raw.time() != dt.time():
                raise ValueError("日期不能有時間")
            return raw.date()
        if isinstance(raw, dt.date):
            return raw
        if isinstance(raw, str):
            try:
                return parse_date(raw.strip())
            except ValueError:
                pass
        raise ValueError("日期要寫成 2026-10-21")
    if kind is FieldKind.PERCENT:
        if isinstance(raw, int | float | Decimal):
            return plain(Decimal(str(raw)))
        cleaned = str(raw).replace(",", "").strip().removesuffix("%").strip()
        if not re.fullmatch(r"-?[0-9]+(\.[0-9]+)?", cleaned):
            raise ValueError("不是數字")
        return plain(Decimal(cleaned))
    if kind is FieldKind.INT:
        if isinstance(raw, int):
            return raw
        if isinstance(raw, float) and raw.is_integer():
            return int(raw)
        text = str(raw).strip()
        if not re.fullmatch(r"[0-9]+", text):
            raise ValueError("不是整數")
        return int(text)
    text = _shown(raw)
    if kind is FieldKind.CHOICE:
        for choice in CHOICES[name]:
            if choice.casefold() == text.casefold():
                return choice
        raise ValueError(f"要選 {'／'.join(CHOICES[name])}")
    return text


def format_field(value: object) -> str:
    """欄位的值 → 畫面顯示的文字。"""
    if value is None:
        return ""
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(plain(value), "f")
    return str(value)


def plain(value: Decimal) -> Decimal:
    """去掉多餘的 0，但不變成科學記號（例：80.000000 → 80、2.150000 → 2.15）。"""
    if value == value.to_integral_value():
        return value.quantize(Decimal(1))
    return value.normalize()


def parse_date(text: str) -> dt.date:
    """`2026-10-13`、`2026/10/13`、`15-Oct-26`、`20-Oct-2026`、`20Oct26`。"""
    m = re.fullmatch(r"([0-9]{4})[-/]([0-9]{1,2})[-/]([0-9]{1,2})", text)
    if m:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.fullmatch(r"([0-9]{1,2})[- ]?([A-Za-z]{3})[- ]?([0-9]{2}|[0-9]{4})", text)
    if m and m.group(2).lower() in _MONTHS:
        year = int(m.group(3))
        return dt.date(year + 2000 if year < 100 else year, _MONTHS[m.group(2).lower()], int(m.group(1)))
    raise ValueError(text)
