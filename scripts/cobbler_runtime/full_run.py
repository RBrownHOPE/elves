"""Full-run supervisor for trusted delegated implementers (Lane A / Grok Build).

Uses adapter-aware ``implement.build_launch_argv`` for real Grok create/resume.
Fixture mode is explicit (``adapter=fixture``) for unit tests only.

Artifacts live under digest-keyed private paths. Worker events enrich telemetry;
liveness also comes from process fingerprint + observed feature-branch HEAD.
A worker report is evidence only — never merge authority.
"""

from __future__ import annotations

import errno
import hashlib
import hmac
import json
import os
import re
import secrets
import signal
import stat
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from .context import redact_text
from .implement import (
    DEFAULT_EFFORT,
    DEFAULT_EXECUTABLE,
    DEFAULT_MODEL,
    DEFAULT_PERMISSION_MODE,
    build_launch_argv,
)
from .schema import ValidationIssue
from .storage import (
    StorageError,
    assert_embedded_id,
    atomic_write_json,
    directory_lock,
    digest_key,
    ensure_private_dir,
    guard_repo_path,
    move_repo_regular_file,
    open_repo_text,
    read_json,
    read_repo_regular_bytes,
    read_repo_text_tail,
    repo_regular_file_exists,
)

FULL_RUN_REL = Path(".elves") / "runtime" / "implement" / "full-run"
EVENT_TYPES = frozenset(
    {
        "run_started",
        "heartbeat",
        "batch_started",
        "commit_pushed",
        "gate_result",
        "batch_complete",
        "blocked",
        "run_complete",
    }
)
TERMINAL_EVENT_TYPES = frozenset({"run_complete", "blocked"})
DEFAULT_STALE_SECONDS = 300
EXIT_RECORD_SETTLE_SECONDS = 0.25
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
_REPORT_STATUSES = frozenset({"running", "complete", "blocked", "failed", "stopped"})
_ACCEPTANCE_DEFINITION_RE = re.compile(
    r"(?m)^\s*[-*]\s+(?:\[[ xX]\]\s+)?"
    r"(?P<id>B\d+-A\d+|M-A\d+)\s*(?:—|--?|:)\s*\S.*$"
)
_ACCEPTANCE_ID_RE = re.compile(r"^(?:B\d+-A\d+|M-A\d+)$")
_FULL_RUN_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])"
    r"(?:api[_-]?key|[A-Za-z0-9_-]*token|jwt|bearer|authorization|auth|"
    r"password|passwd|secret|credential|cookie|private[_-]?key)"
    r"[\"']?\s*[:=]\s*(?:bearer\s+)?[\"']?[^\s,;\"'}]{8,}[\"']?"
)
_FULL_RUN_SECRET_KEY_RE = re.compile(
    r"(?i)(?:api[_-]?key|token|jwt|bearer|authorization|auth|password|passwd|"
    r"secret|credentials?|cookie|private[_-]?key)(?:_value|_header)?$"
)
_PEM_BOUNDARY_RE = re.compile(r"-----\s*(?:BEGIN|END)\s+[A-Z0-9 ]*PRIVATE KEY\s*-----", re.I)
MAX_PACKET_BYTES = 1024 * 1024
MAX_EVENT_FILE_BYTES = 1024 * 1024
MAX_EVENT_LINES = 2000
MAX_EVENT_LINE_BYTES = 64 * 1024
MAX_REPORT_BYTES = 512 * 1024
MAX_TRANSCRIPT_TAIL_BYTES = 256 * 1024
MAX_TRANSCRIPT_LINE_CHARS = 1000
MAX_EVENT_FUTURE_SKEW_SECONDS = 300

# Named non-secret essentials preserved for a usable logged-in Grok process.
NON_SECRET_ESSENTIALS: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TERM",
        "TMPDIR",
        "TMP",
        "TEMP",
        "XDG_RUNTIME_DIR",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
        "PYTHONUNBUFFERED",
        "COLORTERM",
    }
)

# Credential grants by name only (values from parent env / private config — never argv KEY=VALUE).
DEFAULT_CREDENTIAL_GRANT_NAMES: frozenset[str] = frozenset(
    {
        "XAI_API_KEY",
        "GROK_API_KEY",
        "OPENAI_API_KEY",  # some Grok builds share OpenAI-compatible paths
    }
)

STATUS_KEYS = frozenset(
    {
        "ok",
        "session_id",
        "state",
        "batch",
        "head",
        "branch",
        "heartbeat_at",
        "pid",
        "pgid",
        "next_action",
        "blocker",
        "driver_contract",
        "driver_monitor_mode",
        "wake_conditions",
        "check_summary",
        "report_path",
        "events_path",
        "transcript_private",
        "adapter",
        "fingerprint_ok",
        "merge_authority",
    }
)


def _redact_full_run_text(
    value: str,
    *,
    exact_values: frozenset[str] | set[str] | tuple[str, ...] | None = None,
) -> str:
    """Apply shared token redaction plus full-run credential assignments."""
    shared = redact_text(value, exact_values=exact_values).text
    return _FULL_RUN_SECRET_ASSIGNMENT_RE.sub(
        "[REDACTED:credential_assignment]", shared
    )


def _redact_full_run_structure(
    value: Any,
    *,
    exact_values: frozenset[str] | set[str] | tuple[str, ...] | None = None,
) -> Any:
    """Recursively redact keys and values before any driver-visible serialization."""
    if isinstance(value, str):
        return _redact_full_run_text(value, exact_values=exact_values)
    if isinstance(value, Mapping):
        redacted_mapping: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            raw_key = str(key)
            redacted_key = _redact_full_run_text(
                raw_key,
                exact_values=exact_values,
            )
            secret_field = bool(_FULL_RUN_SECRET_KEY_RE.search(raw_key))
            if secret_field:
                redacted_key = "[REDACTED:secret_field_name]"
            if redacted_key in redacted_mapping:
                # Never let multiple secret-shaped keys collapse and hide data.
                redacted_key = f"{redacted_key}#{index}"
            redacted_mapping[redacted_key] = (
                "[REDACTED:secret_field]"
                if secret_field
                else _redact_full_run_structure(item, exact_values=exact_values)
            )
        return redacted_mapping
    if isinstance(value, list):
        return [
            _redact_full_run_structure(item, exact_values=exact_values)
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(
            _redact_full_run_structure(item, exact_values=exact_values)
            for item in value
        )
    return value


def _redact_full_run_mapping_in_place(
    value: dict[str, Any],
    *,
    exact_values: frozenset[str] | set[str] | tuple[str, ...] | None = None,
) -> None:
    """Sanitize values without obscuring a public function's static output shape."""
    redacted = _redact_full_run_structure(value, exact_values=exact_values)
    value.clear()
    value.update(redacted)


def _contains_full_run_secret(
    value: Any,
    *,
    exact_values: frozenset[str] | set[str] | tuple[str, ...] | None = None,
) -> bool:
    return _redact_full_run_structure(value, exact_values=exact_values) != value


def _read_bounded_regular_bytes(
    path: Path,
    *,
    max_bytes: int,
    label: str,
    repo_root: Path | None = None,
) -> bytes:
    """Read one regular non-symlink file with an exact byte ceiling."""
    candidate = Path(path)
    if repo_root is not None:
        try:
            return read_repo_regular_bytes(
                Path(repo_root), candidate, max_bytes=max_bytes
            )
        except StorageError as exc:
            raise StorageError(exc.code, f"{label}: {exc.message}") from exc
    try:
        before = candidate.lstat()
    except FileNotFoundError as exc:
        raise StorageError(f"{label}_missing", f"{label} is missing: {candidate}") from exc
    except OSError as exc:
        raise StorageError(
            f"{label}_unavailable", f"{label} metadata is unavailable: {type(exc).__name__}"
        ) from exc
    if not stat.S_ISREG(before.st_mode):
        raise StorageError(
            f"{label}_not_regular", f"{label} must be a regular non-symlink file"
        )
    if before.st_size > max_bytes:
        raise StorageError(
            f"{label}_too_large", f"{label} exceeds {max_bytes} byte limit"
        )
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(candidate, flags)
    except OSError as exc:
        raise StorageError(
            f"{label}_unavailable", f"{label} cannot be opened safely: {type(exc).__name__}"
        ) from exc
    try:
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
        ):
            raise StorageError(
                f"{label}_identity_changed", f"{label} changed identity while opening"
            )
        if opened.st_size > max_bytes:
            raise StorageError(
                f"{label}_too_large", f"{label} exceeds {max_bytes} byte limit"
            )
        with os.fdopen(fd, "rb", closefd=False) as handle:
            raw = handle.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise StorageError(
                f"{label}_too_large", f"{label} exceeds {max_bytes} byte limit"
            )
        return raw
    finally:
        os.close(fd)


def _read_bounded_json_object(
    path: Path,
    *,
    max_bytes: int = MAX_REPORT_BYTES,
    label: str = "JSON artifact",
    repo_root: Path | None = None,
) -> dict[str, Any]:
    raw = _read_bounded_regular_bytes(
        path,
        max_bytes=max_bytes,
        label=label,
        repo_root=repo_root,
    )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StorageError(
            f"{label}_encoding", f"{label} must be valid UTF-8"
        ) from exc
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StorageError(
            f"{label}_malformed", f"{label} must contain one valid JSON object"
        ) from exc
    if not isinstance(value, dict):
        raise StorageError(
            f"{label}_malformed", f"{label} must contain one JSON object"
        )
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def full_run_root(repo_root: Path, session_id: str) -> Path:
    """Digest-keyed collision-free runtime directory (not raw session path)."""
    key = digest_key(session_id, prefix="fullrun")
    repo = Path(repo_root).expanduser().resolve(strict=True)
    return guard_repo_path(repo, repo / FULL_RUN_REL / key)


_FULL_RUN_LOCK_LOCAL = threading.local()
_FULL_RUN_THREAD_LOCKS: dict[str, threading.RLock] = {}
_FULL_RUN_THREAD_LOCKS_GUARD = threading.Lock()


@contextmanager
def _full_run_lock(repo_root: Path, session_id: str):
    """Cross-process per-session lock with same-thread reentrancy."""
    repo = Path(repo_root).expanduser().resolve(strict=True)
    root = full_run_root(repo, session_id)
    lock_key = str(guard_repo_path(repo, root / "run.lock"))
    held = getattr(_FULL_RUN_LOCK_LOCAL, "held", None)
    if held is None:
        held = set()
        _FULL_RUN_LOCK_LOCAL.held = held
    if lock_key in held:
        yield
        return
    with _FULL_RUN_THREAD_LOCKS_GUARD:
        thread_lock = _FULL_RUN_THREAD_LOCKS.setdefault(lock_key, threading.RLock())
    with thread_lock:
        with directory_lock(
            root,
            name="run.lock",
            timeout=30.0,
            repo_root=repo,
        ):
            held.add(lock_key)
            try:
                yield
            finally:
                held.remove(lock_key)


def _locked_full_run(func):
    """Serialize public full-run mutations without changing their signatures."""
    @wraps(func)
    def wrapper(repo_root: Path, *args: Any, **kwargs: Any):
        session_id = kwargs.get("session_id")
        # Let the wrapped validator produce the stable missing-ID issue.
        if not session_id:
            return func(repo_root, *args, **kwargs)
        with _full_run_lock(Path(repo_root), str(session_id)):
            return func(repo_root, *args, **kwargs)

    return wrapper


def _expected_run_id(session_id: str) -> str:
    return f"full-run-{digest_key(session_id, prefix='run')}"


def _is_sha1(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA1_RE.fullmatch(value))


def _is_utc_iso8601(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return False
    offset = parsed.utcoffset()
    return offset is not None and offset.total_seconds() == 0


def _latest_utc_iso8601(current: str | None, candidate: Any) -> str | None:
    """Return the later valid UTC timestamp without letting old events rewind activity."""
    if not _is_utc_iso8601(candidate):
        return current
    candidate_text = str(candidate)
    candidate_dt = datetime.fromisoformat(candidate_text.replace("Z", "+00:00"))
    if (
        candidate_dt - datetime.now(timezone.utc)
    ).total_seconds() > MAX_EVENT_FUTURE_SKEW_SECONDS:
        return current
    if not current or not _is_utc_iso8601(current):
        return candidate_text
    current_dt = datetime.fromisoformat(current.replace("Z", "+00:00"))
    return candidate_text if candidate_dt > current_dt else current


def validate_event(
    event: Mapping[str, Any],
    *,
    expected_session_id: str | None = None,
    expected_branch: str | None = None,
    expected_start_head: str | None = None,
    seen_terminal: bool = False,
    exact_secret_values: frozenset[str] | set[str] | tuple[str, ...] | None = None,
) -> list[str]:
    errors: list[str] = []
    secret_detected = _contains_full_run_secret(
        event, exact_values=exact_secret_values
    )
    if secret_detected:
        errors.append("event contains secret-shaped content")
    for key in ("timestamp", "session_id", "branch", "head", "batch", "type", "summary"):
        if key not in event:
            errors.append(f"missing event field: {key}")
    timestamp = event.get("timestamp")
    if not _is_utc_iso8601(timestamp):
        errors.append("timestamp must be a UTC ISO-8601 string")
    else:
        parsed_timestamp = datetime.fromisoformat(
            str(timestamp).replace("Z", "+00:00")
        )
        if (
            parsed_timestamp - datetime.now(timezone.utc)
        ).total_seconds() > MAX_EVENT_FUTURE_SKEW_SECONDS:
            errors.append("timestamp exceeds allowed future clock skew")
    for key in ("session_id", "branch", "type", "summary"):
        if key in event and not isinstance(event.get(key), str):
            errors.append(f"{key} must be a string")
    for key in ("session_id", "branch"):
        if isinstance(event.get(key), str) and not event.get(key).strip():
            errors.append(f"{key} must be nonempty")
    if "head" in event and not _is_sha1(event.get("head")):
        errors.append("head must be an exact 40-character commit SHA")
    batch = event.get("batch")
    if isinstance(batch, bool) or not isinstance(batch, int) or batch < 0:
        errors.append("batch must be a non-boolean integer >= 0")
    etype = event.get("type")
    if etype not in EVENT_TYPES:
        errors.append("invalid event type")
    summary_value = event.get("summary")
    summary = summary_value if isinstance(summary_value, str) else ""
    if len(summary) > 500:
        errors.append("summary exceeds 500 chars")
    lowered = summary.lower()
    if not secret_detected and any(
        needle in lowered
        for needle in ("api_key=", "bearer ", "authorization:", "-----begin")
    ):
        errors.append("event contains secret-shaped content")
    if expected_session_id and event.get("session_id") != expected_session_id:
        errors.append("event session_id mismatch")
    if expected_branch and event.get("branch") != expected_branch:
        errors.append("event branch mismatch")
    if seen_terminal:
        errors.append("event appears after terminal event")
    return errors


def validate_run_report(
    report: Mapping[str, Any],
    *,
    expected_session_id: str | None = None,
    expected_branch: str | None = None,
    expected_start_head: str | None = None,
    require_complete_acceptance: bool = False,
    expected_run_id: str | None = None,
    expected_attempt: int | None = None,
    exact_secret_values: frozenset[str] | set[str] | tuple[str, ...] | None = None,
) -> list[str]:
    errors: list[str] = []
    if _contains_full_run_secret(report, exact_values=exact_secret_values):
        errors.append("report contains secret-shaped content")
    required = (
        "run_id",
        "attempt",
        "session_id",
        "branch",
        "start_head",
        "final_head",
        "status",
        "batches",
        "acceptance",
        "commits",
    )
    for key in required:
        if key not in report:
            errors.append(f"missing report field: {key}")
    for key in ("run_id", "session_id", "branch", "start_head", "final_head", "status"):
        if key in report and not isinstance(report.get(key), str):
            errors.append(f"{key} must be a string")
    for key in ("run_id", "session_id", "branch"):
        if isinstance(report.get(key), str) and not report.get(key).strip():
            errors.append(f"{key} must be nonempty")
    if "start_head" in report and not _is_sha1(report.get("start_head")):
        errors.append("start_head must be an exact 40-character commit SHA")
    final_head_value = report.get("final_head")
    if final_head_value and not _is_sha1(final_head_value):
        errors.append("final_head must be empty or an exact 40-character commit SHA")
    status = report.get("status")
    if status not in _REPORT_STATUSES:
        errors.append("invalid report status")
    if expected_session_id and report.get("session_id") != expected_session_id:
        errors.append("report session_id mismatch")
    if expected_branch and report.get("branch") != expected_branch:
        errors.append("report branch mismatch")
    if expected_start_head and report.get("start_head") != expected_start_head:
        errors.append("report start_head mismatch")
    if expected_run_id and report.get("run_id") != expected_run_id:
        errors.append("report run_id mismatch")
    attempt = report.get("attempt")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        errors.append("attempt must be a non-boolean integer >= 1")
    if expected_attempt is not None and report.get("attempt") != expected_attempt:
        errors.append("report attempt mismatch")
    # List-typed evidence surfaces must actually be lists.
    for list_key in ("batches", "acceptance", "commits", "blockers", "docs_changed", "remaining_risks"):
        if list_key in report and report[list_key] is not None and not isinstance(
            report[list_key], list
        ):
            errors.append(f"{list_key} must be a list")
    final_head = str(report.get("final_head") or "").strip()
    if status == "complete" and not final_head:
        errors.append("complete report requires nonempty final_head")
    if status == "complete":
        for key in ("batches", "commits"):
            value = report.get(key)
            if not isinstance(value, list) or not value:
                errors.append(f"complete report requires non-empty {key}")
        for key in ("blockers", "remaining_risks"):
            value = report.get(key)
            if value:
                errors.append(f"complete report requires empty {key}")
    batches = report.get("batches")
    if isinstance(batches, list):
        for i, item in enumerate(batches):
            if not isinstance(item, dict):
                errors.append(f"batches[{i}] must be an object")
                continue
            if status == "complete":
                for field_name in ("id", "status", "evidence"):
                    if field_name not in item:
                        errors.append(f"batches[{i}] missing {field_name}")
            batch_id = item.get("id")
            batch_status = item.get("status")
            batch_evidence = item.get("evidence")
            if "id" in item and (
                not isinstance(batch_id, str) or not batch_id.strip()
            ):
                errors.append(f"batches[{i}].id must be a nonempty string")
            if "status" in item and (
                not isinstance(batch_status, str) or not batch_status.strip()
            ):
                errors.append(f"batches[{i}].status must be a nonempty string")
            if "evidence" in item and not isinstance(batch_evidence, str):
                errors.append(f"batches[{i}].evidence must be a string")
            if status == "complete":
                if batch_status != "complete":
                    errors.append(f"batches[{i}].status must be complete")
                if not isinstance(batch_evidence, str) or not batch_evidence.strip():
                    errors.append(f"batches[{i}].evidence must be nonempty")
    commits = report.get("commits")
    if isinstance(commits, list):
        for i, item in enumerate(commits):
            if isinstance(item, str):
                if not _is_sha1(item):
                    errors.append(f"commits[{i}] must be an exact 40-character SHA")
                continue
            if not isinstance(item, dict):
                errors.append(f"commits[{i}] must be a SHA string or object")
                continue
            sha = item.get("sha")
            subject = item.get("subject")
            if not _is_sha1(sha):
                errors.append(f"commits[{i}].sha must be an exact 40-character SHA")
            if not isinstance(subject, str) or not subject.strip():
                errors.append(f"commits[{i}].subject must be a nonempty string")
    acceptance = report.get("acceptance")
    if acceptance is not None and not isinstance(acceptance, list):
        errors.append("acceptance must be a list")
    elif isinstance(acceptance, list):
        if status == "complete" and not acceptance:
            errors.append("complete report requires non-empty acceptance")
        seen_ids: set[str] = set()
        for i, item in enumerate(acceptance):
            if not isinstance(item, dict):
                errors.append(f"acceptance[{i}] must be an object")
                continue
            for field_name in ("id", "criterion", "met", "evidence"):
                if field_name not in item:
                    errors.append(f"acceptance[{i}] missing {field_name}")
            aid = str(item.get("id") or "").strip()
            criterion = str(item.get("criterion") or "").strip()
            if "id" in item and not isinstance(item.get("id"), str):
                errors.append(f"acceptance[{i}].id must be a string")
            if "criterion" in item and not isinstance(item.get("criterion"), str):
                errors.append(f"acceptance[{i}].criterion must be a string")
            if "met" in item and not isinstance(item.get("met"), bool):
                errors.append(f"acceptance[{i}].met must be a boolean")
            if "evidence" in item and not isinstance(item.get("evidence"), str):
                errors.append(f"acceptance[{i}].evidence must be a string")
            if status == "complete" and not aid:
                errors.append(f"acceptance[{i}] requires nonempty exact id")
            if status == "complete" and not criterion:
                errors.append(f"acceptance[{i}] requires nonempty criterion")
            if status == "complete" and item.get("met") is not True:
                errors.append(f"acceptance[{i}] must be met for complete report")
            if status == "complete" and not item.get("evidence"):
                errors.append(f"acceptance[{i}] requires evidence for complete report")
            if aid:
                if aid in seen_ids:
                    errors.append("duplicate acceptance id")
                seen_ids.add(aid)
            if item.get("met") is True and not item.get("evidence"):
                errors.append(f"acceptance[{i}] met without evidence")
    return errors


@dataclass
class ProcessFingerprint:
    pid: int
    pgid: int | None
    start_time: str | None
    executable: str | None
    session_id: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProcessFingerprint":
        return cls(
            pid=int(data["pid"]),
            pgid=data.get("pgid"),
            start_time=data.get("start_time"),
            executable=data.get("executable"),
            session_id=str(data.get("session_id") or ""),
        )


@dataclass
class FullRunState:
    session_id: str
    branch: str
    start_head: str
    worktree: str
    packet_path: str
    adapter: str = "grok-build"
    model: str = DEFAULT_MODEL
    permission_mode: str = DEFAULT_PERMISSION_MODE
    effort: str = DEFAULT_EFFORT
    executable: str = DEFAULT_EXECUTABLE
    create_session: bool = True
    check: bool = False
    max_turns: int = 80
    output_format: str = "json"
    yolo: bool = True
    credential_grant_names: list[str] = field(
        default_factory=lambda: sorted(DEFAULT_CREDENTIAL_GRANT_NAMES)
    )
    credential_granted_names: list[str] = field(default_factory=list)
    credential_grant_digests: dict[str, str] = field(default_factory=dict)
    status: str = "pending"
    batch: int | None = None
    head: str | None = None
    pid: int | None = None
    pgid: int | None = None
    fingerprint: dict[str, Any] | None = None
    heartbeat_at: str | None = None
    launched_at: str | None = None
    completed_at: str | None = None
    blocker: str | None = None
    next_action: str | None = None
    # ``driver_monitor_mode`` is canonical. ``driver_contract`` remains a
    # compatibility alias and must carry the same machine value.
    driver_monitor_mode: str = "parked_monitor"
    driver_contract: str = "parked_monitor"
    notes: list[str] = field(default_factory=list)
    last_argv: list[str] = field(default_factory=list)
    # Fixture-only: path to python fixture script (never masquerades as Grok).
    fixture_script: str | None = None
    # Protected base/remote refs snapped at prepare; verified unchanged at finalization.
    # Trusted Lane A is policy trust, not an OS Git sandbox — movement still blocks readiness.
    protected_refs: dict[str, str] = field(default_factory=dict)
    launch_start_head: str | None = None
    origin_url: str | None = None
    origin_config_digest: str | None = None
    initial_remote_feature_tip: str | None = None
    acceptance_ids: list[str] = field(default_factory=list)
    attempt: int = 1
    supervision_token: str | None = None
    supervisor_executable: str | None = None
    supervision_canary_passed: bool = False
    closed_process_identity: dict[str, Any] | None = None
    interruption_evidence: dict[str, Any] | None = None
    process_history: list[dict[str, Any]] = field(default_factory=list)
    exit_code: int | None = None
    exit_sidecar_pid: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FullRunState":
        known = set(cls.__dataclass_fields__)
        filtered = {k: v for k, v in data.items() if k in known}
        state = cls(**filtered)  # type: ignore[arg-type]
        state.driver_monitor_mode = "parked_monitor"
        state.driver_contract = state.driver_monitor_mode
        if state.launch_start_head is None:
            state.launch_start_head = state.start_head
        return state


def _state_secret_values(state: FullRunState) -> frozenset[str]:
    """Resolve current granted values in memory without serializing them."""
    return frozenset(
        value
        for name in state.credential_grant_names
        if (value := os.environ.get(name))
    )


def _credential_grant_digest(state: FullRunState, name: str, value: str) -> str:
    """Bind a launch credential value to this private supervisor attempt."""
    key = str(state.supervision_token or state.session_id).encode("utf-8")
    material = f"{name}\0{value}".encode("utf-8")
    return hmac.new(key, material, hashlib.sha256).hexdigest()


def _launch_grants_verified(state: FullRunState) -> bool:
    """True only when the current process can re-identify every launched grant."""
    granted = list(state.credential_granted_names or [])
    if not granted:
        return True
    if set(granted) != set(state.credential_grant_digests):
        return False
    for name in granted:
        value = os.environ.get(name)
        expected = state.credential_grant_digests.get(name)
        if not value or not expected:
            return False
        observed = _credential_grant_digest(state, name, value)
        if not hmac.compare_digest(observed, expected):
            return False
    return True


def build_full_run_env(
    *,
    state: FullRunState,
    root: Path,
    parent_env: Mapping[str, str] | None = None,
    credential_grant_names: Sequence[str] | None = None,
) -> dict[str, str]:
    """Minimal launch env: named essentials + named credential grants (no KEY=VALUE argv)."""
    parent = dict(parent_env if parent_env is not None else os.environ)
    env: dict[str, str] = {}
    for name in NON_SECRET_ESSENTIALS:
        if name in parent and parent[name] is not None:
            env[name] = str(parent[name])
    # Isolated temp under runtime dir when not provided.
    env.setdefault("TMPDIR", str(root / "worker-tmp"))
    env.setdefault("TMP", env["TMPDIR"])
    env.setdefault("TEMP", env["TMPDIR"])
    env.setdefault("HOME", str(root / "worker-home"))
    env.setdefault("PATH", parent.get("PATH", "/usr/bin:/bin"))
    env.setdefault("PYTHONUNBUFFERED", "1")
    grants = list(credential_grant_names or state.credential_grant_names or [])
    for name in grants:
        if name in parent and parent[name]:
            env[name] = str(parent[name])
    # Non-secret full-run contract values for real adapters and fixtures.
    # Packet requirement: adapters must use these paths for events/report/progress.
    env["ELVES_FULL_RUN_SESSION"] = state.session_id
    env["ELVES_FULL_RUN_RUN_ID"] = _expected_run_id(state.session_id)
    env["ELVES_FULL_RUN_EVENTS"] = str(root / "events.jsonl")
    env["ELVES_FULL_RUN_REPORT"] = str(root / "report.json")
    env["ELVES_FULL_RUN_TRANSCRIPT"] = str(root / "transcript.log")
    env["ELVES_FULL_RUN_BRANCH"] = state.branch
    env["ELVES_FULL_RUN_START_HEAD"] = state.start_head
    env["ELVES_FULL_RUN_WORKTREE"] = state.worktree
    env["ELVES_FULL_RUN_ATTEMPT"] = str(state.attempt)
    env["ELVES_DRIVER_MONITOR_MODE"] = "parked_monitor"
    if state.supervision_token:
        env["ELVES_FULL_RUN_SUPERVISION_MARKER"] = state.supervision_token
    return env


def _process_start_time(pid: int) -> str | None:
    """Best-effort process start fingerprint (macOS/Linux)."""
    # Linux /proc start ticks are precise enough to distinguish rapid PID reuse;
    # prefer them over ps(1)'s second-granularity lstart rendering.
    try:
        stat_path = Path(f"/proc/{pid}/stat")
        if stat_path.is_file():
            fields = stat_path.read_text().split()
            if len(fields) >= 22:
                return fields[21]
    except OSError:
        pass
    try:
        # macOS: ps -o lstart= -p PID
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except OSError:
        pass
    return None


def _process_executable(pid: int) -> str | None:
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError:
        pass
    try:
        result = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().split()[0]
    except OSError:
        pass
    return None


def capture_fingerprint(
    *,
    pid: int,
    pgid: int | None,
    session_id: str,
    executable_hint: str | None = None,
) -> ProcessFingerprint:
    return ProcessFingerprint(
        pid=pid,
        pgid=pgid,
        start_time=_process_start_time(pid),
        executable=_process_executable(pid) or executable_hint,
        session_id=session_id,
    )


def verify_fingerprint(
    fp: ProcessFingerprint | Mapping[str, Any],
    *,
    expected_session_id: str | None = None,
) -> tuple[bool, str]:
    if isinstance(fp, Mapping):
        try:
            fp = ProcessFingerprint.from_dict(fp)
        except (KeyError, TypeError, ValueError) as exc:
            return False, f"invalid fingerprint: {exc}"
    if fp.pid <= 0:
        return False, "invalid pid"
    if not str(fp.session_id or "").strip():
        return False, "session_id missing on fingerprint"
    if expected_session_id and str(fp.session_id) != str(expected_session_id):
        return False, "session_id mismatch on fingerprint"
    if not fp.start_time:
        return False, "process start_time missing from fingerprint"
    if not fp.executable:
        return False, "process executable missing from fingerprint"
    if fp.pgid is None:
        return False, "process pgid missing from fingerprint"
    try:
        os.kill(fp.pid, 0)
    except ProcessLookupError:
        return False, "pid not alive"
    except PermissionError:
        # Alive but not owned — still verify start time if possible.
        pass
    current_start = _process_start_time(fp.pid)
    if not current_start:
        return False, "live process start_time unreadable"
    if current_start != fp.start_time:
        return False, "pid start_time mismatch (reused PID)"
    current_exe = _process_executable(fp.pid)
    if not current_exe:
        return False, "live process executable unreadable"
    # Compare the observed command identity exactly when possible. Resolving only
    # basenames would accept a reused PID running a different same-named binary.
    expected_exe = str(Path(fp.executable).expanduser())
    observed_exe = str(Path(current_exe).expanduser())
    try:
        expected_exe = str(Path(expected_exe).resolve())
        observed_exe = str(Path(observed_exe).resolve())
    except OSError:
        pass
    if expected_exe != observed_exe:
        return False, "pid executable mismatch"
    # PGID membership: stored pgid must match live process group of the PID.
    if fp.pgid is not None:
        try:
            live_pgid = os.getpgid(fp.pid)
            if int(live_pgid) != int(fp.pgid):
                return False, "pgid membership mismatch"
        except OSError:
            return False, "pgid membership unreadable"
    return True, "ok"


def _signal_verified_supervisor(
    fingerprint: Mapping[str, Any],
    *,
    expected_session_id: str,
    signum: int,
) -> bool:
    """Signal only the exact live supervisor identity, using pidfd on Linux."""
    try:
        pid = int(fingerprint.get("pid") or 0)
    except (TypeError, ValueError) as exc:
        raise ValidationIssue(
            "full_run_fingerprint_mismatch",
            "Refusing signal: supervisor fingerprint PID is invalid",
        ) from exc
    if pid <= 0:
        raise ValidationIssue(
            "full_run_fingerprint_mismatch",
            "Refusing signal: supervisor fingerprint PID is invalid",
        )

    ok, reason = verify_fingerprint(
        fingerprint,
        expected_session_id=expected_session_id,
    )
    if not ok:
        if reason == "pid not alive":
            return False
        raise ValidationIssue(
            "full_run_fingerprint_mismatch",
            f"Refusing signal: {reason}",
            hint="PID may have been reused; investigate before signaling",
        )

    pidfd: int | None = None
    try:
        if sys.platform.startswith("linux") and hasattr(os, "pidfd_open"):
            try:
                pidfd = os.pidfd_open(pid, 0)
            except ProcessLookupError:
                return False
            except OSError as exc:
                raise ValidationIssue(
                    "full_run_pidfd_unavailable",
                    f"Cannot bind the live supervisor process handle: {type(exc).__name__}",
                ) from exc

        # Revalidate after acquiring the kernel-bound handle (or immediately
        # before Darwin's standard-library signal fallback). This rejects reuse
        # between the first liveness probe and the signal operation.
        ok, reason = verify_fingerprint(
            fingerprint,
            expected_session_id=expected_session_id,
        )
        if not ok:
            if reason == "pid not alive":
                return False
            raise ValidationIssue(
                "full_run_fingerprint_mismatch",
                f"Refusing signal after identity recheck: {reason}",
                hint="PID may have been reused; investigate before signaling",
            )

        try:
            if pidfd is not None and hasattr(signal, "pidfd_send_signal"):
                signal.pidfd_send_signal(pidfd, signum)
            else:
                os.kill(pid, signum)
        except ProcessLookupError:
            return False
        except (PermissionError, OSError) as exc:
            raise ValidationIssue(
                "full_run_signal_failed",
                f"Unable to signal the verified supervisor: {type(exc).__name__}",
            ) from exc
        return True
    finally:
        if pidfd is not None:
            os.close(pidfd)


def read_exit_record(
    root: Path,
    *,
    repo_root: Path | None = None,
) -> dict[str, Any] | None:
    """Host-owned exit sidecar record written after the provider process exits."""
    path = Path(root) / "exit_record.json"
    if repo_root is not None:
        if not repo_regular_file_exists(Path(repo_root), path):
            return None
    elif not path.exists() and not path.is_symlink():
        return None
    # Malformed data is materially different from an absent record: callers
    # must wake/fail instead of parking forever after a corrupted provider exit.
    return _read_bounded_json_object(
        path,
        label="exit record",
        repo_root=repo_root,
    )


def _validate_exit_record(
    record: Mapping[str, Any],
    state: FullRunState,
) -> list[str]:
    """Validate the durable record against launcher-owned supervisor identity."""
    errors: list[str] = []
    if str(record.get("session_id") or "") != state.session_id:
        errors.append("exit record session_id mismatch")
    try:
        if int(record.get("pid") or 0) != int(state.pid or 0):
            errors.append("exit record pid mismatch")
    except (TypeError, ValueError):
        errors.append("exit record pid invalid")
    try:
        if int(record.get("pgid") or 0) != int(state.pgid or 0):
            errors.append("exit record pgid mismatch")
    except (TypeError, ValueError):
        errors.append("exit record pgid invalid")

    expected_provider = str((state.last_argv or [state.executable])[0] or "")
    if str(record.get("provider_executable") or "") != expected_provider:
        errors.append("exit record provider executable mismatch")

    exit_code = record.get("exit_code")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        errors.append("exit record requires an integer exit_code")
    if record.get("attempt") != state.attempt:
        errors.append("exit record attempt mismatch")
    if record.get("supervision_token") != state.supervision_token:
        errors.append("exit record supervision token mismatch")
    if record.get("descendants_absent") is not True:
        errors.append("exit record does not prove recursive descendant absence")
    if record.get("supervision_error") not in {None, ""}:
        errors.append("exit record reports recursive supervision failure")

    observed_fp = record.get("fingerprint")
    expected_fp = state.fingerprint or {}
    if not isinstance(observed_fp, Mapping):
        errors.append("exit record fingerprint missing")
    else:
        for field_name in ("pid", "pgid", "start_time", "executable", "session_id"):
            if observed_fp.get(field_name) != expected_fp.get(field_name):
                errors.append(f"exit record fingerprint {field_name} mismatch")
    return errors


def _reap_supervisor_if_child(pid: int | None) -> None:
    """Best-effort in-process reap; separate monitor CLIs are not the parent."""
    if not pid:
        return
    try:
        os.waitpid(int(pid), os.WNOHANG)
    except (ChildProcessError, OSError):
        pass


def write_exit_record(
    root: Path,
    record: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> Path:
    path = Path(root) / "exit_record.json"
    payload = dict(record)
    payload.setdefault("completed_at", _utc_now())
    atomic_write_json(path, payload, repo_root=repo_root)
    return path


_PROVIDER_SUPERVISOR_SCRIPT = r"""
import errno, json, os, signal, stat, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

exit_path = Path(sys.argv[1])
fingerprint_path = Path(sys.argv[2])
if (
    exit_path.parent != fingerprint_path.parent
    or exit_path.name != "exit_record.json"
    or fingerprint_path.name != "supervisor.fingerprint.json"
):
    raise SystemExit(126)
runtime_flags = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_DIRECTORY", 0)
)
runtime_fd = os.open(exit_path.parent, runtime_flags)
session_id = sys.argv[3]
provider_argv = json.loads(sys.argv[4])
attempt = int(sys.argv[5])
supervision_backend = sys.argv[6]
supervision_token = sys.argv[7]
marker = "ELVES_FULL_RUN_SUPERVISION_MARKER=" + supervision_token
marker_bytes = marker.encode("utf-8")
provider_pid = None
provider = None
exit_code = 127
stop_signal = None
known_identities = {}
historical_pids = set()
supervision_error = None

def request_stop(signum, _frame):
    global stop_signal
    stop_signal = int(signum)

signal.signal(signal.SIGTERM, request_stop)
signal.signal(signal.SIGINT, request_stop)

PROC_SKIP_ERRNOS = {errno.EACCES, errno.EPERM, errno.ENOENT, errno.ESRCH}

def linux_records():
    proc_root = Path(supervision_backend)
    info = proc_root.lstat()
    if (
        proc_root.resolve() != Path("/proc")
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != 0
        or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        or not (proc_root / "self" / "environ").exists()
        or not (proc_root / "self" / "stat").exists()
    ):
        raise RuntimeError("unqualified_procfs")
    records = {}
    marked = set()
    with os.scandir(proc_root) as entries:
        for entry in entries:
            if not entry.name.isdigit():
                continue
            pid = int(entry.name)
            if pid <= 0:
                continue
            proc_dir = proc_root / entry.name
            try:
                raw = (proc_dir / "stat").read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError as exc:
                if exc.errno in PROC_SKIP_ERRNOS:
                    continue
                raise
            close = raw.rfind(")")
            fields = raw[close + 1:].strip().split() if close >= 0 else []
            if len(fields) < 20:
                raise RuntimeError("malformed_proc_stat:%s" % pid)
            try:
                state, ppid, pgid, started = (
                    fields[0], int(fields[1]), int(fields[2]), fields[19]
                )
            except ValueError as exc:
                raise RuntimeError("malformed_proc_identity:%s" % pid) from exc
            records[pid] = (ppid, pgid, state, "", started)
            if state == "Z":
                continue
            try:
                environ = (proc_dir / "environ").read_bytes()
            except OSError as exc:
                # Permission/hidepid policy and ordinary exit races are expected.
                # The launcher canary proves our own supervision domain is readable.
                if exc.errno in PROC_SKIP_ERRNOS:
                    continue
                raise
            if marker_bytes in environ.split(b"\0"):
                marked.add(pid)
    return records, marked, None

def darwin_records():
    probe = None
    try:
        probe = subprocess.Popen(
            [
                supervision_backend,
                "e",
                "-axo",
                "pid=,ppid=,pgid=,state=,lstart=,command=",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = probe.communicate(timeout=2.0)
    except Exception as exc:
        if probe is not None:
            try:
                probe.kill()
                probe.wait(timeout=0.2)
            except Exception:
                pass
        raise
    if probe.returncode != 0:
        raise RuntimeError(
            "scan_exit:%s:%s" % (
                probe.returncode,
                stderr.decode("utf-8", errors="replace")[:160],
            )
        )
    records = {}
    marked = set()
    for raw in stdout.decode("utf-8", errors="replace").splitlines():
        fields = raw.strip().split(None, 9)
        if len(fields) < 10:
            continue
        try:
            pid, ppid, pgid = (int(fields[index]) for index in range(3))
        except ValueError:
            continue
        started = " ".join(fields[4:9])
        command = fields[9]
        records[pid] = (ppid, pgid, fields[3], command, started)
        if fields[3] != "Z" and marker in command.split():
            marked.add(pid)
    return records, marked, probe.pid

def current_records():
    global supervision_error
    try:
        if len(supervision_token) != 48 or any(
            char not in "0123456789abcdef" for char in supervision_token
        ):
            raise RuntimeError("invalid_supervision_token")
        if sys.platform.startswith("linux"):
            records, marked, scanner_pid = linux_records()
        elif sys.platform == "darwin":
            records, marked, scanner_pid = darwin_records()
        else:
            raise RuntimeError("unsupported_supervision_platform:%s" % sys.platform)
    except Exception as exc:
        supervision_error = "scan_failed:%s:%s" % (type(exc).__name__, exc)
        return {}, set(), None
    return records, marked, scanner_pid

def scan_alive():
    global supervision_error
    records, marked, scanner_pid = current_records()
    if supervision_error is not None:
        return set()
    active_known = {
        pid for pid, started in known_identities.items()
        if pid in records
        and records[pid][2] != "Z"
        and records[pid][4] == started
    }
    for pid in list(known_identities):
        if pid not in active_known:
            known_identities.pop(pid, None)
    discovered = set(marked)
    if provider_pid and provider is not None and provider.poll() is None:
        discovered.add(provider_pid)
    changed = True
    while changed:
        before = len(discovered)
        discovered.update(
            pid for pid, (ppid, _pgid, state, _command, _started) in records.items()
            if state != "Z" and (ppid in discovered or ppid in active_known)
        )
        changed = len(discovered) != before
    discovered.discard(os.getpid())
    if scanner_pid:
        discovered.discard(scanner_pid)
    for pid in discovered:
        record = records.get(pid)
        if record is None or record[2] == "Z":
            continue
        started = record[4]
        prior = known_identities.get(pid)
        if prior is not None and prior != started:
            # PID was reused between discovery passes. Never adopt or signal the
            # replacement merely because its integer identifier is familiar.
            continue
        known_identities[pid] = started
        historical_pids.add(pid)
    return {
        pid for pid, started in known_identities.items()
        if pid in records and records[pid][2] != "Z" and records[pid][4] == started
    }

def signal_pids(pids, signum):
    global supervision_error
    for pid in sorted(pids, reverse=True):
        if pid == os.getpid():
            continue
        expected_start = known_identities.get(pid)
        if expected_start is None:
            continue
        pidfd = None
        if sys.platform.startswith("linux") and hasattr(os, "pidfd_open"):
            try:
                # Open the process handle before the final identity read. If the
                # numeric PID was reused, the following start-time comparison
                # rejects the replacement; if it exits afterward, the pidfd stays
                # bound to the original process and cannot target the replacement.
                pidfd = os.pidfd_open(pid, 0)
            except ProcessLookupError:
                continue
            except OSError as exc:
                supervision_error = "pidfd_open_failed:%s:%s" % (pid, exc)
                return
        records, _marked, _scanner_pid = current_records()
        if supervision_error is not None:
            if pidfd is not None:
                os.close(pidfd)
            return
        current = records.get(pid)
        if (
            current is None
            or current[2] == "Z"
            or current[4] != expected_start
        ):
            if pidfd is not None:
                os.close(pidfd)
            continue
        try:
            if pidfd is not None and hasattr(signal, "pidfd_send_signal"):
                signal.pidfd_send_signal(pidfd, signum)
            else:
                # Darwin has no pidfd. Re-reading the exact start identity for
                # each target immediately before this call is the strongest
                # standard-library boundary available; never signal from a
                # batch-cached process table.
                os.kill(pid, signum)
        except ProcessLookupError:
            pass
        except OSError as exc:
            supervision_error = "signal_failed:%s:%s" % (pid, exc)
        finally:
            if pidfd is not None:
                os.close(pidfd)

def terminate_descendants():
    alive = scan_alive()
    signal_pids(alive, signal.SIGTERM)
    deadline = time.monotonic() + 0.5
    while alive and time.monotonic() < deadline and supervision_error is None:
        time.sleep(0.03)
        alive = scan_alive()
    if alive:
        signal_pids(alive, signal.SIGKILL)
    deadline = time.monotonic() + 0.75
    while time.monotonic() < deadline and supervision_error is None:
        alive = scan_alive()
        if not alive:
            return True
        signal_pids(alive, signal.SIGKILL)
        time.sleep(0.03)
    return False

try:
    provider = subprocess.Popen(
        provider_argv,
        stdin=subprocess.DEVNULL,
        close_fds=True,
    )
    provider_pid = provider.pid
    scan_alive()
    while provider.poll() is None and stop_signal is None:
        scan_alive()
        if supervision_error is not None:
            break
        time.sleep(0.03)
    if stop_signal is not None:
        exit_code = 128 + stop_signal
    elif supervision_error is not None:
        exit_code = 125
    else:
        exit_code = int(provider.returncode)
except OSError:
    exit_code = 127

descendants_absent = terminate_descendants()
if not descendants_absent and exit_code == 0:
    exit_code = 125
try:
    if provider_pid:
        provider.wait(timeout=0.2)
except Exception:
    pass

# The launcher writes the supervisor fingerprint immediately after spawning us.
# Wait briefly so even a provider that exits instantly records the exact identity
# that monitor/stop will validate rather than self-certifying a second identity.
fingerprint = {}
deadline = time.monotonic() + 1.0
while time.monotonic() < deadline:
    fingerprint_fd = None
    try:
        fingerprint_fd = os.open(
            fingerprint_path.name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=runtime_fd,
        )
        fingerprint_info = os.fstat(fingerprint_fd)
        if (
            not stat.S_ISREG(fingerprint_info.st_mode)
            or fingerprint_info.st_nlink != 1
            or fingerprint_info.st_size > 65536
        ):
            raise OSError("unsafe_fingerprint_record")
        with os.fdopen(fingerprint_fd, "rb", closefd=False) as fingerprint_handle:
            fingerprint_raw = fingerprint_handle.read(65537)
        if len(fingerprint_raw) > 65536:
            raise OSError("oversized_fingerprint_record")
        fingerprint = json.loads(fingerprint_raw.decode("utf-8"))
        if fingerprint:
            break
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        pass
    finally:
        if fingerprint_fd is not None:
            os.close(fingerprint_fd)
    time.sleep(0.01)

payload = {
    "pid": os.getpid(),
    "pgid": os.getpgrp(),
    "provider_pid": provider_pid,
    "session_id": session_id,
    "provider_executable": provider_argv[0] if provider_argv else None,
    "attempt": attempt,
    "supervision_token": supervision_token,
    "supervised_pids": sorted(historical_pids),
    "descendants_absent": bool(descendants_absent),
    "supervision_error": supervision_error,
    "interrupted_signal": stop_signal,
    "fingerprint": fingerprint,
    "exit_code": exit_code,
    "completed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
}
serialized = json.dumps(payload, separators=(",", ":")).encode("utf-8")
tmp_name = ".exit_record.%s.%s.tmp" % (os.getpid(), time.time_ns())
tmp_fd = None
try:
    tmp_fd = os.open(
        tmp_name,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=runtime_fd,
    )
    offset = 0
    while offset < len(serialized):
        offset += os.write(tmp_fd, serialized[offset:])
    os.fsync(tmp_fd)
    os.close(tmp_fd)
    tmp_fd = None
    os.replace(
        tmp_name,
        exit_path.name,
        src_dir_fd=runtime_fd,
        dst_dir_fd=runtime_fd,
    )
finally:
    if tmp_fd is not None:
        os.close(tmp_fd)
    try:
        os.unlink(tmp_name, dir_fd=runtime_fd)
    except FileNotFoundError:
        pass
    os.close(runtime_fd)

# Preserve the provider result for shell/operator diagnostics. Negative return
# codes indicate signals; map them into the conventional 128+signal range.
if exit_code < 0:
    raise SystemExit(min(255, 128 + abs(exit_code)))
raise SystemExit(min(255, exit_code))
"""


def _provider_supervisor_argv(
    *,
    root: Path,
    session_id: str,
    provider_argv: Sequence[str],
    attempt: int,
    supervisor_executable: str,
    supervision_token: str,
) -> list[str]:
    """Build a parent supervisor that waits and records the provider's real exit."""
    return [
        sys.executable,
        "-c",
        _PROVIDER_SUPERVISOR_SCRIPT,
        str(root / "exit_record.json"),
        str(root / "supervisor.fingerprint.json"),
        session_id,
        json.dumps(list(provider_argv)),
        str(attempt),
        supervisor_executable,
        supervision_token,
    ]


def _origin_present(repo_root: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def _canonical_origin_url(repo_root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "remote", "get-url", "--all", "origin"],
        capture_output=True,
        text=True,
        check=False,
    )
    urls = [row.strip() for row in result.stdout.splitlines() if row.strip()]
    if result.returncode != 0 or len(urls) != 1:
        raise ValidationIssue(
            "full_run_origin_required",
            "Production full-run requires exactly one canonical origin URL",
        )
    url = urls[0]
    if any(ord(ch) < 32 for ch in url) or re.search(r"https?://[^/\s]*@", url, re.I):
        raise ValidationIssue(
            "full_run_origin_url_credentialed",
            "Origin URL must be non-credentialed and free of control characters",
        )
    push = subprocess.run(
        ["git", "-C", str(repo_root), "remote", "get-url", "--push", "--all", "origin"],
        capture_output=True,
        text=True,
        check=False,
    )
    push_urls = [row.strip() for row in push.stdout.splitlines() if row.strip()]
    if push.returncode != 0 or push_urls != [url]:
        raise ValidationIssue(
            "full_run_origin_push_url_mismatch",
            "Origin fetch and push URLs must resolve to the same canonical non-credentialed URL",
        )
    return url


def _origin_config_digest(repo_root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "config", "--local", "--get-regexp", r"^remote\.origin\."],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in {0, 1}:
        raise ValidationIssue(
            "full_run_origin_config_unavailable",
            (result.stderr or "Cannot inspect origin config").strip(),
        )
    return hashlib.sha256(result.stdout.encode("utf-8")).hexdigest()


def _remote_refs(
    repo_root: Path,
    *,
    patterns: Sequence[str] = (),
) -> dict[str, str]:
    if not _origin_present(repo_root):
        return {}
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "ls-remote", "origin", *patterns],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValidationIssue(
            "full_run_remote_refs_unavailable",
            f"Cannot inspect origin refs safely: {exc}",
        ) from exc
    if result.returncode != 0:
        raise ValidationIssue(
            "full_run_remote_refs_unavailable",
            (result.stderr or result.stdout or "git ls-remote origin failed").strip(),
        )
    refs: dict[str, str] = {}
    for row in result.stdout.splitlines():
        parts = row.split(None, 1)
        if len(parts) == 2:
            refs[f"remote::origin::{parts[1].strip()}"] = parts[0].strip()
    return refs


def _github_provider_managed_ref(origin_url: str, ref_name: str) -> bool:
    """GitHub owns refs/pull/*; feature pushes legitimately regenerate them."""
    raw = str(origin_url or "").strip()
    host = ""
    if "://" in raw:
        host = str(urlsplit(raw).hostname or "")
    else:
        # Git's SCP-style transport: [user@]host:path. Absolute/relative local
        # paths and file:// URLs must never gain provider-managed exemptions.
        match = re.fullmatch(r"(?:[^@/\s]+@)?(?P<host>[^:/\s]+):.+", raw)
        if match:
            host = match.group("host")
    github_origin = host.rstrip(".").lower() == "github.com"
    return github_origin and ref_name.startswith("refs/pull/")


def snapshot_protected_refs(
    repo_root: Path,
    *,
    feature_branch: str | None = None,
) -> dict[str, str]:
    """Snapshot every ref namespace except the exact assigned feature refs."""
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "for-each-ref",
            "--format=%(refname) %(objectname)",
            "refs",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValidationIssue(
            "full_run_ref_snapshot_failed",
            (result.stderr or result.stdout or "git for-each-ref failed").strip(),
        )
    excluded = set()
    if feature_branch:
        excluded = {
            f"refs/heads/{feature_branch}",
            f"refs/remotes/origin/{feature_branch}",
        }
    snaps: dict[str, str] = {}
    for row in result.stdout.splitlines():
        parts = row.split(None, 1)
        if len(parts) == 2 and parts[0] not in excluded:
            snaps[parts[0]] = parts[1]
    remote = _remote_refs(repo_root)
    origin_url = _canonical_origin_url(repo_root) if remote else ""
    feature_remote = f"remote::origin::refs/heads/{feature_branch}" if feature_branch else None
    for key, value in remote.items():
        remote_ref = key.removeprefix("remote::origin::")
        if key != feature_remote and not _github_provider_managed_ref(
            origin_url, remote_ref
        ):
            snaps[key] = value
    return snaps


def verify_protected_refs_unchanged(
    repo_root: Path,
    expected: Mapping[str, str],
    *,
    feature_branch: str | None = None,
) -> list[str]:
    """Any observed protected-ref movement blocks readiness (policy trust, not OS sandbox)."""
    errors: list[str] = []
    current = snapshot_protected_refs(repo_root, feature_branch=feature_branch)
    for ref in sorted(set(current) - set(expected)):
        errors.append(f"new protected ref created: {ref}")
    for ref, tip in expected.items():
        now = current.get(ref)
        if now is None:
            errors.append(f"protected ref missing at finalization: {ref}")
        elif now != tip:
            errors.append(f"protected ref moved: {ref} was {tip[:12]} now {now[:12]}")
    return errors


def _git_head(cwd: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    tip = (result.stdout or "").strip()
    return tip or None


def _git_branch(cwd: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), "branch", "--show-current"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None


def _is_ancestor(cwd: Path, ancestor: str, tip: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), "merge-base", "--is-ancestor", ancestor, tip],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0


def _git_common_dir(cwd: Path) -> Path | None:
    result = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    path = Path(result.stdout.strip())
    if not path.is_absolute():
        path = Path(cwd) / path
    return path.resolve()


def _assert_clean_worktree(worktree: Path) -> None:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(worktree),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValidationIssue(
            "full_run_worktree_status_failed",
            (result.stderr or result.stdout or "git status failed").strip(),
        )
    dirty_rows = []
    for row in result.stdout.splitlines():
        path_text = row[3:].strip() if len(row) >= 4 else row.strip()
        # Host-owned ignored runtime state is allowed even in repos whose local
        # ignore file has not yet been conditioned. Product/untracked files are not.
        if path_text.startswith(".elves/runtime/"):
            continue
        dirty_rows.append(row)
    if dirty_rows:
        raise ValidationIssue(
            "full_run_worktree_dirty",
            "Production full-run requires a clean tracked and untracked worktree",
            hint="\n".join(dirty_rows)[:500],
        )


def _feature_remote_tip(repo_root: Path, branch: str) -> str | None:
    key = f"remote::origin::refs/heads/{branch}"
    return _remote_refs(repo_root, patterns=[f"refs/heads/{branch}"]).get(key)


def _assert_origin_binding(
    repo_root: Path,
    state: FullRunState,
    *,
    expected_feature_tip: str | None,
) -> str | None:
    url = _canonical_origin_url(repo_root)
    digest = _origin_config_digest(repo_root)
    if state.origin_url != url or state.origin_config_digest != digest:
        raise ValidationIssue(
            "full_run_origin_binding_changed",
            "Origin URL/config changed after full-run preparation",
        )
    remote_tip = _feature_remote_tip(repo_root, state.branch)
    if remote_tip != expected_feature_tip:
        raise ValidationIssue(
            "full_run_remote_feature_mismatch",
            "Origin feature tip does not equal the required safe checkpoint",
        )
    return remote_tip


def _staged_acceptance_ids(packet_path: Path) -> list[str]:
    try:
        raw = _read_bounded_regular_bytes(
            packet_path,
            max_bytes=MAX_PACKET_BYTES,
            label="full-run packet",
        )
        text = raw.decode("utf-8")
    except StorageError as exc:
        raise ValidationIssue("full_run_packet_unreadable", exc.message) from exc
    except UnicodeDecodeError as exc:
        raise ValidationIssue(
            "full_run_packet_encoding",
            "Full-run packet must be valid UTF-8",
            path=str(packet_path),
        ) from exc
    if packet_path.suffix.lower() == ".json":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValidationIssue(
                "full_run_packet_invalid_json",
                "JSON full-run packet must contain one valid object",
                path=str(packet_path),
            ) from exc
        if not isinstance(payload, Mapping):
            raise ValidationIssue(
                "full_run_packet_invalid_json",
                "JSON full-run packet must contain one object",
                path=str(packet_path),
            )
        rows = payload.get("acceptance")
        if rows is None:
            return []
        if not isinstance(rows, list):
            raise ValidationIssue(
                "full_run_acceptance_invalid",
                "JSON packet acceptance must be an array of definition objects",
                path=str(packet_path),
            )
        ids: list[str] = []
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                raise ValidationIssue(
                    "full_run_acceptance_invalid",
                    f"JSON packet acceptance[{index}] must be an object",
                    path=str(packet_path),
                )
            acceptance_id = row.get("id")
            criterion = row.get("criterion")
            if (
                not isinstance(acceptance_id, str)
                or not _ACCEPTANCE_ID_RE.fullmatch(acceptance_id.strip())
                or not isinstance(criterion, str)
                or not criterion.strip()
            ):
                raise ValidationIssue(
                    "full_run_acceptance_invalid",
                    f"JSON packet acceptance[{index}] requires a stable id and nonempty criterion",
                    path=str(packet_path),
                )
            ids.append(acceptance_id.strip())
        return ids
    # Count only canonical definition rows. Inline references and the required
    # report example may repeat ids without defining a second criterion.
    return [match.group("id") for match in _ACCEPTANCE_DEFINITION_RE.finditer(text)]


_PROC_SCAN_SKIP_ERRNOS = frozenset(
    {errno.EACCES, errno.EPERM, errno.ENOENT, errno.ESRCH}
)


def _qualified_process_supervisor() -> Path:
    """Return the trusted platform process-inspection backend.

    Linux uses procfs directly because GNU and BSD ``ps`` intentionally have
    incompatible environment-display syntax.  macOS retains a root-owned BSD
    ``ps`` binary.  Unsupported platforms fail closed instead of guessing a
    mixed command line.
    """
    if sys.platform.startswith("linux"):
        candidate = Path("/proc")
        try:
            info = candidate.lstat()
        except OSError as exc:
            raise ValidationIssue(
                "full_run_supervision_unavailable",
                f"Production full-run requires trusted Linux procfs: {exc}",
            ) from exc
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != 0
            or (info.st_mode & (stat.S_IWGRP | stat.S_IWOTH))
            or candidate.resolve() != candidate
            or not (candidate / "self" / "environ").exists()
            or not (candidate / "self" / "stat").exists()
        ):
            raise ValidationIssue(
                "full_run_supervision_unavailable",
                "Production full-run requires a root-owned, non-writable /proc",
            )
        return candidate
    if sys.platform != "darwin":
        raise ValidationIssue(
            "full_run_supervision_unavailable",
            f"Recursive process supervision is unsupported on {sys.platform}",
        )
    for candidate in (Path("/bin/ps"), Path("/usr/bin/ps")):
        try:
            info = candidate.stat()
        except OSError:
            continue
        if (
            stat.S_ISREG(info.st_mode)
            and info.st_uid == 0
            and not (info.st_mode & (stat.S_IWGRP | stat.S_IWOTH))
            and os.access(candidate, os.X_OK)
        ):
            return candidate.resolve()
    raise ValidationIssue(
        "full_run_supervision_unavailable",
        "Production full-run requires a trusted system BSD ps for recursive supervision",
    )


def _linux_proc_state(proc_dir: Path) -> str | None:
    """Read one Linux proc stat state, tolerating normal exit/permission races."""
    try:
        raw = (proc_dir / "stat").read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        if exc.errno in _PROC_SCAN_SKIP_ERRNOS:
            return None
        raise ValidationIssue(
            "full_run_supervision_scan_failed",
            f"Cannot inspect {proc_dir}/stat: {exc}",
        ) from exc
    close = raw.rfind(")")
    fields = raw[close + 1 :].strip().split() if close >= 0 else []
    if not fields:
        raise ValidationIssue(
            "full_run_supervision_scan_failed",
            f"Malformed process stat record at {proc_dir}/stat",
        )
    return fields[0]


def _scan_linux_proc_supervision_pids(proc_root: Path, token: str) -> set[int]:
    marker = f"ELVES_FULL_RUN_SUPERVISION_MARKER={token}".encode("utf-8")
    try:
        entries = list(os.scandir(proc_root))
    except OSError as exc:
        raise ValidationIssue(
            "full_run_supervision_scan_failed",
            f"Cannot enumerate trusted procfs: {exc}",
        ) from exc
    found: set[int] = set()
    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid <= 0 or pid == os.getpid():
            continue
        proc_dir = proc_root / entry.name
        state = _linux_proc_state(proc_dir)
        if state is None or state == "Z":
            continue
        try:
            environ = (proc_dir / "environ").read_bytes()
        except OSError as exc:
            # hidepid/ptrace policy and ordinary process-exit races are expected.
            # The same-UID canary proves our own supervision domain is readable.
            if exc.errno in _PROC_SCAN_SKIP_ERRNOS:
                continue
            raise ValidationIssue(
                "full_run_supervision_scan_failed",
                f"Cannot inspect {proc_dir}/environ: {exc}",
            ) from exc
        if marker in environ.split(b"\0"):
            found.add(pid)
    return found


def _scan_bsd_ps_supervision_pids(executable: Path, token: str) -> set[int]:
    marker = f"ELVES_FULL_RUN_SUPERVISION_MARKER={token}"
    try:
        result = subprocess.run(
            [str(executable), "e", "-axo", "pid=,ppid=,pgid=,command="],
            capture_output=True,
            text=True,
            check=False,
            timeout=2.0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValidationIssue(
            "full_run_supervision_scan_failed", f"Recursive process scan failed: {exc}"
        ) from exc
    if result.returncode != 0:
        raise ValidationIssue(
            "full_run_supervision_scan_failed",
            (result.stderr or "trusted ps scan failed").strip(),
        )
    found: set[int] = set()
    for row in result.stdout.splitlines():
        fields = row.strip().split(None, 3)
        if len(fields) < 4 or marker not in fields[3].split():
            continue
        try:
            pid = int(fields[0])
        except ValueError:
            continue
        if pid != os.getpid():
            found.add(pid)
    return found


def _scan_supervision_pids(executable: str | Path, token: str) -> set[int]:
    if not re.fullmatch(r"[0-9a-f]{48}", str(token or "")):
        raise ValidationIssue(
            "full_run_supervision_scan_failed",
            "Recursive supervision token is missing or malformed",
        )
    qualified = _qualified_process_supervisor()
    observed = Path(executable).resolve()
    if observed != qualified:
        raise ValidationIssue(
            "full_run_supervision_executable_changed",
            "Recorded recursive supervision backend is not currently qualified",
        )
    if sys.platform.startswith("linux"):
        return _scan_linux_proc_supervision_pids(qualified, token)
    return _scan_bsd_ps_supervision_pids(qualified, token)


def _run_supervision_canary(executable: Path) -> bool:
    token = secrets.token_hex(24)
    env = dict(os.environ)
    env["ELVES_FULL_RUN_SUPERVISION_MARKER"] = token
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(1)"],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    try:
        deadline = time.monotonic() + 0.75
        while time.monotonic() < deadline:
            if proc.pid in _scan_supervision_pids(executable, token):
                return True
            time.sleep(0.03)
        raise ValidationIssue(
            "full_run_supervision_canary_failed",
            "Trusted recursive supervisor could not observe its marker canary",
        )
    finally:
        try:
            # This is our unreaped direct child; its PID cannot be reused until
            # wait(), so the Popen handle is a stronger identity than its PGID.
            if proc.poll() is None:
                proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=1.0)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _protected_branch_names(repo_root: Path) -> set[str]:
    names = {"main", "master"}
    for args in (
        ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        ["config", "--get", "init.defaultBranch"],
    ):
        result = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            check=False,
        )
        value = result.stdout.strip()
        if result.returncode == 0 and value:
            names.add(value.removeprefix("origin/"))
    return names


def _validate_full_run_git_contract(
    repo_root: Path,
    *,
    worktree: str | Path,
    branch: str,
    start_head: str,
    packet_path: str | Path,
    adapter: str = "grok-build",
    expected_current_head: str | None = None,
    prepare_phase: bool = False,
) -> dict[str, Any]:
    """Fail before model launch unless the staged Git/worktree contract is exact."""
    repo = Path(repo_root).resolve()
    worker = Path(worktree).expanduser().resolve()
    packet_input = Path(packet_path).expanduser()
    packet = packet_input.resolve()
    if not worker.is_dir():
        raise ValidationIssue(
            "full_run_worktree_missing",
            f"Full-run worktree does not exist: {worker}",
        )
    try:
        packet_info = packet_input.lstat()
    except OSError as exc:
        raise ValidationIssue(
            "full_run_packet_missing",
            f"Full-run packet is not readable: {packet}",
        ) from exc
    if not stat.S_ISREG(packet_info.st_mode):
        raise ValidationIssue(
            "full_run_packet_not_regular",
            f"Full-run packet must be a regular non-symlink file: {packet}",
        )
    if packet_info.st_size > MAX_PACKET_BYTES:
        raise ValidationIssue(
            "full_run_packet_too_large",
            f"Full-run packet exceeds {MAX_PACKET_BYTES} byte limit",
            path=str(packet),
        )
    from .leases import worktree_is_registered  # noqa: PLC0415

    if not worktree_is_registered(worker, git_cwd=repo):
        raise ValidationIssue(
            "full_run_worktree_unregistered",
            f"Full-run worktree is not registered in the staged repository: {worker}",
        )
    repo_common = _git_common_dir(repo)
    worker_common = _git_common_dir(worker)
    if repo_common is None or worker_common is None or repo_common != worker_common:
        raise ValidationIssue(
            "full_run_worktree_wrong_repository",
            "Full-run worktree is not linked to the staged repository",
        )
    current_branch = _git_branch(worker)
    if not current_branch or current_branch != branch:
        raise ValidationIssue(
            "full_run_branch_mismatch",
            f"Worktree branch `{current_branch}` != staged feature branch `{branch}`",
        )
    if branch in _protected_branch_names(repo):
        raise ValidationIssue(
            "full_run_protected_branch",
            f"Refusing delegated full-run on protected/default branch `{branch}`",
        )
    current_head = _git_head(worker)
    required_head = expected_current_head or start_head
    if not current_head or current_head != required_head:
        raise ValidationIssue(
            "full_run_start_head_mismatch",
            f"Worktree HEAD `{current_head}` != required checkpoint `{required_head}`",
        )
    commit = subprocess.run(
        ["git", "-C", str(worker), "rev-parse", "--verify", f"{start_head}^{{commit}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if commit.returncode != 0 or commit.stdout.strip() != start_head:
        raise ValidationIssue(
            "full_run_start_head_invalid",
            f"Staged start HEAD is not an exact commit: `{start_head}`",
        )
    metadata: dict[str, Any] = {}
    if adapter != "fixture":
        _assert_clean_worktree(worker)
        origin_url = _canonical_origin_url(repo)
        origin_digest = _origin_config_digest(repo)
        remote_tip = _feature_remote_tip(repo, branch)
        if prepare_phase and remote_tip is not None and remote_tip != start_head:
            raise ValidationIssue(
                "full_run_remote_feature_unsafe_start",
                "Existing origin feature branch must equal the staged safe start HEAD",
            )
        metadata.update(
            origin_url=origin_url,
            origin_config_digest=origin_digest,
            remote_feature_tip=remote_tip,
        )
    return metadata


@_locked_full_run
def prepare_full_run(
    repo_root: Path,
    *,
    session_id: str,
    branch: str,
    start_head: str,
    worktree: str | Path,
    packet_path: str | Path,
    adapter: str = "grok-build",
    model: str = DEFAULT_MODEL,
    permission_mode: str = DEFAULT_PERMISSION_MODE,
    effort: str = DEFAULT_EFFORT,
    executable: str | None = None,
    create: bool = True,
    check: bool = False,
    max_turns: int = 80,
    fixture_script: str | Path | None = None,
    credential_grant_names: Sequence[str] | None = None,
    allow_overwrite: bool = False,
) -> dict[str, Any]:
    """Create private full-run artifact tree for one exact session."""
    sid = (session_id or "").strip()
    if not sid or sid.lower() in {"latest", "continue", "last", "most-recent"}:
        raise ValidationIssue(
            "full_run_session_required",
            "Exact session_id is required for full-run prepare",
            path="full_run.session_id",
        )
    # Reject traversal/collision-prone raw path embedding (digest key already safe).
    if any(ch in sid for ch in ("/", "\\", "\0")) or ".." in sid:
        # Still allowed as session ids abstractly, but we use digest paths only.
        pass

    adapter_name = (adapter or "grok-build").strip().lower()
    if adapter_name == "fixture" and not fixture_script:
        raise ValidationIssue(
            "fixture_script_required",
            "adapter=fixture requires --fixture-script (explicit test mode only)",
        )
    if adapter_name != "fixture" and fixture_script:
        raise ValidationIssue(
            "fixture_script_only_for_fixture_adapter",
            "fixture_script is only valid with adapter=fixture",
        )

    git_metadata = _validate_full_run_git_contract(
        Path(repo_root),
        worktree=worktree,
        branch=branch,
        start_head=start_head,
        packet_path=packet_path,
        adapter=adapter_name,
        prepare_phase=True,
    )

    staged_acceptance_ids = _staged_acceptance_ids(Path(packet_path).expanduser())
    if adapter_name != "fixture":
        if not staged_acceptance_ids:
            raise ValidationIssue(
                "full_run_acceptance_ids_required",
                "Production full-run packet requires canonical B#-A#/M-A# acceptance definition rows",
                path=str(packet_path),
            )
        duplicate_ids = sorted(
            {
                item
                for item in staged_acceptance_ids
                if staged_acceptance_ids.count(item) > 1
            }
        )
        if duplicate_ids:
            raise ValidationIssue(
                "full_run_acceptance_ids_duplicate",
                "Production full-run packet contains duplicate stable acceptance definitions",
                path=str(packet_path),
            )

    root = full_run_root(repo_root, sid)
    state_path = root / "state.json"
    if repo_regular_file_exists(Path(repo_root), state_path) and not allow_overwrite:
        existing = read_json(state_path, repo_root=Path(repo_root))
        if existing.get("session_id") != sid:
            raise ValidationIssue(
                "full_run_collision",
                "Digest path occupied by a different session_id",
                path=str(state_path),
            )
        raise ValidationIssue(
            "full_run_already_prepared",
            f"Full-run state already exists for session `{sid}`",
            path=str(state_path),
            hint="Pass a new session or stop the existing run first",
        )

    ensure_private_dir(root, repo_root=Path(repo_root))
    ensure_private_dir(root / "worker-home", repo_root=Path(repo_root))
    ensure_private_dir(root / "worker-tmp", repo_root=Path(repo_root))

    exe = (executable or DEFAULT_EXECUTABLE).strip() or DEFAULT_EXECUTABLE
    if adapter_name == "fixture":
        exe = sys.executable
    qualified_supervisor = _qualified_process_supervisor()
    supervisor_executable: str | None = str(qualified_supervisor)
    supervision_token: str | None = secrets.token_hex(24)
    supervision_canary_passed = False
    if adapter_name != "fixture":
        supervision_canary_passed = _run_supervision_canary(qualified_supervisor)

    state = FullRunState(
        session_id=sid,
        branch=branch,
        start_head=start_head,
        worktree=str(Path(worktree).expanduser().resolve()),
        packet_path=str(Path(packet_path).expanduser().resolve()),
        adapter=adapter_name,
        model=model,
        permission_mode=permission_mode,
        effort=effort,
        executable=exe,
        create_session=bool(create),
        check=bool(check),
        max_turns=int(max_turns),
        status="pending",
        head=start_head,
        next_action="launch",
        credential_grant_names=list(
            credential_grant_names or sorted(DEFAULT_CREDENTIAL_GRANT_NAMES)
        ),
        fixture_script=str(Path(fixture_script).resolve()) if fixture_script else None,
        protected_refs=snapshot_protected_refs(
            Path(repo_root), feature_branch=branch
        ),
        launch_start_head=start_head,
        origin_url=git_metadata.get("origin_url"),
        origin_config_digest=git_metadata.get("origin_config_digest"),
        initial_remote_feature_tip=git_metadata.get("remote_feature_tip"),
        acceptance_ids=sorted(staged_acceptance_ids),
        supervisor_executable=supervisor_executable,
        supervision_token=supervision_token,
        supervision_canary_passed=supervision_canary_passed,
        notes=[
            "Trusted full-run supervisor prepared; host parks after launch",
            f"adapter={adapter_name}",
            "Worker report is evidence only; never merge authority",
            "Trusted Lane A is policy trust, not an OS Git sandbox; protected-ref movement blocks readiness",
        ],
    )
    atomic_write_json(state_path, state.to_dict(), repo_root=Path(repo_root))
    for name in ("events.jsonl", "transcript.log"):
        path = root / name
        if not repo_regular_file_exists(Path(repo_root), path):
            with open_repo_text(Path(repo_root), path, mode="w"):
                pass
    report = _running_report(state, final_head=start_head)
    errs = validate_run_report(
        report,
        expected_session_id=sid,
        expected_branch=branch,
        expected_start_head=start_head,
        expected_attempt=state.attempt,
    )
    if errs:
        raise ValidationIssue("full_run_report_invalid", "; ".join(errs))
    report_path = root / "report.json"
    atomic_write_json(report_path, report, repo_root=Path(repo_root))
    _append_event(
        root / "events.jsonl",
        {
            "timestamp": _utc_now(),
            "session_id": sid,
            "branch": branch,
            "head": start_head,
            "batch": 0,
            "type": "run_started",
            "summary": "Full-run supervisor prepared",
        },
        expected_session_id=sid,
        expected_branch=branch,
        repo_root=Path(repo_root),
    )
    public_state = state.to_dict()
    # This is a signaling capability for marker-bound descendant supervision,
    # not operator telemetry. Keep it only in the mode-0600 private state file.
    public_state.pop("supervision_token", None)
    return {
        "ok": True,
        "action": "full_run_prepare",
        "session_id": sid,
        "runtime_dir": str(root),
        "state_path": str(state_path),
        "events_path": str(root / "events.jsonl"),
        "report_path": str(report_path),
        "transcript_path": str(root / "transcript.log"),
        "state": public_state,
        "driver_contract": "parked_monitor",
        "driver_monitor_mode": "parked_monitor",
        "model_calls_made": False,
        "merge_authority": False,
    }


def _append_event(
    events_path: Path,
    event: Mapping[str, Any],
    *,
    expected_session_id: str | None = None,
    expected_branch: str | None = None,
    seen_terminal: bool = False,
    repo_root: Path | None = None,
) -> None:
    errors = validate_event(
        event,
        expected_session_id=expected_session_id,
        expected_branch=expected_branch,
        seen_terminal=seen_terminal,
    )
    if errors:
        raise ValidationIssue("full_run_event_invalid", "; ".join(errors))
    payload = json.dumps(dict(event), separators=(",", ":")) + "\n"
    if repo_root is not None:
        with open_repo_text(repo_root, events_path, mode="a") as handle:
            handle.write(payload)
    else:
        with events_path.open("a", encoding="utf-8") as handle:
            handle.write(payload)
        try:
            events_path.chmod(0o600)
        except OSError:
            pass


def load_state(repo_root: Path, session_id: str) -> FullRunState:
    root = full_run_root(repo_root, session_id)
    path = root / "state.json"
    if not repo_regular_file_exists(Path(repo_root), path):
        raise ValidationIssue(
            "full_run_not_found",
            f"No full-run state for session `{session_id}`",
            path=str(path),
        )
    data = read_json(path, repo_root=Path(repo_root))
    try:
        assert_embedded_id(data, session_id, id_field="session_id")
    except StorageError as exc:
        raise ValidationIssue(
            "full_run_embedded_id_mismatch",
            exc.message,
            path=str(path),
        ) from exc
    if data.get("branch") is None or data.get("start_head") is None:
        raise ValidationIssue(
            "full_run_state_incomplete",
            "Full-run state missing branch or start_head",
            path=str(path),
        )
    return FullRunState.from_dict(data)


def save_state(repo_root: Path, state: FullRunState) -> Path:
    root = full_run_root(repo_root, state.session_id)
    ensure_private_dir(root, repo_root=Path(repo_root))
    path = root / "state.json"
    atomic_write_json(path, state.to_dict(), repo_root=Path(repo_root))
    return path


def build_full_run_argv(state: FullRunState) -> list[str]:
    """Adapter-aware argv. Fixture mode uses explicit python + script + packet."""
    if state.adapter == "fixture":
        if not state.fixture_script:
            raise ValidationIssue(
                "fixture_script_required",
                "fixture adapter requires fixture_script in state",
            )
        return [state.executable, state.fixture_script, state.packet_path]
    return build_launch_argv(
        session_id=state.session_id,
        packet=state.packet_path,
        cwd=state.worktree,
        model=state.model,
        permission_mode=state.permission_mode,
        executable=state.executable,
        create=bool(state.create_session),
        effort=state.effort,
        yolo=bool(state.yolo),
        max_turns=state.max_turns,
        output_format=state.output_format,
        adapter=state.adapter,
        check=bool(state.check),
    )


def _running_report(state: FullRunState, *, final_head: str) -> dict[str, Any]:
    return {
        "run_id": _expected_run_id(state.session_id),
        "session_id": state.session_id,
        "branch": state.branch,
        "start_head": state.start_head,
        "final_head": final_head,
        "status": "running",
        "batches": [],
        "acceptance": [],
        "commits": [],
        "blockers": [],
        "merge_authority": False,
        "docs_changed": [],
        "tests": {},
        "security_notes": ["transcript private; status bounded; no merge authority"],
        "remaining_risks": [],
        "attempt": state.attempt,
    }


def _archive_and_reset_resume_attempt(
    repo_root: Path,
    state: FullRunState,
    *,
    checkpoint_head: str,
) -> None:
    root = full_run_root(repo_root, state.session_id)
    archive = root / "attempts" / f"attempt-{state.attempt:04d}"
    try:
        archive.lstat()
    except FileNotFoundError:
        pass
    else:
        raise ValidationIssue(
            "full_run_attempt_archive_exists",
            f"Attempt archive already exists: {archive}",
        )
    ensure_private_dir(archive, repo_root=Path(repo_root))
    atomic_write_json(
        archive / "state.json",
        state.to_dict(),
        repo_root=Path(repo_root),
    )
    for name in (
        "events.jsonl",
        "report.json",
        "exit_record.json",
        "supervisor.fingerprint.json",
        "worker.fingerprint.json",
        "worker.pid",
        "worker.pgid",
        "transcript.log",
    ):
        source = root / name
        if repo_regular_file_exists(Path(repo_root), source):
            move_repo_regular_file(
                Path(repo_root),
                source,
                archive / name,
                replace=False,
            )

    state.attempt += 1
    state.supervision_token = secrets.token_hex(24)
    state.credential_granted_names = []
    state.credential_grant_digests = {}
    state.closed_process_identity = None
    state.interruption_evidence = None
    state.pid = None
    state.pgid = None
    state.fingerprint = None
    state.exit_code = None
    state.exit_sidecar_pid = None
    state.status = "pending"
    state.blocker = None
    state.completed_at = None
    state.launched_at = None
    state.heartbeat_at = None
    state.next_action = "launch"
    state.head = checkpoint_head
    state.create_session = False
    with open_repo_text(Path(repo_root), root / "events.jsonl", mode="w"):
        pass
    with open_repo_text(Path(repo_root), root / "transcript.log", mode="w"):
        pass
    atomic_write_json(
        root / "report.json",
        _running_report(state, final_head=checkpoint_head),
        repo_root=Path(repo_root),
    )
    _append_event(
        root / "events.jsonl",
        {
            "timestamp": _utc_now(),
            "session_id": state.session_id,
            "branch": state.branch,
            "head": checkpoint_head,
            "batch": state.batch or 0,
            "type": "run_started",
            "summary": f"Full-run resume attempt {state.attempt} prepared",
        },
        expected_session_id=state.session_id,
        expected_branch=state.branch,
        repo_root=Path(repo_root),
    )


def _prepare_resume_attempt(repo_root: Path, state: FullRunState) -> str:
    if state.adapter != "fixture" and state.adapter != "grok-build":
        raise ValidationIssue(
            "full_run_resume_adapter_unsupported",
            "Production full-run resume requires the exact Grok Build adapter",
        )
    if state.launch_start_head != state.start_head:
        raise ValidationIssue(
            "full_run_start_head_mutated",
            "Immutable launch start no longer matches staged start_head",
        )
    closed = state.closed_process_identity
    interruption = state.interruption_evidence
    if not isinstance(closed, Mapping) or not isinstance(interruption, Mapping):
        raise ValidationIssue(
            "full_run_resume_unauthenticated",
            "Resume requires a host-authenticated prior interruption and closed identity",
        )
    if (
        interruption.get("authority") != "host_stop"
        or interruption.get("closed_identity_digest") != closed.get("identity_digest")
        or interruption.get("attempt") != state.attempt
    ):
        raise ValidationIssue(
            "full_run_resume_unauthenticated",
            "Resume interruption evidence does not bind the prior attempt identity",
        )
    if state.pid is not None or state.pgid is not None or state.fingerprint is not None:
        raise ValidationIssue(
            "full_run_resume_identity_not_retired",
            "Resume requires prior PID/PGID/fingerprint retirement",
        )
    lingering = _supervised_alive(state)
    if lingering:
        raise ValidationIssue(
            "full_run_resume_process_alive",
            f"Prior supervised descendants still live: {sorted(lingering)}",
        )
    checkpoint = _git_head(Path(state.worktree))
    if not checkpoint or not _is_ancestor(Path(state.worktree), state.start_head, checkpoint):
        raise ValidationIssue(
            "full_run_resume_checkpoint_invalid",
            "Resume checkpoint must be an exact descendant of immutable start_head",
        )
    _validate_full_run_git_contract(
        repo_root,
        worktree=state.worktree,
        branch=state.branch,
        start_head=state.start_head,
        packet_path=state.packet_path,
        adapter=state.adapter,
        expected_current_head=checkpoint,
    )
    if state.adapter != "fixture":
        _assert_origin_binding(repo_root, state, expected_feature_tip=checkpoint)
        if not state.supervisor_executable or not state.supervision_canary_passed:
            raise ValidationIssue(
                "full_run_supervision_unavailable",
                "Resume requires the original qualified recursive supervision domain",
            )
        state.supervision_canary_passed = _run_supervision_canary(
            Path(state.supervisor_executable)
        )
    _archive_and_reset_resume_attempt(repo_root, state, checkpoint_head=checkpoint)
    return checkpoint


@_locked_full_run
def launch_full_run(
    repo_root: Path,
    *,
    session_id: str,
    background: bool = True,
    credential_grant_names: Sequence[str] | None = None,
    resume: bool = False,
) -> dict[str, Any]:
    """Background-launch Grok (or explicit fixture) for one exact session.

    Never accepts KEY=VALUE secrets on argv. Credential grants are by name only.
    """
    del background  # always non-blocking Popen
    state = load_state(repo_root, session_id)
    root = full_run_root(repo_root, session_id)
    if resume:
        _prepare_resume_attempt(Path(repo_root), state)
        save_state(repo_root, state)
    elif state.status in {"complete", "stopped", "blocked", "failed"}:
        raise ValidationIssue(
            "full_run_terminal",
            f"Cannot launch terminal full-run session `{session_id}` ({state.status})",
        )
    elif state.launched_at or state.closed_process_identity:
        raise ValidationIssue(
            "full_run_already_running" if state.status == "healthy" else "full_run_relaunch_requires_resume",
            "A prepared attempt may launch once; authenticated resume is required afterward",
        )

    if state.fingerprint:
        ok, reason = verify_fingerprint(
            state.fingerprint, expected_session_id=session_id
        )
        pid_alive = bool(state.pid and _pid_alive(int(state.pid)))
        group_alive = _process_group_alive(state.pgid)
        supervised = _supervised_alive(state)
        if ok or pid_alive or group_alive or supervised:
            raise ValidationIssue(
                "full_run_already_running",
                f"Full-run session `{session_id}` retains a live or unverifiable process identity",
            )
        raise ValidationIssue(
            "full_run_relaunch_unauthenticated",
            f"Recorded process is dead but has no authenticated resume transition: {reason}",
        )

    _validate_full_run_git_contract(
        Path(repo_root),
        worktree=state.worktree,
        branch=state.branch,
        start_head=state.start_head,
        packet_path=state.packet_path,
        adapter=state.adapter,
        expected_current_head=state.head or state.start_head,
    )
    if state.launch_start_head != state.start_head:
        raise ValidationIssue(
            "full_run_start_head_mutated",
            "Immutable launch start no longer matches staged start_head",
        )
    if state.adapter != "fixture":
        expected_remote = (
            state.head if resume else state.initial_remote_feature_tip
        )
        _assert_origin_binding(Path(repo_root), state, expected_feature_tip=expected_remote)
        if not state.supervision_canary_passed:
            raise ValidationIssue(
                "full_run_supervision_canary_failed",
                "Production launch requires a successful recursive-supervision canary",
            )

    provider_argv = build_full_run_argv(state)
    if resume and state.adapter != "fixture":
        try:
            resume_index = provider_argv.index("--resume")
        except ValueError as exc:
            raise ValidationIssue(
                "full_run_resume_argv_ambiguous",
                "Grok resume argv must contain exact --resume <session-id>",
            ) from exc
        if (
            resume_index + 1 >= len(provider_argv)
            or provider_argv[resume_index + 1] != state.session_id
            or "--session-id" in provider_argv
        ):
            raise ValidationIssue(
                "full_run_resume_argv_ambiguous",
                "Grok resume argv is not bound to the exact staged session id",
            )
    state.last_argv = list(provider_argv)
    effective_grant_names = list(
        credential_grant_names
        if credential_grant_names is not None
        else state.credential_grant_names
    )
    state.credential_grant_names = effective_grant_names
    launch_env = build_full_run_env(
        state=state,
        root=root,
        credential_grant_names=effective_grant_names,
    )
    # Never return credential values.
    granted_names = [
        n
        for n in effective_grant_names
        if n in launch_env
    ]
    state.credential_granted_names = list(granted_names)
    state.credential_grant_digests = {
        name: _credential_grant_digest(state, name, launch_env[name])
        for name in granted_names
    }
    # Persist the exact granted names before spawn so a concurrently invoked
    # monitor/log command can redact opaque values from the first worker byte.
    save_state(repo_root, state)

    transcript = root / "transcript.log"
    ensure_private_dir(root / "worker-home", repo_root=Path(repo_root))
    ensure_private_dir(root / "worker-tmp", repo_root=Path(repo_root))
    for stale_path in (
        root / "exit_record.json",
        root / "supervisor.fingerprint.json",
    ):
        if repo_regular_file_exists(Path(repo_root), stale_path):
            raise ValidationIssue(
                "full_run_stale_exit_artifact",
                "Refusing launch while an unarchived supervisor artifact exists",
                path=str(stale_path),
            )
    if not state.supervisor_executable or not state.supervision_token:
        raise ValidationIssue(
            "full_run_supervision_unavailable",
            "Launch is missing its host-owned recursive supervision identity",
        )
    supervisor_argv = _provider_supervisor_argv(
        root=root,
        session_id=session_id,
        provider_argv=provider_argv,
        attempt=state.attempt,
        supervisor_executable=state.supervisor_executable,
        supervision_token=state.supervision_token,
    )
    # Open transcript for inheritance, then close parent fd so launchers across
    # separate CLI invocations do not leave unclosed handles / ResourceWarnings.
    with open_repo_text(Path(repo_root), transcript, mode="a") as stdout_handle:
        proc = subprocess.Popen(
            supervisor_argv,
            cwd=state.worktree if state.adapter != "fixture" else state.worktree,
            env=launch_env,
            stdout=stdout_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )

    pgid = os.getpgid(proc.pid) if hasattr(os, "getpgid") else proc.pid
    # Brief settle so ps can observe the process.
    time.sleep(0.05)
    fp = capture_fingerprint(
        pid=proc.pid,
        pgid=pgid,
        session_id=session_id,
        executable_hint=sys.executable,
    )
    state.pid = proc.pid
    state.pgid = pgid
    state.fingerprint = fp.to_dict()
    state.status = "healthy"
    state.launched_at = _utc_now()
    state.heartbeat_at = state.launched_at
    state.next_action = "parked_monitor"
    if resume:
        state.create_session = False
    # The supervisor is the provider's real parent, waits it, and records its exact
    # exit code before it exits. Monitor validates this launcher-captured identity.
    atomic_write_json(
        root / "supervisor.fingerprint.json",
        fp.to_dict(),
        repo_root=Path(repo_root),
    )
    state.exit_sidecar_pid = proc.pid  # compatibility field: now the parent supervisor PID
    state.exit_code = None
    # Drop Popen without waiting: process is tracked by fingerprint + durable exit record.
    # Clear handles so GC does not emit ResourceWarning for intentional backgrounding.
    try:
        proc.stdout = None
        proc.stderr = None
        proc.stdin = None
        # Intentional background detach: suppress ResourceWarning on GC.
        if proc.returncode is None:
            proc.returncode = 0
    except Exception:  # noqa: BLE001
        pass
    save_state(repo_root, state)
    with open_repo_text(Path(repo_root), root / "worker.pid", mode="w") as handle:
        handle.write(str(proc.pid) + "\n")
    with open_repo_text(Path(repo_root), root / "worker.pgid", mode="w") as handle:
        handle.write(str(pgid) + "\n")
    atomic_write_json(
        root / "worker.fingerprint.json",
        fp.to_dict(),
        repo_root=Path(repo_root),
    )
    _append_event(
        root / "events.jsonl",
        {
            "timestamp": state.launched_at,
            "session_id": session_id,
            "branch": state.branch,
            "head": state.head or state.start_head,
            "batch": state.batch or 0,
            "type": "heartbeat",
            "summary": "Worker launched in background",
        },
        expected_session_id=session_id,
        expected_branch=state.branch,
        repo_root=Path(repo_root),
    )
    return {
        "ok": True,
        "action": "full_run_launch",
        "session_id": session_id,
        "pid": proc.pid,
        "pgid": pgid,
        "status": state.status,
        "driver_contract": "parked_monitor",
        "driver_monitor_mode": "parked_monitor",
        "returned_promptly": True,
        "argv": provider_argv,
        "adapter": state.adapter,
        "credential_grant_names_present": granted_names,
        "model_calls_made": state.adapter != "fixture",
        "merge_authority": False,
    }


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        waited, _status = os.waitpid(pid, os.WNOHANG)
        if waited == pid:
            return False
    except (ChildProcessError, OSError):
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _process_group_alive(pgid: int | None) -> bool:
    if not pgid:
        return False
    try:
        os.killpg(int(pgid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _settle_recorded_supervisor_exit(
    pid: int | None,
    pgid: int | None,
    *,
    timeout_seconds: float = EXIT_RECORD_SETTLE_SECONDS,
) -> tuple[bool, bool]:
    """Bridge the host-sidecar write/exit race without accepting live groups.

    The launcher-owned supervisor must write its durable exit record immediately
    before it exits.  A monitor can therefore observe a valid record during that
    tiny interval.  Give the exact recorded supervisor a bounded chance to reap;
    a provider or descendant group that remains alive after the window is still
    classified as premature and fails closed.
    """
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while True:
        _reap_supervisor_if_child(pid)
        pid_alive = bool(pid and _pid_alive(pid))
        group_alive = _process_group_alive(pgid)
        if not pid_alive and not group_alive:
            return False, False
        if time.monotonic() >= deadline:
            return pid_alive, group_alive
        time.sleep(0.01)


def _supervised_alive(state: FullRunState) -> set[int]:
    if not state.supervisor_executable or not state.supervision_token:
        if state.adapter == "fixture":
            return set()
        raise ValidationIssue(
            "full_run_supervision_unavailable",
            "Production run is missing its host-owned recursive supervisor identity",
        )
    qualified = _qualified_process_supervisor()
    if Path(state.supervisor_executable).resolve() != qualified:
        raise ValidationIssue(
            "full_run_supervision_executable_changed",
            "Recorded recursive supervisor is not the currently qualified system executable",
        )
    return _scan_supervision_pids(qualified, state.supervision_token)


def _identity_digest(identity: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(dict(identity), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _retire_process_identity(
    state: FullRunState,
    *,
    reason: str,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Move authenticated active IDs into closed history and erase signal targets."""
    identity = {
        "pid": state.pid,
        "pgid": state.pgid,
        "fingerprint": dict(state.fingerprint or {}),
        "attempt": state.attempt,
        "exit_code": state.exit_code,
        "closed_at": _utc_now(),
        "reason": reason,
        "evidence": dict(evidence),
    }
    identity["identity_digest"] = _identity_digest(identity)
    state.closed_process_identity = identity
    state.process_history.append(dict(identity))
    state.pid = None
    state.pgid = None
    state.fingerprint = None
    state.exit_sidecar_pid = None
    return identity


def _record_interruption(
    state: FullRunState,
    *,
    closed_identity: Mapping[str, Any],
    reason: str,
) -> None:
    state.interruption_evidence = {
        "session_id": state.session_id,
        "attempt": state.attempt,
        "closed_identity_digest": closed_identity.get("identity_digest"),
        "recorded_at": _utc_now(),
        "reason": reason,
        "authority": "host_stop",
    }


def _read_events(
    events_path: Path,
    *,
    expected_session_id: str,
    expected_branch: str,
    exact_secret_values: frozenset[str] | set[str] | tuple[str, ...] | None = None,
    allow_partial_final: bool = False,
    repo_root: Path | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    if repo_root is not None:
        try:
            if not repo_regular_file_exists(repo_root, events_path):
                return [], []
        except StorageError as exc:
            return [], [exc.message]
    elif not events_path.exists() and not events_path.is_symlink():
        return [], []
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    seen_terminal = False
    try:
        raw = _read_bounded_regular_bytes(
            events_path,
            max_bytes=MAX_EVENT_FILE_BYTES,
            label="event log",
            repo_root=repo_root,
        )
    except StorageError as exc:
        return [], [exc.message]

    complete_final = not raw or raw.endswith(b"\n")
    raw_lines = raw.splitlines()
    if not complete_final and raw_lines:
        if allow_partial_final:
            raw_lines = raw_lines[:-1]
        else:
            errors.append("final event record is incomplete")
            raw_lines = raw_lines[:-1]
    if len(raw_lines) > MAX_EVENT_LINES:
        return [], [f"event log exceeds {MAX_EVENT_LINES} line limit"]

    for line_no, raw_line in enumerate(raw_lines, 1):
        if len(raw_line) > MAX_EVENT_LINE_BYTES:
            errors.append(f"line {line_no}: event exceeds line-size limit")
            continue
        try:
            line = raw_line.decode("utf-8").strip()
        except UnicodeDecodeError:
            errors.append(f"line {line_no}: event must be UTF-8")
            continue
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"line {line_no}: malformed json: {exc}")
            continue
        if not isinstance(event, dict):
            errors.append(f"line {line_no}: event must be object")
            continue
        verrs = validate_event(
            event,
            expected_session_id=expected_session_id,
            expected_branch=expected_branch,
            seen_terminal=seen_terminal,
            exact_secret_values=exact_secret_values,
        )
        if verrs:
            errors.extend(f"line {line_no}: {e}" for e in verrs)
            continue
        if event.get("type") in TERMINAL_EVENT_TYPES:
            seen_terminal = True
        rows.append(event)
    return rows, errors


def _bounded_text_tail(
    path: Path,
    *,
    lines: int,
    repo_root: Path | None = None,
) -> list[str]:
    """Read a bounded suffix without loading a worker transcript wholesale."""
    if lines <= 0:
        return []
    if repo_root is not None:
        try:
            tail = read_repo_text_tail(
                repo_root,
                path,
                max_bytes=MAX_TRANSCRIPT_TAIL_BYTES,
                max_lines=lines,
            )
        except StorageError as exc:
            if exc.code == "not_found":
                return []
            raise
        return [line[-MAX_TRANSCRIPT_LINE_CHARS:] for line in tail]
    if not path.exists() and not path.is_symlink():
        return []
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise StorageError(
                "transcript_not_regular",
                "transcript must be a regular non-symlink file",
            )
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
        ):
            raise StorageError(
                "transcript_identity_changed",
                "transcript changed identity while opening",
            )
        size = opened.st_size
        start = max(0, size - MAX_TRANSCRIPT_TAIL_BYTES)
        with os.fdopen(fd, "rb", closefd=False) as handle:
            handle.seek(start)
            raw = handle.read(MAX_TRANSCRIPT_TAIL_BYTES)
    except StorageError:
        raise
    except OSError as exc:
        raise StorageError(
            "transcript_unavailable",
            f"transcript cannot be read safely: {type(exc).__name__}",
        ) from exc
    finally:
        if "fd" in locals():
            try:
                os.close(fd)
            except OSError:
                pass
    chunks = raw.splitlines()
    # A nonzero seek can begin in the middle of a line. Discard that fragment;
    # callers receive fewer lines rather than an unbounded backward scan.
    if start and chunks:
        chunks = chunks[1:]
    tail = chunks[-lines:]
    return [
        chunk[-MAX_TRANSCRIPT_LINE_CHARS:].decode("utf-8", errors="replace")
        for chunk in tail
    ]


def _git_commit_chain(cwd: Path, start_head: str, final_head: str) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(cwd), "rev-list", "--reverse", f"{start_head}..{final_head}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValidationIssue(
            "full_run_commit_chain_unavailable",
            (result.stderr or result.stdout or "git rev-list failed").strip(),
        )
    return [row.strip() for row in result.stdout.splitlines() if row.strip()]


def _report_commit_shas(report: Mapping[str, Any]) -> list[str]:
    shas: list[str] = []
    for item in report.get("commits") or []:
        shas.append(str(item.get("sha")) if isinstance(item, Mapping) else str(item))
    return shas


def _validate_git_bound_evidence(
    state: FullRunState,
    report: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    observed_head: str | None,
) -> list[str]:
    if state.adapter == "fixture":
        return []
    errors: list[str] = []
    worktree = Path(state.worktree)
    previous = state.start_head
    if observed_head:
        for index, event in enumerate(events):
            head = str(event.get("head") or "")
            if not (
                _is_ancestor(worktree, state.start_head, head)
                and _is_ancestor(worktree, head, observed_head)
            ):
                errors.append(f"event[{index}] head is outside observed feature ancestry")
                continue
            if not _is_ancestor(worktree, previous, head):
                errors.append(f"event[{index}] head regresses from prior observed event")
            previous = head
    if report.get("status") == "complete":
        final_head = str(report.get("final_head") or "")
        try:
            expected_chain = _git_commit_chain(worktree, state.start_head, final_head)
        except ValidationIssue as issue:
            errors.append(issue.message)
        else:
            if _report_commit_shas(report) != expected_chain:
                errors.append("report commits do not exactly equal start_head..final_head chain")
        if state.acceptance_ids:
            observed_ids = [
                str(item.get("id") or "")
                for item in report.get("acceptance") or []
                if isinstance(item, Mapping)
            ]
            if sorted(observed_ids) != sorted(state.acceptance_ids):
                errors.append("report acceptance ids do not exactly match staged criteria")
        try:
            _assert_clean_worktree(worktree)
        except ValidationIssue as issue:
            errors.append(issue.message)
    return errors


@_locked_full_run
def monitor_full_run(
    repo_root: Path,
    *,
    session_id: str,
    stale_after_seconds: int = DEFAULT_STALE_SECONDS,
) -> dict[str, Any]:
    """Classify health using fingerprint + branch head + validated events/report."""
    state = load_state(repo_root, session_id)
    initial_status = state.status
    initial_next_action = state.next_action
    initial_blocker = state.blocker
    initial_completed_at = state.completed_at
    identity_retired = bool(
        state.closed_process_identity
        and state.pid is None
        and state.pgid is None
        and state.fingerprint is None
    )
    root = full_run_root(repo_root, session_id)
    exact_secret_values = _state_secret_values(state)
    grant_context_verified = _launch_grants_verified(state)
    if grant_context_verified:
        events, event_errors = _read_events(
            root / "events.jsonl",
            expected_session_id=session_id,
            expected_branch=state.branch,
            exact_secret_values=exact_secret_values,
            allow_partial_final=not identity_retired and bool(state.pid or state.pgid),
            repo_root=Path(repo_root),
        )
    else:
        events, event_errors = [], [
            "launch credential context cannot be verified for worker evidence"
        ]
    report_path = root / "report.json"
    report: dict[str, Any] = {}
    report_errors: list[str] = (
        []
        if grant_context_verified
        else ["launch credential context cannot be verified for worker report"]
    )
    try:
        report_exists = repo_regular_file_exists(Path(repo_root), report_path)
    except StorageError as exc:
        report_exists = False
        report_errors = [exc.message]
    if report_exists and grant_context_verified:
        try:
            report = _read_bounded_json_object(
                report_path,
                label="run report",
                repo_root=Path(repo_root),
            )
            report_errors = validate_run_report(
                report,
                expected_session_id=session_id,
                expected_branch=state.branch,
                expected_start_head=state.start_head,
                require_complete_acceptance=report.get("status") == "complete",
                expected_run_id=_expected_run_id(session_id),
                expected_attempt=state.attempt,
                exact_secret_values=exact_secret_values,
            )
            redacted_report = _redact_full_run_structure(
                report, exact_values=exact_secret_values
            )
            if redacted_report != report:
                # The worker writes this private artifact directly. Replace any
                # detected credential material before surfacing a generic error.
                atomic_write_json(
                    report_path,
                    redacted_report,
                    repo_root=Path(repo_root),
                )
            if report_errors:
                report = {}
        except StorageError as exc:
            report_errors = [exc.message]
            report = {}

    # Process fingerprint is primary liveness for long Grok turns. The process
    # group is tracked independently because a provider can leave descendants
    # behind after its direct parent exits.
    fp_ok = False
    fp_reason = "retired process identity" if identity_retired else "no fingerprint"
    alive = False
    if state.fingerprint and not identity_retired:
        fp_ok, fp_reason = verify_fingerprint(
            state.fingerprint, expected_session_id=session_id
        )
        alive = fp_ok
    elif state.pid and not identity_retired:
        alive = _pid_alive(state.pid)
        fp_reason = "legacy pid without fingerprint"
    group_alive = False if identity_retired else _process_group_alive(state.pgid)
    supervised_pids: set[int] = set()
    if not identity_retired:
        try:
            supervised_pids = _supervised_alive(state)
        except ValidationIssue as issue:
            exit_record_errors = [issue.message]
        else:
            exit_record_errors = []
    else:
        exit_record_errors = []

    # Host-owned exit sidecar: actual child exit code + fingerprint after provider exits.
    exit_record: dict[str, Any] | None = None
    candidate_exit_record = None
    if not identity_retired:
        try:
            candidate_exit_record = read_exit_record(
                root,
                repo_root=Path(repo_root),
            )
        except StorageError as exc:
            exit_record_errors.append(exc.message)
    if candidate_exit_record is not None and not identity_retired:
        exit_record_errors.extend(_validate_exit_record(candidate_exit_record, state))
        if not exit_record_errors:
            pid_still_alive, group_alive = _settle_recorded_supervisor_exit(
                state.pid,
                state.pgid,
            )
            if not pid_still_alive and not group_alive:
                try:
                    supervised_pids = _supervised_alive(state)
                except ValidationIssue as issue:
                    exit_record_errors.append(issue.message)
            if pid_still_alive or group_alive or supervised_pids:
                exit_record_errors.append(
                    "premature exit record while supervised process identity remains alive"
                )
                fp_reason = "premature exit record"
            else:
                exit_record = candidate_exit_record
                state.exit_code = int(exit_record["exit_code"])
                alive = False
                fp_ok = False
                fp_reason = "validated exit record after full process-group exit"

    # Observed feature-branch state. Process liveness is not a heartbeat: a hung
    # provider can remain fingerprint-valid indefinitely and must still wake the
    # parked driver when no meaningful worker/event activity is observed.
    observed_head = _git_head(Path(state.worktree))
    observed_branch = _git_branch(Path(state.worktree))
    if observed_head:
        state.head = observed_head

    last_type = None
    saw_run_complete_event = False
    for ev in events:
        last_type = ev.get("type") or last_type
        if ev.get("type") == "batch_started":
            try:
                state.batch = int(ev.get("batch") or state.batch or 0)
            except (TypeError, ValueError):
                pass
        state.heartbeat_at = _latest_utc_iso8601(
            state.heartbeat_at, ev.get("timestamp")
        )
        if ev.get("type") == "blocked":
            state.status = "blocked"
            state.blocker = str(ev.get("summary") or "blocked")
            state.next_action = "driver_wake_blocker"
        if ev.get("type") == "run_complete":
            # Lone run_complete never establishes completion — needs validated report
            # or clean provider exit with feature-branch progress.
            saw_run_complete_event = True

    # Report is evidence only after validation. Completion requires a fully
    # evidenced report, exact head/ancestry, and a clean exit accepted only after
    # the supervisor PID and its entire process group are dead.
    if report and not report_errors:
        if report.get("status") == "complete":
            final_head = str(report.get("final_head") or "")
            # Real adapters must prove feature-branch ancestry. Explicit fixture mode
            # may emit synthetic heads for multi-batch semantics without mutating git.
            if state.adapter != "fixture":
                if final_head and observed_head and final_head != observed_head:
                    report_errors.append(
                        "report final_head does not match observed feature branch head"
                    )
                elif final_head and not _is_ancestor(
                    Path(state.worktree), state.start_head, final_head
                ):
                    report_errors.append(
                        "report final_head is not a descendant of start_head"
                    )
        elif report.get("status") == "blocked":
            state.status = "blocked"
            state.blocker = state.blocker or "report status blocked"
            state.next_action = "driver_wake_blocker"
        elif report.get("status") == "failed":
            state.status = "failed"
            state.next_action = "driver_wake_error"

    report_errors.extend(
        _validate_git_bound_evidence(state, report, events, observed_head)
    )

    # Protected refs: any movement blocks readiness (policy trust, not OS sandbox).
    try:
        protected_errors = verify_protected_refs_unchanged(
            Path(repo_root),
            state.protected_refs or {},
            feature_branch=state.branch,
        )
    except ValidationIssue as issue:
        protected_errors = [issue.message]
    if state.adapter != "fixture":
        try:
            if (
                _canonical_origin_url(Path(repo_root)) != state.origin_url
                or _origin_config_digest(Path(repo_root)) != state.origin_config_digest
            ):
                protected_errors.append("origin URL/config changed after preparation")
        except ValidationIssue as issue:
            protected_errors.append(issue.message)
    if protected_errors:
        state.status = "failed"
        state.blocker = "; ".join(protected_errors)
        state.next_action = "driver_wake_safety_tripwire"

    # Invalid worker evidence is always a wake-worthy failure. In particular, a
    # malformed or premature exit record must never leave the driver parked.
    if event_errors or report_errors or exit_record_errors:
        state.status = "failed"
        evidence_errors = event_errors + report_errors + exit_record_errors
        state.blocker = "; ".join(evidence_errors[:4]) or "untrusted worker evidence"
        state.next_action = "driver_wake_error"

    # Branch mismatch is a safety signal.
    if observed_branch and observed_branch != state.branch:
        state.status = "failed"
        state.blocker = f"worktree branch `{observed_branch}` != staged `{state.branch}`"
        state.next_action = "driver_wake_safety_tripwire"

    # A validated nonzero provider exit is authoritative even if the worker wrote
    # a superficially complete report immediately before terminating.
    if exit_record is not None and state.exit_code != 0:
        state.status = "failed"
        state.blocker = f"provider nonzero exit: {state.exit_code}"
        state.next_action = "driver_wake_error"

    if state.status not in {"blocked", "failed", "stopped"} and not (
        identity_retired
        and initial_status in {"complete", "stopped", "failed", "blocked"}
    ):
        clean_exit = exit_record is not None and state.exit_code == 0
        complete_report = bool(
            report
            and not report_errors
            and report.get("status") == "complete"
        )
        if clean_exit and complete_report and not protected_errors:
            state.status = "complete"
            state.completed_at = state.completed_at or _utc_now()
            state.next_action = "final_readiness"
            state.head = str(report.get("final_head") or observed_head or state.start_head)
        elif clean_exit:
            state.status = "failed"
            state.blocker = "provider exited cleanly without a validated complete report"
            state.next_action = "driver_wake_error"
        elif alive:
            state.status = "healthy"
            state.next_action = "parked_monitor"
            hb = state.heartbeat_at or state.launched_at
            if hb:
                try:
                    hb_dt = datetime.fromisoformat(hb.replace("Z", "+00:00"))
                    age = (datetime.now(timezone.utc) - hb_dt).total_seconds()
                    if age > max(0, stale_after_seconds):
                        state.status = "stale"
                        state.next_action = "driver_wake_stale_heartbeat"
                except ValueError:
                    pass
        elif group_alive:
            state.status = "failed"
            state.blocker = "supervisor exited while its process group remains alive"
            state.next_action = "driver_wake_error"
        elif supervised_pids:
            state.status = "failed"
            state.blocker = "supervisor exited while recursively supervised descendants remain alive"
            state.next_action = "driver_wake_error"
        elif last_type == "blocked":
            state.status = "blocked"
            state.next_action = "driver_wake_blocker"
        elif saw_run_complete_event:
            state.status = "failed"
            state.blocker = "run_complete event without validated complete report and exit"
            state.next_action = "driver_wake_error"
        else:
            # Prepared-but-not-launched and the short atomic exit-record race are
            # pending. Neither can imply success.
            state.status = "pending"
            state.next_action = "parked_monitor" if state.launched_at else "launch"

    if exit_record is not None and state.fingerprint is not None:
        _retire_process_identity(
            state,
            reason="validated_provider_exit",
            evidence=exit_record,
        )
        identity_retired = True

    # Terminal states are monotonic across repeated monitor calls. A completed
    # run may still be demoted to failed by newly detected safety corruption,
    # but stopped/failed/blocked never regress to an active state.
    if initial_status in {"stopped", "failed", "blocked"}:
        state.status = initial_status
        state.next_action = initial_next_action
        state.blocker = initial_blocker
        state.completed_at = initial_completed_at
    elif initial_status == "complete" and state.status != "failed":
        state.status = "complete"
        state.next_action = initial_next_action or "final_readiness"
        state.completed_at = initial_completed_at or state.completed_at

    # Production finalization path: reconcile git + protected refs when complete.
    # Explicit fixture mode may use synthetic heads without mutating git.
    reconcile_payload: dict[str, Any] | None = None
    if (
        state.status == "complete"
        and state.next_action == "final_readiness"
        and state.adapter != "fixture"
    ):
        try:
            reconcile_payload = reconcile_full_run_with_git(
                repo_root, session_id=session_id
            )
        except ValidationIssue as issue:
            state.status = "failed"
            state.blocker = issue.message
            state.next_action = "driver_wake_error"
            reconcile_payload = {"ok": False, "error": issue.message}

    if state.blocker:
        state.blocker = _redact_full_run_text(
            state.blocker, exact_values=exact_secret_values
        )
    save_state(repo_root, state)
    from .behavior_policy import PARKED_MONITOR_WAKE_CONDITIONS  # noqa: PLC0415

    status = {
        "ok": state.status in {"healthy", "complete", "pending"},
        "session_id": session_id,
        "state": state.status,
        "batch": state.batch,
        "head": state.head or state.start_head,
        "branch": state.branch,
        "heartbeat_at": state.heartbeat_at,
        "pid": state.pid,
        "pgid": state.pgid,
        "next_action": state.next_action,
        "blocker": state.blocker,
        "driver_contract": "parked_monitor",
        "driver_monitor_mode": "parked_monitor",
        "wake_conditions": sorted(PARKED_MONITOR_WAKE_CONDITIONS),
        "check_summary": {
            "events": len(events),
            "last_event_type": last_type,
            "alive": alive,
            "group_alive": group_alive,
            "fingerprint_reason": fp_reason,
            "report_status": report.get("status") if report else None,
            "event_errors": len(event_errors),
            "report_errors": len(report_errors),
            "exit_record_errors": len(exit_record_errors),
            "observed_branch": observed_branch,
            "exit_code": state.exit_code,
            "exit_record": bool(exit_record),
            "reconcile_ok": (
                None if reconcile_payload is None else bool(reconcile_payload.get("ok"))
            ),
        },
        "report_path": str(report_path),
        "events_path": str(root / "events.jsonl"),
        "transcript_private": True,
        "adapter": state.adapter,
        "fingerprint_ok": fp_ok,
        "merge_authority": False,
    }
    assert "transcript" not in status
    assert "stdout" not in status
    assert set(status) <= STATUS_KEYS | {"ok"}
    _redact_full_run_mapping_in_place(status, exact_values=exact_secret_values)
    return status


@_locked_full_run
def stop_full_run(
    repo_root: Path,
    *,
    session_id: str,
    grace_seconds: float = 1.0,
) -> dict[str, Any]:
    """Terminate only the exact fingerprinted supervisor identity."""
    state = load_state(repo_root, session_id)
    root = full_run_root(repo_root, session_id)
    if (
        state.closed_process_identity
        and state.pid is None
        and state.pgid is None
        and state.fingerprint is None
    ):
        if state.interruption_evidence is None and state.status != "complete":
            _record_interruption(
                state,
                closed_identity=state.closed_process_identity,
                reason="host acknowledged authenticated closed attempt",
            )
            save_state(repo_root, state)
        return {
            "ok": True,
            "action": "full_run_stop",
            "session_id": session_id,
            "signaled": False,
            "still_alive": False,
            "status": state.status,
            "reason": "process identity already retired",
            "fingerprint_verified": True,
        }
    pid = int(state.pid or 0)
    pgid = state.pgid
    completed_record: dict[str, Any] | None = None
    exit_record_errors: list[str] = []
    try:
        candidate_record = read_exit_record(root, repo_root=Path(repo_root))
    except StorageError as exc:
        candidate_record = None
        exit_record_errors.append(exc.message)
    if candidate_record is not None:
        exit_record_errors.extend(_validate_exit_record(candidate_record, state))
        if not exit_record_errors:
            completed_record = candidate_record

    # A sidecar is completion evidence only after its exact supervisor PID and
    # marker-bound descendants are dead. Never probe a reusable numeric PGID.
    if completed_record is not None:
        _reap_supervisor_if_child(state.pid)
        pid_alive = bool(pid and _pid_alive(pid))
        supervised_pids = _supervised_alive(state)
        if not pid_alive and not supervised_pids:
            state.exit_code = int(completed_record["exit_code"])
            if state.status not in {"complete", "failed", "blocked", "stopped"}:
                state.status = "stopped"
                state.next_action = "stopped"
                state.completed_at = state.completed_at or _utc_now()
            closed = _retire_process_identity(
                state,
                reason="host_stop_after_validated_exit",
                evidence=completed_record,
            )
            _record_interruption(
                state, closed_identity=closed, reason="host acknowledged prior exit"
            )
            save_state(repo_root, state)
            return {
                "ok": True,
                "action": "full_run_stop",
                "session_id": session_id,
                "signaled": False,
                "still_alive": False,
                "status": state.status,
                "reason": "supervisor domain already exited with a validated record",
                "fingerprint_verified": True,
            }

    pid_alive = bool(pid and _pid_alive(pid))
    supervised_pids = _supervised_alive(state)
    if not pid_alive and not supervised_pids:
        if (
            state.fingerprint
            and completed_record is None
            and state.status not in {"stopped", "complete", "failed", "blocked"}
        ):
            raise ValidationIssue(
                "full_run_fingerprint_mismatch",
                "Recorded supervisor disappeared without a validated exit record",
            )
        if state.status not in {"complete", "failed", "blocked"}:
            state.status = "stopped"
            state.next_action = "stopped"
            state.completed_at = state.completed_at or _utc_now()
            save_state(repo_root, state)
        return {
            "ok": True,
            "action": "full_run_stop",
            "session_id": session_id,
            "signaled": False,
            "still_alive": False,
            "status": state.status,
            "reason": "no live supervisor identity or marker-bound descendants",
            "fingerprint_verified": bool(completed_record),
            "exit_record_errors": exit_record_errors,
        }

    fingerprint_verified = False
    if pid_alive and state.fingerprint:
        ok, reason = verify_fingerprint(
            state.fingerprint, expected_session_id=session_id
        )
        if not ok:
            raise ValidationIssue(
                "full_run_fingerprint_mismatch",
                f"Refusing stop: {reason}",
                path=str(root / "worker.fingerprint.json"),
                hint="PID may have been reused; investigate before signaling",
            )
        fingerprint_verified = True
    elif pid_alive:
        raise ValidationIssue(
            "full_run_fingerprint_missing",
            "Refusing stop: live supervisor PID has no exact fingerprint",
        )
    elif supervised_pids:
        # Never signal cached descendant PIDs or a reusable numeric PGID from the
        # host. Recursive cleanup is owned by the still-fingerprinted supervisor;
        # once it is gone, surviving descendants are a wake-worthy failure that
        # requires operator inspection rather than risking an unrelated process.
        raise ValidationIssue(
            "full_run_supervisor_missing_with_descendants",
            "Refusing direct descendant signaling after the supervisor identity disappeared",
        )

    signaled = False
    if pid_alive and state.fingerprint:
        signaled = _signal_verified_supervisor(
            state.fingerprint,
            expected_session_id=session_id,
            signum=signal.SIGTERM,
        )

    # The embedded supervisor may need to terminate a provider plus recursively
    # discovered descendants before it publishes the exit record. Give that
    # identity-bound cleanup a small floor even when callers request zero grace.
    deadline = time.monotonic() + min(max(grace_seconds, 2.5), 5.0)
    while time.monotonic() < deadline:
        if (
            not bool(pid and _pid_alive(pid))
            and not _supervised_alive(state)
        ):
            break
        time.sleep(0.05)

    pid_alive = bool(pid and _pid_alive(pid))
    supervised_pids = _supervised_alive(state)
    still_alive = pid_alive or bool(supervised_pids)
    if still_alive:
        # Escalate only the exact supervisor identity. If it has already exited,
        # do not target reusable PGID/descendant integers; the final state below
        # remains failed with the surviving-domain evidence intact.
        if pid_alive and state.fingerprint:
            _signal_verified_supervisor(
                state.fingerprint,
                expected_session_id=session_id,
                signum=signal.SIGKILL,
            )
        kill_deadline = time.monotonic() + 1.0
        while time.monotonic() < kill_deadline:
            if (
                not bool(pid and _pid_alive(pid))
                and not _supervised_alive(state)
            ):
                break
            time.sleep(0.05)
        still_alive = (
            bool(pid and _pid_alive(pid))
            or bool(_supervised_alive(state))
        )

    state.status = "failed" if still_alive else "stopped"
    state.next_action = "driver_wake_error" if still_alive else "stopped"
    if still_alive:
        state.blocker = (
            "supervised process domain remains alive after identity-bound supervisor signals"
        )
    state.completed_at = _utc_now()
    if not still_alive:
        evidence = completed_record or {
            "authority": "host_stop",
            "signaled": signaled,
            "observed_pid_dead": True,
            "observed_pgid_dead": True,
            "observed_descendants_absent": True,
        }
        closed = _retire_process_identity(
            state,
            reason="host_authenticated_interruption",
            evidence=evidence,
        )
        _record_interruption(
            state, closed_identity=closed, reason="host stop observed full domain exit"
        )
    save_state(repo_root, state)
    _append_event(
        root / "events.jsonl",
        {
            "timestamp": state.completed_at,
            "session_id": session_id,
            "branch": state.branch,
            "head": state.head or state.start_head,
            "batch": state.batch or 0,
            "type": "heartbeat",
            "summary": "Supervisor stop requested; exact supervisor identity signaled",
        },
        expected_session_id=session_id,
        expected_branch=state.branch,
        repo_root=Path(repo_root),
    )
    return {
        "ok": not still_alive,
        "action": "full_run_stop",
        "session_id": session_id,
        "signaled": signaled,
        "still_alive": still_alive,
        "status": state.status,
        "fingerprint_verified": fingerprint_verified,
        "exit_record_errors": exit_record_errors,
    }


def logs_full_run(
    repo_root: Path,
    *,
    session_id: str,
    raw_tail: bool = False,
    tail_lines: int = 40,
) -> dict[str, Any]:
    bounded_tail = min(100, max(0, int(tail_lines)))
    root = full_run_root(repo_root, session_id)
    state = load_state(repo_root, session_id)
    exact_secret_values = _state_secret_values(state)
    launch_grants_verified = _launch_grants_verified(state)
    if launch_grants_verified:
        events, errors = _read_events(
            root / "events.jsonl",
            expected_session_id=session_id,
            expected_branch=state.branch,
            exact_secret_values=exact_secret_values,
            allow_partial_final=bool(state.pid or state.pgid),
            repo_root=Path(repo_root),
        )
    else:
        events, errors = [], [
            "launch credential context cannot be verified for worker logs"
        ]
    payload: dict[str, Any] = {
        "ok": not errors,
        "session_id": session_id,
        "events_tail": events[-bounded_tail:] if bounded_tail else [],
        "event_errors": errors[-20:],
        "transcript_included": False,
        "merge_authority": False,
    }
    if raw_tail and not launch_grants_verified:
        payload["transcript_error"] = (
            "raw transcript unavailable: launch credential context cannot be verified"
        )
    elif raw_tail:
        transcript = root / "transcript.log"
        try:
            transcript_exists = repo_regular_file_exists(Path(repo_root), transcript)
        except StorageError as exc:
            payload["transcript_error"] = exc.message
            transcript_exists = False
        if transcript_exists:
            try:
                raw_lines = _bounded_text_tail(
                    transcript,
                    lines=bounded_tail,
                    repo_root=Path(repo_root),
                )
            except StorageError as exc:
                payload["transcript_error"] = exc.message
                raw_lines = []
            raw_window = "\n".join(raw_lines)
            if _PEM_BOUNDARY_RE.search(raw_window):
                payload["transcript_tail"] = ["[REDACTED:pem_block]"]
            else:
                redacted_window = _redact_full_run_text(
                    raw_window, exact_values=exact_secret_values
                )
                payload["transcript_tail"] = [
                    line[-MAX_TRANSCRIPT_LINE_CHARS:]
                    for line in redacted_window.splitlines()
                ]
            payload["transcript_included"] = True
    _redact_full_run_mapping_in_place(payload, exact_values=exact_secret_values)
    return payload


def write_report(repo_root: Path, session_id: str, report: Mapping[str, Any]) -> Path:
    state = load_state(repo_root, session_id)
    if not _launch_grants_verified(state):
        raise ValidationIssue(
            "full_run_credential_context_unverified",
            "Cannot accept worker report without the exact launch credential context",
        )
    exact_secret_values = _state_secret_values(state)
    errors = validate_run_report(
        report,
        expected_session_id=session_id,
        expected_branch=state.branch,
        expected_start_head=state.start_head,
        require_complete_acceptance=report.get("status") == "complete",
        expected_run_id=_expected_run_id(session_id),
        expected_attempt=state.attempt,
        exact_secret_values=exact_secret_values,
    )
    if errors:
        raise ValidationIssue("full_run_report_invalid", "; ".join(errors))
    # Reports never grant merge authority.
    payload = dict(report)
    payload["merge_authority"] = False
    path = full_run_root(repo_root, session_id) / "report.json"
    atomic_write_json(path, payload, repo_root=Path(repo_root))
    return path


@_locked_full_run
def reconcile_full_run_with_git(
    repo_root: Path,
    *,
    session_id: str,
) -> dict[str, Any]:
    """Verify feature-branch advance and report heads at the supervisor boundary."""
    from .delegated_git import (  # noqa: PLC0415
        DelegatedGitContract,
        assert_action_allowed,
        assert_descendant,
        assert_feature_branch,
        reconcile_worker_report,
    )

    state = load_state(repo_root, session_id)
    if not _launch_grants_verified(state):
        raise ValidationIssue(
            "full_run_credential_context_unverified",
            "Cannot reconcile worker evidence without the exact launch credential context",
        )
    worktree = Path(state.worktree)
    if state.launch_start_head != state.start_head:
        raise ValidationIssue(
            "full_run_start_head_mutated",
            "Immutable launch start no longer matches staged start_head",
        )
    assert_feature_branch(worktree, state.branch)
    tip = assert_descendant(worktree, ancestor=state.start_head)
    _assert_clean_worktree(worktree)
    contract = DelegatedGitContract(
        feature_branch=state.branch,
        base_branch="main",
        start_head=state.start_head,
        session_id=session_id,
        run_id=_expected_run_id(session_id),
    )
    # Protected actions remain forbidden at policy boundary.
    for action in ("merge", "tag", "force_push", "change_base"):
        try:
            assert_action_allowed(contract, action)
            raise ValidationIssue(
                "delegated_git_policy_broken",
                f"Protected action `{action}` was unexpectedly allowed",
            )
        except ValidationIssue as issue:
            if issue.code not in {"delegated_git_protected", "delegated_git_forbidden"}:
                raise

    report_path = full_run_root(repo_root, session_id) / "report.json"
    report: dict[str, Any] = {}
    exact_secret_values = _state_secret_values(state)
    try:
        report_exists = repo_regular_file_exists(Path(repo_root), report_path)
    except StorageError as exc:
        raise ValidationIssue("full_run_report_invalid", exc.message) from exc
    if report_exists:
        try:
            report = _read_bounded_json_object(
                report_path,
                label="run report",
                repo_root=Path(repo_root),
            )
        except StorageError as exc:
            raise ValidationIssue("full_run_report_invalid", exc.message) from exc
        errs = validate_run_report(
            report,
            expected_session_id=session_id,
            expected_branch=state.branch,
            expected_start_head=state.start_head,
            require_complete_acceptance=report.get("status") == "complete",
            expected_run_id=_expected_run_id(session_id),
            expected_attempt=state.attempt,
            exact_secret_values=exact_secret_values,
        )
        redacted_report = _redact_full_run_structure(
            report, exact_values=exact_secret_values
        )
        if redacted_report != report:
            atomic_write_json(
                report_path,
                redacted_report,
                repo_root=Path(repo_root),
            )
        if errs:
            raise ValidationIssue("full_run_report_invalid", "; ".join(errs))
    if not report or report.get("status") != "complete":
        raise ValidationIssue(
            "full_run_report_incomplete",
            "Final Git reconciliation requires a validated complete report",
        )
    if str(report.get("final_head") or "") != tip:
        raise ValidationIssue(
            "full_run_report_head_mismatch",
            "Complete report final_head does not equal the local feature tip",
        )
    events, event_errors = _read_events(
        full_run_root(repo_root, session_id) / "events.jsonl",
        expected_session_id=session_id,
        expected_branch=state.branch,
        exact_secret_values=exact_secret_values,
        allow_partial_final=False,
        repo_root=Path(repo_root),
    )
    evidence_errors = event_errors + _validate_git_bound_evidence(
        state, report, events, tip
    )
    if evidence_errors:
        raise ValidationIssue(
            "full_run_git_evidence_mismatch", "; ".join(evidence_errors[:6])
        )

    host_state = {
        "merge_on_green": False,
        "stop_allowed": False,
        "run_mode": "finite",
        "pr_number": None,
        "driver_monitor_mode": "parked_monitor",
    }
    if report:
        merged = reconcile_worker_report(
            host_state,
            report,
            expected_session_id=session_id,
            expected_branch=state.branch,
            expected_start_head=state.start_head,
        )
    else:
        merged = dict(host_state)
        merged["final_head"] = tip
    # Host controls preserved
    if merged.get("merge_on_green") is not False:
        raise ValidationIssue(
            "report_reconciliation_failed",
            "Host merge_on_green control was not preserved",
        )
    # Protected refs must be unchanged (policy trust — not OS Git sandbox).
    protected_errors = verify_protected_refs_unchanged(
        Path(repo_root),
        state.protected_refs or {},
        feature_branch=state.branch,
    )
    if protected_errors:
        raise ValidationIssue(
            "protected_ref_moved",
            "; ".join(protected_errors),
        )
    # Local + remote feature head / ancestry when remotes are present.
    local_head = tip
    remote_head = _assert_origin_binding(
        Path(repo_root), state, expected_feature_tip=local_head
    )
    if not remote_head or not _is_ancestor(worktree, state.start_head, remote_head):
        raise ValidationIssue(
            "full_run_remote_feature_ancestry",
            "Origin feature tip must be an exact descendant of immutable start_head",
        )
    return {
        "ok": True,
        "session_id": session_id,
        "branch": state.branch,
        "start_head": state.start_head,
        "final_head": tip,
        "local_feature_head": local_head,
        "remote_feature_head": remote_head,
        "protected_refs_ok": True,
        "merged_host_state": {
            k: merged.get(k)
            for k in ("merge_on_green", "stop_allowed", "driver_monitor_mode", "final_head")
        },
        "merge_authority": False,
        "policy_trust_not_os_git_sandbox": True,
    }
