# 0009: Order 條件在視窗裡確認後匯入／Confirm Order Terms in the Window Before Import

- 狀態／Status: Accepted
- 日期／Date: 2026-10-21
- 追蹤／Tracking: #86
- 部分取代／Partly supersedes: [ADR 0006](0006-new-product-data-from-order.md) 的「作業人員確認 Order 條件清單、補好淺紅格子後按匯入」；[ADR 0008](0008-daily-prices-denomination-and-order-dates.md) 的「匯入只讀『Order條件清單』工作表」／ADR 0006's "operators review the Order term list workbook, fill light-red cells, then import"; ADR 0008's "the import reads only the `Order條件清單` sheet"

## 繁體中文

### 背景

ADR 0006、0008 原本的做法是：讀信先輸出 `Order條件清單_<時間>.xlsx`，作業人員在 Excel 補好淺紅格子，再把這個 Excel 匯入資料庫。實作前發現兩個問題：

- 淺紅底只在格式表（原本叫 `example`）那張。「Order條件清單」那張沒有底色，而且有些值是信裡的原文；例如 Citi 的 KO Type 要由兩欄 TRUE／FALSE 換算，那一格是空白。只讀「Order條件清單」的話，作業人員得重補格式表已經算好的東西。
- 改 Excel 再匯入，程式要重新解讀人改過的格子；人打錯的寫法只能等匯入時才知道。

### 決策（2026-10-21 使用者確認）

- **確認改在視窗裡做**：「1 Order 條件」頁讀完信，在畫面上列出這批商品。點一檔就編輯格式表那些欄，再加上承作金額：讀信有問題的欄標紅並寫原因；KO Type、Barrier Type、期初價格用下拉選單；日期打錯當場提示。讀信時不再自動輸出 Excel。
- **匯入與匯出一起做**：「匯入資料庫並匯出 Excel」把通過檢查的商品寫進資料庫，同時輸出跟現在同格式的 Excel，貼到現行的表；另有「只匯出 Excel」。沒通過的留在畫面上並寫原因。
- **匯出的 Excel**：
  - 原本的四張照舊，格式表工作表從 `example` 改名為 `Order原始資料`，另外加一張「人工修改」（TDCC、欄位、信裡的值、改成）。
  - 「Order原始資料」放確認後的值：已匯入的不標底色，沒匯入的保留淺紅底，不另外標改過哪裡。「Order條件清單」維持讀信讀到的結果。
  - 匯出後再改 Excel 不影響資料庫。
- **重新讀信**：資料庫已有、還沒鎖住的商品，信裡空白但資料庫有值的欄帶入資料庫的值；兩邊都有值但不同的欄標黃並寫出資料庫的值，預設用信裡的值（以 Order 為準）。已鎖住的商品不能改、不能匯入。
- **過渡期**：#87 上線前，每日匯入的清單更新遇到由 Order 匯入的商品，只補空白的欄（分公司配置、ISIN、發行日），有值的不覆蓋。

### 考慮過的替代方案

- **照原案：改 Excel 再匯入**：作業人員熟悉 Excel、可以整欄貼上；但要選讀哪一張、要重新解讀人改的格子，打錯也要到匯入時才知道。10 月只有少數商品要人工補，視窗裡逐檔改就夠了。
- **匯入改讀格式表那張**：有淺紅底、值已換算；但少了承作金額，還是要另外對「Order條件清單」，而且一樣要重新解讀人改的格子。

### 後果

- #86 多了畫面工作：1 號頁要有逐檔編輯，並標出紅、黃欄位。
- 還沒匯入就關掉視窗，改過的內容會不見，要重新讀信。
- 匯入紀錄要記下人工修改，可以追查信裡的值跟確認後的值差在哪裡。

## English

### Context

ADR 0006 and 0008 had reading output `Order條件清單_<timestamp>.xlsx` first; operators would fill light-red cells in Excel and import that workbook. Two problems surfaced before implementation:

- Only the template sheet (formerly `example`) has light-red fills. The `Order條件清單` sheet has no fills and keeps some raw email text; for example, Citi's KO Type is derived from two TRUE/FALSE columns and that cell is blank. Reading only `Order條件清單` would make operators redo what the template sheet already derives.
- Editing Excel and importing it means re-parsing hand-edited cells, and typos surface only at import.

### Decision (confirmed by the user 2026-10-21)

- **Confirm in the window**: after reading, the "1 Order 條件" page lists the batch. Selecting a product edits the template sheet's fields plus the amount: fields with reading problems are red with a reason; KO Type, Barrier Type, and initial pricing use drop-downs; bad dates are flagged immediately. Reading no longer writes Excel automatically.
- **Import and export together**: "Import to database and export Excel" writes products that pass the checks and outputs the same-format workbook for pasting into existing sheets; "Export Excel only" is also available. Failing products stay on screen with reasons.
- **Exported workbook**:
  - The four sheets stay; the template sheet is renamed from `example` to `Order原始資料`, and a `人工修改` sheet (TDCC, field, email value, new value) is added.
  - `Order原始資料` holds the confirmed values: imported products have no fill, products not imported keep light red, and edits are not marked. `Order條件清單` keeps the values read from the email.
  - Editing the workbook afterward does not affect the database.
- **Re-reading**: for products already in the database and not locked, fields blank in the email take the database value; fields with differing values are yellow and show the database value, defaulting to the email (the Order is authoritative). Locked products cannot be edited or imported.
- **Transition**: until #87 ships, the daily import's 清單更新 only fills blank fields (branch allocations, ISIN, issue date) of products created by an Order import and never overwrites values.

### Alternatives considered

- **Keep editing Excel and importing it**: familiar and supports pasting whole columns, but requires choosing a sheet and re-parsing hand-edited cells, with typos found only at import. Only a few October products needed manual fixes, so per-product editing in the window suffices.
- **Import from the template sheet instead**: it has the fills and derived values but lacks the amount, still needs `Order條件清單`, and still re-parses edited cells.

### Consequences

- #86 gains UI work: per-product editing on page 1 with red and yellow fields.
- Closing the window before importing loses edits; read the emails again.
- Import records keep the manual edits, so differences between the email and the confirmed values are traceable.
