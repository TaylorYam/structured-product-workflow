"""MS DACN 專屬規則（docs/checker/rules/ms-dacn-check-rules.md）：沒有樣本的型態、第 15 項保證期數 = Non-Call、第 18 項各情境
「本商品第 g 個月發放固定配息」的 g；其他說明書內部規則照 MS FCN（rules/ms.py：月配息率含 `{X%×n(j)/N(j)}` 的 X、受託機構
商品代號、名稱與幣別一致、商品種類、期別範圍、KO 條款、日期表），情境照區間計息核（rules/ms_scenario.py `accrual=True`）。

配息門檻 %（`field.coupon_barrier_pct`，= 第 16 項執行價表頭 %）、第 18 項假設與算式的配息率（`scenario.params`；有區間計息的
情境不交年化報酬率）、重印價格表、較差情境執行價、審查標準（名稱樣板 `product_names.DACN`）與回填是各上手共用的規則。
抓不到、歧義或寫法不在範本規格內一律轉人工覆核。
"""

from __future__ import annotations

from ..schema import CheckResult
from ..schema import CheckStatus as S
from . import dacn, kit, ms
from .kit import IssuerContext

ISSUER = ms.ISSUER
REFERENCE_FIELDS = ms.REFERENCE_FIELDS
NOT_COVERED = [
    *(x for x in ms.NOT_COVERED if x["rule_id"] != "doc.template_variants"),
    {
        "rule_id": "doc.accrual_calculations",
        "description": "區間累積配息的 n(j)、N(j)、配息觀察日與各期配息金額不核對（只核對第 15 項 (2) 的固定寫法、配息率、"
        "配息門檻與保證期數；ADR 0030）；第 10 項「每月配息金額(若有)」不核對",
    },
    {
        "rule_id": "doc.scenario_calculations",
        "description": "第 18 項有區間計息算式「×（n/M）」的情境：依天數假設算出的配息、損益、總配息與年化報酬率不核對（只核"
        "固定配息那一期的金額與各算式的配息率）；沒有區間計息的情境照 MS FCN",
    },
    {
        "rule_id": "doc.template_variants",
        "description": "依規則推得、待樣本驗證（轉人工覆核）：美元以外的幣別、單一標的、5 檔以上標的、KO 非記憶式或每期觀察、"
        "無 KI、KI 每日觀察、保證期數或 Non-Call 不是 1；KI 每期觀察（MKI）在 MS 的不支援樣態清單，整批停止條件核對"
        "（workflow #386）；寫法和現行版不同的範本不支援（不看交易日早晚）",
    },
]


def at_most_four(ctx: IssuerContext) -> list[str]:
    """標的最多 4 檔（5 檔以上沒有樣本，範本規格 §4.5）。"""
    uls = ctx.ts.f("underlyings")
    return [f"{len(uls.value)} 檔標的"] if uls.ok and len(uls.value) > 4 else []


SAMPLED = dacn.Sampled(
    "MS DACN 樣本只有美元計價、2～4 檔標的、KO 每日觀察＋記憶式、KI 到期觀察、保證 1 期、Non-Call 1",
    (
        dacn.currency(("USD",)),
        dacn.baskets,
        at_most_four,
        dacn.ko("ko_observation"),
        dacn.ki(("AM", "P")),  # 每期觀察（MKI）另由 `dacn.monthly_ki` 維持人工
        dacn.guaranteed((1,), field="coupon_guaranteed_periods"),
        dacn.non_call((1,)),
    ),
    manual=(dacn.monthly_ki,),
)


def scenario_guaranteed_periods(ctx: IssuerContext) -> list[CheckResult]:
    """第 18 項每個情境的「本商品第 g 個月發放固定配息」= 第 15 項 (2) 的保證期數（核對規則 §3.5，每個情境一筆）；找不到或
    出現多次轉人工覆核。"""
    g = ctx.ts.f("coupon_guaranteed_periods")
    out = []
    for s in ctx.ts.scenarios.scenarios:
        if s.kind == "default":
            continue
        check = kit.doc_check(
            ctx,
            "doc.scenario_guaranteed_periods",
            f"情境{s.number}_guaranteed_periods",
            f"第 18 項情境{s.number}固定配息的期數",
            g,
        )
        if len(s.guaranteed) != 1:
            found = "出現多次" if s.guaranteed else "找不到"
            out.append(
                check.blocked
                or check.result(
                    S.REVIEW_REQUIRED,
                    expected=g.value,
                    reason="document_missing",
                    message=f"第 18 項情境{s.number}「本商品第 g 個月發放固定配息」{found}",
                    evidence=kit.evidence(s.lines[:1]),
                )
            )
            continue
        h = s.guaranteed[0]
        out.append(
            check.compare(
                g.value,
                h.value,
                evidence=[*kit.evidence(h.lines), *g.evidence],
                fail_message="須等於第 15 項 (2)「每單位配息金額(1)」固定配息的期數",
            )
        )
    return out


def run_all(ctx: IssuerContext) -> list[CheckResult]:
    """說明書內部規則：只用讀出結果、審查標準與宣告的參考條件表欄位（年利率）。"""
    return [
        dacn.template_variant(ctx, SAMPLED),
        dacn.guaranteed_periods(ctx, 15, source="(2)「每單位配息金額(1)」固定配息", field="coupon_guaranteed_periods"),
        *scenario_guaranteed_periods(ctx),
        *ms.run_all(ctx, accrual=True),
    ]
