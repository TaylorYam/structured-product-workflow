"""回補既有的 FCN 以外商品（#303、ADR 0027）。

每日匯入以前把 FCN 以外的商品略過（記在 `workflow.other_product`），開始支援之後要把既有的一次補進來。「維護」選單
讀一份參考條件表格式的 Excel（例如 `FCN外商品數量.xlsx`），每一檔建立鎖住的商品條件與比價要用的資料：
- 範圍：Product 是支援的商品種類（`fcn_checker.products.PRODUCT_RULES`）、而且不是 FCN 的列；其他列只計數，
  Product 空白的不知道是哪種商品，不匯入、列出來。
- 商品條件照起單資料匯入的檢查（`product_terms.create_backfilled`），來源「歷史回補」、建立時就鎖住：
  KO(Freq)＋KO(memo) → KO Type、KI(Freq) → Barrier Type、UF → Upfront / NotePrice；沒有 Non-Call 的商品
  （FCN_Express）Non-Call 一律存 0（舊表寫 1）；舊表沒有期初定價日，照舊商品存交易日；期初定價空白照樣匯入、
  結果寫出來，之後在人工維護補。ISIN、單位面額用表上的值。只在到期判定的商品（BEN，#373、ADR 0029）KO Type 一律
  Period End（舊表的 KO(memo) 不看），參與率帶 100（`product_terms.draft_from`；Digital 由作業人員在人工維護改 0），Non-Call 照舊表、要等於天期。
  要配息門檻的商品（DACN，#367）用表上的配息門檻，舊表沒有這欄（或空白）時帶執行價。
- 價格與比價日：UL_1～UL_5 的四個價格寫進 `workflow.underlying_price`，核對條件寫一筆「完成」；比價日 1～12 照搬
  （比價日_1 是第一個觀察日，沒有 Non-Call 的商品不能空白）。
- 分公司配置：分公司_1～7 與承作金額寫進 `workflow.branch_allocation`。交易明細列（含 FC）不在這裡寫，
  由每日匯入「只匯交易明細」重匯交易清單補上。
- 寫好的 TDCC 從 `workflow.other_product` 移除，寄信不再當成不支援的商品。
- 資料庫已有商品條件的不覆蓋，列「已存在」；表上同一檔有兩列、TDCC 不是 12 碼、發行機構和 TDCC 前三碼不符、
  欄位格式錯的，那一檔不匯入並寫出原因，其他照常；不匯入的另外寫一份錯誤清單放在來源檔旁邊。
- 整次匯入一個 transaction；重跑同一份全部是「已存在」。比價由呼叫端接著做（`window_actions`）；舊表沒有存續狀態欄，
  不寫舊清單事件。
傳入的 `conn` 由 `database.connect` 建立，資料表版本已確認（#200），這裡不再檢查。
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import openpyxl

from fcn_checker.ingestion import IngestionError
from fcn_checker.orders.reference import (
    autocall_date_key,
    header_columns,
    load_reference_sheet,
    locate,
    price_key,
    underlying_key,
)
from fcn_checker.products import FCN, PRODUCT_RULES, product_name
from sp_workflow import import_run
from sp_workflow.check_terms import DONE
from sp_workflow.database import OperatorError
from sp_workflow.legacy_backfill import (
    EXISTING,
    PRICES,
    REQUIRED_PRICES,
    SKIPPED,
    BackfillLine,
    cell_value,
    date_problems,
    write_dates,
)
from sp_workflow.order_terms import describe
from sp_workflow.product_record import (
    COUPON_BARRIER_FIELD,
    KI_CODES,
    KO_CODES,
    MATURITY_ONLY_TERMS,
    PRICING_FIELD,
    STRIKE_FIELD,
    TRADE_FIELD,
    UNDERLYINGS,
    UPFRONT_FIELD,
    default_coupon_barrier,
    invert_upfront,
)
from sp_workflow.product_terms import create_backfilled, read, read_many

if TYPE_CHECKING:
    import psycopg

    from fcn_checker.config import ReferenceFormat
    from fcn_checker.orders.reference import OrderRecord
    from sp_workflow.product_terms import ImportConfig

IMPORTED = "已匯入"  # 已存在、不匯入同舊商品補資料
BRANCHES = 7  # 參考條件表的分公司欄數
UF_COLUMN = "UF"  # 不是參考條件表的標準欄位（核對不比），直接讀格子
_EXTRA_COLUMNS = (
    UF_COLUMN,
    *(name for n in range(1, BRANCHES + 1) for name in (f"分公司_{n}", f"分公司_{n}_承作金額")),
)
ERROR_BOOK = "其他商品回補錯誤_{stamp}.xlsx"
ERROR_BOOK_COLUMNS = (("TDCC", 16), ("原因", 80), ("表上的列", 10))
_TDCC = re.compile(r"[0-9]{12}")
_KO_TYPES = {codes: name for name, codes in KO_CODES.items()}  # (KO(Freq), KO(memo)) → KO Type
_BARRIER_TYPES = {code: name for name, code in KI_CODES.items()}  # KI(Freq) → Barrier Type
# 參考條件表的標準欄位 → 畫面的欄（照搬的欄；KO／KI 型態、UF、Non-Call、標的另外換算）
_COPIED = {
    "currency": "Currency",
    "strike_pct": "Strike (%)",
    "ko_pct": "KO Barrier (%)",
    "coupon_pa_pct": "Coupon p.a. (%)",
    "ki_pct": "KI Barrier (%)",
    "trade_date": TRADE_FIELD,
    "issue_date": "Issue date",
    "final_valuation_date": "Final valuation date",
    "maturity_date": "Maturity date",
    "initial_pricing": PRICING_FIELD,
}


class ProductBackfillError(OperatorError):
    """整份不匯入（檔案讀不了、缺必要欄、錯誤清單寫不出來）；訊息是給作業人員看的中文。"""


@dataclass(frozen=True)
class ProductBackfillReceipt:
    lines: tuple[BackfillLine, ...]
    ignored: int  # 不在範圍內（FCN、不支援的商品種類）的列數
    error_book_path: Path | None = None

    def count(self, result: str) -> int:
        return sum(line.result == result for line in self.lines)


def backfill_products(
    conn: psycopg.Connection,
    path: Path,
    fmt: ReferenceFormat,
    config: ImportConfig,
    *,
    now: dt.datetime | None = None,
) -> ProductBackfillReceipt:
    """讀一份參考條件表格式的 Excel，建立 FCN 以外商品的商品條件、價格、比價日與分公司配置；一個 transaction。"""
    records, extra = _read(path, fmt)
    grouped: dict[str, list[OrderRecord]] = {}
    ignored = 0
    for record in records:
        text = cell_value(record, "product", fmt)
        product = product_name(text)
        if product == FCN or (product is None and text is not None):  # 空白的不知道是哪種商品，列出來
            ignored += 1
            continue
        grouped.setdefault(str(record.product_code.value), []).append(record)
    note = f"其他商品回補：{path.name}"
    lines: list[BackfillLine] = []
    error_book = None
    try:
        with conn.transaction():
            stored = read_many(conn, [t for t in grouped if _TDCC.fullmatch(t)], lock=True)
            for tdcc, rows in grouped.items():
                numbers = tuple(r.row for r in rows)
                if tdcc in stored:
                    lines.append(BackfillLine(tdcc, EXISTING, "資料庫已有這檔的商品條件，沒有動", numbers))
                    continue
                problem = _row_problem(tdcc, rows, fmt, config)
                if problem:
                    lines.append(BackfillLine(tdcc, SKIPPED, problem, numbers))
                    continue
                (record,) = rows
                cells = extra.get(record.row, {})
                values, problems = _values(record, cells, fmt)
                problems += [*_price_problems(record, fmt), *date_problems(record, fmt)]
                product = PRODUCT_RULES[values["Product"]]
                if not product.has_non_call and cell_value(record, autocall_date_key(1), fmt) is None:
                    problems.append(f"比價日_1 空白：{product.name} 沒有 Non-Call，比價日_1 是第一個觀察日")
                allocation, branch_problems = _allocation(cells)
                problems += branch_problems
                if not problems:
                    found = create_backfilled(
                        conn,
                        tdcc,
                        values,
                        config,
                        isin=cell_value(record, "isin", fmt),
                        denomination=_number(cell_value(record, "denomination", fmt)),
                    )
                    problems = [describe(found)] if found else []
                if problems:
                    lines.append(BackfillLine(tdcc, SKIPPED, "；".join(problems), numbers))
                    continue
                _write(conn, tdcc, record, fmt, allocation, note)
                detail = "" if values[PRICING_FIELD] else "期初定價空白，已匯入；請到「7 人工維護」補上"
                lines.append(BackfillLine(tdcc, IMPORTED, detail, numbers))
            problems = [line for line in lines if line.result == SKIPPED]
            if problems:
                error_book = path.parent / ERROR_BOOK.format(stamp=f"{(now or dt.datetime.now()):%Y%m%d-%H%M%S}")
                import_run.error_book(
                    error_book,
                    "其他商品回補錯誤",
                    [name for name, _ in ERROR_BOOK_COLUMNS],
                    ([line.tdcc, line.detail, "、".join(map(str, line.rows))] for line in problems),
                    widths=[width for _, width in ERROR_BOOK_COLUMNS],
                )
    except BaseException:
        if error_book is not None:  # 沒有匯入就不留錯誤清單
            error_book.unlink(missing_ok=True)
        raise
    return ProductBackfillReceipt(tuple(lines), ignored, error_book)


# ---------- 讀表 ----------


def _read(path: Path, fmt: ReferenceFormat) -> tuple[list[OrderRecord], dict[int, dict[str, Any]]]:
    """參考條件表的每一列與 UF、分公司欄（列號 → 欄名 → 值；表上沒有的欄不在內）。"""
    try:
        sheet = load_reference_sheet(path, fmt)
        book = openpyxl.load_workbook(path, data_only=True)
        layout = locate(book, fmt)
    except IngestionError as exc:
        raise ProductBackfillError(f"{str(exc).removesuffix('，整批不核對')}，整份不匯入") from exc
    ws = book[layout.sheet]
    columns = header_columns(ws, layout)
    extra = {
        record.row: {
            name: ws.cell(row=record.row, column=columns[name][0]).value for name in _EXTRA_COLUMNS if name in columns
        }
        for record in sheet.rows
    }
    return sheet.rows, extra


# ---------- 檢查與換算 ----------


def _row_problem(tdcc: str, rows: Sequence[OrderRecord], fmt: ReferenceFormat, config: ImportConfig) -> str:
    if any(cell_value(r, "product", fmt) is None for r in rows):
        return "Product 空白，不知道是哪種商品"
    if len(rows) > 1:
        return f"表上有 {len(rows)} 列（第 {'、'.join(str(r.row) for r in rows)} 列），不知道用哪一列"
    if not _TDCC.fullmatch(tdcc):
        return f"TDCC「{tdcc}」不是 12 碼數字"
    expected = config.issuer_names.get(config.issuer_prefixes.get(tdcc[:3], ""))
    issuer = rows[0].issuer.value if rows[0].issuer is not None else None
    if issuer not in (None, fmt.empty_value) and expected is not None and str(issuer).strip() != expected:
        return f"發行機構「{issuer}」和 TDCC 前三碼的上手（{expected}）不同"
    return ""


def _values(record: OrderRecord, cells: Mapping[str, Any], fmt: ReferenceFormat) -> tuple[dict[str, object], list[str]]:
    """參考條件表的一列（`cells` 是 UF 等直接讀的格子）→ 畫面的欄 → 值，與換算不了的問題；
    型別不對的值照原樣留著，由商品條件的檢查標出來。"""

    def value(key: str) -> Any:
        return cell_value(record, key, fmt)

    product = PRODUCT_RULES[product_name(value("product"))]  # type: ignore[index]
    problems = []
    values: dict[str, object] = {"Product": product.name}
    values.update({name: _number(value(key)) for key, name in _COPIED.items()})
    values[STRIKE_FIELD] = values[TRADE_FIELD]  # 舊表沒有期初定價日：照舊商品存交易日
    for n, name in enumerate(UNDERLYINGS, 1):
        values[name] = value(underlying_key(n))
    ko = (value("ko_observation"), value("ko_memory"))
    values["KO Type"] = MATURITY_ONLY_TERMS["KO Type"] if product.maturity_only else _KO_TYPES.get(ko)  # type: ignore[arg-type]
    if values["KO Type"] is None:
        problems.append(f"KO(Freq)／KO(memo) 看不懂（{ko[0]}／{ko[1]}）")
    ki = value("ki_type") or fmt.empty_value  # 空值寫法「-」就是沒有 KI
    values["Barrier Type"] = _BARRIER_TYPES.get(ki)
    if values["Barrier Type"] is None:
        problems.append(f"KI(Freq)「{ki}」目前不支援")
    uf = cells.get(UF_COLUMN)
    uf = None if uf in (None, fmt.empty_value) else _number(uf)
    values[UPFRONT_FIELD] = invert_upfront(uf) if isinstance(uf, Decimal) else uf
    values["Tenor (m)"] = _whole(value("tenor_months"))
    values["Guaranteed Periods (m)"] = _whole(value("first_callable_period")) if product.has_non_call else 0
    barrier = _number(value("coupon_barrier_pct"))  # 舊表沒有這欄（#367）
    values[COUPON_BARRIER_FIELD] = default_coupon_barrier(values) if barrier is None else barrier
    return values, problems


def _price_problems(record: OrderRecord, fmt: ReferenceFormat) -> list[str]:
    problems = []
    for i in range(1, len(UNDERLYINGS) + 1):
        prices = {label: cell_value(record, price_key(i, col), fmt) for col, _, label in PRICES}
        if cell_value(record, underlying_key(i), fmt) is None:
            if any(v is not None for v in prices.values()):
                problems.append(f"UL_{i} 沒有代碼，卻有價格")
            continue
        for label, price in prices.items():
            if price is None and label in REQUIRED_PRICES:
                problems.append(f"UL_{i} {label}是空白")
            elif price is not None and not isinstance(price, Decimal):
                problems.append(f"UL_{i} {label}不是數字（{price}）")
    return problems


def _allocation(cells: Mapping[str, Any]) -> tuple[list[tuple[str, Decimal]], list[str]]:
    """分公司_1～7 → [(分公司, 承作金額)]（空白與「-」略過）與問題。"""
    allocation, problems = [], []
    for n in range(1, BRANCHES + 1):
        branch, amount = cells.get(f"分公司_{n}"), cells.get(f"分公司_{n}_承作金額")
        if branch is None or str(branch).strip() in ("", "-"):
            continue
        number = _number(amount)
        if not isinstance(number, Decimal) or number <= 0:
            problems.append(f"分公司_{n}（{branch}）的承作金額不是正數（{amount}）")
            continue
        allocation.append((str(branch).strip(), number))
    return allocation, problems


def _number(value: Any) -> Any:
    """Excel 的數字換成 Decimal（參考條件表讀出來已經是 Decimal；分公司欄是直接讀格子）；其他照原樣。"""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return value
    return Decimal(str(value))


def _whole(value: Any) -> Any:
    """整數欄：整數的 Decimal 換成 int；其他照原樣，由商品條件的檢查標出來。"""
    if isinstance(value, Decimal) and value == value.to_integral_value():
        return int(value)
    return value


# ---------- 寫入 ----------


def _write(
    conn: psycopg.Connection,
    tdcc: str,
    record: OrderRecord,
    fmt: ReferenceFormat,
    allocation: Sequence[tuple[str, Decimal]],
    note: str,
) -> None:
    """商品條件寫好之後：核對條件「完成」、四個價格、比價日、分公司配置，並從其他商品移除。"""
    conn.execute(
        "INSERT INTO workflow.check_terms (tdcc, status, reason) VALUES (%s, %s, %s)",
        (tdcc, DONE, f"{note}（價格由表上補入）"),
    )
    columns = read(conn, tdcc).columns  # 代碼用商品條件存的寫法
    for i in range(1, len(UNDERLYINGS) + 1):
        code = columns[f"underlying_{i}"]
        if code is not None:
            conn.execute(
                "INSERT INTO workflow.underlying_price"
                " (tdcc, seq, code, initial_price, strike_price, ki_price, ko_price) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (tdcc, i, code, *(cell_value(record, price_key(i, col), fmt) for col, _, _ in PRICES)),
            )
    write_dates(conn, tdcc, record, fmt)
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO workflow.branch_allocation (tdcc, seq, branch, amount) VALUES (%s, %s, %s, %s)",
            [(tdcc, seq, branch, amount) for seq, (branch, amount) in enumerate(allocation, 1)],
        )
    conn.execute("DELETE FROM workflow.other_product WHERE tdcc = %s", (tdcc,))
