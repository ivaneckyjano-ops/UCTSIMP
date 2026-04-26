"""Spustenie pytest z koreňa projektu (vývoj — netreba reštartovať GUI na manuálny beh)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .git_sync import project_root


def run_pytest() -> tuple[int, str]:
    """
    Spustí `pytest` v adresári projektu (kde je `tests/` a `pyproject.toml`).
    Vráti (exit kód, zlúčený stdout+stderr text).
    """
    root = project_root()
    tests_dir = root / "tests"
    if not tests_dir.is_dir():
        return 127, f"Nenasiel sa priečinok testov: {tests_dir}\n"

    env = os.environ.copy()
    src = str(root / "src")
    prev = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src if not prev else f"{src}{os.pathsep}{prev}"

    venv_python = root / ".venv" / "bin" / "python"
    python = str(venv_python) if venv_python.is_file() else sys.executable
    cmd = [python, "-m", "pytest", "-v", "--tb=short", "tests"]

    try:
        proc = subprocess.run(
            cmd,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=600,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return 124, f"Timeout po 10 min. Cast vystupu:\n{exc.stdout or ''}\n{exc.stderr or ''}"
    except OSError as exc:
        return 125, f"Nepodarilo sa spustit pytest: {exc!r}\nPrikaz: {cmd!r}\nCwd: {root}"

    out = proc.stdout or ""
    err = proc.stderr or ""
    combined = out
    if err.strip():
        combined = f"{out}\n--- stderr ---\n{err}"
    return proc.returncode, combined
