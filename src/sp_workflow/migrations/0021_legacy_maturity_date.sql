-- 舊清單的到期還本補事件日（#189）：還有效、來源「舊清單」、事件「到期還本」、事件日空白、商品有最終比價日的，
-- 事件日補成商品的最終比價日（和比價算出的到期還本一樣是判定的觀察日），備註註明是這個版本補的。
-- 存續事件平常不能改（product_event_insert_only），只在這個版本的 transaction 裡暫時關掉 trigger，改完再開回來；
-- 商品沒有最終比價日的、其他事件都不動。

ALTER TABLE workflow.product_event DISABLE TRIGGER product_event_insert_only;

UPDATE workflow.product_event e
SET event_date = p.final_valuation_date,
    note = concat_ws('；', e.note, '事件日由資料表版本 0021 補成最終比價日（#189）')
FROM workflow.product p
WHERE p.tdcc = e.tdcc
  AND e.source = '舊清單'
  AND e.event = '到期還本'
  AND e.event_date IS NULL
  AND e.superseded_at IS NULL
  AND p.final_valuation_date IS NOT NULL;

ALTER TABLE workflow.product_event ENABLE TRIGGER product_event_insert_only;
