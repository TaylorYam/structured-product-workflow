-- 交易明細新版面（#420）：FC/PB 改名 FC1（照舊存 fc_name），FC1 後面多了 FC2（共同經手的第二位 FC，金額不拆），
-- 前台主管/投專 拿掉、同一個位置換成 新加坡（Y/N）。舊的交易列兩欄空白；supervisor 保留舊值，新版面匯入時空白。

ALTER TABLE workflow.trade_row
    ADD COLUMN fc2_name text,
    ADD COLUMN singapore text;
