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
    current_product_digest = compute_product_test_input_digest(repo_root)
    if cached is None:
        return {
            "reuse": False,
            "reason": "no_cache",
            "key": key,
            "reuse_product_tests": False,
            "product_test_input_digest": current_product_digest,
            "final_readiness_accepts_cache_alone": False,
        }
    if cached.status != "pass":
        return {
            "reuse": False,
            "reason": "cached_not_pass",
            "key": key,
            "reuse_product_tests": False,
            "product_test_input_digest": current_product_digest,
            "final_readiness_accepts_cache_alone": False,
        }
    cached_product_digest = str(
        (cached.gates or {}).get("product_test_input_digest") or ""
    )
    product_tests_reusable = bool(
        cached_product_digest
        and cached_product_digest == current_product_digest
        and cached.status == "pass"
    )
    if cached.key != key:
        return {
            "reuse": False,
            "reason": "key_mismatch",
            "key": key,
            "cached_key": cached.key,
            "reuse_product_tests": product_tests_reusable,
            "product_test_input_digest": current_product_digest,
            "final_readiness_accepts_cache_alone": False,
        }
    if cached.head != head:
        return {
            "reuse": False,
            "reason": "head_mismatch",
            "key": key,
            "reuse_product_tests": product_tests_reusable,
            "product_test_input_digest": current_product_digest,
            "final_readiness_accepts_cache_alone": False,
        }
    return {
        "reuse": True,
        "reason": "identical_head_and_config",
        "key": key,
        "evidence": cached.to_dict(),
        "reuse_product_tests": product_tests_reusable,
        "product_test_input_digest": current_product_digest,
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
    recorded_gates = dict(gates or {})
    recorded_gates["product_test_input_digest"] = compute_product_test_input_digest(
        repo_root
    )
    evidence = PreflightEvidence(
        key=key,
        head=head,
        status="pass",
        recorded_at=_utc_now(),
        gates=recorded_gates,
        notes=list(notes or []),
    )
    store_preflight(repo_root, evidence)
    return evidence


# --- Gate evidence by input digest (docs-only HEAD may reuse) ---

DOCS_ONLY_SUFFIXES = (".md", ".rst", ".adoc")
RUN_METADATA_PREFIXES = (".elves/", "docs/elves/")


def path_is_docs_or_run_metadata(path: str) -> bool:
    rel = path.replace("\\", "/")
    if any(rel.startswith(p) for p in RUN_METADATA_PREFIXES):
        return True
    lower = rel.lower()
    return lower.endswith(DOCS_ONLY_SUFFIXES) or (
        lower.endswith(".txt")
        and (rel.startswith("docs/") or rel.startswith("references/"))
    )


def compute_product_test_input_digest(
    repo_root: Path,
    *,
    relevant_globs: Sequence[str] | None = None,
) -> str:
    """Digest product/runtime/test inputs, ignoring pure docs/run-metadata files."""
    root = Path(repo_root).resolve()
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=str(root),
            capture_output=True,
            check=False,
        )
    except OSError:
        listed = None
    relatives: list[Path] = []
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
            if path.is_file()
            and not any(part in {".git", ".elves", "__pycache__"} for part in path.parts)
        ]
    digest = hashlib.sha256()
    for relative in sorted(relatives, key=lambda value: value.as_posix()):
        rel = relative.as_posix()
        if path_is_docs_or_run_metadata(rel):
            continue
        if relevant_globs:
            # Optional filter: keep scripts/tests/workflows by default markers.
            keep = any(
                rel.startswith(prefix)
                for prefix in ("scripts/", "tests/", ".github/")
            )
            if not keep and not any(rel.endswith(s) for s in (".py", ".sh", ".yml")):
                # still include lockfiles/config for dependency identity
                if not any(
                    name in rel
                    for name in (
                        "pyproject.toml",
                        "requirements",
                        "package.json",
                        "Cargo.toml",
                        "go.mod",
                    )
                ):
                    continue
        path = root / relative
        content = _file_digest(path)
        digest.update(rel.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        digest.update((content or "missing").encode("ascii"))
        digest.update(b"\0")
    # Include runtime identity so tool changes invalidate.
    digest.update(
        json.dumps(
            {
                "python": platform.python_version(),
                "system": platform.system(),
            },
            sort_keys=True,
        ).encode("utf-8")
    )
    return digest.hexdigest()


def gate_evidence_reuse(
    repo_root: Path,
    *,
    cached_input_digest: str | None,
    head: str | None = None,
) -> dict[str, Any]:
    """Reuse gate evidence when product/test input digest matches.

    A docs-only or run-metadata-only commit may change HEAD without invalidating
    runtime proof. Final readiness never accepts cache alone.
    """
    current = compute_product_test_input_digest(repo_root)
    if not cached_input_digest:
        return {
            "reuse": False,
            "reason": "no_cache",
            "input_digest": current,
            "final_readiness_accepts_cache_alone": False,
        }
    if cached_input_digest != current:
        return {
            "reuse": False,
            "reason": "input_digest_mismatch",
            "input_digest": current,
            "final_readiness_accepts_cache_alone": False,
        }
    return {
        "reuse": True,
        "reason": "identical_product_test_input_digest",
        "input_digest": current,
        "head": head,
        "final_readiness_accepts_cache_alone": False,
        "note": "Docs-only/run-metadata HEAD moves do not invalidate this digest",
    }


def cleanup_only_tip_attestation(
    *,
    parent_tip: str,
    proven_tip: str,
    name_status_rows: Sequence[str],
    recorded_operational_paths: Sequence[str],
    product_test_input_digest_unchanged: bool,
) -> dict[str, Any]:
    """Reuse live broad proof after operational-artifact cleanup only."""
    from .risk_policy import cleanup_only_reuse_allowed  # noqa: PLC0415

    changed: list[str] = []
    for row in name_status_rows:
        parts = row.split("\t")
        if len(parts) >= 2 and parts[0].startswith("D"):
            changed.append(parts[-1])
        elif len(parts) >= 2:
            # Any non-delete forces live proof.
            return {
                "reuse": False,
                "reason": "non_delete_in_cleanup_diff",
                "force_live_proof": True,
            }
    return cleanup_only_reuse_allowed(
        parent_tip=parent_tip,
        proven_tip=proven_tip,
        changed_paths=changed,
        recorded_operational_paths=recorded_operational_paths,
        product_test_input_digest_unchanged=product_test_input_digest_unchanged,
    )
