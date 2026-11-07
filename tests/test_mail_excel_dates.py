"""寄信格式範本（#411、#499）：貼進範本的值逐格固定；四個日期寫進 Excel 後要跟資料庫
同一天，不能被時區換算成前一天。資料全部虛構。"""

from __future__ import annotations

import datetime as dt
import os
import shutil
import sys
import types
from decimal import Decimal
from pathlib import Path

import pytest

from sp_workflow.mail_drafts import BUNDLED_MAIL_TEMPLATE, OutlookDrafter

DATE_COLUMNS = {
    2: "trade_date",
    3: "issue_date",
    4: "final_valuation_date",
    5: "maturity_date",
}


def sample_template_product() -> dict:
    return {
        "trade_date": dt.date(2026, 10, 28),
        "issue_date": dt.date(2026, 11, 4),
        "final_valuation_date": dt.date(2027, 6, 4),
        "maturity_date": dt.date(2027, 6, 9),
        "initial_pricing": "",
        "product": "FCN",
        "currency": "USD",
        "non_call_months": 1,
        "underlying_1": "AAA UW",
        "underlying_2": "BBB UW",
        "underlying_3": None,
        "underlying_4": None,
        "underlying_5": None,
        "strike_pct": Decimal("80"),
        "ko_type": "Daily",
        "ko_pct": Decimal("100"),
        "coupon_pct": Decimal("12"),
        "upfront_pct": Decimal("1"),
        "tenor_months": 7,
        "barrier_type": "EKI",
        "ki_pct": Decimal("60"),
    }


class FakeSheet:
    """假的 Excel 工作表：記下每格被寫入的值。"""

    def __init__(self) -> None:
        self.values: dict[tuple[int, int], object] = {}

    def Cells(self, row: int, column: int):  # noqa: N802 — 對應 COM 方法名稱
        sheet = self

        class Cell:
            NumberFormat = None

            def __setattr__(self, name: str, value: object) -> None:
                if name == "Value":
                    sheet.values[(row, column)] = value
                object.__setattr__(self, name, value)

        return Cell()

    def Columns(self, column: int):  # noqa: N802
        return types.SimpleNamespace(ColumnWidth=None)

    def Range(self, address: str):  # noqa: N802
        return types.SimpleNamespace(ClearContents=lambda: None, Copy=lambda *args: None)


def fill_template(tmp_path: Path, product: dict, branches: tuple[str, ...], risk_level: str) -> FakeSheet:
    """用假的 Excel 跑一次 `_open_template`，回傳記下寫入值的工作表。"""
    sheet = FakeSheet()
    workbook = types.SimpleNamespace(Worksheets=lambda index: sheet, Save=lambda: None)
    excel = types.SimpleNamespace(Workbooks=types.SimpleNamespace(Open=lambda path, ReadOnly: workbook))
    win32com = types.SimpleNamespace(DispatchEx=lambda name: excel)
    template = tmp_path / "template.xlsx"
    template.write_bytes(b"not opened")
    _, _, _, temp_dir = OutlookDrafter._open_template(win32com, template, "000000000000", product, branches, risk_level)
    shutil.rmtree(temp_dir)
    return sheet


def test_template_cells_are_filled_from_the_declared_columns(tmp_path: Path) -> None:
    """#499 拿掉 HTML 內文前後，貼進範本的值逐格相同（含沒有 KO／KI 寫「-」、分公司超過 4 間）。"""
    utc = dt.UTC
    summary = {
        (2, 1): "000000000000",
        (2, 2): dt.datetime(2026, 10, 28, tzinfo=utc),
        (2, 3): dt.datetime(2026, 11, 4, tzinfo=utc),
        (2, 4): dt.datetime(2027, 6, 4, tzinfo=utc),
        (2, 5): dt.datetime(2027, 6, 9, tzinfo=utc),
        (2, 7): "RR3",
    }
    terms = {(5, c): v for c, v in enumerate(("USD", 1, "AAA UW", "BBB UW", "", "", ""), 2)}
    terms |= {(5, 9): 80.0, (5, 10): "Daily", (5, 12): 12.0, (5, 13): 99.0, (5, 14): 7}

    fcn = fill_template(tmp_path, sample_template_product(), ("北區",), "RR3")

    assert fcn.values == {
        **summary,
        (2, 6): "",
        (5, 1): "FCN",
        **terms,
        (5, 11): 100.0,
        (5, 15): "EKI",
        (5, 16): 60.0,
        (7, 1): "分公司 1",
        (8, 1): "北區",
    }
    branches = ("北區", "南區", "東區", "西區", "中區")
    product = {
        **sample_template_product(),
        "product": "BEN",
        "ko_pct": None,
        "ki_pct": None,
        "barrier_type": "NONE",
        "initial_pricing": "收盤價",
    }

    ben = fill_template(tmp_path, product, branches, "RR3")

    assert ben.values == {
        **summary,
        (2, 6): "收盤價",
        (5, 1): "BEN",
        **terms,
        (5, 11): "-",
        (5, 15): "NONE",
        (5, 16): "-",
        **{(7, c): f"分公司 {c}" for c in range(1, 6)},
        **{(8, c): b for c, b in enumerate(branches, 1)},
    }


def test_template_dates_are_utc_midnight_of_the_database_date(tmp_path: Path) -> None:
    """pywin32 把沒有時區的 datetime 當本機時間換成 UTC，台北的午夜會變成前一天 16:00；標成 UTC 才不會被換算。"""
    p = sample_template_product()
    sheet = fill_template(tmp_path, p, ("北區",), "RR4")

    for column, field in DATE_COLUMNS.items():
        written = sheet.values[(2, column)]
        assert written == dt.datetime.combine(p[field], dt.time.min, tzinfo=dt.UTC), field
        assert written.utcoffset() == dt.timedelta(0), field


@pytest.mark.skipif(
    sys.platform != "win32" or not os.environ.get("SPW_EXCEL_TEST"),
    reason="真的 Excel 測試只在本機設定 SPW_EXCEL_TEST 時執行（時區要在 UTC 以東才抓得到 #411）",
)
def test_real_excel_shows_the_database_dates() -> None:
    import pythoncom
    import win32com.client

    p = sample_template_product()
    pythoncom.CoInitialize()
    excel = workbook = temp_dir = None
    try:
        excel, workbook, sheet, temp_dir = OutlookDrafter._open_template(
            win32com.client, BUNDLED_MAIL_TEMPLATE, "000000000000", p, ("北區",), "RR4"
        )
        for column, field in DATE_COLUMNS.items():
            serial = (p[field] - dt.date(1899, 12, 30)).days
            assert sheet.Cells(2, column).Value2 == serial, field  # 整數 = 當天 00:00，沒有被減 8 小時
    finally:
        if workbook is not None:
            workbook.Close(SaveChanges=False)
        if excel is not None:
            excel.Quit()
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)
        pythoncom.CoUninitialize()
