-- Strike date（期初定價日，#101、ADR 0011）：起單建檔的期初價格寫「明日開盤價」時，
-- 期初價格記成開盤價、Strike date = 交易日之後標的市場的下一個營業日；其他情況等於交易日。
-- 已有的商品沒有明日開盤價的資料，補成交易日。

ALTER TABLE workflow.product
    ADD COLUMN strike_date date;            -- 期初定價日：取進場價的那一天

UPDATE workflow.product SET strike_date = trade_date WHERE strike_date IS NULL;
