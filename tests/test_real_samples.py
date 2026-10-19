"""真實樣本試跑：只在本機設定 SPW_REAL_SAMPLES_DIR（底下是以 TDCC 命名的商品資料夾）時執行，CI 自動略過。

真實資料不進 repo；這裡只記各上手中文文件的檔名特徵，用來確認挑到的是對的檔。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from sp_workflow.documents import DocKind
from sp_workflow.preparation import PREPARED, prepare_batch

pytestmark = [pytest.mark.real_samples, pytest.mark.word]

# 上手編號 → (說明書檔名特徵, 投資人須知檔名特徵, 是否由 Word 轉檔)
EXPECTED: dict[str, tuple[str, str, bool]] = {
    "029": ("CN TS", "CN IIS", False),  # BARC
    "037": ("(ZH)", "IIS_Chinese", False),  # SG
    "076": ("JPM_Chi_TS", "JPM_Chi_IIS", False),  # JPM
    "082": ("_TS_", "_IIS_", True),  # BNP：只有 Word（舊版 .DOC），另有英文 DOCX
    "086": ("PS_", "IIS_", False),  # CA：TS_ 開頭是英文版
    "092": ("_TS.pdf", "_IIS.pdf", False),  # UBS
    "147": ("_ATS_", "_AD_", True),  # MS：只有 Word，檔名 _TS_ 的是英文版
    "148": ("Product Statement", "_IIS_", False),  # GS
    "156": ("_CN_TS_", "_CN_IIS_", False),  # Nomura
    "325": ("_CH_TS_", "_CH_KIID_", False),  # HSBC
    "326": ("Chi TS", "Chi IIS", False),  # BBVA
    "328": ("Final_TS_", "Final_IIS_", False),  # Natixis
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

    receipt = prepare_batch("\n".join(tdccs), root, tmp_path)  # 用真的 Microsoft Word 轉檔

    for o in receipt.outcomes:
        assert o.tdcc[:3] in EXPECTED, f"{o.tdcc}：EXPECTED 沒有這個上手編號"
        ts_mark, iis_mark, from_word = EXPECTED[o.tdcc[:3]]
        assert o.status == PREPARED, (o.tdcc, o.reason)
        names = {d.kind: d.source.name for d in o.documents}
        assert ts_mark in names[DocKind.TS], (o.tdcc, names)
        assert iis_mark in names[DocKind.IIS], (o.tdcc, names)
        assert all(d.from_word == from_word for d in o.documents), (o.tdcc, names)
