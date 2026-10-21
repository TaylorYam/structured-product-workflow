-- 更正商品條件（#55）：核對通過、條件鎖住之後要改條件時，作業人員在視窗裡改，商品維持鎖住、核對結果不動。
-- 有核對條件的商品，價格與交叉比對由程式重算；改變的價格一起記在這裡。

-- 每次更正一筆
CREATE TABLE workflow.term_correction (
    correction_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tdcc text NOT NULL REFERENCES workflow.product (tdcc) ON DELETE CASCADE,
    corrected_by text NOT NULL,               -- Windows 使用者名稱
    corrected_at timestamptz NOT NULL DEFAULT now(),
    reason text,                              -- 原因（選填）
    changes jsonb NOT NULL,                   -- [[項目, 更正前, 更正後]]：畫面的欄與重算後改變的價格（例：UL_1_執行價）
    previous jsonb NOT NULL                   -- 更正前資料庫的商品欄位
);
