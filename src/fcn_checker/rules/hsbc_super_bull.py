"""HSBC Super Bull 專屬規則（docs/checker/rules/hsbc-super-bull-check-rules.md）：HSBC 限定的型態、出場紅利、提前贖回表、
沒有配息、到期公式、價格表表頭 %、情境分析重算。

名稱與幣別沿用 HSBC FCN（rules/hsbc.py）；參考條件表欄位、價格推算、情境價格表與基本假設、審查標準與回填是各上手共用的
規則。說明書內部規則不讀參考條件表。抓不到或寫法不在範本規格內一律轉人工覆核。
"""

from __future__ import annotations

from dataclasses import replace
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from ..parsers.hsbc_super_bull import TABLE_NAME, WORDS, HsbcSuperBullTermSheet
from ..schema import CheckResult, Item
from ..schema import CheckStatus as S
from . import hsbc, kit, super_bull
from .kit import Check, IssuerContext
from .super_bull import within_tolerance

CENT = Decimal("0.01")
SHARE_TOLERANCE = Decimal("0.0001")  # 情境三股數（4 位）容許的進位差（核對規則 §3.7）

NOT_COVERED = [
    *(x for x in hsbc.NOT_COVERED if x["rule_id"] not in ("doc.scenario_complex", "doc.scenario_other_annualized")),
    {
        "rule_id": "doc.template_variants",
        "description": "只支援現行版範本（寫法不同轉人工覆核、不能放行）；單一標的、非美元、KO% ≠ K%、多個決定日轉人工覆核"
        "（待樣本驗證）",
    },
    {
        "rule_id": "doc.super_bull_skipped",
        "description": "第 11 條(1) 頻率句、(2) 定義段、第 15 條 (4)(7)～(10)、付款日的日期本身（只檢查順序）、決定日到付款日與"
        "最後評價日到到期日的天數、情境的示意股價與條件句、第 18 條重印表的幣別與交易所欄不核對",
    },
]


def _ts(ctx: IssuerContext) -> HsbcSuperBullTermSheet:
    ts = ctx.ts
    if not isinstance(ts, HsbcSuperBullTermSheet):  # 註冊表只把 Super Bull 讀出結果交給這組規則
        raise TypeError("HSBC Super Bull 規則需要 Super Bull 說明書的讀出結果")
    return ts


def _fixed(ctx: IssuerContext, rid: str, field: str, name: str) -> CheckResult:
    """固定寫法（核對規則 §3.3、§3.5）。"""
    return super_bull.fixed(rid, field, name, ctx.ts.f(field), ctx.document)


# ---------------------------------------------------------------- HSBC 限定的型態（核對規則 §3.1）


def scope(ctx: IssuerContext) -> CheckResult:
    """沒有樣本的型態轉人工覆核（待樣本驗證）：單一標的、承作幣別不是美元、KO% ≠ K%、提前贖回表 2 列以上。"""
    f = ctx.ts.f
    uls, cz, ko, strike, table = (f(x) for x in ("underlyings", "currency_zh", "ko_pct", "strike_pct", "ko_table"))
    check = kit.doc_check(
        ctx, "doc.template_variant", "template_variant", "Super Bull 型態", uls, cz, ko, strike, table
    )
    if (problem := check.blocked) is not None:
        return problem
    issues = []
    if len(uls.value) == 1:
        issues.append("只有 1 檔標的")
    if ctx.std.currency_zh_to_iso.get(cz.value) != "USD":
        issues.append(f"計價幣別是{cz.value}")
    if ko.value != strike.value:
        issues.append(f"自動提前贖回價 {ko.value}% 不等於執行價 {strike.value}%")
    if len(table.value) > 1:
        issues.append(f"{TABLE_NAME}有 {len(table.value)} 列（多個決定日）")
    return check.compare(
        None,
        None,
        ok=not issues,
        fail=S.REVIEW_REQUIRED,
        reason="template_variant_unverified",
        message="；".join(issues),
        fail_message="：沒有樣本，待樣本驗證，請人工核對",
    )


# ---------------------------------------------------------------- 價格表表頭（核對規則 §3.5）


def price_headers(ctx: IssuerContext) -> list[CheckResult]:
    """第 12 條 (1) 表頭「(即期初股價的X%)」依欄名對：自動提前贖回價欄 = KO%、執行價欄 = K%（KO 欄在執行價欄前面）。"""
    table = ctx.ts.f("price_table")
    out = []
    for col in ("ko", "strike"):
        pct = ctx.ts.f(col + "_pct")
        header = Check("doc.price_header_pct", col, Item.expected(kit.HEADER_PCT_ITEM[col]), ctx.document)
        header = replace(header, expected=pct.value).needs(table, pct)
        out.append(header.compare(pct.value, table.value.pcts.get(col) if table.ok else None))
    return out


# ---------------------------------------------------------------- 出場紅利、沒有配息、到期公式（核對規則 §3.3、§3.5）


def exit_bonus(ctx: IssuerContext) -> list[CheckResult]:
    """出場紅利的其他出處（提前贖回表贖回價 − 100%、情境假設、情境一）= 第 11 條(1)「自動提前贖回配息為x%」，只比數值；
    第 11 條(1) 贖回金額 `每單位面額 × (100% + 自動提前贖回配息)`。"""
    return [
        *super_bull.exit_bonus_mentions(
            ctx, _ts(ctx).bonus_mentions, "須等於第 11 條(1) 的自動提前贖回配息（只比數值）"
        ),
        _fixed(ctx, "doc.exit_bonus", "ko_redemption_formula", "第 11 條(1) 自動提前贖回金額"),
    ]


def no_coupon(ctx: IssuerContext) -> list[CheckResult]:
    return [
        _fixed(ctx, "doc.no_coupon", field, name)
        for field, name in (
            ("no_coupon_art16", "第 16 條配息資料"),
            ("no_coupon_art17", "第 17 條 (1) 最低保證配息率"),
        )
    ]


def redemption_formula(ctx: IssuerContext) -> list[CheckResult]:
    return [
        _fixed(ctx, "doc.redemption_formula", "maturity_formula", "第 11 條(2) 到期贖回公式"),
        _fixed(ctx, "doc.redemption_formula", "participation_rate", "第 17 條 (2) 參與率"),
    ]


# ---------------------------------------------------------------- 提前贖回表（核對規則 §3.4）


def observation_months(ctx: IssuerContext) -> CheckResult:
    """每個決定日的月數 m（和發行日 + m 個月差 7 個日曆天內、1 ≤ m < 天期、遞增）；Non-Call = 第 1 列的 m。"""
    return super_bull.observation_months(ctx, "第 11 條(1) 提前贖回表", WORDS)


def observation_order(ctx: IssuerContext) -> list[CheckResult]:
    """同列決定日 < 付款日 < 到期日；決定日逐列遞增；末列決定日 < 最後評價日。"""
    f = ctx.ts.f
    table, final, maturity = f("ko_table"), f("final_valuation_date"), f("maturity_date")
    rows = [(n, r["decision"], r["payment"]) for n, r in enumerate(table.value, start=1)] if table.ok else []
    late = [
        f"第 {n} 列付款日 {payment} 沒有早於到期日 {maturity.value}"
        for n, _, payment in rows
        if maturity.ok and not payment < maturity.value
    ]
    return [
        super_bull.observation_order(ctx, TABLE_NAME, WORDS, table, final, rows),
        kit.structure_check(
            ctx, "schedule.observation_order", "payment_order", f"{TABLE_NAME}付款日早於到期日", [table, maturity], late
        ),
    ]


# ---------------------------------------------------------------- 情境分析（核對規則 §3.7）


RID = "doc.scenario_calculations"
NAMES = {
    "fx": "期末匯率",
    "assumed": "期末股價",
    "redemption": "贖回金額",
    "shares": "股數",
    "whole": "交割股數",
    "fraction": "零股數",
    "remainder": "剩餘金額",
    "value": "到期贖回價值",
    "pnl": "損益",
    "annualized": "年化報酬率",
}
KEYS = (
    ("redemption", "pnl", "annualized"),
    ("assumed", "redemption", "pnl", "annualized"),
    ("assumed", "shares", "whole", "fraction", "remainder", "value", "pnl", "annualized"),
)


class _Scenario:
    """一個情境的結果身分：欄位「情境X_<項目>」、項目名稱「情境X<項目>」，找不到算式時證據為情境標題行。"""

    def __init__(self, n: int, title) -> None:
        self.tag = f"情境{'一二三'[n - 1]}"
        self.title = title

    def item(self, key: str) -> Item:
        return Item.expected(f"{self.tag}{NAMES[key]}")

    def unread(self, key: str, why: str) -> CheckResult:
        return kit.calc_result(RID, f"{self.tag}_{key}", self.item(key), [why], kit.evidence(self.title), review=True)

    def calc(self, key: str, issues: list[str], hit, *, expected=None, actual=None) -> CheckResult:
        return kit.calc_result(
            RID, f"{self.tag}_{key}", self.item(key), issues, kit.evidence(hit.lines), expected=expected, actual=actual
        )


def _cents(x: Decimal) -> Decimal:
    return x.quantize(CENT, ROUND_HALF_UP)


def scenario_calculations(ctx: IssuerContext) -> list[CheckResult]:
    """情境一（第 1 個決定日提前贖回）、二（到期 ≥ 執行價）、三（到期 < 執行價交股）的算式全部重算；金額與年化報酬率容許
    ±0.01、股數 ±0.0001；年化報酬率只比數值（整數或 1 位小數不補 0）。"""
    ts = _ts(ctx)
    f, sc = ts.f, ts.sb_scenarios
    denom, tenor, k, bonus, fx = (
        f(x) for x in ("denomination", "tenor_months", "first_callable_period", "coupon_pa_pct", "scenario_fx")
    )
    cases = [_Scenario(n, sc.titles[n - 1]) for n in (1, 2, 3)]
    bad = kit.first_bad((denom, tenor, k, bonus, fx))
    if bad is not None:
        return [
            Check(RID, f"{c.tag}_{key}", c.item(key), ctx.document).review(bad)
            for c, keys in zip(cases, KEYS, strict=True)
            for key in keys
        ]
    fx_check = kit.calc_result(
        RID,
        "情境假設_fx",
        Item.expected("情境假設期末匯率"),
        [] if fx.value == 1 else [f"期末匯率 {fx.value} 應為 1.00（承作幣別與標的同幣別）"],
        list(fx.evidence),
        expected=Decimal("1.00"),
        actual=fx.value,
    )
    if not sc.found:
        why = "第 18 條的情境不是剛好「情境分析一)」（第 1 個決定日提前贖回）、「二)」（到期 ≥ 執行價）、「三)」（低於執行價）三個"
        return [fx_check, *(c.unread(key, why) for c, keys in zip(cases, KEYS, strict=True) for key in keys)]
    n = Decimal(denom.value)
    return [
        fx_check,
        *_autocall(cases[0], sc, n, bonus.value, k.value),
        *_best(cases[1], sc, n, tenor.value),
        *_worse(cases[2], sc, n, fx.value, tenor.value),
    ]


def _pnl(c: _Scenario, hit, amount: Decimal | None, n: Decimal) -> CheckResult:
    """損益「= A - N = 損益」：A = 前面算出的金額、N = 面額、損益 = A − N。"""
    if hit is None:
        return c.unread("pnl", "找不到「… - 每單位面額期初投資金額 = A - N = 損益」")
    p = hit.value
    issues = []
    if amount is not None and p.amount != amount:
        issues.append(f"損益算式的金額 {p.amount} 和前面算出的 {amount} 不同")
    if p.investment != n:
        issues.append(f"損益算式的期初投資金額 {p.investment} 不等於第一章面額 {n}")
    expected = p.amount - p.investment
    if not within_tolerance(p.pnl, expected):
        issues.append(f"損益 {p.pnl} 應為 {p.amount} − {p.investment} = {expected}")
    return c.calc("pnl", issues, hit, expected=expected, actual=p.pnl)


def _annualized(c: _Scenario, hit, pct: Decimal, months: int, how: str) -> CheckResult:
    """年化報酬率 = pct × 12 ÷ months，四捨五入到 2 位後比數值（容許 ±0.01）。"""
    if hit is None:
        return c.unread("annualized", "找不到「平均年化報酬率(以簡單平均年化報酬率之方式計算): y%」")
    expected = _cents(pct * 12 / months)
    issues = [] if within_tolerance(hit.value, expected) else [f"年化報酬率 {hit.value}% 應為 {how} = {expected}%"]
    return c.calc("annualized", issues, hit, expected=expected, actual=hit.value)


def _profit(sc_pnl, n: Decimal) -> Decimal | None:
    return sc_pnl.value.pnl / n * 100 if sc_pnl is not None else None


def _autocall(c: _Scenario, sc, n: Decimal, bonus: Decimal, k: int) -> list[CheckResult]:
    """情境一：A = 面額 × (100% + 紅利)；損益 = A − 面額；年化 = 紅利 × 12 ÷ 第 1 個決定日的月數。"""
    out = []
    amount = None
    if sc.autocall is None:
        out.append(c.unread("redemption", "找不到「每單位面額 × (100% + 自動提前贖回配息) = N × (100% + x%) = A」"))
    else:
        a = sc.autocall.value
        amount = a.amount
        expected = _cents(n * (100 + bonus) / 100)
        issues = [] if a.denomination == n else [f"算式中的面額 {a.denomination} 不等於第一章面額 {n}"]
        if not within_tolerance(a.amount, expected):
            issues.append(f"自動提前贖回金額 {a.amount} 應為 {n} × (100% + {bonus}%) = {expected}")
        out.append(c.calc("redemption", issues, sc.autocall, expected=expected, actual=a.amount))
    out.append(_pnl(c, sc.autocall_pnl, amount, n))
    out.append(_annualized(c, sc.autocall_annualized, bonus, k, f"{bonus}% × 12 ÷ {k}"))
    return out


def _best(c: _Scenario, sc, n: Decimal, tenor: int) -> list[CheckResult]:
    """情境二：期末股價 P ≥ 執行價 K；A = 面額 × P ÷ K（2 位）；損益 = A − 面額；年化 = 損益 ÷ 面額 × 12 ÷ 天期。
    K = 價格表那一檔的執行價由共用規則 `scenario.strike` 核對。"""
    out = []
    amount = None
    if sc.best is None:
        why = "找不到「每單位面額 × (最差表現連結標的期末股價/最差表現連結標的執行價) = N × (P/K) = A」"
        out += [c.unread("assumed", why), c.unread("redemption", why)]
    else:
        b = sc.best.value
        amount = b.amount
        issues = [] if b.close >= b.strike else [f"期末股價 {b.close} 應不低於執行價 {b.strike}"]
        out.append(c.calc("assumed", issues, sc.best, expected=f"≥ {b.strike}", actual=b.close))
        expected = _cents(n * b.close / b.strike)
        issues = [] if b.denomination == n else [f"算式中的面額 {b.denomination} 不等於第一章面額 {n}"]
        if not within_tolerance(b.amount, expected):
            issues.append(f"到期贖回金額 {b.amount} 應為 {n} × ({b.close}/{b.strike}) = {expected}")
        out.append(c.calc("redemption", issues, sc.best, expected=expected, actual=b.amount))
    out.append(_pnl(c, sc.best_pnl, amount, n))
    out.append(_annualized_of_pnl(c, sc.best_annualized, sc.best_pnl, n, tenor))
    return out


def _annualized_of_pnl(c: _Scenario, hit, pnl, n: Decimal, tenor: int) -> CheckResult:
    profit = _profit(pnl, n)
    if profit is None:
        return c.unread("annualized", "找不到損益算式，無法重算年化報酬率")
    return _annualized(c, hit, profit, tenor, f"{pnl.value.pnl} ÷ {n} × 12 ÷ {tenor}")


def _worse(c: _Scenario, sc, n: Decimal, fx: Decimal, tenor: int) -> list[CheckResult]:
    """情境三：期末股價 P < 執行價 K；股數 = 面額 × 匯率 ÷ K（4 位）；交割股數 = 股數無條件捨去；零股數 = 股數 − 交割股數；
    剩餘金額 = P × 零股數 ÷ 匯率（2 位）；到期贖回價值 = P × 交割股數 ÷ 匯率 + 剩餘金額；損益 = 價值 − 面額；
    年化 = 損益 ÷ 面額 × 12 ÷ 天期。"""
    out = []
    s = sc.shares.value if sc.shares is not None else None
    rm = sc.remainder.value if sc.remainder is not None else None
    v = sc.value.value if sc.value is not None else None
    close_price = rm.close if rm is not None else v.close if v is not None else None
    if s is None or close_price is None:
        out.append(c.unread("assumed", "找不到股數或剩餘金額算式，無法比對期末股價和執行價"))
    else:
        issues = [] if close_price < s.strike else [f"期末股價 {close_price} 應低於執行價 {s.strike}"]
        out.append(c.calc("assumed", issues, sc.shares, expected=f"< {s.strike}", actual=close_price))
    if s is None:
        out.append(c.unread("shares", "找不到「每單位面額 × 期末匯率 / 最差表現連結標的執行價 = N × 匯率 / K = S 股」"))
    else:
        expected = (s.denomination * s.fx / s.strike).quantize(SHARE_TOLERANCE, ROUND_HALF_UP)
        issues = [] if s.denomination == n else [f"算式中的面額 {s.denomination} 不等於第一章面額 {n}"]
        if s.fx != fx:
            issues.append(f"算式中的期末匯率 {s.fx} 和情境假設 {fx} 不同")
        if abs(s.shares - expected) > SHARE_TOLERANCE:
            issues.append(f"股數 {s.shares} 應為 {n} × {s.fx} / {s.strike} = {expected}")
        out.append(c.calc("shares", issues, sc.shares, expected=expected, actual=s.shares))
    whole = s.shares.to_integral_value(ROUND_DOWN) if s is not None else None
    if sc.whole is None:
        out.append(c.unread("whole", "找不到「在此例中為W 股」"))
    else:
        issues = (
            []
            if whole is None or sc.whole.value == whole
            else [f"交割股數 {sc.whole.value} 應為股數無條件捨去 = {whole}"]
        )
        out.append(c.calc("whole", issues, sc.whole, expected=whole, actual=sc.whole.value))
    if sc.fraction is None:
        out.append(c.unread("fraction", "找不到「零股數(即F 股)」"))
    else:
        expected = s.shares - whole if s is not None else None
        issues = (
            [] if expected is None or sc.fraction.value == expected else [f"零股數 {sc.fraction.value} 應為 {expected}"]
        )
        out.append(c.calc("fraction", issues, sc.fraction, expected=expected, actual=sc.fraction.value))
    if rm is None:
        out.append(c.unread("remainder", "找不到「期末股價 × 零股數 / 期末匯率 = P × F / 匯率 = R」"))
    else:
        expected = _cents(rm.close * rm.fraction / rm.fx)
        issues = []
        if sc.fraction is not None and rm.fraction != sc.fraction.value:
            issues.append(f"剩餘金額算式的零股數 {rm.fraction} 和前面的 {sc.fraction.value} 不同")
        if rm.fx != fx:
            issues.append(f"剩餘金額算式的期末匯率 {rm.fx} 和情境假設 {fx} 不同")
        if not within_tolerance(rm.amount, expected):
            issues.append(f"剩餘金額 {rm.amount} 應為 {rm.close} × {rm.fraction} / {rm.fx} = {expected}")
        out.append(c.calc("remainder", issues, sc.remainder, expected=expected, actual=rm.amount))
    amount = None
    if v is None:
        out.append(c.unread("value", "找不到「= (P × W / 匯率) + R = V (四捨五入至小數點後第二位)」"))
    else:
        amount = v.amount
        expected = _cents(v.close * v.whole / v.fx + v.remainder)
        issues = []
        for what, got, want in (
            ("期末股價", v.close, rm.close if rm else None),
            ("交割股數", v.whole, sc.whole.value if sc.whole else None),
            ("剩餘金額", v.remainder, rm.amount if rm else None),
        ):
            if want is not None and got != want:
                issues.append(f"到期贖回價值算式的{what} {got} 和前面的 {want} 不同")
        if v.fx != fx:
            issues.append(f"到期贖回價值算式的期末匯率 {v.fx} 和情境假設 {fx} 不同")
        if not within_tolerance(v.amount, expected):
            issues.append(f"到期贖回價值 {v.amount} 應為 {v.close} × {v.whole} / {v.fx} + {v.remainder} = {expected}")
        out.append(c.calc("value", issues, sc.value, expected=expected, actual=v.amount))
    out.append(_pnl(c, sc.worse_pnl, amount, n))
    out.append(_annualized_of_pnl(c, sc.worse_annualized, sc.worse_pnl, n, tenor))
    return out


def run_all(ctx: IssuerContext) -> list[CheckResult]:
    """說明書內部規則：只用讀出結果與審查標準（不讀參考條件表）。"""
    return [
        scope(ctx),
        *price_headers(ctx),
        *hsbc.document_info(ctx),
        *no_coupon(ctx),
        *exit_bonus(ctx),
        *redemption_formula(ctx),
        observation_months(ctx),
        *observation_order(ctx),
        *scenario_calculations(ctx),
    ]
