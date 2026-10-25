"""幣別代碼與價格表的標的交易幣別（docs/checker/rules/shared-check-rules.md §9，Issue #227）：各上手共用，說明書與投資人須知都核對。

- 幣別代碼：全文每一處「中文幣別(代碼)」（例：`日圓(JPY)`、`10,000美元(USD)`），代碼須 = 審查標準 `[currency]` 對到的
  ISO 代碼，或 `[currency_code_aliases]` 列的其他寫法。整份文件一筆結果；各上手規則另比封面與條文的代碼 = 封面幣別
  （`doc.currency_consistency`），同一處代碼錯時兩條都報。
- 價格表的標的交易幣別：`underlying_prices` 每一列有幣別（價格旁的幣別字或幣別欄）時，須 = 審查標準 `[exchange_currency]`
  依彭博交易所碼對到的幣別；對照表沒有的交易所碼轉人工覆核。整份文件一筆結果。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..parsers.money import CURRENCY_WORD
from ..schema import CheckResult, Evidence, Item
from ..schema import CheckStatus as S
from ..trading_calendar import exchange_code
from . import kit
from .kit import Check, Context
from .reference import currency_iso

RULE_CODE = "standard.currency_code"
RULE_EXCHANGE = "standard.underlying_currency"
CODE_PAIR = re.compile(rf"({CURRENCY_WORD})\s*[(（]\s*([A-Z]{{3}})\s*[)）]")


@dataclass(frozen=True)
class CurrencyMention:
    """一處要核對的幣別：顯示用的寫法（例：日圓(USD)）、應為的寫法、是否相符（審查標準對照不到時為 None）、證據。"""

    shown: str
    expected: str | None
    ok: bool | None
    evidence: tuple[Evidence, ...]


def _summary(
    check: Check, marks: list[CurrencyMention], *, unknown_reason: str, unknown: str, wrong: str, tolerance: str
) -> CheckResult:
    """整份文件一筆：有不符 → 不一致；有對不到的 → 人工覆核；其餘通過。預期值與實際值只列有問題的那幾處。"""
    bad = [m for m in marks if m.ok is False]
    missing = [m for m in marks if m.ok is None]
    shown = bad or missing or marks
    expected = "、".join(m.expected or "？" for m in shown)
    actual = "、".join(m.shown for m in shown)
    evidence = [e for m in shown for e in m.evidence]
    if bad:
        return check.result(
            S.MISMATCH,
            expected=expected,
            actual=actual,
            evidence=evidence,
            reason="value_mismatch",
            tolerance=tolerance,
            message=wrong,
        )
    if missing:
        return check.result(
            S.REVIEW_REQUIRED,
            actual=actual,
            evidence=evidence,
            reason=unknown_reason,
            tolerance=tolerance,
            message=unknown,
        )
    return check.result(S.PASS, expected=expected, actual=actual, evidence=evidence, tolerance=tolerance)


def codes(ctx: Context) -> list[CheckResult]:
    """全文每一處「中文幣別(代碼)」的代碼 = 審查標準對照；文件沒有這種寫法時不報。"""
    ti = ctx.ts.full_text
    marks = []
    for m in CODE_PAIR.finditer(ti.text):
        word, code = m[1], m[2]
        iso = ctx.std.currency_zh_to_iso.get(word)
        allowed = (iso, *ctx.std.currency_code_aliases.get(iso, ())) if iso else ()
        evidence = tuple(Evidence.of(ln) for ln in ti.lines_for(m.start(), m.end()))
        marks.append(
            CurrencyMention(
                f"{word}({code})", f"{word}({iso})" if iso else None, code in allowed if iso else None, evidence
            )
        )
    if not marks:
        return []
    check = Check(RULE_CODE, "currency_code", Item.standard("幣別代碼"), ctx.document)
    return [
        _summary(
            check,
            marks,
            unknown_reason="currency_unknown",
            unknown=f"{ctx.document}的幣別不在審查標準的幣別對照表（[currency]），無法核對括號內的代碼",
            wrong=f"{ctx.document}「中文幣別(代碼)」的代碼須對應中文幣別（審查標準幣別對照）",
            tolerance="代碼也接受審查標準 [currency_code_aliases] 列的寫法（例：人民幣寫 CNY、RMB）",
        )
    ]


def underlying_currency(ctx: Context) -> list[CheckResult]:
    """價格表每一列的幣別 = 標的交易所的交易幣別；價格表讀不到（由價格規則報）或各列都沒有幣別（範本沒有）時不報，
    只有部分列讀不到幣別時轉人工覆核。"""
    rows = kit.standard_field(ctx, "underlying_prices")
    if not rows.ok or not any(r.currency for r in rows.value):
        return []
    tickers = kit.field_value(ctx.ts, "underlyings") or ()
    marks = []
    for i, r in enumerate(rows.value):
        ticker = r.ticker or (tickers[i] if i < len(tickers) else None)
        suffix = exchange_code(ticker) if ticker else None
        expected = ctx.std.exchange_currency.get(suffix) if suffix else None
        actual = currency_iso(ctx, r.currency) if r.currency else None  # 這一列讀不到幣別 → 人工覆核
        label = ticker or f"第 {i + 1} 檔"
        ok = None if expected is None or actual is None else actual == expected
        shown = f"{label}：{r.currency or '讀不到幣別'}"
        marks.append(CurrencyMention(shown, f"{label}：{expected}" if expected else None, ok, r.evidence))
    check = Check(RULE_EXCHANGE, "underlying_currency", Item.standard("價格表的標的交易幣別"), ctx.document)
    return [
        _summary(
            check,
            marks,
            unknown_reason="exchange_unknown",
            unknown="價格表有一列讀不到幣別、標的的交易所碼不在審查標準的交易所幣別對照表（[exchange_currency]），"
            "或幣別字不在幣別對照表（[currency]），請人工確認交易幣別",
            wrong="價格表每一列的幣別須 = 標的交易所的交易幣別（審查標準 [exchange_currency]，依彭博交易所碼）",
            tolerance="幣別字依審查標準幣別對照換成 ISO 代碼後比",
        )
    ]


def run(ctx: Context) -> list[CheckResult]:
    return [*codes(ctx), *underlying_currency(ctx)]
