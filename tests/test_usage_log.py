"""面板使用紀錄（#479）：按鈕、選單、分頁、表格雙擊記進當天的 jsonl，彙整時找出沒按過的。"""

from __future__ import annotations

import json
import re
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk
from types import SimpleNamespace

import pytest

from sp_workflow import app, usage_log
from sp_workflow.local_settings import FolderMemory
from sp_workflow.usage_log import TAB_LABEL, UsageLog, UsageRecorder, format_summary, read_records, summarize
from window_synth import new_window

DAY = datetime(2026, 11, 9, 9, 30)


def records(folder: Path, kind: str | None = None) -> list[dict]:
    found = list(read_records(folder))
    return [r for r in found if kind is None or r["kind"] == kind]


@pytest.fixture
def window(master):
    """一個有分頁、按鈕、選單的小視窗，記錄器接在它上面；用完拿掉攔截（整個測試程序共用一個 Tk）。"""
    master.title("FCN Workflow")
    bar = tk.Menu(master)
    tools = tk.Menu(bar, tearoff=False)
    master.ran = []
    tools.add_command(label="信箱維護…", command=lambda: master.ran.append("mailbox"))
    tools.add_separator()
    tools.add_command(label="拆股登記…", command=lambda: master.ran.append("splits"))
    bar.add_cascade(label="維護", menu=tools)
    master.configure(menu=bar)
    notebook = ttk.Notebook(master)
    daily, orders = ttk.Frame(notebook), ttk.Frame(notebook)
    notebook.add(daily, text="1 每日匯入")
    notebook.add(orders, text="2 起單條件")
    notebook.pack()
    master.import_button = ttk.Button(daily, text="匯入")
    master.import_button.pack()
    master.disabled_button = ttk.Button(daily, text="下一步 →", state=tk.DISABLED)
    master.disabled_button.pack()
    master.classic_check = tk.Checkbutton(orders, text="只看今天")
    master.classic_check.pack()
    master.classic_check.winfo_id()  # 舊式 tk 元件要先建出視窗，模擬的滑鼠事件才收得到
    master.side_button = ttk.Button(master, text="1   每日匯入")
    master.side_button.pack()
    master.notebook, master.daily, master.orders = notebook, daily, orders
    master.tree = ttk.Treeview(orders, columns=("tdcc",), show="headings")
    master.tree.heading("tdcc", text="TDCC 代號")
    master.tree.bind("<Double-1>", lambda _event: None)  # 有雙擊功能的表格才進清單
    master.tree.pack()
    master.recorder = UsageRecorder(master, UsageLog(Path(master.folder), clock=lambda: DAY)).install()
    yield master
    master.recorder.uninstall()


@pytest.fixture
def master(master, tmp_path: Path):
    master.folder = str(tmp_path / "usage")
    return master


def click(widget: tk.Misc, *, release_x: int = 0) -> None:
    widget.event_generate("<ButtonPress-1>", x=0, y=0)
    widget.event_generate("<ButtonRelease-1>", x=release_x, y=0)


def test_a_button_click_is_written_to_the_day_file_with_window_and_tab(window) -> None:
    click(window.import_button)
    click(window.side_button)

    folder = Path(window.folder)
    assert [p.name for p in folder.iterdir()] == ["2026-11-09.jsonl"]
    first, second = records(folder, "click")
    assert first == {
        "time": "2026-11-09T09:30:00",
        "kind": "click",
        "window": "FCN Workflow",
        "tab": "1 每日匯入",
        "label": "匯入",
        "path": "!notebook.!frame.!button",
    }
    assert "tab" not in second and second["label"] == "1   每日匯入", "步驟欄不在分頁裡"


def test_classic_tk_buttons_count_too(window) -> None:
    click(window.classic_check)

    assert [(r["tab"], r["label"]) for r in records(Path(window.folder), "click")] == [("2 起單條件", "只看今天")]


def test_dragging_off_the_button_or_pressing_a_disabled_one_is_not_a_click(window) -> None:
    click(window.import_button, release_x=-5)
    click(window.disabled_button)

    assert records(Path(window.folder), "click") == []


def test_menu_items_are_logged_and_still_run(window) -> None:
    bar = window.nametowidget(window["menu"])
    tools = window.nametowidget(bar.entrycget("end", "menu"))
    tools.invoke(2)

    assert window.ran == ["splits"]
    assert [(r["window"], r["label"]) for r in records(Path(window.folder), "menu")] == [
        ("FCN Workflow", "維護 > 拆股登記…")
    ]


def test_switching_tabs_is_logged(window) -> None:
    window.notebook.select(window.orders)
    window.update()
    window.notebook.select(window.daily)
    window.update()

    assert [(r["tab"], r["label"]) for r in records(Path(window.folder), "tab")] == [
        ("2 起單條件", TAB_LABEL),
        ("1 每日匯入", TAB_LABEL),
    ]


def test_double_clicking_a_table_row_is_logged(window, monkeypatch) -> None:
    """Tk 不能模擬雙擊事件，直接交給雙擊的處理；點到表頭不算。"""
    for region in ("cell", "heading"):
        monkeypatch.setattr(window.tree, "identify_region", lambda x, y, r=region: r)
        window.recorder._double(SimpleNamespace(widget=window.tree, x=1, y=1))

    (double,) = records(Path(window.folder), "double")
    assert (double["tab"], double["label"]) == ("2 起單條件", "表格雙擊（TDCC 代號）")


def test_start_lists_every_button_tab_table_and_menu_item_once(window) -> None:
    folder = Path(window.folder)
    (inventory,) = records(folder, "inventory")
    labels = [(item.get("tab"), item["label"]) for item in inventory["items"]]

    assert labels == [
        ("1 每日匯入", TAB_LABEL),
        ("2 起單條件", TAB_LABEL),
        ("1 每日匯入", "匯入"),
        ("1 每日匯入", "下一步 →"),
        ("2 起單條件", "只看今天"),
        ("2 起單條件", "表格雙擊（TDCC 代號）"),
        (None, "1   每日匯入"),
        (None, "維護 > 信箱維護…"),
        (None, "維護 > 拆股登記…"),
    ]
    window.notebook.select(window.orders)
    window.update()
    assert len(records(folder, "inventory")) == 1, "已經記過的不重複記"


def test_another_window_is_listed_on_its_own(window) -> None:
    dialog = tk.Toplevel(window)
    dialog.withdraw()
    dialog.title("信箱維護")
    ttk.Button(dialog, text="新增人員…").pack()
    try:
        window.recorder.list_window(dialog)
    finally:
        dialog.destroy()

    (inventory,) = records(Path(window.folder), "inventory")[-1:]
    assert inventory["items"] == [{"window": "信箱維護", "label": "新增人員…", "path": "!button"}], "路徑從視窗算起"


def test_a_tdcc_in_a_dialog_title_is_masked_before_it_is_written(window) -> None:
    """「人工核對完成：<TDCC>」對話框：清單和按下的按鈕都不把 TDCC（連續 6 位以上的數字）寫進紀錄。"""
    dialog = tk.Toplevel(window)
    dialog.withdraw()
    dialog.title("人工核對完成：999999")
    confirm = ttk.Button(dialog, text="確定")
    confirm.pack()
    try:
        window.recorder.list_window(dialog)
        click(confirm)
    finally:
        dialog.destroy()

    folder = Path(window.folder)
    assert re.search(r"\d{6,}", (folder / "2026-11-09.jsonl").read_text(encoding="utf-8")) is None
    (inventory,) = records(folder, "inventory")[-1:]
    assert inventory["items"] == [{"window": "人工核對完成：＊", "label": "確定", "path": "!button"}]
    assert [(r["window"], r["label"]) for r in records(folder, "click")] == [("人工核對完成：＊", "確定")]


def test_a_failing_log_never_blocks_the_panel(master, monkeypatch) -> None:
    """記錄出錯（不只是寫檔失敗）時按鈕與選單照常運作，也不跳錯誤。"""

    class BrokenLog(UsageLog):
        def write(self, kind: str, **fields) -> None:
            raise RuntimeError("壞掉")

    ran = []
    bar = tk.Menu(master, tearoff=False)
    tools = tk.Menu(bar, tearoff=False)
    tools.add_command(label="信箱維護…", command=lambda: ran.append("mailbox"))
    bar.add_cascade(label="維護", menu=tools)
    master.configure(menu=bar)
    button = ttk.Button(master, text="匯入")
    button.pack()
    errors = []
    monkeypatch.setattr(master._root(), "report_callback_exception", lambda *exc: errors.append(exc))
    recorder = UsageRecorder(master, BrokenLog(Path(master.folder)))
    try:
        recorder.install()
        click(button)
        tools.invoke(0)
    finally:
        recorder.uninstall()

    assert ran == ["mailbox"] and errors == []


def test_uninstall_restores_the_original_bindings(master) -> None:
    before = master.bind_class("TButton", "<ButtonRelease-1>")
    recorder = UsageRecorder(master, UsageLog(Path(master.folder)))
    try:
        recorder.install()
        installed = master.bind_class("TButton", "<ButtonRelease-1>")
    finally:
        recorder.uninstall()

    assert installed != before
    assert master.bind_class("TButton", "<ButtonRelease-1>") == before


def test_an_unwritable_folder_is_ignored(tmp_path: Path) -> None:
    blocker = tmp_path / "usage"
    blocker.write_text("不是資料夾", encoding="utf-8")

    UsageLog(blocker, clock=lambda: DAY).write("click", label="匯入")

    assert blocker.read_text(encoding="utf-8") == "不是資料夾"


def write_day(folder: Path, day: str, *lines: dict) -> None:
    folder.mkdir(exist_ok=True)
    text = "".join(json.dumps({"time": f"{day}T09:00:00", **line}, ensure_ascii=False) + "\n" for line in lines)
    (folder / f"{day}.jsonl").write_text(text + "{broken\n", encoding="utf-8")


def test_summary_puts_never_clicked_items_first_with_counts_per_day(tmp_path: Path) -> None:
    folder = tmp_path / "usage"
    used = {"window": "FCN Workflow", "tab": "1 每日匯入", "label": "匯入", "path": ".a"}
    unused = {"window": "FCN Workflow", "tab": "1 每日匯入", "label": "瀏覽…", "path": ".b"}
    menu = {"window": "FCN Workflow", "label": "維護 > 拆股登記…"}
    write_day(folder, "2026-11-09", {"kind": "start"}, {"kind": "inventory", "items": [used, unused, menu]})
    write_day(
        folder,
        "2026-11-10",
        {"kind": "start"},
        {"kind": "click", **used},
        {"kind": "click", **used},
        {"kind": "menu", **menu},
    )

    days, rows = summarize(read_records(folder))

    assert days == ["2026-11-09", "2026-11-10"]
    assert [(r.label, r.per_day, r.total) for r in rows] == [
        ("瀏覽…", {}, 0),
        ("維護 > 拆股登記…", {"2026-11-10": 1}, 1),
        ("匯入", {"2026-11-10": 2}, 2),
    ]
    table = format_summary(days, rows).splitlines()
    assert table[0] == "window\ttab\tlabel\t2026-11-09\t2026-11-10\ttotal\tpath"
    assert table[1] == "FCN Workflow\t1 每日匯入\t瀏覽…\t0\t0\t0\t.b"


def test_summary_matches_reopened_dialogs_rebuilt_rows_and_tabs(tmp_path: Path) -> None:
    """Tk 自動編號會變：重建的欄位（!button3 → !button27）算同一個；分頁有清單也有切換次數；沒寫到 start 的日子也算。"""
    folder = tmp_path / "usage"
    row = {"window": "FCN Workflow", "tab": "2 起單資料", "label": "用資料庫的值"}
    page = {"window": "FCN Workflow", "tab": "8 進度", "label": TAB_LABEL}
    unopened = {"window": "FCN Workflow", "tab": "9 出場清單", "label": TAB_LABEL}
    inventory = [{**row, "path": "!frame3.!editor.!button3"}, page, unopened]
    write_day(folder, "2026-11-09", {"kind": "start"}, {"kind": "inventory", "items": inventory})
    write_day(
        folder, "2026-11-10", {"kind": "click", **row, "path": "!frame3.!editor.!button27"}, {"kind": "tab", **page}
    )

    days, rows = summarize(read_records(folder))

    assert days == ["2026-11-09", "2026-11-10"]
    assert [(r.tab, r.label, r.total) for r in rows] == [
        ("9 出場清單", TAB_LABEL, 0),
        ("2 起單資料", "用資料庫的值", 1),
        ("8 進度", TAB_LABEL, 1),
    ]


def test_summary_merges_tdccs_and_counts_in_old_records_but_keeps_step_numbers(tmp_path: Path) -> None:
    """舊紀錄不改寫：彙整時遮掉 TDCC、合併文字裡的筆數；分頁與步驟欄開頭的編號照留。"""
    folder = tmp_path / "usage"
    checker = {"window": "FCN Workflow", "tab": "4 核對 > 待處理（1）", "label": TAB_LABEL}
    write_day(
        folder,
        "2026-11-09",
        {"kind": "click", "window": "人工核對完成：111111", "label": "確定", "path": "!button"},
        {"kind": "click", "window": "人工核對完成：222222", "label": "取消", "path": "!button2"},
        {"kind": "click", "window": "人工核對完成：333333", "label": "確定", "path": "!button"},
        {"kind": "tab", **checker},
        {"kind": "tab", **checker, "tab": "4 核對 > 待處理（12）"},
        {"kind": "click", "window": "FCN Workflow", "tab": "4 核對", "label": "人工放行 2 份…", "path": ".a"},
        {"kind": "click", "window": "FCN Workflow", "tab": "4 核對", "label": "人工放行 15 份…", "path": ".a"},
        {"kind": "click", "window": "FCN Workflow", "tab": "4 核對", "label": "取消放行（2 份）", "path": ".a"},
        {"kind": "click", "window": "FCN Workflow", "tab": "4 核對", "label": "取消放行（3 份）", "path": ".a"},
        {"kind": "click", "window": "W", "tab": "3 件數 > 2 份數", "label": "匯出", "path": ".c"},
        {"kind": "click", "window": "FCN Workflow", "label": "1   每日匯入", "path": ".s1"},
        {"kind": "click", "window": "FCN Workflow", "label": "10   出場清單", "path": ".s10"},
        {"kind": "tab", "window": "FCN Workflow", "tab": "1 每日匯入", "label": TAB_LABEL},
    )

    _days, rows = summarize(read_records(folder))

    assert sorted((r.window, r.tab, r.label, r.total) for r in rows) == [
        ("FCN Workflow", "", "1   每日匯入", 1),
        ("FCN Workflow", "", "10   出場清單", 1),
        ("FCN Workflow", "1 每日匯入", TAB_LABEL, 1),
        ("FCN Workflow", "4 核對", "人工放行 N 份…", 2),
        ("FCN Workflow", "4 核對", "取消放行（N 份）", 2),
        ("FCN Workflow", "4 核對 > 待處理（N）", TAB_LABEL, 2),
        ("W", "3 件數 > 2 份數", "匯出", 1),
        ("人工核對完成：＊", "", "取消", 1),
        ("人工核對完成：＊", "", "確定", 2),
    ]


def test_summary_command_prints_the_table(tmp_path: Path, capsys) -> None:
    folder = tmp_path / "usage"
    write_day(
        folder, "2026-11-09", {"kind": "start"}, {"kind": "inventory", "items": [{"window": "W", "label": "匯入"}]}
    )

    usage_log.main([str(folder)])

    assert "W\t\t匯入\t0\t0" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        usage_log.main([])


def test_the_real_window_lists_its_menus_steps_and_tab_buttons(master, tmp_path: Path) -> None:
    """接在真的 FCN Workflow 視窗上：選單項目、步驟欄、各分頁的按鈕都在清單裡；選單照常能用。"""
    window = new_window(master, tmp_path)
    recorder = UsageRecorder(master, UsageLog(Path(master.folder), clock=lambda: DAY)).install()
    try:
        (inventory,) = records(Path(master.folder), "inventory")
        labels = {(item.get("tab"), item["label"]) for item in inventory["items"]}
        assert {(None, "維護 > 信箱維護…"), (None, "分析 > 盤勢速報…")} <= labels
        assert any(tab is None and label.endswith("核對") for tab, label in labels), "步驟欄"
        assert any(tab and tab.endswith("核對") for tab, _label in labels), "核對頁的按鈕"
        assert any(tab and tab.endswith("核對 > 核對結果") for tab, _label in labels), "分頁裡的分頁由外到內接起來"

        opened = []
        window.open_window = opened.append  # 不真的開盤勢速報視窗
        bar = master.nametowidget(master["menu"])
        analysis = master.nametowidget(bar.entrycget("分析", "menu"))
        analysis.invoke(analysis.index("盤勢速報…"))
        assert opened == ["盤勢速報…"]
        assert [r["label"] for r in records(Path(master.folder), "menu")] == ["分析 > 盤勢速報…"]
    finally:
        recorder.uninstall()


def test_main_records_usage_next_to_the_remembered_folders(monkeypatch, tmp_path: Path) -> None:
    class FakeTk:
        def mainloop(self) -> None:
            pass

    class FakeWindow:
        memory = FolderMemory.under(tmp_path)

        def restore_size(self) -> None:
            pass

    installed = []
    monkeypatch.setattr(app.tk, "Tk", FakeTk)
    monkeypatch.setattr(app, "build_window", lambda master, args, **kwargs: FakeWindow())
    monkeypatch.setattr(app, "apply_window_icon", lambda master: None)
    monkeypatch.setattr(app, "enable_windows_dpi_awareness", lambda: None)
    monkeypatch.setattr(app, "set_windows_app_id", lambda: None)
    monkeypatch.setattr(usage_log, "install", lambda master, folder: installed.append(folder))

    app.main(["--install-root", str(tmp_path)])

    assert installed == [tmp_path / ".local" / "usage"]
