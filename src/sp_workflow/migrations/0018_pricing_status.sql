-- 比價狀態（#155、ADR 0020）：每次匯入收盤價（MarketData CSV、歷史收盤價）或「重新比價全部」之後，
-- 核對條件「完成」的商品逐日比到收盤價表最新的一天，每檔一筆比價狀態；和上一次不同時才寫。
-- 算出的 KO、接股、到期還本寫進 workflow.product_event（整組取代，ADR 0019）；這張表只記比到哪裡、為什麼停。

CREATE TABLE workflow.pricing_status (
    tdcc text PRIMARY KEY REFERENCES workflow.product (tdcc) ON DELETE CASCADE,
    status text NOT NULL CHECK (status IN ('在庫', '等股價', '待確認', '已出場')),
    reason text,                              -- 等股價的標的與日期、待確認的原因、出場的事件
    observed_through date,                    -- 已比到的日期（含）；還沒比到任何一天時空白
    locked boolean NOT NULL,                  -- 比價時商品已鎖住；false 時畫面標「未鎖住」
    calculated_at timestamptz NOT NULL DEFAULT now()
);
