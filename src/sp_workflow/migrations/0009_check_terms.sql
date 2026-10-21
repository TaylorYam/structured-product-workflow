-- 核對條件（#88、ADR 0012）：起單資料匯入或每日匯入完成後，替還沒鎖住的商品算出各標的的進場價、執行價、下限價、
-- KO 價，由交易明細算分公司配置，並和交易明細交叉比對幣別、交易日、UF。已鎖住的商品不重算。

-- 每檔一筆：最近一次算出、而且和上一次不同時寫入
CREATE TABLE workflow.check_terms (
    tdcc text PRIMARY KEY REFERENCES workflow.product (tdcc) ON DELETE CASCADE,
    status text NOT NULL CHECK (status IN ('完成', '等股價', '等交易明細', '待確認')),
    reason text,                              -- 待確認的原因、等待的說明、已放行的差異
    price_date date,                          -- 用的股價檔（取價日）；VWAP 或還在等股價時空白
    differences jsonb,                        -- 交易明細與起單資料的差異：[[項目, 起單資料, 交易明細, 已放行]]
    calculated_at timestamptz NOT NULL DEFAULT now()
);

-- 每檔每個標的一筆（UL_1～UL_5）；算不出來的價格留空
CREATE TABLE workflow.underlying_price (
    tdcc text NOT NULL REFERENCES workflow.check_terms (tdcc) ON DELETE CASCADE,
    seq smallint NOT NULL CHECK (seq BETWEEN 1 AND 5),
    code text NOT NULL,                       -- BBG 代碼（同 product.underlying_N）
    initial_price numeric,                    -- 進場價
    strike_price numeric,                     -- 執行價 = 進場價 × 執行價(%) ÷ 100
    ki_price numeric,                         -- 下限價 = 進場價 × KI(%) ÷ 100；KI 型態 NONE 時空白
    ko_price numeric,                         -- KO 價 = 進場價 × KO(%) ÷ 100
    PRIMARY KEY (tdcc, seq)
);

-- 作業人員放行的交叉比對差異：一個差異一筆，兩邊的值都一樣才算同一個差異（值變了要重新放行）
CREATE TABLE workflow.cross_check_release (
    release_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tdcc text NOT NULL REFERENCES workflow.product (tdcc) ON DELETE CASCADE,
    item text NOT NULL CHECK (item IN ('幣別', '交易日', 'UF')),
    order_value text NOT NULL,                -- 起單資料的值
    trade_value text NOT NULL,                -- 交易明細的值（好幾種時以「、」分隔）
    released_by text NOT NULL,                -- Windows 使用者名稱
    released_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tdcc, item, order_value, trade_value)
);
