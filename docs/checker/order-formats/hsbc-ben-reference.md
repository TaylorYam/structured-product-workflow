# 下單資料格式：HSBC BEN（參考條件表）

- 設定檔：所有上手共用的 [`config/reference_sheet.toml`](../../../config/reference_sheet.toml)
- 狀態：規格；實作另開（workflow #375）。共用的版面、配對、回填規則見[參考條件表格式](reference-sheet.md)，本文件只寫 HSBC BEN 的說明書來源與差異；和 HSBC FCN 相同的欄位見 [HSBC 下單資料格式](hsbc-fcn-reference.md)
- 依據：2026-10-30 探勘與開發者螢光標記（workflow #375）。**作業人員獨立標記先略過，2026-10-30 使用者決定**。標「待使用者確認」的是暫定的保守做法，詳見本機疑慮記錄

## 1. 結構

同 HSBC FCN：`發行機構` 欄寫 `HSBC`、商品代號前 3 碼 `325`。`Product` 欄是 `BEN`（Maxi、Digital 都是 `BEN`，不另分商品種類，workflow ADR 0029）；資料庫匯出的參考條件表一定有這欄。

BEN 沿用 FCN 那組欄（ADR 0029，比照 Super Bull 的 ADR 0028）：`Coupon p.a. (%)` 存紅利、`Non-Call(月)` = 天期、比價日只放最終比價日；另有選填欄「參與率(%)」（#373）。

## 2. 欄位對照

和 [HSBC 下單資料格式](hsbc-fcn-reference.md) §2 相同的欄（TDCC Code、ISIN Code、單位面額、承作幣別、交易日／發行日／最終比價日／到期日、天期(月)、UL_n、UL_n_進場價）不重列。不同的：

| Excel 欄 | 標準欄位 | 說明書來源（見[範本規格](../templates/hsbc-zh-ben.md)） |
|---|---|---|
| Product | — | `BEN`：決定能不能自動核對（`[auto_check_products]`）、選範本（範本規格 §8 商品種類一致）、Non-Call 規則（= 天期） |
| Coupon p.a. (%) | `coupon_pa_pct` | 紅利（不年化）：Maxi §11 `紅利配息率為X%`；Digital §11(2) `紅利配息率：X% %` |
| 參與率(%) | `participation_pct` | 說明書沒有明寫（§17(2) `本商品不適用`），由 §11(1) 到期公式推得：Maxi 100、Digital 0（待使用者確認，詳見本機疑慮記錄）。沒有這欄或空白 → 轉人工覆核 |
| K(%) | `strike_pct` | §11 `執行價為期初股價 × X%` |
| KO(%) | `ko_pct` | 說明書沒有 KO：照來源存、不核對（ADR 0029） |
| KI(%) | `ki_pct` | 無 KI，表上必須是 `-` |
| KO(Freq) | `ko_observation` | 固定 `P`（ADR 0029 起單匯入一律存 `Period End`） |
| KO(memo) | `ko_memory` | 固定 `N` |
| KI(Freq) | `ki_type` | 無 KI（全文沒有觸及、§12(1) 沒有觸及價欄）→ `-` |
| Non-Call(月) | `first_callable_period` | = 天期（`PRODUCT_RULES` 的 BEN `maturity_only`） |
| 比價日_1～12 | `autocall_date_n` | 見 §4 |
| UL_n_執行價 | `underlying_n_strike_price` | §12(1) 價格表的執行價欄 |
| UL_n_KO價 | `underlying_n_ko_price` | 說明書沒有 KO 價欄：不核對 |
| UL_n_下限價 | `underlying_n_ki_price` | 無 KI，表上是 `-` 或空白 |

## 3. 允許值

| 欄 | 值 | 其他值 |
|---|---|---|
| KO(Freq) | `P` | 樣態不符，轉人工覆核（核對規則 §3.1） |
| KO(memo) | `N` | 同上 |
| KI(Freq) | `-` | 同上 |
| Non-Call(月) | = 天期(月) | 同上 |
| 參與率(%) | Maxi `100`、Digital `0`（由說明書型態決定） | 和說明書推得的不同 → 不一致；空白 → 轉人工覆核 |
| 承作幣別 | `USD` | 其他轉人工覆核（待樣本驗證，核對規則 §3.1） |
| UL_1～UL_5 | Maxi 2～3 檔、Digital 1 檔 | Maxi 1 檔或 4 檔以上、Digital 2 檔以上轉人工覆核（待樣本驗證；待使用者確認，詳見本機疑慮記錄） |
| 期初定價 | 開盤價、收盤價、VWAP | VWAP 照 #122（說明書沒寫期初定價方式，同 HSBC FCN） |

## 4. 比價日填法

BEN Non-Call = 天期，照[參考條件表格式](reference-sheet.md) §4 的「Non-Call = 天期時只填最後一格」：

| 格 | 填法 | 說明書日期 |
|---|---|---|
| `比價日_{天期}` | 最後評價日 | §15(5)（= 最終比價日；BEN 只做到期判定，ADR 0029） |
| 其他格 | `-` | — |

例（虛構）：4 個月期 → `比價日_4` = 最後評價日，其餘 `-`。

表上空白時通過後回填；有值時逐格比對，不同就不一致、保留原值（共用 `backfill.compare_dates`）。

## 5. 標的順序

參考條件表的 `UL_n` 順序可以和說明書不同（探勘 27 份裡 13 份不同、代號相同）：HSBC BEN 以彭博代號配對各標的與價格，不管順序（比照 #314 HSBC Super Bull；設定 `[underlyings] any_order_products` 的 `BEN` 加 `HSBC`）。標的數量仍要相同。HSBC FCN 照舊依順序比對。

## 6. 不核對的欄位

同 HSBC FCN（`UL_n_Memo`、`私銀註記`、`UF`、無表頭欄等；`期初定價` 只檢查是允許值）；另外 `KO(%)`、`UL_n_KO價` 不核對（§2）。

## 7. 探勘時發現的表上問題（2026-10-30）

- 13 份樣本的標的順序和說明書不同：以代號對齊後一致（§5）。
- 1 份舊總表的一檔標的交易所碼和說明書不同（價格一致）：不改資料、不放寬規則，開放後照常判不一致（待使用者確認，詳見本機疑慮記錄）。
- 2 份舊總表 KO% ≠ K%：說明書沒有 KO，KO% 不核對（ADR 0029）。
- 其餘欄位（代號、ISIN、幣別、面額、各日期、天期、K%、紅利、Non-Call、比價日、各標的價格）全部相符。
