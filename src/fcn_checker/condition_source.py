"""核對條件來源（#445）：核對的是哪份條件——型別化的列（`ReferenceSheet`：列＋表上沒有某檔的原因）加一個憑證。

兩個 adapter：
- Excel 參考條件表（`excel`）：讀檔成列；憑證是檔案路徑，內容的 sha256 在來源快照（`SourceSnapshot`）裡，存檔、
  回報包前照舊確認檔案沒變。核對結果檔與回報包從原表排版。
- 資料庫（`database`）：流程端（`sp_workflow.check_source`）交來排好的表（`Table`），不寫檔；憑證是表內容的
  sha256。列在記憶體裡不會變，所以來源快照不必管它；資料庫在核對後被改，由寫回前比對（現在的條件建出的列 ≠ 憑證那時的列）
  擋下。核對結果檔與回報包從記憶體中排好的表排版，內容和以前產生的參考條件表檔案相同。

來源快照、存檔、回報包、寫回前比對都拿這裡的列與憑證，不需要一個可以重開的檔案。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from openpyxl.workbook.workbook import Workbook

from .config import ReferenceFormat
from .ingestion import SourceSnapshot
from .orders.reference import ReferenceSheet, Table, load_reference_sheet, table_sheet

DATABASE = "資料庫"  # 資料庫來源的名稱（排好的表的工作表名稱、核對紀錄的來源）
DATABASE_STEM = "參考條件表_資料庫"  # 資料庫來源的核對結果檔、回報包檔名開頭
DATE_FORMAT = "yyyy/m/d"  # 資料庫來源排表的日期格式（同以前產生的參考條件表）


@dataclass(frozen=True)
class ConditionSource:
    """一次核對的條件來源：型別化的列加憑證（Excel：檔案路徑；資料庫：表內容的 sha256）。"""

    sheet: ReferenceSheet
    name: str  # 給人看的來源名稱：Excel 檔名，資料庫寫「資料庫」
    stem: str  # 核對結果檔、回報包的檔名開頭
    path: Path | None = None  # Excel 來源的檔案
    table: Table | None = field(default=None, repr=False)  # 資料庫來源排好的表
    digest: str | None = None  # 資料庫來源：表內容的 sha256

    @classmethod
    def excel(cls, path: Path, fmt: ReferenceFormat) -> ConditionSource:
        """讀 Excel 參考條件表；讀不到或格式不對時丟出 IngestionError（整批不核對）。"""
        path = Path(path)
        return cls(load_reference_sheet(path, fmt), path.name, path.stem, path=path)

    @classmethod
    def database(cls, fmt: ReferenceFormat, table: Table, absent: Mapping[str, str]) -> ConditionSource:
        """資料庫交來的表（`absent`：表上沒有某檔的原因，配對不到時寫進結果）。"""
        sheet = table_sheet(fmt, DATABASE, table, absent)
        return cls(sheet, DATABASE, DATABASE_STEM, table=table, digest=_digest(table, absent))

    @property
    def files(self) -> tuple[Path, ...]:
        """來源快照要取 hash 的檔案（資料庫來源沒有）。"""
        return (self.path,) if self.path is not None else ()

    def meta(self, snapshot: SourceSnapshot) -> dict[str, Any]:
        """核對紀錄的來源憑證：Excel 是檔名、路徑與 sha256；資料庫是來源名稱與表內容的 sha256。"""
        if self.path is not None:
            return snapshot.meta(self.path)
        return {"source": DATABASE, "sha256": self.digest}

    def workbook(self) -> Workbook:
        """排核對結果檔與回報包用的參考條件表（讀值）：Excel 開原檔、資料庫是記憶體中排好的表；讀不到時 IngestionError。"""
        if self.path is not None:
            from .result_file import open_reference

            return open_reference(self.path)
        assert self.table is not None
        return self.table.workbook(DATABASE, DATE_FORMAT)


def _digest(table: Table, absent: Mapping[str, str]) -> str:
    """表內容的 sha256：固定的序列（docs/checker/data-contract.md §5）—— JSON `{"headers", "rows", "absent"}`，
    鍵排序、不轉義中文、不留空白；值照表上的型別寫成固定字串（`_canonical`），沒有列的 TDCC（`absent`）依代號排序。"""
    payload = {
        "headers": list(table.headers),
        "rows": [[_canonical(v) for v in row] for row in table.rows],
        "absent": dict(sorted(absent.items())),
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical(v: Any) -> Any:
    """None、布林、字串照原樣；數字（int、Decimal、float）寫成十進位字串；日期、時間寫 ISO 字串。"""
    if v is None or isinstance(v, bool | str):
        return v
    if isinstance(v, int | Decimal):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, dt.date):
        return v.isoformat()
    return str(v)
