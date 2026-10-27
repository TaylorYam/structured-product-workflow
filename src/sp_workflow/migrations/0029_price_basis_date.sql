-- 價格基準日（#275、ADR 0025）：各標的的進場價、執行價、下限價、KO 價是哪一天的股數基準。
-- 空白是 Strike date（交易時的基準）；舊商品的價格常是上手依拆股調整過的，基準日是含到的那次拆股的生效日。
-- 由比價依拆股登記與 Strike date 的收盤價推算（`stock_splits.refresh_basis`），不人工填；既有資料不動，下一次比價補上。

ALTER TABLE workflow.underlying_price ADD COLUMN price_basis_date date;
