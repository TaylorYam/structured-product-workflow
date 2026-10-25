"""自算風險等級（#238、ADR 0023）：照承辦的 Generator 試算表（「境外結構型商品承作檢核表」工作表）的公式算 FCN 的 RR。

算核對條件時一起算（`check_terms.recalculate`）：還沒鎖住的商品每次都算，和上一次不同才寫入 `workflow.risk_level`；
已鎖住的商品只在還沒有風險等級時算一次（本功能上線前就鎖住的），之後凍結、不再重算（更正已鎖住的商品也不重算），
留下承作當時的判斷。對外：
- `assess`：一檔的計算本身（不碰資料庫）。
- `recalculate_risk`：在核對條件重算的 transaction 裡算這幾檔，寫入有變的；`fill_missing_locked`：替還沒有風險等級的已鎖住商品算一次。
- `risk_levels`／`risk_level_of`：讀存好的結果（起單資料頁顯示；說明書／投資人須知的 RR 核對與寄信草稿用，#239）。
指數清單由 `product_terms.load_import_config` 讀設定檔 `config/risk_level.toml`（`ImportConfig.index_underlyings`）。

公式：總分 = 0.4×保本率分 + 0.2×年期分 + 0.3×結構複雜度分 + 0.1×流動性分，十進位計算。
- 保本率分：FCN 一律不保本（資料庫沒有保本率欄），固定 5。
- 年期分（`tenor_months`）：<=12 → 1、<=36 → 2、<=60 → 3、<=120 → 4、更長 5。
- 結構複雜度分 = 平均（標的種類分, 標的數量分, 波動度分）+ 提前贖回 1（FCN 一律可提前出場）。
  - 標的種類：照試算表只看 UL_1（空白算缺資料），在指數清單上是指數 3 分，否則是個股 4 分。清單與 UL_1 都去掉
    彭博最後的類別字（`sheet_cells.bbg_code`，例：`SPX Index` → `SPX`，同存進資料庫的代碼）、不分大小寫比對。
  - 標的數量（非空的 UL_1～UL_5）：1～4 檔各 1～4 分、5 檔 4 分、6 檔以上 5 分。
  - 波動度：各標的 200 日波動率的平均，<=15 → 1、<=30 → 2、<=45 → 3、<60 → 4、>=60 → 5。
- 流動性分 = 0.8×評等分 + 0.2×1（可贖回頻率一律 Daily）。評等取保證機構（評等表沒有這家上手的保證機構時取發行機構）
  在交易日的評等，三家評等機構中最好（分數最小）的一家：AAA／Aaa → 1；AA+、AA、Aa1、Aa2 → 2；AA-、A+、Aa3、A1 → 3；
  A、A-、A2、A3 → 4；BBB+、Baa1 以下 → 5。
- 轉等級（下限含）：<1.8 → RR1、<2.6 → RR2、<3.4 → RR3、<4.2 → RR4、其餘 RR5。總分剛好 3.4 是 RR4（試算表的浮點誤差不重現）。
資料時間點：股價檔取取價日＝交易日那一份（內容是交易日前一個交易日收盤的彭博 VOLATILITY_200D，和試算表一致），
沒有就取交易日以前最近的一份；評等用交易日查（`credit_ratings.RatingRows.on`）。標的代碼和股價檔逐字比對（同核對條件）。
算不出來（缺交易日、天期、標的、UL_1 或股價檔，標的不在股價檔、股價衝突或沒有 200 日波動率，上手認不出來或評等表查不到）：
交易日在 2026-10-30 以前填 RR4、狀態「回填」；之後（或沒有交易日）狀態「待算」，資料補齊後下一次重算時算出。原因都寫出缺什麼。
傳入的 `conn` 由 `database.connect` 建立，資料表版本已確認（#200），這裡不再檢查。
"""

from __future__ import annotations

import bisect
import datetime as dt
import json
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any

from sp_workflow.credit_ratings import GUARANTOR, ISSUER, MOODYS, RatingRows, load_rows
from sp_workflow.database import require_in_transaction
from sp_workflow.product_record import plain
from sp_workflow.sheet_cells import bbg_code
from sp_workflow.status import Risk

if TYPE_CHECKING:
    import psycopg

    from sp_workflow.product_terms import ImportConfig

# 狀態
DONE = Risk.DONE
BACKFILLED = Risk.BACKFILLED
PENDING = Risk.PENDING

BACKFILL_BEFORE = dt.date(2026, 10, 30)  # 交易日在這天以前、算不出來的填 RR4（回填）
BACKFILL_LEVEL = "RR4"
RISK_LEVELS = ("RR1", "RR2", "RR3", "RR4", "RR5")

PRINCIPAL_SCORE = 5  # 保本率分：FCN 不保本
EARLY_REDEMPTION_SCORE = 1  # 提前贖回：FCN 一律有
REDEMPTION_FREQUENCY_SCORE = 1  # 可贖回頻率：一律 Daily
INDEX_SCORE, STOCK_SCORE = 3, 4  # 標的種類：指數、個股

_TENOR = ((12, 1), (36, 2), (60, 3), (120, 4))  # 天期 <= 月數 → 分數；更長 5
_COUNT = {1: 1, 2: 2, 3: 3, 4: 4, 5: 4}  # 標的數量 → 分數；6 檔以上 5
_VOLATILITY = ((Decimal(15), 1), (Decimal(30), 2), (Decimal(45), 3))  # 平均 <= → 分數；<60 → 4；>=60 → 5
_VOLATILITY_TOP = Decimal(60)
_LEVELS = ((Decimal("1.8"), "RR1"), (Decimal("2.6"), "RR2"), (Decimal("3.4"), "RR3"), (Decimal("4.2"), "RR4"))
_LETTER_SCORES = {"AAA": 1, "AA+": 2, "AA": 2, "AA-": 3, "A+": 3, "A": 4, "A-": 4}  # 標準普爾、惠譽；其餘 5
_MOODYS_SCORES = {"Aaa": 1, "Aa1": 2, "Aa2": 2, "Aa3": 3, "A1": 3, "A2": 4, "A3": 4}  # 穆迪；其餘 5
_WORST = 5
_AVERAGE_QUANTUM = Decimal("0.000001")  # 存的平均波動度四捨五入到小數第 6 位（分數用沒有四捨五入的平均）


def index_key(code: str) -> str:
    """指數清單比對用的代碼：去掉彭博最後的類別字（`SPX Index` → `SPX`）、轉大寫。"""
    return bbg_code(code).upper()


# ---------- 計算（不碰資料庫） ----------


@dataclass(frozen=True)
class RiskTerms:
    """`workflow.product` 裡算風險等級要用的欄位；`issuer` 是上手代碼（認不出來時空字串）。"""

    tdcc: str
    issuer: str
    trade_date: dt.date | None
    tenor_months: int | None
    underlyings: tuple[str | None, ...]  # UL_1～UL_5


@dataclass(frozen=True)
class Volatility:
    """股價檔裡一個代碼的 200 日波動率（百分點，例 31.5）；`conflict` 是同一個代碼有好幾列、值不同（沒有存值）。"""

    value: Decimal | None
    conflict: bool = False


@dataclass(frozen=True)
class RiskDetail:
    """算出來（完成）時的明細：各項分數與用到的資料。保本率、提前贖回、可贖回頻率是固定值，不存。"""

    tenor_months: int
    tenor_score: int
    underlying_type_score: int  # 3 指數、4 個股（看 UL_1）
    underlying_count: int
    underlying_count_score: int
    volatilities: tuple[tuple[str, Decimal], ...]  # (代碼, 200 日波動率)，依 UL_1～UL_5
    average_volatility: Decimal  # 平均，存到小數第 6 位
    volatility_score: int
    rating_role: str  # 用的是發行機構或保證機構的評等
    ratings: tuple[tuple[str, str], ...]  # (評等機構, 評等)，交易日當時的
    rating_score: int  # 三家中最好的一家

    @property
    def structure_score(self) -> Decimal:
        """結構複雜度分（畫面顯示用；總分用 `total` 的精確算法）。"""
        return Decimal(self._structure_sum) / 3 + EARLY_REDEMPTION_SCORE

    @property
    def liquidity_score(self) -> Decimal:
        return Decimal("0.8") * self.rating_score + Decimal("0.2") * REDEMPTION_FREQUENCY_SCORE

    @property
    def total(self) -> Decimal:
        """總分。0.3 × 結構複雜度分 = 0.3 ×（三項和 ÷ 3 + 提前贖回）=（三項和 + 3 × 提前贖回）÷ 10：先約掉 3，才是精確的十進位。"""
        return (
            Decimal("0.4") * PRINCIPAL_SCORE
            + Decimal("0.2") * self.tenor_score
            + Decimal(self._structure_sum + 3 * EARLY_REDEMPTION_SCORE) / 10
            + Decimal("0.1") * self.liquidity_score
        )

    @property
    def _structure_sum(self) -> int:
        return self.underlying_type_score + self.underlying_count_score + self.volatility_score


@dataclass(frozen=True)
class RiskLevel:
    """一檔的風險等級。完成時有總分與明細；回填是 RR4、沒有明細；待算沒有等級。"""

    status: str  # 完成／回填／待算
    level: str | None  # RR1～RR5；待算時 None
    reason: str = ""  # 回填、待算時缺什麼
    price_date: dt.date | None = None  # 用的股價檔（取價日）
    total: Decimal | None = None
    detail: RiskDetail | None = None

    @property
    def label(self) -> str:
        """結果表的一格：RR4、RR4（回填）、待算。"""
        if self.status == DONE:
            return self.level or ""
        return f"{self.level}（{self.status}）" if self.level else self.status

    def describe(self) -> str:
        """計算明細（給作業人員跟承辦對帳）。"""
        head = f"風險等級：{self.level or '—'}（{self.status}）"
        source = f"股價檔（取價日）：{self.price_date or '沒有'}"
        detail = self.detail
        if detail is None:
            return "\n".join((head, f"原因：{self.reason}", source))
        structure, liquidity = detail.structure_score, detail.liquidity_score
        vols = "、".join(f"{code} {_number(value)}" for code, value in detail.volatilities)
        ratings = "、".join(f"{agency} {rating}" for agency, rating in detail.ratings)
        kind = "指數" if detail.underlying_type_score == INDEX_SCORE else "個股"
        return "\n".join(
            (
                head,
                f"總分 {_number(detail.total)} = 0.4×保本率 {PRINCIPAL_SCORE} + 0.2×年期 {detail.tenor_score}"
                f" + 0.3×結構複雜度 {_number(structure)} + 0.1×流動性 {_number(liquidity)}",
                f"・保本率：不保本，{PRINCIPAL_SCORE} 分",
                f"・年期：{detail.tenor_months} 個月，{detail.tenor_score} 分",
                f"・結構複雜度：平均（標的種類 {detail.underlying_type_score}、標的數量 {detail.underlying_count_score}、"
                f"波動度 {detail.volatility_score}）+ 提前贖回 {EARLY_REDEMPTION_SCORE} = {_number(structure)}",
                f"　- 標的種類：UL_1 是{kind}，{detail.underlying_type_score} 分",
                f"　- 標的數量：{detail.underlying_count} 檔，{detail.underlying_count_score} 分",
                f"　- 波動度：平均 {_number(detail.average_volatility)}（{vols}），{detail.volatility_score} 分",
                f"　- 提前贖回：有，{EARLY_REDEMPTION_SCORE} 分",
                f"・流動性：0.8×評等 {detail.rating_score} + 0.2×可贖回頻率 {REDEMPTION_FREQUENCY_SCORE}"
                f" = {_number(liquidity)}",
                f"　- 評等：{detail.rating_role} {ratings}，取最好的一家 {detail.rating_score} 分",
                source,
            )
        )


def assess(
    terms: RiskTerms,
    price_dates: Sequence[dt.date],
    volatilities: Mapping[tuple[dt.date, str], Volatility],
    ratings: RatingRows,
    indices: Collection[str],
) -> RiskLevel:
    """一檔的風險等級。`price_dates` 是已匯入的股價檔取價日（由小到大），`volatilities` 以 (取價日, 代碼) 查，
    `ratings` 是評等表，`indices` 是指數清單（彭博代碼）。"""
    problems: list[str] = []
    trade_date = terms.trade_date
    underlyings = [(n, code) for n, code in enumerate(terms.underlyings, 1) if code]
    if trade_date is None:
        problems.append("缺交易日")
    if terms.tenor_months is None:
        problems.append("缺天期（Tenor (m)）")
    first = terms.underlyings[0] if terms.underlyings else None
    if not underlyings:
        problems.append("缺標的")
    elif not first:
        problems.append("缺 UL_1（標的種類看第一檔標的）")
    price_date = None if trade_date is None else price_file_on(trade_date, price_dates)
    if trade_date is not None and price_date is None:
        problems.append(f"沒有取價日在交易日 {trade_date} 以前（含）的股價檔")
    found: list[tuple[str, Decimal]] = []
    for n, code in underlyings if price_date is not None else ():
        volatility = volatilities.get((price_date, code))
        where = f"UL_{n} {code}：股價檔（取價日 {price_date}）"
        if volatility is None:
            problems.append(f"{where}沒有這個代碼")
        elif volatility.conflict:
            problems.append(f"{where}這個代碼有好幾列、值不同")
        elif volatility.value is None:
            problems.append(f"{where}沒有 200 日波動率")
        else:
            found.append((code, volatility.value))
    role, rating, problem = _rating(terms, ratings)
    if problem:
        problems.append(problem)

    if problems:
        reason = "；".join(problems)
        if trade_date is not None and trade_date < BACKFILL_BEFORE:
            return RiskLevel(BACKFILLED, BACKFILL_LEVEL, reason, price_date)
        return RiskLevel(PENDING, None, reason, price_date)
    assert terms.tenor_months is not None and rating is not None and first
    average = sum(value for _, value in found) / len(found)
    by_agency = tuple(rating.items())
    detail = RiskDetail(
        tenor_months=terms.tenor_months,
        tenor_score=tenor_score(terms.tenor_months),
        underlying_type_score=INDEX_SCORE if _is_index(first, indices) else STOCK_SCORE,
        underlying_count=len(underlyings),
        underlying_count_score=count_score(len(underlyings)),
        volatilities=tuple(found),
        average_volatility=average.quantize(_AVERAGE_QUANTUM, rounding=ROUND_HALF_UP),
        volatility_score=volatility_score(average),
        rating_role=role,
        ratings=by_agency,
        rating_score=min(rating_score(agency, value) for agency, value in by_agency),
    )
    total = detail.total
    return RiskLevel(DONE, level_of(total), "", price_date, total, detail)


def price_file_on(trade_date: dt.date, price_dates: Sequence[dt.date]) -> dt.date | None:
    """取價日＝交易日的股價檔；沒有就取交易日以前最近的一份；都沒有時 None。"""
    i = bisect.bisect_right(price_dates, trade_date)
    return price_dates[i - 1] if i else None


def tenor_score(months: int) -> int:
    return next((score for limit, score in _TENOR if months <= limit), _WORST)


def count_score(count: int) -> int:
    return _COUNT.get(count, _WORST)


def volatility_score(average: Decimal) -> int:
    found = next((score for limit, score in _VOLATILITY if average <= limit), None)
    if found is not None:
        return found
    return 4 if average < _VOLATILITY_TOP else _WORST


def rating_score(agency: str, rating: str) -> int:
    """一家評等機構的評等 → 評等分（BBB+、Baa1 以下都是 5）。"""
    return (_MOODYS_SCORES if agency == MOODYS else _LETTER_SCORES).get(rating, _WORST)


def level_of(total: Decimal) -> str:
    """總分 → RR1～RR5（下限含：剛好 3.4 是 RR4）。"""
    return next((level for limit, level in _LEVELS if total < limit), "RR5")


def _is_index(code: str, indices: Collection[str]) -> bool:
    return index_key(code) in {index_key(c) for c in indices}


def _rating(terms: RiskTerms, ratings: RatingRows) -> tuple[str, dict[str, str] | None, str]:
    """(用的角色, 交易日的評等 {評等機構: 評等}, 問題)；評等表有這家上手的保證機構就用保證機構。"""
    issuer = terms.issuer
    if not issuer:
        return "", None, "認不出上手，查不到評等"
    keys = {entity.key for entity in ratings.entities}
    role = GUARANTOR if (issuer, GUARANTOR) in keys else ISSUER
    if (issuer, role) not in keys:
        return role, None, f"評等表沒有 {issuer} 的發行機構或保證機構"
    if terms.trade_date is None:
        return role, None, ""  # 缺交易日另外寫
    found = ratings.on(issuer, role, terms.trade_date)
    if found is None:
        return role, None, f"評等表沒有 {issuer} 的{role}在交易日 {terms.trade_date} 的評等"
    return role, found.by_agency(), ""


def _number(value: Decimal) -> str:
    """畫面上的數字：最多小數 4 位、去掉多餘的 0。"""
    return format(plain(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)), "f")


# ---------- 資料庫 ----------


def recalculate_risk(conn: psycopg.Connection, tdccs: Sequence[str], config: ImportConfig) -> dict[str, RiskLevel]:
    """算這幾檔（還沒鎖住的），和上一次不同的寫入；在呼叫端的 transaction 裡（商品條件列已由核對條件重算鎖住）。

    上手代碼用 `product_terms.issuer_code`（TDCC 前三碼，查不到時用發行機構的寫法反查），指數清單用 `config.index_underlyings`。
    """
    require_in_transaction(conn)
    stored = risk_levels(conn, list(tdccs))
    found = _assess_all(conn, tdccs, config)
    for tdcc, risk in found.items():
        if stored.get(tdcc) != risk:
            _write(conn, tdcc, risk)
    return found


def fill_missing_locked(conn: psycopg.Connection, config: ImportConfig, tdccs: Sequence[str] | None = None) -> int:
    """替還沒有風險等級的已鎖住商品（`tdccs` 有值時只看這幾檔）算一次，回傳寫了幾檔；在呼叫端的 transaction 裡。

    本功能上線前就鎖住的商品用這個補；寫了之後凍結，之後不再重算。不鎖商品條件列（已鎖住的條件只有更正會改，
    更正不重算風險等級），寫入用 `ON CONFLICT DO NOTHING`：別的連線同時補了同一檔時以先寫的為準。
    """
    require_in_transaction(conn)
    where, params = ("", ()) if tdccs is None else (" AND p.tdcc = ANY(%s)", (list(tdccs),))
    missing = [
        tdcc
        for (tdcc,) in conn.execute(
            "SELECT p.tdcc FROM workflow.product p LEFT JOIN workflow.risk_level r USING (tdcc)"
            " WHERE p.locked_at IS NOT NULL AND r.tdcc IS NULL" + where + " ORDER BY p.tdcc",
            params,
        )
    ]
    for tdcc, risk in _assess_all(conn, missing, config).items():
        _write(conn, tdcc, risk, overwrite=False)
    return len(missing)


def _assess_all(conn: psycopg.Connection, tdccs: Sequence[str], config: ImportConfig) -> dict[str, RiskLevel]:
    """這幾檔的風險等級（依 TDCC），不寫入。"""
    from sp_workflow.product_terms import issuer_code  # product_terms 會 import check_terms，那邊再 import 這裡

    if not tdccs:
        return {}
    terms = [
        RiskTerms(tdcc, issuer_code(tdcc, issuer, config), trade_date, tenor, tuple(underlyings))
        for tdcc, issuer, trade_date, tenor, *underlyings in conn.execute(
            "SELECT tdcc, issuer, trade_date, tenor_months, underlying_1, underlying_2, underlying_3, underlying_4,"
            " underlying_5 FROM workflow.product WHERE tdcc = ANY(%s) ORDER BY tdcc",
            (list(tdccs),),
        )
    ]
    price_dates = [d for (d,) in conn.execute("SELECT DISTINCT price_date FROM workflow.price_import ORDER BY 1")]
    volatilities = _volatilities(conn, terms, price_dates)
    ratings = load_rows(conn)
    return {t.tdcc: assess(t, price_dates, volatilities, ratings, config.index_underlyings) for t in terms}


def risk_levels(conn: psycopg.Connection, tdccs: Sequence[str] | None = None) -> dict[str, RiskLevel]:
    """存好的風險等級（`tdccs` 是 None 時全部）；還沒算過的檔不在結果裡。"""
    where, params = ("", ()) if tdccs is None else (" WHERE tdcc = ANY(%s)", (list(tdccs),))
    return {
        tdcc: RiskLevel(status, level, reason or "", price_date, total, None if detail is None else _detail(detail))
        for tdcc, status, level, reason, price_date, total, detail in conn.execute(
            "SELECT tdcc, status, risk_level, reason, price_date, total_score, detail FROM workflow.risk_level" + where,
            params,
        )
    }


def risk_level_of(conn: psycopg.Connection, tdcc: str) -> RiskLevel | None:
    """一檔存好的風險等級；還沒算過時 None。"""
    return risk_levels(conn, [tdcc]).get(tdcc)


def _volatilities(
    conn: psycopg.Connection, terms: Sequence[RiskTerms], price_dates: Sequence[dt.date]
) -> dict[tuple[dt.date, str], Volatility]:
    """每檔用到的那一份股價檔裡、它的標的的 200 日波動率。"""
    wanted = {
        (price_date, code)
        for t in terms
        if t.trade_date is not None and (price_date := price_file_on(t.trade_date, price_dates)) is not None
        for code in t.underlyings
        if code
    }
    if not wanted:
        return {}
    return {
        (price_date, code): Volatility(value, conflict)
        for price_date, code, value, conflict in conn.execute(
            "SELECT price_date, code, volatility_200d, conflict_rows IS NOT NULL FROM workflow.daily_price"
            " WHERE price_date = ANY(%s) AND code = ANY(%s)",
            (sorted({d for d, _ in wanted}), sorted({c for _, c in wanted})),
        )
    }


def _write(conn: psycopg.Connection, tdcc: str, risk: RiskLevel, *, overwrite: bool = True) -> None:
    """寫一檔；`overwrite` 是 False 時已經有的不動（補已鎖住的商品）。"""
    detail = None if risk.detail is None else json.dumps(_detail_json(risk.detail), ensure_ascii=False)
    conflict = (
        " ON CONFLICT (tdcc) DO UPDATE SET status = EXCLUDED.status, risk_level = EXCLUDED.risk_level,"
        " total_score = EXCLUDED.total_score, reason = EXCLUDED.reason, price_date = EXCLUDED.price_date,"
        " detail = EXCLUDED.detail, calculated_at = now()"
        if overwrite
        else " ON CONFLICT (tdcc) DO NOTHING"
    )
    conn.execute(
        "INSERT INTO workflow.risk_level (tdcc, status, risk_level, total_score, reason, price_date, detail)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)" + conflict,
        (tdcc, risk.status, risk.level, risk.total, risk.reason or None, risk.price_date, detail),
    )


def _detail_json(detail: RiskDetail) -> dict[str, Any]:
    """明細 → jsonb（數字存成字串，讀回來是同一個 Decimal）。"""
    return {
        "tenor_months": detail.tenor_months,
        "tenor_score": detail.tenor_score,
        "underlying_type_score": detail.underlying_type_score,
        "underlying_count": detail.underlying_count,
        "underlying_count_score": detail.underlying_count_score,
        "volatilities": [[code, str(value)] for code, value in detail.volatilities],
        "average_volatility": str(detail.average_volatility),
        "volatility_score": detail.volatility_score,
        "rating_role": detail.rating_role,
        "ratings": [list(pair) for pair in detail.ratings],
        "rating_score": detail.rating_score,
    }


def _detail(data: Mapping[str, Any]) -> RiskDetail:
    return RiskDetail(
        **{
            **data,
            "volatilities": tuple((code, Decimal(value)) for code, value in data["volatilities"]),
            "average_volatility": Decimal(data["average_volatility"]),
            "ratings": tuple((agency, rating) for agency, rating in data["ratings"]),
        }
    )
