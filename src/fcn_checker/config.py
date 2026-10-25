"""載入審查標準、參考條件表格式與上手編號對照設定（TOML）。"""

from __future__ import annotations

import datetime as dt
import re
import string
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from .ingestion import IngestionError
from .standard_fields import AGENT_PLACES, GUARANTOR_PLACES
from .trading_calendar import Market, TradingCalendar

# 商品名稱樣板可用的佔位符：天期、幣別，以及依說明書欄位填入的 memory（記憶式）、maxi（標的數 ≥ 2）、daily（KO 每日觀察）、
# autocall（Non-Call < 天期，BNP）、quanto（承作幣別 ≠ 標的報價幣別，DBS）、ki（有 KI，DBS、UBS），與 underlying（標的寫法：
# 1 檔填 single_*、2 檔以上填 basket_*）、ko_observation（KO 觀察方式：每日填 daily_*、每期填 periodic_*，UBS）
NAME_FLAGS = ("memory", "maxi", "daily", "autocall", "quanto", "ki")
NAME_PLACEHOLDERS = frozenset(
    {"tenor", "ccy_zh", "ccy"}
    | {f"{f}_{lang}" for f in (*NAME_FLAGS, "underlying", "ko_observation") for lang in ("zh", "en")}
)
# 發行機構全名比對時可另外忽略的寫法差異（審查標準 issuer_name_ignore）→ 結果上的說明
ISSUER_NAME_IGNORES = {"brackets": "括號（全形／半形）", "trailing_period": "英文名結尾的句點"}
RISK_LEVEL = "{level}"  # 風險等級寫法（審查標準 risk.level_formats）中填入 RRn 的位置


@dataclass(frozen=True)
class ProductNameTemplate:
    zh: str
    en: str
    memory_zh: str
    memory_en: str
    normalize_brackets: bool
    maxi_zh: str = ""
    maxi_en: str = ""
    daily_zh: str = ""
    daily_en: str = ""
    ignore_whitespace_en: bool = False
    single_zh: str = ""  # {underlying_zh}／{underlying_en}：標的 1 檔時填入
    single_en: str = ""
    basket_zh: str = ""  # 標的 2 檔以上時填入
    basket_en: str = ""
    autocall_zh: str = ""  # {autocall_zh}／{autocall_en}：Non-Call < 天期（會提前出場）時填入（BNP、SG）
    autocall_en: str = ""
    autocall_memory_zh: str = ""  # 會提前出場且記憶式時改填這個（有設定時；SG 中文「（記憶式自動提前出場）」）
    autocall_memory_en: str = ""
    ccy_aliases: dict[str, str] = field(
        default_factory=dict
    )  # {ccy} 的其他寫法：ISO 代碼 → 名稱用的代碼（SG：CNH → CNY）
    quanto_zh: str = ""  # {quanto_zh}／{quanto_en}：承作幣別和標的報價幣別不同時填入（DBS「Quanto」）
    quanto_en: str = ""
    ki_zh: str = ""  # {ki_zh}／{ki_en}：有 KI 時填入（DBS「及定價日觸發(歐式)」、UBS「（含觸及下限價事件）」）
    ki_en: str = ""
    # 標的 1 檔沒有樣本、名稱寫法未知：用了 {underlying_*} 而標的只有 1 檔時名稱轉人工覆核，single_* 可不設（DBS）
    single_unsupported: bool = False
    periodic_zh: str = ""  # {ko_observation_*}：KO 每期觀察時填入（每日時填 daily_*；UBS）
    periodic_en: str = ""

    def template(self, lang: str, tenor: int) -> str:
        """樣板（zh／en）；天期 1 個月時英文 `{tenor} Months`／`{tenor}Months` 改成單數 `Month`（只接受單數，Issue #343）。"""
        tpl = getattr(self, lang)
        return re.sub(r"(\{tenor\}\s*)Months\b", r"\1Month", tpl) if tenor == 1 else tpl

    def placeholders(self, lang: str) -> frozenset[str]:
        """樣板（zh／en）用到的佔位符名稱。"""
        return frozenset(name for _, name, _, _ in string.Formatter().parse(getattr(self, lang)) if name is not None)

    def flag_text(self, flag: str, lang: str) -> str:
        return getattr(self, f"{flag}_{lang}")

    def autocall_text(self, memory: bool, lang: str) -> str:
        """{autocall_<lang>}：會提前出場時的字樣；記憶式且有 autocall_memory_* 時用它。"""
        special = getattr(self, f"autocall_memory_{lang}")
        return special if memory and special else getattr(self, f"autocall_{lang}")

    def ccy_text(self, iso: str) -> str:
        return self.ccy_aliases.get(iso, iso)

    def ko_observation_text(self, daily: bool, lang: str) -> str:
        """{ko_observation_<lang>}：KO 每日觀察用 daily_*，每期觀察用 periodic_*。"""
        return getattr(self, f"{'daily' if daily else 'periodic'}_{lang}")

    def underlying_text(self, basket: bool, lang: str) -> str:
        """{underlying_<lang>} 的標的寫法：2 檔以上用 basket_*，1 檔用 single_*。"""
        return getattr(self, f"{'basket' if basket else 'single'}_{lang}")


@dataclass(frozen=True)
class IssuerStandard:
    """某上手適用的審查標準：上手專屬值已解析（固定警語有上手版本就用，否則用預設）。"""

    issuer: str
    fixed_warning: str
    # 固定警語的允許寫法：fixed_warning 與只換開頭句的版本（risk.fixed_warning_openings）
    fixed_warnings: tuple[str, ...]
    fixed_warning_occurrences: int
    risk_level_formats: tuple[str, ...]  # 風險等級的寫法，{level} 處為 RRn（risk.level_formats；沒列出的上手為【RRn】）
    issuer_name: str | None  # None → 審查標準沒有該上手的值，規則轉人工覆核
    issuer_name_ignore: frozenset[str]  # 發行機構全名比對時另外忽略的寫法差異（ISSUER_NAME_IGNORES 的子集）
    product_name: ProductNameTemplate | None
    distributor_name: str
    distributor_phone: str
    distributor_phone_equivalents: tuple[str, ...]  # 電話可接受的其他寫法（所有上手適用）
    distributor_address: str
    distributor_address_equivalents: tuple[str, ...]  # 地址可接受的其他寫法（所有上手適用）
    iis_fixed_warning_occurrences: int | None  # 投資人須知的固定警語次數；None → 審查標準沒有該上手的值
    distributor_name_equivalents: tuple[str, ...] = ()  # 名稱可接受的其他寫法（所有上手適用；SG 第二章簡稱）
    # 下列只有審查標準有該上手分節時才核對（BNP）
    guarantor_names: dict[str, str] = field(default_factory=dict)  # 出處（cover／ch1／ch2）→ 保證機構名稱
    guarantor_name_equivalents: tuple[str, ...] = ()  # 投資人須知相關機構的保證機構名稱其他寫法（SG）
    agent_names: dict[str, str] = field(default_factory=dict)  # 出處（cover／ch2）→ 總代理人名稱（SG）
    product_type: str | None = None  # 封面商品種類固定值
    minimum_issue_size: str | None = None  # 第四章發行不成立門檻的金額寫法
    # 最低金額出處欄位 → 單位面額的倍數（沒列為 1）；依幣別不同時為 {ISO 代碼: 倍數}（SG 最低申購金額）
    min_amount_multiples: dict[str, int | dict[str, int]] = field(default_factory=dict)
    osu_clients: bool = True  # 可否承作 OSU 客戶（issuer.<上手>.osu_clients；沒寫為可以，Issue #224）


@dataclass(frozen=True)
class ReviewStandard:
    version: int
    effective_date: dt.date
    approval_dates: tuple[dt.date, ...]  # 歷次審查通過日期，由舊到新
    chairman: str
    distributor_name: str
    distributor_phone: str
    distributor_address: str
    distributor_established: dt.date  # 受託或銷售機構設立日期（Issue #226）
    # 保本率敘述表示不保本的寫法（值以其中之一開頭，後面可接說明句；Issue #226）
    non_protected: tuple[str, ...]
    fees: dict[str, str]  # 第四章費用項目 → 費率區間
    issue_price_pct: Decimal
    risk_level: str
    fixed_warning: str  # 共用的固定警語（上手表沒有 fixed_warning 時用）
    fixed_warning_occurrences: int  # 說明書的固定警語次數（上手表沒有 fixed_warning_occurrences 時用）
    forbidden: tuple[str, ...]
    allowed_phrases: tuple[str, ...]
    currency_zh_to_iso: dict[str, str]
    denomination: dict[str, int]
    iis_pages: int  # 投資人須知必須剛好的頁數
    # 上手代號（小寫）→ 該上手表 [issuer.<上手>] 已檢查過的欄位值（欄位與讀法見 ISSUER_FIELDS）
    issuers: dict[str, dict[str, Any]] = field(default_factory=dict)
    distributor_phone_equivalents: tuple[str, ...] = ()
    distributor_address_equivalents: tuple[str, ...] = ()
    distributor_name_equivalents: tuple[str, ...] = ()
    # 交易日（參考條件表）在這天（含）以前的文件出現禁用語時特例通過（已通知上手改版，Issue #247）；None 不特例
    forbidden_exempt_until: dt.date | None = None
    # 交易日（參考條件表）在這天（含）以前的文件，受託對象與 OSU 客戶不一致時特例通過（上手改版前的舊範本，Issue #224）
    osu_exempt_until: dt.date | None = None

    def approval_date_on(self, trade_date: dt.date) -> dt.date | None:
        """交易日當天或之前最近一次的審查通過日期；交易日早於最早的日期時為 None。"""
        return next((d for d in reversed(self.approval_dates) if d <= trade_date), None)

    def for_issuer(self, issuer: str) -> IssuerStandard:
        """該上手適用的審查標準：上手表有的欄位用上手的值，沒有的用共用值或視為該上手沒有這項基準。"""
        v = self.issuers.get(issuer.lower(), {})
        warning = v.get("fixed_warning", self.fixed_warning)
        rest = warning[warning.find("。") + 1 :]  # 開頭句以後；有其他開頭寫法時載入已確認有句號
        return IssuerStandard(
            issuer=issuer,
            fixed_warning=warning,
            fixed_warnings=(
                warning,
                *(alt + rest for alt in v.get("fixed_warning_openings", ())),
                *v.get("fixed_warning_variants", ()),
            ),
            fixed_warning_occurrences=v.get("fixed_warning_occurrences", self.fixed_warning_occurrences),
            risk_level_formats=v.get("risk_level_formats", ("【" + RISK_LEVEL + "】",)),
            issuer_name=v.get("issuer_name"),
            issuer_name_ignore=v.get("issuer_name_ignore", frozenset()),
            product_name=v.get("product_name"),
            distributor_name=self.distributor_name,
            distributor_phone=self.distributor_phone,
            distributor_phone_equivalents=self.distributor_phone_equivalents,
            distributor_address=self.distributor_address,
            distributor_address_equivalents=self.distributor_address_equivalents,
            distributor_name_equivalents=self.distributor_name_equivalents,
            iis_fixed_warning_occurrences=v.get("iis_fixed_warning_occurrences"),
            guarantor_names=dict(v.get("guarantor_name", {})),
            guarantor_name_equivalents=v.get("guarantor_name_equivalents", ()),
            agent_names=dict(v.get("agent_name", {})),
            product_type=v.get("product_type"),
            minimum_issue_size=v.get("minimum_issue_size"),
            min_amount_multiples=dict(v.get("min_amount_multiples", {})),
            osu_clients=v.get("osu_clients", True),
        )


@dataclass(frozen=True)
class ImportFormat:
    """匯入格式：核對結果檔「回填後」固定的版面，比照公司原始待建檔表（Issue #203）。"""

    title: str  # 第 1 列 A1；B1 為留下的列裡最晚的交易日
    header_row: int
    checked_column: str  # 一律寫打勾寫法
    fallbacks: dict[str, str]  # 欄名 → 參考條件表沒有這欄時改取的欄
    columns: tuple[str, ...]  # 欄位與左右順序


@dataclass(frozen=True)
class ReferenceFormat:
    """參考條件表（多列表格）的版面與欄位對照，所有上手共用。"""

    version: int
    header_search_rows: int  # 表頭在第一張工作表的前幾列裡找（有配對欄 key_column 的那一列）
    key_column: str
    issuer_column: str  # 不是必要欄：表上有才確認列屬於哪家上手
    empty_value: str
    checked_value: str  # TS、IIS 欄的打勾寫法（核對沒問題）
    issuer_values: dict[str, str]  # 上手代號 → 發行機構欄的寫法
    columns: dict[str, str]  # Excel 欄名 → 標準欄位（要核對或回填的欄；同一標準欄位可有多個欄名）
    optional_columns: tuple[str, ...]  # columns 裡不是必要欄的欄名（表上沒有也照常核對）
    import_format: ImportFormat  # 核對結果檔「回填後」的版面（Issue #203）
    ko_observation_values: dict[str, str]
    ko_memory_values: dict[str, bool]
    ki_type_values: dict[str, str]
    initial_pricing_values: dict[str, str]  # 期初定價：開盤價／收盤價／VWAP → open／close／vwap
    # 標的不看順序的上手代號：表上 UL_n 與說明書同一組代號就相符，價格與 VWAP 回填依代號配對（Citi，Issue #220）
    underlyings_any_order: tuple[str, ...] = ()


def load_toml(path: Path, what: str) -> dict[str, Any]:
    if not path.is_file():
        raise IngestionError("config_not_found", f"找不到{what}設定檔：{path}")
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise IngestionError("config_invalid", f"{what}設定檔格式錯誤：{e}") from e


def _fail(message: str) -> IngestionError:
    return IngestionError("config_invalid", message)


def _text(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _text_value(v: Any, where: str) -> str:
    """一段不空白的文字。"""
    if not _text(v):
        raise _fail(f"審查標準 {where} 須是一段不空白的文字")
    return v


def _texts(v: Any, where: str) -> tuple[str, ...]:
    """一串不空白的文字。"""
    if not isinstance(v, list) or not v or not all(isinstance(x, str) and x for x in v):
        raise _fail(f"審查標準 {where} 須是一串不空白的文字")
    return tuple(v)


def _bool(v: Any, where: str) -> bool:
    if type(v) is not bool:
        raise _fail(f"審查標準 {where} 須是 true 或 false")
    return v


def _positive_int(v: Any, where: str) -> int:
    if type(v) is not int or v <= 0:
        raise _fail(f"審查標準 {where} 須是正整數")
    return v


def _name_ignores(v: Any, where: str) -> frozenset[str]:
    """發行機構全名比對另外忽略的寫法差異：ISSUER_NAME_IGNORES 的項目。"""
    unknown = sorted(set(_texts(v, where)) - set(ISSUER_NAME_IGNORES))
    if unknown:
        raise _fail(
            f"審查標準 {where} 有未知的項目：{'、'.join(unknown)}（可用：{'、'.join(sorted(ISSUER_NAME_IGNORES))}）"
        )
    return frozenset(v)


def _level_formats(v: Any, where: str) -> tuple[str, ...]:
    """風險等級的寫法：每個都剛好有一個 {level}。"""
    formats = _texts(v, where)
    if any(f.count(RISK_LEVEL) != 1 for f in formats):
        raise _fail(f"審查標準 {where} 的每個寫法都要剛好有一個 {RISK_LEVEL}")
    return formats


def _table(v: Any, where: str, what: str) -> dict[str, Any]:
    """不空的子表 [<where>]（`what` 為錯訊中表的說明）。"""
    if not isinstance(v, dict) or not v:
        raise _fail(f"審查標準 {where} 須是{what}（[{where}]）")
    return dict(v)


def _place_texts(places: Iterable[str]) -> Callable[[Any, str], dict[str, str]]:
    """機構名稱的出處（例：保證機構 cover／ch1／ch2、總代理人 cover／ch2）→ 不空白的文字。"""
    keys = tuple(places)

    def read(v: Any, where: str) -> dict[str, str]:
        table = _table(v, where, "一個表，每個鍵一段文字")
        bad = [k for k, x in table.items() if not _text(x)] + sorted(set(table) - set(keys))
        if bad:
            raise _fail(f"審查標準 {where} 的鍵須為 {'、'.join(keys)}，值為不空白的文字（有問題：{'、'.join(bad)}）")
        return table

    return read


def _multiple_ok(v: Any) -> bool:
    """倍數：正整數，或依幣別的表 {ISO 代碼: 正整數}。"""
    if type(v) is int:
        return v >= 1
    return (
        isinstance(v, dict)
        and bool(v)
        and all(isinstance(k, str) and len(k) == 3 and k.isupper() and type(x) is int and x >= 1 for k, x in v.items())
    )


def _min_amount_multiples(v: Any, where: str) -> dict[str, int | dict[str, int]]:
    """最低金額出處欄位 → 正整數倍數，或依承作幣別的 {ISO 代碼: 倍數}（SG 最低申購金額）。"""
    table = _table(v, where, "一個表，每個最低金額出處一個倍數")
    bad = [k for k, x in table.items() if not _multiple_ok(x)]
    if bad:
        raise _fail(
            f"審查標準 {where}.{bad[0]} 須是正整數（單位面額的倍數）或依幣別的表（例：{{ USD = 10, JPY = 1 }}）"
        )
    return table


def _product_name(n: dict[str, Any], where: str) -> ProductNameTemplate:
    tpl = ProductNameTemplate(
        zh=n["zh"],
        en=n["en"],
        memory_zh=n["memory_zh"],
        memory_en=n["memory_en"],
        normalize_brackets=bool(n.get("normalize_brackets", True)),
        maxi_zh=n.get("maxi_zh", ""),
        maxi_en=n.get("maxi_en", ""),
        daily_zh=n.get("daily_zh", ""),
        daily_en=n.get("daily_en", ""),
        ignore_whitespace_en=bool(n.get("ignore_whitespace_en", False)),
        single_zh=n.get("single_zh", ""),
        single_en=n.get("single_en", ""),
        basket_zh=n.get("basket_zh", ""),
        basket_en=n.get("basket_en", ""),
        autocall_zh=n.get("autocall_zh", ""),
        autocall_en=n.get("autocall_en", ""),
        autocall_memory_zh=n.get("autocall_memory_zh", ""),
        autocall_memory_en=n.get("autocall_memory_en", ""),
        ccy_aliases=dict(n.get("ccy_aliases", {})),
        quanto_zh=n.get("quanto_zh", ""),
        quanto_en=n.get("quanto_en", ""),
        ki_zh=n.get("ki_zh", ""),
        ki_en=n.get("ki_en", ""),
        single_unsupported=bool(n.get("single_unsupported", False)),
        periodic_zh=n.get("periodic_zh", ""),
        periodic_en=n.get("periodic_en", ""),
    )
    if any(not (isinstance(k, str) and isinstance(v, str) and v) for k, v in tpl.ccy_aliases.items()):
        raise _fail(f"審查標準商品名稱樣板 {where}.ccy_aliases 須是 ISO 代碼 → 名稱用代碼的文字表")
    for lang in ("zh", "en"):
        unknown = sorted(tpl.placeholders(lang) - NAME_PLACEHOLDERS)
        if unknown:
            raise _fail(
                f"審查標準商品名稱樣板 {where}.{lang} 有未知的佔位符："
                + "、".join(f"{{{u}}}" for u in unknown)
                + "（可用："
                + "、".join(f"{{{p}}}" for p in sorted(NAME_PLACEHOLDERS))
                + "）",
            )
        need = [k for k in (f"daily_{lang}", f"periodic_{lang}") if not getattr(tpl, k)]
        if f"ko_observation_{lang}" in tpl.placeholders(lang) and need:
            raise _fail(
                f"審查標準商品名稱樣板 {where}.{lang} 用了 {{ko_observation_{lang}}}，"
                f"但沒有設定 {'、'.join(need)}（KO 每日與每期觀察的寫法）",
            )
        needed = (f"basket_{lang}",) if tpl.single_unsupported else (f"single_{lang}", f"basket_{lang}")
        missing = [k for k in needed if not getattr(tpl, k)]
        if f"underlying_{lang}" in tpl.placeholders(lang) and missing:
            raise _fail(
                f"審查標準商品名稱樣板 {where}.{lang} 用了 {{underlying_{lang}}}，"
                f"但沒有設定 {'、'.join(missing)}（1 檔與 2 檔以上的標的寫法）",
            )
    return tpl


# 每家上手一張表 [issuer.<上手代號小寫>] 的欄位與讀法（schema 只在這裡宣告，Issue #128）：新上手只在審查標準加一張表，
# 不改程式。讀法收（值, 位置）回傳解析後的值；沒寫的欄位用共用值，或視為該上手沒有這項基準（見 ReviewStandard.for_issuer）。
ISSUER_FIELDS: dict[str, Callable[[Any, str], Any]] = {
    "issuer_name": _text_value,  # 發行機構中英文法人全名；沒有 → 規則轉人工覆核
    "issuer_name_ignore": _name_ignores,  # 全名比對另外忽略的寫法差異（ISSUER_NAME_IGNORES）
    "fixed_warning": _text_value,  # 該上手的固定警語版本；沒有 → [risk] fixed_warning
    "fixed_warning_occurrences": _positive_int,  # 說明書的固定警語次數；沒有 → [risk] fixed_warning_occurrences
    "fixed_warning_openings": _texts,  # 固定警語開頭句的其他寫法
    "fixed_warning_variants": _texts,  # 固定警語的其他完整寫法
    "risk_level_formats": _level_formats,  # 風險等級的寫法；沒有 → 【{level}】
    "iis_fixed_warning_occurrences": _positive_int,  # 投資人須知的固定警語次數；沒有 → 轉人工覆核
    "product_type": _text_value,  # 封面商品種類固定值；沒有 → 不核對
    "minimum_issue_size": _text_value,  # 發行不成立門檻的金額寫法；沒有 → 不核對
    "guarantor_name_equivalents": _texts,  # 投資人須知相關機構的保證機構名稱其他寫法
    "product_name": _product_name,  # 子表 [issuer.<上手>.product_name]：商品名稱樣板；沒有 → 轉人工覆核
    "guarantor_name": _place_texts(GUARANTOR_PLACES),  # 子表：出處 → 保證機構名稱；沒有 → 不核對
    "agent_name": _place_texts(AGENT_PLACES),  # 子表：出處 → 總代理人名稱；沒有 → 不核對
    "min_amount_multiples": _min_amount_multiples,  # 子表：最低金額出處 → 單位面額的倍數；沒列為 1
    "osu_clients": _bool,  # 可否承作 OSU 客戶；沒有 → 可以（Issue #224）
}

# 舊版面（每種基準一個表、底下分上手）的表 → [issuer.<上手>] 的欄位（Issue #128）；讀到舊版面時停止並提示轉換指令
LEGACY_TABLES: dict[tuple[str, ...], str] = {
    ("issuer_name",): "issuer_name",
    ("issuer_name_ignore",): "issuer_name_ignore",
    ("risk", "fixed_warning_by_issuer"): "fixed_warning",
    ("risk", "fixed_warning_occurrences_by_issuer"): "fixed_warning_occurrences",
    ("risk", "fixed_warning_openings"): "fixed_warning_openings",
    ("risk", "fixed_warning_variants"): "fixed_warning_variants",
    ("risk", "level_formats"): "risk_level_formats",
    ("iis", "fixed_warning_occurrences"): "iis_fixed_warning_occurrences",
    ("product_type",): "product_type",
    ("minimum_issue_size",): "minimum_issue_size",
    ("guarantor_name_equivalents",): "guarantor_name_equivalents",
    ("product_name",): "product_name",
    ("guarantor_name",): "guarantor_name",
    ("agent_name",): "agent_name",
    ("min_amount_multiples",): "min_amount_multiples",
}
MIGRATE_COMMAND = "fcn-config-migrate"  # 舊版面轉新版面的指令（fcn_checker.config_migrate）


def legacy_tables(d: dict[str, Any]) -> list[tuple[str, ...]]:
    """設定內容裡舊版面的表（例 ("risk", "level_formats")），依 LEGACY_TABLES 的順序。"""
    found = []
    for path in LEGACY_TABLES:
        node: Any = d
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, dict):
            found.append(path)
    return found


def _warning_forms(where: str, values: dict[str, Any], warning: str) -> None:
    """固定警語開頭句與其他完整寫法要和該上手的警語相容；寫法有誤時載入就回報。"""
    openings = values.get("fixed_warning_openings", ())
    if openings:
        if "。" not in warning or any(not v.endswith("。") for v in openings):
            raise _fail(f"審查標準 {where}.fixed_warning_openings：固定警語與每個開頭寫法都要有以「。」結尾的開頭句")
        own = warning[: warning.index("。") + 1]
        if len({own, *openings}) != len(openings) + 1:  # 重複的寫法會讓同一段警語算兩次
            raise _fail(f"審查標準 {where}.fixed_warning_openings 的開頭寫法重複或與原本的開頭相同")
    variants = values.get("fixed_warning_variants", ())
    if any(v.replace(" ", "") == warning.replace(" ", "") for v in variants) or len(set(variants)) != len(variants):
        raise _fail(f"審查標準 {where}.fixed_warning_variants 的寫法重複或與該上手的固定警語相同")


def _issuer_values(code: str, table: Any, shared_warning: str) -> dict[str, Any]:
    """[issuer.<上手>]：只能有 ISSUER_FIELDS 的欄位，每個欄位依它的讀法檢查。"""
    where = f"issuer.{code}"
    if not isinstance(table, dict):
        raise _fail(f"審查標準 {where} 須是一個表（[{where}]）")
    unknown = sorted(set(table) - set(ISSUER_FIELDS))
    if unknown:
        raise _fail(f"審查標準 {where} 有未知的欄位：{'、'.join(unknown)}（可用：{'、'.join(ISSUER_FIELDS)}）")
    values = {key: ISSUER_FIELDS[key](v, f"{where}.{key}") for key, v in table.items()}
    _warning_forms(where, values, values.get("fixed_warning", shared_warning))
    return values


def _issuers(d: Any, shared_warning: str) -> dict[str, dict[str, Any]]:
    """[issuer.<上手>] 全部的表；上手代號不分大小寫（存成小寫）。"""
    if not isinstance(d, dict):
        raise _fail("審查標準 issuer 須是每家上手一張表（[issuer.<上手代號小寫>]）")
    out: dict[str, dict[str, Any]] = {}
    for code, table in d.items():
        if code.lower() in out:
            raise _fail(f"審查標準 issuer.{code} 重複（上手代號不分大小寫）")
        out[code.lower()] = _issuer_values(code, table, shared_warning)
    return out


def _approval_dates(value: Any) -> tuple[dt.date, ...]:
    """distributor.approval_dates：至少一筆、都是日期、不重複；回傳由舊到新。"""
    where = "審查標準 distributor.approval_dates"
    if not isinstance(value, list) or not value:
        raise IngestionError("config_invalid", f"{where} 須列出至少一個審查通過日期，例如 [2025-12-18, 2026-06-11]")
    bad = [v for v in value if type(v) is not dt.date]
    if bad:
        raise IngestionError("config_invalid", f"{where} 只能放日期（YYYY-MM-DD），不能是：{bad[0]!r}")
    dup = sorted({d for d in value if value.count(d) > 1})
    if dup:
        raise IngestionError("config_invalid", f"{where} 有重複的日期：" + "、".join(d.isoformat() for d in dup))
    return tuple(sorted(value))


def _optional_date(value: Any, where: str) -> dt.date | None:
    """可不寫的日期；寫了就要是日期（YYYY-MM-DD）。"""
    if value is not None and (type(value) is not dt.date):
        raise _fail(f"審查標準 {where} 須是日期（YYYY-MM-DD）")
    return value


def _date(value: Any, where: str) -> dt.date:
    """必填的日期（YYYY-MM-DD）。"""
    if value is None:
        raise _fail(f"審查標準 {where} 須是日期（YYYY-MM-DD）")
    return _optional_date(value, where)


def _non_protected(value: Any) -> tuple[str, ...]:
    """principal_protection.non_protected：至少一個寫法、都是非空字串。"""
    if not isinstance(value, list) or not value or not all(isinstance(v, str) and v.strip() for v in value):
        raise _fail('審查標準 principal_protection.non_protected 須列出至少一個表示不保本的寫法，例如 ["不保本"]')
    return tuple(value)


def load_review_standard(path: Path) -> ReviewStandard:
    d = load_toml(path, "審查標準")
    legacy = legacy_tables(d)
    if legacy:
        shown = "、".join(f"[{'.'.join(t)}]" for t in legacy[:3])
        raise IngestionError(
            "config_outdated",
            f"審查標準設定檔 {path} 還是舊版面（{shown} 等每種基準一個表），現在改成每家上手一張表 [issuer.<上手>]。"
            f'請執行 {MIGRATE_COMMAND} "{path}" 轉換（原檔另存 .bak）後再試。',
        )
    try:
        return ReviewStandard(
            version=int(d["version"]),
            effective_date=d["effective_date"],
            approval_dates=_approval_dates(d["distributor"]["approval_dates"]),
            chairman=d["distributor"]["chairman"],
            distributor_name=d["distributor"]["name"],
            distributor_phone=d["distributor"]["phone"],
            distributor_phone_equivalents=tuple(d["distributor"].get("phone_equivalents", ())),
            distributor_address_equivalents=tuple(d["distributor"].get("address_equivalents", ())),
            distributor_name_equivalents=tuple(d["distributor"].get("name_equivalents", ())),
            distributor_address=d["distributor"]["address"],
            distributor_established=_date(d["distributor"]["established"], "distributor.established"),
            non_protected=_non_protected(d["principal_protection"]["non_protected"]),
            fees=dict(d["fees"]),
            issue_price_pct=Decimal(str(d["issue_price"]["pct"])),
            risk_level=d["risk"]["level"],
            fixed_warning=d["risk"]["fixed_warning"],
            fixed_warning_occurrences=int(d["risk"]["fixed_warning_occurrences"]),
            forbidden=tuple(d["wording"]["forbidden"]),
            allowed_phrases=tuple(d["wording"]["allowed_phrases"]),
            forbidden_exempt_until=_optional_date(d["wording"].get("exempt_until"), "wording.exempt_until"),
            osu_exempt_until=_optional_date(d.get("osu", {}).get("exempt_until"), "osu.exempt_until"),
            currency_zh_to_iso=dict(d["currency"]),
            denomination={k: int(v) for k, v in d["denomination"].items()},
            iis_pages=int(d["iis"]["pages"]),
            issuers=_issuers(d.get("issuer", {}), d["risk"]["fixed_warning"]),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise IngestionError("config_invalid", f"審查標準設定檔缺少或格式錯誤的項目：{e}") from e


def load_reference_format(path: Path) -> ReferenceFormat:
    d = load_toml(path, "參考條件表格式")
    try:
        cols = dict(d["columns"])
        layout = d["layout"]
        fmt = ReferenceFormat(
            version=int(d["version"]),
            header_search_rows=int(layout["header_search_rows"]),
            key_column=layout["key_column"],
            issuer_column=layout["issuer_column"],
            empty_value=layout["empty_value"],
            checked_value=layout["checked_value"],
            issuer_values=dict(d["issuer_values"]),
            columns=cols,
            optional_columns=tuple(str(h) for h in layout["optional_columns"]),
            import_format=_import_format(d["import_format"]),
            ko_observation_values=dict(d["values"]["ko_observation"]),
            ko_memory_values={k: bool(v) for k, v in d["values"]["ko_memory"].items()},
            ki_type_values=dict(d["values"]["ki_type"]),
            initial_pricing_values=dict(d["values"]["initial_pricing"]),
            underlyings_any_order=tuple(str(c) for c in d.get("underlyings", {}).get("any_order_issuers", [])),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise IngestionError("config_invalid", f"參考條件表格式設定檔缺少或格式錯誤的項目：{e}") from e
    _check_column_settings(fmt)
    return fmt


def _import_format(d: dict[str, Any]) -> ImportFormat:
    return ImportFormat(
        title=str(d["title"]),
        header_row=int(d["header_row"]),
        checked_column=str(d["checked_column"]),
        fallbacks={str(k): str(v) for k, v in d["fallbacks"].items()},
        columns=tuple(str(h) for h in d["columns"]),
    )


def _check_column_settings(fmt: ReferenceFormat) -> None:
    """optional_columns 要是 [columns] 的欄名；匯入格式的欄名不重複、表頭在第 1 列以下、涵蓋 checked_column 與
    fallbacks，且每個必要的標準欄位至少有一個欄名在匯入格式裡（核對與回填的值都會進「回填後」）。"""
    unknown = [h for h in fmt.optional_columns if h not in fmt.columns]
    if unknown:
        raise IngestionError(
            "config_invalid", f"參考條件表格式設定檔 optional_columns 有 [columns] 沒有的欄名：{'、'.join(unknown)}"
        )
    imp = fmt.import_format
    cols = imp.columns
    named = {fmt.columns[h] for h in cols if h in fmt.columns}
    optional = {fmt.columns[h] for h in fmt.optional_columns}
    absent = [h for h, std in fmt.columns.items() if std not in named and std not in optional]
    absent += [h for h in (imp.checked_column, *imp.fallbacks) if h not in cols]
    repeated = sorted({h for h in cols if cols.count(h) > 1}, key=cols.index)
    problems = [f"缺少「{h}」" for h in dict.fromkeys(absent)] + [f"「{h}」重複" for h in repeated]
    if imp.header_row < 2:
        problems.append("header_row 須在第 2 列以下（第 1 列是標題）")
    if problems:
        raise IngestionError("config_invalid", f"參考條件表格式設定檔的匯入格式{'、'.join(problems)}")


def load_issuer_prefixes(path: Path) -> dict[str, str]:
    """上手編號（三碼）→ 上手代號。"""
    d = load_toml(path, "上手編號對照")
    try:
        prefixes = {str(k): str(v) for k, v in d["prefixes"].items()}
    except (KeyError, TypeError, AttributeError) as e:
        raise IngestionError("config_invalid", f"上手編號對照設定檔缺少或格式錯誤的項目：{e}") from e
    bad = [k for k in prefixes if not (len(k) == 3 and k.isdigit())]
    if bad:
        raise IngestionError("config_invalid", f"上手編號必須是三位數字：{'、'.join(bad)}")
    return prefixes


def _holiday_dates(values: Iterable[object], what: str) -> frozenset[dt.date]:
    out = set()
    for v in values:
        if not isinstance(v, dt.date) or isinstance(v, dt.datetime):
            raise IngestionError("config_invalid", f"交易所假日表的{what}必須是日期（YYYY-MM-DD）：{v}")
        out.add(v)
    return frozenset(out)


def _holiday_date(value: object, what: str) -> dt.date:
    (d,) = _holiday_dates([value], what)
    return d


def load_exchange_holidays(path: Path) -> TradingCalendar:
    """載入交易所假日表；缺檔、格式錯誤或同一個交易所碼對到兩個市場時丟出 IngestionError。"""
    d = load_toml(path, "交易所假日表")
    try:
        version = int(d["version"])
        markets = tuple(
            Market(
                code=str(code),
                name=str(m["name"]),
                bloomberg_codes=frozenset(str(c).upper() for c in m["bloomberg_codes"]),
                covered_from=_holiday_date(m["covered_from"], f"{code} 涵蓋起日"),
                covered_through=_holiday_date(m["covered_through"], f"{code} 涵蓋迄日"),
                holidays=_holiday_dates(m["holidays"], f"{code} 休市日"),
            )
            for code, m in d["markets"].items()
        )
    except (KeyError, TypeError, AttributeError, ValueError) as e:
        raise IngestionError("config_invalid", f"交易所假日表設定檔缺少或格式錯誤的項目：{e}") from e
    seen: dict[str, str] = {}
    for m in markets:
        if m.covered_through < m.covered_from:
            raise IngestionError("config_invalid", f"交易所假日表 {m.code} 的涵蓋迄日早於起日")
        outside = sorted(h for h in m.holidays if not m.covers(h, h))
        if outside:
            raise IngestionError(
                "config_invalid", f"交易所假日表 {m.code} 有休市日不在涵蓋範圍內：{'、'.join(map(str, outside))}"
            )
        for c in m.bloomberg_codes:
            if c in seen:
                raise IngestionError("config_invalid", f"交易所碼 {c} 同時出現在 {seen[c]} 與 {m.code}")
            seen[c] = m.code
    return TradingCalendar(version, markets)
