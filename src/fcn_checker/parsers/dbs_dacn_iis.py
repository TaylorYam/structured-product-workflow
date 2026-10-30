"""DBS DACN 中文投資人須知（docs/checker/templates/dbs-zh-dacn-iis.md）：版面、相關機構、商品簡介、KO 句型、費用表同 DBS FCN
投資人須知（parsers/dbs_iis.py），先照它讀出，再改讀商品簡介價格表（多區間下限、上限兩欄）與 KI 到期觀察的到期贖回寫法
（`1.`／`2.` 編號）。

依收益分配事項的 `其區間下限價格` 辨識商品家族；只支援現行版範本：認得出是 DBS DACN、但現行版特徵不成立時辨識交出
「不支援的範本版本」（範本規格 §3）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from ..schema import DetectionResult, Line, ParsedField
from . import dbs, dbs_dacn, dbs_iis, iis
from .common import derived
from .layout import TextIndex, parse_date

TEMPLATE_ID = "dbs-zh-dacn-iis"
PARSER_VERSION = "1"
OLD_VERSION = "DBS DACN 投資人須知的寫法和現行版不同"
RATE = (
    r"利率：對於第1(?:~\d{1,2})?個觀察期間而言，利率為最大利率值。對於其後每個觀察期間而言，利率將根據以下公式決定："
    r"最大利率值X累積日數/總日數"
)
TOTAL_DAYS = "總日數：就每個觀察期間而言，為該觀察期間內觀察日的總數。"
# KI 到期觀察的到期贖回：DACN 投資人須知用 `1.`／`2.` 編號（說明書與 FCN 是 `(a)`，投資人須知範本規格 §1）
KI_MATURITY = r"(?:\(a\)|1\.)如果未發生下限觸發事件，將適用現金結算"
PROVIDED = dbs_iis.PROVIDED | {"coupon_barrier_pct"}


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §3）：DBS 投資人須知骨架（條件 1）與區間累計配息（條件 2）都成立後，現行版特徵全部成立才是本範本。"""
    ti, failed, evidence = dbs_iis.skeleton(lines)
    coupon = dbs_dacn.same_chars(dbs_iis.coupon_text(ti))
    if not dbs_dacn.is_range_accrual(coupon):
        failed.append(f"收益分配事項沒有比「{dbs.RANGE_MARK}」的累積日數定義（不是區間累計配息 DACN）")
    if failed:
        return DetectionResult(False, failed, evidence)
    missing = []
    if len(re.findall(RATE, coupon)) != 1:
        missing.append("收益分配事項「利率：…最大利率值 X 累積日數/總日數」和現行版不同")
    missing += [
        f"收益分配事項{where}和現行版不同"
        for where, x in (("累積日數定義", dbs_dacn.ACCRUED_DAYS), ("總日數定義", TOTAL_DAYS))
        if x not in coupon
    ]
    table = TextIndex(dbs_iis.table_region(dbs_iis.body(lines))).text
    missing += [f"價格表表頭沒有「{x}」" for x in (dbs_dacn.BARRIER, dbs_dacn.UPPER) if x not in table]
    zh = re.search(r"^中文投資人須知\(最終版本\)(.+?)\(下稱「本商品」\)", ti.text)
    en = re.search(r"國際證券編碼ISIN：[A-Z0-9]{12}\)(.+?)※投資風險警語：", ti.text)
    if zh is None or dbs_dacn.NAME_ZH_MARK not in zh[1]:
        missing.append(f"中文名稱沒有「{dbs_dacn.NAME_ZH_MARK}」")
    if en is None or "RangeAccrualMemoryDailyKnock-Out" not in en[1]:
        missing.append(f"英文名稱沒有「{dbs_dacn.NAME_EN_MARK}」")
    m = re.search(r"交易日：" + dbs.D + "；", ti.text)
    trade = parse_date(m[1]) if m else None
    if trade is None:
        missing.append("商品簡介讀不到交易日")
    elif trade < dbs_dacn.FIRST_SUPPORTED:
        missing.append(f"交易日 {trade} 早於 {dbs_dacn.FIRST_SUPPORTED}（只支援現行版範本）")
    if missing:
        return DetectionResult(False, missing, evidence, unsupported=f"{OLD_VERSION}：{'；'.join(missing)}")
    return DetectionResult(True, [], evidence)


def read(lines: Sequence[Line]) -> iis.IisSheet:
    """先照 DBS FCN 投資人須知讀出，再改讀價格表（區間下限、上限兩欄）、KI 型態與配息門檻 %。"""
    base = dbs_iis.read(lines)
    fields = dict(base.fields)
    text_lines = dbs_iis.body(lines)
    provided = set(PROVIDED) | (base.provided - dbs_iis.PROVIDED)
    provided |= dbs_iis.prices(fields, text_lines, dbs_dacn.IIS_TABLE)
    table = fields["price_table"]
    sec = iis.section(base.full_text, dbs_iis.TERMS)
    fields["ki_type"] = (
        dbs.ki_type(TextIndex(sec), table, KI_MATURITY)
        if sec is not None
        else ParsedField.missing("ki_type", "找不到「收益分配事項」到「發行或受託不成立之處理」段落")
    )
    fields["coupon_barrier_pct"] = derived(
        "coupon_barrier_pct", table, table.value.pcts.get("barrier") if table.ok else None
    )
    if table.ok and "barrier" not in table.value.pcts:
        fields["coupon_barrier_pct"] = ParsedField.invalid(
            "coupon_barrier_pct", list(table.evidence), f"價格表「{dbs_dacn.BARRIER}」表頭讀不到 (期初價格的X%)"
        )
    fields["range_upper"] = derived(
        "range_upper", table, [r.texts.get("upper", "") for r in table.value.rows] if table.ok else None
    )
    return iis.IisSheet(fields, base.full_text, frozenset(provided))
