# 下單資料格式：BNP Super Bull（參考條件表）

- 設定檔：所有上手共用的 [`config/reference_sheet.toml`](../../../config/reference_sheet.toml)
- 狀態：已實作（workflow #311）。共用的版面、配對、回填規則見[參考條件表格式](reference-sheet.md)，本文件只寫 BNP Super Bull 的說明書來源與差異；和 BNP FCN 相同的欄位見 [BNP 下單資料格式](bnp-fcn-reference.md)

## 1. 結構

同 BNP FCN：`發行機構` 欄寫 `BNP`、商品代號前 3 碼 `082`。`Product` 欄是 `Super Bull`（不分大小寫）；資料庫匯出的參考條件表一定有這欄（workflow #310）。

Super Bull 沿用 FCN 那組欄（workflow ADR 0028）：`Coupon p.a. (%)` 存出場紅利、`Non-Call(月)` 是第一個評價日的月數、比價日照舊總表放。

## 2. 欄位對照

和 [BNP 下單資料格式](bnp-fcn-reference.md) §2 相同的欄（TDCC Code、ISIN Code、單位面額、承作幣別、交易日／發行日／最終比價日／到期日、天期(月)、期初定價、UL_n、UL_n_進場價／執行價）不重列。不同的：

| Excel 欄 | 標準欄位 | 說明書來源（見[範本規格](../templates/bnp-zh-super-bull.md)） |
|---|---|---|
| Product | — | `Super Bull`：決定能不能自動核對（`[auto_check_products]`）、選範本（範本規格 §8 商品種類一致）、Non-Call 規則（至少 1） |
| Coupon p.a. (%) | `coupon_pa_pct` | §16 `自動提前贖回金額 = 商品面額 × [100% + X%]` 的 X（出場紅利，不年化） |
| KO(%) | `ko_pct` | §11 `自動提前贖回價格` 列的 `即期初價格之X%` |
| K(%) | `strike_pct` | §11 `執行價格` 列的 `即期初價格之X%` |
| KI(%) | `ki_pct` | 無 KI，表上必須是 `-` |
| KO(Freq) | `ko_observation` | §14(6) `如下表所列之日期` → `P` |
| KO(memo) | `ko_memory` | §16 定義句 → `N` |
| KI(Freq) | `ki_type` | §17 沒有觸及生效事件 → `-` |
| Non-Call(月) | `first_callable_period` | 日期表第 1 個評價日距發行日的月數（範本規格 §4.1） |
| 比價日_1～12 | `autocall_date_n` | 見 §4 |
| UL_n_KO價 | `underlying_n_ko_price` | §11 自動提前贖回價格 |
| UL_n_下限價 | `underlying_n_ki_price` | 無 KI，表上是 `-` 或空白 |

## 3. 允許值

| 欄 | 值 | 其他值 |
|---|---|---|
| KO(Freq) | `P` | 樣態不符，轉人工覆核（核對規則 §3.1） |
| KO(memo) | `N` | 同上 |
| KI(Freq) | `-` | 同上 |
| KO(%) | = K(%) | BNP 樣本全部相同；不同時轉人工覆核（核對規則 §3.1） |
| 承作幣別 | `USD` | 其他幣別現行版沒有樣本，轉人工覆核 |
| UL_1～UL_5 | 2 檔以上 | 只有 1 檔現行版沒有樣本，轉人工覆核 |

起單建檔的 `KO Type`、`Barrier Type` 經資料庫匯出成上表的 KO(Freq)、KO(memo)、KI(Freq)（`Period End` → `P`＋`N`、`NONE` → `-`）；`Observation Frequency (m)` 不在參考條件表，不核對。

## 4. 比價日填法（Super Bull，所有上手共用；見[參考條件表格式](reference-sheet.md) §4）

| 格 | 填法 | 說明書日期 |
|---|---|---|
| `比價日_m` | 每個評價日放在它距發行日的月數 m 那一格 | §14 日期表各列的自動提前贖回評價日 |
| `比價日_{天期}` | 期末評價日 | §14(4)（= 最終比價日；比價時不算 KO，ADR 0028） |
| 其他格 | `-` | — |

例（虛構）：6 個月期、第 3 個月一個評價日 → `比價日_3` = 評價日、`比價日_6` = 期末評價日，其餘 `-`；9 個月期、第 3、6 個月評價日 → `比價日_3`、`比價日_6`、`比價日_9`。

表上空白時通過後回填；有值時逐格比對，不同就不一致、保留原值（共用 `backfill.compare_dates`）。天期超過 12、評價日月數 ≥ 天期或對不到格子 → 轉人工覆核，不回填。

## 5. 不核對的欄位

同 BNP FCN（`庫存狀態`、`當日比價`、`私銀註記`、`UF`、`UL_n_Memo`、無表頭欄）。

## 6. 探勘時發現的表上問題（2026-10-28）

- 現行版 6 份樣本有 1 檔標的的交易所碼和說明書不同：使用者確認以起單建檔（= 參考條件表）為準，說明書寫錯但當時沒檢查到；核對判不一致是預期結果，不改規則。
- 舊版樣本另有到期日、比價日、拆股還原的差異；舊版不支援自動核對，表上的錯誤在 workflow 回補舊總表時處理（#310）。
