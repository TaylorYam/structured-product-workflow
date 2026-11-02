"""寄信格式範本的日期（#411）：四個日期寫進 Excel 後要跟資料庫同一天，不能被時區換算成前一天。資料全部虛構。"""

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


def product() -> dict:
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


def test_template_dates_are_utc_midnight_of_the_database_date(tmp_path: Path) -> None:
    """pywin32 把沒有時區的 datetime 當本機時間換成 UTC，台北的午夜會變成前一天 16:00；標成 UTC 才不會被換算。"""
    sheet = FakeSheet()
    workbook = types.SimpleNamespace(Worksheets=lambda index: sheet, Save=lambda: None)
    excel = types.SimpleNamespace(Workbooks=types.SimpleNamespace(Open=lambda path, ReadOnly: workbook))
    win32com = types.SimpleNamespace(DispatchEx=lambda name: excel)
    template = tmp_path / "template.xlsx"
    template.write_bytes(b"not opened")
    p = product()

    _, _, _, temp_dir = OutlookDrafter._open_template(win32com, template, "000000000000", p, ("北區",), "RR4")

    for column, field in DATE_COLUMNS.items():
        written = sheet.values[(2, column)]
        assert written == dt.datetime.combine(p[field], dt.time.min, tzinfo=dt.UTC), field
        assert written.utcoffset() == dt.timedelta(0), field
    shutil.rmtree(temp_dir)


@pytest.mark.skipif(
    sys.platform != "win32" or not os.environ.get("SPW_EXCEL_TEST"),
    reason="真的 Excel 測試只在本機設定 SPW_EXCEL_TEST 時執行（時區要在 UTC 以東才抓得到 #411）",
)
def test_real_excel_shows_the_database_dates() -> None:
    import pythoncom
    import win32com.client

    p = product()
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
