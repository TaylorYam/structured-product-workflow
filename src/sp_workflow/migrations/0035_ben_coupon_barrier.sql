-- BEN 的 KO 欄改存配息門檻（#406、ADR 0029）：起單建檔與舊總表 BEN 的 KO 欄是紅利門檻（最差標的 ≥ 這個 % 才有紅利），
-- 不是提前出場的 KO。既有 BEN 的配息門檻改成原本的 KO(%)（沒有時帶執行價），KO(%) 與 KO 價清成空白；
-- 真的有 KO 的 BEN 由作業人員再填 KO(%)，比價待確認。其他商品不動。

UPDATE workflow.product SET coupon_barrier_pct = COALESCE(ko_pct, strike_pct), ko_pct = NULL WHERE product = 'BEN';

UPDATE workflow.underlying_price SET ko_price = NULL
WHERE tdcc IN (SELECT tdcc FROM workflow.product WHERE product = 'BEN');
