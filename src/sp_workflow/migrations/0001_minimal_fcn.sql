-- 最小 FCN 資料庫（#32、ADR 0003）：mailbox 放人員與信箱，workflow 放商品、配置、交易列與匯入紀錄。
-- 只存寄信與對帳需要的欄位；之後要擴充時以 TDCC 關聯新增資料表，不改這幾張。

CREATE SCHEMA mailbox;
CREATE SCHEMA workflow;

-- 人員：每次匯入以 信箱.xlsx 整份取代
CREATE TABLE mailbox.person (
    name text PRIMARY KEY,
    name_key text NOT NULL UNIQUE,            -- 比對用：去頭尾空白、轉小寫
    email text NOT NULL,
    unit text,
    fixed_cc boolean NOT NULL DEFAULT false
);

-- 別名：交易清單上的寫法 → 信箱表的使用者名稱
CREATE TABLE mailbox.alias (
    alias_key text PRIMARY KEY,
    alias text NOT NULL,
    person_name text NOT NULL REFERENCES mailbox.person (name) ON DELETE CASCADE
);

CREATE TABLE workflow.import_run (
    run_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    imported_at timestamptz NOT NULL DEFAULT now(),
    daily_file_name text NOT NULL,
    daily_sha256 text NOT NULL,
    mailbox_file_name text NOT NULL,
    mailbox_sha256 text NOT NULL,
    summary jsonb NOT NULL
);

-- 商品條件：TDCC 第一次匯入時寫入，之後不覆蓋
CREATE TABLE workflow.product (
    tdcc text PRIMARY KEY CHECK (tdcc ~ '^[0-9]{12}$'),
    product text NOT NULL,
    issuer text NOT NULL,
    isin text NOT NULL,
    currency text NOT NULL,
    trade_date date NOT NULL,
    issue_date date NOT NULL,
    final_valuation_date date NOT NULL,
    maturity_date date NOT NULL,
    initial_pricing text,                     -- 期初定價：收盤價、開盤價、VWAP…
    non_call_months integer NOT NULL,
    underlying_1 text NOT NULL,
    underlying_2 text,
    underlying_3 text,
    underlying_4 text,
    underlying_5 text,
    strike_pct numeric(12, 6) NOT NULL,
    ko_freq text NOT NULL,                    -- 原始代碼 D／P
    ko_memory text NOT NULL,                  -- 原始代碼 Y／N
    ko_type text NOT NULL,                    -- 轉換後：Daily Memory、Daily、Period End、Period End Memory
    ko_pct numeric(12, 6) NOT NULL,
    coupon_pct numeric(12, 6) NOT NULL,
    upfront_pct numeric(12, 6) NOT NULL,      -- 庫存總表的 UF（百分點）；NotePrice = 100 − UF
    tenor_months integer NOT NULL,
    ki_freq text NOT NULL,                    -- 原始代碼 -／AM／D／P
    barrier_type text NOT NULL,               -- 轉換後：NONE、EKI、AKI、MKI
    ki_pct numeric(12, 6),
    status_event text NOT NULL CHECK (status_event IN ('在庫', 'KO', '到期還本', '接股', '顧客贖回')),
    status_date date,
    status_underlying text,
    status_raw text,
    first_run_id bigint NOT NULL REFERENCES workflow.import_run (run_id)
);

-- 庫存總表的分公司配置（分公司_1～7）
CREATE TABLE workflow.branch_allocation (
    tdcc text NOT NULL REFERENCES workflow.product (tdcc) ON DELETE CASCADE,
    seq smallint NOT NULL CHECK (seq BETWEEN 1 AND 7),
    branch text NOT NULL,
    amount numeric(20, 4) NOT NULL,
    PRIMARY KEY (tdcc, seq)
);

-- 交易明細的列；庫存可能晚一天才更新，所以不參照 product
CREATE TABLE workflow.trade_row (
    tdcc text NOT NULL CHECK (tdcc ~ '^[0-9]{12}$'),
    row_seq smallint NOT NULL CHECK (row_seq >= 1),   -- 同一檔在貼上時的順序；1 是有編號的那一列
    number integer,
    trade_date date NOT NULL,
    product text NOT NULL,
    currency text NOT NULL,
    amount numeric(20, 4) NOT NULL,
    fc_name text NOT NULL,
    branch text NOT NULL,
    supervisor text,
    uf numeric(12, 6),
    initial_pricing text,
    note text,
    raised text,
    ts_iis_checked text,
    checker text,
    originator text,
    first_run_id bigint NOT NULL REFERENCES workflow.import_run (run_id),
    PRIMARY KEY (tdcc, row_seq)
);
