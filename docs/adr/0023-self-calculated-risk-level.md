# 0023: 自算風險等級／Self-Calculated Risk Level

- 狀態／Status: Accepted
- 日期／Date: 2026-10-26
- 追蹤／Tracking: #238（規格／spec #236）
- 補充／Completes: [ADR 0012](0012-check-terms-calculation.md) 的核對條件計算多算一項風險等級／the check-terms calculation of ADR 0012 gains the risk level

## 繁體中文

### 背景

商品風險等級（RR）目前寫死 RR4：審查標準、各家固定警語、寄信草稿都是 RR4。實際的等級由承辦用「特別約定事項&高齡適合度聲明書 Generator」試算表算，要另外開網路磁碟上的 Excel 抄 200 日波動度、手打發行機構評等，資料庫沒有存。#236 決定照試算表的公式自己算，評等表已由 #237（migration 0024）建立；這份 ADR 記錄 #238 的計算、保存與時間點。說明書／投資人須知的 RR 核對與寄信草稿改用自算等級是 #239，不在這次。

### 決策

公式（還原自 Generator「境外結構型商品承作檢核表」工作表，#236 使用者確認）：

- 總分 = 0.4×保本率分 + 0.2×年期分 + 0.3×結構複雜度分 + 0.1×流動性分，用十進位計算。
- **保本率分**：試算表是 100% 給 1、≥90% 給 2、≥80% 給 3、≥70% 給 4、<70% 給 5。資料庫沒有保本率欄，FCN 一律不保本，固定 5。
- **年期分**（`tenor_months`）：≤12 給 1、≤36 給 2、≤60 給 3、≤120 給 4、>120 給 5。
- **結構複雜度分** = 平均（標的種類分, 標的數量分, 波動度分）+ 提前贖回 1（FCN 一律可提前出場）。
  - 標的種類：照試算表只看 `underlying_1`（空白算缺資料，不改看下一檔），在指數清單上是指數 3 分，否則個股 4 分。指數清單放 `config/risk_level.toml`，由作業人員提供，提供前是空清單；清單與 `underlying_1` 都經 `sheet_cells.bbg_code` 去掉最後的類別字（資料庫存的代碼已經去掉，例：清單寫 `SPX Index`、資料庫存 `SPX`），再去頭尾空白、不分大小寫比對。
  - 標的數量（非空的 `underlying_1..5`）：1→1、2→2、3→3、4→4、5→4、≥6→5。
  - 波動度：各標的 200 日波動率的**平均**，≤15→1、≤30→2、≤45→3、<60→4、≥60→5（試算表用 LOOKUP 與 0／15.0000001／30.0000001／45.0000001／60 的分界，所以剛好 15 是 1 分、剛好 60 是 5 分）。
- **流動性分** = 0.8×評等分 + 0.2×1（可贖回頻率一律 Daily）。評等取**保證機構**（評等表沒有這家上手的保證機構時取發行機構），三家評等機構中取最好（分數最小）的一家：AAA／Aaa→1；AA+、AA、Aa1、Aa2→2；AA-、A+、Aa3、A1→3；A、A-、A2、A3→4；BBB+、Baa1 以下→5。
- **轉等級**（下限含）：<1.8→RR1、<2.6→RR2、<3.4→RR3、<4.2→RR4、其餘 RR5。
- 試算表實例：LITE UW／NBIS UW／MRVL UW、9 個月、Barclays（A+／A1／AA-）、波動率 100.98／113.92／84.04 → 2 + 0.2 + 1.5 + 0.26 = 3.96 → RR4。

資料時間點：

- 波動度用 `workflow.daily_price.volatility_200d`（每日匯入的彭博 VOLATILITY_200D）。取價日＝交易日那一份股價檔（內容是交易日前一個交易日收盤算的值，和試算表一致）；那一份還沒匯入時取交易日以前最近的一份，不用交易日之後的。所有標的都從同一份檔取；標的代碼和股價檔逐字比對，同核對條件。
- 評等用交易日查（#237 的 `RatingRows.on`：生效日 ≤ 交易日的最新一筆）。上手代碼照起單資料的規則，由 TDCC 前三碼查上手編號對照（查不到時用發行機構寫法反查）。

算不出來：

- 缺交易日、天期、標的、`underlying_1` 或股價檔，任一標的不在那份股價檔、股價衝突或沒有 200 日波動率，上手認不出來或評等表查不到：
  - 交易日 < 2026-10-30：填 RR4，狀態「回填」，原因寫缺什麼（舊商品不卡住）。
  - 交易日 ≥ 2026-10-30（或沒有交易日）：狀態「待算」、沒有等級，原因寫缺什麼；資料補齊後下一次重算時算出。
- 這裡的「回填」是風險等級的狀態，和核對把說明書讀出的值寫回參考條件表的「回填」不同。

生命週期與保存（實作時決定，低風險、可回復）：

- 加在核對條件的 `recalculate`（ADR 0012）：每日匯入、起單資料匯入、人工維護存檔、放行差異後，重算還沒鎖住的商品，結果和上一次不同才寫入。已鎖住的商品只有**還沒有風險等級時算一次**（本功能上線前就鎖住的商品，在升級後第一次重算時補上），之後凍結、不再重算；**更正已鎖住的商品（`recalculate_corrected`）也不重算風險等級**。所以已鎖住的商品一旦有了風險等級就不再改變，留下承作當時（或第一次補算時）的判斷。補算不鎖商品條件列，寫入用 `ON CONFLICT DO NOTHING`，別的連線同時補了同一檔時以先寫的為準。
- migration 0025 新增 `workflow.risk_level`（每檔一筆）：狀態、等級、總分、原因、用的取價日，明細（各項分數、各標的波動率、平均波動度、用的評等）放 `detail` jsonb，數字存成字串，讀回來是同一個值。`workflow.product` 不加欄位（同 ADR 0012）。資料庫檢查狀態與等級一致：完成有等級、總分、明細；回填是 RR4、沒有明細；待算沒有等級。
- #239 讀 `sp_workflow.risk_level.risk_levels`／`risk_level_of`。
- 重算要的設定改由 `load_import_config` 一次讀（交易所假日表、上手編號對照、參考條件表格式的發行機構寫法、審查標準、風險等級設定）；任一個讀不到時匯入照樣保留、完成訊息寫出原因（同 ADR 0012）。
- 畫面：「2 起單資料」的結果表多一欄「風險等級」（RR4、RR4（回填）、待算），顯示資料庫存的值：讀取時（有連資料庫）與匯入後重讀時一起讀，已鎖住的商品也顯示；匯入後回填與待算的原因寫在原因欄，待算標紅；選一檔按「風險等級明細…」看各項分數、平均波動度、用的評等與取價日。

與試算表的差異（#236 已確認）：

- 總分剛好 3.4 時試算表因浮點誤差歸 RR3，這裡用十進位歸 RR4。結構複雜度分的「平均 ÷ 3」先和權重 0.3 約掉（0.3 ×（和 ÷ 3 + 1）=（和 + 3）÷ 10），總分是精確的十進位。FCN 不保本時要剛好 3.4 需要 AAA 的上手，實際不會出現。
- UBS 惠譽在試算表上方評等表寫 AA-，評等表用文件上的 AA；評等取三家最好的（穆迪 Aa2），實際不影響。
- 試算表的年期分數表（100／80／60…）沒有被公式用到，不實作。

彭博 VOLATILITY_200D 的算法（只記錄，這次不實作，留作日後自己補波動度的參考）：最近 200 個實際交易日收盤（含取價當天，交易所假日跳過）→ 199 個日對數報酬 → 樣本標準差（n−1）× √260 × 100。已驗證：Generator 的 LITE／NBIS／MRVL 三個值以 2026-09-04 收盤算出，吻合到小數第 4 位；249 檔美股以 10/16 與 10/19 兩天的彭博值各自反推，以前後日的一致性驗證（98% 在 0.1% 內一致）。

### 考慮過的替代方案

- **把等級與分數加在 `workflow.product` 或 `workflow.check_terms`**：少一張表，但起單資料匯入與重算會改同一張表（同 ADR 0012 的理由），核對條件的「有變」也會和風險等級混在一起。
- **明細每項一欄**：查詢方便，但欄位多、日後調整明細要改 schema；#239 只需要等級與狀態，明細只給畫面看，所以放 jsonb。
- **用收盤價自己算 200 日波動度**：算法已驗證，但要有完整的 200 日收盤價歷史與各交易所假日；這次照 #236 只用彭博匯入值。
- **待算時也填 RR4**：不會卡住，但新商品缺資料時會把猜的等級當成正確答案，#239 也無法轉人工覆核；只對 2026-10-30 以前的舊商品這樣做。
- **更正已鎖住的商品時也重算風險等級**：標的寫錯更正後等級會跟著對，但違反「鎖住後不再改變」；需要時另開 Issue 決定。

### 後果

- 更新程式後要跑 `spw-db-migrate`（資料表版本 0025）；升級後第一次匯入或存檔時，還沒鎖住的商品都會算出風險等級；已鎖住的商品在第一次匯入後的重算時補算一次（交易日在 2026-10-30 以前，缺資料的是回填 RR4），之後凍結。
- 新增設定檔 `config/risk_level.toml`；設定資料夾沒有這個檔時用程式內建的（`ConfigPaths.with_fallback`）。
- 匯入後的重算現在也要讀審查標準與參考條件表格式；這兩個設定檔壞掉時，每日匯入的重算也會失敗（匯入保留、寫出原因）。
- 指數清單還是空的：在使用者提供前，UL_1 是指數的商品會多算 1 分（4 分而不是 3 分）。
- 說明書／投資人須知的 RR 核對、固定警語與寄信草稿已改用自算等級（#239）：資料庫產生的參考條件表帶出「風險等級」欄，待算轉人工覆核、寄信草稿寫「待確認」；沒有這欄的舊 Excel 參考條件表照舊用審查標準的 RR4。

## English

### Context

The product risk level (RR) is currently hard-coded as RR4: the review standard, each issuer's fixed warning, and the mail drafts all say RR4. Operators compute the real level in the "特別約定事項&高齡適合度聲明書 Generator" spreadsheet, copying the 200-day volatility from an Excel file on a network drive and typing issuer ratings by hand; the database stores nothing. #236 decided to compute it ourselves with the spreadsheet's formula, and #237 (migration 0024) added the rating table; this ADR records #238's calculation, storage, and data timing. Using the calculated level in the term-sheet / investor-information RR checks and mail drafts is #239, not this change.

### Decision

The formula (recovered from the Generator's "境外結構型商品承作檢核表" sheet, confirmed by the user in #236):

- Total = 0.4 × principal score + 0.2 × tenor score + 0.3 × structure score + 0.1 × liquidity score, computed in decimal.
- **Principal score**: the spreadsheet gives 1 for 100%, 2 for ≥90%, 3 for ≥80%, 4 for ≥70%, and 5 below 70%. The database has no principal-protection column and FCNs are never principal-protected, so it is always 5.
- **Tenor score** (`tenor_months`): ≤12 → 1, ≤36 → 2, ≤60 → 3, ≤120 → 4, >120 → 5.
- **Structure score** = mean(underlying-type score, underlying-count score, volatility score) + early redemption 1 (FCNs always have an early exit).
  - Underlying type: as in the spreadsheet only `underlying_1` counts (blank is missing data; the next underlying is not used instead); 3 when it is on the index list, otherwise 4 (single stock). The index list lives in `config/risk_level.toml`, is supplied by the operators, and is empty until then; both the list entries and `underlying_1` go through `sheet_cells.bbg_code`, which drops the trailing class word (stored codes already lack it: the list says `SPX Index`, the database stores `SPX`), and are then compared trimmed and case-insensitively.
  - Underlying count (non-blank `underlying_1..5`): 1→1, 2→2, 3→3, 4→4, 5→4, ≥6→5.
  - Volatility: the **mean** of the underlyings' 200-day volatilities, ≤15 → 1, ≤30 → 2, ≤45 → 3, <60 → 4, ≥60 → 5 (the spreadsheet uses LOOKUP over the breakpoints 0 / 15.0000001 / 30.0000001 / 45.0000001 / 60, so exactly 15 scores 1 and exactly 60 scores 5).
- **Liquidity score** = 0.8 × rating score + 0.2 × 1 (redemption frequency is always Daily). The rating is the **guarantor's** (the issuing entity's when the rating table has no guarantor for the issuer), taking the best (lowest-scoring) of the three agencies: AAA/Aaa → 1; AA+, AA, Aa1, Aa2 → 2; AA-, A+, Aa3, A1 → 3; A, A-, A2, A3 → 4; BBB+ / Baa1 or worse → 5.
- **Level** (lower bound inclusive): <1.8 → RR1, <2.6 → RR2, <3.4 → RR3, <4.2 → RR4, otherwise RR5.
- Spreadsheet example: LITE UW / NBIS UW / MRVL UW, 9 months, Barclays (A+/A1/AA-), volatilities 100.98 / 113.92 / 84.04 → 2 + 0.2 + 1.5 + 0.26 = 3.96 → RR4.

Data timing:

- Volatility comes from `workflow.daily_price.volatility_200d` (Bloomberg VOLATILITY_200D, imported daily). Use the price file whose price date equals the trade date (it holds the value computed from the previous trading day's close, matching the spreadsheet); when that file is not imported yet, use the latest one before the trade date, never a later one. All underlyings come from that one file; codes are matched literally against the price file, as in the check terms.
- Ratings are looked up on the trade date (#237's `RatingRows.on`: the latest row effective on or before the trade date). The issuer code follows the order-sheet rule: the first three TDCC digits through the issuer prefix table (falling back to the issuer's written name).

When it cannot be calculated:

- Missing trade date, tenor, underlyings, `underlying_1`, or price file; an underlying absent from that price file, conflicting, or without a 200-day volatility; an unrecognized issuer or no rating in the table:
  - Trade date before 2026-10-30: RR4 with status 回填 (backfilled) and a reason naming what is missing, so old products are not blocked.
  - Trade date on or after 2026-10-30 (or no trade date): status 待算 (pending) with no level and the reason; the next recalculation after the data arrives computes it.
- This 回填 is a risk-level status, unrelated to the checker's 回填 (writing values read from the term sheet back to the reference sheet).

Lifecycle and storage (implementation choices, low risk and reversible):

- Added to the check-terms `recalculate` (ADR 0012): after a daily import, an order-sheet import, a manual-maintenance save, or a release, unlocked products are recalculated and written only when the result differs from the last one. A locked product is calculated **once, only while it has no risk level yet** (products locked before this feature shipped get theirs on the first recalculation after the upgrade) and is frozen afterwards; **correcting a locked product (`recalculate_corrected`) does not recalculate its risk level either**. So once a locked product has a risk level it never changes, recording the judgment at trade time (or at that first catch-up). The catch-up does not lock the product rows and writes with `ON CONFLICT DO NOTHING`, so if another connection fills the same product at the same time the first write wins.
- Migration 0025 adds `workflow.risk_level` (one row per product): status, level, total score, reason, and the price date used, with the breakdown (each score, each underlying's volatility, the mean volatility, the rating used) in the `detail` jsonb, numbers stored as strings so they read back unchanged. `workflow.product` gets no new columns (as in ADR 0012). Database checks keep status and level consistent: 完成 has a level, total, and detail; 回填 is RR4 without detail; 待算 has no level.
- #239 reads `sp_workflow.risk_level.risk_levels` / `risk_level_of`.
- The configuration the recalculation needs is now read once by `load_import_config` (exchange holidays, issuer prefixes, the issuer names of the reference-sheet format, the review standard, and the risk-level settings); if any cannot be read, the import is kept and the completion message says why (as in ADR 0012).
- Window: the "2 起單資料" result table gains a 風險等級 column (RR4, RR4（回填）, 待算) showing the stored value, read along with the read (when connected to the database) and with the reload after an import, locked products included; after an import the reasons for 回填 and 待算 go to the reason column and 待算 rows are marked red; select a product and click 「風險等級明細…」 to see each score, the mean volatility, the rating, and the price date used.

Differences from the spreadsheet (confirmed in #236):

- A total of exactly 3.4 is RR3 in the spreadsheet because of floating-point error; here it is RR4 in decimal arithmetic. The structure score's "mean ÷ 3" is folded into the 0.3 weight (0.3 × (sum ÷ 3 + 1) = (sum + 3) ÷ 10), so the total is an exact decimal. Without principal protection an exact 3.4 needs an AAA issuer, so it does not occur in practice.
- The spreadsheet's rating table lists UBS Fitch as AA-; the rating table uses the documents' AA. The best of the three agencies (Moody's Aa2) is used, so it makes no difference.
- The spreadsheet's tenor score table (100 / 80 / 60 …) is not used by its formula and is not implemented.

Bloomberg VOLATILITY_200D algorithm (documentation only, not implemented now; kept for computing volatility ourselves later): take the latest 200 closes on actual trading days (including the price day, skipping exchange holidays) → 199 daily log returns → sample standard deviation (n−1) × √260 × 100. Verified: the Generator's three LITE / NBIS / MRVL values reproduce to 4 decimals from the 2026-09-04 closes, and 249 US stocks checked by deriving each from the 10/16 and 10/19 Bloomberg values agree day over day (98% within 0.1%).

### Alternatives considered

- **Adding the level and scores to `workflow.product` or `workflow.check_terms`**: one table fewer, but the order import and the recalculation would write the same table (ADR 0012's reason), and the check terms' "changed" would mix with the risk level.
- **One column per breakdown item**: easier to query, but many columns and a schema change for every breakdown change; #239 needs only the level and status, and the breakdown is only for the window, so it is jsonb.
- **Computing the 200-day volatility from closes ourselves**: the algorithm is verified, but it needs a full 200-day close history and every exchange's holidays; per #236 only the imported Bloomberg value is used now.
- **Filling RR4 for pending products as well**: nothing blocks, but a guessed level would pass as the right answer for new products with missing data and #239 could not send them to manual review; this is done only for old products traded before 2026-10-30.
- **Recalculating the risk level when a locked product is corrected**: a corrected underlying would fix the level too, but it breaks "no change after lock"; open an Issue if that is ever needed.

### Consequences

- Run `spw-db-migrate` after updating (schema version 0025). After the upgrade, the first import or save calculates the risk level of every unlocked product; locked products get theirs once on the first recalculation after an import (missing data with a trade date before 2026-10-30 gives 回填 RR4) and are frozen afterwards.
- New settings file `config/risk_level.toml`; when the settings folder lacks it, the built-in one is used (`ConfigPaths.with_fallback`).
- The post-import recalculation now also reads the review standard and the reference-sheet format; if either file is broken, the daily import's recalculation fails too (the import is kept and the reason shown).
- The index list is still empty: until the operators supply it, products whose UL_1 is an index score one point more (4 instead of 3).
- The term-sheet / investor-information RR checks, fixed warnings, and mail drafts now use the calculated level (#239): the reference workbook generated from the database carries a 風險等級 column, a pending level goes to manual review, and the mail draft says 待確認; an old Excel reference workbook without that column still uses the review standard's RR4.
