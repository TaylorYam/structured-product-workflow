# 0031: 從公開拆股行事曆自動登記拆股／Registering Stock Splits from Public Split Calendars

- 狀態／Status: Accepted
- 日期／Date: 2026-11-08
- 追蹤／Tracking: #509
- 補充／Completes: [ADR 0024](0024-stock-splits-in-observation.md)（拆股原本只有人工登記）／ADR 0024 (splits were registered by hand only)

## 繁體中文

### 背景

ADR 0024 的拆股登記全部靠人工，實際上多半是生效後比價出現「疑似拆股」待確認才去登記。使用者希望拆股一公布就先登記好，到生效日比價自動換算，而且不用彭博、每天自動更新。2026-11-07～08 實測：

- **Yahoo Finance 拆股行事曆**（免 key）：美、日、港股都有，約提前 1～2 個月列出。對本機已登記的 12 筆：美股 KLAC、CRWD 日期、比例全對；日股 7 筆比例全對，日期 6 筆比收盤價表的斷層日晚一個營業日、1 筆同一天；ETF（SOXX、1321 JT）沒有列。
- **Nasdaq 拆股行事曆**（免 key）：只有美股，但有 ETF（SOXX 1 拆 3 提前數月列出）。
- **FMP**：免費方案只有美股、限個人使用、要 API key，沒有比 Nasdaq 多的。
- 兩個來源都不是官方 API。

### 決策（2026-11-08 使用者確認：自動登記）

- **時機（2026-11-08 使用者決定）**：「1 每日匯入」頁另一個「匯入自動拆股登記」按鈕，跟每日匯入分開按，每日匯入本身不碰：按下去先抓兩個行事曆（Yahoo 抓今天前 10 天到後 120 天），再連資料庫登記與對齊；有新登記或改了生效日才比價全部（受影響、由比價判定出場的重新比）。讀不到假日表時不做（對不到標的的交易所）。用標準函式庫呼叫，不加新的套件。
- **登記**：只收收盤價表或商品有的代碼；代碼依假日表的交易所碼對到行事曆（美國交易所碼 → 代號、`JT` → `.T`、`HK` → 去掉前導 0 的數字）。前後股數相同、比例超過 1000 倍（現金收購少數股東）的，以及同一個代碼生效日前後 7 天內已經有登記（人工或自動、不管比例）的不收、不列出（每天都會再抓到）；兩個來源同一次拆股的日期或比例不同時不登記、列出差異。寫入用既有的 `add_split`，登記人是執行匯入的 Windows 使用者，備註「自動登記：<來源> 行事曆（抓取日）」。
- **對齊價格斷層**：生效日在最近 30 天內的自動登記，若生效日（不是收盤日時是之後第一個收盤日）沒有對應的斷層、但前後 3 個收盤日內恰好有一個「套用這次拆股就消失」的斷層，就把生效日改到那一天（刪掉重登在同一個 transaction，各留紀錄），備註加「依收盤價斷層從 X 改到 Y」。人工登記的不動。
- **失敗**：任一個來源抓不到只寫原因；登記與對齊整段一個 transaction，出任何錯都整段不寫、原因寫在完成訊息。
- **憑證檢查（2026-11-09 使用者同意，#523）**：連線不開 Python 3.13 預設的 `VERIFY_X509_STRICT`，憑證鏈與主機名稱照樣檢查（等於 3.12 以前的預設）。公司網路攔截 HTTPS 時換上的根憑證沒把 Basic Constraints 標成 critical，嚴格模式下 Yahoo 連不上。

### 考慮過的替代方案

- **放在每日匯入裡一起做**：少按一個按鈕，但拆股登記出問題時可能影響原本沒有問題的每日匯入；使用者選分開。
- **列出來讓作業人員按一下確認**：比較保險，但要每天多一步；登錯時比價會因為價格斷層停在待確認，作業人員刪掉即可，所以選自動。
- **日股固定往前推一個營業日**：7 筆裡有 1 筆不合，改用收盤價斷層對齊，日股、港股都適用。
- **yfinance 套件**：會帶進 pandas 等大套件；行事曆只要兩個 HTTP 呼叫，直接用標準函式庫。
- **FMP、JPX 每月 PDF**：FMP 免費方案沒有日股；JPX 是每月一份 PDF，不適合每天更新，之後需要時可以拿來交叉檢查。

### 後果

- 公布到生效通常有數週，大多數拆股會在生效前登記好，比價不再停在待確認。
- 兩個來源都沒有的（例：日本 ETF）照舊靠價格斷層提醒與人工登記。
- 行事曆改版或停用時只會少了自動登記，原因顯示在匯入結果；修程式前照舊人工登記。
- 錯的自動登記（例：上手取消拆股）不會自動刪除，要作業人員在拆股登記刪掉。

## English

### Context

Under ADR 0024 every split is registered by hand, and in practice an entry is usually added only after observation reports a "suspected split" 待確認. The user wants splits registered as soon as they are announced, converted automatically on the effective date, without Bloomberg and updated every day. Measured on 2026-11-07–08:

- **Yahoo Finance split calendar** (no key): US, Japan, and Hong Kong, listed about one to two months ahead. Against the 12 local entries: US KLAC and CRWD matched on date and ratio; all 7 Japanese ratios matched, but 6 dates were one business day later than the gap in the close table and 1 matched; ETFs (SOXX, 1321 JT) were missing.
- **Nasdaq split calendar** (no key): US only, but includes ETFs (SOXX 1-for-3 listed months ahead).
- **FMP**: the free plan is US-only, personal use, and needs an API key; it adds nothing over Nasdaq.
- Neither source is an official API.

### Decision (confirmed by the user 2026-11-08: register automatically)

- **When (decided by the user 2026-11-08)**: a separate 「匯入自動拆股登記」 button on the 「1 每日匯入」 page, pressed separately so the daily import itself is untouched: it fetches both calendars first (Yahoo from 10 days before to 120 days after today), then connects to register and align; everything is observed again (exited products affected by the change re-observed) only when an entry was added or moved. Nothing happens when the holiday calendar cannot be read (exchanges cannot be matched). The standard library is used; no new dependency.
- **Registration**: only codes in the close table or a product; codes map to calendar symbols by the holiday calendar's exchange code (US exchange codes → symbol, `JT` → `.T`, `HK` → the number without leading zeros). Equal share counts, ratios over 1000 (cash-out consolidations), and a code already registered within 7 days of the effective date (by hand or automatically, any ratio) are skipped silently, since they are fetched again every day; when the two sources disagree on the date or ratio of the same split it is not registered and the difference is listed. Writes use the existing `add_split`, recorded by the Windows user running the import, with the note 「自動登記：<source> 行事曆（fetch date）」.
- **Aligning to the price gap**: for automatic entries effective within the last 30 days, when there is no matching gap on the effective date (or the first close after it when it is not a close date) but exactly one gap within 3 closes either side disappears once the split is applied, the effective date moves to that day (delete and re-add in one transaction, each logged) and the note gains 「依收盤價斷層從 X 改到 Y」. Manual entries are untouched.
- **Failures**: an unreachable source only adds a reason; registration and alignment run in one transaction, so any error writes none of it and the completion message gives the reason.
- **Certificate checks (agreed by the user 2026-11-09, #523)**: connections drop Python 3.13's default `VERIFY_X509_STRICT`; the certificate chain and host name are still verified (the pre-3.12 default). The office network intercepts HTTPS with a root certificate whose Basic Constraints is not marked critical, so Yahoo was rejected in strict mode.

### Alternatives considered

- **Running inside the daily import**: one less button, but a problem in split registration could affect the daily import, which works fine today; the user chose a separate button.
- **Listing them for one-click confirmation**: safer, but one more daily step; a wrong entry makes observation stop at 待確認 because of the price gap, and operators can delete it, so automatic registration was chosen.
- **Shifting Japanese dates back one business day**: 1 of 7 did not fit; aligning to the close-table gap works for Japan and Hong Kong alike.
- **The yfinance package**: brings in pandas and other large packages; the calendars need only two HTTP calls, so the standard library is used.
- **FMP, JPX monthly PDF**: FMP's free plan lacks Japan; JPX publishes a monthly PDF, unsuitable for daily updates, but usable as a cross-check later.

### Consequences

- Announcements usually precede the effective date by weeks, so most splits are registered before they take effect and observation no longer stops at 待確認.
- Splits in neither source (for example Japanese ETFs) still rely on the price-gap prompt and manual registration.
- If a calendar changes or disappears, only automatic registration is lost, with the reason in the import result; manual registration still works until the code is fixed.
- A wrong automatic entry (for example a cancelled split) is not removed automatically; operators delete it in 拆股登記.
