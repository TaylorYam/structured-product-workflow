"""CA 合成說明書（docs/checker/templates/ca-zh-product-description.md）；商品、標的與價格皆虛構。

只用 tests/checker/pdf_writer.py 排版：第 1 頁「中文產品說明書」、「● 標籤：值」清單（值跨行時接在下一行）、警語 1～12 與刊印日期；
章名「第一章、商品基本資料」～「第七章、股權連結商品的額外條款及條件」在左邊界，條號「N.」x 36。每頁頁底「第N 頁，共M頁」。
價格表（第 9 項 (2)、第 16 項情境）表頭一格拆成三行（欄名、「(期初訂價之」、「X% )」），每格置中於欄中心，欄序 K → KI → KO；
價格後接標的報價幣別字。改字製造錯誤用 `edits`（tests/checker/pdf_writer.py 的 `Edit`），段落代號：`ts.cover`（第 1–2 頁）、
`ts.warn`（封面警語）、第一章各條 `ts.art{n}`（配息表 `ts.art9.coupon`、KO 觀察日表 `ts.art9.ko`、價格表 `ts.art9.table`、
第 10 項代碼表 `ts.art10.table`、情境價格表 `ts.art16.table`）、其他各章 `ts.ch二`～`ts.ch七`。
投資人須知（docs/checker/templates/ca-zh-iis.md）：`build_pdf` 旁邊另寫一份 `_IIS.pdf`（`iis=False` 時不寫），`edits` 兩份都套用；
段落代號 `iis.cover`、`iis.warn`、`iis.org`（一）、`iis.summary`（二、1.）、`iis.coupon`（二、2.）、`iis.fees`（四）、`iis.rest`。
真實投資人須知日期的「年」是 CJK 相容字 U+F98E；合成用的字型畫不出這個字（擷取時整行後面會掉），改由
tests/checker/test_iis_ca.py 直接以擷取後的行測正規化。
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import fitz

from fcn_checker.parsers.super_bull import add_months
from harness import CONFIG, STANDARD, check_all, iis_path
from pdf_writer import FONT, Edit, PdfWriter, require_used
from reference_synth import (
    FITCH,
    MOODYS,
    NO_RATINGS,
    SP,
    UL,
    ProductSpec,
    Ratings,
    build_reference_sheet,
    price,
    reference_row,
    weekday_on_or_after,
    write_notes,
)

NAME = STANDARD["issuer"]["ca"]["product_name"]
SB_NAME = STANDARD["issuer"]["ca"]["product_names"]["Super Bull"]
SB_PRODUCT = "Super Bull"
BEN_PRODUCT = "BEN"
BEN_NAME = STANDARD["issuer"]["ca"]["product_names"][BEN_PRODUCT]
WARNING = STANDARD["issuer"]["ca"]["fixed_warning"]
WARNING_CH1 = STANDARD["issuer"]["ca"]["fixed_warning_variants"][0]
ISSUER = STANDARD["issuer"]["ca"]["issuer_name"]
PRODUCT_TYPE = STANDARD["issuer"]["ca"]["product_type"]
DIST = STANDARD["distributor"]
ISIN = "XS1999900086"
APPROVAL = dt.date(2026, 6, 11)
Q4 = Decimal("0.0001")
Q2 = Decimal("0.01")
TICKERS = {
    "US": ("ZCA UW", "ZCB UN", "ZCC UW", "ZCD UN", "ZCE UW"),
    "JT": ("7201 JT", "7202 JT", "7203 JT", "7204 JT", "7205 JT"),
}
EXCHANGE = {"UN": "紐約證券交易所", "UW": "那斯達克交易所", "JT": "東京證券交易所"}
QUOTE = {"US": "美元", "JT": "日圓"}  # 標的報價幣別
PRICING = {"close": "收盤價", "open": "開盤價", "vwap": "VWAP"}  # 參考條件表「期初定價」
CN = ("一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二")


def ratings_text(r: Ratings) -> str:
    """評等（#241）：依穆迪、標準普爾、惠譽，`/` 分隔（`穆迪A1/標準普爾A+/惠譽AA-`）。"""
    return "/".join(f"{a}{v}" for a, v in r.stated(MOODYS, SP, FITCH))


def underlyings(count: int, market: str) -> tuple[UL, ...]:
    """CA 合成文件的標的：美股期初價 100n + 0.45，日股 1000n + 500（說明書一律 4 位小數）。"""
    out = []
    for i in range(1, count + 1):
        ticker = TICKERS[market][i - 1]
        initial = Decimal(1000 * i + 500) if market == "JT" else Decimal(100 * i) + Decimal("0.45")
        out.append(UL(f"虛構控股{CN[i - 1]}股份有限公司", EXCHANGE[ticker.split()[1]], ticker, initial))
    return tuple(out)


@dataclass
class Spec(ProductSpec):
    """CA 合成說明書的規格：商品規格加上 CA 專屬的文件選項。預設值下說明書與參考條件表列完全一致。

    標的由 `count`、`market` 產生、最終比價日與到期日由配息表推得：`underlyings`、`final_date`、`maturity_date` 不能直接給。
    """

    issuer: str = "CA"
    product_code: str = "086199990001"
    ko_obs: str = "D"  # D 每日（配息表有配息期結束日）／P 每期（配息表＋自動提前出場觀察日表）
    ki: str = "none"  # none／AM／D
    tenor: int = 6
    first_callable: int = 1  # 0：沒有 Non-Call（FCN_Express，自發行日次一預定交易日起觀察，workflow #305）
    memory: bool = True
    annual: Decimal = Decimal("9.00")
    strike: Decimal = Decimal("70.00")
    ko: Decimal = Decimal("100.00")
    ki_pct: Decimal = Decimal("60.00")
    final_date: dt.date = field(init=False)
    maturity_date: dt.date = field(init=False)
    underlyings: tuple[UL, ...] = field(init=False)
    count: int = 3  # 標的數
    market: str = "US"  # 標的市場：US 美股、JT 日股
    pricing: str = "close"  # 參考條件表期初定價（說明書沒寫）
    approval: dt.date = APPROVAL
    iis_pages: int = 4
    wrap_initial: bool = False  # 說明書第 9 項、第 16 項價格表第 1 檔期初訂價的幣別字換到下一行（真實樣本，Issue #278）
    wrap_currency: bool = False  # 說明書價格表每格的幣別字都換到下一行（`1,234.0000 日` / `圓`，範本規格 §5）
    ratings: Ratings = NO_RATINGS  # 發行機構評等：預設文件寫發行機構沒有評等、評等表也沒有；給值時說明書第二章發行機構那段改寫評等（#241）
    guarantor_ratings: Ratings = Ratings()  # 保證機構評等（#241）
    iis_ticker_with_exchange: bool = False  # 投資人須知標的表的彭博代碼和交易所擠在同一行（真實樣本，workflow #312）

    def __post_init__(self) -> None:
        self.underlyings = underlyings(self.count, self.market)
        self.final_date = self.ends[-1]
        self.maturity_date = self.payments[-1]

    @property
    def ends(self) -> list[dt.date]:
        """各期配息期結束日（= 每期自動提前出場觀察日）：每月 9 日，遇週末順延。"""
        return [weekday_on_or_after(dt.date(2030 + j // 12, j % 12 + 1, 9)) for j in range(1, self.tenor + 1)]

    @property
    def payments(self) -> list[dt.date]:
        return [weekday_on_or_after(d + dt.timedelta(days=2)) for d in self.ends]

    @property
    def monthly(self) -> Decimal:
        return (self.annual / 12).quantize(Q4, ROUND_HALF_UP)

    @property
    def name_zh(self) -> str:
        return NAME["zh"].format(tenor=self.tenor, ccy_zh=self.currency_zh)

    @property
    def name_en(self) -> str:
        return NAME["en"].format(tenor=self.tenor, ccy=self.ccy, express_en="Express " if self.express else "")

    @property
    def express(self) -> bool:
        """沒有 Non-Call（FCN_Express）。"""
        return self.first_callable == 0

    @property
    def ko_daily(self) -> str:
        """第 9 項 (b)、投資人須知二、2. 的 KO 每日觀察句。"""
        start = "發行日(不含)" if self.express else f"第{self.first_callable} 個配息期結束日 (含)"
        return f"自動提前出場觀察日：自{start}至期末訂價日(含)的每一個預定交易日。"

    @staticmethod
    def date(d: dt.date) -> str:
        return f"{d.year} 年{d.month:02d} 月{d.day:02d} 日"

    def money(self, x: int | Decimal) -> str:
        return f"{Decimal(x):,.0f} {self.currency_zh}"

    @property
    def quote(self) -> str:
        return QUOTE[self.market]

    @property
    def print_date(self) -> dt.date:
        return self.trade_date + dt.timedelta(days=1)


@dataclass
class SbSpec(Spec):
    """CA Super Bull 合成規格（docs/checker/templates/ca-zh-super-bull.md、ca-zh-super-bull-iis.md）：沒有配息，`annual` 是
    出場紅利 x%（不年化，同參考條件表 `Coupon p.a. (%)`）；`months` 是觀察日表各觀察日距發行日的月數，觀察日 = 發行日 +
    m 個月再加 `shift` 個日曆天（遇週末順延），出場日 = 觀察日後 2 天（遇週末順延），Non-Call = 第一個 m。預設照現行版
    樣本：非記憶式、無 KI、KO% 80 ≠ K% 70、美元、3 檔。"""

    months: tuple[int, ...] = (3,)
    annual: Decimal = Decimal("5.00")
    ko: Decimal = Decimal("80.00")
    memory: bool = False
    ko_obs: str = "P"
    first_callable: int = field(init=False)
    shift: int = 0
    note: bool = False  # 觀察日表下多一行「註:自動提前出場價以期初訂價的百分比表示。」
    row_bonus: tuple[Decimal, ...] = ()  # 觀察日表各列的出場紅利（預設每列都是 `annual`；給值製造各列不同）
    fx: str = ""  # 第 16 項情境價格表後的匯率行（例 `美元/日圓=150.00`；標的報價幣別和計價幣別不同的型態）

    def __post_init__(self) -> None:
        self.first_callable = self.months[0]
        super().__post_init__()

    @property
    def ends(self) -> list[dt.date]:
        """各觀察日，最後是期末訂價日（發行日 + 天期）。"""
        shift = dt.timedelta(days=self.shift)
        return [
            *(weekday_on_or_after(add_months(self.issue_date, m) + shift) for m in self.months),
            weekday_on_or_after(add_months(self.issue_date, self.tenor)),
        ]

    @property
    def name_zh(self) -> str:
        return SB_NAME["zh"].format(tenor=self.tenor, ccy_zh=self.currency_zh)

    @property
    def name_en(self) -> str:
        return SB_NAME["en"].format(tenor=self.tenor, ccy=self.ccy)

    @property
    def worst(self) -> str:
        """條文的「最差表現之連結標的」；單一標的寫「連結標的」。"""
        return "最差表現之連結標的" if self.count > 1 else "連結標的"

    @property
    def formula(self) -> str:
        """到期公式 (ii) 的「最差表現連結標的期末訂價/最差表現連結標的執行價」。"""
        w = "最差表現連結標的" if self.count > 1 else "連結標的"
        return f"[100%+100%* ({w}期末訂價/{w}執行價-100%)]"

    @property
    def event(self) -> str:
        who = "所有連結標的" if self.count > 1 else "連結標的"
        return (
            f"自動提前出場事件：{who}於預定自動提前出場觀察日之收盤價高於或等於自動提前出場價，則發生自動提前出場事件。"
        )


@dataclass
class BenSpec(Spec):
    """CA BEN 合成規格（docs/checker/templates/ca-zh-ben.md、ca-zh-ben-iis.md）：現行版 Bonus；沒有配息、沒有 KO、無 KI，`annual`
    是紅利率（不年化，同參考條件表 `Coupon p.a. (%)`）、`participation` 是參與率；Non-Call = 天期，參考條件表 KO% 照填 K%
    （說明書沒有 KO，不核對）。預設照現行版樣本：2 檔、K% 100、美元、3 個月。情境以第 1 檔為最差標的：情境一期末價 =
    期初 × `above`%（漲幅大於紅利）、情境二 × `below`%（漲幅小於紅利）、情境三 × `worse`%。`ko_column` 在價格表多 KO 價欄
    （KO 價欄版）；`fx` 是第 16 項情境價格表後的匯率行。"""

    tenor: int = 3
    count: int = 2
    annual: Decimal = Decimal("20.00")
    participation: Decimal = Decimal("100.00")
    strike: Decimal = Decimal("100.00")
    ko: Decimal = Decimal("100.00")
    memory: bool = False
    ko_obs: str = "P"
    first_callable: int = field(init=False)
    above: Decimal = Decimal("150")
    below: Decimal = Decimal("110")
    worse: Decimal = Decimal("40")
    ko_column: bool = False
    fx: str = ""

    def __post_init__(self) -> None:
        self.first_callable = self.tenor
        super().__post_init__()

    @property
    def ends(self) -> list[dt.date]:
        """只有期末訂價日（發行日 + 天期）。"""
        return [weekday_on_or_after(add_months(self.issue_date, self.tenor))]

    @property
    def name_zh(self) -> str:
        return BEN_NAME["zh"].format(tenor=self.tenor, ccy_zh=self.currency_zh)

    @property
    def name_en(self) -> str:
        return BEN_NAME["en"].format(tenor=self.tenor, ccy=self.ccy)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[\x21-\x7e]+|\s|.", text)


def _width(text: str, size: float) -> float:
    return sum(fitz.get_text_length(c, fontname="helv", fontsize=size) if ord(c) < 256 else size for c in text)


class _Doc:
    """逐行排版；文字依目前段落代號（`section`）套用改字，超過寬度時換行（英數字不拆開）。"""

    def __init__(self, edits: Sequence[Edit], used: set[Edit], kind: str = "ts", width: float = 560):
        self.w = PdfWriter(edits, kind, used)
        self.section = "cover"
        self.width = width

    @property
    def section(self) -> str:
        return self.w.section

    @section.setter
    def section(self, value: str) -> None:
        self.w.section = value

    def text(self, x: float, text: str, cont: float | None = None, gap: float = 13, size: float = 10) -> None:
        text = self.w.edit(text)
        if not text:
            return
        nxt = cont if cont is not None else x
        cur, used, here = "", 0.0, x
        with self.w.verbatim():
            for tok in _tokens(text):
                tw = _width(tok, size)
                if used + tw > self.width - here and cur.strip():
                    self.w.line(here, cur.strip(), size=size, gap=gap)
                    cur, used, here = "", 0.0, nxt
                    if tok.isspace():
                        continue
                cur += tok
                used += tw
            self.w.line(here, cur.strip(), size=size, gap=gap)

    def chapter(self, zh: str, name: str) -> None:
        self.w.new_page()
        self.section = f"ch{zh}"
        self.w.line(36, f"第{zh}章、{name}", gap=18)

    def article(self, n: int, title: str) -> None:
        if self.section == "cover" or self.section.startswith("art") or self.section == "ch一":
            self.section = f"art{n}"
        self.text(36, f"{n}. {title}", 54)

    def centered_row(self, cells: Sequence[tuple[float, str]], gap: float, size: float = 8) -> None:
        """一列表格，每格置中於欄中心 x。"""
        self.w.row([(cx - _width(t, size) / 2, t) for cx, t in cells], gap=gap, size=size)

    def furnish(self, label: str) -> fitz.Document:
        """每頁頁底頁碼（真實文件的頁碼，擷取時排除）。"""
        doc = self.w.doc
        for p, page in enumerate(doc):
            page.insert_text((272, 818), label.format(n=p + 1, m=len(doc)), fontname=FONT, fontsize=9)
        return doc


def _warnings(s: Spec, doc: str = "本中文產品說明書") -> list[str]:
    """封面警語 1～12 與第三章 6.(1)～(12)：1 為固定警語；6、7、11 用 CA 的「受託投資」寫法（允許片語）。"""
    return [
        WARNING,
        "本商品係複雜的金融商品，必須經過符合資格的人員解說後再進行投資。",
        "本商品並非存款，投資不受存款保險之保障，最大損失為全部本金及利息。",
        f"本商品雖經{DIST['name']}審查，並不代表證實申請事項或保證該境外結構型商品之價值，{DIST['name']}不負本商品投資盈虧之責。"
        f"{DIST['name']}依法不得承諾擔保投資本金或最低收益率。",
        f"本商品持有期間如有保證配息收益，係由虛構銀行(保證機構)保證，而非由{DIST['name']}所保證。{ISSUER}(發行機構)與保證機構間有關係人之情事。",
        f"{doc}之內容如有虛偽或隱匿之情事者，除商品風險程度、受託對象、受託或銷售機構收取之費用及由受託或銷售機構另行訂定者，"
        f"係由受託或銷售機構(即{DIST['name']})負責外，其餘內容應由虛構分公司及其負責人；以及發行機構（依境外結構型商品管理規則"
        "受託投資或銷售時）依法負責。",
        "本商品係依境外結構型商品管理規則於中華民國境內受託投資，受託買賣或為投資型保單之投資標的。",
        "本商品不得於英國及歐盟境內向零售投資人募集。",
        "本商品依中華民國境外結構型商品管理規則之規定提供予OSU 客戶辦理受託投資或受託買賣。",
        "受託或銷售機構應提供專業投資人或OSU 客戶相關契約審閱期間。",
        "對於依OSU 辦理受託買賣相關規定為OSU 客戶受託投資之特別風險警語：本商品於OSU 僅限於對中華民國境外客戶為推介及交易對象。",
        "專業投資人亦不適用「金融消費者保護法」之金融消費爭議處理機制。",
    ]


# ---------------------------------------------------------------- 說明書


def _cover(d: _Doc, s: Spec) -> None:
    w = d.w
    w.line(257, "中文產品說明書", size=14, gap=22)
    for t in (
        f"● TDCC 商品代號：{s.product_code}",
        f"● 商品中文名稱：{s.name_zh} (ISIN：{ISIN} )(下稱「商品」或「本商品」)",
        f"● 商品英文名稱：{s.name_en} (ISIN：{ISIN})",
        f"● 商品種類：{PRODUCT_TYPE}",
        "● 發行機構註冊地：法國",
        "● 商品註冊地：專業投資人及OSU 客戶不適用",
        f"● 計價幣別：{s.currency_zh}({s.ccy})",
        f"● 發行機構名稱及註冊辦公地點{ISSUER}，註冊辦公地點：1 Fictional Avenue, Paris.",
        "● 總代理人之名稱、電話及地址：虛構銀行臺北分公司，電話：02-0000-0000，地址：台北市虛構路1 號。",
        "● 保證機構之名稱及地址：虛構銀行(Fictional Bank)，地址：1 Fictional Avenue, Paris",
        f"● 受託或銷售機構之名稱、電話及地址：{DIST['name']}，電話：{DIST['phone']}，地址：{DIST['address']}",
        "● 中華民國信託商業同業公會審查通過之日期及文號：專業投資人及OSU 客戶不適用",
        f"●受託或銷售機構審查通過之日期：{s.date(s.approval)}",
    ):
        d.text(36, t)
    d.text(38, "•  本商品之投資風險警語：")
    d.section = "warn"
    for k, t in enumerate(_warnings(s), 1):
        d.text(36, f"{k}、{t}")
    d.section = "cover"
    d.text(36, "● 投資人應詳閱本中文產品說明書內容，並應注意本商品之風險事項。(請參見第19~22 頁)")
    d.text(36, f"● 中文產品說明書刊印日期：{s.date(s.print_date)}")


HEAD_X = {"name": 130, "initial": 250, "strike": 340, "ki": 420, "ko": 500}  # 價格欄寬要夠，相鄰兩格不能擠成一行


def _price_table(d: _Doc, s: Spec, *, index: bool) -> None:
    """價格表：表頭三行（欄名、「(期初訂價之」、「X% )」），欄序 K → KI → KO；每檔一列，價格後接報價幣別字。"""
    cols = [("strike", "執行價", s.strike)]
    if s.ki != "none":
        cols.append(("ki", "下限價", s.ki_pct))
    if not isinstance(s, BenSpec) or s.ko_column:
        cols.append(("ko", "自動提前出場價", s.ko))
    x = dict(HEAD_X)
    if s.ki == "none":
        x["ko"] = 430
    d.w.need(60 + 18 * s.count)
    d.centered_row([(x[c], t) for c, t, _ in cols], gap=13)
    d.centered_row(
        [(x["name"], "連結標的"), (x["initial"], "期初訂價"), *[(x[c], "(期初訂價之") for c, _, _ in cols]], gap=13
    )
    d.centered_row([(x[c], f"{p}% )") for c, _, p in cols], gap=16)
    for n, u in enumerate(s.underlyings, 1):
        prices = [u.initial] + [price(u.initial, p) for _, _, p in cols]
        cells = [
            (x["name"], u.name),
            *[
                (cx, f"{v:,.4f} {s.quote}")
                for cx, v in zip([x["initial"], *[x[c] for c, _, _ in cols]], prices, strict=True)
            ],
        ]
        if index:
            cells.insert(0, (60, str(n)))
        if n == 1 and s.wrap_initial:
            # 數字與幣別字分兩行：第 9 項 (2)（有列號）兩行對同列其他格垂直置中；第 16 項情境表數字高一行、幣別字和同列對齊
            up, down = (7.8, 7.8) if index else (15.6, 0.0)
            d.w.need(32)
            d.w.space(8 if index else 16)  # 真實樣本表頭與這一列之間較寬，換行的第一行不會碰到表頭
            cx, text = next(c for c in cells if c[0] == x["initial"])
            cells.remove((cx, text))
            number, quote = text.rsplit(" ", 1)
            d.w.put(cx - _width(number + " " + quote[0], 8) / 2, d.w.y - up, number + " " + quote[0], 8)
            d.w.put(cx - _width(quote[1:], 8) / 2, d.w.y + down, quote[1:], 8)
            d.centered_row(cells, gap=24 if index else 16)  # 置中換行的那一列較高（真實樣本列距 24）
            continue
        if s.wrap_currency:  # 每格價格的幣別字最後一字換到下一行，這一列兩行高
            d.w.need(26)
            tails = []
            for i, (cx, text) in enumerate(cells):
                if text.endswith(s.quote) and len(s.quote) > 1:
                    cells[i] = (cx, text[:-1])
                    tails.append((cx, text[-1]))
            d.centered_row(cells, gap=10)
            d.centered_row(tails, gap=16)
            continue
        d.centered_row(cells, gap=16)


def _article9(d: _Doc, s: Spec) -> None:
    d.text(36, "(1) 本商品主要給付項目為「自動提前出場贖回金額」或「到期贖回金額」及「配息金額」。")
    d.text(36, "計算方式：")
    d.text(36, "(a) 「配息金額」：於各配息期(i)：每單位（張）商品面額x「配息率」；")
    if s.ko_obs == "D" and s.express:  # 第一期出場配整期息，之後按天數攤（真實樣本的換行位置）
        d.text(36, "若於第一個配息期結束日前發生自動提前出場事件：每單位（張）商品面額x「配息率」x 乘數，乘")
        d.text(36, "數：1；")
        d.text(
            36,
            "若於第一個配息期結束日及其後之自動提前出場觀察日發生自動提前出場事件，配息金額：每單位（張）商品面額x「配息率」"
            "x (n/N) ，該配息金額僅計算至該自動提前出場觀察日。",
        )
    elif s.ko_obs == "D":
        d.text(
            36,
            "若發生自動提前出場事件，配息金額：每單位（張）商品面額x「配息率」x (n/N) ，該配息金額僅計算至該自動提前出場觀察日。",
        )
    d.section = "art9.coupon"
    if s.ko_obs == "D":
        d.w.row([(60, "配息期i"), (130, "配息期結束日"), (260, "配息日"), (390, "配息率")], gap=14)
        for j in range(s.tenor):
            d.w.row(
                [(70, str(j + 1)), (130, s.date(s.ends[j])), (260, s.date(s.payments[j])), (390, f"{s.monthly}%")],
                gap=13,
            )
    else:
        d.w.row([(70, "i"), (130, "配息日"), (260, "配息率")], gap=14)
        for j in range(s.tenor):
            d.w.row([(70, str(j + 1)), (130, s.date(s.payments[j])), (260, f"{s.monthly}%")], gap=13)
    d.section = "art9"
    d.text(36, f"投資人請注意：配息後本商品市場價值將相對降低（例如：月配息率為{s.monthly}%）。")
    d.text(36, "(b) 「自動提前出場贖回金額」：自動提前出場觀察日時持有之商品單位（張）數x 每單位（張）商品面額x100%")
    if s.memory:
        event = (
            "自動提前出場事件：所有連結標的於預定自動提前出場觀察日或任何先前的自動提前出場觀察日之收盤價高於或等於"
            "自動提前出場價，則發生自動提前出場事件。為避免疑義，所有連結標的無須於同一自動提前出場觀察日符合上開條件。"
        )
    else:
        who = "連結標的" if s.count == 1 else "所有連結標的"
        event = (
            f"自動提前出場事件：{who}於預定自動提前出場觀察日之收盤價高於或等於自動提前出場價，則發生自動提前出場事件。"
        )
    d.text(36, event)
    if s.ko_obs == "D":
        d.text(36, "自動提前出場日：於發生自動提前出場事件時，該自動提前出場觀察日後第二個營業日。")
        d.text(36, s.ko_daily)
    else:
        d.section = "art9.ko"
        d.w.row([(70, "i"), (130, "自動提前出場觀察日"), (300, "自動提前出場日")], gap=14)
        for t, j in enumerate(range(s.first_callable - 1, s.tenor), 1):
            d.w.row([(70, str(t)), (130, s.date(s.ends[j])), (300, s.date(s.payments[j]))], gap=13)
        d.section = "art9"
    d.text(
        36,
        "(c) 「到期贖回金額」：若商品並無提前贖回、買回或註銷，或未發生自動提前出場事件，本商品到期日將依下列方式贖回：",
    )
    if s.ki == "none":
        d.text(
            36,
            "(i) 若最差表現之連結標的期末訂價低於執行價：投資人將會收到發行機構交付最差表現之連結標的單位數，"
            f"及以{s.currency_zh}支付剩餘金額（如有）。",
        )
        d.text(36, "(ii) 若最差表現之連結標的期末訂價高於或等於執行價：每單位（張）商品面額x 100%。")
    else:
        d.text(36, "(i) 若未發生界限事件，則到期贖回金額為：每單位（張）商品面額x 100%。")
        d.text(
            36,
            f"(ii) 若發生界限事件：投資人將會收到發行機構交付最差表現之連結標的單位數，及以{s.currency_zh}支付剩餘金額（如有）。",
        )
    d.text(36, "(2) 連結標的：")
    d.section = "art9.table"
    _price_table(d, s, index=True)
    d.section = "art9"
    d.text(36, "投資人請注意：本商品之收益與投資最差表現之連結標的相關。")
    d.text(36, "期初訂價：連結標的於期初訂價日之價格，由計算代理機構決定。")
    d.text(36, "期末訂價：連結標的於期末訂價日之收盤價，由計算代理機構決定。")
    if s.ki == "AM":
        d.text(36, "界限事件：若於期末訂價日，依計算代理機構決定，連結標的之收盤價低於下限價。")
    elif s.ki == "D":
        d.text(
            36,
            "界限事件：若於界限事件觀察期間之任何預定交易日，依計算代理機構決定，至少有一個連結標的之收盤價低於下限價。",
        )
        d.text(36, "界限事件觀察期間：從期初訂價日(含)至期末訂價日(含) 。")


def _article9_sb(d: _Doc, s: SbSpec) -> None:
    """Super Bull 第 9 項（範本規格 §2、§4.1）：沒有配息、(a) 面額 x (100%+紅利)、四欄觀察日表、(b) 到期兩款、價格表。"""
    d.text(36, "(1) 本商品主要給付項目為「自動提前出場贖回金額」或「到期贖回金額」。本商品持有期間並無配息金額。")
    d.text(36, "計算方式：")
    d.text(
        36,
        "(a) 「自動提前出場贖回金額」：自動提前出場觀察日時持有之商品單位（張）數x 每單位（張）商品面額x(100%+自動提前出場"
        "紅利) 。其後無任何金額給付。",
    )
    d.text(36, s.event)
    d.section = "art9.ko"
    d.w.row(
        [(49, "自動提前出場觀察日"), (196, "自動提前出場價"), (332, "自動提前出場日"), (461, "自動提前出場紅利")],
        gap=14,
    )
    bonuses = s.row_bonus or (s.annual,) * len(s.months)
    for obs, exit_, bonus in zip(s.ends[:-1], s.payments[:-1], bonuses, strict=True):
        d.w.row([(54, s.date(obs)), (220, f"{s.ko}%"), (325, s.date(exit_)), (494, f"{bonus}%")], gap=13)
    if s.note:
        d.text(36, "註:自動提前出場價以期初訂價的百分比表示。")
    d.section = "art9"
    d.text(
        36,
        "(b) 「到期贖回金額」：若商品並無提前贖回、買回或註銷，或未發生自動提前出場事件，本商品到期日將依下列(i)或(ii)"
        "方式贖回：",
    )
    d.text(
        36,
        f"(i) 若{s.worst}期末訂價低於執行價：投資人將會收到發行機構交付{s.worst}單位數，及以計價幣別支付剩餘金額（如有）。",
    )
    d.text(
        36,
        f"(ii) 若{s.worst}期末訂價高於或等於執行價：投資人將會收到發行機構以計價幣別現金交付下列金額：到期時持有之商品"
        f"單位（張）數x 每單位（張）商品面額x {s.formula}。",
    )
    d.text(36, "(2) 連結標的：")
    d.section = "art9.table"
    _price_table(d, s, index=True)
    d.section = "art9"
    d.text(36, "投資人請注意：本商品之收益與投資最差表現之連結標的相關。")
    d.text(36, "期初訂價：連結標的於期初訂價日之價格，由計算代理機構決定。")
    d.text(36, "期末訂價：連結標的於期末訂價日之收盤價，由計算代理機構決定。")


BEN_FORMULA = (
    "每單位面額 x[100%+最大值(紅利率，參與率x(最差表現之連結標的期末訂價/最差表現之連結標的期初訂價)-100%))]。"
)


def _ben_maturity(d: _Doc, s: BenSpec, x: float) -> None:
    """BEN 到期（1）交股、（2）領回與 Bonus 公式（括號不對稱照樣本）。"""
    d.text(
        x,
        "（1） 若最差表現之連結標的期末訂價小於執行價，則投資人將會收到發行機構交付表現最差的連結標的股票之股數，"
        f"及以{s.currency_zh}支付剩餘金額（如有）;",
    )
    d.text(x, "（2） 若最差表現之連結標的期末訂價大於或等於執行價，則投資人可領回：")
    d.text(x, BEN_FORMULA)


def _article9_ben(d: _Doc, s: BenSpec) -> None:
    """BEN 第 9 項（範本規格 §2）：沒有配息、沒有 KO 的開頭句、到期（1）（2）與 Bonus 公式、參與率與紅利率、價格表只有執行價。"""
    d.text(36, "(1) 本商品主要給付項目為「到期贖回金額」，持有期間並無配息金額。")
    d.text(
        36,
        "本商品到期日給付視最差表現之連結標期末訂價而定，依每單位面額，發行機構在到期日將依下列（1）或（2）方式支付到期"
        "贖回金額：",
    )
    _ben_maturity(d, s, 36)
    d.text(36, f"參與率：{s.participation}%。")
    d.text(36, f"紅利率： {s.annual}%。")
    d.text(36, "(2) 連結標的：")
    d.section = "art9.table"
    _price_table(d, s, index=True)
    d.section = "art9"
    d.text(36, "投資人請注意：本商品之收益與投資最差表現之連結標的相關。")
    d.text(36, "期初訂價：連結標的於期初訂價日之價格，由計算代理機構決定。")
    d.text(36, "期末訂價：連結標的於期末訂價日之收盤價，由計算代理機構決定。")


def _article10(d: _Doc, s: Spec) -> None:
    d.text(36, "連結標的資產：")
    d.section = "art10.table"
    d.centered_row([(105, "連結標的"), (240, "彭博代碼"), (375, "交易所")], gap=16)
    for u in s.underlyings:
        d.centered_row([(105, u.name), (240, u.ticker), (375, u.exchange)], gap=16)
    d.section = "art10"
    d.text(36, "相對權重：不適用。")


def _article16(d: _Doc, s: Spec) -> None:
    for t in (
        "(1)投資收益計算方法：請參見上述第一章第9 項。",
        "(3)以情境分析解說最大可能獲利、損失及其他狀況之年化平均報酬率：",
        "基本假設",
        f"天期：{s.tenor} 個月",
        f"每單位面額：{s.money(s.denom)}",
        f"初始投資金額：{s.money(s.denom)}",
        f"配息率：{s.monthly}%(年化配息率為{s.annual}%)",
    ):
        d.text(36, t)
    d.section = "art16.table"
    _price_table(d, s, index=False)
    d.section = "art16"
    months = "1" if s.first_callable == 1 else f"1~{s.first_callable}"  # 沒有 Non-Call 時情境一只寫「配息金額」
    coupon = (Decimal(s.denom) * s.monthly / 100).quantize(Decimal("0.01"), ROUND_HALF_UP)
    denom, ccy = f"{s.denom:,}", s.currency_zh

    def coupons(months: str, n: int) -> None:  # 配息金額行與累積配息（Issue #319：配息率、面額、幣別是重複寫出的值）
        if n == 0:  # 沒有 Non-Call：第 1 個觀察日出場配整期息，沒有月數與累積配息
            d.text(36, f"• 配息金額 ＝每單位面額 × {s.monthly}%＝{denom} × {s.monthly}%＝{coupon:,} {ccy}")
            return
        d.text(36, f"• 第 {months} 個月配息金額 ＝每單位面額 × {s.monthly}%＝{denom} × {s.monthly}%＝{coupon:,} {ccy}")
        d.text(36, f"• 累積配息金額 ＝{coupon * n:,} {ccy}")

    d.text(36, "情境分析一：較佳情況")
    d.text(36, "假設於第1 個自動提前出場觀察日發生自動提前出場事件。")
    coupons(months, s.first_callable)
    d.text(36, f"• 自動提前出場贖回金額＝每單位面額 × 100% ＝{denom} × 100% ＝{denom} {ccy}")
    total = "• 總報酬率 ＝[({}+累積配息金額) / 總投資金額] - 100% ＝{}%（平均年化報酬率：" + f"{s.annual}%）"
    if s.express:  # 發行日後第一個觀察日出場：整期配息按 1 天年化（真實樣本 ×360），不是年利率
        d.text(36, f"• 總報酬率 ＝{s.monthly}%（平均年化報酬率：{s.monthly * 360}%）")
    else:
        d.text(36, total.format("自動提前出場贖回金額", "0.75"))
    d.text(36, "情境分析二：一般情況")
    d.text(36, "假設不曾發生任何自動提前出場事件，於到期日以現金結算收益。")
    coupons(f"1~{s.tenor}", s.tenor)
    d.text(36, f"• 到期贖回金額＝每單位面額 × 100% ＝{denom} × 100% ＝{denom} {ccy}")
    d.text(36, total.format("到期贖回金額", "4.50"))
    d.text(36, "情境分析三：較差情況")
    worst = s.underlyings[0]
    d.text(
        36,
        f"假設於期末訂價日表現最差的連結標的為{worst.name}，收盤價為50.00 {s.quote}，低於其執行價。"
        f"投資人將會收到最差表現之連結標的單位數，及以{ccy}支付剩餘金額（如有）。",
    )
    coupons(f"1~{s.tenor}", s.tenor)
    d.text(
        36,
        f"• 到期給付最差表現之連結標的單位數為：每單位面額/表現最差連結標的的執行價* = {denom} / 70.3150 = 142.217 股",
    )
    d.text(36, f"整數股數＝142 股，畸零股數＝0.217 股，剩餘金額＝50.00 x 0.217＝10.85 {s.quote}")
    d.text(36, "• 總報酬率 ＝-39.90% (平均年化報酬率:-79.81%)")  # 虧損情境不核對
    d.text(36, "*在必要時，依期末匯率轉換為計價貨幣。")
    d.text(36, "※ 請注意：情境分析之結果不保證未來績效，亦不代表連結標的未來表現。")


def _article16_sb(d: _Doc, s: SbSpec) -> None:
    """Super Bull 第 16 項 (3)（範本規格 §5）：基本假設多出場紅利、情境一出場、情境二到期漲 10%、情境三交股；數字依算式算出。"""
    ccy, quote, denom = s.currency_zh, s.quote, Decimal(s.denom)
    bonus = s.annual
    for t in (
        "(1)投資收益計算方法：請參見上述第一章第9 項。",
        "(3)以情境分析解說最大可能獲利、損失及其他狀況之年化平均報酬率：",
        "基本假設",
        f"天期：{s.tenor} 個月",
        f"每單位面額：{s.money(s.denom)}",
        f"初始投資金額：{s.money(s.denom)}",
        f"自動提前出場紅利：{bonus}%",
    ):
        d.text(36, t)
    d.section = "art16.table"
    _price_table(d, s, index=False)
    d.section = "art16"
    if s.fx:
        d.text(36, s.fx)
    a1 = (denom * (100 + bonus) / 100).quantize(Q2, ROUND_HALF_UP)
    ann1 = (bonus * 12 / s.months[0]).quantize(Q2, ROUND_HALF_UP)
    d.text(36, "情境分析一：較佳情況")
    d.text(36, "假設於第1 個自動提前出場觀察日發生自動提前出場事件。")
    d.text(36, "• 自動提前出場贖回金額為：每單位商品面額 × (100%+自動提前出場紅利)")
    d.text(36, f"= {denom:,.2f} {ccy} × (100% + {bonus}%)")
    d.text(36, f"= {a1:,.2f} {ccy}")
    d.text(
        36,
        f"• 總報酬率 ＝(自動提前出場贖回金額 / 總投資金額) - 1 ＝({a1:,.2f}  / {s.denom:,} ) - 1 ＝{bonus}%（平均年化"
        f"報酬率：{ann1}%）",
    )
    u = s.underlyings[0]
    k = price(u.initial, s.strike)
    p2 = (k * Decimal("1.1")).quantize(Q4, ROUND_HALF_UP)
    a2 = (denom * Decimal("1.1")).quantize(Q2, ROUND_HALF_UP)
    ann2 = (Decimal(10) * 12 / s.tenor).quantize(Q2, ROUND_HALF_UP)
    named = f"{s.worst}為{u.name}，" if s.count > 1 else "連結標的"
    d.text(36, "情境分析二：一般情況")
    d.text(36, f"假設不曾發生任何自動提前出場事件且於期末訂價日，{s.worst}收盤價高於執行價，於到期日以現金結算收益。")
    d.text(36, f"• 假設於期末訂價日{named}收盤價為{p2:,.4f} {quote}。")
    d.text(36, "• 到期給付金額為：")
    d.text(36, f"每單位面額x[100%+100%x({s.worst}期末訂價/{s.worst}執行價-100%)]。")
    d.text(
        36,
        f"＝{denom:,.2f} {ccy} × [100% + 100% x ( {p2:,.4f} {quote} / {k:,.4f} {quote} - 100%)]＝{denom:,.2f} {ccy} x "
        "(100% + 10%)",
    )
    d.text(36, f"= {a2:,.2f} {ccy}")
    d.text(36, "• 報酬率計算為：")
    d.text(
        36,
        f"＝(每單位面額到期給付金額 / 每單位面額初始投資金額) - 1 ＝({a2:,.2f}  / {s.denom:,} ) - 1 ＝10.00%（平均"
        f"年化報酬率：{ann2}%）",
    )
    p3 = (k * Decimal("0.6")).quantize(Q2, ROUND_HALF_UP)
    shares = (denom / k).quantize(Decimal("0.001"), ROUND_DOWN)
    whole = int(shares)
    frac = shares - whole
    rem = (frac * p3).quantize(Q2, ROUND_HALF_UP)
    total = (whole * p3 + rem) / denom * 100 - 100
    named = f"表現最差的連結標的為{u.name}，" if s.count > 1 else "連結標的"
    d.text(36, "情境分析三：較差情況")
    d.text(
        36,
        f"假設不曾發生任何自動提前出場事件且於期末訂價日，{s.worst}收盤價低於執行價，假設於期末訂價日{named}收盤價為"
        f"{p3:,.2f} {quote}，低於其執行價。投資人將會收到{s.worst}單位數，及以{ccy}支付剩餘金額（如有）。",
    )
    d.text(
        36,
        f"• 到期給付金額{s.worst}單位數為：每單位面額/{s.worst}的執行價* = {s.denom:,} / {k:,.4f} = {shares} 股",
    )
    d.text(36, f"整數股數＝{whole} 股，畸零股數＝{frac} 股，剩餘金額＝{p3:,.2f} x {frac}＝{rem:,.2f} {ccy}")
    d.text(36, "• 報酬率計算為：")
    d.text(36, "((整數股數×期末訂價*+剩餘金額)/每單位面額初始投資金額)-1")
    d.text(36, f"= (({whole} x {p3:,.2f} + {rem:,.2f}) / {s.denom:,}) - 1")
    annualized = (total * 12 / s.tenor).quantize(Q2, ROUND_HALF_UP)
    d.text(36, f"= {total.quantize(Q2, ROUND_HALF_UP)}% (平均年化報酬率:{annualized}%)")
    d.text(36, "*在必要時，依期末匯率轉換為計價貨幣。")
    d.text(36, "※ 請注意：情境分析之結果不保證未來績效，亦不代表連結標的未來表現。")


def _article16_ben(d: _Doc, s: BenSpec) -> None:
    """BEN 第 16 項 (3)（範本規格 §5）：基本假設多紅利率、情境一（漲幅 > 紅利）、二（漲幅 < 紅利）、三交股；數字依算式算出。
    段落代號：情境 `ts.art16.s1`～`ts.art16.s3`。"""
    ccy, quote, denom = s.currency_zh, s.quote, Decimal(s.denom)
    for t in (
        "(1)投資收益計算方法：請參見上述第一章第9 項「本商品主要給付項目及其計算方式」。",
        "(3)以情境分析解說最大可能獲利、損失及其他狀況之年化平均報酬率：",
        "基本假設",
        f"天期：{s.tenor} 個月",
        f"每單位面額：{s.money(s.denom)}",
        f"初始投資金額：{s.money(s.denom)}",
        f"紅利率：{s.annual}%",
    ):
        d.text(36, t)
    d.section = "art16.table"
    _price_table(d, s, index=False)
    d.section = "art16"
    if s.fx:
        d.text(36, s.fx)
    u = s.underlyings[0]
    cases = (("情境分析一：較佳情況", s.above, "高於"), ("情境分析二：一般情況", s.below, "略高於"))
    for n, (title, pct, case) in enumerate(cases, 1):
        p = (u.initial * pct / 100).quantize(Q4, ROUND_HALF_UP)
        gain = (s.participation * (p / u.initial - 1)).quantize(Q2, ROUND_HALF_UP)
        a = (denom * (100 + max(s.annual, gain)) / 100).quantize(Q2, ROUND_HALF_UP)
        r = (a / denom * 100 - 100).quantize(Q2, ROUND_HALF_UP)
        ann = (r * 12 / s.tenor).quantize(Q2, ROUND_HALF_UP)
        d.section = f"art16.s{n}"
        d.text(36, title)
        d.text(36, f"於期末訂價日，最差表現之連結標的收盤價{case}執行價")
        d.text(36, f"假設於期末訂價日，最差表現的連結標的為{u.name}，收盤價為{p:,.4f} {quote}，高於其執行價。")
        d.text(36, "• 到期給付金額為：")
        d.text(36, "每單位面額x[100%+最大值(紅利率，參與率x(最差表現之連結標的期末訂價/最差表現之連結標的期初訂價))]。")
        d.text(
            36,
            f"＝{s.denom:,} × [100%+最大值（{s.annual}%，{s.participation}% x（{p:,.4f} {quote} / {u.initial:,.4f} {quote}"
            " - 100%))]",
        )
        d.text(36, f"＝{s.denom:,} × [100%+最大值（{s.annual}%，{gain}%) = {a:,.2f} {ccy}")
        d.text(36, "• 報酬率計算為：")
        d.text(36, "(每單位面額到期給付金額/每單位面額初始投資金額)-100%")
        d.text(36, f"＝{a:,.2f} {ccy}  / {s.denom:,} )  - 100% ＝{r}%（平均年化報酬率：{ann}%）")
    k = price(u.initial, s.strike)
    p3 = (u.initial * s.worse / 100).quantize(Q2, ROUND_HALF_UP)
    shares = (denom / k).quantize(Decimal("0.001"), ROUND_DOWN)
    whole = int(shares)
    frac = shares - whole
    rem = (frac * p3).quantize(Q2, ROUND_HALF_UP)
    total = (whole * p3 + rem) / denom * 100 - 100
    annualized = (total * 12 / s.tenor).quantize(Q2, ROUND_HALF_UP)
    d.section = "art16.s3"
    d.text(36, "情境分析三：較差情況")
    d.text(36, "於期末訂價日，最差表現之連結標的收盤價低於執行價")
    d.text(
        36,
        f"假設於期末訂價日表現最差的連結標的為{u.name}，收盤價為{p3:,.2f} {quote}。投資人將會收到最差表現之連結標的"
        f"單位數，及以{ccy}支付剩餘金額（如有）。",
    )
    d.text(
        36,
        f"• 到期給付最差表現之連結標的單位數為：每單位面額/表現最差連結標的的執行價* = {s.denom:,} / {k:,.4f} = {shares} 股",
    )
    d.text(36, f"整數股數＝{whole} 股，畸零股數＝{frac} 股，剩餘金額＝{p3:,.2f} x {frac}＝{rem:,.2f} {ccy}")
    d.text(36, "• 報酬率計算為：")
    d.text(36, "((整數股數×期末訂價*+剩餘金額)/每單位面額初始投資金額)-100%")
    d.text(36, f"= (({whole} x {p3:,.2f} + {rem:,.2f} ) / {s.denom:,}) - 100%")
    d.text(36, f"= {total.quantize(Q2, ROUND_HALF_UP)}% (平均年化報酬率:{annualized}%)")
    d.text(36, "*在必要時，依期末匯率轉換為計價貨幣。")
    d.text(36, "※ 請注意：情境分析之結果不保證未來績效，亦不代表連結標的未來表現。")
    d.section = "art16"


ARTICLE_TITLES = {
    1: "商品名稱：",
    2: "商品風險程度：",
    3: "發行機構：",
    4: "商品之發行評等：專業投資人及OSU 客戶不適用。",
    5: "計價幣別：",
    6: "商品面額：",
    7: "計價貨幣本金保本率：不保本。本商品於到期的贖回價值可能低於投資本金。",
    8: "投資本金達成100%保本之各項條件：不適用。本商品為不保障本金之結構型商品。",
    9: "本商品主要給付項目及其計算方式：",
    10: "連結標的資產及其相對權重、與投資績效之關連情形：",
    11: "連結標的之相關說明：",
    13: "商品年期、發行日、交易日、到期日、付息日、贖回日及其他依商品性質而定之日期：",
    15: "到期贖回計算公式，包含保證配息率及參與率：",
    16: "投資收益計算方法，包含本金虧損之機率及以情境分析解說最大可能獲利、損失及其他狀況之年化平均報酬率：",
}  # 第一章各條標題；沒列的條是「第n項說明（不核對）」


def build_pdf(path: Path, s: Spec, *, edits: Sequence[Edit] = (), iis: bool = True) -> Path:
    """合成 CA 說明書 `<商品代號>_TS.pdf`；`edits` 依段落代號改字（兩份都套用）；`iis` 時旁邊另寫一份投資人須知。"""
    used: set[Edit] = set()
    if iis and path.stem.endswith("_TS"):
        build_iis_pdf(iis_path(path), s, edits=edits, used=used)
    d = _Doc(edits, used)
    _cover(d, s)

    d.chapter("一", "商品基本資料")
    guaranteed = "第一個月" if s.first_callable <= 1 else f"前{CN[s.first_callable - 1]}個月"
    for n in range(1, 26):
        title = ARTICLE_TITLES.get(n, f"第{n}項說明（不核對）")
        if n == 1:
            d.article(n, f"{title}{s.name_zh} (ISIN：{ISIN} ) 。")
        elif n == 2:
            d.article(n, title + WARNING_CH1)
        elif n == 3:
            d.article(n, f"{title}{ISSUER}。")
            d.text(
                36,
                "保證機構名稱及其長期債務信用評等：虛構銀行。截至本中文產品說明書刊印日期，其長期債務信用評等為"
                + ratings_text(s.guarantor_ratings),  # 真實樣本沒有句號，直接接第 4 項
            )
        elif n == 5:
            d.article(n, f"{title}{s.currency_zh}({s.ccy}) 。")
        elif n == 6:
            d.article(n, f"{title}每單位(張)商品面額為{s.money(s.denom)}({s.ccy}) 。")
            d.text(36, f"發行價格：每單位(張)商品面額的100%，即{s.money(s.denom)}({s.ccy})。")
        elif n == 9:
            d.article(n, title)
            (_article9_ben if isinstance(s, BenSpec) else _article9_sb if isinstance(s, SbSpec) else _article9)(d, s)
        elif n == 10:
            d.article(n, title)
            _article10(d, s)
        elif n == 11:
            d.article(n, title)
            d.w.row([(79, "連結標的"), (361, "簡介")], gap=16)
            write_notes(d.w, s.underlyings, "column", x=45, note_x=200, heading=None, width=34)
        elif n == 13:
            d.article(n, title)
            for t in (
                f"(1) 商品年期：{s.tenor} 個月。",
                f"(2) 期初訂價日：{s.date(s.trade_date)}。",
                f"(3) 發行日：{s.date(s.issue_date)}。",
                f"(4) 期末訂價日：{s.date(s.final_date)}；如當日非所有連結標的之預定交易日，則為次一預定交易日。",
                f"(5) 到期日：{s.date(s.maturity_date)}。",
                "(6) 配息日："
                + (
                    "本商品持有期間並無配息金額。"
                    if isinstance(s, (SbSpec, BenSpec))
                    else "詳見第9 項「本商品主要給付項目及其計算方式」。"
                ),
            ):
                d.text(36, t)
        elif n == 15:
            d.article(n, title)
            d.text(36, "(1)到期贖回計算公式：請參見上述第一章第9 項。")
            d.text(36, f"(3)提前贖回金額即為公平市值贖回金額，此公平市值贖回金額可能低於100%{s.currency_zh}投資本金。")
            if isinstance(s, BenSpec):
                d.text(36, "(4)最低保證配息率：不適用，本商品持有期間並無配息金額。")
                d.text(36, f"(5)參與率：{s.participation}%。")
            elif isinstance(s, SbSpec):
                d.text(36, "(4)最低保證配息率：不適用。本商品持有期間並無配息金額。")
                d.text(36, "(5)參與率：不適用。")
            else:
                d.text(36, f"(4)最低保證配息率：{guaranteed}配息 {s.monthly}%。")
        elif n == 16:
            d.article(n, title)
            (_article16_ben if isinstance(s, BenSpec) else _article16_sb if isinstance(s, SbSpec) else _article16)(d, s)
        else:
            d.article(n, title)

    d.chapter("二", "相關機構事業概況")
    d.text(36, "1. 發行機構：")
    for t in (
        f"(1) 事業名稱：{ISSUER}",
        "(2) 設立日期：西元2003 年12 月30 日",
        "(3) 營業所在地：法國 (1 Fictional Avenue)",
        "(7) 信用評等："
        + ("發行機構無單獨信用評等，詳見下述保證機構之信用評等" if s.ratings == NO_RATINGS else ratings_text(s.ratings))
        + "。",
        "(8) 經會計師查核簽證最近期財務報告中譯本之查核報告書：不核對。",
    ):
        d.text(36, t)
    d.text(36, "2. 保證機構：")
    d.text(36, "(1) 事業名稱：虛構銀行(Fictional Bank)")
    d.text(36, f"(7) 信用評等：{ratings_text(s.guarantor_ratings)}。")
    d.text(36, "(8) 保證條件、範圍：本商品由保證機構無條件及不可撤銷，依保證書之條款保證。")
    d.text(36, "3. 總代理人、計算代理機構、受託或銷售機構、保管機構及其他相關機構：")
    for t in (
        "(1) 總代理人：",
        "(a) 事業名稱：虛構銀行台北分公司",
        "(5) 受託或銷售機構：",
        f"(a) 事業名稱：{DIST['name']}",
        "(b) 設立日期：2000 年11 月20 日",
        f"(c) 營業所在地：{DIST['address']}",
        f"(d) 負責人姓名：{DIST['chairman']}",
        "(6) 保管機構：不適用。",
    ):
        d.text(36, t)
    d.text(
        36,
        "4. 交易架構說明：依OSU相關規定辦理受託投資或受託買賣時：本商品由發行機構發行，"
        f"投資人透過與{DIST['name']}之受託買賣關係投資本商品。",
    )
    d.text(
        36,
        "依境外結構型商品管理規則受託投資或受託銷售時：本商品由發行機構發行，總代理人為發行機構之總代理人。"
        f"投資人透過與{DIST['name']}之受託買賣關係投資本商品。",
    )
    d.text(36, "5. 利害關係人揭露：發行機構與保證機構有關係人之情事。")

    d.chapter("三", "商品風險揭露")
    d.text(36, "1. 基本風險資訊")
    ccy = s.currency_zh
    d.text(
        36,
        f"(3) 利率風險：本商品之市場價格將受{ccy}利率變動所影響，例如當{ccy}利率上漲時，本商品之市場價格有可能下跌；"
        f"當{ccy}利率下跌時，本商品之市場價格有可能上漲。",
    )
    d.text(36, "2. 個別商品風險資訊")
    d.text(36, f"3. 投資本商品因利率、匯率或其他指標之變動，有直接導致{ccy}投資本金損失之虞：投資人須承擔市場風險。")
    d.text(
        36, f"4. 投資本商品因發行機構之業務或財產狀況之變化，有直接導致{ccy}投資本金損失之虞：投資人須承擔信用風險。"
    )
    d.text(36, "5. 投資本商品因其他重要事項，有直接導致本金損失之虞：不核對。")
    d.text(36, "6. 本商品之投資風險警語：")
    for k, t in enumerate(_warnings(s), 1):
        if k == 7:
            t = "本商品係依境外結構型商品管理規則於中華民國境內受託投資或銷售或OSU 依據相關規定受託投資或受託買賣之投資標的。"
        d.text(36, f"({k}) {t}")

    d.chapter("四", "一般交易事項")
    d.text(36, "1. 本商品開始受理申購日期、結束受理申購日期：")
    for t in (
        f"◎開始受理申購日期：{s.date(s.trade_date)}",
        f"◎結束受理申購日期：{s.date(s.trade_date)}",
        "◎開始受理贖回日期：自發行日次日起。",
    ):
        d.text(36, t)
    d.text(36, "2. 投資人應負擔的各項費用及金額或計算基準之表列：")
    w = d.w
    w.need(40)
    top = w.y
    for x, dy, t in (
        (79, 7, "費用項目"),
        (186, 0, "費率（百分"),
        (204, 14, "比）"),
        (282, 7, "收取時點"),
        (395, 7, "收取方式"),
        (514, 7, "收取人"),
    ):
        w.put(x, top + dy, t, size=9)
    w.y = top + 30
    for cells in (
        [(41, "申購費用"), (176, "申購價金的"), (266, "投資人向受託")],
        [(176, "0%~5%"), (266, "或銷售機構申")],
        [(41, "提前贖回費用"), (176, "投資人提前贖"), (266, "投資人向受託")],
        [(176, "回價金的0%~5%")],
        [(41, "管理費用(信託管理費或"), (176, "無"), (266, "不適用")],
        [(41, "分銷費用(如屬發"), (131, "報酬"), (176, "無"), (266, "不適用")],
        [(131, "費用"), (176, "申購價金的0%~5%"), (266, "於本商品發行")],
        [(131, "折讓"), (176, "無"), (266, "不適用")],
        [(41, "保費費用"), (176, "不適用"), (266, "不適用")],
        [(41, "其他費用"), (176, "無"), (266, "不適用")],
    ):
        w.row(cells, gap=15, size=9)
    d.text(36, "附註：分銷費用係由投資人負擔，投資人同意其列為受託或銷售機構之報酬。")
    d.text(36, "分銷費用之支付並不影響到期給付。")
    m = s.money(s.denom)
    d.text(36, "3. 商品交易架構：請參見上述第二章第4 項。")
    d.text(36, "4. 商品最低申購金額及商品最低遞增金額：")
    d.text(36, f"最低申購金額： {m}(相當於一單位(張)商品)。")
    d.text(36, f"最低累加申購金額：商品面額{m}(相當於一單位(張)商品)。")
    d.text(36, "5. 商品申購價金之計算：申購單位(張)數 x 每單位(張)商品面額 x 發行價格 + 申購費用。")
    d.text(36, "6. 申購手續及資金給付方式：不核對。")
    d.text(36, "7. 發行不成立之情形：本商品有效申購金額至少達等值三十萬美元之總申購價值。")
    d.text(36, f"8. 最低贖回金額或單位(張)數：不得小於{m}(相當於一單位(張)商品)，以每次贖回計算)。")
    d.text(36, f"9. 贖回價金之計算：此公平市值贖回金額可能低於100%{s.currency_zh}投資本金。")
    for n in range(10, 18):
        d.text(36, f"{n}. 第{n}項說明（不核對）")

    d.chapter("五", "特別記載事項")
    d.text(36, "1. 保證機構之保證責任：不核對。")
    d.chapter("六", "通知")
    d.text(36, "通知以書面為之。")
    d.chapter("七", "股權連結商品的額外條款及條件")
    d.text(36, "1. 中斷日：不核對。")
    doc = d.furnish("第{n} 頁，共{m}頁")
    d.w.flush()
    doc.save(path)
    doc.close()
    require_used(edits, used)
    return path


# ---------------------------------------------------------------- 投資人須知


def build_iis_pdf(path: Path, s: Spec, *, edits: Sequence[Edit] = (), used: set[Edit] | None = None) -> Path:
    """CA 中文投資人須知（docs/checker/templates/ca-zh-iis.md）：與 `s` 的說明書、參考條件表列一致，預設 4 頁；`edits` 改字。"""
    d = _Doc(edits, set() if used is None else used, "iis", width=565)
    w = d.w

    dt_ = s.date

    w.line(257, "中文投資人須知", size=14, gap=20)
    for t in (
        f"{s.name_zh} (ISIN：{ISIN})(下稱「商品」或「本商品」)",
        f"{s.name_en} (ISIN：{ISIN})",
        f"(種類：{PRODUCT_TYPE})(TDCC 商品代號：{s.product_code})",
        "本商品之投資風險警語：",
    ):
        d.text(30, t)
    d.section = "warn"
    for k, t in enumerate(_warnings(s, "本中文投資人須知"), 1):
        if k == 7:
            t = "本商品係依境外結構型商品管理規則於中華民國境內受託或銷售或OSU 依據相關規定受託投資或受託買賣之投資標的。"
        elif k == 11:
            t = "對於依OSU 依據相關規定為OSU 客戶受託投資或受託買賣之特別風險警語：本商品僅限境外客戶。"
        d.text(30, f"{k}. {t}")
    d.section = "cover"
    d.text(30, f"中文投資人須知刊印日期：{dt_(s.print_date)}")
    d.section = "org"
    d.text(30, "一、 相關機構事業概況")
    for t in (
        f"1. 發行機構：{ISSUER} (營業所在地：1 Fictional Avenue, Paris)",
        "2. 保證機構：虛構銀行(Fictional Bank)(營業所在地：1 Fictional Avenue, Paris)",
        "3. 總代理人：虛構銀行台北分公司(營業所在地：台北市虛構路1 號)",
        f"4. 受託或銷售機構：{DIST['name']}，地址：{DIST['address']}",
    ):
        d.text(30, t)
    d.section = "summary"
    d.text(30, "二、 本商品事項")
    d.text(30, "1. 商品簡介：")
    for t in (
        "(1) 受託對象：專業投資人及OSU 客戶。",
        "(3) 本商品之風險程度：本商品風險等級為RR4，適合客戶投資風險承受度RR4(穩健型)等級(含)以上之專業投資人。",
        "(4) 保證機構之長期債務信用評等：虛構銀行，長期債務信用評等為" + ratings_text(s.guarantor_ratings) + "。",
        "(5) 本商品之發行評等：專業投資人及OSU 客戶不適用。",
        f"(6) 計價幣別：{s.currency_zh}({s.ccy})。",
        "(7) 計價貨幣本金保本率：不保本。本商品於到期的贖回價值可能低於投資本金。",
        "(8) 投資本金達成100%保本之各項條件：不適用。本商品為不保障本金之結構型商品。",
        "(9) 連結標的：",
    ):
        d.text(30, t)
    d.centered_row([(105, "連結標的"), (240, "彭博代碼"), (375, "交易所")], gap=14)
    for u in s.underlyings:
        if s.iis_ticker_with_exchange:
            d.centered_row([(105, u.name), (300, f"{u.ticker} {u.exchange}")], gap=14)
        else:
            d.centered_row([(105, u.name), (240, u.ticker), (375, u.exchange)], gap=14)
    for t in (
        f"(10) 商品年期：{s.tenor} 個月。",
        f"(11) 期初訂價日：{dt_(s.trade_date)}。",
        f"(12) 發行日： {dt_(s.issue_date)}。",
        f"(13) 期末訂價日：{dt_(s.final_date)}。",
        f"(14) 到期日：{dt_(s.maturity_date)}。",
        "(15) 配息日："
        + (
            "本商品持有期間並無配息金額。"
            if isinstance(s, (SbSpec, BenSpec))
            else "詳見中文產品說明書第9 項「本商品主要給付項目及其計算方式」。"
        ),
        *(
            ()
            if isinstance(s, BenSpec)
            else ("(16) 自動提前出場觀察日：詳見中文產品說明書第9 項「本商品主要給付項目及其計算方式」。",)
        ),
        "(20) 開始受理贖回日及後續受理贖回日：開始受理贖回日期：自發行日次日起。",
    ):
        d.text(30, t)
    d.section = "coupon"
    d.text(30, "2. 收益分配事項：")
    if isinstance(s, BenSpec):
        _iis_payments_ben(d, s)
    elif isinstance(s, SbSpec):
        _iis_payments_sb(d, s)
    else:
        _iis_payments(d, s)
    d.text(30, "3. 贖回價金之計算：請參見中文產品說明書第一章第9 項。")
    d.text(30, f"(4) 提前贖回金額即為公平市值贖回金額，此公平市值贖回金額可能低於100%{s.currency_zh}投資本金。")
    d.text(30, "4. 發行不成立之情形：本商品有效申購金額至少達等值三十萬美元之總申購金額。")
    d.section = "rest"
    d.text(30, "三、 投資風險")
    d.text(30, "(1) 最低收益風險：於最壞之情況，投資人有可能損失所有原始投資本金。")
    ccy = s.currency_zh
    d.text(
        30,
        f"(3) 利率風險：本商品之市場價格將受{ccy}利率變動所影響，例如當{ccy}利率上漲時，本商品之市場價格有可能下跌；"
        f"當{ccy}利率下跌時，本商品之市場價格有可能上漲。",
    )
    d.section = "fees"
    d.text(30, "四、 費用：受託或銷售機構(即受託人)向投資人(即委託人)收取之費用：")
    w.need(40)
    top = w.y
    for x, dy, t in (
        (40, 7, "費用項目"),
        (120, 0, "費率(百分比)"),
        (220, 7, "收取時點"),
        (330, 7, "收取方式"),
        (480, 7, "收取人"),
    ):
        w.put(x, top + dy, t, size=8)
    w.y = top + 26
    for cells in (
        [(30, "申購費用"), (110, "申購價金的"), (200, "投資人向受託")],
        [(110, "0%~5%")],
        [(30, "提前贖回費用"), (110, "投資人提前贖回"), (200, "投資人向受託")],
        [(110, "價金的0%~5%")],
        [(30, "管理費用(信託管理費"), (110, "無"), (200, "不適用")],
        [(30, "分銷費用(如屬"), (80, "報酬 無"), (200, "不適用")],
        [(80, "費用"), (110, "申購價金的0%~5%"), (200, "於本商品發行")],
        [(30, "其他費用"), (110, "無"), (200, "不適用")],
    ):
        w.row(cells, gap=13, size=8)
    d.text(30, "附註：申購費用係由投資人負擔。分銷費用係由投資人負擔，投資人同意其列為受託或銷售機構之報酬。")
    d.section = "rest"
    for t in (
        "五、 相關機構之權利、義務及責任.",
        "六、 協助投資人權益之保護方式",
        "七、總代理人對商品或投資人發生爭議之處理方式及管轄法院",
    ):
        d.text(30, t)
        d.text(30, "1. 不核對。")
    doc = w.doc
    while len(doc) < s.iis_pages:
        w.new_page()
        doc = w.doc
    while len(doc) > s.iis_pages:
        doc.delete_page(len(doc) - 1)
    for n, page in enumerate(doc, 1):
        page.insert_text((270, 830), f"第{n}頁，共{s.iis_pages}頁", fontname=FONT, fontsize=9)
    return w.save(path)


def _iis_payments(d: _Doc, s: Spec) -> None:
    """投資人須知二、2. 收益分配事項（FCN）。"""
    d.text(30, "(a) 「配息金額」：於各配息期(i)：每單位（張）商品面額x「配息率」；")
    d.text(30, f"配息率{s.monthly}%，換算年利率為{s.annual}%")
    d.text(30, "(b) 「自動提前出場贖回金額」：自動提前出場觀察日時持有之商品單位（張）數x 每單位（張）商品面額x100%")
    if s.memory:
        d.text(
            30,
            "自動提前出場事件：所有連結標的於預定自動提前出場觀察日或任何先前的自動提前出場觀察日之收盤價高於或等於自動提前出場價，"
            "則發生自動提前出場事件。為避免疑義，所有連結標的無須於同一自動提前出場觀察日符合上開條件。",
        )
    else:
        who = "連結標的" if s.count == 1 else "所有連結標的"
        d.text(
            30,
            f"自動提前出場事件：{who}於預定自動提前出場觀察日之收盤價高於或等於自動提前出場價，則發生自動提前出場事件。",
        )
    if s.ko_obs == "D":
        d.text(30, "自動提前出場日：於發生自動提前出場事件時，該自動提前出場觀察日後第二個營業日。")
        d.text(30, s.ko_daily)
    d.text(30, "(c) 「到期贖回金額」：本商品到期日將依下列方式贖回：")
    if s.ki == "none":
        d.text(
            30,
            f"(i) 若最差表現之連結標的期末訂價低於執行價：交付連結標的單位數，及以{s.currency_zh}支付剩餘金額（如有）。",
        )
        d.text(30, "(ii) 若最差表現之連結標的期末訂價高於或等於執行價：每單位（張）商品面額x 100%。")
    else:
        d.text(30, "(i) 若未發生界限事件，則到期贖回金額為：每單位（張）商品面額x 100%。")
        d.text(30, f"(ii) 若發生界限事件：交付連結標的單位數，及以{s.currency_zh}支付剩餘金額（如有）。")


def _iis_payments_sb(d: _Doc, s: SbSpec) -> None:
    """投資人須知二、2. 收益分配事項（Super Bull，投資人須知範本規格 §2；到期段原文標號寫成第二個 (a)）。"""
    d.text(30, "本商品主要給付項目為「自動提前出場贖回金額」或「到期贖回金額」。本商品持有期間並無配息金額。")
    d.text(
        30,
        "(a) 「自動提前出場贖回金額」：本商品於自動提前出場觀察日發生自動提前出場事件，發行機構將在該自動提前出場日支付"
        "每單位金額=每單位商品面額 × (100%+自動提前出場紅利) 。其後無任何金額給付。",
    )
    d.text(30, f"自動提前出場紅利：{s.annual:.4f}%。")
    d.text(30, s.event)
    d.text(
        30,
        "(a) 到期贖回金額：若本商品於所有自動提前出場觀察日均未發生自動提前出場事件，則於到期日時，發行機構將依下列計算"
        "公式給付：",
    )
    d.text(
        30,
        f"(i) 若{s.worst}期末訂價低於執行價：投資人將會收到發行機構交付{s.worst}單位數，及以{s.currency_zh}支付剩餘金額"
        "（如有）。",
    )
    d.text(
        30,
        f"(ii) 若{s.worst}期末訂價高於或等於執行價：投資人將會收到發行機構交付下列金額：到期時持有之商品單位數x 每單位"
        f"商品面額x{s.formula}。",
    )
    d.text(30, "詳細請參見中文產品說明書第一章第9 項之說明。")


def _iis_payments_ben(d: _Doc, s: BenSpec) -> None:
    """投資人須知二、2. 收益分配事項（BEN，投資人須知範本規格 §2；開頭「之的」照樣本）。"""
    d.text(30, "本商品主要給付項目為「到期贖回金額」，持有期間並無配息金額。")
    d.text(
        30,
        "到期日給付視最差表現之的連結標的期末訂價而定，依每單位面額，發行機構在到期日將依下列（1）或（2）方式支付到期"
        "贖回金額：",
    )
    _ben_maturity(d, s, 30)
    d.text(30, f"參與率：{s.participation}%。紅利率：{s.annual}%。詳細請參見中文產品說明書第一章第9 項之說明。")


# ---------------------------------------------------------------- 參考條件表與核對


def row(spec: Spec, **overrides: Any) -> dict[str, Any]:
    """與 `spec` 一致的參考條件表列：期初定價依 `pricing`（VWAP 時進場價空白）、Super Bull 的 Product 填 `Super Bull`。
    `overrides` 以 Excel 欄名覆寫。"""
    out = reference_row(spec)
    out["期初定價"] = PRICING[spec.pricing]
    if isinstance(spec, BenSpec):
        out["Product"] = BEN_PRODUCT
        out["參與率(%)"] = float(spec.participation)
    elif isinstance(spec, SbSpec):
        out["Product"] = SB_PRODUCT
    if spec.pricing == "vwap":
        out.update({f"UL_{i}_進場價": None for i in range(1, spec.count + 1)})
    out.update(overrides)
    return out


def check_item(
    tmp_path: Path,
    spec: Spec | None = None,
    *,
    pdf_spec: Spec | None = None,
    edits: Sequence[Edit] = (),
    config=CONFIG,
    **overrides: Any,
):
    """合成說明書（`pdf_spec`，預設同 `spec`；`edits` 改字）＋投資人須知＋與 `spec` 一致的參考條件表一列，經批量入口核對，
    回傳說明書的批量核對項目（BatchItem，含回填決策）。"""
    spec = spec or Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", pdf_spec or spec, edits=edits)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [row(spec, **overrides)])
    outcome = check_all(sheet, [pdf, iis_path(pdf)], config)
    return next(i for i in outcome.items if i.term_sheet == pdf)


def check(tmp_path: Path, spec: Spec | None = None, **kw: Any):
    """同 `check_item`，回傳說明書的 CheckReport。"""
    return check_item(tmp_path, spec, **kw).report


def check_iis(tmp_path: Path, spec: Spec | None = None, *, edits: Sequence[Edit] = (), config=CONFIG, **overrides: Any):
    """同 `check_item`，回傳投資人須知的批量核對項目。"""
    spec = spec or Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", spec, edits=edits)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [row(spec, **overrides)])
    outcome = check_all(sheet, [pdf, iis_path(pdf)], config)
    return next(i for i in outcome.items if i.term_sheet == iis_path(pdf))


def check_pair(
    tmp_path: Path,
    spec: Spec | None = None,
    *,
    pdf_spec: Spec | None = None,
    edits: Sequence[Edit] = (),
    config=CONFIG,
    **overrides: Any,
):
    """同 `check_item`，回傳（說明書, 投資人須知）兩份的批量核對項目（BatchItem）。"""
    spec = spec or Spec()
    pdf = build_pdf(tmp_path / f"{spec.product_code}_TS.pdf", pdf_spec or spec, edits=edits)
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", [row(spec, **overrides)])
    outcome = check_all(sheet, [pdf, iis_path(pdf)], config)
    ts = next(i for i in outcome.items if i.term_sheet == pdf)
    return ts, next(i for i in outcome.items if i.term_sheet != pdf)
