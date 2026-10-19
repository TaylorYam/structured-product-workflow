-- 信箱改由資料庫直接維護（#35、ADR 0004）：人工用 SQL 新增人員與別名時，比對鍵由資料庫自動產生，信箱格式由資料庫檢查。

ALTER TABLE mailbox.person DROP COLUMN name_key;
ALTER TABLE mailbox.person
    ADD COLUMN name_key text GENERATED ALWAYS AS (lower(btrim(name))) STORED,
    ADD CONSTRAINT person_name_key_unique UNIQUE (name_key),
    ADD CONSTRAINT person_email_format CHECK (email ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$');

ALTER TABLE mailbox.alias DROP COLUMN alias_key;
ALTER TABLE mailbox.alias
    ADD COLUMN alias_key text GENERATED ALWAYS AS (lower(btrim(alias))) STORED,
    ADD CONSTRAINT alias_key_unique UNIQUE (alias_key),
    DROP CONSTRAINT alias_person_name_fkey,
    ADD CONSTRAINT alias_person_name_fkey
        FOREIGN KEY (person_name) REFERENCES mailbox.person (name) ON UPDATE CASCADE ON DELETE CASCADE;

-- 每日匯入不再讀信箱表
ALTER TABLE workflow.import_run
    ALTER COLUMN mailbox_file_name DROP NOT NULL,
    ALTER COLUMN mailbox_sha256 DROP NOT NULL;
