"""測試用的虛構 PDF 與商品資料夾合成器；內容全部虛構，不含真實交易資料。"""

from __future__ import annotations

import re
import zipfile
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


def make_fake_word(path: Path, lines: list[str]) -> Path:
    """假的 Word 檔：內容是要轉成 PDF 的文字，交給 FakeConverter 轉。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_DOCX_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
_DOCX_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/></Relationships>'
)


def make_docx(path: Path, paragraphs: list[str | list[str]]) -> Path:
    """最小的真 .docx：每個元素一段；元素是 list 時拆成好幾個 run（Word 常把一行字拆開存）。"""
    body = "".join(
        '<w:p><w:pPr><w:jc w:val="both"/></w:pPr>'
        + "".join(f'<w:r><w:t xml:space="preserve">{run}</w:t></w:r>' for run in ([p] if isinstance(p, str) else p))
        + "</w:p>"
        for p in paragraphs
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W}"><w:body>{body}</w:body></w:document>'
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _DOCX_TYPES)
        zf.writestr("_rels/.rels", _DOCX_RELS)
        zf.writestr("word/document.xml", document)
    return path


def docx_lines(path: Path) -> list[str]:
    """.docx 每段的文字（給 FakeConverter 與測試讀回）。"""
    with zipfile.ZipFile(path) as zf:
        xml = zf.read("word/document.xml").decode("utf-8")
    return ["".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", p)) for p in re.findall(r"<w:p[ >].*?</w:p>", xml, re.S)]


class FakeConverter:
    """測試用轉檔器：把假 Word（純文字，或 `make_docx` 做的真 .docx）的文字寫成 PDF；記下每次收到的工作。

    error：整批失敗（模擬 Word 無法啟動或逾時）；fail：{Word 檔名: 原因}，個別失敗；produce=False：回報成功卻沒產生 PDF。
    """

    def __init__(self, *, error: str = "", fail: dict[str, str] | None = None, produce: bool = True) -> None:
        self.calls: list[list[tuple[Path, Path]]] = []
        self.error = error
        self.fail = fail or {}
        self.produce = produce

    def __call__(self, jobs) -> dict[Path, str]:
        from sp_workflow.conversion import ConversionError

        self.calls.append(list(jobs))
        if self.error:
            raise ConversionError(self.error)
        failures = {}
        for source, target in jobs:
            if source.name in self.fail:
                failures[source] = self.fail[source.name]
            elif self.produce:
                lines = (
                    docx_lines(source)
                    if zipfile.is_zipfile(source)
                    else source.read_text(encoding="utf-8").splitlines()
                )
                make_pdf(target, lines)
        return failures
