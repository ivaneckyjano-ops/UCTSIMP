from __future__ import annotations

from pathlib import Path

import pytest

from uctsimp.database import (
    clear_all_data,
    connect,
    path_for_year,
    save_active_year,
)


def test_path_for_year_naming(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = tmp_path / "data"

    def _ens() -> Path:
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr("uctsimp.database.ensure_data_dir", _ens)
    monkeypatch.setattr("uctsimp.database.DATA_DIR", d)
    p = path_for_year(2026)
    assert p == d / "uctsimp_2026.sqlite3"


def test_connect_custom_path_roundtrip(tmp_path: Path) -> None:
    db = tmp_path / "custom.sqlite3"
    conn = connect(db)
    n1, n2 = clear_all_data(conn)
    assert n1 == 0 and n2 == 0
    conn.close()


def test_save_active_year_writes_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = tmp_path / "data"
    d.mkdir()
    monkeypatch.setattr("uctsimp.database.DATA_DIR", d)
    monkeypatch.setattr("uctsimp.database.ensure_data_dir", lambda: d)
    monkeypatch.setattr("uctsimp.database.APP_DIR", tmp_path)
    monkeypatch.setattr("uctsimp.database.SETTINGS_PATH", tmp_path / "settings.json")
    save_active_year(2025)
    assert (tmp_path / "settings.json").read_text(encoding="utf-8").find("2025") >= 0
