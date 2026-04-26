from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .database import DEFAULT_DB_PATH, connect, import_raw
from .git_sync import commit_all_and_push
from .ibkr_parser import IbkrParseError, parse_ibkr_csv
from .reports import category_summary, export_excel, review_rows, ticker_summary, yearly_summary


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("UCTSIMP - IBKR dane")
        self.resize(1000, 700)
        self.connection = connect()

        self.status_label = QLabel(f"Databaza: {DEFAULT_DB_PATH}")
        self.import_button = QPushButton("Importovat IBKR CSV")
        self.export_button = QPushButton("Exportovat Excel")
        self.refresh_button = QPushButton("Obnovit")
        self.github_button = QPushButton("Ulozit do GitHub (git push)")
        self.import_button.clicked.connect(self.import_csv)
        self.export_button.clicked.connect(self.export_report)
        self.refresh_button.clicked.connect(self.refresh_tables)
        self.github_button.clicked.connect(self.save_to_github)

        button_row = QHBoxLayout()
        button_row.addWidget(self.import_button)
        button_row.addWidget(self.export_button)
        button_row.addWidget(self.refresh_button)
        button_row.addWidget(self.github_button)
        button_row.addStretch()

        self.tabs = QTabWidget()
        self.ticker_table = QTableWidget()
        self.category_table = QTableWidget()
        self.yearly_table = QTableWidget()
        self.review_table = QTableWidget()
        self.tabs.addTab(self.ticker_table, "Tickery")
        self.tabs.addTab(self.category_table, "Kategorie")
        self.tabs.addTab(self.yearly_table, "Roky")
        self.tabs.addTab(self.review_table, "Na kontrolu")

        layout = QVBoxLayout()
        layout.addLayout(button_row)
        layout.addWidget(self.status_label)
        layout.addWidget(self.tabs)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)
        self.refresh_tables()

    def import_csv(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "Vyber IBKR CSV",
            str(Path.home()),
            "CSV subory (*.csv);;Vsetky subory (*)",
        )
        if not file_name:
            return

        try:
            raw_import = parse_ibkr_csv(file_name)
            result = import_raw(self.connection, raw_import)
        except (OSError, IbkrParseError, ValueError) as exc:
            QMessageBox.critical(self, "Import zlyhal", str(exc))
            return

        self.status_label.setText(
            "Import hotovy: "
            f"{result.inserted} novych, "
            f"{result.skipped_duplicates} duplicit, "
            f"{result.total_rows} riadkov."
        )
        self.refresh_tables()

    def export_report(self) -> None:
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "Ulozit Excel report",
            str(Path.home() / "ibkr-danovy-report.xlsx"),
            "Excel subory (*.xlsx)",
        )
        if not file_name:
            return

        try:
            export_excel(self.connection, file_name)
        except OSError as exc:
            QMessageBox.critical(self, "Export zlyhal", str(exc))
            return
        QMessageBox.information(self, "Export hotovy", f"Report ulozeny: {file_name}")

    def save_to_github(self) -> None:
        text, ok = QInputDialog.getText(
            self,
            "Git commit a push",
            "Sprava pre commit (odosle sa na origin):",
        )
        if not ok:
            return
        message = (text or "").strip() or "UCTSIMP: zmeny"
        try:
            success, output = commit_all_and_push(message)
        except OSError as exc:
            QMessageBox.critical(self, "Git", str(exc))
            return
        box = QMessageBox(self)
        box.setWindowTitle("Git" if success else "Git zlyhalo")
        if success:
            box.setIcon(QMessageBox.Information)
            box.setText("Kod sa ulozil a pushol na GitHub (ak SSH funguje).")
        else:
            box.setIcon(QMessageBox.Critical)
            box.setText("Ulozenie do Gitu zlyhalo. Otvor detail.")
        box.setDetailedText(output)
        box.exec()

    def refresh_tables(self) -> None:
        _fill_summary_table(self.ticker_table, ticker_summary(self.connection))
        _fill_summary_table(self.category_table, category_summary(self.connection))
        _fill_summary_table(self.yearly_table, yearly_summary(self.connection))
        _fill_review_table(self.review_table, review_rows(self.connection))


def _fill_summary_table(table: QTableWidget, rows) -> None:
    headers = ["Kluc", "Pocet", "Gross EUR", "Komisia EUR", "Net EUR"]
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setRowCount(len(rows))
    for row_index, row in enumerate(rows):
        values = [
            row.key,
            row.trade_count,
            row.gross_eur,
            row.commission_eur,
            row.net_eur,
        ]
        for column_index, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            if column_index > 0:
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(row_index, column_index, item)
    table.resizeColumnsToContents()


def _fill_review_table(table: QTableWidget, rows) -> None:
    headers = ["Datum", "Popis", "Symbol", "Typ", "Kategoria", "Net EUR"]
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setRowCount(len(rows))
    for row_index, row in enumerate(rows):
        values = [
            row["trade_date"],
            row["description"],
            row["symbol"],
            row["transaction_type"],
            row["category"],
            row["net_amount_eur"],
        ]
        for column_index, value in enumerate(values):
            item = QTableWidgetItem("" if value is None else str(value))
            if column_index == 5:
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(row_index, column_index, item)
    table.resizeColumnsToContents()


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
