"""HSBC 合成說明書與投資人須知 PDF：把商品規格（tests/checker/reference_synth.py 的 `ProductSpec`）畫成仿 HSBC 版面；
商品、標的與價格皆虛構。參考條件表列不在這裡產生（`reference_synth.reference_row`）。

百分比、價格表、期數與日期、面額、幣別與 Non-Call 都由規格算出；預設值下兩份文件與參考條件表列完全一致。
改字製造錯誤用 `edits`（tests/checker/pdf_writer.py 的 `Edit`），段落代號：說明書 `ts.cover`（第一章之前）、第一章各條
`ts.art{n}`（第 12 條價格表 `ts.art12.table`、第 18 條情境表 `ts.art18.table`）、其他各章 `ts.ch二`～`ts.ch五`；
投資人須知 `iis.p1`～`iis.p4`（依頁）。Super Bull（`SbSpec`，#314）的第 11 條提前贖回表另有 `ts.art11.ko`；BEN（`BenSpec`，
#375）沒有配息與提前贖回，第 11、16～18 條另有寫法。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from pathlib import Path

from fcn_checker.parsers.super_bull import add_months
from harness import STANDARD, iis_path
from pdf_writer import FONT, Edit, PdfWriter, require_used, zh_date
from reference_synth import FITCH, MOODYS, SP, UL, ProductSpec, Ratings, price, quote_iso, write_notes

CENT = Decimal("0.01")
SB_PRODUCT = "Super Bull"
SB_NAME = STANDARD["issuer"]["hsbc"]["product_names"][SB_PRODUCT]
# 人民幣商品第一章第 5 條在幣別後面接的說明段落（真實樣本寫法節錄，Issue #180）
CNH_NOTE = (
    "人民幣計價債券之說明事項：交割貨幣：境外人民幣本債券將以境外人民幣交割並於中國大陸境外交易。替代交割貨幣：美金"
)

ISSUER_ZH = "香港商香港上海滙豐銀行股份有限公司"


def ratings_text(r: Ratings) -> str:
    """第一章第 3 項與投資人須知第 4 點的評等寫法（#240）：穆迪、標普 `/` 分隔，最後一家前用「與」。"""
    names = {MOODYS: "穆迪（Moody's）", SP: "標準普爾(Standard & Poor's ) ", FITCH: "惠譽(Fitch)"}
    parts = [f"{names[a]}信評{v}" for a, v in r.stated(MOODYS, SP, FITCH)]
    return " /".join(parts[:-1]) + (" 與" if len(parts) > 1 else "") + parts[-1]


def ratings_ch2(r: Ratings) -> str:
    """第二章 (7) 信用評等的寫法（#240）：穆迪、惠譽、標普各一句，以「；」分隔。"""
    clauses = {
        MOODYS: "穆迪投資服務公司（Moody's Investors Service）給予" + ISSUER_ZH + "長期信用評等為 {v}",
        FITCH: "惠譽國際（Fitch Corporation）給與 {v} 的等級",
        SP: "標準普爾公司（Standard and Poor's Corporation）則給與 {v} 的等級",
    }
    return "；".join(clauses[a].format(v=v) for a, v in r.stated(MOODYS, FITCH, SP))


# 價格表各欄的左緣（真實樣本的欄序）
TABLE_X = {
    "code": 60.0,
    "name": 110.0,
    "ccy": 170.0,
    "exchange": 200.0,
    "initial": 250.0,
    "strike": 310.0,
    "ko": 390.0,
    "ki": 470.0,
}


def underlyings(count: int) -> tuple[UL, ...]:
    """HSBC 合成文件的標的：ZZn UW、虛構標的n、期初價 100。"""
    return tuple(UL(f"虛構標的{i}", "NASDAQ", f"ZZ{i} UW", Decimal("100.0000")) for i in range(1, count + 1))


def zh_date_tight(d: dt.date) -> str:
    """HSBC 封面與第 15 條的日期寫法（數字與年月日之間不空格）。"""
    return f"{d.year}年{d.month}月{d.day}日"


def pct(x: Decimal) -> str:
    """說明書顯示的百分比：去掉多餘的零（70.00 → 70）。"""
    return f"{x.normalize():f}"


@dataclass
class Spec(ProductSpec):
    """HSBC 合成文件的規格：商品規格加上 HSBC 專屬的文件選項（情境一不足一期的配息）。標的由 `count` 產生；最終比價日與到期日由排程推得（不能直接給）。"""

    issuer: str = "HSBC"
    product_code: str = "325199990001"
    ki: str = "AM"
    annual: Decimal = Decimal("12")
    first_callable: int = 2  # 0：FCN_Express（第 1 期期始日起每日觀察，第 1 期提前到期配整期息，workflow #306）
    final_date: dt.date = field(init=False)
    maturity_date: dt.date = field(init=False)
    underlyings: tuple[UL, ...] = field(init=False)
    count: int = 2  # 標的數
    partial_coupon: bool = False  # 情境一多一行不足一期的配息
    inline_articles_from: int | None = None  # 第一章從第 N 條起條號與標題同一行（`10. 發行價格：`，Issue #185）

    def __post_init__(self) -> None:
        self.underlyings = underlyings(self.count)
        self.final_date, self.maturity_date = self.ends[-1], self.payments[-1]

    @property
    def ends(self) -> list[dt.date]:
        """各期期末日：交易日往後第 j 個月的同一天。"""
        return [add_months(self.trade_date, j) for j in range(1, self.tenor + 1)]

    @property
    def payments(self) -> list[dt.date]:
        return [d + dt.timedelta(days=3) for d in self.ends]

    @property
    def starts(self) -> list[dt.date | None]:
        """D 型各期期始日：第 1～k 期「-」，之後是前一期期末日後的下一個平日；Express（k = 0）第 1 期是發行日後的下一個平日。"""
        out: list[dt.date | None] = []
        for i in range(1, self.tenor + 1):
            if i <= self.first_callable:
                out.append(None)
                continue
            start = (self.ends[i - 2] if i > 1 else self.issue_date) + dt.timedelta(days=1)
            while start.weekday() >= 5:
                start += dt.timedelta(days=1)
            out.append(start)
        return out

    @property
    def express(self) -> bool:
        return self.first_callable == 0

    @property
    def monthly(self) -> Decimal:
        return (self.annual / 12).quantize(Decimal("0.0001"), ROUND_HALF_UP)

    @property
    def coupon(self) -> Decimal:
        """每期固定配息金額（面額 × 月配息率）。"""
        return (self.denom * self.monthly / 100).quantize(CENT, ROUND_HALF_UP)

    def coupons(self, periods: int) -> Decimal:
        """前 `periods` 期配息合計：以年利率算後才四捨五入（不是每期金額 × 期數）。"""
        return (self.denom * self.annual / 100 / 12 * periods).quantize(CENT, ROUND_HALF_UP)

    @property
    def name(self):
        return STANDARD["issuer"]["hsbc"]["product_name"]["zh"].format(
            tenor=self.tenor, ccy_zh=self.currency_zh, memory_zh="記憶式" if self.memory else ""
        )

    @property
    def en(self):
        return STANDARD["issuer"]["hsbc"]["product_name"]["en"].format(
            maxi_en="Maxi " if len(self.underlyings) > 1 else "",
            daily_en="Daily " if self.ko_obs == "D" else "",
            memory_en="Memory " if self.memory else "",
            express_en="Express " if self.express else "",
        )


@dataclass
class SbSpec(Spec):
    """HSBC Super Bull 合成規格（docs/checker/templates/hsbc-zh-super-bull.md、hsbc-zh-super-bull-iis.md）：沒有配息，`annual`
    是自動提前贖回配息 x%（不年化，同參考條件表 `Coupon p.a. (%)`）；`months` 是提前贖回表各決定日距發行日的月數，
    決定日 = 發行日 + m 個月再加 `shift` 個日曆天，付款日 = 決定日後 5 天，最後評價日 = 發行日 + 天期，到期日 = 其後 5 天；
    Non-Call = 第一個 m。預設照現行版樣本：非記憶式、無 KI、KO% = K% = 85、美元、3 檔、6 個月、第 3 個月決定一次。
    情境以第 1 檔為最差標的：情境二期末股價 = 期初 × `best`%、情境三 = 期初 × `worse`%。"""

    months: tuple[int, ...] = (3,)
    annual: Decimal = Decimal("6.0000")
    strike: Decimal = Decimal("85.0000")
    ko: Decimal = Decimal("85.0000")
    memory: bool = False
    ko_obs: str = "P"
    ki: str = "none"
    count: int = 3
    first_callable: int = field(init=False)
    shift: int = 0
    maxi: bool = False  # 英文名稱前面多「Maxi 」（樣本 SB-HS01；不核對）
    best: Decimal = Decimal("130")
    worse: Decimal = Decimal("60")

    def __post_init__(self) -> None:
        self.first_callable = self.months[0]
        super().__post_init__()

    @property
    def ends(self) -> list[dt.date]:
        """各決定日，最後是最後評價日（發行日 + 天期）。"""
        shift = dt.timedelta(days=self.shift)
        return [*(add_months(self.issue_date, m) + shift for m in self.months), add_months(self.issue_date, self.tenor)]

    @property
    def payments(self) -> list[dt.date]:
        return [d + dt.timedelta(days=5) for d in self.ends]

    @property
    def name(self):
        return SB_NAME["zh"].format(tenor=self.tenor, ccy_zh=self.currency_zh)

    @property
    def en(self):
        return ("Maxi " if self.maxi else "") + SB_NAME["en"]

    @property
    def k(self) -> Decimal:
        """最差標的（第 1 檔）的執行價。"""
        return price(self.underlyings[0].initial, self.strike)

    def close(self, pct_: Decimal) -> Decimal:
        """最差標的（第 1 檔）的期末股價 = 期初 × pct_%。"""
        return (self.underlyings[0].initial * pct_ / 100).quantize(Decimal("0.0001"))

    @property
    def shares(self) -> Decimal:
        return (Decimal(self.denom) / self.k).quantize(Decimal("0.0001"), ROUND_HALF_UP)


BEN_PRODUCT = "BEN"
BEN_NAME = STANDARD["issuer"]["hsbc"]["product_names"][BEN_PRODUCT]


@dataclass
class BenSpec(Spec):
    """HSBC BEN 合成規格（docs/checker/templates/hsbc-zh-ben.md、hsbc-zh-ben-iis.md）：`kind` 是 `maxi`（紅利表現）或 `digital`
    （紅利配息）；沒有定期配息、沒有 KO、無 KI，`annual` 是紅利（不年化，同參考條件表 `Coupon p.a. (%)`）；Non-Call = 天期。
    參考條件表 KO% 照填 K%（說明書沒有 KO，不核對）。預設照樣本：Maxi 2 檔、Digital 1 檔、美元、6 個月。
    情境以第 1 檔為最差標的：Maxi 情境一期末股價 = 期初 × `above`%（漲幅大於紅利）、情境二 × `below`%（漲幅小於紅利）、
    交股情境 × `worse`%（Digital 情境三期末股價 0）。`residue` 在 §12(1) 表後加方括號殘留註記；`word` 是沒有 (最終版) 的排版。"""

    kind: str = "maxi"
    annual: Decimal = Decimal("12.0000")
    strike: Decimal = Decimal("90.0000")
    ko: Decimal = Decimal("90.0000")
    memory: bool = False
    ko_obs: str = "P"
    ki: str = "none"
    first_callable: int = field(init=False)
    residue: bool = False
    word: bool = False
    above: Decimal = Decimal("150")
    below: Decimal = Decimal("105")
    worse: Decimal = Decimal("60")

    def __post_init__(self) -> None:
        self.first_callable = self.tenor
        super().__post_init__()

    @property
    def digital(self) -> bool:
        return self.kind == "digital"

    @property
    def name(self):
        payoff = BEN_NAME["digital_zh" if self.digital else "enhanced_zh"]
        return BEN_NAME["zh"].format(tenor=self.tenor, ccy_zh=self.currency_zh, payoff_zh=payoff)

    @property
    def en(self):
        if self.digital:
            return BEN_NAME["en"].format(payoff_en=BEN_NAME["digital_en"])
        return "Maxi " + BEN_NAME["en"].format(payoff_en=BEN_NAME["enhanced_en"])

    @property
    def k(self) -> Decimal:
        """最差標的（第 1 檔）的執行價。"""
        return price(self.underlyings[0].initial, self.strike)

    def close(self, pct_: Decimal) -> Decimal:
        return (self.underlyings[0].initial * pct_ / 100).quantize(Decimal("0.0001"))

    @property
    def shares(self) -> Decimal:
        return (Decimal(self.denom) / self.k).quantize(Decimal("0.0001"), ROUND_HALF_UP)


def slash(d: dt.date) -> str:
    """Super Bull 提前贖回表的日期寫法 `YYYY/M/D`。"""
    return f"{d.year}/{d.month}/{d.day}"


def shown_pct(x: Decimal) -> str:
    """情境年化報酬率：2 位，整數時不補 0（樣本寫法）。"""
    q = x.quantize(CENT, ROUND_HALF_UP)
    return f"{q.normalize():f}" if q == q.to_integral_value() else f"{q:.2f}"


def build_pdf(path: Path, s: Spec, *, edits: Sequence[Edit] = (), iis: bool = True) -> Path:
    """合成說明書；檔名是 `<商品代號>_TS.pdf` 且 `iis` 時，旁邊另寫一份同商品投資人須知（ADR 0007：兩份一起核對），
    `edits` 兩份都套用（依段落代號）。"""
    used: set[Edit] = set()  # 說明書與投資人須知共用：改字換到哪一份都算
    if iis and path.stem.endswith("_TS"):
        build_iis_pdf(iis_path(path), s, edits=edits, used=used)
    w = PdfWriter(edits, used=used)
    ccy, denom, monthly, n = s.currency_zh, f"{s.denom:,}", s.monthly, s.tenor
    first = s.first_callable
    sb = isinstance(s, SbSpec)
    ben = isinstance(s, BenSpec)

    def line(t, x=110):
        t = w.edit(t)
        # Wrap between text runs, never in the middle of a printed number.
        with w.verbatim():
            while t:
                end = min(48, len(t))
                while end < len(t) and t[end - 1] in "0123456789,." and t[end] in "0123456789,.":
                    end += 1
                w.line(x, t[:end])
                t = t[end:]

    def chapter(n, name):
        w.new_page()
        w.section = f"ch{n}"
        w.line(60, f"第{n}章 {name}")

    def article(n, text):
        w.section = f"art{n}"
        if s.inline_articles_from is not None and n >= s.inline_articles_from:
            line(f"{n}. {text}", 60)
            return
        w.line(60, f"{n}.")
        line(text)

    def sub(n):
        w.line(80, f"({n})")

    def table(section, names=True, exchange=True):
        """價格表（真實欄列版面）：彭博代號、中文名稱、幣別、交易所、期初股價，價格欄的「(即期初股價的X%)」在欄名下一行。
        Super Bull 的自動提前贖回價欄在執行價欄前面，BEN 只有執行價欄；`names` 為 False 時沒有中文名稱欄（Super Bull、BEN
        情境價格表），`exchange` 為 False 時沒有交易所欄（BEN Digital 情境價格表）。"""
        w.section = section
        x = TABLE_X
        heads = [(x["code"], ["彭博代號"])]
        if names:
            heads.append((x["name"], ["中文名稱"]))
        heads.append((x["ccy"], ["幣別"]))
        if exchange:
            heads.append((x["exchange"], ["交易所"]))
        heads.append((x["initial"], ["期初股價"]))
        if ben:
            cols = [("執行價", s.strike, x["strike"])]
        elif sb:
            cols = [("自動提前贖回價", s.ko, x["strike"]), ("執行價", s.strike, x["ko"])]
        else:
            cols = [("執行價", s.strike, x["strike"]), ("自動提前到期價格", s.ko, x["ko"])]
        if s.ki != "none":
            cols.append(("觸及不保本價格", s.ki_pct, x["ki"]))
        heads += [(at, [label, f"(即期初股價的{pct(p)}%)"]) for label, p, at in cols]
        rows = []
        for u in s.underlyings:
            # 幣別格是標的交易所的交易幣別（Issue #227）
            ccy = quote_iso(u.ticker)
            cells = [(x["code"], [u.ticker])]
            if names:
                cells.append((x["name"], [u.name]))
            cells.append((x["ccy"], [ccy]))
            if exchange:
                cells.append((x["exchange"], [u.exchange]))
            cells.append((x["initial"], [f"{u.initial:.4f}"]))
            cells += [(at, [f"{price(u.initial, p):.4f}"]) for _, p, at in cols]
            rows.append(cells)
        w.grid(heads, rows)

    def sb_article11():
        """Super Bull 第 11 條（範本規格 §2、§4.1）：(1) 贖回金額句、四欄提前贖回表（表頭拆行、一格一行）、事件句、頻率句、
        出場紅利；(2) 到期公式與執行價定義（內層 (1)(2) 不在子項欄位，不是子項）。"""
        line("本商品主要給付項目為「自動提前贖回金額」或「到期贖回金額」")
        sub(1)
        line("若本商品於任一自動提前贖回決定日發生自動提前贖回事件，發行機構將在該自動提前贖回付款日")
        line("支付贖回金額 = 每單位面額 × (100% + 自動提前贖回配息)")
        w.section = "art11.ko"
        for t in ["自動提前贖回價", "自動提前贖回", "自動提前贖", "贖回價", "決定日", "回付款日"]:
            line(t)
        for end, pay in zip(s.ends[:-1], s.payments[:-1], strict=True):
            for t in [f"{s.ko:.4f}%", slash(end), slash(pay), f"{100 + s.annual:.4f}%"]:
                line(t)
        w.section = "art11"
        line(
            "自動提前贖回事件：若於任一自動提前贖回決定日，所有連結標的之收盤價皆大於或等於其自動提前贖回價，"
            "則自動提前贖回事件視同發生。"
        )
        line("自動提前贖回事件頻率: 每半年一次，自最初評價日開始(含)")
        line(f"自動提前贖回配息為{s.annual:.4f}%")
        sub(2)
        line("若本商品於所有自動提前贖回決定日均未發生自動提前贖回事件，則於到期日依下列計算公式給付：")
        line("(1) 若最差表現之連結標的期末股價小於執行價，以實物給付最差表現連結標的之股份數(簡稱為「連結標的股數」)")
        line("及以現金支付零股部分。")
        line("(2) 若最差表現之連結標的期末股價大於或等於執行價：以現金給付")
        line("每單位面額x (最差表現連結標的期末股價/最差表現連結標的執行價)")
        line(f"執行價為期初股價 × {s.strike:.4f}%，如下述12(1)表格所述。")

    def sb_article18():
        """Super Bull 第 18 條（範本規格 §5）：基本假設、情境價格表（沒有中文名稱欄）、情境一～三（第 1 檔為最差標的）。"""
        cash, d = f"{s.denom:,.2f}", Decimal(s.denom)
        k_, ul = s.k, s.underlyings[0]
        line(f"商品天期為{n} 個月期")
        line(f"每單位面額為{ccy} {cash} 元,發行價格為 100.0000%")
        line(f"每單位期初投資金額 = {ccy} {cash} (={denom} × 100.0000%)")
        line(f"期末匯率為1 {ccy} = {ccy} 1.00")
        line(f"自動提前贖回配息為{s.annual:.4f}%")
        table("art18.table", names=False)
        w.section = "art18"

        def amount(x: Decimal) -> str:
            return f"{x:,.2f}"

        def pnl(value: Decimal, label: str) -> Decimal:
            line(f"{label}每單位面額期初投資金額")
            line(f"= {ccy} {amount(value)} - {ccy} {cash}")
            line(f"= {ccy} {amount(value - d)}")
            return value - d

        def annualized(head: str, x: Decimal, tail: str = "") -> None:
            line(
                f"{head}平均年化報酬率(以簡單平均年化報酬率之方式計算){tail}: {shown_pct(x)}% (四捨五入至百分位後第二位)"
            )

        a1 = (d * (100 + s.annual) / 100).quantize(CENT, ROUND_HALF_UP)
        line("情境分析一) 於第 1 個自動提前贖回決定日，籃子中表現最差連結標的之期末股價大於或等於其自動提前贖回價格")
        line(f"1) 最差表現連結標的（{ul.name}）期末股價為{ccy} {s.close(s.best)}，高於自動提前贖回價{ccy}{k_}")
        line("每單位面額 × (100% + 自動提前贖回配息)")
        line(f"= {ccy}{cash} × (100% + {s.annual:.4f}%)")
        line(f"= {ccy}{amount(a1)}")
        pnl(a1, "2) 每單位投資損益: 每單位面額自動提前贖回金額 - ")
        annualized("3)", s.annual * 12 / s.months[0])

        p2 = s.close(s.best)
        a2 = (d * p2 / k_).quantize(CENT, ROUND_HALF_UP)
        line(
            "情境分析二) 不曾發生任何自動提前贖回事件，且於期末評價日籃子中表現最差連結標的之期末股價大於或等於其執行價"
        )
        line(f"1) 最差表現連結標的（{ul.name}）期末股價為{ccy} {p2}，高於執行價{ccy}{k_}")
        line("每單位面額 × (最差表現連結標的期末股價/最差表現連結標的執行價)")
        line(f"={ccy}{cash} × ({p2}/{k_})")
        line(f"={ccy}{amount(a2)}")
        gain = pnl(a2, "2) 每單位投資損益: 每單位面額到期贖回金額 - ")
        annualized("3)", gain / d * 100 * 12 / n)

        p3 = s.close(s.worse)
        whole = s.shares.to_integral_value(ROUND_DOWN)
        fraction = s.shares - whole
        rest = (p3 * fraction).quantize(CENT, ROUND_HALF_UP)
        value = (p3 * whole + rest).quantize(CENT, ROUND_HALF_UP)
        line("情境分析三) 不曾發生任何自動提前贖回事件，且於期末評價日籃子中最差表現連結標的期末股價低於執行價")
        line(f"1) 最差表現連結標的（{ul.name}）期末股價為{ccy} {p3}，低於執行價{ccy} {k_}")
        line("2) 每單位面額最差表現連結標的實物給付計算方式為：")
        line("每單位面額 × 期末匯率 / 最差表現連結標的執行價")
        line(f"= {ccy} {cash} × 1.00 / {ccy} {k_}")
        line(f"= {s.shares} 股")
        line(f"將上述數字由小數點後無條件捨去即為交割股數，在此例中為{whole} 股")
        line(f"3) 每單位面額最差表現連結標的之零股數(即{fraction} 股)則以{ccy}支付(「剩餘金額」)，計算方式為：")
        line("最後評價日最差表現連結標的期末股價 × 零股數 / 期末匯率")
        line(f"={ccy} {p3} × {fraction} / 1.00")
        line(f"={ccy} {amount(rest)}")
        line("4) 每單位面額到期贖回價值計算方式為:")
        line(f"= ({ccy} {p3} × {whole} / 1.00) + {ccy} {amount(rest)}")
        line(f"= {ccy} {amount(value)} (四捨五入至小數點後第二位)")
        loss = pnl(value, "5) 每單位面額結算收益計算方式為: 每單位面額到期贖回價值-")
        annualized("6) ", loss / d * 100 * 12 / n, "為")

    def ben_article11():
        """BEN 第 11 條（範本規格 §2）：開頭句、(1) 到期公式與交股句、定義段（Maxi 紅利、K%）；Digital 另有 (2) 配息金額。"""
        if s.digital:
            line("本商品主要給付項目為配息金額與到期贖回金額，該金額視期末股價而定")
            sub(1)
            line("如連結標的，其期末股價〔等於或大於〕其執行價：以現金給付每單位面額×100%")
            line(
                "如連結標的，其期末股價〔小於〕其執行價：以實物給付連結標的之股份數(簡稱為「連結標的股數」)及以現金支付零股部分"
            )
            line(f"執行價為期初股價 × {pct(s.strike)}%")
            sub(2)
            line(
                "如最差表現連結標的，其期末股價於最後評價日〔等於或大於〕其執行價，下列金額將於到期日由發行機構支付予投資人："
                "以現金給付每單位面額 × 紅利配息率"
            )
            line(f"紅利配息率：{s.annual:.4f}% %")
            line("如最差表現連結標的，其期末股價於最後評價日〔小於〕其執行價，則無紅利配息。")
            return
        line("本商品主要給付項目為商品到期贖回金額")
        sub(1)
        line(
            "(i) 若最差表現連結標的，其期末股價〔等於或大於〕執行價：以現金給付 每單位面額x {100% + 最大值 [紅利配息率，"
            "(最差表現連結標的期末股價 /最差表現連結標的期初股價-1)]}"
        )
        line(
            "(ii) 若最差表現連結標的，其期末股價〔小於〕執行價：以實物給付最差表現連結標的之股份數(簡稱為「連結標的股數」)"
            "及以現金支付零股部分"
        )
        line(f"紅利配息率為{s.annual:.4f}%")
        line(f"執行價為期初股價 × {s.strike:.4f}%，如下述12(1)表格所述。")

    def ben_article18():
        """BEN 第 18 條（範本規格 §5）：基本假設、情境價格表（沒有中文名稱欄，Digital 也沒有交易所欄）、情境一～三（第 1 檔為
        最差標的）。Maxi 情境一、二到期金額以期初股價算漲幅，情境三交股；Digital 情境一付紅利，情境二、三交股。"""
        d, ul, digital = Decimal(s.denom), s.underlyings[0], s.digital
        cash = denom if digital else f"{s.denom:,.2f}"  # Digital 金額不寫小數
        fx = "1" if digital else "1.00"
        bonus = f"{s.annual:.4f}%" + ("%" if digital else "")
        line(f"商品天期為{n} 個月期")
        line(f"每單位面額為{ccy} {cash} 元,發行價格為 100.0000%")
        line(f"每單位期初投資金額 = {ccy} {cash} (={denom} × 100.0000%)")
        line(f"期末匯率為1 {ccy} = {ccy} {fx}")
        line(f"紅利配息率為{bonus}")
        if digital:
            line("*假設天期將因不同可供選擇之商品天期而異。")
        table("art18.table", names=False, exchange=not digital)
        w.section = "art18"
        investment = "初始" if digital else "期初"

        def amount(x: Decimal) -> str:
            return f"{x:,.2f}"

        def pnl(value: Decimal, label: str) -> Decimal:
            line(f"{label}每單位面額{investment}投資金額")
            line(f"= {ccy} {amount(value)} - {ccy} {cash}")
            line(f"= {ccy} {amount(value - d)}")
            return value - d

        def annualized(head: str, gain: Decimal, formula: bool = False) -> None:
            x = gain / d * 100 * 12 / n
            how = f"=美元 {amount(gain)} / 美元 {cash} / {n} × 12 = " if formula else ""
            line(f"{head}平均年化報酬率(以簡單平均年化報酬率之方式計算)為:{how}{shown_pct(x)}%")

        def enhanced(title: str, p: Decimal) -> None:
            gain = (p / ul.initial - 1) * 100
            applied = max(s.annual, gain)
            value = (d * (100 + applied) / 100).quantize(CENT, ROUND_HALF_UP)
            shown = f"{s.annual:.4f}" if applied == s.annual else f"{applied:.2f}"
            line(title)
            line(f"1) 最差表現連結標的（{ul.name}）期末股價為{ccy} {p}，高於執行價{ccy}{s.k}")
            line("每單位面額x {100% + 最大值 [紅利配息率，(最差表現連結標的期末股價 /最差表現連結標的期初股價-1)]}")
            line(f"= {ccy}{cash} x {{100% + 最大值 [{s.annual:.4f}%，({p}/{ul.initial}-1)]}}")
            line(f"= {ccy}{cash} x {{100% + {shown}%}}")
            line(f"= {ccy}{amount(value)}")
            annualized("3) ", pnl(value, "2) 每單位投資損益: 每單位面額到期贖回金額 - "))

        def delivery(title: str, p: Decimal) -> None:
            whole = s.shares.to_integral_value(ROUND_DOWN)
            fraction = s.shares - whole
            rest = (p * fraction).quantize(CENT, ROUND_HALF_UP)
            value = (p * whole + rest).quantize(CENT, ROUND_HALF_UP)
            line(title)
            line(f"1) 最差表現連結標的（{ul.name}）期末股價為{ccy} {p}，低於執行價{ccy} {s.k}")
            line("2) 每單位面額最差表現連結標的實物給付計算方式為：")
            line(f"每單位面額 × 期末匯率 / {'' if digital else '最差表現'}連結標的執行價")
            line(f"= {ccy} {cash} × {fx} / {ccy} {s.k}")
            line(f"= {s.shares} 股")
            line(f"將上述數字由小數點後無條件捨去即為交割股數，在此例中為{whole} 股")
            line(f"3) 每單位面額最差表現連結標的之零股數(即{fraction} 股)則以{ccy}支付(「剩餘金額」)，計算方式為：")
            line("最後評價日最差表現連結標的期末股價 × 零股數 / 期末匯率")
            line(f"={ccy} {p} × {fraction} / {fx}")
            line(f"={ccy} {amount(rest)}")
            line("4) 每單位面額到期贖回價值計算方式為:")
            line(f"= ({ccy} {p} × {whole} / {fx}) + {ccy} {amount(rest)}")
            line(f"= {ccy} {amount(value)} (四捨五入至小數點後第二位)")
            loss = pnl(value, "5) 每單位面額結算收益計算方式為: 每單位面額到期贖回價值-")
            annualized("6) ", loss, formula=digital)

        if digital:
            value = (d * (100 + s.annual) / 100).quantize(CENT, ROUND_HALF_UP)
            line("情境分析一) 在最後評價日最差表現連結標的期末股價>=執行價")
            line(f"1) 最差表現連結標的（{ul.name}）期末股價為{ccy} {s.close(s.above)}")
            line(f"每單位面額 × (100% + {bonus})")
            line(f"= {ccy} {cash} × (100% + {bonus})")
            line(f"= {ccy} {amount(value)}")
            annualized("3) ", pnl(value, "2) 每單位投資損益: 每單位面額到期贖回金額 - "))
            delivery("情境分析二) 在最後評價日最差表現連結標的期末股價<執行價", s.close(s.worse))
            delivery("情境分析三) 在最後評價日最差表現連結標的期末股價為0元(<執行價)", s.close(Decimal(0)))
            return
        ratio = "(最差表現連結標的期末股價 /最差表現連結標的期初股價-1)"
        head = "籃子中最差表現連結標的期末股價高於執行價，且" + ratio
        enhanced(f"情境分析一) {head}大於紅利配息率", s.close(s.above))
        enhanced(f"情境分析二) {head}小於紅利配息率", s.close(s.below))
        delivery("情境分析三) 籃子中最差表現連結標的期末股價低於執行價(以下情境分析之零股數僅供示意)", s.close(s.worse))

    w.section = "cover"
    short = s.name.replace("（以下簡稱「本商品」）", "")
    line(short, 60)
    line(s.en, 60)
    line("中文產品說明書" if ben and s.word else "中文產品說明書(最終版)")
    line(f"商品代號/商品中文名稱：{s.product_code}/{s.name}")
    line(f"商品英文名稱：{s.en}")
    line("商品種類：股權連結商品")
    line(f"計價幣別：{ccy}")
    line("發行機構：" + STANDARD["issuer"]["hsbc"]["issuer_name"])
    line("電話：+852-0000-0000")
    line("[受託或銷售機構]審查通過之日期：2026年6月11日")
    line(f"(參考性審閱版)內容，刊印日期：{zh_date_tight(s.trade_date)}")
    line(f"(最終版)刊印日期：{zh_date_tight(s.trade_date)}")
    line("受託或銷售機構之名稱、電話及地址：" + STANDARD["distributor"]["name"])
    line("電話：+886-2-5556-1313")
    line(STANDARD["distributor"]["address"])
    line("公會審查")
    warning = STANDARD["issuer"]["hsbc"]["fixed_warning"]
    line(warning)
    chapter("一", "商品基本資料")
    for k in range(1, 32):
        article(
            k,
            {
                1: "商品名稱：" + short + s.en,
                2: warning,
                3: f"發行機構名稱及長期債務信用評等：本商品發行機構為{ISSUER_ZH}，截至本商品說明書印製日期前，"
                f"其長期債務信用評等為[{ratings_text(s.ratings)})。]為免疑義，此為發行機構之長期債務信用評等，"
                "並不代表本商品之評等。",
                5: f"計價幣別：{ccy}" + (CNH_NOTE if ccy == "人民幣" else ""),
                6: f"每單位面額：{ccy}{denom}元",
                7: f"最低交易金額：{ccy}{denom}元",
                8: "計價貨幣本金保本率：本商品不保本。",
                9: "投資本金達成保本之各項條件：不適用，因本商品不保本",
                10: "發行價格：100%",
                11: "主要給付項目",
                12: "連結標的資產",
                13: "連結標的之相關說明或評等資料：",
                15: "本商品年期",
                18: "情境分析",
                27: "ISIN：XS1999900001",
                **(
                    {
                        11: "主要給付項目及其計算方式：",
                        16: "配息資料及其計算公式：本商品不適用",
                        17: "到期贖回計算公式，包含最低保證配息率及參與率：請見上述本第一章第11 項之說明",
                    }
                    if sb or ben
                    else {}
                ),
                **(
                    {
                        16: "配息資料及其計算公式：請見上述第一章第11 項之說明,本商品於到期時連同到期贖回金額及應付之紅利配息"
                        "一次給付，並無因配息致投資標的價值而相對降低之情事。"
                    }
                    if ben and s.digital
                    else {}
                ),
            }.get(k, "範本說明"),
        )
        if (sb or ben) and k in (11, 17, 18):  # Super Bull、BEN 的第 11、17、18 條（範本規格 §2）
            if k == 11:
                ben_article11() if ben else sb_article11()
            elif k == 17:
                sub(1)
                line("最低保證配息率：本商品不適用")
                sub(2)
                line("參與率：本商品不適用")
            else:
                ben_article18() if ben else sb_article18()
            continue
        if k == 11:
            sub(1)
            if s.express:  # 第 1 期提前到期配整期息，之後按天數攤（真實樣本寫法）
                line("第1 個計息期間:")
                line("若發生自動提前到期事件：每單位面額 x 固定配息率x 1")
                line("若無發生自動提前到期事件：每單位面額 x 固定配息率")
                line(f"第2 至{n} 個計息期間：每單位面額 x 變動配息率")
                line(f"變動配息率= {s.annual}% × 1/12 × (nt/Nt)")
            line(f"固定配息率={s.annual}%×1/12")
            line(f"配息期數={n}")
            line("Nt" if s.ko_obs == "D" else "付息日(如未在該計息期間自動提前到期)")
            for i, (start, end, pay) in enumerate(zip(s.starts, s.ends, s.payments, strict=True), 1):
                vals = [str(i)]
                if s.ko_obs == "D":
                    nt = "20" if i > max(first, 1) else "-"
                    vals += [zh_date(start) if start else "-", zh_date(end), zh_date(pay), nt]
                else:
                    vals += [zh_date(pay)]
                for t in vals:
                    line(t)
            line("註1：假設日期")
            sub(2)
            line(f"自動提前到期價格為期初股價×{pct(s.ko)}%")
            line("所有連結標的皆已成為鎖定股票" if s.memory else "評價等於或大於其自動提前到期價格")
            if s.ko_obs == "D":
                ko_start = s.starts[0] if s.express else s.ends[first - 1]
                line("自動提前到期決定日為自" + zh_date(ko_start) + "(含)起每個預定交易日")
            else:
                line("自動提前到期決定日")
                line("自動提前到期金額付款日")
                for i, j in enumerate(range(first - 1, n), 1):
                    for t in [str(i), zh_date(s.ends[j]), zh_date(s.payments[j])]:
                        line(t)
                line("投資人應注意")
            sub(3)
            line(f"執行價為期初股價×{pct(s.strike)}%")
            if s.ki == "none":
                line("期末股價〔等於或大於〕執行價；期末股價〔小於〕執行價")
            else:
                line(f"觸及不保本價格為期初股價×{pct(s.ki_pct)}%")
                line("觸及不保本事件決定日為" + ("最後評價日" if s.ki == "AM" else "每個預定交易日"))
        if k == 12:
            sub(1)
            table("art12.table")
            w.section = "art12"
            if ben and s.residue:  # Digital 樣本表後的範本殘留註記
                line("註3：[連結標的可能依發行條件被替換]")
            sub(2)
            line("標的說明")
        if k == 13:
            write_notes(w, s.underlyings, "paragraph", x=233.6, heading=None, width=30)
        if k == 15:
            vals = [
                f"本商品年期為{n}個月",
                f"發行日：{zh_date_tight(s.issue_date)}",
                "到期日目前表定為" + zh_date(s.payments[-1]),
                "日期說明",
                "最後評價日：" + zh_date(s.ends[-1]),
                f"交易日：{zh_date_tight(s.trade_date)}",
            ]
            for i, t in enumerate(vals, 1):
                sub(i)
                line(t)
        if k == 18:
            line(f"商品天期為{n}個月期，每單位面額為{ccy}{denom}元")
            line(f"固定配息率為{monthly}%，配息期數={n}，且假設")
            table("art18.table")
            w.section = "art18"
            line("*假設天期")
            line("發行價格為100%")
            fx = "1" if ccy == "美元" else "0.006"  # 標的以美元計價；匯率不是商品幣別的出處（Issue #179）
            line(f"期末匯率為1{ccy}=美元{fx}")
            line(f"每單位期初投資金額={ccy}{s.denom:,.2f}(={denom}×100%)")
            partial = (s.coupon * 5 / 20).quantize(CENT, ROUND_HALF_UP) if s.partial_coupon else None
            ul = s.underlyings[0]
            ko_cases = 2 if s.express else 1  # Express：情境一在第 1 期、情境二在第 2 期提前到期
            zh_numbers = "一二三四五"[: ko_cases + (2 if s.ki == "none" else 3)]
            for k, zh in enumerate(zh_numbers):
                line("情境分析" + zh + ")")
                if k < ko_cases and s.express:
                    # Express 的提前到期情境：第 1 期配整期息（×1），情境二第 2 期再按天數攤
                    fifth = (s.coupon * 5 / 20).quantize(CENT, ROUND_HALF_UP)
                    if k == 1:
                        line("本商品於第1個計息期間發放固定配息")
                        line(f"支付固定配息金額={ccy}{denom}×{monthly}%×1={ccy}{s.coupon:,.2f}")
                    line(f"自動提前到期給付金額為每單位面額×100%={ccy}{denom}×100%={ccy}{s.denom:,.2f}")
                    if k == 0:
                        line(f"第一個計息期間每單位面額配息金額={ccy}{denom}×{monthly}%×1={ccy}{s.coupon:,.2f}")
                    else:
                        line(f"第二個計息期間每單位面額配息金額={ccy}{denom}x{monthly}%x5/20={ccy}{fifth:,.2f}")
                    paid = s.coupon + (fifth if k == 1 else 0)
                    line(
                        f"損益={ccy}{s.denom:,.2f}+{ccy}{s.coupon:,.2f}"
                        + (f"+{ccy}{fifth:,.2f}" if k == 1 else "")
                        + f"-{ccy}{s.denom:,.2f}={ccy}{paid:,.2f}"
                    )
                    continue
                i = k - ko_cases + 1  # 對應 FCN 的情境序號（0 為提前到期情境）
                periods = first if i == 0 else n
                line(f"第1個至第{periods}個計息期間")
                if i > 0:
                    line(f"於{n}個月存續期間共{n}次配息")
                line(f"固定配息金額={ccy}{denom}×{monthly}%={ccy}{s.coupon:,.2f}")
                if i > 0:
                    line(f"{n}個計息期間配息金額共為{ccy}{s.coupons(n):,.2f}")
                extra = partial if i == 0 else None
                if extra is not None:
                    line(f"第{periods + 1}個計息期間配息金額={ccy}{denom}×{monthly}%×5/20={ccy}{extra:,.2f}")
                if i >= 2:
                    line(f"假設標的（{ul.name}），執行價{ccy}{price(ul.initial, s.strike):.4f}")
                    if s.ki != "none":
                        line(f"觸及不保本價格{ccy}{price(ul.initial, s.ki_pct):.4f}")
                if i == (2 if s.ki == "none" else 3):
                    line("交割股數：假設實物給付")
                    # 實物交割算式：說明在上一行、數字在下一行，股價跟著標的走，剩餘金額以承作幣別支付（Issue #179）
                    line(f"零股數則以{ccy}支付(「剩餘金額」)，計算方式為：")
                    line("最後評價日最差表現連結標的期末股價×零股數/期末匯率")
                    line(f"=美元83.30×0.0166/{fx}")
                    line(f"={ccy}230.45")
                    line("每單位面額到期贖回價值計算方式為：")
                    line(f"最差表現連結標的期末股價×交割股數/期末匯率+以{ccy}支")
                    line("付的剩餘金額")
                    line(f"=(美元83.30×36/{fx})+{ccy}230.45")
                    line(f"={ccy}5,000.45")
                else:
                    line(f"到期贖回金額為{ccy}{denom}×100%={ccy}{s.denom:,.2f}")
                    got = s.coupons(periods)
                    line(
                        f"損益={ccy}{s.denom:,.2f}+{ccy}{got:,.2f}"
                        + (f"+{ccy}{extra:,.2f}" if extra is not None else "")
                        + f"-{ccy}{s.denom:,.2f}={ccy}{got + (extra or 0):,.2f}"
                    )
                    if i > 0:
                        line(f"平均年化報酬率(以簡單平均年化報酬率之方式計算)為{s.annual:.2f}%")
    chapter("二", "相關機構事業概況")
    line("發行機構：(1)事業名稱：" + STANDARD["issuer"]["hsbc"]["issuer_name"].replace("（", "(").replace("）", ")"))
    line(f"(7)信用評等(長期債務信用評等)：{ratings_ch2(s.ratings)}。")
    line("受託或銷售機構：(a)事業名稱：" + STANDARD["distributor"]["name"] + "(b)設立日期：2000年11月20日")
    line(
        "(c)營業所在地：" + STANDARD["distributor"]["address"] + "(d)負責人姓名：" + STANDARD["distributor"]["chairman"]
    )
    line("結算機構")
    chapter("三", "商品風險揭露")
    line(warning)
    # 警語 (9) 重寫一次封面的兩個刊印日期（真實樣本第三章的寫法）
    line(
        f"(9)投資人應詳閱本產品說明書(參考性審閱版)內容，刊印日期：{zh_date_tight(s.trade_date)}，並應注意本商品之風險事項。"
    )
    line(f"本產品說明書(最終版)刊印日期：{zh_date_tight(s.trade_date)}。")
    chapter("四", "一般交易事項")
    line(f"商品開始受理申購日：{zh_date_tight(s.trade_date)}")
    line(f"商品申購結束受理日：{zh_date_tight(s.trade_date)}")
    line(f"最低申購金額：{ccy}{denom}元")
    line(f"最低加購金額：{ccy}{denom}元")
    line("開始受理贖回日期:發行日後的次一個預定交易日(並須為台北營業日)(「受理投資人提前贖回開始日」)")
    for t in ["申購費用", "提前贖回費用", "分銷費用"]:
        w.row([(60, t), (180, "0%~5%")])
    chapter("五", "特別記載事項")
    line("其他說明")
    for i, page in enumerate(w.doc):
        page.insert_text((270, 806), f"第{i + 1}頁，共{len(w.doc)}頁", fontname=FONT, fontsize=8)
    require_used(edits, used)
    return w.save(path)


# ---------------------------------------------------------------- 投資人須知（docs/checker/templates/hsbc-zh-iis.md）

IIS_PAGES = 4  # 審查標準 iis.pages


def build_iis_pdf(
    path: Path,
    s: Spec,
    *,
    edits: Sequence[Edit] = (),
    pages: int = IIS_PAGES,
    page_total: int | None = None,
    used: set[Edit] | None = None,
) -> Path:
    """仿 HSBC 中文投資人須知（4 頁）；值與 `s` 的說明書一致。`edits` 改字（製造錯誤）、`pages` 改頁數、
    `page_total` 改頁首「共 M 頁」的 M。`used` 給 `build_pdf` 用：換到的改字記在呼叫端的 set，由它檢查；省略時這裡自己檢查。"""
    w = PdfWriter(edits, kind="iis", used=used)
    dist, addr = STANDARD["distributor"]["name"], STANDARD["distributor"]["address"]
    warning = STANDARD["issuer"]["hsbc"]["fixed_warning"]
    issuer = STANDARD["issuer"]["hsbc"]["issuer_name"].split("（")[0]
    short = s.name.replace("（以下簡稱「本商品」）", "").replace("（", "(").replace("）", ")")
    names = [u.name for u in s.underlyings]
    tickers = [u.ticker for u in s.underlyings]

    def line(t: str, x: float = 60) -> None:
        t = w.edit(t)
        with w.verbatim():
            for k in range(0, len(t), 44):
                w.line(x, t[k : k + 44])

    def page1() -> None:
        line("中文投資人須知" if isinstance(s, BenSpec) and s.word else "中文投資人須知(最終版)", 214)
        line(short)
        line(s.en)
        line("本商品之投資風險警語：")
        line("一、" + warning)
        line(f"四、本商品雖經{dist}審查，並不代表證實申請事項。")
        line(f"五、本商品持有期間如有保證配息收益或保證保本率，係由{issuer}保證，而非由{dist}所保證。")
        line("七、本商品係依境外結構型商品管理規則規定，於臺灣境內受託投資、受託買賣或為投資型保單之投資標的。")
        line(f"十、投資人應詳閱本中文投資人須知內容，刊印日期：{zh_date(s.trade_date)}")
        line("相關機構")
        line(f"發行機構: {issuer}，電話: 852 0000 0000，地址：香港中環")
        line(f"受託或銷售機構：{dist}，電話：+886-2-5556-1313，地址：{addr}(營業活動所在地)")
        line("第一　商品簡介")
        line("3. " + warning)

    def page2() -> None:
        line(
            f"4. 發行機構名稱及其長期債務信用評等：商品發行機構為{ISSUER_ZH}，截至產品說明書印製日期前，"
            f"其長期債務信用評等為{ratings_text(s.ratings)})。為避免疑慮，此為發行機構之長期債務信用評等，並不代表本商品之評等"
        )
        line("5. 本商品之發行評等：不適用(因本商品受託或銷售對象以專業投資人及OSU 客戶為限)")
        line(f"6. 計價幣別：{s.currency_zh}")
        line(f"7. 每單位面額：{s.denom:,} {s.currency_zh}")
        line("8. 計價貨幣本金保本率：不保本。詳細解釋，請參考產品說明書第一章第八項")
        line("9. 投資本金達成保本率之各項條件：不適用，因本商品不保證保本")
        line(f"10. 連結標的資產: {', '.join(names)}")
        line(f"(彭博代碼: {', '.join(tickers)})。")
        event = "發行機構得提前贖回之條件" if isinstance(s, SbSpec | BenSpec) else "自動提前到期事件"
        line(f"11. 本商品年期: 如未發生{event}，且投資人持有本商品至到期日，為{s.tenor} 個月")
        line(f"12. 發行日：預定為{zh_date(s.issue_date)}")
        line(f"13. 到期日：如未發生提前贖回之條件，目前表定為{zh_date(s.payments[-1])}。")
        line("14. 開始受理贖回日期：發行日後的次一個預定交易日(並須為台北營業日)")
        if isinstance(s, SbSpec | BenSpec):  # 沒有配息（投資人須知範本規格 §2）
            line("第二 收益分配事項：請見產品說明書第一章第11 項之說明。")

    def page3() -> None:
        line("第五　商品風險揭露")
        line("第六　商品相關費用")
        for t in ["費用項目", "申購費用", "申購價金的", "0%~5%", "提前贖回費用", "投資人提前贖", "回價金的", "0%~5%"]:
            line(t)

    def page4() -> None:
        for t in [
            "報酬無",
            "費用申購價金的",
            "0%~5%",
            "分銷費用(如屬發行機構或發行人給予受託或銷售機構之報酬)",
            "折讓無",
        ]:
            line(t)
        line("第七　相關機構之權利、義務及責任")
        line("第八　協助投資人權益之保護方式")
        line("3. 受託或銷售機構連絡方式：電話：+886-2-5556-1313")

    builders = [page1, page2, page3, page4]
    for k, build in enumerate(builders):
        if 0 < k < pages:
            w.new_page()
        w.section = f"p{k + 1}"
        build()
    for _ in range(pages - len(builders)):
        w.new_page()
        line("（續）")
    for i, page in enumerate(w.doc):
        page.insert_text((250, 30), f"第 {i + 1} 頁，共 {page_total or len(w.doc)} 頁", fontname=FONT, fontsize=8)
        page.insert_text((60, 45), "PUBLIC", fontname="helv", fontsize=8)
    if used is None:
        require_used(edits, w.used)
    return w.save(path)
