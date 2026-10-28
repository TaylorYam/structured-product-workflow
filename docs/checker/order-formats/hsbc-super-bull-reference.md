# 下單資料格式：HSBC Super Bull（參考條件表）

- 設定檔：所有上手共用的 [`config/reference_sheet.toml`](../../../config/reference_sheet.toml)
- 狀態：已實作（workflow #314；`config/reference_sheet.toml` 的 `[underlyings] any_order_products`）。共用的版面、配對、回填規則見[參考條件表格式](reference-sheet.md)，本文件只寫 HSBC Super Bull 的說明書來源與差異；和 HSBC FCN 相同的欄位見 [HSBC 下單資料格式](hsbc-fcn-reference.md)

## 1. 結構

同 HSBC FCN：`發行機構` 欄寫 `HSBC`、商品代號前 3 碼 `325`。`Product` 欄是 `Super Bull`（不分大小寫）；資料庫匯出的參考條件表一定有這欄（workflow #310）。

Super Bull 沿用 FCN 那組欄（workflow ADR 0028）：`Coupon p.a. (%)` 存出場紅利、`Non-Call(月)` 是第一個決定日的月數、比價日照舊總表放。

## 2. 欄位對照

和 [HSBC 下單資料格式](hsbc-fcn-reference.md) §2 相同的欄（TDCC Code、ISIN Code、單位面額、承作幣別、交易日／發行日／最終比價日／到期日、天期(月)、UL_n、UL_n_進場價）不重列。不同的：

| Excel 欄 | 標準欄位 | 說明書來源（見[範本規格](../templates/hsbc-zh-super-bull.md)） |
|---|---|---|
| Product | — | `Super Bull`：決定能不能自動核對（`[auto_check_products]`）、選範本（範本規格 §8 商品種類一致）、Non-Call 規則（至少 1） |
| Coupon p.a. (%) | `coupon_pa_pct` | §11(1) `自動提前贖回配息為X%`（出場紅利，不年化） |
| KO(%) | `ko_pct` | §11(1) 提前贖回表每列 `自動提前贖回價`（每列相同） |
| K(%) | `strike_pct` | §11(2) `執行價為期初股價 × X%` |
| KI(%) | `ki_pct` | 無 KI，表上必須是 `-` |
| KO(Freq) | `ko_observation` | §11(1) 四欄提前贖回表 → `P` |
| KO(memo) | `ko_memory` | §11(1) 事件句 → `N` |
| KI(Freq) | `ki_type` | 沒有觸及不保本、§12(1) 沒有觸及不保本價格欄 → `-` |
| Non-Call(月) | `first_callable_period` | 提前贖回表第 1 列決定日距發行日的月數（範本規格 §4.1） |
| 比價日_1～12 | `autocall_date_n` | 見 §4 |
| UL_n_KO價、UL_n_執行價 | `underlying_n_ko_price`、`underlying_n_strike_price` | §12(1) 價格表（KO 價欄在執行價欄前面，依欄名讀） |
| UL_n_下限價 | `underlying_n_ki_price` | 無 KI，表上是 `-` 或空白 |

## 3. 允許值

| 欄 | 值 | 其他值 |
|---|---|---|
| KO(Freq) | `P` | 樣態不符，轉人工覆核（核對規則 §3.1） |
| KO(memo) | `N` | 同上 |
| KI(Freq) | `-` | 同上 |
| KO(%) | = K(%) | ≠ K(%) 轉人工覆核（沒有樣本，核對規則 §3.1） |
| 承作幣別 | `USD` | 其他轉人工覆核（同上） |
| UL_1～UL_5 | 2～5 檔 | 1 檔轉人工覆核（同上）；4 檔以上開放、待樣本驗證 |
| 期初定價 | 開盤價、收盤價、VWAP | VWAP 開放、待樣本驗證（照 #122） |

起單建檔的 `KO Type`、`Barrier Type` 經資料庫匯出成上表的 KO(Freq)、KO(memo)、KI(Freq)（`Period End` → `P`＋`N`、`NONE` → `-`）；`Observation Frequency (m)` 不在參考條件表，不核對。

## 4. 比價日填法（Super Bull，所有上手共用；見[參考條件表格式](reference-sheet.md) §4、[BNP Super Bull 下單資料格式](bnp-super-bull-reference.md) §4）

| 格 | 填法 | 說明書日期 |
|---|---|---|
| `比價日_m` | 每個決定日放在它距發行日的月數 m 那一格 | §11(1) 提前贖回表各列的 `自動提前贖回決定日`（**不是** `自動提前贖回付款日`） |
| `比價日_{天期}` | 最後評價日 | §15(5)（= 最終比價日；比價時不算 KO，ADR 0028） |
| 其他格 | `-` | — |

例（虛構）：6 個月期、第 3 個月決定 → `比價日_3` = 決定日、`比價日_6` = 最後評價日，其餘 `-`。

表上空白時通過後回填；有值時逐格比對，不同就不一致、保留原值（共用 `backfill.compare_dates`）。決定日 2 個以上轉人工覆核、不回填（範本規格 §4.1）。

## 5. 標的順序

參考條件表的 `UL_n` 順序可以和說明書不同（探勘 5 份裡 3 份不同）：HSBC Super Bull 以彭博代號配對各標的與價格，不管順序（#314 已確認；設定 `[underlyings] any_order_products = { "Super Bull" = ["HSBC"] }`；共用規則的「不看順序」，[共用核對規則](../rules/shared-check-rules.md) §5）。標的數量仍要相同。HSBC FCN 照舊依順序比對。

## 6. 不核對的欄位

同 HSBC FCN（`UL_n_Memo`、`私銀註記`、`UF`、無表頭欄等；`期初定價` 只檢查是允許值）。

## 7. 探勘時發現的表上問題（2026-10-28）

- 3 份樣本的標的順序和說明書不同：以代碼對齊後一致（§5）。
- 其餘欄位（代號、ISIN、幣別、面額、各日期、天期、K%、KO%、出場紅利、Non-Call、比價日、各標的價格；KO 價表上存未四捨五入的乘積，四捨五入 4 位後一致）全部相符。
