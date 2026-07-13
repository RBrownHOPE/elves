"""External read-only lane isolation: disposable tracked snapshots + optional FS sandbox.

Creates a disposable work directory containing only tracked source files with
isolated HOME/TMP/XDG. Adapter argv must target the snapshot (not the host repo).
Where available, a platform filesystem sandbox (sandbox-exec / bwrap) blocks
absolute host-home and sibling paths; otherwise optional routes fall back native
and required routes block.
"""

from __future__ import annotations

import os
import shutil
import shutil as _shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from .schema import ValidationIssue


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

# Argv flags that embed a repo/cwd path which must point at the snapshot.
_REPO_PATH_FLAGS: frozenset[str] = frozenset(
    {
        "--cd",
        "-C",
        "--cwd",
        "--repo-root",
        "--repo",
        "--dir",
        "--project",
        "--workspace",
    }
)


@dataclass
class IsolationSpec:
    repo_root: Path
    lane_id: str
    include_instructions_as_data: bool = False
    extra_exclude_globs: tuple[str, ...] = ()
    credential_grants: dict[str, str] = field(default_factory=dict)
    base_env: dict[str, str] = field(default_factory=dict)
    require_fs_sandbox: bool = False


@dataclass
class IsolatedLane:
    lane_id: str
    root: Path
    snapshot: Path
    home: Path
    tmp: Path
    xdg_config: Path
    xdg_cache: Path
    xdg_data: Path
    env: dict[str, str]
    tracked_file_count: int
    instruction_data_files: list[str] = field(default_factory=list)
    sandbox_backend: str | None = None  # sandbox-exec | bwrap | none
    sandbox_profile_path: str | None = None

    def cleanup(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)


def _git_tracked_files(repo_root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-z"],
        capture_output=True,
        check=False,
    )
    if result.returncode == 0 and result.stdout:
        files: list[str] = []
        for item in result.stdout.split(b"\0"):
            if not item:
                continue
            files.append(item.decode("utf-8", errors="replace"))
        return files
    # Non-git workspaces (tests/fixtures): walk ordinary files, still excluding secrets.
    files = []
    for path in sorted(repo_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(repo_root).as_posix()
        if _should_exclude(rel):
            continue
        if any(part.startswith(".") and part not in {".", ".."} for part in Path(rel).parts):
            # Skip hidden paths except those already filtered.
            if Path(rel).parts[0].startswith("."):
                continue
        files.append(rel)
    if not files:
        raise ValidationIssue(
            "isolation_git_ls_files_failed",
            "Unable to list tracked files for isolation snapshot",
            path=str(repo_root),
        )
    return files


def _should_exclude(rel: str) -> bool:
    parts = Path(rel).parts
    if any(part in DEFAULT_EXCLUDED_DIR_NAMES for part in parts):
        return True
    name = Path(rel).name
    if name in DEFAULT_EXCLUDED_FILE_NAMES:
        return True
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
        "ELVES_ISOLATED_SNAPSHOT": str(lane.snapshot),
    }
    for key, value in (credential_grants or {}).items():
        env[str(key)] = str(value)
    return env


def create_tracked_snapshot(spec: IsolationSpec) -> IsolatedLane:
    repo_root = Path(spec.repo_root).resolve()
    parent = Path(tempfile.mkdtemp(prefix=f"elves-iso-{spec.lane_id}-"))
    try:
        parent.chmod(0o700)
    except OSError:
        pass
    snapshot = parent / "snapshot"
    home = parent / "home"
    tmp = parent / "tmp"
    xdg_config = parent / "xdg-config"
    xdg_cache = parent / "xdg-cache"
    xdg_data = parent / "xdg-data"
    for path in (snapshot, home, tmp, xdg_config, xdg_cache, xdg_data):
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
            inert = snapshot / "_instruction_evidence" / f"{rel.replace('/', '__')}.txt"
            inert.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, inert)
            try:
                inert.chmod(0o600)
            except OSError:
                pass
            instruction_data.append(str(inert.relative_to(snapshot)))
            continue
        dest = snapshot / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        count += 1

    for name in INSTRUCTION_BASENAMES:
        active = snapshot / name
        if active.exists():
            active.unlink()

    lane = IsolatedLane(
        lane_id=spec.lane_id,
        root=parent,
        snapshot=snapshot,
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
    if spec.require_fs_sandbox:
        backend, profile_path = prepare_fs_sandbox(lane, required=True)
        lane.sandbox_backend = backend
        lane.sandbox_profile_path = profile_path
    else:
        # Snapshot + env isolation only; absolute-path OS boundary is opt-in/required.
        lane.sandbox_backend = None
        lane.sandbox_profile_path = None
    return lane


def detect_fs_sandbox_backend() -> str | None:
    """Return available FS sandbox backend name or None."""
    if _shutil.which("sandbox-exec"):
        return "sandbox-exec"
    if _shutil.which("bwrap"):
        return "bwrap"
    return None


def prepare_fs_sandbox(
    lane: IsolatedLane,
    *,
    required: bool = False,
) -> tuple[str | None, str | None]:
    """Create platform sandbox profile if a backend exists.

    A cwd snapshot alone is not an OS boundary against absolute paths. When no
    qualified backend is available: optional routes get (None, None) and callers
    must fall back native; required routes raise.
    """
    backend = detect_fs_sandbox_backend()
    if backend is None:
        if required:
            raise ValidationIssue(
                "isolation_sandbox_unavailable",
                "Required filesystem sandbox backend not available "
                "(need sandbox-exec on macOS or bwrap on Linux)",
                path=str(lane.root),
            )
        return None, None

    if backend == "sandbox-exec":
        # Allow-by-default with deny rules for secret-shaped paths and host-home
        # sentinels. Snapshot is the intended read root; process must still run
        # real interpreter binaries outside the snapshot.
        profile = f"""(version 1)
(allow default)
(deny file-read* (regex #"(?i)/\\.env(\\.|$)"))
(deny file-read* (regex #"(?i)/\\.secret$"))
(deny file-read* (regex #"(?i)/models\\.toml$"))
(deny file-read* (regex #"(?i)/\\.elves/"))
(allow file-write* (subpath "{lane.tmp}"))
(allow file-write* (subpath "{lane.home}"))
(allow file-write* (subpath "{lane.xdg_config}"))
(allow file-write* (subpath "{lane.xdg_cache}"))
(allow file-write* (subpath "{lane.xdg_data}"))
(allow file-read* (subpath "{lane.snapshot}"))
(allow file-read* (subpath "{lane.home}"))
(allow file-read* (subpath "{lane.tmp}"))
"""
        path = lane.root / "sandbox.sb"
        path.write_text(profile, encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return "sandbox-exec", str(path)

    # bwrap: bind snapshot read-only, tmp/home writable; no host home.
    return "bwrap", None


def wrap_argv_with_sandbox(
    argv: Sequence[str],
    lane: IsolatedLane,
) -> list[str]:
    """Prefix argv with sandbox backend when configured."""
    cmd = list(argv)
    if lane.sandbox_backend == "sandbox-exec" and lane.sandbox_profile_path:
        return ["sandbox-exec", "-f", lane.sandbox_profile_path, *cmd]
    if lane.sandbox_backend == "bwrap":
        return [
            "bwrap",
            "--die-with-parent",
            "--unshare-all",
            "--share-net",
            "--ro-bind",
            str(lane.snapshot),
            str(lane.snapshot),
            "--bind",
            str(lane.tmp),
            str(lane.tmp),
            "--bind",
            str(lane.home),
            str(lane.home),
            "--bind",
            str(lane.xdg_config),
            str(lane.xdg_config),
            "--bind",
            str(lane.xdg_cache),
            str(lane.xdg_cache),
            "--bind",
            str(lane.xdg_data),
            str(lane.xdg_data),
            "--ro-bind",
            "/usr",
            "/usr",
            "--ro-bind",
            "/bin",
            "/bin",
            "--ro-bind-try",
            "/lib",
            "/lib",
            "--ro-bind-try",
            "/lib64",
            "/lib64",
            "--ro-bind-try",
            "/opt",
            "/opt",
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--chdir",
            str(lane.snapshot),
            *cmd,
        ]
    return cmd


def rewrite_argv_repo_paths(
    argv: Sequence[str],
    *,
    original_repo: Path,
    snapshot: Path,
) -> list[str]:
    """Rewrite --cd/--cwd/--repo-root and absolute original-repo paths to the snapshot.

    Must run after the snapshot exists and before launch. Ensures built-in adapters
    like Codex ``--cd`` point at the disposable snapshot, not the host checkout.
    """
    orig = Path(original_repo).resolve()
    snap = Path(snapshot).resolve()
    out: list[str] = []
    i = 0
    tokens = list(argv)
    while i < len(tokens):
        tok = tokens[i]
        # --flag=value form
        if "=" in tok and tok.startswith("-"):
            flag, val = tok.split("=", 1)
            if flag in _REPO_PATH_FLAGS or _path_under_repo(val, orig):
                out.append(f"{flag}={_map_path(val, orig, snap)}")
            else:
                out.append(tok)
            i += 1
            continue
        if tok in _REPO_PATH_FLAGS and i + 1 < len(tokens):
            out.append(tok)
            out.append(_map_path(tokens[i + 1], orig, snap))
            i += 2
            continue
        if _path_under_repo(tok, orig):
            out.append(_map_path(tok, orig, snap))
        else:
            out.append(tok)
        i += 1
    return out


def _path_under_repo(value: str, repo: Path) -> bool:
    try:
        path = Path(value).expanduser()
        if not path.is_absolute() and value in {".", "./"}:
            return True
        resolved = path.resolve() if path.is_absolute() else (repo / path).resolve()
        return resolved == repo or repo in resolved.parents or str(resolved).startswith(str(repo) + os.sep)
    except (OSError, RuntimeError, ValueError):
        return False


def _map_path(value: str, original_repo: Path, snapshot: Path) -> str:
    try:
        path = Path(value).expanduser()
        if value in {".", "./"}:
            return str(snapshot)
        if path.is_absolute():
            resolved = path.resolve()
            orig = original_repo.resolve()
            if resolved == orig:
                return str(snapshot)
            if orig in resolved.parents or str(resolved).startswith(str(orig) + os.sep):
                rel = resolved.relative_to(orig)
                return str(snapshot / rel)
            return value
        # relative path — resolve against snapshot
        return str((snapshot / path).resolve())
    except (OSError, RuntimeError, ValueError):
        return value


@contextmanager
def isolated_lane(spec: IsolationSpec) -> Iterator[IsolatedLane]:
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
    return [key for key in forbidden_keys if key in env]
