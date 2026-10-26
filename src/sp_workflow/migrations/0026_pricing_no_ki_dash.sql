-- KI 欄沒有 KI 的改寫 `-`（原本 No KI，#257），和有 KI 的代碼一眼分開；其他 KI 與比價狀態其他欄不動。

UPDATE workflow.pricing_status SET ki = '-' WHERE ki = 'No KI';
