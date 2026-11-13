"""MS 第一章第 18 項情境分析（核對規則 §3.7）：情境標題、各情境金額。

MS 的總配息是「已進位的每期配息 × 期數」（文件算式 `A 美元×k`），與 HSBC 用未進位年利率推算不同。
重印價格表、假設的總投資金額與年期、各處月配息率、獲利情境年化報酬率與較差情境執行價由共用情境規則（rules/scenario.py，
Issue #294）核對；每期配息的預期值用參考條件表年利率推得的月配息率（§3.7）。
情境標題、期數寫法未知或算式找不到時轉人工覆核，不跳過後宣稱通過。
DACN（MS DACN 核對規則 §3.7，`accrual=True`）：有區間計息算式 `×（n/M）` 的情境核固定配息那一期的金額，並用文件寫的
n、M 驗算區間計息每期配息與損益加總（workflow #578；n、M 與配息次數本身不核，年化報酬率不核）；沒有區間計息的情境照 FCN。
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ..schema import CheckResult, Item
from . import dacn, kit
from .kit import Check, IssuerContext


def titles(ctx: IssuerContext) -> list[CheckResult]:
    """情境標題要有獲利情境與一個較差情境（假設的總投資金額與年期由共用情境規則 `scenario.params` 核對）。"""
    sec = ctx.ts.scenarios
    kinds = [s.kind for s in sec.scenarios]
    issues = (
        [] if kinds.count("worse") == 1 and "profit" in kinds else ["找不到獲利情境與一個較差情境（標題含「較差」）"]
    )
    return [
        kit.calc_result(
            "doc.scenario_parameters",
            "scenario_titles",
            "第 18 項情境標題",
            issues,
            kit.evidence(sec.lines[:1]),
            review=True,
            actual=kinds,
        )
    ]


def calculations(ctx: IssuerContext, *, accrual: bool = False) -> list[CheckResult]:
    """各情境：每期配息 = 面額 × 月配息率（年利率 ÷ 12，四捨五入到 4 位），half-up 到 2 位；
    獲利情境損益 = 每期配息 × 假設的配息次數；較差情境總配息 = 每期配息 × 天期，
    損益 = 總配息 + 文件寫的到期贖回價值 − 面額。每期配息算式要全部讀得出來，讀不出來的轉人工覆核。
    `accrual`（DACN）時區間計息算式不算每期配息算式，有區間計息的情境另驗算區間計息每期配息、損益照 `_accrual_pnl`。"""
    f, rid = ctx.ts.f, "doc.scenario_calculations"
    denom, tenor = f("denomination"), f("tenor_months")
    annual, ov, problem = kit.annual_rate(ctx, rid, "scenario_calculations")
    if problem:
        return [problem]
    out = []
    for s in ctx.ts.scenarios.scenarios:
        if s.kind == "default":
            continue
        tag = f"情境{s.number}"
        coupon_item, pnl_item = Item.derived(f"{tag}每期配息", [ov]), Item.derived(f"{tag}損益", [ov])
        accrual_item = Item.derived(f"{tag}區間計息每期配息", [ov]) if accrual and s.accrual else None
        bad = next((p for p in (denom, tenor) if not p.ok), None)
        if bad is not None:
            fields = [(f"{tag}_coupon", coupon_item), (f"{tag}_accrual", accrual_item), (f"{tag}_pnl", pnl_item)]
            out.extend(
                Check(rid, field, item, ctx.document, ov=(ov,)).review(bad)
                for field, item in fields
                if item is not None
            )
            continue
        monthly = kit.monthly_of_annual(annual)
        a = (Decimal(denom.value) * monthly / 100).quantize(kit.Q2, ROUND_HALF_UP)
        issues = []
        for h in s.coupons:
            c = h.value
            if c.denomination != denom.value:
                issues.append(f"算式中的面額 {c.denomination} 不等於第一章面額 {denom.value}")
            if c.amount != (c.denomination * c.rate / 100).quantize(kit.Q2, ROUND_HALF_UP) or c.amount != a:
                issues.append(f"每期配息 {c.amount} 應為 {a}（面額 × 月配息率 {monthly}%，四捨五入到 2 位）")
        evidence = [e for h in s.coupons for e in kit.evidence(h.lines)]
        formulas = s.coupon_formulas - (s.accrual_formulas if accrual else 0)
        if not s.coupons or len(s.coupons) != formulas:
            unread = "找不到每期配息算式" if not s.coupons else "有每期配息算式的寫法無法辨識"
            out.append(
                kit.calc_result(
                    rid, f"{tag}_coupon", coupon_item, [unread], kit.evidence(s.lines[:1]), review=True, ov=[ov]
                )
            )
        else:
            out.append(kit.calc_result(rid, f"{tag}_coupon", coupon_item, issues, evidence, expected=a, ov=[ov]))
        if accrual_item is not None:
            out.extend(_accrual(ctx, rid, s, tag, (accrual_item, pnl_item), ov, a, monthly, denom.value, tenor.value))
        else:
            out.append(_pnl(rid, s, tag, pnl_item, a, denom.value, tenor.value, ov))
    return out


def _accrual(ctx, rid, s, tag, items, ov, a, monthly, denom, tenor) -> list[CheckResult]:
    """DACN 有區間計息的情境（MS DACN 核對規則 §3.7，workflow #578）：用文件寫的 n、M 驗算最後一步的區間計息每期配息與
    損益加總（共用 `dacn.accrual_coupon`、`dacn.coupon_total`）；n、M 與配息次數本身不核，年化報酬率不核。
    區間計息每期配息 B 要等於算式自己的 面額 × X% × n ÷ M，也要等於用第一章面額與月配息率算的值（同固定期配息）。"""
    (item, pnl_item), field = items, f"{tag}_accrual"
    readable = bool(s.accruals) and len(s.accruals) == s.accrual_formulas
    if not readable:
        unread = "找不到最後一步是數字的區間計息算式" if not s.accruals else "有區間計息算式的寫法無法辨識"
        accrual = kit.calc_result(rid, field, item, [unread], kit.evidence(s.lines[:1]), review=True, ov=[ov])
    elif any(h.value.total_days == 0 for h in s.accruals):
        evidence = [e for h in s.accruals if h.value.total_days == 0 for e in kit.evidence(h.lines)]
        accrual = kit.calc_result(rid, field, item, ["區間計息算式的 M 是 0，算不出"], evidence, review=True, ov=[ov])
    else:
        issues, expected, label = [], [], "區間計息每期配息"
        for h in s.accruals:
            c = h.value
            if c.denomination != denom:
                issues.append(f"算式中的面額 {c.denomination} 不等於第一章面額 {denom}")
            own = dacn.accrual_coupon_issues(label, c.amount, c.denomination, c.rate, c.days, c.total_days, kit.Q2)
            issues += own or dacn.accrual_coupon_issues(
                label, c.amount, Decimal(denom), monthly, c.days, c.total_days, kit.Q2
            )
            expected.append(dacn.accrual_coupon(Decimal(denom), monthly, c.days, c.total_days, kit.Q2))
        evidence = [e for h in s.accruals for e in kit.evidence(h.lines)]
        actual = [h.value.amount for h in s.accruals]
        accrual = kit.calc_result(
            rid, field, item, issues, evidence, expected=_one_or_all(expected), actual=_one_or_all(actual), ov=[ov]
        )
    return [accrual, _accrual_pnl(ctx, rid, s, tag, pnl_item, ov, a, denom, tenor, readable)]


def _one_or_all(values: list) -> Any:
    return values[0] if len(values) == 1 else tuple(values)


def _accrual_pnl(ctx, rid, s, tag, item, ov, a, denom, tenor, readable: bool) -> CheckResult:
    """損益算式 `= 項+項+…-面額 = 損益`：獲利情境第一項是拿回的本金、較差情境最後一項是到期贖回價值（同 FCN 不核金額），
    其餘是配息項——第一項配息 = 每期配息 A × 保證期數 g，其後的區間計息項金額 = 同情境區間計息算式的 B、次數合計 = 配息
    次數 − g（獲利情境取假設段的配息次數、較差情境 = 天期；B 是 0 可以不寫）；損益 = 各項金額 × 次數加總 − 面額。
    區間計息算式讀不出、同一情境兩個以上不同的 B、或區間計息項對不到 B 時轉人工覆核（不猜）。"""
    field = f"{tag}_pnl"
    g = ctx.ts.f("coupon_guaranteed_periods")
    if not g.ok:
        return Check(rid, field, item, ctx.document, ov=(ov,)).review(g)
    review = _accrual_pnl_unknown(s, readable)
    if review is not None:
        return kit.calc_result(rid, field, item, [review], kit.evidence(s.lines[:1]), review=True, ov=[ov])
    h, guaranteed, profit = s.pnl_sums[0], g.value, s.kind == "profit"
    p = h.value
    base, legs = p.split(profit)
    paid, base_name = (s.paid.value, "拿回的本金") if profit else (tenor, "到期贖回價值")
    issues = [f"拿回的本金 {base} 不等於第一章面額 {denom}"] if profit and base != denom else []
    if p.denomination != denom:
        issues.append(f"算式中的面額 {p.denomination} 不等於第一章面額 {denom}")
    if not legs:
        issues.append("損益算式找不到配息項")
    else:
        fixed, rest = legs[0], legs[1:]
        if fixed.amount != a:
            issues.append(f"固定配息項 {fixed.amount} 應為每期配息 {a}")
        if fixed.times != guaranteed:
            issues.append(f"固定配息項的次數 {fixed.times} 應等於保證期數 {guaranteed}")
        count = sum(t.times for t in rest)
        if rest and count != paid - guaranteed:
            issues.append(
                f"區間計息項的次數合計 {count} 應為配息次數 {paid} − 保證期數 {guaranteed} = {paid - guaranteed}"
            )
        b = s.accruals[0].value.amount
        if not rest and paid > guaranteed and b != 0:
            issues.append(f"損益算式少了區間計息配息（本情境的區間計息每期配息是 {b}）")
    coupons = dacn.coupon_total([dacn.CouponLeg(t.amount, t.times) for t in legs])
    expected = base + coupons - p.denomination
    if p.pnl != expected:
        issues.append(f"損益 {p.pnl} 應為{base_name} {base} + 配息合計 {coupons} − 面額 {p.denomination} = {expected}")
    return kit.calc_result(rid, field, item, issues, kit.evidence(h.lines), expected=expected, actual=p.pnl, ov=[ov])


def _accrual_pnl_unknown(s, readable: bool) -> str | None:
    """損益算式沒辦法驗算的原因（轉人工覆核）；可以驗算時 None。"""
    if len(s.pnl_sums) != 1:
        return "找不到損益算式或出現多次"
    if s.kind == "profit" and s.paid is None:
        return "假設的配息次數（「假設在第 k 個配息觀察期間」）找不到或出現多次"
    if not readable:
        return "區間計息算式讀不出最後一步的數字，損益無法驗算"
    amounts = {h.value.amount for h in s.accruals}
    if len(amounts) > 1:
        return "同一情境有兩個以上不同的區間計息每期配息，無法判斷損益裡是哪一個"
    _, legs = s.pnl_sums[0].value.split(s.kind == "profit")
    missing = [t.amount for t in legs[1:] if t.amount not in amounts]
    if missing:
        return f"損益裡的區間計息項 {missing[0]} 對不到本情境的區間計息算式"
    return None


def _pnl(rid, s, tag, item, a, denom, tenor, ov) -> CheckResult:
    field = f"{tag}_pnl"
    if len(s.pnl) != 1 or s.assumed is None:
        missing = "損益算式" if len(s.pnl) != 1 else "假設的配息次數（「假設在第 k 個…」）"
        return kit.calc_result(
            rid, field, item, [f"找不到{missing}或出現多次"], kit.evidence(s.lines[:1]), review=True, ov=[ov]
        )
    h, assumed = s.pnl[0], s.assumed.value
    p = h.value
    issues = []
    if s.kind == "profit":
        if assumed is None:
            return kit.calc_result(
                rid, field, item, ["獲利情境的假設期數寫法未知"], kit.evidence(s.lines[:1]), review=True, ov=[ov]
            )
        if p.denomination != denom or p.principal != denom:
            issues.append(f"算式中的面額不等於第一章面額 {denom}")
        if p.coupon != a:
            issues.append(f"每期配息 {p.coupon} 應為 {a}")
        if p.periods != assumed:
            issues.append(f"配息次數 {p.periods} 應等於假設的第 {assumed} 期")
        if p.pnl != p.coupon * p.periods:
            issues.append(f"損益 {p.pnl} 應為 {p.coupon} × {p.periods} = {p.coupon * p.periods}")
        expected = a * assumed
    else:
        if assumed is not None:
            return kit.calc_result(
                rid, field, item, ["較差情境的假設期數寫法未知"], kit.evidence(s.lines[:1]), review=True, ov=[ov]
            )
        if p.coupon != a:
            issues.append(f"每期配息 {p.coupon} 應為 {a}")
        if p.periods != tenor:
            issues.append(f"總配息的期數 {p.periods} 應等於天期 {tenor}")
        if p.denomination != denom:
            issues.append(f"算式中的面額 {p.denomination} 不等於第一章面額 {denom}")
        total = p.coupon * p.periods + p.redemption - p.denomination
        if p.pnl != total:
            issues.append(f"損益 {p.pnl} 應為 {p.coupon} × {p.periods} + {p.redemption} − {p.denomination} = {total}")
        expected = a * tenor + p.redemption - denom
    return kit.calc_result(rid, field, item, issues, kit.evidence(h.lines), expected=expected, actual=p.pnl, ov=[ov])


def run(ctx: IssuerContext, *, accrual: bool = False) -> list[CheckResult]:
    return [*titles(ctx), *calculations(ctx, accrual=accrual)]
