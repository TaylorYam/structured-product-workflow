"""讀商品條件（#204、#437）：資料庫裡一檔商品的條件、核對條件、標的價格、比價日、分公司配置與鎖住狀態。

「一檔商品的條件怎麼讀」只有這裡知道，讀取端各自從 `Stored` 轉成自己的形狀（#438）；寫入在 `product_terms`（ADR 0013 的核對寫回也經過它），標的價格只由 `underlying_prices` 寫（#492）。這個 module 不 import
寫入端與算核對條件、風險等級的 module（`product_terms`、`check_terms`、`risk_level`），所以它們都可以在 module 開頭
import 這裡，不必再用函式內的 import 繞開循環。對外：
- `read_many`：資料庫裡這幾檔（或全部）商品的 `Stored`，caller 自己挑要的欄；`read` 是只讀一檔的寫法。`lock=True` 時
  鎖住這幾檔的商品條件列，寫入、核對寫回與重算核對條件共用同一把鎖。
- `locked`：已鎖住了沒（`locked_at` 有值：TS 核對通過，或 migration 0006 之前就在資料庫）。
- `Stored` 的有效期初價格、有效執行價、原始執行價：lookback（#374）之後比價改寫了執行價，「這檔的執行價是多少」
  只在這裡回答一次；人工維護畫面顯示的回顧期初價格文字是 `lookback_text`（#446）。
  價格基準日（`Stored.price_basis`，ADR 0025）也是：比價與曝險換算收盤價都問它（#438）。
- 不讀整檔條件的具名查詢（SQL 照原本的呼叫端搬來；`locked_tdccs` 是風險等級補算那句的鎖住條件，#438）：`lock_product` 只鎖一檔的商品條件列（存續事件）；
  `locked_tdccs` 已鎖住的商品（風險等級補算）；`incomplete_locked` 已鎖住、還缺標的價格或比價日的商品（舊商品補資料）；
  `underlying_codes` 各檔連結的標的代碼（曝險分析）；`observed_exits_using` 拆股影響的已出場商品；`terms_imported` 每檔
  商品條件匯入的時間（作業進度）。
- `CheckTermsState`：上一次算的核對條件（含交叉比對的差異 `Difference`）。
- `issuer_code`：TDCC 前三碼 → 上手代號（對照沒有時用資料庫的發行機構寫法反查）。
標的價格一列的型別 `UnderlyingPrice` 在 `product_record`；價格欄的名字在 `fcn_checker.fields`（#433）。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from functools import cached_property
from typing import TYPE_CHECKING, Protocol, cast

from fcn_checker.fields import PRICE_SQL
from sp_workflow.database import require_in_transaction
from sp_workflow.product_record import PRODUCT_COLUMNS, UnderlyingPrice, fields_of, plain, price_at
from sp_workflow.status import ProductSource

if TYPE_CHECKING:
    import psycopg

# 交叉比對的項目（`check_terms`）
CURRENCY, TRADE_DATE, UF = "幣別", "交易日", "UF"
BLANK = "空白"  # 差異裡空白的值
_HUNDRED = Decimal(100)


# ---------- 核對條件 ----------


@dataclass(frozen=True)
class Difference:
    """交易明細和起單資料的一個差異；`released` 是作業人員放行過同樣的差異。"""

    item: str  # 幣別／交易日／UF
    order_value: str
    trade_value: str  # 交易列之間不一樣時以「、」分隔
    released: bool = False

    @property
    def key(self) -> tuple[str, str, str]:
        return self.item, self.order_value, self.trade_value

    def values_text(self) -> str:
        """兩邊的值；UF 另外寫出交易明細換算成百分點的值。"""
        shown = self.trade_value
        if self.item == UF:
            shown += f"（換算 {_uf_points(self.trade_value)}）"
        return f"起單資料 {self.order_value}、交易明細 {shown}"

    def describe(self) -> str:
        return f"{self.item}不一致：{self.values_text()}"


def _uf_points(trade_value: str) -> str:
    """交易明細的 UF（比例）換成百分點，給錯訊看。"""
    return "、".join(t if t == BLANK else format(plain(Decimal(t) * _HUNDRED), "f") for t in trade_value.split("、"))


@dataclass(frozen=True)
class CheckTermsState:
    """一檔商品上一次算的核對條件（`workflow.check_terms`）。"""

    status: str
    reason: str  # 沒有原因時空字串
    price_date: dt.date | None  # 用的股價檔（取價日）；VWAP 或還在等股價時 None
    calculated_at: dt.datetime
    differences: tuple[Difference, ...] = ()  # 交叉比對的差異（含放行過的）


# ---------- 資料庫裡的這檔 ----------


@dataclass(frozen=True)
class Stored:
    """資料庫裡的一檔商品（`read_many`）：商品條件的欄與畫面的欄、來源、鎖住，和它的核對條件、標的價格、比價日、
    分公司配置；caller 自己挑要的。"""

    tdcc: str
    columns: dict[str, object]  # `PRODUCT_COLUMNS` → 值
    source: str
    isin: str | None
    locked_at: dt.datetime | None
    locked_reason: str  # 已鎖住時給作業人員看的原因；空字串＝還沒鎖住（判斷用 `locked`）
    check: CheckTermsState | None  # 還沒算過核對條件時 None
    prices: tuple[UnderlyingPrice, ...]  # 各標的的價格，依標的序號；沒有時空的
    autocall: dict[int, dt.date | None] | None  # 第幾個比價日 → 日期；沒回填過時 None
    branches: tuple[tuple[str, Decimal], ...]  # 分公司配置（分公司, 承作金額），依序；沒有時空的

    @cached_property
    def fields(self) -> dict[str, object]:
        """畫面的欄 → 值（`product_record.fields_of`）；用到才換算（進度頁一次讀全部商品，不需要）。"""
        return fields_of(self.columns)

    @property
    def underlyings(self) -> tuple[str | None, ...]:
        """UL_1～UL_5 的代碼（空白的是 None）。"""
        return tuple(self.columns[f"underlying_{n}"] for n in range(1, 6))

    def price(self, seq: int) -> UnderlyingPrice | None:
        """第幾個標的的價格；沒有這個標的的價格時 None。"""
        return next((p for p in self.prices if p.seq == seq), None)

    def effective_initial(self, seq: int) -> Decimal | None:
        """有效期初價格：比價寫過回顧期初價格（lookback，#374）就用它，否則進場價；表現的分母（#404）。"""
        price = self.price(seq)
        return None if price is None else price.effective_initial

    def effective_strike(self, seq: int) -> Decimal | None:
        """有效執行價：資料庫存的執行價。lookback 的回顧期間結束後，比價已改寫成回顧期初價格 × 執行價(%)（#374），
        重算核對條件時保留；比價、曝險用這個。"""
        price = self.price(seq)
        return None if price is None else price.strike

    def price_basis(self, seq: int) -> dt.date | None:
        """價格基準日（ADR 0025）：這個標的的價格含到哪天生效的拆股；拆股登記沒調整過時是 Strike date，都沒有時 None。
        比價與曝險換算收盤價都從這天起算（#438）。"""
        price = self.price(seq)
        basis = None if price is None else price.basis
        return basis or cast("dt.date | None", self.columns.get("strike_date"))

    def original_strike(self, seq: int) -> Decimal | None:
        """原始執行價：TS 寫的期初執行價。lookback 寫過回顧期初價格的標的是進場價 × 執行價(%)，其他同有效執行價；
        核對（`check_source`）比這個。"""
        price = self.price(seq)
        if price is None:
            return None
        pct = self.columns.get("strike_pct")
        if price.lookback is not None and price.initial is not None and pct is not None:
            return price_at(price.initial, cast("Decimal", pct))
        return price.strike


def read_many(conn: psycopg.Connection, tdccs: Sequence[str] | None = None, *, lock: bool = False) -> dict[str, Stored]:
    """資料庫裡這幾檔（`tdccs` 是 None 時全部）商品的條件，依 TDCC 排；資料庫沒有的檔不在結果裡（#204）。

    各表在同一個 transaction 裡讀。`lock`：依 TDCC 的順序鎖住這幾檔的商品條件列（`FOR UPDATE`）再讀，在呼叫端的
    transaction 裡執行，鎖到它結束。寫入商品條件、核對寫回與重算核對條件都先這樣讀：同時處理同一檔時，後到的
    等先到的寫完，讀到的是寫完的狀態。
    """
    if lock:
        require_in_transaction(conn)
    # 進度頁每次讀全部商品（#204 量過）：整批 fetchall、各表分開讀、關聯表不在資料庫排序（文字排序慢），在這裡排
    where, params = ("", ()) if tdccs is None else (" WHERE tdcc = ANY(%s)", (list(tdccs),))
    with conn.transaction():
        rows = conn.execute(
            f"SELECT tdcc, {', '.join(PRODUCT_COLUMNS)}, source, isin, locked_at FROM workflow.product{where}"
            " ORDER BY tdcc" + (" FOR UPDATE" if lock else ""),  # 依 TDCC 的順序鎖，同時鎖多檔時不會互等
            params,
        ).fetchall()
        checks = {
            tdcc: CheckTermsState(
                status, reason or "", price_date, calculated_at, tuple(Difference(*d) for d in differences or ())
            )
            for tdcc, status, reason, price_date, calculated_at, differences in conn.execute(
                f"SELECT tdcc, status, reason, price_date, calculated_at, differences FROM workflow.check_terms{where}",
                params,
            ).fetchall()
        }
        prices: dict[str, list[UnderlyingPrice]] = {}
        for tdcc, *values in sorted(
            conn.execute(
                f"SELECT tdcc, seq, code, {PRICE_SQL}, lookback_price,"
                " price_basis_date FROM workflow.underlying_price" + where,
                params,
            ).fetchall()
        ):
            prices.setdefault(tdcc, []).append(UnderlyingPrice(*values))
        autocall: dict[str, dict[int, dt.date | None]] = {}
        for tdcc, slot, value in conn.execute(
            f"SELECT tdcc, slot, value FROM workflow.autocall_date{where}", params
        ).fetchall():
            autocall.setdefault(tdcc, {})[slot] = value
        branches: dict[str, list[tuple[str, Decimal]]] = {}
        for tdcc, _, branch, amount in sorted(
            conn.execute(f"SELECT tdcc, seq, branch, amount FROM workflow.branch_allocation{where}", params).fetchall()
        ):
            branches.setdefault(tdcc, []).append((branch, amount))
    n = len(PRODUCT_COLUMNS)
    found = {}
    for tdcc, *row in rows:
        columns = dict(zip(PRODUCT_COLUMNS, row[:n], strict=True))
        source, isin, locked_at = row[n:]
        found[tdcc] = Stored(
            tdcc=tdcc,
            columns=columns,
            source=source,
            isin=isin,
            locked_at=locked_at,
            locked_reason=_locked_reason(source, locked_at),
            check=checks.get(tdcc),
            prices=tuple(prices.get(tdcc, ())),
            autocall=autocall.get(tdcc),
            branches=tuple(branches.get(tdcc, ())),
        )
    return found


def lookback_text(stored: Stored | None) -> str:
    """畫面上的回顧期初價格（#374）：「UL_1 代碼 價格、…」；還沒算出時空字串。"""
    if stored is None:
        return ""
    return "、".join(
        f"UL_{p.seq} {p.code} {format(plain(p.lookback), 'f')}" for p in stored.prices if p.lookback is not None
    )


def read(conn: psycopg.Connection, tdcc: str, *, lock: bool = False) -> Stored | None:
    """資料庫裡這檔目前的條件與鎖住狀態（`read_many` 只讀一檔）；沒有這檔時 None。`lock` 同 `read_many`。"""
    return read_many(conn, [tdcc], lock=lock).get(tdcc)


def lock_product(conn: psycopg.Connection, tdcc: str) -> bool:
    """只鎖住這檔的商品條件列、不讀條件（同 `read(…, lock=True)` 的鎖，到呼叫端的 transaction 結束）；回傳資料庫有沒有
    這檔。寫存續事件時同一檔一個一個來（`lifecycle`）。"""
    require_in_transaction(conn)
    return conn.execute("SELECT 1 FROM workflow.product WHERE tdcc = %s FOR UPDATE", (tdcc,)).fetchone() is not None


def locked_tdccs(conn: psycopg.Connection, tdccs: Sequence[str] | None = None) -> list[str]:
    """已鎖住的商品（`tdccs` 有值時只看這幾檔），依 TDCC 排；不讀條件、不鎖列（`locked` 的 SQL 寫法）。"""
    where, params = ("", ()) if tdccs is None else (" AND tdcc = ANY(%s)", (list(tdccs),))
    return [
        tdcc
        for (tdcc,) in conn.execute(
            "SELECT tdcc FROM workflow.product WHERE locked_at IS NOT NULL" + where + " ORDER BY tdcc", params
        )
    ]


def underlying_codes(conn: psycopg.Connection) -> list[tuple[str, str]]:
    """每檔商品連結的標的代碼（TDCC, 代碼），不重複、不含空白的欄；曝險分析選標的用（`exposure.load_choices`）。"""
    columns = ", ".join(f"underlying_{n}" for n in range(1, 6))
    return conn.execute(
        f"SELECT DISTINCT tdcc, code FROM workflow.product, unnest(ARRAY[{columns}]) AS code WHERE code IS NOT NULL"
    ).fetchall()


def observed_exits_using(conn: psycopg.Connection, code: str, day: dt.date, observed: str) -> list[str]:
    """用到 `code`、Strike date 早於 `day`、有還有效的存續事件而且全部來自 `observed`（比價）的商品，依 TDCC 排；
    拆股登記改了要重新比價的已出場商品（`stock_splits.affected_exits`）。"""
    rows = conn.execute(
        "SELECT p.tdcc FROM workflow.product p"
        " WHERE p.strike_date < %(day)s"
        " AND EXISTS (SELECT 1 FROM workflow.underlying_price u WHERE u.tdcc = p.tdcc AND u.code = %(code)s)"
        " AND EXISTS (SELECT 1 FROM workflow.product_event e WHERE e.tdcc = p.tdcc AND e.superseded_at IS NULL)"
        " AND NOT EXISTS (SELECT 1 FROM workflow.product_event e WHERE e.tdcc = p.tdcc AND e.superseded_at IS NULL"
        "                 AND e.source <> %(auto)s)"
        " ORDER BY p.tdcc",
        {"day": day, "code": code, "auto": observed},
    )
    return [tdcc for (tdcc,) in rows]


def incomplete_locked(conn: psycopg.Connection) -> list[str]:
    """已鎖住、還沒有標的價格或比價日的商品（舊商品補資料要補的，`legacy_backfill`），依 TDCC 排。"""
    return [
        tdcc
        for (tdcc,) in conn.execute(
            "SELECT p.tdcc FROM workflow.product p WHERE p.locked_at IS NOT NULL AND ("
            " NOT EXISTS (SELECT 1 FROM workflow.underlying_price u WHERE u.tdcc = p.tdcc)"
            " OR NOT EXISTS (SELECT 1 FROM workflow.autocall_date a WHERE a.tdcc = p.tdcc)) ORDER BY p.tdcc"
        )
    ]


def terms_imported(conn: psycopg.Connection) -> dict[str, dt.datetime | None]:
    """每檔商品條件匯入的時間（作業進度顯示，#186）：Order 匯入、清單更新、歷史回補（#303，鎖住的時間）或最後一次
    人工維護存檔。"""
    return dict(
        conn.execute(
            "SELECT p.tdcc, CASE p.source WHEN 'Order' THEN o.imported_at WHEN '清單更新' THEN r.imported_at"
            " WHEN '歷史回補' THEN p.locked_at"
            " ELSE (SELECT max(c.corrected_at) FROM workflow.term_correction c WHERE c.tdcc = p.tdcc) END"
            " FROM workflow.product p"
            " LEFT JOIN workflow.order_import o ON o.import_id = p.order_import_id"
            " LEFT JOIN workflow.import_run r ON r.run_id = p.first_run_id"
        ).fetchall()
    )


def _locked_reason(source: str, locked_at: dt.datetime | None) -> str:
    if locked_at is None:
        return ""
    if source == ProductSource.INVENTORY:
        return "以前清單更新匯入的商品，條件已鎖住，要改請到「7 人工維護」"
    if source == ProductSource.BACKFILL:
        return "歷史回補的商品，條件已鎖住，要改請到「7 人工維護」"
    return "已核對通過，條件已鎖住，要改請到「7 人工維護」"


def locked(stored: Stored | None) -> bool:
    """這檔已鎖住（`workflow.product.locked_at` 有值）；資料庫沒有這檔時 False。

    只用 SQL 篩選的地方（`locked_tdccs`、`incomplete_locked` 的 `locked_at IS NOT NULL`，寫入端
    `product_terms.missing_products` 的 `locked_at IS NULL`）照同一個定義。
    """
    return stored is not None and bool(stored.locked_reason)


# ---------- 上手代號 ----------


class IssuerLookup(Protocol):
    """`issuer_code` 用到的兩張對照；`import_config.ImportConfig` 就是一份（核對讀資料庫另組一份，`check_source`）。"""

    @property
    def issuer_prefixes(self) -> Mapping[str, str]: ...  # TDCC 前三碼 → 上手代號

    @property
    def issuer_names(self) -> Mapping[str, str]: ...  # 上手代號 → 發行機構的寫法


def issuer_code(tdcc: str, issuer: str, config: IssuerLookup) -> str:
    """上手代號：TDCC 前三碼；對照沒有時用資料庫的發行機構寫法反查（上手不改，只用來檢查與顯示）。

    風險等級（`risk_level`）與核對讀資料庫的評等欄（`check_source`）都用這一個。
    """
    code = config.issuer_prefixes.get(tdcc[:3], "")
    if code in config.issuer_names:
        return code
    return next((c for c, name in config.issuer_names.items() if name == issuer), code)
