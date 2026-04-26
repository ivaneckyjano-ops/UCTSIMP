from __future__ import annotations

import subprocess
from pathlib import Path

DEFAULT_REMOTE = "git@github.com:ivaneckyjano-ops/UCTSIMP.git"
DEFAULT_BRANCH = "main"


def project_root() -> Path:
    """Koreň repozitára: .../UCTSIMP (kde leží priečinok `src/uctsimp`)."""
    return Path(__file__).resolve().parent.parent.parent


def is_git_repo(root: Path) -> bool:
    return (root / ".git").is_dir()


def _run_git(
    args: list[str], cwd: Path, timeout: int = 120
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def ensure_repo_and_remote(
    root: Path | None = None, remote_url: str = DEFAULT_REMOTE, remote_name: str = "origin"
) -> tuple[bool, str]:
    """Vytvorí `git` repozitár ak chýba a nastaví `origin` na zadané URL."""
    r = project_root() if root is None else root.resolve()
    out_lines: list[str] = []

    if not is_git_repo(r):
        p = _run_git(["init"], r)
        if p.returncode != 0:
            return False, (p.stdout or "") + (p.stderr or "")
        out_lines.append("Inicializovany lokalny git repozitar.")

    p = _run_git(["remote", "get-url", remote_name], r)
    if p.returncode != 0:
        p = _run_git(["remote", "add", remote_name, remote_url], r)
        if p.returncode != 0:
            return False, (p.stdout or "") + (p.stderr or "")
        out_lines.append(f"Pridany remote {remote_name} -> {remote_url}")
    else:
        current = (p.stdout or "").strip()
        if current != remote_url:
            p = _run_git(["remote", "set-url", remote_name, remote_url], r)
            if p.returncode != 0:
                return False, (p.stdout or "") + (p.stderr or "")
            out_lines.append(f"Upraveny remote {remote_name}: {current!r} -> {remote_url!r}")
        else:
            out_lines.append(f"Remote {remote_name} uz ukazuje na {remote_url}")

    return True, "\n".join(out_lines) if out_lines else "OK"


def commit_all_and_push(
    message: str,
    root: Path | None = None,
    remote_url: str = DEFAULT_REMOTE,
    branch: str = DEFAULT_BRANCH,
) -> tuple[bool, str]:
    """
    `git add -A`, commit (ak sú zmeny), pomenovanie vetvy, `push` na `origin/branch`.
    Vráti (úspech, text výstupu / chyby).
    """
    r = project_root() if root is None else root.resolve()
    if not message.strip():
        return False, "Chybna commit sprava."

    ok, msg = ensure_repo_and_remote(r, remote_url=remote_url)
    if not ok:
        return False, msg

    lines: list[str] = [msg]

    st = _run_git(["status", "--porcelain"], r)
    if st.returncode != 0:
        return False, (st.stdout or "") + (st.stderr or "")

    if (st.stdout or "").strip():
        add = _run_git(["add", "-A"], r)
        if add.returncode != 0:
            return False, f"git add: {(add.stdout or '') + (add.stderr or '')}"
        c = _run_git(["commit", "-m", message.strip()], r)
        combined = (c.stdout or "") + (c.stderr or "")
        lines.append(combined)
        if c.returncode != 0 and "nothing to commit" not in combined.lower():
            return False, "\n".join(lines)
    else:
        log1 = _run_git(["log", "-1", "--oneline"], r)
        lines.append("Nie su zmeny v pracovnom strome (commit preskoceny).")
        if log1.returncode == 0 and (log1.stdout or "").strip():
            lines.append("Posledny commit: " + (log1.stdout or "").strip())
        has_any = _run_git(["rev-parse", "--verify", "HEAD"], r)
        if has_any.returncode != 0:
            return (
                False,
                "\n".join(lines) + "\nNie je co pushnut – este nebol ziaden commit. Pridaj subory a skus znova.",
            )

    # Aktualna vetva sa premenuje na ciel (bez nutnosti vytvarat novu a prepinat sa)
    br = _run_git(["branch", "-M", branch], r, timeout=30)
    lines.append((br.stdout or "") + (br.stderr or ""))
    if br.returncode != 0:
        return False, "\n".join(lines)

    push = _run_git(
        ["push", "-u", "origin", branch],
        r,
        timeout=300,
    )
    out = (push.stdout or "") + (push.stderr or "")
    lines.append(out)
    if push.returncode != 0:
        return False, "\n".join(lines)
    return True, "\n".join(lines)
