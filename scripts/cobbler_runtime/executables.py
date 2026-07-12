"""Resolve optional agent CLIs without depending on a freshly reloaded shell.

Several official installers place binaries in user-local directories and then
append those directories to a shell profile. Long-running hosts such as Codex
and Claude Code do not inherit that profile change until they restart. Keep
Elves discovery and launch behavior aligned by checking the small set of
well-known installer directories after the inherited ``PATH``.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


USER_BIN_RELATIVE_DIRS: tuple[Path, ...] = (
    Path(".local/bin"),
    Path(".opencode/bin"),
    Path(".grok/bin"),
    Path("bin"),
)


def _is_executable_file(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def resolve_executable(
    name: str | None,
    *,
    repo_root: Path | None = None,
    path: str | None = None,
    home: Path | None = None,
) -> str | None:
    """Return an executable path from explicit, repo, PATH, or user-bin lookup.

    ``path`` and ``home`` are injectable so deterministic tests do not depend on
    the developer machine. Relative paths containing a slash remain scoped to
    ``repo_root``; bare command names may use known user installer directories.
    """
    if not name or name.startswith("("):
        return None
    raw = str(name).strip()
    if not raw:
        return None

    if home is not None and raw == "~":
        candidate = Path(home)
    elif home is not None and raw.startswith("~/"):
        candidate = Path(home) / raw[2:]
    else:
        candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        return str(candidate) if _is_executable_file(candidate) else None

    if repo_root is not None and ("/" in raw or raw.startswith(".")):
        under_root = Path(repo_root) / raw
        if _is_executable_file(under_root):
            return str(under_root.resolve())

    inherited = shutil.which(raw, path=path)
    if inherited:
        return inherited

    if "/" in raw:
        return None
    home_dir = Path(home) if home is not None else Path.home()
    for relative_dir in USER_BIN_RELATIVE_DIRS:
        user_candidate = home_dir / relative_dir / raw
        if _is_executable_file(user_candidate):
            return str(user_candidate.resolve())
    return None


def resolve_executable_for_launch(
    name: str | None,
    *,
    path: str | None = None,
    home: Path | None = None,
) -> str | None:
    """Preserve portable bare argv names when PATH works; otherwise use a path."""
    if not name:
        return None
    raw = str(name).strip()
    if not raw:
        return None
    if "/" in raw or raw.startswith("~"):
        return resolve_executable(raw, path=path, home=home) or raw
    if shutil.which(raw, path=path):
        return raw
    return resolve_executable(raw, path=path, home=home) or raw
