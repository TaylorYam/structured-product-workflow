"""SG DACN 專屬規則（docs/checker/rules/sg-dacn-check-rules.md）：沒有樣本的型態、保證期數 = Non-Call、計息範圍、情境種類與
期別；其他說明書內部規則照 SG FCN（rules/sg.py），只是沒有配息按比例條款，也不驗算情境金額。

年利率（第 8 項年配息率，`field.coupon_pa_pct`）、配息門檻（`field.coupon_barrier_pct`）、第 15 項假設與各情境算式的配息率、
情境一二年化（`scenario.params`）、重印價格表、較差情境執行價、審查標準（DACN 名稱樣板）與回填是各上手共用的規則。
抓不到、歧義或寫法不在範本規格內一律轉人工覆核。
"""

from __future__ import annotations

from ..schema import CheckResult
from ..schema import CheckStatus as S
from . import kit, sg
from .kit import IssuerContext

ISSUER = sg.ISSUER
REFERENCE_FIELDS = sg.REFERENCE_FIELDS
NOT_COVERED = [
    *(x for x in sg.NOT_COVERED if x["rule_id"] not in ("doc.template_variants", "doc.scenario_worse_details")),
    {
        "rule_id": "doc.accrual_calculations",
        "description": "區間累積配息的 n(i)、N(i)、各期配息金額與配息日不核對（只核對定義的固定寫法與期別範圍；ADR 0030）",
    },
    {
        "rule_id": "doc.scenario_calculations",
        "description": "第 15 項各情境的 n(i)／N(i) 假設表、配息金額、計息期中間百分比、累積總配息、贖回金額、總報酬率、"
        "情境三～五的平均年化報酬率與情境四、五的實體交付算式不核對；情境金額旁的幣別仍要核對（Issue #170）",
    },
    {
        "rule_id": "doc.template_variants",
        "description": "依規則推得、待樣本驗證（轉人工覆核）：KO 每期觀察、非記憶式、無自動提前出場、單一標的、KI 每日／每期觀察、"
        "非美元計價、保證期數不是 1 或 2；2026-06 以前或寫法不同的範本不支援",
    },
]
SUPPORTED_GUARANTEED = (1, 2)
# 第 15 項情境（範本規格 §4.3）：編號、標題，以及計息期算式（none：沒有；next：第 g+1 期；rest：第 g+1～天期）
SCENARIOS = (
    ("一", "較佳情況(A)", "none"),
    ("二", "較佳情況(B)", "next"),
    ("三", "一般情況", "rest"),
    ("四", "較差情況", "rest"),
    ("五", "最差情況", "rest"),
)


def _period_text(span: tuple[int, int]) -> str:
    a, b = span
    return f"第 {a} 期" if a == b else f"第 {a}～{b} 期"


def template_variant(ctx: IssuerContext) -> CheckResult:
    """沒有樣本的型態轉人工覆核（核對規則 §3.1）：樣本只有 KO 每日觀察＋記憶式、多標的、KI 無或到期觀察、美元、保證 1 或 2 期。
    讀不到的欄位由各自的規則轉人工，這裡不重複報。"""
    f = ctx.ts.f
    issues = []
    wording, memory, uls, ki = f("ko_wording"), f("ko_memory"), f("underlyings"), kit.doc_ki(f("ki_type"))
    cz, g = f("currency_zh"), f("guaranteed_periods")
    if wording.ok and wording.value["type"] != "D":
        issues.append("KO 每期觀察" if wording.value["type"] == "P" else "無自動提前出場")
    if memory.ok and not memory.value and not (wording.ok and wording.value["type"] == "none"):
        issues.append("非記憶式")
    if uls.ok and len(uls.value) < 2:
        issues.append("單一標的")
    if ki is not None and ki not in ("none", "AM"):
        issues.append(f"KI {kit.KI_LABEL[ki]}")
    if cz.ok and ctx.std.currency_zh_to_iso.get(cz.value) != "USD":
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
        fail_message="待樣本驗證（SG DACN 樣本只有 KO 每日觀察＋記憶式、多標的、無 KI 或到期觀察、美元、保證 1 或 2 期），"
        "請人工核對",
    )


def guaranteed_periods(ctx: IssuerContext) -> CheckResult:
    """第 8 項配息公式的保證期數 = 日期表推得的 Non-Call（核對規則 §3.5）。"""
    f = ctx.ts.f
    g, k = f("guaranteed_periods"), f("first_callable_period")
    return kit.doc_check(ctx, "doc.guaranteed_periods", "guaranteed_periods", "配息公式保證期數", k, g).compare(
        k.value,
        g.value,
        evidence=list(g.evidence),
        fail_message="第 8 項配息公式保證全額配息的期數須等於第一個可提前出場期（Non-Call）",
    )


def accrual_range(ctx: IssuerContext) -> CheckResult:
    """配息公式第二行、n(i)、N(i) 的期別範圍都 = 第 g+1 期～天期（核對規則 §3.5）。"""
    f = ctx.ts.f
    g, tenor, ranges = f("guaranteed_periods"), f("tenor_months"), f("accrual_ranges")
    issues = []
    if g.ok and tenor.ok and ranges.ok:
        want = (g.value + 1, tenor.value)
        for where, a, b in ranges.value:
            if (a, b) != want:
                issues.append(
                    f"{where}寫第 {a}-{b} 期，應為第 {want[0]}-{want[1]} 期（保證 {g.value} 期、天期 {tenor.value} 個月）"
                )
    return kit.doc_check(ctx, "doc.accrual_range", "accrual_ranges", "區間累積配息期別範圍", g, tenor, ranges).compare(
        None,
        None,
        ok=not issues,
        reason="document_inconsistent",
        message="；".join(issues),
        evidence=list(ranges.evidence),
    )


def scenario_terms(ctx: IssuerContext) -> list[CheckResult]:
    """第 15 項情境（核對規則 §3.8）：剛好五個且標題依序（不同轉人工覆核）；每個情境保證期算式 = 第 1～g 期、計息期算式
    情境二 = 第 g+1 期、情境三～五 = 第 g+1～天期（情境一沒有），算式找不到或重複轉人工覆核、期別不對判不一致。"""
    f = ctx.ts.f
    terms, g, tenor = f("scenario_terms"), f("guaranteed_periods"), f("tenor_months")
    titles = kit.doc_check(ctx, "doc.scenario_terms", "scenario_titles", "第 15 項情境標題", terms)
    if (blocked := titles.blocked) is not None:
        return [blocked]
    found = [(t.number, t.title) for t in terms.value]
    expected = [(n, title) for n, title, _ in SCENARIOS]
    evidence = kit.evidence(t.lines[0] for t in terms.value)
    out = [
        titles.compare(
            "、".join(f"情境{n}：{t}" for n, t in expected),
            "、".join(f"情境{n}：{t}" for n, t in found),
            fail=S.REVIEW_REQUIRED,
            reason="scenario_unknown_wording",
            evidence=evidence,
            fail_message="情境的數量、順序或標題不在範本規格內（較佳情況(A)、較佳情況(B)、一般情況、較差情況、最差情況）",
        )
    ]
    check = kit.doc_check(ctx, "doc.scenario_terms", "scenario_periods", "第 15 項情境配息算式期別", terms, g, tenor)
    if (blocked := check.blocked) is not None:
        return [*out, blocked]
    if found != expected:  # 情境對不上時期別無從比，只報標題
        return out
    missing, wrong = [], []
    for t, (_, _, kind) in zip(terms.value, SCENARIOS, strict=True):
        want_accrual = {"none": (), "next": ((g.value + 1, g.value + 1),), "rest": ((g.value + 1, tenor.value),)}[kind]
        for what, got, want in (
            ("保證期配息算式", t.guaranteed, ((1, g.value),)),
            ("計息期配息算式", t.accrual, want_accrual),
        ):
            if len(got) != len(want):
                missing.append(f"情境{t.number}{what}應有 {len(want)} 處、找到 {len(got)} 處")
            elif got != want:
                wrong.append(
                    f"情境{t.number}{what}寫{'、'.join(map(_period_text, got))}，應為{'、'.join(map(_period_text, want))}"
                )
    if missing:
        return [
            *out,
            check.compare(
                None,
                None,
                ok=False,
                fail=S.REVIEW_REQUIRED,
                reason="scenario_unknown_wording",
                message="；".join(missing),
                fail_message="（算式找不到或寫法不在範本規格內，請人工核對）",
                evidence=evidence,
            ),
        ]
    return [
        *out,
        check.compare(
            None, None, ok=not wrong, reason="document_inconsistent", message="；".join(wrong), evidence=evidence
        ),
    ]


def run_all(ctx: IssuerContext) -> list[CheckResult]:
    """說明書內部規則：只用讀出結果、審查標準與宣告的參考條件表欄位（年利率）。"""
    return [
        template_variant(ctx),
        guaranteed_periods(ctx),
        accrual_range(ctx),
        *scenario_terms(ctx),
        *sg.monthly_coupon(ctx),
        *sg.document_info(ctx),
        *sg.periods(ctx),
        *sg.autocall_terms(ctx),
        *sg.ki_terms(ctx),
        *sg.redemption(ctx),
        *sg.schedule(ctx),
    ]
