"""MS DACN 中文產品說明書（docs/checker/templates/ms-zh-dacn.md）：版面、封面、第一章條號、第二～四章同 MS FCN 說明書
（parsers/ms.py），照它讀出（第 18 項照區間計息讀，`accrual=True`）後，再加讀配息門檻（第 16 項執行價表頭 %，n(j) 比執行價）
與第 15 項 (2) 的保證期數。

依第 15 項 `{X%×n(j)/N(j)}` 與比執行價的 n(j) 定義辨識商品家族（MS FCN D 型也有這條公式，但 n(j) 是到提前出場日的天數），
名稱另當條件（`區間累積配息`／`Range Accrual Notes`）；只支援現行版範本：認得出是 MS DACN、但現行版特徵任一項不成立時，
辨識交出「不支援的範本版本」（範本規格 §1、§8）。版本只看寫法、不看交易日早晚（同第一波 DACN，workflow #409）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace

from ..schema import DetectionResult, Line, ParsedField
from . import dacn, ms
from .layout import Document, TextIndex, capture, parse_date

TEMPLATE_ID = "ms-zh-dacn-pd"
PARSER_VERSION = "1"
OLD_VERSION = "MS DACN 說明書的寫法和現行版不同"
# 本家族的標記（名稱）：全文有它的文件停在本範本、不退回 BEN／FCN 範本（#436）。MS FCN、BEN 說明書全文沒有
MARK = "區間累積配息"
NAME_EN_MARK = "RangeAccrualNotes"
PCT = r"(\d+(?:\.\d+)?)%"
# 第 15 項 (2) 的區間計息公式與 n(j) 定義（去空白、括號全形；範本規格 §2.1）
FORMULA = r"\{" + PCT + r"×n（j）/N（j）\}"
NJ = (
    "n（j）=於第j個配息觀察期間，指在該配息週期起始日至（i）該配息週期終止日或（ii）自動提前出場事件發生日孰先，"
    "籃子中表現最差的連結標的之收盤價等於或大於其執行價的配息觀察日總數。"
)
# 商品家族（範本規格 §8 條件 2）：有區間計息公式，且 n(j) 數的是收盤價等於或大於執行價的配息觀察日
FAMILY = (FORMULA, r"n（j）=[^=]+?收盤價等於或大於其執行價的配息觀察日總數")
# 第 1～g 期固定配息：括號裡的期別寫法由 `guaranteed` 判斷（只有 `1` 有樣本，其他讀不到、轉人工），不當成範本版本的特徵
GUARANTEED = r"每單位配息金額（([^）]{1,8})）[=＝]每單位商品面額×固定配息率（" + PCT + "）"
GUARANTEED_PERIODS = {"1": 1}  # `每單位配息金額(1)` → g = 1；其他寫法沒有樣本（範本規格 §3）


def wordings(j_range: str) -> tuple[tuple[str, str], ...]:
    """區間累積配息的固定寫法（依序；說明書第 15 項 (2)、投資人須知收益分配事項共用，只有 j 範圍的寫法不同）。"""
    return (
        ("每月配息金額(j)", r"每月配息金額（j）[=＝]配息日（j）持有之商品單位數×每單位配息金額（j）"),
        ("每單位配息金額(g)", GUARANTEED),
        (
            "每單位配息金額(j)",
            r"每單位配息金額（j）[=＝]每單位商品面額×" + FORMULA + re.escape("（四捨五入計算至小數點後第2位）"),
        ),
        ("n(j) 定義", re.escape(NJ)),
        ("N(j) 定義", re.escape("N（j）=於第j個配息觀察期間的配息觀察日總數")),
        ("j 範圍", j_range),
        ("配息觀察日", re.escape("配息觀察日：係任一共同預定交易日，即同時為所有連結標的之預定交易日之日。")),
    )


WORDINGS = wordings(r"j係為\d+至\d+的數字，代表第\d+至\d+個配息觀察期間")


def missing_wordings(text: str, specs: Sequence[tuple[str, str]], where: str) -> list[str]:
    """`specs` 依序找不到的固定寫法（錯訊）。"""
    out, at = [], 0
    for label, pattern in specs:
        m = re.compile(pattern).search(text, at)
        if m is None:
            out.append(f"{where}{label}的寫法和現行版不同")
        else:
            at = m.end()
    return out


def is_accrual(text: str) -> bool:
    """商品家族（區間累積配息，範本規格 §8 條件 2）。"""
    return all(re.search(p, text) for p in FAMILY)


def _article(doc: Document, n: int) -> TextIndex:
    return TextIndex(doc.span_lines(doc.articles(1).get(n)), unify_brackets=True)


# ---------------------------------------------------------------- 辨識


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §8）：MS 說明書骨架與 DACN 名稱（條件 1）、區間累積配息（條件 2）都成立後，現行版特徵全部成立才是
    本範本；缺任一項 → 不支援的範本版本。全文有 `MARK` 的文件停在本範本（不退回 BEN、FCN 範本）。"""
    doc, failed, evidence = ms.skeleton(lines, MARK, NAME_EN_MARK)
    art15 = _article(doc, 15)
    if not is_accrual(art15.text):
        failed.append("第 15 項沒有區間累積配息的 {X%×n(j)/N(j)} 與比「執行價」的 n(j) 定義")
    if failed:
        return DetectionResult(False, failed, evidence, claimed=MARK in TextIndex(doc.lines).text)
    missing = version_problems(doc, art15)
    if missing:
        return DetectionResult(False, missing, evidence, unsupported=f"{OLD_VERSION}：{'；'.join(missing)}")
    return DetectionResult(True, [], evidence)


def version_problems(doc: Document, art15: TextIndex) -> list[str]:
    """現行版特徵（範本規格 §8）中不成立的項目：第 15 項 (2) 的七段固定寫法（依序）、第 14 項 (2) 讀得到交易日（只看寫法，
    不看早晚）。"""
    out = missing_wordings(art15.text, WORDINGS, "第 15 項")
    sub = doc.span_lines(doc.subitems(doc.articles(1).get(14)).get(2))
    if not capture("trade_date", TextIndex(sub), r"交易日：" + ms.D, parse_date).ok:
        out.append("第 14 項 (2) 讀不到交易日")
    return out


# ---------------------------------------------------------------- 讀出


def read(lines: Sequence[Line]) -> ms.MsTermSheet:
    """照 MS FCN 讀出（第 18 項照區間計息）後，加讀配息門檻與保證期數（範本規格 §3）。"""
    base = ms.read(lines, accrual=True)
    art15 = _article(base.document, 15)
    f = dict(base.fields)
    hits = list(art15.finditer(FAMILY[1]))
    f["coupon_barrier_pct"] = (
        dacn.barrier_from_strike(art15.lines_for(hits[0].start(), hits[0].end()), f["strike_pct"])
        if len(hits) == 1
        else ParsedField.missing(dacn.FIELD, "第 15 項找不到比「執行價」的 n(j) 定義，或出現多次")
    )
    f["coupon_guaranteed_periods"] = guaranteed(art15, "coupon_guaranteed_periods", "第 15 項 (2)")
    return replace(base, fields=f)


def guaranteed(ti: TextIndex, name: str, where: str) -> ParsedField:
    """`每單位配息金額(1) = 每單位商品面額×固定配息率(X%)` 的保證期數 g（只寫第 1 期 → 1）；其他寫法沒有樣本，讀不到、轉人工
    （範本規格 §3；投資人須知收益分配事項同一句，parsers/ms_dacn_iis.py）。"""
    hits = list(ti.finditer(GUARANTEED))
    if len(hits) != 1:
        note = f"{where}「每單位配息金額(g)…固定配息率」" + ("出現多次" if hits else "找不到")
        return ParsedField.missing(name, note)
    m = hits[0]
    lines = ti.lines_for(m.start(), m.end())
    if m[1] not in GUARANTEED_PERIODS:
        return ParsedField.invalid(name, lines, f"{where}「每單位配息金額（{m[1]}）」的寫法沒有樣本")
    return ParsedField.present(name, GUARANTEED_PERIODS[m[1]], lines)
