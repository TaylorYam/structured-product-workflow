"""DACN（區間計息）各上手共用的說明書內部規則（各家核對規則 §3.1、§3.5）：沒有樣本的型態轉人工覆核、配息公式的保證期數 =
Non-Call。

各家只宣告「有樣本的型態」（`Sampled`：要看哪幾項、各支援哪些值、依序）與保證期數寫在第幾條；錯訊文字由宣告組出。
配息門檻 % 的兩種來源（執行價 %、價格表欄頭 %）在說明書讀取端（parsers/dacn.py）。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from ..schema import CheckResult
from ..schema import CheckStatus as S
from . import kit
from .kit import IssuerContext

# 型態的一項（Aspect）：讀出結果 → 不在樣本內的描述（依序；讀不到的欄位由各自的規則轉人工，這裡不報）
Aspect = Callable[[IssuerContext], list[str]]


@dataclass(frozen=True)
class Sampled:
    """一家 DACN 有樣本的型態：`checks` 依序檢查、描述依序併成訊息；`summary` 是錯訊括號裡的說明（例：「Nomura DACN 樣本
    只有…」）。"""

    summary: str
    checks: Sequence[Aspect]


def ko(field: str = "ko_wording", kind: Callable[[Any], str] = lambda v: v) -> Aspect:
    """KO 每日觀察＋記憶式：`kind` 把 `field` 的值轉成 `D`／`P`／`none`（無自動提前出場時不看記憶式）。"""

    def check(ctx: IssuerContext) -> list[str]:
        f = ctx.ts.f
        wording, memory = f(field), f("ko_memory")
        t = kind(wording.value) if wording.ok else None
        out = []
        if t is not None and t != "D":
            out.append("KO 每期觀察" if t == "P" else "無自動提前出場")
        if memory.ok and not memory.value and t != "none":
            out.append("非記憶式")
        return out

    return check


def baskets(ctx: IssuerContext) -> list[str]:
    """多標的。"""
    uls = ctx.ts.f("underlyings")
    return ["單一標的"] if uls.ok and len(uls.value) < 2 else []


def ki(supported: tuple[str, ...]) -> Aspect:
    """KI 型態（`kit.KI_LABEL` 的代碼）。"""

    def check(ctx: IssuerContext) -> list[str]:
        k = kit.doc_ki(ctx.ts.f("ki_type"))
        return [f"KI {kit.KI_LABEL[k]}"] if k is not None and k not in supported else []

    return check


def currency(supported: tuple[str, ...], *, quanto: bool = False) -> Aspect:
    """計價幣別（ISO）；`quanto` 另看標的報價幣別和計價幣別相同（表1 每列的幣別）。"""

    def check(ctx: IssuerContext) -> list[str]:
        f = ctx.ts.f
        cz = f("currency_zh")
        out = []
        if cz.ok and ctx.std.currency_zh_to_iso.get(cz.value) not in supported:
            out.append(f"{cz.value}計價")
        if quanto:
            rows = f("underlying_prices")
            if cz.ok and rows.ok and any(r.currency and r.currency != cz.value for r in rows.value):
                out.append("Quanto（標的報價幣別和計價幣別不同）")
        return out

    return check


def guaranteed(supported: tuple[int, ...]) -> Aspect:
    """配息公式保證全額配息的期數。"""

    def check(ctx: IssuerContext) -> list[str]:
        g = ctx.ts.f("guaranteed_periods")
        return [f"保證 {g.value} 期"] if g.ok and g.value not in supported else []

    return check


def non_call(supported: tuple[int, ...]) -> Aspect:
    """第一個可提前出場期。"""

    def check(ctx: IssuerContext) -> list[str]:
        k = ctx.ts.f("first_callable_period")
        return [f"Non-Call {k.value}"] if k.ok and k.value not in supported else []

    return check


def template_variant(ctx: IssuerContext, sampled: Sampled) -> CheckResult:
    """沒有樣本的型態轉人工覆核（核對規則 §3.1）。"""
    issues = [x for check in sampled.checks for x in check(ctx)]
    return kit.doc_check(ctx, "doc.template_variant", "template_variant", "DACN 型態").compare(
        None,
        None,
        ok=not issues,
        fail=S.REVIEW_REQUIRED,
        reason="template_variant_unverified",
        message="、".join(issues),
        fail_message=f"待樣本驗證（{sampled.summary}），請人工核對",
    )


def guaranteed_periods(
    ctx: IssuerContext,
    article: int,
    *,
    source: str = "配息公式保證全額配息",
    name: str = "配息公式保證期數",
    call: str = "提前出場",
) -> CheckResult:
    """保證期數 = 日期表或 KO 起日推得的 Non-Call（核對規則 §3.5）：保證期數寫在第 `article` 項的 `source`（配息公式或
    日期表），`call` 是這家對提前出場的稱呼。"""
    f = ctx.ts.f
    g, k = f("guaranteed_periods"), f("first_callable_period")
    return kit.doc_check(ctx, "doc.guaranteed_periods", "guaranteed_periods", name, k, g).compare(
        k.value,
        g.value,
        evidence=list(g.evidence),
        fail_message=f"第 {article} 項{source}的期數須等於第一個可{call}期（Non-Call）",
    )
