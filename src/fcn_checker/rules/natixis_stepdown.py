"""Natixis FCN_Stepdown 說明書內部規則（docs/checker/rules/natixis-stepdown-check-rules.md，workflow #545）。

沿用 Natixis FCN 的說明書內部規則（`rules/natixis.py`；第 9 項沒有 KO 欄，表頭 % 不產生 KO 那一項），另加第 14 項每期 KO 表
每列日期 = 第 12 項同一期（`doc.ko_table_dates`）。每期 KO% 固定往下降、期別對齊由範本辨識確認，和參考條件表逐期比由共用
`field.ko_levels` 負責。
"""

from __future__ import annotations

from ..schema import CheckResult
from . import kit, natixis
from .kit import IssuerContext

ISSUER = natixis.ISSUER
REFERENCE_FIELDS = natixis.REFERENCE_FIELDS
NOT_COVERED = [
    *(x for x in natixis.NOT_COVERED if x["rule_id"] != "doc.template_variants"),
    {
        "rule_id": "doc.template_variants",
        "description": "依規則推得、待樣本驗證：Non-Call ≥ 2（每期 KO 表從第 Non-Call 期起排）、P 型現行版面、D 型早期版面、"
        "KI 每日／每期觀察、單一標的；非記憶式、KO% 不是每期固定往下降、第 14 項仍有固定 % 定義句、第 9 項有 KO 欄、每期 KO 表"
        "期別和第 12 項對不上 → 範本不支援",
    },
]


def _date_issues(table, dates) -> list[str]:
    """每期 KO 表每列的日期和第 12 項同一期不同的說明（D：開始日、結束日；P：自動提前到期觀察日）。"""
    value = dates.value
    if value["kind"] == "D":
        by = {r["period"]: (r["start"], r["end"]) for r in value["rows"]}
        what = "開始日、結束日"
    else:
        by = {r["t"]: (r["observation"],) for r in value["ko_rows"]}
        what = "自動提前到期觀察日"
    return [
        f"第 14 項每期 KO 表第 {row.t} 期的{what}和第 12 項不同"
        for row in table.value.rows
        if tuple(by.get(row.t, ())) != tuple(row.dates)
    ]


def ko_table_dates(ctx: IssuerContext) -> CheckResult:
    """第 14 項每期 KO 表每列日期 = 第 12 項同一期（核對規則 §3.5）；期別本身對不上在範本辨識就轉人工。"""
    f = ctx.ts.f
    table, dates = f("ko_table"), f("date_table")
    issues = _date_issues(table, dates) if table.ok and dates.ok else []
    return kit.structure_check(
        ctx, "doc.ko_table_dates", "ko_table", "第 14 項每期 KO 表的日期 = 第 12 項同一期", [table, dates], issues
    )


def run_all(ctx: IssuerContext) -> list[CheckResult]:
    return [*natixis.run_all(ctx), ko_table_dates(ctx)]
