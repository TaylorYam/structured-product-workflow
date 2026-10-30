# 下單資料格式：Nomura DACN（參考條件表）

- 設定檔：所有上手共用的 [`config/reference_sheet.toml`](../../../config/reference_sheet.toml)
- 狀態：規格；實作另開 PR（workflow #368）。共用的版面、配對、回填規則見[參考條件表格式](reference-sheet.md)；和 [Nomura FCN 下單資料格式](nomura-fcn-reference.md) 相同的不重寫，本文件只寫 DACN 的差異

## 1. 結構

和 Nomura FCN 相同（商品代號前 3 碼 `156`，`發行機構` 欄寫 `Nomura`）。商品種類看 `Product` = `DACN`：辨識到的範本是 `nomura-zh-dacn-pd`／`nomura-zh-dacn-iis` 時，該列 Product 必須是 `DACN`；Product 是 `DACN`、文件卻是 Nomura FCN 範本（或反過來）→ 商品種類不一致，轉人工覆核（共用 `template.product_family`）。

開放自動核對：`config/issuer_prefixes.toml` `[auto_check_products]` 的 `DACN` 加 `NOMURA`（實作 PR）；開放前 Nomura DACN 照未開放商品轉人工。

探勘用的參考條件表是私人雲端倉庫的舊總表 `FCN外商品數量.xlsx`（第一張工作表，涵蓋全部 11 份樣本；ISIN、發行日、比價日已填好）。舊總表沒有 `配息門檻(%)` 欄。

## 2. 欄位對照（只列和 Nomura FCN 不同的）

| Excel 欄 | 標準欄位 | 說明書來源（見[範本規格](../templates/nomura-zh-dacn.md)） |
|---|---|---|
| Product | `product` | 不和說明書比；= `DACN` 才能用 DACN 範本（§1） |
| Coupon p.a. (%) | `coupon_pa_pct` | 第 7 項 `年化配息率： X%`（**直接寫年利率**，FCN 是情境年化報酬率）；第 14 項設定參數、第 15 項 (1)、情境一另核 = 第 7 項 |
| 配息門檻(%) | `coupon_barrier_pct` | 第 7 項 `實際日數：` 定義 `…所有連結標的之收盤價等於或大於其各自執行價…` → = 第 7 項標的表表頭 `執行價 (期初價格之X% )`。表上**沒有這欄**（舊 Excel）→ 拿說明書的配息門檻和 `K(%)` 比（workflow ADR 0030）；**有這欄但該列空白或 `-`** → 轉人工覆核（DACN 一定要有值） |
| Non-Call(月) | `first_callable_period` | 同 FCN D 型（KO 起日 = 日期表第 k 期觀察期間終止日的 k）；另核 = 第 7 項配息公式的保證期數（`就第一個配息日而言` → 1） |
| KO(Freq)／KO(memo)／KI(Freq)／K(%)／KO(%)／KI(%)／各價格 | 同 FCN | 同 FCN（11 份樣本：`D`、`Y`、`-` 或 `AM`） |

## 3. 允許值與 DACN 差異

| 欄 | 值 | 說明 |
|---|---|---|
| Product | `DACN` | 不分大小寫（共用） |
| 配息門檻(%) | 數字（= `K(%)`） | 資料庫匯出的參考條件表 DACN 一定有值，其他商品寫 `-`；舊總表、待建檔表沒有這欄 |
| KI(Freq) | `-` 或空白、`AM` | 樣本只有無 KI 與到期觀察（EKI）；`D`、`P` 沒有樣本（核對規則 §3.1 轉人工） |
| KO(Freq)、KO(memo) | `D`、`Y` | 樣本只有每日觀察＋記憶式；其他沒有樣本（轉人工） |

## 4. 比價日填法

同 Nomura FCN D 型（[Nomura FCN 下單資料格式](nomura-fcn-reference.md) §4）：`比價日_{Non-Call}` = 第 7 項 KO 起日（= 日期表第 Non-Call 期觀察期間終止日）、`比價日_{天期}` = 最終評價日，其他寫 `-`。11 份樣本的舊總表比價日和這個填法全部相同。

## 5. 不核對的欄位

同 Nomura FCN §5。DACN 不追蹤每日計息（實際日數、配息金額、配息日），沒有對應的欄位要核對（ADR 0030）。

## 6. 探勘時發現的表上狀況（2026-10-30）

- 舊總表 5 格 Nomura DACN 的 `Coupon p.a. (%)` 是前後帶不換行空白的文字（本機疑慮記錄 DACN-367-01）：核對時表上的值不是數字 → 轉人工覆核（共用 `order_value`），不自動去空白。試跑用的複本另行整理，PR 只寫定性結果。
- 一檔（ND02）表上兩檔標的的交易所碼寫 `UQ`，說明書寫 `UW`（本機疑慮記錄 DACN-368-07）：照常判不一致，不放寬規則。
- 表上 `UL_n_執行價` 存多位小數，四捨五入到 4 位後和說明書相等（同 FCN）。
- 其他欄位（代號、幣別、面額、各日期、天期、K／KO／KI %、KO／KI 型態、Non-Call、比價日、各標的進場價與執行價）11 份全部和說明書一致。
