-- 發行機構評等（#237）：每家上手的發行機構與保證機構，在標準普爾、穆迪、惠譽的長期債務信用評等，保留生效日歷史。
-- 自算風險等級（#238）與說明書／投資人須知的評等核對（#240～#243）都用交易日查：取生效日 <= 交易日的最新一筆。
-- 從「維護」選單的「發行機構評等…」人工維護；每次存檔留一筆紀錄（誰、何時、改前改後），比照信箱維護（0019）。
--
-- 一筆評等是一個機構在某個生效日的三家評等；沒有該家評等時留空（例：BNP Paribas Issuance B.V. 只有標普）。
-- 第一批評等的生效日是 1900-01-01（畫面寫「最初」）：使用者決定不設起始日，任何交易日都查得到（2026-10-26）。

CREATE TABLE workflow.rated_entity (
    issuer text NOT NULL CHECK (issuer ~ '^[A-Z]+$'),  -- 上手代碼，同 workflow.product.issuer（BARC、CITI…）
    role text NOT NULL CHECK (role IN ('發行機構', '保證機構')),
    name text NOT NULL CHECK (btrim(name) <> ''),     -- 顯示名稱
    PRIMARY KEY (issuer, role)
);

CREATE TABLE workflow.credit_rating (
    issuer text NOT NULL,
    role text NOT NULL,
    effective_date date NOT NULL,
    sp text CONSTRAINT credit_rating_sp_format
        CHECK (sp ~ '^(AAA|AA[+-]?|A[+-]?|BBB[+-]?|BB[+-]?|B[+-]?|CCC[+-]?|CC|C|D)$'),
    moodys text CONSTRAINT credit_rating_moodys_format
        CHECK (moodys ~ '^(Aaa|Aa[123]|A[123]|Baa[123]|Ba[123]|B[123]|Caa[123]|Ca|C)$'),
    fitch text CONSTRAINT credit_rating_fitch_format
        CHECK (fitch ~ '^(AAA|AA[+-]?|A[+-]?|BBB[+-]?|BB[+-]?|B[+-]?|CCC[+-]?|CC|C|RD|D)$'),
    PRIMARY KEY (issuer, role, effective_date),
    FOREIGN KEY (issuer, role) REFERENCES workflow.rated_entity ON UPDATE CASCADE,
    CONSTRAINT credit_rating_any CHECK (num_nonnulls(sp, moodys, fitch) > 0)
);

CREATE TABLE workflow.credit_rating_log (
    change_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    changed_by text NOT NULL,                 -- Windows 使用者名稱
    changed_at timestamptz NOT NULL DEFAULT now(),
    action text NOT NULL CHECK (action IN ('新增', '修改', '刪除')),
    before jsonb,                             -- 改前（新增時沒有）
    after jsonb,                              -- 改後（刪除時沒有）
    CHECK ((action = '新增') = (before IS NULL)),
    CHECK ((action = '刪除') = (after IS NULL))
);

CREATE FUNCTION workflow.credit_rating_log_insert_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '評等維護紀錄只新增，不能修改或刪除（紀錄 %）', OLD.change_id;
END;
$$;

CREATE TRIGGER credit_rating_log_insert_only
    BEFORE UPDATE OR DELETE ON workflow.credit_rating_log
    FOR EACH ROW EXECUTE FUNCTION workflow.credit_rating_log_insert_only();

-- 第一批：2026-09-01～10-12 交易的 580 份說明書與投資人須知（14 家，各家寫法全部一致；#236）。
-- 文件沒寫的評等留空；BNP Paribas 的惠譽取 Generator 試算表（文件沒寫）。
-- CA、JPM、NOMURA、SG 的發行機構（工具公司）文件不寫評等，不建。
INSERT INTO workflow.rated_entity (issuer, role, name) VALUES
    ('BARC', '發行機構', 'Barclays Bank PLC'),
    ('BBVA', '發行機構', 'BBVA Global Markets B.V.'),
    ('BBVA', '保證機構', 'Banco Bilbao Vizcaya Argentaria, S.A.'),
    ('BNP', '發行機構', 'BNP Paribas Issuance B.V.'),
    ('BNP', '保證機構', 'BNP Paribas'),
    ('CA', '保證機構', 'Crédit Agricole Corporate and Investment Bank'),
    ('CITI', '發行機構', 'Citigroup Global Markets Holdings Inc.'),
    ('CITI', '保證機構', 'Citigroup Inc.'),
    ('DBS', '發行機構', 'DBS Bank Ltd'),
    ('GS', '發行機構', 'Goldman Sachs International'),
    ('GS', '保證機構', 'The Goldman Sachs Group, Inc.'),
    ('HSBC', '發行機構', 'The Hongkong and Shanghai Banking Corporation Limited'),
    ('JPM', '保證機構', 'JPMorgan Chase Bank, N.A.'),
    ('MS', '發行機構', 'Morgan Stanley & Co. International plc'),
    ('NATIXIS', '發行機構', 'Natixis'),
    ('NOMURA', '保證機構', 'Nomura Holdings, Inc.'),
    ('SG', '保證機構', 'Societe Generale'),
    ('UBS', '發行機構', 'UBS AG');

INSERT INTO workflow.credit_rating (issuer, role, effective_date, sp, moodys, fitch) VALUES
    ('BARC', '發行機構', '1900-01-01', 'A+', 'A1', 'AA-'),
    ('BBVA', '發行機構', '1900-01-01', 'A+', 'A2', NULL),
    ('BBVA', '保證機構', '1900-01-01', 'A+', 'A2', 'A'),
    ('BNP', '發行機構', '1900-01-01', 'A+', NULL, NULL),
    ('BNP', '保證機構', '1900-01-01', 'A+', 'A1', 'AA-'),
    ('CA', '保證機構', '1900-01-01', 'A+', 'A1', 'AA-'),
    ('CITI', '發行機構', '1900-01-01', 'A', 'A2', 'AA-'),
    ('CITI', '保證機構', '1900-01-01', 'BBB+', 'A3', 'A'),
    ('DBS', '發行機構', '1900-01-01', 'AA-', 'Aa1', 'AA-'),
    ('GS', '發行機構', '1900-01-01', 'A+', 'A1', 'AA-'),
    ('GS', '保證機構', '1900-01-01', 'BBB+', 'A2', 'A'),
    ('HSBC', '發行機構', '1900-01-01', 'AA-', 'Aa3', 'AA'),
    ('JPM', '保證機構', '1900-01-01', 'AA-', 'Aa2', 'AA'),
    ('MS', '發行機構', '1900-01-01', 'A+', 'Aa3', 'AA'),
    ('NATIXIS', '發行機構', '1900-01-01', 'A+', 'A2', 'A+'),
    ('NOMURA', '保證機構', '1900-01-01', 'BBB+', 'Baa1', 'A-'),
    ('SG', '保證機構', '1900-01-01', 'A', 'A1', 'A+'),
    ('UBS', '發行機構', '1900-01-01', 'A+', 'Aa2', 'AA');
