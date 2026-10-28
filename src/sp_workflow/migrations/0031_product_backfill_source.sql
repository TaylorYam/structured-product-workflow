-- 回補既有的 FCN 以外商品（#303、ADR 0027）：商品條件多一種來源「歷史回補」（維護選單從參考條件表格式的 Excel 建立，
-- 建立時就鎖住，沒有匯入批次）。既有的商品與來源不動。

ALTER TABLE workflow.product
    DROP CONSTRAINT product_source_check,
    ADD CONSTRAINT product_source_check CHECK (source IN ('清單更新', 'Order', '人工維護', '歷史回補')),
    DROP CONSTRAINT product_source_run,
    ADD CONSTRAINT product_source_run CHECK (
        (source = '清單更新' AND first_run_id IS NOT NULL)
        OR (source = 'Order' AND order_import_id IS NOT NULL)
        OR source IN ('人工維護', '歷史回補')
    );
