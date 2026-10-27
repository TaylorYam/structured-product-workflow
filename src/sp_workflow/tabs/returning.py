"""「5 放回」分頁：選批次資料夾與核對結果檔，把核對通過的 TS／IIS 複製回商品資料夾；登入了資料庫時同時留步驟紀錄（#151）。
放回完成後把通過的 TDCC 帶到「6 寄信草稿」（#281）。"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Sequence
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from fcn_checker.ui import Background, HintBar, NextButton
from sp_workflow.local_settings import FolderMemory
from sp_workflow.returning import PENDING_SUFFIX, ReturnReceipt
from sp_workflow.tabs.base import TITLE, BackgroundFactory, DatabaseLogin, Tab, ask_dir, record_note, set_if_chosen
from sp_workflow.window_actions import ReturnResult, run_return


class ReturnTab(Tab):
    def __init__(
        self,
        notebook: ttk.Notebook,
        memory: FolderMemory,
        db: DatabaseLogin,
        on_returned: Callable[[Sequence[str]], bool],
        on_next: Callable[[], None],
        *,
        background: BackgroundFactory = Background,
    ) -> None:
        """on_returned 把通過的 TDCC 帶到寄信草稿頁、回傳是否帶入；on_next 切到寄信草稿頁（#281）。"""
        columns = (("tdcc", "TDCC", 130), ("ts", "TS", 70), ("iis", "IIS", 70), ("reason", "原因", 450))
        super().__init__(notebook, "放回", columns, db, background)
        self.memory = memory
        self.on_returned = on_returned
        self.batch_var = tk.StringVar(value=memory.load().get("batch", ""))
        self.result_var = tk.StringVar()
        self.handed_off: tuple[str, str] | None = None  # 從核對帶入的（批次資料夾, 核對結果檔）
        self.status_var.set("選批次資料夾與「核對」分頁存下的核對結果檔，按「放回資料夾」。")

        self.page_header(
            "放回商品資料夾",
            f"把核對通過的 TS／IIS 用標準檔名複製回商品資料夾；沒通過的那份檔名加「{PENDING_SUFFIX}」。",
        )
        self.hint = HintBar(self.frame, self._change_result)
        self.hint.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        self.hint.hide()
        body = self.body(self.card(2))
        row = self.field(body, 0, "批次資料夾", self.batch_var, lambda: ask_dir("選擇批次資料夾", self.batch_var))
        row = self.field(body, row, "核對結果檔", self.result_var, self._ask_result)
        self.result_area(body, row)
        self.start_button = ttk.Button(self.buttons, text="放回資料夾", command=self.start, style="Primary.TButton")
        self.start_button.pack(side=tk.LEFT)
        self.next_button = NextButton(self.buttons, "下一步：寄信草稿 →", on_next)
        self.place_table(3)
        for var in (self.batch_var, self.result_var):
            var.trace_add("write", lambda *_: self._hide_hint_if_changed())

    def receive(self, result: Path, batch: Path | None) -> None:
        """核對存檔後帶入剛存的核對結果檔與這批的批次資料夾（#76）；不切頁、不自動放回。"""
        self.restart()  # 換了一批要放回的，上一次的結果收起來
        self.next_button.withdraw()
        if batch is not None:
            self.batch_var.set(str(batch))
        self.result_var.set(str(result))
        self.handed_off = (self.batch_var.get(), self.result_var.get())
        where = (
            f"批次資料夾：{batch}"
            if batch is not None
            else "批次資料夾沒有帶入（核對的 PDF 不在同一個批次資料夾），請自己確認。"
        )
        self.hint.show(f"已帶入「4 核對」剛存的核對結果檔：\n{result}\n{where}")

    def _hide_hint_if_changed(self) -> None:
        """自己改了帶入的批次資料夾或核對結果檔，提示條就收起來。"""
        if self.handed_off is not None and (self.batch_var.get(), self.result_var.get()) != self.handed_off:
            self.handed_off = None
            self.hint.hide()

    def _change_result(self) -> None:
        set_if_chosen(self.result_var, self._ask_result)

    def _ask_result(self) -> str:
        return filedialog.askopenfilename(
            title="選擇核對結果檔",
            initialdir=self.batch_var.get() or None,
            filetypes=[("Excel", "*.xlsx"), ("全部檔案", "*.*")],
        )

    def start(self) -> None:
        batch_text, result_text = self.batch_var.get().strip(), self.result_var.get().strip()
        if not batch_text or not result_text:
            messagebox.showerror(TITLE, "請先選擇批次資料夾與核對結果檔。")
            return
        batch, result = Path(batch_text), Path(result_text)

        def starting() -> None:
            self.memory.remember(batch=str(batch))
            self.next_button.withdraw()

        self.runner.run_maybe(  # 沒有連線設定或取消登入：照樣放回，不留步驟紀錄
            lambda settings: run_return(settings, batch, result),
            "放回中…",
            self._done,
            before=starting,
            database_error=lambda returned: returned.recorded.database_error,
        )

    def _busy(self, busy: bool) -> None:
        self.start_button.configure(state=tk.DISABLED if busy else tk.NORMAL)

    def _done(self, result: ReturnResult) -> None:
        recorded, receipt = result.recorded, result.receipt
        for o in receipt.outcomes:
            self.add_row((o.tdcc, o.ts, o.iis, o.reason), problem=o.blocked or o.pending or bool(o.reason))
        blocked = sum(o.blocked for o in receipt.outcomes)
        pending = sum(o.pending and not o.blocked for o in receipt.outcomes)
        returned = len(receipt.outcomes) - blocked - pending
        kpis = (
            ("已放回或已在（檔）", returned, False),
            ("含待確認（檔）", pending, pending > 0),
            ("不放回（檔）", blocked, blocked > 0),
        )
        self.finish(
            kpis,
            f"完成：{returned} 個商品已放回或已在、{pending} 個含「{PENDING_SUFFIX}」檔、"
            f"{blocked} 個不放回。放回清單：{receipt.list_path}"
            + record_note(recorded)
            + f"\n{self._hand_off(receipt)}",
        )

    def _hand_off(self, receipt: ReturnReceipt) -> str:
        """把通過的 TDCC（TS、IIS 都用標準檔名放回或已在）帶到寄信草稿頁（#281）；真的帶入了才有下一步。
        回傳寫在完成訊息的一句。"""
        tdccs = list(dict.fromkeys(o.tdcc for o in receipt.outcomes if not o.blocked and not o.pending))
        if not tdccs:
            return "沒有通過的 TDCC（TS、IIS 都用標準檔名放回或已在），寄信草稿頁沒有帶入。"
        if not self.on_returned(tdccs):
            return "寄信草稿頁沒有帶入（正在處理，或保留原本的 TDCC）。"
        self.next_button.offer()
        return f"已把通過的 TDCC {len(tdccs)} 筆帶到「6 寄信草稿」。"
