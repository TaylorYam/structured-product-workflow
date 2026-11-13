# 下單資料格式：BNP DACN（參考條件表）

- 設定檔：所有上手共用的 [`config/reference_sheet.toml`](../../../config/reference_sheet.toml)
- 狀態：規格（實作另開 PR，workflow #548）。共用的版面、配對、回填規則見[參考條件表格式](reference-sheet.md)；和 [BNP FCN 下單資料格式](bnp-fcn-reference.md) 相同的不重寫，本文件只寫 DACN 的差異

## 1. 結構

和 BNP FCN 相同（商品代號前 3 碼 `082`，`發行機構` 欄寫 `BNP`）。商品種類看 `Product` = `DACN`：辨識到的範本是 `bnp-zh-dacn-pd`／`bnp-zh-dacn-iis` 時，該列 Product 必須是 `DACN`；Product 是 `DACN`、文件卻是 BNP FCN 或 Super Bull 範本（或反過來）→ 商品種類不一致，轉人工覆核（共用 `template.product_family`）。

開放自動核對：`config/issuer_prefixes.toml` `[auto_check_products]` 的 `DACN` 加 `BNP`。BNP 的上手支援樣態表（`[unsupported_variants]`）維持空的；DACN 沒有樣本的型態由範本專屬規則轉人工（[核對規則](../rules/bnp-dacn-check-rules.md) §3.1）。

探勘用的參考條件表是私人雲端倉庫的舊總表（第一張工作表，涵蓋 BP01～BP03）。舊總表沒有 `配息門檻(%)` 欄。

## 2. 欄位對照（只列和 BNP FCN 不同的）

| Excel 欄 | 標準欄位 | 說明書來源（見[範本規格](../templates/bnp-zh-dacn.md)） |
|---|---|---|
| Product | `product` | 不和說明書比；= `DACN` 才能用 DACN 範本（§1） |
| 配息門檻(%) | `coupon_barrier_pct` | §11 `定界價格(Barrier Price)` 列的 `即期初價格之Y%`。表上**沒有這欄**（舊 Excel）或**有這欄但該列空白或 `-`** → 拿說明書的配息門檻和 `K(%)` 比（共用 `field.coupon_barrier_pct`，同第一波 DACN） |
| Coupon p.a. (%) | `coupon_pa_pct` | 同 FCN：§15 `(即年利率Y%)`（共用 `field.coupon_pa_pct` 直接比）；月配息率 = 年利率 ÷ 12（依說明書顯示位數，同 FCN `derive.monthly_coupon`） |
| Non-Call(月) | `first_callable_period` | 同 FCN D 型（§14(8) 的 k）；另核 = §15 配息公式保證期數 g |
| KO(Freq)／KO(memo)／KI(Freq)／K(%)／KO(%)／KI(%)／各價格 | 同 FCN | 同 FCN（樣本：`D`、`Y`、`D`／`P`／`AM`） |

§11 `定界價格` 列每檔價格沒有對應的表上欄位，只做說明書內部核對（= 期初價格 × 配息門檻 %，核對規則 §3.4）。

## 3. 允許值與 DACN 差異

| 欄 | 值 | 說明 |
|---|---|---|
| Product | `DACN` | 不分大小寫（共用） |
| 配息門檻(%) | 數字（樣本都 = `K(%)`） | 資料庫匯出的參考條件表 DACN 一定有值；舊總表、待建檔表沒有這欄 |
| KI(Freq) | `D`、`AM` | 樣本每日 1 份、到期 1 份；`P`（MKI）1 份照 workflow #386 轉人工；`-`（無 KI）沒有樣本（核對規則 §3.1 轉人工） |
| KO(Freq)、KO(memo) | `D`、`Y` | 只有這種有樣本 |
| Non-Call(月) | `1` | 樣本都是 1（= 保證期數）；其他值沒有樣本（轉人工） |
| 計價幣別 | `USD` | 樣本都是美元；其他幣別沒有樣本（轉人工） |
| 期初定價 | `收盤價`、`開盤價` | 同 FCN（說明書沒寫，不比對；不是 VWAP，價格照常比） |

## 4. 比價日填法

同 BNP FCN D 型（[BNP FCN 下單資料格式](bnp-fcn-reference.md) §4）：`比價日_{Non-Call}` = 日期表第 Non-Call 期的配息期結束日、`比價日_{天期}` = 末期結束日（= 期末評價日），其他寫 `-`。BP01～BP03 的舊總表比價日和這個填法全部相同。

## 5. 不核對的欄位

同 BNP FCN §5（`期初定價` 不核對；`UL_n_Memo` 不核對）。DACN 不追蹤每日計息（nj、Nj、合格營業日、各期配息金額），沒有對應的欄位要核對（ADR 0030）。

## 6. 探勘時發現的表上狀況（2026-11-13）

- BP01～BP03 的舊總表欄位（代號、ISIN、幣別、面額、各日期、天期、K／KO／KI %、KO／KI 型態、記憶式、Non-Call、比價日、各標的代號與價格、年利率）和說明書一致。
- BP02 表上 `Coupon p.a. (%)` 前面有一個不換行空白（數值本身和說明書相同）；試跑時確認共用讀法讀得到。
