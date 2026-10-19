"""測試用的虛構 PDF 與商品資料夾合成器；內容全部虛構，不含真實交易資料。"""

from __future__ import annotations

from pathlib import Path

import pymupdf

FONT = "china-t"  # PyMuPDF 內建繁中字型，抽文字讀得回 Unicode


def make_pdf(path: Path, lines: list[str], *, pages: int = 1) -> Path:
    """每行一列寫在第一頁；pages > 1 時補空白頁。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    page = doc.new_page()
    for i, line in enumerate(lines):
        page.insert_text((40, 60 + i * 16), line, fontname=FONT, fontsize=10)
    for _ in range(pages - 1):
        doc.new_page()
    doc.save(path)
    doc.close()
    return path


def ts_lines(tdcc: str | None, *, preamble: list[str] = ()) -> list[str]:
    """中文產品說明書封面；tdcc 為 None 時封面不寫商品代號。"""
    code = [f"商品代號：TDCC商品代號為{tdcc}，受託機構商品代號為{tdcc}"] if tdcc else []
    return [
        *preamble,
        "第1頁，共30頁",
        "中文產品說明書",
        *code,
        "商品中文名稱：虛構銀行12個月美元計價結構型商品",
        "ISIN：XS0000000000",
    ]


def iis_lines(tdcc: str | None) -> list[str]:
    """中文投資人須知封面；第一頁後段提到產品說明書（真實樣本也是如此）。"""
    code = [f"TDCC商品代號{tdcc}，受託機構商品代號{tdcc}"] if tdcc else []
    filler = ["本商品風險程度為RR4，投資人應詳閱下列警語與說明。"] * 20
    return [
        "第1頁，共4頁",
        "中文投資人須知",
        "虛構銀行12個月美元計價結構型商品",
        *code,
        *filler,
        "詳細內容請參閱中文產品說明書。",
    ]


def english_ts_lines(tdcc: str) -> list[str]:
    return ["FINAL TERM SHEET", f"Product Code: {tdcc}", "Issuer: Fictional Bank PLC", "ISIN: XS0000000000"]


def elderly_form_lines(tdcc: str) -> list[str]:
    """高齡聲明書：第一頁很後面才提到中文產品說明書與中文投資人須知（寫上商品代號，只能靠標題位置排除）。"""
    filler = ["委託人於申購前，應充分瞭解下列所述之產品風險特性及可能產生的影響與相關說明。"] * 14
    return [
        "表單編號20251028",
        "高齡顧客商品適合度聲明書",
        f"商品代號：{tdcc}",
        *filler,
        "本人已收到中文產品說明書及中文投資人須知。",
    ]


def contract_form_lines() -> list[str]:
    return [
        "表單編號20260401",
        "境外結構型商品契約說明暨特別約定事項",
        "商品中文名稱：虛構銀行12個月美元計價結構型商品",
    ]


def make_product_folder(root: Path, tdcc: str, *, sub: str = "") -> Path:
    """建立一個標準的商品資料夾：中文說明書、投資人須知、英文版與其他文件。"""
    folder = root / sub / tdcc if sub else root / tdcc
    make_pdf(folder / f"XS0000000000_{tdcc}_CN TS.pdf", ts_lines(tdcc), pages=3)
    make_pdf(folder / f"XS0000000000_{tdcc}_CN IIS.pdf", iis_lines(tdcc), pages=4)
    make_pdf(folder / f"XS0000000000_{tdcc}_EN TS.pdf", english_ts_lines(tdcc))
    make_pdf(folder / f"FCN高齡顧客適合度聲明書_X_{tdcc}.pdf", elderly_form_lines(tdcc))
    make_pdf(folder / f"FCN境外結構型商品契約說明暨特別約定事項_X_{tdcc}.pdf", contract_form_lines())
    (folder / f"(Order)__Fictional--{tdcc}.msg").write_bytes(b"fake msg")
    (folder / f"境外結構型商品承作檢核表_{tdcc}.xlsx").write_bytes(b"fake xlsx")
    return folder


def snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    """根目錄底下每個檔案的內容與修改時間，用來確認原始檔沒被動過。"""
    return {
        str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns) for p in sorted(root.rglob("*")) if p.is_file()
    }
