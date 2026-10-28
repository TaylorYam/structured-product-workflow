# 下單資料格式：CA Super Bull（參考條件表）

- 設定檔：所有上手共用的 [`config/reference_sheet.toml`](../../../config/reference_sheet.toml)
- 狀態：已實作（workflow #312）。共用的版面、配對、回填規則見[參考條件表格式](reference-sheet.md)，本文件只寫 CA Super Bull 的說明書來源與差異；和 CA FCN 相同的欄位見 [CA 下單資料格式](ca-fcn-reference.md)

## 1. 結構

同 CA FCN：`發行機構` 欄寫 `CA`、商品代號前 3 碼 `086`。`Product` 欄是 `Super Bull`（不分大小寫）；資料庫匯出的參考條件表一定有這欄（workflow #310）。

Super Bull 沿用 FCN 那組欄（workflow ADR 0028）：`Coupon p.a. (%)` 存出場紅利、`Non-Call(月)` 是第一個觀察日的月數、比價日照舊總表放。

## 2. 欄位對照

和 [CA 下單資料格式](ca-fcn-reference.md) §2 相同的欄（TDCC Code、ISIN Code、單位面額、承作幣別、交易日（期初訂價日）／發行日／最終比價日／到期日、天期(月)、期初定價、UL_n、UL_n_進場價／執行價）不重列。不同的：

| Excel 欄 | 標準欄位 | 說明書來源（見[範本規格](../templates/ca-zh-super-bull.md)） |
|---|---|---|
| Product | — | `Super Bull`：決定能不能自動核對（`[auto_check_products]`）、選範本（範本規格 §8 商品種類一致）、Non-Call 規則（至少 1） |
| Coupon p.a. (%) | `coupon_pa_pct` | 第 9 項觀察日表 `自動提前出場紅利` 欄（每列相同；出場紅利，不年化） |
| KO(%) | `ko_pct` | 第 9 項價格表表頭 `自動提前出場價 (期初訂價之Y% )`；可和 K(%) 不同 |
| K(%) | `strike_pct` | 第 9 項價格表表頭 `執行價 (期初訂價之X% )` |
| KI(%) | `ki_pct` | 無 KI，表上必須是 `-` |
| KO(Freq) | `ko_observation` | 第 9 項 (a) 四欄觀察日表 → `P` |
| KO(memo) | `ko_memory` | 第 9 項 (a) 事件句 → `N` |
| KI(Freq) | `ki_type` | 沒有界限事件、價格表沒有下限價欄 → `-` |
| Non-Call(月) | `first_callable_period` | 觀察日表第 1 列距發行日的月數（範本規格 §4.1） |
| 比價日_1～12 | `autocall_date_n` | 見 §4 |
| UL_n_KO價 | `underlying_n_ko_price` | 第 9 項價格表自動提前出場價 |
| UL_n_下限價 | `underlying_n_ki_price` | 無 KI，表上是 `-` 或空白 |

## 3. 允許值

| 欄 | 值 | 其他值 |
|---|---|---|
| KO(Freq) | `P` | 樣態不符，轉人工覆核（核對規則 §3.1） |
| KO(memo) | `N` | 同上 |
| KI(Freq) | `-` | 同上 |
| KO(%) | 任意（可 ≠ K(%)） | — |
| 承作幣別 | `CNH` 以外（樣本有 USD、JPY） | `CNH` 轉人工覆核（2026-10-28 使用者決定，核對規則 §3.1） |
| UL_1～UL_5 | 1～5 檔 | — |

起單建檔的 `KO Type`、`Barrier Type` 經資料庫匯出成上表的 KO(Freq)、KO(memo)、KI(Freq)（`Period End` → `P`＋`N`、`NONE` → `-`）；`Observation Frequency (m)` 不在參考條件表，不核對。

## 4. 比價日填法（Super Bull，所有上手共用；見[參考條件表格式](reference-sheet.md) §4、[BNP Super Bull 下單資料格式](bnp-super-bull-reference.md) §4）

| 格 | 填法 | 說明書日期 |
|---|---|---|
| `比價日_m` | 每個觀察日放在它距發行日的月數 m 那一格 | 第 9 項觀察日表各列的 `自動提前出場觀察日`（**不是** `自動提前出場日`） |
| `比價日_{天期}` | 期末訂價日 | 13.(4)（= 最終比價日；比價時不算 KO，ADR 0028） |
| 其他格 | `-` | — |

例（虛構）：6 個月期、第 2、4 個月觀察 → `比價日_2`、`比價日_4` = 觀察日、`比價日_6` = 期末訂價日，其餘 `-`；12 個月期、只有第 6 個月觀察 → `比價日_6`、`比價日_12`。

表上空白時通過後回填；有值時逐格比對，不同就不一致、保留原值（共用 `backfill.compare_dates`）。天期超過 12、觀察日月數 ≥ 天期或對不到格子 → 轉人工覆核，不回填。

## 5. 不核對的欄位

同 CA FCN（`UL_n_Memo`、`私銀註記`、`UF`、無表頭欄等；`期初定價` 只檢查是允許值）。

## 6. 探勘時發現的表上問題（2026-10-28）

- 2 份樣本把某一列的**出場日**填進比價日（應是觀察日）：核對判不一致是預期結果，由作業人員修表（其中 1 份是境外人民幣，本來就轉人工）。
- 1 份樣本漏填中間 2 個比價日（只填了第一個觀察日與天期那格）：漏填的格是空白，通過後回填。
- 其餘欄位（代號、ISIN、幣別、面額、各日期、天期、K%、KO%、出場紅利、KO(Freq)、KO(memo)、KI(Freq)、Non-Call、標的代號與各價格）全部相符。
