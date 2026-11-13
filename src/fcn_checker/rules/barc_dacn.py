"""BARC DACN 專屬規則（docs/checker/rules/barc-dacn-check-rules.md）：沒有樣本的型態、配息公式保證期數 = Non-Call、§15 配息
門檻欄（欄頭 % 與每列價格）、§14 配息觀察期表期始日；其他說明書內部規則照 BARC FCN（rules/barc.py：月配息率推算與各出處、
提前出場表、名稱與幣別一致、價格表欄頭、§9(3) 相關配息率、有利情況總報酬率、§13(7) 定義句），範本沒有年利率、一般情況總報酬率
依計息天數假設算出，不核對。

配息門檻 %（`field.coupon_barrier_pct`）、情境假設與情境試算的月配息率（`scenario.params`）、重印價格表（`scenario.reprint`，
和去掉配息門檻欄的 §15 表比）、審查標準（名稱樣板 `product_names.DACN` 和 FCN 同一句）與回填是各上手共用的規則。
抓不到、歧義或寫法不在範本規格內一律轉人工覆核。
"""

from __future__ import annotations

from ..parsers.barc_schedule import ScheduleRow
from ..schema import CheckResult, Evidence
from ..schema import CheckStatus as S
from . import barc, dacn, kit
from .kit import IssuerContext

ISSUER = barc.ISSUER
REFERENCE_FIELDS = barc.REFERENCE_FIELDS
NOT_COVERED = [
    *(x for x in barc.NOT_COVERED if x["rule_id"] != "field.monthly_ki"),
    {
        "rule_id": "doc.accrual_calculations",
        "description": "區間計息的 nt、Nt、各期配息率與配息金額不核對（只核對第 14 條配息公式與定義的固定寫法、月配息率、"
        "配息門檻與保證期數；ADR 0030）；第 14 條配息觀察期表的 Nt 欄、第 9 條(3) dt、Dt 與中斷日說明不解讀",
    },
    {
        "rule_id": "doc.scenario_calculations",
        "description": "第 16 條一般、最差情況的 n/N 假設天數、各期配息金額、累積配息、總報酬率與平均年化報酬率不核對（依假設"
        "算出，不是年利率）；各情境算式的月配息率、情境假設面額、重印價格表與有利情況總報酬率逐處核對",
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
FAVOURABLE = (barc.SCENARIOS[0],)  # 一般情況的總報酬率依計息天數假設算出（核對規則 §2）


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
    ]
