from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .database import DEFAULT_DB_PATH, connect, import_raw
from .dev_tests import run_pytest
from .git_sync import commit_all_and_push
from .ibkr_parser import IbkrParseError, parse_ibkr_csv
from .restart import spawn_new_uctsimp_instance
from .reports import (
    GROSS_EUR_VYSVETLENIE,
    cashflow_summary,
    category_summary,
    daily_cumulative_net,
    export_excel,
    review_rows,
    tax_split_cashflow,
    ticker_summary,
    yearly_summary,
)


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
        self.test_button = QPushButton("Spusti testy (pytest)")
        self.restart_button = QPushButton("Reštartovať aplikáciu")
        self.test_button.setToolTip(
            "Otvorí okno s výsledkom pytest (nie je to zmena dát v tabuľkách). Hlavné okno sa nemení; "
            "upravený kód v bežacej aplikácii (GUI) uvidíš až po reštarte, ale jadro sa testuje v novom procese."
        )
        self.restart_button.setToolTip(
            "Zatvori toto okno a spusti znova UCTSIMP (najmä po úprave kódu GUI). Nacita sa novy Python modul z disku (editable install)."
        )
        self.import_button.clicked.connect(self.import_csv)
        self.export_button.clicked.connect(self.export_report)
        self.refresh_button.clicked.connect(self.refresh_tables)
        self.github_button.clicked.connect(self.save_to_github)
        self.test_button.clicked.connect(self.run_tests_dialog)
        self.restart_button.clicked.connect(self.restart_application)

        button_row = QHBoxLayout()
        button_row.addWidget(self.import_button)
        button_row.addWidget(self.export_button)
        button_row.addWidget(self.refresh_button)
        button_row.addWidget(self.github_button)
        button_row.addWidget(self.test_button)
        button_row.addWidget(self.restart_button)
        button_row.addStretch()

        self.tabs = QTabWidget()
        self.overview_explain = QLabel()
        self.overview_explain.setWordWrap(True)
        self.overview_explain.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._overview_toggle = QToolButton()
        self._overview_toggle.setText("Vysvetlenie stĺpcov, Gross EUR, daň / nedaň")
        self._overview_toggle.setCheckable(True)
        self._overview_toggle.setChecked(False)
        self._overview_toggle.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self._overview_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self._overview_explain_box = QFrame()
        explain_inner = QVBoxLayout()
        explain_inner.setContentsMargins(8, 4, 8, 8)
        explain_inner.addWidget(self.overview_explain)
        self._overview_explain_box.setLayout(explain_inner)
        self._overview_explain_box.setVisible(False)
        self._overview_toggle.toggled.connect(self._on_overview_explain_toggled)

        self.cashflow_table = QTableWidget()
        self.cashflow_table.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.daily_table = QTableWidget()
        self.ticker_table = QTableWidget()
        self.category_table = QTableWidget()
        self.yearly_table = QTableWidget()
        self.review_table = QTableWidget()
        for _t in (
            self.ticker_table,
            self.category_table,
            self.yearly_table,
            self.review_table,
        ):
            _t.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )

        overview_widget = QWidget()
        overview_layout = QVBoxLayout()
        overview_layout.addWidget(self._overview_toggle)
        overview_layout.addWidget(self._overview_explain_box)
        overview_layout.addWidget(
            QLabel("Príjmy, výdaje, čistý pohyb (Net EUR) — všetky importované transakcie")
        )
        overview_layout.addWidget(self.cashflow_table, stretch=1)
        overview_widget.setLayout(overview_layout)

        daily_widget = QWidget()
        daily_layout = QVBoxLayout()
        daily_layout.addWidget(
            QLabel(
                "Denná zmena a kumulatív Net EUR. Počíta sa len z importu v databáze; "
                "kumulatív nie je stav účtu v TWS, ak chýba úplná história."
            )
        )
        self.daily_table.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        daily_layout.addWidget(self.daily_table, stretch=1)
        daily_widget.setLayout(daily_layout)

        self.tabs.addTab(overview_widget, "Prehlad")
        self.tabs.addTab(daily_widget, "Denný prehľad")
        self.tabs.addTab(self.ticker_table, "Tickery")
        self.tabs.addTab(self.category_table, "Kategorie")
        self.tabs.addTab(self.yearly_table, "Roky")
        self.tabs.addTab(self.review_table, "Na kontrolu")

        layout = QVBoxLayout()
        layout.addLayout(button_row)
        layout.addWidget(self.status_label)
        layout.addWidget(self.tabs, stretch=1)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)
        self.refresh_tables()

    def _on_overview_explain_toggled(self, checked: bool) -> None:
        self._overview_explain_box.setVisible(checked)
        self._overview_toggle.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
        )

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

    def run_tests_dialog(self) -> None:
        self.status_label.setText("Spustam testy (pytest)…")
        QApplication.processEvents()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            try:
                code, output = run_pytest()
            except Exception as exc:
                QMessageBox.critical(
                    self,
                    "Pytest",
                    f"Pri spusteni testu nastala chyba:\n{exc!r}",
                )
                self.status_label.setText("Testy: chyba spustenia (pozri dialog)")
                return
        finally:
            QApplication.restoreOverrideCursor()

        text = (output or "").strip()
        if not text:
            text = (
                "Prazdny vystup. Skontroluj, ci existuje priečinok `tests` v koreni projektu UCTSIMP "
                "a ci je nainstalovany `pytest` v `.venv`."
            )

        box = QMessageBox(self)
        box.setWindowTitle("Vysledok pytest")
        if code == 0:
            box.setIcon(QMessageBox.Icon.Information)
            box.setText("Testy prebehli v poriadku (exit 0).")
        else:
            box.setIcon(QMessageBox.Icon.Critical)
            box.setText(f"Testy skoncili s chybou (exit {code}).")
        box.setInformativeText(
            "Tabuľky v okne sa nemenia — tlačidlo len spusti automaticky pytest a ukaze log. "
            "Celý text výstupu: tlačidlo 'Show Details' / 'Zobraziť podrobnosti' (podľa jazyka systému)."
        )
        box.setDetailedText(text)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()
        self.status_label.setText(
            f"Posledne testy: {'OK' if code == 0 else f'chyba (exit {code})'}. "
            f"Databaza: {DEFAULT_DB_PATH}"
        )

    def restart_application(self) -> None:
        reply = QMessageBox.question(
            self,
            "Reštart UCTSIMP",
            "Aktuálne okno sa zatvorí a spustí sa nová inštancia programu (rovnaký príkaz ako teraz, "
            "zvyčajne v tom istom venv). Po úprave kódu GUI tak môžete načítať zmeny bez ručného vypnutia.\n\n"
            "Pokračovať?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        def _go() -> None:
            try:
                spawn_new_uctsimp_instance()
            except OSError as exc:
                QMessageBox.critical(
                    self,
                    "Reštart",
                    f"Nepodarilo sa spustiť novú inštanciu:\n{exc!s}",
                )
                return
            QApplication.instance().quit()

        QTimer.singleShot(200, _go)

    def refresh_tables(self) -> None:
        self.overview_explain.setText(GROSS_EUR_VYSVETLENIE.replace("**", ""))
        _fill_cashflow_table(
            self.cashflow_table,
            cashflow_summary(self.connection),
            tax_split_cashflow(self.connection),
        )
        _fill_daily_table(self.daily_table, daily_cumulative_net(self.connection))
        _fill_summary_table(self.ticker_table, ticker_summary(self.connection))
        _fill_summary_table(self.category_table, category_summary(self.connection))
        _fill_summary_table(self.yearly_table, yearly_summary(self.connection))
        _fill_review_table(self.review_table, review_rows(self.connection))


def _fill_cashflow_table(table: QTableWidget, cash, tax) -> None:
    table.setColumnCount(2)
    table.setHorizontalHeaderLabels(["Polozka", "EUR"])
    rows = [
        ("Celkovo: Prijem (kl. Net EUR)", cash.prijem_eur),
        ("Celkovo: Vydaj (|zap. Net EUR|)", cash.vydaj_eur),
        ("Celkovo: Cisty pohyb", cash.cisty_pohyb_eur),
        ("---", ""),
        (
            "Danovy: Prijem (ostatne + FIFO realiz. z obchodov)",
            tax.prijem_danovy_eur,
        ),
        (
            "Danovy: Vydaj (ostatne + |FIFO zapor. realiz.|)",
            tax.vydaj_danovy_eur,
        ),
        ("Danovy: Cisty (ostatne Net + suhrn FIFO obchodov)", tax.cisty_danovy_eur),
        ("---", ""),
        ("Nedanovy: Prijem (vklady...)", tax.prijem_nedanovy_eur),
        ("Nedanovy: Vydaj (vybery...)", tax.vydaj_nedanovy_eur),
        ("Nedanovy: Cisty (suma Net v neda. kat.)", tax.cisty_nedanovy_eur),
    ]
    table.setRowCount(len(rows))
    for i, (label, val) in enumerate(rows):
        table.setItem(i, 0, QTableWidgetItem(label))
        if label == "---":
            item = QTableWidgetItem("")
        else:
            item = QTableWidgetItem(str(val) if val != "" else "")
        if label != "---":
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        table.setItem(i, 1, item)
    table.resizeColumnsToContents()


def _fill_daily_table(table: QTableWidget, rows) -> None:
    table.setColumnCount(3)
    table.setHorizontalHeaderLabels(["Den", "Denna zmena EUR", "Kumulativ EUR"])
    table.setRowCount(len(rows))
    for i, r in enumerate(rows):
        table.setItem(i, 0, QTableWidgetItem(r.obchodny_den))
        d = QTableWidgetItem(str(r.denna_zmena_eur))
        d.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        table.setItem(i, 1, d)
        k = QTableWidgetItem(str(r.kumulativ_eur))
        k.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        table.setItem(i, 2, k)
    table.resizeColumnsToContents()


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
