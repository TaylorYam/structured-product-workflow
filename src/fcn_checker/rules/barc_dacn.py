"""BARC DACN 專屬規則（docs/checker/rules/barc-dacn-check-rules.md）：沒有樣本的型態、配息公式保證期數 = Non-Call、§15 配息
門檻欄（欄頭 % 與每列價格）、§14 配息觀察期表期始日；其他說明書內部規則照 BARC FCN（rules/barc.py：月配息率推算與各出處、
提前出場表、名稱與幣別一致、價格表欄頭、§9(3) 相關配息率、有利情況總報酬率、§13(7) 定義句），範本沒有年利率；§16 一般、最差
情況的配息、累積配息、最終現金交割金額與總報酬率用文件寫的 (n/N) 驗算（核對規則 §3.9，workflow #577），n、N 本身與年化不核對。

配息門檻 %（`field.coupon_barrier_pct`）、情境假設與情境試算的月配息率（`scenario.params`）、重印價格表（`scenario.reprint`，
和去掉配息門檻欄的 §15 表比）、審查標準（名稱樣板 `product_names.DACN` 和 FCN 同一句）與回填是各上手共用的規則。
抓不到、歧義或寫法不在範本規格內一律轉人工覆核。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from ..parsers.barc_dacn import Coupon, ScenarioCalc
from ..parsers.barc_schedule import ScheduleRow
from ..schema import CheckResult, Evidence
from ..schema import CheckStatus as S
from . import barc, dacn, kit
from .kit import IssuerContext

ISSUER = barc.ISSUER
REFERENCE_FIELDS = barc.REFERENCE_FIELDS
NOT_COVERED = [
    *(x for x in barc.NOT_COVERED if x["rule_id"] not in ("field.monthly_ki", "doc.scenario_other_returns")),
    {
        "rule_id": "doc.accrual_calculations",
        "description": "區間計息的 nt、Nt、各期配息率與配息金額不核對（只核對第 14 條配息公式與定義的固定寫法、月配息率、"
        "配息門檻與保證期數；ADR 0030）；第 14 條配息觀察期表的 Nt 欄、第 9 條(3) dt、Dt 與中斷日說明不解讀",
    },
    {
        "rule_id": "doc.scenario_calculations",
        "description": "第 16 條一般、最差情況 (n/N) 寫的 n、N 本身不核對（每期配息、累積配息、一般情況最終現金交割金額與"
        "總報酬率用文件寫的 n、N 驗算）；三個情境的平均年化報酬率不核對（依假設算出，不是年利率）",
    },
    {
        "rule_id": "doc.scenario_other_returns",
        "description": "最差情況的交付股數、畸零股數、剩餘金額與每單位最終實物贖回權益等值本身（取決於假設最終價格；總報酬率算式"
        "裡的等值要等於該段寫的等值）",
    },
    {
        "rule_id": "doc.template_variants",
        "description": "依規則推得、待樣本驗證（轉人工覆核）：美元以外的幣別、單一標的、KO 每期觀察或非記憶式、KI 每日觀察、"
        "KI 每期觀察（MKI，維持人工，workflow #386）、配息公式保證期數或 Non-Call 不是 1；寫法和現行版不同的範本不支援"
        "（不看交易日早晚）",
    },
]
SAMPLED = dacn.Sampled(
    "BARC DACN 樣本只有美元計價、多標的、KO 每日觀察＋記憶式、KI 無或到期觀察、配息公式保證 1 期、Non-Call 1",
    (
        dacn.currency(("USD",)),
        dacn.baskets,
        dacn.ko("ko_observation"),
        dacn.ki(("none", "AM")),
        dacn.guaranteed((1,), field="coupon_guaranteed_periods"),
        dacn.non_call((1,)),
    ),
)
FAVOURABLE = (barc.SCENARIOS[0],)  # 一般情況改由 scenario_calculations 用文件寫的 n/N 驗算（核對規則 §3.9）
CALC_RID = "doc.scenario_calculations"
# 一般、最差情況（讀出欄位, 結果欄位前綴, 情境名稱, 總報酬率算式的另一個加數）
CALC_CASES = (
    ("scenario_general_calc", "general", "一般情況", "每單位最終現金交割金額"),
    ("scenario_worst_calc", "worst", "最差情況", "每單位最終實物贖回權益等值"),
)


def coupon_barrier(ctx: IssuerContext) -> list[CheckResult]:
    """§15 價格表配息門檻欄：欄頭 % = §14 配息門檻定義句；每列配息門檻價格 = 最初價格 × %，四捨五入（half-up）到 4 位
    （核對規則 §3.4）。"""
    f = ctx.ts.f
    pct, table, rows, uls = f("coupon_barrier_pct"), f("price_table"), f("underlying_prices"), f("underlyings")
    rid = "doc.coupon_barrier"
    head = kit.doc_check(ctx, rid, "coupon_barrier_head", "第 15 條價格表配息門檻欄頭 %", pct, table)
    if (problem := head.blocked) is not None:
        return [problem]
    lines = table.value.head_lines.get("barrier", ())
    shown = table.value.pcts.get("barrier")
    out = [
        head.compare(
            pct.value,
            shown,
            fail=S.MISMATCH if shown is not None else S.REVIEW_REQUIRED,
            reason="document_inconsistent" if shown is not None else "document_missing",
            evidence=[*pct.evidence, *map(Evidence.of, lines)],
            fail_message="須等於第 14 條「配息門檻」定義句的 %" if shown is not None else "欄頭讀不到 %",
        )
    ]
    whole = kit.doc_check(ctx, rid, "coupon_barrier_prices", "第 15 條價格表配息門檻價格", pct, rows)
    if (problem := whole.blocked) is not None:
        return [*out, problem]
    for i, row in enumerate(rows.value, start=1):
        label = uls.value[i - 1] if uls.ok and i <= len(uls.value) else f"第 {i} 檔標的"
        check = kit.doc_check(ctx, rid, f"{label} 配息門檻價格", f"UL_{i} 配息門檻價格", pct)
        out.append(dacn.barrier_price(check, row, pct, f"第 15 條價格表第 {i} 列讀不到配息門檻價格", "最初價格"))
    return out


def observation_starts(ctx: IssuerContext) -> CheckResult:
    """§14 配息觀察期表每期期始日 = §13(7) 自動提前出場觀察期表同期期始日（第 1 期都是 N/A；期末日由 C3 核對，核對規則
    §3.6）。"""
    f = ctx.ts.f
    coupon, ko = f("coupon_table"), f("ko_table")
    check = kit.doc_check(ctx, "schedule.accrual_starts", "coupon_table", "第 14 條配息觀察期表期始日", coupon, ko)
    if (problem := check.blocked) is not None:
        return problem
    ct, kt = coupon.value.rows, ko.value.rows
    if len(ct) != len(kt):
        return check.compare(len(kt), len(ct), message="第 14 條配息觀察期表和第 13 條自動提前出場觀察期表期數不同")
    bad: list[ScheduleRow] = [c for c, k in zip(ct, kt, strict=True) if c.get("start") != k.get("start")]
    return check.compare(
        None,
        "、".join(f"第 {r.t} 期" for r in bad) or None,
        ok=not bad,
        evidence=[Evidence.of(ln) for r in bad for ln in r.lines],
        reason="document_inconsistent",
        message="每期期始日須等於第 13 條自動提前出場觀察期表同期期始日",
    )


@dataclass(frozen=True)
class _Case:
    """一個情境（一般或最差）的驗算：`g` = 配息公式保證期數、`tenor` = §14 配息表期數。"""

    calc: ScenarioCalc
    key: str  # 欄位前綴 general／worst
    label: str
    final_label: str
    denom: int
    monthly: Decimal
    g: int
    tenor: int

    def result(self, part: str, name: str, issues: list[str], hit, *, review: bool = False, **values) -> CheckResult:
        lines = () if hit is None else hit.lines
        return kit.calc_result(
            CALC_RID,
            f"{self.key}_{part}",
            f"{self.label}{name}",
            issues,
            kit.evidence(lines or self.calc.title),
            review=review,
            **values,
        )

    def unread(self, part: str, name: str, why: str) -> CheckResult:
        return self.result(part, name, [f"{self.label}{why}"], None, review=True)


def _step(decimals: int) -> Decimal:
    """「四捨五入至小數點後第k位」的進位單位。"""
    return Decimal(1).scaleb(-decimals)


def _periods(first: int, last: int) -> str:
    return f"第{first}個" if first == last else f"第{first}到{last}個"


def _coupon_issues(c: _Case, calc: Coupon, want: tuple[int, int], span: str) -> list[str]:
    issues = []
    if (calc.first, calc.last) != want:
        issues.append(f"配息期別寫「{_periods(calc.first, calc.last)}配息支付日」，應為「{_periods(*want)}」（{span}）")
    if calc.denomination != c.denom:
        issues.append(f"算式中的面額 {calc.denomination} 不等於第 6 條面額 {c.denom}")
    return issues


def _first_coupon(c: _Case) -> CheckResult:
    """第 1～g 期配息 = 面額 × 月配息率（第 14 條），依寫的位數四捨五入。"""
    name = "第 1～g 期每單位配息金額"
    if (hit := c.calc.coupon) is None:
        return c.unread("coupon", name, "找不到第 1～g 期配息算式（面額 × X%，四捨五入），或寫法不同、出現多次")
    calc = hit.value
    expected = (Decimal(c.denom) * c.monthly / 100).quantize(_step(calc.decimals), ROUND_HALF_UP)
    issues = _coupon_issues(c, calc, (1, c.g), "1～保證期數")
    if calc.amount != expected:
        issues.append(
            f"每單位配息金額 {calc.amount} 應為 {c.denom} × {c.monthly}% = {expected}"
            f"（四捨五入到小數點以下 {calc.decimals} 位）"
        )
    return c.result("coupon", name, issues, hit, expected=expected, actual=calc.amount)


def _accrual(c: _Case) -> CheckResult:
    """第 g+1 期起配息 = 面額 × 月配息率 × n ÷ N（文件寫的 n、N），依寫的位數四捨五入；N = 0 算不出，轉人工覆核。"""
    name = "第 g+1 期起每單位配息金額"
    if (hit := c.calc.accrual) is None:
        return c.unread(
            "accrual", name, "找不到第 g+1 期起的區間計息配息算式（× X% × (n/N)，四捨五入），或寫法不同、出現多次"
        )
    a = hit.value
    if not a.total_days:
        why = f"{c.label}算式 ({a.days}/{a.total_days}) 的 N 是 0，算不出"
        return c.result("accrual", name, [why], hit, review=True)
    step = _step(a.decimals)
    expected = dacn.accrual_coupon(Decimal(c.denom), c.monthly, a.days, a.total_days, step)
    issues = _coupon_issues(c, a, (c.g + 1, c.tenor), "保證期數 + 1～天期")
    issues += dacn.accrual_coupon_issues(
        "每單位配息金額", a.amount, Decimal(c.denom), c.monthly, a.days, a.total_days, step
    )
    return c.result("accrual", name, issues, hit, expected=expected, actual=a.amount)


def _cumulative(c: _Case) -> CheckResult:
    """每單位累積配息金額 = A × g + B × (天期 − g)（A、B 為文件寫的已進位每期配息）；算式兩段的金額 = A、B，期數 = g、
    天期 − g。第二段沒寫期數只在金額是 0 時接受（最差情況樣本），其他沒寫期數的寫法轉人工覆核。"""
    name = "每單位累積配息金額"
    calc = c.calc
    if (hit := calc.cumulative) is None or calc.coupon is None or calc.accrual is None:
        return c.unread("cumulative", name, "找不到每單位累積配息金額或每期配息算式，或出現多次")
    legs = hit.value.legs
    if len(legs) != 2 or legs[0].periods is None or (legs[1].periods is None and legs[1].amount != 0):
        why = f"{c.label}累積配息算式的寫法無法辨識（須為「A × 期數 + B × 期數」，只有金額是 0 的一段可不寫期數）"
        return c.result("cumulative", name, [why], hit, review=True)
    want = (
        dacn.CouponLeg(calc.coupon.value.amount, c.g),
        dacn.CouponLeg(calc.accrual.value.amount, c.tenor - c.g),
    )
    issues = []
    for i, (leg, w) in enumerate(zip(legs, want, strict=True), start=1):
        if leg.amount != w.amount:
            issues.append(f"累積算式第 {i} 段金額 {leg.amount} 應為每期配息算式寫的 {w.amount}")
        if leg.periods is not None and leg.periods != w.periods:
            issues.append(f"累積算式第 {i} 段期數 {leg.periods} 應為 {w.periods}")
    total = hit.value.total
    issues += dacn.coupon_total_issues(name, total, want)
    return c.result("cumulative", name, issues, hit, expected=dacn.coupon_total(want), actual=total)


def _final(c: _Case) -> CheckResult:
    """一般情況每單位最終現金交割金額：算式的面額與金額 = 第 6 條面額。"""
    name = "每單位最終現金交割金額"
    if (hit := c.calc.final) is None:
        return c.unread("final", name, "找不到每單位最終現金交割金額算式（面額 × 100%），或出現多次")
    final = hit.value
    issues = []
    if final.denomination != c.denom or final.amount != c.denom:
        issues.append(f"算式的面額 {final.denomination} 與金額 {final.amount} 都應等於第 6 條面額 {c.denom}")
    return c.result("final", name, issues, hit, expected=c.denom, actual=final.amount)


def _total_return(c: _Case) -> CheckResult:
    """總報酬率 = [(累積 + 交割金額或等值) / 面額] − 1（加數照文件寫的值、順序不拘），依寫的位數四捨五入。"""
    name = "總報酬率"
    calc = c.calc
    if (hit := calc.total_return) is None or calc.cumulative is None or calc.final is None:
        return c.unread(
            "return", name, "找不到總報酬率算式、每單位累積配息金額或最終交割金額（等值），或寫法不同、出現多次"
        )
    tr = hit.value
    terms = (calc.cumulative.value.total, calc.final.value.amount)
    issues = []
    if sorted(tr.terms) != sorted(terms):
        issues.append(
            f"總報酬率算式的加數 {tr.terms[0]}、{tr.terms[1]} 應為每單位累積配息金額 {terms[0]} 與{c.final_label} {terms[1]}"
        )
    if tr.divisor != c.denom:
        issues.append(f"總報酬率算式的除數 {tr.divisor} 應為第 6 條面額 {c.denom}")
    expected = kit.shown(sum(tr.terms, Decimal(0)) / tr.divisor * 100 - 100, tr.pct) if tr.divisor else None
    if expected != tr.pct:
        issues.append(f"總報酬率 {tr.pct}% 應為 [({tr.terms[0]} + {tr.terms[1]}) / {tr.divisor}] − 1 = {expected}%")
    return c.result("return", name, issues, hit, expected=expected, actual=tr.pct)


def scenario_calculations(ctx: IssuerContext) -> list[CheckResult]:
    """§16(3) 一般、最差情況用文件寫的 (n/N) 驗算（核對規則 §3.9）：第 1～g 期與第 g+1 期起配息、累積配息、一般情況最終
    現金交割金額、總報酬率；算式的月配息率由共用情境規則 `scenario.params` 核對，n、N 本身與年化不核對。"""
    f = ctx.ts.f
    denom, monthly = f("denomination"), f("monthly_coupon_pct")
    g, table = f("coupon_guaranteed_periods"), f("coupon_table")
    out = []
    for name, key, label, final_label in CALC_CASES:
        pf = f(name)
        check = kit.doc_check(ctx, CALC_RID, f"{key}_calculations", f"{label}算式", pf, denom, monthly, g, table)
        if (problem := check.blocked) is not None:
            out.append(problem)
            continue
        c = _Case(pf.value, key, label, final_label, denom.value, monthly.value, g.value, len(table.value.rows))
        out += [_first_coupon(c), _accrual(c), _cumulative(c)]
        out += [_final(c), _total_return(c)] if key == "general" else [_total_return(c)]
    return out


def run_all(ctx: IssuerContext) -> list[CheckResult]:
    """月配息率推算（參考條件表年利率與天期），再加上說明書內部規則。"""
    return [
        dacn.template_variant(ctx, SAMPLED),
        dacn.guaranteed_periods(
            ctx, 14, source="配息率 t 公式第 1 段", name="配息公式保證期數", field="coupon_guaranteed_periods"
        ),
        *coupon_barrier(ctx),
        observation_starts(ctx),
        barc.monthly_coupon(ctx),
        *barc.document_rules(ctx, scenarios=FAVOURABLE),
        *scenario_calculations(ctx),
    ]
