"""「3 準備」分頁：貼 TDCC、選輸出位置，產生批次資料夾與準備清單；登入了資料庫時同時留步驟紀錄（#151）。"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import ttk

from fcn_checker.ui import NextButton
from sp_workflow.local_settings import FolderMemory
from sp_workflow.preparation import PREPARED, PrepareReceipt
from sp_workflow.tabs.base import DatabaseLogin, RootFolder, Tab, ask_dir, open_folder, record_note
from sp_workflow.window_actions import PrepareResult, run_prepare


class PrepareTab(Tab):
    def __init__(
        self,
        notebook: ttk.Notebook,
        memory: FolderMemory,
        db: DatabaseLogin,
        root_folder: RootFolder,
        on_prepared: Callable[[PrepareReceipt], bool],
        on_next: Callable[[], None],
    ) -> None:
        super().__init__(notebook, "準備", (("tdcc", "TDCC", 130), ("status", "狀態", 70), ("reason", "原因", 520)))
        self.on_prepared = on_prepared
        self.memory = memory
        self.db = db
        self.root_folder = root_folder
        self.batch_dir: Path | None = None
        self.out_var = tk.StringVar(value=memory.load().get("out", ""))
        self.status_var.set("貼上 TDCC，選好輸出位置後按「開始準備」（在最上方的根目錄底下找商品資料夾）。")
        self.page_header(
            "準備待核對批次",
            "在最上方的根目錄底下找出每個 TDCC 的中文說明書與投資人須知，改成標準檔名放進新的批次資料夾；"
            "商品資料夾只讀不改。",
        )
        card = self.card(1)
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
        self.place_table(2)

    def start(self) -> None:
        text = self.tdcc_text.get("1.0", tk.END)
        paths = self.root_folder.with_output(self.out_var, "輸出位置")
        if paths is None:
            return
        root, out = paths
        settings = self.db.settings(quiet=True)  # 沒有連線設定或取消登入：照樣準備，不留步驟紀錄
        self.memory.remember(out=str(out))
        self.start_button.configure(state=tk.DISABLED)
        self.open_button.configure(state=tk.DISABLED)
        self.next_button.withdraw()
        self.restart()
        self.run_in_background(
            lambda: run_prepare(settings, text, root, out),
            "處理中…（在根目錄底下搜尋商品資料夾，公槽上可能要一點時間）",
            self._done,
        )

    def _done(self, result: object) -> None:
        self.start_button.configure(state=tk.NORMAL)
        if not isinstance(result, PrepareResult):
            return
        self.db.after_run(result.recorded.database_error)
        recorded, result = result.recorded, result.receipt
        for o in result.outcomes:
            self.add_row((o.tdcc, o.status, o.reason), problem=o.status != PREPARED)
        prepared = sum(o.status == PREPARED for o in result.outcomes)
        skipped = len(result.outcomes) - prepared
        from_word = sum(d.from_word for o in result.outcomes if o.status == PREPARED for d in o.documents)
        status = f"完成：{prepared} 個商品已準備、{skipped} 個跳過。批次資料夾：{result.batch_dir}"
        if result.unreadable_dirs:
            status += f"\n注意：搜尋時有 {len(result.unreadable_dirs)} 個資料夾讀不到（例如 {result.unreadable_dirs[0]}），裡面的商品資料夾找不到。"
        kpis = (
            ("已準備（檔）", prepared, False),
            ("跳過（檔）", skipped, skipped > 0),
            ("Word 轉檔（份）", from_word, False),
        )
        self.finish(kpis, status + record_note(recorded))
        self.batch_dir = result.batch_dir
        self.open_button.configure(state=tk.NORMAL)
        if self.on_prepared(result):  # 核對頁真的帶入了這批 PDF，才有下一步
            self.next_button.offer()

    def open_batch(self) -> None:
        if self.batch_dir is not None:
            open_folder(self.batch_dir)
