# 下單資料格式：Natixis FCN_Stepdown（參考條件表）

- 設定檔：所有上手共用的 [`config/reference_sheet.toml`](../../../config/reference_sheet.toml)
- 狀態：已實作（workflow #545）。共用的版面、配對、回填規則見[參考條件表格式](reference-sheet.md)；和 [Natixis FCN](natixis-fcn-reference.md) 相同的不重寫，本文件只寫 FCN_Stepdown 的差異

## 1. 結構

和 Natixis FCN 相同（商品代號前 3 碼 `328`，`發行機構` 欄寫 `Natixis`）。商品種類看 `Product` = `FCN_Stepdown`：辨識到的範本是 `natixis-zh-stepdown-pd`／`natixis-zh-stepdown-iis` 時，該列 Product 必須是 `FCN_Stepdown`；Product 是 `FCN_Stepdown`、文件卻是 Natixis FCN 範本（或反過來）→ 商品種類不一致，轉人工覆核（共用 `template.product_family`）。

開放自動核對：`config/issuer_prefixes.toml` `[auto_check_products]` 的 `FCN_Stepdown` 加 `NATIXIS`（實作 PR，workflow #545）。

## 2. 欄位對照（只列和 Natixis FCN 不同的）

| Excel 欄 | 標準欄位 | 說明書來源（見[範本規格](../templates/natixis-zh-stepdown.md)） |
|---|---|---|
| Product | `product` | 不和說明書比；= `FCN_Stepdown` 才能用 Stepdown 範本（§1） |
| KO(%) | `ko_pct` | 第 14 項每期 KO 表**第 1 列**（KO 第 1 期）的百分比（Natixis FCN 是第 14 項 `期初進場價格之Y%`） |
| KO降幅(%) | `ko_step_pct` | 第 14 項每期 KO 表相鄰兩期的差（每期相同）；表上空白、`-`、0、負數、非數字 → 轉人工覆核（共用 `field.ko_step_pct`，#544） |
| （沒有單獨的欄） | `ko_levels` | 第 14 項每期 KO 表每一期的百分比 = `KO(%)` − `KO降幅(%)` ×（期數 − 1） |
| UL_n_KO價 | `underlying_prices` 的 KO 價 | 第 1 期 KO 價 = 第 9 項期初進場價格 × 第 1 期 KO%，四捨五入（half-up）到 4 位（第 9 項沒有 KO 欄） |
| 比價日_1～12 | `autocall_schedule` | §4 |

其他欄（代號、ISIN、幣別、面額、各日期、天期、K／KI %、KO／KI 型態、記憶式、Non-Call、標的、期初進場／執行／下限價、期初定價、年利率）照 Natixis FCN。

## 3. 允許值與 Stepdown 差異

| 欄 | 值 | 說明 |
|---|---|---|
| Product | `FCN_Stepdown` | 不分大小寫（共用） |
| KO降幅(%) | 大於 0 的數字 | 7 份樣本都是整數百分點 |
| KO(Freq)、KO(memo) | `D` 或 `P`；`Y` | 樣本 D 型 5 份、P 型 2 份，都是記憶式；非記憶式沒有樣本（範本不支援） |
| KI(Freq) | `-`、`AM` | 樣本到期觀察 5 份、無 KI 2 份；KI 每日、每期觀察照 Natixis FCN 讀法 |
| 承作幣別 | `USD`、`JPY`、`AUD` | 照 Natixis FCN |

## 4. 比價日填法

- P 型：同 Natixis FCN（第 12(7) 項觀察日表每列的 `自動提前到期觀察日(t)`，從 `比價日_{Non-Call}` 填到 `比價日_{天期}`）。
- D 型：**每一期都填**：`比價日_{Non-Call}`～`比價日_{天期}` = 第 12 項 (6) 配息觀察期間表第 Non-Call 期到末期每一期的 `配息觀察期間結束日`（末期 = 最終評價日）。Natixis FCN D 型只填開始與最後兩格；有 KO 降幅的每日型要知道每一期從哪天到哪天，比價時才能用對那一期的 KO%（ADR 0033、[參考條件表格式](reference-sheet.md) `KO降幅(%)`，同 BARC FCN_Stepdown）。表上已有值的格子照常比對，不同時判不一致、保留原值。

## 5. 不核對的欄位

同 Natixis FCN（`期初定價` 只檢查是允許值、`UL_n_Memo` 不核對等）。
