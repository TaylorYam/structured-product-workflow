"""說明書「連結標的之相關說明」：每檔標的的公司介紹（Issue #225）。

段落從標題（`連結標的之相關說明`／`連結標的資產之相關說明`／GS `連結標的資產之連結標的股份發行人之相關說明`）的下一行起，
到下一條（`連結標的調整之條件…`、單獨一行的條號 `12.`）、資料來源與免責說明（`(資料來源：…)`、`來源:…`、
`投資人請注意，以上提供…`、`本產品說明書包含之某些資訊…`）或名詞定義（Nomura `「中斷日」係指…`）為止。

各上手切成一檔一段的方法不同（`NoteStyle`，上手 parser 交出 `underlying_notes` 時指定）：

- `Markers`：每段以行首的編號開始（BARC `標的資產k：`、Citi `連結標的k：`、DBS `k.`、MS／Nomura `(k)連結標的k：`、
  BNP／SG 行首 `(k)`、BBVA 單獨一行 `k`）；第一個編號之前的行（表頭）不算。
- `NameColumn`：兩欄表，左欄標的名稱、右欄介紹（CA、UBS、JPM、Natixis、GS）。左欄有中文的行開始一段（名稱跨行時連續的
  左欄行、以括號開頭的左欄行（GS `（美國航空集團）`，可能隔著換頁）算同一段）；後面沒有右欄介紹就遇到下一段的左欄行
  不算（表頭 `連結標的`／`簡介`）。右欄 = 段落裡最常見的長行左緣。
- `Paragraphs`：沒有編號也沒有名稱欄（HSBC）：介紹欄的行依段落切，句號結尾、右緣明顯短於欄寬的行是一段的最後一行。

只切段、不判斷內容；每段有沒有中文敘述、像不像亂碼由共用規則判斷（rules/underlying_notes.py）。找不到標題時交出缺漏。
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from ..schema import Evidence, Line, ParsedField
from ..standard_fields import UnderlyingNote
from ..text import CJK, squash

HEADING = re.compile(r"^(?:\d+[.、]|[一二三四五六七八九十]+、)?連結標的(?:資產)?之(?:連結標的股份發行人之)?相關說明")
END = re.compile(
    r"調整(?:及更正)?之條件|^\d+\.$|^「[^」]{1,12}」係指|^[（(]?(?:資料)?來源[:：]"
    r"|^投資人請注意，以上提供|^本產品說明書包含之某些資訊"
)
_LEFT_GAP = 15.0  # 名稱欄：左緣比介紹欄左 15pt 以上
_SHORT = 15.0  # 段落最後一行：右緣比欄寬短 15pt 以上


@dataclass(frozen=True)
class Markers:
    pattern: str  # 行首（去空白後）比對的段落編號


@dataclass(frozen=True)
class NameColumn:
    pass


@dataclass(frozen=True)
class Paragraphs:
    pass


NoteStyle = Markers | NameColumn | Paragraphs


def section(lines: Sequence[Line]) -> list[Line] | None:
    """標題下一行起到段落結束的行；找不到標題時為 None。"""
    start = next((i for i, ln in enumerate(lines) if HEADING.search(squash(ln.text))), None)
    if start is None:
        return None
    out = []
    for ln in lines[start + 1 :]:
        if END.search(squash(ln.text)):
            break
        out.append(ln)
    return out


def underlying_notes(lines: Sequence[Line], style: NoteStyle) -> ParsedField:
    """`underlying_notes`：tuple[UnderlyingNote, ...]，依說明書順序。"""
    name = "underlying_notes"
    body = section(lines)
    if body is None:
        return ParsedField.missing(name, "找不到「連結標的之相關說明」")
    match style:
        case Markers(pattern):
            groups = _by_markers(body, re.compile(pattern))
        case NameColumn():
            groups = _by_name_column(body)
        case Paragraphs():
            groups = _by_paragraphs(body)
    notes = tuple(UnderlyingNote("".join(squash(ln.text) for ln in g), tuple(map(Evidence.of, g))) for g in groups)
    return ParsedField.present(name, notes, [ln for g in groups for ln in g[:1]] or body[:1])


def _by_markers(body: Sequence[Line], marker: re.Pattern[str]) -> list[list[Line]]:
    groups: list[list[Line]] = []
    for ln in body:
        if marker.match(squash(ln.text)):
            groups.append([ln])
        elif groups:
            groups[-1].append(ln)
    return groups


def _note_column(body: Sequence[Line]) -> float | None:
    """介紹欄的左緣：長行（15 字以上）最常見的左緣（取整數）。"""
    xs = Counter(round(ln.x0) for ln in body if len(squash(ln.text)) >= 15)
    return xs.most_common(1)[0][0] if xs else None


def _by_name_column(body: Sequence[Line]) -> list[list[Line]]:
    col = _note_column(body)
    if col is None:
        return []
    groups: list[list[Line]] = []
    has_note, prev_left = False, False
    for ln in body:
        left = ln.x0 < col - _LEFT_GAP
        if left and not prev_left and CJK.search(ln.text) and not squash(ln.text).startswith(("(", "（")):
            if groups and not has_note:
                groups.pop()  # 上一段沒有介紹：表頭
            groups.append([ln])
            has_note = False
        elif groups:
            groups[-1].append(ln)
            has_note = has_note or abs(ln.x0 - col) <= _LEFT_GAP
        prev_left = left
    if groups and not has_note:
        groups.pop()
    return groups


def _by_paragraphs(body: Sequence[Line]) -> list[list[Line]]:
    col = _note_column(body)
    if col is None:
        return []
    note_lines = [ln for ln in body if abs(ln.x0 - col) <= 3]
    right = max(ln.x1 for ln in note_lines)
    groups: list[list[Line]] = [[]]
    for ln in note_lines:
        groups[-1].append(ln)
        text = squash(ln.text)
        if text.endswith(("。", ")", "）")) and ln.x1 < right - _SHORT:
            groups.append([])
    return [g for g in groups if g]
