"""分頁 → 動作的契約（#208）：分頁呼叫 `window_actions` 的參數要對得上那個 function 的參數。

分頁測試把動作換成假的、動作測試打真的資料庫，兩邊各自通過時，分頁把參數傳錯（例：對調 `source, paths`）也看不出來。
這裡讀分頁的原始碼（`ast`），找出每一個用到 `window_actions` function 的地方，檢查：
- 參數個數與關鍵字對得上 function 的簽名（`inspect.signature(...).bind`）；直接把 function 交給 `run_action`
  （例：`run_action(read_latest_import, …)`）的，當成只傳 `settings` 一個參數。
- 位置參數是一個名字（`paths`，或 `read.drafts` 的 `drafts`）而且剛好是那個 function 另一個參數的名字時，算傳錯位置。
名字和參數不同、但不是別的參數名（例：`config_paths` 傳給 `paths`）不算錯；關鍵字參數只檢查有沒有這個參數。
`*args`、`**kwargs` 檢查不了，直接算錯。只認 `from sp_workflow.window_actions import …` 的寫法（分頁都這樣寫）。
不碰資料庫、不開視窗。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

import sp_workflow.window_actions as actions

TABS = Path(actions.__file__).parent / "tabs"
SETTINGS = ast.Name("settings")  # 交給 `run_action` 的 function 收到的唯一參數


def _imported(tree: ast.Module) -> dict[str, str]:
    """這份原始碼從 `window_actions` import 的 function：本地名稱 → function 名稱。"""
    return {
        alias.asname or alias.name: alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "sp_workflow.window_actions"
        for alias in node.names
        if inspect.isfunction(getattr(actions, alias.name, None))
    }


def action_uses(source: str) -> list[tuple[int, str, list[ast.expr], list[ast.keyword]]]:
    """原始碼裡用到 `window_actions` function 的地方：(行號, function 名稱, 位置參數, 關鍵字參數)。"""
    tree = ast.parse(source)
    imported = _imported(tree)
    called = set()
    uses = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in imported:
            called.add(id(node.func))
            uses.append((node.lineno, imported[node.func.id], node.args, node.keywords))
    for node in ast.walk(tree):  # 沒有呼叫、直接交出去的
        if isinstance(node, ast.Name) and node.id in imported and id(node) not in called:
            uses.append((node.lineno, imported[node.id], [SETTINGS], []))
    return sorted(uses, key=lambda use: use[0])


def _passed_name(arg: ast.expr) -> str | None:
    """位置參數傳進去的名字（變數名，或屬性的名字）；不是名字時 None。"""
    if isinstance(arg, ast.Name):
        return arg.id
    if isinstance(arg, ast.Attribute):
        return arg.attr
    return None


def contract_problems(source: str) -> list[str]:
    """分頁原始碼裡傳錯參數的地方；都對時是空的。"""
    problems = []
    for line, name, args, keywords in action_uses(source):
        if any(isinstance(a, ast.Starred) for a in args) or any(k.arg is None for k in keywords):
            problems.append(f"第 {line} 行 {name}：`*args`／`**kwargs` 檢查不了參數")
            continue
        signature = inspect.signature(getattr(actions, name))
        params = list(signature.parameters)
        try:
            signature.bind(*args, **{k.arg: k.value for k in keywords})
        except TypeError as exc:
            problems.append(f"第 {line} 行 {name}：{exc}")
            continue
        for position, arg in enumerate(args):
            passed = _passed_name(arg)
            if passed in params and passed != params[position]:
                problems.append(f"第 {line} 行 {name}：`{passed}` 放在 `{params[position]}` 的位置")
    return problems


def _source(name: str) -> str:
    return (TABS / name).read_text(encoding="utf-8")


def _line_of(source: str, text: str) -> int:
    return next(i for i, line in enumerate(source.splitlines(), 1) if text in line)


@pytest.mark.parametrize("module", sorted(TABS.glob("*.py")), ids=lambda p: p.name)
def test_tabs_pass_the_actions_their_parameters(module: Path) -> None:
    assert contract_problems(module.read_text(encoding="utf-8")) == []


def test_every_imported_action_is_checked() -> None:
    """分頁 import 的每個 `window_actions` function 都有檢查到（呼叫或交給 `run_action`）；沒有沒用到的 import。"""
    for module in TABS.glob("*.py"):
        source = module.read_text(encoding="utf-8")
        imported = set(_imported(ast.parse(source)).values())
        assert {name for _, name, _, _ in action_uses(source)} == imported, module.name


def test_swapped_arguments_are_caught() -> None:
    source = _source("daily.py")
    call = "run_price_csv(settings, paths, source)"
    swapped = source.replace("run_price_csv(settings, source, paths)", call)
    assert swapped != source
    line = _line_of(swapped, call)

    assert contract_problems(swapped) == [
        f"第 {line} 行 run_price_csv：`paths` 放在 `source` 的位置",
        f"第 {line} 行 run_price_csv：`source` 放在 `paths` 的位置",
    ]


@pytest.mark.parametrize(
    ("call", "found"),
    [
        ("load_progress_rows(settings)", "missing a required argument: 'paths'"),
        ("load_progress_rows(settings, paths, day=1)", "unexpected keyword argument 'day'"),
        ("load_progress_rows(*settings)", "`*args`／`**kwargs`"),
    ],
)
def test_a_missing_unknown_or_unpacked_argument_is_caught(call: str, found: str) -> None:
    source = _source("progress.py")
    changed = source.replace("load_progress_rows(settings, paths)", call)
    assert changed != source

    (problem,) = contract_problems(changed)

    assert "load_progress_rows" in problem and found in problem


def test_an_action_handed_to_run_action_gets_only_the_settings() -> None:
    """`run_action(read_latest_import, …)`：之後 function 多一個必填參數時要抓到。"""
    source = _source("mail.py").replace(
        "run_action(\n            read_latest_import,", "run_action(\n            make_mail_drafts,"
    )
    assert "make_mail_drafts," in source

    assert any("make_mail_drafts" in p and "missing a required argument" in p for p in contract_problems(source))
