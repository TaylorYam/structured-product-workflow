"""MS BEN 專屬規則（docs/checker/rules/ms-ben-check-rules.md）：型態與 lookback 一致、MS BEN 限定的型態、紅利與參與率、沒有 KO、
沒有定期配息、無 KI、到期公式與定義句、價格表表頭 %、回顧觀察期間、情境分析重算。

名稱、幣別、受託機構商品代號、商品種類沿用 MS FCN（rules/ms.py）；參考條件表欄位（含參與率 `field.participation_pct`）、價格
推算、重印表與基本假設、審查標準與回填（含回顧結束日 `backfill.lookback_end_date`）是各上手共用的規則。交股情境的算式和 HSBC
Super Bull 情境三共用（rules/hsbc_super_bull.py，MS 先把報酬率四捨五入到 2 位再年化）。說明書內部規則不讀參考條件表。
抓不到或寫法不在範本規格內一律轉人工覆核。
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from ..parsers.ms_ben import KIND_LABEL, PARTICIPATION, Kind, MsBenTermSheet
from ..schema import CheckResult, Item
from ..schema import CheckStatus as S
from . import kit, ms, super_bull
from .hsbc_super_bull import NAMES as DELIVERY_NAMES
from .hsbc_super_bull import cents, worse
from .kit import Check, IssuerContext
from .super_bull import ReturnMessages, profit_return_issues, within_tolerance

# ---------------------------------------------------------------- 待使用者確認的暫定做法（核對規則 §9，詳見本機疑慮記錄）
# 每項只在這裡改；說明書端另有 parsers/ms_ben.py 的 OLD_VERSION（BEN-376-05）、NAME_ZH（BEN-376-03）、HEAD15（BEN-376-04）、
# WORST_DEF（BEN-376-01），投資人須知端 parsers/ms_ben_iis.py 的 NO_COUPON（BEN-376-04）、OLD_VERSION（BEN-376-05）。

# BEN-376-06：有樣本的標的數與幣別；其他型態轉人工覆核（待樣本驗證）
SAMPLED_UNDERLYINGS: dict[Kind, range] = {"maxi": range(2, 4), "digital": range(1, 2)}
SAMPLED_CURRENCIES = frozenset({"USD"})
# BEN-376-07：回顧結束日要是共同交易日；交易所假日表沒涵蓋（或標的交易所不在假日表）→ 這個狀態
LOOKBACK_CALENDAR_UNKNOWN = S.REVIEW_REQUIRED

NOT_COVERED = [
    *(x for x in ms.NOT_COVERED if x["rule_id"] not in ("doc.scenario_worse_details", "doc.template_variants")),
    {
        "rule_id": "doc.template_variants",
        "description": "只支援現行版範本（一般 BEN、Digital，各有 lookback；寫法不同轉人工覆核、不能放行）；一般 BEN 1 檔或 4 檔以上"
        "標的、Digital 2 檔以上標的、非美元轉人工覆核（待樣本驗證）",
    },
    {
        "rule_id": "doc.ben_skipped",
        "description": "§15 定義段的轉換匯率、最終實物贖回權益與共同預定交易日說明、§14(6)～(8)、§17～§28、期末定價日到到期日的"
        "天數、情境的示意收盤價與說明句、參考條件表 KO% 與 KO 價（說明書沒有 KO）、回顧期初價格（比價時才算）不核對",
    },
]


def _ts(ctx: IssuerContext) -> MsBenTermSheet:
    ts = ctx.ts
    if not isinstance(ts, MsBenTermSheet):  # 註冊表只把 BEN 讀出結果交給這組規則
        raise TypeError("MS BEN 規則需要 BEN 說明書的讀出結果")
    return ts


def _fixed(ctx: IssuerContext, rid: str, field: str, name: str) -> CheckResult:
    """固定寫法（核對規則 §3.3、§3.5）：讀到 → 通過；找不到或寫法不同 → 不一致。"""
    return super_bull.fixed(rid, field, name, ctx.ts.f(field), ctx.document)


# ---------------------------------------------------------------- 型態、lookback 一致、MS BEN 限定（核對規則 §3.1）


def scope(ctx: IssuerContext) -> list[CheckResult]:
    """型態一致（名稱、§10(a)、§15(1)(a)、§15(3) 參與率）、lookback 一致（五處一起有或沒有）、沒有樣本的型態（待樣本驗證）：
    不成立都轉人工覆核。"""
    f = _ts(ctx).f
    rid = "doc.template_variant"
    kind, formulas, rate = f("ben_kind"), f("formula_kinds"), f("participation_pct")
    out = []
    if kind.ok and formulas.ok and rate.ok:
        issues = [
            f"{where}(a) 的公式是{'、'.join(KIND_LABEL[k] for k in kinds) or '認不出的寫法'}"
            for where, kinds in formulas.value.items()
            if kinds != [kind.value]
        ]
        if rate.value != PARTICIPATION[kind.value]:
            issues.append(f"§15(3) 參與率 {rate.value}% 不是 {KIND_LABEL[kind.value]} 的 {PARTICIPATION[kind.value]}%")
    else:
        issues = []
    out.append(
        kit.doc_check(ctx, rid, "ben_kind", "BEN 型態（名稱、§10、§15 公式與參與率）", kind, formulas, rate).compare(
            None,
            None,
            ok=not issues,
            fail=S.REVIEW_REQUIRED,
            reason="document_inconsistent",
            message="；".join(issues) or (KIND_LABEL[kind.value] if kind.ok else ""),
            fail_message="：型態對不上，請人工核對",
        )
    )
    lookback = f("lookback")
    out.append(
        kit.doc_check(ctx, rid, "lookback", "lookback 寫法（§14(9)、§15、§16 重印表）", lookback).compare(
            None, None, ok=True, message="有 lookback" if lookback.ok and lookback.value else "沒有 lookback"
        )
    )
    uls, cz = f("underlyings"), f("currency_zh")
    check = kit.doc_check(ctx, rid, "template_variant", "BEN 型態", kind, uls, cz)
    if (problem := check.blocked) is not None:
        return [*out, problem]
    issues = []
    if len(uls.value) not in SAMPLED_UNDERLYINGS[kind.value]:
        issues.append(f"{KIND_LABEL[kind.value]}有 {len(uls.value)} 檔標的")
    if ctx.std.currency_zh_to_iso.get(cz.value) not in SAMPLED_CURRENCIES:
        issues.append(f"計價幣別是{cz.value}")
    out.append(
        check.compare(
            None,
            None,
            ok=not issues,
            fail=S.REVIEW_REQUIRED,
            reason="template_variant_unverified",
            message="；".join(issues) or KIND_LABEL[kind.value],
            fail_message="：沒有樣本，待樣本驗證，請人工核對",
        )
    )
    return out


# ---------------------------------------------------------------- 價格表表頭、標的代碼（核對規則 §3.5）


def price_headers(ctx: IssuerContext) -> list[CheckResult]:
    """非 lookback：§15 價格表表頭「（期初價格的X%）」= 執行價定義 K%。lookback 的價格表沒有執行價欄，§16 重印表表頭
    「（回顧期初價格的X%）」由共用 `scenario.reprint` 和 K% 比。"""
    lookback = ctx.ts.f("lookback")
    if lookback.ok and lookback.value:
        return []
    table, pct = ctx.ts.f("price_table"), ctx.ts.f("strike_pct")
    header = Check("doc.price_header_pct", "strike", Item.expected(kit.HEADER_PCT_ITEM["strike"]), ctx.document)
    header = replace(header, expected=pct.value).needs(table, pct)
    return [header.compare(pct.value, table.value.pcts.get("strike") if table.ok else None)]


def underlying_tickers(ctx: IssuerContext) -> CheckResult:
    uls, art11 = ctx.ts.f("underlyings"), ctx.ts.f("underlyings_art11")
    return kit.doc_check(ctx, "doc.underlying_tickers", "underlyings_art11", "第 11 項標的彭博代碼", uls, art11).compare(
        uls.value, art11.value, fail_message="第 11 項標的表的彭博代碼須依序等於第 15 項價格表"
    )


# ---------------------------------------------------------------- 紅利、參與率、沒有 KO、沒有定期配息、無 KI、到期公式（§3.3、§3.5）


def bonus(ctx: IssuerContext) -> list[CheckResult]:
    """紅利、參與率的其他出處（§16 假設、一般 BEN 情境二、Digital 情境一二）= §15 的正式值，只比數值。"""
    ts = _ts(ctx)
    return [
        *super_bull.exit_bonus_mentions(
            ctx, ts.bonus_mentions, "須等於 §15(1) 的紅利配息率（只比數值）", rid="doc.bonus", label="紅利", key="bonus"
        ),
        *super_bull.exit_bonus_mentions(
            ctx,
            ts.participation_mentions,
            "須等於 §15(3) 的參與率（只比數值）",
            rid="doc.participation",
            label="參與率",
            key="participation",
            main_field="participation_pct",
        ),
    ]


def no_autocall(ctx: IssuerContext) -> CheckResult:
    """沒有 KO：§10 開頭固定句、第一章沒有自動提前出場條、全文沒有「自動提前出場」（由 parser 判定；讀不到 → 人工覆核）。"""
    pf = ctx.ts.f("no_autocall")
    check = kit.doc_check(ctx, "doc.no_autocall", "no_autocall", "§10 開頭與第一章條號（沒有自動提前出場）", pf)
    return check.compare(None, None, ok=True, message="沒有 KO：不會提前出場，Non-Call = 天期")


def no_ki(ctx: IssuerContext) -> CheckResult:
    """無 KI：全文沒有「觸及」、價格表沒有下限價欄、§15(1)(b) 直接比執行價（由 parser 判定；讀不到 → 人工覆核）。"""
    pf = ctx.ts.f("ki_type")
    check = kit.doc_check(ctx, "doc.ki_consistency", "ki_type", "無 KI（全文、價格表、§15(1)(b)）", pf)
    return check.compare(None, None, ok=True, message="無 KI")


def no_coupon(ctx: IssuerContext) -> list[CheckResult]:
    return [
        _fixed(ctx, "doc.no_coupon", "opening_art10", "§10 開頭「主要給付項目為到期贖回金額：」"),
        _fixed(ctx, "doc.no_coupon", "no_fixed_coupon", "說明書沒有固定配息"),
    ]


def redemption_formula(ctx: IssuerContext) -> list[CheckResult]:
    """§15(1) 開頭與 (a)(b)、§10(a)(b)（= §15）、§15(2) 與 §16 假設的最低保證贖回率、定義句（非 lookback 執行價由 `strike_pct`
    讀出；lookback 回顧期初價格；一般 BEN 最差表現）。"""
    ts = _ts(ctx)
    rid = "doc.redemption_formula"
    out = [
        _fixed(ctx, rid, "formula_head", "§15(1) 開頭"),
        _fixed(ctx, rid, "maturity_formula", "§15(1)(a)(b) 到期公式"),
        _fixed(ctx, rid, "maturity_formula_art10", "§10(a)(b) 到期公式"),
        _fixed(ctx, rid, "min_rate", "§15(2) 最低保證贖回率"),
        _fixed(ctx, rid, "min_rate_assumed", "第 16 項假設最低保證贖回率"),
    ]
    lookback = ts.f("lookback")
    if lookback.ok and lookback.value:
        out.append(_fixed(ctx, rid, "lookback_initial_definition", "§15 回顧期初價格定義"))
    if ts.kind == "maxi":
        out.append(_fixed(ctx, rid, "worst_definition", "§15 籃子中表現最差的連結標的定義"))
    return out


# ---------------------------------------------------------------- 回顧觀察期間（核對規則 §3.4）


def lookback_period(ctx: IssuerContext) -> list[CheckResult]:
    """lookback：§14(9) 起點「自交易日（包含）起」；交易日 < 回顧結束日 < 期末定價日；回顧結束日是所有標的市場都開市的日子
    （交易所假日表；沒涵蓋 → `LOOKBACK_CALENDAR_UNKNOWN`）。沒有 lookback 時沒有結果（表上的回顧結束日由回填規則比）。"""
    f = ctx.ts.f
    lookback = f("lookback")
    if not (lookback.ok and lookback.value):
        return []
    rid = "doc.lookback_period"
    end, trade, final, uls = f("lookback_end_date"), f("trade_date"), f("final_valuation_date"), f("underlyings")
    out = [_fixed(ctx, rid, "lookback_start", "§14(9) 回顧觀察期間起點")]
    order = kit.doc_check(ctx, rid, "lookback_end_date", "回顧結束日（交易日與期末定價日之間）", end, trade, final)
    out.append(
        order.compare(
            f"{trade.value} < 回顧結束日 < {final.value}" if trade.ok and final.ok else None,
            end.value,
            ok=end.ok and trade.ok and final.ok and trade.value < end.value < final.value,
            fail_message="：回顧結束日要晚於交易日、早於期末定價日",
        )
    )
    common = kit.doc_check(ctx, rid, "lookback_end_trading_day", "回顧結束日（共同交易日）", end, uls)
    if (problem := common.blocked) is not None:
        return [*out, problem]
    markets = [ctx.calendar.market_of(t) for t in uls.value]
    unknown = [t for t, m in zip(uls.value, markets, strict=True) if m is None or not m.covers(end.value, end.value)]
    if unknown:
        out.append(
            common.result(
                LOOKBACK_CALENDAR_UNKNOWN,
                actual=end.value,
                reason="calendar_unavailable",
                message=f"交易所假日表沒有涵蓋 {'、'.join(unknown)} 在 {end.value} 的開休市，無法確認回顧結束日是共同交易日",
            )
        )
        return out
    closed = ctx.calendar.closed(end.value, uls.value)
    out.append(
        common.compare(
            "共同交易日",
            end.value,
            ok=not closed,
            message="所有標的市場都開市" if not closed else f"{end.value} 有標的市場休市（或是週末）",
            fail_message="：回顧結束日要是共同交易日",
        )
    )
    return out


# ---------------------------------------------------------------- 情境分析（核對規則 §3.8）


RID = super_bull.SCENARIO_RID
NAMES = {**DELIVERY_NAMES, "assumed": "期末收盤價", "redemption": "到期贖回金額", "return": "報酬率"}
CASH_KEYS = ("assumed", "redemption", "return", "annualized")
DELIVERY_KEYS = ("assumed", "shares", "whole", "fraction", "remainder", "value", "pnl", "annualized")
KEYS = (CASH_KEYS, CASH_KEYS, DELIVERY_KEYS)
TITLE_WHY: dict[Kind, str] = {
    "maxi": "「情境一:較佳情況 A」「情境二:較佳情況 B」「情境三:最差情況」「情境四：發行機構無法履約」",
    "digital": "「情境一:較佳情況」「情境二:較佳情況」「情境三:最差情況」「情境四：發行機構無法履約」",
}
RETURN_MESSAGES = ReturnMessages(
    amount="報酬率算式的到期贖回金額 {got} 和前面算出的 {want} 不同",
    divisor="報酬率算式的投資金額 {got} 不等於第一章面額 {denomination}",
    pct="報酬率 {pct}% 應為 {amount} ÷ {divisor} − 1 = {expected}%",
    annualized="",
)


def scenario_calculations(ctx: IssuerContext) -> list[CheckResult]:
    """情境的算式全部重算：一般 BEN 情境一 = 面額 × (100% + 最大值〔紅利, 參與率 × (P ÷ I − 1)〕)、情境二與 Digital 情境一二 =
    面額 × (100% + 紅利)；報酬率 = A ÷ 面額 − 1，年化 = 報酬率 × 12 ÷ 天期；情境三交股同 Super Bull 情境三（報酬率先四捨五入到
    2 位再年化）。金額、報酬率與年化容許 ±0.01、股數 ±0.0001；只比數值。"""
    ts = _ts(ctx)
    f, sc = ts.f, ts.ben_scenarios
    denom, tenor, main, rate, prices = (
        f(x) for x in ("denomination", "tenor_months", "coupon_pa_pct", "participation_pct", "underlying_prices")
    )
    cases = [super_bull.Scenario(n, sc.titles[n - 1], NAMES) for n in (1, 2, 3)]
    bad = kit.first_bad((denom, tenor, main, rate, prices))
    if bad is not None:
        return [
            Check(RID, f"{c.tag}_{key}", c.item(key), ctx.document).review(bad)
            for c, ks in zip(cases, KEYS, strict=True)
            for key in ks
        ]
    if not sc.found:
        why = f"第 16 項的情境不是剛好{TITLE_WHY[sc.kind]}四個"
        return [c.unread(key, why) for c, ks in zip(cases, KEYS, strict=True) for key in ks]
    n = Decimal(denom.value)
    out = []
    for case in (1, 2):
        c = cases[case - 1]
        row = sc.rows.get(case)
        initial = prices.value[row - 1].prices.get("initial") if row is not None else None
        out.append(_assumed(c, sc, case, initial, main.value, rate.value))
        out.append(_redemption(c, sc, case, n, main.value, rate.value))
        out += _return(c, sc, case, n, tenor.value)
    out += worse(cases[2], sc.delivery, n, Decimal(1), tenor.value, round_return=True)
    return out


def _gain(participation: Decimal, close: Decimal, initial: Decimal) -> Decimal:
    """參與率 × (P ÷ I − 1)，單位 %。"""
    return participation * (close / initial - 1)


def _assumed(c: super_bull.Scenario, sc, case: int, initial: Decimal | None, bonus: Decimal, rate: Decimal) -> CheckResult:
    """情境假設：一般 BEN 情境一 P ≥ K 且漲幅 > 紅利、情境二 P ≥ K 且漲幅 < 紅利（漲幅用期初價格；lookback 用回顧期初價格欄
    的值）；Digital 情境一 P > K、情境二 P = K。K、I 是不是價格表那一檔的值由共用 `scenario.strike` 核對。"""
    cond = sc.conditions.get(case)
    if cond is None:
        return c.unread("assumed", "找不到「假設於期末定價日時…之收盤價 = P；執行價 = K」")
    p, k = cond.value.close, cond.value.strike
    issues = []
    if sc.kind == "digital":
        want = "> " if case == 1 else "= "
        if case == 1 and not p > k:
            issues.append(f"期末收盤價 {p} 應高於執行價 {k}（情境一）")
        if case == 2 and p != k:
            issues.append(f"期末收盤價 {p} 應等於執行價 {k}（情境二）")
        return c.calc("assumed", issues, cond, expected=f"{want}{k}", actual=p)
    if p < k:
        issues.append(f"期末收盤價 {p} 應不低於執行價 {k}")
    if case == 1 and sc.maxi is not None and sc.maxi.value.close != p:
        issues.append(f"算式的期末收盤價 {sc.maxi.value.close} 和假設的 {p} 不同")
    if initial is None:
        return c.unread("assumed", "認不出情境寫的最差標的是價格表的哪一檔，無法算漲幅")
    gain = _gain(rate, p, initial)
    if case == 1 and not gain > bonus:
        issues.append(f"漲幅 {cents(gain)}% 應大於紅利 {bonus}%（情境一）")
    if case == 2 and not gain < bonus:
        issues.append(f"漲幅 {cents(gain)}% 應小於紅利 {bonus}%（情境二）")
    return c.calc("assumed", issues, cond, expected=f"≥ {k}", actual=p)


def _redemption(c: super_bull.Scenario, sc, case: int, n: Decimal, bonus: Decimal, rate: Decimal) -> CheckResult:
    """到期贖回金額：一般 BEN 情境一 A = 面額 × (100% + 最大值〔紅利, 參與率 × (P ÷ I − 1)〕)；其他 A = 面額 × (100% + 紅利)；
    四捨五入到 2 位（容許 ±0.01）。"""
    if sc.kind == "maxi" and case == 1:
        hit = sc.maxi
        if hit is None:
            return c.unread("redemption", "找不到「= N x [100% + 參與率 x (P/I - 100%)] = A」")
        m = hit.value
        applied = max(bonus, _gain(m.participation, m.close, m.initial))
        expected = cents(n * (100 + applied) / 100)
        issues = [] if m.denomination == n else [f"算式中的面額 {m.denomination} 不等於第一章面額 {n}"]
        if m.participation != rate:
            issues.append(f"算式中的參與率 {m.participation}% 不等於 §15(3) 的 {rate}%")
        if not within_tolerance(m.amount, expected):
            issues.append(f"到期贖回金額 {m.amount} 應為 {n} × (100% + {cents(applied)}%) = {expected}")
        return c.calc("redemption", issues, hit, expected=expected, actual=m.amount)
    hit = sc.bonus.get(case)
    if hit is None:
        return c.unread("redemption", "找不到「= N x (100% + X%) = A」")
    b = hit.value
    expected = cents(n * (100 + bonus) / 100)
    issues = [] if b.denomination == n else [f"算式中的面額 {b.denomination} 不等於第一章面額 {n}"]
    if not within_tolerance(b.amount, expected):
        issues.append(f"到期贖回金額 {b.amount} 應為 {n} × (100% + {bonus}%) = {expected}")
    return c.calc("redemption", issues, hit, expected=expected, actual=b.amount)


def _return(c: super_bull.Scenario, sc, case: int, n: Decimal, tenor: int) -> list[CheckResult]:
    """報酬率「= (A / N) - 1 = r%(年化報酬率:y%)」：r = A ÷ N − 1（容許 ±0.01）；y = r × 12 ÷ 天期，四捨五入到 2 位（容許 ±0.01）。"""
    hit = sc.returns.get(case)
    if hit is None:
        why = "找不到「= (A / N) - 1 = r%(年化報酬率:y%)」"
        return [c.unread("return", why), c.unread("annualized", why)]
    r = hit.value
    amount = sc.maxi.value.amount if sc.kind == "maxi" and case == 1 and sc.maxi else None
    if amount is None and sc.bonus.get(case) is not None:
        amount = sc.bonus[case].value.amount
    expected, issues = profit_return_issues(r, amount, n, RETURN_MESSAGES, tolerance=super_bull.AMOUNT_TOLERANCE)
    annualized = cents(r.pct * 12 / tenor)
    a_issues = (
        []
        if within_tolerance(r.annualized, annualized)
        else [f"年化報酬率 {r.annualized}% 應為 {r.pct}% × 12 ÷ {tenor} = {annualized}%"]
    )
    return [
        c.calc("return", issues, hit, expected=expected, actual=r.pct),
        c.calc("annualized", a_issues, hit, expected=annualized, actual=r.annualized),
    ]


def run_all(ctx: IssuerContext) -> list[CheckResult]:
    """說明書內部規則：只用讀出結果與審查標準（不讀參考條件表）。"""
    return [
        *scope(ctx),
        *price_headers(ctx),
        *ms.document_info(ctx),
        ms.name_months(ctx),
        underlying_tickers(ctx),
        no_autocall(ctx),
        no_ki(ctx),
        *no_coupon(ctx),
        *bonus(ctx),
        *redemption_formula(ctx),
        *lookback_period(ctx),
        *scenario_calculations(ctx),
    ]
