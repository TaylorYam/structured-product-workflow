"""Nomura DACN 專屬規則（docs/checker/rules/nomura-dacn-check-rules.md）：沒有樣本的型態、年利率的其他出處（第 15 項 (1)）、
保證期數 = Non-Call、情境二年化報酬率重算；其他說明書內部規則照 Nomura FCN（rules/nomura.py），只是沒有每期配息率。

年利率（第 7 項年化配息率，`field.coupon_pa_pct`）、配息門檻（`field.coupon_barrier_pct`）、第 14 項設定參數與情境一的
年利率（`scenario.params`）、審查標準（DACN 名稱樣板）與回填是各上手共用的規則。抓不到、歧義或寫法不在範本規格內一律轉人工覆核。
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from ..schema import CheckResult, FieldStatus, ParsedField
from ..schema import CheckStatus as S
from . import kit, nomura
from .kit import IssuerContext

ISSUER = nomura.ISSUER
NOT_COVERED = [
    *(x for x in nomura.NOT_COVERED if x["rule_id"] not in ("doc.template_variants", "doc.scenario_calculations")),
    {
        "rule_id": "doc.accrual_calculations",
        "description": "每日計息的實際日數、總日數、各期配息金額與配息日不核對（只核對定義的固定寫法；ADR 0030）",
    },
    {
        "rule_id": "doc.scenario_calculations",
        "description": "第 14 項情境三、四（假設價格、股數、利息、剩餘金額、損失率）與基本假設的 Actual/360 不核對；"
        "情境三金額旁的幣別仍要核對（Issue #316）",
    },
    {
        "rule_id": "doc.template_variants",
        "description": "依規則推得、待樣本驗證（轉人工覆核）：KO 每期觀察、非記憶式、單一標的、KI 每日／每期觀察、非美元計價、"
        "保證期數或 Non-Call 不是 1；2026-06 以前或寫法不同的範本不支援",
    },
]
Q2 = Decimal("0.01")


def template_variant(ctx: IssuerContext) -> CheckResult:
    """沒有樣本的型態轉人工覆核（核對規則 §3.1）：樣本只有 KO 每日觀察＋記憶式、多標的、KI 無或到期觀察、美元、
    保證期數 1、Non-Call 1。讀不到的欄位由各自的規則轉人工，這裡不重複報。"""
    f = ctx.ts.f
    issues = []
    wording, memory, uls, ki = f("ko_wording"), f("ko_memory"), f("underlyings"), kit.doc_ki(f("ki_type"))
    cz, g, k = f("currency_zh"), f("guaranteed_periods"), f("first_callable_period")
    if wording.ok and wording.value[0] != "D":
        issues.append("KO 每期觀察")
    if memory.ok and not memory.value:
        issues.append("非記憶式")
    if uls.ok and len(uls.value) < 2:
        issues.append("單一標的")
    if ki is not None and ki not in ("none", "AM"):
        issues.append(f"KI {kit.KI_LABEL[ki]}")
    if cz.ok and ctx.std.currency_zh_to_iso.get(cz.value) != "USD":
        issues.append(f"{cz.value}計價")
    if g.ok and g.value != 1:
        issues.append(f"保證 {g.value} 期")
    if k.ok and k.value != 1:
        issues.append(f"Non-Call {k.value}")
    return kit.doc_check(ctx, "doc.template_variant", "template_variant", "DACN 型態").compare(
        None,
        None,
        ok=not issues,
        fail=S.REVIEW_REQUIRED,
        reason="template_variant_unverified",
        message="、".join(issues),
        fail_message="待樣本驗證（Nomura DACN 樣本只有 KO 每日觀察＋記憶式、多標的、無 KI 或到期觀察、美元、Non-Call 1），"
        "請人工核對",
    )


def coupon_mentions(ctx: IssuerContext) -> list[CheckResult]:
    """第 15 項 (1) 兩處年化報酬率 = 第 7 項年化配息率（核對規則 §3.3；設定參數與情境一由共用情境規則核對）。"""
    main = ctx.ts.f("coupon_pa_pct")
    out = []
    for m in ctx.ts.coupon_mentions:
        pf = ParsedField(
            "coupon_mention",
            FieldStatus.PRESENT if m.value is not None else FieldStatus.MISSING,
            m.value,
            list(m.evidence),
            note="" if m.value is not None else f"找不到{m.where}或寫法不在範本規格內",
        )
        out.append(
            kit.doc_check(ctx, "doc.coupon_mentions", "coupon_pa_pct", m.where, main, pf).compare(
                main.value, pf.value, evidence=list(m.evidence), fail_message="須等於第 7 項年化配息率"
            )
        )
    return out


def guaranteed_periods(ctx: IssuerContext) -> CheckResult:
    """配息公式的保證期數 = 第 7 項 KO 起日推得的 Non-Call（核對規則 §3.5）。"""
    f = ctx.ts.f
    g, k = f("guaranteed_periods"), f("first_callable_period")
    return kit.doc_check(ctx, "doc.guaranteed_periods", "guaranteed_periods", "配息公式保證期數", k, g).compare(
        k.value,
        g.value,
        evidence=list(g.evidence),
        fail_message="第 7 項配息公式保證全額配息的期數須等於第一個可提前出場期（Non-Call）",
    )


def scenario_return(ctx: IssuerContext) -> CheckResult:
    """情境二年化報酬率 = 年化配息率 ×（g + P% ×（T − g））÷ T，四捨五入（half-up）到 2 位（核對規則 §3.3）。"""
    f = ctx.ts.f
    annual, g, tenor, p, y = (
        f(x)
        for x in ("coupon_pa_pct", "guaranteed_periods", "tenor_months", "accrual_scenario_pct", "scenario2_return")
    )
    check = kit.doc_check(
        ctx, "doc.scenario_return", "scenario2_return", "第 14 項情境二年化報酬率", annual, g, tenor, p, y
    )
    if (blocked := check.blocked) is not None:
        return blocked
    t, n = Decimal(tenor.value), Decimal(g.value)
    expected = (annual.value * (n + p.value / 100 * (t - n)) / t).quantize(Q2, ROUND_HALF_UP)
    return check.compare(
        expected,
        y.value,
        evidence=list(y.evidence),
        tolerance="年化配息率 ×（保證期數 + P% ×（天期 − 保證期數））÷ 天期，四捨五入（half-up）到 2 位",
        fail_message=f"須等於年化配息率 {annual.value}% 依保證 {g.value} 期、天期 {tenor.value} 個月、"
        f"實際日數/總日數 {p.value}% 算出的值",
    )


def run_all(ctx: IssuerContext) -> list[CheckResult]:
    """說明書內部規則：只用讀出結果與審查標準。"""
    return [
        template_variant(ctx),
        *coupon_mentions(ctx),
        guaranteed_periods(ctx),
        scenario_return(ctx),
        *nomura.document_info(ctx),
        *nomura.periods(ctx),
        *nomura.ko_terms(ctx),
        *nomura.underlying_terms(ctx),
        *nomura.ki_terms(ctx),
        *nomura.redemption(ctx),
        *nomura.scenario_terms(ctx),
        *nomura.schedule(ctx),
    ]
