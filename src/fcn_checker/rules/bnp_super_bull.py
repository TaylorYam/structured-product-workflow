"""BNP Super Bull 專屬規則（docs/checker/rules/bnp-super-bull-check-rules.md）：型態範圍、出場紅利、日期表月數、
沒有配息、到期公式、情境分析。

文件資訊、名稱月數、KO 觀察方式與記憶式、基本公開說明書日期、頁首總頁數沿用 BNP FCN（rules/bnp.py）；參考條件表欄位、
審查標準與回填是各上手共用的規則。說明書內部規則不讀參考條件表。抓不到或寫法不在範本規格內一律轉人工覆核。
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from ..parsers.bnp_super_bull import WORDS, SuperBullTermSheet
from ..schema import CheckResult, Item
from ..schema import CheckStatus as S
from . import bnp, bnp_scenario, kit, super_bull
from .bnp_scenario import _Case, _worse
from .kit import Check, IssuerContext
from .super_bull import within_tolerance

RETURN_MESSAGES = super_bull.ReturnMessages(
    "總報酬率算式的贖回金額 {got} 與贖回金額算式 {want} 不同",
    "總報酬率算式的總投資金額 {got} 不等於第一章面額 {denomination}",
    "總報酬率 {pct}% 應為 ({amount} ÷ {divisor}) − 1 = {expected}%",
    "年化報酬率 {annualized}% 應為 {pct}% × 12 ÷ {months} = {expected}%（四捨五入到文件位數）",
)

NOT_COVERED = [
    *(x for x in bnp.NOT_COVERED if x["rule_id"] != "doc.template_variants"),
    {
        "rule_id": "doc.template_variants",
        "description": "只支援 2026-06 起的現行版範本（舊版轉人工覆核）；非美元計價、單一標的、KO% ≠ K% 現行版沒有樣本，"
        "轉人工覆核；2 檔標的、其他天期與評價日月數依規則推得、待樣本驗證",
    },
    {
        "rule_id": "doc.super_bull_skipped",
        "description": "第一章第 14 項 (8) 以後的營業日等定義、自動提前贖回日的日期本身（只檢查晚於同列評價日）、"
        "情境四末句「除本商品之配息金額外…」、情境三四的年化報酬率不核對",
    },
]


def _ts(ctx: IssuerContext) -> SuperBullTermSheet:
    ts = ctx.ts
    if not isinstance(ts, SuperBullTermSheet):  # 註冊表只把 Super Bull 讀出結果交給這組規則
        raise TypeError("BNP Super Bull 規則需要 Super Bull 說明書的讀出結果")
    return ts


# ---------------------------------------------------------------- 型態範圍（核對規則 §3.1 BNP 限定）


def scope(ctx: IssuerContext) -> CheckResult:
    """現行版範本沒有樣本的型態轉人工覆核：非美元計價、只有 1 檔標的、KO% ≠ K%（範本規格 §4.3）。"""
    f = ctx.ts.f
    cz, und, ko, k = f("currency_zh"), f("underlyings"), f("ko_pct"), f("strike_pct")
    check = kit.doc_check(ctx, "doc.template_variant", "template_variant", "Super Bull 型態", cz, und, ko, k)
    if (problem := check.blocked) is not None:
        return problem
    issues = []
    if ctx.std.currency_zh_to_iso.get(cz.value) != "USD":
        issues.append(f"計價幣別是{cz.value}（現行版只有美元計價的樣本）")
    if len(und.value) < 2:
        issues.append("只有 1 檔標的（現行版沒有單一標的的樣本）")
    if ko.value != k.value:
        issues.append(f"KO% {ko.value} 不等於 K% {k.value}（BNP Super Bull 樣本都相同）")
    return check.compare(
        None,
        None,
        ok=not issues,
        fail=S.REVIEW_REQUIRED,
        reason="template_variant_unverified",
        message="；".join(issues),
        fail_message="；寫法未經樣本驗證，請人工核對",
    )


# ---------------------------------------------------------------- 出場紅利、沒有配息、到期公式（核對規則 §3.3、§3.5）


def exit_bonus(ctx: IssuerContext) -> list[CheckResult]:
    """出場紅利的其他出處（§18 情境一、第二章交易架構）= 第 16 項 `[100% + X%]` 的 X。"""
    ts = _ts(ctx)
    return super_bull.exit_bonus_mentions(ctx, ts.bonus_mentions, f"須等於{ts.chapter1.label('autocall')}出場紅利")


def no_coupon(ctx: IssuerContext) -> CheckResult:
    f, art_label = ctx.ts.f, ctx.ts.chapter1.label
    pf = f("no_coupon")
    return kit.doc_check(ctx, "doc.no_coupon", "no_coupon", f"{art_label('coupon')}沒有配息", pf).compare(
        None, None, ok=True, message="配息資料及其計算公式：不適用"
    )


def redemption_formula(ctx: IssuerContext) -> list[CheckResult]:
    """到期贖回公式、參與率 100%、最低保證配息率無：固定寫法，找不到 → 不一致（核對規則 §3.5）。"""
    f, art_label = ctx.ts.f, ctx.ts.chapter1.label
    where = art_label("maturity")
    return [
        super_bull.fixed("doc.redemption_formula", field, name, f(field), ctx.document)
        for field, name in (
            ("maturity_formula", f"{where}到期贖回公式"),
            ("participation_rate", f"{where}參與率"),
            ("min_guaranteed_rate", f"{where}最低保證配息率"),
        )
    ]


# ---------------------------------------------------------------- 日期表（核對規則 §3.4）


def observation_months(ctx: IssuerContext) -> CheckResult:
    """每個評價日的月數 m（發行日 + m 個月起 7 個日曆天內、1 ≤ m < 天期、遞增）；Non-Call = 第 1 列的 m。"""
    return super_bull.observation_months(ctx, ctx.ts.chapter1.label("dates"), WORDS)


def observation_order(ctx: IssuerContext) -> CheckResult:
    """同列評價日 < 自動提前贖回日；評價日逐列遞增；末列評價日 < 期末評價日。"""
    f = ctx.ts.f
    table, final = f("date_table"), f("final_valuation_date")
    sb = table.ok and table.value["type"] == "SB"
    rows = [(r["period"], r["pricing"], r["redemption"]) for r in table.value["rows"]] if sb else []
    return super_bull.observation_order(ctx, ctx.ts.chapter1.label("dates"), WORDS, table, final, rows)


# ---------------------------------------------------------------- 情境分析（核對規則 §3.7）


def _q(decimals: int) -> Decimal:
    return Decimal(1).scaleb(-decimals)


def scenario_calculations(ctx: IssuerContext) -> list[CheckResult]:
    """情境一（出場）、情境二（較佳）重算；情境三、四沿用 BNP FCN 的股數、剩餘金額、總報酬率驗算（累積配息為 0）。"""
    ts = _ts(ctx)
    f, rid = ts.f, "doc.scenario_calculations"
    denom, tenor, k, bonus = f("denomination"), f("tenor_months"), f("first_callable_period"), f("coupon_pa_pct")
    out: list[CheckResult] = []
    for s, x in zip(ts.scenarios.scenarios, ts.sb_scenarios, strict=True):
        if s.kind == "extreme":
            continue
        tag = f"情境{s.number}"
        items = {
            "assumed": Item.expected(f"{tag}假設"),
            "redemption": Item.expected(f"{tag}贖回金額"),
            "return": Item.expected(f"{tag}總報酬率"),
            "annualized": Item.expected(f"{tag}年化報酬率"),
            "shares": Item.expected(f"{tag}股數"),
            "remainder": Item.expected(f"{tag}剩餘金額"),
        }
        bad = kit.first_bad((denom, tenor, k, bonus))
        if bad is not None:
            out.extend(Check(rid, f"{tag}_{key}", item, ctx.document).review(bad) for key, item in items.items())
            continue
        case = _Case(rid, tag, s, denom.value, tenor.value, k.value, None)
        if s.kind == "unknown":
            out.append(case.unread("redemption", items["redemption"], f"情境標題「{s.title}」不是已知的種類"))
        elif s.kind == "autocall":
            out.extend(_autocall(case, x, items, bonus.value))
        elif s.kind == "best":
            out.extend(_best(case, x, items))
        else:
            out.append(_worse_assumed(case, items["assumed"]))
            out.extend(_worse(case, items))
    return out


def _annualized(c: _Case, item: Item, pct: Decimal, annualized: Decimal, months: int, x) -> CheckResult:
    expected, issues = super_bull.annualized_issues(pct, annualized, months, RETURN_MESSAGES)
    return kit.calc_result(
        c.rid,
        c.field("annualized"),
        item,
        issues,
        kit.evidence(x.total_return.lines),
        expected=expected,
        actual=annualized,
    )


def _autocall(c: _Case, x, items: dict[str, Item], bonus: Decimal) -> list[CheckResult]:
    """情境一：假設第 1 個評價日；A = 面額 × (100% + X%)；總報酬率 = X%；年化 = X × 12 ÷ 第 1 個評價日月數。"""
    s, out = c.s, []
    if s.assumed is None:
        out.append(c.unread("assumed", items["assumed"], "找不到「假設於第…個自動提前贖回評價日」"))
    else:
        a = s.assumed.value
        ok = a.number == 1 and a.j is None
        out.append(
            kit.calc_result(
                c.rid,
                c.field("assumed"),
                items["assumed"],
                [] if ok else ["情境一假設第 1 個自動提前贖回評價日"],
                kit.evidence(s.assumed.lines),
                expected="第1個",
                actual=f"第{a.number}個" if a.j is None else f"第{a.number}(j={a.j})個",
            )
        )
    if x.bonus is None:
        out.append(c.unread("redemption", items["redemption"], "找不到自動提前贖回金額算式（面額 × [100% + X%]）"))
        return out
    b = x.bonus.value
    expected = (Decimal(c.denom) * (100 + bonus) / 100).quantize(Decimal("0.01"), ROUND_HALF_UP)
    issues = []
    if b.denomination != c.denom:
        issues.append(f"算式中的面額 {b.denomination} 不等於第一章面額 {c.denom}")
    if not within_tolerance(b.amount, expected):
        issues.append(f"自動提前贖回金額 {b.amount} 應為 {c.denom} × (100% + {bonus}%) = {expected}")
    out.append(
        kit.calc_result(
            c.rid,
            c.field("redemption"),
            items["redemption"],
            issues,
            kit.evidence(x.bonus.lines),
            expected=expected,
            actual=b.amount,
        )
    )
    out.extend(_profit_return(c, x, items, b.amount, c.k))
    return out


def _best(c: _Case, x, items: dict[str, Item]) -> list[CheckResult]:
    """情境二：假設收盤價 ≥ 執行價；A = 面額 × 收盤價 ÷ 執行價（2 位、容許 ±0.01）；總報酬率 = A ÷ 面額 − 1；
    年化 = 總報酬率 × 12 ÷ 天期。執行價 = 標的表由共用規則 `scenario.strike` 核對。"""
    out = []
    if x.best_assumed is None:
        out.append(c.unread("assumed", items["assumed"], "找不到「收盤價格…大於其執行價格…」假設句"))
        return out
    a = x.best_assumed.value
    out.append(
        kit.calc_result(
            c.rid,
            c.field("assumed"),
            items["assumed"],
            [] if a.close >= a.strike else [f"假設收盤價 {a.close} 應不低於執行價格 {a.strike}"],
            kit.evidence(x.best_assumed.lines),
            expected=f"≥ {a.strike}",
            actual=a.close,
        )
    )
    if x.best is None:
        out.append(c.unread("redemption", items["redemption"], "找不到到期贖回金額算式（面額 × [100% + 100% × (…)]）"))
        return out
    b = x.best.value
    expected = (b.denomination * a.close / a.strike).quantize(_q(b.decimals), ROUND_HALF_UP)
    issues = []
    if b.denomination != c.denom:
        issues.append(f"算式中的面額 {b.denomination} 不等於第一章面額 {c.denom}")
    if not within_tolerance(b.amount, expected):
        issues.append(f"到期贖回金額 {b.amount} 應為 {b.denomination} × {a.close} ÷ {a.strike} = {expected}")
    out.append(
        kit.calc_result(
            c.rid,
            c.field("redemption"),
            items["redemption"],
            issues,
            kit.evidence(x.best.lines),
            expected=expected,
            actual=b.amount,
        )
    )
    out.extend(_profit_return(c, x, items, b.amount, c.tenor))
    return out


def _profit_return(c: _Case, x, items: dict[str, Item], amount: Decimal, months: int) -> list[CheckResult]:
    """獲利情境總報酬率 = (A ÷ 面額) − 1（文件位數）、年化報酬率 = 總報酬率 × 12 ÷ months（文件位數）。"""
    if x.total_return is None:
        return [c.unread("return", items["return"], "找不到總報酬率算式")]
    tr = x.total_return.value
    # BNP 總報酬率照文件位數重算，不容許進位差（核對規則 §3.7）
    expected, issues = super_bull.profit_return_issues(tr, amount, c.denom, RETURN_MESSAGES, tolerance=Decimal(0))
    return [
        kit.calc_result(
            c.rid,
            c.field("return"),
            items["return"],
            issues,
            kit.evidence(x.total_return.lines),
            expected=expected,
            actual=tr.pct,
        ),
        _annualized(c, items["annualized"], tr.pct, tr.annualized, months, x),
    ]


def _worse_assumed(c: _Case, item: Item) -> CheckResult:
    """較差／最差情境假設收盤價 < 執行價（執行價 = 標的表由共用規則 `scenario.strike` 核對）。"""
    s = c.s
    if s.strike is None:
        return c.unread("assumed", item, "找不到「收盤價格…小於其執行價格…」假設句")
    a = s.strike.value
    return kit.calc_result(
        c.rid,
        c.field("assumed"),
        item,
        [] if a.close < a.strike else [f"假設收盤價 {a.close} 應低於執行價格 {a.strike}"],
        kit.evidence(s.strike.lines),
        expected=f"< {a.strike}",
        actual=a.close,
    )


def run_all(ctx: IssuerContext) -> list[CheckResult]:
    """說明書內部規則：只用讀出結果與審查標準（不讀參考條件表）。"""
    return [
        scope(ctx),
        *bnp.document_info(ctx),
        *bnp.periods(ctx),
        *bnp.ko_terms(ctx),
        no_coupon(ctx),
        *exit_bonus(ctx),
        *redemption_formula(ctx),
        observation_months(ctx),
        observation_order(ctx),
        bnp.base_prospectus(ctx),
        bnp.page_count(ctx),
        *bnp_scenario.titles(ctx),
        *scenario_calculations(ctx),
    ]
