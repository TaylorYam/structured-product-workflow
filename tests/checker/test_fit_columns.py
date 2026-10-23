"""結果表依內容自動調整欄寬（#192）：列有變動後重算，表頭與內容最寬的加留白，內容最寬有上限，最後一欄不縮。"""

from __future__ import annotations

from fcn_checker.ui import FitTreeview


def table(master) -> FitTreeview:
    found = FitTreeview(master, columns=("tdcc", "issuer", "note"), show="headings")
    for column, label in (("tdcc", "TDCC"), ("issuer", "上手"), ("note", "說明")):
        found.heading(column, text=label)
        found.column(column, width=300, stretch=column == "note")
    return found


def widths(found: FitTreeview) -> dict[str, int]:
    return {column: int(found.column(column, "width")) for column in found["columns"]}


def test_columns_fit_the_content_after_rows_change(master) -> None:
    found = table(master)
    found.update_idletasks()
    empty = widths(found)
    assert all(width < 300 for width in empty.values()), "沒有列時照表頭寬度，不留一大片空白"

    found.insert("", "end", values=("029000006427", "NATIXIS", "短"))
    assert found._fit_job is not None, "等畫面空下來再算"
    found.update_idletasks()
    filled = widths(found)

    assert found._fit_job is None
    assert filled["tdcc"] > empty["tdcc"] and filled["issuer"] > empty["issuer"], "內容比表頭寬時跟著內容"
    assert int(found.column("note", "minwidth")) == filled["note"], "最後一欄不縮到比內容窄（#179）"

    found.delete(*found.get_children())
    found.update_idletasks()
    assert widths(found) == empty, "列變少了欄也跟著變窄"


def test_very_long_text_is_capped(master) -> None:
    found = table(master)
    found.insert("", "end", values=("029000006427", "SG", "很長的原因" * 60))
    found.update_idletasks()

    scale = found.winfo_fpixels("1i") / 96
    assert int(found.column("note", "width")) == round(FitTreeview.MAX_WIDTH * scale)


def test_changing_a_value_refits(master) -> None:
    found = table(master)
    item = found.insert("", "end", values=("1", "SG", ""))
    found.update_idletasks()
    narrow = widths(found)["issuer"]

    found.set(item, "issuer", "NATIXIS LONG NAME")
    found.update_idletasks()
    assert widths(found)["issuer"] > narrow

    found.item(item, values=("1", "SG", ""))
    found.update_idletasks()
    assert widths(found)["issuer"] == narrow
