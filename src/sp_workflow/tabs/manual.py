"""「7 人工維護」分頁（#115、#116、#122、#291）：檢查缺資料的商品、補齊或更正資料庫裡的商品條件與交易明細、匯出商品條件與商品總表。"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Iterable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from fcn_checker.check_config import ConfigPaths
from fcn_checker.ui import NUMBER_FONT, Background, CopyArea, WrapLabel
from sp_workflow.database import ConnectionSettings
from sp_workflow.local_settings import FolderMemory
from sp_workflow.order_terms import format_field
from sp_workflow.preparation import parse_tdccs
from sp_workflow.product_export import ExportReceipt, export_name, summary_name
from sp_workflow.product_record import REQUIRED
from sp_workflow.product_terms import Draft, ImportConfig, MissingProduct, Receipt
from sp_workflow.status import OrderReview
from sp_workflow.tabs.base import CELL_COPY_HINT, TITLE, BackgroundFactory, DatabaseLogin, Tab
from sp_workflow.tabs.cell_table import CellTable
from sp_workflow.tabs.editor import OrderEditor, TradeRowsEditor
from sp_workflow.tabs.recalc import calc_status
from sp_workflow.trade_rows import TradeRowsDraft
from sp_workflow.window_actions import (
    Checked,
    Opened,
    check_missing,
    export_product_summary,
    export_product_terms,
    open_product,
    save_product,
)


class MaintenanceTab(Tab):
    """「7 人工維護」（#115、ADR 0017）：檢查缺資料的商品，手動補齊、修改資料庫裡的商品條件。

    左邊是「檢查」的結果（`CellTable`，缺的格子淺紅底），也可以輸入 TDCC 打開任何一檔；右邊是和起單資料頁
    同一套的編輯區。已鎖住的照更正的規則存檔（ADR 0014），還沒鎖住的可以留空白；每次存檔都留紀錄。
    """

    READY_TEXT = "按「檢查」列出還沒鎖住、缺資料的商品；或輸入 TDCC 按「打開」，改任何一檔（包括已鎖住的）。"
    INTROS = {
        OrderReview.LOCKED: (
            "已核對通過、條件已鎖住：存檔照更正的規則，商品維持鎖住、核對結果不動，算過核對條件的商品會重算價格。"
            "不能留空白；Issue date 由 TS 回填，不能改。"
        ),
        OrderReview.UNLOCKED: (
            "還沒鎖住：存檔寫進資料庫並重算核對條件。必填欄可以先空著（淺紅底），缺欄時核對條件停在待確認、不能核對；"
            "重新匯入起單資料時照舊整檔覆蓋。"
        ),
        OrderReview.TRADES_ONLY: (
            "資料庫還沒有這檔的商品條件（只有交易明細）：存檔時新增，上手依上手編號對照。"
            "必填欄可以先空著，缺欄時核對條件停在待確認、不能核對。"
        ),
    }
    COLUMNS = (("issuer", "上手"), ("status", "狀態"), *((name, name) for name in REQUIRED))

    def __init__(
        self,
        notebook: ttk.Notebook,
        memory: FolderMemory,
        db: DatabaseLogin,
        config_paths: Callable[[], ConfigPaths],
        *,
        background: BackgroundFactory = Background,
    ) -> None:
        super().__init__(notebook, "人工維護", (), db, background)
        self.memory = memory
        self.config_paths = config_paths
        self.config: ImportConfig | None = None  # 最近一次檢查或打開時讀的設定
        self.checked: dict[str, MissingProduct] = {}  # 最近一次檢查的結果（依 TDCC 排）
        self.status_var.set(self.READY_TEXT)

        self.page_header(
            "維護商品條件",
            "檢查還缺資料的商品，手動補齊或修改資料庫裡的商品條件；已鎖住的照更正的規則，每次存檔都留紀錄。",
        )
        self.header.grid_configure(columnspan=3)
        body = self.body(self.card(1))
        title = ttk.Frame(body, style="CardBody.TFrame")
        title.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))
        ttk.Label(title, text="TDCC", style="Card.TLabel").pack(side=tk.LEFT)
        ttk.Label(title, text="直接打開任何一檔，包括已鎖住、沒缺資料的", style="CardMuted.TLabel").pack(
            side=tk.LEFT, padx=(10, 0)
        )
        self.tdcc_var = tk.StringVar()
        entry = ttk.Entry(body, textvariable=self.tdcc_var, width=18, font=(NUMBER_FONT, 11))
        entry.grid(row=1, column=0, sticky="w")
        entry.bind("<Return>", lambda _event: self.open_typed())
        self.open_button = ttk.Button(body, text="打開", command=self.open_typed)
        self.open_button.grid(row=1, column=1, sticky="w", padx=(8, 0))
        body.columnconfigure(0, weight=0)
        body.columnconfigure(1, weight=1)
        self.result_area(body, 2)
        self.check_button = ttk.Button(self.buttons, text="檢查", command=self.check, style="Primary.TButton")
        self.check_button.pack(side=tk.LEFT)
        self.export_button = ttk.Button(self.buttons, text="匯出商品條件…", command=self.export)
        self.export_button.pack(side=tk.LEFT, padx=(8, 0))
        self.summary_button = ttk.Button(self.buttons, text="匯出商品總表…", command=self.export_summary)
        self.summary_button.pack(side=tk.LEFT, padx=(8, 0))

        self.cell_area = CopyArea(self.frame, CELL_COPY_HINT, lambda: self.cells.copy_all())  # #121
        self.cell_area.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(16, 0))
        self.cells = CellTable(self.cell_area.card, ("tdcc", "TDCC"), self.COLUMNS, self._selected)
        self.cells.grid(row=0, column=0, sticky="nsew")
        self.frame.rowconfigure(2, weight=1)
        self.frame.columnconfigure(2, weight=1)
        side = ttk.Frame(self.frame)
        side.grid(row=1, rowspan=2, column=2, sticky="nsew", padx=(16, 0))  # 從卡片那一列拉到底
        self.intro_var = tk.StringVar()
        intro = WrapLabel(side, textvariable=self.intro_var, style="Muted.TLabel", justify=tk.LEFT)
        intro.pack(anchor="w", fill=tk.X)
        side.bind("<Configure>", lambda event: intro.configure(wraplength=max(240, event.width - 20)))
        self.editor = OrderEditor(
            side,
            lambda: self.config,
            self._edited,
            placeholder="點左邊的商品，或輸入 TDCC 打開",
        )
        self.editor.pack(fill=tk.BOTH, expand=True, pady=(6, 8))
        self.trade_editor = TradeRowsEditor(self.editor, lambda trades: self._edited_tdcc(trades.tdcc))
        actions = ttk.Frame(side, style="Card.TFrame", padding=(14, 10))
        actions.pack(fill=tk.X)
        actions.columnconfigure(1, weight=1)
        ttk.Label(actions, text="原因（選填）", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        self.reason_var = tk.StringVar()
        self.reason_entry = ttk.Entry(actions, textvariable=self.reason_var)
        self.reason_entry.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        buttons = ttk.Frame(actions, style="CardBody.TFrame")
        buttons.grid(row=1, column=0, columnspan=2, sticky="e", pady=(10, 0))
        self.save_button = ttk.Button(buttons, text="存檔", command=self.save, style="Primary.TButton")
        self.save_button.pack(side=tk.LEFT)
        self.reload_button = ttk.Button(buttons, text="重新載入", command=self.reload)
        self.reload_button.pack(side=tk.LEFT, padx=(8, 0))
        self._busy(False)

    def _busy(self, busy: bool) -> None:
        """處理中：按鈕與編輯區都不能用；還沒打開任何一檔時不能存檔、重新載入。

        做完時檢查結果選回打開的那一檔（處理中點了別列、讀不到要打開的那一檔時都選回來）。
        """
        if not busy:
            self.cells.select(self._current_tdcc())
        self.editor.set_frozen(busy)
        self.trade_editor.set_frozen(busy)
        state = tk.DISABLED if busy else tk.NORMAL
        for widget in (self.check_button, self.open_button, self.export_button, self.summary_button):
            widget.configure(state=state)
        has_draft = state if self.editor.draft is not None else tk.DISABLED
        for widget in (self.save_button, self.reload_button, self.reason_entry):
            widget.configure(state=has_draft)

    def has_unsaved(self) -> bool:
        """有改過、還沒存檔的內容（先把編輯區還沒解讀的輸入解讀掉），包括交易明細。"""
        self.editor.commit()
        self.trade_editor.commit()
        draft, trades = self.editor.draft, self.trade_editor.trades
        terms = draft is not None and bool(draft.edits() or draft.input_errors)
        return terms or (trades is not None and bool(trades.edits() or trades.has_input_errors()))

    def _may_discard(self, action: str) -> bool:
        return not self.has_unsaved() or messagebox.askyesno(
            TITLE,
            f"人工維護頁有改過、還沒存檔的內容，{action}後會不見。\n\n確定要繼續嗎？",
            icon=messagebox.WARNING,
            default=messagebox.NO,
            parent=self.master,
        )

    def _current_tdcc(self) -> str | None:
        return None if self.editor.draft is None else self.editor.draft.tdcc

    # ---------- 檢查 ----------

    def check(self) -> None:
        paths = self.config_paths()
        self.runner.run_action(
            lambda settings: check_missing(settings, paths), "檢查中…", self._checked, clear=False, before=self.restart
        )

    def _checked(self, result: Checked) -> None:
        self.config = result.config
        self.checked = {product.tdcc: product for product in result.products}
        self._fill_cells()
        found = len(self.checked)
        new = sum(product.status == OrderReview.TRADES_ONLY for product in self.checked.values())
        if found:
            status = (
                f"檢查完了：{found} 檔還沒鎖住而且缺資料（其中 {new} 檔只有交易明細）。"
                "缺的格子是淺紅底，點一列在右邊補齊。"
            )
        else:
            status = "檢查完了：沒有還沒鎖住、缺資料的商品。"
        self.finish((("缺資料（檔）", found, found > 0), ("只有交易明細（檔）", new, False)), status)

    def _fill_cells(self) -> None:
        rows = [
            (
                product.tdcc,
                {
                    "issuer": product.issuer,
                    "status": product.status,
                    **{name: format_field(product.values.get(name)) for name in REQUIRED},
                },
                product.missing,
            )
            for product in self.checked.values()
        ]
        self.cells.set_rows(rows)
        self.cells.select(self._current_tdcc())

    # ---------- 匯出商品條件（#122）與商品總表（#291） ----------

    def export(self) -> None:
        """選存放位置，把資料庫目前的 `workflow.product` 整張匯出成 Excel；按取消時不連資料庫。"""
        self._export(
            "匯出資料庫的商品條件（workflow.product）",
            export_name(),
            lambda settings, path: export_product_terms(settings, path),  # 寫成呼叫，參數契約才檢查得到（#208）
            "匯出商品條件中…",
            "商品條件（workflow.product）",
        )

    def export_summary(self) -> None:
        """選存放位置，把資料庫所有商品照新商品總表的欄位匯出成 Excel；按取消時不連資料庫。"""
        self._export(
            "匯出商品總表",
            summary_name(),
            lambda settings, path: export_product_summary(settings, path),
            "匯出商品總表中…",
            "商品總表",
        )

    def _export(
        self,
        title: str,
        name: str,
        action: Callable[[ConnectionSettings, Path], ExportReceipt],
        busy: str,
        what: str,
    ) -> None:
        """兩種匯出共用：選檔（記住上次的資料夾）、背景匯出、狀態列寫匯出了什麼（`what`）。"""
        if self.working:
            return
        chosen = filedialog.asksaveasfilename(
            title=title,
            initialdir=self.memory.load().get("product_export") or None,
            initialfile=name,
            defaultextension=".xlsx",
            filetypes=[("Excel 活頁簿", "*.xlsx")],
            parent=self.master,
        )
        if not chosen:
            return
        path = Path(chosen)
        unsaved = self.has_unsaved()

        def starting() -> None:
            self.memory.remember(product_export=str(path.parent))
            self.mark_done(False)

        self.runner.run_action(
            lambda settings: action(settings, path),
            busy,
            lambda result: self._exported(result, unsaved, what),
            clear=False,
            before=starting,
        )

    def _exported(self, result: ExportReceipt, unsaved: bool, what: str) -> None:
        status = f"已匯出資料庫的{what}{result.count} 檔：{result.path}"
        if unsaved:
            status += "\n注意：畫面上還沒存檔的修改不在匯出檔裡。"
        self.status_var.set(status)
        self.mark_done(True)

    # ---------- 打開 ----------

    def _selected(self, tdcc: str) -> None:
        if self.working or tdcc == self._current_tdcc():
            self.cells.select(self._current_tdcc())
            return
        self.open(tdcc)

    def open_typed(self) -> None:
        tdccs = parse_tdccs(self.tdcc_var.get())
        if len(tdccs) != 1:
            messagebox.showerror(TITLE, "請輸入一筆 TDCC。", parent=self.master)
            return
        self.open(tdccs[0])

    def reload(self) -> None:
        """重新讀資料庫目前的值（例如存檔時說打開之後被改過）。"""
        tdcc = self._current_tdcc()
        if tdcc is not None:
            self.open(tdcc, action="重新載入")

    def open(self, tdcc: str, *, action: str = "") -> None:
        """讀這檔目前的條件到右邊；有改過、還沒存檔的先問。"""
        if self.working or not self._may_discard(action or f"打開 {tdcc}"):
            self.cells.select(self._current_tdcc())
            return
        paths = self.config_paths()
        if not self.runner.run_action(
            lambda settings: open_product(settings, tdcc, paths), f"讀取 {tdcc} 的條件中…", self._opened, clear=False
        ):
            self.cells.select(self._current_tdcc())

    def _opened(self, result: Opened) -> None:
        self.config = result.config
        self._show(result.draft, result.trades)
        self.status_var.set(f"已打開 {result.draft.tdcc}（{result.draft.review}），改好按「存檔」。")

    def _show(self, draft: Draft, trades: TradeRowsDraft | None) -> None:
        self.editor.fixed = draft.fixed
        self.editor.typed = {}
        self.editor.show(draft)
        self.trade_editor.show(trades)
        self.intro_var.set(self.INTROS[draft.review])
        self.reason_var.set("")

    def _edited(self, draft: Draft) -> None:
        self._edited_tdcc(draft.tdcc)

    def _edited_tdcc(self, tdcc: str) -> None:
        self.mark_done(False)
        self.status_var.set(f"{tdcc} 有改過、還沒存檔。")

    # ---------- 存檔 ----------

    def save(self) -> None:
        draft, trades, config = self.editor.draft, self.trade_editor.trades, self.config
        if self.working or draft is None or config is None:
            return
        self.editor.commit()
        self.trade_editor.commit()
        reason = self.reason_var.get()
        self.runner.run_action(  # 出錯時畫面上的值不動，可以改好再存或重新載入
            lambda settings: save_product(settings, draft, config, trades=trades, reason=reason),
            f"存檔中…（{draft.tdcc}）",
            self._saved,
            clear=False,
            before=lambda: self.mark_done(False),
        )

    def _saved(self, result: Receipt) -> None:
        draft = self.editor.draft
        if result.saved and draft is not None:
            # 已換成資料庫存的值：黃色的差異清掉，只有交易明細的新增了商品條件時變成未鎖住
            self._show(draft, self.trade_editor.trades)
        if result.correction_id is not None and draft is not None:
            self._update_checked(result, draft)
        self.status_var.set(correction_text(result))
        self.mark_done(result.saved)

    def _update_checked(self, receipt: Receipt, draft: Draft) -> None:
        """檢查結果裡有這檔時跟著更新：補齊了就拿掉，還缺的寫入後的值與缺的欄。"""
        if receipt.tdcc not in self.checked:
            return
        if receipt.missing:
            listed = self.checked[receipt.tdcc]
            self.checked[receipt.tdcc] = MissingProduct(
                receipt.tdcc, listed.issuer, OrderReview.UNLOCKED, dict(draft.values), receipt.missing
            )
        else:
            del self.checked[receipt.tdcc]
        self._fill_cells()


def correction_text(receipt: Receipt) -> str:
    """人工維護存檔（已鎖住的是更正）的結果：改了什麼（前 → 後）、重算後的核對條件、對不到信箱的 FC 名字。"""
    if not receipt.saved:
        return f"{receipt.tdcc} 沒有改任何欄位，沒有寫入。"
    tdcc, text = receipt.tdcc, ""
    changes = _changes_text(receipt.changes)
    if receipt.correction_id is not None and receipt.locked:
        text += f"已更正 {tdcc}（第 {receipt.correction_id} 次更正）：{changes}。"
    elif receipt.correction_id is not None:
        action = "新增" if receipt.created else "存檔"
        text += f"已{action} {tdcc} 的商品條件（第 {receipt.correction_id} 筆紀錄）：{changes}。"
    if receipt.trade_correction_id is not None:
        rows = _changes_text(
            (f"第 {seq} 列 {name}", before, after) for seq, name, before, after in receipt.trade_changes
        )
        text += f"已改 {tdcc} 的交易明細（第 {receipt.trade_correction_id} 筆紀錄）：{rows}。"
        if receipt.correction_id is None and receipt.changes:
            text += f"重算後改變的價格：{changes}。"
    if receipt.locked:
        text += "商品維持鎖住，核對結果沒有變。"
    if receipt.calc is not None:
        text += f"\n核對條件重算後：{calc_status(receipt.calc)}。"
    if receipt.exited:
        text += (
            f"\n注意：這檔已出場（{receipt.exited}），不再比價，存續事件不會跟著改；"
            "結果和上手通知不同時，到「8 進度」提出異議。"
        )
    if receipt.unmatched:
        names = "、".join(receipt.unmatched)
        text += f"\n注意：FC 名字對不到信箱：{names}（寄信草稿找不到收件人；請確認名字或在信箱表補上）。"
    return text


def _changes_text(changes: Iterable[tuple[str, object, object]]) -> str:
    return "、".join(
        f"{name} {format_field(before) or '空白'} → {format_field(after) or '空白'}" for name, before, after in changes
    )
