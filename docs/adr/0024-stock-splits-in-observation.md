# 0024: 比價的拆股處理／Stock Splits in Observation

- 狀態／Status: Accepted（價格已經是上手調整過的標的見 [ADR 0025](0025-price-basis-date.md)／for prices already adjusted by the issuer, see ADR 0025）
- 日期／Date: 2026-10-27
- 追蹤／Tracking: #270
- 補充／Completes: [ADR 0020](0020-close-prices-and-observation-rules.md) 的比價規則；[ADR 0021](0021-stop-observing-exited-products.md)「已出場的不再比價」多一個例外／ADR 0020's observation rules; one exception to ADR 0021's "exited products are not observed again"

## 繁體中文

### 背景

收盤價表（ADR 0020）存的是未調整的原始收盤價。標的拆股或合股之後，同一個代碼前後兩段價格的基準不同（例：1 拆 100 時，前一天約 10,000、隔天約 100）；比價拿拆股後的收盤價去比拆股前定下的進場價、執行價、下限價、KO 價，會誤判 KI 觸及、接股或 KO。事後重抓舊日期的 MarketData，拿到的又是依之後拆股調整過的價格，跟 HP 表同一天的值基準不同。2026-10-27 用本機資料粗查，有數十個代碼出現這種斷層，跨過斷層的商品有在庫的，也有少數已由比價判定出場。

### 決策（2026-10-27 使用者確認）

- **拆股資訊：人工登記＋程式提醒。**「維護」選單「拆股登記…」登記代碼、生效日（第一個用新股數交易的日子）、拆股前股數、拆股後股數與備註，存 `workflow.stock_split`（代碼＋生效日唯一）；只能新增、刪除，每次留一筆只新增的 `workflow.stock_split_log`（誰、何時、內容）。代碼寫法同收盤價表（去掉 Equity 這類字尾），收盤價表與商品都沒有的代碼不收。
- **換算收盤價，不改商品價格。**收盤價表照存原始值，`workflow.underlying_price` 不動。比價時，生效日在 Strike date 之後、不晚於觀察日的拆股，收盤價乘上「拆股後股數 ÷ 拆股前股數」換回交易時的基準；多次拆股相乘。生效日在 Strike date 當天以前的不換算（進場價已經是新基準）。
- **提醒**：同一個代碼相鄰兩個收盤日，套用已登記的拆股後，價格比例低於 0.6 倍或高於 1 ÷ 0.6（約 1.67 倍）時是「疑似拆股或收盤價有誤」。這個日子在 Strike date 之後、比價範圍以內的商品，比到前一天就停下來，比價狀態待確認，原因寫標的、日期與倍數；在那之前算出的出場照樣算。真的大漲大跌就登記一筆前後股數都是 1 的「確認不是拆股」，這段期間不再提醒；價格錯了要重匯收盤價。
- **已出場的重新比。**新增或刪除一筆登記後，用到這個代碼、生效日在 Strike date 之後、還有效的事件全部來自比價、而且最晚的事件不早於生效日的商品，忽略原本的事件重新比價；結果不同就經 `replace_auto_events` 取代並留 `product_event_change`，不再出場就清掉事件（變回在庫或待確認）。舊清單與顧客贖回的事件不動。其他時候已出場的照舊不再比價（ADR 0021）。
- 存檔與比價：新增、刪除各自一個 transaction，之後比價全部；讀不到假日表或比價失敗時登記照樣保留，原因寫在畫面上。

### 考慮過的替代方案

- **改商品的四個價格**（跟上手的調整通知一樣）：會改到已鎖住、跟 TS 核對過的值，四捨五入可能跟上手對不上；換算收盤價對 KO、KI、接股的判定結果相同。
- **把收盤價表整個換成調整價**：每次有新拆股就要重寫歷史，HP 與每天的 CSV 都要重抓；也看不出原始值。
- **從彭博匯入公司行動**：要多一個抓檔步驟、先確認欄位；拆股一年沒幾次，先人工登記。
- **只提醒、不換算**：每次拆股都要人工判斷跨過拆股的每一檔，量多時做不完。
- **門檻 0.7**：1 拆 2 約 0.5、2 拆 3 約 0.67 也抓得到，但單日跌三成以上（正是 KI 會發生的時候）就要人工確認，太常打斷；取 0.6，2 拆 3 這種要靠作業人員主動登記。

### 後果

- 新增資料表版本 0027（`workflow.stock_split`、`workflow.stock_split_log`），不動既有資料。升級後第一次比價，跨過未登記斷層的在庫商品會變成待確認；作業人員登記拆股（或確認不是拆股、重匯錯價）後恢復。
- 上手若在 TS 已經用拆股後的基準寫進場價（TS 晚到、核對判「疑似分割」後放行），再登記同一次拆股會重複換算；這種商品要另外處理。
- 2 拆 3 這類比例接近 1 的拆股不會被提醒，要靠人工登記。
- 現金股利、配股等其他公司行動不處理。

## English

### Context

The close-price table (ADR 0020) stores raw, unadjusted closes. After an underlying splits or consolidates, the same code has prices on two different bases (for example, with a 1-for-100 split, about 10,000 one day and about 100 the next); observation then compares post-split closes with the initial, strike, KI, and KO prices set before the split and can wrongly find a KI touch, a delivery, or a KO. Re-fetching an old date's MarketData later returns prices adjusted for subsequent splits, on a different basis from HP's value for the same day. A rough check of local data on 2026-10-27 found dozens of codes with such gaps; the products spanning them include in-stock products and a few already exited by observation.

### Decision (confirmed by the user 2026-10-27)

- **Split data: manual registration plus a program prompt.** 「拆股登記…」 on the 維護 menu records the code, effective date (the first day trading on the new share count), shares before, shares after, and a note in `workflow.stock_split` (unique per code and effective date); entries can only be added or deleted, each change adding an insert-only row to `workflow.stock_split_log` (who, when, what). Codes are written as in the close-price table (without suffixes such as Equity); a code found in neither the close-price table nor any product is refused.
- **Convert closes, not product prices.** The close-price table keeps raw values and `workflow.underlying_price` is unchanged. During observation, each split effective after the Strike date and on or before the observation day multiplies the close by shares after ÷ shares before, bringing it back to the trade-date basis; several splits multiply. Splits effective on or before the Strike date are not applied (the initial price is already on the new basis).
- **Prompt**: when two consecutive closes of a code, after registered splits, differ by a ratio below 0.6 or above 1 ÷ 0.6 (about 1.67), the day is a "suspected split or wrong close". Products for which that day falls after the Strike date and within the observed range stop the day before, with observation status 待確認 naming the underlying, date, and ratio; exits found before it still stand. A genuine large move is registered as "not a split" (shares before and after both 1), which silences that period; a wrong close needs a re-import.
- **Re-observe exited products.** After adding or deleting an entry, products that use the code, whose Strike date is before the effective date, whose current events all come from observation, and whose latest event is not before the effective date are observed again ignoring their events; a different result replaces them through `replace_auto_events` with a `product_event_change` record, and a product that no longer exits has its events cleared (back to in stock or 待確認). Old-list and customer-redemption events are untouched. Otherwise exited products are still not observed again (ADR 0021).
- Saving and observation: each add or delete is its own transaction followed by observing everything; when the holiday calendar cannot be read or observation fails, the entry is still kept and the reason is shown.

### Alternatives considered

- **Adjusting the four product prices** (as issuers' adjustment notices do): changes locked values checked against the TS, and rounding may disagree with the issuer; converting closes gives the same KO, KI, and delivery results.
- **Storing adjusted closes throughout**: every new split rewrites history, requires re-fetching HP and daily CSVs, and hides the raw values.
- **Importing corporate actions from Bloomberg**: an extra fetch step and fields to confirm first; splits are rare, so manual registration comes first.
- **Prompt only, no conversion**: every split would need a manual decision for each product spanning it, which does not scale.
- **A 0.7 threshold**: would also catch 2-for-3 splits (about 0.67), but every one-day drop over 30% (exactly when KI happens) would need manual confirmation, interrupting too often; 0.6 is used, and splits like 2-for-3 rely on operators registering them.

### Consequences

- Schema version 0027 adds `workflow.stock_split` and `workflow.stock_split_log` without touching existing data. On the first observation after the upgrade, in-stock products spanning an unregistered gap become 待確認 until operators register the split (or confirm it is not one, or re-import a wrong close).
- If an issuer's TS already states the initial price on the post-split basis (a late TS released after the "suspected split" check), registering the same split converts twice; such products need separate handling.
- Splits with ratios close to 1, such as 2-for-3, are not prompted and rely on manual registration.
- Cash dividends, bonus issues, and other corporate actions are not handled.
