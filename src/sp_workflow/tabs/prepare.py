"""「3 準備」分頁：貼 TDCC、選輸出位置，產生批次資料夾與準備清單；登入了資料庫時同時留步驟紀錄（#151）。
起單資料匯入完成後帶入成功匯入的 TDCC（#281）。"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Sequence
from pathlib import Path
from tkinter import ttk

from fcn_checker.ui import Background, NextButton
from sp_workflow.local_settings import FolderMemory
from sp_workflow.preparation import PREPARED, PrepareReceipt
from sp_workflow.tabs.base import BackgroundFactory, DatabaseLogin, RootFolder, Tab, ask_dir, open_folder, record_note
from sp_workflow.window_actions import PrepareResult, run_prepare


class PrepareTab(Tab):
    READY_TEXT = "貼上 TDCC，選好輸出位置後按「開始準備」（在最上方的根目錄底下找商品資料夾）。"

    def __init__(
        self,
        notebook: ttk.Notebook,
        memory: FolderMemory,
        db: DatabaseLogin,
        root_folder: RootFolder,
        on_prepared: Callable[[PrepareReceipt], bool],
        on_next: Callable[[], None],
        *,
        background: BackgroundFactory = Background,
    ) -> None:
        columns = (("tdcc", "TDCC", 130), ("status", "狀態", 70), ("reason", "原因", 520))
        super().__init__(notebook, "準備", columns, db, background)
        self.on_prepared = on_prepared
        self.memory = memory
        self.root_folder = root_folder
        self.batch_dir: Path | None = None
        self.out_var = tk.StringVar(value=memory.load().get("out", ""))
        self.status_var.set(self.READY_TEXT)
        self.page_header(
            "準備待核對批次",
            "在最上方的根目錄底下找出每個 TDCC 的中文說明書與投資人須知，改成標準檔名放進新的批次資料夾；"
            "商品資料夾只讀不改。",
        )
        self.tdcc_hint(1)  # 從起單資料帶入的 TDCC
        card = self.card(2)
        self.tdcc_column(card)
        body = self.body(card, column=1)
        row = self.field(
            body,
            0,
            "輸出位置",
            self.out_var,
            lambda: ask_dir("選擇批次資料夾要開在哪裡", self.out_var),
            "每次在這裡開一個新的「待核對_日期時間」資料夾",
        )
        self.result_area(body, row)
        self.start_button = ttk.Button(self.buttons, text="開始準備", command=self.start, style="Primary.TButton")
        self.start_button.pack(side=tk.LEFT)
        self.open_button = ttk.Button(self.buttons, text="打開批次資料夾", command=self.open_batch, state=tk.DISABLED)
        self.open_button.pack(side=tk.LEFT, padx=8)
        self.next_button = NextButton(self.buttons, "下一步：核對 →", on_next)
        self.place_table(3)

    def receive_tdccs(self, tdccs: Sequence[str]) -> bool:
        """起單資料匯入完成後帶入成功匯入的 TDCC（#281）；回傳是否帶入。不切頁、不自動準備。"""
        return self.hand_in_tdccs(
            tdccs,
            "起單資料成功匯入的 TDCC",
            f"已帶入「2 起單資料」成功匯入的 TDCC {len(tdccs)} 筆，選好輸出位置後按「開始準備」。",
        )

    def _clear_results(self) -> None:
        """帶入新的 TDCC 前收起上一批（#286）：下一步、打開批次資料夾都是上一批的，狀態列回到說明。"""
        super()._clear_results()
        self.next_button.withdraw()
        self.batch_dir = None
        self.open_button.configure(state=tk.DISABLED)
        self.status_var.set(self.READY_TEXT)

    def _busy(self, busy: bool) -> None:
        """處理中不能再按；打開批次資料夾要等準備好了才能按。"""
        self.start_button.configure(state=tk.DISABLED if busy else tk.NORMAL)
        if busy:
            self.open_button.configure(state=tk.DISABLED)

    def start(self) -> None:
        text = self.tdcc_text.get("1.0", tk.END)
        paths = self.root_folder.with_output(self.out_var, "輸出位置")
        if paths is None:
            return
        root, out = paths

        def starting() -> None:
            self.memory.remember(out=str(out))
            self.next_button.withdraw()

        self.runner.run_maybe(  # 沒有連線設定或取消登入：照樣準備，不留步驟紀錄
            lambda settings: run_prepare(settings, text, root, out),
            "處理中…（在根目錄底下搜尋商品資料夾，公槽上可能要一點時間）",
            self._done,
            before=starting,
            database_error=lambda result: result.recorded.database_error,
        )

    def _done(self, result: PrepareResult) -> None:
        recorded, receipt = result.recorded, result.receipt
        for o in receipt.outcomes:
            self.add_row((o.tdcc, o.status, o.reason), problem=o.status != PREPARED)
        prepared = sum(o.status == PREPARED for o in receipt.outcomes)
        skipped = len(receipt.outcomes) - prepared
        from_word = sum(d.from_word for o in receipt.outcomes if o.status == PREPARED for d in o.documents)
        status = f"完成：{prepared} 個商品已準備、{skipped} 個跳過。批次資料夾：{receipt.batch_dir}"
        if receipt.unreadable_dirs:
            status += f"\n注意：搜尋時有 {len(receipt.unreadable_dirs)} 個資料夾讀不到（例如 {receipt.unreadable_dirs[0]}），裡面的商品資料夾找不到。"
        kpis = (
            ("已準備（檔）", prepared, False),
            ("跳過（檔）", skipped, skipped > 0),
            ("Word 轉檔（份）", from_word, False),
        )
        self.finish(kpis, status + record_note(recorded))
        self.batch_dir = receipt.batch_dir
        self.open_button.configure(state=tk.NORMAL)
        if self.on_prepared(receipt):  # 核對頁真的帶入了這批 PDF，才有下一步
            self.next_button.offer()

    def open_batch(self) -> None:
        if self.batch_dir is not None:
            open_folder(self.batch_dir)
