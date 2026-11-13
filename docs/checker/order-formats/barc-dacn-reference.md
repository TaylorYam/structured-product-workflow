# 下單資料格式：BARC DACN（參考條件表）

- 設定檔：所有上手共用的 [`config/reference_sheet.toml`](../../../config/reference_sheet.toml)
- 狀態：規格（實作另開 PR，workflow #547）。共用的版面、配對、回填規則見[參考條件表格式](reference-sheet.md)；和 [BARC 核對規則](../rules/barc-check-rules.md) §9 相同的不重寫，本文件只寫 DACN 的差異

## 1. 結構

和 BARC FCN 相同（商品代號前 3 碼 `029`）。商品種類看 `Product` = `DACN`：辨識到的範本是 `barc-zh-dacn-pd`／`barc-zh-dacn-iis` 時，該列 Product 必須是 `DACN`；Product 是 `DACN`、文件卻是 BARC FCN 範本（或反過來）→ 商品種類不一致，轉人工覆核（共用 `template.product_family`）。BARC DACN 的名稱和 FCN 一樣，只能靠文件的配息公式分辨。

開放自動核對：`config/issuer_prefixes.toml` `[auto_check_products]` 的 `DACN` 加 `BARC`。

探勘用的參考條件表是私人雲端倉庫的舊總表（第一張工作表，涵蓋 BK01～BK04；BK05 是最新的一檔，舊總表還沒有）。舊總表沒有 `配息門檻(%)` 欄。

## 2. 欄位對照（只列和 BARC FCN 不同的）

| Excel 欄 | 標準欄位 | 說明書來源（見[範本規格](../templates/barc-zh-dacn.md)） |
|---|---|---|
| Product | `product` | 不和說明書比；= `DACN` 才能用 DACN 範本（§1） |
| 配息門檻(%) | `coupon_barrier_pct` | §14 `「配息門檻」詳見本章第15條之表格所示（為最初價格的Y%）` 的 Y。表上**沒有這欄**（舊 Excel）或**有這欄但該列空白或 `-`** → 拿說明書的配息門檻和 `K(%)` 比（共用 `field.coupon_barrier_pct`，同第一波 DACN） |
| Coupon p.a. (%) | `coupon_pa_pct` | 說明書與投資人須知都**沒有年利率**：不直接比；改由月配息率推算（`derive.monthly_coupon`：年利率 × 天期 ÷ 12 ÷ 配息表期數，和 §14 `配息率t=1=X%` 差 ≤ 0.0001，同 FCN）與投資人須知 `iis.monthly_coupon`（年利率 ÷ 12，差 ≤ 0.0001）核對 |
| Non-Call(月) | `first_callable_period` | 同 FCN D 型（§13(7) 表 G）；另核 = §14 配息公式保證期數 g |
| KO(Freq)／KO(memo)／KI(Freq)／K(%)／KO(%)／KI(%)／各價格 | 同 FCN | 同 FCN（樣本：`D`、`Y`、`-` 或 `AM`） |

§15 價格表的 `配息門檻` 每列價格沒有對應的表上欄位，只做說明書內部核對（= 最初價格 × 配息門檻 %，核對規則 §3.4）。

## 3. 允許值與 DACN 差異

| 欄 | 值 | 說明 |
|---|---|---|
| Product | `DACN` | 不分大小寫（共用） |
| 配息門檻(%) | 數字（樣本都 = `K(%)`） | 資料庫匯出的參考條件表 DACN 一定有值；舊總表、待建檔表沒有這欄 |
| KI(Freq) | `-` 或空白、`AM` | 樣本無 KI 3 份、到期觀察 2 份；`D` 依 FCN 規則推得、待樣本驗證；`P`（MKI）轉人工（workflow #386） |
| KO(Freq)、KO(memo) | `D`、`Y` | 只有這種有樣本 |
| Non-Call(月) | `1` | 樣本都是 1（= 保證期數）；其他值沒有樣本（核對規則 §3.1 轉人工） |
| 計價幣別 | `USD` | 樣本都是美元；其他幣別沒有樣本（轉人工） |

## 4. 比價日填法

同 BARC FCN D 型（[BARC 核對規則](../rules/barc-check-rules.md) §9）：`比價日_{Non-Call}` = §13(7) 表第 Non-Call 期期末日、`比價日_{天期}` = 最終評價日，其他寫 `-`。BK01～BK04 的舊總表比價日和這個填法全部相同。

## 5. 不核對的欄位

同 BARC FCN（`期初定價` 說明書沒寫；`UL_n_Memo` 不核對）。DACN 不追蹤每日計息（nt、Nt、各期配息率與配息金額），沒有對應的欄位要核對（ADR 0030）。

## 6. 探勘時發現的表上狀況（2026-11-13）

- BK01～BK04 的舊總表欄位（代號、ISIN、幣別、面額、各日期、天期、K／KO／KI %、KO／KI 型態、記憶式、Non-Call、比價日、各標的代號與價格）和說明書一致；表上年利率 ÷ 12 和 §14 月配息率差 ≤ 0.0001（BARC 月配息率顯示到 4 位，有 2 份是進位後的值）。
- BK05 舊總表沒有這一檔，試跑時用說明書反推的列只驗證辨識與說明書內部規則（不當作表上欄位驗證）。
