"""MS BEN 中文產品說明書（docs/checker/templates/ms-zh-ben.md）：版面、封面、標的表、第二章以後同 MS FCN（parsers/ms.py），
先照 FCN 讀出，再改讀 BEN 不同的條目（第一章 §10 主要給付、§14(9) 回顧觀察期間、§15 到期公式／定義／價格表／紅利與參與率、
§16 假設與情境一～三）。一般 BEN（紅利表現）與 Digital 兩型共用本 parser，型態由名稱決定；lookback 由 §14(9) 等五處一起判斷。

只支援現行版範本；認得出是 MS BEN、但現行版特徵不齊時辨識交出「不支援的範本版本」（範本規格 §1、§8）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, NamedTuple

from .. import standard_fields
from ..schema import DetectionResult, Evidence, FieldStatus, Line, ParsedField
from ..standard_fields import ScenarioParam, ScenarioPrice
from ..text import squash
from . import money, ms, ms_scenario, ms_tables, price_table
from .common import Hit, Mention, dec, derived, exactly_one, hits, one
from .hsbc_ben import Delivery, maturity_only
from .hsbc_super_bull import Pnl, RemainderCalc, SharesCalc, ValueCalc
from .layout import Document, Span, TextIndex, capture, parse_date
from .super_bull import ProfitReturn

TEMPLATE_ID = "ms-zh-ben-pd"
PARSER_VERSION = "1"
# 待使用者確認 BEN-376-05：版本依內容判斷、不看交易日；現行版特徵（`current_problems`）對不上 → 不支援的範本版本
OLD_VERSION = "MS BEN 說明書的寫法和現行版不同"
ISSUER_ZH = "英商摩根士丹利發行"
NAME_EN = "MorganStanley&Co.InternationalPlcissuanceof"
Kind = Literal["maxi", "digital"]
KIND_LABEL = {"maxi": "一般 BEN（紅利表現）", "digital": "Digital"}
# 第一章條號：§19 的小節誤印成 20.1～20.4、沒有 §20 標題（範本原樣，18 份樣本都這樣），條號到 28
ARTICLES = (*range(1, 20), *range(21, 29))
# 型態 → 中文名稱、英文名稱（去空白）的辨識寫法（範本規格 §8 條件 2、3）。待使用者確認 BEN-376-03：Digital 名稱太通用，
# 另要 §10／§15 Digital 公式、參與率 0 與 Product BEN 都成立才認（`current_problems`、共用 `template.product_family`）
NAME_ZH: dict[Kind, str] = {
    "maxi": r"紅利表現結構型商品",
    "digital": r"連結(?:股票或指數股票型基金|一籃子股票與/或指數股票型基金)結構型商品",
}
NAME_EN_MARK: dict[Kind, str] = {"maxi": "BonusEnhancedNotes", "digital": "EquityLinkedNotes"}
NOT_IN_ZH = ("固定配息", "區間累積", "自動提前")
NOT_IN_EN = ("FixedCoupon", "Autocallable")
# 說明書明寫的參與率（§15(3)）要是該型態的值
PARTICIPATION: dict[Kind, Decimal] = {"maxi": Decimal(100), "digital": Decimal(0)}
PCT = r"(\d+(?:\.\d+)?)%"

# ---------------------------------------------------------------- §10、§15 的固定寫法（去空白、括號全形，範本規格 §2.1）

OPENING = "主要給付項目及其計算方式：主要給付項目為到期贖回金額："  # §10 開頭：沒有 KO、沒有定期配息的明確句
TITLE15 = "到期贖回計算公式、最低保證贖回率及參與率："
# §15(1) 開頭：Digital（單一標的）也寫「籃子中表現最差的連結標的」（待使用者確認 BEN-376-04：當固定句、不判錯）
HEAD15 = "（1）於到期日，視期末定價日時籃子中表現最差的連結標的之收盤價而定，每單位商品將依據下列情形（a）或（b）在到期日贖回："
SUBJECT: dict[Kind, str] = {"maxi": "籃子中表現最差的連結標的", "digital": "該連結標的"}
UNITS: dict[Kind, tuple[str, str]] = {
    "maxi": ("籃子中表現最差連結標的", "籃子中表現最差連結標的"),
    "digital": ("連結標的", "該連結標的"),
}
MIN_RATE = "最低保證贖回率：本商品不適用。本商品不保本。本商品於到期的本金贖回可能等於零。"
STRIKE_DEF = r"執行價：就任何連結標的而言，為相關連結標的之期初價格的" + PCT
LOOKBACK_INITIAL_DEF = (
    "回顧期初價格：就任何連結標的而言，即為以下兩項中的較低者：（i）該連結標的期初價格及（ii）該連結標的於回顧觀察期間內"
    "任何共同預定交易日之最低收盤價。"
)
LOOKBACK_STRIKE_DEF = r"回顧執行價：就任何連結標的而言，即為該連結標的回顧期初價格乘以" + PCT + "。"
# 一般 BEN 最差表現定義：lookback 版仍寫期初價格（待使用者確認 BEN-376-01：照抄當固定句，不判錯）
WORST_DEF = (
    "籃子中表現最差的連結標的：若籃子中連結標的在期末定價日時收盤價除以其期初價格的數值，比一籃子中其他連結標的有關的數值"
    "為低，則就期末定價日該連結標的為籃子中表現最差的連結標的。倘於該日籃子內超過一個連結標的有相同的最低數值，則由決定代理"
    "機構選擇該日的籃子中表現最差的連結標的。"
)
BONUS = r"（1）紅利配息率：" + PCT
PARTICIPATION_RATE = r"（3）參與率：" + PCT
LOOKBACK_PERIOD = r"（9）回顧觀察期間：(.*?)至(\d{4}年\d{1,2}月\d{1,2}日)（包含）。"
LOOKBACK_START = "自交易日（包含）起"  # §14(9) 起點的固定寫法（核對規則 §3.4）


def strike_word(lookback: bool) -> str:
    return "回顧執行價" if lookback else "執行價"


def cash_clause(kind: Kind, lookback: bool) -> str:
    """§15(1)(a)（= §10(a)、投資人須知贖回價金 1)(a)）：≥ 執行價（lookback：回顧執行價）付現的公式。"""
    if kind == "maxi":
        base = "回顧期初價格" if lookback else "期初價格"
        payoff = (
            "到期日持有之商品單位數x每單位商品面額x{100%+最大值[紅利配息率,參與率x（籃子中表現最差的連結標的期末定價日之收盤價"
            "/籃子中表現最差的連結標的" + base + "-100%）]}；否則，"
        )
    else:
        payoff = "每單位面額x（100%+紅利配息率）；否則，"
    return f"（a）現金交割：若於期末定價日時{SUBJECT[kind]}之收盤價高於或等於其{strike_word(lookback)}，則投資人領回：" + payoff


def delivery_opening(kind: Kind, lookback: bool) -> str:
    """§15(1)(b) 開頭（投資人須知只比這一句，後面的說明寫法略有不同）。"""
    return (
        f"（b）實物交割：若於期末定價日時{SUBJECT[kind]}之收盤價低於其{strike_word(lookback)}，發行機構將於到期日，"
        "以支付「最終實物贖回權益」之方式贖回每單位商品："
    )


def delivery_clause(kind: Kind, lookback: bool) -> str:
    """§15(1)(b)（= §10(b)）：< 執行價（lookback：回顧執行價）交股，直接比執行價（無 KI）。"""
    s, k = SUBJECT[kind], strike_word(lookback)
    unit, held = UNITS[kind]
    return (
        delivery_opening(kind, lookback)
        + f"「最終實物贖回權益」就每單位商品而言，係指每單位商品面額乘以轉換匯率，除以{s}之{k}後所得數目之{unit}。"
        f"如該數目非整數，則{held}之股數為無條件捨去至整數位後之數，非整數之畸零股將以現金支付代替，按{s}於期末定價日之"
        f"收盤價計算（即不滿一股之畸零股數乘以{s}於期末定價日之收盤價除以轉換匯率），並以計價幣別支付。"
    )


def formula_of(text: str) -> list[tuple[Kind, bool]]:
    """文字裡出現的 (a) 現金交割公式是哪一型、有沒有 lookback（應剛好一種）。"""
    return [(k, lb) for k in ("maxi", "digital") for lb in (False, True) if cash_clause(k, lb) in text]


# ---------------------------------------------------------------- 辨識


def kind_of(zh: str | None, en: str | None) -> tuple[Kind | None, list[str]]:
    """商品家族與型態的名稱條件（說明書、投資人須知共用；去空白後的名稱）：中文以「英商摩根士丹利發行」開頭、有一般 BEN 或
    Digital 的寫法、沒有「固定配息」「區間累積」「自動提前」；英文是新版 MS 寫法、有同一型態的辨識字、沒有 Fixed Coupon、
    Autocallable。回傳（型態, 不成立的說明）。"""
    zh_kinds = [k for k, p in NAME_ZH.items() if zh and re.search(p, zh)]
    en_kinds = [k for k, mark in NAME_EN_MARK.items() if en and mark in en]
    out = []
    if not (zh and zh.startswith(ISSUER_ZH) and len(zh_kinds) == 1 and not any(w in zh for w in NOT_IN_ZH)):
        out.append(
            f"商品中文名稱不是以「{ISSUER_ZH}」開頭、沒有「紅利表現結構型商品」或「連結…結構型商品」、或有"
            + "「" + "」「".join(NOT_IN_ZH) + "」"
        )
    if not (en and en.startswith(NAME_EN) and len(en_kinds) == 1 and not any(w in en for w in NOT_IN_EN)):
        out.append("商品英文名稱不是新版 MS 寫法、沒有「Bonus Enhanced Notes」或「Equity Linked Notes」、或有 Fixed Coupon、Autocallable")
    if not out and zh_kinds != en_kinds:
        out.append(f"中文名稱是 {KIND_LABEL[zh_kinds[0]]}、英文名稱是 {KIND_LABEL[en_kinds[0]]}，型態不同")
    return (zh_kinds[0] if not out else None), out


def _names(doc: Document) -> tuple[str | None, str | None]:
    items = ms.cover_items(doc.before_chapter1())
    zh, en = (squash(items[n].value) if n in items else None for n in (4, 5))
    return zh, en


def articles(doc: Document) -> dict[int, Span]:
    return doc.articles(1, ARTICLES)


def article_sub(doc: Document, arts: dict[int, Span], n: int, k: int) -> list[Line]:
    return doc.span_lines(doc.subitems(arts.get(n)).get(k))


def detect(lines: Sequence[Line]) -> DetectionResult:
    """範本辨識（範本規格 §8）：MS 版面與 BEN 名稱（條件 1～3）都成立後，該型態的現行版特徵（條件 4）全部成立才是現行版；
    缺任一項 → 不支援的範本版本（錯訊列出缺的特徵）。"""
    doc = ms.document(lines)
    cover = doc.before_chapter1()
    items = ms.cover_items(cover)
    issuer = squash(items[10].value) if 10 in items else ""
    arts = articles(doc)
    art1 = squash(doc.lines[arts[1].start].text) if 1 in arts else ""
    evidence = [Evidence.of(x) for x in cover[:2]]
    family = (
        ("中文產品說明書" in TextIndex(cover).text, "封面缺少中文產品說明書"),
        (
            items.get(10) is not None and items[10].label == "發行機構" and "英商摩根士丹利國際股份有限公司" in issuer,
            "封面第 10 項發行機構不是英商摩根士丹利國際股份有限公司",
        ),
        (
            art1.startswith("1.商品中文名稱") and set(ARTICLES) <= set(arts),
            "第一章第 1 項不是「商品中文名稱」或條號不是 1～19、21～28",
        ),
        (all(n in doc.chapters for n in (1, 2, 3, 4)), "章名不是「第X章、」寫法或章節不完整"),
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


def current_problems(doc: Document, kind: Kind) -> list[str]:
    """該型態的現行版特徵（範本規格 §1、§8 條件 4）不成立的說明。"""
    arts = articles(doc)
    art10 = TextIndex(doc.span_lines(arts.get(10)), unify_brackets=True).text
    art15 = TextIndex(doc.span_lines(arts.get(15)), unify_brackets=True).text
    label = KIND_LABEL[kind]
    rate = re.search(PARTICIPATION_RATE, art15)
    checks = [
        (OPENING in art10, "§10 開頭不是「主要給付項目為到期贖回金額：」"),
        (art15.startswith("15." + TITLE15), "§15 標題不是「到期贖回計算公式、最低保證贖回率及參與率」"),
        ([k for k, _ in formula_of(art10)] == [kind], f"§10(a) 不是 {label} 的公式"),
        ([k for k, _ in formula_of(art15)] == [kind], f"§15(1)(a) 不是 {label} 的公式"),
        ("（2）" + MIN_RATE in art15, "§15(2) 最低保證贖回率不是「本商品不適用。…」"),
        (
            rate is not None and Decimal(rate[1]) == PARTICIPATION[kind],
            f"§15(3) 參與率不是 {label} 的 {PARTICIPATION[kind]}%",
        ),
        ("自動提前出場" not in TextIndex(doc.lines).text, "說明書有「自動提前出場」"),
    ]
    return [message for ok, message in checks if not ok]


# ---------------------------------------------------------------- §15 價格表

# 非 lookback：期初價格、執行價（表頭 %）；沒有 KO、下限價欄
PRICE_TABLE = replace(
    ms_tables.PRICE_TABLE,
    where="第一章第 15 項價格表",
    columns=(price_table.Column("strike", "執行價"),),
    code="代碼",  # 表頭「彭博(Bloomberg)代碼」
)
# lookback：只有期初價格（沒有執行價欄、沒有表頭 %），執行價由期初價格 × K% 推得（核對規則 §3.2）
LOOKBACK_TABLE = replace(PRICE_TABLE, columns=(), pct=None, pct_label="")
SCENARIO_TABLE = replace(PRICE_TABLE, where="第一章第 16 項重印價格表")
# lookback 的 §16 重印表：表頭寫回顧期初價格、回顧執行價（值是期初價格與期初價格 × K%）
LOOKBACK_SCENARIO_TABLE = replace(
    SCENARIO_TABLE,
    initial="回顧期初價格",
    columns=(price_table.Column("strike", "回顧執行價"),),
    pct=r"[（(]回顧期初價格的([\d.]+)%[）)]",
    pct_label="（回顧期初價格的X%）",
)
PRICE_PLACES = Decimal("0.0001")


def _price_table(art15: list[Line]) -> tuple[ParsedField, bool]:
    """§15 價格表（標題「期初價格…：依下表所示」到「(1) 紅利配息率」）與表上有沒有執行價欄（沒有 → lookback 寫法）。"""
    region = ms_tables.price_region(art15, ("（1）紅利配息率",))
    if region is None:
        note = "找不到第 15 項價格表（標題「期初價格…：依下表所示」到「(1) 紅利配息率」）"
        return ParsedField.missing("price_table", note), False
    title, lines = region
    has_strike = "執行價" in squash(title)
    spec = PRICE_TABLE if has_strike else LOOKBACK_TABLE
    return ms_tables.price_table("price_table", lines, spec, title=title), has_strike


def _with_strike(table: ParsedField, pct: ParsedField) -> ParsedField:
    """lookback 的價格表補上執行價欄：期初價格 × K%，四捨五入（half-up）到 4 位；表頭 % = K%（重印表表頭和它比）。"""
    if not table.ok or not pct.ok:
        return derived(table.name, table if not table.ok else pct)
    t: price_table.PriceTable = table.value
    rows = tuple(
        replace(r, prices={**r.prices, "strike": (r.prices["initial"] * pct.value / 100).quantize(PRICE_PLACES, ROUND_HALF_UP)})
        for r in t.rows
    )
    value = replace(t, columns=(*t.columns, "strike"), pcts={**t.pcts, "strike": pct.value}, rows=rows)
    note = "說明書沒有執行價欄（lookback）：執行價 = 期初價格 × 回顧執行價 %，四捨五入到 4 位"
    return ParsedField(table.name, FieldStatus.PRESENT, value, [*table.evidence, *pct.evidence], note=note)


def _prices(fields: dict[str, ParsedField], table: ParsedField) -> None:
    """價格表 → 標的、各標的價格（標的名稱取 §11 標的表，代碼依序相同才對得上）、主價格表。"""
    fields["underlyings"] = price_table.underlyings(table)
    art11, names = fields["underlyings_art11"], fields["underlying_names_art11"]
    tickers = list(table.value.tickers) if table.ok else []
    aligned = art11.ok and names.ok and art11.value == tickers and len(names.value) == len(tickers)
    fields["underlying_prices"] = price_table.price_rows(
        table, names=list(names.value) if aligned else [None] * len(tickers)
    )
    fields["main_price_table"] = derived(
        "main_price_table", table, standard_fields.TableAt(PRICE_TABLE.where, table.value) if table.ok else None
    )


# ---------------------------------------------------------------- 第 16 項情境（範本規格 §5）

CCY = money.CURRENCY
OPT = rf"(?:{CCY})?"  # 一般 BEN 情境一的金額沒寫幣別
MUL = "[×xX]"
N = r"([\d,]+(?:\.\d+)?)"
SN = r"(-?[\d,]+(?:\.\d+)?)"
SPCT = r"(-?\d+(?:\.\d+)?)%"
# 情境標題（以標題判斷，不以編號；核對規則 §3.8）：情境一～三計算、情境四只有文字
TITLES: dict[Kind, tuple[str, ...]] = {
    "maxi": ("較佳情況A", "較佳情況B", "最差情況", "發行機構無法履約"),
    "digital": ("較佳情況", "較佳情況", "最差情況", "發行機構無法履約"),
}
CONDITION = (
    rf"假設於期末定價日時(?:籃子中表現最差的|該)連結標的[（(]([A-Z0-9./-]+)[）)]之收盤價={N}{CCY}；(回顧)?執行價={N}{CCY}"
)
# 一般 BEN 情境一「= N x [100% + 參與率 x (P/I - 100%)] = A」
MAXI_AMOUNT = rf"={N}{OPT}{MUL}\[100%\+{PCT}{MUL}[（(]{N}{OPT}/{N}{OPT}-100%[）)]\]={N}{OPT}"
# 一般 BEN 情境二、Digital 情境一二「= N x (100% + x%) = A」
BONUS_AMOUNT = rf"={N}{OPT}{MUL}[（(]100%\+{PCT}[）)]={N}{OPT}"
RETURN = rf"=[（(]{N}{OPT}/{N}{OPT}[）)]-1={SPCT}[（(]年化報酬率[:：]{SPCT}[）)]"
SHARES = rf"=1{MUL}{N}{CCY}{MUL}{N}/{N}{CCY}={N}股"
WHOLE = r"在此例中為(\d+)股"
FRACTION = rf"畸零股數[（(]即{N}股[）)]"
REMAINDER = rf"={N}{CCY}{MUL}{N}/{N}={N}{CCY}"
VALUE = rf"=[（(]{N}{CCY}/{N}[）)]{MUL}(\d+)股\+{N}{CCY}={N}{CCY}"
# 損益：「= 價值 + 0 (總配息) - 面額 = 損益」（BEN 沒有配息）
PNL = rf"={N}{CCY}\+0(?:\.0+)?{CCY}-{N}{CCY}={SN}{CCY}"
ANNUALIZED = r"以計算年化報酬率為" + SPCT


class Condition(NamedTuple):
    """情境假設句「…連結標的(代碼)之收盤價 = P；(回顧)執行價 = K」。"""

    ticker: str  # 去空白
    close: Decimal
    lookback: bool
    strike: Decimal


class MaxiCalc(NamedTuple):
    """一般 BEN 情境一「= N x [100% + 參與率 x (P/I - 100%)] = A」。"""

    denomination: Decimal
    participation: Decimal
    close: Decimal
    initial: Decimal
    amount: Decimal


class BonusCalc(NamedTuple):
    """一般 BEN 情境二、Digital 情境一二「= N x (100% + x%) = A」。"""

    denomination: Decimal
    bonus: Decimal
    amount: Decimal


@dataclass
class MsBenScenarios:
    """情境一～三的算式；找不到、或在情境裡有多處時為 None（由規則轉人工覆核）。`titles` 為三個計算情境標題的原文行（情境不是
    剛好四個、標題不是該型態的寫法時三個都空）。情境一二付現（一般 BEN 情境一 `maxi`、其他 `bonus`），情境三交股。"""

    kind: Kind
    titles: tuple[list[Line], list[Line], list[Line]]
    conditions: dict[int, Hit[Condition] | None] = field(default_factory=dict)
    rows: dict[int, int | None] = field(default_factory=dict)  # 情境 → 最差標的是價格表第幾檔
    maxi: Hit[MaxiCalc] | None = None
    bonus: dict[int, Hit[BonusCalc] | None] = field(default_factory=dict)
    returns: dict[int, Hit[ProfitReturn] | None] = field(default_factory=dict)
    delivery: Delivery = field(default_factory=Delivery)

    @property
    def found(self) -> bool:
        return all(self.titles)


def _row_of(ticker: str | None, prices: ParsedField) -> int | None:
    """情境寫的最差標的（代碼）→ 價格表第幾檔；對不到為 None（共用規則轉人工覆核）。"""
    if not prices.ok or ticker is None:
        return None
    rows = [n for n, r in enumerate(prices.value, start=1) if r.ticker and r.ticker.replace(" ", "") == ticker]
    return rows[0] if len(rows) == 1 else None


def _scenarios(sec: ms_scenario.ScenarioSection, kind: Kind, prices: ParsedField) -> MsBenScenarios:
    empty = MsBenScenarios(kind, ([], [], []))
    if [s.title for s in sec.scenarios] != list(TITLES[kind]):
        return empty
    out = MsBenScenarios(kind, tuple(s.lines[:1] for s in sec.scenarios[:3]))  # type: ignore[arg-type]
    for n, s in enumerate(sec.scenarios[:3], start=1):
        ti = TextIndex(s.lines)

        def find(pattern: str, convert, ti=ti):
            return one(hits(ti, pattern, convert))

        cond = find(CONDITION, lambda m: Condition(m[1], dec(m[2]), m[3] is not None, dec(m[4])))
        out.conditions[n] = cond
        out.rows[n] = _row_of(cond.value.ticker if cond else None, prices)
        if n == 3:
            out.delivery = Delivery(
                shares=find(SHARES, lambda m: SharesCalc(dec(m[1]), dec(m[2]), dec(m[3]), dec(m[4]))),
                whole=find(WHOLE, lambda m: Decimal(m[1])),
                fraction=find(FRACTION, lambda m: dec(m[1])),
                remainder=find(REMAINDER, lambda m: RemainderCalc(dec(m[1]), dec(m[2]), dec(m[3]), dec(m[4]))),
                value=find(VALUE, lambda m: ValueCalc(dec(m[1]), Decimal(m[3]), dec(m[2]), dec(m[4]), dec(m[5]))),
                worse_pnl=find(PNL, lambda m: Pnl(dec(m[1]), dec(m[2]), dec(m[3]))),
                worse_annualized=find(ANNUALIZED, lambda m: Decimal(m[1])),
            )
            continue
        if kind == "maxi" and n == 1:
            out.maxi = find(
                MAXI_AMOUNT, lambda m: MaxiCalc(dec(m[1]), Decimal(m[2]), dec(m[3]), dec(m[4]), dec(m[5]))
            )
        else:
            out.bonus[n] = find(BONUS_AMOUNT, lambda m: BonusCalc(dec(m[1]), Decimal(m[2]), dec(m[3])))
        out.returns[n] = find(RETURN, lambda m: ProfitReturn(dec(m[1]), dec(m[2]), Decimal(m[3]), Decimal(m[4])))
    return out


def _reprint(art16: list[Line], sec: ms_scenario.ScenarioSection, lookback: bool) -> ParsedField:
    """§16 重印價格表：假設「▪ 參與率：X%」那一行之後、情境一之前（lookback 表頭寫回顧期初價格、回顧執行價）。"""
    name = "scenario_table"
    first = sec.scenarios[0].lines[0] if sec.scenarios else None
    ti = TextIndex(art16)
    found = [m for m in ti.finditer(r"▪參與率：" + PCT) if first is None or ti.lines_for(m.start(), m.end())[-1] != first]
    if len(found) != 1 or first is None:
        return ParsedField.missing(name, "找不到第 16 項重印價格表（假設「參與率」之後、情境一之前）")
    a = art16.index(ti.lines_for(found[0].start(), found[0].end())[-1]) + 1
    region = [x for x in art16[a : art16.index(first)] if squash(x.text) != "▪"]
    return ms_tables.price_table(name, region, LOOKBACK_SCENARIO_TABLE if lookback else SCENARIO_TABLE)


def _article16(fields: dict[str, ParsedField], kind: Kind, art16: list[Line], lookback: bool) -> tuple:
    """基本假設（總投資金額、年期、紅利、最低保證贖回率、參與率）、重印價格表、情境一～三的算式（範本規格 §5）。"""
    sec = ms_scenario.section(art16)
    heads = [s.lines[0] for s in sec.scenarios]
    end = art16.index(heads[0]) if heads else len(art16)
    ti = TextIndex(art16[:end], unify_brackets=True)
    where = "第 16 項假設"

    def assumed(name: str, pattern: str, convert=lambda m: Decimal(m[1])) -> ParsedField:
        return exactly_one(name, ti, pattern, f"{where}找不到「{pattern}」", convert)

    fields["bonus_assumed"] = assumed("bonus_assumed", r"▪紅利配息率：" + PCT)
    fields["participation_assumed"] = assumed("participation_assumed", r"▪參與率：" + PCT)
    fields["min_rate_assumed"] = exactly_one(
        "min_rate_assumed", ti, re.escape("▪" + MIN_RATE), f"{where}最低保證贖回率不是「本商品不適用。…」"
    )
    table = _reprint(art16, sec, lookback)
    spec = LOOKBACK_SCENARIO_TABLE if lookback else SCENARIO_TABLE
    fields["scenario_reprint_table"] = price_table.at("scenario_reprint_table", table, spec)
    fields["lookback_reprint"] = derived(
        "lookback_reprint", table, table.ok and table.value.heads.get("initial", "").startswith("回顧")
    )
    sc = _scenarios(sec, kind, fields["underlying_prices"])

    def value(code: str, h: Hit | None, note: str) -> ParsedField:
        if h is None:
            return ParsedField(code, FieldStatus.MISSING, evidence=[Evidence.of(x) for x in art16[:1]], note=note)
        return ParsedField.present(code, h.value, list(h.lines))

    fields["scenario_params"] = standard_fields.occurrences(
        "scenario_params",
        [
            ScenarioParam(
                "scenario_investment", where, value("scenario_investment", sec.denomination, f"{where}找不到總投資金額或出現多次")
            ),
            ScenarioParam("scenario_tenor", where, value("scenario_tenor", sec.tenor, f"{where}找不到年期或出現多次")),
        ],
    )
    prices = []
    for n in (1, 2, 3) if sc.found else ():
        tag = f"第 16 項情境{'一二三'[n - 1]}"
        cond = sc.conditions.get(n)
        k = Mention.of_optional_hit(f"{tag}假設的", cond, lambda v: v.strike, sc.titles[n - 1])
        prices.append(ScenarioPrice(f"{tag}假設的", sc.rows.get(n), "strike", k.parsed(f"scenario{n}_strike")))
        if n == 1 and kind == "maxi":
            i = Mention.of_optional_hit(f"{tag}算式的", sc.maxi, lambda v: v.initial, sc.titles[0])
            prices.append(ScenarioPrice(f"{tag}算式的", sc.rows.get(n), "initial", i.parsed("scenario1_initial")))
        if n == 3:
            s = Mention.of_optional_hit(f"{tag}股數算式的", sc.delivery.shares, lambda v: v.strike, sc.titles[2])
            prices.append(ScenarioPrice(f"{tag}股數算式的", sc.rows.get(n), "strike", s.parsed("scenario3_shares_strike")))
    fields["scenario_prices"] = standard_fields.occurrences("scenario_prices", prices)
    return sec, sc


# ---------------------------------------------------------------- 讀出


@dataclass
class MsBenTermSheet(ms.MsTermSheet):
    kind: Kind
    ben_scenarios: MsBenScenarios
    # 紅利、參與率的其他出處（正式值是 §15 的 `coupon_pa_pct`、`participation_pct`）
    bonus_mentions: list[Mention]
    participation_mentions: list[Mention]


# MS FCN 才有的欄位（日期表、配息期別與公式、第 17 項 KO 觀察與記憶、名稱記憶式）：BEN 讀不到，也不交給規則
FCN_ONLY = ("date_table", "coupon_range", "coupon_formula_pct", "ko_observation_art17", "name_memory")


def _lookback(fields: dict[str, ParsedField], indicators: dict[str, bool], evidence: list[Line]) -> None:
    """lookback 與否（核對規則 §3.1）：§14(9)、§15 回顧定義、§15(1) 比回顧執行價、價格表沒有執行價欄、§16 重印表表頭寫回顧
    期初價格，五處一起有或一起沒有；混雜 → 不合法（規則轉人工覆核）。"""
    values = set(indicators.values())
    if len(values) == 1:
        fields["lookback"] = ParsedField.present("lookback", values.pop(), evidence)
        return
    have = "、".join(k for k, v in indicators.items() if v)
    lack = "、".join(k for k, v in indicators.items() if not v)
    fields["lookback"] = ParsedField.invalid("lookback", evidence, f"lookback 寫法混雜：有 {have}；沒有 {lack}")


def _lookback_period(fields: dict[str, ParsedField], art14: list[Line]) -> bool:
    """§14(9) 回顧觀察期間：回顧結束日與起點寫法；沒有 §14(9) → 回顧結束日不適用。回傳有沒有 §14(9)。"""
    ti = TextIndex(art14, unify_brackets=True)
    found = list(ti.finditer(LOOKBACK_PERIOD))
    if not found and "回顧觀察期間" not in ti.text:
        fields["lookback_end_date"] = standard_fields.absent("lookback_end_date", "回顧觀察期間（不是 lookback）")
        return False
    if len(found) != 1:
        fields["lookback_end_date"] = ParsedField.invalid(
            "lookback_end_date", art14, "§14(9) 回顧觀察期間不是「…至YYYY年M月D日（包含）。」或出現多次"
        )
        fields["lookback_start"] = derived("lookback_start", fields["lookback_end_date"])
        return True
    m = found[0]
    lines = ti.lines_for(m.start(), m.end())
    end = parse_date(m[2])
    fields["lookback_end_date"] = (
        ParsedField.present("lookback_end_date", end, lines)
        if end
        else ParsedField.invalid("lookback_end_date", lines, f"「{m[2]}」不是日期")
    )
    fields["lookback_start"] = (
        ParsedField.present("lookback_start", m[1], lines)
        if m[1] == LOOKBACK_START
        else ParsedField.invalid("lookback_start", lines, f"§14(9) 起點寫「{m[1]}」，應為「{LOOKBACK_START}」")
    )
    return True


def read(lines: Sequence[Line]) -> MsBenTermSheet:
    """先照 MS FCN 讀出，再改讀 BEN 不同的條目（範本辨識另由 `detect` 負責；型態由名稱決定，辨識不出時照一般 BEN 讀，
    規則轉人工覆核）。"""
    base = ms.read(lines)
    doc, fields = base.document, dict(base.fields)
    arts = articles(doc)
    kind, _ = kind_of(*_names(doc))

    def article(n: int) -> list[Line]:
        return doc.span_lines(arts.get(n))

    for name in FCN_ONLY:
        fields.pop(name, None)
    fields["monthly_coupon_pct"] = standard_fields.absent("monthly_coupon_pct", "月配息率（BEN 沒有定期配息）")
    fields["ben_kind"] = (
        ParsedField.present("ben_kind", kind, doc.before_chapter1()[:2])
        if kind is not None
        else ParsedField.missing("ben_kind", "名稱看不出是一般 BEN 或 Digital")
    )
    kind = kind or "maxi"
    art10, art14, art15, art16 = article(10), article(14), article(15), article(16)
    ti10, ti15 = TextIndex(art10, unify_brackets=True), TextIndex(art15, unify_brackets=True)

    # §14(9) 回顧觀察期間；§15 價格表、定義、紅利、參與率
    # §14(1) 寫「商品年期：N 個月」（FCN 是「N 個月期」）
    fields["tenor_months"] = capture(
        "tenor_months", TextIndex(article_sub(doc, arts, 14, 1), unify_brackets=True), r"商品年期：(\d+)個月", int, whole_match=True
    )
    period = _lookback_period(fields, art14)
    table, has_strike = _price_table(art15)
    fields["price_table"] = table
    lookback = period
    if lookback:
        fields["strike_pct"] = exactly_one(
            "strike_pct", ti15, LOOKBACK_STRIKE_DEF, "§15 找不到「回顧執行價：…乘以X%。」", lambda m: Decimal(m[1])
        )
        fields["lookback_initial_definition"] = exactly_one(
            "lookback_initial_definition", ti15, re.escape(LOOKBACK_INITIAL_DEF), "§15 回顧期初價格定義不是固定寫法"
        )
    else:
        fields["strike_pct"] = exactly_one(
            "strike_pct", ti15, r"(?<!回顧)" + STRIKE_DEF, "§15 找不到「執行價：…期初價格的X%」", lambda m: Decimal(m[1])
        )
    main = table if has_strike else _with_strike(table, fields["strike_pct"])
    _prices(fields, main)
    if kind == "maxi":
        fields["worst_definition"] = exactly_one(
            "worst_definition", ti15, re.escape(WORST_DEF), "§15 最差表現連結標的定義不是固定寫法"
        )
    fields["coupon_pa_pct"] = exactly_one(
        "coupon_pa_pct", ti15, BONUS, "§15 找不到「(1) 紅利配息率：X%」", lambda m: Decimal(m[1])
    )
    fields["participation_pct"] = exactly_one(
        "participation_pct", ti15, PARTICIPATION_RATE, "§15 找不到「(3) 參與率：X%」", lambda m: Decimal(m[1])
    )
    fields["min_rate"] = exactly_one(
        "min_rate", ti15, re.escape("（2）" + MIN_RATE), "§15(2) 最低保證贖回率不是「本商品不適用。…」"
    )

    # 到期公式（§15(1) 開頭、(a)(b)；§10(a)(b) 要相同）、型態與 lookback 的各處寫法
    word = KIND_LABEL[kind] + ("（lookback）" if lookback else "")
    fields["formula_head"] = exactly_one("formula_head", ti15, re.escape(HEAD15), "§15(1) 開頭不是固定寫法")
    clause = re.escape(cash_clause(kind, lookback) + delivery_clause(kind, lookback))
    fields["maturity_formula"] = exactly_one("maturity_formula", ti15, clause, f"§15(1)(a)(b) 不是 {word} 的寫法")
    fields["maturity_formula_art10"] = exactly_one(
        "maturity_formula_art10", ti10, clause, f"§10(a)(b) 不是 {word} 的寫法（須和 §15(1) 相同）"
    )
    formulas = {"§10": formula_of(ti10.text), "§15": formula_of(ti15.text)}
    fields["formula_kinds"] = ParsedField.present(
        "formula_kinds", {where: [k for k, _ in f] for where, f in formulas.items()}, art15[:1]
    )
    _lookback(
        fields,
        {
            "§14(9) 回顧觀察期間": period,
            "§15 回顧期初價格與回顧執行價定義": LOOKBACK_INITIAL_DEF in ti15.text and bool(re.search(LOOKBACK_STRIKE_DEF, ti15.text)),
            "§15(1) 比回顧執行價": any(lb for _, lb in formulas["§15"]),
            "§15 價格表沒有執行價欄": table.ok and not has_strike,
        },
        art14[:1],
    )

    # 沒有 KO（§10 開頭句、第一章 28 條、全文沒有自動提前出場）、沒有定期配息、無 KI
    _no_autocall(fields, ti10, arts, base.full_text)
    fields["no_fixed_coupon"] = (
        ParsedField.present("no_fixed_coupon", True, art10[:1])
        if "固定配息" not in base.full_text.text
        else ParsedField.invalid("no_fixed_coupon", art10[:1], "說明書有「固定配息」")
    )
    _ki_type(fields, ti15, kind, lookback, base.full_text)

    # §16 假設、重印表、情境；lookback 第五處（重印表表頭）
    sec, scenarios = _article16(fields, kind, art16, lookback)
    if fields["lookback"].ok and fields["lookback_reprint"].ok and fields["lookback_reprint"].value != lookback:
        fields["lookback"] = ParsedField.invalid(
            "lookback", art16[:1], "lookback 寫法混雜：§16 重印表表頭" + ("寫" if not lookback else "沒寫") + "回顧期初價格"
        )
    # 一般 BEN 情境一的金額沒寫幣別（只有股價旁有，範本原樣）：讀不到金額時不交這一處
    currencies = [
        o
        for o in ms.currency_occurrences(fields, sec, 16)
        if not (kind == "maxi" and o.field == "情境一_currency" and not o.value.ok)
    ]
    fields["currency_others"] = standard_fields.occurrences("currency_others", currencies)
    bonus = [Mention.of_field("第 16 項假設「紅利配息率：X%」", fields["bonus_assumed"])]
    for n in (2,) if kind == "maxi" else (1, 2):
        bonus.append(
            Mention.of_optional_hit(
                f"第 16 項情境{'一二'[n - 1]}「(100% + X%)」", scenarios.bonus.get(n), lambda v: v.bonus, scenarios.titles[n - 1]
            )
        )
    participation = [Mention.of_field("第 16 項假設「參與率：X%」", fields["participation_assumed"])]
    return MsBenTermSheet(fields, base.full_text, doc, sec, [], kind, scenarios, bonus, participation)


def _no_autocall(fields: dict[str, ParsedField], ti10: TextIndex, arts: dict[int, Span], whole: TextIndex) -> None:
    """沒有 KO（核對規則 §3.5）：§10 開頭固定句、第一章 1～19、21～28 條（沒有自動提前出場條）、全文沒有「自動提前出場」，
    三者都成立才判定；判定後 KO 每期觀察、非記憶式、Non-Call = 天期，比價日只有期末定價日（下單資料格式 §4）。"""
    problems = []
    opening = exactly_one("no_autocall", ti10, re.escape(OPENING), "§10 開頭不是「主要給付項目為到期贖回金額：」")
    if not opening.ok:
        problems.append(opening.note)
    if set(arts) != set(ARTICLES):
        problems.append("第一章條號不是 1～19、21～28")
    if "自動提前出場" in whole.text:
        problems.append("說明書有「自動提前出場」")
    if problems:
        fields["no_autocall"] = ParsedField.invalid("no_autocall", ti10.lines[:3], "無法判定沒有 KO：" + "；".join(problems))
    else:
        fields["no_autocall"] = ParsedField("no_autocall", FieldStatus.PRESENT, True, list(opening.evidence))
    fields["opening_art10"] = derived("opening_art10", opening, True) if opening.ok else opening
    maturity_only(fields, fields["no_autocall"])


def _ki_type(fields: dict[str, ParsedField], ti15: TextIndex, kind: Kind, lookback: bool, whole: TextIndex) -> None:
    """無 KI（核對規則 §3.5）：全文沒有「觸及」、價格表沒有下限價欄、§15(1)(b) 直接比執行價（lookback：回顧執行價），三者都成立。"""
    problems = []
    if "觸及" in whole.text:
        problems.append("說明書有「觸及」")
    table = fields["price_table"]
    if table.ok and "ki" in table.value.columns:
        problems.append("價格表有下限價格欄")
    if delivery_opening(kind, lookback) not in ti15.text:
        problems.append(f"§15(1)(b) 不是直接比{strike_word(lookback)}")
    lines = ti15.lines[:3]
    if problems:
        note = "無法判定無 KI：" + "；".join(problems)
        fields["ki_type"] = ParsedField.invalid("ki_type", lines, note)
        fields["ki_pct"] = ParsedField.invalid("ki_pct", lines, note)
        return
    fields["ki_type"] = ParsedField.present("ki_type", "none", lines)
    fields["ki_pct"] = ParsedField("ki_pct", FieldStatus.NOT_APPLICABLE, evidence=[Evidence.of(x) for x in lines])
