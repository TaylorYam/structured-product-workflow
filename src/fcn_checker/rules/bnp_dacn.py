"""BNP DACN 專屬規則（docs/checker/rules/bnp-dacn-check-rules.md）：沒有樣本的型態與 MKI、配息公式保證期數 = Non-Call、§11
定界價格列每檔價格、§17 最低配息率 = 月配息率、情境（第 g+1 期起依文件寫的 n、M 驗算，workflow #576）；其他說明書內部規則照 BNP FCN
（rules/bnp.py：月配息率推算、名稱與幣別一致、天期、KO、贖回金額、日期表、基本公開說明書、頁首）。

配息門檻 %（`field.coupon_barrier_pct`）、情境假設與情境試算的月配息率（`scenario.params`，含 `× X% × (n ÷ M)`）、重印標的表
（`scenario.reprint`，含定界價格列）、審查標準（名稱樣板 `product_names.DACN`）與回填是各上手共用的規則。
抓不到、歧義或寫法不在範本規格內一律轉人工覆核。
"""

from __future__ import annotations

from ..schema import CheckResult
from . import bnp, bnp_scenario, dacn, kit
from .kit import IssuerContext

ISSUER = bnp.ISSUER
REFERENCE_FIELDS = bnp.REFERENCE_FIELDS
NOT_COVERED = [
    *(x for x in bnp.NOT_COVERED if x["rule_id"] != "doc.template_variants"),
    {
        "rule_id": "doc.accrual_calculations",
        "description": "區間計息的 nj、Nj、合格營業日與各期配息金額不核對（只核對第 15 項配息公式與 nj、Nj 定義的固定寫法、"
        "月配息率、配息門檻與保證期數；ADR 0030）；第 15 項 KO 那一期的累計配息說明與最後兩句不核對",
    },
    {
        "rule_id": "doc.scenario_calculations",
        "description": "第 18 項自動提前贖回情況以外的情境：合格營業日總天數假設與 (n ÷ M) 的 n、M 本身不核對（第 2 期起的配息、"
        "累積總月配息金額、總報酬率用文件寫的 n、M 驗算）；較差、最差情況的年化報酬率照 FCN 不核對；最佳情況寫的 n ≠ M 時"
        "年化報酬率依假設算出、不等於年利率，不核對",
    },
    {
        "rule_id": "doc.template_variants",
        "description": "依規則推得、待樣本驗證（轉人工覆核）：美元以外的幣別、單一標的、KO 每期觀察或非記憶式、無 KI、"
        "配息公式保證期數或 Non-Call 不是 1；KI 每期觀察（MKI）維持人工（workflow #386）；寫法和現行版不同的範本不支援"
        "（不看交易日早晚）",
    },
]
SAMPLED = dacn.Sampled(
    "BNP DACN 樣本只有美元計價、多標的、KO 每日觀察＋記憶式、KI 每日或到期觀察、配息公式保證 1 期、Non-Call 1",
    (
        dacn.currency(("USD",)),
        dacn.baskets,
        dacn.ko("ko_observation"),
        dacn.ki(("D", "AM", "P")),  # 每期觀察（MKI）另由 `dacn.monthly_ki` 維持人工
        dacn.guaranteed((1,), field="coupon_guaranteed_periods"),
        dacn.non_call((1,)),
    ),
    manual=(dacn.monthly_ki,),
)


def coupon_barrier(ctx: IssuerContext) -> list[CheckResult]:
    """§11 標的表定界價格列：每檔定界價格 = 期初價格 × 配息門檻 %，四捨五入（half-up）到 4 位（核對規則 §3.4）。各檔 %
    相同由讀出端檢查（不同時配息門檻 % 歧義）。"""
    f, art = ctx.ts.f, ctx.ts.chapter1.label("underlying")
    pct, rows, uls = f("coupon_barrier_pct"), f("underlying_prices"), f("underlyings")
    rid = "doc.coupon_barrier"
    whole = kit.doc_check(ctx, rid, "coupon_barrier_prices", f"{art}標的表定界價格", pct, rows)
    if (problem := whole.blocked) is not None:
        return [problem]
    out = []
    for i, row in enumerate(rows.value, start=1):
        label = uls.value[i - 1] if uls.ok and i <= len(uls.value) else f"第 {i} 檔標的"
        check = kit.doc_check(ctx, rid, f"{label} 定界價格", f"UL_{i} 定界價格", pct)
        out.append(dacn.barrier_price(check, row, pct, f"{art}標的表第 {i} 檔讀不到定界價格"))
    return out


def run_all(ctx: IssuerContext) -> list[CheckResult]:
    """月配息率推算（參考條件表年利率），再加上說明書內部規則。"""
    return [
        dacn.template_variant(ctx, SAMPLED),
        dacn.guaranteed_periods(
            ctx,
            ctx.ts.chapter1.numbers["coupon"],
            source="配息公式第 1 個配息日",
            field="coupon_guaranteed_periods",
        ),
        *coupon_barrier(ctx),
        *bnp.monthly_coupon(ctx),
        bnp.min_coupon_rate(ctx, "monthly_coupon_pct", "月配息率"),
        *bnp.document_rules(ctx),
        *bnp_scenario.run(ctx, accrual=True),
    ]
