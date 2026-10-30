# 下單資料格式：CA BEN（參考條件表）

- 設定檔：所有上手共用的 [`config/reference_sheet.toml`](../../../config/reference_sheet.toml)
- 狀態：規格；實作另開（workflow #377）。共用的版面、配對、回填規則見[參考條件表格式](reference-sheet.md)，本文件只寫 CA BEN 的說明書來源與差異；和 CA FCN 相同的欄位見 [CA 下單資料格式](ca-fcn-reference.md)
- 依據：2026-10-30 探勘與開發者螢光標記（workflow #377）。**作業人員獨立標記先略過，2026-10-30 使用者決定**。標「待使用者確認（BEN-377-nn）」的是暫定的保守做法，編號對到本機疑慮記錄

## 1. 結構

同 CA FCN：`發行機構` 欄寫 `CA`、商品代號前 3 碼 `086`。`Product` 欄是 `BEN`（Bonus、Digital 都是 `BEN`，不另分商品種類，workflow ADR 0029）；資料庫匯出的參考條件表一定有這欄。

BEN 沿用 FCN 那組欄（ADR 0029）：`Coupon p.a. (%)` 存紅利、`Non-Call(月)` = 天期、比價日只放最終比價日；另有選填欄「參與率(%)」（#373）。CA BEN 沒有新欄。

## 2. 欄位對照

和 [CA 下單資料格式](ca-fcn-reference.md) §2 相同的欄（TDCC Code、ISIN Code、單位面額、承作幣別、交易日（期初訂價日）／發行日／最終比價日／到期日、天期(月)、期初定價、UL_n、UL_n_進場價／執行價）不重列。不同的：

| Excel 欄 | 標準欄位 | 說明書來源（見[範本規格](../templates/ca-zh-ben.md)） |
|---|---|---|
| Product | — | `BEN`：決定能不能自動核對（`[auto_check_products]`）、選範本（範本規格 §8 商品種類一致）、Non-Call 規則（= 天期） |
| Coupon p.a. (%) | `coupon_pa_pct` | 紅利（不年化）：第 9 項 `紅利率：X%。`。Digital 舊總表存紅利水準 − 100（Digital 轉人工，只記錄；BEN-377-03） |
| 參與率(%) | `participation_pct` | 第 9 項 `參與率：X%。`（CA 明寫，Bonus 樣本 100）。沒有這欄或空白 → 轉人工覆核（同 HSBC、MS BEN） |
| K(%) | `strike_pct` | 第 9 項價格表表頭 `執行價 (期初訂價之X% )` |
| KO(%) | `ko_pct` | 說明書沒有 KO：照來源存、不核對（ADR 0029） |
| KI(%) | `ki_pct` | 無 KI，表上必須是 `-` |
| KO(Freq) | `ko_observation` | 固定 `P`（ADR 0029 起單匯入一律存 `Period End`） |
| KO(memo) | `ko_memory` | 固定 `N` |
| KI(Freq) | `ki_type` | 無 KI → `-` |
| Non-Call(月) | `first_callable_period` | = 天期（`PRODUCT_RULES` 的 BEN `maturity_only`） |
| 比價日_1～12 | `autocall_date_n` | 見 §4 |
| UL_n_KO價 | `underlying_n_ko_price` | 說明書沒有 KO 價：不核對（舊總表填的是進場價） |
| UL_n_下限價 | `underlying_n_ki_price` | 無 KI，表上是 `-` 或空白 |

## 3. 允許值

| 欄 | 值 | 其他值 |
|---|---|---|
| KO(Freq) | `P` | 樣態不符，轉人工覆核（核對規則 §3.1） |
| KO(memo) | `N` | 同上 |
| KI(Freq) | `-` | 同上 |
| Non-Call(月) | = 天期(月) | 同上 |
| 參與率(%) | Bonus `100`（= 說明書第 9 項） | 和說明書不同 → 不一致；空白 → 轉人工覆核 |
| 承作幣別 | `USD` | `CNH` 轉人工覆核（同 CA Super Bull）；其他幣別轉人工覆核、錯訊寫「待樣本驗證」（待使用者確認（BEN-377-07），詳見本機疑慮記錄） |
| UL_1～UL_5 | Bonus 2 檔 | 1 檔或 3 檔以上轉人工覆核（同上，BEN-377-07） |
| K(%) | `100` | 其他值轉人工覆核（同上，BEN-377-07） |
| 期初定價 | 開盤價、收盤價 | VWAP 照 #122（說明書沒寫期初定價方式，同 CA FCN） |

## 4. 比價日填法

BEN Non-Call = 天期，照[參考條件表格式](reference-sheet.md) §4 的「Non-Call = 天期時只填最後一格」：

| 格 | 填法 | 說明書日期 |
|---|---|---|
| `比價日_{天期}` | 期末訂價日 | 13.(4)（= 最終比價日；BEN 只做到期判定，ADR 0029） |
| 其他格 | `-` | — |

例（虛構）：3 個月期 → `比價日_3` = 期末訂價日，其餘 `-`。

表上空白時通過後回填；有值時逐格比對，不同就不一致、保留原值（共用 `backfill.compare_dates`）。

## 5. 不核對的欄位

同 CA FCN（`UL_n_Memo`、`私銀註記`、`UF`、無表頭欄等；`期初定價` 只檢查是允許值）；另外 `KO(%)`、`UL_n_KO價` 不核對（§2）。

## 6. 探勘時發現的表上問題（2026-10-30）

- 舊總表（Product = BEN、發行機構 CA）和 5 份說明書逐項比對全部相符：代號、ISIN、幣別、面額、交易日（= 期初訂價日）、發行日、最終比價日（只有 `比價日_{天期}`，其他 `-`）、到期日、天期 = Non-Call、K%、各標的進場價與執行價、紅利 = `Coupon p.a. (%)`、KO(Freq) `P`、KO(memo) `N`、KI `-`。
- 舊總表的 `UL_n_KO價` 填進場價：照來源存、不核對（ADR 0029）。
