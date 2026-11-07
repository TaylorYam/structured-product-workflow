"""審查標準的商品名稱核對（rule `standard.product_name`）：讀說明書事實決定名稱樣板的旗標，交給 `name_template` 組出預期
名稱與比對（Issue #488；之前在 `review_standard.py`）。"""

from __future__ import annotations

from .. import products
from ..schema import CheckResult, FieldStatus, Item, ParsedField
from ..schema import CheckStatus as S
from .kit import Check, Context, standard_field


def _quanto(ctx: Context, rows) -> bool | None:
    """承作幣別和任一檔標的報價幣別（價格旁的幣別字）不同；讀不到幣別字或對照不到 ISO 代碼時為 None。"""
    cz = standard_field(ctx, "currency_zh")
    to_iso = ctx.std.currency_zh_to_iso
    quotes = [to_iso.get(r.currency) if r.currency else None for r in rows]
    if not cz.ok or to_iso.get(cz.value) is None or None in quotes:
        return None
    return any(q != to_iso[cz.value] for q in quotes)


# 名稱樣板旗標 → (讀的說明書欄位, 判斷（判斷不出為 None）, 用到它的佔位符, 欄位不適用時的值（None：照常轉人工覆核）)
_NAME_FLAG_SOURCES = (
    ("maxi", "underlyings", lambda ctx, v: len(v) >= 2, {"maxi_zh", "maxi_en", "underlying_zh", "underlying_en"}, None),
    (
        "daily",
        "ko_observation",
        lambda ctx, v: v == "D",
        {"daily_zh", "daily_en", "ko_observation_zh", "ko_observation_en"},
        None,
    ),
    (
        "autocall",
        "autocall_schedule",
        lambda ctx, v: v.first_callable < v.periods,
        {"autocall_zh", "autocall_en"},
        None,
    ),
    ("quanto", "underlying_prices", lambda ctx, v: _quanto(ctx, v), {"quanto_zh", "quanto_en"}, None),
    ("ki", "ki_type", lambda ctx, v: v != "none", {"ki_zh", "ki_en"}, False),
    ("express", "autocall_schedule", lambda ctx, v: v.first_callable == 0, {"express_zh", "express_en"}, None),
    ("digital", "participation_pct", lambda ctx, v: v == 0, {"payoff_zh", "payoff_en"}, None),
)


# 結果說明「依…組出」：樣板用到這組佔位符時加上的說明書事實（依序）
_MESSAGE_FACTS = (
    ("underlying", "、標的數"),
    ("autocall", "、是否會提前出場"),
    ("ko_observation", "、KO 觀察方式"),
    ("quanto", "、是否為 Quanto"),
    ("ki", "、有無 KI"),
    ("express", "、有無 Non-Call"),
    ("payoff", "、參與率（到期給付型態）"),
)


def _name_flags(ctx: Context, used: frozenset[str]) -> tuple[dict[str, bool], ParsedField | None]:
    """名稱樣板的 maxi（標的數 ≥ 2；{underlying_*} 也依它選 1 檔或 2 檔以上的寫法）、daily（KO 每日觀察）、
    autocall（第一個可提前出場期早於到期，即 Non-Call < 天期）、quanto（承作幣別 ≠ 標的報價幣別）、ki（有 KI）、
    digital（參與率 0，{payoff_*} 依它選寫法）旗標；
    只讀樣板用到的說明書欄位。不依上手分支。"""
    flags: dict[str, bool] = {}
    for flag, field, test, names, not_applicable in _NAME_FLAG_SOURCES:
        if used & names:
            pf = standard_field(ctx, field)
            if not_applicable is not None and pf.status == FieldStatus.NOT_APPLICABLE:  # 例：無 KI 交成不適用
                flags[flag] = not_applicable
                continue
            if not pf.ok:
                return flags, pf
            value = test(ctx, pf.value)
            if value is None:
                return flags, ParsedField.invalid(field, [], f"判斷不出名稱樣板的「{flag}」旗標")
            flags[flag] = value
    return flags, None


def check(ctx: Context) -> list[CheckResult]:
    """用說明書的天期、幣別、是否記憶式（樣板有用到時再加標的數、KO 觀察方式）組出預期名稱後比對。

    名稱樣板依上手讀取審查標準 `issuer.<上手代號小寫>.product_name`，參考條件表 Product 另有樣板時用
    `product_names."<Product>"`（Super Bull，#311）；沒有樣板時轉人工覆核。比對的正規化與語法（`*` 開頭、`[…]` 結尾）見 `name_template`。
    """
    rid, issuer = "standard.product_name", ctx.issuer
    items = {"name_zh": Item.standard("中文商品名稱"), "name_en": Item.standard("英文商品名稱")}
    product = ctx.order.get("product")
    tpl = ctx.issuer_std.product_name_for(products.product_name(product.value) if product is not None else None)
    if tpl is None:
        return [
            Check(rid, field, items[field], ctx.document)
            .needs(pf)
            .result(
                S.REVIEW_REQUIRED,
                actual=pf.value,
                reason="standard_missing",
                message=f"審查標準沒有 {issuer} 的商品名稱樣板（issuer.{issuer.lower()}.product_name）",
            )
            for field, pf in ((f, standard_field(ctx, f)) for f in ("name_zh", "name_en"))
        ]
    tenor, cz, mem = (
        standard_field(ctx, "tenor_months"),
        standard_field(ctx, "currency_zh"),
        standard_field(ctx, "ko_memory"),
    )
    out = []
    for field in ("name_zh", "name_en"):
        pf = standard_field(ctx, field)
        check = Check(rid, field, items[field], ctx.document).needs(pf)
        # 組名稱要四個欄位都讀得到；結果的證據只掛名稱那一欄，守門時才把其他三個暫掛上去
        if (problem := check.needs(tenor, cz, mem).blocked) is not None:
            out.append(problem)
            continue
        iso = ctx.std.currency_zh_to_iso.get(cz.value)
        if iso is None:
            out.append(
                check.result(
                    S.REVIEW_REQUIRED,
                    actual=pf.value,
                    reason="currency_unknown",
                    message="幣別不在審查標準的對照表，無法組出預期名稱",
                )
            )
            continue
        lang = "zh" if field == "name_zh" else "en"
        used = tpl.placeholders(lang)
        flags, bad = _name_flags(ctx, used)
        if bad is not None:
            out.append(check.review(bad))
            continue
        if tpl.single_unsupported and flags.get("maxi") is False:
            out.append(
                check.result(
                    S.REVIEW_REQUIRED,
                    actual=pf.value,
                    reason="variant_not_covered",
                    message="單一標的沒有樣本，名稱寫法未知，請人工確認",
                )
            )
            continue
        flags["memory"] = bool(mem.value)
        expected = tpl.expected(lang, tpl.values(tenor=tenor.value, ccy_zh=cz.value, iso=iso, flags=flags))
        same, tol = tpl.matches(lang, expected, pf.value)
        facts = "".join(text for group, text in _MESSAGE_FACTS if {f"{group}_zh", f"{group}_en"} & used)
        out.append(
            check.compare(
                expected.name,
                pf.value,
                ok=same,
                tolerance=tol,
                message=f"依審查標準名稱樣板與說明書天期、幣別、是否記憶式{facts}組出",
            )
        )
    return out
