from __future__ import annotations

import csv
import hashlib
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Final, Iterable

from .models import RawImport, StatementMetadata, Transaction, TransactionCategory

# Zaokrúhlenie peňažných údajov a EUR (polovičné zaokrúhlenie vždy hore smerom od nuly)
MONEY_QUANT = Decimal("0.01")


class IbkrParseError(ValueError):
    """Raised when an IBKR CSV cannot be parsed into known sections."""


def parse_ibkr_csv(path: str | Path) -> RawImport:
    source_path = Path(path)
    content = source_path.read_bytes()
    rows = list(csv.reader(content.decode("utf-8-sig").splitlines()))
    metadata = _parse_metadata(rows)
    transactions = list(_parse_transactions(rows, metadata, content))
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
            # Posledné výskyt kľúča vyhrá (napr. Base Currency v Account Information).
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
    rows: list[list[str]], metadata: StatementMetadata, content: bytes
) -> Iterable[Transaction]:
    history_out: list[Transaction] = []
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
            history_out.append(
                _transaction_from_payload(
                    payload, metadata, occurrence_counts[base_hash]
                )
            )

    if history_out:
        yield from history_out
        return

    if _is_realized_summary(metadata) or _has_realized_summary_markers(rows):
        yield from _parse_realized_summary(rows, metadata, content)
        return

    raise IbkrParseError(
        "CSV neobsahuje sekciu „Transaction History“ ani rozpoznateľný "
        "výkaz IBKR „Zrealizovaný súhrn“ (Realized Summary)."
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
    quantity = _money2(_decimal_or_none(payload.get("Quantity")))
    price = _money2(_decimal_or_none(payload.get("Price")))
    price_currency = _empty_to_none(payload.get("Price Currency"))
    gross_amount = _money2(_decimal_or_zero(payload.get("Gross Amount")))
    commission = _money2(_decimal_or_zero(payload.get("Commission")))
    net_amount = _money2(_decimal_or_zero(payload.get("Net Amount")))
    transaction_fees = _money2(_decimal_or_zero(payload.get("Transaction Fees")))
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


def _money2(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


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


RS_SOURCE: Final = "Zrealizovaný súhrn (Realized Summary)"


def _is_realized_summary(metadata: StatementMetadata) -> bool:
    t = (metadata.title or "").strip().lower()
    return t == "realized summary"


def _has_realized_summary_markers(rows: list[list[str]]) -> bool:
    for row in rows:
        if len(row) >= 2 and row[0] == "Trades" and row[1] == "Header":
            return True
        if len(row) >= 2 and row[0] == "Deposits & Withdrawals" and row[1] == "Header":
            return True
    return False


def _parse_realized_summary(
    rows: list[list[str]], metadata: StatementMetadata, content: bytes
) -> Iterable[Transaction]:
    """Import z CSV „Realized Summary“ (Zrealizovaný súhrn) — podklady s realized P/L podľa IB."""
    file_salt = hashlib.sha256(content).hexdigest()[:20]
    occ: dict[str, int] = {}
    for tx in _rs_parse_trades(rows, metadata, file_salt, occ):
        yield tx
    for tx in _rs_parse_deposits_withdrawals(rows, metadata, file_salt, occ):
        yield tx
    for tx in _rs_parse_fees(rows, metadata, file_salt, occ):
        yield tx
    for tx in _rs_parse_interest(rows, metadata, file_salt, occ):
        yield tx
    for tx in _rs_parse_forex_pl_details(rows, metadata, file_salt, occ):
        yield tx

    n = occ.get("_total", 0)
    if n == 0:
        raise IbkrParseError("Realized Summary: nenašli sa importovateľné dátové sekcie (Trades, …).")


def _rs_occ(occ: dict[str, int], key: str) -> int:
    occ[key] = occ.get(key, 0) + 1
    occ["_total"] = occ.get("_total", 0) + 1
    return occ[key]


def _rs_row_dict(header: list[str], row: list[str]) -> dict[str, str]:
    n = len(header)
    rest = row[2 : 2 + n] if len(row) >= 2 else []
    pad = rest + [""] * max(0, n - len(rest))
    return {header[i].strip(): pad[i].strip() for i in range(n)}


def _rs_alloc_to_lines(order_vals: list[Decimal], eur_total: Decimal) -> list[Decimal]:
    if not order_vals:
        return []
    s = sum(order_vals, start=Decimal("0"))
    if s != 0:
        return [eur_total * (v / s) for v in order_vals]
    n = len(order_vals)
    per = eur_total / Decimal(n) if n else Decimal("0")
    return [per for _ in order_vals]


def _rs_parse_trades(
    rows: list[list[str]],
    metadata: StatementMetadata,
    file_salt: str,
    occ: dict[str, int],
) -> Iterable[Transaction]:
    header_keys: list[str] | None = None
    buffer: list[dict[str, str]] = []
    base_currency = (metadata.base_currency or "EUR").upper()

    for row in rows:
        if len(row) < 2 or row[0] != "Trades":
            continue
        if row[1] == "Header":
            header_keys = [k.strip() for k in row[2:]] if len(row) > 2 else None
            buffer = []
            continue
        if (
            row[1] == "Data"
            and len(row) > 2
            and row[2] == "Order"
            and header_keys
        ):
            buffer.append(_rs_row_dict(header_keys, row))
            continue
        if row[1] == "Total" and "EUR" in row and buffer and header_keys:
            d_usd = _rs_find_trades_total_before_eur(rows, row)
            if d_usd is not None:
                for tx in _rs_emit_trade_rows(
                    buffer,
                    _rs_row_dict(header_keys, row),
                    base_currency,
                    file_salt,
                    occ,
                ):
                    yield tx
            buffer = []
            continue

    return


def _rs_find_trades_total_before_eur(
    all_rows: list[list[str]], eur_row: list[str]
) -> dict[str, str] | None:
    try:
        idx = all_rows.index(eur_row)
    except ValueError:
        idx = -1
        for i, r in enumerate(all_rows):
            if len(r) == len(eur_row) and r == eur_row:
                idx = i
                break
    if idx <= 0:
        return None
    h = [
        "DataDiscriminator",
        "Asset Category",
        "Currency",
        "Symbol",
        "Date/Time",
        "Quantity",
        "T. Price",
        "Proceeds",
        "Comm/Fee",
        "Basis",
        "Realized P/L",
        "Code",
    ]
    hlen = len(h)
    for j in range(idx - 1, -1, -1):
        r = all_rows[j]
        if len(r) > 1 and r[0] == "Trades" and r[1] == "Total" and "USD" in r:
            rest = r[2 : 2 + hlen] + [""] * max(0, hlen - (len(r) - 2))
            return {h[i]: rest[i] for i in range(hlen)}
    return None


def _rs_emit_trade_rows(
    buffer: list[dict[str, str]],
    d_eur: dict[str, str],
    base_currency: str,
    file_salt: str,
    occ: dict[str, int],
) -> Iterable[Transaction]:
    if not buffer:
        return
    e_proceeds = _decimal_or_zero(d_eur.get("Proceeds"))
    e_comm = _decimal_or_zero(d_eur.get("Comm/Fee"))
    e_rpl = _decimal_or_zero(d_eur.get("Realized P/L"))

    o_pro = [_decimal_or_zero(x.get("Proceeds")) for x in buffer]
    o_comm = [_decimal_or_zero(x.get("Comm/Fee")) for x in buffer]
    o_rpl = [_decimal_or_zero(x.get("Realized P/L")) for x in buffer]

    a_pro = _rs_alloc_to_lines(o_pro, e_proceeds)
    a_comm = _rs_alloc_to_lines(o_comm, e_comm)
    a_rpl = _rs_alloc_to_lines(o_rpl, e_rpl)

    for k, o in enumerate(buffer):
        n_usd = o_pro[k] + o_comm[k]
        n_eur = a_pro[k] + a_comm[k]
        ex_rate = (n_eur / n_usd) if n_usd != 0 else Decimal("1")
        g_eur = a_pro[k]
        c_eur = a_comm[k]
        qty = o.get("Quantity", "")
        try:
            qd = _decimal_or_none(qy) if (qy := qty.strip()) else None
        except (InvalidOperation, AttributeError):
            qd = None
        ttype = "Buy" if (qd and qd > 0) else "Sell" if (qd and qd < 0) else "Sell"
        sym = _empty_to_none(o.get("Symbol", ""))
        tdt, tdesc = _rs_trade_date_desc(o, a_rpl[k])
        inst = _instrument_type(sym)
        desc = tdesc
        pcy = o.get("Currency", "USD")
        raw: dict[str, str] = {
            "Source": RS_SOURCE,
            "Section": "Trades",
            "file_salt": file_salt,
            "Asset Category": o.get("Asset Category", ""),
            "Code": o.get("Code", ""),
            "USD Proceeds": str(o_pro[k]),
            "USD Comm/Fee": str(o_comm[k]),
            "USD Realized P/L": str(o_rpl[k]),
            "EUR Proceeds (alloc)": str(a_pro[k].quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)),
            "EUR Comm/Fee (alloc)": str(a_comm[k].quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)),
            "EUR Realized P/L (alloc)": str(a_rpl[k].quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)),
        }
        raw.update({k: v for k, v in o.items() if k and v is not None})

        yield _tx_from_realized(
            trade_date=tdt,
            description=desc,
            symbol=sym,
            inst=inst,
            ttype=ttype,
            qd=qd,
            tprice=_decimal_or_none(o.get("T. Price")) or None,
            pcy=pcy,
            gross=o_pro[k],
            comm=o_comm[k],
            net=n_usd,
            ex=(
                Decimal("1")
                if pcy and pcy.upper() == base_currency
                else (ex_rate if n_usd != 0 else Decimal("1"))
            ),
            gross_eur=g_eur,
            comm_eur=c_eur,
            net_eur=n_eur,
            pcy_out=pcy,
            needs_review=inst == "option" and ttype not in ("Buy", "Sell"),
            raw=raw,
            file_salt=file_salt,
            occ=occ,
        )


def _rs_trade_date_desc(o: dict[str, str], eur_rpl: Decimal) -> tuple[date, str]:
    dts = (o.get("Date/Time") or "").strip()
    if "," in dts:
        d_part = dts.split(",")[0].strip()
    else:
        d_part = dts[:10] if len(dts) >= 10 else dts
    try:
        tdt = date.fromisoformat(d_part)
    except ValueError:
        tdt = date(1970, 1, 1)
    sym = o.get("Symbol", "")
    c = o.get("Code", "")
    d = f"{RS_SOURCE} · {o.get('Asset Category', '')} {sym} · {dts} · {c} · P/L IB EUR {eur_rpl}"
    if len(d) > 2000:
        d = d[:1997] + "..."
    return tdt, d


def _tx_from_realized(
    *,
    trade_date: date,
    description: str,
    symbol: str | None,
    inst: str,
    ttype: str,
    qd: Decimal | None,
    tprice: Decimal | None,
    pcy: str,
    gross: Decimal,
    comm: Decimal,
    net: Decimal,
    ex: Decimal,
    gross_eur: Decimal,
    comm_eur: Decimal,
    net_eur: Decimal,
    pcy_out: str,
    needs_review: bool,
    raw: dict[str, str],
    file_salt: str,
    occ: dict[str, int],
) -> Transaction:
    ocr = _rs_occ(occ, "trade")
    raw_full = {**raw, "occurrence": str(ocr)}
    row_hash = _row_hash(
        {**raw_full, "kind": "rs_trade", "salt": file_salt},
        ocr,
    )
    g_eu = _money2(gross_eur) or Decimal("0")
    c_eu = _money2(comm_eur) or Decimal("0")
    n_eu = _money2(net_eur) or (g_eu + c_eu).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    tick = _ticker_from_symbol(symbol)
    return Transaction(
        row_hash=row_hash,
        trade_date=trade_date,
        description=description,
        symbol=symbol,
        ticker=tick,
        instrument_type=inst,
        price=tprice,
        price_currency=pcy_out,
        gross_amount=gross,
        commission=comm,
        net_amount=net,
        transaction_fees=Decimal("0"),
        sub_type=None,
        transaction_type=ttype,
        quantity=qd,
        exchange_rate=ex,
        gross_amount_eur=g_eu,
        commission_eur=c_eu,
        net_amount_eur=n_eu,
        category=TransactionCategory.TRADE,
        needs_review=needs_review,
        raw_payload=raw_full,
    )


def _rs_payload_simple(header: list[str], row: list[str]) -> dict[str, str]:
    n = len(header)
    rest = row[2 : 2 + n] if len(row) > 1 else []
    pad = rest + [""] * max(0, n - len(rest))
    return {header[i].strip(): pad[i].strip() for i in range(n)}


def _rs_parse_deposits_withdrawals(
    rows: list[list[str]], _metadata: StatementMetadata, file_salt: str, occ: dict[str, int]
) -> Iterable[Transaction]:
    header: list[str] | None = None
    for row in rows:
        if len(row) < 2 or row[0] != "Deposits & Withdrawals":
            continue
        if row[1] == "Header":
            header = [k.strip() for k in row[2:]] if len(row) > 2 else None
        elif (
            row[1] == "Data"
            and header
            and (len(row) < 3 or not row[2].lower().startswith("total"))
        ):
            p = _rs_payload_simple(header, row)
            ocr = _rs_occ(occ, "dep")
            d_raw = {**p, "Source": RS_SOURCE, "Section": "Deposits & Withdrawals", "salt": file_salt}
            row_hash = _row_hash({**d_raw, "kind": "rs_deposit"}, ocr)
            sdt = (p.get("Settle Date") or "").strip() or "1970-01-01"
            try:
                tdt = date.fromisoformat(sdt[:10])
            except ValueError:
                tdt = date(1970, 1, 1)
            amt = _decimal_or_zero(p.get("Amount"))
            ccy = (p.get("Currency") or "EUR").upper()
            ex = Decimal("1") if ccy == "EUR" else Decimal("1")
            desc = f"{RS_SOURCE} · {p.get('Description', '')}"
            yield Transaction(
                row_hash=row_hash,
                trade_date=tdt,
                description=desc[:2000],
                symbol=None,
                ticker=None,
                instrument_type="cash",
                price=None,
                price_currency=ccy,
                gross_amount=amt,
                commission=Decimal("0"),
                net_amount=amt,
                transaction_fees=Decimal("0"),
                sub_type=None,
                transaction_type="Deposit" if amt >= 0 else "Withdrawal",
                quantity=None,
                exchange_rate=ex,
                gross_amount_eur=amt,
                commission_eur=Decimal("0"),
                net_amount_eur=amt,
                category=TransactionCategory.DEPOSIT_WITHDRAWAL,
                needs_review=False,
                raw_payload=d_raw,
            )


def _rs_parse_fees(
    rows: list[list[str]], _metadata: StatementMetadata, file_salt: str, occ: dict[str, int]
) -> Iterable[Transaction]:
    header: list[str] | None = None
    for row in rows:
        if len(row) < 2 or row[0] != "Fees":
            continue
        if row[1] == "Header":
            header = [k.strip() for k in row[2:]] if len(row) > 2 else None
        elif row[1] == "Data" and header:
            r2 = (row[2] or "").strip() if len(row) > 2 else ""
            if r2 in ("Total", "Notes"):
                continue
            p = _rs_payload_simple(header, row)
            ocr = _rs_occ(occ, "fee")
            d_raw = {**p, "Source": RS_SOURCE, "Section": "Fees", "salt": file_salt}
            row_hash = _row_hash({**d_raw, "kind": "rs_fee"}, ocr)
            sdt = (p.get("Date") or "").strip() or "1970-01-01"
            try:
                tdt = date.fromisoformat(sdt[:10])
            except ValueError:
                tdt = date(1970, 1, 1)
            amt = _decimal_or_zero(p.get("Amount"))
            ccy = (p.get("Currency") or "EUR").upper()
            ex = Decimal("1")
            desc = f"{RS_SOURCE} · {p.get('Description', '')}"
            n_eu = amt if ccy == "EUR" else amt
            yield Transaction(
                row_hash=row_hash,
                trade_date=tdt,
                description=desc[:2000],
                symbol=None,
                ticker=None,
                instrument_type="cash",
                price=None,
                price_currency=ccy,
                gross_amount=amt,
                commission=Decimal("0"),
                net_amount=amt,
                transaction_fees=Decimal("0"),
                sub_type=p.get("Subtitle"),
                transaction_type="Other Fee",
                quantity=None,
                exchange_rate=ex,
                gross_amount_eur=n_eu,
                commission_eur=Decimal("0"),
                net_amount_eur=n_eu,
                category=TransactionCategory.FEE,
                needs_review=False,
                raw_payload=d_raw,
            )


def _rs_parse_interest(
    rows: list[list[str]], _metadata: StatementMetadata, file_salt: str, occ: dict[str, int]
) -> Iterable[Transaction]:
    header: list[str] | None = None
    for row in rows:
        if len(row) < 2 or row[0] != "Interest":
            continue
        if row[1] == "Header":
            header = [k.strip() for k in row[2:]] if len(row) > 2 else None
        elif row[1] == "Data" and header and len(row) > 2:
            cur0 = (row[2] or "").strip()
            if cur0.lower().startswith("total in eur") or "Total in EUR" in cur0:
                p = _rs_payload_simple(header, row)
                amt = _decimal_or_zero(p.get("Amount"))
                if amt == 0 and len(row) > 5:
                    amt = _decimal_or_zero(row[-1] if len(row) > 5 else None)
                ocr = _rs_occ(occ, "int")
                d_raw = {**p, "Source": RS_SOURCE, "Section": "Interest", "kind": "eur_total", "salt": file_salt}
                row_hash = _row_hash({**d_raw, "occ": str(ocr)}, 1)
                sdt = (p.get("Date") or "").strip() or "1970-01-01"
                try:
                    tdt = date.fromisoformat(sdt[:10]) if sdt[0:4].isdigit() else date(1970, 1, 1)
                except (ValueError, IndexError):
                    tdt = date(1970, 1, 1)
                desc = f"{RS_SOURCE} · Úrok (súčet v EUR z výpisu)"
                yield Transaction(
                    row_hash=row_hash,
                    trade_date=tdt,
                    description=desc,
                    symbol=None,
                    ticker=None,
                    instrument_type="cash",
                    price=None,
                    price_currency="EUR",
                    gross_amount=amt,
                    commission=Decimal("0"),
                    net_amount=amt,
                    transaction_fees=Decimal("0"),
                    sub_type=None,
                    transaction_type="Credit Interest",
                    quantity=None,
                    exchange_rate=Decimal("1"),
                    gross_amount_eur=amt,
                    commission_eur=Decimal("0"),
                    net_amount_eur=amt,
                    category=TransactionCategory.INTEREST,
                    needs_review=False,
                    raw_payload=d_raw,
                )


def _rs_parse_forex_pl_details(
    rows: list[list[str]], _metadata: StatementMetadata, file_salt: str, occ: dict[str, int]
) -> Iterable[Transaction]:
    header: list[str] | None = None
    for row in rows:
        if len(row) < 2 or row[0] != "Forex P/L Details":
            continue
        if row[1] == "Header":
            header = [k.strip() for k in row[2:]] if len(row) > 2 else None
        elif row[1] == "Data" and header and (len(row) < 3 or row[2] != "Total"):
            p = _rs_payload_simple(header, row)
            rpl = _decimal_or_zero(p.get("Realized P/L in EUR"))
            if p.get("DataDiscriminator", "").lower() == "total":
                continue
            if p.get("Description", "").lower().strip() in ("", "total"):
                continue
            ocr = _rs_occ(occ, "fx")
            d_raw = {**p, "Source": RS_SOURCE, "Section": "Forex P/L Details", "salt": file_salt}
            row_hash = _row_hash({**d_raw, "kind": "rs_fx", "occ": str(ocr)}, 1)
            _dtf = p.get("Date/Time") or ""
            dts = (_dtf.split(",")[0] if _dtf else "").strip()[:10]
            try:
                tdt = date.fromisoformat(dts) if len(dts) == 10 else date(1970, 1, 1)
            except ValueError:
                tdt = date(1970, 1, 1)
            desc = f"{RS_SOURCE} · FX: {p.get('Description', '')} · {p.get('Code', '')}"
            yield Transaction(
                row_hash=row_hash,
                trade_date=tdt,
                description=desc[:2000],
                symbol=None,
                ticker=None,
                instrument_type="cash",
                price=None,
                price_currency="EUR",
                gross_amount=rpl,
                commission=Decimal("0"),
                net_amount=rpl,
                transaction_fees=Decimal("0"),
                sub_type=None,
                transaction_type="Fx",
                quantity=_decimal_or_none(p.get("Quantity")),
                exchange_rate=Decimal("1"),
                gross_amount_eur=rpl,
                commission_eur=Decimal("0"),
                net_amount_eur=rpl,
                category=TransactionCategory.FX,
                needs_review=False,
                raw_payload=d_raw,
            )
