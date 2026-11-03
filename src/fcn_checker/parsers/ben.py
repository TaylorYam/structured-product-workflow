"""BEN（Bonus Enhanced Notes）說明書各上手共用的讀法（HSBC、MS、CA）：沒有 KO 時交出的 KO 與排程欄位、紅利門檻。

各家只負責讀出自己的寫法（沒有 KO 的判定、到期公式、執行價），再交給這裡推得標準欄位；交股情境的算式型別在
parsers/delivery.py（HSBC 寫法）與 parsers/ca_super_bull.py（CA 寫法）。
"""

from __future__ import annotations

from .. import standard_fields
from ..schema import FieldStatus, ParsedField
from ..standard_fields import AutocallSchedule
from .common import derived


def maturity_only(fields: dict[str, ParsedField], no_ko: ParsedField) -> None:
    """沒有 KO（`no_ko` 讀得到）的 BEN 交出的 KO 與排程欄位：KO% 不適用、每期觀察、非記憶式、沒有日期表、Non-Call = 天期、
    比價日只有最後評價日（期末定價日）。"""
    fields["ko_pct"] = (
        ParsedField("ko_pct", FieldStatus.NOT_APPLICABLE, evidence=list(no_ko.evidence), note="BEN 沒有 KO")
        if no_ko.ok
        else derived("ko_pct", no_ko)
    )
    fields["ko_observation"] = derived("ko_observation", no_ko, "P")
    fields["ko_memory"] = derived("ko_memory", no_ko, False)
    fields["schedule_table"] = standard_fields.absent("schedule_table", "日期表（BEN 不會提前出場）")
    tenor, final = fields["tenor_months"], fields["final_valuation_date"]
    bad = next((p for p in (no_ko, tenor, final) if not p.ok), None)
    if bad is not None:
        fields["first_callable_period"] = derived("first_callable_period", bad)
        fields["autocall_schedule"] = derived("autocall_schedule", bad)
        return
    ev = [*no_ko.evidence, *tenor.evidence, *final.evidence]
    fields["first_callable_period"] = ParsedField("first_callable_period", FieldStatus.PRESENT, tenor.value, ev)
    fields["autocall_schedule"] = ParsedField(
        "autocall_schedule",
        FieldStatus.PRESENT,
        AutocallSchedule("P", tenor.value, tenor.value, {tenor.value: final.value}, listed=True),
        ev,
    )


def bonus_barrier(fields: dict[str, ParsedField]) -> None:
    """紅利門檻（標準欄位 `coupon_barrier_pct`，workflow #406）：到期公式是「最差表現標的 ≥ 執行價」就領紅利，紅利門檻 =
    執行價 %，由到期公式（`maturity_formula`）與執行價（`strike_pct`）推得。"""
    formula, strike = fields["maturity_formula"], fields["strike_pct"]
    bad = next((p for p in (formula, strike) if not p.ok), None)
    fields["coupon_barrier_pct"] = (
        derived("coupon_barrier_pct", bad)
        if bad is not None
        else ParsedField("coupon_barrier_pct", FieldStatus.PRESENT, strike.value, [*formula.evidence, *strike.evidence])
    )
