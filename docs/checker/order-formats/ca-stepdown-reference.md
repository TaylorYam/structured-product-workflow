# 下單資料格式：CA FCN_Stepdown（參考條件表）

- 設定檔：所有上手共用的 [`config/reference_sheet.toml`](../../../config/reference_sheet.toml)
- 狀態：規格（workflow #546）；實作另開 PR。共用的版面、配對、回填規則見[參考條件表格式](reference-sheet.md)；和 [CA FCN](ca-fcn-reference.md) 相同的不重寫，本文件只寫 FCN_Stepdown 的差異

## 1. 結構

和 CA FCN 相同（商品代號前 3 碼 `086`，`發行機構` 欄寫 `CA`）。商品種類看 `Product` = `FCN_Stepdown`：說明書辨識到的範本是 `ca-zh-stepdown-pd` 時，該列 Product 必須是 `FCN_Stepdown`；Product 是 `FCN_Stepdown`、說明書卻是 CA FCN 範本（或反過來）→ 商品種類不一致，轉人工覆核（共用 `template.product_family`）。投資人須知用 CA FCN 的 `ca-zh-iis`，FCN、FCN_Express、FCN_Stepdown 都接受（[核對規則](../rules/ca-stepdown-check-rules.md) §3.6）。

開放自動核對：`config/issuer_prefixes.toml` `[auto_check_products]` 的 `FCN_Stepdown` 加 `CA`（實作 PR，workflow #546）。

## 2. 欄位對照（只列和 CA FCN 不同的）

| Excel 欄 | 標準欄位 | 說明書來源（見[範本規格](../templates/ca-zh-stepdown.md)） |
|---|---|---|
| Product | `product` | 不和說明書比；= `FCN_Stepdown` 才能用 Stepdown 範本（§1） |
| KO(%) | `ko_pct` | 第 9 項 `(1)(b)` 每期 KO 表**第 1 列**（KO 第 1 期）的 `期初訂價之N.NN%`（CA FCN 是第 9 項價格表表頭 `自動提前出場價 (期初訂價之Y% )`） |
| KO降幅(%) | `ko_step_pct` | 每期 KO 表相鄰兩期的差（每期相同）；表上空白、`-`、0、負數、非數字 → 轉人工覆核（共用 `field.ko_step_pct`，#544） |
| （沒有單獨的欄） | `ko_levels` | 每期 KO 表每一期的百分比 = `KO(%)` − `KO降幅(%)` ×（期數 − 1） |
| KO(memo) | `ko_memory` | 照 CA FCN 兩句判斷，另收兩種新措辭（範本規格 §2.2） |
| UL_n_KO價 | `underlying_prices` 的 KO 價 | 第 1 期 KO 價 = 第 9 項價格表期初訂價 × 第 1 期 KO%，四捨五入（half-up）到 4 位（價格表沒有 KO 欄） |
| 比價日_1～12 | `autocall_schedule` | §4 |

其他欄（代號、ISIN、幣別、面額、各日期、天期、K／KI %、KO／KI 型態、Non-Call、標的、期初訂價、執行價、下限價、期初定價、年利率）照 CA FCN。

## 3. 允許值與 Stepdown 差異

| 欄 | 值 | 說明 |
|---|---|---|
| Product | `FCN_Stepdown` | 不分大小寫（共用） |
| KO降幅(%) | 大於 0 的數字 | 3 份樣本都是整數百分點 |
| KO(Freq)、KO(memo)、Non-Call | `D`；`Y`；`1` | 3 份樣本都是 D 型、記憶式、k = 1；P 型現行版面、非記憶式、k ≥ 2 沒有樣本（範本不支援） |
| KI(Freq) | `-`、`AM` | 樣本無 KI 2 份、到期觀察 1 份；KI 每日照 CA FCN 讀法；每期觀察照 CA 不支援樣態 |
| 承作幣別 | `USD` | 3 份都是美元；其他幣別照 CA FCN 讀法 |

## 4. 比價日填法

D 型：**每一期都填**：`比價日_{Non-Call}`～`比價日_{天期}` = 第 9 項 `(1)(a)` 配息表第 Non-Call 期到末期每一期的 `配息期結束日`（= 每期 KO 表同一列的 `自動提前出場觀察期結束日`；末期 = 期末訂價日）。CA FCN D 型只填開始與最後兩格；有 KO 降幅的每日型要知道每一期從哪天到哪天，比價時才能用對那一期的 KO%（ADR 0033、[參考條件表格式](reference-sheet.md) `KO降幅(%)`，同 BARC、Natixis FCN_Stepdown）。表上已有值的格子照常比對，不同時判不一致、保留原值。

## 5. 不核對的欄位

同 CA FCN（`期初定價` 只檢查是允許值、`UL_n_Memo` 不核對等）。
