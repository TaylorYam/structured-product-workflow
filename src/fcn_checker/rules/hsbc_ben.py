"""HSBC BEN 專屬規則（docs/checker/rules/hsbc-ben-check-rules.md）：HSBC 限定的型態、紅利、沒有 KO、沒有定期配息、無 KI、到期公式、
價格表表頭 %、殘留註記、(最終版)、情境分析重算。

名稱與幣別沿用 HSBC FCN（rules/hsbc.py）；參考條件表欄位（含參與率 `field.participation_pct`）、價格推算、情境價格表與基本假設、
審查標準與回填是各上手共用的規則。交股情境的算式和 HSBC Super Bull 情境三共用（rules/hsbc_super_bull.py）。說明書內部規則
不讀參考條件表。抓不到或寫法不在範本規格內一律轉人工覆核。
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from ..parsers.hsbc_ben import KIND_LABEL, HsbcBenTermSheet, Kind
from ..schema import CheckResult, Item
from ..schema import CheckStatus as S
from . import hsbc, kit, super_bull
from .hsbc_super_bull import NAMES, annualized_of_pnl, cents, pnl_check, worse
from .kit import Check, IssuerContext
from .super_bull import within_tolerance

# ---------------------------------------------------------------- 待使用者確認的暫定做法（核對規則 §9，詳見本機疑慮記錄）
# 每項只在這裡改；說明書端另有 parsers/hsbc_ben.py 的 PARTICIPATION、ART16、BONUS、OLD_VERSION（見該檔）。

# BEN-375-03：有樣本的標的數與幣別；其他型態轉人工覆核（待樣本驗證）
SAMPLED_UNDERLYINGS: dict[Kind, range] = {"maxi": range(2, 4), "digital": range(1, 2)}
SAMPLED_CURRENCIES = frozenset({"USD"})
# BEN-375-04：Digital 標的表後的方括號殘留註記 → 不一致（作業人員確認後人工放行）
RESIDUE_STATUS = S.MISMATCH
# BEN-375-06：封面（投資人須知標題）沒有「(最終版)」的 Word 排版 → 不一致（作業人員確認後人工放行）
FINAL_EDITION_STATUS = S.MISMATCH

NOT_COVERED = [
    *(x for x in hsbc.NOT_COVERED if x["rule_id"] not in ("doc.scenario_complex", "doc.scenario_other_annualized")),
    {
        "rule_id": "doc.template_variants",
        "description": "只支援現行版範本（Maxi、Digital；寫法不同轉人工覆核、不能放行）；Maxi 1 檔或 4 檔以上標的、Digital 2 檔以上"
        "標的、非美元轉人工覆核（待樣本驗證）",
    },
    {
        "rule_id": "doc.ben_skipped",
        "description": "第 11 條定義段（K% 與 Maxi 紅利的定義句除外）、第 15 條 (4)(7)～(12)、最後評價日到到期日的天數、情境的示意"
        "股價與條件句、第 18 條重印表的幣別與交易所欄、參考條件表 KO% 與 KO 價（說明書沒有 KO）不核對",
    },
]


def _ts(ctx: IssuerContext) -> HsbcBenTermSheet:
    ts = ctx.ts
    if not isinstance(ts, HsbcBenTermSheet):  # 註冊表只把 BEN 讀出結果交給這組規則
        raise TypeError("HSBC BEN 規則需要 BEN 說明書的讀出結果")
    return ts


def _fixed(ctx: IssuerContext, rid: str, field: str, name: str) -> CheckResult:
    """固定寫法（核對規則 §3.3、§3.5）。"""
    return super_bull.fixed(rid, field, name, ctx.ts.f(field), ctx.document)


def _flagged(ctx: IssuerContext, rid: str, field: str, name: str, status: S) -> CheckResult:
    """寫法不在範本規格內但條款可讀（殘留註記、沒有 (最終版)）：沒有 → 通過；有 → `status`（暫定做法，見上方設定）。"""
    pf = ctx.ts.f(field)
    check = Check(rid, field, Item.expected(name), ctx.document)
    if pf.ok:
        return check.result(S.PASS, evidence=pf.evidence, message="範本固定寫法")
    return check.result(status, evidence=pf.evidence, reason="value_mismatch", message=pf.note)


# ---------------------------------------------------------------- HSBC 限定的型態（核對規則 §3.1）


def scope(ctx: IssuerContext) -> CheckResult:
    """沒有樣本的型態轉人工覆核（待樣本驗證）：標的數不在 `SAMPLED_UNDERLYINGS`、承作幣別不在 `SAMPLED_CURRENCIES`。"""
    ts = _ts(ctx)
    f = ts.f
    kind, uls, cz = f("ben_kind"), f("underlyings"), f("currency_zh")
    check = kit.doc_check(ctx, "doc.template_variant", "template_variant", "BEN 型態", kind, uls, cz)
    if (problem := check.blocked) is not None:
        return problem
    issues = []
    if len(uls.value) not in SAMPLED_UNDERLYINGS[kind.value]:
        issues.append(f"{KIND_LABEL[kind.value]}有 {len(uls.value)} 檔標的")
    if ctx.std.currency_zh_to_iso.get(cz.value) not in SAMPLED_CURRENCIES:
        issues.append(f"計價幣別是{cz.value}")
    return check.compare(
        None,
        None,
        ok=not issues,
        fail=S.REVIEW_REQUIRED,
        reason="template_variant_unverified",
        message="；".join(issues) or KIND_LABEL[kind.value],
        fail_message="：沒有樣本，待樣本驗證，請人工核對",
    )


# ---------------------------------------------------------------- 價格表表頭（核對規則 §3.5）


def price_headers(ctx: IssuerContext) -> CheckResult:
    """第 12 條 (1) 表頭「執行價(即期初股價的X%)」= 第 11 條 K%（沒有 KO 價欄）。"""
    table, pct = ctx.ts.f("price_table"), ctx.ts.f("strike_pct")
    header = Check("doc.price_header_pct", "strike", Item.expected(kit.HEADER_PCT_ITEM["strike"]), ctx.document)
    header = replace(header, expected=pct.value).needs(table, pct)
    return header.compare(pct.value, table.value.pcts.get("strike") if table.ok else None)


# ---------------------------------------------------------------- 紅利、沒有 KO、沒有定期配息、無 KI、到期公式（§3.3、§3.5）


def bonus(ctx: IssuerContext) -> list[CheckResult]:
    """紅利的其他出處（第 18 條假設、Maxi 情境一二「最大值 [x%」、Digital 情境一「(100% + x%%)」）= 第 11 條紅利，只比數值；
    Digital 第 11 條(2) 配息金額兩句固定寫法。"""
    out = super_bull.exit_bonus_mentions(
        ctx,
        _ts(ctx).bonus_mentions,
        "須等於第 11 條的紅利配息率（只比數值）",
        rid="doc.bonus",
        label="紅利",
        key="bonus",
    )
    if _ts(ctx).kind == "digital":
        out += [
            _fixed(ctx, "doc.bonus", "digital_coupon", "第 11 條(2) 大於或等於執行價的配息金額"),
            _fixed(ctx, "doc.bonus", "digital_no_coupon", "第 11 條(2) 小於執行價沒有紅利"),
        ]
    return out


def no_autocall(ctx: IssuerContext) -> CheckResult:
    """沒有 KO：第 11 條開頭固定句、第 11 條沒有提前贖回、第 15 條沒有自動提前贖回決定日（由 parser 判定；讀不到 → 人工覆核）。"""
    pf = ctx.ts.f("no_autocall")
    check = kit.doc_check(ctx, "doc.no_autocall", "no_autocall", "第 11 條開頭與第 15 條（沒有自動提前贖回）", pf)
    return check.compare(None, None, ok=True, message="沒有 KO：不會提前出場，Non-Call = 天期")


def no_ki(ctx: IssuerContext) -> CheckResult:
    """無 KI：全文沒有「觸及」、價格表沒有觸及不保本價格欄、交股句直接比執行價（由 parser 判定；讀不到 → 人工覆核）。"""
    pf = ctx.ts.f("ki_type")
    check = kit.doc_check(ctx, "doc.ki_consistency", "ki_type", "無 KI（全文、價格表、第 11 條交股句）", pf)
    return check.compare(None, None, ok=True, message="無 KI")


def no_coupon(ctx: IssuerContext) -> list[CheckResult]:
    return [
        _fixed(ctx, "doc.no_coupon", field, name)
        for field, name in (
            ("no_coupon_art16", "第 16 條配息資料"),
            ("no_coupon_art17", "第 17 條 (1) 最低保證配息率"),
        )
    ]


def redemption_formula(ctx: IssuerContext) -> list[CheckResult]:
    """第 11 條(1) 到期公式與交股句、第 17 條 (2) 參與率「本商品不適用」（不當成參與率 0；參與率由公式推得，和參考條件表比是
    共用的 `field.participation_pct`）。"""
    return [
        _fixed(ctx, "doc.redemption_formula", "maturity_formula", "第 11 條(1) 大於或等於執行價的到期公式"),
        _fixed(ctx, "doc.redemption_formula", "delivery_formula", "第 11 條(1) 小於執行價交股"),
        _fixed(ctx, "doc.redemption_formula", "participation_rate", "第 17 條 (2) 參與率"),
    ]


def template_residue(ctx: IssuerContext) -> CheckResult:
    return _flagged(ctx, "doc.template_residue", "template_residue", "第 12 條(1) 標的表後的註記", RESIDUE_STATUS)


def final_edition(ctx: IssuerContext) -> CheckResult:
    return _flagged(ctx, "doc.final_edition", "final_edition", "封面標題「(最終版)」", FINAL_EDITION_STATUS)


# ---------------------------------------------------------------- 情境分析（核對規則 §3.7）


RID = super_bull.SCENARIO_RID
KEYS: dict[Kind, tuple[tuple[str, ...], ...]] = {
    "maxi": (
        ("assumed", "redemption", "pnl", "annualized"),
        ("assumed", "redemption", "pnl", "annualized"),
        ("assumed", "shares", "whole", "fraction", "remainder", "value", "pnl", "annualized"),
    ),
    "digital": (
        ("redemption", "pnl", "annualized"),
        ("assumed", "shares", "whole", "fraction", "remainder", "value", "pnl", "annualized"),
        ("assumed", "shares", "whole", "fraction", "remainder", "value", "pnl", "annualized"),
    ),
}
TITLE_WHY: dict[Kind, str] = {
    "maxi": "「情境分析一)」（漲幅大於紅利）、「二)」（漲幅小於紅利）、「三)」（低於執行價）",
    "digital": "「情境分析一)」（≥ 執行價）、「二)」（< 執行價）、「三)」（期末股價 0）",
}


def scenario_calculations(ctx: IssuerContext) -> list[CheckResult]:
    """情境的算式全部重算：Maxi 情境一、二 = 面額 × (100% + 最大值〔紅利, P ÷ 期初股價 − 1〕)；Digital 情境一 = 面額 ×
    (100% + 紅利)；交股情境同 Super Bull 情境三。金額與年化報酬率容許 ±0.01、股數 ±0.0001；年化只比數值。"""
    ts = _ts(ctx)
    f, sc = ts.f, ts.ben_scenarios
    denom, tenor, main, fx, prices = (
        f(x) for x in ("denomination", "tenor_months", "coupon_pa_pct", "scenario_fx", "underlying_prices")
    )
    keys = KEYS[sc.kind]
    cases = [super_bull.Scenario(n, sc.titles[n - 1], NAMES) for n in (1, 2, 3)]
    bad = kit.first_bad((denom, tenor, main, fx, prices))
    if bad is not None:
        return [
            Check(RID, f"{c.tag}_{key}", c.item(key), ctx.document).review(bad)
            for c, ks in zip(cases, keys, strict=True)
            for key in ks
        ]
    fx_check = kit.calc_result(
        RID,
        "情境假設_fx",
        Item.expected("情境假設期末匯率"),
        [] if fx.value == 1 else [f"期末匯率 {fx.value} 應為 1（承作幣別與標的同幣別）"],
        list(fx.evidence),
        expected=Decimal(1),
        actual=fx.value,
    )
    if not sc.found:
        why = f"第 18 條的情境不是剛好{TITLE_WHY[sc.kind]}三個"
        return [fx_check, *(c.unread(key, why) for c, ks in zip(cases, keys, strict=True) for key in ks)]
    n = Decimal(denom.value)
    out = [fx_check]
    for case in (1, 2, 3):
        c = cases[case - 1]
        if case in sc.delivery_cases:
            out += worse(c, sc.deliveries[case], n, fx.value, tenor.value)
        elif sc.kind == "maxi":
            row = sc.enhanced_rows.get(case)
            strike = prices.value[row - 1].prices.get("strike") if row is not None else None
            out += _enhanced(c, sc, case, n, main.value, strike, tenor.value)
        else:
            out += _digital(c, sc, n, main.value, tenor.value)
    return out


def _enhanced(
    c: super_bull.Scenario, sc, case: int, n: Decimal, bonus_pct: Decimal, strike: Decimal | None, tenor: int
) -> list[CheckResult]:
    """Maxi 情境一（漲幅 > 紅利）、二（漲幅 < 紅利）：P ≥ 執行價 K；A = 面額 × (100% + 最大值〔x%, P ÷ I − 1〕)（2 位）；算式
    第二步寫的 % = 最大值；損益 = A − 面額；年化 = 損益 ÷ 面額 × 12 ÷ 天期。I = 價格表那一檔的期初股價由共用規則
    `scenario.strike` 核對，x = 第 11 條紅利由 `doc.bonus` 核對。"""
    hit = sc.enhanced.get(case)
    out = []
    amount = None
    if hit is None:
        why = "找不到「= N × {100% + 最大值 [x%，(P/I-1)]} = N × {100% + y%} = A」"
        out += [c.unread("assumed", why), c.unread("redemption", why)]
    else:
        e = hit.value
        amount = e.amount
        gain = (e.close / e.initial - 1) * 100
        if strike is None:
            out.append(c.unread("assumed", "認不出情境寫的最差標的是價格表的哪一檔，無法比對執行價"))
        else:
            issues = [] if e.close >= strike else [f"期末股價 {e.close} 應不低於執行價 {strike}"]
            above = case == 1
            if above and not gain > bonus_pct:
                issues.append(f"漲幅 {cents(gain)}% 應大於紅利 {bonus_pct}%（情境一）")
            if not above and not gain < bonus_pct:
                issues.append(f"漲幅 {cents(gain)}% 應小於紅利 {bonus_pct}%（情境二）")
            out.append(c.calc("assumed", issues, hit, expected=f"≥ {strike}", actual=e.close))
        applied = max(bonus_pct, gain)
        expected = cents(n * (100 + applied) / 100)
        issues = [] if e.denomination == n else [f"算式中的面額 {e.denomination} 不等於第一章面額 {n}"]
        if not within_tolerance(e.applied, cents(applied)):
            issues.append(f"算式第二步的 {e.applied}% 應為最大值〔{e.bonus}%, {cents(gain)}%〕= {cents(applied)}%")
        if not within_tolerance(e.amount, expected):
            issues.append(f"到期贖回金額 {e.amount} 應為 {n} × (100% + {cents(applied)}%) = {expected}")
        out.append(c.calc("redemption", issues, hit, expected=expected, actual=e.amount))
    out.append(pnl_check(c, sc.pnl.get(case), amount, n))
    out.append(annualized_of_pnl(c, sc.annualized.get(case), sc.pnl.get(case), n, tenor))
    return out


def _digital(c: super_bull.Scenario, sc, n: Decimal, bonus_pct: Decimal, tenor: int) -> list[CheckResult]:
    """Digital 情境一（≥ 執行價）：A = 面額 × (100% + 紅利)；損益 = A − 面額；年化 = 損益 ÷ 面額 × 12 ÷ 天期。"""
    out = []
    amount = None
    if sc.digital is None:
        out.append(c.unread("redemption", "找不到「每單位面額 × (100% + x%) = N × (100% + x%) = A」"))
    else:
        d = sc.digital.value
        amount = d.amount
        expected = cents(n * (100 + bonus_pct) / 100)
        issues = [] if d.denomination == n else [f"算式中的面額 {d.denomination} 不等於第一章面額 {n}"]
        if d.bonus_formula != d.bonus:
            issues.append(f"算式前後的紅利 {d.bonus_formula}%、{d.bonus}% 不同")
        if not within_tolerance(d.amount, expected):
            issues.append(f"到期金額 {d.amount} 應為 {n} × (100% + {bonus_pct}%) = {expected}")
        out.append(c.calc("redemption", issues, sc.digital, expected=expected, actual=d.amount))
    out.append(pnl_check(c, sc.pnl.get(1), amount, n))
    out.append(annualized_of_pnl(c, sc.annualized.get(1), sc.pnl.get(1), n, tenor))
    return out


def run_all(ctx: IssuerContext) -> list[CheckResult]:
    """說明書內部規則：只用讀出結果與審查標準（不讀參考條件表）。"""
    return [
        scope(ctx),
        price_headers(ctx),
        *hsbc.document_info(ctx),
        no_autocall(ctx),
        no_ki(ctx),
        *no_coupon(ctx),
        *bonus(ctx),
        *redemption_formula(ctx),
        template_residue(ctx),
        final_edition(ctx),
        *scenario_calculations(ctx),
    ]
