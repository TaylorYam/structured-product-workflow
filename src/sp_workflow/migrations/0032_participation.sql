-- 參與率（%）（#373、ADR 0029）：只有 BEN 有，其他商品空白；BEN 一定要有值由商品條件寫入前的檢查擋（`product_terms`）。
-- 既有商品不動。

ALTER TABLE workflow.product
    ADD COLUMN participation_pct numeric(12, 6),
    ADD CONSTRAINT product_participation_pct_check CHECK (participation_pct >= 0);
