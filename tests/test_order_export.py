"""匯出問題信件（#42）：用虛構商品資料夾與假的 .msg 檔，不開 Outlook。"""

from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path

import pytest

from sp_workflow.order_export import ExportError, can_export, export_problem_mails
from sp_workflow.order_terms import NO_ORDER, PENDING, READ, UNSUPPORTED, OrderReceipt, OrderRow

NOW = dt.datetime(2026, 10, 20, 15, 0, 0)


def folder(root: Path, tdcc: str, *names: str) -> Path:
    path = root / "FCN" / tdcc
    path.mkdir(parents=True)
    for name in names:
        (path / name).write_bytes(f"msg {name}".encode())
    (path / "TS.pdf").write_bytes(b"pdf")  # 不是 .msg，不複製
    return path


@pytest.fixture
def setup(tmp_path: Path):
    root, out = tmp_path / "root", tmp_path / "out"
    out.mkdir()
    ok = folder(root, "029199990001", "order.msg")
    pending = folder(root, "328199990001", "(Order) two.msg", "RE (Order).msg")
    unsupported = folder(root, "999199990001", "order.msg")
    list_path = out / "Order條件清單_20261020-145900.xlsx"
    list_path.write_bytes(b"xlsx")
    rows = (
        OrderRow("029199990001", READ, folders=(ok,)),
        OrderRow("328199990001", PENDING, folders=(pending,)),
        OrderRow("328199990002", PENDING, folders=(pending,)),  # 同一封信的另一檔，資料夾只複製一次
        OrderRow("999199990001", UNSUPPORTED, folders=(unsupported,)),
        OrderRow("156199990001", NO_ORDER, reasons=("找不到商品資料夾",)),
    )
    return OrderReceipt(list_path, rows), out


def test_only_problem_folders_are_copied(setup) -> None:
    receipt, out = setup

    result = export_problem_mails(receipt, out, now=NOW)

    assert result.folder == out / "Order問題信件_20261020-150000"
    assert result.products == ("328199990001", "328199990002", "999199990001", "156199990001")
    assert (result.copied, result.failures) == (3, ())
    files = sorted(p.relative_to(result.folder).as_posix() for p in result.folder.rglob("*") if p.is_file())
    assert files == [
        "328199990001/(Order) two.msg",
        "328199990001/RE (Order).msg",
        "999199990001/order.msg",
        "Order條件清單_20261020-145900.xlsx",
    ]
    # 商品資料夾不動
    assert (out.parent / "root" / "FCN" / "328199990001" / "RE (Order).msg").exists()


def test_existing_export_folder_is_never_overwritten(setup) -> None:
    receipt, out = setup
    (out / "Order問題信件_20261020-150000").mkdir()

    with pytest.raises(ExportError, match="已存在"):
        export_problem_mails(receipt, out, now=NOW)


def test_nothing_to_export(tmp_path: Path) -> None:
    receipt = OrderReceipt(
        tmp_path / "list.xlsx",
        (OrderRow("029199990001", READ, folders=(tmp_path,)), OrderRow("156199990001", NO_ORDER)),
    )

    assert not can_export(receipt)
    with pytest.raises(ExportError, match="沒有需要匯出"):
        export_problem_mails(receipt, tmp_path, now=NOW)


def test_a_failed_copy_does_not_stop_the_rest(setup, monkeypatch: pytest.MonkeyPatch) -> None:
    receipt, out = setup
    real_copy = shutil.copy2

    def copy(src, dst, *args, **kwargs):
        if Path(src).name == "RE (Order).msg":
            raise PermissionError("拒絕存取")
        return real_copy(src, dst, *args, **kwargs)

    monkeypatch.setattr(shutil, "copy2", copy)

    result = export_problem_mails(receipt, out, now=NOW)

    assert result.copied == 2
    assert len(result.failures) == 1 and "RE (Order).msg" in result.failures[0] and "拒絕存取" in result.failures[0]


def test_same_named_folders_in_different_places_get_their_own_subfolders(tmp_path: Path) -> None:
    first = folder(tmp_path / "a", "029199990001", "one.msg")
    second = folder(tmp_path / "b", "029199990001", "two.msg")
    list_path = tmp_path / "list.xlsx"
    list_path.write_bytes(b"xlsx")
    receipt = OrderReceipt(list_path, (OrderRow("029199990001", NO_ORDER, folders=(first, second)),))

    result = export_problem_mails(receipt, tmp_path, now=NOW)

    assert (result.folder / "029199990001" / "one.msg").exists()
    assert (result.folder / "029199990001_2" / "two.msg").exists()
