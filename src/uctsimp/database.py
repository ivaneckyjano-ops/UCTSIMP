from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from .models import ImportResult, RawImport, Transaction

APP_DIR = Path.home() / ".local" / "share" / "uctsimp"
DEFAULT_DB_PATH = APP_DIR / "uctsimp.sqlite3"


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    migrate(connection)
    return connection


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
