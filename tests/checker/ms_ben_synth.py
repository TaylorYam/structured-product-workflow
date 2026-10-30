"""MS BEN 合成說明書與投資人須知（docs/checker/templates/ms-zh-ben.md、ms-zh-ben-iis.md，#376）；商品、標的與價格皆虛構。

封面、第 11 項標的表、第二章以後與投資人須知的版面沿用 tests/checker/ms_synth.py；這裡只畫 BEN 不同的條目：第一章 28 條
（§19 小節照範本誤印成 20.1～20.4、沒有 §20）、§10 主要給付、§14 日期與 §14(9) 回顧觀察期間、§15 到期公式／定義／價格表／
紅利與參與率、§16 假設、重印表與情境一～四；投資人須知的商品簡介、收益分配事項與贖回價金之計算。

改字用 `edits`（段落代號同 ms_synth：說明書 `ts.art{n}`、價格表 `ts.art15.table`、重印表 `ts.art16.table`、情境 `ts.scen.一`…；
投資人須知 `iis.summary`、`iis.coupon`、`iis.redeem`、`iis.risk`）。日期在交易所假日表涵蓋的年份（回顧結束日要查共同交易日）。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from harness import STANDARD, check_all, iis_path
from ms_synth import (
    ISSUER,
    WARNING_CH1,
    Spec,
    _Doc,
    build_iis_pdf,
    cover,
    later_chapters,
    money,
    ratings_text,
    underlying_name,
    underlying_table,
)
from pdf_writer import Edit, zh_date
from reference_synth import (
    REFERENCE_HEADERS,
    build_reference_sheet,
    price,
    reference_row,
    weekday_on_or_after,
    write_notes,
)

BEN_NAME = STANDARD["issuer"]["ms"]["product_names"]["BEN"]
BEN_PRODUCT = "BEN"
HEADERS = [*REFERENCE_HEADERS, "參與率(%)", "回顧結束日"]
TRADE = dt.date(2027, 4, 12)  # 週一；交易所假日表涵蓋 2027（回顧結束日查共同交易日）
Q4, Q2 = Decimal("0.0001"), Decimal("0.01")
MIN_RATE = "最低保證贖回率：本商品不適用。本商品不保本。本商品於到期的本金贖回可能等於零。"
HEAD = "於到期日，視期末定價日時籃子中表現最差的連結標的之收盤價而定，每單位商品將依據下列情形(a) 或 (b)在到期日贖回："


def _add_months(d: dt.date, n: int) -> dt.date:
    m = d.month - 1 + n
    return dt.date(d.year + m // 12, m % 12 + 1, d.day)


@dataclass
class BenSpec(Spec):
    """MS BEN 合成規格：`kind` 是 `maxi`（一般 BEN，紅利表現）或 `digital`；`lookback` 有沒有回顧觀察期間。沒有定期配息、
    沒有 KO、無 KI，`annual` 是紅利（不年化，同參考條件表 `Coupon p.a. (%)`）；Non-Call = 天期；參考條件表 KO% 照填 K%。
    預設照樣本：一般 BEN 2 檔、K% 90、6 個月；Digital 1 檔。情境以第 1 檔為最差標的：期末股價 = 期初 × `above`%（情境一）、
    × `below`%（一般 BEN 情境二；Digital 情境二收盤價 = 執行價）、× `worse`%（情境三交股）。`lookback_end` 是回顧結束日
    （None → 發行日）。"""

    kind: str = "maxi"
    lookback: bool = False
    annual: Decimal = Decimal("12.50")
    strike: Decimal = Decimal("90.00")
    ko: Decimal = Decimal("90.00")
    memory: bool = False
    ko_obs: str = "P"
    ki: str = "none"
    tenor: int = 6
    trade_date: dt.date = TRADE
    issue_date: dt.date = TRADE + dt.timedelta(days=7)
    first_callable: int = field(init=False)
    lookback_end: dt.date | None = None
    above: Decimal = Decimal("130")
    below: Decimal = Decimal("105")
    worse: Decimal = Decimal("72")

    def __post_init__(self) -> None:
        self.first_callable = self.tenor
        super().__post_init__()

    @property
    def digital(self) -> bool:
        return self.kind == "digital"

    @property
    def ends(self) -> list[dt.date]:
        return [weekday_on_or_after(_add_months(self.trade_date, j)) for j in range(1, self.tenor + 1)]

    @property
    def payments(self) -> list[dt.date]:
        return [weekday_on_or_after(d + dt.timedelta(days=3)) for d in self.ends]

    @property
    def lookback_end_date(self) -> dt.date | None:
        return (self.lookback_end or self.issue_date) if self.lookback else None

    @property
    def participation(self) -> Decimal:
        return Decimal(0) if self.digital else Decimal(100)

    @property
    def name_zh(self) -> str:
        return BEN_NAME["zh"].format(
            tenor=self.tenor,
            ccy_zh=self.currency_zh,
            underlying_zh=BEN_NAME["basket_zh"] if len(self.underlyings) > 1 else BEN_NAME["single_zh"],
            payoff_zh=BEN_NAME["digital_zh" if self.digital else "enhanced_zh"],
        )

    @property
    def name_en(self) -> str:
        return BEN_NAME["en"].format(
            tenor=self.tenor,
            ccy=self.ccy,
            payoff_en=BEN_NAME["digital_en" if self.digital else "enhanced_en"],
            underlying_en=BEN_NAME["basket_en"] if len(self.underlyings) > 1 else BEN_NAME["single_en"],
        )

    # 文字
    @property
    def subject(self) -> str:
        return "該連結標的" if self.digital else "籃子中表現最差的連結標的"

    @property
    def strike_word(self) -> str:
        return "回顧執行價" if self.lookback else "執行價"

    def cash(self) -> str:
        if self.digital:
            payoff = "每單位面額 x (100% +紅利配息率)；否則，"
        else:
            base = "回顧期初價格" if self.lookback else "期初價格"
            payoff = (
                "到期日持有之商品單位數 x 每單位商品面額 x {100% + 最大值 [紅利配息率, 參與率 x (籃子中表現最差的連結標的"
                f"期末定價日之收盤價/籃子中表現最差的連結標的{base} -100%)]}}；否則，"
            )
        return (
            f"(a) 現金交割：若於期末定價日時{self.subject}之收盤價高於或等於其{self.strike_word}，則投資人領回："
            + payoff
        )

    def delivery(self, iis: bool = False) -> str:
        s, k = self.subject, self.strike_word
        unit, held = ("連結標的", "該連結標的") if self.digital else ("籃子中表現最差連結標的",) * 2
        return (
            f"(b)實物交割：若於期末定價日時{s}之收盤價低於其{k}，發行機構將於到期日，以支付「最終實物贖回權益」之方式贖回"
            f"每單位商品：「最終實物贖回權益」就每單位商品而言，係指每單位商品面額乘以轉換匯率，除以{s}之{k}後所得數目之"
            f"{unit}。如該數目非整數，則{held}之股數為無條件捨去至整數位後之數{'。' if iis else '，'}非整數之畸零股將以現金"
            f"支付代替，按{s}於期末定價日之收盤價計算（即不滿一股之畸零股數乘以{s}於期末定價日之收盤價除以轉換匯率），"
            "並以計價幣別支付。"
        )


def _pct(x: Decimal) -> str:
    return f"{x:.2f}%"


def _price_grid(d: _Doc, s: BenSpec, section: str, *, reprint: bool) -> None:
    """§15 價格表（lookback 只有期初價格欄）或 §16 重印表（lookback 表頭寫回顧期初價格、回顧執行價）。"""
    d.section = section
    lb = s.lookback
    heads = [
        (40, ["連結", "標的"]),
        (90, ["彭博(Bloomberg)代碼"]),
        (230, ["回顧期初價格" if lb and reprint else "期初價格"]),
    ]
    if not lb:
        heads.append((330, ["執行價 （期初價格的", f"{s.strike:.2f}%）"]))
    elif reprint:
        heads.append((330, ["回顧執行價 （回顧期", f"初價格的{s.strike:.2f}%）"]))
    rows = []
    for i, (ticker, initial) in enumerate(zip(s.tickers, s.initials, strict=True), 1):
        cells = [(44, [str(i)]), (90, [ticker]), (230, [f"{initial:.4f}"])]
        if not lb or reprint:
            cells.append((330, [f"{price(initial, s.strike):.4f}"]))
        rows.append(cells)
    d.w.grid(heads, rows, size=10, gap=14)


def _article10(d: _Doc, s: BenSpec) -> None:
    d.text(72, "主要給付項目為到期贖回金額：")
    d.text(72, HEAD)
    d.text(72, s.cash())
    d.text(72, s.delivery())
    d.text(72, "詳見第15 項「到期贖回計算公式、最低保證贖回率及參與率」")


def _article14(d: _Doc, s: BenSpec) -> None:
    d.sub(1, f"商品年期：{s.tenor} 個月")
    d.sub(2, f"交易日：{zh_date(s.trade_date)}")
    d.sub(3, f"發行日：{zh_date(s.issue_date)}")
    d.sub(4, f"到期日：{zh_date(s.maturity_date)}。到期日為期末定價日後至少第三個營業日之日。")
    d.sub(5, f"期末定價日*：{zh_date(s.final_date)}，如當日非預定交易日，次一預定交易日為之。")
    d.sub(6, "次級市場投資人提前贖回日：自開始受理贖回日期至結束受理贖回日期止。")
    d.sub(7, "付款營業日：係指得在紐約從事外幣交易之任一日。")
    d.sub(8, "營業日慣例：經調整順延制。")
    if s.lookback:
        d.sub(9, f"回顧觀察期間：自交易日（包含）起至{zh_date(s.lookback_end_date)}（包含）。")


def _article15(d: _Doc, s: BenSpec) -> None:
    d.sub(1, HEAD)
    d.text(72, s.cash())
    d.text(72, s.delivery())
    d.text(72, "*發行機構應以實物交割時，有關風險及費用均由投資人承擔。")
    d.text(72, "其中：")
    if s.lookback:
        d.text(
            72,
            "回顧期初價格：就任何連結標的而言，即為以下兩項中的較低者：（i）該連結標的期初價格及（ii）該連結標的於回顧觀察"
            "期間內任何共同預定交易日之最低收盤價。",
        )
        d.text(72, f"回顧執行價：就任何連結標的而言，即為該連結標的回顧期初價格乘以{_pct(s.strike)}。")
    else:
        d.text(72, f"執行價：就任何連結標的而言，為相關連結標的之期初價格的{_pct(s.strike)}")
    if not s.digital:
        d.text(
            72,
            "籃子中表現最差的連結標的：若籃子中連結標的在期末定價日時收盤價除以其期初價格的數值，比一籃子中其他連結標的有關"
            "的數值為低，則就期末定價日該連結標的為籃子中表現最差的連結標的。倘於該日籃子內超過一個連結標的有相同的最低數值，"
            "則由決定代理機構選擇該日的籃子中表現最差的連結標的。",
        )
    d.text(72, "期初價格： 依下表所示" if s.lookback else "期初價格及執行價： 依下表所示")
    _price_grid(d, s, "art15.table", reprint=False)
    d.section = "art15"
    d.sub(1, f"紅利配息率：{_pct(s.annual)}")
    d.sub(2, MIN_RATE)
    d.sub(3, f"參與率：{s.participation:.2f}%" if not s.digital else "參與率：0%")


def _article16(d: _Doc, s: BenSpec) -> None:
    unit, denom = s.currency_zh, Decimal(s.denom)
    d.sub(1, "投資收益計算方法：請參閱以上第15 項之「到期贖回計算公式、最低保證贖回率及參與率」。")
    d.sub(3, "以情境分析解說最大可能獲利、損失及其他狀況之平均年化報酬率：")
    d.text(72, "假設：")
    d.text(72, f"▪ 總投資金額 = 1 單位商品面額 = {money(denom)} {unit}")
    d.text(72, f"▪ 年期：{s.tenor} 個月")
    d.text(72, f"▪ 紅利配息率：{_pct(s.annual)}")
    d.text(72, f"▪ {MIN_RATE}")
    d.text(72, f"▪ 參與率：{s.participation:.2f}%" if not s.digital else "▪ 參與率：0%")
    _price_grid(d, s, "art16.table", reprint=True)
    initial, ticker = s.initials[0], s.tickers[0]
    strike = price(initial, s.strike)
    k, who = s.strike_word, s.subject

    def condition(close: Decimal, tail: str) -> None:
        d.text(72, f"假設於期末定價日時{who}({ticker})之收盤價= {close:.4f} 美元；{k}= {strike:.4f} 美元，{tail}")

    def returns(amount: Decimal, written: str) -> None:
        ret = ((amount / denom - 1) * 100).quantize(Q2, ROUND_HALF_UP)
        annual = (ret * 12 / s.tenor).quantize(Q2, ROUND_HALF_UP)
        d.text(72, "▪ 每單位商品報酬率 =(每單位商品到期贖回金額/ 每單位商品投資金額)-1")
        d.text(72, f"= ({written} / {money(denom)} {unit}) - 1")
        d.text(72, f"= {ret}%(年化報酬率:{annual}%)")

    def bonus_amount(close: Decimal) -> None:
        amount = (denom * (100 + s.annual) / 100).quantize(Q2, ROUND_HALF_UP)
        d.text(72, "▪ 每單位商品到期贖回金額=每單位商品面額x (100%+紅利配息率)")
        d.text(72, f"= {money(denom)} {unit}x(100%+{_pct(s.annual)})")
        d.text(72, f"= {money(amount)} {unit}")
        returns(amount, f"{money(amount)} {unit}")

    titles = ("較佳情況", "較佳情況") if s.digital else ("較佳情況 A", "較佳情況 B")
    d.section = "scen.一"
    d.text(72, f"情境一:{titles[0]}")
    close = (initial * s.above / 100).quantize(Q4)
    condition(close, f"收盤價高於或等於{k}，投資人可於到期日以現金交割形式領回：")
    if s.digital:
        bonus_amount(close)
    else:
        amount = (denom * close / initial).quantize(Q2, ROUND_HALF_UP)
        base = "回顧期初價格" if s.lookback else "期初價格"
        d.text(
            72,
            f"▪ 每單位商品到期贖回金額=每單位商品面額x{{100%+最大值[紅利配息率, 參與率x({who}期末定價日之收盤價/{who}{base}-100%)]}}",
        )
        d.text(72, f"= {denom:,}x[100%+100% x ({close:.4f} 美元/{initial:.4f} 美元-100%)]")
        d.text(72, f"= {money(amount)}")
        returns(amount, money(amount))
    d.section = "scen.二"
    d.text(72, f"情境二:{titles[1]}")
    close = strike if s.digital else (initial * s.below / 100).quantize(Q4)
    condition(close, f"收盤價高於或等於{k}，到期日以現金結算收益：")
    bonus_amount(close)
    d.section = "scen.三"
    d.text(72, "情境三:最差情況")
    close = (initial * s.worse / 100).quantize(Q4)
    condition(close, f"收盤價低於其{k}，到期日以實物交割：")
    shares = (denom / strike).quantize(Q4, ROUND_HALF_UP)
    whole = shares.quantize(Decimal(1), ROUND_DOWN)
    fraction = shares - whole
    remainder = (close * fraction).quantize(Q2, ROUND_HALF_UP)
    value = close * whole + remainder
    pnl = value - denom
    annual = ((pnl / denom * 100).quantize(Q2, ROUND_HALF_UP) * 12 / s.tenor).quantize(Q2, ROUND_HALF_UP)
    d.text(72, f"▪ {who}交割股數計算方式為：單位數×單位商品面額×期末匯率/{who}股票{k}")
    d.text(72, f"= 1x{money(denom)} 美元x 1/{strike:.4f} 美元")
    d.text(
        72, f"= {shares} 股(四捨五入至小數點後第四位）；將上述數字由小數點後無條件捨去即為每單位商品面額{who}交割股數，"
    )
    d.text(72, f"在此例中為 {whole} 股。")
    d.text(72, f"▪ {who}畸零股數（即 {fraction} 股）則以美元支付（剩餘金額），計算方式為：")
    d.text(72, f"= {close:.4f} 美元x {fraction}/1")
    d.text(72, f"= {money(remainder)} 美元（四捨五入至小數點後第二位)")
    d.text(72, "每單位商品面額到期贖回價值計算方式為：")
    d.text(72, f"= ({close:.4f} 美元/1) x {whole} 股+{money(remainder)} 美元={money(value)} 美元")
    d.text(72, "每單位商品面額結算收益計算方式為：")
    d.text(72, f"= {money(value)} 美元+0 美元-{money(denom)} 美元={money(pnl)} 美元")
    d.text(72, f"以簡單平均計算報酬率之方式以計算年化報酬率為{annual}%（四捨五入至百分位後第二位)")
    d.section = "scen.四"
    d.text(72, "情境四：發行機構無法履約")
    d.text(72, "投資人要承擔發行機構之無擔保信用風險(報酬率則為-100%)。")


TITLES = {
    1: None,  # 名稱，依規格
    2: WARNING_CH1,
    5: None,
    6: None,
    7: "發行價格：商品面額之100%",
    8: "計價貨幣本金保本率：無，本商品為不保障本金之結構型商品。",
    9: "原計價幣別投資本金達成保本之各項事件：不適用。本商品為不保障本金之結構型商品。",
    10: "主要給付項目及其計算方式：",
    11: "連結標的資產，及其相對權重、與投資績效之關連情形：",
    12: "連結標的資產之相關說明",
    14: "商品年期、發行日、到期日及其他依個別商品性質而定之日期：",
    15: "到期贖回計算公式、最低保證贖回率及參與率：",
    16: "投資收益計算方法，包含本金虧損之機率及以情境分析解說最大可能獲利、損失及其他狀況之年化平均報酬率：",
    19: "發行機構得提前買回/贖回或投資人得提前贖回之條件及應注意事項：",
}
ARTICLES = (*range(1, 20), *range(21, 29))


def build_pdf(path: Path, s: BenSpec, *, edits: Sequence[Edit] = (), iis: bool = True) -> Path:
    """合成 MS BEN 說明書；檔名是 `<商品代號>_TS.pdf` 且 `iis` 時旁邊另寫一份投資人須知，`edits` 兩份都套用（依段落代號）。"""
    used: set[Edit] = set()
    if iis and path.stem.endswith("_TS"):
        build_iis_pdf(iis_path(path), s, edits=edits, used=used, body=_iis_body)
    d = _Doc(edits, "ts", used)
    cover(d, s)
    d.chapter("一", "商品基本資料")
    titles = {
        **TITLES,
        1: f"商品中文名稱：{s.name_zh.replace('(下稱「本商品」)', '')}",
        3: f"發行機構名稱及其長期債務信用評等：本商品發行機構為{ISSUER.split('(')[0]}，截至本中文產品說明書刊印日期，"
        f"發行機構之長期債務信用評等{ratings_text(s.ratings, '經{name}評定為{value}')}。",
        5: f"計價幣別：{s.currency_zh} ({s.ccy})",
        6: f"每單位商品面額：{s.currency_zh} {money(s.denom)} 元",
    }
    for n in ARTICLES:
        d.article(n, titles.get(n) or "範本說明。")
        if n == 10:
            _article10(d, s)
        elif n == 11:
            underlying_table(d, s)
        elif n == 12:
            write_notes(d.w, s.underlyings, "inline", x=72, label="({k})連結標的{k}：", heading=None, width=44)
        elif n == 14:
            _article14(d, s)
        elif n == 15:
            _article15(d, s)
        elif n == 16:
            _article16(d, s)
        elif n == 19:
            for k, what in enumerate(("稅務贖回事件", "違約事件", "不合法性事件", "證券法律之遵循"), 1):
                d.text(72, f"20.{k} {what}")
                d.text(72, "範本說明。")
    return later_chapters(d, s, edits, used, path)


def _iis_body(d: _Doc, s: BenSpec) -> None:
    """MS BEN 投資人須知：商品簡介（11) 開始受理贖回日期，沒有配息項）、收益分配事項、贖回價金之計算、主要投資風險。"""
    w = d.w
    zh_issuer = ISSUER.split("(")[0]
    d.section = "summary"
    d.text(18, "二、 境外結構型商品事項：")
    d.text(18, "1. 商品簡介：")
    label = (lambda i: f"連結標的{i}") if len(s.underlyings) > 1 else (lambda i: "連結標的")
    uls = " ".join(f"({i}) {label(i)}: {underlying_name(i)}" for i in range(1, len(s.underlyings) + 1))
    redeem_from = weekday_on_or_after(s.issue_date + dt.timedelta(days=1))
    for n, t in enumerate(
        [
            "受託對象：專業投資人及OSU 客戶",
            "與國外相當之交易條件：專業投資人及OSU 客戶不適用。",
            "本商品風險程度： RR4",
            f"發行機構之長期債務信用評等：本商品發行機構為{zh_issuer}，截至本投資人須知刊印日期，"
            f"發行機構之長期債務信用評等{ratings_text(s.ratings, '{name}{value}')}。",
            "商品之發行評等：無",
            f"計價幣別：{s.currency_zh} ({s.ccy})",
            "計價貨幣本金保本率：無。",
            "投資本金達成100%保本率之各項條件：不適用。",
            f"連結標的資產：{uls}",
            f"商品年期：{s.tenor} 個月期；交易日：{zh_date(s.trade_date)}；發行日: {zh_date(s.issue_date)}；到期日："
            f"{zh_date(s.maturity_date)}；期末定價日：{zh_date(s.final_date)} ，如當日非預定交易日，次一預定交易日為之。",
            f"開始受理贖回日期：{zh_date(redeem_from)}",
            "後續受理贖回日期：範本說明。",
        ],
        1,
    ):
        d.text(18, f"{n}) {t}", 47)
    d.section = "coupon"
    d.text(18, "2. 收益分配事項：於到期日，視期末定價日時籃子中表現最差的連結標的之收盤價而定。")
    w.new_page()
    participation = "0%" if s.digital else f"{s.participation:.2f}%"
    d.text(18, f"紅利配息率：{_pct(s.annual)}；{MIN_RATE}；參與率：{participation}")
    d.section = "redeem"
    d.text(18, "3. 贖回價金之計算：")
    d.text(
        18,
        f"1) 到期贖回：{HEAD}{s.cash()}{s.delivery(iis=True)}"
        f"{s.subject}之認定及{s.strike_word}請參閱本商品中文產品說明書第一章第15 項之說明。",
        47,
    )
    d.text(18, "2) 投資人於次級市場申請提前贖回：提前贖回之商品單位數 x 每單位商品面額 x 提前贖回價格百分比", 47)
    d.text(18, "4. 發行不成立之處理：範本說明。")
    d.section = "risk"
    d.text(18, "三、 主要投資風險：")
    d.text(
        18,
        "1. 基本風險資訊：範本說明。2. 個別商品風險資訊：(6)本金轉換風險：若於期末定價日時籃子中表現最差的連結標的之收盤價"
        "低於其執行價，投資人將收到連結標的。",
    )


def row(s: BenSpec, **overrides: Any) -> dict[str, Any]:
    """與 `s` 一致的參考條件表列：Product `BEN`、參與率依型態、回顧結束日空白（回填）；`overrides` 以 Excel 欄名覆寫。"""
    out = reference_row(s)
    out["Product"] = BEN_PRODUCT
    out["參與率(%)"] = float(s.participation)
    out.update(overrides)
    return out


def check_pair(tmp_path: Path, s: BenSpec, *, edits: Sequence[Edit] = (), headers=HEADERS, **overrides: Any):
    """合成說明書＋投資人須知＋參考條件表一列，經批量入口核對，回傳（說明書, 投資人須知）的批量核對項目。"""
    tmp_path.mkdir(parents=True, exist_ok=True)
    pdf = build_pdf(tmp_path / f"{s.product_code}_TS.pdf", s, edits=edits)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [row(s, **overrides)], headers)
    outcome = check_all(sheet, [pdf, iis_path(pdf)])
    ts = next(i for i in outcome.items if i.term_sheet == pdf)
    return ts, next(i for i in outcome.items if i.term_sheet != pdf)
