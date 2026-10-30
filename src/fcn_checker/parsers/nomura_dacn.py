"""Nomura DACN 中文產品說明書（docs/checker/templates/nomura-zh-dacn.md）：骨架、封面、標的表、日期表、第二章以後同 Nomura FCN
說明書（parsers/nomura.py），先照它讀出，再改讀每日計息的配息段落（第 7 項配息公式與年化配息率、第 14 項設定參數與情境一／二、
第 15 項 (1)）。

依第 7 項的每日計息定義辨識商品家族（不看標題，workflow #366 決定 10）；只支援現行版範本：認得出是 Nomura DACN、但現行版的
固定寫法任一段不同或交易日早於 2026-06-01 時，辨識交出「不支援的範本版本」（範本規格 §1、§8）。
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Sequence
from decimal import Decimal

from .. import standard_fields
from ..schema import DetectionResult, Evidence, Line, ParsedField
from ..standard_fields import ScenarioParam
from ..text import squash
from . import nomura
from .common import Hit, Mention, derived, hits, one
from .layout import TextIndex, capture, parse_date

TEMPLATE_ID = "nomura-zh-dacn-pd"
PARSER_VERSION = "1"
OLD_VERSION = "Nomura DACN 說明書的寫法和現行版（2026-06 以後）不同"
FIRST_SUPPORTED = dt.date(2026, 6, 1)  # 交易日早於這天的是舊版範本（沒有樣本，範本規格 §1；待使用者確認）
NAME_EN_MARKS = ("Autocall Daily Range Accrual Notes", f"issued by {nomura.ISSUER_EN}")
NAME_ZH_MARK = "計價每日計息("
# 範本規格 §2.1 的四段固定寫法（去空白後逐字相等；原文的「没」（U+6CA1）和「沒」視為同一字，見 `same_chars`）
FORMULA = (
    "配息金額：(i)就第一個配息日而言，每單位商品面額以計價幣別之配息金額為（四捨五入至小數第二位）：每單位商品面額×年化配息率/12"
    "(ii)就其他配息日而言，每單位商品面額以計價幣別之配息金額為（四捨五入至小數第二位）：每單位商品面額×年化配息率/12×實際日數/總日數"
)
ACTUAL_DAYS = (
    "實際日數：係指於自觀察期間開始日起（含）至自動提前出場決定日（含）（係自動提前出場事件視為發生之日）或觀察期間終止日止（含）"
    "（係自動提前出場事件没有視為發生）之相關觀察期間內之一籃子股票中所有連結標的之收盤價等於或大於其各自執行價之預定交易日數。"
)
TOTAL_DAYS = "總日數：係指自觀察期間開始日起（含）至觀察期間結束日止（含）之相關觀察期間內之預定交易日數。"
DISRUPTION = (
    "為決定一籃子股票內連結標的之收盤價及觀察期間內之預定交易日數，如任一連結標的於觀察期間之預定交易日為中斷日，則就決定一籃子股票內"
    "連結標的之收盤價而言，該日應予忽略，並將該預定交易日從觀察期間內之預定交易日數中剔除。"
)
WORDINGS = (
    ("第 7 項配息公式 (i)(ii)", FORMULA),
    ("第 7 項實際日數定義", ACTUAL_DAYS),
    ("第 7 項總日數定義", TOTAL_DAYS),
    ("第 7 項中斷日剔除句", DISRUPTION),
)
GUARANTEED = "(i)就第一個配息日而言"  # 配息公式第 1 期保證全額 → 保證期數 1（範本規格 §4.2）
ANNUAL = r"年化配息率：" + nomura.PCT  # 第 7 項年利率（主來源）
ART15 = (
    r"\(1\)年化報酬率：就第一個配息日而言:" + nomura.PCT + r"。就其他配息日而言:" + nomura.PCT + r"×實際日數/總日數。"
)
ACCRUAL_PCT = r"[“”\"]實際日數/總日數[“”\"]是" + nomura.PCT  # 情境的「實際日數/總日數」假設值 P（引號全半形混用）
SCENARIO_RETURN = r"本情境中年化報酬率為" + nomura.PCT
TRADE_DATE = r"\(2\)交易日：" + nomura.D


# ---------------------------------------------------------------- 辨識


def same_chars(text: str) -> str:
    """固定寫法比對前把異體字統一：「没」→「沒」（原文用「没」；合成文件的字型沒有這個字形）。"""
    return text.replace("没", "沒")


def is_accrual(art7: str) -> bool:
    """商品家族（每日計息，範本規格 §8 條件 6）：第 7 項有「實際日數：」「總日數：」定義，且配息公式有「×實際日數/總日數」。"""
    return all(label in art7 for label in nomura.ACCRUAL_LABELS) and "×實際日數/總日數" in art7


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §8）：Nomura 骨架（條件 1～5）與每日計息（條件 6）都成立後，現行版特徵全部成立才是本範本；
    缺任一項 → 不支援的範本版本。"""
    sk = nomura.skeleton(lines)
    art7 = TextIndex(sk.article(7)).text
    failed = list(sk.failed)
    if not is_accrual(art7):
        failed.append("第 7 項沒有每日計息的「實際日數：」「總日數：」定義與「×實際日數/總日數」配息公式")
    if failed:
        return DetectionResult(False, failed, sk.evidence)
    missing = version_problems(sk, art7)
    if missing:
        return DetectionResult(False, missing, sk.evidence, unsupported=f"{OLD_VERSION}：{'；'.join(missing)}")
    return DetectionResult(True, [], sk.evidence)


def version_problems(sk: nomura.Skeleton, art7: str) -> list[str]:
    """現行版特徵（範本規格 §8）中不成立的項目。"""
    out = [f"{where}和現行版不同" for where, text in WORDINGS if same_chars(text) not in same_chars(art7)]
    if len(re.findall(ANNUAL, art7)) != 1:
        out.append("第 7 項「年化配息率：X%」不是剛好一處")
    if re.search(ART15, TextIndex(sk.article(15)).text) is None:
        out.append("第 15 項 (1) 不是「年化報酬率：就第一個配息日而言: X%。就其他配息日而言: X% × 實際日數/總日數。」")
    if not all(squash(x) in sk.en for x in NAME_EN_MARKS):
        out.append("商品英文名稱沒有「" + "」「".join(NAME_EN_MARKS) + "」")
    if NAME_ZH_MARK not in sk.zh:
        out.append(f"商品中文名稱沒有「{NAME_ZH_MARK}」")
    m = re.search(TRADE_DATE, TextIndex(sk.article(11)).text)
    trade = parse_date(m[1]) if m else None
    if trade is None:
        out.append("第 11 項 (2) 讀不到交易日")
    elif trade < FIRST_SUPPORTED:
        out.append(f"交易日 {trade} 早於 {FIRST_SUPPORTED}（只支援現行版範本）")
    return out


# ---------------------------------------------------------------- 讀出


def read(lines: Sequence[Line]) -> nomura.NomuraTermSheet:
    """先照 Nomura FCN 讀出，再改讀配息段落（範本規格 §3）：年利率取第 7 項，沒有每期配息率；另交配息門檻、保證期數、
    情境二的實際日數比例與年化報酬率。"""
    base = nomura.read(lines)
    doc = base.document
    fields = dict(base.fields)
    art7 = nomura.article_lines(doc, 7)
    t7 = TextIndex(art7)
    annual = capture("coupon_pa_pct", t7, ANNUAL, Decimal, whole_match=True)
    fields["coupon_pa_pct"] = annual
    for name in ("coupon_period_pct", "monthly_coupon_pct", "coupon_art15"):
        fields[name] = standard_fields.absent(name, "每期配息率（範本只寫公式「年化配息率/12」）")
    fields["coupon_barrier_pct"] = _coupon_barrier(t7, fields["strike_pct"])
    fields["guaranteed_periods"] = _guaranteed(t7)
    mentions = _article15(nomura.article_lines(doc, 15))
    _article14(fields, nomura.article_lines(doc, 14))
    return nomura.NomuraTermSheet(fields, base.full_text, doc, mentions)


def _found(name: str, t7: TextIndex, text: str, what: str) -> tuple[list[Line], ParsedField | None]:
    """第 7 項固定寫法 `text` 所在的原文行；找不到時另回傳讀不到的欄位。"""
    a = same_chars(t7.text).find(same_chars(text))  # 換字不改長度，位置照用
    if a < 0:
        return [], ParsedField.missing(name, f"第 7 項找不到{what}的現行版寫法")
    return t7.lines_for(a, a + len(text)), None


def _coupon_barrier(t7: TextIndex, strike: ParsedField) -> ParsedField:
    """實際日數比的是「其各自執行價」→ 配息門檻 = 標的表表頭執行價 %（範本規格 §4.1）；證據掛定義句與表頭。"""
    name = "coupon_barrier_pct"
    lns, missing = _found(name, t7, ACTUAL_DAYS, "實際日數定義")
    if missing is not None:
        return missing
    pf = derived(name, strike, strike.value)
    return ParsedField(name, pf.status, pf.value, [*map(Evidence.of, lns), *pf.evidence], note=pf.note)


def _guaranteed(t7: TextIndex) -> ParsedField:
    """配息公式 (i)「就第一個配息日而言」→ 保證期數 1（範本規格 §4.2）。"""
    name = "guaranteed_periods"
    lns, missing = _found(name, t7, FORMULA, "配息公式")
    if missing is not None:
        return missing
    return ParsedField.present(name, 1, lns)


def _article15(art15: list[Line]) -> list[Mention]:
    """第 15 項 (1) 的兩處年化報酬率（第一個配息日、其他配息日）：各要等於第 7 項年化配息率。"""
    t15 = TextIndex(art15)
    found = list(t15.finditer(ART15))
    where = ("第 15 項(1) 第一個配息日年化報酬率", "第 15 項(1) 其他配息日年化報酬率")
    if len(found) != 1:
        return [Mention(w, None, ()) for w in where]
    m = found[0]
    lns = tuple(map(Evidence.of, t15.lines_for(m.start(), m.end())))
    return [Mention(w, Decimal(m[k]), lns) for k, w in enumerate(where, 1)]


def _article14(fields: dict[str, ParsedField], art14: list[Line]) -> None:
    """設定參數的年化配息率、情境一年化報酬率（交給共用情境規則和年利率比）；情境二的實際日數比例與年化報酬率（重算）。"""
    t14 = TextIndex(art14)
    a = re.search(r"設定參數[:：]", t14.text)
    b = t14.text.find("情境一（")
    params = t14.lines_for(a.end(), b) if a and b > a.end() else []
    fields["coupon_art14"] = capture("coupon_art14", TextIndex(params), r"年化配息率" + nomura.PCT, Decimal)
    sections = {k: t14.text.find(f"情境{k}（") for k in ("一", "二", "三")}
    returns = hits(t14, SCENARIO_RETURN, lambda m: Decimal(m[1]))
    accruals = hits(t14, ACCRUAL_PCT, lambda m: Decimal(m[1]))
    fields["scenario1_return"] = _one_in(t14, returns, sections, "一", "二", "scenario1_return", "年化報酬率")
    fields["scenario2_return"] = _one_in(t14, returns, sections, "二", "三", "scenario2_return", "年化報酬率")
    fields["accrual_scenario_pct"] = _one_in(
        t14, accruals, sections, "二", "三", "accrual_scenario_pct", "「實際日數/總日數」是P%"
    )
    where = "第 14 項設定參數"
    params_out = []
    for p in fields["scenario_params"].value:
        if p.code == "scenario_monthly_coupon":  # FCN 的「固定配息率」改成 DACN 的「年化配息率」（年利率）
            p = ScenarioParam("scenario_coupon_pa", where, fields["coupon_art14"])
        params_out.append(p)
    params_out.append(ScenarioParam("scenario_annualized_return", "第 14 項情境一", fields["scenario1_return"]))
    fields["scenario_params"] = standard_fields.occurrences("scenario_params", params_out)


def _one_in(
    t14: TextIndex, found: list[Hit], sections: dict[str, int], k: str, nxt: str, name: str, what: str
) -> ParsedField:
    """情境 `k` 段落（到情境 `nxt` 之前）裡剛好一處的值；找不到這段、沒有或多處 → 讀不到。"""
    lo = sections[k]
    if lo < 0:
        return ParsedField.missing(name, f"找不到第 14 項「情境{k}（」段落")
    hi = sections[nxt] if sections[nxt] >= 0 else len(t14.text)
    hit = one(nomura._in_section(t14, found, lo, hi))
    if hit is None:
        note = f"第 14 項情境{k}「{what}」缺漏、重複或不在範本規格內"
        return ParsedField.invalid(name, t14.lines_for(lo, min(hi, lo + 1)), note)
    return ParsedField.present(name, hit.value, hit.lines)
