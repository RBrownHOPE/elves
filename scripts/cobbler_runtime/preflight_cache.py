"""HEAD/environment/config-keyed preflight evidence reuse.

A passing preflight may be reused when HEAD, relevant config digests, and tool
identity are unchanged. Final readiness never accepts cached evidence alone.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .storage import atomic_write_json, ensure_private_dir


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


def compute_preflight_key(
    repo_root: Path,
    *,
    head: str,
    config_paths: Sequence[str] | None = None,
    env_names: Sequence[str] | None = None,
) -> str:
    root = Path(repo_root).resolve()
    components: dict[str, str] = {"head": head}
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
    return Path(repo_root).resolve() / CACHE_REL / "latest.json"


def store_preflight(repo_root: Path, evidence: PreflightEvidence) -> Path:
    root = ensure_private_dir(Path(repo_root).resolve() / CACHE_REL)
    path = root / "latest.json"
    atomic_write_json(path, evidence.to_dict())
    return path


def load_preflight(repo_root: Path) -> PreflightEvidence | None:
    path = cache_path(repo_root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return PreflightEvidence.from_dict(data)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
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
