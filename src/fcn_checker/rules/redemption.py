"""開始受理贖回日期（docs/checker/rules/shared-check-rules.md §6.2，Issue #227）：各上手共用，說明書與投資人須知都核對。

找全文「開始受理（投資人）（提前）贖回日（期）：」標籤後面的寫法（每家範本都有一處）：
- 寫具體日期（MS、Citi、BBVA），或文字後寫「(即現預計為 <日期>)」（UBS 說明書）：日期 = 發行日的次一臺北營業日（交易所假日表
  [markets.TW]；寫法要求預定交易日的另須標的交易所開市）。只核對說明書；投資人須知的日期由上手規則和同商品說明書比
  （`iis.redemption_start`）。
- 寫文字：須以審查標準 `issuer.<上手>.redemption_start_wordings` 列出的寫法之一開頭（忽略空白、括號不分全形半形；
  只收樣本看過的寫法），沒列出的寫法轉人工覆核。
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from ..parsers.layout import TextIndex, parse_date
from ..schema import CheckResult, DocKind, Evidence, Item
from ..schema import CheckStatus as S
from ..text import full_brackets, squash
from . import kit
from .kit import Check, Context, IssuerContext

RULE_DATE = "doc.redemption_start_date"
RULE_WORDING = "standard.redemption_start"
LABEL = re.compile(r"開始受理(?:投資人)?(?:提前)?(?:透過次級市場買賣之)?贖回日(?:期)?[:：]")
_DATE = r"\d{4}年\d{1,2}月\d{1,2}日"
_EXPECTED_AT = re.compile(r"即現預計為(" + _DATE + ")")
_SHOWN = 60  # 結果上顯示標籤後的前幾個字（到句號為止）
NAME = "開始受理贖回日期"
TAIPEI = "TW"  # 交易所假日表的臺北營業日（config/exchange_holidays.toml [markets.TW]）


@dataclass(frozen=True)
class Mention:
    """標籤後的一處寫法：去空白的文字（到句號或 `_SHOWN` 字為止）、寫出的日期（開頭的日期或「即現預計為」的日期）。"""

    text: str
    date: dt.date | None
    dated: bool  # 開頭就是日期（沒有文字寫法）
    evidence: tuple[Evidence, ...]


def mentions(ti: TextIndex) -> list[Mention]:
    """全文每個「開始受理贖回日期：」標籤後的寫法。"""
    out = []
    for m in LABEL.finditer(ti.text):
        rest = ti.text[m.end() : m.end() + _SHOWN * 2]
        text = rest[:_SHOWN].split("。", 1)[0]
        head = re.match(_DATE, rest)
        at = _EXPECTED_AT.search(rest)
        date = parse_date(head[0]) if head else parse_date(at[1]) if at else None
        lines = ti.lines_for(m.start(), m.end() + max(len(text), head.end() if head else 0))
        out.append(Mention(text, date, head is not None, tuple(Evidence.of(ln) for ln in lines)))
    return out


def _missing(document: DocKind) -> CheckResult:
    return Check(RULE_WORDING, "redemption_start", Item.standard(NAME), document).result(
        S.REVIEW_REQUIRED, reason="document_missing", message=f"{document}找不到「開始受理贖回日期：」"
    )


def _wording(issuer: str, wordings: tuple[str, ...], m: Mention, document: DocKind) -> CheckResult:
    check = Check(RULE_WORDING, "redemption_start_wording", Item.standard(f"{NAME}寫法"), document)
    if not wordings:
        return check.result(
            S.REVIEW_REQUIRED,
            actual=m.text,
            evidence=m.evidence,
            reason="standard_missing",
            message=f"審查標準沒有 {issuer} 的{NAME}寫法（issuer.{issuer.lower()}.redemption_start_wordings）",
        )
    text = full_brackets(m.text)
    hit = next((w for w in wordings if text.startswith(full_brackets(squash(w)))), None)
    return check.compare(
        list(wordings) if hit is None else hit,
        m.text,
        ok=hit is not None,
        evidence=m.evidence,
        tolerance=f"以審查標準列出的寫法開頭（issuer.{issuer.lower()}.redemption_start_wordings），忽略空白與括號全形半形",
        fail_message=f"{NAME}的寫法不是審查標準列出的寫法",
    )


def _date(ctx: IssuerContext, m: Mention) -> CheckResult:
    """寫出的日期 = 發行日的次一臺北營業日；寫法要求預定交易日（UBS「預定交易日（並須為臺北營業日）」）時標的交易所也要開市。
    相等通過；推算結果無法確定（超出假日表涵蓋範圍、標的交易所不在假日表）而文件寫得較晚時轉人工覆核，其餘不一致。"""
    issue = kit.read_standard(ctx.ts, "issue_date")
    check = Check(RULE_DATE, "redemption_start", Item.expected(NAME), ctx.document).needs(issue)
    if (problem := check.blocked) is not None:
        return problem
    taipei = ctx.calendar.market(TAIPEI)
    if taipei is None:
        return check.result(
            S.REVIEW_REQUIRED,
            actual=m.date,
            evidence=m.evidence,
            reason="calendar_missing",
            message=f"交易所假日表沒有臺北營業日（markets.{TAIPEI}），無法推算{NAME}",
        )
    tickers = kit.field_value(ctx.ts, "underlyings") if "交易日" in m.text else None
    following = (
        ctx.calendar.next_trading_day(issue.value, tickers, also=[taipei])
        if "交易日" in m.text
        else ctx.calendar.next_business_day(issue.value, taipei)
    )
    if m.date == following.day:
        status, reason, note = S.PASS, "", ""
    elif following.uncertain and m.date > following.day and m.date.weekday() < 5 and m.date not in taipei.holidays:
        status, reason, note = S.REVIEW_REQUIRED, kit.UNCERTAIN_TRADING_DAY, f"（{following.uncertain}）"
    else:
        status, reason, note = S.MISMATCH, "value_mismatch", ""
    return check.result(
        status,
        expected=following.day,
        actual=m.date,
        evidence=[*m.evidence, *issue.evidence],
        reason=reason,
        tolerance="發行日後的下一個臺北營業日" + ("（標的交易所也開市）" if tickers is not None else ""),
        message="" if status == S.PASS else f"{NAME}須為發行日的次一營業日：應為 {following.day}{note}",
    )


def term_sheet(ctx: IssuerContext) -> list[CheckResult]:
    """說明書：每處寫法（文字）與日期都核對。"""
    found = mentions(ctx.ts.full_text)
    if not found:
        return [_missing(ctx.document)]
    wordings = ctx.issuer_std.redemption_start_wordings
    out = []
    for m in found:
        if not m.dated:
            out.append(_wording(ctx.issuer, wordings, m, ctx.document))
        if m.date is not None:
            out.append(_date(ctx, m))
    return out


def investor_sheet(ctx: Context) -> list[CheckResult]:
    """投資人須知：只核對文字寫法；寫日期的由上手規則和同商品說明書比（`iis.redemption_start`）。"""
    found = mentions(ctx.ts.full_text)
    if not found:
        return [_missing(ctx.document)]
    wordings = ctx.issuer_std.redemption_start_wordings
    return [_wording(ctx.issuer, wordings, m, ctx.document) for m in found if not m.dated]
