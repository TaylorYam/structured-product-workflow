"""支援的商品種類與各自的 Non-Call、比價、配息門檻規則（#302、#304、#310、#367、#373、ADR 0027、0028、0029、0030）。

流程（`sp_workflow`：交易明細、起單建檔、人工維護、歷史回補、比價）與核對（說明書推得的 Non-Call 符不符合參考條件表的
商品種類，`rules/reference.non_call_product`）共用這一份；新增商品時在 `PRODUCT_RULES` 加一筆，訂它自己的規則。

商品條件（#434）：規則自己回答哪些欄固定成什麼（`fixed_choices`、`order_overrides`、`fixed_non_call`）、哪些必填或可以空白
（`required_fields`、`extra_required`）、哪些要空白（`blank_problem`）、空白怎麼補（`default_participation`、
`default_coupon_barrier`）、起單建檔與舊表的哪一欄要搬到哪（`moved_fields`、`ignored_prices`）、值合不合法
（`non_call_problem`、`fixed_choice_problem`、`participation_problem`）。流程端只問規則，不自己比商品名或讀下面的布林。
欄名用 `fields` 的商品條件畫面欄名。

比價政策與核對樣態（#435）：最終比價日算不算 KO（`final_date_can_ko`）、能不能提前出場（`no_early_exit`）、KO% 有值時
比價要不要待確認（`ko_needs_review`）、Non-Call 是不是一定等於天期（`non_call_is_tenor`）、參考條件表要不要核對參與率
（`has_term(PARTICIPATION_FIELD)`）與配息門檻（`has_term(COUPON_BARRIER_FIELD)`）、配息門檻空白時拿 K(%) 比還是轉人工
（`blank_coupon_barrier_uses_strike`）、辨識後只支援 KO 每期觀察＋非記憶式＋無 KI 的樣態（`only_periodic_no_ki`）。
比價（`sp_workflow.observation`）、辨識（`variants`）、參考條件表規則（`rules/reference`）都只問規則。
風險等級的提前贖回分一律 1（`sp_workflow.risk_level.EARLY_REDEMPTION_SCORE`，明文決策、所有商品同一套）不問規則；
那個決策改成依商品種類時，就是這裡的下一個問題（例：用 `no_early_exit`）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from .fields import (
    BARRIER_TYPE_FIELD,
    COUPON_BARRIER_FIELD,
    KI_BARRIER_FIELD,
    KO_BARRIER_FIELD,
    KO_TYPE_FIELD,
    LOOKBACK_FIELD,
    NON_CALL_FIELD,
    PARTICIPATION_FIELD,
)

FCN = "FCN"  # 商品種類（#201）
FCN_EXPRESS = "FCN_Express"  # 沒有 Non-Call 的每日記憶式 FCN（#302、ADR 0027）
SUPER_BULL = "Super Bull"  # 沒有配息、每期觀察、到期依最差標的對執行價結算（#310、ADR 0028）
BEN = (
    "BEN"  # 沒有配息、不會提前出場、沒有 KI，只在到期依最差標的對執行價結算；紅利門檻存配息門檻（#373、#406、ADR 0029）
)
DACN = "DACN"  # 每日計息：依所有標的 ≥ 配息門檻的天數比例配息，其他和 FCN 相同（#367、ADR 0030）


_MATURITY_ONLY_CHOICES = {KO_TYPE_FIELD: "Period End", BARRIER_TYPE_FIELD: "NONE"}  # 不會提前出場、沒有 KI
_DEFAULT_PARTICIPATION = Decimal(100)  # 起單建檔沒有參與率：BEN 預設 100，Digital 由作業人員改 0（#373）
# 只有某些商品種類有的欄 → 錯訊用的名稱（沒有的商品種類這欄要空白）
_OPTIONAL_TERMS = {PARTICIPATION_FIELD: "參與率", COUPON_BARRIER_FIELD: "配息門檻", LOOKBACK_FIELD: "回顧期間"}


@dataclass(frozen=True)
class ProductRule:
    """一種商品種類的條件規則。布林是規則的內部設定；流程端問下面的方法，不直接讀。"""

    name: str
    has_non_call: bool = True  # False：沒有 Non-Call，Guaranteed Periods (m) 一定是 0（FCN_Express）
    # False：最終比價日即使也存在比價日裡，也不算 KO、只做到期判定（Super Bull：出場表不含期末評價日，
    # 舊總表卻把它放在比價日_{天期}，#310）
    final_date_callable: bool = True
    # True：只在到期判定（BEN）：不會提前出場（Non-Call = 天期）、KO Type 一律 Period End、沒有 KI，
    # 起單資料匯入不管起單建檔填什麼都套這組；另有參與率（%）（#373、ADR 0029）
    maturity_only: bool = False
    # True：要有配息門檻（DACN，#367；BEN 的紅利門檻，#406）；起單建檔、舊總表沒有這欄時預設帶執行價。
    # False 的商品配息門檻要空白
    has_coupon_barrier: bool = False
    # True：參考條件表該列配息門檻空白時和「K(%)」比（DACN，2026-11-02 使用者決定 DACN-409-01）；False 的商品轉人工覆核
    blank_coupon_barrier_is_strike: bool = False
    # True：核對只支援 KO 每期觀察、非記憶式、無 KI 的樣態（Super Bull，#309 決策、ADR 0028）；只在到期判定的商品本來就是
    periodic_no_ki_only: bool = False

    def non_call_problem(self, months: int, tenor: int | None = None) -> str:
        """Guaranteed Periods (m) 不符合這種商品時的說明；符合時空字串。`tenor` 是天期（月），只在到期判定的商品要等於它，
        沒給時不看。"""
        if not self.has_non_call:
            return "" if months == 0 else f"{self.name} 沒有 Non-Call，要填 0"
        if self.non_call_is_tenor() and tenor is not None and months != tenor:
            return f"{self.name} 不會提前出場，要等於天期（{tenor}）"
        return "" if months >= 1 else "至少 1"

    # ---------- 比價政策（#435） ----------

    def non_call_is_tenor(self) -> bool:
        """Non-Call 一定等於天期（只在到期判定的商品，BEN）：`non_call_problem` 檢查、起單資料匯入套 `order_overrides`、
        核對辨識時參考條件表要符合（`variants.product_terms_problems`）。"""
        return self.maturity_only

    def no_early_exit(self, non_call: int | None, tenor: int | None) -> bool:
        """這檔不會提前出場、比價不觀察 KO：Non-Call 期間涵蓋整個天期（任何商品都是；`non_call_is_tenor` 的商品通過
        `non_call_problem` 後一定是）。Non-Call 或天期空白時 False。"""
        return non_call is not None and tenor is not None and non_call >= tenor

    def final_date_can_ko(self) -> bool:
        """最終比價日算不算 KO：Super Bull、BEN 不算，那天只做到期判定（#310、#373、ADR 0028）。"""
        return self.final_date_callable

    def ko_needs_review(self, ko_pct: object) -> bool:
        """KO% 有值時比價待確認、由人判斷會不會提前出場：只在到期判定的商品（BEN）預設沒有 KO，填了 KO% 才有 KO 機制（#406）。"""
        return ko_pct is not None and self.maturity_only

    # ---------- 核對（參考條件表與辨識，#435） ----------

    def blank_coupon_barrier_uses_strike(self) -> bool:
        """參考條件表該列配息門檻空白時改和「K(%)」比（DACN，DACN-409-01）；False：要配息門檻的商品轉人工覆核（BEN 一定要有值）。"""
        return self.blank_coupon_barrier_is_strike

    def only_periodic_no_ki(self) -> bool:
        """辨識後只支援 KO 每期觀察、非記憶式、無 KI 的樣態（Super Bull：#309 決策；BEN：只在到期判定，#375）；
        另外 `non_call_is_tenor` 的商品 Non-Call 要等於天期。"""
        return self.periodic_no_ki_only or self.maturity_only

    # ---------- 固定的欄 ----------

    def fixed_choices(self) -> dict[str, str]:
        """選項欄只能選這個值（畫面欄名 → 值）：只在到期判定的商品（BEN）KO Type 一律 Period End、Barrier Type NONE。"""
        return dict(_MATURITY_ONLY_CHOICES) if self.maturity_only else {}

    def fixed_choice_problem(self, name: str, value: object) -> str:
        """選項欄的值不是這種商品固定的值時的說明；不固定或相符時空字串。"""
        fixed = self.fixed_choices().get(name)
        return "" if fixed is None or value == fixed else f"{self.name} 不會提前出場、沒有 KI，要選 {fixed}"

    def order_overrides(self, tenor: object) -> dict[str, object]:
        """起單資料匯入時不管起單建檔填什麼都套的欄（畫面欄名 → 值；`tenor` 是讀到的天期，讀不到時 None）：
        只在到期判定的商品（BEN）Non-Call = 天期、KO Type、Barrier Type 固定、沒有 KI（#373）。"""
        if not self.maturity_only:
            return {}
        return {NON_CALL_FIELD: tenor, **self.fixed_choices(), KI_BARRIER_FIELD: None}

    def fixed_non_call(self) -> int | None:
        """Non-Call 一定是這個值時的值：沒有 Non-Call 的商品（FCN_Express）是 0；其他照填的（只在到期判定的商品
        要等於天期，由 `non_call_problem` 檢查、起單資料匯入由 `order_overrides` 帶）為 None。"""
        return None if self.has_non_call else 0

    # ---------- 必填與要空白的欄 ----------

    def optional_fields(self) -> tuple[str, ...]:
        """共同的必填欄裡這種商品可以空白的欄：只在到期判定的商品（BEN）預設沒有 KO，KO% 不必填（#406）。"""
        return (KO_BARRIER_FIELD,) if self.maturity_only else ()

    def extra_required(self) -> tuple[str, ...]:
        """共同的必填欄以外，這種商品也必填的欄：要配息門檻的商品（DACN、BEN）是配息門檻（#367、#406）。"""
        return (COUPON_BARRIER_FIELD,) if self.has_coupon_barrier else ()

    def required_fields(self, common: Sequence[str]) -> tuple[str, ...]:
        """這種商品的必填欄：共同的必填欄 `common` 扣掉 `optional_fields`，最後加上 `extra_required`。"""
        optional = self.optional_fields()
        return (*(name for name in common if name not in optional), *self.extra_required())

    def has_term(self, name: str) -> bool:
        """這種商品有這一欄：參與率、回顧期間只有只在到期判定的商品（BEN）有；配息門檻只有要配息門檻的商品有。
        不是這幾個只有部分商品有的欄時一律 True。"""
        if name in (PARTICIPATION_FIELD, LOOKBACK_FIELD):
            return self.maturity_only
        if name == COUPON_BARRIER_FIELD:
            return self.has_coupon_barrier
        return True

    def blank_problem(self, name: str) -> str:
        """這種商品沒有這一欄（要空白）時的說明；有這一欄時空字串。"""
        return "" if self.has_term(name) else f"{self.name} 沒有{_OPTIONAL_TERMS[name]}，要空白"

    def participation_problem(self, value: Decimal | int | None) -> str:
        """參與率（%）的問題：有參與率的商品（BEN）必填、不能是負數；沒有的商品要空白。沒問題時空字串。"""
        if not self.has_term(PARTICIPATION_FIELD):
            return "" if value is None else self.blank_problem(PARTICIPATION_FIELD)
        if value is None:
            return f"{self.name} 必填（一般 100、Digital 0）"
        return "不能是負數" if value < 0 else ""

    # ---------- 空白怎麼補、哪一欄搬到哪 ----------

    def default_participation(self) -> Decimal | None:
        """起單建檔沒有參與率：有參與率的商品（BEN）預設 100（#373）；其他 None。"""
        return _DEFAULT_PARTICIPATION if self.has_term(PARTICIPATION_FIELD) else None

    def default_coupon_barrier(self, strike_pct: object) -> object:
        """起單建檔、舊總表沒有配息門檻（#367）：要配息門檻的商品預設帶執行價（執行價空白時也空白），其他空白。"""
        return strike_pct if self.has_coupon_barrier else None

    def moved_fields(self) -> dict[str, str]:
        """起單建檔、舊總表的欄 → 這種商品實際存的欄：只在到期判定的商品（BEN）的 KO% 其實是紅利門檻，搬到配息門檻、
        KO% 空白（#406）。"""
        return {KO_BARRIER_FIELD: COUPON_BARRIER_FIELD} if self.maturity_only else {}

    def ignored_prices(self) -> frozenset[str]:
        """舊表上不讀的標的價格（`fields.PRICE_FIELDS` 的鍵）：KO% 搬走的商品（BEN）那格 KO 價是紅利門檻價（#406）。"""
        return frozenset(("ko",)) if KO_BARRIER_FIELD in self.moved_fields() else frozenset()


# 支援的商品種類（依加入的順序）
PRODUCT_RULES = {
    rule.name: rule
    for rule in (
        ProductRule(FCN),
        ProductRule(FCN_EXPRESS, has_non_call=False),
        ProductRule(SUPER_BULL, final_date_callable=False, periodic_no_ki_only=True),
        ProductRule(BEN, final_date_callable=False, maturity_only=True, has_coupon_barrier=True),
        ProductRule(DACN, has_coupon_barrier=True, blank_coupon_barrier_is_strike=True),
    )
}
_PRODUCT_NAMES = {name.upper(): name for name in PRODUCT_RULES}
SUPPORTED_PRODUCTS = "、".join(PRODUCT_RULES)  # 錯訊用：目前只做這些商品種類
# 任一商品種類另外必填的欄（依商品種類順序、不重複）：人工維護清單要列出來（#367）
EXTRA_REQUIRED = tuple(dict.fromkeys(name for rule in PRODUCT_RULES.values() for name in rule.extra_required()))


def rule_or_fcn(product: object) -> ProductRule:
    """商品種類（`PRODUCT_RULES` 的寫法）→ 規則；不支援、空白或不是文字時照 FCN 的規則（比價與核對分流的預設）。"""
    return PRODUCT_RULES.get(product, PRODUCT_RULES[FCN]) if isinstance(product, str) else PRODUCT_RULES[FCN]


def full_coupon_periods(non_call: int) -> int:
    """第一個可提前出場期出場時配整期息的期數：Non-Call 期間；沒有 Non-Call（FCN_Express）時第 1 期配整期息，為 1
    （workflow #305、#306）。"""
    return max(non_call, 1)


def product_name(text: object) -> str | None:
    """交易明細、起單建檔、參考條件表的 Product → 支援的商品種類的寫法（不分大小寫、不看頭尾空白）；不支援時 None。"""
    return _PRODUCT_NAMES.get(text.strip().upper()) if isinstance(text, str) else None
