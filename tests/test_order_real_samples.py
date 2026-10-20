"""Order 條件讀取的真實樣本試跑（#40）：只在本機設定 SPW_REAL_SAMPLES_DIR 時執行，用真的 Outlook 開信，CI 自動略過。

真實資料不進 repo；這裡只檢查每家上手都讀得出必要欄位，沒有其他待確認原因（價格改過的 `→`、Order 本來就沒有的日期除外），
已讀取的商品都排得進格式表。
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

from sp_workflow.order_formats import ISSUERS, REQUIRED
from sp_workflow.order_template import COLUMNS as TEMPLATE_COLUMNS
from sp_workflow.order_terms import READ, read_orders

pytestmark = [
    pytest.mark.real_samples,
    pytest.mark.skipif(sys.platform != "win32", reason="需要 Windows 與傳統版 Outlook"),
]


def _samples_dir() -> Path:
    value = os.environ.get("SPW_REAL_SAMPLES_DIR")
    if not value or not Path(value).is_dir():
        pytest.skip("沒有設定 SPW_REAL_SAMPLES_DIR")
    return Path(value)


def test_real_order_mails(tmp_path: Path) -> None:
    root = _samples_dir()
    tdccs = sorted(p.name for p in root.iterdir() if p.is_dir() and re.fullmatch(r"\d{12}", p.name))
    assert tdccs, f"{root} 底下沒有以 TDCC 命名的商品資料夾"

    receipt = read_orders("\n".join(tdccs), root, tmp_path)

    for row in receipt.rows:
        assert row.tdcc[:3] in ISSUERS, f"{row.tdcc}：ISSUERS 沒有這個上手編號"
        assert row.issuer == ISSUERS[row.tdcc[:3]].name
        # 允許：價格改過（→）、GS／HSBC 的 Order 本來就沒有最終比價日與到期日
        allowed = ("有修改", "格式表缺：Final valuation date、Maturity date")
        problems = [r for r in row.reasons if not any(a in r for a in allowed)]
        assert not problems, (row.tdcc, row.reasons)
        assert row.status == READ or row.reasons, row.tdcc
        missing = [name for name in REQUIRED if name not in row.values]
        assert not missing, (row.tdcc, missing)
        assert len(row.template) == len(TEMPLATE_COLUMNS), row.tdcc
    assert {t[:3] for t in tdccs} <= {r.tdcc[:3] for r in receipt.rows}
