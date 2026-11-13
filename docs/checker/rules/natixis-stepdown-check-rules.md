# 核對規則：Natixis FCN_Stepdown 中文產品說明書與投資人須知

- 文件側：[Natixis FCN_Stepdown 範本規格](../templates/natixis-zh-stepdown.md)（`natixis-zh-stepdown-pd`）、[Natixis FCN_Stepdown 投資人須知範本規格](../templates/natixis-zh-stepdown-iis.md)（`natixis-zh-stepdown-iis`）
- 下單側：參考條件表，見[參考條件表格式](../order-formats/reference-sheet.md)與 [Natixis FCN_Stepdown 下單資料格式](../order-formats/natixis-stepdown-reference.md)
- 商品：FCN_Stepdown（workflow ADR 0033、#537）；Barclays 先開放（workflow #544），本文件是第二家（workflow #545）。KO 降幅與每期 KO% 的核對、D 型每期比價日沿用 Barclays 實作時放進共用層的讀法與規則（`parsers/stepdown.py`、`field.ko_step_pct`、`field.ko_levels`，[共用核對規則](shared-check-rules.md)）
- 狀態：規格（workflow #545）；實作另開 PR。核對範圍以開發者凍結的螢光標記為準；**作業人員獨立標記略過**（同 DACN／BEN 第一波，§7）
- 本文件只寫和 [Natixis 核對規則](natixis-check-rules.md)（FCN）不同或 Stepdown 特有的部分。沒提到的（審查標準、固定警語、名稱、受託機構、發行機構名稱與信用評等、幣別、費率、最低申購與贖回、受理申購日、刊印日期、審查通過日期、配息率、價格推算、表頭 %、KI、日期表、Non-Call、KO 每日到最終評價日含不含、情境分析、受託機構商品代號）照 Natixis FCN

## 1. 範圍

- 依第 14 項的 `自動提前到期贖回價格為下表所列之期初進場價格` 辨識商品家族，不靠名稱（Stepdown 名稱和 FCN 相同）；同一家上手由專到泛，依序試 Stepdown、DACN、FCN 範本（`issuers.py`，#436；範本規格 §4）。辨識結果和參考條件表 Product 不一致時轉人工覆核。
- 認得出 Stepdown、但寫法不在範本規格內（KO% 不是每期固定往下降、非記憶式、第 14 項仍有固定 % 定義句、每期 KO 表和第 12 項對不上期別等）→ 不支援的範本版本（`template_unsupported`），轉人工覆核、不能人工放行。
- 說明書先把 CJK 相容字轉成標準字再辨識與讀取（早期版面，範本規格 §3）；只在 Stepdown 範本做，Natixis FCN 範本不變。
- 兩份都通過或人工放行才打 `TS`／`IIS` 的 `V` 並回填（ADR 0007）。

## 2. 不核對的項目

- 照 Natixis FCN §2。
- 第 14 項固定 % 定義句、第 9 項 KO 價欄與表頭 KO %：Stepdown 沒有（`doc.table_header_pct` 沒有 KO 欄就不產生 KO 那一項，同 FCN 無 KI 時的做法）。
- 第 2 期以後各標的的 KO 價：說明書沒有寫，系統也不存（ADR 0033 比價時才算），只核對每期 KO%。
- 投資人須知：沒有 KO%、降幅或價格，不核對這幾項；KO 價格定義只核寫法（§3.6）。

## 3. 核對規則

### 3.1 樣態與分流

| 檢查 | 規則 |
|---|---|
| 開放 | `[auto_check_products]` 的 `FCN_Stepdown` 加 `NATIXIS`（實作 PR）；沒開放的上手照未開放商品轉人工 |
| 範本版本 | 寫法和範本規格 §4 不同 → 不支援的範本版本（`template.detect`，原因 `template_unsupported`），錯訊列出不成立的條件 |
| 商品種類一致 | 辨識到的範本商品家族 ≠ Product → 轉人工覆核（共用 `template.product_family`）；Product 空白視為 FCN |
| 不支援樣態 | 照 Natixis FCN（沒有黑名單） |

### 3.2 參考條件表欄位（共用 `rules/reference.py`、`backfill.py`）

| 標準欄位 | 規則 | Stepdown 差異 |
|---|---|---|
| `ko_pct` | 共用 `field.ko_pct` | 說明書值 = 第 14 項每期 KO 表第 1 期百分比 |
| `ko_step_pct` | 共用 `field.ko_step_pct`（#544） | — |
| `ko_levels` | 共用 `field.ko_levels`（#544） | — |
| `ko_memory` | 共用 `field.ko_memory` | 同 FCN（第 14 項 `皆曾經`） |
| 各標的 KO 價 | 共用 `field.underlying_prices` | 說明書值 = 期初進場價格 × 第 1 期 KO%，四捨五入（half-up）到 4 位（adapter 推算，範本規格 §2.3） |
| 比價日 | 共用 `backfill.compare_dates` | D 型每一期期末日都填（§3.4）；P 型照 FCN |
| 其他 | 共用 `field.*`、`backfill.*` | 同 Natixis FCN |

### 3.3 KO 降幅與每期 KO%

照 [BARC FCN_Stepdown 核對規則](barc-stepdown-check-rules.md) §3.3（共用規則，所有上手的 Stepdown 共用）：說明書降幅 = 表上 `KO降幅(%)`；第 n 期 KO% = 表上 `KO(%)` − `KO降幅(%)` ×（n − 1），逐期比；表上沒有降幅欄、空白、0、負數、非數字 → 轉人工覆核。

共用讀法多一個檢查（顧問審查 #544 的建議）：讀出的期數須 = 期數 − Non-Call + 1（D 型；P 型 = 第 12(7) 項觀察日表列數），不符 → `ko_levels` 讀不出（轉人工覆核）。Natixis 在範本辨識時已用 `t` 對齊第 12 項（範本規格 §2.1），這個檢查是共用層的保險，BARC 也一併套用。

### 3.4 比價日（D 型）

D 型 Stepdown：`比價日_{Non-Call}`～`比價日_{天期}` = 第 12 項 (6) 配息觀察期間表第 k 期到末期每一期的 `配息觀察期間結束日`（ADR 0033「有 KO 降幅的每日型要填每一期的結束日」，共用 `stepdown.every_period`，同 BARC）；最晚的比價日 = 最終評價日。表上空白的格子通過後回填，有值的格子比對。P 型照 FCN。

### 3.5 說明書內部交叉驗證

照 Natixis FCN §3.5。不同的地方：

| 檢查 | Stepdown |
|---|---|
| 每期 KO 表日期（新） | 第 14 項每期 KO 表每一列的日期 = 第 12 項同一期：D 型 `配息觀察期間開始日`、`配息觀察期間結束日` = 第 12 項 (6) 同一個 `配息觀察期間(t)`；P 型 `自動提前到期觀察日(t)` = 第 12 項 (7) 同一個 `t`（`doc.ko_table_dates`，不同 → 判不一致，列出哪幾期）。`t` 本身對不上是範本不支援（§3.1），不走到這裡 |
| 表頭 % | 第 9 項只有界限價格、執行價格欄頭（`doc.table_header_pct`，沒有 KO 那一項） |
| 記憶式、KO 型態、Non-Call、情境 | 同 FCN；情境一句前沒有 `•`（早期版面）也收（`parsers/natixis.py` 的情境一列項符號改成可有可無；實作時確認 Natixis FCN、DACN 本機樣本結果不變） |
| 價格推算 | 界限、執行價 = 期初進場價格 × %（共用 `derive.prices`）；KO 價是 adapter 推算的，同 BARC Stepdown |

### 3.6 投資人須知

照[投資人須知核對規則](iis-check-rules.md) §3 的 Natixis 欄；不同的地方：

| 檢查點 | Stepdown |
|---|---|
| 名稱 | = 說明書封面名稱（同 FCN 名稱樣板） |
| KO 價格定義（`iis.ko_definition`） | 只接受參照說明書第 14 項的一句（`為中文產品說明書第一章第14項所列之期初進場價格`）；寫固定 %（`期初進場價格之Y%`）或其他寫法 → 判不一致（Stepdown 每期 KO% 不同）；同時有 `為下表所列之期初進場價格` 與參照說明書兩句（投資人須知沒有表）→ 轉人工覆核，可以人工放行（**待使用者決定**，§9） |
| 早期版面 | 第 1 行標題與刊印日期分兩行 → 三個 Natixis 投資人須知範本都不符，轉人工覆核（同 Natixis FCN，範本規格 §3） |

## 4. 判定原則

同 Natixis FCN §4：必核欄位抓不到、抓到多個不同值、或寫法不在範本規格內，一律轉人工覆核。每期 KO% 只由第 14 項每期 KO 表的數字推得，不由名稱或關鍵字推測。

## 5. 探勘比對結果（2026-11-13）

探勘腳本（本機 `data/tmp/stepdown/`）用現有 Natixis parser 讀 7 份說明書與投資人須知：

| 範圍 | 結果 |
|---|---|
| 現行 Natixis FCN 範本 | 說明書 6 份被辨識成 FCN（名稱、骨架相同），NS02 因章名有 CJK 相容字對不上；讀不到 KO%（第 14 項沒有固定 %）與價格表（沒有 KO 欄）→ 要有自己的範本。投資人須知現行版面 5 份被辨識成 FCN，早期版面 2 份不符 |
| 相容字轉換＋價格表拿掉 KO 欄 | 7 份說明書除了 KO%（改讀每期 KO 表）以外全部讀得到；早期版面 2 份另讀不到情境一期別（句前沒有 `•`） |
| 每期 KO 表 | D 型 5 份、P 型 2 份；`t` 從 1 起連續、列數 = 天期（Non-Call 都是 1）；每列日期和第 12 項同一期相同；每期固定降整數百分點（共三種降幅），起始 KO% 有 100 與小於 100 |
| 範本標記 | 第 14 項 `為下表所列之期初進場價格` 只出現在 7 份 Stepdown；本機 60 份 Natixis FCN／DACN 說明書（另有 2 份 BEN）都沒有。投資人須知 `於相關配息觀察期間(t)之自動提前到期觀察日` 只出現在 7 份 Stepdown；本機 Natixis FCN 投資人須知都沒有 |

## 6. 試跑預期（實作 PR 的驗收）

以私人雲端倉庫的 7 份樣本、參考條件表（資料庫來源或照樣本整理，`Product` = `FCN_Stepdown`、`KO降幅(%)` 有值）在本機跑完整核對：

- 說明書 7 份全部辨識成 `natixis-zh-stepdown-pd`；投資人須知現行版面 5 份辨識成 `natixis-zh-stepdown-iis`，早期版面 2 份範本不符（轉人工）。沒有一份被 Natixis FCN、DACN 範本認走。
- KO%、降幅、每期 KO%、第 1 期 KO 價和參考條件表一致；改錯降幅或某一期 KO% 時判不一致。
- D 型比價日每一期都回填；P 型同 FCN。
- NS04、NS05 投資人須知的 KO 價格定義轉人工覆核（§3.6）。
- Natixis FCN、DACN 與其他上手既有測試、本機真實樣本結果不變。

## 7. 核對範圍（開發者螢光標記，2026-11-13）

**作業人員獨立標記略過**（同 DACN／BEN 第一波）；範圍以開發者凍結的標記為準。代表樣本 2 份：NS07（D 型、現行版面、有 KI、起始 KO 不是 100）、NS01（P 型、早期版面、有相容字）。標記檔、清單與凍結雜湊在本機 `data/tmp/stepdown/marks/`。說明書 N01～N13、投資人須知 J01～J06（兩份編號相同）。

| 標記 | 條號／欄位 | 決定 |
|---|---|---|
| N01 | 封面代號、ISIN、名稱、幣別、審查日、受託機構、刊印日期 | 照 Natixis FCN |
| N02 | 封面警語 | 照 Natixis FCN |
| N03 | 第一章第 1 項名稱 | 和 FCN 相同（不能用名稱辨識）；名稱樣板照 FCN |
| N04 | 第 9 項連結標的表（沒有 KO 欄） | 標的、期初進場、界限、執行價照 FCN；KO 價 = 期初進場價格 × 第 1 期 KO%（§3.2） |
| N05 | 第 12 項天期與日期 | 照 FCN |
| N06 | 第 12 項 (7) KO 觀察方式與起始期 | 照 FCN（D 從第 k 個配息觀察期間結束日每日、P 依表）；比價日 D 每一期都填（§3.4） |
| N07 | 第 13 項月配息率、年利率 | 照 FCN |
| N08 | 第 14 項 `為下表所列之期初進場價格` | Stepdown 範本辨識標記（FCN 是 `為期初進場價格之Y%`） |
| N09 | 第 14 項每期 KO 表 | 第 1 期 = `KO(%)`；降幅 = `KO降幅(%)`；每期 = 起始 − 降幅 ×（n − 1）（§3.3）；不是每期固定往下降 → 範本不支援；每列日期 = 第 12 項同一期（§3.5） |
| N10 | 第 14 項記憶式寫法 | 照 FCN `ko_memory`；非記憶式沒有樣本 → 範本不支援 |
| N11 | 第 15 項執行價格、界限價格與 KI 型態 | 照 FCN |
| N12 | 第 16 項情境（有利情況第 1 期出場）、情境價格表（沒有 KO 欄） | 照 FCN；情境一句前沒有 `•` 也收（§3.5） |
| N13 | 第二～四章（發行機構與受託機構負責人、費用、最低申購與贖回、受理日） | 照 FCN |
| J01 | 封面名稱、代號、ISIN、風險等級 | 照 Natixis FCN 投資人須知（名稱 = 說明書） |
| J02 | 警語、相關機構 | 照 FCN |
| J03 | 商品簡介面額、日期、標的、年期 | 照 FCN |
| J04 | 收益分配事項月配息率、年利率 | 照 FCN |
| J05 | KO 價格定義（參照說明書第 14 項，或 `下表所列`、投資人須知沒有表） | Stepdown 投資人須知辨識（`於相關配息觀察期間(t)之`）；KO% 不核（同 #272）；寫法規則見 §3.6 |
| J06 | 執行價格、界限價格 | 照 FCN |

每條新規則實作時要有合成反例：表上 `KO降幅(%)` 和說明書不同；表上降幅空白；KO% 每期相同、往上升或不等距（範本不支援）；表上 KO 價 ≠ 期初進場價格 × 第 1 期 KO%；Product 是 FCN 但文件是 Stepdown（及反過來）；Natixis FCN、DACN 說明書與投資人須知不被 Stepdown 範本認走；Stepdown 說明書仍有固定 % 定義句（範本不支援）；非記憶式（範本不支援）；第 9 項有 KO 欄（範本不支援）；每期 KO 表 `t` 和第 12 項對不上（範本不支援）；同一期日期和第 12 項不同（`doc.ko_table_dates` 不一致）；Non-Call 2、表上 `t` 從第 2 期起排（接受）；D 型比價日表上只填開始與最後（中間格回填、已有值的格子比對）；投資人須知 KO 定義寫固定 %（判不一致）、兩句（轉人工）；有相容字的說明書照常辨識與讀取；Natixis FCN D 型比價日仍只填兩格（回歸）。

## 8. 實作方式（workflow #545）

| 內容 | 位置 |
|---|---|
| Stepdown 共用讀法 | 沿用 `parsers/stepdown.py`（#544）：`ko_terms` 交入每期百分比，`with_ko_prices`、`every_period` 照用；多一個期數檢查（§3.3） |
| 範本辨識與讀出 | `parsers/natixis_stepdown.py`（新）：相容字轉換後沿用 `natixis.detect`、`natixis.read`（`Variant` 的價格表宣告拿掉 KO 欄），再讀第 14 項每期 KO 表，換掉 KO%、價格與比價日；`parsers/natixis_stepdown_iis.py`（新）：沿用 `natixis_iis`，加商品家族標記 |
| 上手註冊 | `issuers.py` 登記 `NATIXIS_STEPDOWN`（`products=frozenset({FCN_STEPDOWN})`，上手代號同 `NATIXIS`，順序 Stepdown → DACN → FCN） |
| 規則 | `rules/natixis_stepdown.py`：沿用 `rules/natixis.py`，加 `doc.ko_table_dates`；投資人須知 `iis.ko_definition` 的 Stepdown 版（§3.6） |
| 設定 | `[auto_check_products]` 的 `FCN_Stepdown` 加 `NATIXIS`；`[issuer.natixis.product_names.FCN_Stepdown]`（寫法同 FCN；家族範本都要登記，`test_issuer_registry`） |
| 測試 | 合成 PDF（沿用 Natixis 合成樣本，改第 9、14 項）；§7 的反例；本機真實樣本辨識與讀取測試（環境變數指向本機樣本，不進 repo）；試跑（§6） |

## 9. 決策紀錄

| 日期 | 決定 |
|---|---|
| 2026-11-13 | 作業人員獨立標記略過（同 DACN／BEN 第一波），核對範圍以開發者凍結的標記為準 |
| 2026-11-13 | 依第 14 項 `為下表所列之期初進場價格` 辨識 Stepdown 說明書、`於相關配息觀察期間(t)之自動提前到期觀察日` 辨識投資人須知，不看名稱（和 FCN 相同） |
| 2026-11-13 | KO 期別照 ADR 0033；每期 KO 表用 `t` 對齊第 12 項，`t` 對不上是範本不支援，同一期日期不同是判不一致 |
| 2026-11-13 | 相容字轉換只在 Stepdown 範本做；Natixis FCN 早期版面的相容字問題不在本 Issue（要支援時另開） |
| 2026-11-13 | 早期版面投資人須知（標題與刊印日期分兩行）不支援，轉人工覆核（同 Natixis FCN）；要不要另開 Issue 支援 FCN 與 Stepdown 的早期版面，**待使用者決定** |
| 2026-11-13 | 投資人須知 KO 定義同時有 `下表所列`（沒有表）與參照說明書兩句 → 暫定轉人工覆核、可以人工放行；改判不一致（請上手更正）或直接通過，**待使用者決定** |
| 2026-11-13 | 共用讀法加期數檢查（期數 − Non-Call + 1），BARC 一併套用（顧問審查 #544 建議） |

## 10. 規則對照

新 rule_id 的名稱實作時可調整，調整後同步更新本表。

| 核對規則 | rule_id |
|---|---|
| §3.1 範本版本、商品種類一致 | `template.detect`（`template_unsupported`）、`template.product_family` |
| §3.2 參考條件表欄位 | 共用 `field.*`、`backfill.*` |
| §3.3 KO 降幅與每期 KO% | `field.ko_step_pct`、`field.ko_levels`（共用，#544）；`field.ko_pct` |
| §3.4 比價日 | 共用 `backfill.compare_dates` |
| §3.5 內部交叉驗證 | 照 Natixis FCN（`doc.*`、`derive.prices`）；`doc.ko_table_dates`（新） |
| §3.6 投資人須知 | 共用 `iis.*`；`iis.ko_definition`（Stepdown 版） |
