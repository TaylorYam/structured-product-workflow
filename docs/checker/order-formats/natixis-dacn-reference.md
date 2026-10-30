# 下單資料格式：Natixis DACN（參考條件表）

- 設定檔：所有上手共用的 [`config/reference_sheet.toml`](../../../config/reference_sheet.toml)
- 狀態：規格；實作另開 PR（workflow #371）。共用的版面、配對、回填規則見[參考條件表格式](reference-sheet.md)；和 [Natixis FCN 下單資料格式](natixis-fcn-reference.md) 相同的不重寫，本文件只寫 DACN 的差異

## 1. 結構

和 Natixis FCN 相同（商品代號前 3 碼 `328`，`發行機構` 欄寫 `Natixis`）。商品種類看 `Product` = `DACN`：辨識到的範本是 `natixis-zh-dacn-pd`／`natixis-zh-dacn-iis` 時，該列 Product 必須是 `DACN`；Product 是 `DACN`、文件卻是 Natixis FCN 範本（或反過來）→ 商品種類不一致，轉人工覆核（共用 `template.product_family`）。

開放自動核對：`config/issuer_prefixes.toml` `[auto_check_products]` 的 `DACN` 加 `NATIXIS`（實作 PR，workflow #371）。

探勘用的參考條件表是私人雲端倉庫的舊總表 `FCN外商品數量.xlsx`（第一張工作表，涵蓋全部 7 份樣本，另有 2026-06 以前交易、沒有說明書的 Natixis DACN 列；ISIN、發行日、比價日已填好）。舊總表沒有 `配息門檻(%)` 欄。

## 2. 欄位對照（只列和 Natixis FCN 不同的）

| Excel 欄 | 標準欄位 | 說明書來源（見[範本規格](../templates/natixis-zh-dacn.md)） |
|---|---|---|
| Product | `product` | 不和說明書比；= `DACN` 才能用 DACN 範本（§1） |
| 配息門檻(%) | `coupon_barrier_pct` | 第 9 項價格表 `配息界限價格(每日觀察)/執行價格 (期初進場價格之X%)` 欄頭的 X（= `K(%)`）。表上**沒有這欄**（舊 Excel）→ 拿說明書的配息門檻和 `K(%)` 比；**有這欄但該列空白或 `-`** → 轉人工覆核（共用 `field.coupon_barrier_pct`，workflow #368、ADR 0030） |
| Non-Call(月) | `first_callable_period` | 同 FCN D 型（第 12 項 `從第k個配息觀察期間結束日` 的 k）；另核 = 日期表 `固定配息金額` 的期數 |
| Coupon p.a. (%) | `coupon_pa_pct` | 同 FCN：第 13 項 `月配息率：x% (即年利率y%)` 的 y；月配息率 x = y ÷ 12 四捨五入到 4 位 |
| KO(Freq)／KO(memo)／KI(Freq)／K(%)／KO(%)／KI(%)／各價格 | 同 FCN | 同 FCN（7 份樣本：`D`、`Y`、`AM`／`D`／`-`）；表上 `UL_n_執行價` 也是配息界限價格 |

## 3. 允許值與 DACN 差異

| 欄 | 值 | 說明 |
|---|---|---|
| Product | `DACN` | 不分大小寫（共用） |
| 配息門檻(%) | 數字（= `K(%)`） | 資料庫匯出的參考條件表 DACN 一定有值，其他商品寫 `-`；舊總表、待建檔表沒有這欄 |
| KO(Freq)、KO(memo) | `D`、`Y` | 樣本只有每日觀察＋記憶式；其他沒有樣本（核對規則 §3.1 轉人工） |
| KI(Freq) | `-` 或空白、`AM`、`D` | 樣本有無 KI（1 份）、到期觀察（1 份）、每日觀察（5 份）；`P` 沒有樣本（轉人工，workflow Issue #386） |
| Non-Call(月) | `1` | = 保證期數（7 份都是 1） |
| 承作幣別 | `USD`、`JPY` | 樣本 6 份美元、1 份日圓（日股）；其他幣別沒有樣本（轉人工） |

## 4. 比價日填法

同 Natixis FCN D 型（[Natixis FCN 下單資料格式](natixis-fcn-reference.md) §4）：`比價日_{Non-Call}` = 日期表第 Non-Call 期 `配息觀察期間結束日`（= 第 12 項 `從第k個配息觀察期間結束日(含)` 那一天，第一個 KO 觀察日）、`比價日_{天期}` = 最終評價日（= 日期表末列結束日），其他寫 `-`。KO 觀察到最終評價日 `(不含)` 時最終評價日本身不是 KO 觀察日，但 `比價日_{天期}` 仍填最終評價日（同 FCN，作為到期比價日）。7 份樣本的舊總表比價日和這個填法全部相同。

## 5. 不核對的欄位

同 Natixis FCN §5（`期初定價` 不和說明書比，只檢查是允許值；VWAP 列照 Issue #122；`UL_n_Memo` 不核對）。DACN 不追蹤每日計息（n、N、配息金額、配息日），沒有對應的欄位要核對（ADR 0030）。

## 6. 探勘時發現的表上狀況（2026-10-30）

- 7 份樣本的舊總表欄位（代號、ISIN、幣別、面額、各日期、天期、K／KO／KI %、KO／KI 型態、記憶式、Non-Call、比價日、年利率）全部和說明書一致；無 KI 那份表上 `KI(%)`、`KI(Freq)` 空白。
- `期初定價`：開盤價 1 份、收盤價 6 份；說明書都沒寫（同 Natixis FCN）。
- `UL_n_Memo` 逐檔值和 `KO(memo)` 不一定一致（同 Natixis FCN，不核對）。
