"""Order 條件確認與視窗測試共用的虛構讀信結果（用 `template_row` 組出格式表列）。資料全部虛構。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

from sp_workflow.order_import import ImportConfig
from sp_workflow.order_template import template_row
from sp_workflow.order_terms import PENDING, READ, OrderRow

A = "029199990001"  # BARC
B = "037199990002"  # SG
SENT = dt.datetime(2026, 10, 15, 14, 30)
CONFIG = ImportConfig(
    {"USD": Decimal(10000), "JPY": Decimal(1000000)},
    {"BARC": "Barclays", "SG": "SG"},
)


def order_row(tdcc: str = A, *, issuer: str = "BARC", flagged_fields=(), fields=None, **overrides) -> OrderRow:
    """讀信結果；`overrides` 改統一欄位（欄名有括號的用 `fields`）。"""
    values = {
        "商品": "FCN",
        "幣別": "USD",
        "承作金額": Decimal(500000),
        "價格(%)": Decimal("98.5"),
        "標的1": "AAA UW",
        "標的2": "BBB UN",
        "執行價(%)": Decimal(80),
        "KO(%)": Decimal(100),
        "KO觀察方式": "Daily Memory",
        "KI型態": "EKI",
        "KI(%)": Decimal(65),
        "年利率(%)": Decimal("12.5"),
        "天期(月)": 6,
        "Non-Call(月)": 1,
        "觀察頻率(月)": 1,
        "期初定價": "收盤價",
        "交易日": dt.date(2026, 10, 15),
        "發行日": dt.date(2026, 10, 22),
        "最終比價日": dt.date(2027, 4, 15),
        "到期日": dt.date(2027, 4, 22),
    }
    values.update(overrides)
    values.update(fields or {})
    values = {k: v for k, v in values.items() if v is not None}
    template, problems, flagged = template_row(tdcc, values, (), "", SENT, flagged_fields)
    return OrderRow(
        tdcc,
        PENDING if problems else READ,
        issuer,
        tuple(problems),
        values,
        (),
        (),
        Path(f"(Order) {tdcc}.msg"),
        SENT,
        template,
        flagged,
    )
