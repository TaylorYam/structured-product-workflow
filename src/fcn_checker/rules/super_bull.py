"""Super Bull 各上手共用的規則（workflow #326）：固定寫法、出場紅利其他出處、觀察日月數與順序、獲利情境的報酬率與年化、
投資人須知出場紅利。各上手的型態範圍、要核對哪些出處與情境的算式留在各自的規則（bnp_super_bull、ca_super_bull）。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from decimal import Decimal

from ..parsers.common import Mention
from ..parsers.super_bull import ProfitReturn, Wording
from ..schema import CheckResult, DocKind, Item, ParsedField
from ..schema import CheckStatus as S
from . import kit
from .iis import NO_TERM_SHEET, IisIssuerContext, ts_unavailable
from .kit import Check, IssuerContext

AMOUNT_TOLERANCE = Decimal("0.01")  # 情境金額（CA 另含報酬率）容許的進位差（各上手核對規則 §3.7）


def close(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) <= AMOUNT_TOLERANCE


def fixed(rid: str, field: str, name: str, pf: ParsedField, document: DocKind) -> CheckResult:
    """固定寫法：讀到 → 通過；找不到或寫法不同 → 不一致。"""
    check = Check(rid, field, Item.expected(name), document)
    if pf.ok:
        return check.result(S.PASS, actual=pf.value, evidence=pf.evidence, message="範本固定寫法")
    return check.result(S.MISMATCH, evidence=pf.evidence, reason="value_mismatch", message=pf.note)


def exit_bonus_mentions(ctx: IssuerContext, mentions: Sequence[Mention], fail_message: str) -> list[CheckResult]:
    """出場紅利的其他出處，每處一筆：數值 = 正式值 `coupon_pa_pct`。"""
    main = ctx.ts.f("coupon_pa_pct")
    out = []
    for k, m in enumerate(mentions, start=1):
        pf = m.parsed(f"exit_bonus_{k}")
        out.append(
            kit.doc_check(ctx, "doc.exit_bonus", pf.name, f"出場紅利（{m.where}）", main, pf).compare(
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
    needs: Sequence[ParsedField],
    rows: Sequence[tuple[int, dt.date, dt.date]],
) -> CheckResult:
    """同列觀察日 < 出場日；觀察日逐列遞增；末列觀察日 < 期末日。`rows` 交出（列號, 觀察日, 出場日），`needs` 是要讀到的
    觀察日表與期末日（最後一個是期末日）。"""
    final = needs[-1]
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
        ctx, "schedule.observation_order", "observation_order", f"{table_name}{words.day}順序", list(needs), issues
    )


# ---------------------------------------------------------------- 獲利情境的報酬率（各上手核對規則 §3.7）


def profit_return_issues(
    r: ProfitReturn, amount: Decimal | None, denomination: Decimal, *, tolerance: Decimal
) -> tuple[Decimal | None, list[str]]:
    """報酬率算式「(A ÷ N) - 1 = r%」：A = 贖回金額算式（讀不到時不比）、N = 面額、r = A ÷ N − 1 四捨五入到文件位數
    （差在 `tolerance` 內算對）。回傳（應有的 r, 問題）。"""
    issues = []
    if amount is not None and r.amount != amount:
        issues.append(f"報酬率算式的贖回金額 {r.amount} 和贖回金額算式 {amount} 不同")
    if r.divisor != denomination:
        issues.append(f"報酬率算式的總投資金額 {r.divisor} 不等於第一章面額 {denomination}")
    expected = kit.shown(r.amount / r.divisor * 100 - 100, r.pct) if r.divisor else None
    if expected is None or abs(expected - r.pct) > tolerance:
        issues.append(f"報酬率 {r.pct}% 應為 ({r.amount} ÷ {r.divisor}) − 1 = {expected}%")
    return expected, issues


def annualized_issues(pct: Decimal, annualized: Decimal, months: int) -> tuple[Decimal, list[str]]:
    """年化報酬率 = 報酬率 × 12 ÷ months，四捨五入到文件位數。回傳（應有的年化, 問題）。"""
    expected = kit.shown(pct * 12 / months, annualized)
    if expected == annualized:
        return expected, []
    return expected, [f"年化報酬率 {annualized}% 應為 {pct}% × 12 ÷ {months} = {expected}%（四捨五入到文件位數）"]


# ---------------------------------------------------------------- 投資人須知（各上手核對規則 §3.8）


def iis_exit_bonus(ctx: IisIssuerContext, fail_message: str) -> CheckResult:
    """投資人須知的出場紅利 = 同商品說明書的出場紅利（`coupon_pa_pct`）。"""
    rid, name = "iis.exit_bonus", "出場紅利"
    pf = ctx.sheet.f("exit_bonus")
    check = Check(rid, "exit_bonus", Item.term_sheet(name), ctx.document).needs(pf)
    if (problem := check.blocked) is not None:
        return problem
    if ctx.term_sheet is None:
        return ts_unavailable(rid, "exit_bonus", name, NO_TERM_SHEET, pf.value, document=ctx.document)
    expected = ctx.term_sheet.f("coupon_pa_pct")
    if not expected.ok:
        why = "同商品說明書讀不到出場紅利，無法比對"
        return ts_unavailable(rid, "exit_bonus", name, why, pf.value, document=ctx.document)
    return check.compare(expected.value, pf.value, fail_message=fail_message)
