"""每日匯入與寄信草稿共用的虛構資料：交易列、庫存列、每日更新檔、信箱。資料全部虛構。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import openpyxl

from sp_workflow import daily_import
from sp_workflow.daily_import import INVENTORY_HEADERS, TRADE_HEADERS


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


def inventory(tdcc: str, branches: list[tuple[str, int]], **overrides) -> dict:
    row = {name: "-" for name in INVENTORY_HEADERS}
    row.update(
        {
            "庫存狀態": None,
            "Product": "FCN",
            "發行機構": "XBANK",
            "TDCC Code": tdcc,
            "ISIN Code": "XS0000000001",
            "承作幣別": "USD",
            "交易日": dt.datetime(2026, 10, 15),
            "發行日": dt.datetime(2026, 10, 22),
            "最終比價日": dt.datetime(2027, 4, 22),
            "到期日": dt.datetime(2027, 4, 27),
            "期初定價": "收盤價",
            "KO(%)": 100,
            "KO(Freq)": "D",
            "KO(memo)": "Y",
            "K(%)": 80,
            "KI(%)": 65,
            "KI(Freq)": "AM",
            "UF": 2.1500000000000057,
            "Coupon p.a. (%)": 12.5,
            "天期(月)": 6,
            "Non-Call(月)": 1,
            "UL_1": "AAA UW",
            "UL_2": "BBB UN",
        }
    )
    for i, (branch, amount) in enumerate(branches, 1):
        row[f"分公司_{i}"], row[f"分公司_{i}_承作金額"] = branch, amount
    row.update(overrides)
    return row


def write_daily(path: Path, trades: list[dict], inventories: list[dict], *, trade_headers=TRADE_HEADERS) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = daily_import.TRADE_SHEET
    ws.append(list(trade_headers))
    for t in trades:
        ws.append([t.get(h) for h in TRADE_HEADERS])
    inv = wb.create_sheet(daily_import.INVENTORY_SHEET)
    inv.append(list(INVENTORY_HEADERS))
    for row in inventories:
        inv.append([row.get(h) for h in INVENTORY_HEADERS])
    wb.save(path)
    return path


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
