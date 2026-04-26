from __future__ import annotations

import sys
from functools import partial
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
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

from .database import (
    APP_DIR,
    DATA_DIR,
    backup_year_database,
    clear_all_data,
    connect_for_year,
    import_raw,
    list_years_on_disk,
    load_active_year,
    migrate_legacy_to_per_year,
    path_for_year,
    restore_year_from_file,
    save_active_year,
)
from .dev_tests import run_pytest
from .version_info import get_package_version
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
    tax_danove_rozpis,
    tax_split_cashflow,
    ticker_summary,
    yearly_summary,
)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self._app_version = get_package_version()
        self.resize(1000, 700)

        mig_msg = migrate_legacy_to_per_year()
        yrs = list_years_on_disk()
        self._active_year = load_active_year()
        if yrs and self._active_year not in yrs:
            self._active_year = max(yrs)
        save_active_year(self._active_year)
        self.connection = connect_for_year(self._active_year)

        self.setWindowTitle(
            f"UCTSIMP {self._app_version} – {self._active_year} – IBKR dane"
        )
        if mig_msg:
            QTimer.singleShot(
                0,
                partial(
                    QMessageBox.information,
                    self,
                    "Migrácia databázy",
                    mig_msg,
                ),
            )

        self.status_label = QLabel()
        self.import_button = QPushButton("Importovat IBKR CSV")
        self.export_button = QPushButton("Exportovat Excel")
        self.refresh_button = QPushButton("Obnovit")
        self.github_button = QPushButton("Ulozit do GitHub (git push)")
        self.test_button = QPushButton("Spusti testy (pytest)")
        self.restart_button = QPushButton("Reštartovať aplikáciu")
        self.clear_data_button = QPushButton("Vymazať dáta pre rok")
        self.clear_data_button.setToolTip(
            f"Zmaže všetky transakcie len v súbore pre zvolený rok (priečinok: {DATA_DIR}). "
            "Ostatné roky ostanú nedotknuté. Súbory CSV/PDF na disku sa nemenia."
        )
        self.about_button = QPushButton("O programe")
        self.about_button.clicked.connect(self.show_about)
        self.clear_data_button.clicked.connect(self.clear_imported_data)
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
        button_row.addWidget(self.clear_data_button)
        button_row.addWidget(self.github_button)
        button_row.addWidget(self.test_button)
        button_row.addWidget(self.restart_button)
        button_row.addWidget(self.about_button)
        button_row.addStretch()

        year_row = QHBoxLayout()
        year_row.addWidget(QLabel("Rok dát:"))
        self._year_combo = QComboBox()
        self._year_combo.setMinimumWidth(110)
        self._year_combo.activated.connect(self._on_year_combo_activated)
        year_row.addWidget(self._year_combo)
        self._new_year_btn = QPushButton("Nový rok…")
        self._new_year_btn.setToolTip(
            "Otvorí alebo vytvorí samostatnú SQLite databázu pre zadaný rok (jeden súbor = jeden rok)."
        )
        self._new_year_btn.clicked.connect(self._add_year_dialog)
        year_row.addWidget(self._new_year_btn)
        self._backup_btn = QPushButton("Zálohovať rok…")
        self._backup_btn.setToolTip("Skopíruje .sqlite3 súbor aktuálne zvoleného roka (záloha na iný disk/USB).")
        self._backup_btn.clicked.connect(self._backup_current_year)
        year_row.addWidget(self._backup_btn)
        self._restore_btn = QPushButton("Obnoviť rok…")
        self._restore_btn.setToolTip(
            "Prepíše databázu zvoleného roka kópiou zo záložného .sqlite3 súboru. Aktuálny súbor bude stratený."
        )
        self._restore_btn.clicked.connect(self._restore_current_year)
        year_row.addWidget(self._restore_btn)
        year_row.addStretch()

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
        self._tax_danove_toggle = QToolButton()
        self._tax_danove_toggle.setText(
            "Rozpis daňových príjmov a výdajov (dátum, suma, zdroj — kontrola / párovanie)"
        )
        self._tax_danove_toggle.setCheckable(True)
        self._tax_danove_toggle.setChecked(False)
        self._tax_danove_toggle.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self._tax_danove_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self._tax_prijem_table = QTableWidget()
        self._tax_vydaj_table = QTableWidget()
        for _t in (self._tax_prijem_table, self._tax_vydaj_table):
            _t.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
        _tax_rozpis_tabs = QTabWidget()
        _tax_rozpis_tabs.addTab(self._tax_prijem_table, "Príjmy (daň)")
        _tax_rozpis_tabs.addTab(self._tax_vydaj_table, "Výdaje (daň)")
        _tax_danove_inner = QVBoxLayout()
        _tax_danove_inner.setContentsMargins(8, 4, 8, 8)
        _tax_danove_inner.addWidget(_tax_rozpis_tabs)
        self._tax_danove_box = QFrame()
        self._tax_danove_box.setLayout(_tax_danove_inner)
        self._tax_danove_box.setVisible(False)
        self._tax_danove_toggle.toggled.connect(self._on_tax_danove_toggled)
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
        overview_layout.addWidget(self._tax_danove_toggle)
        overview_layout.addWidget(self._tax_danove_box)
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
        layout.addLayout(year_row)
        layout.addWidget(self.status_label)
        layout.addWidget(self.tabs, stretch=1)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)
        self._sync_year_combo()
        self._set_status_line(
            f"Rok {self._active_year}: {self._current_db_path()} · Koreň: {APP_DIR}"
        )
        self.refresh_tables()

    def _set_status_line(self, detail: str) -> None:
        self.status_label.setText(f"Verzia {self._app_version} · {detail}")

    def _current_db_path(self) -> Path:
        return path_for_year(self._active_year)

    def _sync_year_combo(self) -> None:
        self._year_combo.blockSignals(True)
        self._year_combo.clear()
        years = set(list_years_on_disk())
        years.add(self._active_year)
        for y in sorted(years):
            self._year_combo.addItem(str(y), y)
        idx = self._year_combo.findData(self._active_year)
        if idx >= 0:
            self._year_combo.setCurrentIndex(idx)
        self._year_combo.blockSignals(False)
        self.setWindowTitle(
            f"UCTSIMP {self._app_version} – {self._active_year} – IBKR dane"
        )

    def _on_year_combo_activated(self, index: int) -> None:
        data = self._year_combo.itemData(index)
        if data is None:
            return
        y = int(data)
        if y == self._active_year:
            return
        self._active_year = y
        save_active_year(y)
        self.connection.close()
        self.connection = connect_for_year(y)
        self.refresh_tables()
        self._set_status_line(
            f"Rok {y}: {self._current_db_path()}"
        )

    def _add_year_dialog(self) -> None:
        y, ok = QInputDialog.getInt(
            self,
            "Nový rok",
            "Kalendárny rok (vytvorí alebo otvorí samostatnú databázu):",
            self._active_year,
            2000,
            2100,
            1,
        )
        if not ok:
            return
        self._active_year = y
        save_active_year(y)
        self.connection.close()
        self.connection = connect_for_year(y)
        self._sync_year_combo()
        self.refresh_tables()
        self._set_status_line(
            f"Rok {y}: {self._current_db_path()}"
        )

    def _backup_current_year(self) -> None:
        src = self._current_db_path()
        if not src.is_file():
            QMessageBox.warning(
                self,
                "Záloha",
                "Súbor databázy pre tento rok ešte neexistuje (žiadne dáta).",
            )
            return
        dest, _ = QFileDialog.getSaveFileName(
            self,
            "Uložiť kópiu SQLite",
            str(Path.home() / f"uctsimp_{self._active_year}_zaloha.sqlite3"),
            "SQLite (*.sqlite3);;Všetky (*)",
        )
        if not dest:
            return
        try:
            backup_year_database(src, Path(dest))
        except OSError as exc:
            QMessageBox.critical(self, "Záloha", str(exc))
            return
        QMessageBox.information(self, "Záloha", f"Uložené do:\n{dest}")

    def _restore_current_year(self) -> None:
        reply = QMessageBox.warning(
            self,
            "Obnoviť databázu roka",
            f"Aktuálna databáza pre rok {self._active_year} bude nahradená obsahom vybraného súboru.\n\n"
            f"Cieľ: {self._current_db_path()}\n\n"
            "Najprv môžete zvoliť „Zálohovať rok…“ (odporúčané).",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        src, _ = QFileDialog.getOpenFileName(
            self,
            "Záložný súbor SQLite",
            str(Path.home()),
            "SQLite (*.sqlite3);;Všetky (*)",
        )
        if not src:
            return
        self.connection.close()
        try:
            restore_year_from_file(Path(src), self._active_year)
        except OSError as exc:
            self.connection = connect_for_year(self._active_year)
            QMessageBox.critical(self, "Obnova", str(exc))
            self.refresh_tables()
            return
        self.connection = connect_for_year(self._active_year)
        self._sync_year_combo()
        self.refresh_tables()
        self._set_status_line(
            f"Rok {self._active_year} obnovený z: {src}"
        )
        QMessageBox.information(
            self,
            "Obnova",
            f"Dáta pre rok {self._active_year} boli načítané zo zálohy.",
        )

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            "O programe UCTSIMP",
            f"<p><b>UCTSIMP</b> {self._app_version}</p>"
            "<p>Import IBKR CSV (vrátane Zrealizovaného súhrnu), prehľady a export pre daňové podklady.</p>"
            f"<p>Každý kalendárny rok je v samostatnom súbore (priečinok <code>{DATA_DIR}</code>); "
            f"zálohovanie a obnova v hornej lište.</p>"
            "<p>Python / PySide6</p>",
        )

    def clear_imported_data(self) -> None:
        reply = QMessageBox.warning(
            self,
            f"Vymazať dáta pre rok {self._active_year}",
            "Z aktuálneho ročného súboru sa odstránia všetky transakcie a záznamy o importoch. "
            "Ostatné roky ostanú v ich vlastných súboroch zmenené.\n\n"
            f"Súbor: {self._current_db_path()}\n\n"
            "Aplikácia sa tým neodinštaluje. Túto zmenu v rámci roka nie je možné vrátiť. "
            "Súbory CSV a PDF na disku ostanú nedotknuté.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        n_tx, n_files = clear_all_data(self.connection)
        self.refresh_tables()
        self._set_status_line(
            f"Rok {self._active_year} vyčistený: {n_tx} transakcií, {n_files} importov. {self._current_db_path()}"
        )
        QMessageBox.information(
            self,
            "Dáta vymazané",
            f"Hotovo. Pre rok {self._active_year} bolo odstránených {n_tx} transakcií a {n_files} záznamov o súboroch.\n\n"
            "Môžete spustiť čistý import (napr. Zrealizovaný súhrn).",
        )

    def _on_overview_explain_toggled(self, checked: bool) -> None:
        self._overview_explain_box.setVisible(checked)
        self._overview_toggle.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
        )

    def _on_tax_danove_toggled(self, checked: bool) -> None:
        self._tax_danove_box.setVisible(checked)
        self._tax_danove_toggle.setArrowType(
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

        self._set_status_line(
            "Import hotový: "
            f"{result.inserted} nových, "
            f"{result.skipped_duplicates} duplicít, "
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
        self._set_status_line("Spúšťam testy (pytest)…")
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
                self._set_status_line("Testy: chyba spustenia (pozri dialog)")
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
        self._set_status_line(
            f"Posledné testy: {'OK' if code == 0 else f'chyba (exit {code})'}. "
            f"Rok {self._active_year}: {self._current_db_path()}"
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
        _fill_tax_rozpis(
            self._tax_prijem_table,
            self._tax_vydaj_table,
            tax_danove_rozpis(self.connection),
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


def _fill_tax_rozpis(
    prijem_table: QTableWidget,
    vydaj_table: QTableWidget,
    pair: tuple,
) -> None:
    p_rows, v_rows = pair
    headers = ["ID", "Dátum", "Suma EUR", "Popis", "Zdroj"]
    for table, lines in ((prijem_table, p_rows), (vydaj_table, v_rows)):
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setRowCount(len(lines))
        for i, x in enumerate(lines):
            table.setItem(i, 0, QTableWidgetItem(str(x.transaction_id)))
            table.setItem(i, 1, QTableWidgetItem(x.trade_date))
            a = QTableWidgetItem(str(x.amount_eur))
            a.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(i, 2, a)
            table.setItem(i, 3, QTableWidgetItem(x.description))
            table.setItem(i, 4, QTableWidgetItem(x.source))
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
