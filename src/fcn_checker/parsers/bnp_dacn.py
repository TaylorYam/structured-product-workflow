"""BNP DACN 中文產品說明書（docs/checker/templates/bnp-zh-dacn.md）：版面、封面、日期表、KO／KI、§16、§17 到期贖回、第二章
以後同 BNP FCN 說明書（parsers/bnp.py），先照它讀出（§11 標的表與 §18 重印表多定界價格列），再改讀 §15 配息公式（月配息率、
保證期數 g、N）、§17 最低配息率（月配息率，沒有 `(年化)`）與配息門檻（§11 定界價格列的 %）。

依 §15 的 `×(nj/Nj)` 公式與比定界價格的 nj 定義辨識商品家族，名稱另當條件（中文沒有 `固定配息`、英文有 `Daily Accrual`）；
只支援現行版範本：認得出是 BNP DACN、但現行版特徵任一項不成立時，辨識交出「不支援的範本版本」（範本規格 §1、§8）。
版本只看寫法、不看交易日早晚（同第一波 DACN，workflow #409）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal

from ..schema import DetectionResult, Evidence, Line, ParsedField
from ..text import squash
from . import bnp, bnp_tables
from .common import Mention, derived
from .layout import Document, TextIndex, capture

TEMPLATE_ID = "bnp-zh-dacn-pd"
PARSER_VERSION = "1"
OLD_VERSION = "BNP DACN 說明書的寫法和現行版不同"
# 本家族的標記（範本規格 §7、§8）：全文有這幾個字就停在 DACN 範本、不退回 Super Bull 或 FCN 範本（#436）；BNP FCN、
# Super Bull 全文沒有
MARK = "定界價格"
NAME_EN = "Daily Accrual"
X = r"(\d+(?:\.\d+)?)"
ROUNDING = r"\((?:四捨五入至小數點以下第二位|四捨五入至整數位)\)"
CCY = f"(?:{bnp.CCY})"
# §15 四段固定寫法（範本規格 §2.1，去空白後依序、連續出現）
P1 = rf"月配息率：{X}%\(即年利率\d+(?:\.\d+)?%\)，惟實際配息金額將取決於連結標的之表現。配息將於各配息日依以下公式計算給付："
P2 = (
    r"就每單位商品及第1(?:到(\d+))?個配息日而言，由計算代理機構根據以下公式"
    + ROUNDING
    + rf"計算以{CCY}計值的配息款項：配息金額=商品面額×{X}%。"
)
P3 = (
    r"就每單位商品及第(\d+)到(\d+)個配息日而言，由計算代理機構根據以下公式"
    + ROUNDING
    + rf"計算以{CCY}計值的配息款項：配息金額=商品面額×{X}%×\(nj/Nj\)。"
)
P4 = re.escape(
    "其中：nj指於相關第j個配息期內籃子內所有連結標的之收盤價格皆大於或等於其各自定界價格的合格營業日天數。"
    "Nj指於相關第j個配息期內合格營業日總天數。"
)
FORMULA = re.compile(P1 + P2 + P3 + P4)
WORDINGS = (
    ("第一段（月配息率）", P1),
    ("第二段（第 1～g 個配息日）", P2),
    ("第三段（區間計息）", P3),
    ("第四段（nj、Nj 定義）", P4),
)
NJ = re.compile(r"nj指.+?其各自定界價格的合格營業日天數")  # 計息天數比的是定界價格（範本規格 §8 條件 2）
MIN_RATE = rf"最低配息率為{X}%。"  # §17：月配息率，沒有 `(年化)`（FCN 是 `最低配息率為Y%（年化）`）


# ---------------------------------------------------------------- 辨識


def is_accrual(art15: str) -> bool:
    """商品家族（區間計息，範本規格 §8 條件 2）：§15 有 `×(nj/Nj)` 公式與比定界價格的 nj 定義。"""
    return "×(nj/Nj)" in art15 and NJ.search(art15) is not None


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §8）：BNP 說明書骨架與 DACN 名稱（條件 1）、區間計息（條件 2）都成立後，現行版特徵全部成立才是
    本範本；缺任一項 → 不支援的範本版本。全文有 `MARK` 的文件停在本範本（不退回 Super Bull 或 FCN 範本）。"""
    doc = bnp.document(lines)
    cover = doc.before_chapter1()
    items = bnp.cover_items(cover)
    zh, en = (squash(items[n].value) if n in items else "" for n in (2, 3))
    issuer = items.get(5)
    ch1 = bnp.chapter1(doc)
    art15 = TextIndex(doc.span_lines(ch1.spans.get("coupon")))
    evidence = [Evidence.of(x) for x in cover[:2]]
    failed = [
        message
        for ok, message in (
            (bnp.COVER_TITLE in TextIndex(cover).text, "封面標題不是「中文產品說明書(適用於專業投資人及OSU 客戶)」"),
            (
                issuer is not None and issuer.label == "發行機構名稱" and squash(issuer.value) == squash(bnp.ISSUER_EN),
                f"封面第 5 項發行機構名稱不是 {bnp.ISSUER_EN}",
            ),
            (
                zh.startswith("法國巴黎銀行") and "結構型商品(不保本)(無擔保)" in zh and "固定配息" not in zh,
                "商品中文名稱不是法國巴黎銀行…結構型商品(不保本)(無擔保)（不含固定配息）",
            ),
            (
                squash(NAME_EN) in en and squash(f"issued by {bnp.ISSUER_EN} (unsecured)") in en,
                f"商品英文名稱不含 {NAME_EN} 與 issued by {bnp.ISSUER_EN} (unsecured)",
            ),
            (all(n in doc.chapters for n in range(1, 6)), "章名不是「第X章、」寫法或五章不完整"),
            (set(range(1, 28)) <= set(doc.articles(1)), "第一章條號 1–27 不連續"),
            (
                is_accrual(art15.text),
                f"{ch1.label('coupon')}沒有區間計息的「×(nj/Nj)」公式與比定界價格的「nj」定義",
            ),
        )
        if not ok
    ]
    if failed:
        return DetectionResult(False, failed, evidence, claimed=MARK in TextIndex(doc.lines).text)
    missing = version_problems(doc, art15, ch1)
    if missing:
        return DetectionResult(False, missing, evidence, unsupported=f"{OLD_VERSION}：{'；'.join(missing)}")
    return DetectionResult(True, [], evidence)


def version_problems(doc: Document, art15: TextIndex, ch1: bnp.Chapter1) -> list[str]:
    """現行版特徵（範本規格 §8）中不成立的項目：§15 四段固定寫法（依序）、§11 標的表有定界價格列、§17 最低配息率沒有
    `(年化)`、§14 讀得到交易日（只看寫法，不看早晚）。"""
    out, at = [], 0
    for where, pattern in WORDINGS:
        m = re.compile(pattern).search(art15.text, at)
        if m is None:
            out.append(f"{ch1.label('coupon')}{where}的寫法和現行版不同")
        else:
            at = m.end()
    art11 = TextIndex(doc.span_lines(ch1.spans.get("underlying"))).text
    if not re.search(r"定界價格\(BarrierPrice\)[:：]", art11):
        out.append(f"{ch1.label('underlying')}標的表沒有「定界價格(Barrier Price)」列")
    if not re.search(MIN_RATE, TextIndex(doc.span_lines(ch1.spans.get("maturity"))).text):
        out.append(f"{ch1.label('maturity')}沒有「最低配息率為X%。」（不含年化）")
    trade = capture("trade_date", TextIndex(doc.span_lines(ch1.spans.get("dates"))), r"期初訂價日：" + bnp.D)
    if not trade.ok:
        out.append(f"{ch1.label('dates')}讀不到期初訂價日")
    return out


# ---------------------------------------------------------------- 讀出


def read(lines: Sequence[Line]) -> bnp.BnpTermSheet:
    """照 BNP FCN 讀出（標的表宣告定界價格列）後，改讀 DACN 不同的欄位（範本規格 §3）。"""
    base = bnp.read(lines, bnp_tables.DACN_PRICE_LABELS)
    doc, ch1 = base.document, base.chapter1
    f = dict(base.fields)
    art15 = TextIndex(doc.span_lines(ch1.spans.get("coupon")))
    f["monthly_coupon_pct"], f["coupon_guaranteed_periods"] = _rate(
        art15, f["monthly_coupon_pct"], f["tenor_months"], f"{ch1.label('coupon')}配息公式"
    )
    del f["coupon_formula_pct"]  # FCN 的「固定配息金額 = 每單位面額 × X%」；DACN 改由 `_rate` 比三個 X
    f["min_coupon_rate"] = capture(
        "min_coupon_rate", TextIndex(doc.span_lines(ch1.spans.get("maturity"))), MIN_RATE, Decimal, whole_match=True
    )
    f["coupon_barrier_pct"] = barrier_pct(f["underlying_table"], f"{ch1.label('underlying')}標的表")
    coupons = [Mention.of_field(f"{ch1.label('coupon')}月配息率", f["monthly_coupon_pct"])]
    return replace(base, fields=f, coupon_mentions=coupons)


def barrier_pct(table: ParsedField, where: str) -> ParsedField:
    """配息門檻 % = 標的表定界價格列每檔的 `即期初價格之Y%`（須全部相同，否則歧義；範本規格 §4.1）。說明書與投資人須知共用。"""
    name = "coupon_barrier_pct"
    if not table.ok:
        return derived(name, table)
    pcts = table.value["pcts"].get("barrier")
    if pcts is None:
        return ParsedField.invalid(name, list(table.evidence), f"{where}沒有定界價格列")
    hits = list(zip(pcts, table.value["row_lines"], strict=True))
    return ParsedField.from_hits(name, hits, ambiguous_note=f"{where}各檔定界價格的百分比不同")


def _rate(art15: TextIndex, monthly: ParsedField, tenor: ParsedField, where: str) -> tuple[ParsedField, ParsedField]:
    """§15 配息公式 → 月配息率與保證期數 g（範本規格 §2.1、§3）：三個 X 須相同、第三段從 g+1 起、N = 天期；不成立時兩個
    欄位都讀不到（核對規則 §3.3、§3.5）。月配息率的證據沿用第一句（`monthly`）。"""
    m_name, g_name = "monthly_coupon_pct", "coupon_guaranteed_periods"
    hits = list(art15.finditer(FORMULA))
    if len(hits) != 1:
        note = f"{where}出現多次" if hits else f"{where}的寫法不在範本規格內"
        return ParsedField.missing(m_name, note), ParsedField.missing(g_name, note)
    m = hits[0]
    lines = art15.lines_for(m.start(), m.end())
    x1, g, x2, start, last, x3 = Decimal(m[1]), int(m[2] or 1), Decimal(m[3]), int(m[4]), int(m[5]), Decimal(m[6])
    problems = []
    if not x1 == x2 == x3:
        problems.append(f"三處月配息率不同（{x1}%、{x2}%、{x3}%）")
    if start != g + 1:
        problems.append(f"第三段從第 {start} 個配息日起，不是第 {g + 1} 個")
    if tenor.ok and last != tenor.value:
        problems.append(f"第三段到第 {last} 個配息日，不等於天期 {tenor.value} 個月")
    if problems:
        note = f"{where}：" + "；".join(problems)
        return ParsedField.invalid(m_name, lines, note), ParsedField.invalid(g_name, lines, note)
    rate = monthly if monthly.ok and monthly.value == x1 else ParsedField.present(m_name, x1, lines)
    return rate, ParsedField.present(g_name, g, lines)
