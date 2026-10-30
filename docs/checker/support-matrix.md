# 支援範圍與文件索引

核實日期：2026-10-30。依據：上手註冊表 `src/fcn_checker/issuers.py`、支援樣態設定 `config/issuer_prefixes.toml`，以及各上手 parser、規則與測試。

## 文件如何使用

- 支援範圍以本頁及設定為準；共用判定見[共用核對規則](rules/shared-check-rules.md)、[審查標準](rules/review-standard.md)及[投資人須知核對規則](rules/iis-check-rules.md)。
- 範本文件的樣本總表、探勘結果與日期決策是歷史證據，不等於目前全部交易都通過。現行資料契約見[資料契約](data-contract.md)，公司上線須另依[驗收流程](acceptance.md)。
- ADR 記錄當時的架構決策；已取代的 ADR、BARC 詢價表與舊版參考條件表只供回溯。現行核對只讀[共用參考條件表](order-formats/reference-sheet.md)。

## 已實作的上手

14 家均已登記說明書（TS）與投資人須知（IIS）範本；MS、UBS 只支援文件規格列出的新版。範本有實作不代表該上手所有樣態都已取得樣本或通過驗收。

| 上手 | 編號 | 說明書範本 | 投資人須知範本 | 參考條件表對照 | 專屬規則 |
|---|---|---|---|---|---|
| BARC | `029` | [barc-zh-pd](templates/barc-zh-product-description.md) | [barc-zh-iis](templates/barc-zh-iis.md) | [共用格式](order-formats/reference-sheet.md) | [核對規則](rules/barc-check-rules.md) |
| HSBC | `325` | [hsbc-zh-pd](templates/hsbc-zh-product-description.md) | [hsbc-zh-iis](templates/hsbc-zh-iis.md) | [欄位來源](order-formats/hsbc-fcn-reference.md) | [核對規則](rules/hsbc-check-rules.md) |
| MS | `147` | [ms-zh-pd](templates/ms-zh-product-description.md) | [ms-zh-iis](templates/ms-zh-iis.md) | [欄位來源](order-formats/ms-fcn-reference.md) | [核對規則](rules/ms-check-rules.md) |
| BNP | `082` | [bnp-zh-pd](templates/bnp-zh-product-description.md) | [bnp-zh-iis](templates/bnp-zh-iis.md) | [欄位來源](order-formats/bnp-fcn-reference.md) | [核對規則](rules/bnp-check-rules.md) |
| SG | `037` | [sg-zh-pd](templates/sg-zh-product-description.md) | [sg-zh-iis](templates/sg-zh-iis.md) | [欄位來源](order-formats/sg-fcn-reference.md) | [核對規則](rules/sg-check-rules.md) |
| CITI | `159` | [citi-zh-pd](templates/citi-zh-product-description.md) | [citi-zh-iis](templates/citi-zh-iis.md) | [欄位來源](order-formats/citi-fcn-reference.md) | [核對規則](rules/citi-check-rules.md) |
| JPM | `076` | [jpm-zh-pd](templates/jpm-zh-product-description.md) | [jpm-zh-iis](templates/jpm-zh-iis.md) | [欄位來源](order-formats/jpm-fcn-reference.md) | [核對規則](rules/jpm-check-rules.md) |
| NOMURA | `156` | [nomura-zh-pd](templates/nomura-zh-product-description.md) | [nomura-zh-iis](templates/nomura-zh-iis.md) | [欄位來源](order-formats/nomura-fcn-reference.md) | [核對規則](rules/nomura-check-rules.md) |
| GS | `148` | [gs-zh-pd](templates/gs-zh-product-description.md) | [gs-zh-iis](templates/gs-zh-iis.md) | [欄位來源](order-formats/gs-fcn-reference.md) | [核對規則](rules/gs-check-rules.md) |
| NATIXIS | `328` | [natixis-zh-pd](templates/natixis-zh-product-description.md) | [natixis-zh-iis](templates/natixis-zh-iis.md) | [欄位來源](order-formats/natixis-fcn-reference.md) | [核對規則](rules/natixis-check-rules.md) |
| CA | `086` | [ca-zh-pd](templates/ca-zh-product-description.md) | [ca-zh-iis](templates/ca-zh-iis.md) | [欄位來源](order-formats/ca-fcn-reference.md) | [核對規則](rules/ca-check-rules.md) |
| BBVA | `326` | [bbva-zh-pd](templates/bbva-zh-product-description.md) | [bbva-zh-iis](templates/bbva-zh-iis.md) | [欄位來源](order-formats/bbva-fcn-reference.md) | [核對規則](rules/bbva-check-rules.md) |
| DBS | `078` | [dbs-zh-pd](templates/dbs-zh-product-description.md) | [dbs-zh-iis](templates/dbs-zh-iis.md) | [欄位來源](order-formats/dbs-fcn-reference.md) | [核對規則](rules/dbs-check-rules.md) |
| UBS | `092` | [ubs-zh-pd](templates/ubs-zh-product-description.md) | [ubs-zh-iis](templates/ubs-zh-iis.md) | [欄位來源](order-formats/ubs-fcn-reference.md) | [核對規則](rules/ubs-check-rules.md) |

## 開放自動核對的商品種類

下表是 `config/issuer_prefixes.toml` 的 `[auto_check_products]`（白名單，#302、ADR 0027）。參考條件表 Product 欄（不分大小寫）沒列、或沒開放給該上手的商品，照不支援樣態處理：兩份都停止條件核對、不回填、不能人工放行，請人工核對。Product 空白或表上沒有這欄時照 FCN。

| 商品種類 | 開放的上手 |
|---|---|
| FCN | 全部 |
| FCN_Express | BARC（#304）、CA（#305）、HSBC（#306） |
| Super Bull | BNP（#311）、CA（#312）、HSBC（#314） |
| BEN | HSBC（#375）；MS、CA 待 #376、#377 |
| DACN | 尚未開放（依序 Nomura → SG → DBS → Natixis，#368～#371） |

Super Bull 只支援 KO 每期觀察＋非記憶式＋無 KI（`Period End`＋`NONE`），其他樣態照不支援樣態轉人工，所有上手共用。BNP Super Bull 的文件：[說明書範本](templates/bnp-zh-super-bull.md)、[投資人須知範本](templates/bnp-zh-super-bull-iis.md)、[參考條件表對照](order-formats/bnp-super-bull-reference.md)、[核對規則](rules/bnp-super-bull-check-rules.md)（只支援 2026-06 以後的範本版本）。CA Super Bull 的文件：[說明書範本](templates/ca-zh-super-bull.md)、[投資人須知範本](templates/ca-zh-super-bull-iis.md)、[參考條件表對照](order-formats/ca-super-bull-reference.md)、[核對規則](rules/ca-super-bull-check-rules.md)（只支援現行版範本；境外人民幣轉人工）。HSBC Super Bull 的文件：[說明書範本](templates/hsbc-zh-super-bull.md)、[投資人須知範本](templates/hsbc-zh-super-bull-iis.md)、[參考條件表對照](order-formats/hsbc-super-bull-reference.md)、[核對規則](rules/hsbc-super-bull-check-rules.md)（只支援現行版範本；單一標的、非美元、KO% ≠ K%、多個決定日轉人工）。

BEN 沒有 KO、沒有定期配息、無 KI，只在到期判定（[ADR 0029](../adr/0029-ben.md)）；參考條件表的參與率(%) 只有 BEN 核對，沒有這欄或空白轉人工。HSBC BEN 的文件：[說明書範本](templates/hsbc-zh-ben.md)、[投資人須知範本](templates/hsbc-zh-ben-iis.md)、[參考條件表對照](order-formats/hsbc-ben-reference.md)、[核對規則](rules/hsbc-ben-check-rules.md)（只支援現行版範本的 Maxi 與 Digital；Maxi 1 檔或 4 檔以上標的、Digital 2 檔以上標的、非美元轉人工；參與率由到期公式推得：Maxi 100、Digital 0）。

Nomura DACN 的文件（workflow #368 規格，實作另開；開放前照未開放商品轉人工）：[說明書範本](templates/nomura-zh-dacn.md)、[投資人須知範本](templates/nomura-zh-dacn-iis.md)、[參考條件表對照](order-formats/nomura-dacn-reference.md)、[核對規則](rules/nomura-dacn-check-rules.md)（只支援現行版範本；沒有樣本的型態轉人工）。

## 目前設定明確不支援的樣態

下表是 `config/issuer_prefixes.toml` 的 `[unsupported_variants]`，不是 parser 能力或樣本涵蓋率的完整清單。參考條件表、說明書或同商品投資人須知任一邊明確是黑名單樣態，兩份都停止條件核對、不回填、不能人工放行。未列入黑名單的組合仍須通過範本、必要欄位與規則檢查。

| 上手 | 不支援樣態 |
|---|---|
| BARC | KI 每期觀察 |
| HSBC | KI 每期觀察 |
| MS | KI 每期觀察 |
| BNP | 無設定黑名單（仍受範本與規則限制） |
| SG | 無設定黑名單（仍受範本與規則限制） |
| CITI | 無設定黑名單（仍受範本與規則限制） |
| JPM | KI 每期觀察 |
| NOMURA | 無設定黑名單（仍受範本與規則限制） |
| GS | KO 每期觀察＋記憶式、KO 每期觀察＋非記憶式、KI 每期觀察、Non-Call = 天期 |
| NATIXIS | 無設定黑名單（仍受範本與規則限制） |
| CA | KI 每期觀察 |
| BBVA | KO 每期觀察＋非記憶式、KI 每日觀察、KI 每期觀察、Non-Call = 天期 |
| DBS | 期初定價 VWAP、KO 每日觀察＋非記憶式、KO 每期觀察＋記憶式、KO 每期觀察＋非記憶式、KI 每日觀察、KI 每期觀察、Non-Call = 天期 |
| UBS | 期初定價 VWAP、Non-Call = 天期 |

## 共用限制與維護

- 只處理可讀文字層 PDF，使用 PyMuPDF；沒有 OCR 或 LLM fallback。掃描、未知範本或讀不出的必核欄位不自動通過；混合型文件的圖像內容不會被 OCR 補讀。
- 標的中文名稱與外部價格正確性、各上手部分情境重算未涵蓋；逐項範圍見各上手規則的「不核對項目」及程式 `not_covered`，PANEL「待處理」與核對紀錄會列出。
- 「受託投資」禁用語的共用交易日特例截止日為 **2026-10-30（含）**，10/31 起依原規則；詳見[審查標準 §2.12](rules/review-standard.md#212-禁用語特例改版前的舊範本2026-10-15-確認issue-247)。
- 日期表與情境參數由上手讀出標準資料，統一交給 `rules/schedule.py`、`rules/scenario.py` 核對；仍有上手專屬規則。共用規則不等於核對整份文件所有條款。
- 新增上手或改版依[上手導入流程](issuer-onboarding.md)；待辦與人工確認見[TODO](TODO.md)。
