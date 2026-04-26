"""Reštart celej desktop aplikácie (nový proces); po úprave kódu sa načíta nové GUI."""

from __future__ import annotations

import os
import subprocess
import sys

from .git_sync import project_root


def spawn_new_uctsimp_instance() -> None:
    """
    Spustí `python -m uctsimp` s rovnakým interpretom a argumentmi ako tento beh.
    Volaj až chvíľu pred ukončením `QApplication`, aby bežač nového procesu prebehol.
    """
    args: list[str] = [sys.executable, "-m", "uctsimp", *sys.argv[1:]]
    root = str(project_root())
    env = os.environ.copy()
    if sys.platform == "win32":
        subprocess.Popen(
            args,
            cwd=root,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    else:
        subprocess.Popen(
            args,
            cwd=root,
            env=env,
            start_new_session=True,
        )
