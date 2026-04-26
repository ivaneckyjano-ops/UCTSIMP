from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

from .models import SummaryRow


def ticker_summary(connection: sqlite3.Connection) -> list[SummaryRow]:
    return _summary(
        connection,
        """
        SELECT COALESCE(ticker, 'Bez tickera') AS key,
               COUNT(*) AS trade_count,
               SUM(CAST(gross_amount_eur AS REAL)) AS gross_eur,
               SUM(CAST(commission_eur AS REAL)) AS commission_eur,
               SUM(CAST(net_amount_eur AS REAL)) AS net_eur
        FROM transactions
        WHERE category = 'trade'
        GROUP BY COALESCE(ticker, 'Bez tickera')
        ORDER BY key
        """,
    )


def category_summary(connection: sqlite3.Connection) -> list[SummaryRow]:
    return _summary(
        connection,
        """
        SELECT category AS key,
               COUNT(*) AS trade_count,
               SUM(CAST(gross_amount_eur AS REAL)) AS gross_eur,
               SUM(CAST(commission_eur AS REAL)) AS commission_eur,
               SUM(CAST(net_amount_eur AS REAL)) AS net_eur
        FROM transactions
        GROUP BY category
        ORDER BY category
        """,
    )


def yearly_summary(connection: sqlite3.Connection) -> list[SummaryRow]:
    return _summary(
        connection,
        """
        SELECT substr(trade_date, 1, 4) AS key,
               COUNT(*) AS trade_count,
               SUM(CAST(gross_amount_eur AS REAL)) AS gross_eur,
               SUM(CAST(commission_eur AS REAL)) AS commission_eur,
               SUM(CAST(net_amount_eur AS REAL)) AS net_eur
        FROM transactions
        GROUP BY substr(trade_date, 1, 4)
        ORDER BY key
        """,
    )


def review_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT trade_date, description, symbol, transaction_type, category,
                   net_amount_eur
            FROM transactions
            WHERE needs_review = 1
            ORDER BY trade_date, id
            """
        )
    )


def export_excel(connection: sqlite3.Connection, path: str | Path) -> None:
    from openpyxl import Workbook

    workbook = Workbook()
    summary_sheet = workbook.active
    summary_sheet.title = "Suhrn"
    _write_summary_block(summary_sheet, "Podla tickerov", ticker_summary(connection), 1)
    _write_summary_block(summary_sheet, "Podla kategorii", category_summary(connection), 1 + len(ticker_summary(connection)) + 4)
    _write_summary_block(summary_sheet, "Podla rokov", yearly_summary(connection), 1 + len(ticker_summary(connection)) + len(category_summary(connection)) + 8)

    transaction_sheet = workbook.create_sheet("Transakcie")
    transaction_headers = [
        "Datum",
        "Ticker",
        "Symbol",
        "Popis",
        "Typ",
        "Kategoria",
        "Mnozstvo",
        "Cena",
        "Mena ceny",
        "Gross EUR",
        "Komisia EUR",
        "Net EUR",
        "Na kontrolu",
    ]
    transaction_sheet.append(transaction_headers)
    for row in connection.execute(
        """
        SELECT trade_date, ticker, symbol, description, transaction_type, category,
               quantity, price, price_currency, gross_amount_eur, commission_eur,
               net_amount_eur, needs_review
        FROM transactions
        ORDER BY trade_date, id
        """
    ):
        transaction_sheet.append(
            [
                row["trade_date"],
                row["ticker"],
                row["symbol"],
                row["description"],
                row["transaction_type"],
                row["category"],
                _number_or_text(row["quantity"]),
                _number_or_text(row["price"]),
                row["price_currency"],
                _number_or_text(row["gross_amount_eur"]),
                _number_or_text(row["commission_eur"]),
                _number_or_text(row["net_amount_eur"]),
                "ano" if row["needs_review"] else "",
            ]
        )

    review_sheet = workbook.create_sheet("Na kontrolu")
    review_sheet.append(["Datum", "Popis", "Symbol", "Typ", "Kategoria", "Net EUR"])
    for row in review_rows(connection):
        review_sheet.append(
            [
                row["trade_date"],
                row["description"],
                row["symbol"],
                row["transaction_type"],
                row["category"],
                _number_or_text(row["net_amount_eur"]),
            ]
        )

    for sheet in workbook.worksheets:
        for column_cells in sheet.columns:
            max_length = max(len(str(cell.value or "")) for cell in column_cells)
            sheet.column_dimensions[column_cells[0].column_letter].width = min(max_length + 2, 60)

    workbook.save(path)


def _summary(connection: sqlite3.Connection, sql: str) -> list[SummaryRow]:
    rows = connection.execute(sql).fetchall()
    return [
        SummaryRow(
            key=row["key"],
            trade_count=int(row["trade_count"]),
            gross_eur=_decimal_from_sql(row["gross_eur"]),
            commission_eur=_decimal_from_sql(row["commission_eur"]),
            net_eur=_decimal_from_sql(row["net_eur"]),
        )
        for row in rows
    ]


def _write_summary_block(
    sheet, title: str, rows: list[SummaryRow], start_row: int
) -> None:
    sheet.cell(row=start_row, column=1, value=title)
    headers = ["Kluc", "Pocet", "Gross EUR", "Komisia EUR", "Net EUR"]
    for index, header in enumerate(headers, start=1):
        sheet.cell(row=start_row + 1, column=index, value=header)
    for row_index, row in enumerate(rows, start=start_row + 2):
        sheet.cell(row=row_index, column=1, value=row.key)
        sheet.cell(row=row_index, column=2, value=row.trade_count)
        sheet.cell(row=row_index, column=3, value=float(row.gross_eur))
        sheet.cell(row=row_index, column=4, value=float(row.commission_eur))
        sheet.cell(row=row_index, column=5, value=float(row.net_eur))


def _decimal_from_sql(value) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value)).quantize(Decimal("0.000001"))


def _number_or_text(value: str | None):
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return value
