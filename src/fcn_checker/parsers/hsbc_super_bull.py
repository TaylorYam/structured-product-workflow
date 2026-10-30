"""HSBC Super Bull 中文產品說明書（docs/checker/templates/hsbc-zh-super-bull.md）：版面、封面、標的表與第二章以後同 HSBC FCN
（parsers/hsbc.py），先照 FCN 讀出，再改讀 Super Bull 不同的條目（第 11 條沒有配息、四欄提前贖回表、無 KI 與到期公式；
第 12 條 (1) 價格表欄序；第 16、17 條沒有配息；第 18 條假設與情境一～三）。

只支援現行版範本；認得出是 HSBC Super Bull、但現行版特徵不齊時辨識交出「不支援的範本版本」（範本規格 §1、§8）。
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import NamedTuple

from .. import standard_fields
from ..schema import DetectionResult, Evidence, FieldStatus, Line, ParsedField
from ..standard_fields import Money, ScenarioParam, ScenarioPrice
from . import hsbc, money, price_table
from .common import Hit, Mention, dec, derived, exactly_one, one
from .layout import TextIndex
from .super_bull import Wording, fill_schedule

TEMPLATE_ID = "hsbc-zh-sb-pd"
PARSER_VERSION = "1"
OLD_VERSION = "HSBC Super Bull 說明書的寫法和現行版不同"
ISSUER_ZH = "香港上海滙豐銀行"
NAME_ZH_MARK = "自動提前贖回股權連結"
NAME_EN_MARK = "SuperBullNotes"  # 去空白
MUL = "[×xX]"
PCT = r"(\d+(?:\.\d+)?)%"
# 第 11 條 (1) 贖回金額句（範本規格 §2）
KO_REDEMPTION = rf"支付贖回金額=每單位面額{MUL}\(100%\+自動提前贖回配息\)"
# 四欄提前贖回表頭：擷取文字會把表頭拆開（`自動提前贖回價`／`自動提前贖回`／`自動提前贖`／`贖回價`／`決定日`／`回付款日`）
TABLE_HEAD = r"自動提前贖回價自動提前贖回(?:自動提前贖贖回價決定日回付款日|決定日自動提前贖回付款日贖回價)"
SLASH_DATE = r"(\d{4}/\d{1,2}/\d{1,2})"
# 一列一格一行：自動提前贖回價 %、決定日、付款日、贖回價 %（相鄰數字行之間有 `|`，見 hsbc.ScenarioIndex）
TABLE_ROW = rf"{PCT}\|?{SLASH_DATE}\|{SLASH_DATE}\|?{PCT}"
EVENT = (
    "自動提前贖回事件：若於任一自動提前贖回決定日，所有連結標的之收盤價皆大於或等於其自動提前贖回價，"
    "則自動提前贖回事件視同發生。"
)
BONUS = r"自動提前贖回配息為" + PCT
STRIKE = rf"執行價為期初股價{MUL}" + PCT
NO_KI = r"\(1\)若最差表現之連結標的期末股價小於執行價"
MATURITY_FORMULA = (
    NO_KI + r"，以實物給付最差表現連結標的之股份數[(（]簡稱為「連結標的股數」[)）]及以現金支付零股部分。"
    r"\(2\)若最差表現之連結標的期末股價大於或等於執行價[:：]以現金給付"
    rf"每單位面額{MUL}\(最差表現連結標的期末股價/最差表現連結標的執行價\)"
)
NO_COUPON_ART16 = "配息資料及其計算公式[:：]本商品不適用"
MIN_RATE = r"\(1\)最低保證配息率[:：]本商品不適用"
PARTICIPATION = r"\(2\)參與率[:：]本商品不適用"
MONTH_TOLERANCE = 7  # 決定日和「發行日 + m 個月」前後最多差幾個日曆天（範本規格 §4.1；樣本都是當天）
WORDS = Wording("HSBC", "決定日", "付款日", "最後評價日")
TABLE_NAME = "第 11 條(1) 提前贖回表"
# 第 12 條 (1)：自動提前贖回價欄在執行價欄前面（欄序依表頭 x 位置）；沒有觸及不保本價格欄
PRICE_TABLE = replace(
    hsbc.PRICE_TABLE,
    columns=(price_table.Column("ko", "自動提前贖回價"), price_table.Column("strike", "執行價")),
)
SCENARIO_TABLE = replace(PRICE_TABLE, where="第一章第 18 條情境價格表")
# HSBC FCN 才有的欄位（配息表、配息期數、每日觀察起日、第 1 期整期配息句）：Super Bull 讀不到，也不交給規則
FCN_ONLY = ("coupon_table", "coupon_periods", "ko_start", "first_period_full_coupon")


# ---------------------------------------------------------------- 辨識


def name_problems(zh: str | None, en: str | None) -> list[str]:
    """商品家族的名稱條件（說明書、投資人須知共用；去空白後的名稱）：中文以發行機構開頭、有 Super Bull 的名稱、沒有
    「固定配息」，英文有 `SuperBull Notes`。"""
    out = []
    if not (zh and zh.startswith(ISSUER_ZH) and NAME_ZH_MARK in zh and "固定配息" not in zh):
        out.append(f"商品中文名稱不是以「{ISSUER_ZH}」開頭、沒有「{NAME_ZH_MARK}」或有「固定配息」")
    if not (en and NAME_EN_MARK in en):
        out.append("商品英文名稱沒有「SuperBull Notes」")
    return out


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §8）：HSBC 版面與 Super Bull 名稱（條件 1～3）都成立後，現行版特徵（條件 4）全部成立才是現行版；
    缺任一項 → 不支援的範本版本（錯訊列出缺的特徵）。"""
    doc = hsbc.document(lines)
    cover = TextIndex(doc.before_chapter1()).text
    arts = doc.articles(1)
    zh = re.search(r"商品代號/商品中文名稱[:：]\d{12}/(.+?)商品英文名稱[:：]", cover)
    en = re.search(r"商品英文名稱[:：](.+?)商品種類[:：]", cover)
    evidence = [Evidence.of(x) for x in doc.before_chapter1()[:2]]
    family = (
        (
            "中文產品說明書" in cover and "發行機構：香港商香港上海滙豐銀行股份有限公司" in cover,
            "封面缺少中文產品說明書或發行機構不是HSBC",
        ),
        (len(doc.chapters) == 5 and set(range(1, 32)) <= set(arts), "章節不完整或第一章條號 1–31 不連續"),
    )
    failed = [message for ok, message in family if not ok]
    failed += name_problems(zh and zh[1], en and en[1])
    if failed:
        return DetectionResult(False, failed, evidence)

    def text(lns: list[Line]) -> str:
        return TextIndex(lns).text

    subs11 = doc.subitems(arts.get(11))
    s1, s2 = (text(doc.span_lines(subs11.get(k))) for k in (1, 2))
    current = (
        (
            re.search(KO_REDEMPTION, s1) is not None,
            "第 11 條(1) 贖回金額不是「每單位面額 × (100% + 自動提前贖回配息)」",
        ),
        (
            re.search(TABLE_HEAD, s1) is not None,
            "第 11 條(1) 提前贖回表不是「自動提前贖回價／決定日／付款日／贖回價」四欄",
        ),
        (re.search(MATURITY_FORMULA, s2) is not None, "第 11 條(2) 到期公式不是「每單位面額 x (期末股價/執行價)」"),
        (re.search(NO_COUPON_ART16, text(doc.span_lines(arts.get(16)))) is not None, "第 16 條不是「本商品不適用」"),
        (
            all(re.search(p, text(doc.span_lines(arts.get(17)))) for p in (MIN_RATE, PARTICIPATION)),
            "第 17 條 (1)(2) 不是「本商品不適用」",
        ),
    )
    missing = [message for ok, message in current if not ok]
    if missing:
        return DetectionResult(False, missing, evidence, unsupported=OLD_VERSION)
    return DetectionResult(True, [], evidence)


# ---------------------------------------------------------------- 第 11 條


def _slash_date(s: str) -> dt.date | None:
    try:
        return dt.date(*map(int, s.split("/")))
    except ValueError:
        return None


def _ko_table(lines: list[Line]) -> ParsedField:
    """(1) 四欄提前贖回表（範本規格 §4.1）：表頭之後每列「自動提前贖回價 %、決定日、付款日、贖回價 %」，沒有列號；表後
    緊接事件句。"""
    name = "ko_table"
    ti = hsbc.ScenarioIndex(lines)
    head = re.search(TABLE_HEAD, ti.text)
    if head is None:
        return ParsedField.missing(name, f"{TABLE_NAME}找不到四欄表頭")
    rows, pos = [], head.end()
    row = re.compile(TABLE_ROW)
    while (m := row.match(ti.text, pos)) is not None:
        rows.append(
            {
                "ko": Decimal(m[1]),
                "decision": _slash_date(m[2]),
                "payment": _slash_date(m[3]),
                "redemption": Decimal(m[4]),
            }
        )
        pos = m.end()
    lns = ti.lines_for(head.start(), pos)
    if not rows or any(r["decision"] is None or r["payment"] is None for r in rows):
        return ParsedField.invalid(name, lns, f"{TABLE_NAME}的列讀不到")
    if not ti.text.startswith("自動提前贖回事件：", pos):
        return ParsedField.invalid(name, ti.lines_for(head.start(), pos + 20), f"{TABLE_NAME}後有表格以外的文字")
    return ParsedField.present(name, rows, lns)


def _row_ko(table: ParsedField) -> ParsedField:
    """KO% = 提前贖回表各列的自動提前贖回價（每列相同才交出；不同 → 歧義，轉人工覆核）。"""
    name = "ko_pct"
    if not table.ok:
        return derived(name, table)
    kos = sorted({r["ko"] for r in table.value})
    if len(kos) > 1:
        return ParsedField(
            name, FieldStatus.AMBIGUOUS, None, list(table.evidence), kos, f"{TABLE_NAME}各列自動提前贖回價不同"
        )
    return ParsedField(name, FieldStatus.PRESENT, kos[0], list(table.evidence))


def _ki_type(fields: dict[str, ParsedField], maturity: list[Line], whole: TextIndex) -> None:
    """無 KI（範本規格 §3）：全文沒有「觸及不保本」、價格表沒有觸及不保本價格欄、(2) 到期 (1) 直接比執行價，三者都成立。"""
    problems = []
    if "觸及不保本" in whole.text:
        problems.append("說明書有觸及不保本")
    table = fields["price_table"]
    if table.ok and "ki" in table.value.columns:
        problems.append("價格表有觸及不保本價格欄")
    if re.search(NO_KI, TextIndex(maturity).text) is None:
        problems.append("第 11 條(2) 的 (1) 不是「若最差表現之連結標的期末股價小於執行價」")
    if problems:
        note = "無法判定無 KI：" + "；".join(problems)
        fields["ki_type"] = ParsedField.invalid("ki_type", maturity, note)
        fields["ki_pct"] = ParsedField.invalid("ki_pct", maturity, note)
        return
    fields["ki_type"] = ParsedField.present("ki_type", "none", maturity)
    fields["ki_pct"] = ParsedField("ki_pct", FieldStatus.NOT_APPLICABLE, evidence=[Evidence.of(x) for x in maturity])


def _ko_memory(lines: list[Line]) -> ParsedField:
    """(1) 事件句：非記憶式句 → False；其他句型 → 不合法（範本規格 §3）。"""
    pf = exactly_one("ko_memory", TextIndex(lines), re.escape(EVENT), "第 11 條(1) 自動提前贖回事件不是非記憶式句型")
    if not pf.ok:
        return ParsedField.invalid("ko_memory", lines, pf.note)
    return ParsedField("ko_memory", FieldStatus.PRESENT, False, list(pf.evidence))


def _article11(fields: dict[str, ParsedField], ko: list[Line], maturity: list[Line], whole: TextIndex) -> None:
    fields["ko_table"] = _ko_table(ko)
    fields["ko_pct"] = _row_ko(fields["ko_table"])
    fields["ko_memory"] = _ko_memory(ko)
    fields["coupon_pa_pct"] = exactly_one(
        "coupon_pa_pct", TextIndex(ko), BONUS, "第 11 條(1) 找不到「自動提前贖回配息為x%」", lambda m: Decimal(m[1])
    )
    fields["ko_redemption_formula"] = exactly_one(
        "ko_redemption_formula",
        TextIndex(ko),
        KO_REDEMPTION,
        "第 11 條(1) 贖回金額不是「每單位面額 × (100% + 自動提前贖回配息)」",
    )
    fields["strike_pct"] = exactly_one(
        "strike_pct",
        TextIndex(maturity),
        STRIKE,
        "第 11 條(2) 找不到「執行價為期初股價 × x%」",
        lambda m: Decimal(m[1]),
    )
    fields["maturity_formula"] = exactly_one(
        "maturity_formula",
        TextIndex(maturity),
        MATURITY_FORMULA,
        "第 11 條(2) 到期公式不是「(1) 小於執行價實物給付…(2) 大於或等於執行價：每單位面額 x (期末股價/執行價)」",
    )
    _ki_type(fields, maturity, whole)


def _schedule(fields: dict[str, ParsedField]) -> None:
    """提前贖回表只列決定日（範本規格 §4.1）：決定日和「發行日 + m 個月」前後差 7 個日曆天內。"""
    table = fields["ko_table"]
    fill_schedule(
        fields,
        table,
        lambda: [(n, r["decision"]) for n, r in enumerate(table.value, start=1)],
        table_name=TABLE_NAME,
        words=WORDS,
        early=MONTH_TOLERANCE,
        late=MONTH_TOLERANCE,
    )


# ---------------------------------------------------------------- 第 18 條情境（範本規格 §5）


CCY = money.CURRENCY
# 期末匯率兩邊的幣別字（範本規格 §5：`1 {幣別} = {幣別} 1.00`）；共用 `amounts` 不收匯率裡的金額，另交一處出處
FX_CURRENCY = rf"期末匯率為1({CCY})=({CCY})[\d.]+"
N = r"([\d,]+(?:\.\d+)?)"
SN = r"(-?[\d,]+(?:\.\d+)?)"
HEADING = r"情境分析([一二三四五六七八九十]+)\)"
# 情境標題的辨識字（以標題判斷，不以編號；核對規則 §3.7）
TITLES = (
    ("情境分析一)", "於第1個自動提前贖回決定日"),
    ("情境分析二)", "不曾發生任何自動提前贖回事件"),
    ("情境分析三)", "低於執行價"),
)
WORST = r"最差表現連結標的[（(](.+?)[）)]期末股價為"
S1_AMOUNT = rf"每單位面額{MUL}\(100%\+自動提前贖回配息\)={CCY}{N}{MUL}\(100%\+{PCT}\)={CCY}{N}"
S2_AMOUNT = rf"每單位面額{MUL}\(最差表現連結標的期末股價/最差表現連結標的執行價\)={CCY}{N}{MUL}\({N}/{N}\)={CCY}{N}"
S3_SHARES = rf"每單位面額{MUL}期末匯率/最差表現連結標的執行價={CCY}{N}{MUL}{N}/{CCY}{N}={N}股"
S3_WHOLE = r"在此例中為(\d+)股"
S3_FRACTION = rf"零股數\(即{N}股\)"
S3_REMAINDER = rf"期末股價{MUL}零股數/期末匯率={CCY}{N}{MUL}{N}/{N}={CCY}{N}"
S3_VALUE = rf"=\({CCY}{N}{MUL}(\d+)/{N}\)\+{CCY}{N}={CCY}{N}\(四捨五入至小數點後第二位\)"
# 損益：「…-每單位面額期初投資金額 = A - N = 損益」
PNL = rf"-每單位面額期初投資金額={CCY}{N}-{CCY}{N}={CCY}{SN}"
ANNUALIZED = r"平均年化報酬率\(以簡單平均年化報酬率之方式計算\)為?[:：](-?\d+(?:\.\d+)?)%"


class AutocallCalc(NamedTuple):
    """情境一「= N × (100% + x%) = A」。"""

    denomination: Decimal
    bonus: Decimal
    amount: Decimal


class BestCalc(NamedTuple):
    """情境二「= N × (P/K) = A」。"""

    denomination: Decimal
    close: Decimal
    strike: Decimal
    amount: Decimal


class SharesCalc(NamedTuple):
    """情境三「= N × 匯率 / K = S 股」。"""

    denomination: Decimal
    fx: Decimal
    strike: Decimal
    shares: Decimal


class RemainderCalc(NamedTuple):
    """情境三「= P × F / 匯率 = R」。"""

    close: Decimal
    fraction: Decimal
    fx: Decimal
    amount: Decimal


class ValueCalc(NamedTuple):
    """情境三「= (P × W / 匯率) + R = V」。"""

    close: Decimal
    whole: Decimal
    fx: Decimal
    remainder: Decimal
    amount: Decimal


class Pnl(NamedTuple):
    """損益「= A - N = 損益」。"""

    amount: Decimal
    investment: Decimal
    pnl: Decimal


@dataclass
class SbScenarios:
    """情境一～三的算式；找不到、或在情境裡有多處時為 None（由規則轉人工覆核）。`titles` 為三個情境標題的原文行
    （情境不是剛好這三個時三個都空）。"""

    titles: tuple[list[Line], list[Line], list[Line]]
    autocall: Hit[AutocallCalc] | None = None
    autocall_pnl: Hit[Pnl] | None = None
    autocall_annualized: Hit[Decimal] | None = None
    best_name: Hit[str] | None = None
    best: Hit[BestCalc] | None = None
    best_pnl: Hit[Pnl] | None = None
    best_annualized: Hit[Decimal] | None = None
    worse_name: Hit[str] | None = None
    shares: Hit[SharesCalc] | None = None
    whole: Hit[Decimal] | None = None
    fraction: Hit[Decimal] | None = None
    remainder: Hit[RemainderCalc] | None = None
    value: Hit[ValueCalc] | None = None
    worse_pnl: Hit[Pnl] | None = None
    worse_annualized: Hit[Decimal] | None = None

    @property
    def found(self) -> bool:
        return all(self.titles)


def _pnl(m: re.Match[str]) -> Pnl:
    return Pnl(dec(m[1]), dec(m[2]), dec(m[3]))


def _scenarios(ti: hsbc.ScenarioIndex) -> SbScenarios:
    """以標題切出情境一～三（到下一個標題或第 18 條結束）；情境要剛好這三個、依序，標題有各自的辨識字（核對規則 §3.7）。"""
    headings = list(ti.finditer(HEADING))
    if [m[0] for m in headings] != [t for t, _ in TITLES]:
        return SbScenarios(([], [], []))
    bounds = [m.start() for m in headings] + [len(ti.text)]
    if not all(mark in ti.text[a : a + 80] for (_, mark), a in zip(TITLES, bounds, strict=False)):
        return SbScenarios(([], [], []))
    titles = tuple(ti.lines_for(m.start(), m.end()) for m in headings)
    out = SbScenarios(titles)  # type: ignore[arg-type]

    def find(n: int, pattern: str, convert):
        lo, hi = bounds[n - 1], bounds[n]
        return one(
            [
                Hit(convert(m), tuple(ti.lines_for(m.start(), m.end())))
                for m in ti.finditer(pattern)
                if lo <= m.start() < hi
            ]
        )

    def annualized(m: re.Match[str]) -> Decimal:
        return Decimal(m[1])

    out.autocall = find(1, S1_AMOUNT, lambda m: AutocallCalc(dec(m[1]), Decimal(m[2]), dec(m[3])))
    out.autocall_pnl = find(1, PNL, _pnl)
    out.autocall_annualized = find(1, ANNUALIZED, annualized)
    out.best_name = find(2, WORST, lambda m: m[1])
    out.best = find(2, S2_AMOUNT, lambda m: BestCalc(dec(m[1]), dec(m[2]), dec(m[3]), dec(m[4])))
    out.best_pnl = find(2, PNL, _pnl)
    out.best_annualized = find(2, ANNUALIZED, annualized)
    out.worse_name = find(3, WORST, lambda m: m[1])
    out.shares = find(3, S3_SHARES, lambda m: SharesCalc(dec(m[1]), dec(m[2]), dec(m[3]), dec(m[4])))
    out.whole = find(3, S3_WHOLE, lambda m: Decimal(m[1]))
    out.fraction = find(3, S3_FRACTION, lambda m: dec(m[1]))
    out.remainder = find(3, S3_REMAINDER, lambda m: RemainderCalc(dec(m[1]), dec(m[2]), dec(m[3]), dec(m[4])))
    out.value = find(3, S3_VALUE, lambda m: ValueCalc(dec(m[1]), Decimal(m[2]), dec(m[3]), dec(m[4]), dec(m[5])))
    out.worse_pnl = find(3, PNL, _pnl)
    out.worse_annualized = find(3, ANNUALIZED, annualized)
    return out


def _worst_row(name: Hit[str] | None, prices: ParsedField) -> int | None:
    """情境寫的最差標的（「（中文名稱）」）→ 價格表第幾檔：單一標的為第 1 檔；對不到為 None（共用規則轉人工覆核）。"""
    if not prices.ok:
        return None
    if len(prices.value) == 1:
        return 1
    if name is None:
        return None
    rows = [n for n, r in enumerate(prices.value, start=1) if r.name and r.name.replace(" ", "") == name.value]
    return rows[0] if len(rows) == 1 else None


def _assumed_bonus(ti: hsbc.ScenarioIndex, end: int) -> ParsedField:
    """基本假設「自動提前贖回配息為x%」（或「：x%」）：除了一個 % 數值外還有其他字（樣本有殘留數字）→ 值為原文，由規則判不一致。"""
    name = "bonus_assumed"
    found = [m for m in ti.finditer(r"自動提前贖回配息[為:：](.+?)彭博代號") if m.start() < end]
    if len(found) != 1:
        return ParsedField.missing(name, "第 18 條假設找不到「自動提前贖回配息為x%」")
    m = found[0]
    lns = ti.lines_for(m.start(), m.start(1) + len(m[1]))
    single = re.fullmatch(PCT, m[1])
    return ParsedField.present(name, Decimal(single[1]) if single else m[1], lns)


def fx_currency(art18: list[Line]) -> standard_fields.Occurrence:
    """期末匯率兩邊的幣別字：承作幣別的其他出處（共用 `money.amounts` 不收匯率裡的金額）。"""
    ti = hsbc.ScenarioIndex(art18)
    found = [
        Money(m[0], m[g], tuple(Evidence.of(x) for x in ti.lines_for(m.start(), m.end())))
        for m in ti.finditer(FX_CURRENCY)
        for g in (1, 2)
    ]
    return money.scenario_occurrence(
        "scenario_fx_currency", "scenario_assumption_currency", "第 18 條情境假設期末匯率", found, art18[:1]
    )


def _article18(fields: dict[str, ParsedField], art18: list[Line]) -> SbScenarios:
    """基本假設（天期、面額、發行價格、期初投資金額、期末匯率、出場紅利）、情境價格表、情境一～三的算式（範本規格 §5）。"""
    ti = hsbc.ScenarioIndex(art18)
    first = re.search(HEADING, ti.text)
    end = first.start() if first else len(ti.text)
    where = "第 18 條情境假設"

    def assumed(code: str, pattern: str, convert) -> ParsedField:
        found = [m for m in ti.finditer(pattern) if m.start() < end]
        if len(found) != 1:
            return ParsedField.missing(code, f"{where}找不到或重複：{pattern}")
        m = found[0]
        return ParsedField.present(code, convert(m[1]), ti.lines_for(m.start(), m.end()))

    fields["scenario_fx"] = assumed("scenario_fx", rf"期末匯率為1{CCY}={CCY}{N}", dec)
    fields["bonus_assumed"] = _assumed_bonus(ti, end)
    params = [
        ScenarioParam("scenario_tenor", where, assumed("scenario_tenor", r"商品天期為(\d+)個月期", int)),
        ScenarioParam(
            "scenario_denomination", where, assumed("scenario_denomination", rf"每單位面額為{CCY}{N}元", dec)
        ),
        ScenarioParam("scenario_issue_price", where, assumed("scenario_issue_price", r"發行價格為" + PCT, Decimal)),
        ScenarioParam(
            "scenario_initial_investment",
            where,
            assumed("scenario_initial_investment", rf"每單位期初投資金額={CCY}{N}", dec),
        ),
    ]
    fields["scenario_params"] = standard_fields.occurrences("scenario_params", params)
    fields["scenario_table"] = _scenario_table(art18, ti, end)
    fields["scenario_reprint_table"] = price_table.at(
        "scenario_reprint_table", fields["scenario_table"], SCENARIO_TABLE
    )
    sc = _scenarios(ti)
    prices = []
    for n, name, calc in ((2, sc.best_name, sc.best), (3, sc.worse_name, sc.shares)):
        place = f"第 18 條情境分析{'二三'[n - 2]})" + ("到期贖回金額算式的" if n == 2 else "股數算式的")
        strike = Mention.of_optional_hit(place, calc, lambda v: v.strike, sc.titles[n - 1]).parsed(
            f"scenario{n}_strike"
        )
        prices.append(ScenarioPrice(place, _worst_row(name, fields["underlying_prices"]), "strike", strike))
    fields["scenario_prices"] = standard_fields.occurrences("scenario_prices", prices)
    return sc


def _scenario_table(art18: list[Line], ti: hsbc.ScenarioIndex, end: int) -> ParsedField:
    """情境價格表：基本假設「自動提前贖回配息」那一行之後、情境分析一之前。沒有中文名稱欄；交易所欄有樣本放中文名稱，
    幣別與交易所不比（核對規則 §2），只讀不驗。"""
    found = [m for m in ti.finditer(r"自動提前贖回配息[為:：]") if m.start() < end]
    if len(found) != 1 or end == len(ti.text):
        return ParsedField.missing("scenario_table", "情境價格表錨點缺漏／重複")
    a = art18.index(ti.lines_for(found[0].start(), found[0].end())[-1]) + 1
    b = art18.index(ti.lines_for(end, end + 1)[0])
    return price_table.read("scenario_table", art18[a:b], SCENARIO_TABLE)


# ---------------------------------------------------------------- 讀出


@dataclass
class HsbcSuperBullTermSheet(hsbc.HsbcTermSheet):
    sb_scenarios: SbScenarios
    bonus_mentions: list[
        Mention
    ]  # 出場紅利的其他出處（提前贖回表贖回價、基本假設、情境一；正式值是第 11 條(1) 的 `coupon_pa_pct`）


def read(lines: Sequence[Line]) -> HsbcSuperBullTermSheet:
    """先照 HSBC FCN 讀出，再改讀 Super Bull 不同的條目（範本辨識另由 `detect` 負責）。"""
    base = hsbc.read(lines)
    doc, fields = base.document, dict(base.fields)
    arts = doc.articles(1)

    def article(n: int) -> list[Line]:
        return doc.span_lines(arts.get(n))

    def sub(n: int, k: int) -> list[Line]:
        return doc.span_lines(doc.subitems(arts.get(n)).get(k))

    for name in FCN_ONLY:
        fields.pop(name, None)
    fields["monthly_coupon_pct"] = standard_fields.absent("monthly_coupon_pct", "月配息率（Super Bull 沒有配息）")

    # 第 12 條 (1) 價格表（KO 欄在執行價欄前面）、第 11 條、提前贖回表
    fields["price_table"] = hsbc.checked_price_table("price_table", sub(12, 1), PRICE_TABLE)
    fields["underlyings"] = price_table.underlyings(fields["price_table"])
    fields["underlying_prices"] = price_table.price_rows(
        fields["price_table"],
        names=[r.texts.get("label") or None for r in fields["price_table"].value.rows]
        if fields["price_table"].ok
        else None,
    )
    fields["main_price_table"] = price_table.at("main_price_table", fields["price_table"], PRICE_TABLE)
    _article11(fields, sub(11, 1), sub(11, 2), base.full_text)
    _schedule(fields)

    # 第 16、17 條：沒有配息、參與率
    ti16, ti17 = TextIndex(article(16)), TextIndex(article(17))
    fields["no_coupon_art16"] = exactly_one("no_coupon_art16", ti16, NO_COUPON_ART16, "第 16 條不是「本商品不適用」")
    fields["no_coupon_art17"] = exactly_one(
        "no_coupon_art17", ti17, MIN_RATE, "第 17 條 (1) 最低保證配息率不是「本商品不適用」"
    )
    fields["participation_rate"] = exactly_one(
        "participation_rate", ti17, PARTICIPATION, "第 17 條 (2) 參與率不是「本商品不適用」"
    )

    # 第 18 條情境；期末匯率的幣別字併入承作幣別的其他出處（field.currency）
    scenarios = _article18(fields, article(18))
    fields["currency_others"] = standard_fields.occurrences(
        "currency_others", [*fields["currency_others"].value, fx_currency(article(18))]
    )
    table = fields["ko_table"]
    rows = table.value if table.ok else []
    mentions = [
        *(
            Mention(f"{TABLE_NAME}第 {n} 列贖回價 − 100%", r["redemption"] - 100, tuple(table.evidence))
            for n, r in enumerate(rows, start=1)
        ),
        Mention.of_field("第 18 條情境假設「自動提前贖回配息為x%」", fields["bonus_assumed"]),
        Mention.of_optional_hit(
            "第 18 條情境分析一)「(100% + x%)」", scenarios.autocall, lambda v: v.bonus, scenarios.titles[0]
        ),
    ]
    return HsbcSuperBullTermSheet(fields, base.full_text, doc, base.scenarios, base.scenario_index, scenarios, mentions)
