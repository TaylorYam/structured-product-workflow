-- 配息門檻（#367、ADR 0030）：DACN 依「所有標的收盤價 ≥ 配息門檻」的天數比例配息，商品條件多存配息門檻（最初價格的 %）。
-- 起單建檔、舊總表都沒有這欄：匯入與回補時預設帶執行價，可在確認視窗、人工維護改。只有 DACN 有值，其他商品空白；
-- 既有的商品不動（必填與否依商品種類，由 `fcn_checker.products.PRODUCT_RULES` 檢查，不放在資料表）。

ALTER TABLE workflow.product ADD COLUMN coupon_barrier_pct numeric(12, 6);
