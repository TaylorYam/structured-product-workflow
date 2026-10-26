"""「維護」選單的「信箱維護…」（#158）：另開一個視窗，列出、新增、修改、刪除人員與別名，不必寫 SQL。

左邊是人員（名字、信箱、單位、固定副本、別名），右邊是別名（別名 → 人員）；每次按確定就存進資料庫、留紀錄，
再重讀全部顯示。寫入規則與資料庫檢查的原因在 `sp_workflow.mailbox`，這裡只收輸入、在背景呼叫 `window_actions`。
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Sequence
from tkinter import messagebox, ttk

from fcn_checker.ui import DANGER, Background, FitTreeview, Scrollbar, WrapLabel
from sp_workflow.database import ConnectionSettings
from sp_workflow.mailbox import Alias, MailboxError, MailboxRows, Person, alias_from, person_from
from sp_workflow.tabs.base import TITLE, ActionRunner, BackgroundFactory, DatabaseLogin
from sp_workflow.window_actions import (
    delete_mailbox_alias,
    delete_mailbox_person,
    load_mailbox_rows,
    save_mailbox_alias,
    save_mailbox_person,
)

PEOPLE_COLUMNS = (("name", "名字", 140), ("email", "信箱", 220), ("unit", "單位", 80), ("cc", "固定副本", 70))
ALIAS_COLUMNS = (("alias", "別名", 160), ("person", "人員", 140))


class MailboxWindow(tk.Toplevel):
    """信箱維護視窗：打開時讀一次，之後每次存檔後重讀；處理中按鈕不能按。"""

    def __init__(self, parent: tk.Misc, db: DatabaseLogin, *, background: BackgroundFactory = Background) -> None:
        super().__init__(parent)
        self.title(f"{TITLE} — 信箱維護")
        self.background = background(self)
        self.rows = MailboxRows((), ())
        frame = ttk.Frame(self, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(0, weight=3)
        frame.columnconfigure(1, weight=2)
        frame.rowconfigure(1, weight=1)
        top = ttk.Frame(frame)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        WrapLabel(
            top,
            text="交易明細的 FC 名字用這裡比對信箱（不分大小寫，先查別名）；寄信草稿的副本是勾了固定副本的人。"
            "每次按確定就存進資料庫，並記下誰、何時、改前改後。",
            wraplength=640,
            justify="left",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.refresh_button = ttk.Button(top, text="重新整理", command=self.refresh)
        self.refresh_button.pack(side=tk.RIGHT)
        self.people, people_buttons = self._section(frame, 0, "人員", PEOPLE_COLUMNS + (("aliases", "別名", 160),))
        self.aliases, alias_buttons = self._section(frame, 1, "別名", ALIAS_COLUMNS)
        self.buttons = [self.refresh_button]
        for parent, actions in (
            (
                people_buttons,
                (("新增人員…", self.add_person), ("修改…", self.edit_person), ("刪除…", self.delete_person)),
            ),
            (alias_buttons, (("新增別名…", self.add_alias), ("修改…", self.edit_alias), ("刪除…", self.delete_alias))),
        ):
            for text, command in actions:
                button = ttk.Button(parent, text=text, command=command)
                button.pack(side=tk.LEFT, padx=(0, 8))
                self.buttons.append(button)
        self.people.bind("<Double-1>", lambda _: self.edit_person())
        self.aliases.bind("<Double-1>", lambda _: self.edit_alias())
        self.status_var = tk.StringVar()
        self.runner = ActionRunner(db, self.background, self.status_var, self._busy, parent=self)
        WrapLabel(frame, textvariable=self.status_var, wraplength=900, justify="left").grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0)
        )
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.geometry("1000x560")

    def _section(
        self, frame: ttk.Frame, column: int, title: str, columns: Sequence[tuple[str, str, int]]
    ) -> tuple[ttk.Treeview, ttk.Frame]:
        box = ttk.LabelFrame(frame, text=title, padding=8)
        box.grid(row=1, column=column, sticky="nsew", padx=(0, 12) if column == 0 else 0)
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        table = FitTreeview(box, columns=[c for c, _, _ in columns], show="headings", selectmode="browse")
        for col, label, width in columns:
            table.heading(col, text=label)
            table.column(col, width=width, stretch=col == columns[-1][0])
        table.grid(row=0, column=0, sticky="nsew")
        scroll = Scrollbar(box, orient=tk.VERTICAL, command=table.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        table.configure(yscrollcommand=scroll.set)
        buttons = ttk.Frame(box)
        buttons.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        return table, buttons

    # ---------- 讀與顯示 ----------

    @property
    def working(self) -> bool:
        return self.runner.working

    def refresh(self, notice: str = "") -> None:
        """重新讀資料庫；`notice` 是剛存好的那一筆，寫在狀態列。"""
        self.runner.run_action(load_mailbox_rows, "讀取信箱中…", lambda rows: self._show(rows, notice))

    def _show(self, rows: MailboxRows, notice: str = "") -> None:
        self.rows = rows
        self.people.delete(*self.people.get_children())
        self.aliases.delete(*self.aliases.get_children())
        for index, person in enumerate(rows.people):
            names = "、".join(a.alias for a in rows.aliases_of(person.name))
            values = (person.name, person.email, person.unit or "", "是" if person.fixed_cc else "", names)
            self.people.insert("", tk.END, iid=f"p{index}", values=values)
        for index, alias in enumerate(rows.aliases):
            self.aliases.insert("", tk.END, iid=f"a{index}", values=(alias.alias, alias.person_name))
        cc = sum(p.fixed_cc for p in rows.people)
        summary = f"人員 {len(rows.people)} 位（固定副本 {cc} 位）、別名 {len(rows.aliases)} 個。"
        if not cc:
            summary += "沒有任何固定副本，寄信草稿會停下來。"
        self.status_var.set(f"{notice}{summary}")

    def _picked_person(self) -> Person | None:
        selection = self.people.selection()
        if not selection:
            messagebox.showinfo(TITLE, "請先在人員表點一個人。", parent=self)
            return None
        return self.rows.people[int(selection[0][1:])]

    def _picked_alias(self) -> Alias | None:
        selection = self.aliases.selection()
        if not selection:
            messagebox.showinfo(TITLE, "請先在別名表點一個別名。", parent=self)
            return None
        return self.rows.aliases[int(selection[0][1:])]

    # ---------- 人員 ----------

    def add_person(self) -> None:
        if self.working:
            return
        after = ask_person(self, None)
        if after is not None:
            self._save(lambda s: save_mailbox_person(s, None, after), f"新增人員「{after.name}」")

    def edit_person(self) -> None:
        if self.working:
            return
        before = self._picked_person()
        if before is None:
            return
        after = ask_person(self, before)
        if after is None:
            return
        if after == before:
            self.status_var.set("沒有改任何欄位，這次沒有存。")
            return
        self._save(lambda s: save_mailbox_person(s, before, after), f"修改人員「{after.name}」")

    def delete_person(self) -> None:
        if self.working:
            return
        before = self._picked_person()
        if before is None:
            return
        aliases = self.rows.aliases_of(before.name)
        also = f"\n他的別名也會一起刪掉：{'、'.join(a.alias for a in aliases)}。" if aliases else ""
        if not messagebox.askyesno(
            TITLE,
            f"刪除人員「{before.name}」（{before.email}）？{also}\n交易明細上寫這個名字的 FC 之後會對不到信箱。",
            icon=messagebox.WARNING,
            default=messagebox.NO,
            parent=self,
        ):
            return
        self._save(lambda s: delete_mailbox_person(s, before), f"刪除人員「{before.name}」")

    # ---------- 別名 ----------

    def add_alias(self) -> None:
        if self.working:
            return
        if not self.rows.people:
            messagebox.showinfo(TITLE, "信箱還沒有任何人員，請先新增人員。", parent=self)
            return
        after = ask_alias(self, None, [p.name for p in self.rows.people])
        if after is not None:
            self._save(lambda s: save_mailbox_alias(s, None, after), f"新增別名「{after.alias}」")

    def edit_alias(self) -> None:
        if self.working:
            return
        before = self._picked_alias()
        if before is None:
            return
        after = ask_alias(self, before, [p.name for p in self.rows.people])
        if after is None:
            return
        if after == before:
            self.status_var.set("沒有改任何欄位，這次沒有存。")
            return
        self._save(lambda s: save_mailbox_alias(s, before, after), f"修改別名「{after.alias}」")

    def delete_alias(self) -> None:
        if self.working:
            return
        before = self._picked_alias()
        if before is None or not messagebox.askyesno(
            TITLE, f"刪除別名「{before.alias}」（→ {before.person_name}）？", default=messagebox.NO, parent=self
        ):
            return
        self._save(lambda s: delete_mailbox_alias(s, before), f"刪除別名「{before.alias}」")

    # ---------- 背景執行 ----------

    def _save(self, action: Callable[[ConnectionSettings], MailboxRows], done: str) -> None:
        """存檔後重讀；寫不進去（`MailboxError`）時原因寫在狀態列並跳出來。"""
        self.runner.run_action(action, "存檔中…", lambda rows: self._show(rows, f"已存：{done}。"))

    def _busy(self, busy: bool) -> None:
        for button in self.buttons:
            button.configure(state=tk.DISABLED if busy else tk.NORMAL)

    def close(self) -> None:
        self.background.close()
        self.destroy()


class _Form(tk.Toplevel):
    """新增、修改用的小視窗：按確定（或 Enter）時讀輸入，有錯寫在視窗裡、不關；取消（或 Esc）關掉。"""

    def __init__(self, parent: tk.Misc, title: str) -> None:
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.result: object = None
        self.body = ttk.Frame(self, padding=16)
        self.body.pack(fill=tk.BOTH, expand=True)
        self.body.columnconfigure(1, weight=1)
        self.error_var = tk.StringVar()
        self.fields: list[tk.Widget] = []

    def entry(self, row: int, label: str, value: str) -> tk.StringVar:
        var = tk.StringVar(value=value)
        ttk.Label(self.body, text=label).grid(row=row, column=0, sticky="w", pady=(0, 8), padx=(0, 12))
        field = ttk.Entry(self.body, textvariable=var, width=40)
        field.grid(row=row, column=1, sticky="ew", pady=(0, 8))
        self.fields.append(field)
        return var

    def finish_layout(self, row: int) -> None:
        ttk.Label(self.body, textvariable=self.error_var, foreground=DANGER).grid(
            row=row, column=0, columnspan=2, sticky="w"
        )
        buttons = ttk.Frame(self.body)
        buttons.grid(row=row + 1, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="確定", command=self.confirm).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side=tk.LEFT)
        self.bind("<Escape>", lambda _: self.destroy())
        self.bind("<Return>", lambda _: self.confirm())
        self.update_idletasks()  # 置中在信箱維護視窗上
        top = self.master.winfo_toplevel()
        x = top.winfo_rootx() + (top.winfo_width() - self.winfo_width()) // 2
        y = top.winfo_rooty() + (top.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.fields[0].focus_set()

    def read(self) -> object:
        raise NotImplementedError

    def confirm(self) -> None:
        try:
            self.result = self.read()
        except MailboxError as exc:
            self.error_var.set(str(exc))
            return
        self.destroy()


class PersonForm(_Form):
    def __init__(self, parent: tk.Misc, before: Person | None) -> None:
        super().__init__(parent, "修改人員" if before else "新增人員")
        self.name_var = self.entry(0, "名字", before.name if before else "")
        self.email_var = self.entry(1, "信箱", before.email if before else "")
        self.unit_var = self.entry(2, "單位", (before.unit or "") if before else "")
        self.cc_var = tk.BooleanVar(value=bool(before and before.fixed_cc))
        ttk.Checkbutton(self.body, text="固定副本（每封寄信草稿都副本給他）", variable=self.cc_var).grid(
            row=3, column=1, sticky="w", pady=(0, 8)
        )
        if before:
            note = "改名時他的別名會跟著改。名字不分大小寫不能和別人重複。"
        else:
            note = "名字要和交易明細上的 FC 名字一樣（不分大小寫）；寫法不同時另加別名。"
        WrapLabel(self.body, text=note, wraplength=380, justify="left").grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(0, 4)
        )
        self.finish_layout(5)

    def read(self) -> Person:
        return person_from(self.name_var.get(), self.email_var.get(), self.unit_var.get(), self.cc_var.get())


class AliasForm(_Form):
    def __init__(self, parent: tk.Misc, before: Alias | None, names: Sequence[str]) -> None:
        super().__init__(parent, "修改別名" if before else "新增別名")
        self.alias_var = self.entry(0, "別名", before.alias if before else "")
        ttk.Label(self.body, text="人員").grid(row=1, column=0, sticky="w", pady=(0, 8), padx=(0, 12))
        self.person_var = tk.StringVar(value=before.person_name if before else "")
        self.person_box = ttk.Combobox(self.body, textvariable=self.person_var, values=list(names), state="readonly")
        self.person_box.grid(row=1, column=1, sticky="ew", pady=(0, 8))
        WrapLabel(
            self.body,
            text="別名是交易明細上的另一種寫法（不分大小寫），比對時先查別名。",
            wraplength=380,
            justify="left",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 4))
        self.finish_layout(3)

    def read(self) -> Alias:
        return alias_from(self.alias_var.get(), self.person_var.get())


def _ask(form: _Form) -> object:
    form.grab_set()
    form.master.wait_window(form)
    return form.result


def ask_person(parent: tk.Misc, before: Person | None) -> Person | None:
    """新增（before 是 None）或修改一個人的輸入；取消時 None。"""
    found = _ask(PersonForm(parent, before))
    return found if isinstance(found, Person) else None


def ask_alias(parent: tk.Misc, before: Alias | None, names: Sequence[str]) -> Alias | None:
    """新增或修改一個別名的輸入；人員從信箱裡的名字選。取消時 None。"""
    found = _ask(AliasForm(parent, before, names))
    return found if isinstance(found, Alias) else None
