"""DBS DACN 專屬規則（docs/checker/rules/dbs-dacn-check-rules.md）：沒有樣本的型態、表1 配息門檻價格、情境種類與
`於第a~b個觀察期間` 的範圍；其他說明書內部規則照 DBS FCN（rules/dbs.py：最大利率值無條件進位、第 1～k 期與情境一出場期、
名稱與幣別一致、觀察起始日、月數、情境投資金額與情境三）。

配息門檻 %（`field.coupon_barrier_pct`）、年利率、情境價格表逐格（含區間下限欄，`scenario.reprint`）、審查標準（DACN 名稱
樣板）與回填是各上手共用的規則。抓不到、歧義或寫法不在範本規格內一律轉人工覆核。
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP

from ..parsers import dbs_dacn
from ..schema import CheckResult
from ..schema import CheckStatus as S
from . import dbs, kit
from .kit import Q4, IssuerContext

ISSUER = dbs.ISSUER
REFERENCE_FIELDS = dbs.REFERENCE_FIELDS
NOT_COVERED = [
    *(x for x in dbs.NOT_COVERED if x["rule_id"] not in ("doc.template_variants", "doc.scenario_calculations")),
    {
        "rule_id": "doc.accrual_calculations",
        "description": "區間累計配息的累積日數、總日數、各期利率與利息金額不核對（只核對定義的固定寫法；干擾日的計數方式"
        "不解讀；ADR 0030）；到期贖回「區間下限價格」「區間上限價格」定義句不核對（表1 已核對）",
    },
    {
        "rule_id": "doc.scenario_calculations",
        "description": "情境分析只核對基本假設、情境價格表（含區間下限欄）、情境一出場期、情境二年化報酬率、情境種類與"
        "「於第a~b個觀察期間」範圍，以及重複寫出的最大利率值、k、情境三（較差情況）期初價格與表現最差標的價格、金額旁的"
        "幣別；利息金額、股數、零股金額、報酬率（含於到期較佳情況的年化報酬率）、假設匯率、「20」「10」的假設日數不核對",
    },
    {
        "rule_id": "doc.template_variants",
        "description": "依規則推得、待樣本驗證（轉人工覆核）：非美元或 Quanto、單一標的、保證期數或 Non-Call 不是 1、區間上限"
        "價格適用；2026-06 以前或寫法不同的範本不支援",
    },
]
TITLES = {  # KI 型態 → 情境二～四應有的標題（依序，範本規格 §4.3）
    "none": (dbs_dacn.BEST, dbs_dacn.WORSE),
    "AM": (dbs_dacn.BEST, dbs_dacn.BETTER, dbs_dacn.WORSE),
}


def template_variant(ctx: IssuerContext) -> CheckResult:
    """沒有樣本的型態轉人工覆核（核對規則 §3.1）：樣本只有美元計價、非 Quanto、多標的、保證 1 期、Non-Call 1、區間上限
    `不適用`。讀不到的欄位由各自的規則轉人工，這裡不重複報。"""
    f = ctx.ts.f
    issues = []
    cz, uls, rows = f("currency_zh"), f("underlyings"), f("underlying_prices")
    g, k, upper = f("guaranteed_periods"), f("first_callable_period"), f("range_upper")
    if cz.ok and ctx.std.currency_zh_to_iso.get(cz.value) != "USD":
        issues.append(f"{cz.value}計價")
    if cz.ok and rows.ok and any(r.currency and r.currency != cz.value for r in rows.value):
        issues.append("Quanto（標的報價幣別和計價幣別不同）")
    if uls.ok and len(uls.value) < 2:
        issues.append("單一標的")
    if g.ok and g.value != 1:
        issues.append(f"保證 {g.value} 期")
    if k.ok and k.value != 1:
        issues.append(f"Non-Call {k.value}")
    if upper.ok and any(x != dbs_dacn.NOT_APPLICABLE for x in upper.value):
        issues.append(f"區間上限價格不是「{dbs_dacn.NOT_APPLICABLE}」")
    return kit.doc_check(ctx, "doc.template_variant", "template_variant", "DACN 型態").compare(
        None,
        None,
        ok=not issues,
        fail=S.REVIEW_REQUIRED,
        reason="template_variant_unverified",
        message="、".join(issues),
        fail_message="待樣本驗證（DBS DACN 樣本只有美元計價、多標的、保證 1 期、Non-Call 1、無區間上限），請人工核對",
    )


def coupon_barrier(ctx: IssuerContext) -> list[CheckResult]:
    """表1 每列區間下限價格 = 期初價格 × 配息門檻 %，四捨五入（half-up）到 4 位（核對規則 §3.4；情境價格表的區間下限欄由
    共用 `scenario.reprint` 逐格和表1 比）。"""
    f = ctx.ts.f
    pct, rows, uls = f("coupon_barrier_pct"), f("underlying_prices"), f("underlyings")
    rid = "doc.coupon_barrier"
    whole = kit.doc_check(ctx, rid, "coupon_barrier_prices", "表1 區間下限價格", pct, rows)
    if (problem := whole.blocked) is not None:
        return [problem]
    out = []
    for i, row in enumerate(rows.value, start=1):
        label = uls.value[i - 1] if uls.ok and i <= len(uls.value) else (row.ticker or f"第 {i} 檔標的")
        actual = row.prices.get("barrier")
        check = kit.doc_check(ctx, rid, f"{label} 區間下限價格", f"UL_{i} 區間下限價格（配息門檻價）", pct)
        if actual is None:
            out.append(
                check.result(
                    S.REVIEW_REQUIRED,
                    reason="document_missing",
                    message=f"表1 第 {i} 列讀不到區間下限價格",
                    evidence=list(row.evidence),
                )
            )
            continue
        initial = row.prices["initial"]
        out.append(
            check.compare(
                (initial * pct.value / 100).quantize(Q4, ROUND_HALF_UP),
                actual,
                evidence=[*row.evidence, *pct.evidence],
                tolerance="四捨五入（half-up）到 4 位",
                message=f"期初價格 {initial} × {pct.value}%",
            )
        )
    return out


def scenario_periods(ctx: IssuerContext) -> list[CheckResult]:
    """情境種類（無 KI：於到期最佳、較差；KI 到期觀察：多於到期較佳，依序）與每個情境 `於第a~b個觀察期間` 的 a = 保證期數
    + 1、b = 天期（核對規則 §3.5、§3.6）。"""
    f = ctx.ts.f
    rid = "doc.scenario_periods"
    titles, ranges, ki, g, tenor = (
        f(x) for x in ("scenario_titles", "scenario_ranges", "ki_type", "full_rate_periods", "tenor_months")
    )
    kinds = kit.doc_check(ctx, rid, "scenario_titles", "情境分析的情境種類", titles, ki)
    if (problem := kinds.blocked) is not None:
        return [problem]
    expected = TITLES.get(kit.doc_ki(ki) or "", ())
    actual = tuple(t for t, _ in titles.value)
    out = [
        kinds.compare(
            "、".join(x.rstrip("：") for x in expected) or f"（KI {ki.value} 沒有樣本）",
            "、".join(x.rstrip("：") for x in actual),
            ok=bool(expected) and actual == expected,
            fail=S.REVIEW_REQUIRED,
            reason="document_inconsistent",
            fail_message="情境種類和 KI 型態不符或不在範本規格內（無 KI：於到期最佳、較差；KI 到期觀察：於到期最佳、"
            "於到期較佳、較差）",
        )
    ]
    span = kit.doc_check(ctx, rid, "scenario_ranges", "情境「於第a~b個觀察期間」", ranges, g, tenor)
    if (problem := span.blocked) is not None:
        return [*out, problem]
    want = (g.value + 1, tenor.value)
    for title, a, b in ranges.value:
        check = kit.doc_check(ctx, rid, "scenario_ranges", f"{title}「於第a~b個觀察期間」", ranges, g, tenor)
        if a is None:
            out.append(
                check.result(
                    S.REVIEW_REQUIRED,
                    reason="document_missing",
                    message=f"{title}找不到或有多處「於第a~b個觀察期間」",
                )
            )
            continue
        out.append(
            check.compare(
                f"第{want[0]}~{want[1]}個",
                f"第{a}~{b}個",
                ok=(a, b) == want,
                fail_message="a 須為保證期數 + 1（第一個以累積日數計息的觀察期間）、b 須為天期",
            )
        )
    return out


def run_all(ctx: IssuerContext) -> list[CheckResult]:
    """說明書內部規則：只用讀出結果與審查標準。"""
    return [
        template_variant(ctx),
        *coupon_barrier(ctx),
        *scenario_periods(ctx),
        *dbs.run_all(ctx),
    ]
