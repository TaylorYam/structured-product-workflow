"""CA BEN 專屬規則（docs/checker/rules/ca-ben-check-rules.md）：CA 限定的型態、紅利與參與率的其他出處、沒有 KO、沒有配息、
無 KI、到期公式、情境分析重算。

名稱、ISIN、幣別與名稱月數沿用 CA FCN（rules/ca.py）；參考條件表欄位（含參與率 `field.participation_pct`）、價格推算、
情境價格表與基本假設、審查標準與回填是各上手共用的規則。交股情境的算式和 CA Super Bull 情境三共用
（rules/ca_super_bull.py）。說明書內部規則不讀參考條件表。抓不到或寫法不在範本規格內一律轉人工覆核。
"""

from __future__ import annotations

from decimal import Decimal

from ..parsers.ca_ben import CaBenTermSheet
from ..schema import CheckResult
from ..schema import CheckStatus as S
from . import ca, kit, super_bull
from .ca_super_bull import NAMES, RETURN_MESSAGES, worse
from .kit import Check, IssuerContext
from .super_bull import AMOUNT_TOLERANCE, within_tolerance

# ---------------------------------------------------------------- 待使用者確認的暫定做法（核對規則 §9.2，詳見本機疑慮記錄）
# 每項只在這裡改；說明書端另有 parsers/ca_ben.py 的 OLD_VERSION、DIGITAL（BEN-377-01～03），投資人須知端另有
# parsers/ca_ben_iis.py 的名稱辨識（BEN-377-04）。

# BEN-377-07：有樣本的 Bonus 型態（2 檔標的、K% 100、美元）；其他型態轉人工覆核（待樣本驗證）
SAMPLED_UNDERLYINGS = range(2, 3)
SAMPLED_STRIKE_PCTS = frozenset({Decimal(100)})
SAMPLED_CURRENCIES = frozenset({"USD"})
# BEN-377-05：情境報酬率行重寫的金額 ≠ 到期給付金額 → 不一致（作業人員確認後人工放行）
RETURN_AMOUNT_STATUS = S.MISMATCH

NOT_COVERED = [
    *(x for x in ca.NOT_COVERED if x["rule_id"] not in ("doc.scenario_calculations", "doc.template_variants")),
    {
        "rule_id": "doc.template_variants",
        "description": "只支援現行版 Bonus 範本（KO 價欄版、早期版、Digital 轉人工覆核、不能放行）；境外人民幣、標的報價幣別和"
        "計價幣別不同轉人工覆核；1 檔或 3 檔以上標的、K% 不是 100、非美元轉人工覆核（待樣本驗證）",
    },
    {
        "rule_id": "doc.ben_skipped",
        "description": "第 9 項 (1) 到期開頭句、(2) 定義段與注意段、13.(7)～(9)、15.(1)～(3)、期末訂價日到到期日的天數、情境的"
        "條件句與公式標籤行、文末註記不核對；參考條件表 KO% 與 KO 價不核對（說明書沒有 KO）",
    },
]


def _ts(ctx: IssuerContext) -> CaBenTermSheet:
    ts = ctx.ts
    if not isinstance(ts, CaBenTermSheet):  # 註冊表只把 BEN 讀出結果交給這組規則
        raise TypeError("CA BEN 規則需要 BEN 說明書的讀出結果")
    return ts


def _fixed(ctx: IssuerContext, rid: str, field: str, name: str) -> CheckResult:
    """固定寫法（核對規則 §3.4）：讀到 → 通過；找不到或寫法不同 → 不一致。"""
    return super_bull.fixed(rid, field, name, ctx.ts.f(field), ctx.document)


# ---------------------------------------------------------------- CA 限定的型態（核對規則 §3.1）


def scope(ctx: IssuerContext) -> CheckResult:
    """境外人民幣、標的報價幣別和計價幣別不同（第 16 項有匯率行）轉人工覆核；沒有樣本的型態（標的數、K%、幣別不在
    `SAMPLED_*`）轉人工覆核（待樣本驗證）。"""
    f = ctx.ts.f
    cz, uls, k, fx = f("currency_zh"), f("underlyings"), f("strike_pct"), f("scenario_fx")
    check = kit.doc_check(ctx, "doc.template_variant", "template_variant", "BEN 型態", cz, uls, k, fx)
    if (problem := check.blocked) is not None:
        return problem
    issues, unsampled = [], []
    iso = ctx.std.currency_zh_to_iso.get(cz.value)
    if iso == "CNH":
        issues.append(f"計價幣別是{cz.value}（境外人民幣轉人工覆核）")
    elif iso not in SAMPLED_CURRENCIES:
        unsampled.append(f"計價幣別是{cz.value}")
    if fx.value:
        issues.append(f"第 16 項有匯率 {'、'.join(fx.value)}（標的報價幣別和計價幣別不同，轉人工覆核）")
    if len(uls.value) not in SAMPLED_UNDERLYINGS:
        unsampled.append(f"有 {len(uls.value)} 檔標的")
    if k.value not in SAMPLED_STRIKE_PCTS:
        unsampled.append(f"執行價 {k.value}%")
    if unsampled:
        issues.append("、".join(unsampled) + "：沒有樣本，待樣本驗證")
    return check.compare(
        None,
        None,
        ok=not issues,
        fail=S.REVIEW_REQUIRED,
        reason="template_variant_unverified",
        message="；".join(issues) or "Bonus",
        fail_message="；請人工核對",
    )


# ---------------------------------------------------------------- 紅利、參與率、沒有 KO／配息、無 KI、到期公式（§3.3、§3.4）


def bonus(ctx: IssuerContext) -> list[CheckResult]:
    """紅利的其他出處（基本假設、情境一二兩行「最大值（x%，」）= 第 9 項紅利率，只比數值。"""
    return super_bull.exit_bonus_mentions(
        ctx, _ts(ctx).bonus_mentions, "須等於第 9 項的紅利率（只比數值）", rid="doc.bonus", label="紅利", key="bonus"
    )


def participation(ctx: IssuerContext) -> list[CheckResult]:
    """參與率的其他出處（15.(5)、情境一二「x% x（」）= 第 9 項參與率，只比數值（和參考條件表比是共用的
    `field.participation_pct`）。"""
    return super_bull.exit_bonus_mentions(
        ctx,
        _ts(ctx).participation_mentions,
        "須等於第 9 項的參與率（只比數值）",
        rid="doc.participation",
        label="參與率",
        key="participation",
        main_field="participation_pct",
    )


def no_autocall(ctx: IssuerContext) -> CheckResult:
    """沒有 KO：第 9 項開頭句、全文沒有「提前出場」、第 13 項沒有觀察日條（由 parser 判定；讀不到 → 人工覆核）。"""
    pf = ctx.ts.f("no_autocall")
    check = kit.doc_check(ctx, "doc.no_autocall", "no_autocall", "第 9 項開頭與全文（沒有自動提前出場）", pf)
    return check.compare(None, None, ok=True, message="沒有 KO：不會提前出場，Non-Call = 天期")


def no_ki(ctx: IssuerContext) -> CheckResult:
    """無 KI：全文沒有「界限事件」、價格表沒有下限價欄、到期（1）直接比執行價（由 parser 判定；讀不到 → 人工覆核）。"""
    pf = ctx.ts.f("ki_type")
    check = kit.doc_check(ctx, "doc.ki_consistency", "ki_type", "無 KI（全文、價格表、第 9 項到期（1））", pf)
    return check.compare(None, None, ok=True, message="無 KI")


def no_coupon(ctx: IssuerContext) -> list[CheckResult]:
    return [
        _fixed(ctx, "doc.no_coupon", field, name)
        for field, name in (
            ("no_coupon_art9", "第 9 項 (1) 沒有配息"),
            ("no_coupon_art13", "第 13 項 (6) 配息日"),
            ("no_coupon_art15", "第 15 項 (4) 最低保證配息率"),
        )
    ]


def redemption_formula(ctx: IssuerContext) -> list[CheckResult]:
    return [
        _fixed(ctx, "doc.redemption_formula", field, name)
        for field, name in (
            ("delivery_formula", "第 9 項到期（1）小於執行價交股"),
            ("maturity_formula", "第 9 項到期（2）與 Bonus 公式"),
        )
    ]


# ---------------------------------------------------------------- 情境分析（核對規則 §3.7）


KEYS = (
    ("assumed", "redemption", "return", "annualized"),
    ("assumed", "redemption", "return", "annualized"),
    ("assumed", "shares", "remainder", "return", "annualized"),
)
RID = super_bull.SCENARIO_RID


def scenario_calculations(ctx: IssuerContext) -> list[CheckResult]:
    """情境一（漲幅 > 紅利）、二（漲幅 < 紅利）、三（交股）的算式全部重算；金額、報酬率與年化容許 ±0.01。"""
    ts = _ts(ctx)
    f, sc = ts.f, ts.scenarios
    denom, tenor, bonus_pct, rate, prices = (
        f(x) for x in ("denomination", "tenor_months", "coupon_pa_pct", "participation_pct", "underlying_prices")
    )
    cases = [super_bull.Scenario(n, sc.titles[n - 1], NAMES) for n in (1, 2, 3)]
    bad = kit.first_bad((denom, tenor, bonus_pct, rate, prices))
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
    out = []
    for case in (1, 2):
        out += _bonus_case(cases[case - 1], sc, case, n, bonus_pct.value, rate.value, prices.value, tenor.value)
    return [*out, *worse(cases[2], sc, n, tenor.value)]


def _strike_of(sc, case: int, prices) -> Decimal | None:
    """情境假設的最差標的在價格表的執行價；認不出是哪一檔時為 None。"""
    from ..parsers.ca_super_bull import worst_row

    hit = sc.assumed.get(case)
    row = worst_row(hit.value.name, kit.present(prices)) if hit is not None else None
    return prices[row - 1].prices.get("strike") if row is not None else None


def _bonus_case(
    c: super_bull.Scenario,
    sc,
    case: int,
    n: Decimal,
    bonus_pct: Decimal,
    rate: Decimal,
    prices,
    tenor: int,
) -> list[CheckResult]:
    """情境一、二：假設收盤價 P ≥ 執行價 K；漲幅 r = 參與率 × (P ÷ I − 1)（文件位數），情境一 r > 紅利、情境二 r < 紅利；
    A = 面額 × (100% + 最大值〔紅利, r〕)（2 位）；報酬率行的金額 = A、報酬率 = A ÷ 面額 − 1；年化 = 報酬率 × 12 ÷ 天期。
    I = 價格表那一檔的期初訂價由共用規則 `scenario.strike` 核對；代入的紅利、參與率由 `doc.bonus`、`doc.participation` 核對。"""
    out = []
    assumed, calc, amount, ret = (x.get(case) for x in (sc.assumed, sc.calc, sc.amount, sc.returns))
    gain = None
    if calc is not None:
        v = calc.value
        gain = v.participation * (v.close / v.initial - 1)
    if assumed is None or calc is None:
        out.append(c.unread("assumed", "找不到「假設於期末訂價日…收盤價為P，高於其執行價」或到期給付金額代入行"))
    else:
        p = assumed.value.close
        strike = _strike_of(sc, case, prices)
        issues = []
        if strike is None:
            out.append(c.unread("assumed", "認不出情境寫的最差標的是價格表的哪一檔，無法比對執行價"))
        else:
            if p < strike:
                issues.append(f"假設收盤價 {p} 應不低於執行價 {strike}")
            if calc.value.close != p:
                issues.append(f"算式代入的收盤價 {calc.value.close} 和假設句 {p} 不同")
            if case == 1 and not gain > bonus_pct:
                issues.append(f"漲幅 {gain:.2f}% 應大於紅利 {bonus_pct}%（情境一）")
            if case == 2 and not gain < bonus_pct:
                issues.append(f"漲幅 {gain:.2f}% 應小於紅利 {bonus_pct}%（情境二）")
            out.append(c.calc("assumed", issues, assumed, expected=f"≥ {strike}", actual=p))
    total = None
    if amount is None or gain is None:
        out.append(c.unread("redemption", "找不到「＝N × [100%+最大值（x%，y% x（P / I - 100%))] ＝N × [100%+最大值（x%，r%) = A」"))
    else:
        a = amount.value
        shown_gain = kit.shown(gain, a.gain)
        applied = max(bonus_pct, shown_gain)
        expected = kit.shown(n * (100 + applied) / 100, a.amount)
        issues = [
            f"算式中的面額 {x} 不等於第一章面額 {n}"
            for x in dict.fromkeys((calc.value.denomination, a.denomination))  # type: ignore[union-attr]
            if x != n
        ]
        if a.gain != shown_gain:
            issues.append(f"漲幅 {a.gain}% 應為參與率 × (P / I - 100%) = {shown_gain}%")
        if not within_tolerance(a.amount, expected):
            issues.append(f"到期給付金額 {a.amount} 應為 {n} × (100% + {applied}%) = {expected}")
        total = a.amount
        out.append(c.calc("redemption", issues, amount, expected=expected, actual=a.amount))
    if ret is None:
        out += [c.unread(key, "找不到「＝A / N ) - 100% ＝r%（平均年化報酬率：y%）」") for key in ("return", "annualized")]
        return out
    r = ret.value
    expected, issues = super_bull.profit_return_issues(r, None, n, RETURN_MESSAGES, tolerance=AMOUNT_TOLERANCE)
    out.append(c.calc("return", issues, ret, expected=expected, actual=r.pct))
    if total is not None and r.amount != total:
        out.append(
            c.calc("return", [RETURN_MESSAGES.amount.format(got=r.amount, want=total)], ret, expected=total, actual=r.amount)
            if RETURN_AMOUNT_STATUS == S.MISMATCH
            else c.unread("return", RETURN_MESSAGES.amount.format(got=r.amount, want=total))
        )
    annualized = kit.shown(r.pct * 12 / tenor, r.annualized)
    issues = (
        []
        if within_tolerance(r.annualized, annualized)
        else [f"年化報酬率 {r.annualized}% 應為 {r.pct}% × 12 ÷ {tenor} = {annualized}%"]
    )
    out.append(c.calc("annualized", issues, ret, expected=annualized, actual=r.annualized))
    return out


def run_all(ctx: IssuerContext) -> list[CheckResult]:
    """說明書內部規則：只用讀出結果與審查標準（不讀參考條件表）。"""
    return [
        scope(ctx),
        *ca.document_info(ctx),
        *ca.periods(ctx),
        no_autocall(ctx),
        no_ki(ctx),
        *no_coupon(ctx),
        *bonus(ctx),
        *participation(ctx),
        *redemption_formula(ctx),
        *scenario_calculations(ctx),
    ]
