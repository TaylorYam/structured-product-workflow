"""Order 條件讀取對照 10 月清單（#52）：只在本機設定 SPW_ORDER_CHECK_DIR 時執行，用真的 Outlook 開信，CI 自動略過。

把 2026-10-20 的人工比對變成回歸測試：重跑私人樣本倉庫裡每個 `Order問題信件_*` 回報包，
格式表逐欄對照作業人員確認過的 10 月清單，抓「無聲錯誤」——格子有值、沒標淺紅底，但和清單不同。
已標淺紅底的格子、待確認列裡留空的格子交給人工，不算錯。

真實資料不進 repo；這裡只有欄位對應與允許清單（TDCC 與欄名）。
"""

from __future__ import annotations

import datetime as dt
import os
import re
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import load_workbook

from sp_workflow.order_template import COLUMNS as TEMPLATE_COLUMNS
from sp_workflow.order_terms import PENDING, read_orders

pytestmark = [
    pytest.mark.real_samples,
    pytest.mark.skipif(sys.platform != "win32", reason="需要 Windows 與傳統版 Outlook"),
]

ANSWER_FILE = "10月清單(用來核對order專案).xlsx"  # ELN待建檔格式，第 1 列表頭
PACK_GLOB = "Order問題信件_*"

# 格式表欄 → 10 月清單欄（值直接比）
DIRECT = {
    "Product": "Product",
    "Currency": "計價幣別",
    "Guaranteed Periods (m)": "Non-Call(月)",
    **{f"BBG Code {n}": f"UL_{n}" for n in range(1, 6)},
    "Strike (%)": "K(%)",
    "KO Barrier (%)": "KO(%)",
    "Coupon p.a. (%)": "Coupon p.a. (%)",
    "Tenor (m)": "天期(月)",
    "KI Barrier (%)": "KI(%)",
    "Trade date": "交易日",
    "Issue date": "發行日",
    "Final valuation date": "最終比價日",
    "Maturity date": "到期日",
    "期初價格": "期初定價",
}
# (KO(Freq), KO(memo)) → KO Type
KO_TYPES = {
    ("D", "N"): "Daily",
    ("D", "Y"): "Daily Memory",
    ("P", "N"): "Period End",
    ("P", "Y"): "Period End Memory",
}
# KI(Freq) → Barrier Type
BARRIER_TYPES = {"-": "NONE", "AM": "EKI", "D": "AKI"}
PRICE = "Upfront / NotePrice (%)"  # = 100 − UF

_UBS_DATES = "UBS Order 原文的日期與最終資料不同（發行日、最終比價日、到期日三個一起）"
# Order 原文與最終資料本來就不同（清單是事後更正過的值），程式照原文讀是對的
# （BNP、MS 兩筆來自公司全量比對，目前樣本回報包沒有這兩檔，留著讓之後補進樣本時不誤報）
KNOWN = {
    ("082001154045", "BBG Code 3"): "BNP Order 原文的 BBG 交易所代碼與最終資料不同",
    ("147000029714", "BBG Code 3"): "MS Order 原文的 BBG 交易所代碼與最終資料不同",
    ("148000011430", "BBG Code 3"): "GS Order 原文的 BBG 交易所代碼與最終資料不同",
    ("148000011443", "BBG Code 4"): "GS Order 原文的 BBG 交易所代碼與最終資料不同",
    ("092001742900", "Issue date"): _UBS_DATES,
    ("092001742900", "Final valuation date"): _UBS_DATES,
    ("092001742900", "Maturity date"): _UBS_DATES,
}

_COLUMN_INDEX = {c.header.strip(): i for i, c in enumerate(TEMPLATE_COLUMNS)}


def _check_dir() -> Path:
    value = os.environ.get("SPW_ORDER_CHECK_DIR")
    if not value or not Path(value).is_dir():
        pytest.skip("沒有設定 SPW_ORDER_CHECK_DIR")
    return Path(value)


def _norm(value: object) -> object:
    """兩邊統一成可比較的值：`-` 與空白 = None、日期去掉時間、數字去掉浮點誤差。"""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, str):
        value = value.strip()
        return None if value in ("", "-") else value
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return Decimal(str(round(float(value), 6))).normalize()
    return value


def _expected(answer: dict[str, object]) -> dict[str, object]:
    want = {column: _norm(answer.get(name)) for column, name in DIRECT.items()}
    want["KO Type"] = KO_TYPES.get((answer.get("KO(Freq)"), answer.get("KO(memo)")))
    want["Barrier Type"] = BARRIER_TYPES.get(answer.get("KI(Freq)"))
    uf = answer.get("UF")
    want[PRICE] = _norm(100 - uf) if isinstance(uf, (int, float)) else None
    return want


def _answers(path: Path) -> dict[str, list[dict[str, object]]]:
    """TDCC → 清單裡的列（同一檔可能因通路不同出現多列，商品條件相同）。"""
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        rows = wb.worksheets[0].iter_rows(values_only=True)
        header = [str(h).strip() if h is not None else "" for h in next(rows)]
        found: dict[str, list[dict[str, object]]] = {}
        for row in rows:
            answer = dict(zip(header, row, strict=False))
            tdcc = str(answer.get("TDCC Code") or "").strip()
            if tdcc:
                found.setdefault(tdcc, []).append(_expected(answer))
        return found
    finally:
        wb.close()


def test_order_terms_match_october_list(tmp_path: Path) -> None:
    root = _check_dir()
    answer_path = root / ANSWER_FILE
    assert answer_path.is_file(), f"{root} 底下沒有 {ANSWER_FILE}"
    answers = _answers(answer_path)
    packs = sorted(p for p in root.glob(PACK_GLOB) if p.is_dir())
    assert packs, f"{root} 底下沒有 {PACK_GLOB} 回報包"

    silent: list[str] = []
    missing: list[str] = []
    compared = 0
    for pack in packs:
        tdccs = sorted(p.name for p in pack.iterdir() if p.is_dir() and re.fullmatch(r"\d{12}", p.name))
        if not tdccs:
            continue
        out = tmp_path / pack.name
        out.mkdir()
        receipt = read_orders("\n".join(tdccs), pack, out)
        for row in receipt.rows:
            if not row.template:  # 找不到 Order、未支援上手：沒有格式表列
                continue
            wants = answers.get(row.tdcc)
            if not wants:
                missing.append(f"{pack.name} {row.tdcc}")
                continue
            compared += 1
            for column in wants[0]:
                index = _COLUMN_INDEX[column]
                got = _norm(row.template[index])
                if any(got == want[column] for want in wants):
                    continue
                if index in row.flagged or (row.tdcc, column) in KNOWN:
                    continue
                if got is None and row.status == PENDING:  # 待確認列留空的格子本來就要人補
                    continue
                shown = " / ".join(repr(w[column]) for w in wants)
                silent.append(f"{pack.name} {row.tdcc} [{row.status}] {column}：程式 {got!r}，清單 {shown}")

    assert not missing, "10 月清單沒有這些 TDCC，無法核對：\n" + "\n".join(missing)
    assert compared, "沒有任何一檔讀出格式表列（Outlook 是否能開信？）"
    assert not silent, f"有 {len(silent)} 格沒標淺紅底但和 10 月清單不同：\n" + "\n".join(silent)
