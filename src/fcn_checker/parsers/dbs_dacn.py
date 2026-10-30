"""DBS DACN 中文產品說明書（docs/checker/templates/dbs-zh-dacn.md）：骨架、封面、商品基本資料、表A、強制贖回、到期贖回、
情境分析、相關機構與一般交易事項同 DBS FCN 說明書（parsers/dbs.py），先照它讀出，再改讀 DACN 不同的地方：表1 與情境
價格表多 `區間下限價格`（配息門檻）、`區間上限價格` 兩欄，情境一出場期寫 `第j個觀察期間結束日`，情境二～四寫
`於第a~b個觀察期間`，KI 到期觀察多一個 `於到期較佳情況`。

依商品基本資料累積日數定義的 `其區間下限價格` 辨識商品家族（不看標題，workflow #366 決定 10）；只支援現行版範本：認得出是
DBS DACN、但現行版的固定寫法任一段不同或交易日早於 2026-06-01 時，辨識交出「不支援的範本版本」（範本規格 §1、§8）。
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal

from ..schema import DetectionResult, Evidence, FieldStatus, Line, ParsedField
from ..text import squash
from . import dbs, price_table
from .common import derived
from .layout import TextIndex, capture, parse_date

TEMPLATE_ID = "dbs-zh-dacn-pd"
PARSER_VERSION = "1"
OLD_VERSION = "DBS DACN 說明書的寫法和現行版（2026-06 以後）不同"
FIRST_SUPPORTED = dt.date(2026, 6, 1)  # 交易日早於這天的是舊版範本（沒有樣本，範本規格 §1；待使用者確認）
NAME_ZH_MARK = "區間累計配息記憶型每日觀察自動提前出場"
NAME_EN_MARK = "Range Accrual Memory Daily Knock-Out"
ACCRUAL_LABEL = "累積日數："
# 範本規格 §2.1 的三段固定寫法（去空白後逐字相等；乘號原文是數學粗體 X（U+1D417），比對前換成 X，見 `same_chars`）
RATE = (
    r"利率：1\)對於第1(?:~\d{1,2})?個觀察期間而言，利率為最大利率值；及2\)對於其後每個觀察期間而言，利率將根據以下公式決定："
    r"最大利率值X累積日數總日數"
)
ACCRUED_DAYS = (
    "累積日數：就每個觀察期間而言，(a)如果該觀察期間內未發生自動提前出場事件，則累積日數為該觀察期間內所有連結標的的參考價格"
    "皆等於或高於其區間下限價格(且等於或低於其區間上限價格(如適用))的觀察日的總數；或者(b)如果該觀察期間內發生了自動提前出場"
    "事件，則累積日數為該觀察期間內截至(包含)自動提前出場事件日為止之期間內，所有連結標的的參考價格皆等於或高於其區間下限價格"
    "(且等於或低於其區間上限價格(如適用))的觀察日的總數。"
)
TOTAL_DAYS = "總日數：就每個觀察期間而言，該觀察期間內觀察日的總數。"
BARRIER = "區間下限價格"
UPPER = "區間上限價格"
NOT_APPLICABLE = "不適用"  # 區間上限價格欄每列的寫法（樣本都是；其他寫法是沒有樣本的型態）
# 表1、情境價格表、投資人須知價格表：FCN 的三個價格欄＋區間下限價格（配息門檻）價格欄、區間上限價格文字欄
PRICE_TABLE = replace(
    dbs.PRICE_TABLE,
    columns=(*dbs.PRICE_TABLE.columns, price_table.Column("barrier", BARRIER)),
    texts=(price_table.Column("upper", UPPER),),
)
SCENARIO_TABLE = replace(PRICE_TABLE, where="情境分析價格表")
IIS_TABLE = replace(PRICE_TABLE, where="商品簡介價格表")
SCENARIO_FIRST = r"有利情況：假設本商品於第(\d{1,2})個觀察期間結束日發生自動提前出場事件"
# 情境二～四的標題（依序）；KI 到期觀察才有「於到期較佳情況」（範本規格 §4.3）
BEST, BETTER, WORSE = "於到期最佳情況：", "於到期較佳情況：", "較差情況："
RANGE = r"於第(\d{1,2})~(\d{1,2})個觀察期間"


# ---------------------------------------------------------------- 辨識


def same_chars(text: str) -> str:
    """固定寫法比對前把乘號統一：數學粗體 X（U+1D417）→ X。"""
    return text.replace("\U0001d417", "X")


def is_range_accrual(basic: str) -> bool:
    """商品家族（區間累計配息，範本規格 §8 條件 6）：商品基本資料有「累積日數：」定義，且比的是「其區間下限價格」。"""
    a = basic.find(ACCRUAL_LABEL)
    return a >= 0 and dbs.RANGE_MARK in basic[a:]


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §8）：DBS 骨架（條件 1～5）與區間累計配息（條件 6）都成立後，現行版特徵全部成立才是本範本；
    缺任一項 → 不支援的範本版本。"""
    sk = dbs.skeleton(lines)
    basic = sk.text("商品基本資料")
    failed = list(sk.failed)
    if not is_range_accrual(basic):
        failed.append(f"商品基本資料沒有比「{dbs.RANGE_MARK}」的累積日數定義（不是區間累計配息 DACN）")
    if failed:
        return DetectionResult(False, failed, sk.evidence)
    missing = version_problems(sk, basic)
    if missing:
        return DetectionResult(False, missing, sk.evidence, unsupported=f"{OLD_VERSION}：{'；'.join(missing)}")
    return DetectionResult(True, [], sk.evidence)


def version_problems(sk: dbs.Skeleton, basic: str) -> list[str]:
    """現行版特徵（範本規格 §8）中不成立的項目。"""
    text = same_chars(basic)
    out = []
    if len(re.findall(RATE, text)) != 1:
        out.append("配息資料「利率：1)…2)…最大利率值 X 累積日數 / 總日數」和現行版不同")
    out += [
        f"{where}和現行版不同"
        for where, x in (("累積日數定義", ACCRUED_DAYS), ("總日數定義", TOTAL_DAYS))
        if x not in text
    ]
    table = TextIndex(dbs.table_region(sk.secs.get("商品基本資料", []), "表1", "到期贖回計算公式，含")).text
    out += [f"表1 表頭沒有「{x}」" for x in (BARRIER, UPPER) if x not in table]
    cover = sk.text("cover")
    zh = re.search(r"•商品中文名稱：(.+?)\(下稱「本商品」\)", cover)
    en = re.search(r"•商品英文名稱：(.+?)•", cover)
    if zh is None or NAME_ZH_MARK not in zh[1]:
        out.append(f"商品中文名稱沒有「{NAME_ZH_MARK}」")
    if en is None or squash(NAME_EN_MARK) not in en[1]:
        out.append(f"商品英文名稱沒有「{NAME_EN_MARK}」")
    m = re.search(r"(?<!劃)(?<!定)交易日：" + dbs.D, basic)
    trade = parse_date(m[1]) if m else None
    if trade is None:
        out.append("商品基本資料讀不到交易日")
    elif trade < FIRST_SUPPORTED:
        out.append(f"交易日 {trade} 早於 {FIRST_SUPPORTED}（只支援現行版範本）")
    return out


# ---------------------------------------------------------------- 讀出


def read(lines: Sequence[Line]) -> dbs.DbsTermSheet:
    """先照 DBS FCN 讀出，再改讀表1 與情境價格表（多區間下限、上限兩欄）、配息門檻、保證期數與情境（範本規格 §3、§4.3）。"""
    base = dbs.read(lines)
    fields = dict(base.fields)
    secs = dbs.sections(dbs.body(lines))
    basic = secs.get("商品基本資料", [])
    table = price_table.read("underlying_table", dbs.table_region(basic, "表1", "到期贖回計算公式，含"), PRICE_TABLE)
    fields["underlying_table"] = table
    fields["main_price_table"] = price_table.at("main_price_table", table, PRICE_TABLE)
    fields["ki_type"] = dbs.ki_type(TextIndex(basic, separate_digits=True), table)
    dbs.table_fields(fields)
    fields["coupon_barrier_pct"] = _coupon_barrier(TextIndex(basic), table)
    fields["range_upper"] = derived(
        "range_upper", table, [r.texts.get("upper", "") for r in table.value.rows] if table.ok else None
    )
    full = fields["full_rate_periods"]
    fields["guaranteed_periods"] = derived("guaranteed_periods", full, full.value)
    _scenario(fields, secs.get("商品投資收益計算方法及其他資料", []))
    return dbs.DbsTermSheet(fields, base.full_text)


def _coupon_barrier(ti: TextIndex, table: ParsedField) -> ParsedField:
    """累積日數比的是「其區間下限價格」→ 配息門檻 = 表1 `區間下限價格 (期初價格的X%)` 的 X（範本規格 §3、§4.1）；證據掛
    定義句與表頭。"""
    name = "coupon_barrier_pct"
    a = ti.text.find(ACCRUAL_LABEL)
    b = ti.text.find(dbs.RANGE_MARK, a) if a >= 0 else -1
    if b < 0:
        return ParsedField.missing(name, f"商品基本資料找不到比「{dbs.RANGE_MARK}」的累積日數定義")
    if not table.ok:
        return derived(name, table)
    pct = table.value.pcts.get("barrier")
    head = list(table.value.head_lines.get("barrier", ()))
    if pct is None:
        return ParsedField.invalid(name, head, f"表1「{BARRIER}」表頭讀不到 (期初價格的X%)")
    lines = ti.lines_for(a, b + len(dbs.RANGE_MARK))
    return ParsedField(name, FieldStatus.PRESENT, pct, [*map(Evidence.of, lines), *map(Evidence.of, head)])


def _scenario(fields: dict[str, ParsedField], sec: list[Line]) -> None:
    """情境價格表（多兩欄）、情境一出場期（`第j個觀察期間結束日`）、情境二年化報酬率（到「於到期較佳情況」或「較差情況」
    為止）與情境二～四的 `於第a~b個觀察期間`（範本規格 §4.3）；改讀後重交共用情境規則的標準欄位。"""
    ti = TextIndex(sec)
    table = price_table.read("scenario_table", dbs.table_region(sec, "各連結標的之相關價格", "1."), SCENARIO_TABLE)
    fields["scenario_table"] = table
    fields["scenario_first_period"] = capture("scenario_first_period", ti, SCENARIO_FIRST, int, whole_match=True)
    titles = scenario_titles(ti)
    fields["scenario_titles"] = titles
    fields["scenario_best_annual_pct"] = _best_annual(ti, titles)
    fields["scenario_ranges"] = _ranges(ti, titles)
    dbs.scenario_standard(fields)


def scenario_titles(ti: TextIndex) -> ParsedField:
    """情境二～四的標題與位置（依序；「於到期較佳情況」只有 KI 到期觀察才有）：每個標題至多一處、依序；否則讀不到。"""
    name = "scenario_titles"
    found = []
    for title in (BEST, BETTER, WORSE):
        hits = [m.start() for m in ti.finditer(re.escape(title))]
        if len(hits) > 1:
            return ParsedField.invalid(name, ti.lines_for(hits[1], hits[1] + len(title)), f"情境分析有多處「{title}」")
        if hits:
            found.append((title, hits[0]))
    if [p for _, p in found] != sorted(p for _, p in found):
        return ParsedField.invalid(name, [], "情境分析的情境順序不是「於到期最佳情況」「於到期較佳情況」「較差情況」")
    if not found:
        return ParsedField.missing(name, "情境分析找不到「於到期最佳情況：」「較差情況：」")
    ev = [ln for title, p in found for ln in ti.lines_for(p, p + len(title))]
    return ParsedField.present(name, found, ev)


def _spans(ti: TextIndex, titles: list[tuple[str, int]]) -> list[tuple[str, int, int]]:
    """每個情境標題到下一個情境標題（最後一個到「最大損失情況：」或段落結尾）的範圍。"""
    end = ti.text.find("最大損失情況：")
    ends = [p for _, p in titles[1:]] + [end if end >= 0 else len(ti.text)]
    return [(title, p, e) for (title, p), e in zip(titles, ends, strict=True)]


def _best_annual(ti: TextIndex, titles: ParsedField) -> ParsedField:
    """情境二（於到期最佳情況）的「年化平均報酬率約為y%」：範圍到下一個情境標題為止（KI 到期觀察的「於到期較佳情況」也寫
    年化報酬率，不能算進來）。"""
    name = "scenario_best_annual_pct"
    if not titles.ok:
        return derived(name, titles)
    span = next(((a, b) for t, a, b in _spans(ti, titles.value) if t == BEST), None)
    if span is None:
        return ParsedField.missing(name, f"情境分析找不到「{BEST}」")
    return capture(name, TextIndex(ti.lines_for(*span)), r"年化平均報酬率約為" + dbs.PCT, Decimal, whole_match=True)


def _ranges(ti: TextIndex, titles: ParsedField) -> ParsedField:
    """各情境標題後的 `於第a~b個觀察期間`（每個情境剛好一處；讀不到的情境記成 None）。"""
    name = "scenario_ranges"
    if not titles.ok:
        return derived(name, titles)
    out, ev = [], []
    for title, a, b in _spans(ti, titles.value):
        hits = [m for m in ti.finditer(RANGE) if a <= m.start() < b]
        if len(hits) == 1:
            m = hits[0]
            out.append((title.rstrip("："), int(m[1]), int(m[2])))
            ev += ti.lines_for(m.start(), m.end())
        else:
            out.append((title.rstrip("："), None, None))
    return ParsedField.present(name, out, ev)
