# 下單資料格式：SG DACN（參考條件表）

- 設定檔：所有上手共用的 [`config/reference_sheet.toml`](../../../config/reference_sheet.toml)
- 狀態：規格；實作另開 PR（workflow #369）。共用的版面、配對、回填規則見[參考條件表格式](reference-sheet.md)；和 [SG FCN 下單資料格式](sg-fcn-reference.md) 相同的不重寫，本文件只寫 DACN 的差異

## 1. 結構

和 SG FCN 相同（商品代號前 3 碼 `037`，`發行機構` 欄寫 `SG`）。商品種類看 `Product` = `DACN`：辨識到的範本是 `sg-zh-dacn-pd`／`sg-zh-dacn-iis` 時，該列 Product 必須是 `DACN`；Product 是 `DACN`、文件卻是 SG FCN 範本（或反過來）→ 商品種類不一致，轉人工覆核（共用 `template.product_family`）。下單信主旨寫 `FCN` 不影響（以交易明細／起單建檔的 Product 為準，workflow #366；本機疑慮記錄）。

開放自動核對：`config/issuer_prefixes.toml` `[auto_check_products]` 的 `DACN` 加 `SG`（實作 PR）；開放前 SG DACN 照未開放商品轉人工。

探勘用的參考條件表是私人雲端倉庫的舊總表 `FCN外商品數量.xlsx`（第一張工作表，涵蓋全部 10 份樣本；ISIN、發行日、比價日已填好）。舊總表沒有 `配息門檻(%)` 欄。

## 2. 欄位對照（只列和 SG FCN 不同的）

| Excel 欄 | 標準欄位 | 說明書來源（見[範本規格](../templates/sg-zh-dacn.md)） |
|---|---|---|
| Product | `product` | 不和說明書比；= `DACN` 才能用 DACN 範本（§1） |
| Coupon p.a. (%) | `coupon_pa_pct` | 第 8 項 `（相當於年配息率Y%）`（FCN 是 `(年率 Y%)`）；第 8 項配息率 X = 年利率 ÷ 12 四捨五入到 4 位（同 FCN） |
| 配息門檻(%) | `coupon_barrier_pct` | 第 8 項 n(i) 定義 `…所有連結標的收盤價皆大於或等於其執行價…` → = 第 8 項 `執行價：連結標的期初價格的X%`。表上**沒有這欄**（舊 Excel）→ 拿說明書的配息門檻和 `K(%)` 比；**有這欄但該列空白或 `-`** → 轉人工覆核（共用 `field.coupon_barrier_pct`，workflow #368、ADR 0030） |
| Non-Call(月) | `first_callable_period` | 同 FCN D 型（日期表第一個有起始日的期別 − 1）；另核 = 第 8 項配息公式的保證期數（`第1個` → 1、`第1~2個` → 2） |
| KO(Freq)／KO(memo)／KI(Freq)／K(%)／KO(%)／KI(%)／各價格 | 同 FCN | 同 FCN（10 份樣本：`D`、`Y`、`-` 或 `AM`） |

## 3. 允許值與 DACN 差異

| 欄 | 值 | 說明 |
|---|---|---|
| Product | `DACN` | 不分大小寫（共用） |
| 配息門檻(%) | 數字（= `K(%)`） | 資料庫匯出的參考條件表 DACN 一定有值，其他商品寫 `-`；舊總表、待建檔表沒有這欄 |
| KI(Freq) | `-` 或空白、`AM` | 樣本只有無 KI 與到期觀察；`D`、`P` 沒有樣本（核對規則 §3.1 轉人工） |
| KO(Freq)、KO(memo) | `D`、`Y` | 樣本只有每日觀察＋記憶式；其他沒有樣本（轉人工） |
| Non-Call(月) | `1`、`2` | = 保證期數（樣本 9 份 1、1 份 2） |

## 4. 比價日填法

同 SG FCN D 型（[SG FCN 下單資料格式](sg-fcn-reference.md) §4）：`比價日_{Non-Call}` = 第 8 項記憶事件觀察期間起日（= 日期表第 Non-Call+1 期起始日）、`比價日_{天期}` = 最終評價日，其他寫 `-`。10 份樣本的舊總表比價日和這個填法全部相同（含保證 2 期的那份）。

## 5. 不核對的欄位

同 SG FCN §5（`期初定價` 不核對；VWAP 列照 Issue #122）。DACN 不追蹤每日計息（n(i)、N(i)、配息金額、配息日），沒有對應的欄位要核對（ADR 0030）。

## 6. 探勘時發現的表上狀況（2026-10-30）

- 10 份樣本的舊總表欄位（代號、ISIN、幣別、面額、各日期、天期、K／KO／KI %、KO／KI 型態、記憶式、Non-Call、比價日、年利率、各標的代號與進場價／執行價／下限價／KO 價）全部和說明書一致；表上價格四捨五入到 4 位後相等。
- `期初定價`：開盤價 5 份、VWAP 2 份、收盤價 3 份；說明書都只寫「於訂價日之價格」（同 SG FCN；本機疑慮記錄）。
