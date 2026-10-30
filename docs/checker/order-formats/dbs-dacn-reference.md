# 下單資料格式：DBS DACN（參考條件表）

- 設定檔：所有上手共用的 [`config/reference_sheet.toml`](../../../config/reference_sheet.toml)
- 狀態：規格；實作另開 PR（workflow #370）。共用的版面、配對、回填規則見[參考條件表格式](reference-sheet.md)；和 [DBS FCN 下單資料格式](dbs-fcn-reference.md) 相同的不重寫，本文件只寫 DACN 的差異

## 1. 結構

和 DBS FCN 相同（商品代號前 3 碼 `078`，`發行機構` 欄寫 `DBS`）。商品種類看 `Product` = `DACN`：辨識到的範本是 `dbs-zh-dacn-pd`／`dbs-zh-dacn-iis` 時，該列 Product 必須是 `DACN`；Product 是 `DACN`、文件卻是 DBS FCN 範本（或反過來）→ 商品種類不一致，轉人工覆核（共用 `template.product_family`）。

開放自動核對：`config/issuer_prefixes.toml` `[auto_check_products]` 的 `DACN` 加 `DBS`（實作 PR）；開放前 DBS DACN 照未開放商品轉人工。`[unsupported_variants]` 的 DBS 清單（VWAP、KO 每期、KO 每日非記憶式、KI 每日／每期、Non-Call = 天期）不分商品種類，DACN 一樣在辨識時攔下。

探勘用的參考條件表是私人雲端倉庫的舊總表 `FCN外商品數量.xlsx`（第一張工作表，涵蓋全部 7 份樣本；ISIN、發行日、比價日已填好）。舊總表沒有 `配息門檻(%)` 欄。

## 2. 欄位對照（只列和 DBS FCN 不同的）

| Excel 欄 | 標準欄位 | 說明書來源（見[範本規格](../templates/dbs-zh-dacn.md)） |
|---|---|---|
| Product | `product` | 不和說明書比；= `DACN` 才能用 DACN 範本（§1） |
| 配息門檻(%) | `coupon_barrier_pct` | 表1 表頭 `區間下限價格 (期初價格的X%)`（累積日數定義比的是 `其區間下限價格`，範本規格 §4.1）。表上**沒有這欄**（舊 Excel）→ 拿說明書的配息門檻和 `K(%)` 比；**有這欄但該列空白或 `-`** → 轉人工覆核（共用 `field.coupon_barrier_pct`，workflow #368、ADR 0030） |
| Non-Call(月) | `first_callable_period` | 同 FCN（強制贖回 `自動提前出場決定日: 從第k個觀察期間結束日` 的 k）；另核 = 配息 `利率：1) 對於第1個觀察期間而言` 的保證期數 |
| Coupon p.a. (%)／KO(Freq)／KO(memo)／KI(Freq)／K(%)／KO(%)／KI(%)／各價格 | 同 FCN | 同 FCN（7 份樣本：`D`、`Y`、`-` 或 `AM`；年利率 = `最大利率值：…（即y%/12）` 的 y） |

表1 的 `區間下限價格` 每列價格沒有對應的表上欄位（參考條件表沒有配息門檻價格欄），只做說明書內部核對（= 期初價格 × 配息門檻 %，核對規則 §3.4）。

## 3. 允許值與 DACN 差異

| 欄 | 值 | 說明 |
|---|---|---|
| Product | `DACN` | 不分大小寫（共用） |
| 配息門檻(%) | 數字（樣本都 = `K(%)`） | 資料庫匯出的參考條件表 DACN 一定有值，其他商品寫 `-`；舊總表、待建檔表沒有這欄 |
| KI(Freq) | `-` 或空白、`AM` | 同 FCN（樣本無 KI 4 份、到期觀察 3 份） |
| KO(Freq)、KO(memo) | `D`、`Y` | 同 FCN（只支援這種） |
| Non-Call(月) | `1` | 樣本都是 1（= 保證期數）；其他值沒有樣本（核對規則 §3.1 轉人工） |
| 計價幣別 | `USD` | 樣本都是美元、沒有 Quanto；其他幣別沒有樣本（轉人工） |

## 4. 比價日填法

同 DBS FCN D 型（[DBS FCN 下單資料格式](dbs-fcn-reference.md) §4）：`比價日_{Non-Call}` = 表A 第 Non-Call 期 `觀察期間結束日`、`比價日_{天期}` = 定價日，其他寫 `-`。7 份樣本的舊總表比價日和這個填法全部相同。

## 5. 不核對的欄位

同 DBS FCN §5（`期初定價` 說明書沒寫，只檢查是允許值；`UL_n_Memo` 不核對）。DACN 不追蹤每日計息（累積日數、總日數、各期利率與利息金額），沒有對應的欄位要核對（ADR 0030）。

## 6. 探勘時發現的表上狀況（2026-10-30）

- 7 份樣本的舊總表欄位（代號、ISIN、幣別、面額、各日期、天期、K／KO／KI %、KO／KI 型態、記憶式、Non-Call、比價日、年利率、各標的代號與進場價／執行價／下限價／KO 價）全部和說明書一致；表上價格四捨五入到 4 位後相等。
- `期初定價`：開盤價 4 份、收盤價 3 份（說明書不寫，同 FCN）。
- `UL_n_Memo`：Y／N 混合（不核對，同 FCN）。
