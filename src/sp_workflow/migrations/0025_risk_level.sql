-- 自算風險等級（#238、ADR 0023）：算核對條件時，照 Generator 試算表的公式，用交易日的 200 日波動率、商品條件與
-- 發行機構評等（0024）算出 RR1～RR5。跟著核對條件重算還沒鎖住的商品，結果有變才寫入；已鎖住的不再重算。
-- 商品條件表 workflow.product 不加欄位（同 ADR 0012）。說明書／投資人須知的 RR 核對與寄信草稿（#239）讀這張表。

-- 每檔一筆：最近一次算出、而且和上一次不同時寫入
CREATE TABLE workflow.risk_level (
    tdcc text PRIMARY KEY REFERENCES workflow.product (tdcc) ON DELETE CASCADE,
    status text NOT NULL CHECK (status IN ('完成', '回填', '待算')),
    risk_level text CHECK (risk_level IN ('RR1', 'RR2', 'RR3', 'RR4', 'RR5')),  -- 待算時空白
    total_score numeric,                      -- 總分；只有完成時有
    reason text,                              -- 回填、待算時缺什麼
    price_date date,                          -- 用的股價檔（取價日 <= 交易日的最新一份）；找不到時空白
    -- 計算明細（完成時才有）：各項分數、各標的的 200 日波動率、平均波動度、用的評等，見 sp_workflow.risk_level
    detail jsonb,
    calculated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT risk_level_status_level CHECK (
        (status = '完成' AND risk_level IS NOT NULL AND total_score IS NOT NULL AND detail IS NOT NULL)
        OR (status = '回填' AND risk_level = 'RR4' AND total_score IS NULL AND detail IS NULL)
        OR (status = '待算' AND risk_level IS NULL AND total_score IS NULL AND detail IS NULL)
    )
);
