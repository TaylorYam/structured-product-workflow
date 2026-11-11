"""欄位字彙：核對（`fcn_checker`）與流程（`sp_workflow`）共用的唯一宣告。

- 標的價格欄（#433）：`PRICE_FIELDS`、`PRICE_COLUMNS`、`PRICE_SQL`。
- 商品條件畫面的欄名（#434）：商品規則（`fcn_checker.products`）回答固定、必填、要空白、空白怎麼補時用的名字。

標的價格欄：

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

# 商品條件畫面的欄名（起單建檔的欄名，`sp_workflow.product_record.FIELDS`）：商品規則（`fcn_checker.products`）
# 回答哪些欄固定、必填、要空白、空白怎麼補時用這些名字（#434）
NON_CALL_FIELD = "Guaranteed Periods (m)"
STRIKE_PCT_FIELD = "Strike (%)"
KO_TYPE_FIELD = "KO Type"
KO_BARRIER_FIELD = "KO Barrier (%)"
# KO 降幅（Stepdown，#537、ADR 0033）：KO% 每期固定往下降的百分點；KO Barrier (%) 是第 1 期。起單建檔沒有這欄
KO_STEP_FIELD = "KO Step Down (%)"
BARRIER_TYPE_FIELD = "Barrier Type"
KI_BARRIER_FIELD = "KI Barrier (%)"
COUPON_BARRIER_FIELD = "Coupon Barrier (%)"  # 配息門檻（DACN，#367；BEN 的紅利門檻，#406）：起單建檔沒有這欄
PARTICIPATION_FIELD = "Participation (%)"  # 參與率（%）：只有 BEN 有（#373、ADR 0029）；起單建檔沒有這欄
LOOKBACK_FIELD = "Lookback end date"  # 回顧結束日：只有 lookback 的 BEN 有（#374、ADR 0029）；起單建檔沒有這欄
