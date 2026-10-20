-- 交易明細備註為 Cancel 的列不保留在資料庫。

DELETE FROM workflow.trade_row
WHERE lower(btrim(note)) = 'cancel';
