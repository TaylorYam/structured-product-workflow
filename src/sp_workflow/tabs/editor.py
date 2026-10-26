"""起單資料與人工維護兩頁右邊的編輯區（#86、#115、#116）：商品條件的欄位 `OrderEditor` 與接在底下的交易明細 `TradeRowsEditor`。"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Collection, Iterable, Mapping
from tkinter import ttk

from fcn_checker.ui import BODY_FONT, CAPTION_FONT, DANGER, SECTION_FONT, SURFACE, TEXT, TEXT_SECONDARY, Scrollbar
from sp_workflow.editor_cells import (
    CellState,
    K,
    Tone,
    commit_order,
    commit_trade_rows,
    order_cell_states,
    order_texts,
    trade_cell_states,
    trade_texts,
)
from sp_workflow.order_terms import TDCC_FIELD, OrderRow, describe, format_field
from sp_workflow.product_record import CHOICES, FIELDS
from sp_workflow.product_terms import Draft, ImportConfig, problems
from sp_workflow.trade_rows import FIELDS as TRADE_ROW_FIELDS
from sp_workflow.trade_rows import TradeRowsDraft

PROBLEM_BG = "#FFC7CE"  # 同 Excel 的淺紅色填滿：擋匯入，或讀起單建檔時有問題
DIFFERENT_BG = "#FFEB9C"  # 同 Excel 的淺黃色：和資料庫的值不同
TONE_BG = {Tone.PLAIN: SURFACE, Tone.DIFFERENT: DIFFERENT_BG, Tone.PROBLEM: PROBLEM_BG}


class _FieldRow:
    """編輯區的一欄：欄名、輸入框（選項用下拉）、和資料庫不同時的「用資料庫的值」；說明放在下一行，有才顯示。

    `follow_width` 是 False 時說明的換行寬度由呼叫端更新（會拿掉重畫的欄，不能在 parent 上留自己的事件）。
    """

    def __init__(
        self,
        parent: tk.Frame,
        row: int,
        name: str,
        on_commit: Callable[[str], object],
        on_database: Callable[[str], None],
        *,
        follow_width: bool = True,
    ) -> None:
        self.var = tk.StringVar()
        row *= 2  # 每欄兩行：輸入一行、說明一行
        self.label = tk.Label(parent, text=name, anchor="w", background=SURFACE, font=BODY_FONT, padx=6)
        self.label.grid(row=row, column=0, sticky="nsew", pady=(1, 0))
        if name in CHOICES:
            self.input: ttk.Entry = ttk.Combobox(parent, textvariable=self.var, values=CHOICES[name], width=18)
            self.input.bind("<<ComboboxSelected>>", lambda _e: on_commit(name))
        else:
            self.input = ttk.Entry(parent, textvariable=self.var, width=20)
        self.input.grid(row=row, column=1, sticky="ew", pady=(1, 0))
        for event in ("<FocusOut>", "<Return>"):
            self.input.bind(event, lambda _e: on_commit(name))
        self.database = ttk.Button(parent, text="用資料庫的值", command=lambda: on_database(name))
        self.database.grid(row=row, column=2, sticky="w", padx=(6, 0), pady=(1, 0))
        self.database.grid_remove()
        self.note = tk.Label(parent, anchor="w", justify=tk.LEFT, background=SURFACE, font=CAPTION_FONT, padx=6)
        self.note.grid(row=row + 1, column=0, columnspan=3, sticky="ew", pady=(0, 2))
        self.note.grid_remove()
        if follow_width:
            parent.bind("<Configure>", lambda event: self.wrap(event.width), add="+")

    @property
    def widgets(self) -> tuple[tk.Misc, ...]:
        return self.label, self.input, self.database, self.note

    def wrap(self, width: int) -> None:
        """說明依欄位區的寬度換行。"""
        self.note.configure(wraplength=max(240, width - 20))

    def mark(self, state: CellState) -> None:
        """套用格子狀態（`editor_cells.CellState`）。"""
        for widget in (self.label, self.note):
            widget.configure(background=TONE_BG[state.tone])
        self.note.configure(text=state.note, foreground=DANGER if state.tone is Tone.PROBLEM else TEXT_SECONDARY)
        if state.note:
            self.note.grid()
        else:
            self.note.grid_remove()
        if state.offer_database:
            self.database.grid()
        else:
            self.database.grid_remove()


class OrderEditor(ttk.Frame):
    """起單資料頁右邊的編輯區：一次一檔，欄位是 `product_record.FIELDS`（#86、#97、ADR 0009、0010）。

    擋匯入的欄與讀起單建檔時有提醒的欄淺紅底並寫原因；和資料庫不同的欄淺黃底，寫出資料庫的值，可以一鍵改回。
    離開欄位、按 Enter 或選了下拉時才解讀；寫錯的欄保留打的字並標紅（說明記在 `Draft.input_errors`，
    匯入時那一檔不匯入）。值真的變了才通知 `on_change`。`fixed` 的欄只顯示、不能改（已鎖住時的 Issue date，#55）。
    標紅的欄是 `product_terms.problems`：起單資料用匯入的檢查，人工維護用存檔的檢查（依 `Draft.mode`，#115）。
    """

    PLACEHOLDER = "點左邊的商品來確認、修改"
    FIXED_NOTE = "TS 回填的欄位，不能更正"

    def __init__(
        self,
        parent: tk.Misc,
        config: Callable[[], ImportConfig | None],
        on_change: Callable[[Draft], None],
        *,
        fixed: Collection[str] = (),
        placeholder: str = PLACEHOLDER,
    ) -> None:
        super().__init__(parent, style="Card.TFrame", padding=(16, 14))
        self.config = config
        self.on_change = on_change
        self.fixed = frozenset(fixed)
        self.placeholder = placeholder
        self.draft: Draft | None = None
        self.typed: dict[str, dict[str, str]] = {}  # TDCC → {欄位: 寫錯時打的字}
        self.frozen = False  # 存檔中整個編輯區不能改
        self.title_var = tk.StringVar(value=placeholder)
        self.note_var = tk.StringVar()
        ttk.Label(self, textvariable=self.title_var, style="CardSection.TLabel").pack(anchor="w")
        self.note = ttk.Label(self, textvariable=self.note_var, style="CardMuted.TLabel", justify=tk.LEFT)
        self.note.pack(anchor="w", fill=tk.X, pady=(4, 8))
        self.bind("<Configure>", lambda event: self.note.configure(wraplength=max(240, event.width - 40)))
        area = ttk.Frame(self, style="CardBody.TFrame")
        area.pack(fill=tk.BOTH, expand=True)
        canvas = tk.Canvas(area, highlightthickness=0, background=SURFACE)
        scroll = Scrollbar(area, orient=tk.VERTICAL, command=canvas.yview)
        self.fields = tk.Frame(canvas, background=SURFACE)
        self.fields.columnconfigure(2, weight=1)  # 「用資料庫的值」右邊的空白吃掉多的寬度
        self.fields.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        window = canvas.create_window((0, 0), window=self.fields, anchor="nw")
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.LEFT, fill=tk.Y)
        self.rows = {
            name: _FieldRow(self.fields, i, name, lambda name: self._commit([name]), self._use_database)
            for i, name in enumerate(FIELDS)
        }
        self.show(None)

    @property
    def editable(self) -> bool:
        return self.draft is not None and not self.draft.locked_reason and not self.frozen

    def show(self, draft: Draft | None, row: OrderRow | None = None) -> None:
        """顯示一檔；`draft` 是 None 時（起單建檔裡沒有這檔）只顯示 `row` 的原因，欄位不能改。"""
        self.draft = draft
        texts = order_texts(draft, self.typed.get(draft.tdcc, {})) if draft else {}
        for name, field_row in self.rows.items():
            field_row.var.set(texts.get(name, ""))
        self._apply_state()
        if draft is None:
            self.title_var.set(self.placeholder if row is None else f"{row.tdcc}　{row.issuer}　{row.status}")
            self.note_var.set("" if row is None else "沒有可以改的條件：" + describe(row.notes))
            for field_row in self.rows.values():
                field_row.mark(CellState())
            return
        self.refresh()

    def set_frozen(self, frozen: bool) -> None:
        """存檔時整個編輯區不能改，存完再打開。"""
        self.frozen = frozen
        self._apply_state()

    def _apply_state(self) -> None:
        state = tk.NORMAL if self.editable else tk.DISABLED
        for name, field_row in self.rows.items():
            field_row.input.configure(state=tk.DISABLED if name in self.fixed else state)

    def refresh(self) -> None:
        """依目前的值重新標紅、標黃與寫說明。讀起單建檔時有問題的欄就算和資料庫不同也維持紅色，兩個說明都寫。"""
        draft = self.draft
        if draft is None:
            return
        config = self.config()
        found = problems(draft, config) if config else dict(draft.input_errors)
        states = order_cell_states(draft, found, fixed=self.fixed, fixed_note=self.FIXED_NOTE)
        for name, field_row in self.rows.items():
            field_row.mark(states[name])
        self.title_var.set(f"{draft.tdcc}　{draft.issuer}")
        row_notes = [found.get(TDCC_FIELD, ""), draft.notes.get(TDCC_FIELD, "")]
        self.note_var.set("；".join(filter(None, dict.fromkeys(row_notes))))

    def commit(self) -> bool:
        """把目前這檔畫面上的輸入都解讀一次（換檔、存檔、關閉前用）；有改到才通知，回傳有沒有改到。"""
        return self._commit(self.rows)

    def _commit(self, names: Iterable[str]) -> bool:
        """解讀這幾欄（離開欄位時只有那一欄）；有改到才重畫、通知。"""
        draft = self.draft
        if draft is None or not self.editable:
            return False
        typed = self.typed.setdefault(draft.tdcc, {})
        texts = {name: field_row.var.get() for name, field_row in self.rows.items() if name not in self.fixed}
        changed = commit_order(draft, typed, texts, names)
        _show_texts(self.rows, order_texts(draft, typed))
        if not typed:
            self.typed.pop(draft.tdcc, None)
        if changed:
            self.refresh()
            self.on_change(draft)
        return changed

    def _use_database(self, name: str) -> None:
        draft = self.draft
        if draft is None or draft.stored is None:
            return
        self.rows[name].var.set(format_field(draft.stored.get(name)))
        self._commit([name])


def _show_texts(rows: Mapping[K, _FieldRow], texts: Mapping[K, str]) -> None:
    """解讀後的字寫回輸入框；沒變的不動（游標留在原位）。"""
    for key, text in texts.items():
        if rows[key].var.get() != text:
            rows[key].var.set(text)


class TradeRowsEditor:
    """人工維護編輯區裡、商品條件底下的交易明細（#116）：一列一段，欄位是交易明細的表頭（`trade_rows.FIELDS`）。

    畫在 `OrderEditor` 的欄位區、接在商品條件後面（同一個 grid，欄寬對齊），換檔時整段重畫。只改值，不新增、
    不刪除列。和起單資料的欄一樣：離開欄位、按 Enter 才解讀（規則同每日匯入），寫錯的保留打的字並標紅；
    必填空白、備註改成取消標紅（擋存檔）；改過的標黃、寫出資料庫的值，可以一鍵改回；FC 名字對不到信箱標紅提示（不擋）。
    """

    TITLE = "交易明細"
    NOTE = (
        "只改值，不新增、不刪除列；存檔時和上面的商品條件一起寫入。"
        "改到交易日期、幣別、金額、分公司、UF 時重算核對條件（分公司配置、交叉比對）。"
    )
    EMPTY = "資料庫還沒有這檔的交易明細。"

    def __init__(self, editor: OrderEditor, on_change: Callable[[TradeRowsDraft], None]) -> None:
        self.parent = editor.fields
        self.first_row = len(FIELDS)  # 接在商品條件的欄後面（`_FieldRow` 的列號）
        self.on_change = on_change
        self.trades: TradeRowsDraft | None = None
        self.rows: dict[tuple[int, str], _FieldRow] = {}  # (row_seq, 欄) → 那一欄
        self.typed: dict[tuple[int, str], str] = {}  # 寫錯時打的字
        self.frozen = False
        self.widgets: list[tk.Misc] = []  # 這一段所有的元件（換檔時拿掉）
        self.note: tk.Label | None = None  # 標題下的說明
        self.empty: tk.Label | None = None  # 沒有交易明細時的說明
        self.parent.bind("<Configure>", lambda event: self._wrap(event.width), add="+")

    @property
    def editable(self) -> bool:
        return self.trades is not None and not self.frozen

    def show(self, trades: TradeRowsDraft | None) -> None:
        """換成這檔的交易明細；None 時（還沒打開任何一檔）整段不顯示。"""
        for widget in self.widgets:
            widget.destroy()
        self.widgets, self.rows, self.typed = [], {}, {}
        self.note = self.empty = None
        self.trades = trades
        if trades is None:
            return
        row = self.first_row
        self._label(row, self.TITLE, SECTION_FONT, pady=(16, 0))
        self.note = self._label(row, self.NOTE, CAPTION_FONT, foreground=TEXT_SECONDARY, second=True)
        if not trades.rows:
            self.empty = self._label(row + 1, self.EMPTY, BODY_FONT)
        for trade in trades.rows:
            row += 1
            self._label(row, f"第 {trade.seq} 列", SECTION_FONT, pady=(8, 0))
            for name in TRADE_ROW_FIELDS:
                row += 1
                key = (trade.seq, name)
                field_row = _FieldRow(
                    self.parent,
                    row,
                    name,
                    lambda _name: self.commit(),
                    lambda _name, key=key: self._use_database(key),
                    follow_width=False,
                )
                field_row.var.set(format_field(trade.values.get(name)))
                self.rows[key] = field_row
                self.widgets += field_row.widgets
        self._wrap(self.parent.winfo_width())
        self._apply_state()
        self.refresh()

    def set_frozen(self, frozen: bool) -> None:
        """存檔時不能改，存完再打開。"""
        self.frozen = frozen
        self._apply_state()

    def refresh(self) -> None:
        """依目前的值重新標紅、標黃與寫說明。"""
        if self.trades is None:
            return
        states = trade_cell_states(self.trades)
        for key, field_row in self.rows.items():
            field_row.mark(states[key])

    def commit(self) -> bool:
        """把畫面上的輸入都解讀一次（離開欄位、換檔、存檔、關閉前用）；有改到才通知，回傳有沒有改到。"""
        trades = self.trades
        if trades is None or not self.editable:
            return False
        texts = {key: field_row.var.get() for key, field_row in self.rows.items()}
        changed = commit_trade_rows(trades, self.typed, texts)
        _show_texts(self.rows, trade_texts(trades, self.typed))
        if changed:
            self.refresh()
            self._notify()
        return changed

    def _use_database(self, key: tuple[int, str]) -> None:
        trades = self.trades
        if trades is None:
            return
        seq, name = key
        trade = next(trade for trade in trades.rows if trade.seq == seq)
        self.rows[key].var.set(format_field(trade.stored.get(name)))
        self.commit()

    def _notify(self) -> None:
        if self.trades is not None:
            self.on_change(self.trades)

    def _apply_state(self) -> None:
        state = tk.NORMAL if self.editable else tk.DISABLED
        for field_row in self.rows.values():
            field_row.input.configure(state=state)

    def _wrap(self, width: int) -> None:
        if self.note is not None:
            self.note.configure(wraplength=max(240, width - 20))
        for field_row in self.rows.values():
            field_row.wrap(width)

    def _label(
        self,
        row: int,
        text: str,
        font: tuple[object, ...],
        *,
        foreground: str = TEXT,
        second: bool = False,
        pady: tuple[int, int] = (0, 0),
    ) -> tk.Label:
        """這一段的標題或說明，佔一整行（`_FieldRow` 的列號；`second` 放在那一列的第二行）。"""
        label = tk.Label(
            self.parent, text=text, anchor="w", justify=tk.LEFT, background=SURFACE, foreground=foreground, font=font
        )
        label.grid(row=row * 2 + second, column=0, columnspan=3, sticky="ew", padx=6, pady=pady)
        self.widgets.append(label)
        return label
