"""寄信草稿：為核對通過的 FCN 在 Outlook 建立 TS／IIS 草稿給承作的 FC（#29，需求更新見 #29 留言）。

對外只有 `create_drafts`。資料全部讀最小 FCN 資料庫（#32、#35），不讀 Excel。每檔 TDCC 都要符合：
1. 資料庫有這檔的商品條件與交易列；
2. 根目錄底下找得到剛好一個商品資料夾，裡面有 `<TDCC>_TS.pdf` 與 `<TDCC>_IIS.pdf`
   （準備與放回工具只會以標準檔名放回核對通過的文件，所以這就代表核對通過）；
3. 交易列上的 FC（略過 PB）都對得到信箱（不分大小寫、別名）。
只有 PB 承作時「不需寄送」。符合的一檔建一封草稿：收件人是 FC，副本是信箱的固定副本（已在收件人的不重複）。
只建草稿、不寄出；同一檔重跑會再建一封。結果寫成 `寄信草稿清單_<日期時間>.xlsx`。商品資料夾只讀。
"""

from __future__ import annotations

import datetime as dt
import html
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import openpyxl
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from sp_workflow.daily_import import PB, Mailbox, load_mailbox
from sp_workflow.database import DatabaseError, require_current_schema
from sp_workflow.preparation import find_product_folders, parse_tdccs

if TYPE_CHECKING:
    import psycopg

DRAFTED = "已建草稿"
PENDING = "待處理"
NOT_NEEDED = "不需寄送"
FAILED = "建立失敗"

SUBJECT_FORMAT = "TDCC : {tdcc} 中文TS/IIS"
BODY_INTRO = "FYI"
RISK_LEVEL = "RR4"  # 使用者確認：風險等級固定 RR4
NAVY = "#002060"
GREEN = "#00AB8E"
ORANGE = "#C55A11"  # 格式檔分公司表頭：佈景主題「輔色 2、較深 25%」
ROW_FILL = "#F0F0F0"
DATE_FORMAT = "%m-%d-%y"

LIST_COLUMNS = (("TDCC", 15), ("狀態", 10), ("收件人", 40), ("副本", 50), ("原因", 80))


class MailDraftError(Exception):
    """整批無法建立草稿（資料庫、信箱或路徑有問題）；訊息是給作業人員看的中文。"""


@dataclass(frozen=True)
class Draft:
    tdcc: str
    to: tuple[str, ...]  # 信箱
    cc: tuple[str, ...]
    subject: str
    html_body: str
    attachments: tuple[Path, ...]


Drafter = Callable[[Draft], None]


@dataclass(frozen=True)
class DraftOutcome:
    tdcc: str
    status: str
    to: tuple[str, ...] = ()  # 人名
    cc: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class DraftReceipt:
    list_path: Path
    outcomes: tuple[DraftOutcome, ...]
    last_import: dt.datetime | None
    unreadable_dirs: tuple[Path, ...] = ()


def create_drafts(
    text: str,
    root: Path,
    out_parent: Path,
    conn: psycopg.Connection,
    *,
    drafter: Drafter | None = None,
    now: dt.datetime | None = None,
) -> DraftReceipt:
    """依輸入順序處理每個 TDCC；回傳結果清單路徑、逐檔結果與最近一次每日匯入時間。

    drafter 預設用 Outlook 建草稿（只有真的要建時才會啟動 Outlook）。
    """
    tokens = parse_tdccs(text)
    if not tokens:
        raise MailDraftError("沒有輸入 TDCC")
    if not root.is_dir():
        raise MailDraftError(f"根目錄不存在：{root}")
    if not out_parent.is_dir():
        raise MailDraftError(f"結果清單的位置不存在：{out_parent}")
    try:
        require_current_schema(conn)
    except DatabaseError as exc:
        raise MailDraftError(str(exc)) from exc
    with conn.transaction():
        mailbox = load_mailbox(conn)
        last_import = conn.execute("SELECT max(imported_at) FROM workflow.import_run").fetchone()[0]
    fixed_cc = tuple(p for p in mailbox.people if p.fixed_cc)
    if not fixed_cc:
        raise MailDraftError("信箱沒有任何固定副本（mailbox.person 的 fixed_cc），請先設定")
    stamp = f"{(now or dt.datetime.now()):%Y%m%d-%H%M%S}"
    list_path = out_parent / f"寄信草稿清單_{stamp}.xlsx"
    if list_path.exists():
        raise MailDraftError(f"結果清單已存在：{list_path}")

    valid = {t for t in tokens if t.isdigit() and len(t) == 12}
    folders, unreadable = find_product_folders(root, valid)
    make_draft = drafter or OutlookDrafter()
    outcomes = []
    for tdcc in tokens:
        if tdcc not in valid:
            outcomes.append(DraftOutcome(tdcc, PENDING, reason="不是 12 碼數字"))
            continue
        outcomes.append(_draft_one(tdcc, conn, mailbox, fixed_cc, folders.get(tdcc, []), make_draft))
    _write_list(list_path, outcomes, last_import)
    return DraftReceipt(list_path, tuple(outcomes), last_import, tuple(unreadable))


def _draft_one(
    tdcc: str,
    conn: psycopg.Connection,
    mailbox: Mailbox,
    fixed_cc: Sequence[Any],
    folders: Sequence[Path],
    make_draft: Drafter,
) -> DraftOutcome:
    with conn.transaction():
        product = _product(conn, tdcc)
        branches = [b for (b,) in conn.execute(
            "SELECT branch FROM workflow.branch_allocation WHERE tdcc = %s ORDER BY seq", (tdcc,)
        )]  # fmt: skip
        fc_names = [n for (n,) in conn.execute(
            "SELECT fc_name FROM workflow.trade_row WHERE tdcc = %s ORDER BY row_seq", (tdcc,)
        )]  # fmt: skip
    problems = []
    if product is None:
        problems.append("資料庫沒有這檔的商品條件（每日匯入的清單更新還沒有這檔）")
    if not fc_names:
        problems.append("資料庫沒有這檔的交易列")
    names = list(dict.fromkeys(n for n in fc_names if n.strip().upper() != PB))
    if fc_names and not names:
        return DraftOutcome(tdcc, NOT_NEEDED, reason="只有 PB 承作，不需寄送")
    people = [mailbox.match(n) for n in names]
    unmatched = [n for n, p in zip(names, people, strict=True) if p is None]
    if unmatched:
        problems.append("名字對不到信箱：" + "、".join(unmatched))
    attachments, folder_problem = _attachments(tdcc, folders)
    if folder_problem:
        problems.append(folder_problem)
    if problems:
        return DraftOutcome(tdcc, PENDING, reason="；".join(problems))

    to = list(dict.fromkeys(p for p in people if p is not None))
    cc = [p for p in fixed_cc if p not in to]
    draft = Draft(
        tdcc=tdcc,
        to=tuple(p.email for p in to),
        cc=tuple(p.email for p in cc),
        subject=SUBJECT_FORMAT.format(tdcc=tdcc),
        html_body=render_body(tdcc, product, branches),
        attachments=attachments,
    )
    to_names, cc_names = tuple(p.name for p in to), tuple(p.name for p in cc)
    try:
        make_draft(draft)
    except Exception as exc:  # 一檔失敗不影響其他檔；原因寫進結果
        return DraftOutcome(tdcc, FAILED, to_names, cc_names, f"建立草稿失敗：{exc}")
    return DraftOutcome(tdcc, DRAFTED, to_names, cc_names)


_PRODUCT_FIELDS = (
    "trade_date", "issue_date", "final_valuation_date", "maturity_date", "initial_pricing", "product", "currency",
    "non_call_months", "underlying_1", "underlying_2", "underlying_3", "underlying_4", "underlying_5", "strike_pct",
    "ko_type", "ko_pct", "coupon_pct", "upfront_pct", "tenor_months", "barrier_type", "ki_pct",
)  # fmt: skip


def _product(conn: psycopg.Connection, tdcc: str) -> dict[str, Any] | None:
    row = conn.execute(f"SELECT {', '.join(_PRODUCT_FIELDS)} FROM workflow.product WHERE tdcc = %s", (tdcc,)).fetchone()
    return None if row is None else dict(zip(_PRODUCT_FIELDS, row, strict=True))


def _attachments(tdcc: str, folders: Sequence[Path]) -> tuple[tuple[Path, ...], str]:
    if not folders:
        return (), "根目錄底下找不到商品資料夾"
    if len(folders) > 1:
        return (), f"有 {len(folders)} 個同名商品資料夾：" + "、".join(map(str, folders))
    files = tuple(folders[0] / f"{tdcc}_{kind}.pdf" for kind in ("TS", "IIS"))
    missing = [f.name for f in files if not f.is_file()]
    if missing:
        return (), "商品資料夾沒有 " + "、".join(missing) + "（還沒核對通過或還沒放回）"
    return files, ""


# ---------- 信件內容 ----------


def render_body(tdcc: str, product: dict[str, Any], branches: Sequence[str]) -> str:
    """`FYI` 加三個表格（版面與配色依使用者提供的寄送格式）。"""
    p = product
    terms_1 = [
        ("TDCC", tdcc),
        ("Trade date", _date(p["trade_date"])),
        ("Issue date", _date(p["issue_date"])),
        ("Final valuation date", _date(p["final_valuation_date"])),
        ("Maturity date", _date(p["maturity_date"])),
        ("期初價格", p["initial_pricing"] or ""),
        ("風險等級", RISK_LEVEL),
    ]
    terms_2 = [
        ("Product", p["product"]),
        ("Currency", p["currency"]),
        ("Guaranteed Periods (m)", str(p["non_call_months"])),
        *((f"BBG Code {i}", p[f"underlying_{i}"] or "") for i in range(1, 6)),
        ("Strike (%)", _pct(p["strike_pct"])),
        ("KO Type", p["ko_type"]),
        ("KO Barrier (%)", _pct(p["ko_pct"])),
        ("Coupon p.a. (%)", _pct(p["coupon_pct"])),
        ("Upfront / NotePrice (%)", _pct(Decimal(100) - p["upfront_pct"])),
        ("Tenor (m)", str(p["tenor_months"])),
        ("Barrier Type", p["barrier_type"]),
        ("KI Barrier (%)", _pct(p["ki_pct"]) if p["ki_pct"] is not None else "-"),
    ]
    branch_cells = [(f"分公司 {i}", b) for i, b in enumerate(branches, 1)]
    parts = [f"<p>{BODY_INTRO}</p>", _table(terms_1, NAVY), _table(terms_2, GREEN)]
    if branch_cells:
        parts.append(_table(branch_cells, ORANGE))
    return (
        '<html><body style="font-family: Calibri, sans-serif; font-size: 11pt;">'
        + "<br>".join(parts)
        + "</body></html>"
    )


def _table(cells: Sequence[tuple[str, str]], header_color: str) -> str:
    cell = "border: 1px solid #000000; padding: 2px 6px; white-space: nowrap;"
    head = "".join(
        f'<th style="{cell} background: {header_color}; color: #FFFFFF; font-weight: bold;">{html.escape(k)}</th>'
        for k, _ in cells
    )
    body = "".join(f'<td style="{cell} background: {ROW_FILL};">{html.escape(v)}</td>' for _, v in cells)
    return f'<table style="border-collapse: collapse;"><tr>{head}</tr><tr>{body}</tr></table>'


def _date(value: dt.date) -> str:
    return value.strftime(DATE_FORMAT)


def _pct(value: Decimal) -> str:
    return f"{value:.2f}"


# ---------- Outlook ----------


OUTLOOK_FAILURE = "Outlook 無法建立郵件（可能還沒設定 Outlook 帳號、Outlook 啟動時跳出對話框，或附件打不開）"


class OutlookError(Exception):
    """Outlook 建草稿失敗；訊息是給作業人員看的中文，附上 COM 錯誤碼方便維護人員查。"""


class OutlookDrafter:
    """用傳統版 Outlook（COM）在執行者的預設信箱建草稿；只呼叫 Save()，不會寄出。"""

    def __call__(self, draft: Draft) -> None:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()  # 視窗在背景執行緒呼叫，COM 要在這個執行緒初始化
        try:
            mail = win32com.client.Dispatch("Outlook.Application").CreateItem(0)  # 0 = olMailItem
            mail.To = "; ".join(draft.to)
            mail.CC = "; ".join(draft.cc)
            mail.Subject = draft.subject
            mail.HTMLBody = draft.html_body
            for path in draft.attachments:
                mail.Attachments.Add(str(path))
            mail.Save()
        except pythoncom.com_error as exc:  # 原始訊息常是系統語系的亂碼，改成中文說明
            code = exc.args[0] if exc.args else ""
            hresult = f"0x{code & 0xFFFFFFFF:08X}" if isinstance(code, int) else str(code)
            raise OutlookError(f"{OUTLOOK_FAILURE}，錯誤碼 {hresult}") from exc
        finally:
            pythoncom.CoUninitialize()


# ---------- 結果清單 ----------


def _write_list(path: Path, outcomes: Sequence[DraftOutcome], last_import: dt.datetime | None) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "寄信草稿清單"
    ws.append([name for name, _ in LIST_COLUMNS])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for o in outcomes:
        ws.append([o.tdcc, o.status, "、".join(o.to) or None, "、".join(o.cc) or None, o.reason or None])
        ws.cell(ws.max_row, 1).number_format = "@"  # TDCC 是文字，保留前導零
    for i, (_, width) in enumerate(LIST_COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "A2"
    info = wb.create_sheet("資料來源")
    info.append(["最近一次每日匯入", f"{last_import:%Y-%m-%d %H:%M:%S}" if last_import else "沒有匯入紀錄"])
    info.column_dimensions["A"].width = 20
    info.column_dimensions["B"].width = 24
    wb.save(path)
