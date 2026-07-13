"""External read-only lane isolation: disposable tracked snapshots + env grants.

Creates a disposable work directory containing only tracked source files (or an
explicit allowlist), with isolated HOME/TMP/XDG. Ignored secrets, runtime state,
sibling worktrees, and auto-loaded instruction files are excluded. Requested
instruction evidence is exposed under inert data names only.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from .schema import ValidationIssue


# Instruction surfaces that must never be auto-loaded as active control files.
INSTRUCTION_BASENAMES: frozenset[str] = frozenset(
    {
        "AGENTS.md",
        "CLAUDE.md",
        "Claude.md",
        "SKILL.md",
        "CONSTITUTION.md",
        ".cursorrules",
        ".cursor",
        ".claude",
        ".codex",
        ".grok",
    }
)

DEFAULT_EXCLUDED_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        ".elves",
        ".env",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".worktrees",
        "worktrees",
    }
)

DEFAULT_EXCLUDED_FILE_NAMES: frozenset[str] = frozenset(
    {
        ".env",
        ".env.local",
        ".env.production",
        "models.toml",
        ".elves-session.json",
    }
)


@dataclass
class IsolationSpec:
    """Configuration for one disposable isolation lane."""

    repo_root: Path
    lane_id: str
    include_instructions_as_data: bool = False
    extra_exclude_globs: tuple[str, ...] = ()
    credential_grants: dict[str, str] = field(default_factory=dict)
    base_env: dict[str, str] = field(default_factory=dict)


@dataclass
class IsolatedLane:
    lane_id: str
    root: Path
    home: Path
    tmp: Path
    xdg_config: Path
    xdg_cache: Path
    xdg_data: Path
    env: dict[str, str]
    tracked_file_count: int
    instruction_data_files: list[str] = field(default_factory=list)

    def cleanup(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)


def _git_tracked_files(repo_root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-z"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValidationIssue(
            "isolation_git_ls_files_failed",
            "Unable to list tracked files for isolation snapshot",
            path=str(repo_root),
        )
    raw = result.stdout.split(b"\0")
    files: list[str] = []
    for item in raw:
        if not item:
            continue
        files.append(item.decode("utf-8", errors="replace"))
    return files


def _should_exclude(rel: str) -> bool:
    parts = Path(rel).parts
    if any(part in DEFAULT_EXCLUDED_DIR_NAMES for part in parts):
        return True
    name = Path(rel).name
    if name in DEFAULT_EXCLUDED_FILE_NAMES:
        return True
    if name == "models.toml" and ".elves" in parts:
        return True
    # Never copy ignored runtime or home-like surfaces.
    if rel.startswith(".elves/") or rel.startswith(".git/"):
        return True
    return False


def _is_instruction_surface(rel: str) -> bool:
    path = Path(rel)
    if path.name in INSTRUCTION_BASENAMES:
        return True
    if path.parts and path.parts[0] in INSTRUCTION_BASENAMES:
        return True
    return False


def build_isolated_env(
    lane: IsolatedLane,
    *,
    credential_grants: Mapping[str, str] | None = None,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Minimal environment with isolated HOME/TMP/XDG and explicit credential grants."""
    env: dict[str, str] = {
        "HOME": str(lane.home),
        "TMPDIR": str(lane.tmp),
        "TMP": str(lane.tmp),
        "TEMP": str(lane.tmp),
        "XDG_CONFIG_HOME": str(lane.xdg_config),
        "XDG_CACHE_HOME": str(lane.xdg_cache),
        "XDG_DATA_HOME": str(lane.xdg_data),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PATH": (base_env or {}).get("PATH") or os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": (base_env or {}).get("LANG") or "C.UTF-8",
    }
    # Never inherit host secrets; only explicit grants.
    for key, value in (credential_grants or {}).items():
        env[str(key)] = str(value)
    return env


def create_tracked_snapshot(spec: IsolationSpec) -> IsolatedLane:
    """Create a disposable tracked-source snapshot for an external read-only lane."""
    repo_root = Path(spec.repo_root).resolve()
    parent = Path(tempfile.mkdtemp(prefix=f"elves-iso-{spec.lane_id}-"))
    try:
        parent.chmod(0o700)
    except OSError:
        pass
    root = parent / "snapshot"
    home = parent / "home"
    tmp = parent / "tmp"
    xdg_config = parent / "xdg-config"
    xdg_cache = parent / "xdg-cache"
    xdg_data = parent / "xdg-data"
    for path in (root, home, tmp, xdg_config, xdg_cache, xdg_data):
        path.mkdir(parents=True, exist_ok=True)
        try:
            path.chmod(0o700)
        except OSError:
            pass

    tracked = _git_tracked_files(repo_root)
    instruction_data: list[str] = []
    count = 0
    for rel in tracked:
        if _should_exclude(rel):
            continue
        src = repo_root / rel
        if not src.is_file():
            continue
        if _is_instruction_surface(rel):
            if not spec.include_instructions_as_data:
                continue
            # Inert evidence name — never as active repository control file.
            inert = root / "_instruction_evidence" / f"{rel.replace('/', '__')}.txt"
            inert.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, inert)
            try:
                inert.chmod(0o600)
            except OSError:
                pass
            instruction_data.append(str(inert.relative_to(root)))
            continue
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        count += 1

    # Ensure no active AGENTS.md / CLAUDE.md at snapshot root.
    for name in INSTRUCTION_BASENAMES:
        active = root / name
        if active.exists():
            active.unlink()

    lane = IsolatedLane(
        lane_id=spec.lane_id,
        root=parent,
        home=home,
        tmp=tmp,
        xdg_config=xdg_config,
        xdg_cache=xdg_cache,
        xdg_data=xdg_data,
        env={},
        tracked_file_count=count,
        instruction_data_files=instruction_data,
    )
    lane.env = build_isolated_env(
        lane,
        credential_grants=spec.credential_grants,
        base_env=spec.base_env,
    )
    # Snapshot content is under parent/snapshot; expose that as work cwd convention.
    lane.env["ELVES_ISOLATED_SNAPSHOT"] = str(root)
    return lane


@contextmanager
def isolated_lane(spec: IsolationSpec) -> Iterator[IsolatedLane]:
    """Context manager that always cleans up the disposable lane."""
    lane = create_tracked_snapshot(spec)
    try:
        yield lane
    finally:
        lane.cleanup()


def implement_min_env(
    *,
    adapter: str,
    worktree: Path,
    credential_grants: Mapping[str, str] | None = None,
    home: Path | None = None,
    tmp: Path | None = None,
) -> dict[str, str]:
    """Minimal implementer environment with explicit credential grants only."""
    home_path = home or Path(tempfile.mkdtemp(prefix="elves-impl-home-"))
    tmp_path = tmp or Path(tempfile.mkdtemp(prefix="elves-impl-tmp-"))
    for path in (home_path, tmp_path):
        path.mkdir(parents=True, exist_ok=True)
        try:
            path.chmod(0o700)
        except OSError:
            pass
    env = {
        "HOME": str(home_path),
        "TMPDIR": str(tmp_path),
        "TMP": str(tmp_path),
        "TEMP": str(tmp_path),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "ELVES_IMPLEMENT_ADAPTER": adapter,
        "ELVES_IMPLEMENT_WORKTREE": str(Path(worktree).resolve()),
    }
    for key, value in (credential_grants or {}).items():
        env[str(key)] = str(value)
    return env


def assert_no_host_secrets(env: Mapping[str, str], *, forbidden_keys: Sequence[str]) -> list[str]:
    """Return forbidden keys present in env (empty means clean)."""
    return [key for key in forbidden_keys if key in env]
