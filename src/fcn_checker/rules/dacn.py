"""DACN（區間計息）各上手共用的說明書內部規則（各家核對規則 §3.1、§3.4、§3.5）：沒有樣本的型態轉人工覆核、配息公式的
保證期數 = Non-Call、價格表一列的配息門檻價格 = 期初價格 × 配息門檻 %；另有情境試算共用的 n/M 配息算法（路線圖 #575）：
區間計息的每期配息、累積配息 = 各段已進位的每期配息 × 期數的加總。

各家只宣告「有樣本的型態」（`Sampled`：要看哪幾項、各支援哪些值、依序）與保證期數寫在第幾條；錯訊文字由宣告組出。
配息門檻 % 的兩種來源（執行價 %、價格表欄頭 %）在說明書讀取端（parsers/dacn.py）。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal
from typing import Any

from ..investor_sheet import read_iis
from ..schema import CheckResult, Item, ParsedField
from ..schema import CheckStatus as S
from ..standard_fields import PriceRow
from . import kit
from .iis import NO_TERM_SHEET, IisIssuerContext, ts_unavailable
from .kit import Q4, Check, IssuerContext

# 型態的一項（Aspect）：讀出結果 → 不在樣本內的描述（依序；讀不到的欄位由各自的規則轉人工，這裡不報）
Aspect = Callable[[IssuerContext], list[str]]


@dataclass(frozen=True)
class Sampled:
    """一家 DACN 有樣本的型態：`checks` 依序檢查、描述依序併成訊息；`summary` 是錯訊括號裡的說明（例：「Nomura DACN 樣本
    只有…」）。`manual` 是有樣本、但使用者決定維持人工的型態，描述本身就是錯訊、不寫「待樣本驗證」（例：BNP DACN 的
    MKI，`monthly_ki`）。"""

    summary: str
    checks: Sequence[Aspect]
    manual: Sequence[Aspect] = ()


def ko(field: str = "ko_wording", kind: Callable[[Any], str] = lambda v: v, *, memory_only: bool = True) -> Aspect:
    """KO 每日觀察＋記憶式：`kind` 把 `field` 的值轉成 `D`／`P`／`none`（無自動提前出場時不看記憶式）；`memory_only` 為
    False 時非記憶式也有樣本（UBS DACN）。"""

    def check(ctx: IssuerContext) -> list[str]:
        f = ctx.ts.f
        wording, memory = f(field), f("ko_memory")
        t = kind(wording.value) if wording.ok else None
        out = []
        if t is not None and t != "D":
            out.append("KO 每期觀察" if t == "P" else "無自動提前出場")
        if memory_only and memory.ok and not memory.value and t != "none":
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


def monthly_ki(ctx: IssuerContext) -> list[str]:
    """KI 每期觀察（MKI）維持人工（workflow #386／#403；有樣本也不開放，所以不寫「待樣本驗證」）。"""
    return ["每期觀察 KI 維持人工處理（#386）"] if kit.doc_ki(ctx.ts.f("ki_type")) == "P" else []


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


def guaranteed(supported: tuple[int, ...], field: str = "guaranteed_periods") -> Aspect:
    """配息公式保證全額配息的期數（讀出欄位 `field`；BARC 的 `guaranteed_periods` 是提前出場表的保證配息期，配息公式的
    另叫 `coupon_guaranteed_periods`）。"""

    def check(ctx: IssuerContext) -> list[str]:
        g = ctx.ts.f(field)
        return [f"保證 {g.value} 期"] if g.ok and g.value not in supported else []

    return check


def non_call(supported: tuple[int, ...]) -> Aspect:
    """第一個可提前出場期。"""

    def check(ctx: IssuerContext) -> list[str]:
        k = ctx.ts.f("first_callable_period")
        return [f"Non-Call {k.value}"] if k.ok and k.value not in supported else []

    return check


def template_variant(ctx: IssuerContext, sampled: Sampled) -> CheckResult:
    """沒有樣本的型態（待樣本驗證）與維持人工的型態轉人工覆核（核對規則 §3.1）。"""
    issues = [x for check in sampled.checks for x in check(ctx)]
    parts = [f"{'、'.join(issues)}待樣本驗證（{sampled.summary}）"] if issues else []
    parts += [x for check in sampled.manual for x in check(ctx)]
    return kit.doc_check(ctx, "doc.template_variant", "template_variant", "DACN 型態").compare(
        None,
        None,
        ok=not parts,
        fail=S.REVIEW_REQUIRED,
        reason="template_variant_unverified",
        fail_message="；".join(parts) + "，請人工核對",
    )


def guaranteed_periods(
    ctx: IssuerContext,
    article: int | str,
    *,
    source: str = "配息公式保證全額配息",
    name: str = "配息公式保證期數",
    call: str = "提前出場",
    field: str = "guaranteed_periods",
) -> CheckResult:
    """保證期數 = 日期表或 KO 起日推得的 Non-Call（核對規則 §3.5）：保證期數寫在第 `article` 項（條號；文件用其他寫法時
    直接給位置，例 UBS「商品基本資料八」）的 `source`（配息公式或日期表；讀出欄位 `field`），`call` 是這家對提前出場的稱呼。"""
    f = ctx.ts.f
    g, k = f(field), f("first_callable_period")
    return kit.doc_check(ctx, "doc.guaranteed_periods", field, name, k, g).compare(
        k.value,
        g.value,
        evidence=list(g.evidence),
        fail_message=f"{_where(article)}{source}的期數須等於第一個可{call}期（Non-Call）",
    )


def _where(article: int | str) -> str:
    return f"第 {article} 項" if isinstance(article, int) else article


def iis_guaranteed_periods(ctx: IisIssuerContext) -> CheckResult:
    """投資人須知（`guaranteed_periods`）推得的保證期數 = 同商品說明書配息公式的保證期數（`coupon_guaranteed_periods`；
    BNP、UBS、MS DACN 投資人須知共用）。"""
    rid, field, name = "iis.guaranteed_periods", "guaranteed_periods", "配息公式保證期數"
    pf = read_iis(ctx.sheet, field)
    check = Check(rid, field, Item.term_sheet(name), ctx.document).needs(pf)
    if (problem := check.blocked) is not None:
        return problem
    ts = ctx.term_sheet.f("coupon_guaranteed_periods") if ctx.term_sheet is not None else None
    if ts is None or not ts.ok:
        why = NO_TERM_SHEET if ts is None else f"同商品說明書讀不到「{name}」，無法比對"
        return ts_unavailable(rid, field, name, why, pf.value, document=ctx.document)
    return check.compare(ts.value, pf.value, message="須等於同商品說明書配息公式的保證期數")


def barrier_price(
    check: Check, row: PriceRow, pct: ParsedField, missing: str, initial: str = "期初價格"
) -> CheckResult:
    """價格表一列的配息門檻價格（`barrier` 欄）= `initial` × 配息門檻 %，四捨五入（half-up）到 4 位（核對規則 §3.4）；
    該列讀不到配息門檻價格時轉人工覆核（錯訊 `missing`）。說明書與投資人須知共用，各家給比對項目（`check`）與用語。"""
    actual = row.prices.get("barrier")
    if actual is None:
        return check.result(S.REVIEW_REQUIRED, reason="document_missing", message=missing, evidence=list(row.evidence))
    base = row.prices["initial"]
    return check.compare(
        (base * pct.value / 100).quantize(Q4, ROUND_HALF_UP),
        actual,
        evidence=[*row.evidence, *pct.evidence],
        tolerance="四捨五入（half-up）到 4 位",
        message=f"{initial} {base} × {pct.value}%",
    )


# ---------------------------------------------------------------- 情境試算的 n/M 配息（路線圖 #575）

# 文件寫的進位方式 → decimal 進位常數；各家情境的讀取端把文件用字換成常數，錯訊再換回用字
ROUNDING_ZH = {ROUND_HALF_UP: "四捨五入", ROUND_DOWN: "無條件捨去", ROUND_UP: "無條件進位"}


def _places(step: Decimal) -> str:
    exp = step.as_tuple().exponent
    return "整數" if exp == 0 else f"小數點以下 {-exp} 位"


def accrual_coupon(
    denomination: Decimal, rate: Decimal, days: int, total_days: int, step: Decimal, rounding: str = ROUND_HALF_UP
) -> Decimal | None:
    """區間計息的每期配息 = 面額 × 月配息率 `rate`% × (n ÷ M)，依文件寫的位數（`step`，例 0.01）與進位方式（`rounding`）。
    n、M 是文件自己寫的假設天數，本身不核對；M 是 0 時算不出（None）。"""
    if total_days == 0:
        return None
    return (denomination * rate / 100 * days / total_days).quantize(step, rounding)


def accrual_coupon_issues(
    label: str,
    actual: Decimal,
    denomination: Decimal,
    rate: Decimal,
    days: int,
    total_days: int,
    step: Decimal,
    rounding: str = ROUND_HALF_UP,
) -> list[str]:
    """文件寫的區間計息每期配息 `actual` 和 `accrual_coupon` 不同時的錯訊（相同時空清單）。"""
    expected = accrual_coupon(denomination, rate, days, total_days, step, rounding)
    if expected == actual:
        return []
    formula = f"{denomination} × {rate}% × ({days} ÷ {total_days})"
    if expected is None:
        return [f"{label} {actual} 的算式 {formula} 的 M 是 0，算不出"]
    return [f"{label} {actual} 應為 {formula} = {expected}（{ROUNDING_ZH[rounding]}到{_places(step)}）"]


@dataclass(frozen=True)
class CouponLeg:
    """累積配息的一段：文件寫的（已進位的）每期配息 × 期數，例：第 1～g 期、第 g+1 期到天期。"""

    amount: Decimal
    periods: int


def coupon_total(legs: Sequence[CouponLeg]) -> Decimal:
    """累積（總）配息 = 各段已進位的每期配息 × 期數的加總（同各家 FCN 的累積配息算法）。"""
    return sum((leg.amount * leg.periods for leg in legs), Decimal(0))


def coupon_total_issues(label: str, actual: Decimal, legs: Sequence[CouponLeg]) -> list[str]:
    """文件寫的累積配息 `actual` 和 `coupon_total` 不同時的錯訊（相同時空清單）。"""
    expected = coupon_total(legs)
    if expected == actual:
        return []
    parts = " + ".join(f"{leg.amount} × {leg.periods} 期" for leg in legs)
    return [f"{label} {actual} 應為 {parts} = {expected}"]
