"""核對的資料庫來源（#68、ADR 0006、0008、0012、0013）。

核對規則照舊只讀參考條件表的列：資料庫來源依 PDF 檔名的 TDCC 從資料庫排出一張同格式的表（`Table`，不寫檔，#445），
讀成和 Excel 參考條件表一樣的列（`fcn_checker.condition_source.ConditionSource`），之後的配對、核對、人工放行、
核對結果檔與回報包都和 Excel 來源相同。儲存確認來源沒變之後再寫回資料庫（核對結果檔寫不出去也照樣寫回，#443）。對外：
- `conditions`：排出核對條件，連同沒有列的 TDCC 與原因（資料庫沒有這檔、核對條件還沒完成）。
- `write_back`：保存每份文件最新的核對結果；說明書與投資人須知都通過或人工放行的商品，把回填值寫進商品資料並鎖住。
- `WorkflowDatabase`：核對分頁與 `fcn-batch --database` 用的資料庫來源（`fcn_checker.panel_workflow.DatabaseSource`）。

規則：
- 還沒鎖住的商品，核對條件（#88）要是「完成」才產生列；否則那一份不核對、需人工覆核、不能放行。
  已鎖住的商品照資料庫的值產生（TS、IIS 打勾，回填過的欄位有值），可以再核對，但不再回填。
  商品條件缺起單資料的必填欄（#114）時不看核對條件，一律不產生列（核對條件可能還沒重算）。
- 風險等級欄（#239）：自算風險等級（ADR 0023，`workflow.risk_level`）的 RR1～RR5（回填是 RR4）；待算寫「待算」，
  還沒算過的空白。核對規則拿它當 RR 核對的期望值（待算、空白轉人工覆核）。寫回前一樣比對，核對後重算改了等級也不回填。
- 參與率欄（#373）：`workflow.product.participation_pct`，只有 BEN 有值。
- 配息門檻欄（#367）：DACN 的配息門檻，其他商品寫空值寫法。
- 回顧結束日欄（#376）：`workflow.product.lookback_end_date`，只有 lookback 的 BEN 有值，其他空白；是回填欄位。
- 回填：ISIN、發行日、比價日_1～12（空值寫法存成空白）、回顧結束日（已算出回顧期初價格的不改）、
  期初定價 VWAP 時各標的四個價格；人工放行的說明書
  若單位面額和資料庫不同，改成 TS 的值。每個改變的原值記在 `workflow.backfill_log`。寫入、紀錄與鎖住都經過
  `product_terms.backfill`／`lock_checked`（#203），和商品條件寫入共用同一把鎖（`product_store.read(…, lock=True)`）。
- 寫回在同一個 transaction 裡，失敗時什麼都不寫；核對結果檔已經存好，可以重新寫回（使用者 2026-10-21 確認）。
- 信用評等（#240）：每列帶出交易日當時的發行機構、保證機構評等（三家評等機構各一欄，評等表 `credit_ratings` 依生效日查），
  核對規則從參考條件表讀，不直接讀資料庫（ADR 0013）。上手代號和風險等級同一個 `product_store.issuer_code`
  （TDCC 前三碼，查不到再用發行機構寫法反查）。評等表沒有這個機構（或沒有該家評等）時那幾欄空白。
  評等在核對後改了也算條件改了（不回填、不鎖住），但只看有做評等核對的上手（`Issuer.ratings`）。
傳入的 `conn` 由 `database.connect` 建立，資料表版本已確認（#200），這裡不再檢查。
"""

from __future__ import annotations

import datetime as dt
import getpass
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fcn_checker.backfill_values import CHECKED_MARKS, ROW_FIELDS
from fcn_checker.condition_source import ConditionSource
from fcn_checker.config import RISK_LEVEL_PENDING
from fcn_checker.orders.reference import OrderRecord, Table
from fcn_checker.rating_formats import AGENCY_KEYS, RATING_FIELDS
from sp_workflow.check_terms import DONE
from sp_workflow.credit_ratings import RatingRows, load_rows
from sp_workflow.database import ConnectionSettings, DatabaseError, SchemaOutdatedError, connect
from sp_workflow.product_record import missing_fields, same_value
from sp_workflow.product_store import Stored, issuer_code, locked, read, read_many
from sp_workflow.product_terms import backfill, lock_checked
from sp_workflow.reference_values import sheet_values
from sp_workflow.risk_level import PENDING, RiskLevel, risk_level_of, risk_levels

if TYPE_CHECKING:
    import psycopg

    from fcn_checker.batch import BatchItem, BatchOutcome
    from fcn_checker.config import ReferenceFormat
    from fcn_checker.issuers import Issuer
    from fcn_checker.saving import SaveReceipt

CHECKED_FIELDS = tuple(m.field for m in CHECKED_MARKS)  # TS、IIS 欄（回填欄位宣告在 backfill_values）
RISK_FIELD = "risk_level"  # 參考條件表的風險等級（#239；不在匯入格式裡；欄序依設定檔，#445）


class CheckSourceError(Exception):
    """資料庫來源用不了（沒有登入、核對的不是資料庫來源）；訊息是給作業人員看的中文。"""


# ---------- 排出核對條件 ----------


@dataclass(frozen=True)
class IssuerCodes:
    """找上手代號用的兩張對照（`product_store.IssuerLookup`）：上手編號對照與參考條件表格式的發行機構寫法。"""

    issuer_prefixes: Mapping[str, str]  # TDCC 前三碼 → 上手代號
    issuer_names: Mapping[str, str]  # 上手代號 → 發行機構的寫法

    @classmethod
    def of(cls, prefixes: Mapping[str, str], fmt: ReferenceFormat) -> IssuerCodes:
        return cls(prefixes, fmt.issuer_values)

    def code(self, tdcc: str, issuer: str) -> str:
        return issuer_code(tdcc, issuer, self)


def conditions(
    conn: psycopg.Connection,
    tdccs: Sequence[str],
    fmt: ReferenceFormat,
    *,
    prefixes: Mapping[str, str],
) -> ConditionSource:
    """依 TDCC 從資料庫排出核對條件（每檔一列，欄位照匯入格式；不寫檔，#445），連同沒有列的 TDCC → 原因。

    `prefixes`：上手編號對照（TDCC 前三碼 → 上手代號），評等欄依上手代號查評等表。
    """
    codes = list(dict.fromkeys(tdccs))
    stored, risks = read_many(conn, codes), risk_levels(conn, codes)
    ratings = _Ratings(load_rows(conn), IssuerCodes.of(prefixes, fmt))
    rows, absent = [], {}
    for tdcc in codes:
        found = stored.get(tdcc)
        reason = _absent_reason(found)
        if reason:
            absent[tdcc] = reason
        else:
            assert found is not None
            rows.append(_row(found, fmt, ratings, risks.get(tdcc)))
    return ConditionSource.database(fmt, Table.of(fmt, rows), absent)


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


def _row(found: Stored, fmt: ReferenceFormat, ratings: _Ratings, risk: RiskLevel | None) -> dict[str, Any]:
    """一檔商品 → {標準欄位: 值}（沒有標準欄位的 UF、交易日期、分公司欄以欄名為鍵；排成表由 `Table.of` 做）。
    空值寫法用在 KI NONE、沒有的標的與不需要填的比價日。`risk` 是存好的風險等級（還沒算過是 None，風險等級欄空白）。"""
    p = found.columns
    values = sheet_values(found, fmt.empty_value)  # 商品條件、比價日、標的與價格（#498）
    values[RISK_FIELD] = _risk_cell(risk)
    values.update(ratings.columns(found.tdcc, p["issuer"], p["trade_date"]))
    if locked(found):
        values.update(dict.fromkeys(CHECKED_FIELDS, fmt.checked_value))
    extra_columns = fmt.extra  # 發行機構、UF、交易日期、分公司欄（#433）：沒有標準欄位，以欄名為鍵
    values[fmt.issuer_column] = p["issuer"]
    values[extra_columns.upfront] = p["upfront_pct"]
    values[extra_columns.trade_date] = p["trade_date"]
    branch_columns = extra_columns.branch_columns()
    for (branch_column, amount_column), (branch, amount) in zip(branch_columns, found.branches, strict=False):
        values[branch_column], values[amount_column] = branch, amount
    return values


def _record(found: Stored, fmt: ReferenceFormat, ratings: _Ratings, risk: RiskLevel | None) -> OrderRecord:
    """一檔商品現在的條件讀成的列：和核對時的列（`conditions`）同一個做法，寫回前比對用。"""
    (record,) = ConditionSource.database(fmt, Table.of(fmt, [_row(found, fmt, ratings, risk)]), {}).sheet.rows
    return record


@dataclass(frozen=True)
class _Ratings:
    """評等表＋上手代號對照：排核對條件的評等欄。`registry` 是核對用的上手清單（None：預設的 `REGISTRY`）。"""

    rows: RatingRows
    issuers: IssuerCodes
    registry: Sequence[Issuer] | None = None

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
        from fcn_checker.issuers import REGISTRY, by_code

        found = by_code(self.issuers.code(tdcc, issuer), REGISTRY if self.registry is None else self.registry)
        return found is not None and found.ratings


def _risk_cell(risk: RiskLevel | None) -> str | None:
    """風險等級欄的值：完成、回填寫等級，待算寫核對規則認得的待算寫法，還沒算過空白。"""
    if risk is None:
        return None
    if risk.status == PENDING:
        return RISK_LEVEL_PENDING
    return risk.level


# ---------- 寫回 ----------

# 核對結果檔沒寫成時，`check_result`、`backfill_log` 的結果檔欄寫這句（#443；同比價寫回寫說明文字的做法）
NO_RESULT_FILE = "核對結果檔沒寫成"


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
    registry: Sequence[Issuer] | None = None,
) -> WriteBackReceipt:
    """儲存確認來源沒變之後寫回資料庫；整次在同一個 transaction 裡，失敗時什麼都不寫。

    要回填哪些依核對結果（`BatchItem.fills_sheet`：說明書與投資人須知都通過或人工放行）決定，核對結果檔寫不出去
    也照樣回填、鎖住（#443：資料庫是正式紀錄，檔案可以重存）；那時核對結果與回填紀錄的結果檔寫 `NO_RESULT_FILE`。
    回填前確認資料庫現在的條件和核對時的列（`batch.source`，#445）相同、核對條件仍是完成：
    預覽之後重新匯入起單資料或重算過的商品不回填、不鎖住（鎖住的一定是核對過的條件）。
    `registry` 是核對用的上手清單（看哪家有做評等核對；None：預設的 `REGISTRY`）。
    """
    if not receipt.sources_unchanged:
        raise CheckSourceError("儲存時沒有確認來源與核對時相同，不寫回資料庫")
    if not isinstance(batch.source, ConditionSource) or batch.source.table is None:
        raise CheckSourceError("這批核對的不是資料庫來源，不寫回資料庫")
    checked_rows = {r.product_code.value: r for r in batch.source.sheet.rows}
    user = user or getpass.getuser()
    result_file = str(receipt.output) if receipt.output is not None else NO_RESULT_FILE
    codes = list(dict.fromkeys(i.product_code for i in batch.items if i.product_code))
    with conn.transaction():
        known = set(read_many(conn, codes))
        ratings = _Ratings(load_rows(conn), IssuerCodes.of(prefixes, fmt), registry)
        saved = 0
        for item in batch.items:
            if item.product_code in known and item.kind is not None:
                filled = any(i.fills_sheet for i in (item, item.partner) if i is not None)  # 這檔商品有回填
                _save_result(conn, item, filled, result_file, user)
                saved += 1
        done, already, stale, changes = [], [], [], 0
        for item in (i for i in batch.items if i.fills_sheet):
            tdcc = item.product_code
            if tdcc not in known:
                continue
            current = read(conn, tdcc, lock=True)  # 和商品條件寫入共用同一把鎖（#124）
            if locked(current):
                already.append(tdcc)
                continue
            rated = ratings.checked(tdcc, current.columns["issuer"])
            now = _record(current, fmt, ratings, risk_level_of(conn, tdcc))
            if _absent_reason(current) or _changed(now, checked_rows.get(tdcc), rated=rated):
                stale.append(tdcc)
                continue
            for v in item.backfill_values:  # 型別化的回填值（#444）：照目標寫表，值有改變時記原值
                changed = backfill(conn, tdcc, v.target, v.value, item=v.label, result_file=result_file, user=user)
                changes += int(changed)
            lock_checked(conn, tdcc)
            done.append(tdcc)
    return WriteBackReceipt(tuple(done), tuple(already), saved, changes, tuple(stale))


def _changed(current: OrderRecord, checked: OrderRecord | None, *, rated: bool) -> bool:
    """資料庫現在的值和核對時那一列（`OrderRecord`）不同；回填欄位（ISIN、發行日、比價日、回顧結束日、TS、IIS）不算。

    評等欄只在 `rated`（這家上手有做評等核對）時才比。核對時的列和現在的值由同一個 `_row` 排出（#445：不再讀以前
    產生的檔案），欄位一定相同，不必再處理舊版表少欄的情況。
    """
    if checked is None:
        return True
    skip = {target.field for target in ROW_FIELDS}  # 回填欄位只在 backfill_values 宣告（#444）
    for std, value in current.fields.items():
        if std in skip or (std in RATING_FIELDS and not rated):
            continue
        found = checked.get(std)
        if not same_value(value.value, None if found is None else found.value):
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


# ---------- 核對分頁與 fcn-batch 用的資料庫來源 ----------


class WorkflowDatabase:
    """`fcn_checker.panel_workflow.DatabaseSource` 的實作：連線設定由外殼先取得（視窗的資料庫登入、或 `.env`）。

    `settings` 回傳已取得的連線設定、不詢問，`conditions`、`write_back` 在背景連線時才呼叫；None 表示沒有登入。
    問密碼是畫面的事，由外殼在主執行緒先處理（#129）。核對條件不寫檔（#445），憑證記在核對紀錄。
    """

    def __init__(
        self,
        settings: Callable[[], ConnectionSettings | None],
        *,
        user: str | None = None,
        on_failure: Callable[[], None] = lambda: None,
        issuer_prefixes: Path | None = None,
    ) -> None:
        self._current_settings = settings
        self.user = user
        self.on_failure = on_failure  # 連不上資料庫時呼叫（例：視窗下次重新問密碼）
        self.issuer_prefixes = issuer_prefixes  # 上手編號對照設定檔；None 是預設位置（評等欄找上手代號，#240）

    def _prefixes(self) -> dict[str, str]:
        from fcn_checker.check_config import DEFAULTS
        from fcn_checker.config import load_issuer_prefixes

        return load_issuer_prefixes(self.issuer_prefixes or DEFAULTS.issuer_prefixes)

    def conditions(self, term_sheets: Sequence[Path], fmt: ReferenceFormat) -> ConditionSource:
        import psycopg

        from fcn_checker.identification import file_code
        from fcn_checker.ingestion import IngestionError

        tdccs = [code for code in (file_code(Path(p)) for p in term_sheets) if code]
        prefixes = self._prefixes()  # 設定檔有問題：IngestionError，整批不核對
        try:
            with self._connect() as conn:
                return conditions(conn, tdccs, fmt, prefixes=prefixes)
        except (CheckSourceError, DatabaseError, psycopg.Error) as exc:
            first = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            raise IngestionError("database_unavailable", f"從資料庫讀核對條件失敗：{first}") from exc

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
