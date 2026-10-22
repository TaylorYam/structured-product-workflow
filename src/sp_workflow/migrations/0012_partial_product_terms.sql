-- 商品條件可以部分空白（#114、ADR 0016）：人工維護（#115）可以先存一部分、之後再補，所以起單資料匯入的必填欄
-- （`order_terms.REQUIRED`）對應的欄與 KO Type、Barrier Type 換算的代碼欄改成允許空白；TDCC、商品種類、上手與狀態
-- 照舊不可空白。Strike date、期初定價本來就可以空白。缺欄的商品核對條件停在「待確認」並寫出缺哪幾欄，不能核對、放行，也不會回填或鎖住。已有的資料不變。

ALTER TABLE workflow.product
    ALTER COLUMN currency DROP NOT NULL,
    ALTER COLUMN trade_date DROP NOT NULL,
    ALTER COLUMN final_valuation_date DROP NOT NULL,
    ALTER COLUMN maturity_date DROP NOT NULL,
    ALTER COLUMN non_call_months DROP NOT NULL,
    ALTER COLUMN underlying_1 DROP NOT NULL,
    ALTER COLUMN strike_pct DROP NOT NULL,
    ALTER COLUMN ko_freq DROP NOT NULL,       -- 由 KO Type 換算
    ALTER COLUMN ko_memory DROP NOT NULL,     -- 由 KO Type 換算
    ALTER COLUMN ko_type DROP NOT NULL,
    ALTER COLUMN ko_pct DROP NOT NULL,
    ALTER COLUMN coupon_pct DROP NOT NULL,
    ALTER COLUMN upfront_pct DROP NOT NULL,
    ALTER COLUMN tenor_months DROP NOT NULL,
    ALTER COLUMN ki_freq DROP NOT NULL,       -- 由 Barrier Type 換算
    ALTER COLUMN barrier_type DROP NOT NULL;
