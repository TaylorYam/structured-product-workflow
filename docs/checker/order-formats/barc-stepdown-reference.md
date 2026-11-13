# 下單資料格式：BARC FCN_Stepdown（參考條件表）

- 設定檔：所有上手共用的 [`config/reference_sheet.toml`](../../../config/reference_sheet.toml)
- 狀態：規格（workflow #544）；實作另開 PR。共用的版面、配對、回填規則見[參考條件表格式](reference-sheet.md)；和 BARC FCN 相同的不重寫，本文件只寫 FCN_Stepdown 的差異

## 1. 結構

和 BARC FCN 相同（商品代號前 3 碼 `029`，`發行機構` 欄寫 `Barclays`）。商品種類看 `Product` = `FCN_Stepdown`：辨識到的範本是 `barc-zh-stepdown-pd`／`barc-zh-stepdown-iis` 時，該列 Product 必須是 `FCN_Stepdown`；Product 是 `FCN_Stepdown`、文件卻是 BARC FCN 範本（或反過來）→ 商品種類不一致，轉人工覆核（共用 `template.product_family`）。

開放自動核對：`config/issuer_prefixes.toml` `[auto_check_products]` 新增 `FCN_Stepdown = ["BARC"]`（實作 PR，workflow #544）。

## 2. 欄位對照（只列和 BARC FCN 不同的）

| Excel 欄 | 標準欄位 | 說明書來源（見[範本規格](../templates/barc-zh-stepdown.md)） |
|---|---|---|
| Product | `product` | 不和說明書比；= `FCN_Stepdown` 才能用 Stepdown 範本（§1） |
| KO(%) | `ko_pct` | 第 13 條 (7) 提前出場表**第 1 期**（第一個可提前出場那一列）的觸發百分比（BARC FCN 是第 15 條定義句） |
| KO降幅(%) | `ko_step_pct` | 提前出場表相鄰兩期觸發百分比的差（每期相同）；表上空白或 `-` → 轉人工覆核（Stepdown 一定要有降幅，ADR 0033） |
| （沒有單獨的欄） | `ko_levels` | 提前出場表每一期的觸發百分比 = `KO(%)` − `KO降幅(%)` ×（期數 − 1） |
| UL_n_KO價 | `underlying_prices` 的 KO 價 | 第 1 期 KO 價 = 第 15 條最初價格 × 第 1 期 KO%，四捨五入（half-up）到 4 位（說明書沒有 KO 價欄） |
| KO(memo) | `ko_memory` | 第 13 條 (7) 記憶事件句（BARC FCN 看第 15 條觸發價格名詞）；樣本都是 `Y` |
| 比價日_1～12 | `autocall_schedule` | §4 |

其他欄（代號、ISIN、幣別、面額、各日期、天期、K／KI %、KO／KI 型態、Non-Call、標的、進場／執行／下限價、期初定價、年利率）照 BARC FCN。

## 3. 允許值與 Stepdown 差異

| 欄 | 值 | 說明 |
|---|---|---|
| Product | `FCN_Stepdown` | 不分大小寫（共用） |
| KO降幅(%) | 大於 0 的數字 | 6 份樣本都是整數百分點；空白、`-`、0 → 轉人工覆核 |
| KO(Freq)、KO(memo) | `D` 或 `P`；`Y` | 樣本 D 型 4 份、P 型 2 份，都是記憶式；非記憶式沒有樣本（範本不支援） |
| KI(Freq) | `-`、`AM` | 樣本到期觀察 5 份、無 KI 1 份；KI 每期觀察照 BARC 不支援樣態 |
| 承作幣別 | `USD` | 樣本都是美元；其他幣別照 BARC FCN 讀法（沒有 Stepdown 樣本） |

## 4. 比價日填法

- P 型：同 BARC FCN（`比價日_{Non-Call}`～`比價日_{期數}` = 各期自動提前出場評價日）。
- D 型：**每一期都填**：`比價日_{Non-Call}`～`比價日_{期數}` = 各期提前出場觀察期的期末日（最後一期 = 最終比價日）。BARC FCN D 型只填開始與最後兩格；有 KO 降幅的每日型要知道每一期從哪天到哪天，比價時才能用對那一期的 KO%（ADR 0033、[參考條件表格式](reference-sheet.md) `KO降幅(%)`）。表上已有值的格子照常比對，不同時判不一致、保留原值。

## 5. 不核對的欄位

同 BARC FCN（`期初定價` 只檢查是允許值、`UL_n_Memo` 不核對等）。
