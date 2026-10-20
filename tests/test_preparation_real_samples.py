"""真實樣本試跑：只在本機設定 SPW_REAL_SAMPLES_DIR（底下是以 TDCC 命名的商品資料夾）時執行，CI 自動略過。

真實資料不進 repo；這裡只記各上手中文文件的檔名特徵，用來確認挑到的是對的檔。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from sp_workflow.documents import DocKind, first_page_text
from sp_workflow.preparation import PREPARED, prepare_batch
from sp_workflow.trustee_code import FILL_ISSUERS

pytestmark = [pytest.mark.real_samples, pytest.mark.word]

# 上手編號 → (說明書檔名 regex, 投資人須知檔名 regex, 是否由 Word 轉檔)；不分大小寫
EXPECTED: dict[str, tuple[str, str, bool]] = {
    "029": (r"_CN TS\.pdf$", r"_CN IIS\.pdf$", False),  # BARC
    "037": (r"\(ZH\)\.pdf$", r"^IIS_Chinese_.*\.pdf$", False),  # SG
    "076": (r"^JPM_Chi_TS_.*\.pdf$", r"^JPM_Chi_IIS_.*\.pdf$", False),  # JPM
    "078": (r"_TS_.*\.pdf$", r"_IFS_.*\.pdf$", False),  # DBS：投資人須知檔名寫 IFS
    "082": (r"_TS_.*\.doc$", r"_IIS_.*\.doc$", True),  # BNP：只有 Word（舊版 .DOC），另有英文 DOCX
    "086": (r"^PS_.*\.pdf$", r"^IIS_.*\.pdf$", False),  # CA：TS_ 開頭是英文版
    "092": (r"^\d{12}_TS\.pdf$", r"^\d{12}_IIS\.pdf$", False),  # UBS：另有同內容的 Word
    "147": (r"_ATS_.*\.docx$", r"_AD_.*\.docx$", True),  # MS：只有 Word，檔名 _TS_ 的是英文版
    "148": (r"_Product Statement_.*\.pdf$", r"_IIS_.*\.pdf$", False),  # GS
    "156": (r"_CN_TS_.*\.pdf$", r"_CN_IIS_.*\.pdf$", False),  # Nomura
    "159": (r"^\d{12}_TS\.pdf$", r"^\d{12}_IIS\.pdf$", False),  # Citi：資料夾裡已有標準檔名 PDF，另有 Word 原檔
    "325": (r"_CH_TS_.*\.pdf$", r"_CH_KIID_.*\.pdf$", False),  # HSBC
    "326": (r"Chi TS_.*\.pdf$", r"Chi IIS_.*\.pdf$", False),  # BBVA
    "328": (r"^Final_TS_.*\.pdf$", r"^Final_IIS_.*\.pdf$", False),  # Natixis
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
        assert re.search(ts_mark, names[DocKind.TS], re.IGNORECASE), (o.tdcc, names)
        assert re.search(iis_mark, names[DocKind.IIS], re.IGNORECASE), (o.tdcc, names)
        assert all(d.from_word == from_word for d in o.documents), (o.tdcc, names)
        if o.tdcc[:3] in FILL_ISSUERS:  # MS 封面空白的受託機構商品代號，轉出的 PDF 要已補上（#56）
            text = first_page_text(receipt.batch_dir / f"{o.tdcc}_TS.pdf") or ""
            assert f"受託機構商品代號：{o.tdcc}" in re.sub(r"\s+", "", text), o.tdcc
