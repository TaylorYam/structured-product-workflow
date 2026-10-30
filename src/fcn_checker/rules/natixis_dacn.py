"""Natixis DACN 專屬規則（docs/checker/rules/natixis-dacn-check-rules.md）：沒有樣本的型態、保證期數 = Non-Call、各情境配息算式的
保證期／計息期；其他說明書內部規則照 Natixis FCN（rules/natixis.py），情境金額與區間計息的 n、N 不核對。

年利率（第 13 項，`field.coupon_pa_pct`）、配息門檻（第 9 項欄頭，`field.coupon_barrier_pct`）、情境假設與各情境算式的月配息率、
情境一年化（`scenario.params`）、重印價格表、實物交割執行價格、審查標準（DACN 名稱樣板）與回填是各上手共用的規則。
抓不到、歧義或寫法不在範本規格內一律轉人工覆核。
"""

from __future__ import annotations

from ..schema import CheckResult
from ..schema import CheckStatus as S
from . import kit, natixis
from .kit import IssuerContext

ISSUER = natixis.ISSUER
REFERENCE_FIELDS = natixis.REFERENCE_FIELDS
NOT_COVERED = [
    *(x for x in natixis.NOT_COVERED if x["rule_id"] not in ("doc.template_variants", "doc.scenario_calculations")),
    {
        "rule_id": "doc.accrual_calculations",
        "description": "區間計息的 n、N、各期配息金額與配息日不核對（只核對第 13 項定義的固定寫法與日期表的保證期數；ADR 0030）；"
        "第 12 項 (13) D. 配息觀察日為中斷日不解讀",
    },
    {
        "rule_id": "doc.scenario_calculations",
        "description": "第 16 項情境假設 g. 預定交易日(N)、各情境 n/N 假設天數、配息金額、累積配息、股數、剩餘現金與情境一以外的"
        "平均年化報酬率不核對；各情境算式的月配息率、面額、保證期／計息期列、情境一年化、贖回 %、執行價格、配息列數與金額幣別"
        "逐處核對",
    },
    {
        "rule_id": "doc.template_variants",
        "description": "依規則推得、待樣本驗證（轉人工覆核）：KO 每期觀察、非記憶式、單一標的、KI 每期觀察（workflow Issue #386）、"
        "美元與日圓以外的幣別、保證期數不是 1；2026-06 以前或寫法不同的範本不支援",
    },
]
SUPPORTED_CURRENCIES = ("USD", "JPY")
SUPPORTED_KI = ("none", "AM", "D")
SUPPORTED_GUARANTEED = (1,)
GUARANTEED, ACCRUAL = "固定", "變動"


def template_variant(ctx: IssuerContext) -> CheckResult:
    """沒有樣本的型態轉人工覆核（核對規則 §3.1）：樣本只有 KO 每日觀察＋記憶式、多標的、KI 無／到期／每日觀察、美元或日圓、
    保證 1 期。讀不到的欄位由各自的規則轉人工，這裡不重複報。"""
    f = ctx.ts.f
    issues = []
    obs, memory, uls, ki = f("ko_observation"), f("ko_memory"), f("underlyings"), kit.doc_ki(f("ki_type"))
    cz, g = f("currency_zh"), f("guaranteed_periods")
    if obs.ok and obs.value != "D":
        issues.append("KO 每期觀察")
    if memory.ok and not memory.value:
        issues.append("非記憶式")
    if uls.ok and len(uls.value) < 2:
        issues.append("單一標的")
    if ki is not None and ki not in SUPPORTED_KI:
        issues.append(f"KI {kit.KI_LABEL[ki]}")
    if cz.ok and ctx.std.currency_zh_to_iso.get(cz.value) not in SUPPORTED_CURRENCIES:
        issues.append(f"{cz.value}計價")
    if g.ok and g.value not in SUPPORTED_GUARANTEED:
        issues.append(f"保證 {g.value} 期")
    return kit.doc_check(ctx, "doc.template_variant", "template_variant", "DACN 型態").compare(
        None,
        None,
        ok=not issues,
        fail=S.REVIEW_REQUIRED,
        reason="template_variant_unverified",
        message="、".join(issues),
        fail_message="待樣本驗證（Natixis DACN 樣本只有 KO 每日觀察＋記憶式、多標的、KI 無／到期／每日觀察、美元或日圓、"
        "保證 1 期），請人工核對",
    )


def guaranteed_periods(ctx: IssuerContext) -> CheckResult:
    """第 12 項日期表的保證期數（連續「固定配息金額」的列數）= Non-Call（核對規則 §3.5）。"""
    f = ctx.ts.f
    g, k = f("guaranteed_periods"), f("first_callable_period")
    return kit.doc_check(ctx, "doc.guaranteed_periods", "guaranteed_periods", "日期表保證期數", k, g).compare(
        k.value,
        g.value,
        evidence=list(g.evidence),
        fail_message="第 12 項日期表「固定配息金額」的期數須等於第一個可提前到期期（Non-Call）",
    )


def _show(kinds: tuple[str, ...]) -> str:
    """配息算式種類的顯示值：連續相同的種類併成一段（例：第 1 期固定、第 2～12 期變動）。"""
    out, start = [], 0
    for n in range(1, len(kinds) + 1):
        if n == len(kinds) or kinds[n] != kinds[start]:
            span = f"第 {start + 1} 期" if n == start + 1 else f"第 {start + 1}～{n} 期"
            out.append(f"{span}{kinds[start]}")
            start = n
    return "、".join(out) or "（沒有配息算式）"


def scenario_terms(ctx: IssuerContext) -> CheckResult:
    """第 16 項每個情境的配息算式：第 1～g 列是保證期算式（沒有「x (n/N)」）、之後是計息期算式（核對規則 §3.8）。列數由
    `natixis.repeated_terms` 核對（情境一 = Non-Call、其他 = 天期）。"""
    f = ctx.ts.f
    rows, g = f("scenario_coupon_rows"), f("guaranteed_periods")
    check = kit.doc_check(
        ctx, "doc.scenario_terms", "scenario_coupon_rows", "第 16 項情境配息算式的保證期與計息期", rows, g
    )
    if (blocked := check.blocked) is not None:
        return blocked
    wrong = []
    for r in rows.value:
        n = len(r.kinds)
        want = (GUARANTEED,) * min(g.value, n) + (ACCRUAL,) * max(n - g.value, 0)
        if r.kinds != want:
            wrong.append(f"{r.case}寫{_show(r.kinds)}，應為{_show(want)}")
    return check.compare(
        None,
        None,
        ok=not wrong,
        reason="document_inconsistent",
        message="；".join(wrong),
        evidence=kit.evidence(ln for r in rows.value for ln in r.lines),
        fail_message=f"每個情境第 1～{g.value} 列須是保證期算式（沒有「x (n/N)」），之後是計息期算式",
    )


def run_all(ctx: IssuerContext) -> list[CheckResult]:
    """說明書內部規則：只用讀出結果、審查標準與宣告的參考條件表欄位（交易日）。"""
    return [
        template_variant(ctx),
        guaranteed_periods(ctx),
        scenario_terms(ctx),
        *natixis.run_all(ctx),
    ]
