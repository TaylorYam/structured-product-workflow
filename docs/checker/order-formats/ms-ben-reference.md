# 下單資料格式：MS BEN（參考條件表）

- 設定檔：所有上手共用的 [`config/reference_sheet.toml`](../../../config/reference_sheet.toml)
- 狀態：規格；實作另開（workflow #376）。共用的版面、配對、回填規則見[參考條件表格式](reference-sheet.md)，本文件只寫 MS BEN 的說明書來源與差異；和 MS FCN 相同的欄位見 [MS 下單資料格式](ms-fcn-reference.md)
- 依據：2026-10-30 探勘與開發者螢光標記（workflow #376）。**作業人員獨立標記先略過，2026-10-30 使用者決定**。標「待使用者確認（BEN-376-nn）」的是暫定的保守做法，編號對到本機疑慮記錄

## 1. 結構

同 MS FCN：`發行機構` 欄寫 `MS`、商品代號前 3 碼 `147`。`Product` 欄是 `BEN`（一般 BEN、Digital、有沒有 lookback 都是 `BEN`，不另分商品種類，workflow ADR 0029）；資料庫匯出的參考條件表一定有這欄。

BEN 沿用 FCN 那組欄（ADR 0029）：`Coupon p.a. (%)` 存紅利、`Non-Call(月)` = 天期、比價日只放最終比價日；另有選填欄「參與率(%)」（#373）。MS BEN 再加一個選填欄 **「回顧結束日」**（本文件 §5，新）。

## 2. 欄位對照

和 [MS 下單資料格式](ms-fcn-reference.md) §2 相同的欄（TDCC Code、ISIN Code、單位面額、承作幣別、交易日／發行日／最終比價日／到期日、天期(月)、期初定價、UL_n）不重列；說明書條號改看 [MS BEN 範本規格](../templates/ms-zh-ben.md)（日期仍是 §14(2)／(3)／(5)／(4)）。不同的：

| Excel 欄 | 標準欄位 | 說明書來源 |
|---|---|---|
| Product | — | `BEN`：決定能不能自動核對（`[auto_check_products]`）、選範本（範本規格 §8 商品種類一致）、Non-Call 規則（= 天期） |
| Coupon p.a. (%) | `coupon_pa_pct` | 紅利（不年化）：§15(1) `紅利配息率：X%` |
| 參與率(%) | `participation_pct` | §15(3) `參與率：X%`（MS 明寫；一般 BEN 100、Digital 0）。沒有這欄或空白 → 轉人工覆核 |
| K(%) | `strike_pct` | 非 lookback：§15 `執行價：…期初價格的X%`；lookback：§15 `回顧執行價：…回顧期初價格乘以X%`（lookback 的 K% 是回顧執行價 %） |
| KO(%) | `ko_pct` | 說明書沒有 KO：照來源存、不核對（ADR 0029；不套 MS FCN「Non-Call = 天期時 KO 視為 100%」，那條只給 FCN） |
| KI(%) | `ki_pct` | 無 KI，表上必須是 `-` |
| KO(Freq) | `ko_observation` | 固定 `P`（ADR 0029 起單匯入一律存 `Period End`） |
| KO(memo) | `ko_memory` | 固定 `N` |
| KI(Freq) | `ki_type` | 無 KI → `-` |
| Non-Call(月) | `first_callable_period` | = 天期（`PRODUCT_RULES` 的 BEN `maturity_only`） |
| 比價日_1～12 | `autocall_date_n` | 見 §4 |
| UL_n_進場價 | `underlying_n_initial_price` | §15 價格表期初價格（lookback 也是**說明書期初價格**，不是回顧期初價格） |
| UL_n_執行價 | `underlying_n_strike_price` | 非 lookback：§15 價格表執行價欄；lookback：表上沒有執行價欄，期望值 = 期初價格 × K%（half-up 4 位） |
| UL_n_KO價 | `underlying_n_ko_price` | 說明書沒有 KO 價：不核對 |
| UL_n_下限價 | `underlying_n_ki_price` | 無 KI，表上是 `-` 或空白 |
| 回顧結束日（新） | `lookback_end_date` | §14(9) `回顧觀察期間：自交易日（包含）起至YYYY 年M 月D 日（包含）`；見 §5 |

資料庫匯出 lookback 商品時，`UL_n_執行價` 寫期初價格 × K%（`check_source._row`，#374），不是比價改寫後的回顧執行價；所以 lookback 算出回顧執行價之後再核對，執行價仍會一致。

## 3. 允許值

| 欄 | 值 | 其他值 |
|---|---|---|
| KO(Freq) | `P` | 樣態不符，轉人工覆核（核對規則 §3.1） |
| KO(memo) | `N` | 同上 |
| KI(Freq) | `-` | 同上 |
| Non-Call(月) | = 天期(月) | 同上 |
| 參與率(%) | 一般 BEN `100`、Digital `0`（= 說明書 §15(3)） | 和說明書不同 → 不一致；空白 → 轉人工覆核 |
| 回顧結束日 | 日期（lookback）或空白 | 見 §5 |
| 承作幣別 | `USD` | 其他轉人工覆核（待樣本驗證；待使用者確認（BEN-376-06），詳見本機疑慮記錄） |
| UL_1～UL_5 | 一般 BEN 2～3 檔、Digital 1 檔 | 一般 BEN 1 檔或 4 檔以上、Digital 2 檔以上轉人工覆核（同上，BEN-376-06） |
| 期初定價 | 開盤價、收盤價、VWAP | VWAP 照 #122（說明書沒寫期初定價方式，同 MS FCN） |

## 4. 比價日填法

BEN Non-Call = 天期，照[參考條件表格式](reference-sheet.md) §4 的「Non-Call = 天期時只填最後一格」：

| 格 | 填法 | 說明書日期 |
|---|---|---|
| `比價日_{天期}` | 期末定價日 | §14(5)（= 最終比價日；BEN 只做到期判定，ADR 0029） |
| 其他格 | `-` | — |

例（虛構）：12 個月期 → `比價日_12` = 期末定價日，其餘 `-`。回顧結束日**不是**比價日，不放進比價日欄。

表上空白時通過後回填；有值時逐格比對，不同就不一致、保留原值（共用 `backfill.compare_dates`）。

## 5. 回顧結束日（新欄，回填）

ADR 0029 寫明「MS TS 回填在 #376」：說明書寫明回顧結束日，比照 ISIN、發行日，核對通過後回填資料庫 `workflow.product.lookback_end_date`。

| 項目 | 規則 |
|---|---|
| 欄名、標準欄位 | `回顧結束日`、`lookback_end_date`；加到 `[columns]` 與 `layout.optional_columns`（不是必要欄；舊的 Excel 參考條件表沒有這欄不影響其他核對） |
| 資料庫匯出 | `check_source` 匯出 `product.lookback_end_date`（沒有值空白）；排在「參與率(%)」後面 |
| 匯入格式 | 不在匯入格式裡（起單建檔沒有這欄，README 起單建檔說明），「回填後」不帶；回填只寫資料庫 |
| 說明書有 §14(9) | 回顧期間起點必須是 `自交易日（包含）起`（固定寫法，不同 → 不一致）；結束日要晚於交易日、早於期末定價日，且是所有標的市場都開市的共同交易日（交易所假日表，ADR 0029 的共同交易日定義），任一不成立 → 不一致；假日表沒涵蓋那一天或那個市場 → 轉人工覆核 |
| 表上空白 | 說明書核對 **PASS** 或人工放行後回填；寫入經 `product_terms.backfill`，原值（空白）記在 `workflow.backfill_log`（同 ISIN、發行日） |
| 表上有值且相同 | 一致，不寫入 |
| 表上有值但不同 | 不一致、保留原值、不覆蓋，不能人工放行（同其他回填欄位） |
| 資料庫已有回顧期初價格 | #374 已鎖住回顧結束日（算過之後不能改）：只比對、不回填；不同照上一列判不一致 |
| 說明書沒有 §14(9) | 表上回顧結束日必須空白；有值 → 不一致（非 lookback 的說明書配到 lookback 的商品資料） |
| 表上沒有這欄 | lookback 說明書的回顧結束日回填轉人工覆核、訊息寫出缺的欄（同 §5 共用規則「格式設定沒有回填欄位的欄名」）；非 lookback 說明書不受影響 |
| 已鎖住的商品 | 照共用規則只比對、不回填（`check_source` 規則：已鎖住的商品可以再核對，但不再回填） |

以上 lookback 回填的處理方式是保守做法（待使用者確認（BEN-376-07），詳見本機疑慮記錄）。

## 6. 不核對的欄位

同 MS FCN（`庫存狀態`、`當日比價`、`私銀註記`、`UF`、`UL_n_Memo`、無表頭欄）；另外 `KO(%)`、`UL_n_KO價` 不核對（§2）。回顧期初價格（`lookback_price`）不在參考條件表，也不在說明書。

## 7. 探勘時發現的表上問題（2026-10-30）

- lookback 5 份的舊總表 `UL_n_進場價` 是回顧期初價格（作業人員在回顧期間結束後改掉），`UL_n_執行價` = 該價 × K%；1 份回顧期間還沒結束的仍是說明書期初價格。資料庫的進場價應該是說明書期初價格、回顧期初價格另存 `lookback_price`（ADR 0029）。不改資料、不放寬規則，開放後照常判不一致（待使用者確認（BEN-376-02），詳見本機疑慮記錄）。
- 3 份期初定價空白：照共用規則當成不是 VWAP、價格照常比對。
- 其餘欄位（代號、ISIN、幣別、面額、各日期、天期、Non-Call、K%（lookback 是回顧執行價 %）、紅利 = `Coupon p.a. (%)`、比價日、無 KI、非 lookback 的各標的價格）全部相符；標的順序全部和說明書相同（MS BEN 照 MS FCN 依順序比對）。
