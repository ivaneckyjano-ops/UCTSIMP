"""Jednotné načítanie verzie: pyproject v repozitári, potom importlib, potom záloha."""

from __future__ import annotations

import tomllib
from pathlib import Path

# Záloha, ak nič iné nejde.
_FALLBACK: str = "0.2.0"


def get_package_version() -> str:
    here = Path(__file__).resolve()
    for anc in (here, *here.parents):
        pr = anc / "pyproject.toml"
        if not pr.is_file():
            continue
        try:
            with pr.open("rb") as f:
                data = tomllib.load(f)
        except OSError:
            continue
        proj = data.get("project", {})
        if proj.get("name") != "uctsimp":
            continue
        v = proj.get("version")
        if v:
            return str(v)
    try:
        from importlib.metadata import version

        return version("uctsimp")
    except Exception:
        return _FALLBACK
