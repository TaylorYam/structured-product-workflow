-- Lookback BEN（#374、ADR 0029）：回顧結束日（只有 BEN 可以填）與每檔標的的回顧期初價格（比價算出、系統寫入）。
-- 既有商品不動。

ALTER TABLE workflow.product
    ADD COLUMN lookback_end_date date,        -- 回顧結束日：有值就是 lookback 商品
    ADD CONSTRAINT product_lookback_end_check CHECK (lookback_end_date IS NULL OR product = 'BEN');

ALTER TABLE workflow.underlying_price
    ADD COLUMN lookback_price numeric;        -- 回顧期初價格 = min(進場價, 回顧觀察期間共同交易日的最低收盤價)
