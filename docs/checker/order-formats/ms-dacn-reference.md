# 下單資料格式：MS DACN（參考條件表）

- 設定檔：所有上手共用的 [`config/reference_sheet.toml`](../../../config/reference_sheet.toml)
- 狀態：規格（實作另開 PR，workflow #550）。共用的版面、配對、回填規則見[參考條件表格式](reference-sheet.md)；和 [MS 下單資料格式](ms-fcn-reference.md)（FCN）相同的不重寫，本文件只寫 DACN 的差異

## 1. 結構

和 MS FCN 相同（商品代號前 3 碼 `147`，`發行機構` 欄寫 `MS`；支援樣態表照舊）。商品種類看 `Product` = `DACN`：辨識到的範本是 `ms-zh-dacn-pd`／`ms-zh-dacn-iis` 時，該列 Product 必須是 `DACN`；Product 是 `DACN`、文件卻是 MS FCN 或 BEN 範本（或反過來）→ 商品種類不一致，轉人工覆核（共用 `template.product_family`）。

開放自動核對：`config/issuer_prefixes.toml` `[auto_check_products]` 的 `DACN` 加 `MS`。DACN 沒有樣本的型態由範本專屬規則轉人工（[核對規則](../rules/ms-dacn-check-rules.md) §3.1）。

探勘用的參考條件表是私人雲端倉庫的舊總表（第一張工作表，涵蓋 MD1、MD2）。舊總表沒有 `配息門檻(%)` 欄。

## 2. 欄位對照（只列和 MS FCN 不同的）

| Excel 欄 | 標準欄位 | 說明書來源（見[範本規格](../templates/ms-zh-dacn.md)） |
|---|---|---|
| Product | `product` | 不和說明書比；= `DACN` 才能用 DACN 範本（§1） |
| 配息門檻(%) | `coupon_barrier_pct` | 第 16 項價格表 `執行價 （期初價格的X%）` 的 X（第 15 項 n(j) 比執行價）。表上**沒有這欄**（舊 Excel）或**有這欄但該列空白或 `-`** → 拿說明書的配息門檻和 `K(%)` 比（共用 `field.coupon_barrier_pct`，同第一波 DACN） |
| Coupon p.a. (%) | `coupon_pa_pct` | 同 FCN：說明書沒有年利率；月配息率 = 年利率 ÷ 12、情境一年化報酬率 = 年利率（核對規則 §3.3、§3.7） |
| Non-Call(月) | `first_callable_period` | 同 FCN D 型（第 17 項 `記憶事件觀察日：每日觀察，為自第k 個配息週期終止日…` 的 k）；另核 = 第 15 項保證期數 g |
| KO(Freq)／KO(memo)／KI(Freq)／K(%)／KO(%)／KI(%)／各價格 | 同 FCN | 同 FCN（樣本：`D`、`Y`、`AM`） |

配息門檻價格 = 各標的執行價，沒有另外的表上欄位（核對規則 §3.4）。

## 3. 允許值與 DACN 差異

| 欄 | 值 | 說明 |
|---|---|---|
| Product | `DACN` | 不分大小寫（共用） |
| 配息門檻(%) | 數字（樣本都 = `K(%)`） | 資料庫匯出的參考條件表 DACN 一定有值；舊總表、待建檔表沒有這欄 |
| KO(Freq) | `D` | 只有每日有樣本；`P` 的 MS 說明書沒有區間計息公式（範本無法辨識、轉人工） |
| KO(memo) | `Y` | 樣本都是記憶式；`N` 沒有樣本（轉人工） |
| KI(Freq) | `AM` | 樣本都是到期觀察；`-`、`D` 沒有樣本（轉人工）；`P`（MKI）照 workflow #386 轉人工 |
| Non-Call(月) | `1` | 樣本都是 1（= 保證期數）；其他值沒有樣本（轉人工） |
| 計價幣別 | `USD` | 樣本都是美元；其他幣別沒有樣本（轉人工） |

## 4. 比價日填法

同 MS FCN D 型（[MS 下單資料格式](ms-fcn-reference.md) §4）：`比價日_{Non-Call}` = 第 14 項 (6) 第 Non-Call 期的配息週期終止日、`比價日_{天期}` = 期末定價日（= 末期終止日），其他寫 `-`。MD1、MD2 的舊總表比價日和這個填法全部相同。

## 5. 不核對的欄位

同 MS FCN §5。DACN 不追蹤每日計息（n(j)、N(j)、配息觀察日、各期配息金額），沒有對應的欄位要核對（ADR 0030）。

## 6. 探勘時發現的表上狀況（2026-11-13）

- MD1、MD2 的舊總表欄位（代號、ISIN、面額、各日期、天期、K／KI／KO %、KO 型態、記憶式、KI、Non-Call、比價日、各標的代號與期初價格、年利率）和說明書一致。
- 舊總表的 `K(%)` 等於說明書的執行價 %（= 配息門檻）；表上沒有配息門檻欄，試跑時另做加欄、改錯版本（核對規則 §6）。
