"""HEAD/environment/config-keyed preflight evidence reuse.

A passing preflight may be reused when HEAD, relevant config digests, and tool
identity are unchanged. Final readiness never accepts cached evidence alone.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .storage import (
    StorageError,
    atomic_write_json,
    ensure_private_dir,
    guard_repo_path,
    read_json,
)


CACHE_REL = Path(".elves") / "runtime" / "preflight-cache"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _file_digest(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _source_tree_digest(root: Path) -> str:
    """Digest tracked/fallback source surfaces without retaining their contents."""
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=str(root),
            capture_output=True,
            check=False,
        )
    except OSError:
        listed = None
    if listed is not None and listed.returncode == 0:
        relatives = [
            Path(raw.decode("utf-8", errors="surrogateescape"))
            for raw in listed.stdout.split(b"\0")
            if raw
        ]
    else:
        relatives = [
            path.relative_to(root)
            for path in root.rglob("*")
            if not any(part in {".git", ".elves", "__pycache__"} for part in path.parts)
        ]
    digest = hashlib.sha256()
    for relative in sorted(relatives, key=lambda value: value.as_posix()):
        if any(part in {".git", ".elves", "__pycache__"} for part in relative.parts):
            continue
        path = root / relative
        try:
            info = path.lstat()
        except OSError:
            digest.update(f"missing:{relative.as_posix()}\0".encode("utf-8"))
            continue
        digest.update(relative.as_posix().encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        digest.update(str(stat.S_IMODE(info.st_mode)).encode("ascii"))
        digest.update(b"\0")
        if stat.S_ISLNK(info.st_mode):
            try:
                digest.update(os.readlink(path).encode("utf-8", errors="surrogateescape"))
            except OSError:
                digest.update(b"unreadable-symlink")
        elif stat.S_ISREG(info.st_mode):
            content_digest = _file_digest(path)
            digest.update((content_digest or "unreadable").encode("ascii"))
        else:
            digest.update(f"non-regular:{info.st_mode}".encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def compute_preflight_key(
    repo_root: Path,
    *,
    head: str,
    config_paths: Sequence[str] | None = None,
    env_names: Sequence[str] | None = None,
    tool_names: Sequence[str] | None = None,
) -> str:
    root = Path(repo_root).resolve()
    components: dict[str, str] = {"head": head}
    components["source_tree"] = _source_tree_digest(root)
    for rel in config_paths or (
        "SKILL.md",
        "AGENTS.md",
        "config.json.example",
        "scripts/verify_repo.py",
        "scripts/check_repo_consistency.py",
    ):
        digest = _file_digest(root / rel)
        if digest:
            components[f"file:{rel}"] = digest
    env = os.environ
    for name in env_names or ("PATH", "PYTHONPATH"):
        components[f"env:{name}"] = hashlib.sha256(
            (env.get(name) or "").encode("utf-8")
        ).hexdigest()[:16]
    components["runtime"] = hashlib.sha256(
        json.dumps(
            {
                "python_executable": str(Path(sys.executable).resolve()),
                "python_version": platform.python_version(),
                "python_implementation": platform.python_implementation(),
                "platform_system": platform.system(),
                "platform_release": platform.release(),
                "platform_machine": platform.machine(),
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    search_path = env.get("PATH") or os.defpath
    for name in tool_names or ("python3", "git", "gh", "bash"):
        found = shutil.which(name, path=search_path)
        if found is None:
            components[f"tool:{name}"] = "missing"
            continue
        resolved = Path(found).resolve()
        try:
            info = resolved.stat()
        except OSError as exc:
            components[f"tool:{name}"] = f"unreadable:{type(exc).__name__}"
            continue
        components[f"tool:{name}"] = hashlib.sha256(
            json.dumps(
                {
                    "resolved": str(resolved),
                    "mode": stat.S_IMODE(info.st_mode),
                    "size": info.st_size,
                    "content": _file_digest(resolved),
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
    material = json.dumps(components, sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass
class PreflightEvidence:
    key: str
    head: str
    status: str  # pass | fail
    recorded_at: str
    gates: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PreflightEvidence":
        return cls(
            key=str(data["key"]),
            head=str(data["head"]),
            status=str(data["status"]),
            recorded_at=str(data["recorded_at"]),
            gates=dict(data.get("gates") or {}),
            notes=list(data.get("notes") or []),
        )


def cache_path(repo_root: Path) -> Path:
    repo = Path(repo_root).expanduser().resolve()
    return guard_repo_path(repo, repo / CACHE_REL / "latest.json")


def store_preflight(repo_root: Path, evidence: PreflightEvidence) -> Path:
    repo = Path(repo_root).expanduser().resolve()
    root = ensure_private_dir(repo / CACHE_REL, repo_root=repo)
    path = root / "latest.json"
    atomic_write_json(path, evidence.to_dict(), repo_root=repo)
    return path


def load_preflight(repo_root: Path) -> PreflightEvidence | None:
    repo = Path(repo_root).expanduser().resolve()
    path = cache_path(repo)
    try:
        data = read_json(path, repo_root=repo)
        return PreflightEvidence.from_dict(data)
    except StorageError as exc:
        if exc.code in {"not_found", "malformed_json"}:
            return None
        raise
    except (OSError, KeyError, TypeError, ValueError):
        return None


def reuse_preflight(
    repo_root: Path,
    *,
    head: str,
    config_paths: Sequence[str] | None = None,
    env_names: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Return reuse decision. Final readiness must ignore reuse alone."""
    key = compute_preflight_key(
        repo_root, head=head, config_paths=config_paths, env_names=env_names
    )
    cached = load_preflight(repo_root)
    if cached is None:
        return {
            "reuse": False,
            "reason": "no_cache",
            "key": key,
            "final_readiness_accepts_cache_alone": False,
        }
    if cached.status != "pass":
        return {
            "reuse": False,
            "reason": "cached_not_pass",
            "key": key,
            "final_readiness_accepts_cache_alone": False,
        }
    if cached.key != key:
        return {
            "reuse": False,
            "reason": "key_mismatch",
            "key": key,
            "cached_key": cached.key,
            "final_readiness_accepts_cache_alone": False,
        }
    if cached.head != head:
        return {
            "reuse": False,
            "reason": "head_mismatch",
            "key": key,
            "final_readiness_accepts_cache_alone": False,
        }
    return {
        "reuse": True,
        "reason": "identical_head_and_config",
        "key": key,
        "evidence": cached.to_dict(),
        "final_readiness_accepts_cache_alone": False,
        "note": "Cached preflight may skip Batch-1-style broad gate; final readiness still requires live proof",
    }


def record_passing_preflight(
    repo_root: Path,
    *,
    head: str,
    gates: Mapping[str, Any] | None = None,
    notes: Sequence[str] | None = None,
) -> PreflightEvidence:
    key = compute_preflight_key(repo_root, head=head)
    evidence = PreflightEvidence(
        key=key,
        head=head,
        status="pass",
        recorded_at=_utc_now(),
        gates=dict(gates or {}),
        notes=list(notes or []),
    )
    store_preflight(repo_root, evidence)
    return evidence
