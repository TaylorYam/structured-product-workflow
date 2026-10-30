"""CA Super Bull 專屬規則（docs/checker/rules/ca-super-bull-check-rules.md）：CA 限定的型態、出場紅利、觀察日表、沒有配息、
到期公式、情境分析重算。

名稱、ISIN、幣別與名稱月數沿用 CA FCN（rules/ca.py）；參考條件表欄位、價格推算、情境價格表與基本假設、審查標準與回填
是各上手共用的規則。說明書內部規則不讀參考條件表。抓不到或寫法不在範本規格內一律轉人工覆核。
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from ..parsers.ca_super_bull import WORDS, CaSuperBullTermSheet
from ..schema import CheckResult
from ..schema import CheckStatus as S
from . import ca, kit, super_bull
from .kit import Check, IssuerContext
from .super_bull import AMOUNT_TOLERANCE, within_tolerance

RETURN_MESSAGES = super_bull.ReturnMessages(
    "報酬率算式的金額 {got} 和贖回金額算式 {want} 不同",
    "報酬率算式的投資金額 {got} 不等於第一章面額 {denomination}",
    "報酬率 {pct}% 應為 ({amount} / {divisor}) - 1 = {expected}%",
    "年化報酬率 {annualized}% 應為 {pct}% × 12 ÷ {months} = {expected}%",
)

NOT_COVERED = [
    *(x for x in ca.NOT_COVERED if x["rule_id"] not in ("doc.scenario_calculations", "doc.template_variants")),
    {
        "rule_id": "doc.template_variants",
        "description": "只支援現行版範本（舊版轉人工覆核、不能放行）；境外人民幣、標的報價幣別和計價幣別不同、觀察日表各列出場價"
        "或紅利不同轉人工覆核",
    },
    {
        "rule_id": "doc.super_bull_skipped",
        "description": "第 9 項 (2) 定義段、13.(7)～(10)、自動提前出場日的日期本身（只檢查晚於同列觀察日）、觀察日到出場日與期末"
        "訂價日到到期日的天數、情境的條件句與文末註記不核對",
    },
]


def _ts(ctx: IssuerContext) -> CaSuperBullTermSheet:
    ts = ctx.ts
    if not isinstance(ts, CaSuperBullTermSheet):  # 註冊表只把 Super Bull 讀出結果交給這組規則
        raise TypeError("CA Super Bull 規則需要 Super Bull 說明書的讀出結果")
    return ts


# ---------------------------------------------------------------- CA 限定的型態（核對規則 §3.1）


def scope(ctx: IssuerContext) -> CheckResult:
    """境外人民幣、標的報價幣別和計價幣別不同（第 16 項有匯率行）轉人工覆核；觀察日表各列出場價須等於價格表表頭 KO%
    （不同轉人工覆核，各列紅利不同由出場紅利歧義處理）。"""
    f = ctx.ts.f
    cz, table, ko, fx = f("currency_zh"), f("ko_table"), f("ko_pct"), f("scenario_fx")
    check = kit.doc_check(ctx, "doc.template_variant", "template_variant", "Super Bull 型態", cz, table, ko, fx)
    if (problem := check.blocked) is not None:
        return problem
    issues = []
    if ctx.std.currency_zh_to_iso.get(cz.value) == "CNH":
        issues.append(f"計價幣別是{cz.value}（境外人民幣轉人工覆核）")
    if fx.value:
        issues.append(f"第 16 項有匯率 {'、'.join(fx.value)}（標的報價幣別和計價幣別不同，轉人工覆核）")
    rows = [str(n) for n, r in enumerate(table.value, start=1) if r["ko"] != ko.value]
    if rows:
        issues.append(f"觀察日表第 {'、'.join(rows)} 列的自動提前出場價不等於價格表表頭 KO% {ko.value}%")
    return check.compare(
        None,
        None,
        ok=not issues,
        fail=S.REVIEW_REQUIRED,
        reason="template_variant_unverified",
        message="；".join(issues),
        fail_message="；請人工核對",
    )


# ---------------------------------------------------------------- 出場紅利、沒有配息、到期公式（核對規則 §3.3、§3.5）


def exit_bonus(ctx: IssuerContext) -> list[CheckResult]:
    """出場紅利的其他出處（基本假設、情境一）= 觀察日表的紅利；第 9 項 (a) 贖回金額 `x(100%+自動提前出場紅利)`。"""
    return [
        *super_bull.exit_bonus_mentions(ctx, _ts(ctx).bonus_mentions, "須等於第 9 項觀察日表的出場紅利"),
        super_bull.fixed(
            "doc.exit_bonus",
            "ko_redemption_formula",
            "第 9 項 (a) 自動提前出場贖回金額",
            ctx.ts.f("ko_redemption_formula"),
            ctx.document,
        ),
    ]


def no_coupon(ctx: IssuerContext) -> list[CheckResult]:
    return [
        super_bull.fixed("doc.no_coupon", field, name, ctx.ts.f(field), ctx.document)
        for field, name in (
            ("no_coupon_art9", "第 9 項 (1) 沒有配息"),
            ("no_coupon_art13", "第 13 項 (6) 配息日"),
            ("no_coupon_art15", "第 15 項 (4) 最低保證配息率"),
        )
    ]


def redemption_formula(ctx: IssuerContext) -> list[CheckResult]:
    return [
        super_bull.fixed("doc.redemption_formula", field, name, ctx.ts.f(field), ctx.document)
        for field, name in (
            ("maturity_formula", "第 9 項 (b)(ii) 到期贖回公式"),
            ("participation_rate", "第 15 項 (5) 參與率"),
        )
    ]


# ---------------------------------------------------------------- 觀察日表（核對規則 §3.4）


def observation_months(ctx: IssuerContext) -> CheckResult:
    """每個觀察日的月數 m（和發行日 + m 個月差 7 個日曆天內、1 ≤ m < 天期、遞增）；Non-Call = 第 1 列的 m。"""
    return super_bull.observation_months(ctx, "第 9 項", WORDS)


def observation_order(ctx: IssuerContext) -> CheckResult:
    """同列觀察日 < 自動提前出場日；觀察日逐列遞增；末列觀察日 < 期末訂價日。"""
    f = ctx.ts.f
    table, final = f("ko_table"), f("final_valuation_date")
    rows = [(n, r["observation"], r["exit"]) for n, r in enumerate(table.value, start=1)] if table.ok else []
    return super_bull.observation_order(ctx, "第 9 項", WORDS, table, final, rows)


# ---------------------------------------------------------------- 情境分析（核對規則 §3.7）


NAMES = {
    "assumed": "假設",
    "redemption": "贖回金額",
    "shares": "股數",
    "remainder": "剩餘金額",
    "return": "報酬率",
    "annualized": "年化報酬率",
}
KEYS = (
    ("assumed", "redemption", "return", "annualized"),
    ("assumed", "redemption", "return", "annualized"),
    ("assumed", "shares", "remainder", "return", "annualized"),
)


RID = super_bull.SCENARIO_RID


def scenario_calculations(ctx: IssuerContext) -> list[CheckResult]:
    """情境一（出場）、二（到期 ≥ 執行價）、三（到期交股）的算式全部重算；金額與報酬率容許 ±0.01。"""
    ts = _ts(ctx)
    f, sc = ts.f, ts.scenarios
    denom, tenor, k, bonus = f("denomination"), f("tenor_months"), f("first_callable_period"), f("coupon_pa_pct")
    cases = [super_bull.Scenario(n, sc.titles[n - 1], NAMES) for n in (1, 2, 3)]
    bad = kit.first_bad((denom, tenor, k, bonus))
    if bad is not None:
        return [
            Check(RID, f"{c.tag}_{key}", c.item(key), ctx.document).review(bad)
            for c, keys in zip(cases, KEYS, strict=True)
            for key in keys
        ]
    if not sc.found:
        why = "第 16 項的情境不是剛好「情境分析一：較佳情況」「情境分析二：一般情況」「情境分析三：較差情況」三個"
        return [c.unread(key, why) for c, keys in zip(cases, KEYS, strict=True) for key in keys]
    n = Decimal(denom.value)
    return [
        *_autocall(cases[0], sc, n, bonus.value, k.value),
        *_best(cases[1], sc, n, tenor.value),
        *worse(cases[2], sc, n, tenor.value),
    ]


def _annualized(c: super_bull.Scenario, hit, pct: Decimal, annualized: Decimal, months: int) -> CheckResult:
    expected, issues = super_bull.annualized_issues(pct, annualized, months, RETURN_MESSAGES)
    return c.calc("annualized", issues, hit, expected=expected, actual=annualized)


def _profit_return(c: super_bull.Scenario, hit, amount: Decimal | None, n: Decimal) -> CheckResult:
    """獲利情境「(A / N) - 1 = r%」：A = 贖回金額算式、N = 面額、r = A ÷ N − 1（文件位數）。"""
    r = hit.value
    expected, issues = super_bull.profit_return_issues(r, amount, n, RETURN_MESSAGES, tolerance=AMOUNT_TOLERANCE)
    return c.calc("return", issues, hit, expected=expected, actual=r.pct)


def _autocall(c: super_bull.Scenario, sc, n: Decimal, bonus: Decimal, k: int) -> list[CheckResult]:
    """情境一：假設第 1 個觀察日出場；A = 面額 × (100% + 紅利)；報酬率 = A ÷ 面額 − 1；年化 = 紅利 × 12 ÷ 第 1 個觀察日月數。"""
    out = []
    if sc.first is None:
        out.append(c.unread("assumed", "找不到「假設於第1 個自動提前出場觀察日發生自動提前出場事件」"))
    else:
        issues = [] if sc.first.value == 1 else ["情境一要假設第 1 個自動提前出場觀察日"]
        out.append(c.calc("assumed", issues, sc.first, expected="第1個", actual=f"第{sc.first.value}個"))
    amount = None
    if sc.autocall is None:
        out.append(c.unread("redemption", "找不到「每單位商品面額 × (100%+自動提前出場紅利) = N × (100% + x%) = A」"))
    else:
        a = sc.autocall.value
        amount = a.amount
        expected = kit.shown(n * (100 + bonus) / 100, a.amount)
        issues = [] if a.denomination == n else [f"算式中的面額 {a.denomination} 不等於第一章面額 {n}"]
        if not within_tolerance(a.amount, expected):
            issues.append(f"自動提前出場贖回金額 {a.amount} 應為 {n} × (100% + {bonus}%) = {expected}")
        out.append(c.calc("redemption", issues, sc.autocall, expected=expected, actual=a.amount))
    if sc.autocall_return is None:
        out += [c.unread(key, "找不到總報酬率算式") for key in ("return", "annualized")]
        return out
    out.append(_profit_return(c, sc.autocall_return, amount, n))
    out.append(_annualized(c, sc.autocall_return, bonus, sc.autocall_return.value.annualized, k))
    return out


def _best(c: super_bull.Scenario, sc, n: Decimal, tenor: int) -> list[CheckResult]:
    """情境二：假設收盤價 P ≥ 執行價 K；r = P ÷ K − 1（文件位數）；A = 面額 × (100% + r%)；報酬率 = A ÷ 面額 − 1；
    年化 = 報酬率 × 12 ÷ 天期。
    K = 價格表那一檔的執行價由共用規則 `scenario.strike` 核對。"""
    out = []
    b = sc.best.value if sc.best is not None else None
    if sc.best_assumed is None:
        out.append(c.unread("assumed", "找不到「假設於期末訂價日…收盤價為P」"))
    elif b is not None:
        p = sc.best_assumed.value.close
        issues = [] if p >= b.strike else [f"假設收盤價 {p} 應不低於執行價 {b.strike}"]
        if b.close != p:
            issues.append(f"算式代入的收盤價 {b.close} 和假設句 {p} 不同")
        out.append(c.calc("assumed", issues, sc.best_assumed, expected=f"≥ {b.strike}", actual=p))
    amount = None
    if b is None:
        out.append(c.unread("redemption", "找不到到期給付金額算式（面額 × [100% + 100% x (P / K - 100%)]）"))
        if sc.best_assumed is not None:
            out.append(c.unread("assumed", "找不到到期給付金額算式，無法比對假設收盤價和執行價"))
    else:
        amount = b.amount
        ratio = b.close / b.strike
        issues = [
            f"算式中的面額 {x} 不等於第一章面額 {n}"
            for x in dict.fromkeys((b.denomination, b.denomination_again))
            if x != n
        ]
        pct = kit.shown(ratio * 100 - 100, b.pct)
        if pct != b.pct:
            issues.append(f"(100% + {b.pct}%) 應為 {b.close} / {b.strike} - 100% = {pct}%")
        expected = kit.shown(n * (100 + b.pct) / 100, b.amount)  # 文件用寫出的 r%（已四捨五入）算金額
        if not within_tolerance(b.amount, expected):
            issues.append(f"到期給付金額 {b.amount} 應為 {n} × (100% + {b.pct}%) = {expected}")
        out.append(c.calc("redemption", issues, sc.best, expected=expected, actual=b.amount))
    if sc.best_return is None:
        out += [c.unread(key, "找不到報酬率算式") for key in ("return", "annualized")]
        return out
    r = sc.best_return.value
    out.append(_profit_return(c, sc.best_return, amount, n))
    out.append(_annualized(c, sc.best_return, r.pct, r.annualized, tenor))
    return out


def worse(c: super_bull.Scenario, sc, n: Decimal, tenor: int) -> list[CheckResult]:
    """情境三：假設收盤價 P < 執行價 K；股數 = 面額 ÷ K 無條件去位到 3 位；整數股數、畸零股數；剩餘金額 = 畸零股數 × P；
    報酬率 = (整數股數 × P + 剩餘金額) ÷ 面額 − 1；年化 = 未四捨五入的報酬率 × 12 ÷ 天期。"""
    out = []
    assumed = sc.worse_assumed.value.close if sc.worse_assumed is not None else None
    s = sc.shares.value if sc.shares is not None else None
    if sc.worse_assumed is None:
        out.append(c.unread("assumed", "找不到「假設於期末訂價日…收盤價為P，低於其執行價」"))
    elif s is None:
        out.append(c.unread("assumed", "找不到股數算式，無法比對假設收盤價和執行價"))
    else:
        issues = [] if assumed < s.strike else [f"假設收盤價 {assumed} 應低於執行價 {s.strike}"]
        out.append(c.calc("assumed", issues, sc.worse_assumed, expected=f"< {s.strike}", actual=assumed))
    if s is None:
        out.append(c.unread("shares", "找不到「每單位面額/…執行價* = N / K = S 股」"))
    else:
        expected = (n / s.strike).quantize(Decimal("0.001"), ROUND_DOWN)
        issues = [] if s.denomination == n else [f"算式中的面額 {s.denomination} 不等於第一章面額 {n}"]
        if s.shares != expected:
            issues.append(f"股數 {s.shares} 應為 {n} / {s.strike} 無條件去位到 3 位 = {expected}")
        out.append(c.calc("shares", issues, sc.shares, expected=expected, actual=s.shares))
    shares = s.shares if s is not None else None
    if sc.remainder is None:
        out.append(c.unread("remainder", "找不到「整數股數＝W 股，畸零股數＝F 股，剩餘金額＝P x F＝R」"))
    else:
        rm = sc.remainder.value
        issues = []
        if shares is not None:
            whole = Decimal(int(shares))
            if rm.whole != whole:
                issues.append(f"整數股數 {rm.whole} 應為 {whole}")
            if rm.fraction != shares - whole or rm.fraction_again != rm.fraction:
                issues.append(f"畸零股數 {rm.fraction}、{rm.fraction_again} 應為 {shares} - {whole} = {shares - whole}")
        if assumed is not None and rm.close != assumed:
            issues.append(f"剩餘金額算式的收盤價 {rm.close} 和假設句 {assumed} 不同")
        expected = kit.shown(rm.fraction * rm.close, rm.amount)
        if not within_tolerance(rm.amount, expected):
            issues.append(f"剩餘金額 {rm.amount} 應為 {rm.close} × {rm.fraction} = {expected}")
        out.append(c.calc("remainder", issues, sc.remainder, expected=expected, actual=rm.amount))
    if sc.worse_return is None:
        out += [
            c.unread(key, "找不到「= ((W x P + R) / N) - 1 = r% (平均年化報酬率:y%)」")
            for key in ("return", "annualized")
        ]
        return out
    w = sc.worse_return.value
    issues = []
    if sc.remainder is not None:
        rm = sc.remainder.value
        for what, got, want in (
            ("整數股數", w.whole, rm.whole),
            ("收盤價", w.close, rm.close),
            ("剩餘金額", w.remainder, rm.amount),
        ):
            if got != want:
                issues.append(f"報酬率算式的{what} {got} 和前面的 {want} 不同")
    if w.divisor != n:
        issues.append(f"報酬率算式的投資金額 {w.divisor} 不等於第一章面額 {n}")
    total = (w.whole * w.close + w.remainder) / w.divisor * 100 - 100
    expected = kit.shown(total, w.pct)
    if not within_tolerance(w.pct, expected):
        issues.append(f"報酬率 {w.pct}% 應為 (({w.whole} × {w.close} + {w.remainder}) / {w.divisor}) - 1 = {expected}%")
    out.append(c.calc("return", issues, sc.worse_return, expected=expected, actual=w.pct))
    annualized = kit.shown(total * 12 / tenor, w.annualized)
    issues = (
        []
        if within_tolerance(w.annualized, annualized)
        else [f"年化報酬率 {w.annualized}% 應為未四捨五入的報酬率 × 12 ÷ {tenor} = {annualized}%"]
    )
    out.append(c.calc("annualized", issues, sc.worse_return, expected=annualized, actual=w.annualized))
    return out


def run_all(ctx: IssuerContext) -> list[CheckResult]:
    """說明書內部規則：只用讀出結果與審查標準（不讀參考條件表）。"""
    return [
        scope(ctx),
        *ca.document_info(ctx),
        *ca.periods(ctx),
        *no_coupon(ctx),
        *exit_bonus(ctx),
        *redemption_formula(ctx),
        observation_months(ctx),
        observation_order(ctx),
        *scenario_calculations(ctx),
    ]
