from __future__ import annotations

import json
import re
import shutil
import sqlite3
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .models import ImportResult, RawImport, Transaction

APP_DIR = Path.home() / ".local" / "share" / "uctsimp"
DATA_DIR = APP_DIR / "data"
SETTINGS_PATH = APP_DIR / "settings.json"
# Starý jeden súbor (pred ročným rozčlenením) — po migrácii sa premenuje
LEGACY_DB_PATH = APP_DIR / "uctsimp.sqlite3"


def _default_year() -> int:
    y = date.today().year
    if y < 2000 or y > 2100:
        return 2026
    return y


def ensure_data_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def path_for_year(year: int) -> Path:
    if year < 2000 or year > 2100:
        raise ValueError("rok mimo rozsah 2000–2100")
    ensure_data_dir()
    return DATA_DIR / f"uctsimp_{year}.sqlite3"


def list_years_on_disk() -> list[int]:
    if not DATA_DIR.is_dir():
        return []
    out: list[int] = []
    for p in DATA_DIR.glob("uctsimp_*.sqlite3"):
        m = re.match(r"^uctsimp_(\d{4})\.sqlite3$", p.name, re.IGNORECASE)
        if m:
            y = int(m.group(1))
            if 2000 <= y <= 2100:
                out.append(y)
    return sorted(out)


def load_active_year() -> int:
    try:
        if SETTINGS_PATH.is_file():
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            y = int(data.get("active_year", _default_year()))
            if 2000 <= y <= 2100:
                return y
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return _default_year()


def save_active_year(year: int) -> None:
    if year < 2000 or year > 2100:
        raise ValueError("rok mimo rozsah")
    ensure_data_dir()
    APP_DIR.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if SETTINGS_PATH.is_file():
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    data["active_year"] = year
    SETTINGS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def get_default_connection_path() -> Path:
    """Aktuálna databáza podľa uloženého roka."""
    return path_for_year(load_active_year())


def _open_db_file(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    migrate(connection)
    return connection


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    if db_path is not None:
        return _open_db_file(Path(db_path))
    return _open_db_file(get_default_connection_path())


def connect_for_year(year: int) -> sqlite3.Connection:
    return _open_db_file(path_for_year(year))


def migrate(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS import_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_path TEXT NOT NULL,
            file_name TEXT NOT NULL,
            file_hash TEXT NOT NULL UNIQUE,
            statement_title TEXT,
            statement_period TEXT,
            generated_at TEXT,
            base_currency TEXT,
            imported_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            import_file_id INTEGER NOT NULL REFERENCES import_files(id),
            row_hash TEXT NOT NULL UNIQUE,
            trade_date TEXT NOT NULL,
            description TEXT NOT NULL,
            symbol TEXT,
            ticker TEXT,
            instrument_type TEXT NOT NULL,
            price TEXT,
            price_currency TEXT,
            gross_amount TEXT NOT NULL,
            commission TEXT NOT NULL,
            net_amount TEXT NOT NULL,
            transaction_fees TEXT NOT NULL,
            sub_type TEXT,
            transaction_type TEXT NOT NULL,
            quantity TEXT,
            exchange_rate TEXT NOT NULL,
            gross_amount_eur TEXT NOT NULL,
            commission_eur TEXT NOT NULL,
            net_amount_eur TEXT NOT NULL,
            category TEXT NOT NULL,
            needs_review INTEGER NOT NULL,
            raw_payload TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_transactions_trade_date
            ON transactions(trade_date);
        CREATE INDEX IF NOT EXISTS idx_transactions_ticker
            ON transactions(ticker);
        CREATE INDEX IF NOT EXISTS idx_transactions_category
            ON transactions(category);
        """
    )
    connection.commit()


def migrate_legacy_to_per_year() -> str | None:
    """
    Ak existuje len starý uctsimp.sqlite3 s dátami a ešte nie sú ročné súbory,
    rozdelí transakcie do data/uctsimp_YYYY.sqlite3 a pôvodný súbor premenuje.
    Vráti text pre používateľa alebo None.
    """
    if not LEGACY_DB_PATH.is_file():
        return None
    ensure_data_dir()
    if list_years_on_disk():
        return None
    try:
        src = sqlite3.connect(LEGACY_DB_PATH)
    except OSError:
        return None
    src.row_factory = sqlite3.Row
    n = int(src.execute("SELECT COUNT(*) AS c FROM transactions").fetchone()["c"])
    if n == 0:
        src.close()
        return None
    years_rows = src.execute(
        "SELECT DISTINCT substr(trade_date,1,4) AS y FROM transactions WHERE length(trade_date) >= 4 ORDER BY y"
    ).fetchall()
    years: list[int] = []
    for r in years_rows:
        ys = (r["y"] or "").strip()
        if len(ys) == 4 and ys.isdigit():
            years.append(int(ys))
    if not years:
        src.close()
        return None

    for y in years:
        p = path_for_year(y)
        if p.is_file():
            continue
        dst = sqlite3.connect(p)
        dst.row_factory = sqlite3.Row
        migrate(dst)
        txs = list(
            src.execute(
                "SELECT * FROM transactions WHERE trade_date >= ? AND trade_date < ? ORDER BY id",
                (f"{y}-01-01", f"{y + 1}-01-01"),
            )
        )
        if not txs:
            dst.close()
            p.unlink(missing_ok=True)
            continue
        old_fids = sorted({int(r["import_file_id"]) for r in txs})
        id_map: dict[int, int] = {}
        for old_fid in old_fids:
            frow = src.execute("SELECT * FROM import_files WHERE id = ?", (old_fid,)).fetchone()
            if frow is None:
                continue
            keys = [k for k in frow.keys() if k != "id"]
            ph = ", ".join("?" * len(keys))
            dst.execute(
                f"INSERT INTO import_files ({', '.join(keys)}) VALUES ({ph})",
                tuple(frow[k] for k in keys),
            )
            new_id = int(dst.execute("SELECT last_insert_rowid()").fetchone()[0])
            id_map[old_fid] = new_id
        tkeys = [k for k in txs[0].keys() if k != "id"]
        for r in txs:
            vals = [id_map[int(r["import_file_id"])] if k == "import_file_id" else r[k] for k in tkeys]
            ph = ", ".join("?" * len(tkeys))
            dst.execute(
                f"INSERT INTO transactions ({', '.join(tkeys)}) VALUES ({ph})",
                tuple(vals),
            )
        dst.commit()
        dst.close()

    src.close()
    try:
        bak = APP_DIR / "uctsimp_pred_ročnou_migráciou.sqlite3.bak"
        LEGACY_DB_PATH.rename(bak)
    except OSError:
        pass
    y_min, y_max = min(years), max(years)
    return (
        f"Migrácia: stará databáza sa rozčlenila do rokov {y_min}–{y_max} "
        f"(priečinok dát: {DATA_DIR}). Pôvodný súbor je zálohovaný."
    )


def import_raw(connection: sqlite3.Connection, raw_import: RawImport) -> ImportResult:
    with connection:
        import_file_id = _insert_or_get_import_file(connection, raw_import)
        inserted = 0
        skipped = 0
        for transaction in raw_import.transactions:
            if _insert_transaction(connection, import_file_id, transaction):
                inserted += 1
            else:
                skipped += 1

    return ImportResult(
        import_file_id=import_file_id,
        inserted=inserted,
        skipped_duplicates=skipped,
        total_rows=len(raw_import.transactions),
    )


def clear_all_data(connection: sqlite3.Connection) -> tuple[int, int]:
    n_tx = int(
        connection.execute("SELECT COUNT(*) AS c FROM transactions").fetchone()["c"]
    )
    n_f = int(
        connection.execute("SELECT COUNT(*) AS c FROM import_files").fetchone()["c"]
    )
    with connection:
        connection.execute("DELETE FROM transactions")
        connection.execute("DELETE FROM import_files")
    return (n_tx, n_f)


def backup_year_database(db_path: Path, destination: Path) -> None:
    shutil.copy2(Path(db_path), Path(destination))


def restore_year_from_file(source: Path, year: int) -> None:
    """Prepíše databázu daného roka kópiou (volajte po zatvorení pripojenia)."""
    dest = path_for_year(year)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(source), dest)


def _insert_or_get_import_file(
    connection: sqlite3.Connection, raw_import: RawImport
) -> int:
    existing = connection.execute(
        "SELECT id FROM import_files WHERE file_hash = ?", (raw_import.file_hash,)
    ).fetchone()
    if existing:
        return int(existing["id"])

    cursor = connection.execute(
        """
        INSERT INTO import_files (
            source_path, file_name, file_hash, statement_title, statement_period,
            generated_at, base_currency, imported_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(raw_import.source_path),
            raw_import.source_path.name,
            raw_import.file_hash,
            raw_import.metadata.title,
            raw_import.metadata.period,
            raw_import.metadata.generated_at.isoformat()
            if raw_import.metadata.generated_at
            else None,
            raw_import.metadata.base_currency,
            datetime.now().isoformat(timespec="seconds"),
        ),
    )
    return int(cursor.lastrowid)


def _insert_transaction(
    connection: sqlite3.Connection, import_file_id: int, transaction: Transaction
) -> bool:
    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO transactions (
            import_file_id, row_hash, trade_date, description, symbol, ticker,
            instrument_type, price, price_currency, gross_amount, commission,
            net_amount, transaction_fees, sub_type, transaction_type, quantity,
            exchange_rate, gross_amount_eur, commission_eur, net_amount_eur,
            category, needs_review, raw_payload
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            import_file_id,
            transaction.row_hash,
            transaction.trade_date.isoformat(),
            transaction.description,
            transaction.symbol,
            transaction.ticker,
            transaction.instrument_type,
            _decimal_to_text(transaction.price),
            transaction.price_currency,
            _decimal_to_text(transaction.gross_amount),
            _decimal_to_text(transaction.commission),
            _decimal_to_text(transaction.net_amount),
            _decimal_to_text(transaction.transaction_fees),
            transaction.sub_type,
            transaction.transaction_type,
            _decimal_to_text(transaction.quantity),
            _decimal_to_text(transaction.exchange_rate),
            _decimal_to_text(transaction.gross_amount_eur),
            _decimal_to_text(transaction.commission_eur),
            _decimal_to_text(transaction.net_amount_eur),
            transaction.category.value,
            1 if transaction.needs_review else 0,
            json.dumps(transaction.raw_payload, ensure_ascii=True, sort_keys=True),
        ),
    )
    return cursor.rowcount == 1


def _decimal_to_text(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None
