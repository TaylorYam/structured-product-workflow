-- 核對讀資料庫（#68、ADR 0013）：核對從資料庫產生參考條件表；說明書與投資人須知都通過或人工放行後，
-- 把 TS 回填的 ISIN、發行日、比價日（VWAP 商品另含各標的價格）寫回商品資料並鎖住。每份 TS／IIS 保存最新一筆核對結果。

-- 比價日_1～12：回填後每一格都有一筆；value 空白是「不需要填的期別」（參考條件表的空值寫法 -）
CREATE TABLE workflow.autocall_date (
    tdcc text NOT NULL REFERENCES workflow.product (tdcc) ON DELETE CASCADE,
    slot smallint NOT NULL CHECK (slot BETWEEN 1 AND 12),
    value date,
    PRIMARY KEY (tdcc, slot)
);

-- 每份文件（說明書／投資人須知）最新一次儲存的核對結果；重跑與人工放行後再儲存都覆蓋
CREATE TABLE workflow.check_result (
    tdcc text NOT NULL REFERENCES workflow.product (tdcc) ON DELETE CASCADE,
    kind text NOT NULL CHECK (kind IN ('說明書', '投資人須知')),
    file_name text NOT NULL,
    category text NOT NULL,                   -- 通過、人工放行、不一致、需人工覆核、未支援上手、執行錯誤
    status text NOT NULL,                     -- 原判定（PASS／MISMATCH／REVIEW_REQUIRED／ERROR）
    released boolean NOT NULL,
    filled boolean NOT NULL,                  -- 這檔商品這次有回填（說明書與投資人須知都通過或人工放行），兩份都記
    problems jsonb,                           -- 錯訊（同錯誤清單）
    result_file text NOT NULL,                -- 核對結果檔
    checked_by text NOT NULL,                 -- Windows 使用者名稱
    checked_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tdcc, kind)
);

-- 寫回商品資料的每一個改變：原值留在這裡（例：人工放行後單位面額改成 TS 的值、VWAP 價格）
CREATE TABLE workflow.backfill_log (
    log_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tdcc text NOT NULL REFERENCES workflow.product (tdcc) ON DELETE CASCADE,
    item text NOT NULL,                       -- 參考條件表欄名，例：ISIN Code、比價日_3、UL_1_進場價、單位面額
    old_value text,
    new_value text,
    result_file text NOT NULL,
    written_by text NOT NULL,
    written_at timestamptz NOT NULL DEFAULT now()
);
