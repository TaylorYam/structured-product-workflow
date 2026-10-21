-- Order 條件匯入（#86、ADR 0006、0008、0009）：新商品由作業人員在視窗確認 Order 條件後匯入；
-- ISIN、發行日之後由 TS 回填，所以可以空白。套用這版時已在資料庫的商品（以前清單更新匯入、已建檔）視同鎖住；
-- 之後才由清單更新建立的商品沒有鎖，Order 匯入可以接手。

-- 每次匯入一筆
CREATE TABLE workflow.order_import (
    import_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    imported_at timestamptz NOT NULL DEFAULT now(),
    imported_by text NOT NULL,                -- Windows 使用者名稱
    summary jsonb NOT NULL
);

-- 每次匯入、每檔一筆：可以查到匯入的值、覆蓋前的值與人工修改
CREATE TABLE workflow.order_import_line (
    import_id bigint NOT NULL REFERENCES workflow.order_import (import_id),
    seq integer NOT NULL CHECK (seq >= 1),
    tdcc text NOT NULL,
    result text NOT NULL CHECK (result IN ('新增', '覆蓋', '沒有變', '不匯入', '已鎖住')),
    detail text,
    source_mail text,                         -- Order 原信檔名
    confirmed jsonb,                          -- 確認後的值（格式表欄名 → 值）
    previous jsonb,                           -- 覆蓋前資料庫的值
    edits jsonb,                              -- 人工修改：[[欄位, 信裡的值, 改成]]
    PRIMARY KEY (import_id, seq)
);

ALTER TABLE workflow.product
    ALTER COLUMN isin DROP NOT NULL,
    ALTER COLUMN issue_date DROP NOT NULL,
    ALTER COLUMN first_run_id DROP NOT NULL,
    ADD COLUMN denomination numeric(20, 4),   -- 單位面額：Order 匯入時填幣別預設值
    ADD COLUMN order_amount numeric(20, 4),   -- Order 上的承作金額（只用來和交易明細比對）
    ADD COLUMN source text NOT NULL DEFAULT '清單更新' CHECK (source IN ('清單更新', 'Order')),
    ADD COLUMN order_import_id bigint REFERENCES workflow.order_import (import_id),  -- 最後一次寫入的 Order 匯入
    ADD COLUMN locked_at timestamptz,         -- 鎖住時間：TS 核對通過（#68 設定），或套用這版時已在資料庫
    ADD CONSTRAINT product_source_run CHECK (
        (source = '清單更新' AND first_run_id IS NOT NULL)
        OR (source = 'Order' AND order_import_id IS NOT NULL)
    );

UPDATE workflow.product SET locked_at = now();
