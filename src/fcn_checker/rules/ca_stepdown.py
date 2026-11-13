"""CA FCN_Stepdown 說明書內部規則（docs/checker/rules/ca-stepdown-check-rules.md，workflow #546）。

沿用 CA FCN 的說明書內部規則（`rules/ca.py`；價格表與情境價格表沒有 KO 欄，表頭 % 不產生 KO 那一項），另加第 9 項 (1)(b)
每期 KO 表每列的結束日 = 配息表同一期的配息期結束日（`doc.ko_table_dates`）。每期 KO% 固定往下降、期別對齊由範本辨識確認，
和參考條件表逐期比由共用 `field.ko_levels` 負責。
"""

from __future__ import annotations

from ..schema import CheckResult, ParsedField
from . import ca, kit
from .kit import IssuerContext

ISSUER = ca.ISSUER
REFERENCE_FIELDS = ca.REFERENCE_FIELDS
NOT_COVERED = [
    *(x for x in ca.NOT_COVERED if x["rule_id"] != "doc.template_variants"),
    {
        "rule_id": "doc.template_variants",
        "description": "依規則推得、待樣本驗證：單一標的、KI 每日觀察、USD 以外的幣別；舊範本（每期 KO 表是觀察日表多一欄、"
        "沒有觀察期定義句）、非記憶式、KO 每期觀察、Non-Call ≥ 2、KO% 不是每期固定往下降、價格表有 KO 欄、每期 KO 表期別和"
        "配息表對不上 → 範本不支援",
    },
]


def _date_issues(table: ParsedField, coupon: ParsedField) -> list[str]:
    """每期 KO 表每列的結束日和配息表同一期的配息期結束日不同的說明。"""
    ends = {r["period"]: r.get("end") for r in coupon.value["rows"]}
    return [
        f"第 9 項 (1)(b) 每期 KO 表第 {row.i} 期的結束日和配息表第 {row.i} 期的配息期結束日不同"
        for row in table.value
        if ends.get(row.i) != row.end
    ]


def ko_table_dates(ctx: IssuerContext) -> CheckResult:
    """每期 KO 表每列結束日 = 配息表同一期（核對規則 §3.5）；期別本身對不上在範本辨識就轉人工。"""
    f = ctx.ts.f
    table, coupon = f("ko_table"), f("coupon_table")
    issues = _date_issues(table, coupon) if table.ok and coupon.ok else []
    return kit.structure_check(
        ctx,
        "doc.ko_table_dates",
        "ko_table",
        "第 9 項 (1)(b) 每期 KO 表的結束日 = 配息表同一期",
        [table, coupon],
        issues,
    )


def run_all(ctx: IssuerContext) -> list[CheckResult]:
    return [*ca.run_all(ctx), ko_table_dates(ctx)]
