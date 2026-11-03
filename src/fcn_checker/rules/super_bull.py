"""Super Bull 各上手共用的規則（workflow #326）：固定寫法、出場紅利其他出處、觀察日月數與順序、獲利情境的報酬率與年化、
投資人須知出場紅利。各上手的型態範圍、要核對哪些出處與情境的算式留在各自的規則（bnp_super_bull、ca_super_bull、hsbc_super_bull）。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import NamedTuple

from ..parsers.common import Mention
from ..parsers.super_bull import ProfitReturn, Wording
from ..schema import CheckResult, DocKind, Item, ParsedField
from ..schema import CheckStatus as S
from . import kit
from .iis import NO_TERM_SHEET, IisIssuerContext, ts_unavailable
from .kit import Check, IssuerContext

AMOUNT_TOLERANCE = Decimal("0.01")  # 情境金額（CA 另含報酬率）容許的進位差（各上手核對規則 §3.7）
SCENARIO_RID = "doc.scenario_calculations"  # 情境算式重算（CA、HSBC）


def within_tolerance(a: Decimal, b: Decimal) -> bool:
    """兩個金額差在 `AMOUNT_TOLERANCE` 內。"""
    return abs(a - b) <= AMOUNT_TOLERANCE


def fixed(rid: str, field: str, name: str, pf: ParsedField, document: DocKind) -> CheckResult:
    """固定寫法：讀到 → 通過；找不到或寫法不同 → 不一致。"""
    check = Check(rid, field, Item.expected(name), document)
    if pf.ok:
        return check.result(S.PASS, actual=pf.value, evidence=pf.evidence, message="範本固定寫法")
    return check.result(S.MISMATCH, evidence=pf.evidence, reason="value_mismatch", message=pf.note)


def exit_bonus_mentions(
    ctx: IssuerContext,
    mentions: Sequence[Mention],
    fail_message: str,
    *,
    rid: str = "doc.exit_bonus",
    label: str = "出場紅利",
    key: str = "exit_bonus",
    main_field: str = "coupon_pa_pct",
) -> list[CheckResult]:
    """出場紅利的其他出處，每處一筆：數值 = 正式值 `main_field`（BEN 的到期紅利、CA BEN 與 MS BEN 的參與率同樣比，規則與名稱另給）。"""
    main = ctx.ts.f(main_field)
    out = []
    for k, m in enumerate(mentions, start=1):
        pf = m.parsed(f"{key}_{k}")
        out.append(
            kit.doc_check(ctx, rid, pf.name, f"{label}（{m.where}）", main, pf).compare(
                main.value, pf.value, fail_message=fail_message
            )
        )
    return out


def observation_months(ctx: IssuerContext, table_name: str, words: Wording) -> CheckResult:
    """觀察日的月數讀得到（月數與 Non-Call 由 parser 依觀察日表推得；對不到 → 轉人工覆核）。"""
    pf = ctx.ts.f("observation_months")
    check = kit.doc_check(ctx, "schedule.observation_months", "observation_months", f"{table_name}{words.day}月數", pf)
    months = "、".join(map(str, pf.value)) if pf.ok else ""
    return check.compare(
        None, None, ok=True, message=f"{words.day}在第 {months} 個月；Non-Call = {months.split('、')[0]}"
    )


def observation_order(
    ctx: IssuerContext,
    table_name: str,
    words: Wording,
    table: ParsedField,
    final: ParsedField,
    rows: Sequence[tuple[int, dt.date, dt.date]],
) -> CheckResult:
    """同列觀察日 < 出場日；觀察日逐列遞增；末列觀察日 < 期末日 `final`。`rows` 交出觀察日表 `table` 的（列號, 觀察日,
    出場日）。"""
    issues = []
    for n, day, exit_day in rows:
        if not day < exit_day:
            issues.append(f"第 {n} 列{words.exit_day} {exit_day} 沒有晚於{words.day} {day}")
    for (m, a, _), (n, b, _) in zip(rows, rows[1:], strict=False):
        if not a < b:
            issues.append(f"第 {n} 列{words.day} {b} 沒有晚於第 {m} 列 {a}")
    if rows and final.ok and not rows[-1][1] < final.value:
        issues.append(f"末列{words.day} {rows[-1][1]} 沒有早於{words.final_day} {final.value}")
    return kit.structure_check(
        ctx, "schedule.observation_order", "observation_order", f"{table_name}{words.day}順序", [table, final], issues
    )


# ---------------------------------------------------------------- 獲利情境的報酬率（各上手核對規則 §3.7）


class ReturnMessages(NamedTuple):
    """報酬率與年化的錯訊，各上手照原本的寫法（`str.format` 代入的名稱見各欄）。"""

    amount: str  # 報酬率算式的贖回金額和贖回金額算式不同：{got}、{want}
    divisor: str  # 報酬率算式的除數不等於第一章面額：{got}、{denomination}
    pct: str  # 報酬率算錯：{pct}、{amount}、{divisor}、{expected}
    annualized: str  # 年化算錯：{annualized}、{pct}、{months}、{expected}


def profit_return_issues(
    r: ProfitReturn, amount: Decimal | None, denomination: Decimal, messages: ReturnMessages, *, tolerance: Decimal
) -> tuple[Decimal | None, list[str]]:
    """報酬率算式「(A ÷ N) - 1 = r%」：A = 贖回金額算式（讀不到時不比）、N = 面額、r = A ÷ N − 1 四捨五入到文件位數
    （差在 `tolerance` 內算對）。回傳（應有的 r, 問題）。"""
    issues = []
    if amount is not None and r.amount != amount:
        issues.append(messages.amount.format(got=r.amount, want=amount))
    if r.divisor != denomination:
        issues.append(messages.divisor.format(got=r.divisor, denomination=denomination))
    expected = kit.shown(r.amount / r.divisor * 100 - 100, r.pct) if r.divisor else None
    if expected is None or abs(expected - r.pct) > tolerance:
        issues.append(messages.pct.format(pct=r.pct, amount=r.amount, divisor=r.divisor, expected=expected))
    return expected, issues


def annualized_issues(
    pct: Decimal, annualized: Decimal, months: int, messages: ReturnMessages
) -> tuple[Decimal, list[str]]:
    """年化報酬率 = 報酬率 × 12 ÷ months，四捨五入到文件位數。回傳（應有的年化, 問題）。"""
    expected = kit.shown(pct * 12 / months, annualized)
    if expected == annualized:
        return expected, []
    return expected, [messages.annualized.format(annualized=annualized, pct=pct, months=months, expected=expected)]


# ---------------------------------------------------------------- 損益寫法（HSBC Super Bull、HSBC BEN、MS BEN 交股情境）


CENT = Decimal("0.01")


def cents(x: Decimal) -> Decimal:
    """四捨五入（half-up）到 2 位。"""
    return x.quantize(CENT, ROUND_HALF_UP)


def pnl_check(c: Scenario, hit, amount: Decimal | None, n: Decimal) -> CheckResult:
    """損益「= A - N = 損益」：A = 前面算出的金額、N = 面額、損益 = A − N。"""
    if hit is None:
        return c.unread("pnl", "找不到「… - 每單位面額期初投資金額 = A - N = 損益」")
    p = hit.value
    issues = []
    if amount is not None and p.amount != amount:
        issues.append(f"損益算式的金額 {p.amount} 和前面算出的 {amount} 不同")
    if p.investment != n:
        issues.append(f"損益算式的期初投資金額 {p.investment} 不等於第一章面額 {n}")
    expected = p.amount - p.investment
    if not within_tolerance(p.pnl, expected):
        issues.append(f"損益 {p.pnl} 應為 {p.amount} − {p.investment} = {expected}")
    return c.calc("pnl", issues, hit, expected=expected, actual=p.pnl)


def annualized_of(c: Scenario, hit, pct: Decimal, months: int, how: str) -> CheckResult:
    """年化報酬率 = pct × 12 ÷ months，四捨五入到 2 位後比數值（容許 ±0.01）。"""
    if hit is None:
        return c.unread("annualized", "找不到「平均年化報酬率(以簡單平均年化報酬率之方式計算): y%」")
    expected = cents(pct * 12 / months)
    issues = [] if within_tolerance(hit.value, expected) else [f"年化報酬率 {hit.value}% 應為 {how} = {expected}%"]
    return c.calc("annualized", issues, hit, expected=expected, actual=hit.value)


def _profit(sc_pnl, n: Decimal) -> Decimal | None:
    return sc_pnl.value.pnl / n * 100 if sc_pnl is not None else None


def annualized_of_pnl(c: Scenario, hit, pnl, n: Decimal, tenor: int, *, round_return: bool = False) -> CheckResult:
    """年化 = 損益 ÷ 面額 × 12 ÷ 天期；`round_return`：報酬率先四捨五入到 2 位再年化（MS BEN 的寫法，#376）。"""
    profit = _profit(pnl, n)
    if profit is None:
        return c.unread("annualized", "找不到損益算式，無法重算年化報酬率")
    if round_return:
        return annualized_of(c, hit, cents(profit), tenor, f"({pnl.value.pnl} ÷ {n} 四捨五入到 2 位) × 12 ÷ {tenor}")
    return annualized_of(c, hit, profit, tenor, f"{pnl.value.pnl} ÷ {n} × 12 ÷ {tenor}")


# ---------------------------------------------------------------- 投資人須知（各上手核對規則 §3.8）


def iis_exit_bonus(
    ctx: IisIssuerContext,
    fail_message: str,
    *,
    rid: str = "iis.exit_bonus",
    name: str = "出場紅利",
    field: str = "exit_bonus",
    ts_field: str = "coupon_pa_pct",
) -> CheckResult:
    """投資人須知的出場紅利 `field` = 同商品說明書的出場紅利 `ts_field`（CA BEN 的紅利、參與率同樣比，規則與名稱另給）。"""
    pf = ctx.sheet.f(field)
    check = Check(rid, field, Item.term_sheet(name), ctx.document).needs(pf)
    if (problem := check.blocked) is not None:
        return problem
    if ctx.term_sheet is None:
        return ts_unavailable(rid, field, name, NO_TERM_SHEET, pf.value, document=ctx.document)
    expected = ctx.term_sheet.f(ts_field)
    if not expected.ok:
        why = f"同商品說明書讀不到{name}，無法比對"
        return ts_unavailable(rid, field, name, why, pf.value, document=ctx.document)
    return check.compare(expected.value, pf.value, fail_message=fail_message)


class Scenario:
    """情境算式重算（`SCENARIO_RID`）的一個情境：欄位「情境X_<項目>」、項目名稱「情境X<項目名稱>」（`names` 由上手給），
    找不到算式時證據為情境標題行。"""

    def __init__(self, n: int, title, names: Mapping[str, str]) -> None:
        self.tag = f"情境{'一二三'[n - 1]}"
        self.title = title
        self.names = names

    def item(self, key: str) -> Item:
        return Item.expected(f"{self.tag}{self.names[key]}")

    def unread(self, key: str, why: str) -> CheckResult:
        return kit.calc_result(
            SCENARIO_RID, f"{self.tag}_{key}", self.item(key), [why], kit.evidence(self.title), review=True
        )

    def calc(self, key: str, issues: list[str], hit, *, expected=None, actual=None) -> CheckResult:
        return kit.calc_result(
            SCENARIO_RID,
            f"{self.tag}_{key}",
            self.item(key),
            issues,
            kit.evidence(hit.lines),
            expected=expected,
            actual=actual,
        )
