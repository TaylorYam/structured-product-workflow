# Structured Product Workflow／結構型商品作業流程

## 繁體中文

狀態：「TS／IIS 準備與放回」已完成，以真實樣本複本試跑過，等作業主機公槽實測（[#5](https://github.com/TaylorYam/structured-product-workflow/issues/5)）。「每日匯入」最小 FCN 資料庫見 [#32](https://github.com/TaylorYam/structured-product-workflow/issues/32)；「寄信草稿」見 [#29](https://github.com/TaylorYam/structured-product-workflow/issues/29)；「Order 條件」讀取見 [#40](https://github.com/TaylorYam/structured-product-workflow/issues/40)。其他流程仍在設計階段。

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

**作業視窗**：一個「FCN Workflow」視窗，分頁照每天的順序排：1 Order 條件 → 2 每日匯入 → 3 準備 → 4 核對 → 5 放回 → 6 寄信草稿；切換分頁時各頁內容都保留。商品資料夾所在的根目錄在視窗最上方設一次（按「變更…」），1 Order 條件、3 準備、6 寄信草稿三頁共用；各頁的輸出位置仍各自記住。做完一步，下一頁會先帶入結果，上方藍色提示條寫出帶入了什麼（按「改選…」可以換掉）；完成訊息旁出現「下一步 →」，按了才切頁，也不會自動開始：準備完成 → 「4 核對」帶入這批已準備的 PDF（參考條件表沿用核對頁目前選的；核對頁有還沒存檔的結果時先問）；核對存檔 → 「5 放回」帶入批次資料夾與剛存的核對結果檔。審查通過日期、匯出回報包與使用的設定檔不是每天的步驟，在視窗上方的「維護」選單（[ADR 0007](docs/adr/0007-single-workflow-window.md)）。關閉視窗時，核對頁還在處理或有還沒存檔的核對結果會先提醒。第一次開啟時最大化，之後照上次調整的大小開啟。

**從兩個捷徑的版本更新**：重新執行 `setup_panel.cmd` 後，桌面與專案資料夾只剩「FCN Workflow」一個捷徑，開啟時停在「1 Order 條件」（以前停在核對）。以前建的「TS IIS 準備與放回」捷徑，指向這個專案資料夾的會自動刪掉，指向別的資料夾的不動（[#77](https://github.com/TaylorYam/structured-product-workflow/issues/77)）。

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

規則、範本、支援範圍、參考條件表格式與核對的 ADR 見 [docs/checker/](docs/checker/README.md)（只有中文，裡面的 Issue 編號指原 repo）；新增上手或範本改版依[新增上手流程](docs/checker/issuer-onboarding.md)。

### 每日匯入（最小 FCN 資料庫）

把每天的 FCN 交易與商品條件匯入 PostgreSQL；信箱在資料庫直接維護（[ADR 0004](docs/adr/0004-mailbox-maintained-in-database.md)、[#35](https://github.com/TaylorYam/structured-product-workflow/issues/35)）（[#32](https://github.com/TaylorYam/structured-product-workflow/issues/32)、[ADR 0003](docs/adr/0003-minimal-fcn-database.md)）。寄信草稿之後只讀這個資料庫。

**第一次設定（每台電腦一次）**：
1. 安裝 PostgreSQL，建立資料庫與日常使用的帳號（可用 `scripts/provision_postgres_dev.py`）。
2. 在安裝資料夾（開發時是 repo 根目錄）建立 `.env`，填 `PGHOST`、`PGPORT`、`PGDATABASE`、`PGUSER`；`PGPASSWORD` 可以不填，匯入時會詢問。`.env` 不進 Git。
3. 用有建立權限的帳號建立資料表，並把讀寫權限給 `PGUSER`：

```bash
spw-db-migrate --admin-user postgres
```

之後程式更新、有新的資料表版本時，再執行一次同樣的指令。換電腦（例如從筆電到公司電腦）時不複製資料庫，照上面三步重建，再把每日更新重新匯入。

**每天**：
1. 打開 `每日更新.xlsx`：把前一天的交易整列貼到 `交易明細`（欄位同交易清單月份工作表），這些商品在庫存總表的列整列貼到 `清單更新`（欄位同 `FCN清單.xlsx`）。第 1 列欄名不要動。
2. 視窗切到「2 每日匯入」，選 `每日更新.xlsx`，按「匯入」。
3. 看結果：
   - 「新增商品」「新增交易」：寫入成功。「已存在」：之前匯過，這次沒有寫入。
   - 「內容不同（未覆蓋）」：和資料庫裡的不一樣，資料庫保留第一次的內容。
   - 「擋下」：這一列缺值或寫法不認得（例如庫存狀態、KO／KI 代碼），修正 Excel 後重新匯入。
   - 「分公司對帳差異」：交易明細各分公司金額加總和庫存總表的承作金額不同（PB 也算），只提醒。
   - 「庫存尚未更新」：交易已匯入、清單更新還沒有這檔；之後貼進來再匯入一次即可。
   - 「交易已取消」：交易明細的「備註」去頭尾空白、不分大小寫等於 `Cancel`，或備註包含「取消」；該列不會匯入資料庫。
   - 「名字對不到信箱」：交易明細的 FC 名字在 `mailbox.person` 找不到（大小寫不同視為同一人）；拼法不同請在 `mailbox.alias` 加一筆別名。
   - 「信箱尚未建立」：`mailbox.person` 還是空的，名字無法比對。

只收 FCN，其他商品略過並計數；備註為 `Cancel` 或包含「取消」的交易列不會匯入資料庫；同一 TDCC、商品條件相同且分公司不重複的多列庫存資料會合併；同一份重複匯入不會重複寫入；任何錯誤都不會留下一半的資料。所有匯入問題會集中重建到來源檔同資料夾的 `每日匯入錯誤.xlsx`，包含既有匯入歷史；因此 9 月、10 月會在同一份檔案。交易列是否取消以備註欄為準，不讀取 Excel 底色。已存在商品的庫存狀態這一版不更新。每日匯入不會修改信箱。

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
4. 信件：一檔一封；收件人是承作的 FC（略過 PB，只有 PB 時標「不需寄送」），副本是信箱裡 `fixed_cc` 的人；主旨 `FCN <TDCC> 中文TS/IIS`；內文使用 `.env` 的 `SPW_MAIL_TEMPLATE` Excel 範本，填入資料後以可編輯表格貼進 Outlook；附上兩份 PDF。
   表格內容置中；英文字型使用 Calibri、中文字型使用 Microsoft JhengHei；表頭最多兩行。固定副本排序為 MAX、BOB、PM，再接其他人員。
5. 結果清單 `寄信草稿清單_<日期時間>.xlsx` 列出每檔的狀態、收件人、副本與原因；「資料來源」工作表記錄用的是哪一次每日匯入（時間、編號、檔名、SHA-256）與搜尋時讀不到的資料夾。

每日匯入略過的非 FCN 商品會標「不支援的商品類型」。同一檔重跑會再建一封草稿，重複的請自己刪除。某一檔出錯（例如 Outlook 沒回應、資料有問題）只標那一檔「建立失敗」，其他檔照常處理。商品資料夾只讀不改。更新程式後請執行一次 `spw-db-migrate`（新增了資料表版本）。

### Order 條件

從商品資料夾裡我方寄出的 `(Order)` 原信，依各上手格式讀出全部條件，統一欄名後輸出 `Order條件清單_<日期時間>.xlsx`，給人確認（[#40](https://github.com/TaylorYam/structured-product-workflow/issues/40)）。這一版只輸出清單，不寫資料庫、不回填其他 Excel；回填到哪一份另外討論。

1. 視窗切到「1 Order 條件」，貼上 TDCC（分隔方式同「3 準備」），選輸出位置（根目錄用視窗最上方的），按「讀取 Order」。
2. 程式在商品資料夾（不含子資料夾）用傳統版 Outlook 開每個 `.msg`，只讀主旨含 `(Order)` 或 `[Order]`、而且前面沒有 `RE:`、`FW:`、`DONE:` 的原信；條件不讀上手回覆信。唯一例外是 Order 沒寫的日期：GS 到同資料夾主旨有「Indicative Docs for <TDCC>」的信的 Order Summary 表補，HSBC 到 Order 信下方引用的 HSBC 回覆（「TDCC code: <TDCC>」後面的表，日/月/年；寫「TDCC: A / B」而表只有一欄值時兩檔共用）補；只補交易日、發行日、最終比價日、到期日，和 Order 寫的不同時標待確認。信件只開來讀，不會修改或寄出。
3. 讀法：
   - 開頭的 `We buy <TDCC> <幣別> <金額> @<價格>`、`Please settle thru …`、`Risk level`、`Initial price`、`PG rate`、`Capital size` 各家共用。
   - 條件表依 TDCC 前三碼的上手對照欄名（`src/sp_workflow/order_formats.py`），例如 `BBG Code 1`、`Underlying 1`、`Stock 1` 都是「標的1」。UBS 寫在 Quote Id 裡的日期、GS 寫在 System Remark 裡的發行日會拆出來。
   - 信件下方引用的上一封信不讀。
4. 統一格式：百分比存數字（`80.00`、`75%` 都是 80），金額換算成數字（`200K` → 200000），日期為 `YYYY-MM-DD`，TDCC 保留前導零。不屬於統一欄位的欄原樣放在「其他欄位」。
5. 一封信下兩檔以上時拆成多列；只貼了其中一檔，同一封信的其他檔也會接在後面。每段屬於哪檔，看表上的 TDCC 欄、段落前的「1. <TDCC>」「TDCC = <TDCC>」「TDCC <TDCC>」，或段落裡只寫一檔的 We buy 行；只寫「1.」「2.」時依 We buy 行的 TDCC 順序對應，但段數和每段的條件表都要剛好對得上，對不上就標待確認由人工回填。
原則：不過度客製，格式明顯偏離常態的信（例如一封信下多檔、又沒寫清楚哪段是哪檔）接受人工回填。
6. 下面情況標「待確認」並寫原因，不猜值：
   - 值有修改，例如 `96.20%→96.15%`、`99.65->99.62`、`99.92 >> 99.89`、`97.50>97.47`：取最後的值，同時寫出原文。
   - 同一欄兩處寫法不同，例如 We buy 行的價格和條件表不同：這一欄留空。
   - 讀不到幣別、承作金額、價格、標的1，或格式不認得。
   - 條件表欄數和值數對不上、有好幾列值，或出現這家沒列過的欄名（上手格式可能改版）。
   - 資料夾裡有兩封以上 Order 原信，或有信讀不到。
7. 找不到商品資料夾或 Order 原信時標「找不到 Order」。14 家上手（含 Citi）都有讀法；TDCC 前三碼不認得時標「未支援上手」。Citi 的 We buy 行只有 `UF=x%`，價格 = 100 − UF；Citi 的 Non Callable Periods 算法不同，統一 +1（0 → 1）。
8. 清單有四個工作表：
   - 「example」：作業人員既有的「Outlook orders 轉成 Excel」格式（第 2 列表頭、從 B 欄開始），讀到 Order 的商品都放（含待確認），**有問題的格子照填能確定的值、標淺紅底**（例如價格寫成 `→` 的那格；整列的問題標在 TDCC 那格），原因看「Order條件清單」；可以直接複製貼上；起單FC 留空由人補。信裡沒直接寫的欄依固定規則換算（`src/sp_workflow/order_template.py`）：
     - KO Type 統一成 Daily／Daily Memory／Period End／Period End Memory；MS 看 KO Memory 欄決定是否記憶式（`OBSERVATION` 是每期），Citi 看 Daily KO 和 Memory Autocall 兩欄。
     - Barrier Type 統一成 NONE／EKI／AKI（`European` = EKI）；沒有 KI 時 KI Barrier 留空。
     - Product 條件表沒有或是上手自家名稱（Citi 的 FCA）時取主旨的 `FCN`；OTC 沒寫時填 `Note`；SG 的 Tenor = No. of Periods。
     - Trade date 條件表沒有時用 Order 寄出日；Issue date 只用信裡寫的，不從偏移天數推算（實測會差一天），讀不到就留空標紅；Effective Date Offset = Issue date − Trade date 的日曆天數。
     - 換算不了或格式表要的欄讀不到時，那一格空白、淺紅底，整檔標「待確認」。
   - 「Order條件清單」：每檔一列，含待確認原因。
   - 「原文對照」：每個欄位的原始欄名、原始值與對到的統一欄位。
   - 「資料來源」：根目錄與讀不到的資料夾。

**匯出問題信件**（[#42](https://github.com/TaylorYam/structured-product-workflow/issues/42)）：讀取完成、有不是「已讀取」的商品時，按「匯出問題信件」，會在輸出位置新開 `Order問題信件_<日期時間>/` 資料夾：
- 每個有問題商品的商品資料夾（不含子資料夾）裡全部 `.msg` 複製到同名子資料夾，同一個資料夾只複製一次。
- 一併附上這次的條件清單，可以看待確認原因。
- 只複製，不改、不刪商品資料夾；個別檔案複製失敗不中斷，完成訊息會列出來。找不到商品資料夾的商品沒有信件可複製。
- **信件含真實交易與客戶資料，帶出公司請依公司的資料規定；不要放進 repo、Issue 或 PR。**

新增上手或上手改版：在 `order_formats.py` 的 `ISSUERS` 加上或修改那家條件表的欄名，再用真實樣本試跑。

測試：`pytest -q` 一次跑完流程與核對（`tests/checker/`）兩組測試（資料全部虛構；真的 Word 轉檔測試只在本機有 Word 時執行，CI 自動略過）。要快一點可以分兩次：`pytest -q --ignore=tests/checker`，再 `pytest -q -n auto tests/checker`（核對測試用 pytest-xdist 平行跑；資料庫測試共用同一個測試資料庫，不能平行跑）。`--shard=i/n` 只跑第 i 份（同一個測試檔在同一份），例如 `pytest -q -n auto tests/checker --shard=2/4`。CI 依 PR 改到的檔案決定要跑哪些測試（搬檔時新舊路徑都算）：只改文件（`*.md`、`docs/`）就只做空白檢查；只改流程（`src/sp_workflow/`、`tests/` 的流程測試、`scripts/`）就不跑核對測試；改到核對（`src/fcn_checker/`、`tests/checker/`、`config/`、`panel_bootstrap.py`、`launch_checker_panel.*`、`scripts/build_panel_icon.py`）就把核對測試分 4 份同時跑；改 `tests/conftest.py`、`tests/shard.py` 會跑流程與核對測試，但不跑 Windows 安裝解析；改到 `pyproject.toml`、`constraints.txt`、CI、安裝腳本或其他沒列到的檔案就全部跑。合併進 `main` 或手動執行 CI 時一律全部跑；讀不到改動的檔案時 CI 會變紅，不會略過測試還顯示通過。核對的本機真實樣本測試讀 `data/`，或把環境變數 `FCN_TEST_DATA_DIR` 設成放 `orders/`、`term-sheets/` 的資料夾（結構見 `tests/checker/real_data.py`）。資料庫測試需要一個名稱以 `_test` 結尾、可以清空的 PostgreSQL 資料庫，把連線字串設在環境變數 `SPW_TEST_DSN`（例如 `host=localhost dbname=spw_test user=...`）；沒設定時略過，CI 用 PostgreSQL 服務執行。真的 Outlook 建草稿測試只在本機設定 `SPW_OUTLOOK_TEST=1` 時執行（會在草稿匣建一封測試信再刪掉）。真實樣本試跑只在本機執行：把環境變數 `SPW_REAL_SAMPLES_DIR` 設成放商品資料夾的目錄（Order 條件試跑會用傳統版 Outlook 開信），CI 自動略過；真實資料不進 repo，放在 repo 裡時用 `.gitignore` 已排除的 `FCN商品/`。Order 條件對照 10 月清單的回歸測試另外設 `SPW_ORDER_CHECK_DIR`（私人樣本倉庫，底下有 `Order問題信件_*` 回報包與 `10月清單(用來核對order專案).xlsx`），格子沒標淺紅底卻和清單不同（含已讀取列漏值）時失敗並列出 TDCC、欄位與兩邊的值。

## English

Status: TS/IIS preparation and return is complete and trial-run on copies of real samples; operational shared-drive testing is pending ([#5](https://github.com/TaylorYam/structured-product-workflow/issues/5)). The minimal FCN database with daily import is [#32](https://github.com/TaylorYam/structured-product-workflow/issues/32); mail drafts are [#29](https://github.com/TaylorYam/structured-product-workflow/issues/29); Order term reading is [#40](https://github.com/TaylorYam/structured-product-workflow/issues/40). Other workflows remain in design.

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

**Working window**: one "FCN Workflow" window with tabs in the daily order: 1 Order 條件 (Order terms) → 2 每日匯入 (daily import) → 3 準備 (prepare) → 4 核對 (check) → 5 放回 (return) → 6 寄信草稿 (mail drafts); each tab keeps its contents when you switch. The root folder holding the product folders is set once at the top of the window (press 變更…) and shared by 1 Order 條件, 3 準備, and 6 寄信草稿; each tab still remembers its own output location. After a step finishes, the next tab is filled in first and a blue hint bar says what was brought over (press 改選… to replace it); a 下一步 → (next step) button appears beside the completion message and switches tabs only when pressed, and nothing starts automatically: after preparation, 4 核對 receives the batch's prepared PDFs (keeping the reference workbook already chosen there, and asking first if an unsaved check result would be cleared); after saving a check, 5 放回 receives the batch folder and the result workbook just saved. Approval dates, report bundle export, and the configuration paths in use are not daily steps and live in the 「維護」 (maintenance) menu at the top ([ADR 0007](docs/adr/0007-single-workflow-window.md)). Closing the window warns while the check tab is still working or a check result has not been saved. The window opens maximized the first time and at the last adjusted size afterwards.

**Updating from the two-shortcut version**: after rerunning `setup_panel.cmd`, the desktop and the project folder have only the "FCN Workflow" shortcut, which now opens on 1 Order 條件 (previously on the check tab). An old "TS IIS 準備與放回" shortcut is removed automatically when it points to this project folder; one pointing to another folder is left alone ([#77](https://github.com/TaylorYam/structured-product-workflow/issues/77)).

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

Rules, templates, support scope, the reference workbook format, and the checker's ADRs are in [docs/checker/](docs/checker/README.md) (Chinese only; issue numbers there refer to the original repository). For a new issuer or template version, follow the [issuer onboarding flow](docs/checker/issuer-onboarding.md).

### Daily import (minimal FCN database)

Imports each day's FCN trades and product terms into PostgreSQL; the mailbox is maintained directly in the database ([ADR 0004](docs/adr/0004-mailbox-maintained-in-database.md), [#35](https://github.com/TaylorYam/structured-product-workflow/issues/35)) ([#32](https://github.com/TaylorYam/structured-product-workflow/issues/32), [ADR 0003](docs/adr/0003-minimal-fcn-database.md)). Mail drafts will read only this database.

**First-time setup (once per computer)**:
1. Install PostgreSQL and create the database and the daily account (`scripts/provision_postgres_dev.py` can help).
2. Create `.env` in the install folder (the repository root during development) with `PGHOST`, `PGPORT`, `PGDATABASE`, and `PGUSER`; `PGPASSWORD` is optional and is asked for at import time. `.env` never enters Git.
3. Create the tables with an account that can create schemas, granting read/write to `PGUSER`:

```bash
spw-db-migrate --admin-user postgres
```

Run the same command again after updates that add schema versions. When moving to another computer (for example from the laptop to the company computer), do not copy the database: repeat the three steps and re-import the daily updates.

**Daily**:
1. Open `每日更新.xlsx`: paste yesterday's trade rows whole into `交易明細` (same columns as the trade-list monthly sheets) and those products' inventory rows whole into `清單更新` (same columns as `FCN清單.xlsx`). Do not change the header row.
2. In the window, open "2 每日匯入", choose `每日更新.xlsx`, and click "匯入".
3. Review the results:
   - "新增商品"/"新增交易": written. "已存在": imported before; nothing written this time.
   - "內容不同（未覆蓋）": differs from the database, which keeps the first version.
   - "擋下": a row is missing values or uses an unrecognized form (for example inventory status or KO/KI codes); fix the workbook and re-import.
   - "分公司對帳差異": per-branch trade totals differ from the inventory allocation (PB included); warning only.
   - "庫存尚未更新": trades are imported but `清單更新` lacks the product; paste it later and import again.
   - "交易已取消": the trade-row 備註, after trimming and case-folding, is `Cancel`, or it contains `取消`; the row is not imported into the database.
   - "名字對不到信箱": an FC name is missing from `mailbox.person` (case is ignored); for spelling variants add an alias to `mailbox.alias`.
   - "信箱尚未建立": `mailbox.person` is empty, so names cannot be matched.

Only FCN is imported; other products are skipped and counted. Trade rows whose 備註 is `Cancel` or contains `取消` are not imported. Inventory rows for the same TDCC with identical product terms and disjoint branches are merged. Re-importing the same file writes nothing new, and any error leaves no partial data. All import problems are rebuilt into one `每日匯入錯誤.xlsx` beside the source workbook, including prior import history, so September and October appear in the same workbook. The note field is authoritative for cancellation; Excel fill colors are not inspected. Inventory status of existing products is not refreshed in this version. The daily import never changes the mailbox.

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
4. Mail: one per product; recipients are the FCs (PB skipped; PB-only products are "不需寄送"), CC is everyone with `fixed_cc` in the mailbox; subject `FCN <TDCC> 中文TS/IIS`; the body uses the Excel template configured by `.env` `SPW_MAIL_TEMPLATE`, fills it, and pastes editable tables into Outlook; both PDFs are attached.
   Table content is centered; English uses Calibri and Chinese uses Microsoft JhengHei; table headers are limited to two lines. Fixed CCs are ordered MAX, BOB, then PM, followed by other people.
5. The result list `寄信草稿清單_<timestamp>.xlsx` records each product's status, recipients, CCs, and reasons; its "資料來源" sheet records which daily import was used (time, run number, file name, SHA-256) and any folders that could not be read.

Non-FCN products skipped by the daily import are marked "不支援的商品類型". Running a product again creates another draft; delete duplicates yourself. An error on one product (for example Outlook not responding or bad data) marks only that product "建立失敗"; the others continue. Product folders are read only. Run `spw-db-migrate` once after updating (a schema version was added).

### Order terms

Reads every term from our original `(Order)` email in each product folder using each issuer's format, normalizes the field names, and writes `Order條件清單_<timestamp>.xlsx` for review ([#40](https://github.com/TaylorYam/structured-product-workflow/issues/40)). This version only writes the list; it does not touch the database or fill any other workbook. Where the terms are written back is decided separately.

1. In the window, open "1 Order 條件", paste TDCCs (same separators as preparation), choose the output location (the root folder is the one at the top of the window), and click "讀取 Order".
2. Each `.msg` directly inside the product folder is opened with classic Outlook. Only originals are read: the subject contains `(Order)` or `[Order]` with no `RE:`, `FW:`, or `DONE:` before it. Terms never come from issuer replies, with one exception for dates the order omits: GS dates come from the Order Summary table in the same folder's "Indicative Docs for <TDCC>" email, and HSBC dates come from HSBC's reply quoted below the order (the table after "TDCC code: <TDCC>", day/month/year; "TDCC: A / B" with a single value column means both share the dates). Only trade, issue, final valuation, and maturity dates are filled this way, and a date differing from the order is 待確認. Messages are opened read-only, never changed or sent.
3. Reading:
   - The opening `We buy <TDCC> <currency> <amount> @<price>`, `Please settle thru …`, `Risk level`, `Initial price`, `PG rate`, and `Capital size` lines are shared by all issuers.
   - The terms table is mapped by the issuer from the TDCC's first three digits (`src/sp_workflow/order_formats.py`); for example `BBG Code 1`, `Underlying 1`, and `Stock 1` all become 標的1. Dates inside the UBS Quote Id and the GS System Remark issue date are split out.
   - Quoted earlier messages below the order are ignored.
4. Normalization: percentages become numbers (`80.00` and `75%` are both 80), amounts are expanded (`200K` → 200000), dates are `YYYY-MM-DD`, and TDCCs keep leading zeros. Columns outside the normalized fields are kept as-is in 其他欄位.
5. A message ordering several TDCCs is split into rows; when only one of them is pasted, the others from the same message follow it. Each section belongs to the TDCC in the table's TDCC column, in a preceding "1. <TDCC>", "TDCC = <TDCC>", or "TDCC <TDCC>" line, or in a We buy line inside the section naming one TDCC; bare "1." and "2." follow the TDCC order of the We buy line only when the section count and one table per section match exactly, otherwise they are left 待確認 for manual entry.
Principle: avoid over-customizing; emails that stray far from the normal format (for example several TDCCs without saying which section is which) are filled in manually.
6. The following cases are marked 待確認 with reasons, without guessing:
   - A revised value such as `96.20%→96.15%`, `99.65->99.62`, `99.92 >> 99.89`, or `97.50>97.47`: the last value is used and the original text is shown.
   - A field that differs between two places, for example the We buy price and the table: that field is left blank.
   - Currency, amount, price, or 標的1 cannot be read, or a value's format is not recognized.
   - Header and value counts differ, a table has several value rows, or a column unknown for that issuer appears (the issuer format may have changed).
   - A folder holds two or more original orders, or a message cannot be read.
7. A missing product folder or original order is 找不到 Order. All 14 issuers (including Citi) have a reading; an unknown TDCC prefix is 未支援上手. Citi's We buy line only gives `UF=x%`, so the price is 100 − UF; Citi counts Non Callable Periods differently, so it is shifted by +1 (0 → 1).
8. The workbook has four sheets:
   - "example": the operators' existing "Outlook orders to Excel" layout (row-2 header starting in column B), holding every product whose order was read (including 待確認), **with problem cells filled with what is certain and shaded light red** (for example the price written with `→`; row-level problems mark the TDCC cell), reasons in "Order條件清單"; ready to copy and paste; 起單FC is left blank for a person to fill. Columns the email does not state directly follow fixed rules (`src/sp_workflow/order_template.py`):
     - KO Type becomes Daily, Daily Memory, Period End, or Period End Memory; MS uses its KO Memory column for memory (`OBSERVATION` means per period), and Citi uses its Daily KO and Memory Autocall columns.
     - Barrier Type becomes NONE, EKI, or AKI (`European` = EKI); without KI, KI Barrier is blank.
     - Product falls back to the subject's `FCN` when the table lacks it or uses the issuer's own name (Citi's FCA); OTC defaults to `Note`; SG Tenor = No. of Periods.
     - Trade date falls back to the order's sent date; Issue date is never inferred from the offset (it was a day off in testing) and stays blank and red when missing; Effective Date Offset = Issue date − Trade date in calendar days.
     - When a value cannot be converted or a required column is missing, that cell is blank and light red, and the product is 待確認.
   - "Order條件清單": one row per product, with reasons to check.
   - "原文對照": each field's original name, original value, and normalized field.
   - "資料來源": the root folder and any folders that could not be read.

**Export problem mails** ([#42](https://github.com/TaylorYam/structured-product-workflow/issues/42)): after reading, when any product is not 已讀取, click "匯出問題信件" to create `Order問題信件_<timestamp>/` in the output location:
- Every `.msg` directly inside each problem product's folder is copied into a same-named subfolder; a folder is copied only once.
- This run's list is included so the reasons are at hand.
- Files are copied only; product folders are never changed or deleted. A failed copy does not stop the rest and is listed in the completion message. Products without a product folder have no mail to copy.
- **The mails contain real trade and customer data. Follow company data rules before taking them out, and never put them in the repository, Issues, or PRs.**

New issuers or format changes: add or update that issuer's table headers in `ISSUERS` in `order_formats.py`, then run a real-sample trial.

Tests: `pytest -q` runs both the workflow and checker (`tests/checker/`) suites in one go (synthetic data only; real Word conversion tests run locally only when Word is available and are skipped in CI). For speed, run `pytest -q --ignore=tests/checker` and then `pytest -q -n auto tests/checker` (the checker suite runs in parallel with pytest-xdist; database tests share one test database and must not run in parallel). `--shard=i/n` runs only part i (each test file stays in one part), for example `pytest -q -n auto tests/checker --shard=2/4`. CI picks tests from the files a PR changes (both old and new paths count when files move): documentation-only changes (`*.md`, `docs/`) run just the whitespace check; workflow-only changes (`src/sp_workflow/`, workflow tests under `tests/`, `scripts/`) skip the checker tests; checker changes (`src/fcn_checker/`, `tests/checker/`, `config/`, `panel_bootstrap.py`, `launch_checker_panel.*`, `scripts/build_panel_icon.py`) run the checker tests in 4 parallel parts; changes to `tests/conftest.py` or `tests/shard.py` run both the workflow and checker tests but not the Windows install resolution; changes to `pyproject.toml`, `constraints.txt`, CI, installer scripts, or anything unlisted run everything. Every push to `main` and every manual CI run runs everything; if the changed files cannot be listed, CI fails rather than passing with tests skipped. The checker's local real-sample tests read `data/`, or set `FCN_TEST_DATA_DIR` to a folder holding `orders/` and `term-sheets/` (layout in `tests/checker/real_data.py`). Database tests need a disposable PostgreSQL database whose name ends in `_test`; put its connection string in `SPW_TEST_DSN` (for example `host=localhost dbname=spw_test user=...`). Without it they are skipped; CI runs them against a PostgreSQL service. The real Outlook draft test runs only when `SPW_OUTLOOK_TEST=1` is set locally (it creates and then deletes one test draft). Real-sample runs are local only: set `SPW_REAL_SAMPLES_DIR` to the folder containing product folders (the Order terms trial opens messages with classic Outlook); CI skips it, and real data never enters the repository. Inside the repository, keep it in `FCN商品/`, which `.gitignore` excludes. The Order terms regression check against the October list uses `SPW_ORDER_CHECK_DIR` (the private samples repository holding `Order問題信件_*` report packs and `10月清單(用來核對order專案).xlsx`); it fails when a cell is not highlighted yet differs from the list (including a value missing from a read row), naming the TDCC, column, and both values.
