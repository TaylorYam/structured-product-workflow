-- 每日更新也寫收盤價（#280、ADR 0026）：收盤價匯入紀錄多一種來源「每日更新」。
-- 每日匯入把 BBG_Data 的最新收盤價記到各代碼市場在取價日之前最近的交易日；既有的收盤價與匯入紀錄不動。

ALTER TABLE workflow.close_price_import DROP CONSTRAINT close_price_import_source_check;
ALTER TABLE workflow.close_price_import ADD CONSTRAINT close_price_import_source_check
    CHECK (source IN ('MarketData CSV', '歷史收盤價', '每日更新'));
