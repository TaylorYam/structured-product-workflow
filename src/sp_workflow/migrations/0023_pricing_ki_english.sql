-- KI 欄改英文代碼（#198）：No KI、EKI、AKI、MKI (manual)，觸及時 `AKI hit 日期 標的`。
-- 清掉 0022 之後存的中文 KI；下一次比價（回補股價後或「重新比價全部」）改寫成英文，
-- 已出場、沒存過 KI 的商品寫 KI 種類。比價狀態其他欄不動。

UPDATE workflow.pricing_status SET ki = NULL WHERE ki IS NOT NULL;
