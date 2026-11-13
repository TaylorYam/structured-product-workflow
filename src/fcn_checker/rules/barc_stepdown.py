"""BARC FCN_Stepdown 說明書內部規則（docs/checker/rules/barc-stepdown-check-rules.md，workflow #544）。

沿用 BARC FCN 的說明書內部規則（`rules/barc.py`），只換掉「每期觸發百分比 = §15 定義句」：Stepdown 沒有定義句，每期 KO%
固定往下降由範本辨識確認，和參考條件表逐期比由共用 `field.ko_levels` 負責。
"""

from __future__ import annotations

from ..schema import CheckResult, Item
from ..schema import CheckStatus as S
from . import barc
from .kit import Check, IssuerContext

NOT_COVERED = barc.NOT_COVERED


def trigger_steps(ctx: IssuerContext) -> CheckResult:
    """§13(7) 每期觸發百分比：Stepdown 每期不同、沒有 §15 定義句可比，不適用（每期 KO% 見共用 `field.ko_levels`）。"""
    ko = ctx.ts.f("ko_levels")
    check = Check("doc.autocall_trigger_per_period", "trigger", Item.expected("提前出場觸發價格百分比"), ctx.document)
    return check.result(
        S.NOT_APPLICABLE,
        evidence=ko.evidence,
        message="KO% 每期固定往下降（Stepdown）：第 15 條沒有觸發價格定義句；每期 KO% 和參考條件表逐期比（field.ko_levels）",
    )


def run_all(ctx: IssuerContext) -> list[CheckResult]:
    return barc.run_all(ctx, trigger=trigger_steps)
