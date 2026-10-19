-- 每日匯入略過的非 FCN 商品（#29）：只記 TDCC 與 Product，讓寄信草稿能說明「不支援的商品類型」。

CREATE TABLE workflow.other_product (
    tdcc text PRIMARY KEY CHECK (tdcc ~ '^[0-9]{12}$'),
    product text NOT NULL,
    first_run_id bigint NOT NULL REFERENCES workflow.import_run (run_id)
);
