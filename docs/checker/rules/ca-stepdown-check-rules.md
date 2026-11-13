# 核對規則：CA FCN_Stepdown 中文產品說明書與投資人須知

- 文件側：[CA FCN_Stepdown 範本規格](../templates/ca-zh-stepdown.md)（`ca-zh-stepdown-pd`）；投資人須知用 CA FCN 的 [`ca-zh-iis`](../templates/ca-zh-iis.md)（§3.6）
- 下單側：參考條件表，見[參考條件表格式](../order-formats/reference-sheet.md)與 [CA FCN_Stepdown 下單資料格式](../order-formats/ca-stepdown-reference.md)
- 商品：FCN_Stepdown（workflow ADR 0033、#537）；BARC（workflow #544）、Natixis（workflow #545）已開放，本文件是第三家（workflow #546）。KO 降幅與每期 KO% 的核對、D 型每期比價日、KO% 期數檢查沿用共用層（`parsers/stepdown.py`、`field.ko_step_pct`、`field.ko_levels`，[共用核對規則](shared-check-rules.md) §11）
- 狀態：已實作（workflow #546：`rules/ca_stepdown.py`；投資人須知沿用 `rules/ca_iis.py`）。核對範圍以開發者凍結的螢光標記為準；**作業人員獨立標記略過**（同 BARC、Natixis Stepdown，§7）
- 本文件只寫和 [CA 核對規則](ca-check-rules.md)（FCN）不同或 Stepdown 特有的部分。沒提到的（審查標準、固定警語、名稱、受託機構、發行機構名稱、幣別、費率、最低申購與贖回、受理申購日、刊印日期、配息率、價格推算、KI、配息表、Non-Call、情境分析、重複寫出處）照 CA FCN

## 1. 範圍

- 依第 9 項 `(1)(b)` 有 `期初訂價之N.NN%` 辨識商品家族，不靠名稱（Stepdown 名稱和 FCN 相同）；同一家上手由專到泛，依序試 Stepdown、BEN、Super Bull、FCN 範本（`issuers.py`，#436；範本規格 §4）。說明書辨識結果和參考條件表 Product 不一致時轉人工覆核。
- 認得出 Stepdown、但寫法不在範本規格內（舊範本表頭、KO% 不是每期固定往下降、非記憶式、k ≥ 2、價格表仍有 KO 欄、每期 KO 表期別和配息表對不上等）→ 不支援的範本版本（`template_unsupported`），轉人工覆核、不能人工放行；作業人員人工核對完，在 workflow「7 人工維護」用「人工核對完成…」輸入 ISIN 與比價日、鎖住（workflow #527，ADR 0032）。
- 投資人須知沒有可靠的 Stepdown 寫法，不另立範本：`ca-zh-iis` 同時涵蓋 FCN、FCN_Express 與 FCN_Stepdown（§3.6）。
- 兩份都通過或人工放行才打 `TS`／`IIS` 的 `V` 並回填（ADR 0007）。

## 2. 不核對的項目

- 照 CA FCN §2。
- 第 9 項價格表與第 16 項情境價格表的 KO 欄與表頭 KO %：Stepdown 沒有（表頭 % 檢查沒有 KO 欄就不產生 KO 那一項，同 FCN 無 KI 時的做法）。
- 第 2 期以後各標的的 KO 價：說明書沒有寫，系統也不存（ADR 0033 比價時才算），只核對每期 KO%。
- 每期 KO 表後的觀察期定義句：只當範本辨識特徵，內容不核對。
- 投資人須知：沒有 KO%、降幅或價格，不核對這幾項（同 CA FCN 投資人須知）。

## 3. 核對規則

### 3.1 樣態與分流

| 檢查 | 規則 |
|---|---|
| 開放 | `[auto_check_products]` 的 `FCN_Stepdown` 含 `CA`；沒開放的上手照未開放商品轉人工 |
| 範本版本 | 寫法和範本規格 §4 不同 → 不支援的範本版本（`template.detect`，原因 `template_unsupported`），錯訊列出不成立的條件 |
| 商品種類一致 | 說明書辨識到的範本商品家族 ≠ Product → 轉人工覆核（共用 `template.product_family`）；Product 空白視為 FCN。投資人須知見 §3.6 |
| 不支援樣態 | 照 CA FCN（`CA = ["ki_periodic"]`，KI 每期觀察轉人工） |

### 3.2 參考條件表欄位（共用 `rules/reference.py`、`backfill.py`）

| 標準欄位 | 規則 | Stepdown 差異 |
|---|---|---|
| `ko_pct` | 共用 `field.ko_pct` | 說明書值 = 第 9 項 `(1)(b)` 每期 KO 表第 1 期百分比（CA FCN 是價格表表頭） |
| `ko_step_pct` | 共用 `field.ko_step_pct`（#544） | — |
| `ko_levels` | 共用 `field.ko_levels`（#544） | — |
| `ko_memory` | 共用 `field.ko_memory` | 照 CA FCN 兩句判斷，另收範本規格 §1 的兩種措辭（§3.5） |
| 各標的 KO 價 | 共用 `field.underlying_prices` | 說明書值 = 期初訂價 × 第 1 期 KO%，四捨五入（half-up）到 4 位（adapter 推算，範本規格 §2.3） |
| 比價日 | 共用 `backfill.compare_dates` | D 型每一期期末日都填（§3.4） |
| 其他 | 共用 `field.*`、`backfill.*` | 同 CA FCN |

### 3.3 KO 降幅與每期 KO%

照 [BARC FCN_Stepdown 核對規則](barc-stepdown-check-rules.md) §3.3（共用規則，所有上手的 Stepdown 共用）：說明書降幅 = 表上 `KO降幅(%)`；第 n 期 KO% = 表上 `KO(%)` − `KO降幅(%)` ×（n − 1），逐期比；表上沒有降幅欄、空白、0、負數、非數字 → 轉人工覆核。共用讀法的期數檢查（#545）照常：讀出的期數須 = 天期 − Non-Call + 1，不符 → `ko_levels` 讀不出（轉人工覆核）。CA 在範本辨識時已要求每期 KO 表 `i` 從 1 起連續、列數 = 配息表列數（範本規格 §4），這個檢查是共用層的保險。

### 3.4 比價日（D 型）

`比價日_{Non-Call}`～`比價日_{天期}` = 第 9 項 `(1)(a)` 配息表第 k 期到末期每一期的 `配息期結束日`（ADR 0033「有 KO 降幅的每日型要填每一期的結束日」，共用 `stepdown.every_period`，同 BARC、Natixis）；最晚的比價日 = 期末訂價日。表上空白的格子通過後回填，有值的格子比對。CA FCN D 型只填開始與最後兩格，不受影響。

### 3.5 說明書內部交叉驗證

照 CA FCN §3.5。不同的地方：

| 檢查 | Stepdown |
|---|---|
| 每期 KO 表日期（新） | 每期 KO 表第 `i` 列的 `自動提前出場觀察期結束日` = 配息表同一期 `i` 的 `配息期結束日`（`doc.ko_table_dates`，同 Natixis；不同 → 判不一致，列出哪幾期）。`i` 本身對不上（不連續、列數 ≠ 配息表列數）是範本不支援（§3.1），不走到這裡 |
| 表頭 %、價格推算 | 第 9 項價格表只有執行價、下限價欄（共用 `derive.prices` 只算這兩欄；表頭 % 沒有 KO 那一項）；KO 價是 adapter 推算的，同 BARC、Natixis Stepdown |
| 情境價格表 | 共用 `scenario.reprint` 照常逐格比第 9 項價格表（兩張都沒有 KO 欄） |
| 記憶式 | 照 CA FCN 兩句判斷；第一句另收 `高於或等於相關自動提前出場期所適用之自動提前出場價`，第二句另收 `無須於同一預定交易日或同一自動提前出場觀察期符合上開條件`（CS01、CS02）。只有其中一句 → 照 CA FCN 轉人工覆核 |
| KO 型態、Non-Call | 只有 D 型、k = 1（範本規格 §4）；配息表有 `配息期結束日` 欄照 CA FCN 檢查 |

### 3.6 投資人須知

CA Stepdown 的投資人須知和 FCN 的沒有可靠的區別：CS03 和 FCN 的寫法完全相同；CS01、CS02 只有記憶式的兩種措辭不同，CS02 另多觀察期定義句（CS01 沒有）。投資人須知沒有 KO%、降幅或價格表。因此：

| 檢查點 | Stepdown |
|---|---|
| 範本 | 用 `ca-zh-iis`，不另立 Stepdown 投資人須知範本；範本辨識條件不變 |
| 商品種類一致 | 投資人須知範本自己宣告涵蓋的商品種類（`IisTemplate.products`，沒有宣告時沿用同商品說明書範本的商品種類）：`ca-zh-iis` 涵蓋 `FCN`、`FCN_Express`、`FCN_Stepdown`，所以 Product 是 `FCN_Stepdown` 的投資人須知不會被 `template.product_family` 擋下。Stepdown 和 FCN 的分辨只由說明書把關（§3.1）。其他上手的投資人須知不受影響（沒有宣告） |
| 記憶式 | 照 CA FCN 投資人須知（`ca-zh-iis` §4.2）的兩句判斷，另收 §3.5 的兩種措辭（所有 CA 投資人須知都收；CA FCN 投資人須知目前沒有這兩種措辭，結果不變） |
| 觀察期定義句 | 不核對；不影響 `自動提前出場觀察日：` 的 D 句讀取 |
| 其他 | 照 [投資人須知核對規則](iis-check-rules.md) §3 的 CA 欄（名稱 = 說明書、日期、標的、年期、月配息率與年利率、KO 每日起始期、有無 KI `iis.has_ki`、費率） |

## 4. 判定原則

同 CA FCN §4：必核欄位抓不到、抓到多個不同值、或寫法不在範本規格內，一律轉人工覆核。每期 KO% 只由第 9 項 `(1)(b)` 每期 KO 表的數字推得，不由名稱或關鍵字推測。

## 5. 探勘比對結果（2026-11-13）

探勘腳本（本機 `data/tmp/stepdown/`）用現有 CA parser 讀 3 份現行版面說明書與投資人須知、5 份舊範本說明書，並掃描本機全部 CA 說明書：

| 範圍 | 結果 |
|---|---|
| 現行 CA FCN 範本 | 3 份說明書都被辨識成 `ca-zh-pd`（名稱、骨架相同）；價格表找不到 `自動提前出場價` 欄 → 標的、價格、KO% 都讀不到；CS01、CS02 的記憶式只認得一句 → 轉人工。投資人須知 3 份辨識成 `ca-zh-iis`；CS03 全部讀得到，CS01、CS02 只有記憶式讀不到（同上） |
| 每期 KO 表 | 3 份都是 D 型、k = 1；`i` 從 1 起連續、列數 = 天期；每列結束日 = 配息表同一期 `配息期結束日`；每期固定降整數百分點（共兩種降幅），起始 KO% 有 100 與大於 100 |
| 舊範本 | 5 份（2026-06 以前）每期 KO 表是 P 型觀察日表多一欄 KO 價，沒有觀察期定義句；目前都被 `ca-zh-pd` 辨識成 FCN（讀到價格表才出錯） |
| 範本標記 | 第 9 項 `(1)(b)` 有 `期初訂價之N.NN%` 只出現在 8 份 Stepdown（現行 3、舊 5）；本機 CA FCN 27 份、BEN 2 份、Super Bull 34 份說明書都沒有 |

## 6. 試跑預期（實作 PR 的驗收）

以私人雲端倉庫的 3 組現行樣本、參考條件表（資料庫來源或照樣本整理，`Product` = `FCN_Stepdown`、`KO降幅(%)` 有值）在本機跑完整核對：

- 說明書 3 份全部辨識成 `ca-zh-stepdown-pd`；投資人須知 3 份辨識成 `ca-zh-iis` 且商品種類一致。舊範本 5 份判範本不支援（轉人工）。沒有一份 Stepdown 說明書被 CA FCN、BEN、Super Bull 範本認走。
- KO%、降幅、每期 KO%、第 1 期 KO 價、記憶式（含新措辭）和參考條件表一致；改錯降幅或某一期 KO% 時判不一致。
- D 型比價日每一期都回填。
- CA FCN（本機 27 組）、BEN、Super Bull 與其他上手既有測試、本機真實樣本結果不變。

## 7. 核對範圍（開發者螢光標記，2026-11-13）

**作業人員獨立標記略過**（同 BARC、Natixis Stepdown）；範圍以開發者凍結的標記為準。代表樣本 2 份：CS03（同 FCN 措辭、有 KI（到期觀察）、起始 KO 不是 100）、CS02（新措辭、無 KI、投資人須知有觀察期定義句）。標記檔、清單與凍結雜湊在本機 `data/tmp/stepdown/marks/`。說明書 C01～C14、投資人須知 E01～E09（C11 只在有 KI 的 CS03 標到；E06 只在 CS02 標到）。

| 標記 | 條號／欄位 | 決定 |
|---|---|---|
| C01 | 封面代號、名稱、ISIN、幣別、刊印日期等 | 照 CA FCN |
| C02 | 封面警語 | 照 CA FCN |
| C03 | 第一章第 1 項名稱 | 和 FCN 相同（不能用名稱辨識）；名稱樣板照 FCN |
| C04 | 第 9 項 `(1)(a)` 配息表 | 照 FCN；每期 KO 表第 `i` 列結束日 = 同一期配息期結束日（§3.5） |
| C05 | 第 9 項 `(1)(b)` 記憶式兩句 | 照 FCN，另收兩種新措辭（§3.5）；非記憶式沒有樣本 → 範本不支援 |
| C06 | 每期 KO 表表頭 | 現行版面（`自動提前出場觀察期結束日`｜`自動提前出場價`）；舊範本表頭 → 範本不支援 |
| C07 | 每期 KO 表 KO 欄 | 第 1 期 = `KO(%)`；降幅 = `KO降幅(%)`；每期 = 起始 − 降幅 ×（n − 1）（§3.3）；第 9 項 `(1)(b)` 有 `N.NN%` 是 Stepdown 辨識標記；不是每期固定往下降 → 範本不支援 |
| C08 | 觀察期定義句 | 現行版面特徵（範本辨識），內容不核對 |
| C09 | `自動提前出場觀察日：自第k 個配息期結束日…` | KO 每日、Non-Call 照 FCN；k ≥ 2 沒有樣本 → 範本不支援；比價日每一期都填（§3.4） |
| C10 | 第 9 項 `(2)` 價格表（沒有 KO 欄） | 標的、期初訂價、執行價、下限價照 FCN；KO 價 = 期初訂價 × 第 1 期 KO%（§3.2） |
| C11 | `界限事件：` | KI 型態照 FCN |
| C12 | 第 13 項天期與日期 | 照 FCN |
| C13 | 第 16 項情境與情境價格表（沒有 KO 欄） | 照 FCN（`scenario.reprint`） |
| C14 | 第二～四章（費用、最低申購與贖回、受理日） | 照 FCN |
| E01 | 名稱、ISIN、代號、風險等級 | 照 CA FCN 投資人須知（名稱 = 說明書） |
| E02 | 警語、相關機構 | 照 FCN |
| E03 | 商品簡介幣別、標的、年期、日期 | 照 FCN |
| E04 | 月配息率、年利率 | 照 FCN |
| E05 | 自動提前出場事件 | 記憶式照 FCN，另收兩種新措辭（§3.6）；投資人須知沒有 KO%，不核 |
| E06 | 觀察期定義句（只有部分樣本有） | 不核對，也不當辨識標記 |
| E07 | `自動提前出場觀察日：自第k 個…` | KO 每日起始期照 FCN |
| E08 | 到期贖回金額 | 有無 KI 照 FCN（`iis.has_ki`） |
| E09 | 費用 | 照 FCN |

每條新規則實作時要有合成反例：表上 `KO降幅(%)` 和說明書不同；表上降幅空白；KO% 每期相同、往上升或不等距（範本不支援）；表上 KO 價 ≠ 期初訂價 × 第 1 期 KO%；Product 是 FCN 但說明書是 Stepdown（及反過來）；CA FCN、BEN、Super Bull 說明書不被 Stepdown 範本認走；舊範本表頭（範本不支援）；沒有觀察期定義句（範本不支援）；非記憶式（範本不支援）；價格表有 KO 欄（範本不支援）；k ≥ 2（範本不支援）；每期 KO 表 `i` 不連續或列數 ≠ 配息表（範本不支援）；同一期結束日和配息表不同（`doc.ko_table_dates` 不一致）；兩種新記憶式措辭（說明書與投資人須知都讀成 Y）、只有一句（轉人工）；D 型比價日表上只填開始與最後（中間格回填、已有值的格子比對）；Product 是 `FCN_Stepdown` 的投資人須知用 `ca-zh-iis` 通過；CA FCN D 型比價日仍只填兩格（回歸）。

## 8. 實作方式（workflow #546）

| 內容 | 位置 |
|---|---|
| Stepdown 共用讀法 | 沿用 `parsers/stepdown.py`（#544、#545）：`ko_terms`、`apply_ko_terms`（期數檢查、KO 價、每期比價日） |
| 範本辨識與讀出 | `parsers/ca_stepdown.py`（新）：沿用 `ca.detect` 的條件 1～6 與 `ca.read`（價格表、情境價格表的欄宣告拿掉 KO 欄；記憶式收新措辭），再讀第 9 項 `(1)(b)` 每期 KO 表，換掉 KO%、價格與比價日 |
| 投資人須知 | 沿用 `parsers/ca_iis.py`：記憶式收新措辭；`IisTemplate` 加可選的 `products`，`ca-zh-iis` 宣告 `FCN`、`FCN_Express`、`FCN_Stepdown`；`identification._product_family` 對投資人須知改看投資人須知範本的宣告（沒有宣告時照舊用說明書範本） |
| 上手註冊 | `issuers.py` 登記 `CA_STEPDOWN`（`products=frozenset({FCN_STEPDOWN})`，上手代號同 `CA`，順序 Stepdown → BEN → Super Bull → FCN）。**不掛投資人須知範本**（`iis=None`）：投資人須知辨識由專到泛取第一個命中，掛同一個 `ca-zh-iis` 會讓每份 CA FCN 投資人須知都先掛在 Stepdown 範本底下；不掛時 Stepdown 的投資人須知照舊由 CA FCN 範本認走，CA FCN 投資人須知的路徑完全不變（顧問審查 #566）。`Template.iis` 的註解與註冊表測試（每個家族範本命中自己的投資人須知範本）照此改寫 |
| 規則 | `rules/ca_stepdown.py`：沿用 `rules/ca.py`，加 `doc.ko_table_dates` |
| 設定 | `[auto_check_products]` 的 `FCN_Stepdown` 加 `CA`；`[issuer.ca.product_names.FCN_Stepdown]`（寫法同 FCN；家族範本都要登記，`test_issuer_registry`） |
| 測試 | 合成 PDF（沿用 CA 合成樣本，改第 9 項 `(1)(b)`、價格表與情境價格表）；§7 的反例；商品種類一致四種情況：Product 是 FCN 或 FCN_Express 配 `ca-zh-iis`（結果不變）、FCN_Stepdown 配 `ca-zh-iis`（通過）、BEN 或 Super Bull 配 `ca-zh-iis`（仍轉人工）、沒宣告的上手（例：BARC FCN 投資人須知配 FCN_Stepdown，仍轉人工）；FCN_Express 與 KO 每期觀察的 CA FCN 說明書不被 Stepdown 範本認走；本機真實樣本辨識與讀取測試（環境變數指向本機樣本，不進 repo）；CA FCN 本機樣本 main 與分支逐欄比對（回歸）；試跑（§6） |

## 9. 決策紀錄

| 日期 | 決定 |
|---|---|
| 2026-11-13 | 作業人員獨立標記略過（同 BARC、Natixis Stepdown），核對範圍以開發者凍結的標記為準 |
| 2026-11-13 | 依第 9 項 `(1)(b)` 有 `期初訂價之N.NN%` 辨識 Stepdown 說明書，不看名稱（和 FCN 相同）；只開放現行版面，舊範本（P 型表頭、沒有觀察期定義句）認得出但範本不支援；版本只看寫法、不看交易日（#409） |
| 2026-11-13 | 投資人須知不另立範本：`ca-zh-iis` 同時涵蓋 FCN、FCN_Express、FCN_Stepdown（`IisTemplate.products`），商品種類由說明書把關；`CA_STEPDOWN` 不掛投資人須知範本（`iis=None`，顧問審查 #566） |
| 2026-11-13 | 記憶式另收 `相關自動提前出場期所適用之自動提前出場價`、`無須於同一預定交易日或同一自動提前出場觀察期符合上開條件` 兩種措辭（說明書只在 Stepdown 範本收；投資人須知共用一個範本，所有 CA 投資人須知都收） |
| 2026-11-13 | KO 期別照 ADR 0033；每期 KO 表用 `i` 對齊配息表，`i` 對不上是範本不支援，同一期結束日不同是判不一致 |
| 2026-11-13 | 非記憶式、k ≥ 2、P 型現行版面沒有樣本 → 範本不支援；之後有樣本再開 Issue |

## 10. 規則對照

新 rule_id 的名稱實作時可調整，調整後同步更新本表。

| 核對規則 | rule_id |
|---|---|
| §3.1 範本版本、商品種類一致 | `template.detect`（`template_unsupported`）、`template.product_family` |
| §3.2 參考條件表欄位 | 共用 `field.*`、`backfill.*` |
| §3.3 KO 降幅與每期 KO% | `field.ko_step_pct`、`field.ko_levels`（共用，#544）；`field.ko_pct` |
| §3.4 比價日 | 共用 `backfill.compare_dates` |
| §3.5 內部交叉驗證 | 照 CA FCN（`doc.*`、`derive.prices`、`scenario.*`）；`doc.ko_table_dates`（同 Natixis） |
| §3.6 投資人須知 | 共用 `iis.*`、`iis.has_ki`（照 CA FCN） |
