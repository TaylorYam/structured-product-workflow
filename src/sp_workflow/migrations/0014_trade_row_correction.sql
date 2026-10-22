-- 人工維護修改交易明細（#116、ADR 0018）：「7 人工維護」可以改資料庫裡交易明細列的值（不新增、不刪除列）。
-- 每次存檔有改到交易明細時留一筆；只有交易明細、還沒有商品條件的 TDCC 也可以改，所以不參照 product。
-- 同一次存檔也改了商品條件時，商品條件照舊記在 workflow.term_correction。

CREATE TABLE workflow.trade_row_correction (
    correction_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tdcc text NOT NULL CHECK (tdcc ~ '^[0-9]{12}$'),
    corrected_by text NOT NULL,               -- Windows 使用者名稱
    corrected_at timestamptz NOT NULL DEFAULT now(),
    reason text,                              -- 原因（選填）
    changes jsonb NOT NULL,                   -- [[row_seq, 欄, 存檔前, 存檔後]]：欄是交易明細的表頭（例：金額）
    previous jsonb NOT NULL,                  -- 存檔前這檔的交易明細列（依 row_seq，欄名同 workflow.trade_row）
    price_changes jsonb,                      -- [[項目, 存檔前, 存檔後]]：已鎖住的商品重算後改變的價格（只改交易明細時才記在這裡）
    locked boolean NOT NULL                   -- 存檔時商品已鎖住（沒有商品條件時 false）
);
