-- KO 降幅（#537、ADR 0033）：FCN_Stepdown、Snowball_Stepdown（與部分 Snowball）的 KO% 每期固定往下降，
-- 商品條件多存每期降幾個百分點；KO% 存第 1 期。起單建檔、舊總表沒有這欄：Stepdown 必填（起單資料頁、人工維護、
-- 回補表的「KO降幅(%)」欄填），Snowball 空白是不降，其他商品要空白（必填與否依 `fcn_checker.products.PRODUCT_RULES`，
-- 不放在資料表）。既有的商品不動。

ALTER TABLE workflow.product ADD COLUMN ko_step_pct numeric(12, 6) CHECK (ko_step_pct >= 0);
