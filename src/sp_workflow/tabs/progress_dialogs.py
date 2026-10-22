"""「8 進度」登記用的小視窗（#157）：顧客贖回（事件日、備註）、提出異議（選一筆自動事件、備註）、解除異議（選一筆）。

`ask_*` 開視窗、等作業人員按確定或取消，回傳輸入的值或 None；檢查輸入的規則在 `parse_*`，不碰 Tk。
"""

from __future__ import annotations

import datetime as dt
import tkinter as tk
from collections.abc import Sequence
from tkinter import ttk

from sp_workflow.lifecycle import Objection, StoredEvent


def tdcc_list(tdccs: Sequence[str], limit: int = 10) -> str:
    """寫在訊息裡的 TDCC：太多時只列前面幾檔，後面寫「等 N 檔」。"""
    if len(tdccs) <= limit:
        return "、".join(tdccs)
    return f"{'、'.join(tdccs[:limit])} 等 {len(tdccs)} 檔"


def parse_redemption(day_text: str) -> tuple[dt.date | None, str]:
    """(事件日, 錯誤說明)：事件日必填、格式 YYYY-MM-DD。"""
    text = day_text.strip()
    if not text:
        return None, "請填事件日（顧客贖回的日期）。"
    try:
        return dt.date.fromisoformat(text), ""
    except ValueError:
        return None, f"事件日「{text}」不是 YYYY-MM-DD。"


def event_label(event: StoredEvent) -> str:
    parts = (event.event, event.underlying, f"{event.event_date}" if event.event_date else "沒有事件日")
    return " ".join(part for part in parts if part)


def objection_label(objection: Objection) -> str:
    when = f"{objection.raised_at.astimezone():%Y-%m-%d %H:%M}"
    target = f"{objection.event} {objection.event_date or ''}".strip()
    return f"{target}：{objection.note}（{objection.raised_by}，{when}）"


class FormDialog(tk.Toplevel):
    """一個選項清單（可以沒有）、事件日（可以沒有）與備註的小視窗；按確定（或 Enter）時檢查，有錯寫在視窗裡、不關。"""

    def __init__(
        self,
        parent: tk.Misc,
        title: str,
        intro: str,
        *,
        choices: Sequence[str] = (),
        with_day: bool = False,
        with_note: bool = True,
        note_required: bool = False,
    ) -> None:
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.result: tuple[int | None, dt.date | None, str] | None = None
        self.note_required = note_required
        self.with_day = with_day
        self.has_choices = bool(choices)
        frame = ttk.Frame(self, padding=16)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text=intro, wraplength=460, justify="left").grid(row=0, column=0, columnspan=2, sticky="w")
        row = 1
        fields: list[tk.Widget] = []
        self.choice_list = tk.Listbox(frame, height=min(max(len(choices), 1), 8), width=60, exportselection=False)
        if choices:
            fields.append(self.choice_list)
            for text in choices:
                self.choice_list.insert(tk.END, text)
            self.choice_list.selection_set(0)
            self.choice_list.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(12, 0))
            row += 1
        self.day_var = tk.StringVar()
        if with_day:
            ttk.Label(frame, text="事件日").grid(row=row, column=0, sticky="w", pady=(12, 0))
            day = ttk.Entry(frame, textvariable=self.day_var, width=14)
            day.grid(row=row, column=1, sticky="w", pady=(12, 0))
            fields.append(day)
            row += 1
        self.note_var = tk.StringVar()
        if with_note:
            ttk.Label(frame, text="備註").grid(row=row, column=0, sticky="w", pady=(12, 0))
            note = ttk.Entry(frame, textvariable=self.note_var, width=50)
            note.grid(row=row, column=1, sticky="ew", pady=(12, 0))
            fields.append(note)
            row += 1
        self.error_var = tk.StringVar()
        ttk.Label(frame, textvariable=self.error_var, foreground="#b42318").grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )
        buttons = ttk.Frame(frame)
        buttons.grid(row=row + 1, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="確定", command=self.confirm).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="left")
        self.bind("<Escape>", lambda _: self.destroy())
        self.bind("<Return>", lambda _: self.confirm())
        self.update_idletasks()  # 置中在主視窗上
        top = parent.winfo_toplevel()
        x = top.winfo_rootx() + (top.winfo_width() - self.winfo_width()) // 2
        y = top.winfo_rooty() + (top.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        if fields:
            fields[0].focus_set()
        self.grab_set()

    def confirm(self) -> None:
        choice = None
        if self.has_choices:
            picked = self.choice_list.curselection()
            if not picked:
                self.error_var.set("請先選一筆。")
                return
            choice = picked[0]
        day = None
        if self.with_day:
            day, error = parse_redemption(self.day_var.get())
            if error:
                self.error_var.set(error)
                return
        note = self.note_var.get().strip()
        if self.note_required and not note:
            self.error_var.set("請填備註（上手通知的內容或哪裡不同）。")
            return
        self.result = (choice, day, note)
        self.destroy()


def _ask(dialog: FormDialog) -> tuple[int | None, dt.date | None, str] | None:
    dialog.master.wait_window(dialog)
    return dialog.result


def ask_redemption(parent: tk.Misc, tdccs: Sequence[str]) -> tuple[dt.date, str] | None:
    """顧客贖回的 (事件日, 備註)；取消時 None。"""
    found = _ask(
        FormDialog(
            parent,
            "登記顧客贖回",
            f"登記 {len(tdccs)} 檔顧客贖回：{tdcc_list(tdccs)}。已有存續事件的不能登記；登記後不再比價。",
            with_day=True,
        )
    )
    if found is None or found[1] is None:
        return None
    return found[1], found[2]


def ask_objection(parent: tk.Misc, tdcc: str, events: Sequence[StoredEvent]) -> tuple[int, str] | None:
    """對哪一筆自動事件提出異議的 (事件編號, 備註)；取消時 None。"""
    found = _ask(
        FormDialog(
            parent,
            "提出異議",
            f"{tdcc}：選一筆比價產生的事件，寫下上手通知的內容或哪裡不同。異議不改事件，進度頁顯示待確認。",
            choices=[event_label(e) for e in events],
            note_required=True,
        )
    )
    if found is None or found[0] is None:
        return None
    return events[found[0]].event_id, found[2]


def ask_resolution(parent: tk.Misc, tdcc: str, objections: Sequence[Objection]) -> int | None:
    """要解除的異議編號；取消時 None。"""
    found = _ask(
        FormDialog(
            parent,
            "解除異議",
            f"{tdcc}：選一筆要解除的異議（修好資料或程式、確認沒問題之後）。會記下解除的人與時間。",
            choices=[objection_label(o) for o in objections],
            with_note=False,
        )
    )
    if found is None or found[0] is None:
        return None
    return objections[found[0]].objection_id
