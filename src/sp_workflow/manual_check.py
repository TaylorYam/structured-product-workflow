"""人工核對完成（#527、ADR 0032）：作業人員自己核對完說明書，在「7 人工維護」輸入核對要回填的值，跳過自動核對、鎖住。

系統不支援的上手或商品種類（「4 核對」是未支援上手／不支援樣態，不能人工放行）不會回填 ISIN、比價日，也不會鎖住，
比價一直是「沒有比價日」。這裡讓任何還沒鎖住的商品（使用者決定不限不支援的）照核對寫回的方式補上：
- 輸入（`ManualCheck`）：ISIN、比價日（依序，1～12 期）、期初定價 VWAP 時各標的進場價；原因沿用人工維護的原因欄。
- 檢查（`problems`，全部列出、任一項不過就什麼都不寫）：還沒鎖住；核對條件是完成；有 Issue date（鎖住後不能改）；
  ISIN 是 2 碼英文＋9 碼英數＋1 碼數字；比價日不超過 12 期、由小到大不重複、最後一期等於 Final valuation date
  （同比價的檢查，`observation.autocall_problem`）；VWAP 每個標的都有大於 0 的進場價。
- 寫入（`confirm`，在呼叫端的 transaction 裡）：ISIN、比價日 1～12（沒用到的期別寫空白）、VWAP 的四個價格
  （執行價、KI、KO 由進場價乘比例算，比例空白時空白，同更正 VWAP 的算法）都走 `product_terms.backfill`，
  值有改變時 `workflow.backfill_log` 留原值；另記一筆「人工核對」（原因），結果檔欄寫 `RESULT_FILE`。最後鎖住。
比價由呼叫端接著做（`window_actions.manual_check`）。
傳入的 `conn` 由 `database.connect` 建立，資料表版本已確認（#200），這裡不再檢查。
"""

from __future__ import annotations

import datetime as dt
import getpass
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

from fcn_checker.backfill_values import AUTOCALL_SLOTS, ISIN, SLOTS, Target, UnderlyingPrice
from fcn_checker.fields import PRICE_FIELDS
from sp_workflow.check_terms import is_vwap
from sp_workflow.database import OperatorError, require_in_transaction
from sp_workflow.observation import autocall_problem
from sp_workflow.order_terms import parse_date
from sp_workflow.product_record import derived_prices, round_price, same_value
from sp_workflow.product_store import Stored, locked, read
from sp_workflow.product_terms import backfill, lock_checked, log_backfill
from sp_workflow.status import CheckTerms

if TYPE_CHECKING:
    import psycopg

RESULT_FILE = "人工核對（人工維護）"  # 回填紀錄的結果檔欄：不是核對結果檔
LOG_ITEM = "人工核對"  # 回填紀錄的項目：這一筆記原因（新值）
_ISIN = re.compile(r"[A-Z]{2}[A-Z0-9]{9}[0-9]")


class ManualCheckError(OperatorError):
    """人工核對檢查不過；`problems` 是每一項原因。"""

    def __init__(self, tdcc: str, problems: Sequence[str]) -> None:
        self.problems = tuple(problems)
        super().__init__(f"{tdcc} 沒有鎖住：" + "；".join(self.problems))


@dataclass(frozen=True)
class ManualCheck:
    """人工核對要回填的值。`autocall` 是比價日（依序）；`initial` 是 VWAP 的進場價（標的序號 → 價格）；
    `seen` 是畫面打開時資料庫的條件（`Draft.stored`），和現在不同時不寫（同人工維護存檔）。"""

    tdcc: str
    isin: str
    autocall: tuple[dt.date, ...]
    initial: Mapping[int, Decimal] = field(default_factory=dict)
    reason: str = ""
    seen: Mapping[str, object] | None = None


@dataclass(frozen=True)
class ManualCheckReceipt:
    tdcc: str
    changes: int  # 改了幾個值（回填紀錄的筆數，不含「人工核對」那一筆）


def parse_entry(
    tdcc: str,
    isin: str,
    dates: str,
    prices: Mapping[int, str],
    reason: str = "",
    seen: Mapping[str, object] | None = None,
) -> ManualCheck:
    """視窗打的字 → `ManualCheck`。比價日以換行、逗號、頓號或空白分開；看不懂的丟 `OperatorError`（全部列出）。
    格式以外的檢查（期數、順序、最終比價日、ISIN）在 `problems`。"""
    found: list[str] = []
    days: list[dt.date] = []
    for n, text in enumerate((t for t in re.split(r"[\s,，、;；]+", dates) if t), 1):
        try:
            days.append(parse_date(text))
        except ValueError:
            found.append(f"第 {n} 個比價日「{text}」看不懂（寫成 2026-10-21）")
    initial: dict[int, Decimal] = {}
    for seq, text in prices.items():
        cleaned = text.replace(",", "").strip()
        if not cleaned:
            continue
        try:
            initial[seq] = round_price(Decimal(cleaned))  # 同核對條件算的進場價，取到小數第 4 位
        except ArithmeticError:
            found.append(f"UL_{seq} 進場價「{text.strip()}」不是數字")
    if found:
        raise OperatorError("；".join(found))
    return ManualCheck(tdcc, isin.strip().upper(), tuple(days), initial, reason.strip(), seen)


def needs_initial(stored: Stored) -> tuple[tuple[int, str], ...]:
    """期初定價是 VWAP 時要輸入進場價的標的（序號, 代碼）；不是 VWAP 時空的（價格由核對條件算好了）。"""
    if not is_vwap(stored.columns["initial_pricing"]):
        return ()
    return tuple((p.seq, p.code) for p in stored.prices)


def problems(stored: Stored | None, entry: ManualCheck) -> list[str]:
    """人工核對前的檢查；沒有問題時空的。"""
    if stored is None:
        return ["資料庫沒有這檔的商品條件"]
    if locked(stored):
        return ["已經鎖住（核對過了），不能再人工核對"]
    seen = entry.seen
    if seen is not None and any(not same_value(stored.fields[name], seen.get(name)) for name in stored.fields):
        return ["資料庫裡這檔的條件在打開之後改過了，請重新載入"]
    found = []
    check = stored.check
    if check is None or check.status != CheckTerms.DONE:
        status = "還沒算" if check is None else f"{check.status}：{check.reason}" if check.reason else check.status
        found.append(f"核對條件不是完成（{status}）")
    if stored.columns["issue_date"] is None:
        found.append("沒有 Issue date（鎖住後不能改，先在人工維護補上存檔）")
    if not _ISIN.fullmatch(entry.isin):
        found.append(f"ISIN「{entry.isin}」格式不對（2 碼英文＋9 碼英數＋1 碼數字）")
    found += _autocall_problems(entry.autocall, stored.columns["final_valuation_date"])
    for seq, code in needs_initial(stored):
        price = entry.initial.get(seq)
        if price is None or price <= 0:
            found.append(f"UL_{seq} {code} 期初定價是 VWAP，要輸入大於 0 的進場價")
    return found


def _autocall_problems(dates: Sequence[dt.date], final: dt.date | None) -> list[str]:
    """比價前的檢查（`autocall_problem`）之外，人工輸入另外檢查期數與順序。"""
    found = []
    if len(dates) > SLOTS:
        found.append(f"比價日有 {len(dates)} 期，最多 {SLOTS} 期")
    if any(b <= a for a, b in zip(dates, dates[1:], strict=False)):
        found.append("比價日要由小到大、不重複")
    problem = autocall_problem(dates, final)
    return [*found, problem] if problem else found


def confirm(conn: psycopg.Connection, entry: ManualCheck, *, user: str | None = None) -> ManualCheckReceipt:
    """檢查後寫入人工核對的值並鎖住；檢查不過丟 `ManualCheckError`（什麼都不寫）。在呼叫端的 transaction 裡執行。"""
    require_in_transaction(conn)
    stored = read(conn, entry.tdcc, lock=True)  # 和商品條件寫入、核對寫回共用同一把鎖
    found = problems(stored, entry)
    if found:
        raise ManualCheckError(entry.tdcc, found)
    assert stored is not None
    user = user or getpass.getuser()
    values: list[tuple[Target, object, str]] = [(ISIN, entry.isin, "ISIN Code")]
    padded = (*entry.autocall, *([None] * (SLOTS - len(entry.autocall))))
    values += [(slot, day, f"比價日_{slot.slot}") for slot, day in zip(AUTOCALL_SLOTS, padded, strict=True)]
    for seq, _code in needs_initial(stored):
        values += _vwap_prices(stored, seq, entry.initial[seq])
    changes = 0
    for target, value, item in values:
        changes += backfill(conn, entry.tdcc, target, value, item=item, result_file=RESULT_FILE, user=user)
    reason = entry.reason.strip() or "跳過自動核對"
    log_backfill(conn, entry.tdcc, [(LOG_ITEM, None, reason)], result_file=RESULT_FILE, user=user)
    lock_checked(conn, entry.tdcc)
    return ManualCheckReceipt(entry.tdcc, changes)


def _vwap_prices(stored: Stored, seq: int, initial: Decimal) -> list[tuple[Target, object, str]]:
    """VWAP 一個標的的四個價格：進場價照輸入，執行價、KI、KO 由進場價乘比例（比例空白時空白）。"""
    p = stored.columns
    strike, ki, ko = derived_prices(initial, p["strike_pct"], p["ki_pct"], p["ko_pct"], p["barrier_type"])
    derived = {"initial": initial, "strike": strike, "ki": ki, "ko": ko}
    return [(UnderlyingPrice(seq, f.key), derived[f.key], f"UL_{seq}_{f.header}") for f in PRICE_FIELDS]
