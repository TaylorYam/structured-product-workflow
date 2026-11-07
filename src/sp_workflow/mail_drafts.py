"""寄信草稿：為核對通過的商品（FCN、FCN_Express、Super Bull、BEN、DACN，#302、#310、#373、#367）在 Outlook 建立 TS／IIS 草稿給承作的 FC（#29，需求更新見 #29 留言）。

對外只有 `create_drafts`。資料全部讀最小 FCN 資料庫（#32、#35），不讀 Excel。每檔 TDCC 都要符合：
1. 資料庫有這檔的商品條件（沒有缺信裡要用的必填欄，#114）與交易列；
2. 根目錄底下找得到剛好一個商品資料夾，裡面有 `<TDCC>_TS.pdf` 與 `<TDCC>_IIS.pdf`
   （準備與放回工具只會以標準檔名放回核對通過的文件，所以這就代表核對通過）；
3. 交易列上的 FC1、FC2（#420；略過 PB）都對得到信箱（不分大小寫、別名）。
只有 PB 承作時「不需寄送」；每日匯入略過的不支援商品標「不支援的商品類型」。符合的一檔建一封草稿：收件人是 FC1、FC2，副本是信箱的固定副本（已在收件人的不重複）。
風險等級寫自算風險等級（#239、ADR 0023，回填的是 RR4）；待算或還沒算過時寫「待確認」，照樣建草稿，結果清單的原因寫出來，
請寄出前補上（不自動寫 RR4，以免 2026-10-30 以後交易的商品寫錯等級）。
信件內文是寄信格式範本（Excel，未設定時用內建範本，#279）填好值後貼上的三張表；信件表每一欄的表頭與取值只在
`SUMMARY_COLUMNS`／`TERMS_COLUMNS` 宣告一次，沒有範本時報錯、不退回 HTML 內文（#499）。
只建草稿、不寄出；同一檔重跑會再建一封；某一檔出錯只影響那一檔。結果寫成 `寄信草稿清單_<日期時間>.xlsx`，
並記錄用的是哪一次每日匯入。商品資料夾只讀。
傳入的 `conn` 由 `database.connect` 建立，資料表版本已確認（#200），這裡不再檢查。
"""

from __future__ import annotations

import datetime as dt
import shutil
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openpyxl.utils import get_column_letter

from fcn_checker.products import SUPPORTED_PRODUCTS
from sp_workflow.daily_import import ImportInfo, latest_import
from sp_workflow.database import OperatorError, load_env_file
from sp_workflow.file_tools import save_book, table_book, tdcc_as_text
from sp_workflow.mailbox import Mailbox, is_pb, load_mailbox
from sp_workflow.preparation import find_product_folders, is_tdcc, parse_tdccs
from sp_workflow.product_record import (
    PRICING_FIELD,
    REQUIRED,
    STRIKE_FIELD,
    invert_upfront,
    missing_fields,
)
from sp_workflow.product_store import read
from sp_workflow.risk_level import RiskLevel, risk_level_of

if TYPE_CHECKING:
    import psycopg

DRAFTED = "已建草稿"
PENDING = "待處理"
NOT_NEEDED = "不需寄送"
FAILED = "建立失敗"

SUBJECT_FORMAT = "[中文TS/IIS] [{product}] - {tdcc}"  # 例：[中文TS/IIS] [FCN] - 029199990001
BODY_INTRO = "FYI"
RISK_TO_CONFIRM = "待確認"  # 風險等級待算或還沒算過時草稿上寫這個（#239）

LIST_COLUMNS = (("TDCC", 15), ("狀態", 10), ("收件人", 40), ("副本", 50), ("原因", 80))


class MailDraftError(OperatorError):
    """整批無法建立草稿（資料庫、信箱或路徑有問題）；訊息是給作業人員看的中文。"""


class MailTemplateError(MailDraftError):
    """沒有寄信格式範本可用（沒傳範本，或內建範本遺失）；不退回 HTML 內文（#499）。"""


@dataclass(frozen=True)
class Draft:
    tdcc: str
    to: tuple[str, ...]  # 信箱
    cc: tuple[str, ...]
    subject: str
    attachments: tuple[Path, ...]
    template_path: Path | None = None
    template_product: dict[str, Any] | None = None
    template_branches: tuple[str, ...] = ()
    risk_level: str = RISK_TO_CONFIRM  # 草稿上的風險等級（`risk_text`）


Drafter = Callable[[Draft], None]


@dataclass(frozen=True)
class DraftOutcome:
    tdcc: str
    status: str
    to: tuple[str, ...] = ()  # 人名
    cc: tuple[str, ...] = ()
    reason: str = ""
    to_confirm: bool = False  # 建了草稿、但風險等級寫「待確認」（待算或還沒算過，#239）


@dataclass(frozen=True)
class DraftReceipt:
    list_path: Path
    outcomes: tuple[DraftOutcome, ...]
    last_import: ImportInfo | None
    unreadable_dirs: tuple[Path, ...] = ()


MAIL_TEMPLATE_ENV = "SPW_MAIL_TEMPLATE"
BUNDLED_MAIL_TEMPLATE = Path(__file__).resolve().parent / "assets" / "TS IIS信件寄送格式.xlsx"


def mail_template_from_env(env_path: Path) -> Path | None:
    """寄信格式範本：`.env` 的 SPW_MAIL_TEMPLATE；#279 起未設定時回內建範本 `BUNDLED_MAIL_TEMPLATE`（不是 None）。"""
    values = load_env_file(env_path)  # 設定檔有問題時丟 `DatabaseError`
    raw = values.get(MAIL_TEMPLATE_ENV)
    # 未設定時使用專案內建範本；設定了路徑則以該檔為準
    if not raw:
        return BUNDLED_MAIL_TEMPLATE
    path = Path(raw)
    if not path.is_file():
        raise MailDraftError(f"MAIL 草稿格式範本不存在：{path}")
    return path


def create_drafts(
    text: str,
    root: Path,
    out_parent: Path,
    conn: psycopg.Connection,
    *,
    drafter: Drafter | None = None,
    now: dt.datetime | None = None,
    template_path: Path | None = None,
) -> DraftReceipt:
    """依輸入順序處理每個 TDCC；回傳結果清單路徑、逐檔結果與最近一次每日匯入。

    drafter 預設用 Outlook 建草稿（只有真的要建時才會啟動 Outlook）。
    """
    tokens = parse_tdccs(text)
    if not tokens:
        raise MailDraftError("沒有輸入 TDCC")
    if not root.is_dir():
        raise MailDraftError(f"根目錄不存在：{root}")
    if not out_parent.is_dir():
        raise MailDraftError(f"結果清單的位置不存在：{out_parent}")
    with conn.transaction():
        mailbox = load_mailbox(conn)
    last_import = latest_import(conn)
    fixed_cc = _order_fixed_cc(tuple(p for p in mailbox.people if p.fixed_cc))
    if not fixed_cc:
        raise MailDraftError("信箱沒有任何固定副本（mailbox.person 的 fixed_cc），請先設定")
    stamp = f"{(now or dt.datetime.now()):%Y%m%d-%H%M%S}"
    list_path = out_parent / f"寄信草稿清單_{stamp}.xlsx"
    if list_path.exists():
        raise MailDraftError(f"結果清單已存在：{list_path}")

    valid = {t for t in tokens if is_tdcc(t)}
    folders, unreadable = find_product_folders(root, valid)
    make_draft = drafter or OutlookDrafter()
    outcomes = []
    for tdcc in tokens:
        if tdcc not in valid:
            outcomes.append(DraftOutcome(tdcc, PENDING, reason="不是 12 碼數字"))
            continue
        try:
            outcome = _draft_one(
                tdcc,
                conn,
                mailbox,
                fixed_cc,
                folders.get(tdcc, []),
                unreadable,
                make_draft,
                template_path,
            )
        except Exception as exc:  # 一檔出錯（資料、內文、資料庫）只影響這一檔，結果清單照樣寫出
            outcome = DraftOutcome(tdcc, FAILED, reason=f"處理失敗：{exc}")
        outcomes.append(outcome)
    _write_list(list_path, outcomes, last_import, unreadable)
    return DraftReceipt(list_path, tuple(outcomes), last_import, tuple(unreadable))


def _draft_one(
    tdcc: str,
    conn: psycopg.Connection,
    mailbox: Mailbox,
    fixed_cc: Sequence[Any],
    folders: Sequence[Path],
    unreadable: Sequence[Path],
    make_draft: Drafter,
    template_path: Path | None,
) -> DraftOutcome:
    with conn.transaction():
        stored = read(conn, tdcc)
        product = None if stored is None else stored.columns
        branches = [] if stored is None else [branch for branch, _ in stored.branches]
        # FC1、FC2 都是收件人（#420）：依交易列順序，每列先 FC1 再 FC2
        fc_names = [n for row in conn.execute(
            "SELECT fc_name, fc2_name FROM workflow.trade_row WHERE tdcc = %s ORDER BY row_seq", (tdcc,)
        ) for n in row if n is not None]  # fmt: skip
        other = conn.execute("SELECT product FROM workflow.other_product WHERE tdcc = %s", (tdcc,)).fetchone()
        risk = risk_level_of(conn, tdcc)
    if product is None and not fc_names and other is not None:
        return DraftOutcome(tdcc, PENDING, reason=f"不支援的商品類型：{other[0]}（目前只做 {SUPPORTED_PRODUCTS}）")
    problems = []
    if product is None:
        problems.append("資料庫沒有這檔的商品條件（還沒匯入起單資料）")
    else:
        missing = missing_fields(product, _MAIL_REQUIRED)
        if missing:
            problems.append(f"商品條件缺 {'、'.join(missing)}")
        if product["issue_date"] is None:
            problems.append("商品資料還沒有發行日（等核對回填）")
    if not fc_names:
        problems.append("資料庫沒有這檔的交易列")
    names = list(dict.fromkeys(n for n in fc_names if not is_pb(n)))
    if fc_names and not names:
        return DraftOutcome(tdcc, NOT_NEEDED, reason="只有 PB 承作，不需寄送")
    people = [mailbox.match(n) for n in names]
    unmatched = [n for n, p in zip(names, people, strict=True) if p is None]
    if unmatched:
        problems.append("名字對不到信箱：" + "、".join(unmatched))
    attachments, folder_problem = _attachments(tdcc, folders, unreadable)
    if folder_problem:
        problems.append(folder_problem)
    if problems:
        return DraftOutcome(tdcc, PENDING, reason="；".join(problems))

    to = list(dict.fromkeys(p for p in people if p is not None))
    cc = [p for p in fixed_cc if p not in to]
    level, note = risk_text(risk)
    draft = Draft(
        tdcc=tdcc,
        to=tuple(p.email for p in to),
        cc=tuple(p.email for p in cc),
        subject=SUBJECT_FORMAT.format(product=product["product"], tdcc=tdcc),
        attachments=attachments,
        template_path=template_path,
        template_product=product,
        template_branches=tuple(branches),
        risk_level=level,
    )
    to_names, cc_names = tuple(p.name for p in to), tuple(p.name for p in cc)
    try:
        make_draft(draft)
    except Exception as exc:  # 一檔失敗不影響其他檔；原因寫進結果
        return DraftOutcome(tdcc, FAILED, to_names, cc_names, f"建立草稿失敗：{exc}")
    return DraftOutcome(tdcc, DRAFTED, to_names, cc_names, note, to_confirm=level == RISK_TO_CONFIRM)


def risk_text(risk: RiskLevel | None) -> tuple[str, str]:
    """草稿上的風險等級與結果清單的提醒：自算風險等級（完成、回填）照寫；待算或還沒算過寫「待確認」並提醒寄出前補上。"""
    if risk is not None and risk.level:
        return risk.level, ""
    why = f"風險等級待算（{risk.reason}）" if risk is not None else "資料庫還沒有風險等級"
    return RISK_TO_CONFIRM, f"{why}，草稿寫「{RISK_TO_CONFIRM}」，寄出前請補上風險等級"


def _order_fixed_cc(people: Sequence[Any]) -> tuple[Any, ...]:
    def key(person: Any) -> tuple[int, str]:
        name = person.name.strip().casefold()
        if name == "max":
            return 0, name
        if name == "bob":
            return 1, name
        if (person.unit or "").strip().casefold() == "pm":
            return 2, name
        return 3, name

    return tuple(sorted(people, key=key))


# 信裡要用、空白就不寄的必填欄（#114）：信裡沒有 Strike date；期初價格空白時信裡空白（以前清單更新的商品可能沒有）
_MAIL_REQUIRED = tuple(name for name in REQUIRED if name not in (STRIKE_FIELD, PRICING_FIELD))


def _attachments(tdcc: str, folders: Sequence[Path], unreadable: Sequence[Path]) -> tuple[tuple[Path, ...], str]:
    if not folders:
        hint = f"（搜尋時有 {len(unreadable)} 個資料夾讀不到，可能在裡面）" if unreadable else ""
        return (), "根目錄底下找不到商品資料夾" + hint
    if len(folders) > 1:
        return (), f"有 {len(folders)} 個同名商品資料夾：" + "、".join(map(str, folders))
    files = tuple(folders[0] / f"{tdcc}_{kind}.pdf" for kind in ("TS", "IIS"))
    missing = [f.name for f in files if not f.is_file()]
    if missing:
        return (), "商品資料夾沒有 " + "、".join(missing) + "（還沒核對通過或還沒放回）"
    return files, ""


# ---------- 信件表 ----------


@dataclass(frozen=True)
class MailRow:
    """一封信的信件表要用的資料：TDCC、資料庫的商品條件與草稿上的風險等級（`risk_text`）。"""

    tdcc: str
    product: dict[str, Any]
    risk_level: str


@dataclass(frozen=True)
class MailColumn:
    """信件表的一欄：範本上印好的表頭（對照用）與這封信要貼的值。"""

    header: str
    value: Callable[[MailRow], object]


def _field(name: str) -> Callable[[MailRow], object]:
    return lambda row: row.product[name]


def _text(name: str) -> Callable[[MailRow], object]:
    return lambda row: row.product[name] or ""


def _or_dash(name: str) -> Callable[[MailRow], object]:
    """沒有值寫「-」：BEN 預設沒有 KO（#406）、沒有 KI 的商品沒有 KI Barrier。"""
    return lambda row: "-" if row.product[name] is None else row.product[name]


def _date_cell(name: str) -> Callable[[MailRow], object]:
    return lambda row: _excel_date(row.product[name])


SUMMARY_ROW, TERMS_ROW, BRANCH_ROW = 2, 5, 7  # 範本裡要填的列；表頭在上一列（分公司的表頭也由這裡寫）
SUMMARY_COLUMNS = (  # 範本 A1:G2
    MailColumn("TDCC", lambda row: row.tdcc),
    MailColumn("Trade date", _date_cell("trade_date")),
    MailColumn("Issue date", _date_cell("issue_date")),
    MailColumn("Final valuation date", _date_cell("final_valuation_date")),
    MailColumn("Maturity date", _date_cell("maturity_date")),
    MailColumn("期初價格", _text("initial_pricing")),
    MailColumn("風險等級", lambda row: row.risk_level),
)
TERMS_COLUMNS = (  # 範本 A4:P5
    MailColumn("Product", _field("product")),
    MailColumn("Currency", _field("currency")),
    MailColumn("Guaranteed Periods (m)", _field("non_call_months")),
    *(MailColumn(f"BBG Code {i}", _text(f"underlying_{i}")) for i in range(1, 6)),
    MailColumn("Strike (%)", _field("strike_pct")),
    MailColumn("KO Type", _field("ko_type")),
    MailColumn("KO Barrier (%)", _or_dash("ko_pct")),
    MailColumn("Coupon p.a. (%)", _field("coupon_pct")),
    MailColumn("Upfront / NotePrice (%)", lambda row: invert_upfront(row.product["upfront_pct"])),
    MailColumn("Tenor (m)", _field("tenor_months")),
    MailColumn("Barrier Type", _field("barrier_type")),
    MailColumn("KI Barrier (%)", _or_dash("ki_pct")),
)


def template_values(tdcc: str, product: dict[str, Any], risk_level: str) -> dict[tuple[int, int], object]:
    """貼進範本的值：(列, 欄) → 值。Decimal 轉 float（COM 不收 Decimal）；日期是當天 UTC 午夜（#411）。"""
    row = MailRow(tdcc, product, risk_level)
    values: dict[tuple[int, int], object] = {}
    for sheet_row, columns in ((SUMMARY_ROW, SUMMARY_COLUMNS), (TERMS_ROW, TERMS_COLUMNS)):
        for column, spec in enumerate(columns, 1):
            value = spec.value(row)
            values[(sheet_row, column)] = float(value) if isinstance(value, Decimal) else value
    return values


# ---------- Outlook ----------


OUTLOOK_FAILURE = "Outlook 無法建立郵件（可能還沒設定 Outlook 帳號、Outlook 啟動時跳出對話框，或附件打不開）"


class OutlookError(Exception):
    """Outlook 建草稿失敗；訊息是給作業人員看的中文，附上 COM 錯誤碼方便維護人員查。"""


class OutlookDrafter:
    """用傳統版 Outlook（COM）在執行者的預設信箱建草稿；只呼叫 Save()，不會寄出。"""

    def __call__(self, draft: Draft) -> None:
        template_path = _require_template(draft.template_path)
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()  # 視窗在背景執行緒呼叫，COM 要在這個執行緒初始化
        excel = None
        workbook = None
        temp_dir = None
        try:
            mail = win32com.client.Dispatch("Outlook.Application").CreateItem(0)  # 0 = olMailItem
            mail.To = "; ".join(draft.to)
            mail.CC = "; ".join(draft.cc)
            mail.Subject = draft.subject
            if draft.template_product is None:
                raise OutlookError("MAIL 草稿缺少 Excel 範本資料")
            excel, workbook, sheet, temp_dir = self._open_template(
                win32com.client,
                template_path,
                draft.tdcc,
                draft.template_product,
                draft.template_branches,
                draft.risk_level,
            )
            inspector = mail.GetInspector
            inspector.Display()
            inspector.Activate()
            time.sleep(1)
            self._paste_template(sheet, inspector.WordEditor, len(draft.template_branches))
            for path in draft.attachments:
                mail.Attachments.Add(str(path))
            mail.Save()
        except pythoncom.com_error as exc:  # 原始訊息常是系統語系的亂碼，改成中文說明
            code = exc.args[0] if exc.args else ""
            hresult = f"0x{code & 0xFFFFFFFF:08X}" if isinstance(code, int) else str(code)
            raise OutlookError(f"{OUTLOOK_FAILURE}，錯誤碼 {hresult}") from exc
        finally:
            if workbook is not None:
                workbook.Close(SaveChanges=False)
            if excel is not None:
                excel.Quit()
            if temp_dir is not None:
                shutil.rmtree(temp_dir, ignore_errors=True)
            pythoncom.CoUninitialize()

    @staticmethod
    def _open_template(
        win32com: Any,
        template_path: Path,
        tdcc: str,
        product: dict[str, Any],
        branches: Sequence[str],
        risk_level: str,
    ) -> tuple[Any, Any, Any, str]:
        if len(branches) > 16:
            raise OutlookError("MAIL 格式範本最多支援 16 間分公司")
        excel = win32com.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        temp_dir = tempfile.mkdtemp(prefix="spw-mail-")
        temp_path = Path(temp_dir) / template_path.name
        shutil.copy2(template_path, temp_path)
        workbook = excel.Workbooks.Open(str(temp_path), ReadOnly=False)
        sheet = workbook.Worksheets(1)
        sheet.Columns(5).ColumnWidth = 12.0  # BBG Code 2：比範本略寬，避免表頭擠壓
        sheet.Cells(SUMMARY_ROW, 1).NumberFormat = "@"  # TDCC 當文字，不變成數字
        for (row, column), value in template_values(tdcc, product, risk_level).items():
            sheet.Cells(row, column).Value = value
        sheet.Range("A7:P8").ClearContents()
        for column, branch in enumerate(branches, 1):
            if column > 4:
                letter = get_column_letter(column)
                sheet.Range("D7:D8").Copy(sheet.Range(f"{letter}7"))
            sheet.Cells(BRANCH_ROW, column).Value = f"分公司 {column}"
            sheet.Cells(BRANCH_ROW + 1, column).Value = branch
        workbook.Save()
        return excel, workbook, sheet, temp_dir

    @staticmethod
    def _paste_template(sheet: Any, word_editor: Any, branch_count: int) -> None:
        selection = word_editor.Application.Selection
        selection.WholeStory()
        selection.Delete()
        selection.TypeText(BODY_INTRO)
        selection.TypeParagraph()
        selection.TypeParagraph()
        ranges = ("A1:G2", "A4:P5")
        if branch_count:
            ranges += (f"A7:{get_column_letter(branch_count)}8",)
        for address in ranges:
            sheet.Range(address).Copy()
            selection.PasteExcelTable(False, False, False)
            selection.TypeParagraph()


def _require_template(path: Path | None) -> Path:
    """沒有範本就停下來（#499）：正式環境 `mail_template_from_env` 一定給路徑，沒有就是設定或安裝有問題。"""
    if path is None:
        raise MailTemplateError(
            f"沒有寄信格式範本，無法建草稿：請在 .env 設定 {MAIL_TEMPLATE_ENV}，"
            f"或確認內建範本 {BUNDLED_MAIL_TEMPLATE.name} 還在。"
        )
    if not path.is_file():
        raise MailTemplateError(
            f"寄信格式範本不見了，無法建草稿：{path}。請在 .env 設定 {MAIL_TEMPLATE_ENV}，或重新安裝補回內建範本。"
        )
    return path


def _excel_date(value: dt.date) -> dt.datetime:
    """寫進 Excel 儲存格（COM）的日期：當天 00:00，標成 UTC。

    pywin32 會把沒有時區的 datetime 當本機時間換成 UTC 再交給 Excel，台北（UTC+8）的午夜會變成前一天 16:00，
    範本顯示成 yyyy/m/d 就早一天（#411）；標成 UTC 就不會被換算。
    """
    return dt.datetime.combine(value, dt.time.min, tzinfo=dt.UTC)


# ---------- 結果清單 ----------


def _write_list(
    path: Path, outcomes: Sequence[DraftOutcome], last_import: ImportInfo | None, unreadable: Sequence[Path]
) -> None:
    book = table_book(
        "寄信草稿清單",
        [name for name, _ in LIST_COLUMNS],
        ([o.tdcc, o.status, "、".join(o.to) or None, "、".join(o.cc) or None, o.reason or None] for o in outcomes),
        widths=[width for _, width in LIST_COLUMNS],
        style=tdcc_as_text,
    )
    info = book.create_sheet("資料來源")
    if last_import is None:
        info.append(["最近一次每日匯入", "沒有匯入紀錄"])
    else:
        info.append(["最近一次每日匯入", f"{last_import.imported_at:%Y-%m-%d %H:%M:%S}"])
        info.append(["匯入編號", last_import.run_id])
        info.append(["每日更新檔", last_import.file_name])
        info.append(["SHA-256", last_import.sha256])
    info.append(["讀不到的資料夾", len(unreadable)])
    for folder in unreadable:
        info.append([None, str(folder)])
    info.column_dimensions["A"].width = 20
    info.column_dimensions["B"].width = 70
    save_book(book, path)
