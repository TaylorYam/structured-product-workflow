-- 拆股登記（#270、ADR 0024）：標的拆股或合股之後，收盤價表前後兩段價格的基準不同；
-- 比價時把生效日在 Strike date 之後、不晚於觀察日的拆股，收盤價乘上「拆股後股數 ÷ 拆股前股數」換回交易時的基準。
-- 從「維護」選單的「拆股登記…」人工維護；每次新增、刪除留一筆紀錄（誰、何時、內容），比照評等維護（0024）。
--
-- 生效日是第一個用新股數交易的日子。拆股前後股數都是 1 表示「確認不是拆股」：那天的大漲大跌是真的，比價不再提醒。

CREATE TABLE workflow.stock_split (
    code text NOT NULL CONSTRAINT stock_split_code CHECK (code = btrim(code) AND code <> ''),  -- 同 close_price.code
    effective_date date NOT NULL,
    shares_before integer NOT NULL CONSTRAINT stock_split_shares_before CHECK (shares_before > 0),
    shares_after integer NOT NULL CONSTRAINT stock_split_shares_after CHECK (shares_after > 0),
    note text,
    recorded_by text NOT NULL,                -- Windows 使用者名稱
    recorded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (code, effective_date)
);

CREATE TABLE workflow.stock_split_log (
    change_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    changed_by text NOT NULL,                 -- Windows 使用者名稱
    changed_at timestamptz NOT NULL DEFAULT now(),
    action text NOT NULL CHECK (action IN ('新增', '刪除')),
    split jsonb NOT NULL                      -- 新增或刪除的那一筆
);

CREATE FUNCTION workflow.stock_split_log_insert_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '拆股登記紀錄只新增，不能修改或刪除（紀錄 %）', OLD.change_id;
END;
$$;

CREATE TRIGGER stock_split_log_insert_only
    BEFORE UPDATE OR DELETE ON workflow.stock_split_log
    FOR EACH ROW EXECUTE FUNCTION workflow.stock_split_log_insert_only();
