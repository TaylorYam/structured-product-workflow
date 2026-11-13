"""Natixis FCN_Stepdown 投資人須知專屬規則（docs/checker/rules/natixis-stepdown-check-rules.md §3.6，workflow #545）。

KO 價格定義只接受參照說明書第一章第 14 項的寫法（Stepdown 每期 KO% 不同，寫固定 % 和說明書矛盾）；「下表所列」與參照說明書
兩句時讀出是歧義、轉人工覆核（待使用者決定）。其他同 Natixis FCN 投資人須知（`natixis_iis.run_all`，只換掉 KO 價格定義的核對）。
"""

from __future__ import annotations

from ..investor_sheet import read_iis
from ..parsers.natixis_iis import KO_DEF_INITIAL
from ..schema import CheckResult, Item
from . import natixis_iis
from .iis import IisIssuerContext
from .kit import Check


def ko_definition(ctx: IisIssuerContext) -> CheckResult:
    pf = read_iis(ctx.sheet, "ko_definition")
    check = Check("iis.ko_definition", "ko_definition", Item.expected("KO 價格定義的寫法"), ctx.document).needs(pf)
    return check.compare(
        f"參照說明書的「{KO_DEF_INITIAL}」",
        pf.value,
        ok=pf.value == KO_DEF_INITIAL,
        fail_message="Stepdown 每期 KO% 不同，KO 價格定義應參照說明書第一章第 14 項；寫法不在範本規格內，請上手更正",
    )


def run_all(ctx: IisIssuerContext) -> list[CheckResult]:
    return natixis_iis.run_all(ctx, ko_check=ko_definition)
