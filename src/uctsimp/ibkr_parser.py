from __future__ import annotations

import csv
import hashlib
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Iterable

from .models import RawImport, StatementMetadata, Transaction, TransactionCategory

MONEY_QUANT = Decimal("0.000001")


class IbkrParseError(ValueError):
    """Raised when an IBKR CSV cannot be parsed into known sections."""


def parse_ibkr_csv(path: str | Path) -> RawImport:
    source_path = Path(path)
    content = source_path.read_bytes()
    rows = list(csv.reader(content.decode("utf-8-sig").splitlines()))
    metadata = _parse_metadata(rows)
    transactions = list(_parse_transactions(rows, metadata))
    return RawImport(
        source_path=source_path,
        file_hash=hashlib.sha256(content).hexdigest(),
        metadata=metadata,
        transactions=transactions,
    )


def _parse_metadata(rows: list[list[str]]) -> StatementMetadata:
    values: dict[str, str] = {}
    for row in rows:
        if len(row) >= 4 and row[1] == "Data":
            key = row[2].strip()
            values[key] = row[3].strip()

    generated_at = None
    when_generated = values.get("WhenGenerated")
    if when_generated:
        for fmt in ("%Y-%m-%d, %H:%M:%S %Z", "%Y-%m-%d, %H:%M:%S"):
            try:
                generated_at = datetime.strptime(when_generated, fmt)
                break
            except ValueError:
                continue

    return StatementMetadata(
        title=values.get("Title"),
        period=values.get("Period"),
        generated_at=generated_at,
        base_currency=values.get("Base Currency"),
    )


def _parse_transactions(
    rows: list[list[str]], metadata: StatementMetadata
) -> Iterable[Transaction]:
    header: list[str] | None = None
    occurrence_counts: dict[str, int] = {}

    for row in rows:
        if len(row) >= 3 and row[0] == "Transaction History" and row[1] == "Header":
            header = [field.strip() for field in row[2:]]
            continue

        if len(row) >= 3 and row[0] == "Transaction History" and row[1] == "Data":
            if header is None:
                raise IbkrParseError("Transaction History data found before header.")
            payload = _payload_from_row(header, row[2:])
            base_hash = _payload_hash(payload)
            occurrence_counts[base_hash] = occurrence_counts.get(base_hash, 0) + 1
            yield _transaction_from_payload(
                payload, metadata, occurrence_counts[base_hash]
            )


def _payload_from_row(header: list[str], values: list[str]) -> dict[str, str]:
    padded_values = values + [""] * max(0, len(header) - len(values))
    return {
        key.strip(): value.strip()
        for key, value in zip(header, padded_values, strict=False)
    }


def _transaction_from_payload(
    payload: dict[str, str], metadata: StatementMetadata, occurrence: int
) -> Transaction:
    trade_date = date.fromisoformat(_required(payload, "Date"))
    description = _clean_text(payload.get("Description")) or ""
    symbol = _empty_to_none(payload.get("Symbol"))
    ticker = _ticker_from_symbol(symbol)
    instrument_type = _instrument_type(symbol)
    transaction_type = _clean_text(payload.get("Transaction Type")) or "Unknown"
    category = _category_for(transaction_type, description)
    quantity = _decimal_or_none(payload.get("Quantity"))
    price = _decimal_or_none(payload.get("Price"))
    price_currency = _empty_to_none(payload.get("Price Currency"))
    gross_amount = _decimal_or_zero(payload.get("Gross Amount"))
    commission = _decimal_or_zero(payload.get("Commission"))
    net_amount = _decimal_or_zero(payload.get("Net Amount"))
    transaction_fees = _decimal_or_zero(payload.get("Transaction Fees"))
    exchange_rate = _decimal_or_zero(payload.get("Exchange Rate")) or Decimal("1")

    row_hash = _row_hash(payload, occurrence)
    base_currency = (metadata.base_currency or "EUR").upper()
    needs_review = category in {TransactionCategory.ADJUSTMENT, TransactionCategory.OTHER}
    if instrument_type == "option" and transaction_type not in {"Buy", "Sell"}:
        needs_review = True

    return Transaction(
        row_hash=row_hash,
        trade_date=trade_date,
        description=description,
        symbol=symbol,
        ticker=ticker,
        instrument_type=instrument_type,
        price=price,
        price_currency=price_currency,
        gross_amount=gross_amount,
        commission=commission,
        net_amount=net_amount,
        transaction_fees=transaction_fees,
        sub_type=_empty_to_none(payload.get("Sub Type")),
        transaction_type=transaction_type,
        quantity=quantity,
        exchange_rate=exchange_rate,
        gross_amount_eur=_amount_to_eur(gross_amount, base_currency, exchange_rate),
        commission_eur=_amount_to_eur(commission, base_currency, exchange_rate),
        net_amount_eur=_amount_to_eur(net_amount, base_currency, exchange_rate),
        category=category,
        needs_review=needs_review,
        raw_payload=payload,
    )


def _required(payload: dict[str, str], key: str) -> str:
    value = _clean_text(payload.get(key))
    if not value:
        raise IbkrParseError(f"Missing required Transaction History field: {key}")
    return value


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return None if stripped in {"", "-"} else stripped


def _empty_to_none(value: str | None) -> str | None:
    return _clean_text(value)


def _decimal_or_none(value: str | None) -> Decimal | None:
    cleaned = _clean_text(value)
    if cleaned is None:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise IbkrParseError(f"Invalid decimal value: {value}") from exc


def _decimal_or_zero(value: str | None) -> Decimal:
    return _decimal_or_none(value) or Decimal("0")


def _amount_to_eur(
    amount: Decimal, base_currency: str, exchange_rate: Decimal
) -> Decimal:
    if base_currency == "EUR":
        eur_amount = amount
    else:
        eur_amount = amount * exchange_rate
    return eur_amount.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _ticker_from_symbol(symbol: str | None) -> str | None:
    if not symbol:
        return None
    return symbol.split()[0]


def _instrument_type(symbol: str | None) -> str:
    if not symbol:
        return "cash"
    normalized = " ".join(symbol.split())
    parts = normalized.split()
    if len(parts) >= 2 and len(parts[1]) == 15 and parts[1][6] in {"C", "P"}:
        return "option"
    return "stock"


def _category_for(transaction_type: str, description: str) -> TransactionCategory:
    normalized_type = transaction_type.lower()
    normalized_description = description.lower()

    if normalized_type in {"buy", "sell"}:
        return TransactionCategory.TRADE
    if normalized_type in {"deposit", "withdrawal"}:
        return TransactionCategory.DEPOSIT_WITHDRAWAL
    if "interest" in normalized_type or "interest" in normalized_description:
        return TransactionCategory.INTEREST
    if "fee" in normalized_type or "fee" in normalized_description or "bundle" in normalized_description:
        return TransactionCategory.FEE
    if "fx" in normalized_description or "forex" in normalized_description:
        return TransactionCategory.FX
    if normalized_type == "adjustment":
        return TransactionCategory.ADJUSTMENT
    return TransactionCategory.OTHER


def _payload_hash(payload: dict[str, str]) -> str:
    stable_payload = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    return hashlib.sha256(stable_payload.encode("utf-8")).hexdigest()


def _row_hash(payload: dict[str, str], occurrence: int) -> str:
    stable_payload = json.dumps(
        {"payload": payload, "occurrence": occurrence},
        ensure_ascii=True,
        sort_keys=True,
    )
    return hashlib.sha256(stable_payload.encode("utf-8")).hexdigest()
