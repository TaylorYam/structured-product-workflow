"""面板使用紀錄（#479）：記下每天按了哪些按鈕，累積幾天後找出從沒用到的功能，用來做減法。

紀錄是給維護的人（agent）看的，不做畫面：`install` 在視窗建好後統一攔截，各分頁的程式碼不用改。

- 按鈕、核取方塊、選項按鈕（ttk 與 tk）：放開滑鼠時游標還在按鈕上、按鈕沒停用，才算按到（`click`）。
- 視窗上方選單的每個項目（`menu`）、分頁切換（`tab`）、表格雙擊一列（`double`）。
- 清單（`inventory`）：視窗建好、切到某一頁、另開視窗時掃一次看得到的按鈕、選單項目、分頁與能雙擊的表格，用來比對沒用過的。

路徑（`path`）從所在視窗算起；彙整時去掉 Tk 自動編號的數字，重開的對話框、換一檔重建的欄位才對得起來。
按鈕文字會變的（例如「人工放行…」↔「取消放行」）以按下當時的文字記。

只記介面上的名稱，不記商品、客戶或檔案內容；存在 `.local/usage/YYYY-MM-DD.jsonl`，一行一筆，寫不進去就略過。
彙整：`python -m sp_workflow.usage_log <資料夾>`。
"""

from __future__ import annotations

import json
import re
import sys
import tkinter as tk
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from tkinter import ttk
from typing import Any

FOLDER_NAME = "usage"
TAB_LABEL = "（切到這一頁）"
BUTTON_CLASSES = ("TButton", "Button", "TCheckbutton", "Checkbutton", "TRadiobutton", "Radiobutton")
_CLASS_BINDINGS = (
    *((name, "<ButtonPress-1>") for name in BUTTON_CLASSES),
    *((name, "<ButtonRelease-1>") for name in BUTTON_CLASSES),
    ("TNotebook", "<<NotebookTabChanged>>"),
    ("Treeview", "<Double-1>"),
    ("Toplevel", "<Map>"),
)


class UsageLog:
    """一天一個 jsonl 檔；寫不進去（資料夾唯讀、磁碟滿）一律略過，不影響操作。"""

    def __init__(self, folder: Path, clock: Callable[[], datetime] = datetime.now) -> None:
        self.folder = folder
        self.clock = clock

    def write(self, kind: str, **fields: Any) -> None:
        now = self.clock()
        record = {"time": now.isoformat(timespec="seconds"), "kind": kind, **fields}
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            with (self.folder / f"{now:%Y-%m-%d}.jsonl").open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass


@dataclass
class _Pressed:
    fields: dict[str, Any]
    width: int
    height: int


@dataclass
class UsageRecorder:
    """接在主視窗上的記錄器：按鈕等攔截對整個 Tk 都有效，選單只包 `root` 的選單；`uninstall` 拿掉攔截（測試共用同一個 Tk）。"""

    root: tk.Tk | tk.Toplevel
    log: UsageLog
    _pressed: dict[str, _Pressed] = field(default_factory=dict)
    _listed: set[tuple[Any, ...]] = field(default_factory=set)
    _mapped: set[str] = field(default_factory=set)
    _current_tabs: dict[str, str] = field(default_factory=dict)
    _original_bindings: dict[tuple[str, str], str] = field(default_factory=dict)
    _handler_ids: list[str] = field(default_factory=list)
    _original_menu_commands: list[tuple[tk.Menu, int, str]] = field(default_factory=list)

    def install(self) -> UsageRecorder:
        handlers: dict[str, Callable[[tk.Event], None]] = {
            "<ButtonPress-1>": _quiet(self._press),
            "<ButtonRelease-1>": _quiet(self._release),
            "<<NotebookTabChanged>>": _quiet(self._tab_changed),
            "<Double-1>": _quiet(self._double),
            "<Map>": _quiet(self._mapped_window),
        }
        for class_name, sequence in _CLASS_BINDINGS:
            self._original_bindings[(class_name, sequence)] = self.root.bind_class(class_name, sequence)
            self._handler_ids.append(self.root.bind_class(class_name, sequence, handlers[sequence], add="+"))
        self._wrap_menus()
        _quiet(self.log.write)("start")
        _quiet(self.list_window)(self.root)
        return self

    def uninstall(self) -> None:
        for (class_name, sequence), script in self._original_bindings.items():
            self.root.tk.call("bind", class_name, sequence, script)
        self._original_bindings.clear()
        for handler in self._handler_ids:
            self.root._root().deletecommand(handler)  # bind_class 把指令登記在 Tk 本身
        self._handler_ids.clear()
        for menu, index, command in self._original_menu_commands:
            if menu.winfo_exists():
                wrapper = str(menu.entrycget(index, "command"))
                menu.entryconfigure(index, command=command)
                menu.deletecommand(wrapper)
        self._original_menu_commands.clear()

    # 按鈕：按下時記住，放開時游標還在按鈕上才算（拖出去放開不算）；按鈕的功能可能先把視窗關掉，所以按下時就先記下名稱
    def _press(self, event: tk.Event) -> None:
        widget = event.widget
        if not isinstance(widget, tk.Misc) or not _enabled(widget):
            return
        self._pressed[str(widget)] = _Pressed(_describe(widget), widget.winfo_width(), widget.winfo_height())

    def _release(self, event: tk.Event) -> None:
        pressed = self._pressed.pop(str(event.widget), None)
        if pressed and 0 <= int(event.x) < pressed.width and 0 <= int(event.y) < pressed.height:
            self.log.write("click", **pressed.fields)

    def _tab_changed(self, event: tk.Event) -> None:
        notebook = event.widget
        page = notebook.select() if isinstance(notebook, ttk.Notebook) else ""
        if not page or self._current_tabs.get(str(notebook)) == page:  # 切一次 Tk 會送兩次 <<NotebookTabChanged>>
            return
        self._current_tabs[str(notebook)] = page
        page_widget = notebook.nametowidget(page)
        self.log.write("tab", window=_window_title(notebook), tab=_tab_of(page_widget), label=TAB_LABEL)
        self.list_window(page_widget)

    def _double(self, event: tk.Event) -> None:
        tree = event.widget
        if not isinstance(tree, ttk.Treeview) or tree.identify_region(event.x, event.y) not in ("cell", "tree"):
            return
        self.log.write("double", **_describe(tree, label=_table_label(tree)))

    def _mapped_window(self, event: tk.Event) -> None:
        window = event.widget
        if isinstance(window, tk.Toplevel) and str(window) not in self._mapped:
            self._mapped.add(str(window))
            window.after_idle(lambda: window.winfo_exists() and _quiet(self.list_window)(window))

    def list_window(self, top: tk.Misc) -> None:
        """把 `top` 底下的按鈕、分頁、能雙擊的表格（與主視窗的選單項目）記成清單；這次開視窗已記過的不重複記。"""
        items = []
        for widget in _walk(top):
            if _is_button(widget) and _label(widget):
                items.append(_describe(widget))
            elif isinstance(widget, ttk.Notebook):
                for page in widget.tabs():
                    page_widget = widget.nametowidget(page)
                    items.append({"window": _window_title(widget), "tab": _tab_of(page_widget), "label": TAB_LABEL})
            elif isinstance(widget, ttk.Treeview) and widget.bind("<Double-1>"):
                items.append(_describe(widget, label=_table_label(widget)))
        if top is self.root:
            items += [{"window": _window_title(self.root), "label": label} for label, _menu, _i in self._menu_items()]
        fresh = []
        for item in items:
            key = tuple(sorted(item.items()))
            if key not in self._listed:
                self._listed.add(key)
                fresh.append(item)
        if fresh:
            self.log.write("inventory", items=fresh)

    # 選單：把每個項目的指令包一層，先記再照原本的指令做
    def _wrap_menus(self) -> None:
        for label, menu, index in self._menu_items():
            command = menu.entrycget(index, "command")
            if not command:
                continue
            self._original_menu_commands.append((menu, index, command))
            menu.entryconfigure(index, command=self._menu_command(label, menu, command))

    def _menu_command(self, label: str, menu: tk.Menu, command: str) -> Callable[[], None]:
        def run() -> None:
            _quiet(lambda: self.log.write("menu", window=_window_title(self.root), label=label))()
            menu.tk.call(command)

        return run

    def _menu_items(self) -> Iterator[tuple[str, tk.Menu, int]]:
        name = self.root.cget("menu")
        if name:
            yield from _menu_entries(self.root.nametowidget(name), ())


def install(root: tk.Tk | tk.Toplevel, folder: Path) -> UsageRecorder:
    """在主視窗建好之後呼叫（選單要先建好才包得到）。"""
    return UsageRecorder(root, UsageLog(folder)).install()


def _quiet(function: Callable[..., None]) -> Callable[..., None]:
    """記錄出錯一律略過：使用紀錄不能讓面板跳錯誤或擋住原本的功能。"""

    def run(*args: Any) -> None:
        try:
            function(*args)
        except Exception:  # noqa: BLE001 — 紀錄只是輔助
            pass

    return run


def _menu_entries(menu: tk.Menu, parents: tuple[str, ...]) -> Iterator[tuple[str, tk.Menu, int]]:
    end = menu.index("end")
    for index in range(0 if end is None else end + 1):
        kind = menu.type(index)
        if kind in ("separator", "tearoff"):
            continue
        label = str(menu.entrycget(index, "label"))
        if kind == "cascade":
            yield from _menu_entries(menu.nametowidget(menu.entrycget(index, "menu")), (*parents, label))
        else:
            yield " > ".join((*parents, label)), menu, index


def _walk(widget: tk.Misc) -> Iterator[tk.Misc]:
    for child in widget.winfo_children():
        if isinstance(child, tk.Toplevel | tk.Menu):  # 另開的視窗自己記；選單另外處理
            continue
        yield child
        yield from _walk(child)


def _is_button(widget: tk.Misc) -> bool:
    return widget.winfo_class() in BUTTON_CLASSES


def _enabled(widget: tk.Misc) -> bool:
    if isinstance(widget, ttk.Widget):
        return bool(widget.instate(["!disabled"]))
    try:
        return str(widget.cget("state")) != tk.DISABLED
    except tk.TclError:
        return True


def _label(widget: tk.Misc) -> str:
    try:
        return str(widget.cget("text")).strip()
    except tk.TclError:
        return ""


def _table_label(tree: ttk.Treeview) -> str:
    columns = tree["columns"]
    heading = tree.heading(columns[0], "text") if columns else tree.heading("#0", "text")
    return f"表格雙擊（{heading}）"


def _window_title(widget: tk.Misc) -> str:
    return widget.winfo_toplevel().title()


def _tab_of(widget: tk.Misc) -> str | None:
    """往上找到所在的分頁（Notebook 的直接子元件），回傳分頁名稱；分頁裡還有分頁時由外到內接起來（「4 核對 > 核對結果」）。

    不在分頁裡（步驟欄、上方列）回傳 None。
    """
    tabs: list[str] = []
    child, parent = widget, widget.master
    while parent is not None:
        if isinstance(parent, ttk.Notebook):
            tabs.insert(0, str(parent.tab(child, "text")))
        child, parent = parent, parent.master
    return " > ".join(tabs) if tabs else None


def _describe(widget: tk.Misc, *, label: str | None = None) -> dict[str, Any]:
    fields: dict[str, Any] = {"window": _window_title(widget)}
    tab = _tab_of(widget)
    if tab is not None:
        fields["tab"] = tab
    fields["label"] = _label(widget) if label is None else label
    # 同一頁有同名按鈕（例如兩個「選擇…」）時用來區分；從所在視窗算起，重開的對話框路徑才一樣
    fields["path"] = str(widget).removeprefix(str(widget.winfo_toplevel())).lstrip(".")
    return fields


# ── 彙整（給 agent 讀）──────────────────────────────────────────────────────


def read_records(folder: Path) -> Iterator[dict[str, Any]]:
    for path in sorted(folder.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                yield record


_AUTO_NUMBER = re.compile(r"(?<=[^.\d])\d+(?=\.|$)")  # Tk 自動命名的編號：!button23 → !button


def _key(item: dict[str, Any]) -> tuple[str, str, str, str]:
    path = _AUTO_NUMBER.sub("", str(item.get("path", "")))
    return (item.get("window", ""), item.get("tab") or "", item.get("label", ""), path)


@dataclass(frozen=True)
class UsageRow:
    window: str
    tab: str
    label: str
    path: str
    per_day: dict[str, int]

    @property
    def total(self) -> int:
        return sum(self.per_day.values())


def summarize(records: Iterable[dict[str, Any]]) -> tuple[list[str], list[UsageRow]]:
    """回傳（有紀錄的日子, 每個按鈕、選單項目、分頁、表格每天用幾次）；沒用過的排最前面，其次依次數由少到多。

    比對時去掉路徑裡 Tk 自動編號的數字：重開的對話框、重建的欄位算同一個；同一處的同名按鈕合在一起算。
    """
    days: set[str] = set()
    counts: dict[tuple[str, str, str, str], dict[str, int]] = defaultdict(dict)
    for record in records:
        day = str(record.get("time", ""))[:10]
        kind = record.get("kind")
        if day:
            days.add(day)
        if kind == "inventory":
            for item in record.get("items", []):
                counts.setdefault(_key(item), {})
        elif kind in ("click", "menu", "double", "tab"):
            per_day = counts[_key(record)]
            per_day[day] = per_day.get(day, 0) + 1
    rows = [UsageRow(*key, per_day=per_day) for key, per_day in counts.items()]
    rows.sort(key=lambda row: (row.total, row.window, row.tab, row.label, row.path))
    return sorted(days), rows


def format_summary(days: list[str], rows: list[UsageRow]) -> str:
    lines = ["\t".join(["window", "tab", "label", *days, "total", "path"])]
    for row in rows:
        cells = [str(row.per_day.get(day, 0)) for day in days]
        lines.append("\t".join([row.window, row.tab, row.label, *cells, str(row.total), row.path]))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        raise SystemExit("用法：python -m sp_workflow.usage_log <.local/usage 資料夾>")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(format_summary(*summarize(read_records(Path(args[0])))))


if __name__ == "__main__":
    main()
