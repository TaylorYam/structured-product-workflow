-- 比價狀態把 KI 觸及存成結構（#442）：第一次觸及的日期與標的代碼，曝險分析讀這兩欄、不再解析 KI 欄的顯示文字。
-- KI 欄（`<KI 種類> hit 日期 UL_n 代碼`）照舊由比價產生給進度頁看。既有列由 KI 欄的文字回填；兩欄要嘛都有、要嘛都空白。

ALTER TABLE workflow.pricing_status
    ADD COLUMN ki_touch_date date,
    ADD COLUMN ki_touch_code text,
    ADD CONSTRAINT pricing_status_ki_touch_pair CHECK ((ki_touch_date IS NULL) = (ki_touch_code IS NULL));

UPDATE workflow.pricing_status
SET ki_touch_date = substring(ki FROM '^\S+ hit (\d{4}-\d{2}-\d{2}) UL_\d+ .+$')::date,
    ki_touch_code = substring(ki FROM '^\S+ hit \d{4}-\d{2}-\d{2} UL_\d+ (.+)$')
WHERE ki ~ '^\S+ hit \d{4}-\d{2}-\d{2} UL_\d+ .+$';
