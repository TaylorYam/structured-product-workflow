"""Super Bull 各上手共用的讀法（workflow #326）：觀察日距發行日的月數、觀察日表 → 提前出場排程、
獲利情境的報酬率算式。各上手的範本寫法（句型、表格欄位、段落位置）留在各自的 parser（bnp_super_bull、ca_super_bull、hsbc_super_bull）。
"""

from __future__ import annotations

import calendar
import datetime as dt
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import NamedTuple

from .. import standard_fields
from ..schema import Evidence, FieldStatus, ParsedField
from ..standard_fields import AutocallSchedule
from . import schedule
from .common import derived


@dataclass(frozen=True)
class Wording:
    """觀察日表的用語（各上手範本不同，錯訊照範本的叫法）。"""

    issuer: str  # 上手代號（「BNP」「CA」「HSBC」）
    day: str  # 觀察日：「評價日」「觀察日」
    exit_day: str  # 出場日：「自動提前贖回日」「自動提前出場日」
    final_day: str  # 期末日：「期末評價日」「期末訂價日」


def add_months(d: dt.date, months: int) -> dt.date:
    """d 加 months 個月；該月沒有同一天時取月底。"""
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    return dt.date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def observation_month(issue: dt.date, observation: dt.date, tenor: int, *, early: int, late: int) -> int | None:
    """觀察日距發行日的月數 m（1 ≤ m < 天期）：觀察日在「發行日 + m 個月」前 `early` 到後 `late` 個日曆天內；對不到或多個
    時 None。"""
    found = [m for m in range(1, tenor) if -early <= (observation - add_months(issue, m)).days <= late]
    return found[0] if len(found) == 1 else None


def _window(early: int, late: int) -> str:
    """容許天數的說法（錯訊用）；`early` 只有 0（BNP）或等於 `late`（CA、HSBC）兩種。"""
    return f"當天到其後 {late} 個日曆天" if early == 0 else f"前後 {late} 個日曆天"


def fill_schedule(
    fields: dict[str, ParsedField],
    table: ParsedField,
    rows: Callable[[], Sequence[tuple[int, dt.date]]],
    *,
    table_name: str,
    words: Wording,
    early: int,
    late: int,
    needs: Sequence[ParsedField] = (),
    evidence: Sequence[Evidence] = (),
    problem: Callable[[], str] = lambda: "",
) -> None:
    """觀察日表只列觀察日：每列的月數 m、KO 觀察方式 P、Non-Call = 第 1 列的 m、提前出場排程（`比價日_m` = 觀察日、
    `比價日_{天期}` = 期末日）。共用日期表檢查不適用（`schedule_table` 交 absent）。

    `rows` 交出（列號, 觀察日）；`needs` 是觀察日表以外還要讀到的欄位，`evidence` 是排程另外引用的證據；`problem` 是上手
    自己的表格檢查（例：表頭），有問題時優先回報。月數對不到或不是遞增不重複 → 排程不合法。"""
    fields["schedule_table"] = standard_fields.absent(
        "schedule_table", f"每期一列的日期表（Super Bull 只列{words.day}，由 {words.issuer} Super Bull 規則核對）"
    )
    issue, tenor, final = fields["issue_date"], fields["tenor_months"], fields["final_valuation_date"]
    bad = next((p for p in (table, *needs, issue, tenor, final) if not p.ok), None)
    if bad is not None:
        fields.update(schedule.failed(bad))
        fields["observation_months"] = derived("observation_months", bad)
        return
    ev = [*table.evidence, *evidence]
    listed = list(rows())
    months = [observation_month(issue.value, day, tenor.value, early=early, late=late) for _, day in listed]
    note = problem()
    if not note and None in months:
        bad_rows = [str(n) for (n, _), m in zip(listed, months, strict=True) if m is None]
        note = (
            f"{table_name}第 {'、'.join(bad_rows)} 列的{words.day}對不到「發行日 + m 個月」（{_window(early, late)}、"
            "1 ≤ m < 天期）"
        )
    if not note and months != sorted(set(months)):
        note = f"{table_name}各列{words.day}的月數不是遞增且不重複：" + "、".join(map(str, months))
    if note:
        fields.update(schedule.invalid(ev, note))
        fields["observation_months"] = ParsedField("observation_months", FieldStatus.INVALID, evidence=ev, note=note)
        return
    dates = {m: day for m, (_, day) in zip(months, listed, strict=True)}
    dates[tenor.value] = final.value
    fields["observation_months"] = ParsedField(
        "observation_months", FieldStatus.PRESENT, tuple(months), list(table.evidence)
    )
    fields["ko_observation"] = ParsedField("ko_observation", FieldStatus.PRESENT, "P", ev)
    fields["first_callable_period"] = ParsedField("first_callable_period", FieldStatus.PRESENT, months[0], ev)
    fields["autocall_schedule"] = ParsedField(
        "autocall_schedule",
        FieldStatus.PRESENT,
        AutocallSchedule("P", months[0], tenor.value, dates, listed=True),
        ev,
    )


class ProfitReturn(NamedTuple):
    """獲利情境（出場、到期高於執行價）的報酬率算式「(A ÷ N) - 1 = r%（年化報酬率：y%）」。"""

    amount: Decimal
    divisor: Decimal
    pct: Decimal
    annualized: Decimal
