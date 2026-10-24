"""「6 寄信草稿」分頁：貼核對通過的 TDCC，在 Outlook 建 TS／IIS 草稿給承作的 FC（#29），同時留步驟紀錄（#151）。"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from fcn_checker.ui import Background
from sp_workflow.daily_import import ImportInfo
from sp_workflow.database import OperatorError
from sp_workflow.local_settings import FolderMemory
from sp_workflow.mail_drafts import DRAFTED, NOT_NEEDED, mail_template_from_env
from sp_workflow.tabs.base import TITLE, BackgroundFactory, DatabaseLogin, RootFolder, Tab, ask_dir, record_note
from sp_workflow.window_actions import DraftResult, make_mail_drafts, read_latest_import


class MailDraftTab(Tab):
    def __init__(
        self,
        notebook: ttk.Notebook,
        memory: FolderMemory,
        db: DatabaseLogin,
        root_folder: RootFolder,
        *,
        background: BackgroundFactory = Background,
    ) -> None:
        columns = (("tdcc", "TDCC", 130), ("status", "狀態", 80), ("to", "收件人", 160), ("reason", "原因", 380))
        super().__init__(notebook, "寄信草稿", columns, db, background)
        self.memory = memory
        self.root_folder = root_folder
        self.out_var = tk.StringVar(value=memory.load().get("mail_out", ""))
        self.status_var.set(
            "貼上核對通過的 TDCC，選結果清單位置，按「建立草稿」（在最上方的根目錄底下找商品資料夾）。只建草稿，不會寄出。"
        )

        self.page_header(
            "建立寄信草稿",
            "依資料庫的承作 FC 與信箱，在 Outlook 建 TS／IIS 寄信草稿；只建草稿，不會寄出。",
        )
        card = self.card(1)
        self.tdcc_column(card)
        body = self.body(card, column=1)
        row = self.field(
            body,
            0,
            "結果清單位置",
            self.out_var,
            lambda: ask_dir("選擇寄信草稿清單要存在哪裡", self.out_var),
            "寄信草稿清單存在這裡",
        )
        self.result_area(body, row)
        self.start_button = ttk.Button(self.buttons, text="建立草稿", command=self.start, style="Primary.TButton")
        self.start_button.pack(side=tk.LEFT)
        self.place_table(2)

    def _busy(self, busy: bool) -> None:
        self.start_button.configure(state=tk.DISABLED if busy else tk.NORMAL)

    def start(self) -> None:
        text = self.tdcc_text.get("1.0", tk.END)
        paths = self.root_folder.with_output(self.out_var, "結果清單位置")
        if paths is None:
            return
        root, out = paths
        try:
            template_path = mail_template_from_env(self.db.env_path)
        except OperatorError as exc:  # 連線設定檔或範本有問題
            messagebox.showerror(TITLE, str(exc), parent=self.master)
            return

        def confirm(info: ImportInfo | None) -> None:
            # 先讓作業人員看到用的是哪一次每日匯入，確認後才建草稿，避免用到舊資料
            question = (
                f"最近一次每日匯入：{_import_text(info)}\n\n要用這份資料建立草稿嗎？"
                if info is not None
                else "資料庫還沒有任何每日匯入紀錄。仍要繼續嗎？"
            )
            if not messagebox.askyesno(TITLE, question, parent=self.master):
                self.status_var.set("已取消，沒有建立任何草稿。")
                return
            self.runner.run_action(
                lambda settings: make_mail_drafts(settings, text, root, out, template_path=template_path),
                "建立草稿中…（在根目錄底下搜尋商品資料夾）",
                self._done,
            )

        self.runner.run_action(
            read_latest_import,
            "讀取最近一次每日匯入…",
            confirm,
            before=lambda: self.memory.remember(mail_out=str(out)),
        )

    def _done(self, result: DraftResult) -> None:
        recorded, receipt = result.recorded, result.receipt
        problems = [o.status not in (DRAFTED, NOT_NEEDED) for o in receipt.outcomes]  # 不需寄送的不算問題
        for o, problem in zip(receipt.outcomes, problems, strict=True):
            self.add_row((o.tdcc, o.status, "、".join(o.to), o.reason), problem=problem)
        drafted = sum(o.status == DRAFTED for o in receipt.outcomes)
        not_drafted = len(receipt.outcomes) - drafted
        last = _import_text(receipt.last_import) if receipt.last_import else "沒有匯入紀錄"
        status = (
            f"完成：{drafted} 封草稿已建立（在 Outlook 草稿匣，請確認後再寄出）、"
            f"{not_drafted} 檔沒有建立。最近一次每日匯入：{last}。結果清單：{receipt.list_path}"
        )
        if receipt.unreadable_dirs:
            status += f"\n注意：搜尋時有 {len(receipt.unreadable_dirs)} 個資料夾讀不到（例如 {receipt.unreadable_dirs[0]}），裡面的商品資料夾找不到。"
        status += record_note(recorded)
        self.finish((("已建草稿（檔）", drafted, False), ("沒有建立（檔）", not_drafted, any(problems))), status)


def _import_text(info: ImportInfo) -> str:
    return f"{info.imported_at:%Y-%m-%d %H:%M}（第 {info.run_id} 次，{info.file_name}）"
