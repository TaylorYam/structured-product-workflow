-- 每日股價（#87、ADR 0006「每日股價（新 schema）」、ADR 0008）：每日更新的「BBG_Data」工作表，
-- C1 是取價日，內容是前一個交易日的價格。以「取價日＋代碼」為一筆；同一個取價日重新匯入時整天取代。
-- 「清單更新」停用，每日匯入不再寫商品條件；以前寫入的商品與分公司配置照舊保留。

-- 每次股價匯入一筆，跟著那一次每日匯入（檔名、SHA-256、時間記在 import_run）
CREATE TABLE workflow.price_import (
    run_id bigint PRIMARY KEY REFERENCES workflow.import_run (run_id),
    price_date date NOT NULL,                 -- 取價日（BBG_Data 的 C1）
    price_count integer NOT NULL CHECK (price_count >= 0),
    conflict_count integer NOT NULL CHECK (conflict_count >= 0)
);

CREATE TABLE workflow.daily_price (
    price_date date NOT NULL,
    code text NOT NULL CHECK (code <> ''),    -- BBG 代碼，只去頭尾空白，不改大小寫、不換交易所碼
    company_name text,
    last_close numeric,                       -- 最新收盤價
    last_open numeric,                        -- 最新開盤價
    previous_close numeric,                   -- 1日前收盤價
    volatility_200d numeric,                  -- 200日波動率
    market_cap_millions numeric,              -- 市值（原幣／百萬）
    -- 同一個代碼在檔案裡出現多列、值不同：價格欄留空，各列的值記在 conflict_rows，算價格時（#88）不能用
    conflict_rows jsonb,
    run_id bigint NOT NULL REFERENCES workflow.price_import (run_id),
    PRIMARY KEY (price_date, code),
    CONSTRAINT daily_price_conflict_has_no_values CHECK (
        conflict_rows IS NULL
        OR (last_close IS NULL AND last_open IS NULL AND previous_close IS NULL
            AND volatility_200d IS NULL AND market_cap_millions IS NULL)
    )
);
