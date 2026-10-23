-- 移除 workflow.product 的舊狀態欄（#172）：status_event、status_date、status_underlying、status_raw。
-- 存續狀態改看存續事件（ADR 0019）之後程式不再讀寫這四欄；資料表版本 0016 已把不是「在庫」的舊狀態轉成來源「舊清單」的事件。
-- 刪之前先檢查：每一檔不是在庫的舊狀態都要有同一檔、同一種事件、同一個事件日的舊清單事件；少了就整個版本失敗、什麼都不改，
-- 錯誤訊息寫出那些 TDCC。其他欄與資料不動。

DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(p.tdcc, '、' ORDER BY p.tdcc) INTO missing
    FROM workflow.product p
    WHERE p.status_event <> '在庫'
      AND NOT EXISTS (
          SELECT 1 FROM workflow.product_event e
          WHERE e.tdcc = p.tdcc
            AND e.source = '舊清單'
            AND e.event = p.status_event
            AND e.event_date IS NOT DISTINCT FROM p.status_date
      );
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION '這些商品的舊狀態沒有對應的舊清單事件，舊狀態欄沒有移除：%', missing;
    END IF;
END;
$$;

ALTER TABLE workflow.product
    DROP COLUMN status_event,
    DROP COLUMN status_date,
    DROP COLUMN status_underlying,
    DROP COLUMN status_raw;
