"""BARC 說明書合成器：把商品規格（tests/checker/reference_synth.py 的 `ProductSpec`）畫成仿 BARC 中文產品說明書版面的 PDF。

所有數值、代號、名稱皆為虛構；不含任何真實交易資料。版面座標依範本規格
docs/checker/templates/barc-zh-product-description.md 觀察值設定。PDF 排版用 tests/checker/pdf_writer.py，
參考條件表列用 tests/checker/reference_synth.py、單份核對用 tests/checker/harness.py。

改字製造錯誤用 `edits`（tests/checker/pdf_writer.py 的 `Edit`），段落代號：
- 說明書封面 `ts.cover`：各欄的值 `ts.cover.{欄}`（product_code、distributor_code、isin、name_zh、name_en、kind、
  issuer、currency、distributor、approval_date），標題 `ts.title`。
- 說明書第一章各條 `ts.art{n}`：第 13 條配息表 `ts.art13.coupon.{t}.{欄}`（valuation、payment）、觀察期／提前出場表
  `ts.art13.ko.{t}.{欄}`（start、end、trigger、payment、ko_valuation、early_redemption；定日記憶式表頭
  `ts.art13.ko.header`）；第 15／16 條價格表 `ts.art15.price`／`ts.art16.price`（格子 `.{標的序}.{欄}`：name、
  initial、strike、ko、ki）；第 16 條情境 `ts.art16.i`／`.ii`／`.iii`。
- 說明書其他各章：第二章各條 `ts.ch二.{n}`（2 = 保證機構、5 = 受託或銷售機構）、第三章 `ts.ch三`、第四章 `ts.ch四`（費用表各列
  `ts.ch四.fees.{費用項目}`）、第五章 `ts.ch五`。
- 投資人須知：`iis.p1`～`iis.p4`（依頁；費用表各列 `iis.p3.fees.{費用項目}`）。

`DacnSpec` 畫 BARC DACN（docs/checker/templates/barc-zh-dacn.md、barc-zh-dacn-iis.md）：§9(1)、§13(6)、§17 不寫配息率，
§14 改成區間計息公式與配息觀察期表（格子 `ts.art14.coupon.{t}.{欄}`：start、end、days、payment），§15 價格表多配息門檻欄
（格子欄名 `barrier`；列靠上對齊、名稱每行 6 字），§16 一般、最差情況第 2 期起 `× X% × (n/N)`；投資人須知收益分配事項改寫
配息率 t、價格表多配息門檻欄。
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from fcn_checker.issuers import BARC, Issuer, Template
from fcn_checker.schema import CheckReport
from harness import STANDARD, SynthChecks, check_sheet, iis_path
from pdf_writer import FONT, Edit, PdfWriter, require_used, zh_date
from reference_synth import (
    Q4,
    REFERENCE_HEADERS,
    ProductSpec,
    Ratings,
    build_reference_sheet,
    price,
    reference_row,
    set_dacn,
    write_notes,
)

FIXED_WARNING = STANDARD["risk"]["fixed_warning"]
DISTRIBUTOR = STANDARD["distributor"]
ISSUER_NAME = STANDARD["issuer"]["barc"]["issuer_name"]
FEES = dict(STANDARD["fees"])
SYNTH_ISIN = "XS0000000000"  # 合成說明書封面的 ISIN
APPROVAL_DATE = dt.date(2026, 6, 11)  # 封面受託或銷售機構審查通過之日期


@dataclass
class Spec(ProductSpec):
    """BARC 合成說明書的規格：商品規格加上 BARC 專屬的文件選項。預設值下說明書、投資人須知與參考條件表列完全一致。

    Non-Call 由保證配息期 G 推得（D 型 = G；P 型 = G + 1）；`first_callable` 不能直接給。
    只是改字的錯誤（名稱、日期、警語、費率、表格某一格、重複出現處的數值…）一律用 `build_pdf(..., edits=...)`；
    這裡只留改變版面結構的選項（`omit`、`extra_strike_def`、`extra_text`、`cross_page_price_table`、
    `break_coupon_after`），以及一次連動 §13(7) 定義句多處數字的 `guaranteed_text`。
    """

    issuer: str = "BARC"
    product_code: str = "029199990001"
    first_callable: int = field(init=False, default=1)
    monthly: Decimal | None = None  # 月配息率；None → 由年利率推算
    guaranteed: int | None = None  # 保證配息期；None → Daily 為 1、Period End 為 0
    # ---- BARC 專屬的文件選項 ----
    guaranteed_text: int | None = None  # §13(7) 定義句的期數；None → 同 guaranteed
    omit: frozenset[str] = frozenset()  # 不畫的項目："trade_date"／"issue_date"（改寫另行公告）、"strike_def"
    extra_strike_def: str | None = None  # 第二個執行價格定義（歧義）
    extra_text: str = ""  # 額外插入第五章的文字
    cross_page_price_table: bool = False  # 第 15 條價格表表頭跨頁
    break_coupon_after: int | None = None  # 第 13 條表格在第 N 期之後換頁
    # FCN_Stepdown（#544）：KO% 每期降幾個百分點；None 是 FCN。有值時第 13 條 (7) 多記憶事件句、`自動提前出場觸發百分比:詳見下表
    # 所示` 與觸發價格定義，提前出場表觸發百分比 = `ko` − 降幅 ×（期數 − 1），第 15 條沒有 KO 定義句、價格表與投資人須知沒有 KO 欄
    step: Decimal | None = None

    def __post_init__(self) -> None:
        self.first_callable = self.guaranteed_value if self.ko_obs == "D" else self.guaranteed_value + 1

    @property
    def monthly_value(self) -> Decimal:
        if self.monthly is not None:
            return self.monthly
        return (self.annual * self.tenor / 12 / self.tenor).quantize(Q4, ROUND_HALF_UP)

    def ko_at(self, period: int) -> Decimal:
        """KO 第 `period` 期的 KO%（第一個可提前出場的期別是第 1 期）；不是 Stepdown 時每期都是 `ko`。"""
        return self.ko - (self.step or 0) * (period - 1)

    @property
    def guaranteed_value(self) -> int:
        if self.guaranteed is not None:
            return self.guaranteed
        return 1 if self.ko_obs == "D" else 0

    @property
    def print_date(self) -> dt.date:
        """封面刊印日期：交易日 +1。"""
        return self.trade_date + dt.timedelta(days=1)

    def expected_name_zh(self) -> str:
        mem = "記憶式" if self.memory else ""
        return (
            f"英商巴克萊銀行{self.tenor}個月{self.currency_zh}計價連結股權{mem}自動提前出場結構型商品"
            "（不保本）（無擔保及無保證機構）（下稱「本商品」）"
        )

    def expected_name_en(self) -> str:
        mem = "Memory " if self.memory else ""
        return (
            f"{self.tenor} Months {self.ccy} {mem}Autocallable Equity Linked Note issued by Barclays Bank PLC "
            "(unsecured and non-guaranteed)"
        )


@dataclass
class DacnSpec(Spec):
    """BARC DACN 合成規格：預設同樣本的型態（美元、多標的、KO 每日觀察＋記憶式、KI 無、配息公式保證 1 期 = Non-Call 1）。
    名稱和 FCN 同一句。`barrier` 是配息門檻 %（說明書 §14 定義句、§15 與投資人須知價格表、參考條件表「配息門檻(%)」；
    None → 同執行價）；`formula_g` 是 §14 與投資人須知配息率 t 公式第 1 段的期數（None → 同保證配息期）。"""

    barrier: Decimal | None = None
    formula_g: int | None = None

    @property
    def barrier_value(self) -> Decimal:
        return self.strike if self.barrier is None else self.barrier

    @property
    def g(self) -> int:
        return self.guaranteed_value if self.formula_g is None else self.formula_g


# DACN §15 價格表各欄（欄頭 x、數字 x），依有沒有 KI 欄（範本規格 §5；仿樣本版面）
DACN_PRICE_COLUMNS = {
    False: (("initial", 126.0, 127.3), ("strike", 179.7, 206.0), ("barrier", 284.5, 310.7), ("ko", 389.2, 415.5)),
    True: (
        ("initial", 118.9, 120.1),
        ("strike", 173.5, 194.8),
        ("barrier", 270.9, 292.1),
        ("ko", 368.1, 389.4),
        ("ki", 465.5, 486.7),
    ),
}
# DACN 投資人須知價格表各欄（欄頭 x、數字 x）
DACN_IIS_COLUMNS = (
    ("initial", 129.4, 130.9),
    ("strike", 206.4, 224.9),
    ("barrier", 300.4, 318.9),
    ("ko", 394.5, 413.0),
    ("ki", 488.5, 507.0),
)


def _wrap_dacn_name(name: str) -> list[str]:
    """DACN 價格表名稱欄較窄：每行 12 個半形寬（中文字算 2）；英文字不拆開，長名稱折成三行。"""
    out, cur, width = [], "", 0
    for token in re.findall(r"[A-Za-z0-9&.,'-]+|\s+|.", name):
        if token.isspace():
            if cur:
                cur, width = cur + " ", width + 1
            continue
        w = len(token) if token.isascii() else 2 * len(token)
        if cur and width + w > 12:
            out.append(cur.rstrip())
            cur, width = "", 0
        cur, width = cur + token, width + w
    return [*out, cur.rstrip()]


def fmt_price(v: Decimal) -> str:
    return f"{v:,.4f}"


def ratings_text(r: Ratings) -> str:
    """發行機構評等（#243）：評等寫在評等機構前面，依標準普爾、穆迪、惠譽以 `/` 分隔
    （`A+（標準普爾長期債務信用評等）/A1（穆迪長期債務信用評等）/…`）；說明書第一章第 3 項、第二章 (7) 與投資人須知同寫法。"""
    return "/".join(f"{v}（{a}長期債務信用評等）" for a, v in r.stated())


def _finish(w: PdfWriter, path: Path) -> Path:
    """BARC 頁尾：封面右下「Page 1 of N」，其餘頁置中頁碼。"""
    n = w.doc.page_count
    for i, page in enumerate(w.doc):
        if i == 0:
            page.insert_text((508.7, 792), f"Page 1 of {n}", fontname=FONT, fontsize=8)
        else:
            page.insert_text((295.5, 806), str(i + 1), fontname=FONT, fontsize=8)
    return w.save(path)


# ---------------------------------------------------------------- 說明書


def next_weekday(d: dt.date) -> dt.date:
    d += dt.timedelta(days=1)
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d


def schedule_rows(s: Spec) -> list[dict[str, Any]]:
    """虛構的每期日期（末期 = 最終評價日／到期日），以及依保證配息期 G 產生的提前出場欄位。"""
    g = s.guaranteed_value
    rows: list[dict[str, Any]] = []
    for t in range(1, s.tenor + 1):
        if t == s.tenor:
            v, p = s.final_date, s.maturity_date
        else:
            v = s.trade_date + dt.timedelta(days=(s.final_date - s.trade_date).days * t // s.tenor)
            while v.weekday() >= 5:
                v += dt.timedelta(days=1)
            p = v + dt.timedelta(days=3)
        if s.ko_obs == "D":
            if t < g:
                start, end = "N/A", "N/A"
            elif t == g:
                start, end = "N/A", zh_date(v)
            else:
                prev = s.issue_date if t == 1 else rows[-1]["valuation"]
                start, end = zh_date(next_weekday(prev)), zh_date(v)
        else:
            start = end = None
        rows.append(
            {
                "t": t,
                "valuation": v,
                "payment": p,
                "start": start,
                "end": end,
                "noncallable": s.ko_obs == "P" and not s.memory and t <= g,
            }
        )
    return rows


def build_pdf(path: Path, s: Spec, *, edits: Sequence[Edit] = (), iis: bool = True) -> Path:
    """合成說明書；檔名是 `<商品代號>_TS.pdf` 且 `iis` 時，旁邊另寫一份同商品投資人須知（ADR 0007：兩份一起核對），
    `edits` 兩份都套用（依段落代號）。"""
    used: set[Edit] = set()  # 說明書與投資人須知共用：改字換到哪一份都算
    if iis and path.stem.endswith("_TS"):
        build_iis_pdf(iis_path(path), s, edits=edits, used=used)
    w = PdfWriter(edits, used=used)
    name_zh = s.expected_name_zh()
    name_en = s.expected_name_en()
    monthly = s.monthly_value
    ko_term = "自動提前出場觸發價格" if s.memory else "觸發水準"
    dacn = s if isinstance(s, DacnSpec) else None

    def split_at(text: str, *cuts: int) -> list[str]:
        """整段改字後在固定位置切開（版面上分成幾行的文字，改字要看整段）。"""
        bounds = [0, *cuts, len(text)]
        return [text[a:b] for a, b in zip(bounds, bounds[1:], strict=False)]

    # ---- p1 封面 ----
    w.section = "cover"
    w.line(255.6, "中文產品說明書", size=12, gap=16)
    w.section = "title"
    w.para(45.4, name_zh.replace("（下稱「本商品」）", ""), width=42, size=12, gap=16)
    w.section = "cover"
    w.space(6)

    def cover(label: str, key: str, value: str, lines: Callable[[str], list[str]] = lambda v: [v]) -> None:
        """封面一欄：值的段落代號是 cover.<key>；整段改字後再依 `lines` 分行。"""
        value_lines = lines(w.edit(value, f"cover.{key}"))
        w.need(13 * len(value_lines) + 6)
        w.put(41.0, w.y, label)
        with w.verbatim():
            for k, v in enumerate(value_lines):
                w.put(301.4, w.y + 13 * k, v)
        w.y += 13 * len(value_lines) + 6

    def en_lines(v: str) -> list[str]:
        cut = v.find("issued")
        return [v[:cut].strip(), v[cut:]] if cut > 0 else [v]

    cover("商品代號:", "product_code", s.product_code)
    cover("受託或銷售機構商品代號:", "distributor_code", s.product_code)
    cover("ISIN:", "isin", SYNTH_ISIN)
    cover("商品中文名稱:", "name_zh", name_zh, lambda v: [v[k : k + 25] for k in range(0, len(v), 25)])
    cover("商品英文名稱:", "name_en", name_en, en_lines)
    cover("商品種類:", "kind", "股權連結債券")
    cover("發行機構:", "issuer", ISSUER_NAME)
    cover("計價幣別:", "currency", s.currency_zh)
    d_name, d_phone, d_addr = DISTRIBUTOR["name"], DISTRIBUTOR["phone"], DISTRIBUTOR["address"]
    cover(
        "受託或銷售機構之名稱、電話及地址:",
        "distributor",
        f"{d_name}，電話：{d_phone}，地址：{d_addr}",
        lambda v: [v[: v.find("，地址：") + 3], v[v.find("，地址：") + 3 :]] if "，地址：" in v else [v],
    )
    cover("受託或銷售機構審查通過之日期:", "approval_date", zh_date(APPROVAL_DATE))
    w.line(41.0, "警語：", size=12, gap=18)
    first, rest = split_at(w.edit(FIXED_WARNING), 40)
    with w.verbatim():
        w.numbered("1.", 41.0, 69.4, first, gap=13)
        w.para(69.4, rest)
    w.numbered("2.", 41.0, 69.4, "本商品係複雜的金融商品，必須經過符合資格的人員解說後再進行投資。", gap=16)
    w.numbered("3.", 41.0, 69.4, "本商品係依境外結構型商品管理規則於中華民國境內受託投資或受託買賣之投資標的。", gap=16)
    w.para(69.4, "OSU 依據國際金融業務條例辦理受託投資或受託買賣所允許之投資標的。")
    w.space()
    w.line(41.0, f"本中文產品說明書刊印日期：{zh_date(s.print_date)}", gap=16)

    # ---- 第一章 ----
    w.new_page()
    w.section = "ch一"
    w.line(41.0, "第一章 商品基本資料", size=12, gap=22)
    art = "art"  # 條的段落代號前綴：第一章 art{n}，第二、三章 ch二.{n}／ch三.{n}

    def article(n: int, title: str) -> None:
        w.section = f"{art}{n}"
        w.numbered(f"{n}.", 41.0, 69.4, title)

    def sub(n: int, title: str, x_num: float = 69.4, x_body: float = 97.7) -> None:
        w.numbered(f"({n})", x_num, x_body, title)

    w.section = "art1"
    first, rest = split_at(w.edit(f"商品名稱：{name_zh}"), 45)
    with w.verbatim():
        article(1, first)
        w.para(69.4, rest)
    w.section = "art2"
    first, rest = split_at(w.edit("商品風險程度:" + FIXED_WARNING), 41)
    with w.verbatim():
        article(2, first)
        w.para(69.4, rest, width=44)
    article(3, "發行機構名稱及其長期債務信用評等：英商巴克萊銀行股份有限公司（Barclays Bank PLC）；")
    w.para(69.4, ratings_text(s.ratings), width=44)  # 真實樣本沒有句號，直接接第 4 項
    article(4, "商品之發行評等：不適用。")
    article(5, f"計價幣別：{s.currency_zh}")
    article(6, f"商品面額與發行價格：每單位商品面額為{s.denom:,} {s.currency_zh}。發行價格為商品面額之100%。")
    article(7, "計價貨幣本金保本率：無，本商品為不保障本金之境外結構型商品。")
    article(8, "投資本金達成100％保本之各項條件：不適用。")
    article(9, "主要給付項目及其計算方式：")
    sub(1, "配息金額：")
    if dacn:
        w.line(81.0, "詳細計算配息金額之方式請參閱本章第14 條之說明。")
    else:
        w.line(81.0, "以本商品未發生提前贖回或終止為前提，發行機構將於每一個「配息支付日t」支付每單位商品面額乘以每")
        w.line(81.0, f"月之配息率（為{monthly}%(顯示至小數點後第4 位)，即年利率為{s.annual}%）所計算之配息金額。")
    sub(2, "到期贖回：")
    w.line(81.0, "有關到期贖回之詳細說明，請參閱本章第15 條之說明。")
    sub(3, "指定提前現金交割金額：")
    w.line(81.0, "若「指定提前贖回事件」發生，發行機構將支付商品面額100%加計「相關配息金額」。")
    if dacn and s.ko_obs == "D":
        w.line(81.0, "「相關配息金額」係指依以下相關配息率而計算之金額：")
        w.line(126.0, "(i) 就於第1 個自動提前出場觀察期期末日當日發生之指定提前贖回事件日而言，相關配息率為")
        w.line(126.0, f"{monthly}%；或")
        w.line(126.0, f"(ii) 就除(i)外之其他情況而言，相關配息率為：{monthly}% × dt/Dt")
        w.line(126.0, "“dt”指就於某自動提前出場觀察期期間發生之指定提前贖回事件日而言，各標的資產之相關價格均")
        w.line(126.0, "高於或等於其配息門檻之自動提前出場評價日天數。")
        w.line(126.0, "“Dt”指該自動提前出場觀察期由計算代理機構決定之一籃子預定交易日之日數。")
        w.line(
            66.0, "投資人請注意，本商品於配息觀察期1 後之配息率之計算取決於相關配息觀察期內各標的資產之表現，故相關配"
        )
        w.line(66.0, f"息率可能遠低於月配息率{monthly}%，最壞情況下可能為零。")
    elif s.ko_obs == "D":
        w.line(81.0, "「相關配息金額」係指依以下相關配息率而計算之金額：")
        w.line(106.1, f"(i) 就於第1 個自動提前出場觀察期期末日當日發生者而言，相關配息率為{monthly}%；或")
        w.line(106.1, f"(ii) 就除(i)外之其他情況而言，相關配息率為：{monthly}% × Ant/Dt")
    else:
        w.line(81.0, "「相關配息金額」係指若「指定提前贖回事件」未曾發生時，原應支付之配息金額。")
    article(10, "連結標的資產及其相對權重、與投資績效之關連情形：")
    w.line(56.0, "(1) 連結標的資產：係指下表所示之標的資產（合稱「一籃子標的資產」）。", gap=19)
    w.row([(108.6, "標的資產"), (284.7, "交易所"), (418.6, "彭博代號（僅供參考）")], gap=22)
    for u in s.underlyings:
        w.row([(60.0, u.name[:14]), (254.7, u.exchange), (433.0, f"{u.ticker} Equity")], gap=24)
    w.line(71.1, "ADR 即存託憑證。", gap=19)
    w.line(56.0, "(2) 相對權重：不適用。", gap=19)
    w.line(56.0, "(3) 投資績效之關連情形：請參閱本章第15 條之說明。", gap=19)
    article(11, "連結標的資產之相關說明：")
    write_notes(w, s.underlyings, "label", x=81.0, label="標的資產{k}：{name}", heading=None)
    article(12, "標的資產調整：")
    w.line(81.0, "計算代理機構得依相關規定調整標的資產。")

    article(13, "商品年期、發行日、到期日及其他依商品性質而定之日期：")
    sub(1, f"商品年期：{s.tenor} 個月")
    if "trade_date" not in s.omit:
        sub(2, f"交易日：{zh_date(s.trade_date)}")
    else:
        sub(2, "交易日：另行公告")
    sub(3, f"發行日：{zh_date(s.issue_date)}" if "issue_date" not in s.omit else "發行日：另行公告")
    sub(4, f"最終評價日**：係指{zh_date(s.final_date)}，應視為評價日，如該日為「中斷日」應適用評價日有關")
    w.line(97.7, "「中斷日」之順延規定（並請參閱本條第(9)項之說明）")
    sub(5, f"到期日或最終實物贖回日†*：{zh_date(s.maturity_date)}（並請參閱本條第(9)項之說明）")
    rows = schedule_rows(s)

    def cells(table: str, t: int, spec: list[tuple[float, str, str]]) -> list[tuple[float, str, str]]:
        """一列的格子；每格的段落代號是 art13.<表>.<期>.<欄>。"""
        return [(x, text, f"art13.{table}.{t}.{key}") for x, key, text in spec]

    def table_row(t: int, cs: list[tuple[float, str, str]], gap: float = 20) -> None:
        w.row([(58.1 if t < 10 else 55.6, str(t)), *cs], gap=gap)
        if s.break_coupon_after == t:
            w.new_page()

    if dacn:
        sub(6, "配息支付日†*：依本章第14 條下表格所示（並請參閱本條第(9)項之說明）")
    elif s.ko_obs == "D" and not s.memory:
        sub(6, "配息支付日†*：依下表「觀察期」所示（並請參閱本條第(9)項之說明）")
    else:
        hdr = "評價日t" if (s.ko_obs == "P" and not s.memory) else "配息評價日t"
        sub(6, "配息支付日†*：依下表所示（並請參閱本條第(9)項之說明）")
        w.row([(68.7, "t"), (142.8, hdr), (281.8, "配息支付日t")])
        for r in rows:
            val = zh_date(r["valuation"])
            if r["noncallable"]:  # 註記換行：日期左移、下一行接「前出場評價日)」
                cs = cells(
                    "coupon",
                    r["t"],
                    [(106.7, "valuation", f"{val}(非自動提"), (268.1, "payment", zh_date(r["payment"]))],
                )
                w.put(137.9, w.y + 13, "前出場評價日)")
                table_row(r["t"], cs, gap=32)
            else:
                cs = cells("coupon", r["t"], [(131.7, "valuation", val), (270.6, "payment", zh_date(r["payment"]))])
                table_row(r["t"], cs)
    sub(7, "指定提前贖回事件：係指倘若所有標的資產之相關價格等於或大於其觸發價格，發行機構應提前贖回本商品。")
    w.line(97.7, "指定提前現金贖回日：係指定提前贖回事件發生後第三個營業日。")
    if s.step is not None:  # Stepdown（#544）：記憶事件句、商品家族標記與觸發價格定義
        w.para(
            97.7,
            "如於任何自動提前出場評價日，一標的資產之相關價格係高於或等於其個別之自動提前出場觸發價格，"
            "則該標的資產應視為已發生「記憶事件」。",
            width=42,
        )
        w.line(97.7, "自動提前出場觸發百分比:詳見下表所示。")
        w.para(
            97.7,
            "自動提前出場觸發價格：就任一標的資產而言，指該標的資產之「最初價格」乘以相對應之「自動提前出場觸發百分比」。",
            width=42,
        )
    if s.memory and s.ko_obs == "D":
        n, g = s.tenor, s.guaranteed_text if s.guaranteed_text is not None else s.guaranteed_value
        if g == 0:
            text = f"自動提前出場觀察期：就t 等於1 至{n} 的情況而言，則指自相關期始日起（含）至相關期末日止（含）之各期間；"
        else:
            ordinal = "首個" if g == 1 else f"第{'一二三四五六七八九十'[g - 1]}個"
            text = (
                f"自動提前出場觀察期：就{ordinal}（即t 等於{g} 的情況）自動提前出場觀察期而言，指期末日{g}，"
                f"且就各後續自動提前出場觀察期（其中當t 等於{g + 1} 至{n} 的情況）而言，"
                "則指自相關期始日起（含）至相關期末日止（含）之各期間；"
            )
        w.para(97.7, text + "上述各期間仍不為調整（如以下「自動提前出場觀察期」一表所示）。", width=42)
        w.line(97.7, "自動提前出場評價日：指自動提前出場觀察期內之各一籃子預定交易日。")
    if s.ko_obs == "D":
        w.line(69.4, "自動提前出場觀察期：" if dacn else "觀察期：")  # DACN 表名只有這幾個字（範本規格 §2）
        if s.memory:
            w.line(246.4, "自動提前出場觀察期")
            w.row([(58.1, "t"), (130.5, "期始日(含)t"), (284.3, "期末日(含)t"), (409.4, "自動提前出場觸發百分比")])
            spec = [(118.0, "start"), (271.9, "end"), (447.0, "trigger")]
        else:
            w.line(199.5, "觀察期", gap=11)
            w.put(436.9, w.y, "配息支付日t")  # 實際樣本此表頭比其他表頭高約 11 pt
            w.y += 11
            w.row([(58.1, "t"), (130.5, "期始日(含)t"), (284.3, "期末日(含)t")])
            spec = [(118.0, "start"), (271.9, "end"), (425.7, "payment")]
        first_row = max(s.guaranteed_value, 1)  # 第一個可提前出場的列（KO 第 1 期）
        for r in rows:
            text = {
                "start": r["start"],
                "end": r["end"],
                "trigger": "N/A" if r["end"] == "N/A" else f"{s.ko_at(r['t'] - first_row + 1)}%",
                "payment": zh_date(r["payment"]),
            }
            table_row(r["t"], cells("ko", r["t"], [(x, k, text[k]) for x, k in spec]))
    elif s.memory and s.ko_obs == "P":
        w.line(97.7, "自動提前出場評價日：依下表所示：")
        w.row(
            [
                (74.5, "t"),
                (139.1, "自動提前出場評價日", "art13.ko.header"),
                (272.0, "自動提前出場觸發百分比", "art13.ko.header"),
                (424.9, "指定提前現金贖回日", "art13.ko.header"),
            ]
        )
        for r in rows:
            spec_p = [
                (145.5, "ko_valuation", zh_date(r["valuation"])),
                (309.5, "trigger", f"{s.ko_at(r['t'])}%"),
                (428.7, "early_redemption", zh_date(r["payment"])),
            ]
            w.row([(74.5, str(r["t"])), *cells("ko", r["t"], spec_p)])
    sub(8, "評價日：指各配息評價日、自動提前出場評價日及最終評價日。")
    sub(9, "附註及相關定義：")
    w.line(81.0, "「營業日」係指倫敦及紐約之商業銀行開門營業之日。")

    article(14, "配息資料及其計算公式：")
    if dacn:
        _dacn_art14(w, dacn, rows)
    else:
        w.line(81.0, "每單位商品面額 × 配息率")
        w.line(81.0, f"「配息率」係指每月之配息率為{monthly}%(顯示至小數點後第4 位)（即年利率為{s.annual}%）。")

    article(15, "到期贖回計算公式，最低保證配息率及參與率:")
    sub(1, "到期贖回：", x_num=67.7, x_body=96.0)
    multi = len(s.underlyings) > 1
    if multi:
        w.line(67.7, "「最終價格」就某標的資產而言，指該標的資產於「最終評價日」之「相關價格」；")
    else:
        w.line(67.7, "「最終價格」指標的資產於「最終評價日」之「相關價格」；")
    w.line(67.7, "「最初價格」詳見下表所示；", gap=23)
    if "strike_def" not in s.omit:
        w.line(67.7, f"「執行價格」詳見下表所示（為最初價格的{s.strike}%）；", gap=23)
    if s.extra_strike_def:
        w.line(67.7, f"「執行價格」詳見下表所示（為最初價格的{s.extra_strike_def}%）；", gap=23)
    if s.ki == "D":
        w.line(66.0, "「觸及生效事件」係指於任一觸及生效評價日，如任一標的資產的「相關價格」小於其「觸")
        w.line(66.0, "及生效價格」，則視為發生「觸及生效事件」；", gap=23)
        w.line(67.7, "「觸及生效評價日」指所有標的資產自交易日起（含）至最終評價日止（含）之各預定交易日；", gap=23)
    if s.ki == "M":
        w.line(67.7, "「觸及生效評價日」指各配息評價日；", gap=23)
    if s.ki != "none":
        w.line(67.7, f"「觸及生效價格」詳見下表所示（為最初價格的{s.ki_pct}%)；", gap=23)
    if s.step is None:  # Stepdown 第 15 條沒有 KO 定義句（#544）
        w.line(67.7, f"「{ko_term}」詳見下表所示（為最初價格的{s.ko}%）；", gap=23)

    def price_table(section: str, cross_page: bool) -> None:
        """價格表：表頭在段落 `section`，格子在 `section.{標的序}.{欄}`；畫完回到原段落。"""
        outer, w.section = w.section, section
        cols = [("initial", 173.3), ("strike", 251.4), ("ko", 354.5)] + ([("ki", 457.5)] if s.ki != "none" else [])
        if s.step is not None:  # Stepdown 價格表沒有 KO 欄（#544）
            cols = [c for c in cols if c[0] != "ko"]
        head = {
            "initial": ["最初價格"],
            "strike": ["執行價格（為最初價", f"格的{s.strike}%）(四捨", "五入至小數點後第4", "位)"],
            "ko": (
                ["自動提前出場觸發價", "格（為最初價格乘以", "自動提前出場觸發百", "分比）(四捨五入至"]
                if s.memory
                else ["觸發水準（為最初價", f"格的{s.ko}%）(四捨", "五入至小數點後第4", "位)"]
            ),
            "ki": ["觸及生效價格（為最", f"初價格的{s.ki_pct}%）(", "四捨五入至小數點後", "第4 位)"],
        }
        if cross_page:
            w.y = w.BOTTOM - 26  # 表頭前兩行在本頁底部，其餘在下一頁
        w.need(26)
        # 表頭逐欄寫入（同一欄的各行相連），與真實樣本「每格一個文字區塊」的擷取順序一致
        for rows in ((0, 1), (2, 3)) if cross_page else ((0, 1, 2, 3),):
            if rows[0] == 2:
                w.new_page()
            w.need(13 * len(rows))
            if rows[0] == 0:
                w.put(71.5, w.y, "標的資產")
            for key, x in cols:
                for j, k in enumerate(rows):
                    if k < len(head[key]):
                        w.put(x, w.y + 13 * j, head[key][k])
            w.y += 13 * len(rows)
        w.space(10)
        pcts = {"strike": s.strike, "ko": s.ko, "ki": s.ki_pct}
        for i, u in enumerate(s.underlyings, 1):
            vals = {"initial": fmt_price(u.initial)}
            for key in ("strike", "ko", "ki"):
                vals[key] = fmt_price(price(u.initial, pcts[key]))
            w.need(40)
            y = w.y
            for key, x in (("initial", 174.6), ("strike", 277.7), ("ko", 380.8)) + (
                (("ki", 483.8),) if s.ki != "none" else ()
            ):
                if key == "ko" and s.step is not None:
                    continue
                w.put(x, y, vals[key], section=f"{section}.{i}.{key}")
            name_lines = _wrap_name(u.name)
            for k, part in enumerate(name_lines):
                w.put(51.6, y + 1 + 13 * k, part, section=f"{section}.{i}.name")
            w.y += max(24, 13 * len(name_lines) + 12)
        w.section = outer

    if dacn:
        _dacn_price_table(w, dacn)
    else:
        price_table("art15.price", s.cross_page_price_table)
    if multi:
        w.line(67.7, "「表現最差之標的資產」指於最終評價日當日價值最低之標的資產。")
    w.line(96.0, "發行機構應以實物交割時，將根據發行機構及相關結算機構規則進行交割活動。")
    when = "第1 個配息支付日" if dacn else "每一個配息支付日"
    sub(2, f"最低保證配息率：發行機構將於{when}支付依下列公式計算之配息金額：", x_num=67.7, x_body=96.0)
    w.line(182.5, f"每單位商品面額 × {monthly}%(顯示至小數點後第4 位)")
    if dacn:
        w.line(96.0, "投資人請注意，本商品之後續配息的配息率取決於配息觀察期內各標的資產相關價格之表現，故後續配")
        w.line(96.0, "息並無保證，最壞情況下可能為零。")
    sub(3, "參與率：不適用。", x_num=67.7, x_body=96.0)
    article(16, "投資收益計算方法，包含本金虧損之機率及以情境分析解說最大可能獲利、損失：")
    sub(3, "以情境分析解說最大可能獲利、損失及其他狀況之年化平均報酬率：", x_num=67.7, x_body=96.0)
    notional = f"{s.denom:,} {s.currency_zh}"
    w.line(77.7, f"a) 每單位商品面額 = {notional}")
    w.line(77.7, "b) 投資標的單位數 = 1 單位")
    w.line(77.7, "d) 本商品標的資產之相關資訊：")
    price_table("art16.price", False)
    w.line(77.7, "情境分析結果不保證未來績效。")
    _scenarios(w, s, notional)
    article(17, "平均年化報酬率：")
    if dacn:
        sub(1, "平均年化報酬率：請見本章第16 條情境分析中。")
    else:
        sub(1, "平均年化報酬率：本商品於各配息支付日支付之配息金額，")
        w.line(97.7, f"均以每月之配息率（為{monthly}%，即年利率為{s.annual}%）乘以每單位商品面額計算。")
    article(18, "提前贖回事件：")
    w.line(81.0, "發行機構於發生違約事件時得提前贖回本商品。")

    # ---- 第二章 ----
    w.new_page()
    w.section = "ch二"
    w.line(41.0, "第二章 相關機構事業概況", size=12, gap=22)
    art = "ch二."
    example = ("範例股份有限公司", "範例市範例路1 號")
    distributor = (DISTRIBUTOR["name"], DISTRIBUTOR["address"])
    for n, title, boss, (corp, addr) in [
        (1, "發行機構：", "Alex Example（CFO）", (ISSUER_NAME, "1 Example Place, London")),
        (2, "保證機構：不適用，本商品無保證機構。", None, example),  # 同真實樣本（#243：確認沒有保證機構）
        (3, "總代理人：", "王小明（董事長）", example),
        (4, "計算代理機構：", "Casey Sample（CFO）", example),
        (5, "受託或銷售機構：", DISTRIBUTOR["chairman"], distributor),
        (6, "報價機構：", "Robin Test", example),
    ]:
        article(n, title)
        if boss is None:
            continue
        sub(1, f"事業名稱：{corp}", x_num=67.7, x_body=96.0)
        established = (
            "2000 年11 月20 日" if n == 5 else "2000 年1 月1 日"
        )  # 受託機構 = 審查標準 distributor.established
        sub(2, f"設立日期：{established}", x_num=67.7, x_body=96.0)
        sub(3, f"營業所在地：{addr}", x_num=67.7, x_body=96.0)
        sub(4, f"負責人姓名：{boss}", x_num=67.7, x_body=96.0)
        if n == 1:  # 發行機構 (6)～(8)：(7) 信用評等沒有句號，直接接 (8)（#243）
            sub(6, "財務狀況：請參閱以下第(8)點。", x_num=67.7, x_body=96.0)
            sub(7, "信用評等：", x_num=67.7, x_body=96.0)
            w.para(96.0, ratings_text(s.ratings), width=40)
            sub(8, "經會計師查核簽證最近期財務報告中譯本之查核報告書：請參閱第二章。", x_num=67.7, x_body=96.0)

    # ---- 第三章 ----
    w.new_page()
    w.section = "ch三"
    w.line(41.0, "第三章 商品風險揭露", size=12, gap=22)
    art = "ch三."
    article(1, "投資風險警語：")
    first, rest = split_at(w.edit(FIXED_WARNING), 38)
    with w.verbatim():
        w.numbered("(1)", 69.4, 97.7, first, gap=13)
        w.para(97.7, rest, width=38)

    # ---- 第四章 ----
    w.new_page()
    w.section = "ch四"
    w.line(41.0, "第四章 一般交易事項", size=12, gap=22)
    w.numbered("1.", 35.4, 59.5, "商品開始受理申購、開始受理贖回日期及後續受理贖回日期：")
    w.numbered("(1)", 59.5, 83.7, f"商品開始受理申購日期：{zh_date(s.trade_date)}。")
    w.numbered("(2)", 59.5, 83.7, "開始受理投資人提前贖回日期：於發行日後的次一個營業日。")
    w.numbered("2.", 35.4, 59.5, "投資人應負擔的各項費用及金額或計算基準之表列：")
    w.row([(108.4, "費用項目"), (248.4, "費率"), (314.2, "收取時點"), (378.0, "收取方式"), (517.4, "收取人")])
    for label, rate in [
        (["申購費用"], ["申購價金的", FEES["申購費用"]]),
        (["提前贖回費用"], ["投資人提前贖", "回價金的", FEES["提前贖回費用"]]),
        (["管理費用（信託管理費）"], ["無"]),
        (["分銷費用（如屬發行機構", "給予受託機構之報酬）"], ["申購價金的", FEES["分銷費用"]]),
        (["其他費用"], ["無"]),
    ]:
        h = 11 * max(len(label), len(rate)) + 10
        w.need(h)
        fee = f"ch四.fees.{label[0].split('（')[0]}"  # 費用列的段落代號：ch四.fees.<費用項目>
        for k, t in enumerate(label):
            w.put(41.4, w.y + 11 * k, t, section=fee)
        for k, t in enumerate(rate):
            w.put(226.2, w.y + 11 * k, t, section=fee)
        w.put(301.4, w.y, "不適用" if rate == ["無"] else "申購時")
        w.y += h
    w.line(35.4, "附註：分銷費用係由投資人負擔。")
    w.numbered("4.", 35.4, 59.5, "最低申購金額及累加申購金額：")
    w.line(59.5, f"最低申購金額依受託或銷售機構規定，至少為{s.denom:,} {s.currency_zh}，最低累加申購金額為1 單位。")
    w.numbered("8.", 35.4, 59.5, "提前贖回之方式：")
    w.line(88.5, f"(a) 若投資人透過受託或銷售機構要求發行機構於次級市場提前贖回，最低贖回商品面額為{s.denom:,}")
    w.line(88.5, f"{s.currency_zh}，且須為商品面額之整數倍。")

    # ---- 第五章 ----
    w.new_page()
    w.section = "ch五"
    w.line(41.0, "第五章 特別記載事項", size=12, gap=22)
    w.line(41.0, "本商品之其他事項依銷售說明書辦理。")
    if s.extra_text:
        w.para(41.0, s.extra_text)
    require_used(edits, used)
    return _finish(w, path)


def _scenarios(w: PdfWriter, s: Spec, notional: str) -> None:
    """§16(3) 情境分析：(i) 有利（第 1 期提前出場）、(ii) 一般（持有至到期）、(iii) 最差。"""
    m = s.monthly_value
    ccy = s.currency_zh
    gross = Decimal(s.denom) * (100 + m) / 100
    w.section = "art16.i"
    w.numbered("(i)", 77.7, 106.1, "有利情況：假設本商品於第1 個觀察期期末日發生「指定提前贖回事件」。")
    w.line(77.7, "每單位指定提前現金交割金額 = 每單位商品面額 × (100% + 相關配息率)")
    w.line(77.7, f"= {notional} × (100% + {m}%) = {gross:,.2f} {ccy}")
    w.line(77.7, f"每單位累積配息金額 = 0.00 {ccy}")
    w.line(81.0, "總報酬率(截至指定提前贖回事件日之報酬) = [(每單位指定提前現金交割金額 + 每單位累積配息金額) /")
    w.line(52.7, f"= [({gross:,.2f} {ccy} + 0.00 {ccy}) / {notional}] - 1 = {m}%(平均年化報酬率：{s.annual}%)")
    if isinstance(s, DacnSpec):
        _dacn_scenarios(w, s, notional)
        return
    w.section = "art16.ii"
    w.numbered("(ii)", 77.7, 106.1, "一般情況：假設本商品未發生「指定提前贖回事件」，發行機構將於每一個「配息支付日")
    w.line(106.1, f"t」支付商品面額乘以{m}%之配息率所計算之配息金額。舉例說明如下：")
    w.line(77.7, f"執行價格（為最初價格的{s.strike}%）")
    coupon = (Decimal(s.denom) * m / 100).quantize(Decimal("0.01"), ROUND_HALF_UP)
    w.line(77.7, f"每單位配息金額 = {notional} × {m}% (四捨五入至小數點後第2 位)")
    w.line(77.7, f"每單位累積配息金額 = {coupon:,.2f} {ccy} × {s.tenor} = {coupon * s.tenor:,.2f} {ccy}")
    w.line(77.7, "總報酬率(截至到期日之報酬) = [(每單位累積配息金額 + 每單位最終現金交割金額) / 每單位商品面額] - 1")
    w.line(
        77.7,
        f"= [({coupon * s.tenor:,.2f} {ccy} + {notional})/ {notional}] - 1 = {m * s.tenor}% (平均年化報酬率：{s.annual}%)",
    )
    w.section = "art16.iii"
    w.numbered("(iii)", 77.7, 106.1, "最差情況：假設表現最差之標的資產之最終價格小於其執行價格，發行機構將支付每單位")
    w.line(106.1, f"商品面額乘以{m}%之配息率所計算之每單位配息金額。")
    w.line(77.7, f"每單位配息金額 = {notional} × {m}% (四捨五入至小數點後第2 位)")
    w.line(77.7, f"= [(5,000.00 {ccy})/ {notional}] - 1 = -50.00% (平均年化報酬率：-100.00%)")


def _dacn_scenarios(w: PdfWriter, s: DacnSpec, notional: str) -> None:
    """DACN §16(3) 一般、最差情況：第 1～g 期 `× X%`，其後 `× X% × (n/N)`（假設 10/20、0/20；範本規格 §4.3）。"""
    m, ccy, n, g = s.monthly_value, s.currency_zh, s.tenor, s.g
    first = (Decimal(s.denom) * m / 100).quantize(Decimal("0.01"), ROUND_HALF_UP)
    for section, label, case, k in (
        ("art16.ii", "(ii)", "一般情況：所有標的資產之收盤價於一籃子預定交易日僅有一半日期大於或等於其配息門檻，", 10),
        ("art16.iii", "(iii)", "最差情況：所有標的資產之收盤價於一籃子預定交易日均無大於或等於其配息門檻，", 0),
    ):
        w.section = section
        w.numbered(label, 77.7, 106.1, case)
        w.line(106.1, "發行機構將於每一個「配息支付日t」支付商品面額乘以各配息觀察期之配息率所計算之配息金額。")
        w.line(77.7, f"執行價格（為最初價格的{s.strike}%）")
        w.line(77.7, "第1 個配息支付日：" if g == 1 else f"第1 到{g} 個配息支付日：")
        w.line(77.7, f"每單位配息金額 = {notional} × {m}% (四捨五入至小數點後第2 位)")
        w.line(77.7, f"= {first:,.2f} {ccy}")
        rest = (Decimal(s.denom) * m / 100 * k / 20).quantize(Decimal("0.01"), ROUND_HALF_UP)
        w.line(77.7, f"第{g + 1} 到{n} 個配息支付日：")
        w.line(77.7, f"每單位配息金額 = {notional} × {m}% × ({k}/20) (四捨五入至小數點後第2 位)")
        w.line(77.7, f"= {rest:,.2f} {ccy}")
        total = first * g + rest * (n - g)
        w.line(
            77.7, f"每單位累積配息金額 = {first:,.2f} {ccy} × {g} + {rest:,.2f} {ccy} × {n - g} = {total:,.2f} {ccy}"
        )
        w.line(
            77.7, "總報酬率(截至到期日之報酬) = [(每單位累積配息金額 + 每單位最終現金交割金額) / 每單位商品面額] - 1"
        )
        ret = (total / s.denom * 100).quantize(Decimal("0.01"), ROUND_HALF_UP) if k else Decimal("-50.00")
        ann = (ret * 12 / n).quantize(Decimal("0.01"), ROUND_HALF_UP)
        w.line(77.7, f"= [({total:,.2f} {ccy} + {notional})/ {notional}] - 1 = {ret}% (平均年化報酬率：{ann}%)")


def _dacn_art14(w: PdfWriter, s: DacnSpec, rows: list[dict[str, Any]]) -> None:
    """DACN §14：六段固定寫法（範本規格 §2.1）與配息觀察期表（雙層表頭、Nt 欄；格子 `art14.coupon.{t}.{欄}`）。"""
    m, n, g = s.monthly_value, s.tenor, s.g
    w.line(66.0, "除非於相關配息觀察期內之所有配息評價日，一籃子標的資產中任一標的之相關價格小於其配息門檻，以至於")
    w.line(66.0, "配息率為零之情況外，發行機構將於每一個配息支付日支付依下列公式計算之配息金額。相關配息率t 將依配息")
    w.line(66.0, "觀察期t 內各標的資產之相關價格均大於或等於其配息門檻之配息評價日天數而定。請注意,任何配息金額的支")
    w.line(66.0, "付都以本商品未發生提前贖回或終止為前提。發行機構將於每一個配息支付日支付依下列公式計算之配息金額：")
    w.line(237.6, "每單位商品面額 × 配息率t", gap=23)
    w.line(66.0, "其中：", gap=23)
    w.line(81.0, f"「t」係自1 至{n} 的數字，每個數字代表一個配息支付日及相對應的配息觀察期以及配息率;", gap=23)
    w.line(81.0, "「配息率」", gap=23)
    first = "配息率t=1" if g == 1 else f"配息率t=1 到{g}"
    w.row([(241.9, "(i)"), (270.2, f"{first}={m}%;及")], gap=23)
    w.line(224.3, f"(ii) 配息率t={g + 1} 到{n}={m}% × nt/Nt;", gap=23)
    w.line(81.0, f"就配息支付日t={g + 1} 到{n}而言，", gap=23)
    w.line(81.0, "「nt」指與配息支付日t相對應之配息觀察期內，各標的資產之相關價格均高於或等於其配息門檻之配息評價")
    w.line(81.0, "日天數。", gap=23)
    w.line(
        81.0, "「Nt」指與配息支付日t相對應之配息觀察期內之一籃子預定交易日天數-如以下「配息觀察期」表格所示。", gap=23
    )
    w.line(81.0, f"「配息門檻」詳見本章第15 條之表格所示（為最初價格的{s.barrier_value}%）。", gap=23)
    w.line(81.0, "「配息觀察期」指從相關期始日起（含）至相關期末日止（含）之各期間，縱使發生任何標的資產之中斷日")
    w.line(81.0, "致配息評價日經延後，上述各期間仍不為調整，如下表所示:", gap=20)
    w.need(40)
    w.row([(59.1, "t"), (160.0, "配息觀察期*"), (302.0, "預定交易日日數(Nt)"), (443.7, "配息支付日t†*")], gap=19.5)
    w.row([(108.4, "期始日(含)t"), (214.0, "期末日(含)t")], gap=19.5)
    for r in rows:
        t = r["t"]
        text = {
            "start": r["start"],
            "end": r["end"],
            "days": "N/A" if t <= g else str(20 + t % 3),
            "payment": zh_date(r["payment"]),
        }
        xs = {
            "start": 127.2 if text["start"] == "N/A" else 93.4,
            "end": 199.0,
            "days": 338.4 if text["days"] == "N/A" else 340.9,
            "payment": 435.0,
        }
        last = t == s.tenor
        w.need(33 if last else 19.5)
        w.row(
            [
                (59.1, str(t), f"art14.coupon.{t}.t"),
                *((xs[k], text[k], f"art14.coupon.{t}.{k}") for k in ("start", "end", "days", "payment")),
            ],
            gap=13 if last else 19.5,
        )
        if last:
            w.line(451.3, "(即到期日)", gap=22)
    w.line(81.0, f"*為決定配息率t（其中當t 等於{g + 1} 至{n} 的情況）之目的，第1 個配息觀察期不適用。", gap=23)
    w.line(65.4, "投資人應注意，配息後，本商品之市場價值會相對降低。", gap=23)


def _dacn_price_table(w: PdfWriter, s: DacnSpec) -> None:
    """DACN §15 價格表：執行價格欄右邊多配息門檻欄；每列的字靠上對齊，名稱每行 6 字（長名稱折成三行，範本規格 §5）。
    格子 `art15.price.{標的序}.{欄}`。"""
    outer, w.section = w.section, "art15.price"
    cols = DACN_PRICE_COLUMNS[s.ki != "none"]
    ko_head = (
        ["自動提前出場觸發價", "格（為最初價格乘以", "自動提前出場觸發百", "分比）(四捨五入至", "小數點後第4 位)"]
        if s.memory
        else ["觸發水準（為最初價", f"格的{s.ko}%）(四捨", "五入至小數點後第4", "位)"]
    )
    head = {
        "initial": ["最初價格"],
        "strike": ["執行價格（為最初價", f"格的{s.strike}%）(四捨", "五入至小數點後第4", "位)"],
        "barrier": ["配息門檻（為最初價", f"格的{s.barrier_value}%）(四捨", "五入至小數點後第4", "位)"],
        "ko": ko_head,
        "ki": ["觸及生效價格（為最", f"初價格的{s.ki_pct}%）(", "四捨五入至小數點後", "第4 位)"],
    }
    rows = max(len(head[key]) for key, _, _ in cols)
    w.need(13 * rows + 40)
    y = w.y
    w.put(57.1, y + 13 * (rows // 2), "標的資產")
    for key, x, _ in cols:
        top = y + 13 * (rows // 2) if key == "initial" else y
        for k, t in enumerate(head[key]):
            w.put(x, top + 13 * k, t)
    w.y = y + 13 * rows + 10
    pcts = {"strike": s.strike, "barrier": s.barrier_value, "ko": s.ko, "ki": s.ki_pct}
    for i, u in enumerate(s.underlyings, 1):
        names = _wrap_dacn_name(u.name)
        w.need(13 * len(names) + 12)
        y = w.y
        for key, _, x in cols:
            value = u.initial if key == "initial" else price(u.initial, pcts[key])
            w.put(x, y, fmt_price(value), section=f"art15.price.{i}.{key}")
        for k, part in enumerate(names):
            w.put(47.2, y + 1 + 13 * k, part, section=f"art15.price.{i}.name")
        w.y += 13 * len(names) + 12
    w.section = outer


def _wrap_name(name: str) -> list[str]:
    if name.isascii():
        out, cur = [], ""
        for word in name.split():
            if cur and len(cur) + 1 + len(word) > 18:
                out.append(cur)
                cur = word
            else:
                cur = f"{cur} {word}".strip()
        return [*out, cur]
    return [name[k : k + 9] for k in range(0, len(name), 9)]


def build_not_barc_pdf(path: Path) -> Path:
    w = PdfWriter()
    w.line(255.6, "中文產品說明書", size=12, gap=16)
    w.line(41.0, "法商範例銀行12 個月美元計價連結股權結構型商品（不保本）")
    w.line(41.0, "商品代號:")
    w.put(301.4, w.y - 13, "037199990001")
    w.line(41.0, "發行機構:")
    w.put(301.4, w.y - 13, "法商範例銀行（Example Bank SA）")
    return _finish(w, path)


# ---------------------------------------------------------------- 參考條件表與單份核對


def check_rows(
    tmp_path: Path, pdf: Path, rows: list[dict[str, Any]], *, headers: list[str] | None = None
) -> CheckReport:
    """以合成參考條件表（rows）核對一份說明書，回傳該份的 CheckReport。"""
    sheet = build_reference_sheet(tmp_path / "FCN參考條件.xlsx", rows, headers)
    return check_sheet(pdf, sheet)


def check_pdf(tmp_path: Path, pdf: Path, spec: Spec | None = None, **overrides: Any) -> CheckReport:
    """以合成參考條件表（一列，依 spec；`overrides` 以 Excel 欄名覆寫）核對一份現成的說明書，回傳該份的 CheckReport。"""
    return check_rows(tmp_path, pdf, [row(spec or Spec(), **overrides)])


def row(spec: Spec, **overrides: Any) -> dict[str, Any]:
    """與 `spec` 一致的參考條件表列（DACN：Product 與配息門檻）；`overrides` 以 Excel 欄名覆寫。"""
    out = reference_row(spec)
    if isinstance(spec, DacnSpec):
        set_dacn(out, spec.strike, spec.barrier)
    out.update(overrides)
    return out


CHECKS = SynthChecks(Spec, build_pdf, row)  # 單份核對殼（harness.SynthChecks）
check = CHECKS.check

# ---- FCN_Stepdown（#544）：參考條件表 Product = FCN_Stepdown、多一欄 KO降幅(%)


@dataclass
class StepdownSpec(Spec):
    """BARC FCN_Stepdown 的預設規格：D 型、記憶式、6 個月、起始 KO 100%、每期降 2 個百分點（其他同 `Spec`）。"""

    product_code: str = "029199990002"
    step: Decimal | None = Decimal("2.00")


STEPDOWN_PRODUCT = "FCN_Stepdown"
STEPDOWN_HEADERS = [*REFERENCE_HEADERS, "KO降幅(%)"]


def stepdown_row(spec: Spec, **overrides: Any) -> dict[str, Any]:
    """與 Stepdown 合成說明書一致的參考條件表列：Product = FCN_Stepdown、KO降幅(%) = `spec.step`、KO(%) 與 KO 價是第 1 期。"""
    assert spec.step is not None, "Stepdown 的參考條件表列要有降幅"
    return reference_row(spec, **{"Product": STEPDOWN_PRODUCT, "KO降幅(%)": float(spec.step), **overrides})


STEPDOWN_CHECKS = SynthChecks(StepdownSpec, build_pdf, stepdown_row, STEPDOWN_HEADERS)


def barc_adapter(**overrides: Any) -> Issuer:
    """依上手 adapter interface 建立以 BARC parser 為底、只有一個範本的測試用上手；overrides 換掉其中幾項（上手的
    `reference_fields`、`ratings`、`with_calendar`，其餘是範本的欄位）。"""
    from fcn_checker.parsers import barc as parser
    from fcn_checker.rules import barc as rules

    fields: dict[str, Any] = {
        "code": "BARC",
        "template_id": parser.TEMPLATE_ID,
        "label": "BARC 測試 adapter",
        "parser_version": "test",
        "not_covered": (),
        "detect": lambda lines: parser.detect(parser.document(lines)),
        "read": parser.read,
        "rules": rules.run_all,
        "reference_fields": rules.REFERENCE_FIELDS,
        "iis": BARC.iis,  # 投資人須知照 BARC 範本
    }
    merged = {**fields, **overrides}
    issuer = {k: merged.pop(k) for k in ("reference_fields", "ratings", "with_calendar") if k in merged}
    return Issuer(merged["code"], (Template(**merged),), **issuer)


# ---------------------------------------------------------------- 投資人須知（docs/checker/templates/barc-zh-iis.md）

IIS_PAGES = 4  # 審查標準 iis.pages


def _dacn_iis_income(w: PdfWriter, s: DacnSpec) -> None:
    """DACN 投資人須知收益分配事項：配息率 t 寫法（範本規格 barc-zh-dacn-iis.md §1.1，沒有年利率）與價格表（多配息門檻欄、
    列靠上對齊、名稱每行 6 字）。"""
    m, n, g = s.monthly_value, s.tenor, s.g
    first = "如t=1" if g == 1 else f"如t=1 到{g}"
    w.line(70.1, "配息金額：以提前贖回或終止未發生為前提，發行機構將於每一個「配息支付日t」支付每單位商品面", gap=11.7)
    w.line(70.1, "額乘以相關配息率所計算之配息金額：", gap=11.7)
    w.para(
        70.1,
        f"「配息率t」係指， (i){first}，為{m}%；及(ii)如t={g + 1} 到{n}，為{m}% × nt/Nt；其中：「t」係自1至{n} 的數字，"
        "每個數字代表一個配息支付日及相對應的配息觀察期及配息率。就配息支付日t而言，「nt」指與配息支付日t 相對應之配息觀察"
        "期內，各標的資產之相關價格高於或等於其配息門檻之配息評價日天數。「Nt」指與該配息支付日t相對應之配息觀察期內之一籃子"
        "預定交易日天數。",
        width=44,
        gap=11.7,
    )
    w.line(48.4, "(2) 到期贖回：請參閱中文產品說明書第一章第15 條。", gap=11.7)
    cols = [c for c in DACN_IIS_COLUMNS if c[0] != "ki" or s.ki != "none"]
    ko_head = (
        [
            "自動提前出場觸",
            "發價格（為最初",
            "價格乘以自動提",
            "前出場觸發百分",
            "比）(四捨五入",
            "至小數點後第4",
            "位)",
        ]
        if s.memory
        else ["觸發水準（為最", f"初價格的{s.ko}%", "）(四捨五入至", "小數點後第4 位", ")"]
    )
    head = {
        "initial": ["最初價格"],
        "strike": ["執行價格（為最", f"初價格的{s.strike}%", "）(四捨五入至", "小數點後第4 位", ")"],
        "barrier": ["配息門檻（為最", f"初價格的{s.barrier_value}%", "）(四捨五入至", "小數點後第4 位", ")"],
        "ko": ko_head,
        "ki": ["觸及生效價格（", "為最初價格的", f"{s.ki_pct}%）(四捨五", "入至小數點後第", "4 位)"],
    }
    rows = max(len(head[key]) for key, _, _ in cols)
    w.need(12 * rows + 40)
    y = w.y
    w.put(40.1, y + 12 * 3, "標的資產")
    for key, x, _ in cols:
        top = y + 12 * 3 if key == "initial" else y
        for k, t in enumerate(head[key]):
            w.put(x, top + 12 * k, t)
    w.y = y + 12 * rows
    pcts = {"strike": s.strike, "barrier": s.barrier_value, "ko": s.ko, "ki": s.ki_pct}
    for u in s.underlyings:
        names = _wrap_dacn_name(u.name)
        w.need(12 * len(names) + 2)
        for k, part in enumerate(names):
            w.put(28.8, w.y + 12 * k, part)
        for key, _, x in cols:
            w.put(x, w.y, fmt_price(u.initial if key == "initial" else price(u.initial, pcts[key])))
        w.y += 12 * len(names)
    w.line(45.0, "「表現最差之標的資產」指於最終評價日當日價值最低之標的資產，係按下列公式計算：", gap=16)


def build_iis_pdf(
    path: Path, s: Spec, *, edits: Sequence[Edit] = (), pages: int = IIS_PAGES, used: set[Edit] | None = None
) -> Path:
    """仿 BARC 中文投資人須知（4 頁）；值與 `s` 的說明書一致。`edits` 改字（製造錯誤）、`pages` 改頁數。

    `used` 給 `build_pdf` 用：換到的改字記在呼叫端的 set，由它檢查；省略時這裡自己檢查。
    """
    w = PdfWriter(edits, kind="iis", used=used)
    dist = DISTRIBUTOR["name"]
    name_zh = s.expected_name_zh().replace("（下稱「本商品」）", "")
    name_en = s.expected_name_en()

    def para(t: str) -> None:
        w.para(20.5, t, width=48)

    def page1() -> None:
        w.line(191.0, "中文投資人須知（專業投資人與OSU 客戶）")
        w.line(41.3, name_zh)
        w.line(20.6, f"({name_en})（下稱「本商品」）（商品種類：股權連結債券）")
        w.need(13)
        w.put(20.0, w.y, f"商品代號:{s.product_code}")
        w.put(157.9, w.y, f"受託或銷售機構商品代號:{s.product_code}")
        w.put(441.8, w.y, f"ISIN:{SYNTH_ISIN}")
        w.y += 13
        w.line(20.0, "警語：")
        para("1." + FIXED_WARNING)
        para(
            f"4.本商品雖經{dist}審查，並不代表證實申請事項或保證本商品之價值，且{dist}不負本商品投資盈虧之責。{dist}依法不得承諾擔保投資本金或最低收益率。"
        )
        para(
            f"5.本商品持有期間如有保證配息收益和保證保本率係由英商巴克萊銀行股份有限公司（發行機構）保證，而非由{dist}保證。"
        )
        para(
            f"6.本中文投資人須知之內容如有虛偽或隱匿之情事者，除受託或銷售機構另行訂定者，係由{dist}負責外，其餘內容由總代理人負責。"
        )
        para("7.本商品係依境外結構型商品管理規則於中華民國境內受託投資或受託買賣之投資標的。")
        para(f"9.{dist}應提供專業投資人及OSU 客戶相關契約審閱期間。")
        w.line(20.0, "相關機構事業概況：")
        para(f"1.發行機構：{ISSUER_NAME}；營業所在地：1 Example Road, London。")
        para(f"3.受託或銷售機構：{dist}；營業所在地：{DISTRIBUTOR['address']}。")
        w.line(20.0, "商品簡介：")

    def page2() -> None:
        w.line(20.5, "3.本商品風險程度：RR4")
        para(f"4.發行機構之長期債務信用評等：{ratings_text(s.ratings)}。")  # #243
        para("5.本商品之發行評等：不適用（本商品之受託或銷售對象僅限於專業投資人及OSU 客戶）。")
        w.line(20.5, f"6.計價幣別：{s.currency_zh}。")
        para(
            f"7.商品面額與發行價格：每單位商品面額為{s.denom:,} {s.currency_zh}，最低申購金額為"
            f"{s.denom:,} {s.currency_zh}。發行價格為商品面額之100%。"
        )
        w.line(20.5, "8.計價貨幣本金保本率：無，本商品為不保障本金之境外結構型商品。")
        w.line(20.5, "9.投資本金達成100％保本率之各項條件：不適用，本商品為不保障本金之境外結構型商品。")
        w.line(20.5, "10.連結標的資產：" + "、".join(f"{u.ticker} Equity" for u in s.underlyings) + ".")
        w.line(20.5, f"11.商品年期：{s.tenor} 個月。")
        w.line(20.5, f"12.發行日：{zh_date(s.issue_date)}。")
        w.line(20.5, f"13.到期日或最終實物贖回日：{zh_date(s.maturity_date)}。")
        w.line(20.5, "14.開始受理投資人提前贖回日期：於發行日後的次一個營業日。")
        w.line(20.0, "收益分配事項：")
        if isinstance(s, DacnSpec):
            _dacn_iis_income(w, s)
            w.line(48.4, "(3) 指定提前現金交割金額：請參閱中文產品說明書。")
            return
        para(
            f"(1) 配息金額：每單位商品面額乘以每月之配息率（為{s.monthly_value}%(顯示至小數點後第4位)，"
            f"即年利率為{s.annual}%）所計算之配息金額。"
        )
        # 價格表前的說明句列出表上的價格名詞（Stepdown 沒有觸發價格，#544）
        terms = ["最初價格", "執行價格"]
        if s.step is None:
            terms.append("自動提前出場觸發價格" if s.memory else "觸發水準")
        if s.ki != "none":
            terms.append("觸及生效價格")
        listed = "、".join(f"「{t}」" for t in terms[:-1]) + f"與「{terms[-1]}」"
        para(
            f"其中，「最終價格」就某標的資產而言，指該標的資產於「最終評價日」之「相關價格」；而{listed}，請詳見下表所示。"
        )
        # 價格表：記憶式商品 KO 欄頭不寫百分比；有 KI 時多一欄「觸及生效價格」（docs/checker/templates/barc-zh-iis.md）
        ko_head = (
            [
                "自動提前出場觸發",
                "價格（為最初價格",
                "乘以自動提前出場",
                "觸發百分比）(四捨",
                "五入至小數點後第4",
                "位)",
            ]
            if s.memory
            else ["觸發水準（為最初", f"價格的{s.ko}%）(", "四捨五入至小數點", "後第4 位)"]
        )
        columns = [  # 欄頭 x、數字 x、欄頭各行、百分比
            (164.3, 165.7, ["最初價格"], None),
            (252.6, 276.6, ["執行價格（為最初", f"價格的{s.strike}%）(四", "捨五入至小數點後", "第4 位)"], s.strike),
            (363.5, 387.5, ko_head, s.ko),
        ]
        if s.step is not None:  # Stepdown 投資人須知價格表沒有 KO 欄（#544）
            columns.pop()
        if s.ki != "none":
            columns.append(
                (
                    474.5,
                    498.5,
                    ["觸及生效價格（為", f"最初價格的{s.ki_pct}%", "）(四捨五入至小數", "點後第4 位)"],
                    s.ki_pct,
                )
            )
        rows = max(len(h) for _, _, h, _ in columns)
        w.need(12 * rows + 20)
        y = w.y
        w.put(53.3, y + 12 * (rows // 2), "標的資產")
        for x, _, head, _ in columns:
            top = y + 12 * ((rows - len(head)) // 2)
            for k, t in enumerate(head):
                w.put(x, top + 12 * k, t)
        w.y = y + 12 * rows + 6
        for u in s.underlyings:
            names = _wrap_name(u.name)
            w.need(12 * len(names) + 4)
            for k, n in enumerate(names):
                w.put(30.7, w.y + 12 * k, n)
            for _, x, _, pct in columns:
                w.put(x, w.y, fmt_price(u.initial if pct is None else price(u.initial, pct)))
            w.y += 12 * len(names) + 4
        w.line(48.4, "(3) 指定提前現金交割金額：請參閱中文產品說明書。")

    def page3() -> None:
        w.line(20.0, "本商品各類投資風險：")
        para("(1) 最低收益風險：在最差的狀況下，投資人將損失所有本金及利息。")
        w.line(20.0, "本商品之費用明細表：")
        for label, rate in (
            (["申購費用"], ["申購價金的", FEES["申購費用"]]),
            (["提前贖回費用"], ["投資人提前贖", "回價金的", FEES["提前贖回費用"]]),
            (["管理費用（信託管理費或管銷費用）"], ["無"]),
            (
                ["分銷費用（如屬發行機構或發行人給予", "受託或銷售機構之報酬、費用、折讓等", "各項利益應單獨列示）"],
                ["申購價金的", FEES["分銷費用"]],
            ),
        ):
            h = 11 * max(len(label), len(rate)) + 6
            w.need(h)
            fee = f"p3.fees.{label[0].split('（')[0]}"  # 費用列的段落代號：p3.fees.<費用項目>
            for k, t in enumerate(label):
                w.put(20.4, w.y + 11 * k, t, section=fee)
            for k, t in enumerate(rate):
                w.put(230.8, w.y + 11 * k, t, section=fee)
            w.y += h

    def page4() -> None:
        w.line(20.0, "相關機構之權利、義務及責任：")
        para("1. 發行機構將根據本商品有關條件支付應付之相關款項。")

    builders = [page1, page2, page3, page4]
    for k, build in enumerate(builders):
        if 0 < k < pages:
            w.new_page()
        w.section = f"p{k + 1}"
        build()
    for _ in range(pages - len(builders)):
        w.new_page()
        w.line(20.0, "（續）")
    if used is None:
        require_used(edits, w.used)
    return _finish(w, path)
