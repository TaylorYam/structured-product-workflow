# 下單資料格式：UBS DACN（參考條件表）

- 設定檔：所有上手共用的 [`config/reference_sheet.toml`](../../../config/reference_sheet.toml)
- 狀態：規格（實作另開 PR，workflow #549）。共用的版面、配對、回填規則見[參考條件表格式](reference-sheet.md)；和 [UBS 下單資料格式](ubs-fcn-reference.md)（FCN）相同的不重寫，本文件只寫 DACN 的差異

## 1. 結構

和 UBS FCN 相同（商品代號前 3 碼 `092`，`發行機構` 欄寫 `UBS`；支援樣態表 `UBS = ["vwap", "non_call_equals_tenor"]` 照舊）。商品種類看 `Product` = `DACN`：辨識到的範本是 `ubs-zh-dacn-pd`／`ubs-zh-dacn-iis` 時，該列 Product 必須是 `DACN`；Product 是 `DACN`、文件卻是 UBS FCN 範本（或反過來）→ 商品種類不一致，轉人工覆核（共用 `template.product_family`）。

開放自動核對：`config/issuer_prefixes.toml` `[auto_check_products]` 的 `DACN` 加 `UBS`。DACN 沒有樣本的型態由範本專屬規則轉人工（[核對規則](../rules/ubs-dacn-check-rules.md) §3.1）。

探勘用的參考條件表是私人雲端倉庫的舊總表（第一張工作表，涵蓋 UD1～UD3）。舊總表沒有 `配息門檻(%)` 欄。

## 2. 欄位對照（只列和 UBS FCN 不同的）

| Excel 欄 | 標準欄位 | 說明書來源（見[範本規格](../templates/ubs-zh-dacn.md)） |
|---|---|---|
| Product | `product` | 不和說明書比；= `DACN` 才能用 DACN 範本（§1） |
| 配息門檻(%) | `coupon_barrier_pct` | 八價格表表頭 `區間下限價(即期初價格的X%)` 的 X。表上**沒有這欄**（舊 Excel）或**有這欄但該列空白或 `-`** → 拿說明書的配息門檻和 `K(%)` 比（共用 `field.coupon_barrier_pct`，同第一波 DACN） |
| Coupon p.a. (%) | `coupon_pa_pct` | 同 FCN：八 `固定配息率：x%。（年化配息率為y%）` 的 y |
| Non-Call(月) | `first_callable_period` | 同 FCN D 型（`提前出場觀察日：每日觀察，為自第k 個配息觀察期間結束日…` 的 k）；另核 = 八配息表保證期數 g |
| UL_n_下限價 | `underlying_n_ki_price` | 同 FCN 對應八價格表 `下限價` 欄；**不是 `區間下限價` 欄**（DACN 樣本都沒有 KI，表上 `-`） |
| KO(Freq)／KO(memo)／KI(Freq)／K(%)／KO(%)／KI(%)／其他價格 | 同 FCN | 同 FCN（樣本：`D`、`Y`／`N`、`-`） |

八價格表 `區間下限價` 欄每檔價格沒有對應的表上欄位，只做說明書內部核對（= 期初價格 × 配息門檻 %，核對規則 §3.4）。

## 3. 允許值與 DACN 差異

| 欄 | 值 | 說明 |
|---|---|---|
| Product | `DACN` | 不分大小寫（共用） |
| 配息門檻(%) | 數字（樣本都 = `K(%)`） | 資料庫匯出的參考條件表 DACN 一定有值；舊總表、待建檔表沒有這欄 |
| KO(Freq) | `D` | 只有每日有樣本；`P` 沒有樣本（轉人工） |
| KO(memo) | `Y`、`N` | 樣本記憶式 2 份、非記憶式 1 份，兩種都照常核對 |
| KI(Freq) | `-` | 樣本都沒有 KI；`D`、`AM` 沒有樣本（轉人工）；`P`（MKI）照 workflow #386 轉人工 |
| Non-Call(月) | `1` | 樣本都是 1（= 保證期數）；其他值沒有樣本（轉人工） |
| 計價幣別 | `USD` | 樣本都是美元；其他幣別沒有樣本（轉人工） |
| 期初定價 | `開盤價`／`收盤價`／空白 | 同 FCN（說明書沒寫，不比對；`VWAP` 是 UBS 不支援樣態） |

## 4. 比價日填法

同 UBS FCN D 型（[UBS 下單資料格式](ubs-fcn-reference.md) §4）：`比價日_{Non-Call}` = 配息表第 Non-Call 期的配息觀察期間結束日、`比價日_{天期}` = 期末訂價日（= 末列結束日），其他寫 `-`。UD1～UD3 的舊總表比價日和這個填法全部相同。

## 5. 不核對的欄位

同 UBS FCN §5（`期初定價` 只檢查允許值；`UL_n_Memo` 不核對）。DACN 不追蹤每日計息（nt、Nt、交易所營業日、各期配息金額），沒有對應的欄位要核對（ADR 0030）。

## 6. 探勘時發現的表上狀況（2026-11-13）

- UD1～UD3 的舊總表欄位（代號、ISIN、面額、各日期、天期、K／KO %、KO 型態、記憶式、KI、Non-Call、比價日、各標的代號與期初價格、年利率）和說明書一致。
- 舊總表的 `K(%)` 等於說明書的區間下限價 %（3 份都是）；表上沒有配息門檻欄，試跑時另做加欄、改錯版本（核對規則 §6）。
