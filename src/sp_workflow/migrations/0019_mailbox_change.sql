-- 信箱維護畫面（#158）：在視窗裡新增、修改、刪除人員與別名，不再要求作業人員寫 SQL。
-- 每次存檔留一筆：誰、何時、哪一種（人員／別名）、改前改後；比照人工維護的紀錄（workflow.term_correction）。
-- 刪除人員時資料庫連帶刪掉他的別名（0002 的 ON DELETE CASCADE），改前會寫出那些別名。
-- 只新增、不修改；既有的人員與別名不動。

CREATE TABLE mailbox.change_log (
    change_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    changed_by text NOT NULL,                 -- Windows 使用者名稱
    changed_at timestamptz NOT NULL DEFAULT now(),
    kind text NOT NULL CHECK (kind IN ('人員', '別名')),
    action text NOT NULL CHECK (action IN ('新增', '修改', '刪除')),
    before jsonb,                             -- 改前（新增時沒有）；刪除人員時含連帶刪掉的別名
    after jsonb,                              -- 改後（刪除時沒有）
    CHECK ((action = '新增') = (before IS NULL)),
    CHECK ((action = '刪除') = (after IS NULL))
);

CREATE FUNCTION mailbox.change_log_insert_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '信箱維護紀錄只新增，不能修改或刪除（紀錄 %）', OLD.change_id;
END;
$$;

CREATE TRIGGER change_log_insert_only
    BEFORE UPDATE OR DELETE ON mailbox.change_log
    FOR EACH ROW EXECUTE FUNCTION mailbox.change_log_insert_only();
