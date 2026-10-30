"""HSBC BEN 中文產品說明書（docs/checker/templates/hsbc-zh-ben.md）：版面、封面、標的表與第二章以後同 HSBC FCN（parsers/hsbc.py），
先照 FCN 讀出，再改讀 BEN 不同的條目（第 11 條沒有配息與提前贖回、到期公式與紅利；第 12 條 (1) 沒有 KO 價欄；第 16、17 條；
第 18 條假設與情境一～三）。Maxi（紅利表現）與 Digital（紅利配息）兩型共用本 parser，型態由名稱決定。

只支援現行版範本；認得出是 HSBC BEN、但現行版特徵不齊時辨識交出「不支援的範本版本」（範本規格 §1、§8）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Literal, NamedTuple

from .. import standard_fields
from ..schema import DetectionResult, Evidence, FieldStatus, Line, ParsedField
from ..standard_fields import AutocallSchedule, ScenarioParam, ScenarioPrice
from ..text import squash
from . import hsbc, price_table
from .common import Hit, Mention, dec, derived, exactly_one, one
from .hsbc_super_bull import (
    CCY,
    MUL,
    PCT,
    S3_FRACTION,
    S3_REMAINDER,
    S3_VALUE,
    S3_WHOLE,
    SN,
    N,
    Pnl,
    RemainderCalc,
    SharesCalc,
    ValueCalc,
    fx_currency,
)
from .layout import TextIndex

TEMPLATE_ID = "hsbc-zh-ben-pd"
PARSER_VERSION = "1"
# 待使用者確認 BEN-375-08：版本依內容判斷、不看交易日；現行版特徵（`current_problems`）對不上 → 不支援的範本版本
OLD_VERSION = "HSBC BEN 說明書的寫法和現行版不同"
ISSUER_ZH = "香港上海滙豐銀行"
Kind = Literal["maxi", "digital"]
KIND_LABEL = {"maxi": "Maxi（紅利表現）", "digital": "Digital（紅利配息）"}
# 型態 → 中文名稱、英文名稱（去空白）的辨識字（範本規格 §8 條件 2、3）
NAME_MARKS: dict[Kind, tuple[str, str]] = {
    "maxi": ("紅利表現股權連結", "BonusEnhancedNotes"),
    "digital": ("紅利配息股權連結", "ReverseConvertiblewithDigitalCouponNotes"),
}
# 第 11 條開頭（沒有 KO、沒有定期配息的明確句，範本規格 §2）。待使用者確認：沒有 KO 只由這句、第 11 條沒有自動提前、第 15 條
# 沒有自動提前贖回決定日判定（`_no_autocall`），通用風險段的「自動提前到期」不看
OPENING: dict[Kind, str] = {
    "maxi": r"主要給付項目及其計算方式[:：]本商品主要給付項目為商品到期贖回金額\(1\)",
    "digital": r"主要給付項目及其計算方式[:：]本商品主要給付項目為配息金額與到期贖回金額，該金額視期末股價而定\(1\)",
}
STOCK_UNITS = r"[(（]簡稱為「連結標的股數」[)）]及以現金支付零股部分"
# 第 11 條(1) 到期公式：≥ 執行價（參與率由它推得，核對規則 §3.4）、< 執行價交股（直接比執行價：無 KI）
FORMULA: dict[Kind, str] = {
    "maxi": r"\(i\)若最差表現連結標的，其期末股價〔等於或大於〕執行價[:：]以現金給付每單位面額"
    + MUL
    + r"\{100%\+最大值\[紅利配息率[，,]\(最差表現連結標的期末股價/最差表現連結標的期初股價-1\)\]\}",
    "digital": r"如連結標的，其期末股價〔等於或大於〕其執行價[:：]以現金給付每單位面額" + MUL + r"100%(?![\d.])",
}
DELIVERY: dict[Kind, str] = {
    "maxi": r"\(ii\)若最差表現連結標的，其期末股價〔小於〕執行價[:：]以實物給付最差表現連結標的之股份數" + STOCK_UNITS,
    "digital": r"如連結標的，其期末股價〔小於〕其執行價[:：]以實物給付連結標的之股份數" + STOCK_UNITS,
}
# 待使用者確認 BEN-375-02：說明書不寫參與率，由第 11 條(1) 到期公式（`FORMULA`）推得；公式不是這兩種 → 讀不到、轉人工
PARTICIPATION: dict[Kind, Decimal] = {"maxi": Decimal(100), "digital": Decimal(0)}
# Digital 第 11 條(2) 配息金額：≥ 執行價付面額 × 紅利、< 執行價沒有紅利
DIGITAL_COUPON = (
    r"如最差表現連結標的，其期末股價於最後評價日〔等於或大於〕其執行價，下列金額將於到期日由發行機構支付予投資人[:：]"
    rf"以現金給付每單位面額{MUL}紅利配息率"
)
DIGITAL_NO_COUPON = r"如最差表現連結標的，其期末股價於最後評價日〔小於〕其執行價，則無紅利配息。"
# 紅利：Maxi 第 11 條定義段「紅利配息率為X%」、Digital 第 11 條(2)「紅利配息率：X% %」（待使用者確認 BEN-375-05：多的 % 只比數值）
BONUS = r"紅利配息率[為:：]" + PCT + "%?"
STRIKE = rf"執行價為期初股價{MUL}" + PCT
# 第 16 條：Maxi「本商品不適用」；Digital 說明句（待使用者確認 BEN-375-05：當固定寫法核對）
ART16: dict[Kind, str] = {
    "maxi": r"配息資料及其計算公式[:：]本商品不適用",
    "digital": r"配息資料及其計算公式[:：]請見上述第一章第11項之說明[,，]本商品於到期時連同到期贖回金額及應付之紅利配息"
    r"一次給付，並無因配息致投資標的價值而相對降低之情事。",
}
MIN_RATE = r"\(1\)最低保證配息率[:：]本商品不適用"
PARTICIPATION_NA = r"\(2\)參與率[:：]本商品不適用"
FINAL_EDITION = r"中文產品說明書\(最終版\)"
# 第 12 條 (1)：沒有 KO 價欄、沒有觸及不保本價格欄
PRICE_TABLE = replace(hsbc.PRICE_TABLE, columns=(price_table.Column("strike", "執行價"),))
SCENARIO_TABLE = replace(PRICE_TABLE, where="第一章第 18 條情境價格表")
# Digital 的情境價格表沒有交易所欄（範本規格 §5）
DIGITAL_SCENARIO_TABLE = replace(SCENARIO_TABLE, texts=tuple(c for c in PRICE_TABLE.texts if c.key != "exchange"))
# HSBC FCN 才有的欄位（配息表、配息期數、提前贖回表、每日觀察起日、第 1 期整期配息句）：BEN 讀不到，也不交給規則
FCN_ONLY = ("coupon_table", "coupon_periods", "ko_table", "ko_start", "first_period_full_coupon")


# ---------------------------------------------------------------- 辨識


def kind_of(zh: str | None, en: str | None) -> tuple[Kind | None, list[str]]:
    """商品家族與型態的名稱條件（說明書、投資人須知共用；去空白後的名稱）：中文以發行機構開頭、有 Maxi 或 Digital 的中文
    辨識字、沒有「固定配息」「自動提前」，英文有同一型態的辨識字。回傳（型態, 不成立的說明）。"""
    zh_kinds = [k for k, (mark, _) in NAME_MARKS.items() if zh and mark in zh]
    en_kinds = [k for k, (_, mark) in NAME_MARKS.items() if en and mark in en]
    out = []
    if not (zh and zh.startswith(ISSUER_ZH) and len(zh_kinds) == 1 and "固定配息" not in zh and "自動提前" not in zh):
        out.append(
            f"商品中文名稱不是以「{ISSUER_ZH}」開頭、沒有「紅利表現股權連結」或「紅利配息股權連結」、或有「固定配息」「自動提前」"
        )
    if len(en_kinds) != 1:
        out.append("商品英文名稱沒有「Bonus Enhanced Notes」或「Reverse Convertible with Digital Coupon Notes」")
    if not out and zh_kinds != en_kinds:
        out.append(f"中文名稱是 {KIND_LABEL[zh_kinds[0]]}、英文名稱是 {KIND_LABEL[en_kinds[0]]}，型態不同")
    return (zh_kinds[0] if not out else None), out


def _names(doc) -> tuple[str | None, str | None]:
    cover = TextIndex(doc.before_chapter1()).text
    zh = re.search(r"商品代號/商品中文名稱[:：]\d{12}/(.+?)商品英文名稱[:：]", cover)
    en = re.search(r"商品英文名稱[:：](.+?)商品種類[:：]", cover)
    return (zh[1] if zh else None), (en[1] if en else None)


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §8）：HSBC 版面與 BEN 名稱（條件 1～3）都成立後，該型態的現行版特徵（條件 4）全部成立才是現行版；
    缺任一項 → 不支援的範本版本（錯訊列出缺的特徵）。"""
    doc = hsbc.document(lines)
    cover = TextIndex(doc.before_chapter1()).text
    arts = doc.articles(1)
    evidence = [Evidence.of(x) for x in doc.before_chapter1()[:2]]
    family = (
        (
            "中文產品說明書" in cover and "發行機構：香港商香港上海滙豐銀行股份有限公司" in cover,
            "封面缺少中文產品說明書或發行機構不是HSBC",
        ),
        (len(doc.chapters) == 5 and set(range(1, 32)) <= set(arts), "章節不完整或第一章條號 1–31 不連續"),
    )
    failed = [message for ok, message in family if not ok]
    kind, problems = kind_of(*_names(doc))
    failed += problems
    if failed or kind is None:
        return DetectionResult(False, failed, evidence)
    missing = current_problems(doc, kind)
    if missing:
        return DetectionResult(False, missing, evidence, unsupported=OLD_VERSION)
    return DetectionResult(True, [], evidence)


def current_problems(doc, kind: Kind) -> list[str]:
    """該型態的現行版特徵（範本規格 §1、§8 條件 4）不成立的說明。"""
    arts = doc.articles(1)

    def text(n: int, k: int | None = None) -> str:
        span = arts.get(n) if k is None else doc.subitems(arts.get(n)).get(k)
        return TextIndex(doc.span_lines(span)).text

    art11 = text(11)
    label = KIND_LABEL[kind]
    checks = [
        (re.search(OPENING[kind], art11) is not None, f"第 11 條開頭不是 {label} 的「本商品主要給付項目為…」"),
        (re.search(FORMULA[kind], art11) is not None, f"第 11 條(1) 到期公式不是 {label} 的寫法"),
        (re.search(DELIVERY[kind], art11) is not None, f"第 11 條(1) 小於執行價交股句不是 {label} 的寫法"),
        ("自動提前" not in art11, "第 11 條有自動提前贖回"),
        (re.search(ART16[kind], text(16)) is not None, f"第 16 條不是 {label} 的寫法"),
        (
            all(re.search(p, text(17)) for p in (MIN_RATE, PARTICIPATION_NA)),
            "第 17 條 (1)(2) 不是「本商品不適用」",
        ),
    ]
    if kind == "digital":
        checks.append(
            (
                all(re.search(p, art11) for p in (DIGITAL_COUPON, DIGITAL_NO_COUPON)),
                "第 11 條(2) 配息金額不是「以現金給付每單位面額 × 紅利配息率」「則無紅利配息。」",
            )
        )
    return [message for ok, message in checks if not ok]


# ---------------------------------------------------------------- 第 11、15～17 條


def _no_autocall(fields: dict[str, ParsedField], kind: Kind, art11: list[Line], art15: list[Line]) -> None:
    """沒有 KO（核對規則 §3.5）：第 11 條開頭是該型態的固定句、第 11 條沒有提前贖回（表）、第 15 條沒有自動提前贖回決定日，
    三者都成立才判定；判定後 KO 每期觀察、非記憶式、Non-Call = 天期，比價日只有最後評價日（下單資料格式 §4）。"""
    problems = []
    opening = exactly_one("no_autocall", TextIndex(art11), OPENING[kind], f"第 11 條開頭不是 {KIND_LABEL[kind]} 的寫法")
    if not opening.ok:
        problems.append(opening.note)
    if "自動提前" in TextIndex(art11).text:
        problems.append("第 11 條有自動提前贖回")
    if "自動提前贖回決定日" in TextIndex(art15).text:
        problems.append("第 15 條有自動提前贖回決定日")
    if problems:
        note = "無法判定沒有 KO：" + "；".join(problems)
        fields["no_autocall"] = ParsedField.invalid("no_autocall", art11[:3], note)
    else:
        fields["no_autocall"] = ParsedField("no_autocall", FieldStatus.PRESENT, True, list(opening.evidence))
    maturity_only(fields, fields["no_autocall"])


def maturity_only(fields: dict[str, ParsedField], no_ko: ParsedField) -> None:
    """沒有 KO（`no_ko` 讀得到）的 BEN 交出的 KO 與排程欄位（各上手 BEN 共用）：KO% 不適用、每期觀察、非記憶式、沒有日期表、
    Non-Call = 天期、比價日只有最後評價日（期末定價日）。"""
    fields["ko_pct"] = (
        ParsedField("ko_pct", FieldStatus.NOT_APPLICABLE, evidence=list(no_ko.evidence), note="BEN 沒有 KO")
        if no_ko.ok
        else derived("ko_pct", no_ko)
    )
    fields["ko_observation"] = derived("ko_observation", no_ko, "P")
    fields["ko_memory"] = derived("ko_memory", no_ko, False)
    fields["schedule_table"] = standard_fields.absent("schedule_table", "日期表（BEN 不會提前出場）")
    tenor, final = fields["tenor_months"], fields["final_valuation_date"]
    bad = next((p for p in (no_ko, tenor, final) if not p.ok), None)
    if bad is not None:
        fields["first_callable_period"] = derived("first_callable_period", bad)
        fields["autocall_schedule"] = derived("autocall_schedule", bad)
        return
    ev = [*no_ko.evidence, *tenor.evidence, *final.evidence]
    fields["first_callable_period"] = ParsedField("first_callable_period", FieldStatus.PRESENT, tenor.value, ev)
    fields["autocall_schedule"] = ParsedField(
        "autocall_schedule",
        FieldStatus.PRESENT,
        AutocallSchedule("P", tenor.value, tenor.value, {tenor.value: final.value}, listed=True),
        ev,
    )


def _ki_type(fields: dict[str, ParsedField], kind: Kind, art11: list[Line], whole: TextIndex) -> None:
    """無 KI（核對規則 §3.5）：全文沒有「觸及」、價格表沒有觸及不保本價格欄、第 11 條(1) 交股句直接比執行價，三者都成立。"""
    problems = []
    if "觸及" in whole.text:
        problems.append("說明書有「觸及」")
    table = fields["price_table"]
    if table.ok and "ki" in table.value.columns:
        problems.append("價格表有觸及不保本價格欄")
    if re.search(DELIVERY[kind], TextIndex(art11).text) is None:
        problems.append("第 11 條(1) 小於執行價交股句不是直接比執行價")
    if problems:
        note = "無法判定無 KI：" + "；".join(problems)
        fields["ki_type"] = ParsedField.invalid("ki_type", art11[:3], note)
        fields["ki_pct"] = ParsedField.invalid("ki_pct", art11[:3], note)
        return
    fields["ki_type"] = ParsedField.present("ki_type", "none", art11)
    fields["ki_pct"] = ParsedField("ki_pct", FieldStatus.NOT_APPLICABLE, evidence=[Evidence.of(x) for x in art11])


def _article11(fields: dict[str, ParsedField], kind: Kind, art11: list[Line], coupon: list[Line]) -> None:
    """到期公式、交股句、參與率（由公式推得）、K%、紅利；Digital 另有 (2) 配息金額的兩句固定寫法。"""
    ti = TextIndex(art11)
    label = KIND_LABEL[kind]
    fields["maturity_formula"] = exactly_one(
        "maturity_formula", ti, FORMULA[kind], f"第 11 條(1) 大於或等於執行價的到期公式不是 {label} 的寫法"
    )
    fields["delivery_formula"] = exactly_one(
        "delivery_formula", ti, DELIVERY[kind], f"第 11 條(1) 小於執行價交股句不是 {label} 的寫法"
    )
    fields["participation_pct"] = derived("participation_pct", fields["maturity_formula"], PARTICIPATION[kind])
    fields["strike_pct"] = exactly_one(
        "strike_pct", ti, STRIKE, "第 11 條找不到「執行價為期初股價 × x%」", lambda m: Decimal(m[1])
    )
    where = "第 11 條" if kind == "maxi" else "第 11 條(2)"
    fields["coupon_pa_pct"] = exactly_one(
        "coupon_pa_pct", TextIndex(coupon), BONUS, f"{where}找不到「紅利配息率為x%」", lambda m: Decimal(m[1])
    )
    if kind == "digital":
        fields["digital_coupon"] = exactly_one(
            "digital_coupon",
            ti,
            DIGITAL_COUPON,
            "第 11 條(2) 大於或等於執行價不是「以現金給付每單位面額 × 紅利配息率」",
        )
        fields["digital_no_coupon"] = exactly_one(
            "digital_no_coupon", ti, DIGITAL_NO_COUPON, "第 11 條(2) 小於執行價不是「則無紅利配息。」"
        )


def _table_lines(lines: list[Line]) -> list[Line]:
    """第 12 條 (1) 的表：去掉表前的「(1)」「連結標的：」標籤行（標籤的左緣可能剛好對齊代碼的交易所碼，讓價格表把同一格
    的代碼 `ABCD UQ` 切成兩欄、認不出列）與表後的註記（`註3：[…]` 的數字會被當成價格；殘留註記另由 `_residue` 讀）。"""
    labels = [i for i, ln in enumerate(lines[:3]) if re.search(r"連結標的[:：]$", squash(ln.text))]
    body = lines[labels[0] + 1 :] if labels else lines
    notes = [i for i, ln in enumerate(body) if re.match(r"註\d+[:：]", squash(ln.text))]
    return body[: notes[0]] if notes else body


def _residue(lines: list[Line]) -> ParsedField:
    """第 12 條 (1) 的方括號殘留註記（Digital 樣本的 `註3：[…]`，核對規則 §3.5）：有 → 不合法（規則判不一致）。"""
    ti = TextIndex(lines)
    m = re.search(r"\[[^\]]*\]", ti.text)
    if m is not None:
        return ParsedField.invalid(
            "template_residue", ti.lines_for(m.start(), m.end()), "第 12 條(1) 標的表有方括號殘留註記（範本殘留）"
        )
    return ParsedField.present("template_residue", None, lines[:1])


# ---------------------------------------------------------------- 第 18 條情境（範本規格 §5）


HEADING = r"情境分析([一二三四五六七八九十]+)\)"
# 情境標題的辨識字（以標題判斷，不以編號；核對規則 §3.7）
TITLES: dict[Kind, tuple[tuple[str, str], ...]] = {
    "maxi": (("情境分析一)", "大於紅利配息率"), ("情境分析二)", "小於紅利配息率"), ("情境分析三)", "低於執行價")),
    "digital": (
        ("情境分析一)", "期末股價>=執行價"),
        ("情境分析二)", "期末股價<執行價"),
        ("情境分析三)", "期末股價為0元(<執行價)"),
    ),
}
WORST = r"連結標的[（(](.+?)[）)]期末股價為"
# Maxi 情境一、二「= N × {100% + 最大值 [x%，(P/I-1)]} = N × {100% + y%} = A」（情境二的括號寫法不齊，只取數字）
MAXI_AMOUNT = rf"={CCY}{N}{MUL}\{{100%\+最大值\[{PCT}[，,]\(?{N}/{N}-1\)?\]\}}?={CCY}{N}{MUL}\{{100%\+{PCT}\}}={CCY}{N}"
# Digital 情境一「每單位面額 × (100% + x%%) = N × (100% + x%%) = A」
DIGITAL_AMOUNT = rf"每單位面額{MUL}\(100%\+{PCT}%?\)={CCY}{N}{MUL}\(100%\+{PCT}%?\)={CCY}{N}"
# 交股：Digital 沒有「最差表現」
SHARES = rf"每單位面額{MUL}期末匯率/(?:最差表現)?連結標的執行價={CCY}{N}{MUL}{N}/{CCY}{N}={N}股"
# 損益：「…-每單位面額期初投資金額 = A - N = 損益」（Digital 寫「初始投資金額」）
PNL = rf"-每單位面額(?:期初|初始)投資金額={CCY}{N}-{CCY}{N}={CCY}{SN}"
# 年化：Digital 交股情境寫成算式「為:=美元 -5,000.00 / 美元 10,000 / 6 × 12 = -100.00%」，取最後的 %
ANNUALIZED = r"平均年化報酬率\(以簡單平均年化報酬率之方式計算\)為?[:：](?:=[^%]+?=)?(-?\d+(?:\.\d+)?)%"


class EnhancedCalc(NamedTuple):
    """Maxi 情境一、二「= N × {100% + 最大值 [x%，(P/I-1)]} = N × {100% + y%} = A」。"""

    denomination: Decimal
    bonus: Decimal
    close: Decimal
    initial: Decimal
    applied: Decimal
    amount: Decimal


class DigitalCalc(NamedTuple):
    """Digital 情境一「每單位面額 × (100% + x%) = N × (100% + x%) = A」。"""

    bonus_formula: Decimal
    denomination: Decimal
    bonus: Decimal
    amount: Decimal


@dataclass
class Delivery:
    """交股情境的算式（欄位名同 Super Bull 情境三，交給 `rules.hsbc_super_bull.worse` 重算）。"""

    shares: Hit[SharesCalc] | None = None
    whole: Hit[Decimal] | None = None
    fraction: Hit[Decimal] | None = None
    remainder: Hit[RemainderCalc] | None = None
    value: Hit[ValueCalc] | None = None
    worse_pnl: Hit[Pnl] | None = None
    worse_annualized: Hit[Decimal] | None = None


@dataclass
class BenScenarios:
    """情境一～三的算式；找不到、或在情境裡有多處時為 None（由規則轉人工覆核）。`titles` 為三個情境標題的原文行（情境不是
    剛好這三個時三個都空）。Maxi：情境一、二是 `enhanced`，情境三交股；Digital：情境一是 `digital`，情境二、三交股。"""

    kind: Kind
    titles: tuple[list[Line], list[Line], list[Line]]
    enhanced: dict[int, Hit[EnhancedCalc] | None] = field(default_factory=dict)
    enhanced_rows: dict[int, int | None] = field(default_factory=dict)  # 情境 → 最差標的是價格表第幾檔
    digital: Hit[DigitalCalc] | None = None
    deliveries: dict[int, Delivery] = field(default_factory=dict)
    pnl: dict[int, Hit[Pnl] | None] = field(default_factory=dict)  # 非交股情境的損益
    annualized: dict[int, Hit[Decimal] | None] = field(default_factory=dict)

    @property
    def found(self) -> bool:
        return all(self.titles)

    @property
    def delivery_cases(self) -> tuple[int, ...]:
        return (3,) if self.kind == "maxi" else (2, 3)


def _pnl(m: re.Match[str]) -> Pnl:
    return Pnl(dec(m[1]), dec(m[2]), dec(m[3]))


def _worst_row(name: str | None, prices: ParsedField) -> int | None:
    """情境寫的最差標的（「（中文名稱）」）→ 價格表第幾檔：單一標的為第 1 檔；對不到為 None（共用規則轉人工覆核）。"""
    if not prices.ok:
        return None
    if len(prices.value) == 1:
        return 1
    if name is None:
        return None
    rows = [n for n, r in enumerate(prices.value, start=1) if r.name and r.name.replace(" ", "") == name]
    return rows[0] if len(rows) == 1 else None


def _scenarios(ti: hsbc.ScenarioIndex, kind: Kind, prices: ParsedField) -> tuple[BenScenarios, dict[int, int | None]]:
    """以標題切出情境一～三（到下一個標題或第 18 條結束）；情境要剛好這三個、依序，標題有各自的辨識字（核對規則 §3.7）。
    另回傳每個情境的最差標的是價格表第幾檔。"""
    titles = TITLES[kind]
    headings = list(ti.finditer(HEADING))
    empty = BenScenarios(kind, ([], [], []))
    if [m[0] for m in headings] != [t for t, _ in titles]:
        return empty, {}
    bounds = [m.start() for m in headings] + [len(ti.text)]
    if not all(mark in ti.text[a : a + 100] for (_, mark), a in zip(titles, bounds, strict=False)):
        return empty, {}
    out = BenScenarios(kind, tuple(ti.lines_for(m.start(), m.end()) for m in headings))  # type: ignore[arg-type]

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

    rows = {}
    for n in (1, 2, 3):
        name = find(n, WORST, lambda m: m[1])
        rows[n] = _worst_row(name.value if name else None, prices)
        if n in out.delivery_cases:
            out.deliveries[n] = Delivery(
                shares=find(n, SHARES, lambda m: SharesCalc(dec(m[1]), dec(m[2]), dec(m[3]), dec(m[4]))),
                whole=find(n, S3_WHOLE, lambda m: Decimal(m[1])),
                fraction=find(n, S3_FRACTION, lambda m: dec(m[1])),
                remainder=find(n, S3_REMAINDER, lambda m: RemainderCalc(dec(m[1]), dec(m[2]), dec(m[3]), dec(m[4]))),
                value=find(n, S3_VALUE, lambda m: ValueCalc(dec(m[1]), Decimal(m[2]), dec(m[3]), dec(m[4]), dec(m[5]))),
                worse_pnl=find(n, PNL, _pnl),
                worse_annualized=find(n, ANNUALIZED, annualized),
            )
            continue
        if kind == "maxi":
            out.enhanced[n] = find(
                n,
                MAXI_AMOUNT,
                lambda m: EnhancedCalc(dec(m[1]), Decimal(m[2]), dec(m[3]), dec(m[4]), Decimal(m[6]), dec(m[7])),
            )
            out.enhanced_rows[n] = rows[n]
        else:
            out.digital = find(
                n, DIGITAL_AMOUNT, lambda m: DigitalCalc(Decimal(m[1]), dec(m[2]), Decimal(m[3]), dec(m[4]))
            )
        out.pnl[n] = find(n, PNL, _pnl)
        out.annualized[n] = find(n, ANNUALIZED, annualized)
    return out, rows


def _article18(fields: dict[str, ParsedField], kind: Kind, art18: list[Line]) -> BenScenarios:
    """基本假設（天期、面額、發行價格、期初投資金額、期末匯率、紅利）、情境價格表、情境一～三的算式（範本規格 §5）。"""
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
    spec = SCENARIO_TABLE if kind == "maxi" else DIGITAL_SCENARIO_TABLE
    fields["scenario_table"] = _scenario_table(art18, ti, end, spec)
    fields["scenario_reprint_table"] = price_table.at("scenario_reprint_table", fields["scenario_table"], spec)
    sc, rows = _scenarios(ti, kind, fields["underlying_prices"])
    prices = []
    for n in (1, 2) if kind == "maxi" else ():
        place = f"第 18 條情境分析{'一二'[n - 1]})到期贖回金額算式的"
        initial = Mention.of_optional_hit(place, sc.enhanced.get(n), lambda v: v.initial, sc.titles[n - 1])
        prices.append(ScenarioPrice(place, rows.get(n), "initial", initial.parsed(f"scenario{n}_initial")))
    for n in sc.delivery_cases if sc.found else ():
        place = f"第 18 條情境分析{'一二三'[n - 1]})股數算式的"
        strike = Mention.of_optional_hit(place, sc.deliveries[n].shares, lambda v: v.strike, sc.titles[n - 1])
        prices.append(ScenarioPrice(place, rows.get(n), "strike", strike.parsed(f"scenario{n}_strike")))
    fields["scenario_prices"] = standard_fields.occurrences("scenario_prices", prices)
    return sc


def _assumed_bonus(ti: hsbc.ScenarioIndex, end: int) -> ParsedField:
    """基本假設「紅利配息率為x%」（Digital「x%%」）：除了 % 數值外還有其他字 → 值為原文，由規則判不一致。"""
    name = "bonus_assumed"
    found = [m for m in ti.finditer(r"紅利配息率為(.+?)(?:\*假設天期|彭博代號)") if m.start() < end]
    if len(found) != 1:
        return ParsedField.missing(name, "第 18 條假設找不到「紅利配息率為x%」")
    m = found[0]
    lns = ti.lines_for(m.start(), m.start(1) + len(m[1]))
    single = re.fullmatch(PCT + "%?", m[1])
    return ParsedField.present(name, Decimal(single[1]) if single else m[1], lns)


def _scenario_table(art18: list[Line], ti: hsbc.ScenarioIndex, end: int, spec: price_table.TableSpec) -> ParsedField:
    """情境價格表：基本假設「紅利配息率」（Digital 另有假設天期註記）那一行之後、情境分析一之前。沒有中文名稱欄，Digital 也
    沒有交易所欄；幣別與交易所不比（核對規則 §2），只讀不驗。"""
    anchor = r"紅利配息率為[\d.]+%+(?:\*假設天期將因不同可供選擇之商品天期而異。)?"
    found = [m for m in ti.finditer(anchor) if m.start() < end]
    if len(found) != 1 or end == len(ti.text):
        return ParsedField.missing("scenario_table", "情境價格表錨點缺漏／重複")
    a = art18.index(ti.lines_for(found[0].start(), found[0].end())[-1]) + 1
    b = art18.index(ti.lines_for(end, end + 1)[0])
    return price_table.read("scenario_table", art18[a:b], spec)


# ---------------------------------------------------------------- 讀出


@dataclass
class HsbcBenTermSheet(hsbc.HsbcTermSheet):
    kind: Kind
    ben_scenarios: BenScenarios
    # 紅利的其他出處（第 18 條假設、Maxi 情境一二「最大值 [x%」、Digital 情境一「(100% + x%%)」；正式值是 `coupon_pa_pct`）
    bonus_mentions: list[Mention]


def read(lines: Sequence[Line]) -> HsbcBenTermSheet:
    """先照 HSBC FCN 讀出，再改讀 BEN 不同的條目（範本辨識另由 `detect` 負責；型態由名稱決定，辨識不出時照 Maxi 讀，
    規則轉人工覆核）。"""
    base = hsbc.read(lines)
    doc, fields = base.document, dict(base.fields)
    arts = doc.articles(1)
    kind, _ = kind_of(*_names(doc))

    def article(n: int) -> list[Line]:
        return doc.span_lines(arts.get(n))

    def sub(n: int, k: int) -> list[Line]:
        return doc.span_lines(doc.subitems(arts.get(n)).get(k))

    for name in FCN_ONLY:
        fields.pop(name, None)
    fields["monthly_coupon_pct"] = standard_fields.absent("monthly_coupon_pct", "月配息率（BEN 沒有定期配息）")
    fields["ben_kind"] = (
        ParsedField.present("ben_kind", kind, doc.before_chapter1()[:2])
        if kind is not None
        else ParsedField.missing("ben_kind", "名稱看不出是 Maxi 或 Digital")
    )
    kind = kind or "maxi"

    # 第 12 條 (1) 價格表（沒有 KO 價欄）、殘留註記
    fields["price_table"] = hsbc.checked_price_table("price_table", _table_lines(sub(12, 1)), PRICE_TABLE)
    fields["underlyings"] = price_table.underlyings(fields["price_table"])
    fields["underlying_prices"] = price_table.price_rows(
        fields["price_table"],
        names=[r.texts.get("label") or None for r in fields["price_table"].value.rows]
        if fields["price_table"].ok
        else None,
    )
    fields["main_price_table"] = price_table.at("main_price_table", fields["price_table"], PRICE_TABLE)
    fields["template_residue"] = _residue(sub(12, 1))

    # 第 11 條、沒有 KO（第 15 條）、無 KI
    _article11(fields, kind, article(11), article(11) if kind == "maxi" else sub(11, 2))
    _no_autocall(fields, kind, article(11), article(15))
    _ki_type(fields, kind, article(11), base.full_text)

    # 第 16、17 條：沒有定期配息；參與率寫「本商品不適用」（不當成參與率 0）
    ti16, ti17 = TextIndex(article(16)), TextIndex(article(17))
    fields["no_coupon_art16"] = exactly_one(
        "no_coupon_art16", ti16, ART16[kind], f"第 16 條不是 {KIND_LABEL[kind]} 的寫法"
    )
    fields["no_coupon_art17"] = exactly_one(
        "no_coupon_art17", ti17, MIN_RATE, "第 17 條 (1) 最低保證配息率不是「本商品不適用」"
    )
    fields["participation_rate"] = exactly_one(
        "participation_rate", ti17, PARTICIPATION_NA, "第 17 條 (2) 參與率不是「本商品不適用」"
    )
    fields["final_edition"] = exactly_one(
        "final_edition", TextIndex(doc.before_chapter1()), FINAL_EDITION, "封面標題沒有「(最終版)」"
    )

    # 第 18 條情境；期末匯率的幣別字併入承作幣別的其他出處（field.currency）
    scenarios = _article18(fields, kind, article(18))
    fields["currency_others"] = standard_fields.occurrences(
        "currency_others", [*fields["currency_others"].value, fx_currency(article(18))]
    )
    mentions = [Mention.of_field("第 18 條情境假設「紅利配息率為x%」", fields["bonus_assumed"])]
    if kind == "maxi":
        mentions += [
            Mention.of_optional_hit(
                f"第 18 條情境分析{'一二'[n - 1]})「最大值 [x%，…]」",
                scenarios.enhanced.get(n),
                lambda v: v.bonus,
                scenarios.titles[n - 1],
            )
            for n in (1, 2)
        ]
    else:
        mentions.append(
            Mention.of_optional_hit(
                "第 18 條情境分析一)「(100% + x%)」", scenarios.digital, lambda v: v.bonus, scenarios.titles[0]
            )
        )
    return HsbcBenTermSheet(fields, base.full_text, doc, base.scenarios, base.scenario_index, kind, scenarios, mentions)
