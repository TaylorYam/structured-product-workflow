"""KO% 每期固定往下降（FCN_Stepdown，ADR 0033、workflow #544）的共用讀法：各上手讀出每期 KO% 後交給這裡。

上手只交出上手知識：每期 KO%（表上的順序；不可提前出場的期別為 None）與各列證據。這裡推得標準欄位：

- `ko_levels`：每期 KO%，第一個可提前出場的期別是第 1 期（ADR 0033：第一個比價日是第 1 期）；
- `ko_pct`：第 1 期 KO%；
- `ko_step_pct`：相鄰兩期的差（每期相同且大於 0）；不是固定降幅時不合法，上手的範本辨識據此轉人工（範本不支援）；
- 各標的第 1 期 KO 價：說明書沒有 KO 價欄時 = 最初價格 × 第 1 期 KO%，四捨五入（half-up）到 4 位；
- 每日型（D）的提前出場排程：每一期期末日都列（比價時才知道每一期從哪天到哪天）。
- 每期 KO% 的期數須 = 提前出場排程第 k 期到末期的期數（`matching_periods`，workflow #545）；不符時轉人工。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from decimal import ROUND_HALF_UP, Decimal

from ..schema import Evidence, FieldStatus, Line, ParsedField
from ..standard_fields import AutocallSchedule, DateTable, KoLevel, PriceRow

Q4 = Decimal("0.0001")
KEYS = ("ko_levels", "ko_pct", "ko_step_pct")


def _bad(status: FieldStatus, evidence: Sequence[Evidence], note: str) -> dict[str, ParsedField]:
    return {key: ParsedField(key, status, None, list(evidence), note=note) for key in KEYS}


def ko_terms(rows: Sequence[tuple[Decimal | None, Sequence[Line]]], where: str) -> dict[str, ParsedField]:
    """表上每一列的 KO%（None 為不可提前出場的期別）→ `ko_levels`、`ko_pct`、`ko_step_pct`。

    不可提前出場的列只能在開頭；之後每一列都要有 KO%，至少兩期，且每期降的百分點相同、大於 0。不符時三個欄位都不合法，
    說明寫出哪裡不符（`where` 是表的位置，例：第 13 條 (7) 提前出場表）。
    """
    evidence = [Evidence.of(ln) for _, lines in rows for ln in lines]
    start = next((i for i, (pct, _) in enumerate(rows) if pct is not None), None)
    if start is None:
        return _bad(FieldStatus.MISSING, evidence, f"{where}沒有可提前出場的期別（每期 KO% 都讀不到）")
    gaps = [i + 1 for i, (pct, _) in enumerate(rows) if i > start and pct is None]
    if gaps:
        note = f"{where}可提前出場的期別之後，第 " + "、".join(map(str, gaps)) + " 列沒有 KO%"
        return _bad(FieldStatus.INVALID, evidence, note)
    levels = tuple(
        KoLevel(n, pct, tuple(Evidence.of(ln) for ln in lines))
        for n, (pct, lines) in enumerate(rows[start:], start=1)
        if pct is not None
    )
    first = levels[0]
    ko_pct = ParsedField("ko_pct", FieldStatus.PRESENT, first.pct, list(first.evidence))
    level_field = ParsedField("ko_levels", FieldStatus.PRESENT, levels, evidence)
    steps = [a.pct - b.pct for a, b in zip(levels, levels[1:], strict=False)]
    shown = "、".join(f"{lv.pct}%" for lv in levels)
    if not steps:
        note = f"{where}只有 1 期 KO%（{shown}），看不出每期降幅"
    elif len(set(steps)) != 1 or steps[0] <= 0:
        note = f"{where}的 KO% 不是每期固定往下降：{shown}"
    else:
        step = ParsedField("ko_step_pct", FieldStatus.PRESENT, steps[0], evidence)
        return {"ko_levels": level_field, "ko_pct": ko_pct, "ko_step_pct": step}
    return {"ko_levels": level_field, "ko_pct": ko_pct, "ko_step_pct": ParsedField.invalid("ko_step_pct", [], note)}


def matching_periods(terms: dict[str, ParsedField], schedule: ParsedField) -> dict[str, ParsedField]:
    """每期 KO% 的期數須 = 提前出場排程第 k 期到末期的期數（期數 − Non-Call + 1，ADR 0033 的 KO 期別）；不符時三個欄位
    都不合法（轉人工覆核），以免第 n 期 KO% 對錯比價日。KO% 或排程讀不到時原樣交回（各自已轉人工）。"""
    levels = terms["ko_levels"]
    if not (levels.ok and schedule.ok):
        return terms
    sched: AutocallSchedule = schedule.value
    expected = sched.periods - sched.first_callable + 1
    if len(levels.value) == expected:
        return terms
    note = (
        f"讀到 {len(levels.value)} 期 KO%，提前出場排程是第 {sched.first_callable}～{sched.periods} 期"
        f"（{expected} 期），期別對不上"
    )
    return _bad(FieldStatus.INVALID, levels.evidence, note)


def step_problem(fields: dict[str, ParsedField]) -> str:
    """`ko_terms` 的結果不是每期固定往下降時的說明（範本辨識用）；是固定降幅時空字串。"""
    pf = fields["ko_step_pct"]
    return "" if pf.ok else pf.note or "讀不到每期 KO%"


def with_ko_prices(prices: ParsedField, ko_pct: ParsedField) -> ParsedField:
    """說明書價格表沒有 KO 價欄：各標的 KO 價 = 最初價格 × 第 1 期 KO%，四捨五入（half-up）到 4 位（ADR 0033 資料庫存的也是
    第 1 期 KO 價；同共用核對規則 §8 沒有 KO 欄的做法）。價格表或第 1 期 KO% 讀不到時原樣交回（沒有 KO 價，由規則轉人工覆核）。"""
    if not prices.ok or not ko_pct.ok:
        return prices
    rows: tuple[PriceRow, ...] = tuple(
        replace(
            row,
            prices={**row.prices, "ko": (row.prices["initial"] * ko_pct.value / 100).quantize(Q4, ROUND_HALF_UP)},
            evidence=row.evidence + tuple(ko_pct.evidence),
        )
        for row in prices.value
    )
    return ParsedField(prices.name, FieldStatus.PRESENT, rows, list(prices.evidence), prices.candidates, prices.note)


def every_period(schedule: ParsedField, table: ParsedField) -> ParsedField:
    """每日型（D）的提前出場排程改成第 k 期到末期每一期都列期末日（ADR 0033：有 KO 降幅的每日型要填每一期的結束日），
    只填列出的期別（`AutocallSchedule.listed`）。每期型（P）原樣交回（已經是每一期）。

    沒有 Non-Call（k = 0）的每日型 Stepdown 沒有樣本、某一期沒有期末日 → 排程不合法（比價日轉人工覆核）。"""
    if not schedule.ok or schedule.value.observation != "D":
        return schedule
    if not table.ok:
        return ParsedField(schedule.name, table.status, None, list(table.evidence), note=table.note)
    sched: AutocallSchedule = schedule.value
    t: DateTable = table.value
    if sched.first_callable < 1:
        note = "沒有 Non-Call 的每日型 KO 每期往下降沒有樣本，比價日無法對應"
        return ParsedField(schedule.name, FieldStatus.INVALID, None, list(schedule.evidence), note=note)
    periods = range(sched.first_callable, sched.periods + 1)
    dates = {p: t.rows[p - 1].end for p in periods}
    blank = [p for p, d in dates.items() if d is None]
    if blank:
        note = f"{t.where}以下期別沒有期末日：" + "、".join(f"第 {p} 期" for p in blank)
        return ParsedField(schedule.name, FieldStatus.INVALID, None, list(schedule.evidence), note=note)
    # 末期仍用原排程的比價日（上手可能取最終評價日），其他期取期末日
    dates[sched.periods] = sched.dates[sched.periods]
    listed = replace(sched, dates={p: d for p, d in dates.items() if d is not None}, listed=True)
    return ParsedField(schedule.name, FieldStatus.PRESENT, listed, list(schedule.evidence))
