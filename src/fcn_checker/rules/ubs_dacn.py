"""UBS DACN 專屬規則（docs/checker/rules/ubs-dacn-check-rules.md）：沒有樣本的型態、配息表保證期數 = Non-Call、八價格表每列
區間下限價、十四的保證期數、nt 定義的計息範圍；其他說明書內部規則照 UBS FCN（rules/ubs.py：配息率與變動配息率、受託機構
商品代號、名稱與幣別一致、發行分行、面額的其他出處、固定／變動配息期別與 t 範圍、KO 條款的 KO %）。

配息門檻 %（`field.coupon_barrier_pct`）、十五設定參數與情境算式的面額與配息率（`scenario.params`；DACN 不讀平均年化報酬率）、
情境價格表逐格（含區間下限價欄，`scenario.reprint`）、審查標準（名稱樣板 `product_names.DACN`）與回填是各上手共用的規則。
抓不到、歧義或寫法不在範本規格內一律轉人工覆核。
"""

from __future__ import annotations

from ..schema import CheckResult
from ..schema import CheckStatus as S
from . import dacn, kit, ubs
from .kit import IssuerContext

ISSUER = ubs.ISSUER
REFERENCE_FIELDS = ubs.REFERENCE_FIELDS
ART8 = "商品基本資料八"
NOT_COVERED = [
    *(x for x in ubs.NOT_COVERED if x["rule_id"] not in ("doc.template_variants", "doc.scenario_calculations")),
    {
        "rule_id": "doc.accrual_calculations",
        "description": "區間累計配息的 nt、Nt、交易所營業日與各期配息金額不核對（只核對八的變動配息率、nt、Nt 定義的固定寫法、"
        "配息率、配息門檻、保證期數與 nt 的計息範圍；ADR 0030）；十二「交易所營業日」定義不核對",
    },
    {
        "rule_id": "doc.scenario_calculations",
        "description": "商品基本資料十五情境：「假設每個觀察期間皆為M個交易所營業日」、算式的 n/M、配息金額、累積配息、結算收益、"
        "股數、剩餘金額與所有情境的平均年化報酬率不核對（依計息天數假設算出）；設定參數、情境價格表（含區間下限價欄）、"
        "算式代入的每單位面額與配息率、金額幣別要等於正式條款",
    },
    {
        "rule_id": "doc.template_variants",
        "description": "依規則推得、待樣本驗證（轉人工覆核）：美元以外的幣別、單一標的、有 KI、配息表保證期數或 Non-Call 不是 1；"
        "KI 每期觀察（MKI）維持人工（workflow #386）；寫法和現行版不同的範本不支援（不看交易日早晚），KO 每期觀察的配息觀察日表"
        "沒有 DACN 配息表公式，也是不支援的範本版本",
    },
]
SAMPLED = dacn.Sampled(
    "UBS DACN 樣本只有美元計價、多標的、KO 每日觀察（記憶式或非記憶式）、無 KI、配息表保證 1 期、Non-Call 1",
    (
        dacn.currency(("USD",)),
        dacn.baskets,
        dacn.ko("ko_observation", memory_only=False),
        dacn.ki(("none", "P")),  # 每期觀察（MKI）另由 `dacn.monthly_ki` 維持人工
        dacn.guaranteed((1,), field="coupon_guaranteed_periods"),
        dacn.non_call((1,)),
    ),
    manual=(dacn.monthly_ki,),
)
SCOPE = {
    "all": "所有連結標的股票收盤價皆高於或等於區間下限價",
    "single": "連結標的股票收盤價高於或等於區間下限價",
}


def coupon_barrier(ctx: IssuerContext) -> list[CheckResult]:
    """八價格表每列區間下限價 = 期初價格 × 配息門檻 %，四捨五入（half-up）到 4 位（核對規則 §3.4；十五情境價格表的區間
    下限價欄由共用 `scenario.reprint` 逐格和八比）。"""
    f = ctx.ts.f
    pct, rows, uls = f("coupon_barrier_pct"), f("underlying_prices"), f("underlyings")
    rid = "doc.coupon_barrier"
    whole = kit.doc_check(ctx, rid, "coupon_barrier_prices", f"{ART8}價格表區間下限價", pct, rows)
    if (problem := whole.blocked) is not None:
        return [problem]
    out = []
    for i, row in enumerate(rows.value, start=1):
        label = uls.value[i - 1] if uls.ok and i <= len(uls.value) else f"第 {i} 檔標的"
        check = kit.doc_check(ctx, rid, f"{label} 區間下限價", f"UL_{i} 區間下限價（配息門檻價）", pct)
        out.append(dacn.barrier_price(check, row, pct, f"{ART8}價格表第 {i} 列讀不到區間下限價"))
    return out


def min_coupon_guarantee(ctx: IssuerContext) -> CheckResult:
    """十四「到期最低保證配息率：第一個月有固定配息」推得的保證期數 = 配息表保證期數（核對規則 §3.5）。"""
    f = ctx.ts.f
    g, art14 = f("coupon_guaranteed_periods"), f("guaranteed_periods_art14")
    return kit.doc_check(
        ctx,
        "doc.min_coupon_guarantee",
        "guaranteed_periods_art14",
        "商品基本資料十四到期最低保證配息率的期數",
        g,
        art14,
    ).compare(g.value, art14.value, fail_message="須等於配息表「固定配息率」的期數")


def accrual_scope(ctx: IssuerContext) -> list[CheckResult]:
    """多標的時 nt 定義要寫「所有連結標的股票收盤價皆…」；寫成單一標的的寫法時計息看哪幾檔不明確，轉人工覆核（核對規則
    §3.6；暫定做法，待使用者決定）。十五設定參數重複的 nt 定義寫法要和八相同（§3.3、D41）。"""
    f = ctx.ts.f
    scope, art15, uls = f("accrual_scope"), f("accrual_scope_art15"), f("underlyings")
    rid = "doc.accrual_scope"
    check = kit.doc_check(ctx, rid, "accrual_scope", f"{ART8} nt 定義的計息範圍", scope, uls)
    repeated = kit.doc_check(ctx, rid, "accrual_scope_art15", "商品基本資料十五 nt 定義的計息範圍", scope, art15)
    same = repeated.blocked or repeated.compare(
        SCOPE[scope.value], SCOPE[art15.value], fail_message=f"須等於{ART8}的 nt 定義（計息看哪幾檔）"
    )
    if (blocked := check.blocked) is not None:
        return [blocked, same]
    expected = SCOPE["all"] if len(uls.value) > 1 else SCOPE[scope.value]  # 單一標的兩種寫法都收
    return [
        check.compare(
            expected,
            SCOPE[scope.value],
            fail=S.REVIEW_REQUIRED,
            reason="document_inconsistent",
            fail_message=f"{len(uls.value)} 檔標的，nt 定義卻寫成單一標的的「{SCOPE['single']}」（多標的應寫"
            f"「{SCOPE['all']}」），計息看哪幾檔不明確，請向上手確認",
        ),
        same,
    ]


def run_all(ctx: IssuerContext) -> list[CheckResult]:
    """說明書內部規則：只用讀出結果與審查標準。"""
    return [
        dacn.template_variant(ctx, SAMPLED),
        dacn.guaranteed_periods(ctx, ART8, source="配息表「固定配息率」", field="coupon_guaranteed_periods"),
        *coupon_barrier(ctx),
        min_coupon_guarantee(ctx),
        *accrual_scope(ctx),
        *ubs.run_all(ctx),
    ]
