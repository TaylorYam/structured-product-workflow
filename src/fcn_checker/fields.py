"""標的價格欄位的字彙：核對（`fcn_checker`）與流程（`sp_workflow`）共用的唯一宣告（#433）。

每檔標的有四個價格：進場價、執行價、下限價、KO 價。同一個價格在各處的名字：
- `key`：價格的鍵（`OrderRecord.price`、說明書價格表 `PriceRow.prices`、`UnderlyingPrice` 的屬性）。
- `column`：`workflow.underlying_price` 的欄名，也是參考條件表標準欄位 `underlying_{i}_{column}` 的後半。
- `header`：參考條件表欄名 `UL_{i}_{header}` 的後半（例：UL_1_進場價；欄名本身在 `config/reference_sheet.toml`）。
- `label`：核對結果與錯訊用的中文（KO 價中間有空白）。

這個 module 不 import 兩個套件的任何 module，兩個套件都能 import（`fcn_checker` 只在 `cli`、`panel` 延遲 import `sp_workflow`）。
作業人員指定版面的輸出檔（商品總表、曝險表）的表頭文字不跟這裡，只用 `key` 對到價格。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PriceField:
    """一個標的價格欄在各處的名字。"""

    key: str
    column: str
    header: str
    label: str


# 依參考條件表與 `workflow.underlying_price` 的順序
PRICE_FIELDS = (
    PriceField("initial", "initial_price", "進場價", "進場價"),
    PriceField("strike", "strike_price", "執行價", "執行價"),
    PriceField("ki", "ki_price", "下限價", "下限價"),
    PriceField("ko", "ko_price", "KO價", "KO 價"),
)
PRICE_COLUMNS = {f.key: f.column for f in PRICE_FIELDS}  # 價格的鍵 → `workflow.underlying_price` 的欄
PRICE_SQL = ", ".join(PRICE_COLUMNS.values())  # SQL 的四個價格欄（依 `PRICE_FIELDS` 的順序）
