-- 存續事件（#152、ADR 0019）：存續狀態 = 最新一筆還有效的存續事件，沒有事件就是在庫。
-- 自動事件（KO、接股、到期還本）只由比價產生，每次比價和現有不同才整組取代：舊的那組記下取代時間、
-- 新增新的一組，差異寫進 workflow.product_event_change；事件列不能改、不能刪，只能記一次取代時間。
-- 人工事件只有顧客贖回；舊清單匯入的狀態轉成來源「舊清單」的事件。
-- 對自動事件有意見只能提出異議（workflow.event_objection），異議不改事件。

CREATE TABLE workflow.product_event (
    event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tdcc text NOT NULL REFERENCES workflow.product (tdcc),
    event text NOT NULL CHECK (event IN ('KO', '接股', '到期還本', '顧客贖回')),
    event_date date,                          -- 觸價或判定的觀察日（不存上手贖回付款日）；只有舊清單可以空白
    underlying text,                          -- 標的（例：接股的最差標的、舊清單的狀態標的）
    source text NOT NULL CHECK (source IN ('比價', '人工', '舊清單')),
    note text,
    recorded_by text NOT NULL,                -- Windows 使用者名稱（比價、舊清單轉換時是執行的人或資料表版本）
    recorded_at timestamptz NOT NULL DEFAULT now(),
    superseded_at timestamptz,                -- 自動事件被下一組取代的時間；空白是還有效的事件
    CHECK (event_date IS NOT NULL OR source = '舊清單'),
    CHECK (source <> '比價' OR event IN ('KO', '接股', '到期還本')),
    CHECK (source <> '人工' OR event = '顧客贖回'),
    CHECK (superseded_at IS NULL OR source = '比價')
);

CREATE INDEX product_event_current ON workflow.product_event (tdcc) WHERE superseded_at IS NULL;

-- 事件列只新增：不能刪除；唯一能改的是比價事件還沒取代時記一次取代時間
CREATE FUNCTION workflow.product_event_insert_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION '存續事件不能刪除（事件 %）', OLD.event_id;
    END IF;
    IF OLD.superseded_at IS NOT NULL OR NEW.superseded_at IS NULL
        OR (to_jsonb(NEW) - 'superseded_at') <> (to_jsonb(OLD) - 'superseded_at') THEN
        RAISE EXCEPTION '存續事件不能修改（事件 %），自動事件只能由比價整組取代', OLD.event_id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER product_event_insert_only
    BEFORE UPDATE OR DELETE ON workflow.product_event
    FOR EACH ROW EXECUTE FUNCTION workflow.product_event_insert_only();

-- 自動事件整組取代的紀錄：[[事件, 事件日, 標的]]，依事件日排序；只新增
CREATE TABLE workflow.product_event_change (
    change_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tdcc text NOT NULL REFERENCES workflow.product (tdcc),
    changed_by text NOT NULL,
    changed_at timestamptz NOT NULL DEFAULT now(),
    previous jsonb NOT NULL,                  -- 取代前的自動事件（沒有時是空陣列）
    current jsonb NOT NULL                    -- 取代後的自動事件（沒有時是空陣列）
);

CREATE FUNCTION workflow.product_event_change_insert_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '存續事件的取代紀錄只新增，不能修改或刪除（紀錄 %）', OLD.change_id;
END;
$$;

CREATE TRIGGER product_event_change_insert_only
    BEFORE UPDATE OR DELETE ON workflow.product_event_change
    FOR EACH ROW EXECUTE FUNCTION workflow.product_event_change_insert_only();

CREATE TABLE workflow.event_objection (
    objection_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id bigint NOT NULL REFERENCES workflow.product_event (event_id),
    note text NOT NULL,
    raised_by text NOT NULL,
    raised_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz,                  -- 解除異議的時間；空白是還沒解除
    resolved_by text,
    CHECK ((resolved_at IS NULL) = (resolved_by IS NULL))
);

-- 異議不能刪除；唯一能改的是還沒解除時記一次解除的人與時間
CREATE FUNCTION workflow.event_objection_resolve_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION '異議不能刪除（異議 %）', OLD.objection_id;
    END IF;
    IF OLD.resolved_at IS NOT NULL OR NEW.resolved_at IS NULL
        OR (to_jsonb(NEW) - 'resolved_at' - 'resolved_by') <> (to_jsonb(OLD) - 'resolved_at' - 'resolved_by') THEN
        RAISE EXCEPTION '異議不能修改（異議 %），只能解除一次', OLD.objection_id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER event_objection_resolve_only
    BEFORE UPDATE OR DELETE ON workflow.event_objection
    FOR EACH ROW EXECUTE FUNCTION workflow.event_objection_resolve_only();

-- 舊清單匯入的狀態轉成事件（ADR 0019）；原欄位保留但程式不再讀寫，之後另行移除
INSERT INTO workflow.product_event (tdcc, event, event_date, underlying, source, note, recorded_by)
SELECT tdcc, status_event, status_date, status_underlying, '舊清單', status_raw, '資料表版本 0016'
FROM workflow.product
WHERE status_event <> '在庫'
ORDER BY tdcc;

ALTER TABLE workflow.product ALTER COLUMN status_event DROP NOT NULL;  -- 新商品不再寫
