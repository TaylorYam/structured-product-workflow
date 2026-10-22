"""「2 起單資料」分頁：貼 TDCC、讀一份起單建檔（#97），在右邊逐檔確認、修改後匯入資料庫（#86）；匯入後顯示重算的核對條件（#88）。"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Sequence
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from fcn_checker.check_config import ConfigPaths
from fcn_checker.ui import HintBar
from sp_workflow.check_terms import CalcLine
from sp_workflow.local_settings import FolderMemory
from sp_workflow.order_import import BLOCKED, IMPORTABLE, READY, TO_CHECK, OrderDraft, OrderImportResult, review_status
from sp_workflow.order_terms import OrderRow
from sp_workflow.preparation import parse_tdccs
from sp_workflow.product_write import LOCKED, NEW, OVERWRITTEN, SAME, SKIPPED, OrderImportLine
from sp_workflow.tabs.base import TITLE, DatabaseLogin, Tab
from sp_workflow.tabs.editor import OrderEditor
from sp_workflow.tabs.recalc import Releaser, recalc_text, status_counts
from sp_workflow.window_actions import ReadResult, Recalc, import_order_sheet, read_order_sheet

# 起單建檔的資料夾（#97）；本機沒有選過時「選擇…」先打開這裡
ORDER_SHEET_DIR = Path(r"G:\Bond Data\PM事項\0.常用工具\ELN每日起單建檔")


class OrderTermsTab(Tab):
    READY_TEXT = "貼上 TDCC，選一份起單建檔，按「讀取」。只讀起單建檔，不會修改它。"

    def __init__(
        self,
        notebook: ttk.Notebook,
        memory: FolderMemory,
        db: DatabaseLogin,
        config_paths: Callable[[], ConfigPaths],
    ) -> None:
        super().__init__(
            notebook,
            "起單資料",
            (
                ("tdcc", "TDCC", 130),
                ("status", "狀態", 110),
                ("issuer", "上手", 85),
                ("calc", "核對條件", 90),
                ("reason", "原因", 260),
            ),
        )
        self.memory = memory
        self.db = db
        self.config_paths = config_paths
        self.read: ReadResult | None = None  # 最近一次讀取的結果
        self.items: dict[str, tuple[OrderRow, OrderDraft | None]] = {}  # 結果表的列 → (讀取結果, 確認資料)
        self.unsaved = False  # 有改過、還沒匯入資料庫
        self.working = False  # 正在讀取或匯入
        self.sheet_var = tk.StringVar(value=memory.load().get("order_sheet", ""))
        self.status_var.set(self.READY_TEXT)

        self.page_header(
            "確認起單資料",
            "讀作業人員的起單建檔，在右邊逐檔確認、修改後匯入資料庫；不修改起單建檔。",
        )
        self.header.grid_configure(columnspan=3)
        self.handed_off: list[str] | None = None  # 從每日匯入帶入的 TDCC
        self.hint = HintBar(self.frame, self._clear_handed_off)
        self.hint.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 12))
        self.hint.hide()
        card = self.card(2)  # 只佔左邊，和結果表同寬；右邊讓給確認區（#106）
        self.tdcc_column(card)
        self.tdcc_text.configure(height=6)  # 讓出高度給下方的結果表
        body = self.body(card, column=1)
        row = self.field(body, 0, "起單建檔", self.sheet_var, self._ask_sheet, "每天的 起單建檔_<日期>.xlsm")
        self.result_area(body, row)
        self.start_button = ttk.Button(self.buttons, text="讀取", command=self.start, style="Primary.TButton")
        self.start_button.pack(side=tk.LEFT)
        self.import_button = ttk.Button(self.buttons, text="匯入資料庫", command=self.import_now, state=tk.DISABLED)
        self.import_button.pack(side=tk.LEFT, padx=(8, 0))
        self.place_table(3)
        self.frame.columnconfigure(2, weight=1)
        self.editor = OrderEditor(self.frame, lambda: self.read.config if self.read else None, self._edited)
        self.editor.grid(row=2, rowspan=2, column=2, sticky="nsew", padx=(16, 0))  # 從卡片那一列拉到底
        self.table.bind("<<TreeviewSelect>>", self._selected)
        self.imported: dict[str, OrderImportLine] = {}  # 最近一次匯入每檔的結果（放行後重寫那一列）
        self.releaser = Releaser(
            self, db, self._selected_tdcc, self._released, idle=lambda: not self.working, busy=self._busy
        )

    def _selected_tdcc(self) -> str | None:
        item = self.selected_item()
        found = None if item is None else self.items.get(item)
        return None if found is None else found[0].tdcc

    def _released(self, calc: CalcLine) -> None:
        for iid, (row, draft) in self.items.items():
            line = self.imported.get(row.tdcc)
            if draft is not None and line is not None and row.tdcc == calc.tdcc:
                self._show_imported(iid, row, line, calc)

    def _show_imported(self, iid: str, row: OrderRow, line: OrderImportLine, calc: CalcLine | None) -> None:
        """匯入後的一列：狀態是匯入結果，核對條件是重算後的狀態，原因寫兩邊的說明。"""
        calc_reason = f"核對條件：{calc.reason}" if calc is not None and calc.reason else ""
        problem = line.result == SKIPPED or (calc is not None and calc.problem)
        reason = "；".join(filter(None, (line.detail, calc_reason)))
        self._show_row(iid, row, line.result, reason, problem, calc.status if calc is not None else "")

    def _ask_sheet(self) -> str:
        """選起單建檔；有選就記住（沒按讀取就關視窗，下次也打開這個資料夾）。"""
        current = self.sheet_var.get().strip()
        folder = Path(current).parent if current else ORDER_SHEET_DIR
        chosen = filedialog.askopenfilename(
            title="選擇起單建檔",
            initialdir=str(folder) if folder.is_dir() else None,
            filetypes=[("Excel", "*.xlsm *.xlsx"), ("全部檔案", "*.*")],
        )
        if chosen:
            self.memory.remember(order_sheet=str(Path(chosen)))
        return chosen

    def receive_tdccs(self, tdccs: Sequence[str]) -> bool:
        """每日匯入完成後帶入交易明細的 FCN TDCC（#90）；回傳是否帶入。不切頁、不自動讀取。

        正在處理時不帶入；已經貼了不同的 TDCC（不看順序）時先問（#93）；有改過還沒匯入的內容也先問（#86）。
        """
        if self.working:
            messagebox.showinfo(
                TITLE,
                "「2 起單資料」正在處理，這次每日匯入的 FCN TDCC 沒有帶入；處理完後請自己貼。",
                parent=self.master,
            )
            return False
        current = parse_tdccs(self.tdcc_text.get("1.0", tk.END))
        if (
            current
            and set(current) != set(tdccs)
            and not messagebox.askyesno(
                TITLE,
                f"「2 起單資料」已經有 {len(current)} 筆 TDCC，和這次每日匯入的 FCN TDCC（{len(tdccs)} 筆）不同。"
                "\n\n要換成每日匯入的嗎？",
                parent=self.master,
            )
        ):
            return False
        if not self._may_discard("帶入每日匯入的 TDCC"):
            return False
        self._clear_results()  # 換了一批要讀的，上一次的結果收起來
        self.handed_off = list(tdccs)
        self._set_tdccs("\n".join(tdccs))
        self.hint.show(f"已帶入每日匯入的 FCN TDCC {len(tdccs)} 筆，選好起單建檔後按「讀取」。")
        return True

    def _set_tdccs(self, text: str) -> None:
        self.tdcc_text.delete("1.0", tk.END)
        self.tdcc_text.insert("1.0", text)
        self.count_var.set(f"已貼 {len(parse_tdccs(text))} 筆")

    def _tdcc_changed(self, _event: object = None) -> None:
        """自己改了帶入的 TDCC，提示條就收起來。"""
        super()._tdcc_changed(_event)
        if self.handed_off is not None and parse_tdccs(self.tdcc_text.get("1.0", tk.END)) != self.handed_off:
            self._forget_handed_off()

    def _clear_handed_off(self) -> None:
        """提示條的「改選…」：清掉帶入的 TDCC，讓作業人員自己貼。"""
        self._forget_handed_off()
        self._set_tdccs("")
        self.tdcc_text.focus_set()

    def _forget_handed_off(self) -> None:
        self.handed_off = None
        self.hint.hide()

    def has_unsaved(self) -> bool:
        """有改過、還沒匯入資料庫的內容（先把編輯區還沒解讀的輸入解讀掉）。"""
        self.editor.commit()
        return self.unsaved

    def _may_discard(self, action: str) -> bool:
        """有改過、還沒匯入資料庫的內容時先問。"""
        return not self.has_unsaved() or messagebox.askyesno(
            TITLE,
            f"起單資料頁有改過、還沒匯入資料庫的內容，{action}後會不見。\n\n確定要繼續嗎？",
            icon=messagebox.WARNING,
            default=messagebox.NO,
            parent=self.master,
        )

    def _clear_results(self) -> None:
        """回到還沒讀取的樣子：清掉結果表、編輯區與重點數字，狀態列回到說明，按鈕不能按。"""
        self.restart()
        self.table.delete(*self.table.get_children())
        self.status_var.set(self.READY_TEXT)
        self.read = None
        self.items = {}
        self.imported = {}
        self.releaser.show(Recalc())
        self.unsaved = False
        self.editor.typed = {}
        self.editor.show(None)
        self.import_button.configure(state=tk.DISABLED)

    def _busy(self, busy: bool) -> None:
        """處理中：按鈕與編輯區都不能用，結果表點了也不換檔。"""
        self.working = busy
        self.editor.set_frozen(busy)
        state = tk.DISABLED if busy else tk.NORMAL
        self.start_button.configure(state=state)
        has_drafts = self.read is not None and bool(self.read.drafts)
        self.import_button.configure(state=state if has_drafts else tk.DISABLED)
        self.releaser.refresh()

    def _show_row(self, iid: str, row: OrderRow, status: str, reason: str, problem: bool, calc: str = "") -> None:
        values = (row.tdcc, status, row.issuer, calc, reason)
        tags = ("problem",) if problem else ()
        if self.table.exists(iid):
            self.table.item(iid, values=values, tags=tags)
        else:
            self.table.insert("", tk.END, iid=iid, values=values, tags=tags)

    # ---------- 讀取 ----------

    def start(self) -> None:
        text = self.tdcc_text.get("1.0", tk.END)
        sheet_text = self.sheet_var.get().strip()
        if not sheet_text:
            messagebox.showerror(TITLE, "請先選擇起單建檔。", parent=self.master)
            return
        if not self._may_discard("重新讀取"):
            return
        sheet = Path(sheet_text)
        self.memory.remember(order_sheet=str(sheet))
        self._clear_results()
        settings = self.db.settings(quiet=True)  # 用來帶入資料庫的值；沒有連線設定或取消時照樣讀
        config_paths = self.config_paths()
        self._busy(True)

        def run() -> ReadResult:
            return read_order_sheet(settings, text, sheet, config_paths)

        self.run_in_background(run, f"讀取中…（{sheet.name}）", self._done)

    def _done(self, result: object) -> None:
        if isinstance(result, ReadResult):
            self.db.after_run(result.database_error)
            self.read = result
            self._fill_table(result)
        self._busy(False)
        if not isinstance(result, ReadResult):
            return
        statuses = [self.table.set(iid, "status") for iid in self.items]
        ready = sum(status in IMPORTABLE for status in statuses)
        locked = statuses.count(LOCKED)
        todo = len(statuses) - ready - locked
        status = (
            f"讀完了：{ready} 檔可以匯入、{todo} 檔要處理、{locked} 檔已鎖住。點商品就能在右邊確認、修改，"
            "好了按「匯入資料庫」。"
        )
        if result.database_note:
            status += f"\n注意：{result.database_note}。"
        self.finish((("可匯入（檔）", ready, False), ("要處理（檔）", todo, todo > 0)), status)

    def _fill_table(self, result: ReadResult) -> None:
        """有問題的排前面：待確認（含找不到）、請確認、可匯入、已鎖住。"""
        by_tdcc = {draft.tdcc: draft for draft in result.drafts}
        order = {BLOCKED: 0, TO_CHECK: 1, READY: 2, LOCKED: 3}
        listed = []
        for row in result.rows:
            draft = by_tdcc.get(row.tdcc) if row.found else None
            listed.append((row, draft, review_status(row, draft, result.config)))
        listed.sort(key=lambda item: order.get(item[2][0], 0))
        for i, (row, draft, (status, reason, problem)) in enumerate(listed):
            self.items[str(i)] = (row, draft)
            self._show_row(str(i), row, status, reason, problem)
        if listed:
            self.table.selection_set("0")
            self._selected()

    def _selected(self, _event: object = None) -> None:
        item = self.selected_item()
        if self.working or item not in self.items:
            return
        row, draft = self.items[item]
        if draft is self.editor.draft and draft is not None:
            return  # 同一檔（例如 selection_set 之後又收到一次通知）
        self.editor.commit()  # 換檔前把上一檔還沒解讀的輸入解讀掉
        self.editor.show(draft, row)

    def _edited(self, draft: OrderDraft) -> None:
        """編輯區改了一檔：標成還沒匯入，結果表那一列改回匯入前的狀態。"""
        self.unsaved = True
        for iid, (row, item) in self.items.items():
            if item is draft and self.read is not None:
                self._show_row(iid, row, *review_status(row, draft, self.read.config))

    # ---------- 匯入 ----------

    def import_now(self) -> None:
        """寫錯的欄不擋整批：那一檔不匯入（OrderDraft.input_errors），其他照常匯入。"""
        read = self.read
        if read is None or not read.drafts:
            return
        self.editor.commit()
        settings = self.db.settings()
        if settings is None:
            return
        self._busy(True)
        self.mark_done(False)

        def run() -> OrderImportResult:
            return import_order_sheet(settings, read.drafts, read.config)

        self.run_in_background(run, "匯入資料庫中…", self._saved, clear=False)

    def _saved(self, result: object) -> None:
        self.db.after_run(result)
        if isinstance(result, OrderImportResult):
            self.unsaved = False
        self._busy(False)
        if not isinstance(result, OrderImportResult):
            return
        receipt, calcs = result.receipt, result.recalc
        calendar = None if self.read is None else self.read.config.calendar  # 放行後重算要用
        recalc = Recalc(calcs, calendar, result.recalc_error)
        self.imported = {line.tdcc: line for line in receipt.lines}
        batch: dict[str, CalcLine] = {}
        for iid, (row, draft) in self.items.items():
            line = self.imported.get(row.tdcc) if draft is not None else None
            if line is not None:
                calc = None if calcs is None or not line.imported else calcs.line_of(row.tdcc)
                if calc is not None:
                    batch[row.tdcc] = calc
                self._show_imported(iid, row, line, calc)
        if self.editor.draft is not None:
            self.editor.show(self.editor.draft)  # 資料庫的值更新了，黃色的差異與帶入的值跟著重算
        imported = sum(line.imported for line in receipt.lines)
        not_imported = len(receipt.lines) - imported
        counts = "、".join(f"{kind} {receipt.count(kind)}" for kind in (NEW, OVERWRITTEN, SAME, SKIPPED, LOCKED))
        status = f"完成（第 {receipt.import_id} 次起單資料匯入）：{counts}。"
        if calcs is not None:
            status += f"\n核對條件（這批匯入的 {len(batch)} 檔）：{status_counts(batch.values())}。"
        elif recalc.error:
            status += f"\n{recalc_text(recalc)}"
        if result.reload_error:
            status += f"\n注意：已寫進資料庫，但重新讀取資料庫的值失敗（{result.reload_error}）；再匯入前請重新讀取。"
        kpis = [("已匯入（檔）", imported, False), ("沒匯入（檔）", not_imported, receipt.count(SKIPPED) > 0)]
        if calcs is not None:
            to_confirm = sum(calc.problem for calc in batch.values())
            kpis.append(("核對條件待確認（檔）", to_confirm, to_confirm > 0))
        self.finish(kpis, status)
        self.releaser.show(recalc)
