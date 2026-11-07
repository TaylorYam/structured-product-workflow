"""商品名稱樣板（審查標準 `[issuer.<上手>.product_name]`、`[issuer.<上手>.product_names."<Product>"]`）：載入、組出預期名稱、
預期與實際比對（Issue #488）。核對規則 `standard.product_name` 在 `rules/product_name.py`，讀說明書事實決定旗標後交給這裡。

樣板語法（`zh`／`en` 字串）：

- `{佔位符}`：天期、幣別與依說明書事實填入的字樣，見 `PLACEHOLDERS`。
- 開頭 `*`：名稱前面多的字不核對，只比結尾（HSBC Super Bull 的「Maxi 」，#314）。
- 結尾 `[…]`：這一段可有可無（UBS 英文名稱結尾的「（Non Principal Guaranteed, …）」，workflow #475）。

`*`、`[`、`]` 是樣板的保留字，只能用在上面兩個位置（一個樣板最多各一處、不能巢狀），載入時檢查；現有名稱都沒有這三個字，
名稱本身真的出現時再加跳脫寫法。

比對前的正規化（每張表一個鍵，值是清單，可用的值固定）：

- `normalize_zh`：中文一律忽略空白；`brackets` 另外不計全形／半形括號（沒寫時預設 `["brackets"]`）。
- `normalize_en`：英文預設連續空白視為一個；`whitespace` 改成忽略所有空白與換行（PDF 換行處常黏字）。
"""

from __future__ import annotations

import re
import string
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from .ingestion import IngestionError
from .products import SUPPORTED_PRODUCTS, product_name
from .text import full_brackets, squash

LANGS = ("zh", "en")
# 依說明書事實填入的旗標：memory（記憶式）、maxi（標的數 ≥ 2）、daily（KO 每日觀察）、autocall（Non-Call < 天期，BNP）、
# quanto（承作幣別 ≠ 標的報價幣別，DBS）、ki（有 KI，DBS、UBS）、express（沒有 Non-Call，CA、HSBC）；有旗標時填 <旗標>_<語言>
FLAGS = ("memory", "maxi", "daily", "autocall", "quanto", "ki", "express")
# 樣板可用的佔位符：天期、幣別、旗標字樣，與 underlying（標的寫法：1 檔填 single_*、2 檔以上填 basket_*）、ko_observation
# （KO 觀察方式：每日填 daily_*、每期填 periodic_*，UBS）、payoff（到期給付：參與率 0 填 digital_*、其他填 enhanced_*，HSBC BEN）
PLACEHOLDERS = frozenset(
    {"tenor", "ccy_zh", "ccy", "payoff_zh", "payoff_en"}
    | {f"{f}_{lang}" for f in (*FLAGS, "underlying", "ko_observation") for lang in LANGS}
)
NORMALIZE = {"zh": {"brackets"}, "en": {"whitespace"}}  # normalize_<語言> 可用的值
DEFAULT_NORMALIZE_ZH = frozenset({"brackets"})  # normalize_zh 沒寫時的預設
ANY_PREFIX = "*"
_OPTIONAL = re.compile(r"\[([^\[\]]*)\]\Z")
# 舊版開關（審查標準 version 39 以前）→ 新寫法；出現時載入報錯並提示改寫
LEGACY_KEYS = {
    "normalize_brackets": 'normalize_zh = ["brackets"]（不寫也是這個預設）',
    "ignore_whitespace_en": 'normalize_en = ["whitespace"]',
    "ignore_prefix_en": '英文樣板開頭加 *（例：en = "*SuperBull Notes …"）',
    "optional_suffix_en": '英文樣板結尾加 [可有可無的一段]（例：en = "… Fixed Coupon[（Non Principal …）]"）',
}
# 樣板之外的字樣鍵（都是選填文字）
_TEXT_KEYS = tuple(
    f"{k}_{lang}"
    for k in (
        "maxi",
        "daily",
        "single",
        "basket",
        "autocall",
        "autocall_memory",
        "quanto",
        "ki",
        "periodic",
        "express",
        "digital",
        "enhanced",
    )
    for lang in LANGS
)


def _fail(message: str) -> IngestionError:
    return IngestionError("config_invalid", message)


class Parts(NamedTuple):
    """一個語言的樣板拆開：`*` 開頭、主體、結尾可有可無的一段（沒有為空字串）。"""

    any_prefix: bool
    body: str
    optional: str


def split(tpl: str) -> Parts:
    """樣板 → `Parts`；`*`、`[`、`]` 不在語法位置時留在主體裡（由載入時的 `_syntax` 擋下）。"""
    any_prefix = tpl.startswith(ANY_PREFIX)
    body = tpl[1:] if any_prefix else tpl
    m = _OPTIONAL.search(body)
    return Parts(any_prefix, body[: m.start()] if m else body, m[1] if m else "")


class Expected(NamedTuple):
    """預期名稱（不含 `*` 與可有可無的一段）與結尾可有可無的一段，都已填入佔位符；`any_prefix` 為樣板開頭有 `*`。"""

    name: str
    optional: str
    any_prefix: bool


@dataclass(frozen=True)
class NameTemplate:
    """一張名稱樣板表。`zh`／`en` 是含語法的樣板（見 module 說明），其餘是佔位符填入的字樣。"""

    zh: str
    en: str
    memory_zh: str
    memory_en: str
    normalize_zh: frozenset[str] = DEFAULT_NORMALIZE_ZH
    normalize_en: frozenset[str] = frozenset()
    maxi_zh: str = ""
    maxi_en: str = ""
    daily_zh: str = ""
    daily_en: str = ""
    single_zh: str = ""  # {underlying_zh}／{underlying_en}：標的 1 檔時填入
    single_en: str = ""
    basket_zh: str = ""  # 標的 2 檔以上時填入
    basket_en: str = ""
    autocall_zh: str = ""  # {autocall_zh}／{autocall_en}：Non-Call < 天期（會提前出場）時填入（BNP、SG）
    autocall_en: str = ""
    autocall_memory_zh: str = ""  # 會提前出場且記憶式時改填這個（有設定時；SG 中文「（記憶式自動提前出場）」）
    autocall_memory_en: str = ""
    # {ccy} 的其他寫法：ISO 代碼 → 名稱用的代碼（SG：CNH → CNY）
    ccy_aliases: Mapping[str, str] = field(default_factory=dict)
    quanto_zh: str = ""  # {quanto_zh}／{quanto_en}：承作幣別和標的報價幣別不同時填入（DBS「Quanto」）
    quanto_en: str = ""
    ki_zh: str = ""  # {ki_zh}／{ki_en}：有 KI 時填入（DBS「及定價日觸發(歐式)」、UBS「（含觸及下限價事件）」）
    ki_en: str = ""
    # 標的 1 檔沒有樣本、名稱寫法未知：用了 {underlying_*} 而標的只有 1 檔時名稱轉人工覆核，single_* 可不設（DBS）
    single_unsupported: bool = False
    periodic_zh: str = ""  # {ko_observation_*}：KO 每期觀察時填入（每日時填 daily_*；UBS）
    periodic_en: str = ""
    express_zh: str = (
        ""  # {express_zh}／{express_en}：沒有 Non-Call（FCN_Express）時填入（CA、HSBC，workflow #305、#306）
    )
    express_en: str = ""
    digital_zh: str = ""  # {payoff_zh}／{payoff_en}：參與率 0（BEN Digital）時填入，其他填 enhanced_*（HSBC BEN，#375）
    digital_en: str = ""
    enhanced_zh: str = ""
    enhanced_en: str = ""

    def placeholders(self, lang: str) -> frozenset[str]:
        """樣板（zh／en，含可有可無的一段）用到的佔位符名稱。"""
        return frozenset(name for _, name, _, _ in string.Formatter().parse(getattr(self, lang)) if name is not None)

    def expected(self, lang: str, values: Mapping[str, Any]) -> Expected:
        """預期名稱。天期 1 個月時英文 `{tenor} Months`／`{tenor}Months` 改成單數 `Month`（只接受單數，Issue #343）。"""
        p = split(getattr(self, lang))
        body = re.sub(r"(\{tenor\}\s*)Months\b", r"\1Month", p.body) if values.get("tenor") == 1 else p.body
        return Expected(body.format(**values), p.optional.format(**values), p.any_prefix)

    def values(self, *, tenor: int, ccy_zh: str, iso: str, flags: Mapping[str, bool]) -> dict[str, Any]:
        """佔位符的值：天期、幣別，與依旗標（`FLAGS` 加 memory；樣板沒用到的旗標不在 `flags` 裡）選的字樣。"""
        out: dict[str, Any] = {"tenor": tenor, "ccy_zh": ccy_zh, "ccy": self.ccy_aliases.get(iso, iso)}
        for flag in FLAGS:
            for lang in LANGS:
                out[f"{flag}_{lang}"] = getattr(self, f"{flag}_{lang}") if flags.get(flag) else ""
        for lang in LANGS:
            if flags.get("autocall"):  # 會提前出場且記憶式時改用 autocall_memory_*（SG「（記憶式自動提前出場）」）
                special = getattr(self, f"autocall_memory_{lang}")
                out[f"autocall_{lang}"] = (
                    special if flags.get("memory") and special else getattr(self, f"autocall_{lang}")
                )
            if "maxi" in flags:
                out[f"underlying_{lang}"] = getattr(self, f"{'basket' if flags['maxi'] else 'single'}_{lang}")
            if "daily" in flags:
                out[f"ko_observation_{lang}"] = getattr(self, f"{'daily' if flags['daily'] else 'periodic'}_{lang}")
            if "digital" in flags:  # 到期給付寫法（HSBC BEN：參與率 0 是 Digital，#375）
                out[f"payoff_{lang}"] = getattr(self, f"{'digital' if flags['digital'] else 'enhanced'}_{lang}")
        return out

    def matches(self, lang: str, expected: Expected, actual: str) -> tuple[bool, str]:
        """實際名稱是否符合預期，與容差說明。`*` 與 `[…]` 並用時，結尾是預期名稱或加上可有可無那一段都算。"""
        body, optional, any_prefix = expected
        if lang == "zh":
            brackets = "brackets" in self.normalize_zh
            norm = (lambda s: full_brackets(squash(s))) if brackets else squash
            tol = "忽略空白；全形／半形括號不計" if brackets else "忽略空白"
        elif "whitespace" in self.normalize_en:
            norm, tol = squash, "忽略所有空白與換行"
        else:
            norm, tol = (lambda s: re.sub(r"\s+", " ", s).strip()), "連續空白視為一個"
        wants = [norm(body)]
        if optional:
            wants.append(norm(body + optional))
            tol += f"；結尾的「{optional}」可有可無"
        got = norm(actual)
        if any_prefix:
            tol += "；名稱前面多的字不核對"
            return any(got.endswith(w) for w in wants), tol
        return got in wants, tol


def _syntax(tpl: str, where: str) -> None:
    """`*` 只能在開頭、`[…]` 只能在結尾，各最多一處、不能巢狀。"""
    _, body, optional = split(tpl)
    if ANY_PREFIX in body + optional:
        raise _fail(f"審查標準商品名稱樣板 {where} 的「*」只能放在開頭（名稱前面多的字不核對）")
    if "[" in body or "]" in body:
        raise _fail(f"審查標準商品名稱樣板 {where} 的「[…]」只能有一段、放在結尾（可有可無的一段），不能巢狀")


def _normalize(n: Mapping[str, Any], lang: str, where: str, default: frozenset[str]) -> frozenset[str]:
    v = n.get(f"normalize_{lang}")
    if v is None:
        return default
    allowed = NORMALIZE[lang]
    if not isinstance(v, list) or not all(isinstance(x, str) and x in allowed for x in v):
        raise _fail(f"審查標準商品名稱樣板 {where}.normalize_{lang} 須是清單，可用的值：{'、'.join(sorted(allowed))}")
    return frozenset(v)


def load(n: dict[str, Any], where: str) -> NameTemplate:
    """讀一張名稱樣板表（審查標準 `[issuer.<上手>.product_name]` 的讀法）。"""
    legacy = [k for k in LEGACY_KEYS if k in n]
    if legacy:
        raise _fail(
            f"審查標準商品名稱樣板 {where} 用了舊寫法："
            + "；".join(f"{k} 請改成 {LEGACY_KEYS[k]}" for k in legacy)
            + "（Issue #488）"
        )
    tpl = NameTemplate(
        zh=n["zh"],
        en=n["en"],
        memory_zh=n["memory_zh"],
        memory_en=n["memory_en"],
        normalize_zh=_normalize(n, "zh", where, DEFAULT_NORMALIZE_ZH),
        normalize_en=_normalize(n, "en", where, frozenset()),
        ccy_aliases=dict(n.get("ccy_aliases", {})),
        single_unsupported=bool(n.get("single_unsupported", False)),
        **{k: n.get(k, "") for k in _TEXT_KEYS},
    )
    if any(not (isinstance(k, str) and isinstance(v, str) and v) for k, v in tpl.ccy_aliases.items()):
        raise _fail(f"審查標準商品名稱樣板 {where}.ccy_aliases 須是 ISO 代碼 → 名稱用代碼的文字表")
    for lang in LANGS:
        _syntax(getattr(tpl, lang), f"{where}.{lang}")
        unknown = sorted(tpl.placeholders(lang) - PLACEHOLDERS)
        if unknown:
            raise _fail(
                f"審查標準商品名稱樣板 {where}.{lang} 有未知的佔位符："
                + "、".join(f"{{{u}}}" for u in unknown)
                + "（可用："
                + "、".join(f"{{{p}}}" for p in sorted(PLACEHOLDERS))
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
        missing = [k for k in (f"digital_{lang}", f"enhanced_{lang}") if not getattr(tpl, k)]
        if f"payoff_{lang}" in tpl.placeholders(lang) and missing:
            raise _fail(
                f"審查標準商品名稱樣板 {where}.{lang} 用了 {{payoff_{lang}}}，"
                f"但沒有設定 {'、'.join(missing)}（參與率 0 與其他的到期給付寫法）",
            )
    return tpl


def load_by_product(n: Any, where: str) -> dict[str, NameTemplate]:
    """商品種類（參考條件表 Product 的寫法，須是支援的商品種類）→ 名稱樣板（`[issuer.<上手>.product_names."<Product>"]`）。"""
    if not isinstance(n, dict) or not all(isinstance(v, dict) for v in n.values()):
        raise _fail(f'審查標準 {where} 須是「商品種類 → 名稱樣板」的子表（例：[{where}."Super Bull"]）')
    out = {}
    for key, tpl in n.items():
        product = product_name(key)
        if product is None or product != key:
            raise _fail(f"審查標準 {where} 的「{key}」不是支援的商品種類寫法（可用：{SUPPORTED_PRODUCTS}）")
        out[product] = load(tpl, f"{where}.{key}")
    return out
