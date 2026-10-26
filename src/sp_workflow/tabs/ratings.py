"""「維護」選單的「發行機構評等…」（#237）：另開一個視窗，列出、新增、修改各上手發行機構與保證機構的評等。

左邊是機構（上手、角色、名稱與目前評等），右邊是點選機構的歷次評等（生效日由新到舊）。
新增評等的生效日要比最新一筆晚；只能修改、刪除最新一筆（比照審查通過日期）。每次按確定就存進資料庫、留紀錄，
再重讀全部顯示。規則與資料庫檢查的原因在 `sp_workflow.credit_ratings`，這裡只收輸入、在背景呼叫 `window_actions`。
"""

from __future__ import annotations

import datetime as dt
import tkinter as tk
from collections.abc import Callable, Sequence
from tkinter import messagebox, ttk

from fcn_checker.date_picker import DateEntry, parse_day
from fcn_checker.ui import DANGER, Background, FitTreeview, Scrollbar, WrapLabel
from sp_workflow.credit_ratings import (
    AGENCIES,
    INITIAL,
    ROLES,
    CreditRatingError,
    Entity,
    Rating,
    RatingRows,
    effective_label,
    entity_from,
    rating_from,
)
from sp_workflow.database import ConnectionSettings
from sp_workflow.tabs.base import TITLE, ActionRunner, BackgroundFactory, DatabaseLogin
from sp_workflow.window_actions import (
    add_credit_rating,
    add_rated_entity,
    delete_credit_rating,
    edit_credit_rating,
    load_rating_rows,
)

ENTITY_COLUMNS = (
    ("issuer", "上手", 80),
    ("role", "角色", 80),
    ("name", "名稱", 260),
    ("sp", "標準普爾", 80),
    ("moodys", "穆迪", 70),
    ("fitch", "惠譽", 70),
    ("since", "生效日", 100),
)
HISTORY_COLUMNS = (("since", "生效日", 100), ("sp", "標準普爾", 80), ("moodys", "穆迪", 70), ("fitch", "惠譽", 70))


def _ratings(rating: Rating | None) -> tuple[str, str, str]:
    if rating is None:
        return ("", "", "")
    return (rating.sp or "—", rating.moodys or "—", rating.fitch or "—")


class RatingsWindow(tk.Toplevel):
    """評等維護視窗：打開時讀一次，之後每次存檔後重讀；處理中按鈕不能按。"""

    def __init__(self, parent: tk.Misc, db: DatabaseLogin, *, background: BackgroundFactory = Background) -> None:
        super().__init__(parent)
        self.title(f"{TITLE} — 發行機構評等")
        self.background = background(self)
        self.rows = RatingRows((), ())
        frame = ttk.Frame(self, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(0, weight=3)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(1, weight=1)
        top = ttk.Frame(frame)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        WrapLabel(
            top,
            text="自算風險等級與說明書、投資人須知的評等核對都用這裡的評等，依交易日取生效日在那天以前的最新一筆。"
            "評等變了請新增一筆並填生效日；每次按確定就存進資料庫，並記下誰、何時、改前改後。",
            wraplength=760,
            justify="left",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.refresh_button = ttk.Button(top, text="重新整理", command=self.refresh)
        self.refresh_button.pack(side=tk.RIGHT)
        self.entities, entity_buttons = self._section(frame, 0, "機構（目前評等）", ENTITY_COLUMNS)
        self.history, history_buttons = self._section(frame, 1, "歷次評等", HISTORY_COLUMNS)
        self.buttons = [self.refresh_button]
        for parent, actions in (
            (entity_buttons, (("新增機構…", self.add_entity), ("新增評等…", self.add_rating))),
            (history_buttons, (("修改最新一筆…", self.edit_latest), ("刪除最新一筆…", self.delete_latest))),
        ):
            for text, command in actions:
                button = ttk.Button(parent, text=text, command=command)
                button.pack(side=tk.LEFT, padx=(0, 8))
                self.buttons.append(button)
        self.entities.bind("<<TreeviewSelect>>", lambda _: self._show_history())
        self.entities.bind("<Double-1>", lambda _: self.add_rating())
        self.status_var = tk.StringVar()
        self.runner = ActionRunner(db, self.background, self.status_var, self._busy, parent=self)
        WrapLabel(frame, textvariable=self.status_var, wraplength=1000, justify="left").grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0)
        )
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.geometry("1180x560")

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
        self.runner.run_action(load_rating_rows, "讀取評等中…", lambda rows: self._show(rows, notice))

    def _show(self, rows: RatingRows, notice: str = "", keep: tuple[str, str] | None = None) -> None:
        picked = keep or self._picked_key()
        self.rows = rows
        self.entities.delete(*self.entities.get_children())
        for index, entity in enumerate(rows.entities):
            latest = rows.latest(*entity.key)
            since = effective_label(latest.effective_date) if latest else ""
            values = (entity.issuer, entity.role, entity.name, *_ratings(latest), since)
            self.entities.insert("", tk.END, iid=f"e{index}", values=values)
            if entity.key == picked:
                self.entities.selection_set(f"e{index}")
        self._show_history()
        issuers = len({e.issuer for e in rows.entities})
        self.status_var.set(f"{notice}上手 {issuers} 家、機構 {len(rows.entities)} 個、評等 {len(rows.ratings)} 筆。")

    def _show_history(self) -> None:
        self.history.delete(*self.history.get_children())
        entity = self._selected_entity()
        if entity is None:
            return
        for index, rating in enumerate(reversed(self.rows.history(*entity.key))):
            values = (effective_label(rating.effective_date), *_ratings(rating))
            self.history.insert("", tk.END, iid=f"h{index}", values=values)

    def _selected_entity(self) -> Entity | None:
        selection = self.entities.selection()
        return self.rows.entities[int(selection[0][1:])] if selection else None

    def _picked_key(self) -> tuple[str, str] | None:
        entity = self._selected_entity()
        return entity.key if entity else None

    def _picked_entity(self) -> Entity | None:
        entity = self._selected_entity()
        if entity is None:
            messagebox.showinfo(TITLE, "請先在機構表點一個機構。", parent=self)
        return entity

    # ---------- 動作 ----------

    def add_entity(self) -> None:
        if self.working:
            return
        found = ask_entity(self)
        if found is None:
            return
        entity, first = found
        self._save(
            lambda s: add_rated_entity(s, entity, first),
            f"新增 {entity.issuer} 的{entity.role}「{entity.name}」",
            entity,
        )

    def add_rating(self) -> None:
        if self.working:
            return
        entity = self._picked_entity()
        if entity is None:
            return
        latest = self.rows.latest(*entity.key)
        after = ask_rating(self, entity, None, latest)
        if after is not None:
            label = f"新增 {entity.issuer} 的{entity.role}評等（生效日 {effective_label(after.effective_date)}）"
            self._save(lambda s: add_credit_rating(s, latest, after), label, entity)

    def edit_latest(self) -> None:
        if self.working:
            return
        entity = self._picked_entity()
        if entity is None:
            return
        before = self.rows.latest(*entity.key)
        if before is None:
            return
        after = ask_rating(self, entity, before)
        if after is None:
            return
        if after == before:
            self.status_var.set("沒有改任何欄位，這次沒有存。")
            return
        self._save(
            lambda s: edit_credit_rating(s, before, after), f"修改 {entity.issuer} 的{entity.role}最新評等", entity
        )

    def delete_latest(self) -> None:
        if self.working:
            return
        entity = self._picked_entity()
        if entity is None:
            return
        before = self.rows.latest(*entity.key)
        if before is None:
            return
        if len(self.rows.history(*entity.key)) == 1:
            messagebox.showinfo(TITLE, "每個機構至少要留一筆評等，不能刪掉唯一的一筆。", parent=self)
            return
        shown = "／".join(_ratings(before))
        if not messagebox.askyesno(
            TITLE,
            f"刪除 {entity.issuer} 的{entity.role}在 {effective_label(before.effective_date)} 生效的評等（{shown}）？",
            icon=messagebox.WARNING,
            default=messagebox.NO,
            parent=self,
        ):
            return
        self._save(lambda s: delete_credit_rating(s, before), f"刪除 {entity.issuer} 的{entity.role}最新評等", entity)

    # ---------- 背景執行 ----------

    def _save(self, action: Callable[[ConnectionSettings], RatingRows], done: str, entity: Entity) -> None:
        """存檔後重讀並停在同一個機構；寫不進去（`CreditRatingError`）時原因寫在狀態列並跳出來。"""
        self.runner.run_action(action, "存檔中…", lambda rows: self._show(rows, f"已存：{done}。", keep=entity.key))

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
        self.row = 0

    def entry(self, label: str, value: str, width: int = 40) -> tk.StringVar:
        var = tk.StringVar(value=value)
        ttk.Label(self.body, text=label).grid(row=self.row, column=0, sticky="w", pady=(0, 8), padx=(0, 12))
        field = ttk.Entry(self.body, textvariable=var, width=width)
        field.grid(row=self.row, column=1, sticky="w" if width < 40 else "ew", pady=(0, 8))
        self.fields.append(field)
        self.row += 1
        return var

    def rating_fields(self, before: Rating | None) -> list[tk.StringVar]:
        values = (before.sp, before.moodys, before.fitch) if before else (None, None, None)
        return [self.entry(agency, value or "", width=10) for agency, value in zip(AGENCIES, values, strict=True)]

    def day_field(self, label: str, value: str) -> tk.StringVar:
        var = tk.StringVar(value=value)
        ttk.Label(self.body, text=label).grid(row=self.row, column=0, sticky="w", pady=(0, 8), padx=(0, 12))
        day = DateEntry(self.body, var, width=14)
        day.grid(row=self.row, column=1, sticky="w", pady=(0, 8))
        self.fields.append(day.entry)
        self.row += 1
        return var

    def note(self, text: str) -> None:
        WrapLabel(self.body, text=text, wraplength=400, justify="left").grid(
            row=self.row, column=0, columnspan=2, sticky="w", pady=(0, 4)
        )
        self.row += 1

    def finish_layout(self) -> None:
        ttk.Label(self.body, textvariable=self.error_var, foreground=DANGER).grid(
            row=self.row, column=0, columnspan=2, sticky="w"
        )
        buttons = ttk.Frame(self.body)
        buttons.grid(row=self.row + 1, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="確定", command=self.confirm).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side=tk.LEFT)
        self.bind("<Escape>", lambda _: self.destroy())
        self.bind("<Return>", lambda _: self.confirm())
        self.update_idletasks()  # 置中在評等視窗上
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
        except CreditRatingError as exc:
            self.error_var.set(str(exc))
            return
        self.destroy()


def read_day(text: str, *, blank: dt.date | None = None) -> dt.date:
    """生效日欄：YYYY-MM-DD；`blank` 有值時空白（或「最初」）就是它。"""
    if blank is not None and text.strip() in ("", "最初"):
        return blank
    day = parse_day(text)
    if day is None:
        raise CreditRatingError("生效日請填 YYYY-MM-DD（可以點右邊的小月曆）。")
    return day


class RatingForm(_Form):
    """新增（before 是 None）或修改最新一筆評等。第一批評等（最初）的生效日不能改。

    新增時三家評等先帶入 `latest`（打開時看到的最新一筆），只改變了的那家就好；空白是「沒有該家評等」，不是「沒變」。
    """

    def __init__(self, parent: tk.Misc, entity: Entity, before: Rating | None, latest: Rating | None = None) -> None:
        super().__init__(parent, f"{'修改最新一筆' if before else '新增'}評等：{entity.issuer} {entity.role}")
        self.entity = entity
        self.before = before
        ttk.Label(self.body, text=entity.name).grid(row=self.row, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self.row += 1
        self.initial = before is not None and before.effective_date == INITIAL
        if self.initial:
            self.day_var = tk.StringVar(value="最初")
            ttk.Label(self.body, text="生效日").grid(row=self.row, column=0, sticky="w", pady=(0, 8), padx=(0, 12))
            ttk.Label(self.body, text="最初（第一批評等，不能改）").grid(
                row=self.row, column=1, sticky="w", pady=(0, 8)
            )
            self.row += 1
        else:
            self.day_var = self.day_field("生效日", before.effective_date.isoformat() if before else "")
        self.agency_vars = self.rating_fields(before or latest)
        self.note("沒有該家評等就留空，三家至少填一家；格式像 AA-、A+（標普、惠譽）或 Aa2、A1（穆迪）。")
        self.finish_layout()

    def read(self) -> Rating:
        day = INITIAL if self.initial else read_day(self.day_var.get())
        return rating_from(self.entity.issuer, self.entity.role, day, *(v.get() for v in self.agency_vars))


class EntityForm(_Form):
    """新增一個機構（新上手用）和它的第一筆評等；生效日空白就是「最初」。"""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent, "新增機構")
        self.issuer_var = self.entry("上手代碼", "", width=12)
        ttk.Label(self.body, text="角色").grid(row=self.row, column=0, sticky="w", pady=(0, 8), padx=(0, 12))
        self.role_var = tk.StringVar(value=ROLES[0])
        ttk.Combobox(self.body, textvariable=self.role_var, values=list(ROLES), state="readonly", width=10).grid(
            row=self.row, column=1, sticky="w", pady=(0, 8)
        )
        self.row += 1
        self.name_var = self.entry("名稱", "")
        self.day_var = self.day_field("生效日", "")
        self.agency_vars = self.rating_fields(None)
        self.note("上手代碼同上手編號對照（BARC、CITI…）。生效日空白表示不設起始日（最初）。")
        self.finish_layout()

    def read(self) -> tuple[Entity, Rating]:
        entity = entity_from(self.issuer_var.get(), self.role_var.get(), self.name_var.get())
        day = read_day(self.day_var.get(), blank=INITIAL)
        return entity, rating_from(entity.issuer, entity.role, day, *(v.get() for v in self.agency_vars))


def _ask(form: _Form) -> object:
    form.grab_set()
    form.master.wait_window(form)
    return form.result


def ask_rating(parent: tk.Misc, entity: Entity, before: Rating | None, latest: Rating | None = None) -> Rating | None:
    """新增（before 是 None，三家評等先帶入 `latest`）或修改最新一筆評等的輸入；取消時 None。"""
    found = _ask(RatingForm(parent, entity, before, latest))
    return found if isinstance(found, Rating) else None


def ask_entity(parent: tk.Misc) -> tuple[Entity, Rating] | None:
    """新增機構的輸入：(機構, 第一筆評等)；取消時 None。"""
    found = _ask(EntityForm(parent))
    return found if isinstance(found, tuple) else None
