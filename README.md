# Structured Product Workflow／結構型商品作業流程

## 繁體中文

狀態：「TS／IIS 準備與放回」已完成，以真實樣本複本試跑過，等作業主機公槽實測（[#5](https://github.com/TaylorYam/structured-product-workflow/issues/5)）。「每日匯入」最小 FCN 資料庫見 [#32](https://github.com/TaylorYam/structured-product-workflow/issues/32)；「寄信草稿」見 [#29](https://github.com/TaylorYam/structured-product-workflow/issues/29)；「起單資料」讀起單建檔見 [#97](https://github.com/TaylorYam/structured-product-workflow/issues/97)；匯入後算出核對條件見 [#88](https://github.com/TaylorYam/structured-product-workflow/issues/88)；核對從資料庫讀條件並寫回見 [#68](https://github.com/TaylorYam/structured-product-workflow/issues/68)。其他流程仍在設計階段。

本專案管理正式 Order、建檔、上手文件歸檔、核對串接及 FC 寄送。詢價端沿用既有系統。
核對規則原本在獨立的 [fcn-term-sheet-checker](https://github.com/TaylorYam/fcn-term-sheet-checker)，已併入本專案（[ADR 0005](docs/adr/0005-merge-checker-into-workflow.md)）：程式與歷史在 `src/fcn_checker/`，同一次安裝就有 `fcn-batch`、`fcn-panel`，核對文件在 [docs/checker/](docs/checker/README.md)；新商品資料改由 Order、交易明細與股價建立（[ADR 0006](docs/adr/0006-new-product-data-from-order.md)）。

閱讀順序：[業務背景](CONTEXT.md) → [架構](docs/architecture.md) → [待定事項](docs/open-questions.md)。
資料保存與部署先讀 [PostgreSQL 決策](docs/adr/0001-postgresql-workflow-storage.md) 與 [開發部署設計](docs/database-and-deployment.md)。第一階段的資料範圍見 [ADR 0003](docs/adr/0003-minimal-fcn-database.md)。
串接工作另讀 [核對工具串接提案](docs/checker-integration.md)。

從 TaylorYam/ai-project-template 建立，初始化日期 2026-10-18。保留範本的 Issue、PR、CI 與 Agent 工作流程。
只提交文件與虛構範例；公司信件、交易、客戶、庫存、公槽檔案及憑證保留在公司環境。

### TS／IIS 批次準備

從公槽 FCN 底下以 TDCC 命名的商品資料夾，挑出中文產品說明書與中文投資人須知，改名成核對工具要的 `<TDCC>_TS.pdf`、`<TDCC>_IIS.pdf`，放進新開的批次資料夾。商品資料夾裡的檔案只讀不改。

**作業人員安裝（Windows）**：先安裝官方 Python 3.11 以上（包含 Tcl/Tk），把專案放到自己有寫入權限的資料夾，雙擊 `setup_panel.cmd`。安裝會建立專案自己的 `.venv`，依 `constraints.txt` 安裝套件，需要能連到公司允許的 Python 套件來源。看到「安裝完成」後，雙擊桌面或專案資料夾裡的「FCN Workflow」捷徑開啟作業視窗；捷徑建立失敗時改雙擊 `launch_panel.cmd`（`launch_checker_panel.cmd` 開同一個視窗並停在「4 核對」）。日常操作不用打指令；上次選的資料夾與視窗大小記在安裝資料夾的 `.local/`，核對紀錄寫在安裝資料夾的 `runtime/核對紀錄/`。搬移資料夾或更新程式後請重新執行安裝。Word 轉檔需要這台電腦裝有 Microsoft Word。

**作業視窗**：一個「FCN Workflow」視窗，左側步驟欄照每天的順序排：1 每日匯入 → 2 起單資料 → 3 準備 → 4 核對 → 5 放回 → 6 寄信草稿，按一下就切到那一步，目前所在的那一步用淺藍底標出；切換時各頁內容都保留。畫面是淺灰底、白色卡片的簡潔風格：每頁上方寫這一頁的功能，貼 TDCC 的欄位是直的、底下顯示「已貼 N 筆」；做完先列重點數字（例如已準備／跳過、通過／不一致），有問題的數字用紅色，狀態列變成綠色結果條（[#83](https://github.com/TaylorYam/structured-product-workflow/issues/83)、[#85](https://github.com/TaylorYam/structured-product-workflow/issues/85)）。商品資料夾所在的根目錄在視窗最上方設一次（按「變更…」），3 準備、6 寄信草稿兩頁共用；各頁的輸出位置仍各自記住。做完一步，下一頁會先帶入結果，上方藍色提示條寫出帶入了什麼（按「改選…」可以換掉）；完成訊息旁出現「下一步 →」，按了才切頁，也不會自動開始：每日匯入完成 → 「2 起單資料」帶入這份交易明細的 FCN TDCC（依出現順序、不重複；TDCC 不是剛好 12 碼、整檔已取消的不帶；起單資料頁已有不同的 TDCC、或有改過還沒匯入資料庫的內容時先問，正在讀取或匯入時不帶入；帶入時清掉上一次讀取的結果）；準備完成 → 「4 核對」帶入這批已準備的 PDF（參考條件表沿用核對頁目前選的；核對頁有還沒存檔的結果時先問）；核對存檔 → 「5 放回」帶入批次資料夾與剛存的核對結果檔。審查通過日期、匯出回報包與使用的設定檔不是每天的步驟，在視窗上方的「維護」選單（[ADR 0007](docs/adr/0007-single-workflow-window.md)）。關閉視窗時，核對頁還在處理、有還沒存檔的核對結果，或 起單資料頁還在處理、有改過還沒匯入資料庫的內容，會先提醒。第一次開啟時最大化，之後照上次調整的大小開啟。

**從兩個捷徑的版本更新**：重新執行 `setup_panel.cmd` 後，桌面與專案資料夾只剩「FCN Workflow」一個捷徑，開啟時停在「1 每日匯入」（以前停在核對）。以前建的「TS IIS 準備與放回」捷徑，指向這個專案資料夾的會自動刪掉，指向別的資料夾的不動（[#77](https://github.com/TaylorYam/structured-product-workflow/issues/77)）。

**從舊的核對工具安裝移過來**：以前另外裝過 `fcn-term-sheet-checker` 的電腦，在本專案資料夾執行一次 `setup_panel.cmd`，之後改用「FCN Workflow」捷徑；桌面上舊的「Term Sheet 核對」捷徑還指向舊資料夾，請手動刪除。兩邊各用自己資料夾裡的 `.venv`，不會互相影響。舊資料夾 `runtime/核對紀錄/` 裡的核對紀錄要留查時，整個複製到本專案的 `runtime/核對紀錄/`（檔名是日期時間，不會重複）；核對 PANEL 記住的選檔資料夾不會帶過來，第一次重新選即可。確認新的捷徑能用之後，舊資料夾可以刪掉。

**開發者安裝**：

```bash
python -m pip install -c constraints.txt -e ".[dev]"
```

PostgreSQL 驅動已包含在套件裡。核對程式（`fcn_checker`）也在同一個套件裡；如果這個 Python 環境以前另外裝過舊的 `fcn-term-sheet-checker`（套件名稱 `fcn-checker`），請先 `python -m pip uninstall fcn-checker`，避免兩份 `fcn_checker` 同時在 Python 路徑上。

開發時用指令開啟視窗（`fcn-panel` 開同一個視窗並停在「4 核對」）：

```bash
spw-panel
```

在「3 準備」分頁：

1. 在輸入框貼上要核對的 TDCC（換行、空白、逗號、頓號都可以分隔；重複的只算一次）。
2. 確認視窗最上方的根目錄（商品資料夾可以在底下任何一層；要換時按「變更…」），再選輸出位置；兩者都會記在本機 `.local/`，下次直接帶入。
3. 按「開始準備」。每次在輸出位置開一個 `待核對_<日期時間>` 資料夾，已存在就不做。
4. 看第一頁內容判斷：開頭附近出現「中文產品說明書」或「中文投資人須知」；說明書第一頁要有資料夾的 TDCC，投資人須知有 12 碼代號時要相符。檔名只當參考。
5. 有上手 PDF 就用 PDF；某種文件沒有 PDF 時（例如 MS、BNP 只給 Word），才把資料夾裡的 Word（`.docx`、`.doc`）複製到暫存區，用 Microsoft Word 轉成 PDF 再判斷（[ADR 0002](docs/adr/0002-word-to-pdf-with-microsoft-word.md)）。原始 Word 不動；轉檔失敗或逾時（Word 跳出對話框）時那個商品跳過。MS 說明書封面的「受託機構商品代號：」固定空白，轉檔前在暫存複本補上 TDCC（已有值不動），不用再手動改 Word；商品資料夾裡的原始 Word 仍是空白，放回與寄信用的是補好的 PDF；準備清單的原因欄會寫「已補受託機構商品代號」（[#56](https://github.com/TaylorYam/structured-product-workflow/issues/56)）。
6. 說明書或投資人須知任一份找不到、或有兩份以上候選時，整個商品跳過並寫出原因與候選檔名。
   **重新核對上手修正版**：資料夾裡上次放回的「_待確認」檔不當候選，只用來認出哪一版沒通過（比對全文）；內容相同的幾份只算一份。上手寄來新版時會挑新版，連上次已放回的標準名舊版也一併換掉（修正版沿用舊檔名、覆蓋了舊原檔也一樣：內容不同時上手檔名的版本優先，只以標準檔名存在的內容當成上次放回的舊版）；上手還沒寄新版時，沿用上次沒通過的那一版照常準備（原因寫「沿用上次沒通過的版本」；資料夾裡若另有封面商品代號不符或轉檔失敗的新檔，也會一起寫出），所以改過參考條件表或核對規則後，可以用同一版重複核對；同一版又沒通過時，錯訊和資料夾裡最新的錯訊檔不同才會再寫一份。準備清單的原因欄會寫出排除了哪些舊版。MS、BNP 這類只給 Word 的上手，只有沒通過的那份會換成新版；如果兩份都改了，請先把舊的標準名檔移到子資料夾。上次沒通過的是 PDF 時，修正版也要有 PDF 才會被挑到（例如 JPM 同時給 PDF 和 Word，修正版只有 Word 時請上手補 PDF）。
7. 批次資料夾裡的 `準備清單.xlsx` 記錄每份文件的來源檔、是否由 Word 轉檔、輸出檔名、SHA-256 指紋、狀態與原因。完成後可以按「打開批次資料夾」查看；按「下一步：核對 →」切到「4 核對」，這批已準備的 PDF 已經帶入，選好參考條件表再載入預覽。

核對完、存下核對結果檔後，按「下一步：放回 →」切到「5 放回」分頁：

1. 批次資料夾與「4 核對」剛存的核對結果檔已經帶入（要換時按「選擇…」或提示條的「改選…」）。
2. 按「放回資料夾」。核對結果檔「回填後」上 `TS/IIS完成檢核` 為 `V` 的商品（說明書與投資人須知都通過或人工放行）用標準檔名放回；「錯誤清單」上沒通過的那份，檔名最後加「_待確認」放回（例：`<TDCC>_TS_待確認.pdf`），同商品通過的那份仍用標準檔名，原因欄附上錯訊；同時在商品資料夾寫一份 `<TDCC>_錯誤訊息_<日期時間>.xlsx`（一條錯訊一列，附 PDF 頁碼，頁碼對應放回的「_待確認」檔），打開資料夾就看得到問題在哪裡。重複放回、待確認檔已經在時不會再寫。核對結果檔裡沒出現的商品（還沒核對）不放回。
3. 放回前重算批次裡 PDF 的指紋，和準備清單不符就不放。商品資料夾已有同名檔：內容相同（位元組相同，或全文相同，例如同一份 Word 重新轉出的 PDF）算「已在」（例如 UBS 本來就給標準檔名），內容不同不覆蓋、整個商品不放回，由作業人員決定留哪份。
4. 只複製，批次資料夾保留；結果寫在批次資料夾的 `放回清單_<日期時間>.xlsx`；這批準備了但還沒核對的商品也會列出，標「不放回」。修好未通過的商品、重新核對後可以再放一次，已放回的會顯示「已在」；改用標準檔名放回時，舊的「_待確認」檔和舊錯訊檔不會自動刪除，清單會提醒你確認後手動刪除。

### TS／IIS 核對

核對程式（`src/fcn_checker/`，原 [fcn-term-sheet-checker](https://github.com/TaylorYam/fcn-term-sheet-checker)）把中文產品說明書與中文投資人須知 PDF 跟參考條件表逐欄比對：14 家上手、固定規則、不使用 LLM。同一檔的兩份都通過或人工放行，才輸出到核對結果檔的「回填後」（`TS/IIS完成檢核` 打 `V`，回填 ISIN、發行日、比價日；VWAP 商品另回填各標的價格）；沒通過的每條錯訊列在「錯誤清單」。上面的「放回資料夾」讀的就是這份核對結果檔。

1. 在「4 核對」分頁選參考條件表，再選批次資料夾裡的 `<TDCC>_TS.pdf`、`<TDCC>_IIS.pdf`，按「載入／重新載入預覽」→「開始核對」。看過錯訊認定沒問題的可以「人工放行」，最後按「儲存核對結果…」。有未通過的商品時，可以從「維護」→「匯出回報包…」交給維護者。
2. 指令版：`fcn-batch <參考條件表.xlsx> <PDF…> --out <輸出資料夾>`，一次完成預覽、核對與儲存，不能人工放行。結束碼 0 是已涵蓋的規則全部通過（沒涵蓋的項目仍要人工看）、1 有不一致或需人工覆核（含未支援上手）、2 錯誤。
3. 每次儲存另寫一份內部核對紀錄（程式版本、設定與每份 PDF 的指紋、逐項結果），PANEL 寫在安裝資料夾的 `runtime/核對紀錄/`，指令版寫在執行目錄的 `runtime/核對紀錄/`。
4. **從資料庫讀條件**（[#68](https://github.com/TaylorYam/structured-product-workflow/issues/68)、[ADR 0013](docs/adr/0013-check-from-database.md)）：「條件來源」選「資料庫」就不用選參考條件表。載入預覽時依 PDF 檔名的 TDCC 從資料庫產生一份同格式的參考條件表（留在安裝資料夾的 `runtime/資料庫參考條件表/`），之後的核對、人工放行、核對結果檔與回報包都和 Excel 一樣。預設仍是 Excel，行為不變。
   - 資料庫沒有這檔、核對條件還不是「完成」（等股價、等交易明細、待確認，見下面「核對條件」），或以前清單更新匯入、已鎖住的商品：那一份不核對、需人工覆核並寫原因，不能人工放行，同批其他照常。
   - 按「儲存核對結果…」存好核對結果檔後寫回資料庫：說明書與投資人須知都通過或人工放行的商品，回填 ISIN、發行日、比價日（VWAP 商品另含各標的價格），人工放行時單位面額改成 TS 的值，然後**鎖住**（之後起單資料不能再覆蓋）。每份文件最新的核對結果與每個改變的原值都留在資料庫。已鎖住的商品可以再核對，但不再回填。
   - 寫回失敗（例如連線中斷）時核對結果檔照樣保留，畫面寫出原因，排除後按「重新寫回資料庫」。
   - 指令版：`fcn-batch --database <PDF…> --out <輸出資料夾>`（連線設定讀 `.env`，沒寫密碼時會問；產生的參考條件表放在輸出資料夾）。
   - 更新程式後請執行一次 `spw-db-migrate`（資料表版本 0010）。

規則、範本、支援範圍、參考條件表格式與核對的 ADR 見 [docs/checker/](docs/checker/README.md)（只有中文，裡面的 Issue 編號指原 repo）；新增上手或範本改版依[新增上手流程](docs/checker/issuer-onboarding.md)。

### 每日匯入（最小 FCN 資料庫）

把每天的 FCN 交易明細與每日股價匯入 PostgreSQL（[#87](https://github.com/TaylorYam/structured-product-workflow/issues/87)、[ADR 0008](docs/adr/0008-daily-prices-denomination-and-order-dates.md)）；商品條件改由「2 起單資料」確認後匯入；信箱在資料庫直接維護（[ADR 0004](docs/adr/0004-mailbox-maintained-in-database.md)、[#35](https://github.com/TaylorYam/structured-product-workflow/issues/35)）（[#32](https://github.com/TaylorYam/structured-product-workflow/issues/32)、[ADR 0003](docs/adr/0003-minimal-fcn-database.md)）。寄信草稿之後只讀這個資料庫。

**第一次設定（每台電腦一次）**：
1. 安裝 PostgreSQL，建立資料庫與日常使用的帳號（可用 `scripts/provision_postgres_dev.py`）。
2. 在安裝資料夾（開發時是 repo 根目錄）建立 `.env`，填 `PGHOST`、`PGPORT`、`PGDATABASE`、`PGUSER`；`PGPASSWORD` 可以不填，匯入時會詢問。`.env` 不進 Git。
3. 用有建立權限的帳號建立資料表，並把讀寫權限給 `PGUSER`：

```bash
spw-db-migrate --admin-user postgres
```

之後程式更新、有新的資料表版本時，再執行一次同樣的指令。換電腦（例如從筆電到公司電腦）時不複製資料庫，照上面三步重建，再把每日更新重新匯入。

**每天**：
1. 打開每日更新（範本 `PostgreSQL每日更新.xlsx`，只放在私人樣本倉庫）：把前一天的交易整列貼到 `交易明細`（欄位同交易清單月份工作表，第 1 列欄名不要動）；`BBG_Data` 依範本取價（C1 是取價日，內容是前一個交易日的價格）。以前的 `清單更新` 工作表已停用，檔案裡有也不讀。
2. 視窗切到「1 每日匯入」，選每日更新，按「匯入」。
3. 看結果：
   - 「新增交易」：寫入成功。「已存在」：之前匯過，這次沒有寫入。
   - 「內容不同（未覆蓋）」：交易列和資料庫裡的不一樣，資料庫保留第一次的內容。
   - 「擋下」：這一列缺值或寫法不認得，修正 Excel 後重新匯入。
   - 「股價衝突」：`BBG_Data` 同一個代碼出現多列、值不一樣；這個代碼的價格不存，算價格時不能用。值都一樣的重複列只算一筆。
   - 「股價不是數字」：價格格子不是數字（例如 `#N/A`），存成空白。
   - 「交易已取消」：交易明細的「備註」去頭尾空白、不分大小寫等於 `Cancel`，或備註包含「取消」；該列不會匯入資料庫。
   - 「名字對不到信箱」：交易明細的 FC 名字在 `mailbox.person` 找不到（大小寫不同視為同一人）；拼法不同請在 `mailbox.alias` 加一筆別名。
   - 「信箱尚未建立」：`mailbox.person` 還是空的，名字無法比對。
   - 完成訊息另外寫出股價的取價日與筆數；同一個取價日重新匯入時，整份取代那一天的股價（每次匯入都留紀錄）。
   - 「核對條件：…」：匯入後重算還沒鎖住的商品（見下面「核對條件」），列出這次有變、或還沒完成的商品。
4. 匯入完成後，這份交易明細的 FCN TDCC 已經帶到「2 起單資料」（完成訊息寫出帶了幾筆）；按「下一步：起單資料 →」接著讀起單建檔（[#90](https://github.com/TaylorYam/structured-product-workflow/issues/90)）。已存在的商品照樣帶；交易明細沒有 FCN 或整份不匯入時不帶。

只收 FCN，其他商品略過並計數；備註為 `Cancel` 或包含「取消」的交易列不會匯入資料庫；同一份重複匯入不會重複寫入交易；任何錯誤都不會留下一半的資料。所有匯入問題會集中重建到來源檔同資料夾的 `每日匯入錯誤.xlsx`，包含既有匯入歷史；因此 9 月、10 月會在同一份檔案。交易列是否取消以備註欄為準，不讀取 Excel 底色。股價以「取價日＋代碼」為一筆，代碼只去頭尾空白，不改大小寫、不換交易所碼；`BBG_Data` 的 C1 不是日期或第 2 列欄名不同時整份不匯入。每日匯入不寫商品條件、不做分公司對帳，也不會修改信箱；分公司配置在匯入後重算核對條件時由交易明細算（#88）。#87 之前清單更新寫入的商品照舊保留，還沒鎖住的會一起重算。

**信箱維護**：直接在資料庫的 `mailbox.person`（人員）、`mailbox.alias`（別名）新增或修改，例如用 pgAdmin 以有寫入權限的帳號執行 SQL。比對用的小寫鍵由資料庫自動產生；名字不分大小寫不能重複，信箱格式不對、別名指到不存在的人都會被拒絕。改名時別名會跟著改。

若同一個 PostgreSQL 已有其他系統的 `public.mailbox`（例如欄位為 `user_name`、`email`、`unit`），本專案不會讀取或修改它。`public.mailbox` 是資料表；本專案使用的 `mailbox.person` 與 `mailbox.alias` 是本專案 migration 建立的另一個 schema／資料表集合，兩者可並存。若要把既有名單初次帶入本專案，請先檢查資料並由管理者明確執行一次性 SQL；不會自動同步，`fixed_cc` 也必須另外確認：

```sql
INSERT INTO mailbox.person (name, email, unit, fixed_cc)
SELECT user_name, email, unit, false
FROM public.mailbox
ON CONFLICT (name_key) DO NOTHING;
```

```sql
-- 新增人員（fixed_cc = true 是寄信的固定副本）
INSERT INTO mailbox.person (name, email, unit, fixed_cc) VALUES ('Amy', 'amy@example.com', 'FC', false);
-- 交易清單上的拼法不同時，加別名指到使用者名稱
INSERT INTO mailbox.alias (alias, person_name) VALUES ('Amie', 'Amy');
-- 修改、刪除
UPDATE mailbox.person SET email = 'amy.new@example.com' WHERE name = 'Amy';
DELETE FROM mailbox.person WHERE name = 'Amy';  -- 她的別名會一起刪除
```

### 寄信草稿

為核對通過的 FCN，在**自己的傳統版 Outlook 草稿匣**建立 TS／IIS 信件給承作的 FC（[#29](https://github.com/TaylorYam/structured-product-workflow/issues/29)）。**只建草稿，不會寄出**；請在 Outlook 逐封確認後自己按寄出。資料全部讀資料庫，所以先做完每日匯入。

1. 視窗切到「6 寄信草稿」，貼上核對通過的 TDCC（分隔方式同「3 準備」）。
2. 選結果清單位置（根目錄用視窗最上方的，同「3 準備」），按「建立草稿」。視窗會先顯示最近一次每日匯入（時間、第幾次、檔名），確認後才開始建立。
3. 每檔要符合才會建草稿，否則標「待處理」並寫出原因：
   - 資料庫有這檔的商品條件與交易列；
   - 商品資料夾裡有 `<TDCC>_TS.pdf` 與 `<TDCC>_IIS.pdf`（放回資料夾只會以標準檔名放回核對通過的文件；`_待確認` 檔不算）；
   - 交易列上的 FC 都對得到信箱。
4. 信件：一檔一封；收件人是承作的 FC（略過 PB，只有 PB 時標「不需寄送」），副本是信箱裡 `fixed_cc` 的人；主旨 `[中文TS/IIS] [FCN] - <TDCC>`；內文使用 `.env` 的 `SPW_MAIL_TEMPLATE` Excel 範本，填入資料後以可編輯表格貼進 Outlook；附上兩份 PDF。
   表格內容置中；英文字型使用 Calibri、中文字型使用 Microsoft JhengHei；表頭最多兩行。固定副本排序為 MAX、BOB、PM，再接其他人員。
5. 結果清單 `寄信草稿清單_<日期時間>.xlsx` 列出每檔的狀態、收件人、副本與原因；「資料來源」工作表記錄用的是哪一次每日匯入（時間、編號、檔名、SHA-256）與搜尋時讀不到的資料夾。

每日匯入略過的非 FCN 商品會標「不支援的商品類型」。同一檔重跑會再建一封草稿，重複的請自己刪除。某一檔出錯（例如 Outlook 沒回應、資料有問題）只標那一檔「建立失敗」，其他檔照常處理。商品資料夾只讀不改。更新程式後請執行一次 `spw-db-migrate`（新增了資料表版本）。

### 起單資料

讀作業人員每天的起單建檔 `起單建檔_<日期>.xlsm`，在視窗裡逐檔確認、修改後匯入資料庫（[#97](https://github.com/TaylorYam/structured-product-workflow/issues/97)、[ADR 0010](docs/adr/0010-order-terms-from-daily-order-sheet.md)；確認畫面見 [#86](https://github.com/TaylorYam/structured-product-workflow/issues/86)、[ADR 0009](docs/adr/0009-confirm-order-terms-in-window.md)）。不再讀 `(Order)` 原信，也不再匯出 Excel。

1. 視窗切到「2 起單資料」，貼上 TDCC（分隔方式同「3 準備」；剛做完每日匯入時已帶入交易明細的 FCN TDCC，按提示條的「改選…」可以清掉自己貼），在「起單建檔」按「選擇…」選當天的檔（第一次打開 `G:\Bond Data\PM事項\0.常用工具\ELN每日起單建檔`，之後打開上次選的資料夾），按「讀取」。這一頁不用根目錄。
2. 只讀選的那一份、不修改它。起單建檔只有一張工作表，前幾列裡有 `TDCC` 的那列是表頭（目前第 2 列、從 B 欄開始）；欄位依表頭文字對，不分大小寫、不看位置。讀的欄：Product、Currency、Guaranteed Periods (m)、BBG Code 1～5、Strike (%)、KO Type、KO Barrier (%)、Coupon p.a. (%)、`UF (%)`（舊表頭 `Upfront / NotePrice (%)`）、Tenor (m)、Barrier Type、KI Barrier (%)、四個日期、期初價格。其他欄（No.、Observation Frequency、OTC、Funding Spread、Effective Date Offset、起單FC）沒有對應的資料庫欄位，不讀。
3. `UF (%)` 那欄放的是價格（例如 97.14），畫面上叫 Upfront / NotePrice (%)；寫進資料庫時 UF = 100 − 價格。
   Strike date（期初定價日，[#101](https://github.com/TaylorYam/structured-product-workflow/issues/101)、[ADR 0011](docs/adr/0011-strike-date.md)）起單建檔沒有這欄：期初價格寫「明日開盤價」時，期初價格記成開盤價、Strike date 是交易日之後標的市場的下一個營業日（依 `config/exchange_holidays.toml`，例如週五交易是週一；遇到休市日再往後）；其他情況 Strike date 等於 Trade date。假日表確定不了（標的交易所不在表上、日期超出範圍）時 Strike date 留空標紅，由作業人員填（不會用資料庫的值補）；Strike date 不能早於 Trade date。畫面上改 Trade date 時，原本等於 Trade date 的 Strike date 會跟著改，明日開盤價算出的不動。
4. 規則式、不猜值：
   - KO Type 要是 Daily／Daily Memory／Period End／Period End Memory，Barrier Type 要是 NONE／EKI／AKI，期初價格要是 開盤價／收盤價／VWAP，不分大小寫（`DAILY MEMORY`、`None` 都認）。
   - 認不得的值（例如 `MKI`、KO Type 寫成數字、期初價格寫「待確認」）、數字欄不是數字、日期欄不是日期：那格留空、標紅，寫出讀到的值，由作業人員在畫面上改。
   - 上手依 TDCC 前三碼查 `config/issuer_prefixes.toml`；查不到時擋匯入。
   - 同一檔 TDCC 有好幾列：內容一樣就用；不同的欄留空，寫出各列的值，填好才可以匯入（正確是空白時請改起單建檔後重新讀取）。
   - 貼的 TDCC 不在這份檔裡標「找不到」；不是 12 碼標「待確認」。
   - 選到的檔有好幾張工作表（例如總表 `ELN每日起單建檔.xlsm`）、找不到表頭、少欄或同一欄出現兩次時，整批不開始並寫原因。
5. 讀完後，下面左邊列出這批商品（有問題的排前面），右邊是編輯區：
   - 狀態：「待確認」是有欄位擋匯入（必填空白、格式不對等）；「請確認」是讀起單建檔時有提醒、但可以匯入；「可匯入」；「已鎖住」是核對通過、或 migration 0006 時已在資料庫的商品，不能改也不能匯入（要改走更正商品條件，#55）。找不到的照舊顯示原因。
   - 點一檔，右邊列出上面那些欄。淺紅底是擋匯入或讀起單建檔時有問題，下面寫原因；淺黃底是和資料庫的值不同，寫出資料庫的值，可以按「用資料庫的值」改回。KO Type、Barrier Type、期初價格用下拉選單，日期寫成 `2026-10-21`；離開欄位或按 Enter 時檢查，寫錯的保留打的字並標紅，匯入時那一檔不匯入、其他照常。
   - `.env` 有連線設定時會連資料庫（沒寫密碼時讀取前會問一次）：資料庫已有、還沒鎖住的商品（包含清單更新建立的），起單建檔空白的欄帶入資料庫的值（好幾列不同的欄除外）。沒有連線設定也照樣可以讀，只是不帶入；讀取時沒連上資料庫的商品，匯入時不會覆蓋資料庫裡已有的那檔（請重新讀取）。
6. 確認好按「匯入資料庫」：通過檢查的寫進資料庫（單位面額依幣別預設值；最終比價日、到期日必填；核對通過前重新匯入會覆蓋並留紀錄，紀錄寫起單建檔檔名與列號、和起單建檔不同的人工修改；空白的欄不會蓋掉你沒看過的資料庫值），結果欄改成新增／覆蓋／沒有變／不匯入／已鎖住，「核對條件」欄是匯入後重算的狀態（見下面「核對條件」）。承作金額以交易明細為準，這裡不填。加了 Strike date 之後請執行一次 `spw-db-migrate`（資料表版本 0008，已有的商品 Strike date 補成交易日）。有改過、還沒匯入資料庫就重新讀取、帶入新的 TDCC 或關閉視窗時會先問；處理中編輯區不能改。

### 核對條件

「1 每日匯入」或「2 起單資料」匯入完成後，自動替每檔**還沒鎖住**的商品算出核對需要、但起單資料沒有的資料，和上一次算的不同才寫進資料庫（[#88](https://github.com/TaylorYam/structured-product-workflow/issues/88)、[ADR 0012](docs/adr/0012-check-terms-calculation.md)）；已鎖住（核對通過）的商品不重算。更新程式後請執行一次 `spw-db-migrate`（資料表版本 0009）。

- **進場價、執行價、下限價、KO 價**（UL_1～UL_5 每個標的）：用取價日比 Strike date 晚的第一份股價檔，期初定價是收盤價取「最新收盤價」、開盤價取「最新開盤價」。執行價／下限價／KO 價 = 股價檔原值 × 執行價(%)／KI(%)／KO(%) ÷ 100，四個價格各自四捨五入（half-up）到小數第 4 位；KI 型態 NONE 時下限價空白。期初定價是 VWAP 時四個價格留空（由 TS 回填，#68），不算待確認。
- **股價檔對不對得上**：依 `config/exchange_holidays.toml` 確認 Strike date 是標的市場的交易日，而且和取價日之間沒有該市場的其他交易日（例如週五交易用週一的檔沒問題；漏匯一天就不行）。交易所碼不在假日表（目前只有美國、日本，港股會待確認）或日期超出涵蓋範圍也是待確認。
- **分公司配置**：這檔的交易列（取消的列不在資料庫，PB 也算）同一分公司的金額加總，依出現先後，取代原本的配置；寄信草稿照舊讀它。
- **交叉比對**：交易明細和起單資料的幣別、交易日、UF（交易明細寫比例 0.0199，起單資料是百分點 1.99），交易列之間不一樣也算不一致。承作金額不比。
- **狀態**：完成／等股價（還沒有取價日比 Strike date 晚的股價檔）／等交易明細（交易明細還沒有這檔）／待確認（原因，例如「UL_2 AAA UW：股價檔（取價日 2026-10-16）沒有這個代碼」「UF不一致：起單資料 1.99、交易明細 0.0215（換算 2.15）」）。代碼找不到、沒有價格、股價衝突時只有那個標的的價格留空。股價或交易明細之後才到時，下一次匯入會自己補算。
- **放行差異**：交叉比對不一致、看過確定沒問題時，在「1 每日匯入」或「2 起單資料」的結果表點那一檔，按「放行差異…」。會記下 Windows 使用者名稱、時間與放行了哪些差異，然後重算那一檔；之後同樣的差異不再擋，資料改了、差異跟著變時要重新放行。股價、假日表的問題不能放行，要修正資料（例如補匯股價檔）。
- 結果存在 `workflow.check_terms`（狀態、原因、用的股價檔）、`workflow.underlying_price`（各標的的四個價格）與 `workflow.branch_allocation`；放行紀錄在 `workflow.cross_check_release`。#68 核對時讀這些表。重算失敗（例如假日表設定檔有問題）時匯入照樣保留，完成訊息寫出原因，排除後再匯入一次就會重算。

測試：`pytest -q` 一次跑完流程與核對（`tests/checker/`）兩組測試（資料全部虛構；真的 Word 轉檔測試只在本機有 Word 時執行，CI 自動略過）。要快一點可以分兩次：`pytest -q --ignore=tests/checker`，再 `pytest -q -n auto tests/checker`（核對測試用 pytest-xdist 平行跑；資料庫測試共用同一個測試資料庫，不能平行跑）。`--shard=i/n` 只跑第 i 份（同一個測試檔在同一份），例如 `pytest -q -n auto tests/checker --shard=2/4`。CI 依 PR 改到的檔案決定要跑哪些測試（搬檔時新舊路徑都算）：只改文件（`*.md`、`docs/`）就只做空白檢查；只改流程（`src/sp_workflow/`、`tests/` 的流程測試、`scripts/`）就不跑核對測試；改到核對（`src/fcn_checker/`、`tests/checker/`、`config/`、`panel_bootstrap.py`、`launch_checker_panel.*`、`scripts/build_panel_icon.py`）就把核對測試分 4 份同時跑；改 `tests/conftest.py`、`tests/shard.py` 會跑流程與核對測試，但不跑 Windows 安裝解析；改到 `pyproject.toml`、`constraints.txt`、CI、安裝腳本或其他沒列到的檔案就全部跑。合併進 `main` 或手動執行 CI 時一律全部跑；讀不到改動的檔案時 CI 會變紅，不會略過測試還顯示通過。核對的本機真實樣本測試讀 `data/`，或把環境變數 `FCN_TEST_DATA_DIR` 設成放 `orders/`、`term-sheets/` 的資料夾（結構見 `tests/checker/real_data.py`）。資料庫測試需要一個名稱以 `_test` 結尾、可以清空的 PostgreSQL 資料庫，把連線字串設在環境變數 `SPW_TEST_DSN`（例如 `host=localhost dbname=spw_test user=...`）；沒設定時略過，CI 用 PostgreSQL 服務執行。核對讀資料庫的測試在 `tests/checker/` 並標 `db`，要另外依序跑 `pytest -q -m db tests/checker`（不能和 `-n auto` 一起跑）。真的 Outlook 建草稿測試只在本機設定 `SPW_OUTLOOK_TEST=1` 時執行（會在草稿匣建一封測試信再刪掉）。真實樣本試跑只在本機執行：把環境變數 `SPW_REAL_SAMPLES_DIR` 設成放商品資料夾的目錄，CI 自動略過；真實資料不進 repo，放在 repo 裡時用 `.gitignore` 已排除的 `FCN商品/`。

## English

Status: TS/IIS preparation and return is complete and trial-run on copies of real samples; operational shared-drive testing is pending ([#5](https://github.com/TaylorYam/structured-product-workflow/issues/5)). The minimal FCN database with daily import is [#32](https://github.com/TaylorYam/structured-product-workflow/issues/32); mail drafts are [#29](https://github.com/TaylorYam/structured-product-workflow/issues/29); Order term reading from the daily order sheet is [#97](https://github.com/TaylorYam/structured-product-workflow/issues/97); calculating check terms after imports is [#88](https://github.com/TaylorYam/structured-product-workflow/issues/88); checking against database terms with write-back is [#68](https://github.com/TaylorYam/structured-product-workflow/issues/68). Other workflows remain in design.

This project coordinates formal Orders, booking, issuer document archiving, checker integration, and FC distribution. Inquiry remains in the existing system.
Checking rules used to live in the independent [fcn-term-sheet-checker](https://github.com/TaylorYam/fcn-term-sheet-checker) and have been merged into this repository ([ADR 0005](docs/adr/0005-merge-checker-into-workflow.md)): their code and history live in `src/fcn_checker/`, one installation provides `fcn-batch` and `fcn-panel`, and its documents are in [docs/checker/](docs/checker/README.md); new product data will be built from Orders, trade rows, and prices ([ADR 0006](docs/adr/0006-new-product-data-from-order.md)).

Read [business context](CONTEXT.md), [architecture](docs/architecture.md), then [open questions](docs/open-questions.md).
For persistence/deployment, read the [PostgreSQL decision](docs/adr/0001-postgresql-workflow-storage.md) and [development/deployment design](docs/database-and-deployment.md). First-phase data scope is in [ADR 0003](docs/adr/0003-minimal-fcn-database.md).
For integration, also read the [checker integration proposal](docs/checker-integration.md).

Created from TaylorYam/ai-project-template on 2026-10-18, retaining its issue, PR, CI, and agent workflows.
Commit documentation and synthetic examples only. Company messages, trades, customers, inventory, network files, and credentials remain in the company environment.

### TS/IIS batch preparation

Picks the Chinese term sheet and investor information sheet from TDCC-named product folders under the shared FCN directory, renames them to the checker's `<TDCC>_TS.pdf` and `<TDCC>_IIS.pdf`, and places them in a new batch folder. Product folders are read only.

**Operator setup (Windows)**: install official Python 3.11 or later (with Tcl/Tk), place the project in a folder you can write to, and double-click `setup_panel.cmd`. Setup creates the project's own `.venv` and installs pinned packages from `constraints.txt`, which requires access to the company-approved Python package source. After "安裝完成", double-click the "FCN Workflow" shortcut on the desktop or in the project folder to open the working window; if shortcut creation failed, double-click `launch_panel.cmd` (`launch_checker_panel.cmd` opens the same window on 4 核對). Daily use needs no commands; the last chosen folders and the window size are remembered in the install folder's `.local/`, and check records go to the install folder's `runtime/核對紀錄/`. Rerun setup after moving the folder or updating the code. Word conversion requires Microsoft Word on this computer.

**Working window**: one "FCN Workflow" window whose left step bar follows the daily order: 1 每日匯入 (daily import) → 2 起單資料 (order sheet) → 3 準備 (prepare) → 4 核對 (check) → 5 放回 (return) → 6 寄信草稿 (mail drafts); click a step to switch to it, and the current step is highlighted in light blue; each step keeps its contents when you switch. The look is a clean light-gray background with white cards: each page starts with what it does, TDCC boxes are tall and show how many were pasted; results lead with key figures (prepared/skipped, passed/mismatched) with problem counts in red, and the status line turns into a green result bar when a step finishes ([#83](https://github.com/TaylorYam/structured-product-workflow/issues/83), [#85](https://github.com/TaylorYam/structured-product-workflow/issues/85)). The root folder holding the product folders is set once at the top of the window (press 變更…) and shared by 3 準備 and 6 寄信草稿; each tab still remembers its own output location. After a step finishes, the next tab is filled in first and a blue hint bar says what was brought over (press 改選… to replace it); a 下一步 → (next step) button appears beside the completion message and switches tabs only when pressed, and nothing starts automatically: after the daily import, 2 起單資料 receives the FCN TDCCs of that trade sheet (in order of appearance, without duplicates; TDCCs that are not exactly 12 digits and products whose trades are all cancelled are left out; if the Order terms tab already holds different TDCCs or has edits not yet imported into the database, you are asked first, nothing is filled in while it is reading or importing, and filling in clears the previous read's results); after preparation, 4 核對 receives the batch's prepared PDFs (keeping the reference workbook already chosen there, and asking first if an unsaved check result would be cleared); after saving a check, 5 放回 receives the batch folder and the result workbook just saved. Approval dates, report bundle export, and the configuration paths in use are not daily steps and live in the 「維護」 (maintenance) menu at the top ([ADR 0007](docs/adr/0007-single-workflow-window.md)). Closing the window warns while the check tab is still working, a check result has not been saved, or the Order terms page is still working or has edits not yet imported into the database. The window opens maximized the first time and at the last adjusted size afterwards.

**Updating from the two-shortcut version**: after rerunning `setup_panel.cmd`, the desktop and the project folder have only the "FCN Workflow" shortcut, which now opens on 1 每日匯入 (previously on the check tab). An old "TS IIS 準備與放回" shortcut is removed automatically when it points to this project folder; one pointing to another folder is left alone ([#77](https://github.com/TaylorYam/structured-product-workflow/issues/77)).

**Moving from a separate checker installation**: on a computer that had `fcn-term-sheet-checker` installed separately, run `setup_panel.cmd` once in this project folder and use the "FCN Workflow" shortcut from then on; the old desktop "Term Sheet 核對" shortcut still points to the old folder, so delete it manually. Each installation uses the `.venv` inside its own folder, so they do not interfere. To keep old check records, copy the old folder's `runtime/核對紀錄/` into this project's `runtime/核對紀錄/` (file names are timestamps and do not collide); the checker PANEL's remembered folders are not carried over, so choose them once again. After the new shortcut works, the old folder can be deleted.

**Developer setup**:

```bash
python -m pip install -c constraints.txt -e ".[dev]"
```

The PostgreSQL driver is included in the package. The checker (`fcn_checker`) ships in the same package; if this Python environment previously had the old `fcn-term-sheet-checker` installed (package name `fcn-checker`), run `python -m pip uninstall fcn-checker` first so two copies of `fcn_checker` are never on the Python path.

Open the window from a terminal during development (`fcn-panel` opens the same window on 4 核對):

```bash
spw-panel
```

On the 3 準備 (prepare) tab:

1. Paste the TDCCs to check (newlines, spaces, commas, or ideographic commas; duplicates count once).
2. Check the root folder at the top of the window (product folders may be at any depth; press 變更… to change it), then choose the output location; both are remembered in local `.local/`.
3. Press Start. Each run creates `待核對_<timestamp>` in the output location and refuses an existing folder.
4. Identification uses page-one content: a 中文產品說明書 or 中文投資人須知 title near the top; the term sheet's first page must contain the folder TDCC, and an investor sheet's 12-digit code must match when present. File names are hints only.
5. Issuer PDFs take precedence. Only when a document type has no PDF (for example MS and BNP provide Word only) are the folder's Word files (`.docx`, `.doc`) copied to a temporary area and converted with Microsoft Word before identification ([ADR 0002](docs/adr/0002-word-to-pdf-with-microsoft-word.md)). Original Word files stay untouched; a failed or timed-out conversion (for example a Word dialog) skips that product. The MS term sheet cover always leaves 受託機構商品代號 (trustee product code) blank, so the TDCC is filled into the temporary copy before conversion (existing values are left alone) and nobody edits the Word by hand; the original Word in the product folder stays blank, while the returned and mailed PDF is the filled one; the preparation list's reason column says 已補受託機構商品代號 ([#56](https://github.com/TaylorYam/structured-product-workflow/issues/56)).
6. If either document is missing or has multiple candidates, the whole product is skipped with the reason and candidate names.
   **Rechecking revised issuer documents**: `_待確認` files returned last time are never candidates and only identify which version failed (full-text comparison); identical copies count once. A newly sent version is picked, replacing even the previously returned standard-name version (also when the revision reuses the old file name and overwrites the old original: with differing contents the issuer-named version wins and content found only under the standard name is treated as the previous return); until the issuer sends one, the previously failed version is reused (the reason says so, and also lists new files that could not be used because of a cover-code mismatch or failed conversion), so the same version can be rechecked after fixing the reference workbook or checker rules; a new error workbook is written only when the errors differ from the latest one in the folder. The preparation list's reason column names the excluded versions. For Word-only issuers such as MS and BNP, only the failed document is replaced; if both changed, move the old standard-name file into a subfolder first. When the failed version was a PDF, the revision must also include a PDF to be picked (for example, ask JPM, which sends both PDF and Word, for a PDF when the revision is Word only).
7. `準備清單.xlsx` in the batch folder records each document's source, whether it was converted from Word, output name, SHA-256, status, and reason. Optionally press 打開批次資料夾 to look at the batch folder; press 下一步：核對 → to switch to 4 核對 (check), where the prepared PDFs are already filled in, then choose the reference workbook and load the preview.

After checking and saving the checker result workbook, press 下一步：放回 → to switch to the 5 放回 (return) tab:

1. The batch folder and the result workbook just saved on 4 核對 are already filled in (change them with 選擇… or the hint bar's 改選…).
2. Press Return. Products whose `TS/IIS完成檢核` is `V` on the result workbook's 回填後 sheet (both documents passed or manually released) are returned under standard names. A document listed on the error-list sheet is returned with `_待確認` appended (for example `<TDCC>_TS_待確認.pdf`), while the product's passing document keeps its standard name and the reason column carries the error messages; a `<TDCC>_錯誤訊息_<timestamp>.xlsx` workbook (one error per row with the PDF page numbers, which refer to the returned `_待確認` file) is also written to the product folder so operators can see the problems there. It is not written again when the pending files are already in place. Products absent from the result workbook (not yet checked) are not returned.
3. Batch PDFs are re-hashed and must match the preparation list. When a product folder already has a same-named file, identical content (same bytes, or the same full text such as a PDF re-converted from the same Word file) counts as already there (for example UBS issuing standard names); different content is never overwritten and the whole product is not returned, leaving the decision to the operator.
4. Files are copied only and the batch folder is kept; results go to `放回清單_<timestamp>.xlsx` in the batch folder, which also lists prepared products not yet checked as not returned. After fixing and rechecking failed products, returning again marks already returned files as already there; when a standard name is returned, old `_待確認` files and error workbooks are never deleted automatically and the list reminds you to remove them after review.

### TS/IIS checking

The checker (`src/fcn_checker/`, formerly [fcn-term-sheet-checker](https://github.com/TaylorYam/fcn-term-sheet-checker)) compares the Chinese term sheet and investor information sheet PDFs field by field with the reference workbook: 14 issuers, fixed rules, no LLM. Only when both documents of a product pass or are manually released does the product go to the result workbook's 回填後 sheet (`TS/IIS完成檢核` = `V`, with ISIN, issue date, and observation dates backfilled, plus underlying prices for VWAP products); every error for a failed document is listed on 錯誤清單. Returning to folders (above) reads this result workbook.

1. On the 4 核對 (check) tab, choose the reference workbook, then the batch folder's `<TDCC>_TS.pdf` and `<TDCC>_IIS.pdf`, and press 載入／重新載入預覽 then 開始核對. After reviewing the errors, a document can be released manually (人工放行); finally press 儲存核對結果…. When products fail, 維護 → 匯出回報包… exports a report bundle for the maintainer.
2. Command line: `fcn-batch <reference.xlsx> <PDFs…> --out <output folder>` previews, checks, and saves in one go, without manual release. Exit code 0 means every covered rule passed (uncovered items still need a person), 1 mismatch or review required (including unsupported issuers), 2 error.
3. Each save also writes an internal check record (program version, configuration and PDF hashes, per-item results): the PANEL writes to the install folder's `runtime/核對紀錄/`, the command line to `runtime/核對紀錄/` under the working directory.
4. **Terms from the database** ([#68](https://github.com/TaylorYam/structured-product-workflow/issues/68), [ADR 0013](docs/adr/0013-check-from-database.md)): choose 資料庫 as 條件來源 and no reference workbook is needed. Loading the preview generates a same-format reference workbook from the database for the TDCCs in the PDF file names (kept in the install folder's `runtime/資料庫參考條件表/`); checking, manual release, the result workbook, and the report bundle then work exactly as with Excel. Excel remains the default, with unchanged behaviour.
   - A product missing from the database, whose check terms are not 完成 (等股價, 等交易明細, 待確認; see "Check terms" below), or an old locked 清單更新 product: that document is not checked, needs review with the reason, and cannot be released; the rest of the batch proceeds.
   - After "儲存核對結果…" writes the result workbook, the database is updated: products whose term sheet and investor sheet both passed or were released get their ISIN, issue date, and observation dates (plus each underlying's prices for VWAP) backfilled, a released term sheet's denomination becomes the TS value, and the product is **locked** (order-sheet imports can no longer overwrite it). Each document's latest result and every changed value's old value stay in the database. Locked products can be checked again but are not backfilled again.
   - If the write-back fails (for example a dropped connection) the result workbook is kept, the window gives the reason, and "重新寫回資料庫" retries after the fix.
   - Command line: `fcn-batch --database <PDFs…> --out <output folder>` (connection settings from `.env`, asking for a missing password; the generated reference workbook goes to the output folder).
   - Run `spw-db-migrate` once after updating (schema version 0010).

Rules, templates, support scope, the reference workbook format, and the checker's ADRs are in [docs/checker/](docs/checker/README.md) (Chinese only; issue numbers there refer to the original repository). For a new issuer or template version, follow the [issuer onboarding flow](docs/checker/issuer-onboarding.md).

### Daily import (minimal FCN database)

Imports each day's FCN trade rows and daily prices into PostgreSQL ([#87](https://github.com/TaylorYam/structured-product-workflow/issues/87), [ADR 0008](docs/adr/0008-daily-prices-denomination-and-order-dates.md)); product terms are imported after confirmation on 2 起單資料; the mailbox is maintained directly in the database ([ADR 0004](docs/adr/0004-mailbox-maintained-in-database.md), [#35](https://github.com/TaylorYam/structured-product-workflow/issues/35)) ([#32](https://github.com/TaylorYam/structured-product-workflow/issues/32), [ADR 0003](docs/adr/0003-minimal-fcn-database.md)). Mail drafts will read only this database.

**First-time setup (once per computer)**:
1. Install PostgreSQL and create the database and the daily account (`scripts/provision_postgres_dev.py` can help).
2. Create `.env` in the install folder (the repository root during development) with `PGHOST`, `PGPORT`, `PGDATABASE`, and `PGUSER`; `PGPASSWORD` is optional and is asked for at import time. `.env` never enters Git.
3. Create the tables with an account that can create schemas, granting read/write to `PGUSER`:

```bash
spw-db-migrate --admin-user postgres
```

Run the same command again after updates that add schema versions. When moving to another computer (for example from the laptop to the company computer), do not copy the database: repeat the three steps and re-import the daily updates.

**Daily**:
1. Open the daily update (template `PostgreSQL每日更新.xlsx`, kept only in the private samples repository): paste yesterday's trade rows whole into `交易明細` (same columns as the trade-list monthly sheets; do not change the header row); `BBG_Data` pulls prices per the template (C1 is the price date, and the values are the previous trading day's). The old `清單更新` sheet is retired and is ignored if present.
2. In the window, open "1 每日匯入", choose the daily update, and click "匯入".
3. Review the results:
   - "新增交易": written. "已存在": imported before; nothing written this time.
   - "內容不同（未覆蓋）": the trade rows differ from the database, which keeps the first version.
   - "擋下": a row is missing values or uses an unrecognized form; fix the workbook and re-import.
   - "股價衝突": a code appears on several `BBG_Data` rows with different values; its prices are not stored and cannot be used for pricing. Duplicate rows with identical values count once.
   - "股價不是數字": a price cell is not a number (for example `#N/A`) and is stored blank.
   - "交易已取消": the trade-row 備註, after trimming and case-folding, is `Cancel`, or it contains `取消`; the row is not imported into the database.
   - "名字對不到信箱": an FC name is missing from `mailbox.person` (case is ignored); for spelling variants add an alias to `mailbox.alias`.
   - "信箱尚未建立": `mailbox.person` is empty, so names cannot be matched.
   - The completion message also gives the price date and count; re-importing the same price date replaces that whole day (every import is recorded).
   - "核對條件：…": unlocked products are recalculated after the import (see "Check terms" below); products that changed or are not done are listed.
4. After the import, the trade sheet's FCN TDCCs are already filled into "2 起單資料" (the completion message says how many); click "下一步：起單資料 →" to go on and read the order sheet ([#90](https://github.com/TaylorYam/structured-product-workflow/issues/90)). Products already in the database are included; nothing is filled in when the trade sheet has no FCN or the whole file is rejected.

Only FCN is imported; other products are skipped and counted. Trade rows whose 備註 is `Cancel` or contains `取消` are not imported. Re-importing the same file writes no new trades, and any error leaves no partial data. All import problems are rebuilt into one `每日匯入錯誤.xlsx` beside the source workbook, including prior import history, so September and October appear in the same workbook. The note field is authoritative for cancellation; Excel fill colors are not inspected. Prices are keyed by price date and code; codes are only trimmed, never re-cased or mapped to another exchange; the whole file is rejected when `BBG_Data` C1 is not a date or the row-2 headers differ. The daily import writes no product terms, does no branch reconciliation, and never changes the mailbox; branch allocations are derived from the trade rows when check terms are recalculated after the import (#88). Products written by 清單更新 before #87 are kept, and unlocked ones are recalculated too.

**Mailbox maintenance**: add or edit rows directly in `mailbox.person` (people) and `mailbox.alias` (aliases), for example with SQL in pgAdmin using an account with write access. The lowercase matching keys are generated by the database; names must be unique ignoring case, and invalid email formats or aliases pointing to unknown people are rejected. Renaming a person updates their aliases.

If the same PostgreSQL database already has another system's `public.mailbox` (for example, with `user_name`, `email`, and `unit` columns), this project does not read or modify it. `public.mailbox` is a table; this project uses the separate `mailbox.person` and `mailbox.alias` schema/table set created by its migrations, so both can coexist. To seed the project mailbox from an existing list, review the data first and have an administrator run a one-time SQL statement explicitly; there is no automatic synchronization, and `fixed_cc` must be confirmed separately:

```sql
INSERT INTO mailbox.person (name, email, unit, fixed_cc)
SELECT user_name, email, unit, false
FROM public.mailbox
ON CONFLICT (name_key) DO NOTHING;
```

```sql
-- Add a person (fixed_cc = true marks a fixed mail CC)
INSERT INTO mailbox.person (name, email, unit, fixed_cc) VALUES ('Amy', 'amy@example.com', 'FC', false);
-- Add an alias when the trade list spells the name differently
INSERT INTO mailbox.alias (alias, person_name) VALUES ('Amie', 'Amy');
-- Update or delete
UPDATE mailbox.person SET email = 'amy.new@example.com' WHERE name = 'Amy';
DELETE FROM mailbox.person WHERE name = 'Amy';  -- their aliases are deleted too
```

### Mail drafts

Creates TS/IIS emails to the FCs handling each checked FCN in **your own classic Outlook Drafts folder** ([#29](https://github.com/TaylorYam/structured-product-workflow/issues/29)). **It only creates drafts and never sends**; review each one in Outlook and send it yourself. All data comes from the database, so run the daily import first.

1. In the window, open "6 寄信草稿" and paste the checked TDCCs (same separators as preparation).
2. Choose the result-list location (the root folder is the one at the top of the window, as in preparation), then click "建立草稿". The window first shows the latest daily import (time, run number, file name) and creates drafts only after you confirm.
3. A draft is created only when all of these hold; otherwise the product is marked "待處理" with reasons:
   - the database has the product's terms and trade rows;
   - the product folder holds `<TDCC>_TS.pdf` and `<TDCC>_IIS.pdf` (return-to-folders only writes standard names for checked documents; `_待確認` files do not count);
   - every FC on the trade rows matches the mailbox.
4. Mail: one per product; recipients are the FCs (PB skipped; PB-only products are "不需寄送"), CC is everyone with `fixed_cc` in the mailbox; subject `[中文TS/IIS] [FCN] - <TDCC>`; the body uses the Excel template configured by `.env` `SPW_MAIL_TEMPLATE`, fills it, and pastes editable tables into Outlook; both PDFs are attached.
   Table content is centered; English uses Calibri and Chinese uses Microsoft JhengHei; table headers are limited to two lines. Fixed CCs are ordered MAX, BOB, then PM, followed by other people.
5. The result list `寄信草稿清單_<timestamp>.xlsx` records each product's status, recipients, CCs, and reasons; its "資料來源" sheet records which daily import was used (time, run number, file name, SHA-256) and any folders that could not be read.

Non-FCN products skipped by the daily import are marked "不支援的商品類型". Running a product again creates another draft; delete duplicates yourself. An error on one product (for example Outlook not responding or bad data) marks only that product "建立失敗"; the others continue. Product folders are read only. Run `spw-db-migrate` once after updating (a schema version was added).

### Order sheet (起單資料)

Reads the operators' daily order sheet `起單建檔_<date>.xlsm`; operators confirm and edit each product in the window, then import it into the database ([#97](https://github.com/TaylorYam/structured-product-workflow/issues/97), [ADR 0010](docs/adr/0010-order-terms-from-daily-order-sheet.md); the confirmation screen is from [#86](https://github.com/TaylorYam/structured-product-workflow/issues/86) and [ADR 0009](docs/adr/0009-confirm-order-terms-in-window.md)). `(Order)` emails are no longer read, and no Excel is exported.

1. In the window, open "2 起單資料", paste TDCCs (same separators as preparation; right after a daily import the trade sheet's FCN TDCCs are already filled in, and 改選… on the hint bar clears them so you can paste your own), click 選擇… next to 起單建檔 to pick the day's file (the first time it opens `G:\Bond Data\PM事項\0.常用工具\ELN每日起單建檔`, later the folder last used), and click "讀取". This page does not use the root folder.
2. Only the chosen file is read, and it is never changed. The sheet has one worksheet; the row among the first few that contains `TDCC` is the header (currently row 2 from column B), and columns match by header text, ignoring case and position. Columns read: Product, Currency, Guaranteed Periods (m), BBG Code 1–5, Strike (%), KO Type, KO Barrier (%), Coupon p.a. (%), `UF (%)` (older header `Upfront / NotePrice (%)`), Tenor (m), Barrier Type, KI Barrier (%), the four dates, and 期初價格. Other columns (No., Observation Frequency, OTC, Funding Spread, Effective Date Offset, 起單FC) have no database field and are not read.
3. The `UF (%)` column holds the price (for example 97.14) and is shown as Upfront / NotePrice (%); the database gets UF = 100 − price.
   Strike date (the initial pricing date, [#101](https://github.com/TaylorYam/structured-product-workflow/issues/101), [ADR 0011](docs/adr/0011-strike-date.md)) is not a sheet column: when 期初價格 says 明日開盤價 (next day's open), 期初價格 becomes 開盤價 and Strike date is the underlyings' next business day after the trade date (from `config/exchange_holidays.toml`; a Friday trade gives Monday, and holidays push it further); otherwise Strike date equals Trade date. When the holiday calendar cannot tell (an exchange not in the calendar, or a date outside its range) Strike date stays blank and red for the operator (never filled from the database); Strike date may not be earlier than Trade date. Editing Trade date on screen moves a Strike date that equalled it; a 明日開盤價 Strike date stays put.
4. Rule-based, never guessing:
   - KO Type must be Daily, Daily Memory, Period End, or Period End Memory; Barrier Type NONE, EKI, or AKI; 期初價格 開盤價, 收盤價, or VWAP; case is ignored (`DAILY MEMORY` and `None` are accepted).
   - Unknown values (for example `MKI`, a number as KO Type, or 期初價格 written as 待確認), non-numbers in number columns, and non-dates in date columns leave the cell blank and red with the value read, for the operator to fix on screen.
   - The issuer comes from the TDCC's first three digits in `config/issuer_prefixes.toml`; an unknown prefix blocks the import.
   - A TDCC with several rows: identical rows are used; differing fields stay blank with each row's value shown and must be filled before importing (if blank is right, fix the sheet and read again).
   - A pasted TDCC missing from the file is 找不到; one that is not 12 digits is 待確認.
   - A file with several worksheets (such as the master `ELN每日起單建檔.xlsm`), no header, missing columns, or a duplicated column stops the whole batch with a reason.
5. After reading, the batch is listed below on the left (problems first) with an editor on the right:
   - Status: "待確認" means a field blocks the import (a required blank, a bad format, and so on); "請確認" means reading the sheet raised a reminder but the product can be imported; "可匯入"; "已鎖住" means the check passed or the product was already in the database at migration 0006, so it cannot be edited or imported (use the term correction flow, #55). Products not found still show their reason.
   - Selecting a product lists the fields above on the right. Light red marks a field that blocks the import or had a reading problem, with the reason underneath; light yellow marks a value that differs from the database, showing the database value with a "用資料庫的值" button to restore it. KO Type, Barrier Type, and initial pricing use drop-downs, and dates are written `2026-10-21`; a field is checked when you leave it or press Enter, and a mistyped one keeps your text and turns red; that product is skipped on import while the rest proceed.
   - With connection settings in `.env` the database is consulted (a missing password is asked once before reading): for products already in the database and not locked (including ones 清單更新 created), fields blank in the sheet take the database value (except fields where duplicate rows differ). Without settings you can still read, with nothing filled from the database; a product read without the database never overwrites one already in it on import (read again first).
6. When done, click "匯入資料庫": products passing the checks are written to the database (denomination from the currency default; final valuation and maturity dates required; re-imports before the check passes overwrite with a record of the sheet file and row numbers and of manual edits against the sheet; blank fields never wipe database values you did not see), and the status column shows 新增/覆蓋/沒有變/不匯入/已鎖住, with the recalculated status in the 核對條件 column (see "Check terms" below). The order amount comes from the trade rows and is not entered here. Run `spw-db-migrate` once after updating to Strike date (schema version 0008; existing products get Strike date = trade date). Reading again, receiving new TDCCs, or closing the window with edits not yet imported asks first; the editor is locked while work is running.

### Check terms

After an import on "1 每日匯入" or "2 起單資料", every **unlocked** product automatically gets the data checking needs but the order terms lack; results are written only when they differ from the last calculation ([#88](https://github.com/TaylorYam/structured-product-workflow/issues/88), [ADR 0012](docs/adr/0012-check-terms-calculation.md)). Locked products (check passed) are never recalculated. Run `spw-db-migrate` once after updating (schema version 0009).

- **Initial, strike, KI, and KO prices** (each underlying UL_1–UL_5): taken from the first price file dated after the Strike date, using the latest close for 收盤價 and the latest open for 開盤價. Strike/KI/KO price = raw price file value × strike/KI/KO % ÷ 100, and each of the four prices is rounded half-up to 4 decimals; the KI price is blank for barrier type NONE. With VWAP initial pricing all four stay blank (backfilled from the TS, #68) without 待確認.
- **Matching the price file**: `config/exchange_holidays.toml` must confirm that the Strike date is a trading day of the underlying's market and that no other trading day of that market lies between it and the price date (a Friday trade with Monday's file is fine; a missed day is not). Exchange codes not in the calendar (only the US and Japan so far, so Hong Kong is 待確認) and dates outside its range are also 待確認.
- **Branch allocation**: the product's trade rows (cancelled rows are not in the database; PB counts) summed per branch in order of appearance replace the previous allocation; mail drafts read it as before.
- **Cross-check**: currency, trade date, and UF between the trade rows and the order terms (trade rows hold a ratio such as 0.0199, the order terms percentage points such as 1.99); differences between trade rows also count. The order amount is not compared.
- **Status**: 完成 (done) / 等股價 (no price file after the Strike date yet) / 等交易明細 (no trade rows yet) / 待確認 with a reason, for example "UL_2 AAA UW：股價檔（取價日 2026-10-16）沒有這個代碼" or "UF不一致：起單資料 1.99、交易明細 0.0215（換算 2.15）". A missing code, missing price, or price conflict blanks only that underlying's prices. Prices or trade rows arriving later are picked up by the next import.
- **Releasing differences**: when a cross-check difference has been reviewed and is acceptable, select the product in the result table of "1 每日匯入" or "2 起單資料" and click "放行差異…". The Windows user name, time, and released differences are recorded and the product is recalculated; the same differences no longer block, while changed data with changed differences needs a new release. Price and calendar problems cannot be released; fix the data (for example import the missing price file).
- Results live in `workflow.check_terms` (status, reason, price file used), `workflow.underlying_price` (the four prices per underlying), and `workflow.branch_allocation`; releases are in `workflow.cross_check_release`. #68 reads these tables when checking. If recalculation fails (for example a broken holiday calendar file) the import is kept and the completion message says why; importing again after the fix recalculates.

Tests: `pytest -q` runs both the workflow and checker (`tests/checker/`) suites in one go (synthetic data only; real Word conversion tests run locally only when Word is available and are skipped in CI). For speed, run `pytest -q --ignore=tests/checker` and then `pytest -q -n auto tests/checker` (the checker suite runs in parallel with pytest-xdist; database tests share one test database and must not run in parallel). `--shard=i/n` runs only part i (each test file stays in one part), for example `pytest -q -n auto tests/checker --shard=2/4`. CI picks tests from the files a PR changes (both old and new paths count when files move): documentation-only changes (`*.md`, `docs/`) run just the whitespace check; workflow-only changes (`src/sp_workflow/`, workflow tests under `tests/`, `scripts/`) skip the checker tests; checker changes (`src/fcn_checker/`, `tests/checker/`, `config/`, `panel_bootstrap.py`, `launch_checker_panel.*`, `scripts/build_panel_icon.py`) run the checker tests in 4 parallel parts; changes to `tests/conftest.py` or `tests/shard.py` run both the workflow and checker tests but not the Windows install resolution; changes to `pyproject.toml`, `constraints.txt`, CI, installer scripts, or anything unlisted run everything. Every push to `main` and every manual CI run runs everything; if the changed files cannot be listed, CI fails rather than passing with tests skipped. The checker's local real-sample tests read `data/`, or set `FCN_TEST_DATA_DIR` to a folder holding `orders/` and `term-sheets/` (layout in `tests/checker/real_data.py`). Database tests need a disposable PostgreSQL database whose name ends in `_test`; put its connection string in `SPW_TEST_DSN` (for example `host=localhost dbname=spw_test user=...`). Without it they are skipped; CI runs them against a PostgreSQL service. The database-source checker tests live in `tests/checker/` marked `db`; run them serially with `pytest -q -m db tests/checker` (not with `-n auto`). The real Outlook draft test runs only when `SPW_OUTLOOK_TEST=1` is set locally (it creates and then deletes one test draft). Real-sample runs are local only: set `SPW_REAL_SAMPLES_DIR` to the folder containing product folders; CI skips it, and real data never enters the repository. Inside the repository, keep it in `FCN商品/`, which `.gitignore` excludes.
