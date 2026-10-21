"""每日匯入與寄信草稿共用的虛構資料：交易列、股價、每日更新檔、商品、信箱。資料全部虛構。"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from pathlib import Path

import openpyxl

from sp_workflow import daily_import
from sp_workflow.daily_import import PRICE_HEADERS, TRADE_HEADERS

PRICE_DATE = dt.datetime(2026, 10, 16)  # 虛構的取價日：交易日 2026-10-15 的下一份股價檔

# 庫存總表／ELN 的代碼 → 轉換後的寫法（以前清單更新用的對照，#87 起每日匯入不再讀清單更新）
KO_TYPES = {
    ("D", "Y"): "Daily Memory",
    ("D", "N"): "Daily",
    ("P", "N"): "Period End",
    ("P", "Y"): "Period End Memory",
}
BARRIER_TYPES = {"-": "NONE", "AM": "EKI", "D": "AKI", "P": "MKI"}


def trade(tdcc: str, fc: str, branch: str, amount: int, *, number: int | None = 1, product: str = "FCN") -> dict:
    return {
        "編號": number,
        "交易日期": dt.datetime(2026, 10, 15),
        "Product": product,
        "TDCC": tdcc,
        "幣別": "USD",
        "金額": amount,
        "FC/PB": fc,
        "分公司": branch,
        "UF": 0.0215,
        "期初價格": "收盤價",
        "募集": "N",
        "起單人員": "A",
    }


def price(code: str, close: float | None = 100.5, *, name: str = "虛構公司", open_: float | None = 99.25) -> tuple:
    """BBG_Data 的一列：代碼、公司名稱、最新收盤、最新開盤、1日前收盤、200日波動率、市值（原幣／百萬）。"""
    return (code, name, close, open_, 98.0, 31.5, 12345.6)


def write_daily(
    path: Path,
    trades: list[dict],
    prices: Sequence[tuple] | None = None,
    *,
    trade_headers=TRADE_HEADERS,
    price_date=PRICE_DATE,
    price_headers=PRICE_HEADERS,
) -> Path:
    """新版每日更新（#87）：交易明細＋BBG_Data。BBG_Data 的 B1 是 =TODAY()、C1 是取價日、第 3 列空白。

    沒給股價時放一檔虛構的（BBG_Data 沒有任何股價會整份不匯入）；要測空的就明確給 []。
    """
    prices = [price("AAA UW")] if prices is None else prices
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = daily_import.TRADE_SHEET
    ws.append(list(trade_headers))
    for t in trades:
        ws.append([t.get(h) for h in TRADE_HEADERS])
    bbg = wb.create_sheet(daily_import.PRICE_SHEET)
    bbg.append([None, "=TODAY()", price_date])
    bbg.append([None, *price_headers])
    bbg.append([])
    for row in prices:
        bbg.append([None, *row])
    wb.save(path)
    return path


def inventory(tdcc: str, branches: list[tuple[str, int]], **overrides) -> dict:
    """以前庫存總表（清單更新）的一列，給 `seed_products` 建商品用。"""
    row = {
        "Product": "FCN",
        "發行機構": "XBANK",
        "TDCC Code": tdcc,
        "ISIN Code": "XS0000000001",
        "承作幣別": "USD",
        "交易日": dt.date(2026, 10, 15),
        "發行日": dt.date(2026, 10, 22),
        "最終比價日": dt.date(2027, 4, 22),
        "到期日": dt.date(2027, 4, 27),
        "期初定價": "收盤價",
        "KO(%)": 100,
        "KO(Freq)": "D",
        "KO(memo)": "Y",
        "K(%)": 80,
        "KI(%)": 65,
        "KI(Freq)": "AM",
        "UF": 2.15,
        "Coupon p.a. (%)": 12.5,
        "天期(月)": 6,
        "Non-Call(月)": 1,
        "UL_1": "AAA UW",
        "UL_2": "BBB UN",
        "branches": branches,
    }
    row.update(overrides)
    return row


def seed_products(pg, inventories: Sequence[dict]) -> None:
    """像以前清單更新那樣直接寫商品與分公司配置（source 清單更新、沒鎖住）；#87 起每日匯入不再建商品。

    掛在最近一次每日匯入上（還沒有就建一筆）；非 FCN 的列略過。
    """
    found = pg.execute("SELECT max(run_id) FROM workflow.import_run").fetchone()[0]
    run_id = (
        found
        or pg.execute(
            "INSERT INTO workflow.import_run (daily_file_name, daily_sha256, summary)"
            " VALUES ('清單更新.xlsx', %s, '{}') RETURNING run_id",
            ("0" * 64,),
        ).fetchone()[0]
    )
    for row in inventories:
        if row["Product"] != "FCN":
            continue
        ki_freq = row["KI(Freq)"]
        product = {
            "tdcc": row["TDCC Code"],
            "product": "FCN",
            "issuer": row["發行機構"],
            "isin": row["ISIN Code"],
            "currency": row["承作幣別"],
            "trade_date": row["交易日"],
            "issue_date": row["發行日"],
            "final_valuation_date": row["最終比價日"],
            "maturity_date": row["到期日"],
            "initial_pricing": row["期初定價"],
            "non_call_months": row["Non-Call(月)"],
            **{f"underlying_{i}": row.get(f"UL_{i}") for i in range(1, 6)},
            "strike_pct": row["K(%)"],
            "ko_freq": row["KO(Freq)"],
            "ko_memory": row["KO(memo)"],
            "ko_type": KO_TYPES[(row["KO(Freq)"], row["KO(memo)"])],
            "ko_pct": row["KO(%)"],
            "coupon_pct": row["Coupon p.a. (%)"],
            "upfront_pct": row["UF"],
            "tenor_months": row["天期(月)"],
            "ki_freq": ki_freq,
            "barrier_type": BARRIER_TYPES[ki_freq],
            "ki_pct": None if ki_freq == "-" else row["KI(%)"],
            "status_event": "在庫",
            "first_run_id": run_id,
        }
        pg.execute(
            f"INSERT INTO workflow.product ({', '.join(product)}) VALUES ({', '.join(['%s'] * len(product))})",
            tuple(product.values()),
        )
        for seq, (branch, amount) in enumerate(row["branches"], 1):
            pg.execute(
                "INSERT INTO workflow.branch_allocation (tdcc, seq, branch, amount) VALUES (%s, %s, %s, %s)",
                (row["TDCC Code"], seq, branch, amount),
            )


def seed_mailbox(pg, people=None, aliases=()) -> None:
    """像使用者用 SQL 維護信箱一樣直接寫入；比對鍵由資料庫產生。"""
    people = people or [
        ("Amy", "amy@example.com", "FC", False),
        ("Ben", "ben@example.com", "FC", False),
        ("Pat", "pat@example.com", "PM", True),
    ]
    for row in people:
        pg.execute("INSERT INTO mailbox.person (name, email, unit, fixed_cc) VALUES (%s, %s, %s, %s)", row)
    for row in aliases:
        pg.execute("INSERT INTO mailbox.alias (alias, person_name) VALUES (%s, %s)", row)
