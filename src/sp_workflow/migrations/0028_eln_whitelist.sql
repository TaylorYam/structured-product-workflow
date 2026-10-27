-- 可連結標的白名單（#273）：作業人員大約每月從「維護」選單匯入「ELN可連結標的白名單.xlsm」的總表，每次整份取代。
-- 可以連結的標的只有九個指數的成分股、ADR、ETF；自算風險等級（0025）的標的種類分把這裡的 ETF 算 3 分（#252）。
-- 編號跳過 0027：同時開發的拆股（#270）用了 0027；版本檔只要不重複，跳號照樣依序套用。

-- 目前這一份白名單：每檔一筆
CREATE TABLE workflow.eln_whitelist (
    code text PRIMARY KEY CHECK (btrim(code) <> ''),       -- 白名單寫法，例 NVDA UW、AAAU US、1 HK
    groups text[] NOT NULL CHECK (cardinality(groups) > 0)  -- 所屬：SP、NQ、SOX、R1000、R2000、R3000、NKY、TPX500、HSI、ADR、ETF
);

-- 每次匯入一筆紀錄
CREATE TABLE workflow.eln_whitelist_import (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fetched_on date NOT NULL,                  -- 白名單「說明」B3 的抓取日期
    source_file text NOT NULL,                 -- 匯入的檔名
    codes integer NOT NULL CHECK (codes > 0),  -- 檔數
    etfs integer NOT NULL CHECK (etfs > 0),    -- 其中 ETF 檔數
    added integer NOT NULL CHECK (added >= 0),      -- 比上一份新增幾檔（第一次 = 全部）
    removed integer NOT NULL CHECK (removed >= 0),  -- 比上一份移除幾檔
    imported_by text NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT now()
);
