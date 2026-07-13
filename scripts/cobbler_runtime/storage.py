"""Shared storage primitives for session/lease/audit records.

- Collision-free digest-based record keys
- Embedded-ID verification on read
- Atomic mode-0600 JSON writes
- Revision-aware updates
- Common-directory locking (fcntl when available)
- Safe snapshot path resolution (no raw ID path traversal)
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping


try:
    import fcntl
except ImportError:  # pragma: no cover - non-Unix
    fcntl = None  # type: ignore[assignment]


class StorageError(Exception):
    """Fail-closed storage boundary error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def digest_key(record_id: str, *, prefix: str = "rec") -> str:
    """Return a filesystem-safe key derived from the full id (not a truncation of raw id)."""
    if not record_id or not str(record_id).strip():
        raise StorageError("empty_record_id", "Record id is required")
    material = str(record_id).encode("utf-8")
    digest = hashlib.sha256(material).hexdigest()
    # Short readable prefix from sanitized id for operator debugging only.
    safe = "".join(ch if ch.isalnum() else "_" for ch in str(record_id))[:24]
    return f"{prefix}-{safe}-{digest[:16]}"


def record_filename(record_id: str, *, prefix: str = "rec") -> str:
    return f"{digest_key(record_id, prefix=prefix)}.json"


def assert_embedded_id(data: Mapping[str, Any], expected_id: str, *, id_field: str = "session_id") -> None:
    embedded = data.get(id_field)
    if embedded != expected_id:
        raise StorageError(
            "embedded_id_mismatch",
            f"Embedded {id_field}={embedded!r} does not match expected {expected_id!r}",
        )


def ensure_private_dir(path: Path, *, mode: int = 0o700) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(mode)
    except OSError:
        pass
    return path


def atomic_write_json(
    path: Path,
    data: Mapping[str, Any],
    *,
    mode: int = 0o600,
) -> None:
    """Write JSON via temp file + os.replace with private permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(dict(data), indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        try:
            path.chmod(mode)
        except OSError:
            pass
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def read_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except FileNotFoundError as exc:
        raise StorageError("not_found", f"Missing record: {path}") from exc
    except json.JSONDecodeError as exc:
        raise StorageError("malformed_json", f"Malformed JSON at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise StorageError("malformed_json", f"JSON object required at {path}")
    return data


def snapshot_path(store_root: Path, record_id: str, *, kind: str = "snapshot") -> Path:
    """Store-owned snapshot directory; never interpolate raw ids into parent paths."""
    key = digest_key(record_id, prefix=kind)
    path = (store_root / "snapshots" / key).resolve()
    root = store_root.resolve()
    if root not in path.parents and path != root:
        raise StorageError("path_escape", f"Snapshot path escapes store root: {path}")
    return path


@contextmanager
def directory_lock(store_root: Path, *, name: str = "store.lock", timeout: float = 10.0) -> Iterator[Path]:
    """Exclusive lock for lease/session mutation under one store directory."""
    ensure_private_dir(store_root)
    lock_path = store_root / name
    handle = lock_path.open("a+", encoding="utf-8")
    start = time.time()
    locked = False
    try:
        while True:
            try:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except BlockingIOError:
                if time.time() - start > timeout:
                    raise StorageError("lock_timeout", f"Timed out locking {lock_path}")
                time.sleep(0.02)
            except OSError:
                # Best-effort on platforms without flock semantics.
                locked = True
                break
        yield lock_path
    finally:
        if locked and fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


def bump_revision(data: dict[str, Any]) -> int:
    current = int(data.get("revision") or 0)
    next_rev = current + 1
    data["revision"] = next_rev
    return next_rev


SUPPORTED_SANDBOX_PROFILES: frozenset[str] = frozenset(
    {
        "devbox",
        "docker",
        "firecracker",
        "bubblewrap",
        "isolated",
    }
)

# Explicitly unsupported for write qualification (fail closed).
UNSUPPORTED_SANDBOX_PROFILES: frozenset[str] = frozenset(
    {
        "workspace",
        "host",
        "none",
        "inherited",
        "preference-declared",
    }
)


QUALIFICATION_REQUIRED_FIELDS: tuple[str, ...] = (
    "adapter",
    "model",
    "sandbox",
    "worktree",
    "cwd",
    "parent",
    "source_head",
    "capabilities",
    "evidence_kind",
    "observed_at",
)


def qualify_write_evidence(evidence: Mapping[str, Any] | None) -> tuple[bool, list[str]]:
    """Host-issued observed write qualification. Missing/mismatch/stale/unsupported fail closed."""
    reasons: list[str] = []
    if not evidence or not isinstance(evidence, Mapping):
        return False, ["missing qualification evidence object"]

    required = (
        "adapter",
        "model",
        "sandbox",
        "worktree",
        "cwd",
        "parent",
        "source_head",
        "capabilities",
        "evidence_kind",
        "observed_at",
    )
    for key in required:
        value = evidence.get(key)
        if value is None or value == "" or value == [] or value == {}:
            reasons.append(f"missing_or_empty:{key}")

    sandbox = str(evidence.get("sandbox") or "").strip().lower()
    if sandbox in UNSUPPORTED_SANDBOX_PROFILES:
        reasons.append(f"unsupported_sandbox:{sandbox}")
    elif sandbox and sandbox not in SUPPORTED_SANDBOX_PROFILES:
        reasons.append(f"unsupported_sandbox:{sandbox}")

    if evidence.get("preference_declared") is True and not evidence.get("host_observed"):
        reasons.append("preference_declared_without_host_observation")

    if evidence.get("host_observed") is not True:
        reasons.append("host_observed_required")

    stale = evidence.get("stale")
    if stale is True:
        reasons.append("stale_evidence")

    caps = evidence.get("capabilities")
    if isinstance(caps, Mapping):
        if caps.get("write") is not True:
            reasons.append("capabilities.write_not_true")
    elif caps is not None:
        reasons.append("capabilities_must_be_object")

    return (not reasons), reasons
