"""說明書 parser 的欄位累積器（Issue #487）：`put`、出處登記、收尾補「範本可無」欄位只寫一次；parser 只剩上手文字與版面知識。

用法：`fields = FieldsBuilder()` 當一般的欄位 dict 用（`fields["x"] = ...`），另外：

- `fields.put(name, lines, pattern, convert)`：在 `lines` 裡剛好找到一處 `pattern`（整句比對）就交出第 1 組。
- `fields.occur(field, where, code=..., value=...)`：登記一處出處；值預設是當下已交出的同名欄位，代號預設同欄位名。
  依 `standard_fields.OCCURRENCE_LISTS`（代號 → 出處清單）自動分組，同一清單照登記順序。
- `fields.add(*occurrences)`：登記 helper 組好的 `Occurrence`（例：`money.currency_words`），一樣依代號分組。
- `fields.build()`：收尾。把各清單包成出處清單欄位（`standard_fields.occurrences`），`standard_fields.MAY_BE_ABSENT`
  裡沒交出的欄位補 `absent`（說明用預設；說法不同的上手自己先交出 `absent`），其餘沒交出的仍由 `lookup` 回 `not_provided`。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .. import standard_fields
from ..schema import Line, ParsedField
from ..standard_fields import Occurrence
from .layout import TextIndex, capture


class FieldsBuilder(dict[str, ParsedField]):
    """一份說明書的欄位，邊讀邊累積；見 module 說明。"""

    def __init__(self, *, unify_brackets: bool = False) -> None:
        """`unify_brackets`：`put` 整句擷取前先把括號統一成全形（MS 條款句全形、半形混用）。"""
        super().__init__()
        self._lists: dict[str, list[Occurrence]] = {}
        self._unify_brackets = unify_brackets

    def put(self, name: str, lines: Sequence[Line], pattern: str, convert: Callable = lambda x: x) -> ParsedField:
        self[name] = capture(
            name, TextIndex(lines, unify_brackets=self._unify_brackets), pattern, convert, whole_match=True
        )
        return self[name]

    def occur(
        self, field: str, where: str, *, code: str | None = None, value: ParsedField | None = None, qualifier: str = ""
    ) -> None:
        self.add(Occurrence(field, code or field, where, self[field] if value is None else value, qualifier))

    def add(self, *occurrences: Occurrence) -> None:
        for occ in occurrences:
            self._lists.setdefault(standard_fields.occurrence_list(occ.code), []).append(occ)

    def build(self) -> dict[str, ParsedField]:
        out = dict(self)
        for name, items in self._lists.items():
            if name in out:  # 同一張清單不能一邊登記出處、一邊直接交出
                raise ValueError(f"出處清單 {name} 已直接交出，不能再登記出處")
            out[name] = standard_fields.occurrences(name, items)
        for name, what in standard_fields.MAY_BE_ABSENT.items():
            out.setdefault(name, standard_fields.absent(name, what))
        return out


def occurrence_of(fields: Mapping[str, ParsedField], name: str, where: str) -> Occurrence:
    """出處代號就是欄位名、值是同名欄位的一處（`FieldsBuilder.occur` 的同義寫法）：給回傳出處清單的 helper 用，
    例如 MS FCN 與 MS BEN 共用的幣別出處（BEN 先過濾再交出，不經過 `FieldsBuilder`）。"""
    return Occurrence(name, name, where, fields[name])


@dataclass
class ParsedTermSheet:
    """說明書 parser 交出的 `TermSheet`：欄位、全文索引、版面；各上手的子類別只加自己規則需要的專屬資料。"""

    fields: dict[str, ParsedField]
    full_text: TextIndex
    document: Any

    def f(self, name: str) -> ParsedField:
        return standard_fields.lookup(self.fields, name)
