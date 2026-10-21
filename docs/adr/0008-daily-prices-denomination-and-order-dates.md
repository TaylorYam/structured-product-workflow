# 0008: 每日股價範本、單位面額與 Order 日期／Daily Price Template, Denomination, and Order Dates

- 狀態／Status: Accepted
- 日期／Date: 2026-10-21
- 追蹤／Tracking: #86、#87、#88、#68
- 補充／Completes: [ADR 0006](0006-new-product-data-from-order.md) 的待定事項／the open items of ADR 0006
- 部分取代／Partly supersedes: ADR 0006 的過渡期「清單更新照樣匯入、只比對」；[ADR 0003](0003-minimal-fcn-database.md) 的每日更新範本（`交易明細`、`清單更新` 兩張工作表）／ADR 0006's transition rule of importing 清單更新 for comparison; ADR 0003's daily template with the `交易明細` and `清單更新` sheets

## 繁體中文

### 背景

ADR 0006 決定新商品資料改由 Order 條件、交易明細與隔天早上的股價建立，但留下三件事待定：股價怎麼取得與匯入、單位面額從哪裡來、Order 沒寫最終比價日或到期日怎麼處理。使用者提供了新的每日更新範本（只放在私人樣本倉庫），只有「交易明細」和「BBG_Data」兩張工作表，沒有「清單更新」。

讀範本與本機樣本得到的事實：

- BBG_Data：C1 是取價日；B1 是 `=TODAY()`。第 2 列是欄名（代碼、公司名稱、最新收盤價、最新開盤價、1日前收盤價、200日波動率、市值），第 4 列起每列一檔，含匯率、利率與指數。每一列沒有日期。
- 10 月 Order 清單的標的代碼，幾乎都和股價檔一字不差，少數只差交易所碼。
- Order 原信與交易明細都沒有單位面額。庫存總表的 FCN 絕大多數是幣別預設值，少數例外的面額都等於承作總額或能整除承作總額，看起來是客戶指定。
- 10 月 Order 清單中，條件讀得到的都有最終比價日與到期日；缺的少數幾檔是整封讀不到的特殊格式。

### 決策（2026-10-21 使用者確認）

- **每日更新改版**：只有「交易明細」（格式不變）與「BBG_Data」兩張工作表。「清單更新」停用：ADR 0006 過渡期的「只比對、不覆蓋」與分公司對帳一起取消，分公司配置改由交易明細算（同一分公司金額加總、依出現先後，取消的列不算）。新商品只由「匯入 Order 條件」建立。實作順序：Order 條件匯入資料庫（#86）→ 每日匯入改版（#87）→ 算出核對條件（#88）→ 核對讀資料庫並回填（#68）；#87 上線前作業繼續用舊的每日更新檔。
- **取價日**：C1 是取價日，C1 不是日期就整份不匯入。檔案裡全部是前一個交易日的價格（現行在日股開盤前取價）。
- **對到交易日**：交易日 T 的商品用取價日比 T 晚的第一份股價檔。依交易所假日表確認 T 是該標的市場在取價日前的最後一個交易日，否則標「待確認」。
- **價格欄**：期初定價是收盤價取「最新收盤價」，開盤價取「最新開盤價」；VWAP 不算，由 TS 回填。其他欄照存但不用。
- **代碼**：以 BBG 代碼一字不差比對，不同交易所碼（例：UW、UQ）視為不同股票。找不到、沒有價格、同一代碼重複且值不同 → 標「待確認」。
- **單位面額**：匯入 Order 條件時，依承作幣別的預設值（`config/review_standard.toml` 的 `[denomination]`）寫進資料庫；Order 條件清單的格式不變，因為作業人員要照原格式貼到其他現行的表。TS 面額不同時，作業人員到 Order 信求證：TS 正確就人工放行，放行時把 TS 的面額寫入商品資料、原值留紀錄；TS 錯誤就照舊以「_待確認」放回。
- **最終比價日、到期日**：Order 讀不到就留空、標淺紅，作業人員補上才匯入；這兩格空白的商品不匯入，其他照常。不推算。發行日可以空白，由 TS 回填。
- **匯入 Order 條件**：只讀「Order條件清單」工作表，兩張工作表格式都不變。逐檔重新檢查必要欄，有問題的那一檔不匯入並寫原因；淺紅底與「待確認」只是提醒，不擋匯入。資料庫已有、但不是由 Order 匯入建立的商品（以前清單更新進來的）視同已鎖住，不覆蓋。

### 考慮過的替代方案

- **日股開盤價改用交易日當天的檔，或由 TS 回填**：現行取價在日股開盤前，暫不需要；範本之後會改成指定日期。
- **單位面額由 TS 回填，或在 Order 條件清單多一欄**：回填會少一道驗證；多一欄會破壞作業人員貼到現行表的格式。
- **用交易日＋天期推算最終比價日、到期日**：各家營業日規則不同，發行日也是因此決定不推算。
- **保留清單更新、只比對**：使用者決定停用，避免兩套來源並行。

### 後果

- 商品資料要能在沒有清單更新的情況下建立：ISIN、發行日要允許空白；需要新的股價 schema、Order 匯入紀錄與股價匯入紀錄。
- 寄信草稿要讀商品資料，所以新商品要先匯入 Order 條件。
- 已知限制：哪天取價晚於日股開盤，日股的「最新開盤價」會是當天的，由 TS 核對抓出不一致。股價範本改成指定日期後再調整（待定）。
- 港股不在交易所假日表，暫時會標「待確認」，需要時再補。

## English

### Context

ADR 0006 builds new product data from Order terms, trade rows, and next-morning prices, but left three items open: how prices are obtained and imported, where the denomination comes from, and what to do when an Order omits the final valuation or maturity date. The user supplied a new daily update template (kept only in the private samples repository) with just the `交易明細` and `BBG_Data` sheets, without `清單更新`.

Facts from the template and local samples:

- BBG_Data: C1 holds the price date; B1 is `=TODAY()`. Row 2 holds headers (code, company name, latest close, latest open, previous close, 200-day volatility, market cap); one instrument per row from row 4, including FX, rates, and indices. Rows carry no dates.
- Nearly all underlying codes in the October Order list match the price file exactly; a few differ only by exchange code.
- Neither the Order emails nor the trade rows carry a denomination. Nearly all FCNs in the inventory master use the currency default; the few exceptions equal or divide the total amount, apparently at the client's request.
- In the October Order list, every readable product has the final valuation and maturity dates; the few missing them are unusual emails that could not be read at all.

### Decision (confirmed by the user 2026-10-21)

- **Daily update redesign**: only the `交易明細` (unchanged) and `BBG_Data` sheets. `清單更新` is retired: ADR 0006's compare-only transition and branch reconciliation are dropped, and branch allocations are derived from trade rows (amounts summed per branch in order of appearance, cancelled rows excluded). New products are created only by "Import Order terms". Order: Order-term import (#86) → daily import redesign (#87) → check-term calculation (#88) → database-sourced checking with backfill (#68); operators keep using the old daily file until #87 ships.
- **Price date**: C1 is the price date; a non-date C1 rejects the whole file. All prices are from the previous trading day (prices are currently pulled before the Japanese open).
- **Trade-date mapping**: a product traded on T uses the first price file dated after T. The exchange holiday calendar must confirm T is that market's last trading day before the price date; otherwise the product is marked 待確認.
- **Price columns**: initial pricing 收盤價 uses the latest close and 開盤價 the latest open; VWAP is not calculated and comes from TS backfill. Other columns are stored but unused.
- **Codes**: exact Bloomberg-code match; different exchange codes (for example UW and UQ) are different stocks. Missing codes, missing prices, or duplicate codes with different values mark the product 待確認.
- **Denomination**: "Import Order terms" writes the currency default (`[denomination]` in `config/review_standard.toml`) to the database; the Order term list layout stays unchanged because operators paste it into existing sheets. When the TS denomination differs, the operator verifies against the Order email: if the TS is right, a manual release writes the TS denomination into product data and records the old value; if the TS is wrong, it returns as `_待確認` as today.
- **Final valuation and maturity dates**: unreadable dates stay blank and light red; operators fill them in before importing, and products with either date blank are not imported while the rest proceed. No inference. The issue date may stay blank and is backfilled from the TS.
- **Importing Order terms**: read only the `Order條件清單` sheet; neither sheet's layout changes. Each product's required fields are revalidated, and a failing product is skipped with a reason; light-red cells and 待確認 are reminders and do not block. Products already in the database but not created by an Order import (from the old 清單更新) are treated as locked and not overwritten.

### Alternatives considered

- **Japanese opens from the same-day file, or from TS backfill**: unnecessary while prices are pulled before the Japanese open; the template will later name the date explicitly.
- **Denomination from TS backfill, or an extra column in the Order term list**: backfill drops one verification; an extra column breaks the layout operators paste into existing sheets.
- **Infer final valuation and maturity dates from trade date plus tenor**: issuers' business-day rules differ, the same reason issue dates are not inferred.
- **Keep 清單更新 for comparison**: the user chose to retire it rather than run two sources in parallel.

### Consequences

- Product data must be creatable without 清單更新: ISIN and issue date must allow blanks, and a new price schema, Order import records, and price import records are needed.
- Mail drafts read product data, so a new product needs its Order terms imported first.
- Known limitation: if prices are pulled after the Japanese open, the Japanese latest open is that day's, and the TS check catches the mismatch. Revisit once the template names the date (open).
- Hong Kong is not in the exchange holiday calendar, so HK underlyings are marked 待確認 for now until it is added.
