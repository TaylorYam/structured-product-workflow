-- 比價狀態加 KI 欄（#196、ADR 0022）：比價狀態、已比到、原因只講 KO，KI 另寫一欄給進度頁顯示
-- （沒有 KI、到期觀察、未觸及、已觸及 日期 標的、每期觀察，需人工追蹤）。既有的比價狀態不動、KI 空白，
-- 下一次比價（回補股價後或「重新比價全部」）補上。

ALTER TABLE workflow.pricing_status ADD COLUMN ki text;
