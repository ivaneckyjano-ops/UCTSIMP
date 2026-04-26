from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path


class TransactionCategory(StrEnum):
    TRADE = "trade"
    FEE = "fee"
    INTEREST = "interest"
    DEPOSIT_WITHDRAWAL = "deposit_withdrawal"
    FX = "fx"
    ADJUSTMENT = "adjustment"
    OTHER = "other"


@dataclass(frozen=True)
class StatementMetadata:
    title: str | None = None
    period: str | None = None
    generated_at: datetime | None = None
    base_currency: str | None = None


@dataclass(frozen=True)
class RawImport:
    source_path: Path
    file_hash: str
    metadata: StatementMetadata
    transactions: list["Transaction"]


@dataclass(frozen=True)
class Transaction:
    row_hash: str
    trade_date: date
    description: str
    symbol: str | None
    ticker: str | None
    instrument_type: str
    price: Decimal | None
    price_currency: str | None
    gross_amount: Decimal
    commission: Decimal
    net_amount: Decimal
    transaction_fees: Decimal
    sub_type: str | None
    transaction_type: str
    quantity: Decimal | None
    exchange_rate: Decimal
    gross_amount_eur: Decimal
    commission_eur: Decimal
    net_amount_eur: Decimal
    category: TransactionCategory
    needs_review: bool
    raw_payload: dict[str, str]


@dataclass(frozen=True)
class ImportResult:
    import_file_id: int
    inserted: int
    skipped_duplicates: int
    total_rows: int


@dataclass(frozen=True)
class SummaryRow:
    key: str
    trade_count: int
    gross_eur: Decimal
    commission_eur: Decimal
    net_eur: Decimal
