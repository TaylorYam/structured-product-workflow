# 核對規則：BARC FCN_Stepdown 中文產品說明書與投資人須知

- 文件側：[BARC FCN_Stepdown 範本規格](../templates/barc-zh-stepdown.md)（`barc-zh-stepdown-pd`）、[BARC FCN_Stepdown 投資人須知範本規格](../templates/barc-zh-stepdown-iis.md)（`barc-zh-stepdown-iis`）
- 下單側：參考條件表，見[參考條件表格式](../order-formats/reference-sheet.md)與 [BARC FCN_Stepdown 下單資料格式](../order-formats/barc-stepdown-reference.md)
- 商品：FCN_Stepdown（workflow ADR 0033、#537）；Barclays 是第一家開放的上手（workflow #544），Natixis 接著做（workflow #545），共用的分流與 KO 降幅核對（本文件 §3.3）寫在[共用核對規則](shared-check-rules.md)（§3 參考條件表欄位、§11 Stepdown）
- 狀態：已實作（workflow #544）。核對範圍以開發者凍結的螢光標記為準；**作業人員獨立標記略過**（同 DACN／BEN 第一波，§7）
- 本文件只寫和 [BARC 核對規則](barc-check-rules.md)（FCN）不同或 Stepdown 特有的部分。沒提到的（審查標準、固定警語、受託機構名稱與其他出處、發行機構名稱、信用評等、幣別、費率、最低申購與贖回、受理申購日、刊印日期、審查通過日期、月配息率、價格推算、表頭 %、KI 的內部一致、配息表與提前出場表的日期關係、Non-Call、保證配息期、§13(7) 觀察期 t 範圍、§9(3) 與 §16 重複出現的月配息率、情境總報酬率與年化、受託機構商品代號）照 BARC FCN

## 1. 範圍

- 依第 13 條 (7) 的 `自動提前出場觸發百分比:詳見下表所示` 辨識商品家族，不靠名稱（Stepdown 名稱和 FCN 相同）；同一家上手由專到泛，先試 Stepdown 範本、不符才試 FCN 範本（`issuers.py`，#436；範本規格 §4）。辨識結果和參考條件表 Product 不一致時轉人工覆核。
- 認得出 Stepdown、但寫法不在範本規格內（KO% 不是每期固定往下降、非記憶式、§15 仍有 KO 定義句等）→ 不支援的範本版本（`template_unsupported`），轉人工覆核、不能人工放行。
- 兩份都通過或人工放行才打 `TS`／`IIS` 的 `V` 並回填（ADR 0007）。

## 2. 不核對的項目

- 照 BARC FCN §2。
- 第 15 條 KO 定義句、KO 價欄、表頭 KO %：Stepdown 沒有（FCN 的 `doc.autocall_trigger_per_period` 改成 §3.3 的每期 KO% 規則；`doc.price_header_pct` 沒有 KO 欄頭就不產生 KO 那一項）。
- 第 2 期以後各標的的 KO 價：說明書沒有寫，系統也不存（ADR 0033 比價時才算），只核對每期 KO%。
- 投資人須知：沒有 KO%、降幅或 KO 價，不核對這幾項。

## 3. 核對規則

### 3.1 樣態與分流

| 檢查 | 規則 |
|---|---|
| 開放 | `[auto_check_products]` 的 `FCN_Stepdown = ["BARC"]`；沒開放的上手照未開放商品轉人工 |
| 範本版本 | 寫法和範本規格 §4 不同 → 不支援的範本版本（`template.detect`，原因 `template_unsupported`），錯訊列出不成立的條件 |
| 商品種類一致 | 辨識到的範本商品家族 ≠ Product → 轉人工覆核（共用 `template.product_family`）；Product 空白視為 FCN |
| 不支援樣態 | 照 BARC FCN（KI 每期觀察） |

### 3.2 參考條件表欄位（共用 `rules/reference.py`、`backfill.py`）

| 標準欄位 | 規則 | Stepdown 差異 |
|---|---|---|
| `ko_pct` | 共用 `field.ko_pct` | 說明書值 = 提前出場表第 1 期觸發百分比 |
| `ko_step_pct` | 共用 `field.ko_step_pct`（新，§3.3） | — |
| `ko_levels` | 共用 `field.ko_levels`（新，§3.3） | — |
| `ko_memory` | 共用 `field.ko_memory` | 說明書值由第 13 條 (7) 記憶事件句判定 |
| 各標的 KO 價 | 共用 `field.underlying_prices` | 說明書值 = 最初價格 × 第 1 期 KO%，四捨五入（half-up）到 4 位（adapter 推算，範本規格 §2.3） |
| 比價日 | 共用 `backfill.compare_dates` | D 型每一期期末日都填（§3.4）；P 型照 FCN |
| 其他 | 共用 `field.*`、`backfill.*` | 同 BARC FCN |

### 3.3 KO 降幅與每期 KO%（共用規則，所有上手的 Stepdown 共用）

| 檢查 | 規則 |
|---|---|
| 降幅 | 說明書降幅（每期 KO% 相鄰兩期的差，每期相同）= 表上 `KO降幅(%)`，依說明書顯示位數比（`field.ko_step_pct`）。表上沒有這欄、空白、`-` → 轉人工覆核（Stepdown 一定要有降幅）；這條跟 `ko_step_required`（FCN_Stepdown、Snowball_Stepdown）走，Snowball 表上空白是不降，不在本規則定義 |
| 每期 KO% | 說明書第 n 期 KO% = 表上 `KO(%)` − `KO降幅(%)` ×（n − 1），逐期比；不一致時列出哪幾期（`field.ko_levels`）。表上兩欄有一欄讀不到 → 轉人工覆核 |
| 起始 KO% | 第 1 期 = 表上 `KO(%)`（共用 `field.ko_pct`，同 FCN） |

只有 Product 的商品規則有 KO 降幅的商品（`ProductRule.has_term(KO_STEP_FIELD)`：FCN_Stepdown、Snowball 類）產生這兩項；其他商品不產生結果。說明書 KO% 不是每期固定往下降時在範本辨識就轉人工（§3.1），不會走到這裡。

### 3.4 比價日（D 型）

D 型 Stepdown：`比價日_{Non-Call}`～`比價日_{期數}` = 提前出場表第 k 期到末期每一期的期末日（ADR 0033「有 KO 降幅的每日型要填每一期的結束日」）；最晚的比價日 = 最終比價日（同 FCN）。表上空白的格子通過後回填，有值的格子比對。P 型照 FCN。

### 3.5 說明書內部交叉驗證

照 BARC FCN §3.7。不同的地方：

| 檢查 | Stepdown |
|---|---|
| 觸發百分比 | FCN「§13(7) 每期百分比 = §15 定義句」不適用（Stepdown 沒有定義句）；改由範本辨識確認每期固定往下降（§3.1）、§3.3 和參考條件表逐期比 |
| 價格表欄頭比例 | 只有執行價格、觸及生效價格欄頭（同 FCN 規則，沒有 KO 欄頭） |
| 記憶式 | 第 13 條 (7) 有記憶事件句 ⇔ 商品名稱含 `記憶式`（同 FCN 的一致性檢查，出處換成記憶事件句） |
| 價格推算 | 執行價、下限價 = 最初價格 × %（共用 `derive.prices`）；KO 價是 adapter 推算的，同 SG／MS 沒有 KO 欄的做法 |

### 3.6 投資人須知

照[投資人須知核對規則](iis-check-rules.md) §3 的 BARC 欄；不同的地方：

| 檢查點 | Stepdown |
|---|---|
| 名稱 | = 說明書封面名稱（同 FCN 名稱樣板） |
| 價格表 | 進場、執行、下限價照 FCN；沒有 KO 欄，KO 價與 KO% 不核對 |

## 4. 判定原則

同 BARC FCN §4：必核欄位抓不到、抓到多個不同值、或寫法不在範本規格內，一律轉人工覆核。每期 KO% 只由提前出場表的數字推得，不由名稱或關鍵字推測。

## 5. 探勘比對結果（2026-11-13）

探勘腳本（本機 `data/tmp/stepdown/`）用現有 BARC FCN parser 讀 6 份說明書與投資人須知：

| 範圍 | 結果 |
|---|---|
| 現行 BARC FCN 範本 | 6 份說明書、投資人須知都被辨識成 BARC FCN（名稱、骨架相同）；說明書讀不到 KO%、記憶式（§15 沒有定義句）與價格表（沒有 KO 欄），投資人須知價格表讀不到 → 不能直接沿用，要有自己的範本 |
| 提前出場表 | D 型 4 份、P 型 2 份；觸發百分比從第一個可提前出場的期別起每期固定往下降（起始 KO% 有 100 與小於 100，降幅都是整數百分點），沒有不等距 |
| 其他欄位 | 配息表、提前出場表日期欄、保證配息期、KI、標的、第 16 條情境的讀法和 FCN 相同 |

## 6. 試跑預期（實作 PR 的驗收）

以私人雲端倉庫的 6 份樣本、參考條件表（資料庫來源或照樣本整理，`Product` = `FCN_Stepdown`、`KO降幅(%)` 有值）在本機跑完整核對：

- 全部辨識成 `barc-zh-stepdown-pd`／`barc-zh-stepdown-iis`；沒有一份被 BARC FCN 範本認走。
- KO%、降幅、每期 KO%、第 1 期 KO 價和參考條件表一致；改錯降幅或某一期 KO% 時判不一致。
- D 型比價日每一期都回填；P 型同 FCN。
- BARC FCN 與其他上手既有測試、本機真實樣本結果不變。

## 7. 核對範圍（開發者螢光標記，2026-11-13）

**作業人員獨立標記略過**（同 DACN／BEN 第一波）；範圍以開發者凍結的標記為準。代表樣本 2 份：BS05（D 型、6 個月、有 KI）、BS03（P 型、12 個月、無 KI）。標記檔、清單與凍結雜湊在本機 `data/tmp/stepdown/marks/`。說明書 D01～D18、投資人須知 I01～I06（兩份編號相同）。

| 標記 | 條號／欄位 | 決定 |
|---|---|---|
| D01 | 封面中英文名稱 | 名稱樣板照 BARC FCN（Stepdown 名稱與 FCN 相同，不能用名稱辨識） |
| D02 | 封面代號、ISIN、幣別、審查日、受託代號、發行機構、刊印日期 | 照 BARC FCN |
| D03 | 第一章第 1、5、6、10 條 | 照 BARC FCN |
| D04 | 第 9 條 (3) 相關配息率（D 型） | 照 FCN `doc.coupon_repeats` |
| D05 | 第 13 條 (1)～(5) 天期與日期 | 照 FCN |
| D06 | 第 13 條 (6) 配息表 | 照 FCN |
| D07 | 第 13 條 (7) 記憶事件句 | 記憶式改由這句判定（§3.5）；非記憶式沒有樣本 → 範本不支援 |
| D08 | 第 13 條 (7) `自動提前出場觸發百分比:詳見下表所示` | Stepdown 範本辨識標記（§3.1） |
| D09 | 第 13 條 (7) 觸發價格定義（最初價格 × 相對應觸發百分比） | 範本辨識（固定寫法）；第 1 期 KO 價 = 最初價格 × 第 1 期 KO%（§3.2） |
| D10 | 提前出場表 `自動提前出場觸發百分比` 欄 | 第 1 期 = `KO(%)`；降幅 = `KO降幅(%)`；每期 = 起始 − 降幅 ×（n − 1）（§3.3）；不是每期固定往下降 → 範本不支援 |
| D11 | 提前出場表日期欄、Non-Call | 照 FCN C1～C3、Non-Call、保證配息期；比價日 P 照 FCN、D 每一期期末日都填（§3.4） |
| D12 | 第 13 條 (7) 觀察期 t 範圍定義句、評價日定義 | 照 FCN |
| D13 | 第 14 條配息率、年利率 | 照 FCN |
| D14 | 第 15 條執行價格、觸及生效價格定義句與 KI 型態 | 照 FCN |
| D15 | 第 15 條價格表（沒有 KO 欄） | 進場、執行、下限價照 FCN；KO 價由第 1 期 KO% 推算後和參考條件表比（§3.2） |
| D16 | 第 16 條有利情況 | 照 FCN `doc.scenario_returns` |
| D17 | 第 16 條一般、最差情況與重印價格表 | 照 FCN（重印表也沒有 KO 欄） |
| D18 | 第二～四章（董事長、機構、費用、最低申購與贖回、受理日） | 照 BARC FCN |
| I01 | 封面名稱、代號、ISIN、風險等級 | 照 BARC FCN 投資人須知（名稱 = 說明書） |
| I02 | 警語、相關機構事業概況 | 照 FCN |
| I03 | 商品簡介面額、發行價格、標的、年期、發行日、到期日 | 照 FCN |
| I04 | 收益分配事項月配息率、年利率 | 照 FCN |
| I05 | 收益分配事項價格表（沒有 KO 欄）與表前說明句（不列觸發價格） | 進場、執行、下限價照 FCN；投資人須知範本辨識（兩項都成立）；KO% 不核 |
| I06 | 費用明細表、信用評等 | 照 FCN |

每條新規則實作時要有合成反例：表上 `KO降幅(%)` 和說明書不同；表上降幅空白；某一期 KO% 不是起始 − 降幅 ×（n − 1）（說明書不等距 → 範本不支援）；KO% 每期相同或往上升（範本不支援）；表上 KO 價 ≠ 最初價格 × 第 1 期 KO%；Product 是 FCN 但文件是 Stepdown（及反過來）；BARC FCN 說明書不被 Stepdown 範本認走；Stepdown 說明書仍有 §15 KO 定義句（範本不支援）；沒有記憶事件句（範本不支援）；D 型比價日表上只填開始與最後（中間格回填、已有值的格子比對）；投資人須知價格表有 KO 欄，或表頭沒有 KO 欄、表前說明句卻列觸發價格（都不是 Stepdown 投資人須知）；D 型第 1 列期始日就有日期（範本不支援）；保證配息期 2（開頭兩列 `N/A`，照 BARC FCN 讀法接受）；BARC FCN D 型比價日仍只填兩格（回歸）。

## 8. 實作方式（workflow #544）

| 內容 | 位置 |
|---|---|
| Stepdown 共用讀法 | `parsers/stepdown.py`（新）：每期 KO%（`ko_levels`）、降幅（`ko_step_pct`）、第 1 期 KO 價、D 型每期比價日；各上手交入讀好的每期觸發百分比，Natixis（workflow #545）沿用 |
| 範本辨識與讀出 | `parsers/barc_stepdown.py`、`parsers/barc_stepdown_iis.py`：沿用 `parsers/barc.py`、`barc_iis.py` 的 `read`（價格表的宣告由參數交入：沒有 KO 欄），再換掉 KO%、記憶式、價格與比價日 |
| 上手註冊 | `issuers.py` 登記 `BARC_STEPDOWN`（`products=frozenset({FCN_STEPDOWN})`，上手代號同 `BARC`，放在 `BARC` 前面） |
| 規則 | `rules/barc_stepdown.py`：沿用 `rules/barc.py`，把 `doc.autocall_trigger_per_period` 換成不適用（§3.5）；共用 `field.ko_step_pct`、`field.ko_levels` 放在 `rules/reference.py` |
| 設定 | `[auto_check_products]` 新增 `FCN_Stepdown = ["BARC"]`；名稱樣板沿用 `[issuer.barc.product_name]`（不另加 `product_names`） |
| 測試 | 合成 PDF（沿用 BARC 合成樣本，改 §13(7) 與 §15）；§7 的反例；本機真實樣本辨識與讀取測試（環境變數指向本機樣本，不進 repo）；試跑（§6） |

## 9. 決策紀錄

| 日期 | 決定 |
|---|---|
| 2026-11-13 | 作業人員獨立標記略過（同 DACN／BEN 第一波），核對範圍以開發者凍結的標記為準 |
| 2026-11-13 | 依第 13 條 (7) `自動提前出場觸發百分比:詳見下表所示` 辨識 Stepdown，不看名稱（和 FCN 相同）；FCN 範本不加條件，由上手註冊表由專到泛決定 |
| 2026-11-13 | KO 期別照 ADR 0033：第一個可提前出場的期別是第 1 期；KO(%) 是第 1 期 |
| 2026-11-13 | 各標的 KO 價 = 最初價格 × 第 1 期 KO%（half-up 4 位），和表上 `UL_n_KO價` 比（同 ADR 0033 資料庫存法） |
| 2026-11-13 | D 型比價日每一期期末日都填（ADR 0033、參考條件表格式 `KO降幅(%)`）；Issue 寫「照 FCN 規則（P／D 型）」，FCN D 型只填兩格會讓比價待確認，所以照 ADR 0033（顧問審查確認，不列待使用者決定） |
| 2026-11-13 | 沒有樣本的型態逐項處置（範本規格 §3）：非記憶式、KO 不降或不等距、D 型保證配息期 0 → 範本不支援；保證配息期 ≥ 2、單一標的、美元以外幣別 → 照 BARC FCN 讀法接受 |
| 2026-11-13 | 投資人須知要表頭沒有 KO 欄、表前說明句也不列觸發價格兩項都成立才是 Stepdown（只看表頭時，KO 欄頭讀不到的 FCN 投資人須知會被認成 Stepdown、不能人工放行） |

## 10. 規則對照

| 核對規則 | rule_id |
|---|---|
| §3.1 範本版本、商品種類一致 | `template.detect`（`template_unsupported`）、`template.product_family` |
| §3.2 參考條件表欄位 | 共用 `field.*`、`backfill.*` |
| §3.3 KO 降幅與每期 KO% | `field.ko_step_pct`、`field.ko_levels`（新，共用）；`field.ko_pct`（同 FCN） |
| §3.4 比價日 | 共用 `backfill.compare_dates` |
| §3.5 內部交叉驗證 | 照 BARC FCN（`doc.*`、`schedule.*`、`derive.prices`）；`doc.autocall_trigger_per_period` 不適用 |
| §3.6 投資人須知 | 共用 `iis.*` |
