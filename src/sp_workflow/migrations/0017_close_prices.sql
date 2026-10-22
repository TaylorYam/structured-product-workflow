-- 收盤價（#153、ADR 0020）：以「實際收盤日＋代碼」為鍵，給比價用；workflow.daily_price（取價日）不改。
-- 來源：每天的 MarketData CSV（「1 每日匯入」的回補股價同時寫）與一次匯入的歷史收盤價（HP 格式）。
-- 代碼去掉彭博最後的類別字（Equity、Curncy…），和 workflow.product.underlying_n 的寫法一致；
-- 只存該代碼市場的交易日（週末、假日表上的休市日不存）。同一（收盤日、代碼）再匯入整筆取代，
-- 值改了的舊值記在 workflow.close_price_change。

CREATE TABLE workflow.close_price_import (
    import_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source text NOT NULL CHECK (source IN ('MarketData CSV', '歷史收盤價')),
    file_name text NOT NULL,
    file_sha256 text NOT NULL,
    run_id bigint REFERENCES workflow.import_run (run_id),  -- MarketData CSV：同一次回補股價
    imported_by text NOT NULL,                -- Windows 使用者名稱
    imported_at timestamptz NOT NULL DEFAULT now(),
    summary jsonb NOT NULL                    -- 日期範圍、代碼數、存了幾筆、取代、休市不存、沒有價格的格數
);

CREATE TABLE workflow.close_price (
    close_date date NOT NULL,                 -- 實際收盤日
    code text NOT NULL CHECK (code <> ''),    -- 去掉類別字的彭博代碼（例：AAA UW、7203 JT）
    close numeric NOT NULL CHECK (close > 0),
    import_id bigint NOT NULL REFERENCES workflow.close_price_import (import_id),
    PRIMARY KEY (close_date, code)
);

CREATE INDEX close_price_code ON workflow.close_price (code, close_date);

CREATE TABLE workflow.close_price_change (
    change_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    close_date date NOT NULL,
    code text NOT NULL,
    previous_close numeric NOT NULL,
    previous_import_id bigint NOT NULL REFERENCES workflow.close_price_import (import_id),
    import_id bigint NOT NULL REFERENCES workflow.close_price_import (import_id)  -- 取代它的那一次匯入
);
