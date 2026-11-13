"""DACN（區間計息）說明書各上手共用的讀法：配息門檻 % 的兩種來源（各家範本規格 §4.1）。

- 計息比的是執行價（Nomura、SG）：配息門檻 = 執行價 %，`barrier_from_strike`。
- 計息比的是價格表的一欄（DBS 區間下限價格、Natixis 配息界限價格）：配息門檻 = 該欄欄頭 %，`barrier_from_head`。
  Barclays 說明書的配息門檻 % 取 §14 定義句（價格表欄頭由專屬規則比），投資人須知取價格表欄頭（`barrier_from_head`）。

各家只負責找到計息定義句（證據）與指出哪一欄；說明書內部規則共用的部分在 rules/dacn.py。
"""

from __future__ import annotations

from collections.abc import Sequence

from ..schema import Evidence, FieldStatus, Line, ParsedField
from .common import derived

FIELD = "coupon_barrier_pct"


def barrier_from_strike(definition: Sequence[Line], strike: ParsedField) -> ParsedField:
    """計息定義句比的是「執行價」→ 配息門檻 = 執行價 %；證據掛定義句與執行價的出處。"""
    pf = derived(FIELD, strike, strike.value)
    return ParsedField(FIELD, pf.status, pf.value, [*map(Evidence.of, definition), *pf.evidence], note=pf.note)


def barrier_from_head(
    table: ParsedField,
    column: str,
    no_pct: str,
    definition: Sequence[Line] = (),
    *,
    head_mark: str = "",
    no_mark: str = "",
) -> ParsedField:
    """配息門檻 = 價格表 `column` 欄的欄頭 %；證據掛定義句（有的話）與欄頭。價格表讀不到時沿用它的狀態；欄頭沒有 % 時
    讀不到（說明 `no_pct`）。`head_mark` 不空時，欄頭要有這段字（否則讀不到，說明 `no_mark`）。"""
    if not table.ok:
        return derived(FIELD, table)
    value = table.value
    lines = list(value.head_lines.get(column, ()))
    if head_mark and head_mark not in value.heads.get(column, ""):
        return ParsedField.invalid(FIELD, lines, no_mark)
    pct = value.pcts.get(column)
    if pct is None:
        return ParsedField.invalid(FIELD, lines, no_pct)
    return ParsedField(FIELD, FieldStatus.PRESENT, pct, [*map(Evidence.of, definition), *map(Evidence.of, lines)])
