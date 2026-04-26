from __future__ import annotations

from pathlib import Path

from uctsimp.database import connect, import_raw
from uctsimp.ibkr_parser import parse_ibkr_csv
from uctsimp.models import TransactionCategory
from uctsimp.reports import category_summary, export_excel, ticker_summary


SAMPLE_CSV = """Statement,Header,Field Name,Field Value
Statement,Data,Title,Transaction History
Statement,Data,Period,"January 1, 2026 - March 31, 2026"
Summary,Header,Field Name,Field Value
Summary,Data,Base Currency,EUR
Transaction History,Header,Date,Description,Symbol,Price,Price Currency,Gross Amount ,Commission,Net Amount,Transaction Fees,Sub Type,Transaction Type,Quantity,Exchange Rate
Transaction History,Data,2026-03-31,AMZN 17APR26 195 P,AMZN  260417P00195000,2.63,USD,-227.64754,-0.907084561,-228.55462456100003,-,-,Buy,1.0,0.86558
Transaction History,Data,2026-03-03,v************n7:US Securities Snapshot and Futures Value Bundle Non-Professional for Mar 2026,-,-,-,-8.46,-,-8.46,-,-,Other Fee,-,1.0
Transaction History,Data,2026-02-18,Electronic Fund Transfer,-,-,-,1000.0,-,1000.0,-,-,Deposit,-,1.0
Transaction History,Data,2026-01-06,USD IBKR Managed Securities (SYEP) Interest for Dec-2025,-,-,-,0.0427775,-,0.0427775,-,-,Credit Interest,-,0.85555
"""


def test_parser_reads_transaction_history(tmp_path: Path) -> None:
    csv_path = tmp_path / "ibkr.csv"
    csv_path.write_text(SAMPLE_CSV, encoding="utf-8")

    raw_import = parse_ibkr_csv(csv_path)

    assert raw_import.metadata.base_currency == "EUR"
    assert len(raw_import.transactions) == 4
    trade = raw_import.transactions[0]
    assert trade.ticker == "AMZN"
    assert trade.instrument_type == "option"
    assert trade.category == TransactionCategory.TRADE
    assert str(trade.net_amount_eur) == "-228.554625"


def test_import_skips_duplicates(tmp_path: Path) -> None:
    csv_path = tmp_path / "ibkr.csv"
    csv_path.write_text(SAMPLE_CSV, encoding="utf-8")
    raw_import = parse_ibkr_csv(csv_path)
    connection = connect(tmp_path / "test.sqlite3")

    first = import_raw(connection, raw_import)
    second = import_raw(connection, raw_import)

    assert first.inserted == 4
    assert first.skipped_duplicates == 0
    assert second.inserted == 0
    assert second.skipped_duplicates == 4


def test_reports_and_export(tmp_path: Path) -> None:
    csv_path = tmp_path / "ibkr.csv"
    csv_path.write_text(SAMPLE_CSV, encoding="utf-8")
    connection = connect(tmp_path / "test.sqlite3")
    import_raw(connection, parse_ibkr_csv(csv_path))

    tickers = ticker_summary(connection)
    categories = {row.key: row for row in category_summary(connection)}
    export_path = tmp_path / "report.xlsx"
    export_excel(connection, export_path)

    assert tickers[0].key == "AMZN"
    assert categories["fee"].trade_count == 1
    assert categories["deposit_withdrawal"].trade_count == 1
    assert export_path.exists()
