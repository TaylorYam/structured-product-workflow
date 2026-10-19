"""真實樣本試跑：只在本機設定 SPW_REAL_SAMPLES_DIR（底下是以 TDCC 命名的商品資料夾）時執行，CI 自動略過。

真實資料不進 repo；這裡只記各上手中文文件的檔名特徵，用來確認挑到的是對的檔。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from sp_workflow.documents import DocKind
from sp_workflow.preparation import PREPARED, SKIPPED, prepare_batch

pytestmark = pytest.mark.real_samples

# 上手編號 → (說明書檔名特徵, 投資人須知檔名特徵)；None 表示只有 Word、這一版要跳過
EXPECTED: dict[str, tuple[str, str] | None] = {
    "029": ("CN TS", "CN IIS"),  # BARC
    "037": ("(ZH)", "IIS_Chinese"),  # SG
    "076": ("JPM_Chi_TS", "JPM_Chi_IIS"),  # JPM
    "082": None,  # BNP：只有 Word
    "086": ("PS_", "IIS_"),  # CA：TS_ 開頭是英文版
    "092": ("_TS.pdf", "_IIS.pdf"),  # UBS
    "147": None,  # MS：只有 Word
    "148": ("Product Statement", "_IIS_"),  # GS
    "156": ("_CN_TS_", "_CN_IIS_"),  # Nomura
    "325": ("_CH_TS_", "_CH_KIID_"),  # HSBC
    "326": ("Chi TS", "Chi IIS"),  # BBVA
    "328": ("Final_TS_", "Final_IIS_"),  # Natixis
}


def _samples_dir() -> Path:
    value = os.environ.get("SPW_REAL_SAMPLES_DIR")
    if not value or not Path(value).is_dir():
        pytest.skip("沒有設定 SPW_REAL_SAMPLES_DIR")
    return Path(value)


def test_real_product_folders(tmp_path: Path) -> None:
    root = _samples_dir()
    tdccs = sorted(p.name for p in root.iterdir() if p.is_dir() and re.fullmatch(r"\d{12}", p.name))
    assert tdccs, f"{root} 底下沒有以 TDCC 命名的商品資料夾"

    receipt = prepare_batch("\n".join(tdccs), root, tmp_path)

    for o in receipt.outcomes:
        expected = EXPECTED.get(o.tdcc[:3], "unknown")
        assert expected != "unknown", f"{o.tdcc}：EXPECTED 沒有這個上手編號"
        if expected is None:
            assert o.status == SKIPPED and "只有 Word" in o.reason, (o.tdcc, o.reason)
            continue
        assert o.status == PREPARED, (o.tdcc, o.reason)
        names = {d.kind: d.source.name for d in o.documents}
        assert expected[0] in names[DocKind.TS], (o.tdcc, names)
        assert expected[1] in names[DocKind.IIS], (o.tdcc, names)
