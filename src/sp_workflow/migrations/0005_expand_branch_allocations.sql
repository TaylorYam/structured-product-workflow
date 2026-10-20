-- 同一 TDCC 的庫存資料可能分多列提供，合併後不限制分公司數量上限。

ALTER TABLE workflow.branch_allocation
    DROP CONSTRAINT branch_allocation_seq_check;

ALTER TABLE workflow.branch_allocation
    ADD CONSTRAINT branch_allocation_seq_check CHECK (seq >= 1);
