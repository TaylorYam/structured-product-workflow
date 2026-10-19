"""寄信草稿（#29）：資料庫用每日匯入寫入虛構資料，草稿用假的建立器，不碰真的信箱。資料全部虛構。"""

from __future__ import annotations

import datetime as dt
import os
import sys
import types
from pathlib import Path

import openpyxl
import pytest

from db_synth import inventory, seed_mailbox, trade, write_daily
from sp_workflow.daily_import import import_daily
from sp_workflow.mail_drafts import (
    DRAFTED,
    FAILED,
    NOT_NEEDED,
    OUTLOOK_FAILURE,
    PENDING,
    Draft,
    MailDraftError,
    OutlookDrafter,
    OutlookError,
    create_drafts,
)
from synth import snapshot

pytestmark = pytest.mark.db

A = "029199990001"
B = "037199990002"
NOW = dt.datetime(2026, 10, 20, 9, 0, 0)


class FakeDrafter:
    def __init__(self, fail: set[str] = frozenset()) -> None:
        self.drafts: list[Draft] = []
        self.fail = fail

    def __call__(self, draft: Draft) -> None:
        if draft.tdcc in self.fail:
            raise RuntimeError("Outlook 沒有回應")
        self.drafts.append(draft)


@pytest.fixture
def db(pg):
    seed_mailbox(pg)  # Amy、Ben 是 FC；Pat 是 PM、固定副本
    return pg


@pytest.fixture
def root(tmp_path: Path) -> Path:
    folder = tmp_path / "FCN"
    folder.mkdir()
    return folder


@pytest.fixture
def out(tmp_path: Path) -> Path:
    folder = tmp_path / "out"
    folder.mkdir()
    return folder


def load(db, tmp_path: Path, trades: list[dict], inventories: list[dict]) -> None:
    import_daily(write_daily(tmp_path / f"d{len(list(tmp_path.glob('d*.xlsx')))}.xlsx", trades, inventories), db)


def passed_folder(root: Path, tdcc: str, *, sub: str = "2026") -> Path:
    folder = root / sub / tdcc
    folder.mkdir(parents=True)
    (folder / f"{tdcc}_TS.pdf").write_bytes(b"%PDF ts")
    (folder / f"{tdcc}_IIS.pdf").write_bytes(b"%PDF iis")
    return folder


def run(db, root: Path, out: Path, text: str, drafter=None):
    drafter = drafter or FakeDrafter()
    return create_drafts(text, root, out, db, drafter=drafter, now=NOW), drafter


def test_ready_product_gets_one_draft_to_its_fcs_with_fixed_cc(db, tmp_path: Path, root: Path, out: Path) -> None:
    load(
        db,
        tmp_path,
        [trade(A, "Amy", "北區", 300000), trade(A, "Ben", "南區", 100000, number=None)],
        [inventory(A, [("北區", 300000), ("南區", 100000)])],
    )
    folder = passed_folder(root, A)

    receipt, drafter = run(db, root, out, A)

    assert [(o.tdcc, o.status, o.to, o.cc) for o in receipt.outcomes] == [(A, DRAFTED, ("Amy", "Ben"), ("Pat",))]
    (draft,) = drafter.drafts
    assert draft.to == ("amy@example.com", "ben@example.com") and draft.cc == ("pat@example.com",)
    assert draft.subject == f"FCN {A} 中文TS/IIS"
    assert draft.attachments == (folder / f"{A}_TS.pdf", folder / f"{A}_IIS.pdf")
    assert draft.html_body.count("<table") == 3 and "<p>FYI</p>" in draft.html_body


def test_body_shows_the_terms_in_the_mail_format(db, tmp_path: Path, root: Path, out: Path) -> None:
    load(db, tmp_path, [trade(A, "Amy", "北區", 1)], [inventory(A, [("北區", 1), ("南區", 2)])])
    passed_folder(root, A)

    (_, drafter) = run(db, root, out, A)

    body = drafter.drafts[0].html_body
    for expected in (
        ">10-15-26<",  # Trade date
        ">04-27-27<",  # Maturity date
        ">收盤價<",
        ">RR4<",
        ">AAA UW<",
        ">80.00<",  # Strike
        ">Daily Memory<",
        ">100.00<",  # KO Barrier
        ">12.50<",  # Coupon
        ">97.85<",  # NotePrice = 100 − UF 2.15
        ">EKI<",
        ">65.00<",  # KI Barrier
        ">分公司 2<",
        ">南區<",
        "#002060",
        "#00AB8E",
        'background: #C55A11; color: #FFFFFF; font-weight: bold;">分公司 1<',
    ):
        assert expected in body, expected


@pytest.mark.parametrize(
    ("text", "width"),
    [
        ("Guaranteed Periods (m)", 83),  # Guaranteed／Periods (m)：較長行 11 字
        ("Upfront / NotePrice (%)", 98),  # Upfront /／NotePrice (%)：13 字
        ("Final valuation date", 105),  # Final／valuation date：14 字
        ("Daily Memory", 75),  # 較長行只有 6 字，欄寬取 BBG Code 4 的 10 字
    ],
)
def test_wide_columns_wrap_into_at_most_two_lines(db, tmp_path: Path, root: Path, out: Path, text, width) -> None:
    load(db, tmp_path, [trade(A, "Amy", "北區", 1)], [inventory(A, [("北區", 1)])])
    passed_folder(root, A)

    (_, drafter) = run(db, root, out, A)

    assert (
        f' width="{width}" style="border: 1px solid #000000; padding: 2px 6px; width: {width}px; white-space: normal;'
        in (_cell_style(drafter.drafts[0].html_body, text))
    )


def test_columns_no_wider_than_bbg_code_4_stay_on_one_line(db, tmp_path: Path, root: Path, out: Path) -> None:
    load(db, tmp_path, [trade(A, "Amy", "北區", 1)], [inventory(A, [("北區", 1)])])
    passed_folder(root, A)

    (_, drafter) = run(db, root, out, A)

    for narrow in ("BBG Code 4", "Product", "期初價格", "分公司 1", "Strike (%)"):
        assert "white-space: nowrap;" in _cell_style(drafter.drafts[0].html_body, narrow), narrow


def _cell_style(body: str, text: str) -> str:
    """回傳內容是 text 的那一格（th 或 td）的開頭標籤。"""
    end = body.index(f">{text}<")
    return body[body.rindex("<t", 0, end) : end]


def test_no_ki_shows_none_and_a_dash(db, tmp_path: Path, root: Path, out: Path) -> None:
    load(db, tmp_path, [trade(A, "Amy", "北區", 1)], [inventory(A, [("北區", 1)], **{"KI(Freq)": "-", "KI(%)": "-"})])
    passed_folder(root, A)

    (_, drafter) = run(db, root, out, A)

    assert ">NONE<" in drafter.drafts[0].html_body and ">-<" in drafter.drafts[0].html_body


def test_pb_is_left_out_and_a_pb_only_product_needs_no_mail(db, tmp_path: Path, root: Path, out: Path) -> None:
    load(
        db,
        tmp_path,
        [trade(A, "Amy", "北區", 1), trade(A, "PB", "南區", 1, number=None), trade(B, "PB", "北區", 1)],
        [inventory(A, [("北區", 1), ("南區", 1)]), inventory(B, [("北區", 1)])],
    )
    passed_folder(root, A)
    passed_folder(root, B)

    receipt, drafter = run(db, root, out, f"{A}\n{B}")

    assert [(o.status, o.to) for o in receipt.outcomes] == [(DRAFTED, ("Amy",)), (NOT_NEEDED, ())]
    assert [d.tdcc for d in drafter.drafts] == [A]


def test_same_fc_twice_and_a_fixed_cc_who_is_a_recipient_appear_once(db, tmp_path: Path, root: Path, out: Path) -> None:
    load(
        db,
        tmp_path,
        [trade(A, "Amy", "北區", 1), trade(A, "amy", "南區", 1, number=None), trade(A, "Pat", "北區", 1, number=None)],
        [inventory(A, [("北區", 2), ("南區", 1)])],
    )
    passed_folder(root, A)

    (_, drafter) = run(db, root, out, A)

    assert drafter.drafts[0].to == ("amy@example.com", "pat@example.com") and drafter.drafts[0].cc == ()


def test_alias_reaches_the_right_person(pg, tmp_path: Path, root: Path, out: Path) -> None:
    seed_mailbox(pg, aliases=[("Amie", "Amy")])
    load(pg, tmp_path, [trade(A, "Amie", "北區", 1)], [inventory(A, [("北區", 1)])])
    passed_folder(root, A)

    (_, drafter) = run(pg, root, out, A)

    assert drafter.drafts[0].to == ("amy@example.com",)


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        ("no_ts", f"商品資料夾沒有 {A}_TS.pdf（還沒核對通過或還沒放回）"),
        ("pending_only", f"商品資料夾沒有 {A}_TS.pdf、{A}_IIS.pdf"),
        ("no_folder", "根目錄底下找不到商品資料夾"),
        ("two_folders", "有 2 個同名商品資料夾"),
    ],
)
def test_attachments_must_be_the_two_standard_files_in_one_folder(
    db, tmp_path: Path, root: Path, out: Path, setup: str, reason: str
) -> None:
    load(db, tmp_path, [trade(A, "Amy", "北區", 1)], [inventory(A, [("北區", 1)])])
    if setup == "no_ts":
        (passed_folder(root, A) / f"{A}_TS.pdf").unlink()
    elif setup == "pending_only":
        folder = root / A
        folder.mkdir()
        (folder / f"{A}_TS_待確認.pdf").write_bytes(b"x")
        (folder / f"{A}_IIS_待確認.pdf").write_bytes(b"x")
    elif setup == "two_folders":
        passed_folder(root, A, sub="a")
        passed_folder(root, A, sub="b")

    receipt, drafter = run(db, root, out, A)

    assert receipt.outcomes[0].status == PENDING and reason in receipt.outcomes[0].reason
    assert drafter.drafts == []


def test_missing_database_data_and_unmatched_names_are_all_listed(db, tmp_path: Path, root: Path, out: Path) -> None:
    load(db, tmp_path, [trade(A, "Zoe", "北區", 1)], [inventory(B, [("北區", 1)])])

    receipt, _ = run(db, root, out, f"{A} {B} 12345")

    reasons = {o.tdcc: o.reason for o in receipt.outcomes}
    assert all(o.status == PENDING for o in receipt.outcomes)
    assert reasons[A].startswith("資料庫沒有這檔的商品條件") and "名字對不到信箱：Zoe" in reasons[A]
    assert "資料庫沒有這檔的交易列" in reasons[B]
    assert reasons["12345"] == "不是 12 碼數字"


def test_one_failed_draft_does_not_stop_the_others(db, tmp_path: Path, root: Path, out: Path) -> None:
    load(
        db,
        tmp_path,
        [trade(A, "Amy", "北區", 1), trade(B, "Ben", "北區", 1)],
        [inventory(A, [("北區", 1)]), inventory(B, [("北區", 1)])],
    )
    passed_folder(root, A)
    passed_folder(root, B)

    receipt, drafter = run(db, root, out, f"{A},{B}", FakeDrafter(fail={A}))

    assert [(o.status, o.reason) for o in receipt.outcomes] == [
        (FAILED, "建立草稿失敗：Outlook 沒有回應"),
        (DRAFTED, ""),
    ]
    assert [d.tdcc for d in drafter.drafts] == [B]


def test_running_again_creates_another_draft(db, tmp_path: Path, root: Path, out: Path) -> None:
    load(db, tmp_path, [trade(A, "Amy", "北區", 1)], [inventory(A, [("北區", 1)])])
    passed_folder(root, A)
    drafter = FakeDrafter()

    create_drafts(A, root, out, db, drafter=drafter, now=NOW)
    create_drafts(A, root, out, db, drafter=drafter, now=NOW + dt.timedelta(seconds=1))

    assert len(drafter.drafts) == 2


def test_product_folders_are_read_only(db, tmp_path: Path, root: Path, out: Path) -> None:
    load(db, tmp_path, [trade(A, "Amy", "北區", 1)], [inventory(A, [("北區", 1)])])
    passed_folder(root, A)
    before = snapshot(root)

    run(db, root, out, A)

    assert snapshot(root) == before


def test_result_list_records_outcomes_and_last_import(db, tmp_path: Path, root: Path, out: Path) -> None:
    load(db, tmp_path, [trade(A, "Amy", "北區", 1)], [inventory(A, [("北區", 1)])])
    passed_folder(root, A)

    receipt, _ = run(db, root, out, f"{A} {B}")

    assert receipt.list_path == out / "寄信草稿清單_20261020-090000.xlsx" and receipt.last_import is not None
    wb = openpyxl.load_workbook(receipt.list_path)
    rows = list(wb["寄信草稿清單"].iter_rows(values_only=True))
    assert rows[0] == ("TDCC", "狀態", "收件人", "副本", "原因")
    assert rows[1][:4] == (A, DRAFTED, "Amy", "Pat") and rows[2][:2] == (B, PENDING)
    assert wb["資料來源"]["A1"].value == "最近一次每日匯入" and wb["資料來源"]["B1"].value != "沒有匯入紀錄"


def test_without_fixed_cc_nothing_is_drafted(pg, tmp_path: Path, root: Path, out: Path) -> None:
    seed_mailbox(pg, [("Amy", "amy@example.com", "FC", False)])

    with pytest.raises(MailDraftError, match="固定副本"):
        run(pg, root, out, A)

    assert list(out.iterdir()) == []


def test_bad_paths_and_empty_input_stop_before_anything(db, root: Path, out: Path, tmp_path: Path) -> None:
    with pytest.raises(MailDraftError, match="沒有輸入 TDCC"):
        run(db, root, out, " \n ")
    with pytest.raises(MailDraftError, match="根目錄不存在"):
        run(db, tmp_path / "none", out, A)
    with pytest.raises(MailDraftError, match="結果清單的位置不存在"):
        run(db, root, tmp_path / "none", A)


class FakeMail:
    """假的 Outlook 郵件：記下被設定的欄位；Send() 被呼叫就記下來，讓測試抓出來。"""

    def __init__(self) -> None:
        self.attached: list[str] = []
        self.Attachments = types.SimpleNamespace(Add=self.attached.append)
        self.saved = self.sent = False

    def Save(self) -> None:  # noqa: N802 — 對應 COM 方法名稱
        self.saved = True

    def Send(self) -> None:  # noqa: N802
        self.sent = True


@pytest.fixture
def fake_com(monkeypatch):
    """用假的 pythoncom／win32com 取代真的 Outlook；筆電與 CI 都能跑（#29）。"""

    class ComError(Exception):
        pass

    state = types.SimpleNamespace(mail=FakeMail(), error=None, com_depth=0, ComError=ComError)

    def dispatch(name: str):
        assert name == "Outlook.Application"
        if state.error is not None:
            raise state.error

        def create_item(kind: int) -> FakeMail:
            assert kind == 0  # olMailItem
            return state.mail

        return types.SimpleNamespace(CreateItem=create_item)

    pythoncom = types.ModuleType("pythoncom")
    pythoncom.com_error = ComError
    pythoncom.CoInitialize = lambda: setattr(state, "com_depth", state.com_depth + 1)
    pythoncom.CoUninitialize = lambda: setattr(state, "com_depth", state.com_depth - 1)
    client = types.ModuleType("win32com.client")
    client.Dispatch = dispatch
    package = types.ModuleType("win32com")
    package.client = client
    monkeypatch.setitem(sys.modules, "pythoncom", pythoncom)
    monkeypatch.setitem(sys.modules, "win32com", package)
    monkeypatch.setitem(sys.modules, "win32com.client", client)
    return state


def sample_draft(tmp_path: Path) -> Draft:
    return Draft(
        A,
        ("amy@example.com", "ben@example.com"),
        ("pat@example.com",),
        f"FCN {A} 中文TS/IIS",
        "<p>FYI</p>",
        (tmp_path / "a.pdf", tmp_path / "b.pdf"),
    )


def test_outlook_drafter_fills_the_mail_and_only_saves_it(fake_com, tmp_path: Path) -> None:
    OutlookDrafter()(sample_draft(tmp_path))

    mail = fake_com.mail
    assert (mail.To, mail.CC, mail.Subject, mail.HTMLBody) == (
        "amy@example.com; ben@example.com",
        "pat@example.com",
        f"FCN {A} 中文TS/IIS",
        "<p>FYI</p>",
    )
    assert mail.attached == [str(tmp_path / "a.pdf"), str(tmp_path / "b.pdf")]
    assert mail.saved and not mail.sent
    assert fake_com.com_depth == 0  # CoInitialize／CoUninitialize 成對


def test_outlook_errors_are_explained_in_chinese(fake_com, tmp_path: Path) -> None:
    fake_com.error = fake_com.ComError(-2147467260, "亂碼", None, None)

    with pytest.raises(OutlookError) as info:
        OutlookDrafter()(sample_draft(tmp_path))

    assert str(info.value) == f"{OUTLOOK_FAILURE}，錯誤碼 0x80004004"
    assert fake_com.com_depth == 0


def test_outlook_failure_marks_only_that_product(fake_com, db, tmp_path: Path, root: Path, out: Path) -> None:
    load(db, tmp_path, [trade(A, "Amy", "北區", 1)], [inventory(A, [("北區", 1)])])
    passed_folder(root, A)
    fake_com.error = fake_com.ComError(-2147467260, "亂碼", None, None)

    receipt = create_drafts(A, root, out, db, drafter=OutlookDrafter(), now=NOW)

    assert receipt.outcomes[0].status == FAILED and OUTLOOK_FAILURE in receipt.outcomes[0].reason


@pytest.mark.skipif(
    sys.platform != "win32" or not os.environ.get("SPW_OUTLOOK_TEST"),
    reason="真的 Outlook 測試只在本機設定 SPW_OUTLOOK_TEST 時執行",
)
def test_outlook_creates_a_draft_without_sending(tmp_path: Path) -> None:
    import win32com.client

    attachment = tmp_path / "test.pdf"
    attachment.write_bytes(b"%PDF test")
    subject = f"SPW 測試草稿 {dt.datetime.now():%Y%m%d%H%M%S}（可刪除）"

    OutlookDrafter()(Draft("000000000000", ("nobody@example.com",), (), subject, "<p>FYI</p>", (attachment,)))

    drafts = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI").GetDefaultFolder(16).Items  # 草稿匣
    found = [m for m in drafts if m.Subject == subject]
    try:
        assert len(found) == 1 and found[0].Attachments.Count == 1 and not found[0].Sent
    finally:
        for mail in found:
            mail.Delete()
