"""分頁 → 動作的契約（#208）：分頁呼叫 `window_actions` 的參數要對得上那個 function 的參數。

分頁測試把動作換成假的、動作測試打真的資料庫，兩邊各自通過時，分頁把參數傳錯（例：對調 `source, paths`）也看不出來。
這裡讀分頁的原始碼（`ast`），找出每一個呼叫 `window_actions` function 的地方，檢查：
- 參數個數與關鍵字對得上 function 的簽名（`inspect.signature(...).bind`）；
- 位置參數是一個名字（`paths`、`read.drafts` 的 `drafts`）而且剛好是那個 function 另一個參數的名字時，算傳錯位置。
名字和參數不同、但不是別的參數名（例：`config_paths` 傳給 `paths`）不算錯。不碰資料庫、不開視窗。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

import sp_workflow.window_actions as actions

TABS = Path(actions.__file__).parent / "tabs"


def action_calls(source: str) -> list[tuple[int, str, ast.Call]]:
    """原始碼裡呼叫 `window_actions` function 的地方：(行號, function 名稱, 呼叫)。"""
    tree = ast.parse(source)
    imported = {
        alias.asname or alias.name: alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "sp_workflow.window_actions"
        for alias in node.names
        if inspect.isfunction(getattr(actions, alias.name, None))
    }
    return [
        (node.lineno, imported[node.func.id], node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in imported
    ]


def _name(arg: ast.expr) -> str | None:
    if isinstance(arg, ast.Name):
        return arg.id
    if isinstance(arg, ast.Attribute):
        return arg.attr
    return None


def contract_problems(source: str) -> list[str]:
    """分頁原始碼裡傳錯參數的呼叫；都對時是空的。"""
    problems = []
    for line, name, call in action_calls(source):
        signature = inspect.signature(getattr(actions, name))
        params = list(signature.parameters)
        try:
            signature.bind(*call.args, **{k.arg: k.value for k in call.keywords if k.arg is not None})
        except TypeError as exc:
            problems.append(f"第 {line} 行 {name}：{exc}")
            continue
        for position, arg in enumerate(call.args):
            passed = _name(arg)
            if passed in params and passed != params[position]:
                problems.append(f"第 {line} 行 {name}：`{passed}` 放在 `{params[position]}` 的位置")
    return problems


@pytest.mark.parametrize("module", sorted(TABS.glob("*.py")), ids=lambda p: p.name)
def test_tabs_pass_the_actions_their_parameters(module: Path) -> None:
    assert contract_problems(module.read_text(encoding="utf-8")) == []


def test_every_tab_action_call_is_checked() -> None:
    """有讀到分頁的呼叫（避免 import 寫法變了、這裡什麼都沒檢查）。"""
    called = {name for module in TABS.glob("*.py") for _, name, _ in action_calls(module.read_text(encoding="utf-8"))}

    assert {"run_daily_import", "run_price_csv", "load_progress_rows", "save_mailbox_person"} <= called


def test_swapped_arguments_are_caught() -> None:
    source = (TABS / "daily.py").read_text(encoding="utf-8")
    swapped = source.replace("run_price_csv(settings, source, paths)", "run_price_csv(settings, paths, source)")
    assert swapped != source

    assert contract_problems(swapped) == [
        f"第 {_line_of(swapped, 'run_price_csv(settings, paths, source)')} 行 run_price_csv：`paths` 放在 `source` 的位置",
        f"第 {_line_of(swapped, 'run_price_csv(settings, paths, source)')} 行 run_price_csv：`source` 放在 `paths` 的位置",
    ]


def test_a_missing_or_unknown_argument_is_caught() -> None:
    source = (TABS / "progress.py").read_text(encoding="utf-8")
    missing = source.replace("load_progress_rows(settings, paths)", "load_progress_rows(settings)")
    unknown = source.replace("load_progress_rows(settings, paths)", "load_progress_rows(settings, paths, day=1)")

    assert len(contract_problems(missing)) == 1 and "load_progress_rows" in contract_problems(missing)[0]
    assert len(contract_problems(unknown)) == 1 and "day" in contract_problems(unknown)[0]


def _line_of(source: str, text: str) -> int:
    return next(i for i, line in enumerate(source.splitlines(), 1) if text in line)
