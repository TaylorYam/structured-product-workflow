-- 人工維護（#115、ADR 0017）：「7 人工維護」分頁補齊、修改資料庫裡的商品條件。
-- 只有交易明細、還沒有商品條件的 TDCC 可以在這裡從頭建立商品條件，來源記成「人工維護」（沒有匯入批次）；
-- 之後重新匯入起單資料時照舊整檔覆蓋，來源改成 Order。
-- 每次存檔都在 workflow.term_correction 留一筆：已鎖住的商品照更正（ADR 0014），還沒鎖住的也記。

ALTER TABLE workflow.product
    DROP CONSTRAINT product_source_check,
    ADD CONSTRAINT product_source_check CHECK (source IN ('清單更新', 'Order', '人工維護')),
    DROP CONSTRAINT product_source_run,
    ADD CONSTRAINT product_source_run CHECK (
        (source = '清單更新' AND first_run_id IS NOT NULL)
        OR (source = 'Order' AND order_import_id IS NOT NULL)
        OR source = '人工維護'
    );

ALTER TABLE workflow.term_correction
    ALTER COLUMN previous DROP NOT NULL,              -- 新建商品條件時沒有存檔前的商品欄位
    ADD COLUMN locked boolean NOT NULL DEFAULT true;  -- 存檔時商品已鎖住（更正）；false 是還沒鎖住的商品（人工維護）
