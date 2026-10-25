"""核對的資料庫來源（#68、ADR 0006、0008、0012、0013）。

核對規則照舊只讀參考條件表：資料庫來源依 PDF 檔名的 TDCC 從資料庫產生一份同格式的參考條件表（Excel 檔），
之後的配對、核對、人工放行、核對結果檔與回報包都和 Excel 來源相同。核對結果檔存好之後再寫回資料庫。對外：
- `export_reference`：產生參考條件表，回傳沒有列的 TDCC 與原因（資料庫沒有這檔、核對條件還沒完成）。
- `write_back`：保存每份文件最新的核對結果；說明書與投資人須知都通過或人工放行的商品，把回填值寫進商品資料並鎖住。
- `WorkflowDatabase`：核對分頁與 `fcn-batch --database` 用的資料庫來源（`fcn_checker.panel_workflow.DatabaseSource`）。

規則：
- 還沒鎖住的商品，核對條件（#88）要是「完成」才產生列；否則那一份不核對、需人工覆核、不能放行。
  已鎖住的商品照資料庫的值產生（TS、IIS 打勾，回填過的欄位有值），可以再核對，但不再回填。
  商品條件缺起單資料的必填欄（#114）時不看核對條件，一律不產生列（核對條件可能還沒重算）。
- 風險等級欄（#239）：自算風險等級（ADR 0023，`workflow.risk_level`）的 RR1～RR5（回填是 RR4）；待算寫「待算」，
  還沒算過的空白。核對規則拿它當 RR 核對的期望值（待算、空白轉人工覆核）。寫回前一樣比對，核對後重算改了等級也不回填
  （核對時的表沒有風險等級欄（v19 以前產生的）不算改了）。
- 回填：ISIN、發行日、比價日_1～12（空值寫法存成空白）、期初定價 VWAP 時各標的四個價格；人工放行的說明書
  若單位面額和資料庫不同，改成 TS 的值。每個改變的原值記在 `workflow.backfill_log`。寫入、紀錄與鎖住都經過
  `product_terms.backfill`／`lock_checked`（#203），和商品條件寫入共用同一把鎖（`product_terms.read(…, lock=True)`）。
- 寫回在同一個 transaction 裡，失敗時什麼都不寫；核對結果檔已經存好，可以重新寫回（使用者 2026-10-21 確認）。
- 信用評等（#240）：每列帶出交易日當時的發行機構、保證機構評等（三家評等機構各一欄，評等表 `credit_ratings` 依生效日查），
  核對規則從參考條件表讀，不直接讀資料庫（ADR 0013）。上手代號和風險等級同一個 `product_terms.issuer_code`
  （TDCC 前三碼，查不到再用發行機構寫法反查）。評等表沒有這個機構（或沒有該家評等）時那幾欄空白。
  評等在核對後改了也算條件改了（不回填、不鎖住），但只看有做評等核對的上手（`Issuer.ratings`）；
  核對時的參考條件表沒有評等欄（v18 以前產生的）不算改了。
傳入的 `conn` 由 `database.connect` 建立，資料表版本已確認（#200），這裡不再檢查。
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

from openpyxl.cell.cell import Cell

from fcn_checker.config import RISK_LEVEL_PENDING
from fcn_checker.rating_formats import AGENCY_KEYS, RATING_FIELDS
from sp_workflow.check_terms import DONE
from sp_workflow.credit_ratings import RatingRows, load_rows
from sp_workflow.database import ConnectionSettings, DatabaseError, SchemaOutdatedError, connect
from sp_workflow.file_tools import save_book, table_book
from sp_workflow.product_record import PRICE_COLUMNS, missing_fields, same_value
from sp_workflow.product_terms import Stored, backfill, lock_checked, locked, read, read_many
from sp_workflow.risk_level import PENDING, RiskLevel, risk_level_of, risk_levels

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
CHECKED_FIELDS = ("checked_term_sheet", "checked_iis")
RISK_FIELD = "risk_level"  # 參考條件表的風險等級（#239；不在匯入格式裡，排在最後）
DENOMINATION_RULE = "field.denomination"
DENOMINATION_COLUMN = "單位面額"


class CheckSourceError(Exception):
    """資料庫來源用不了（沒有登入、寫不出參考條件表）；訊息是給作業人員看的中文。"""


# ---------- 產生參考條件表 ----------


@dataclass(frozen=True)
class IssuerCodes:
    """找上手代號用的兩張對照（`product_terms.IssuerLookup`）：上手編號對照與參考條件表格式的發行機構寫法。"""

    issuer_prefixes: Mapping[str, str]  # TDCC 前三碼 → 上手代號
    issuer_names: Mapping[str, str]  # 上手代號 → 發行機構的寫法

    @classmethod
    def of(cls, prefixes: Mapping[str, str], fmt: ReferenceFormat) -> IssuerCodes:
        return cls(prefixes, fmt.issuer_values)

    def code(self, tdcc: str, issuer: str) -> str:
        from sp_workflow.product_terms import issuer_code  # product_terms 牽涉很多模組，用到才載入

        return issuer_code(tdcc, issuer, self)


def export_reference(
    conn: psycopg.Connection,
    tdccs: Sequence[str],
    fmt: ReferenceFormat,
    path: Path,
    *,
    prefixes: Mapping[str, str],
) -> dict[str, str]:
    """依 TDCC 從資料庫產生參考條件表到 `path`（每檔一列，欄位照匯入格式），回傳沒有列的 TDCC → 原因。

    `prefixes`：上手編號對照（TDCC 前三碼 → 上手代號），評等欄依上手代號查評等表。
    """
    codes = list(dict.fromkeys(tdccs))
    stored, risks = read_many(conn, codes), risk_levels(conn, codes)
    ratings = _Ratings(load_rows(conn), IssuerCodes.of(prefixes, fmt))
    headers, rows, absent = _headers(fmt), [], {}
    for tdcc in codes:
        found = stored.get(tdcc)
        reason = _absent_reason(found)
        if reason:
            absent[tdcc] = reason
        else:
            assert found is not None
            rows.append(_row(found, fmt, headers, ratings, risks.get(tdcc)))
    _write_sheet(path, headers, rows)
    return absent


def _absent_reason(found: Stored | None) -> str:
    """不能從資料庫核對的原因；空字串表示可以。"""
    if found is None:
        return "資料庫沒有這檔商品（還沒匯入起單資料），不核對"
    check, is_locked = found.check, locked(found)
    if is_locked and check is None:
        return "以前清單更新匯入、已鎖住的商品，資料庫沒有核對條件；請用 Excel 參考條件表核對"
    missing = missing_fields(found.columns)
    if missing:
        return f"商品條件缺 {'、'.join(missing)}；補齊後再核對"
    if is_locked:
        return ""
    if check is None:
        return "資料庫還沒算這檔的核對條件；請重新匯入起單資料或每日更新後再核對"
    if check.status != DONE:
        why = f"（{check.reason}）" if check.reason else ""
        return f"資料庫的核對條件還沒完成：{check.status}{why}；等資料齊了再核對"
    return ""


def _headers(fmt: ReferenceFormat) -> list[str]:
    """TS、IIS、發行機構，加上匯入格式的欄（「TS/IIS完成檢核」除外）、風險等級與信用評等欄：讀檔需要的欄都在，
    核對結果檔也帶得出其他欄。"""
    names = {std: header for header, std in reversed(list(fmt.columns.items()))}
    checked = [names[key] for key in CHECKED_FIELDS]
    return [
        *checked,
        fmt.issuer_column,
        *(c for c in fmt.import_format.columns if c != fmt.import_format.checked_column),
        names[RISK_FIELD],
        *(names[key] for key in RATING_FIELDS if key in names),
    ]


def _row(
    found: Stored, fmt: ReferenceFormat, headers: Sequence[str], ratings: _Ratings, risk: RiskLevel | None
) -> dict[str, Any]:
    """一檔商品 → {欄名: 值}。標準欄位依格式設定對到欄名；空值寫法用在 KI NONE、沒有的標的與不需要填的比價日。
    `risk` 是存好的風險等級（還沒算過是 None，風險等級欄空白）。"""
    p, empty = found.columns, fmt.empty_value
    no_ki = p["barrier_type"] == "NONE"
    prices = {price.seq: price for price in found.prices}
    values: dict[str, Any] = {
        "product_code": found.tdcc,
        "isin": found.isin,
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
        RISK_FIELD: _risk_cell(risk),
    }
    values.update(ratings.columns(found.tdcc, p["issuer"], p["trade_date"]))
    if locked(found):
        values.update(dict.fromkeys(CHECKED_FIELDS, fmt.checked_value))
    for slot in range(1, SLOTS + 1):
        if found.autocall is not None:
            values[f"autocall_date_{slot}"] = found.autocall.get(slot) or empty
    for i in range(1, UNDERLYINGS + 1):
        code = p[f"underlying_{i}"]
        values[f"underlying_{i}"] = code or empty
        price = prices.get(i)
        for col, column in PRICE_COLUMNS.items():
            key = f"underlying_{i}_{column}"
            if not code or (col == "ki" and no_ki):
                values[key] = empty
            else:
                values[key] = None if price is None else getattr(price, col)  # VWAP 還沒回填時空白，核對通過後回填
    extra: dict[str, Any] = {fmt.issuer_column: p["issuer"], "Product": p["product"], "UF": p["upfront_pct"]}
    extra["交易日期"] = p["trade_date"]
    for n, (branch, amount) in enumerate(found.branches[:BRANCHES], 1):
        extra[f"分公司_{n}"], extra[f"分公司_{n}_承作金額"] = branch, amount
    row = {}
    for header in headers:
        std = fmt.columns.get(header)
        row[header] = values.get(std) if std is not None else extra.get(header)
    return row


@dataclass(frozen=True)
class _Ratings:
    """評等表＋上手代號對照：產生參考條件表的評等欄。"""

    rows: RatingRows
    issuers: IssuerCodes

    def columns(self, tdcc: str, issuer: str, trade_date: dt.date | None) -> dict[str, Any]:
        """交易日當時的發行機構、保證機構評等（標準欄位 → 評等）；評等表沒有這個機構或沒有該家評等時空白（None）。"""
        code = self.issuers.code(tdcc, issuer)
        out: dict[str, Any] = dict.fromkeys(RATING_FIELDS)
        if not code or trade_date is None:
            return out
        for key, (role, agency) in RATING_FIELDS.items():
            rating = self.rows.on(code, role, trade_date)
            out[key] = None if rating is None else getattr(rating, AGENCY_KEYS[agency])
        return out

    def checked(self, tdcc: str, issuer: str) -> bool:
        """這家上手有做評等核對（`Issuer.ratings`）：評等欄改了才算條件改了。"""
        from fcn_checker.issuers import by_code

        found = by_code(self.issuers.code(tdcc, issuer))
        return found is not None and found.ratings


def _risk_cell(risk: RiskLevel | None) -> str | None:
    """風險等級欄的值：完成、回填寫等級，待算寫核對規則認得的待算寫法，還沒算過空白。"""
    if risk is None:
        return None
    if risk.status == PENDING:
        return RISK_LEVEL_PENDING
    return risk.level


def _write_sheet(path: Path, headers: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> None:
    """第 1 列表頭、第 2 列起每檔一列；日期用日期格式。寫到暫存檔再改名，失敗時不留半份。"""
    book = table_book(SHEET_TITLE, headers, ([row.get(h) for h in headers] for row in rows), style=_date_cell)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        save_book(book, path)
    except OSError as exc:
        raise CheckSourceError(f"資料庫參考條件表寫入失敗：{path}（{exc}）") from exc


def _date_cell(cell: Cell) -> None:
    if isinstance(cell.value, dt.date):
        cell.number_format = DATE_FORMAT


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
    prefixes: Mapping[str, str],
    user: str | None = None,
) -> WriteBackReceipt:
    """核對結果檔存好之後寫回資料庫；整次在同一個 transaction 裡，失敗時什麼都不寫。

    回填前確認資料庫現在的條件和核對時用的參考條件表（`batch.reference_sheet`）相同、核對條件仍是完成：
    預覽之後重新匯入起單資料或重算過的商品不回填、不鎖住（鎖住的一定是核對過的條件）。
    """
    from fcn_checker.orders.reference import load_reference_sheet

    checked_rows = {r.product_code.value: r for r in load_reference_sheet(batch.reference_sheet, fmt).rows}
    headers = _headers(fmt)
    user = user or getpass.getuser()
    result_file = str(receipt.output)
    codes = list(dict.fromkeys(i.product_code for i in batch.items if i.product_code))
    with conn.transaction():
        known = set(read_many(conn, codes))
        ratings = _Ratings(load_rows(conn), IssuerCodes.of(prefixes, fmt))
        saved = 0
        for item in batch.items:
            if item.product_code in known and item.kind is not None:
                filled = any(receipt.filled(i) for i in (item, item.partner) if i is not None)  # 這檔商品有回填
                _save_result(conn, item, filled, result_file, user)
                saved += 1
        done, already, stale, changes = [], [], [], 0
        for item in receipt.filled_items:
            tdcc = item.product_code
            if tdcc not in known:
                continue
            current = read(conn, tdcc, lock=True)  # 和商品條件寫入共用同一把鎖（#124）
            if locked(current):
                already.append(tdcc)
                continue
            rated = ratings.checked(tdcc, current.columns["issuer"])
            now = _row(current, fmt, headers, ratings, risk_level_of(conn, tdcc))
            if _absent_reason(current) or _changed(now, checked_rows.get(tdcc), fmt, rated=rated):
                stale.append(tdcc)
                continue
            for column, new in [*_backfill_values(item, fmt), *_released_denomination(item)]:
                changes += _write_value(conn, tdcc, column, new, fmt, result_file, user)
            lock_checked(conn, tdcc)
            done.append(tdcc)
    return WriteBackReceipt(tuple(done), tuple(already), saved, changes, tuple(stale))


def _changed(current: Mapping[str, Any], checked: Any, fmt: ReferenceFormat, *, rated: bool) -> bool:
    """資料庫現在的值和核對時那一列（`OrderRecord`）不同；回填欄位（ISIN、發行日、比價日、TS、IIS）不算。

    評等欄只在 `rated`（這家上手有做評等核對）時才比；核對時的表沒有評等欄（v18 以前產生的）也不算改了。
    風險等級欄同樣：核對時的表沒有這欄（v19 以前產生的）不算改了。
    """
    if checked is None:
        return True
    skip = {"isin", "issue_date", *CHECKED_FIELDS}
    for header, value in current.items():
        std = fmt.columns.get(header)
        if std is None or std in skip or std.startswith("autocall_date_"):
            continue
        if std in RATING_FIELDS and not (rated and checked.has(std)):
            continue
        if std == RISK_FIELD and not checked.has(std):
            continue
        found = checked.get(std)
        if not same_value(None if value == "" else value, None if found is None else found.value):
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
    """把一格寫進商品資料（`product_terms.backfill`，值有改變時記原值）；回傳改了幾個值（0 或 1）。"""
    std = "denomination" if column == DENOMINATION_COLUMN else fmt.columns.get(column)
    if std is None:
        return 0
    value = None if new == fmt.empty_value else new
    return int(backfill(conn, tdcc, std, value, item=column, result_file=result_file, user=user))


# ---------- 核對分頁與 fcn-batch 用的資料庫來源 ----------


class WorkflowDatabase:
    """`fcn_checker.panel_workflow.DatabaseSource` 的實作：連線設定由外殼先取得（視窗的資料庫登入、或 `.env`）。

    `settings` 回傳已取得的連線設定、不詢問，`export`、`write_back` 在背景連線時才呼叫；None 表示沒有登入。
    問密碼是畫面的事，由外殼在主執行緒先處理（#129）。產生的參考條件表放在 `folder`（每次一個新檔，留作紀錄）。
    """

    def __init__(
        self,
        settings: Callable[[], ConnectionSettings | None],
        folder: Path,
        *,
        user: str | None = None,
        on_failure: Callable[[], None] = lambda: None,
        issuer_prefixes: Path | None = None,
    ) -> None:
        self._current_settings = settings
        self.folder = folder
        self.user = user
        self.on_failure = on_failure  # 連不上資料庫時呼叫（例：視窗下次重新問密碼）
        self.issuer_prefixes = issuer_prefixes  # 上手編號對照設定檔；None 是預設位置（評等欄找上手代號，#240）

    def _prefixes(self) -> dict[str, str]:
        from fcn_checker.check_config import DEFAULTS
        from fcn_checker.config import load_issuer_prefixes

        return load_issuer_prefixes(self.issuer_prefixes or DEFAULTS.issuer_prefixes)

    def export(self, term_sheets: Sequence[Path], fmt: ReferenceFormat) -> tuple[Path, dict[str, str]]:
        import psycopg

        from fcn_checker.identification import file_code
        from fcn_checker.ingestion import IngestionError

        tdccs = [code for code in (file_code(Path(p)) for p in term_sheets) if code]
        path = self._new_path()
        prefixes = self._prefixes()  # 設定檔有問題：IngestionError，整批不核對
        try:
            with self._connect() as conn:
                absent = export_reference(conn, tdccs, fmt, path, prefixes=prefixes)
        except (CheckSourceError, DatabaseError, psycopg.Error) as exc:
            first = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            raise IngestionError("database_unavailable", f"從資料庫讀核對條件失敗：{first}") from exc
        return path, absent

    def write_back(self, batch: BatchOutcome, receipt: SaveReceipt) -> str:
        assert batch.reference_format is not None
        with self._connect() as conn:
            fmt = batch.reference_format
            return write_back(conn, batch, receipt, fmt, prefixes=self._prefixes(), user=self.user).summary

    def _connect(self) -> psycopg.Connection:
        """連上資料庫；連不上時呼叫 `on_failure`（可能是密碼錯）。資料表版本等其他錯誤不算登入問題。"""
        try:
            return connect(self._require())
        except SchemaOutdatedError:
            raise
        except DatabaseError:
            self.on_failure()
            raise

    def _require(self) -> ConnectionSettings:
        settings = self._current_settings()
        if settings is None:
            raise CheckSourceError("沒有登入資料庫")
        return settings

    def _new_path(self) -> Path:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        path = self.folder / f"參考條件表_資料庫_{stamp}.xlsx"
        n = 2
        while path.exists():
            path = self.folder / f"參考條件表_資料庫_{stamp}_{n}.xlsx"
            n += 1
        return path
