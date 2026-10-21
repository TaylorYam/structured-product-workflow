"""核對的資料庫來源（#68、ADR 0006、0008、0012、0013）。

核對規則照舊只讀參考條件表：資料庫來源依 PDF 檔名的 TDCC 從資料庫產生一份同格式的參考條件表（Excel 檔），
之後的配對、核對、人工放行、核對結果檔與回報包都和 Excel 來源相同。核對結果檔存好之後再寫回資料庫。對外：
- `export_reference`：產生參考條件表，回傳沒有列的 TDCC 與原因（資料庫沒有這檔、核對條件還沒完成）。
- `write_back`：保存每份文件最新的核對結果；說明書與投資人須知都通過或人工放行的商品，把回填值寫進商品資料並鎖住。
- `WorkflowDatabase`：核對分頁與 `fcn-batch --database` 用的資料庫來源（`fcn_checker.panel_workflow.DatabaseSource`）。

規則：
- 還沒鎖住的商品，核對條件（#88）要是「完成」才產生列；否則那一份不核對、需人工覆核、不能放行。
  已鎖住的商品照資料庫的值產生（TS、IIS 打勾，回填過的欄位有值），可以再核對，但不再回填。
- 回填：ISIN、發行日、比價日_1～12（空值寫法存成空白）、期初定價 VWAP 時各標的四個價格；人工放行的說明書
  若單位面額和資料庫不同，改成 TS 的值。每個改變的原值記在 `workflow.backfill_log`。
- 寫回在同一個 transaction 裡，失敗時什麼都不寫；核對結果檔已經存好，可以重新寫回（使用者 2026-10-21 確認）。
"""

from __future__ import annotations

import datetime as dt
import getpass
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import openpyxl
from openpyxl.styles import Font

from sp_workflow.check_terms import DONE
from sp_workflow.database import ConnectionSettings, DatabaseError, connect, require_current_schema

if TYPE_CHECKING:
    import psycopg

    from fcn_checker.batch import BatchItem, BatchOutcome
    from fcn_checker.config import ReferenceFormat
    from fcn_checker.saving import SaveReceipt

EXPORT_FOLDER = "資料庫參考條件表"  # 安裝資料夾 runtime/ 底下，放每次產生的參考條件表
SHEET_TITLE = "資料庫"
DATE_FORMAT = "yyyy/m/d"
SLOTS = 12
UNDERLYINGS = 5
BRANCHES = 7  # 匯入格式的分公司欄數
PRICE_COLUMNS = {"initial": "initial_price", "strike": "strike_price", "ki": "ki_price", "ko": "ko_price"}
CHECKED_FIELDS = ("checked_term_sheet", "checked_iis")
DENOMINATION_RULE = "field.denomination"
DENOMINATION_COLUMN = "單位面額"

_PRODUCT_COLUMNS = (
    "tdcc", "product", "issuer", "isin", "currency", "trade_date", "issue_date", "final_valuation_date",
    "maturity_date", "initial_pricing", "non_call_months", "underlying_1", "underlying_2", "underlying_3",
    "underlying_4", "underlying_5", "strike_pct", "ko_freq", "ko_memory", "ko_pct", "coupon_pct", "upfront_pct",
    "tenor_months", "ki_freq", "barrier_type", "ki_pct", "denomination", "locked_at",
)  # fmt: skip


class CheckSourceError(Exception):
    """資料庫來源用不了（沒有登入、資料表不是最新版本）；訊息是給作業人員看的中文。"""


# ---------- 產生參考條件表 ----------


@dataclass(frozen=True)
class _Stored:
    """一檔商品在資料庫裡的資料。"""

    product: dict[str, Any]
    status: str | None  # 核對條件的狀態（#88）；沒算過時 None
    reason: str
    prices: dict[int, dict[str, Any]]  # 第幾檔 → {code, initial_price, …}
    branches: list[tuple[str, Decimal]]
    autocall: dict[int, dt.date | None] | None  # 回填過的比價日；沒回填過時 None


def export_reference(
    conn: psycopg.Connection, tdccs: Sequence[str], fmt: ReferenceFormat, path: Path
) -> dict[str, str]:
    """依 TDCC 從資料庫產生參考條件表到 `path`（每檔一列，欄位照匯入格式），回傳沒有列的 TDCC → 原因。"""
    try:
        require_current_schema(conn)
    except DatabaseError as exc:
        raise CheckSourceError(str(exc)) from exc
    codes = list(dict.fromkeys(tdccs))
    with conn.transaction():
        stored = _load(conn, codes)
    headers, rows, absent = _headers(fmt), [], {}
    for tdcc in codes:
        found = stored.get(tdcc)
        reason = _absent_reason(found)
        if reason:
            absent[tdcc] = reason
        else:
            assert found is not None
            rows.append(_row(found, fmt, headers))
    _write_sheet(path, headers, rows)
    return absent


def _absent_reason(found: _Stored | None) -> str:
    """不能從資料庫核對的原因；空字串表示可以。"""
    if found is None:
        return "資料庫沒有這檔商品（還沒匯入起單資料），不核對"
    locked = found.product["locked_at"] is not None
    if locked and found.status is None:
        return "以前清單更新匯入、已鎖住的商品，資料庫沒有核對條件；請用 Excel 參考條件表核對"
    if locked:
        return ""
    if found.status is None:
        return "資料庫還沒算這檔的核對條件；請重新匯入起單資料或每日更新後再核對"
    if found.status != DONE:
        why = f"（{found.reason}）" if found.reason else ""
        return f"資料庫的核對條件還沒完成：{found.status}{why}；等資料齊了再核對"
    return ""


def _load(conn: psycopg.Connection, tdccs: list[str]) -> dict[str, _Stored]:
    products = {
        row[0]: dict(zip(_PRODUCT_COLUMNS, row, strict=True))
        for row in conn.execute(
            f"SELECT {', '.join(_PRODUCT_COLUMNS)} FROM workflow.product WHERE tdcc = ANY(%s)", (tdccs,)
        )
    }
    terms = {
        tdcc: (status, reason or "")
        for tdcc, status, reason in conn.execute(
            "SELECT tdcc, status, reason FROM workflow.check_terms WHERE tdcc = ANY(%s)", (tdccs,)
        )
    }
    prices: dict[str, dict[int, dict[str, Any]]] = {}
    for tdcc, seq, code, *values in conn.execute(
        "SELECT tdcc, seq, code, initial_price, strike_price, ki_price, ko_price FROM workflow.underlying_price"
        " WHERE tdcc = ANY(%s)",
        (tdccs,),
    ):
        prices.setdefault(tdcc, {})[seq] = {"code": code, **dict(zip(PRICE_COLUMNS.values(), values, strict=True))}
    branches: dict[str, list[tuple[str, Decimal]]] = {}
    for tdcc, branch, amount in conn.execute(
        "SELECT tdcc, branch, amount FROM workflow.branch_allocation WHERE tdcc = ANY(%s) ORDER BY tdcc, seq",
        (tdccs,),
    ):
        branches.setdefault(tdcc, []).append((branch, amount))
    autocall: dict[str, dict[int, dt.date | None]] = {}
    for tdcc, slot, value in conn.execute(
        "SELECT tdcc, slot, value FROM workflow.autocall_date WHERE tdcc = ANY(%s)", (tdccs,)
    ):
        autocall.setdefault(tdcc, {})[slot] = value
    return {
        tdcc: _Stored(
            product=product,
            status=terms[tdcc][0] if tdcc in terms else None,
            reason=terms[tdcc][1] if tdcc in terms else "",
            prices=prices.get(tdcc, {}),
            branches=branches.get(tdcc, []),
            autocall=autocall.get(tdcc),
        )
        for tdcc, product in products.items()
    }


def _headers(fmt: ReferenceFormat) -> list[str]:
    """TS、IIS、發行機構，加上匯入格式的欄（「TS/IIS完成檢核」除外）：讀檔需要的欄都在，核對結果檔也帶得出其他欄。"""
    names = {std: header for header, std in reversed(list(fmt.columns.items()))}
    checked = [names[key] for key in CHECKED_FIELDS]
    return [
        *checked,
        fmt.issuer_column,
        *(c for c in fmt.import_format.columns if c != fmt.import_format.checked_column),
    ]


def _row(found: _Stored, fmt: ReferenceFormat, headers: Sequence[str]) -> dict[str, Any]:
    """一檔商品 → {欄名: 值}。標準欄位依格式設定對到欄名；空值寫法用在 KI NONE、沒有的標的與不需要填的比價日。"""
    p, empty = found.product, fmt.empty_value
    no_ki = p["barrier_type"] == "NONE"
    values: dict[str, Any] = {
        "product_code": p["tdcc"],
        "isin": p["isin"],
        "denomination": p["denomination"],
        "currency": p["currency"],
        "trade_date": p["trade_date"],
        "issue_date": p["issue_date"],
        "final_valuation_date": p["final_valuation_date"],
        "maturity_date": p["maturity_date"],
        "initial_pricing": p["initial_pricing"],
        "ko_pct": p["ko_pct"],
        "ko_observation": p["ko_freq"],
        "ko_memory": p["ko_memory"],
        "strike_pct": p["strike_pct"],
        "ki_pct": empty if no_ki else p["ki_pct"],
        "ki_type": p["ki_freq"],
        "coupon_pa_pct": p["coupon_pct"],
        "tenor_months": p["tenor_months"],
        "first_callable_period": p["non_call_months"],
    }
    if p["locked_at"] is not None:
        values.update(dict.fromkeys(CHECKED_FIELDS, fmt.checked_value))
    for slot in range(1, SLOTS + 1):
        if found.autocall is not None:
            values[f"autocall_date_{slot}"] = found.autocall.get(slot) or empty
    for i in range(1, UNDERLYINGS + 1):
        code = p[f"underlying_{i}"]
        values[f"underlying_{i}"] = code or empty
        stored = found.prices.get(i, {})
        for col, column in PRICE_COLUMNS.items():
            key = f"underlying_{i}_{column}"
            if not code or (col == "ki" and no_ki):
                values[key] = empty
            else:
                values[key] = stored.get(column)  # VWAP 還沒回填時空白，核對通過後回填
    extra: dict[str, Any] = {fmt.issuer_column: p["issuer"], "Product": p["product"], "UF": p["upfront_pct"]}
    extra["交易日期"] = p["trade_date"]
    for n, (branch, amount) in enumerate(found.branches[:BRANCHES], 1):
        extra[f"分公司_{n}"], extra[f"分公司_{n}_承作金額"] = branch, amount
    row = {}
    for header in headers:
        std = fmt.columns.get(header)
        row[header] = values.get(std) if std is not None else extra.get(header)
    return row


def _write_sheet(path: Path, headers: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> None:
    """第 1 列表頭、第 2 列起每檔一列；日期用日期格式。寫到暫存檔再改名，失敗時不留半份。"""
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = SHEET_TITLE
    sheet.append(list(headers))
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in rows:
        sheet.append([row.get(h) for h in headers])
        for cell in sheet[sheet.max_row]:
            if isinstance(cell.value, dt.date):
                cell.number_format = DATE_FORMAT
    sheet.freeze_panes = "A2"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    try:
        workbook.save(temp)
        temp.replace(path)
    except OSError as exc:
        temp.unlink(missing_ok=True)
        raise CheckSourceError(f"資料庫參考條件表寫入失敗：{path}（{exc}）") from exc


# ---------- 寫回 ----------


@dataclass(frozen=True)
class WriteBackReceipt:
    locked: tuple[str, ...]  # 這次回填並鎖住的 TDCC
    already_locked: tuple[str, ...]  # 已鎖住、沒有再回填的 TDCC
    results: int  # 保存了幾份核對結果
    changes: int  # 商品資料改了幾個值
    stale: tuple[str, ...] = ()  # 核對後資料庫的條件改了（或核對條件不再是完成）、沒有回填也沒有鎖住的 TDCC

    @property
    def summary(self) -> str:
        text = (
            f"已寫回資料庫：{len(self.locked)} 檔回填並鎖住（改了 {self.changes} 個值），保存 {self.results} 份核對結果"
        )
        if self.already_locked:
            text += f"；{'、'.join(self.already_locked)} 已鎖住，沒有再回填"
        if self.stale:
            text += f"；{'、'.join(self.stale)} 的條件在核對後改了，沒有回填也沒有鎖住，請重新載入預覽再核對"
        return text + "。"


def write_back(
    conn: psycopg.Connection,
    batch: BatchOutcome,
    receipt: SaveReceipt,
    fmt: ReferenceFormat,
    *,
    user: str | None = None,
) -> WriteBackReceipt:
    """核對結果檔存好之後寫回資料庫；整次在同一個 transaction 裡，失敗時什麼都不寫。

    回填前確認資料庫現在的條件和核對時用的參考條件表（`batch.reference_sheet`）相同、核對條件仍是完成：
    預覽之後重新匯入起單資料或重算過的商品不回填、不鎖住（鎖住的一定是核對過的條件）。
    """
    from fcn_checker.orders.reference import load_reference_sheet

    require_current_schema(conn)
    checked_rows = {r.product_code.value: r for r in load_reference_sheet(batch.reference_sheet, fmt).rows}
    headers = _headers(fmt)
    user = user or getpass.getuser()
    result_file = str(receipt.output)
    codes = list(dict.fromkeys(i.product_code for i in batch.items if i.product_code))
    with conn.transaction():
        known = {t for (t,) in conn.execute("SELECT tdcc FROM workflow.product WHERE tdcc = ANY(%s)", (codes,))}
        saved = 0
        for item in batch.items:
            if item.product_code in known and item.kind is not None:
                filled = any(receipt.filled(i) for i in (item, item.partner) if i is not None)  # 這檔商品有回填
                _save_result(conn, item, filled, result_file, user)
                saved += 1
        locked, already, stale, changes = [], [], [], 0
        for item in receipt.filled_items:
            tdcc = item.product_code
            if tdcc not in known:
                continue
            (locked_at,) = conn.execute(
                "SELECT locked_at FROM workflow.product WHERE tdcc = %s FOR UPDATE", (tdcc,)
            ).fetchone()
            if locked_at is not None:
                already.append(tdcc)
                continue
            current = _load(conn, [tdcc]).get(tdcc)
            if _absent_reason(current) or _changed(_row(current, fmt, headers), checked_rows.get(tdcc), fmt):
                stale.append(tdcc)
                continue
            for column, new in [*_backfill_values(item, fmt), *_released_denomination(item)]:
                changes += _write_value(conn, tdcc, column, new, fmt, result_file, user)
            conn.execute("UPDATE workflow.product SET locked_at = now() WHERE tdcc = %s", (tdcc,))
            locked.append(tdcc)
    return WriteBackReceipt(tuple(locked), tuple(already), saved, changes, tuple(stale))


def _changed(current: Mapping[str, Any], checked: Any, fmt: ReferenceFormat) -> bool:
    """資料庫現在的值和核對時那一列（`OrderRecord`）不同；回填欄位（ISIN、發行日、比價日、TS、IIS）不算。"""
    if checked is None:
        return True
    skip = {"isin", "issue_date", *CHECKED_FIELDS}
    for header, value in current.items():
        std = fmt.columns.get(header)
        if std is None or std in skip or std.startswith("autocall_date_"):
            continue
        found = checked.get(std)
        if not _same(None if value == "" else value, None if found is None else found.value):
            return True
    return False


def _save_result(conn: psycopg.Connection, item: BatchItem, filled: bool, result_file: str, user: str) -> None:
    conn.execute(
        "INSERT INTO workflow.check_result (tdcc, kind, file_name, category, status, released, filled, problems,"
        " result_file, checked_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)"
        " ON CONFLICT (tdcc, kind) DO UPDATE SET file_name = EXCLUDED.file_name, category = EXCLUDED.category,"
        " status = EXCLUDED.status, released = EXCLUDED.released, filled = EXCLUDED.filled,"
        " problems = EXCLUDED.problems, result_file = EXCLUDED.result_file, checked_by = EXCLUDED.checked_by,"
        " checked_at = now()",
        (
            item.product_code,
            item.kind.value,
            item.term_sheet.name,
            item.category.value,
            item.report.status.value,
            item.released,
            filled,
            json.dumps(list(item.problem_messages), ensure_ascii=False) if item.problem_messages else None,
            result_file,
            user,
        ),
    )


def _backfill_values(item: BatchItem, fmt: ReferenceFormat) -> list[tuple[str, Any]]:
    """說明書這次要寫進「回填後」的格子（回填與 VWAP 覆寫）：(欄名, 值)；TS、IIS 打勾不算（改成鎖住）。"""
    from fcn_checker.backfill import WRITE_ACTIONS

    return [
        (d.column, d.expected)
        for d in item.report.backfill
        if d.action in WRITE_ACTIONS and fmt.columns.get(d.column) not in CHECKED_FIELDS
    ]


def _released_denomination(item: BatchItem) -> list[tuple[str, Any]]:
    """人工放行的說明書，單位面額和資料庫不同時寫入 TS 的值（ADR 0008）。"""
    from fcn_checker.schema import CheckStatus

    if not item.released:
        return []
    found = {
        r.actual
        for r in item.report.results
        if r.rule_id == DENOMINATION_RULE and r.status != CheckStatus.PASS and r.actual is not None
    }
    if len(found) != 1:
        return []
    (value,) = found
    try:
        return [(DENOMINATION_COLUMN, Decimal(str(value)))]
    except ArithmeticError:
        return []


def _write_value(
    conn: psycopg.Connection, tdcc: str, column: str, new: Any, fmt: ReferenceFormat, result_file: str, user: str
) -> int:
    """把一格寫進商品資料；值有改變時記原值，回傳改了幾個值（0 或 1）。"""
    std = "denomination" if column == DENOMINATION_COLUMN else fmt.columns.get(column)
    if std is None:
        return 0
    value = None if new == fmt.empty_value else new
    if std in ("isin", "issue_date", "denomination"):
        (old,) = conn.execute(f"SELECT {std} FROM workflow.product WHERE tdcc = %s", (tdcc,)).fetchone()
        conn.execute(f"UPDATE workflow.product SET {std} = %s WHERE tdcc = %s", (value, tdcc))
    elif std.startswith("autocall_date_"):
        slot = int(std.rsplit("_", 1)[1])
        found = conn.execute(
            "SELECT value FROM workflow.autocall_date WHERE tdcc = %s AND slot = %s", (tdcc, slot)
        ).fetchone()
        old = None if found is None else found[0]
        conn.execute(
            "INSERT INTO workflow.autocall_date (tdcc, slot, value) VALUES (%s, %s, %s)"
            " ON CONFLICT (tdcc, slot) DO UPDATE SET value = EXCLUDED.value",
            (tdcc, slot, value),
        )
        if found is None and value is None:
            return 0  # 不需要填的期別：記下來就好，不算改值
    elif std.startswith("underlying_") and std.endswith("_price"):
        _, i, *rest = std.split("_")
        price = "_".join(rest)
        if price not in PRICE_COLUMNS.values():  # 欄名放進 SQL 前只接受這四個
            return 0
        found = conn.execute(
            f"SELECT {price} FROM workflow.underlying_price WHERE tdcc = %s AND seq = %s", (tdcc, int(i))
        ).fetchone()
        if found is None:
            return 0  # 沒有這個標的（空值寫法的格子）
        old = found[0]
        conn.execute(
            f"UPDATE workflow.underlying_price SET {price} = %s WHERE tdcc = %s AND seq = %s", (value, tdcc, int(i))
        )
    else:
        return 0
    if _same(old, value):
        return 0
    conn.execute(
        "INSERT INTO workflow.backfill_log (tdcc, item, old_value, new_value, result_file, written_by)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (tdcc, column, _text(old), _text(value), result_file, user),
    )
    return 1


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, Decimal | int) and isinstance(b, Decimal | int):
        return Decimal(a) == Decimal(b)
    return a == b


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dt.date):
        return value.isoformat()
    return str(value)


# ---------- 核對分頁與 fcn-batch 用的資料庫來源 ----------


class WorkflowDatabase:
    """`fcn_checker.panel_workflow.DatabaseSource` 的實作：連線設定由呼叫端給（視窗的資料庫登入、或 `.env`）。

    `ready` 在主執行緒取得連線設定（需要時問密碼）；`export`、`write_back` 可以在背景執行。
    產生的參考條件表放在 `folder`（每次一個新檔，留作紀錄）。
    """

    def __init__(
        self,
        settings: Callable[[], ConnectionSettings | None],
        folder: Path,
        *,
        user: str | None = None,
        on_failure: Callable[[], None] = lambda: None,
    ) -> None:
        self._ask = settings
        self.folder = folder
        self.user = user
        self.on_failure = on_failure  # 連不上資料庫時呼叫（例：視窗下次重新問密碼）
        self._settings: ConnectionSettings | None = None

    def ready(self) -> bool:
        self._settings = self._ask()
        return self._settings is not None

    def export(self, term_sheets: Sequence[Path], fmt: ReferenceFormat) -> tuple[Path, dict[str, str]]:
        import psycopg

        from fcn_checker.identification import file_code
        from fcn_checker.ingestion import IngestionError

        tdccs = [code for code in (file_code(Path(p)) for p in term_sheets) if code]
        path = self._new_path()
        try:
            with self._connect() as conn:
                absent = export_reference(conn, tdccs, fmt, path)
        except (CheckSourceError, DatabaseError, psycopg.Error) as exc:
            first = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            raise IngestionError("database_unavailable", f"從資料庫讀核對條件失敗：{first}") from exc
        return path, absent

    def write_back(self, batch: BatchOutcome, receipt: SaveReceipt) -> str:
        assert batch.reference_format is not None
        with self._connect() as conn:
            return write_back(conn, batch, receipt, batch.reference_format, user=self.user).summary

    def _connect(self) -> psycopg.Connection:
        """連上資料庫；連不上時呼叫 `on_failure`（可能是密碼錯）。資料表版本等其他錯誤不算登入問題。"""
        try:
            return connect(self._require())
        except DatabaseError:
            self.on_failure()
            raise

    def _require(self) -> ConnectionSettings:
        if self._settings is None:
            raise CheckSourceError("沒有登入資料庫")
        return self._settings

    def _new_path(self) -> Path:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        path = self.folder / f"參考條件表_資料庫_{stamp}.xlsx"
        n = 2
        while path.exists():
            path = self.folder / f"參考條件表_資料庫_{stamp}_{n}.xlsx"
            n += 1
        return path
