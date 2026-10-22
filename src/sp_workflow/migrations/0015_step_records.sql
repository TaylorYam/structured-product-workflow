-- 步驟紀錄（#151、ADR 0019）：「3 準備」「5 放回」「6 寄信草稿」寫 Excel 清單的同時，每檔留一筆；
-- 「已寄出」由作業人員在進度頁登記。只新增、不修改，同一檔重做多一筆，進度取同一步最新的一筆。
-- 準備可以只用 Excel 跑、資料庫還沒有這檔的商品條件，所以不參照 product。

CREATE TABLE workflow.step_record (
    record_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tdcc text NOT NULL CHECK (tdcc ~ '^[0-9]{12}$'),
    step text NOT NULL CHECK (step IN ('已準備', '已放回', '草稿已建立', '已寄出')),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    recorded_by text NOT NULL,                -- Windows 使用者名稱
    result text NOT NULL,                     -- 這一步的結果（例：放回待確認檔時寫出哪一份）
    related_file text                         -- 關聯的清單或檔案（準備清單、放回清單、寄信草稿清單）
);

CREATE INDEX step_record_tdcc ON workflow.step_record (tdcc, step, recorded_at);
