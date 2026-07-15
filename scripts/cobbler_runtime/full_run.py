"""Full-run supervisor for trusted delegated implementers (Lane A / Grok Build).

Uses adapter-aware ``implement.build_launch_argv`` for real Grok create/resume.
Fixture mode is explicit (``adapter=fixture``) for unit tests only.

Artifacts live under digest-keyed private paths. Worker events enrich telemetry;
liveness also comes from process fingerprint + observed feature-branch HEAD.
A worker report is evidence only — never merge authority.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import hashlib
import hmac
import json
import math
import mmap
import os
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from .acceptance import (
    STABLE_ACCEPTANCE_ID_RE,
    parse_plan_acceptance_contract,
    parse_markdown_acceptance_rows,
    validate_contract_mapping,
)
from .context import redact_text, validate_credential_grant_names
from .implement import (
    DEFAULT_EFFORT,
    DEFAULT_EXECUTABLE,
    DEFAULT_MODEL,
    DEFAULT_PERMISSION_MODE,
    build_launch_argv,
)
from .schema import ValidationIssue
from .toml_compat import loads as _load_toml
from .storage import (
    StorageError,
    _assert_directory_fd_identity,
    _open_repo_directory,
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
        "high_risk_checkpoint",
        "material_scope_or_assumption_change",
        "blocked",
        "run_complete",
        "devin_session_captured",
        "devin_capture_failed",
    }
)
TERMINAL_EVENT_TYPES = frozenset({"run_complete", "blocked"})
DEFAULT_STALE_SECONDS = 300
# Darwin process-group reaping is slower under CI load; give the exact recorded
# supervisor a little longer to leave the group before failing closed.
EXIT_RECORD_SETTLE_SECONDS = 0.75 if sys.platform == "darwin" else 0.25
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
_REPORT_STATUSES = frozenset({"running", "complete", "blocked", "failed", "stopped"})
_ACCEPTANCE_ID_RE = STABLE_ACCEPTANCE_ID_RE
_HIGH_RISK_CHECKPOINT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_MATERIAL_CHANGE_ID_RE = _HIGH_RISK_CHECKPOINT_ID_RE
_MATERIAL_CHANGE_KINDS = frozenset({"scope", "assumption"})
WORKER_EVENT_CONTRACT = {
    "version": 1,
    "required": [
        "timestamp",
        "session_id",
        "branch",
        "head",
        "batch",
        "type",
        "summary",
    ],
    "types": sorted(EVENT_TYPES),
    "material_change": {
        "type": "material_scope_or_assumption_change",
        "required": ["change_id", "change_kind"],
        "change_kind": sorted(_MATERIAL_CHANGE_KINDS),
        "driver_action": "wake",
    },
}
_HIGH_RISK_CHECKPOINT_DEFINITION_RE = re.compile(
    r"(?im)^\s*[-*]\s+high-risk\s+checkpoint\s*:\s*"
    r"(?P<id>[A-Za-z0-9][A-Za-z0-9._-]{0,63})\s*$"
)
_ENV_GRANT_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
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
MAX_HIGH_RISK_CHECKPOINTS = 64
MAX_REPORT_BYTES = 512 * 1024
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 20_000
MAX_JSON_KEYS = 5_000
MAX_JSON_STRING_CHARS = 64 * 1024
MAX_JSON_KEY_CHARS = 512
MAX_JSON_TOTAL_STRING_CHARS = MAX_REPORT_BYTES
MAX_JSON_INTEGER_BITS = 256
MAX_JSON_NUMBER_CHARS = 128
MAX_TRANSCRIPT_TAIL_BYTES = 256 * 1024
MAX_TRANSCRIPT_LINE_CHARS = 1000
MAX_GROK_AUTH_BYTES = 64 * 1024
MAX_EVENT_FUTURE_SKEW_SECONDS = 300
MAX_STOP_REQUEST_BYTES = 4096
STOP_REQUEST_NAME = "stop_request.json"
GROK_HOME_REL = Path("worker-grok-home")
GROK_AUTH_FILE_NAME = "auth.json"
GROK_AUTH_PATH_MIN_VERSION = (0, 2, 93)
MAX_GROK_EXECUTABLE_PROBE_BYTES = 512 * 1024 * 1024
MAX_GITHUB_TOKEN_BYTES = 64 * 1024
MAX_GIT_IDENTITY_BYTES = 4096
MAX_DEVIN_AUTH_BYTES = 64 * 1024
DEVIN_CONFIG_FILE_NAME = "config.json"
DEVIN_CREDENTIALS_FILE_NAME = "credentials.toml"
DEVIN_HOST_EVENT_TYPES = frozenset(
    {"devin_session_captured", "devin_capture_failed"}
)
_GROK_VERSION_RE = re.compile(r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?!\d)")
_GITHUB_PUSH_TOKEN_NAMES = ("GH_TOKEN", "GITHUB_TOKEN")
_GITHUB_PUSH_AUTH_STRATEGIES = frozenset(
    {"host_gh_token", "env_gh_token", "env_github_token"}
)
_GITHUB_CREDENTIAL_HELPER = (
    "!f() { test \"$1\" = get || exit 0; "
    "printf 'username=%s\\npassword=%s\\n' x-access-token "
    "\"${GH_TOKEN:-${GITHUB_TOKEN:-}}\"; }; f"
)
_DARWIN_ACL_TYPE_EXTENDED = 0x00000100
_DARWIN_ACL_FIRST_ENTRY = 0
_DARWIN_ACL_NEXT_ENTRY = -1
_DARWIN_ACL_EXTENDED_ALLOW = 1
_DARWIN_ACL_EXTENDED_DENY = 2
_DARWIN_ACL_API: tuple[Any, Any, Any, Any] | None = None
_MACH_O_MAGICS = frozenset(
    {
        bytes.fromhex(value)
        for value in (
            "feedface",
            "cefaedfe",
            "feedfacf",
            "cffaedfe",
            "cafebabe",
            "bebafeca",
            "cafebabf",
            "bfbafeca",
        )
    }
)

# Named non-secret essentials preserved for a usable Grok process. Home, temp,
# XDG, and proxy controls are deliberately absent: they either cross the
# isolation boundary or may embed opaque credentials.
NON_SECRET_ESSENTIALS: frozenset[str] = frozenset(
    {
        "PATH",
        "USER",
        "LOGNAME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TERM",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "PYTHONUNBUFFERED",
        "COLORTERM",
    }
)

# Credential grants are explicit by name (values never appear as argv KEY=VALUE).
# In particular, do not leak unrelated OPENAI/GROK variables into a Grok run.
DEFAULT_CREDENTIAL_GRANT_NAMES: frozenset[str] = frozenset()

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
        "poll_after_seconds",
        "user_heartbeat_seconds",
        "chat_update_policy",
        "chat_update_recommended",
        "unchanged_healthy_poll_silent",
        "material_transition",
        "monitor_depth",
        "remote_all_ref_audit",
        "goal_launch_mode",
        "report_provenance",
        "wake_conditions",
        "planned_high_risk_checkpoints",
        "pending_high_risk_checkpoint",
        "acknowledged_high_risk_checkpoints",
        "check_summary",
        "report_path",
        "events_path",
        "transcript_private",
        "adapter",
        "fingerprint_ok",
        "merge_authority",
    }
)


def _normalize_credential_grant_names(
    names: Sequence[str] | None,
) -> list[str]:
    """Return deterministic environment-name grants without ever echoing bad input."""
    if names is None:
        return sorted(DEFAULT_CREDENTIAL_GRANT_NAMES)
    if isinstance(names, (str, bytes)) or not isinstance(names, Sequence):
        raise ValidationIssue(
            "full_run_credential_grant_name_invalid",
            "Credential grants must be supplied as a sequence of environment names",
            hint="Use ['XAI_API_KEY'] in Python or --grant-env XAI_API_KEY in the CLI",
        )
    normalized: set[str] = set()
    for name in names:
        if not isinstance(name, str) or not _ENV_GRANT_NAME_RE.fullmatch(name):
            raise ValidationIssue(
                "full_run_credential_grant_name_invalid",
                "Credential grants must be environment variable names only",
                hint="Use --grant-env XAI_API_KEY, never KEY=VALUE",
            )
        normalized.add(name)
    ordered = sorted(normalized)
    validate_credential_grant_names(
        ordered,
        code="full_run_isolation_control_grant_forbidden",
        path="credential_grant_names",
    )
    return ordered


def _normalize_persisted_credential_grant_names(
    value: Any,
) -> tuple[bool, list[str]]:
    """Parse one persisted grant-name field without trusting JSON field types.

    Persisted launch evidence is canonical only as a sorted, duplicate-free JSON
    array.  Corrupt scalar/string values must make evidence unavailable, never
    escape as a raw iteration ``TypeError`` from a public monitor or log call.
    """
    if not isinstance(value, list):
        return False, []
    try:
        normalized = _normalize_credential_grant_names(value)
    except ValidationIssue:
        return False, []
    return value == normalized, normalized


class _PreTransferHandoffError(ValidationIssue):
    """The staged supervisor was killed before provider start became possible."""

    def __init__(self, cause: BaseException) -> None:
        super().__init__(
            "full_run_supervision_secret_handoff_not_transferred",
            "Supervisor start capability was not transferred",
            hint="The staged child was killed and reaped before provider spawn",
        )
        self.cause_type = type(cause).__name__


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


def _collision_safe_mapping_keys(desired_keys: Sequence[str]) -> list[str]:
    """Allocate deterministic redacted keys without shadowing real key names.

    The full set of primary names is reserved before suffixes are allocated.
    Otherwise a generated ``#N`` name can overwrite a later, pre-existing key
    with that exact suffix and silently discard evidence.
    """
    reserved = set(desired_keys)
    used: set[str] = set()
    allocated: list[str] = []
    for desired in desired_keys:
        candidate = desired
        if candidate in used:
            suffix = 1
            while True:
                candidate = f"{desired}#{suffix}"
                if candidate not in used and candidate not in reserved:
                    break
                suffix += 1
        used.add(candidate)
        allocated.append(candidate)
    return allocated


def _redact_full_run_structure(
    value: Any,
    *,
    exact_values: frozenset[str] | set[str] | tuple[str, ...] | None = None,
) -> Any:
    """Recursively redact keys and values before any driver-visible serialization."""
    if isinstance(value, str):
        return _redact_full_run_text(value, exact_values=exact_values)
    if isinstance(value, Mapping):
        rows: list[tuple[str, Any, bool]] = []
        desired_keys: list[str] = []
        for key, item in value.items():
            raw_key = str(key)
            redacted_key = _redact_full_run_text(
                raw_key,
                exact_values=exact_values,
            )
            secret_field = bool(_FULL_RUN_SECRET_KEY_RE.search(raw_key))
            if secret_field:
                redacted_key = "[REDACTED:secret_field_name]"
            rows.append((redacted_key, item, secret_field))
            desired_keys.append(redacted_key)
        redacted_mapping: dict[str, Any] = {}
        allocated_keys = _collision_safe_mapping_keys(desired_keys)
        for redacted_key, (_desired, item, secret_field) in zip(
            allocated_keys, rows
        ):
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
    credential_grant_state: "FullRunState | None" = None,
) -> bool:
    if _redact_full_run_structure(value, exact_values=exact_values) != value:
        return True
    return bool(
        credential_grant_state
        and _contains_persisted_credential_grant(value, credential_grant_state)
    )


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


def _bounded_json_int(raw: str) -> int:
    digits = raw[1:] if raw.startswith("-") else raw
    if len(digits) > MAX_JSON_NUMBER_CHARS:
        raise ValueError("JSON integer token exceeds the numeric budget")
    value = int(raw)
    if value.bit_length() > MAX_JSON_INTEGER_BITS:
        raise ValueError("JSON integer exceeds the numeric budget")
    return value


def _bounded_json_float(raw: str) -> float:
    if len(raw) > MAX_JSON_NUMBER_CHARS:
        raise ValueError("JSON number token exceeds the numeric budget")
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError("JSON number must be finite")
    return value


def _reject_json_constant(_raw: str) -> Any:
    raise ValueError("non-standard JSON constants are forbidden")


def _bounded_json_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    if len(pairs) > MAX_JSON_KEYS:
        raise ValueError("JSON object exceeds the key budget")
    value: dict[str, Any] = {}
    for key, child in pairs:
        if len(key) > MAX_JSON_KEY_CHARS:
            raise ValueError("JSON key exceeds the character budget")
        if key in value:
            raise ValueError("duplicate JSON object keys are forbidden")
        value[key] = child
    return value


def _loads_bounded_json(text: str, *, label: str) -> Any:
    """Parse standard JSON while bounding numeric work before conversion."""
    del label  # Kept in the signature so callers cannot forget the evidence surface.
    return json.loads(
        text,
        parse_int=_bounded_json_int,
        parse_float=_bounded_json_float,
        parse_constant=_reject_json_constant,
        object_pairs_hook=_bounded_json_object_pairs,
    )


def _assert_bounded_json_structure(value: Any, *, label: str) -> None:
    """Iteratively bound JSON structure before recursive scans/redaction.

    Byte ceilings alone do not bound parser recursion, node fan-out, or the
    substring work performed by exact-secret detection. This validator runs
    before any recursive evidence handling and deliberately avoids recursion.
    """
    stack: list[tuple[Any, int]] = [(value, 0)]
    node_count = 0
    key_count = 0
    total_string_chars = 0
    while stack:
        item, depth = stack.pop()
        node_count += 1
        if node_count > MAX_JSON_NODES:
            raise StorageError(
                f"{label}_structure",
                f"{label} exceeds the {MAX_JSON_NODES} node budget",
            )
        if depth > MAX_JSON_DEPTH:
            raise StorageError(
                f"{label}_structure",
                f"{label} exceeds the {MAX_JSON_DEPTH} level depth budget",
            )
        if isinstance(item, str):
            if len(item) > MAX_JSON_STRING_CHARS:
                raise StorageError(
                    f"{label}_structure",
                    f"{label} contains a string exceeding the character budget",
                )
            total_string_chars += len(item)
        elif isinstance(item, Mapping):
            key_count += len(item)
            if key_count > MAX_JSON_KEYS:
                raise StorageError(
                    f"{label}_structure",
                    f"{label} exceeds the {MAX_JSON_KEYS} key budget",
                )
            for key, child in item.items():
                if not isinstance(key, str):
                    raise StorageError(
                        f"{label}_structure",
                        f"{label} contains a non-string object key",
                    )
                if len(key) > MAX_JSON_KEY_CHARS:
                    raise StorageError(
                        f"{label}_structure",
                        f"{label} contains a key exceeding the character budget",
                    )
                total_string_chars += len(key)
                stack.append((child, depth + 1))
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, bool) or item is None:
            pass
        elif isinstance(item, int):
            if item.bit_length() > MAX_JSON_INTEGER_BITS:
                raise StorageError(
                    f"{label}_structure",
                    f"{label} contains an integer exceeding the numeric budget",
                )
        elif isinstance(item, float):
            if not math.isfinite(item):
                raise StorageError(
                    f"{label}_structure",
                    f"{label} contains a non-finite number",
                )
        else:
            raise StorageError(
                f"{label}_structure",
                f"{label} contains a non-JSON value",
            )
        if total_string_chars > MAX_JSON_TOTAL_STRING_CHARS:
            raise StorageError(
                f"{label}_structure",
                f"{label} exceeds the total string character budget",
            )


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
        value = _loads_bounded_json(text, label=label)
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise StorageError(
            f"{label}_malformed",
            f"{label} must contain one bounded valid JSON object",
        ) from exc
    if not isinstance(value, dict):
        raise StorageError(
            f"{label}_malformed", f"{label} must contain one JSON object"
        )
    _assert_bounded_json_structure(value, label=label)
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
    expected_high_risk_checkpoints: Sequence[str] | None = None,
    seen_high_risk_checkpoints: Sequence[str] | None = None,
    exact_secret_values: frozenset[str] | set[str] | tuple[str, ...] | None = None,
    credential_grant_state: "FullRunState | None" = None,
) -> list[str]:
    errors: list[str] = []
    try:
        _assert_bounded_json_structure(event, label="event")
    except StorageError as exc:
        return [exc.message]
    secret_detected = _contains_full_run_secret(
        event,
        exact_values=exact_secret_values,
        credential_grant_state=credential_grant_state,
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
    checkpoint_id = event.get("checkpoint_id")
    if etype == "high_risk_checkpoint":
        if (
            not isinstance(checkpoint_id, str)
            or not _HIGH_RISK_CHECKPOINT_ID_RE.fullmatch(checkpoint_id)
        ):
            errors.append("high-risk checkpoint event requires a stable checkpoint_id")
        else:
            if (
                expected_high_risk_checkpoints is not None
                and checkpoint_id not in expected_high_risk_checkpoints
            ):
                errors.append("high-risk checkpoint was not staged in the packet")
            if (
                seen_high_risk_checkpoints is not None
                and checkpoint_id in seen_high_risk_checkpoints
            ):
                errors.append("high-risk checkpoint event is duplicated")
    elif checkpoint_id is not None:
        errors.append("checkpoint_id is only valid on high-risk checkpoint events")
    change_id = event.get("change_id")
    change_kind = event.get("change_kind")
    if etype == "material_scope_or_assumption_change":
        if (
            not isinstance(change_id, str)
            or not _MATERIAL_CHANGE_ID_RE.fullmatch(change_id)
        ):
            errors.append("material change event requires a stable change_id")
        if change_kind not in _MATERIAL_CHANGE_KINDS:
            errors.append("material change event requires change_kind scope or assumption")
    else:
        if change_id is not None:
            errors.append("change_id is only valid on material change events")
        if change_kind is not None:
            errors.append("change_kind is only valid on material change events")
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
    # Provider-session discovery is host-owned transport evidence. A very fast
    # Devin process can write its terminal event before the parked monitor gets
    # its first chance to bind the provider UUID, so these two informational
    # host events may legitimately follow the worker terminal event. All worker
    # lifecycle events remain forbidden after terminal.
    if seen_terminal and etype not in DEVIN_HOST_EVENT_TYPES:
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
    expected_acceptance_criteria: Mapping[str, str] | None = None,
    exact_secret_values: frozenset[str] | set[str] | tuple[str, ...] | None = None,
    credential_grant_state: "FullRunState | None" = None,
) -> list[str]:
    errors: list[str] = []
    try:
        _assert_bounded_json_structure(report, label="run report")
    except StorageError as exc:
        return [exc.message]
    if _contains_full_run_secret(
        report,
        exact_values=exact_secret_values,
        credential_grant_state=credential_grant_state,
    ):
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
        if status == "complete" and expected_acceptance_criteria is not None:
            observed_criteria = {
                str(item.get("id")): item.get("criterion")
                for item in acceptance
                if isinstance(item, Mapping) and isinstance(item.get("id"), str)
            }
            if set(observed_criteria) != set(expected_acceptance_criteria):
                errors.append(
                    "report acceptance ids do not exactly match staged criteria"
                )
            for acceptance_id in sorted(
                set(observed_criteria) & set(expected_acceptance_criteria)
            ):
                if observed_criteria[acceptance_id] != expected_acceptance_criteria[acceptance_id]:
                    errors.append(
                        f"report acceptance criterion text mismatch for {acceptance_id}"
                    )
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
        if not isinstance(data, Mapping):
            raise TypeError("process fingerprint must be a mapping")
        pid = data.get("pid")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            raise TypeError("process fingerprint pid must be a positive integer")
        pgid = data.get("pgid")
        if pgid is not None and (
            not isinstance(pgid, int) or isinstance(pgid, bool) or pgid <= 0
        ):
            raise TypeError("process fingerprint pgid must be a positive integer or null")
        for field_name in ("start_time", "executable", "session_id"):
            value = data.get(field_name)
            if value is not None and not isinstance(value, str):
                raise TypeError(f"process fingerprint {field_name} must be a string or null")
        return cls(
            pid=pid,
            pgid=pgid,
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
    # ``packet_path`` remains the immutable source location staged by the host.
    # The provider consumes only ``staged_packet_path``, a private copy whose
    # bytes and parsed acceptance contract are bound below at prepare time.
    packet_path: str
    staged_packet_path: str | None = None
    staged_packet_identity: dict[str, Any] | None = None
    packet_sha256: str | None = None
    packet_size: int | None = None
    packet_contract_sha256: str | None = None
    acceptance_criteria: dict[str, str] = field(default_factory=dict)
    acceptance_plan_path: str | None = None
    acceptance_plan_sha256: str | None = None
    acceptance_session_path: str | None = None
    acceptance_session_sha256: str | None = None
    acceptance_contract_sha256: str | None = None
    # Packet-bound driver wake gates. Worker events may only name staged IDs;
    # acknowledgements are host-owned and one-shot within an attempt.
    planned_high_risk_checkpoints: list[str] = field(default_factory=list)
    acknowledged_high_risk_checkpoints: list[str] = field(default_factory=list)
    pending_high_risk_checkpoint: str | None = None
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
    credential_grant_lengths: dict[str, int] = field(default_factory=dict)
    credential_grant_metadata_mac: str | None = None
    grok_auth_strategy: str | None = None
    github_push_auth_strategy: str | None = None
    # Private state only. The leaf inode is deliberately excluded because Grok
    # atomically replaces auth.json when rotating refresh tokens. Binding the
    # canonical path and safe parent identity survives those legitimate writes.
    grok_auth_path_identity: dict[str, Any] | None = None
    # Exact resolved provider identity proven during auth/capability preflight
    # and rechecked by the child supervisor immediately before process spawn.
    grok_executable_identity: dict[str, Any] | None = None
    devin_auth_strategy: str | None = None
    # Private state only. Binds the canonical source path identity for Devin CLI
    # config and credentials. Raw credential bytes are never stored here.
    devin_auth_identity: dict[str, Any] | None = None
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
    # Bounded monitor cache: event file size/digest and last remote-audit stamp.
    # Used so unchanged healthy polls stay incremental.
    monitor_cache: dict[str, Any] = field(default_factory=dict)
    # native_goal | headless_compatible_fallback | fixture | devin_prompt_file | unknown
    goal_launch_mode: str | None = None
    report_provenance: str | None = None
    # Devin CLI: exact provider session id captured from `devin list --format json`.
    provider_session_id: str | None = None
    # Absolute path to the full-run runtime directory (used for ATIF export, etc.).
    runtime_dir: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FullRunState":
        if not isinstance(data, Mapping):
            raise TypeError("full-run state must be a mapping")
        required_strings = (
            "session_id",
            "branch",
            "start_head",
            "worktree",
            "packet_path",
        )
        for field_name in required_strings:
            value = data.get(field_name)
            if not isinstance(value, str) or not value:
                raise TypeError(f"{field_name} must be a non-empty string")
        nullable_strings = (
            "staged_packet_path",
            "packet_sha256",
            "packet_contract_sha256",
            "acceptance_plan_path",
            "acceptance_plan_sha256",
            "acceptance_session_path",
            "acceptance_session_sha256",
            "acceptance_contract_sha256",
            "credential_grant_metadata_mac",
            "grok_auth_strategy",
            "devin_auth_strategy",
            "github_push_auth_strategy",
            "head",
            "heartbeat_at",
            "launched_at",
            "completed_at",
            "blocker",
            "next_action",
            "fixture_script",
            "launch_start_head",
            "origin_url",
            "origin_config_digest",
            "initial_remote_feature_tip",
            "supervision_token",
            "supervisor_executable",
            "pending_high_risk_checkpoint",
            "goal_launch_mode",
            "report_provenance",
            "provider_session_id",
            "runtime_dir",
        )
        for field_name in nullable_strings:
            value = data.get(field_name)
            if value is not None and not isinstance(value, str):
                raise TypeError(f"{field_name} must be a string or null")
        default_string_fields = (
            "adapter",
            "model",
            "permission_mode",
            "effort",
            "executable",
            "output_format",
            "status",
            "driver_monitor_mode",
            "driver_contract",
        )
        for field_name in default_string_fields:
            value = data.get(field_name)
            if field_name in data and (not isinstance(value, str) or not value):
                raise TypeError(f"{field_name} must be a non-empty string")
        for field_name in ("create_session", "check", "yolo", "supervision_canary_passed"):
            value = data.get(field_name)
            if field_name in data and not isinstance(value, bool):
                raise TypeError(f"{field_name} must be a boolean")
        for field_name in ("max_turns", "attempt"):
            value = data.get(field_name)
            if field_name in data and (
                not isinstance(value, int) or isinstance(value, bool) or value <= 0
            ):
                raise TypeError(f"{field_name} must be a positive integer")
        for field_name in ("packet_size", "batch"):
            value = data.get(field_name)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 0
            ):
                raise TypeError(f"{field_name} must be a non-negative integer or null")
        for field_name in ("pid", "pgid", "exit_sidecar_pid"):
            value = data.get(field_name)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value <= 0
            ):
                raise TypeError(f"{field_name} must be a positive integer or null")
        exit_code = data.get("exit_code")
        if exit_code is not None and (
            not isinstance(exit_code, int) or isinstance(exit_code, bool)
        ):
            raise TypeError("exit_code must be an integer or null")
        nullable_mapping_fields = (
            "staged_packet_identity",
            "grok_auth_path_identity",
            "grok_executable_identity",
            "devin_auth_identity",
            "fingerprint",
            "closed_process_identity",
            "interruption_evidence",
        )
        for field_name in nullable_mapping_fields:
            value = data.get(field_name)
            if value is not None and not isinstance(value, Mapping):
                raise TypeError(f"{field_name} must be a mapping or null")
        default_mapping_fields = (
            "acceptance_criteria",
            "credential_grant_digests",
            "credential_grant_lengths",
            "protected_refs",
            "monitor_cache",
        )
        for field_name in default_mapping_fields:
            value = data.get(field_name)
            if field_name in data and not isinstance(value, Mapping):
                raise TypeError(f"{field_name} must be a mapping")
        string_list_fields = (
            "credential_grant_names",
            "credential_granted_names",
            "notes",
            "last_argv",
            "acceptance_ids",
            "planned_high_risk_checkpoints",
            "acknowledged_high_risk_checkpoints",
        )
        for field_name in string_list_fields:
            value = data.get(field_name)
            if field_name in data and (
                not isinstance(value, list)
                or not all(isinstance(item, str) for item in value)
            ):
                raise TypeError(f"{field_name} must be a string list")
        process_history = data.get("process_history")
        if "process_history" in data and (
            not isinstance(process_history, list)
            or not all(isinstance(item, Mapping) for item in process_history)
        ):
            raise TypeError("process_history must be a list of mappings")
        for field_name in (
            "acceptance_criteria",
            "credential_grant_digests",
            "protected_refs",
        ):
            value = data.get(field_name)
            if value is not None and not all(
                isinstance(key, str) and isinstance(item, str)
                for key, item in value.items()
            ):
                raise TypeError(f"{field_name} must be a string mapping")
        grant_lengths = data.get("credential_grant_lengths")
        if grant_lengths is not None and not all(
            isinstance(key, str)
            and isinstance(item, int)
            and not isinstance(item, bool)
            and item >= 0
            for key, item in grant_lengths.items()
        ):
            raise TypeError("credential_grant_lengths must map strings to non-negative integers")
        planned_checkpoints = data.get("planned_high_risk_checkpoints", [])
        acknowledged_checkpoints = data.get(
            "acknowledged_high_risk_checkpoints", []
        )
        pending_checkpoint = data.get("pending_high_risk_checkpoint")
        for label, values in (
            ("planned_high_risk_checkpoints", planned_checkpoints),
            ("acknowledged_high_risk_checkpoints", acknowledged_checkpoints),
        ):
            if (
                not isinstance(values, list)
                or len(values) > MAX_HIGH_RISK_CHECKPOINTS
                or len(values) != len(set(values))
                or any(
                    not isinstance(item, str)
                    or not _HIGH_RISK_CHECKPOINT_ID_RE.fullmatch(item)
                    for item in values
                )
            ):
                raise TypeError(f"{label} must contain unique checkpoint ids")
        if not set(acknowledged_checkpoints).issubset(set(planned_checkpoints)):
            raise TypeError("acknowledged checkpoints must be planned")
        if pending_checkpoint is not None and (
            not isinstance(pending_checkpoint, str)
            or pending_checkpoint not in planned_checkpoints
            or pending_checkpoint in acknowledged_checkpoints
        ):
            raise TypeError("pending checkpoint must be planned and unacknowledged")
        github_push_auth_strategy = data.get("github_push_auth_strategy")
        if (
            github_push_auth_strategy is not None
            and github_push_auth_strategy not in _GITHUB_PUSH_AUTH_STRATEGIES
        ):
            raise TypeError("github_push_auth_strategy is invalid")
        fingerprint = data.get("fingerprint")
        if fingerprint is not None:
            parsed_fingerprint = ProcessFingerprint.from_dict(fingerprint)
            if parsed_fingerprint.session_id != data["session_id"]:
                raise TypeError("process fingerprint session identity mismatch")
        known = set(cls.__dataclass_fields__)
        filtered = {k: v for k, v in data.items() if k in known}
        state = cls(**filtered)  # type: ignore[arg-type]
        state.driver_monitor_mode = "parked_monitor"
        state.driver_contract = state.driver_monitor_mode
        if state.launch_start_head is None:
            state.launch_start_head = state.start_head
        return state


def _grok_auth_source(parent_env: Mapping[str, str] | None = None) -> Path:
    parent = dict(parent_env if parent_env is not None else os.environ)
    configured_auth = str(parent.get("GROK_AUTH_PATH") or "").strip()
    configured_home = str(parent.get("GROK_HOME") or "").strip()
    if configured_auth:
        source = Path(configured_auth).expanduser()
    elif configured_home:
        source = Path(configured_home).expanduser() / GROK_AUTH_FILE_NAME
    else:
        host_home = str(parent.get("HOME") or "").strip()
        if not host_home:
            raise ValidationIssue(
                "full_run_grok_auth_source_missing",
                "Shared OAuth requires host HOME, GROK_HOME, or GROK_AUTH_PATH",
            )
        source = Path(host_home).expanduser() / ".grok" / GROK_AUTH_FILE_NAME
    if not source.is_absolute() or source.name != GROK_AUTH_FILE_NAME:
        raise ValidationIssue(
            "full_run_grok_auth_source_invalid",
            "Host Grok auth path must be an absolute auth.json path",
        )
    return source


def _grok_auth_directory_identity(info: os.stat_result) -> dict[str, int]:
    """Return only stable, non-path metadata for one canonical directory."""
    return {
        "dev": int(info.st_dev),
        "ino": int(info.st_ino),
        "uid": int(info.st_uid),
        "mode": int(stat.S_IMODE(info.st_mode)),
    }


def _darwin_acl_api() -> tuple[Any, Any, Any, Any]:
    """Load the native descriptor-based macOS ACL API once."""
    global _DARWIN_ACL_API
    if _DARWIN_ACL_API is not None:
        return _DARWIN_ACL_API
    try:
        library = ctypes.CDLL(
            ctypes.util.find_library("System") or "/usr/lib/libSystem.B.dylib",
            use_errno=True,
        )
        acl_get_fd_np = library.acl_get_fd_np
        acl_get_fd_np.argtypes = [ctypes.c_int, ctypes.c_int]
        acl_get_fd_np.restype = ctypes.c_void_p
        acl_get_entry = library.acl_get_entry
        acl_get_entry.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        acl_get_entry.restype = ctypes.c_int
        acl_get_tag_type = library.acl_get_tag_type
        acl_get_tag_type.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int),
        ]
        acl_get_tag_type.restype = ctypes.c_int
        acl_free = library.acl_free
        acl_free.argtypes = [ctypes.c_void_p]
        acl_free.restype = ctypes.c_int
    except (AttributeError, OSError) as exc:
        raise ValidationIssue(
            "full_run_grok_auth_acl_inspection_failed",
            "Cannot inspect host Grok auth extended ACLs",
        ) from exc
    _DARWIN_ACL_API = (
        acl_get_fd_np,
        acl_get_entry,
        acl_get_tag_type,
        acl_free,
    )
    return _DARWIN_ACL_API


def _assert_no_darwin_extended_allow_acl(
    descriptor: int,
    *,
    unsafe_code: str = "full_run_grok_auth_acl_unsafe",
    inspection_code: str = "full_run_grok_auth_acl_inspection_failed",
    subject: str = "Host Grok auth path",
) -> None:
    """Reject any macOS extended allow entry on an already-bound object."""
    if sys.platform != "darwin":
        return
    try:
        acl_get_fd_np, acl_get_entry, acl_get_tag_type, acl_free = _darwin_acl_api()
    except ValidationIssue as exc:
        raise ValidationIssue(
            inspection_code,
            f"Cannot inspect {subject} extended ACLs",
        ) from exc
    ctypes.set_errno(0)
    acl = acl_get_fd_np(descriptor, _DARWIN_ACL_TYPE_EXTENDED)
    if not acl:
        # macOS reports ENOENT when an object has no extended ACL. Every other
        # result is an inspection failure, including unsupported filesystems.
        if ctypes.get_errno() == errno.ENOENT:
            return
        raise ValidationIssue(
            inspection_code,
            f"Cannot inspect {subject} extended ACLs",
        )

    issue: ValidationIssue | None = None
    saw_entry = False
    entry_id = _DARWIN_ACL_FIRST_ENTRY
    try:
        while True:
            entry = ctypes.c_void_p()
            ctypes.set_errno(0)
            result = acl_get_entry(acl, entry_id, ctypes.byref(entry))
            entry_errno = ctypes.get_errno()
            if result == -1:
                # acl_get_entry uses EINVAL as its documented end-of-list
                # result after at least one successful entry retrieval.
                if saw_entry and entry_errno == errno.EINVAL:
                    break
                issue = ValidationIssue(
                    inspection_code,
                    f"Cannot inspect {subject} extended ACLs",
                )
                break
            if result != 0 or not entry.value:
                issue = ValidationIssue(
                    inspection_code,
                    f"Cannot inspect {subject} extended ACLs",
                )
                break
            tag = ctypes.c_int()
            ctypes.set_errno(0)
            if acl_get_tag_type(entry, ctypes.byref(tag)) != 0:
                issue = ValidationIssue(
                    inspection_code,
                    f"Cannot inspect {subject} extended ACLs",
                )
                break
            if tag.value == _DARWIN_ACL_EXTENDED_ALLOW:
                issue = ValidationIssue(
                    unsafe_code,
                    f"{subject} must not grant access through an extended ACL",
                )
                break
            if tag.value != _DARWIN_ACL_EXTENDED_DENY:
                issue = ValidationIssue(
                    inspection_code,
                    f"Cannot inspect {subject} extended ACLs",
                )
                break
            saw_entry = True
            entry_id = _DARWIN_ACL_NEXT_ENTRY
    finally:
        ctypes.set_errno(0)
        if acl_free(acl) != 0:
            issue = ValidationIssue(
                inspection_code,
                f"Cannot inspect {subject} extended ACLs",
            )
    if issue is not None:
        raise issue


def _open_verified_owner_parent_chain_fds(
    canonical_parent: Path,
    *,
    final_owner_uids: frozenset[int],
    unsafe_code: str,
    unsafe_message: str,
    acl_unsafe_code: str,
    acl_inspection_code: str,
    acl_subject: str,
) -> tuple[list[int], list[dict[str, int]]]:
    """Bind a complete canonical owner-controlled directory chain."""
    if not canonical_parent.is_absolute():
        raise ValidationIssue(
            unsafe_code,
            unsafe_message,
        )
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    anchor = Path(canonical_parent.anchor)
    parts = canonical_parent.relative_to(anchor).parts
    directory_fds: list[int] = []
    identities: list[dict[str, int]] = []
    try:
        directory_fds.append(os.open(anchor, directory_flags))
        for index, component in enumerate((None, *parts)):
            if component is not None:
                directory_fds.append(
                    os.open(component, directory_flags, dir_fd=directory_fds[-1])
                )
            directory_fd = directory_fds[-1]
            info = os.fstat(directory_fd)
            _assert_no_darwin_extended_allow_acl(
                directory_fd,
                unsafe_code=acl_unsafe_code,
                inspection_code=acl_inspection_code,
                subject=acl_subject,
            )
            mode = stat.S_IMODE(info.st_mode)
            is_final = index == len(parts)
            safe_sticky_root = bool(
                info.st_uid == 0
                and mode & stat.S_ISVTX
                and mode & (stat.S_IWGRP | stat.S_IWOTH)
            )
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid not in {0, os.geteuid()}
                or (
                    mode & (stat.S_IWGRP | stat.S_IWOTH)
                    and not safe_sticky_root
                )
                or (is_final and info.st_uid not in final_owner_uids)
            ):
                raise ValidationIssue(
                    unsafe_code,
                    unsafe_message,
                )
            identities.append(_grok_auth_directory_identity(info))
        return directory_fds, identities
    except BaseException:
        for directory_fd in reversed(directory_fds):
            os.close(directory_fd)
        raise


def _open_verified_grok_auth_parent_chain(
    canonical_parent: Path,
) -> tuple[int, list[dict[str, int]]]:
    """Open every canonical auth ancestor with the full chain bound at once."""
    directory_fds, identities = _open_verified_owner_parent_chain_fds(
        canonical_parent,
        final_owner_uids=frozenset({os.geteuid()}),
        unsafe_code="full_run_grok_auth_parent_unsafe",
        unsafe_message=(
            "Host Grok auth path must traverse only bound "
            "owner/root-controlled directories"
        ),
        acl_unsafe_code="full_run_grok_auth_acl_unsafe",
        acl_inspection_code="full_run_grok_auth_acl_inspection_failed",
        acl_subject="host Grok auth path",
    )
    final_fd = directory_fds.pop()
    for directory_fd in reversed(directory_fds):
        os.close(directory_fd)
    return final_fd, identities


def _grok_executable_identity(
    candidate: Path,
    info: os.stat_result,
) -> dict[str, Any]:
    """Return an exact executable binding suitable for child-side recheck."""
    return {
        "path": str(candidate),
        "dev": int(info.st_dev),
        "ino": int(info.st_ino),
        "uid": int(info.st_uid),
        "mode": int(stat.S_IMODE(info.st_mode)),
        "nlink": int(info.st_nlink),
        "size": int(info.st_size),
        "mtime_ns": int(info.st_mtime_ns),
        "ctime_ns": int(info.st_ctime_ns),
        "security_profile": "exact_path",
    }


def _resolve_grok_executable(executable: str) -> tuple[Path, dict[str, Any]]:
    """Resolve one provider path and bind the exact non-symlink executable."""
    located = shutil.which(executable)
    if not located:
        raise ValidationIssue(
            "full_run_grok_executable_unavailable",
            "Configured Grok executable is unavailable",
        )
    try:
        candidate = Path(located).resolve(strict=True)
        info = os.stat(candidate, follow_symlinks=False)
    except OSError as exc:
        raise ValidationIssue(
            "full_run_grok_executable_unavailable",
            "Configured Grok executable is unavailable",
        ) from exc
    if (
        not candidate.is_absolute()
        or not stat.S_ISREG(info.st_mode)
        or not (stat.S_IMODE(info.st_mode) & 0o111)
        or info.st_size <= 0
    ):
        raise ValidationIssue(
            "full_run_grok_executable_unsafe",
            "Configured Grok executable is not one executable regular file",
        )
    return candidate, _grok_executable_identity(candidate, info)


def _native_executable_format(descriptor: int) -> str | None:
    """Identify only host-native executable formats from an already-open FD."""
    try:
        header = os.pread(descriptor, 4, 0)
    except AttributeError:
        offset = os.lseek(descriptor, 0, os.SEEK_CUR)
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            header = os.read(descriptor, 4)
        finally:
            os.lseek(descriptor, offset, os.SEEK_SET)
    if sys.platform == "darwin" and header in _MACH_O_MAGICS:
        return "mach-o"
    if sys.platform.startswith("linux") and header == b"\x7fELF":
        return "elf"
    return None


def _shared_oauth_grok_executable_identity(
    candidate: Path,
    info: os.stat_result,
    *,
    native_format: str,
    parent_chain: Sequence[Mapping[str, int]],
) -> dict[str, Any]:
    identity = _grok_executable_identity(candidate, info)
    identity["security_profile"] = "shared_oauth_native"
    identity["native_format"] = native_format
    identity["parent_chain"] = [dict(item) for item in parent_chain]
    return identity


def _resolve_shared_oauth_grok_executable(
    executable: str,
) -> tuple[Path, dict[str, Any]]:
    """Bind a safe native Grok executable and its complete canonical chain."""
    located = shutil.which(executable)
    if not located:
        raise ValidationIssue(
            "full_run_grok_executable_unavailable",
            "Configured Grok executable is unavailable",
        )
    try:
        candidate = Path(located).resolve(strict=True)
    except OSError as exc:
        raise ValidationIssue(
            "full_run_grok_executable_unavailable",
            "Configured Grok executable is unavailable",
        ) from exc
    if not candidate.is_absolute():
        raise ValidationIssue(
            "full_run_grok_executable_unsafe",
            "Shared OAuth requires an absolute native Grok executable",
        )

    directory_fds: list[int] = []
    executable_fd = -1
    try:
        directory_fds, parent_chain = _open_verified_owner_parent_chain_fds(
            candidate.parent,
            final_owner_uids=frozenset({0, os.geteuid()}),
            unsafe_code="full_run_grok_executable_parent_unsafe",
            unsafe_message=(
                "Shared OAuth Grok must traverse only bound "
                "owner/root-controlled executable directories"
            ),
            acl_unsafe_code="full_run_grok_executable_acl_unsafe",
            acl_inspection_code="full_run_grok_executable_acl_inspection_failed",
            acl_subject="shared OAuth Grok executable path",
        )
        executable_fd = os.open(
            candidate.name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=directory_fds[-1],
        )
        before = os.fstat(executable_fd)
        mode = stat.S_IMODE(before.st_mode)
        _assert_no_darwin_extended_allow_acl(
            executable_fd,
            unsafe_code="full_run_grok_executable_acl_unsafe",
            inspection_code="full_run_grok_executable_acl_inspection_failed",
            subject="shared OAuth Grok executable",
        )
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid not in {0, os.geteuid()}
            or before.st_nlink != 1
            or mode & (stat.S_IWGRP | stat.S_IWOTH)
            or not (mode & 0o111)
            or before.st_size <= 0
            or before.st_size > MAX_GROK_EXECUTABLE_PROBE_BYTES
        ):
            raise ValidationIssue(
                "full_run_grok_executable_unsafe",
                "Shared OAuth requires one owner-controlled native Grok executable",
            )
        native_format = _native_executable_format(executable_fd)
        if native_format is None:
            raise ValidationIssue(
                "full_run_grok_executable_not_native",
                "Shared OAuth requires a native Grok binary, not a script wrapper",
            )
        after = os.fstat(executable_fd)
        published = os.stat(
            candidate.name,
            dir_fd=directory_fds[-1],
            follow_symlinks=False,
        )
        observed = _shared_oauth_grok_executable_identity(
            candidate,
            after,
            native_format=native_format,
            parent_chain=parent_chain,
        )
        if (
            _shared_oauth_grok_executable_identity(
                candidate,
                before,
                native_format=native_format,
                parent_chain=parent_chain,
            )
            != observed
            or (published.st_dev, published.st_ino)
            != (after.st_dev, after.st_ino)
        ):
            raise ValidationIssue(
                "full_run_grok_executable_changed",
                "Shared OAuth Grok executable changed during secure binding",
            )
        return candidate, observed
    except ValidationIssue:
        raise
    except OSError as exc:
        raise ValidationIssue(
            "full_run_grok_executable_unsafe",
            "Shared OAuth Grok executable could not be bound safely",
        ) from exc
    finally:
        if executable_fd >= 0:
            os.close(executable_fd)
        for directory_fd in reversed(directory_fds):
            os.close(directory_fd)


def _assert_stable_grok_executable(
    executable: str,
    expected_identity: Mapping[str, Any],
) -> Path:
    resolver = (
        _resolve_shared_oauth_grok_executable
        if expected_identity.get("security_profile") == "shared_oauth_native"
        else _resolve_grok_executable
    )
    resolved, observed = resolver(executable)
    if observed != dict(expected_identity):
        raise ValidationIssue(
            "full_run_grok_executable_changed",
            "Configured Grok executable changed during launch preflight",
        )
    return resolved


def _executable_advertises_grok_auth_path(
    executable: str,
    *,
    expected_identity: Mapping[str, Any] | None = None,
) -> bool:
    """Check the exact bound artifact for the native auth-path marker."""
    descriptor = -1
    try:
        resolver = (
            _resolve_shared_oauth_grok_executable
            if expected_identity is not None
            and expected_identity.get("security_profile") == "shared_oauth_native"
            else _resolve_grok_executable
        )
        candidate, identity = resolver(executable)
        if expected_identity is not None and identity != dict(expected_identity):
            return False
        descriptor = os.open(
            candidate,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        before_identity = _grok_executable_identity(candidate, before)
        if (
            any(
                identity.get(key) != value
                for key, value in before_identity.items()
                if key != "security_profile"
            )
            or before.st_size > MAX_GROK_EXECUTABLE_PROBE_BYTES
        ):
            return False
        with mmap.mmap(descriptor, 0, access=mmap.ACCESS_READ) as image:
            advertised = image.find(b"GROK_AUTH_PATH") >= 0
        after = os.fstat(descriptor)
        _, published_identity = resolver(str(candidate))
        return bool(
            advertised
            and _grok_executable_identity(candidate, after) == before_identity
            and published_identity == identity
        )
    except (OSError, ValueError, ValidationIssue):
        return False
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _isolated_grok_capability_probe_env(root: Path) -> dict[str, str]:
    """Build a credential-free environment without inherited auth controls."""
    home = root / "home"
    temp_root = root / "tmp"
    config = home / ".config"
    cache = home / ".cache"
    data = home / ".local" / "share"
    for directory in (home, temp_root, config, cache, data):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return {
        # PATH is the sole inherited operational value: script launchers may
        # require their declared interpreter, while every auth/config control
        # is rebuilt under the private capability-probe root.
        "PATH": os.environ.get("PATH") or os.defpath,
        "HOME": str(home),
        "TMPDIR": str(temp_root),
        "TMP": str(temp_root),
        "TEMP": str(temp_root),
        "XDG_CONFIG_HOME": str(config),
        "XDG_CACHE_HOME": str(cache),
        "XDG_DATA_HOME": str(data),
        "LANG": "C",
        "LC_ALL": "C",
    }


def _assert_grok_auth_path_capability(
    executable: str,
    *,
    expected_identity: Mapping[str, Any] | None = None,
) -> tuple[int, int, int]:
    """Probe only one exact Grok artifact in an isolated credential-free env."""
    resolved, identity = _resolve_shared_oauth_grok_executable(executable)
    if expected_identity is not None and identity != dict(expected_identity):
        raise ValidationIssue(
            "full_run_grok_executable_changed",
            "Configured Grok executable changed during launch preflight",
        )
    version: tuple[int, int, int] | None = None
    with tempfile.TemporaryDirectory(prefix="elves-grok-capability-") as tmp:
        probe_root = Path(tmp)
        probe_env = _isolated_grok_capability_probe_env(probe_root)
        for argv in (
            [str(resolved), "version", "--json"],
            [str(resolved), "--version"],
        ):
            try:
                result = subprocess.run(
                    argv,
                    cwd=str(probe_root / "home"),
                    env=probe_env,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5.0,
                    close_fds=True,
                )
            except (OSError, subprocess.TimeoutExpired):
                _assert_stable_grok_executable(str(resolved), identity)
                continue
            _assert_stable_grok_executable(str(resolved), identity)
            if result.returncode == 0:
                output = (result.stdout or "") + "\n" + (result.stderr or "")
                match = _GROK_VERSION_RE.search(output)
                if match:
                    version = tuple(int(part) for part in match.groups())
                    break
    if (
        version is not None
        and version >= GROK_AUTH_PATH_MIN_VERSION
        and _executable_advertises_grok_auth_path(
            str(resolved), expected_identity=identity
        )
    ):
        _assert_stable_grok_executable(str(resolved), identity)
        return version
    raise ValidationIssue(
        "full_run_grok_auth_path_unsupported",
        "Installed Grok does not provide the required shared OAuth path capability",
        hint="Upgrade Grok Build to 0.2.93 or newer, or grant XAI_API_KEY instead",
    )


def _read_grok_auth_path(source: Path) -> tuple[bytes, dict[str, Any]]:
    """Read one canonical auth file while binding its safe parent directory."""
    if not source.is_absolute() or source.name != GROK_AUTH_FILE_NAME:
        raise ValidationIssue(
            "full_run_grok_auth_source_invalid",
            "Host Grok auth path must be an absolute auth.json path",
        )
    try:
        canonical_parent = source.parent.resolve(strict=True)
    except OSError as exc:
        raise ValidationIssue(
            "full_run_grok_auth_source_missing",
            "Shared Grok OAuth parent directory is unavailable",
        ) from exc
    canonical = canonical_parent / GROK_AUTH_FILE_NAME
    file_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    parent_fd = -1
    source_fd = -1
    parent_chain_before: list[dict[str, int]] = []

    def close_opened_descriptors() -> None:
        if source_fd >= 0:
            os.close(source_fd)
        if parent_fd >= 0:
            os.close(parent_fd)

    try:
        parent_fd, parent_chain_before = _open_verified_grok_auth_parent_chain(
            canonical_parent
        )
        parent_before = os.fstat(parent_fd)
        source_fd = os.open(GROK_AUTH_FILE_NAME, file_flags, dir_fd=parent_fd)
    except FileNotFoundError as exc:
        close_opened_descriptors()
        raise ValidationIssue(
            "full_run_grok_auth_source_missing",
            "Requested shared Grok OAuth auth.json is unavailable",
        ) from exc
    except ValidationIssue:
        close_opened_descriptors()
        raise
    except OSError as exc:
        close_opened_descriptors()
        if exc.errno == getattr(errno, "ELOOP", None):
            raise ValidationIssue(
                "full_run_grok_auth_source_unsafe",
                "Host Grok auth.json must not be a symlink",
            ) from exc
        raise ValidationIssue(
            "full_run_grok_auth_source_unavailable",
            f"Host Grok auth metadata is unavailable: {type(exc).__name__}",
        ) from exc
    try:
        before = os.fstat(source_fd)
        _assert_no_darwin_extended_allow_acl(source_fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size <= 0
            or before.st_size > MAX_GROK_AUTH_BYTES
        ):
            raise ValidationIssue(
                "full_run_grok_auth_source_unsafe",
                "Host Grok auth.json must be one owner-only regular file within the size limit",
            )
        with os.fdopen(source_fd, "rb", closefd=False) as handle:
            raw = handle.read(MAX_GROK_AUTH_BYTES + 1)
        after = os.fstat(source_fd)
        published = os.stat(
            GROK_AUTH_FILE_NAME,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
        parent_after = os.fstat(parent_fd)
        parent_chain_after_fd, parent_chain_after = (
            _open_verified_grok_auth_parent_chain(canonical_parent)
        )
        os.close(parent_chain_after_fd)
        if len(raw) > MAX_GROK_AUTH_BYTES or (
            before.st_dev,
            before.st_ino,
            before.st_uid,
            before.st_nlink,
            stat.S_IMODE(before.st_mode),
            before.st_size,
            getattr(before, "st_mtime_ns", None),
            getattr(before, "st_ctime_ns", None),
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_uid,
            after.st_nlink,
            stat.S_IMODE(after.st_mode),
            after.st_size,
            getattr(after, "st_mtime_ns", None),
            getattr(after, "st_ctime_ns", None),
        ) or len(raw) != after.st_size or (
            published.st_dev,
            published.st_ino,
        ) != (
            after.st_dev,
            after.st_ino,
        ) or (
            parent_before.st_dev,
            parent_before.st_ino,
            parent_before.st_uid,
            stat.S_IMODE(parent_before.st_mode),
        ) != (
            parent_after.st_dev,
            parent_after.st_ino,
            parent_after.st_uid,
            stat.S_IMODE(parent_after.st_mode),
        ) or parent_chain_before != parent_chain_after:
            raise ValidationIssue(
                "full_run_grok_auth_source_changed",
                "Host Grok auth.json changed while it was being read",
            )
        payload = json.loads(raw.decode("utf-8"))
    except ValidationIssue:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationIssue(
            "full_run_grok_auth_source_invalid",
            "Host Grok auth.json is not bounded valid JSON",
        ) from exc
    except OSError as exc:
        raise ValidationIssue(
            "full_run_grok_auth_source_unavailable",
            f"Host Grok auth data is unavailable: {type(exc).__name__}",
        ) from exc
    finally:
        if source_fd >= 0:
            os.close(source_fd)
        if parent_fd >= 0:
            os.close(parent_fd)
    if not isinstance(payload, Mapping) or not any(
        isinstance(record, Mapping)
        and any(
            isinstance(record.get(key), str) and bool(str(record.get(key)).strip())
            for key in ("key", "access_token", "refresh_token")
        )
        for record in payload.values()
    ):
        raise ValidationIssue(
            "full_run_grok_auth_source_invalid",
            "Host Grok auth.json has no recognized authenticated record",
        )
    identity = {
        "path": str(canonical),
        "parent_dev": int(parent_before.st_dev),
        "parent_ino": int(parent_before.st_ino),
        "parent_uid": int(parent_before.st_uid),
        "parent_mode": int(stat.S_IMODE(parent_before.st_mode)),
        "parent_chain": parent_chain_before,
    }
    return raw, identity


def _read_host_grok_auth(
    parent_env: Mapping[str, str] | None = None,
    *,
    source_path: str | Path | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """Read a rotating shared auth file, retrying only observed write races."""
    source = Path(source_path) if source_path is not None else _grok_auth_source(parent_env)
    last_change: ValidationIssue | None = None
    for _attempt in range(3):
        try:
            return _read_grok_auth_path(source)
        except ValidationIssue as exc:
            if exc.code != "full_run_grok_auth_source_changed":
                raise
            last_change = exc
    assert last_change is not None
    raise last_change


def _revalidate_shared_grok_auth(state: FullRunState) -> bytes:
    expected = state.grok_auth_path_identity
    if state.grok_auth_strategy != "oauth_shared_file" or not isinstance(expected, Mapping):
        raise ValidationIssue(
            "full_run_grok_auth_identity_missing",
            "Shared Grok OAuth path identity is missing from private run state",
        )
    path = expected.get("path")
    if not isinstance(path, str) or not path:
        raise ValidationIssue(
            "full_run_grok_auth_identity_missing",
            "Shared Grok OAuth path identity is incomplete",
        )
    raw, observed = _read_host_grok_auth(source_path=path)
    if dict(expected) != observed:
        raise ValidationIssue(
            "full_run_grok_auth_identity_changed",
            "Shared Grok OAuth canonical path identity changed",
        )
    return raw


def _remove_failed_launch_artifacts(repo_root: Path, root: Path) -> None:
    """Remove only host launch metadata after a supervisor never received its start secret."""
    guarded_root, directory_fd = _open_repo_directory(
        Path(repo_root), root, create=False
    )
    try:
        _assert_directory_fd_identity(Path(repo_root), guarded_root, directory_fd)
        for leaf in (
            "supervisor.fingerprint.json",
            "worker.fingerprint.json",
            "worker.pid",
            "worker.pgid",
        ):
            try:
                os.unlink(leaf, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
    except OSError as exc:
        raise ValidationIssue(
            "full_run_failed_launch_cleanup_failed",
            f"Cannot remove failed-launch metadata: {type(exc).__name__}",
        ) from exc
    finally:
        os.close(directory_fd)


def _cleanup_refused_launch(
    repo_root: Path,
    root: Path,
    state: FullRunState,
    proc: subprocess.Popen[bytes] | None,
    *,
    cause: BaseException,
) -> None:
    """Roll back a launch only while provider start is attested impossible."""
    if proc is not None:
        try:
            proc.kill()
        except OSError:
            pass
        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except OSError:
                pass
            finally:
                proc.stdin = None
        try:
            proc.wait(timeout=1.0)
        except (OSError, subprocess.TimeoutExpired):
            pass
    cleanup_errors: list[str] = []
    try:
        _remove_failed_launch_artifacts(repo_root, root)
    except (StorageError, ValidationIssue) as exc:
        cleanup_errors.append(type(exc).__name__)
    state.pid = None
    state.pgid = None
    state.fingerprint = None
    state.exit_sidecar_pid = None
    state.status = "pending"
    state.launched_at = None
    state.heartbeat_at = None
    state.next_action = "launch"
    try:
        save_state(repo_root, state)
    except (OSError, StorageError, ValidationIssue):
        pass
    if cleanup_errors:
        raise ValidationIssue(
            "full_run_failed_launch_cleanup_failed",
            "Refused launch could not remove all private transient artifacts",
        ) from cause


def _oauth_secret_values(raw: bytes) -> set[str]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return set()
    values: set[str] = set()

    def walk(value: Any, key: str | None = None) -> None:
        if isinstance(value, Mapping):
            for child_key, child in value.items():
                walk(child, str(child_key))
        elif isinstance(value, list):
            for child in value:
                walk(child, key)
        elif isinstance(value, str) and key and (
            key == "key" or _FULL_RUN_SECRET_KEY_RE.search(key)
        ):
            if value:
                values.add(value)

    walk(payload)
    return values


def _state_secret_values(state: FullRunState) -> frozenset[str]:
    """Resolve one current launch-evidence snapshot for defensive redaction."""
    _verified, values = _launch_evidence_context(state)
    return values


def _supervision_secret(state: FullRunState) -> str:
    """Return the private control-channel secret after validating its shape.

    The trusted branch-progress worker shares the host user's filesystem, so this
    capability rejects malformed/accidental runtime artifacts; it is not an OS
    security boundary against a malicious worker that violates the lane contract.
    """
    secret = str(state.supervision_token or "")
    if not re.fullmatch(r"[0-9a-f]{48}", secret):
        raise ValidationIssue(
            "full_run_supervision_unavailable",
            "Full-run host stop secret is missing or malformed",
        )
    return secret


def _descendant_supervision_marker(state: FullRunState) -> str:
    """Derive the public process marker without exposing the host stop secret."""
    secret = _supervision_secret(state)
    message = f"descendant-marker\0{state.session_id}\0{state.attempt}".encode("utf-8")
    return hmac.new(secret.encode("ascii"), message, hashlib.sha256).hexdigest()


def _credential_grant_digest(state: FullRunState, name: str, value: str) -> str:
    """Bind a launch credential value to this private supervisor attempt."""
    # The private supervision capability is the HMAC authority.  Falling back
    # to a public identifier would let state corruption silently change the
    # scanner key and turn exact-leak detection into a false negative.
    key = _supervision_secret(state).encode("ascii")
    material = f"{name}\0{value}".encode("utf-8")
    return hmac.new(key, material, hashlib.sha256).hexdigest()


def _credential_grant_metadata_mac(state: FullRunState) -> str:
    """Authenticate the exact persisted grant scanner metadata and key domain."""
    payload = {
        "session_id": state.session_id,
        "attempt": state.attempt,
        "granted_names": state.credential_granted_names,
        "digests": state.credential_grant_digests,
        "lengths": state.credential_grant_lengths,
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hmac.new(
        _supervision_secret(state).encode("ascii"),
        b"credential-grant-metadata-v1\0" + canonical,
        hashlib.sha256,
    ).hexdigest()


def _persisted_grant_metadata_valid(state: FullRunState) -> bool:
    granted = state.credential_granted_names
    digests = state.credential_grant_digests
    lengths = state.credential_grant_lengths
    if (
        not isinstance(digests, dict)
        or not isinstance(lengths, dict)
    ):
        return False
    granted_valid, normalized = _normalize_persisted_credential_grant_names(granted)
    if not granted_valid:
        return False
    if set(normalized) != set(digests) or set(normalized) != set(lengths):
        return False
    if normalized and not re.fullmatch(
        r"[0-9a-f]{48}", str(state.supervision_token or "")
    ):
        return False
    if not all(
        isinstance(digests.get(name), str)
        and bool(_SHA256_RE.fullmatch(str(digests[name])))
        and not isinstance(lengths.get(name), bool)
        and isinstance(lengths.get(name), int)
        and int(lengths[name]) >= 0
        for name in normalized
    ):
        return False
    if not normalized:
        return state.credential_grant_metadata_mac in {None, ""}
    supplied_mac = state.credential_grant_metadata_mac
    if not isinstance(supplied_mac, str) or not _SHA256_RE.fullmatch(supplied_mac):
        return False
    try:
        expected_mac = _credential_grant_metadata_mac(state)
    except ValidationIssue:
        return False
    return hmac.compare_digest(supplied_mac, expected_mac)


def _contains_persisted_credential_grant(
    value: Any,
    state: FullRunState,
) -> bool:
    """Detect exact grant bytes without retaining the credential itself.

    Event/report artifacts are bounded before this scan. For every persisted
    grant length, candidate substrings are compared through the attempt-keyed
    HMAC used at launch. Mapping keys are evidence surfaces too.
    """
    if not _persisted_grant_metadata_valid(state):
        return False

    def walk(item: Any) -> bool:
        if isinstance(item, str):
            return _text_contains_persisted_credential_grant(item, state)
        if isinstance(item, Mapping):
            return any(
                _text_contains_persisted_credential_grant(str(key), state)
                or walk(child)
                for key, child in item.items()
            )
        if isinstance(item, (list, tuple)):
            return any(walk(child) for child in item)
        return False

    return walk(value)


def _text_contains_persisted_credential_grant(
    text: str,
    state: FullRunState,
) -> bool:
    if not _persisted_grant_metadata_valid(state):
        return False
    for name in state.credential_granted_names:
        length = state.credential_grant_lengths[name]
        if length <= 0 or len(text) < length:
            continue
        expected = state.credential_grant_digests[name]
        for start in range(0, len(text) - length + 1):
            candidate = text[start : start + length]
            if hmac.compare_digest(
                _credential_grant_digest(state, name, candidate),
                expected,
            ):
                return True
    return False


def _redact_persisted_credential_grants(value: Any, state: FullRunState) -> Any:
    """Redact a whole string/key when an absent launch grant is detected."""
    if isinstance(value, str):
        return (
            "[REDACTED:credential_grant]"
            if _text_contains_persisted_credential_grant(value, state)
            else value
        )
    if isinstance(value, Mapping):
        rows: list[tuple[str, Any]] = []
        desired_keys: list[str] = []
        for key, child in value.items():
            key_text = str(key)
            cleaned_key = (
                "[REDACTED:credential_grant_key]"
                if _text_contains_persisted_credential_grant(key_text, state)
                else key_text
            )
            rows.append((cleaned_key, child))
            desired_keys.append(cleaned_key)
        cleaned: dict[str, Any] = {}
        allocated_keys = _collision_safe_mapping_keys(desired_keys)
        for cleaned_key, (_desired, child) in zip(allocated_keys, rows):
            cleaned[cleaned_key] = _redact_persisted_credential_grants(child, state)
        return cleaned
    if isinstance(value, list):
        return [_redact_persisted_credential_grants(child, state) for child in value]
    if isinstance(value, tuple):
        return tuple(
            _redact_persisted_credential_grants(child, state) for child in value
        )
    return value


def _stop_request_authority(state: FullRunState) -> str:
    """Authenticate a control request without placing the secret in provider env/argv."""
    token = _supervision_secret(state)
    message = f"stop\0{state.session_id}\0{state.attempt}".encode("utf-8")
    return hmac.new(token.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _write_supervisor_stop_request(
    repo_root: Path,
    root: Path,
    state: FullRunState,
) -> Path:
    """Atomically publish a capability-authenticated stop over an untrusted leaf.

    The worker can create artifacts in its runtime directory, including a FIFO
    or symlink at the request name. Publishing through a directory descriptor
    replaces that leaf without opening or following it, so a hostile FIFO
    cannot block either side of the stop path. This is artifact hardening inside
    a trusted-worker route, not worker-resistant privilege separation.
    """
    repo_root = Path(repo_root)
    guarded_root = guard_repo_path(repo_root, root)
    request_path = guarded_root / STOP_REQUEST_NAME
    payload = (
        json.dumps(
            {
                "session_id": state.session_id,
                "attempt": state.attempt,
                "authority": _stop_request_authority(state),
                "requested_at": _utc_now(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    temporary_name = f".{STOP_REQUEST_NAME}.{secrets.token_hex(12)}.host"
    runtime_fd = -1
    temporary_fd = -1
    try:
        guarded_root, runtime_fd = _open_repo_directory(
            repo_root,
            guarded_root,
            create=False,
        )
        _assert_directory_fd_identity(repo_root, guarded_root, runtime_fd)
        temporary_fd = os.open(
            temporary_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=runtime_fd,
        )
        os.fchmod(temporary_fd, 0o600)
        offset = 0
        while offset < len(payload):
            written = os.write(temporary_fd, payload[offset:])
            if written <= 0:
                raise OSError(errno.EIO, "short stop-request write")
            offset += written
        os.fsync(temporary_fd)
        temporary_info = os.fstat(temporary_fd)
        named_temporary = os.stat(
            temporary_name,
            dir_fd=runtime_fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(temporary_info.st_mode)
            or temporary_info.st_nlink != 1
            or (temporary_info.st_dev, temporary_info.st_ino)
            != (named_temporary.st_dev, named_temporary.st_ino)
        ):
            raise OSError(errno.ESTALE, "stop-request temporary identity changed")
        _assert_directory_fd_identity(repo_root, guarded_root, runtime_fd)
        os.replace(
            temporary_name,
            STOP_REQUEST_NAME,
            src_dir_fd=runtime_fd,
            dst_dir_fd=runtime_fd,
        )
        published = os.stat(
            STOP_REQUEST_NAME,
            dir_fd=runtime_fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(published.st_mode)
            or published.st_nlink != 1
            or (published.st_dev, published.st_ino)
            != (temporary_info.st_dev, temporary_info.st_ino)
        ):
            raise OSError(errno.ESTALE, "published stop-request identity changed")
    except OSError as exc:
        raise StorageError(
            "stop_request_write_failed",
            "Cannot publish authenticated supervisor stop request: "
            f"{type(exc).__name__}: {exc}",
        ) from exc
    finally:
        if temporary_fd >= 0:
            os.close(temporary_fd)
        if runtime_fd >= 0:
            try:
                os.unlink(temporary_name, dir_fd=runtime_fd)
            except FileNotFoundError:
                pass
            finally:
                os.close(runtime_fd)
    return request_path


def _launch_evidence_context(
    state: FullRunState,
) -> tuple[bool, frozenset[str]]:
    """Atomically pair launch verification with its exact redaction values.

    Shared OAuth rotates its leaf in place. Public evidence callers must not
    validate one token generation and redact with another, so this function
    reads that authority exactly once. The canonical path is also treated as an
    exact redaction value even though it is metadata rather than a credential.
    """
    values: set[str] = set()
    if state.supervision_token:
        values.add(str(state.supervision_token))
    identity = state.grok_auth_path_identity
    if isinstance(identity, Mapping):
        path = identity.get("path")
        if isinstance(path, str) and path:
            values.add(path)
    if state.grok_auth_strategy not in {None, "xai_api_key", "oauth_shared_file"}:
        return False, frozenset(values)
    if (
        state.github_push_auth_strategy is not None
        and state.github_push_auth_strategy not in _GITHUB_PUSH_AUTH_STRATEGIES
    ):
        return False, frozenset(values)
    requested_valid, requested = _normalize_persisted_credential_grant_names(
        state.credential_grant_names
    )
    granted_valid, normalized_granted = (
        _normalize_persisted_credential_grant_names(
            state.credential_granted_names
        )
    )
    grants_valid = requested_valid and granted_valid
    for name in set(requested) | set(normalized_granted):
        value = os.environ.get(name)
        if value:
            values.add(value)
    persisted_grants_valid = _persisted_grant_metadata_valid(state)
    if not persisted_grants_valid:
        # Current environment values were collected above so any driver-visible
        # diagnostic can still redact them.  Do not attempt a keyed comparison
        # after the private HMAC authority or its authenticated metadata failed
        # validation: `_credential_grant_digest` deliberately rejects that
        # downgrade, and evidence must become unavailable rather than crashing
        # the parked monitor.
        return False, frozenset(values)
    for name in normalized_granted:
        value = os.environ.get(name)
        expected = state.credential_grant_digests.get(name)
        expected_length = state.credential_grant_lengths.get(name)
        if value is None:
            # Parked monitoring is a fresh host process and need not retain or
            # reload launch credentials. The private keyed digest + length is
            # sufficient to detect an exact leak in bounded worker evidence.
            continue
        if value:
            values.add(value)
        if (
            not expected
            or isinstance(expected_length, bool)
            or not isinstance(expected_length, int)
            or len(value) != expected_length
        ):
            grants_valid = False
            continue
        if not hmac.compare_digest(
            _credential_grant_digest(state, name, value), expected
        ):
            grants_valid = False
    if state.grok_auth_strategy == "oauth_shared_file":
        try:
            raw = _revalidate_shared_grok_auth(state)
        except ValidationIssue:
            grants_valid = False
        else:
            values.update(_oauth_secret_values(raw))
    if state.devin_auth_strategy == "projected_files":
        identity = state.devin_auth_identity
        if not isinstance(identity, Mapping):
            grants_valid = False
        else:
            for key in ("config_path", "credentials_path"):
                path = identity.get(key)
                if isinstance(path, str) and path:
                    values.add(path)
            try:
                _read_and_validate_devin_auth(state)
            except ValidationIssue:
                grants_valid = False
    return grants_valid, frozenset(values)


def _launch_grants_verified(state: FullRunState) -> bool:
    """True only when one launch-evidence snapshot verifies every grant."""
    verified, _values = _launch_evidence_context(state)
    return verified


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
    # Isolation controls are assigned, never inherited or setdefault-preserved.
    worker_home = root / "worker-home"
    worker_tmp = root / "worker-tmp"
    env["HOME"] = str(worker_home)
    env["TMPDIR"] = str(worker_tmp)
    env["TMP"] = str(worker_tmp)
    env["TEMP"] = str(worker_tmp)
    env["XDG_RUNTIME_DIR"] = str(worker_tmp / "runtime")
    env["XDG_CONFIG_HOME"] = str(worker_home / ".config")
    env["XDG_CACHE_HOME"] = str(worker_home / ".cache")
    env["XDG_DATA_HOME"] = str(worker_home / ".local" / "share")
    env["GROK_HOME"] = str(root / GROK_HOME_REL)
    env.setdefault("PATH", parent.get("PATH", "/usr/bin:/bin"))
    env.setdefault("PYTHONUNBUFFERED", "1")
    grants = _normalize_credential_grant_names(
        state.credential_grant_names
        if credential_grant_names is None
        else credential_grant_names
    )
    for name in grants:
        if name in parent and parent[name]:
            env[name] = str(parent[name])
    # Non-secret full-run contract values for real adapters and fixtures.
    # Packet requirement: adapters must use these paths for events/report/progress.
    env["ELVES_FULL_RUN_SESSION"] = state.session_id
    env["ELVES_FULL_RUN_RUN_ID"] = _expected_run_id(state.session_id)
    env["ELVES_FULL_RUN_EVENTS"] = str(root / "events.jsonl")
    # Machine-readable worker packet supplement. This keeps the exact event
    # grammar next to the append-only path without asking a worker to infer a
    # safety wake from prose.
    env["ELVES_FULL_RUN_EVENT_CONTRACT"] = json.dumps(
        WORKER_EVENT_CONTRACT, sort_keys=True, separators=(",", ":")
    )
    env["ELVES_FULL_RUN_REPORT"] = str(root / "report.json")
    env["ELVES_FULL_RUN_TRANSCRIPT"] = str(root / "transcript.log")
    env["ELVES_FULL_RUN_BRANCH"] = state.branch
    env["ELVES_FULL_RUN_START_HEAD"] = state.start_head
    env["ELVES_FULL_RUN_WORKTREE"] = state.worktree
    env["ELVES_FULL_RUN_ATTEMPT"] = str(state.attempt)
    env["ELVES_DRIVER_MONITOR_MODE"] = "parked_monitor"
    if state.supervision_token:
        env["ELVES_FULL_RUN_SUPERVISION_MARKER"] = _descendant_supervision_marker(state)
    return env


def _bind_state_grok_executable(
    state: FullRunState,
    *,
    require_auth_path: bool,
) -> None:
    """Bind one exact provider artifact, preserving identity across resumes."""
    resolver = (
        _resolve_shared_oauth_grok_executable
        if require_auth_path
        else _resolve_grok_executable
    )
    resolved, executable_identity = resolver(state.executable)
    if (
        state.grok_executable_identity is not None
        and state.grok_executable_identity != executable_identity
    ):
        raise ValidationIssue(
            "full_run_grok_executable_changed",
            "Configured Grok executable changed since the prior launch",
        )
    if require_auth_path:
        _assert_grok_auth_path_capability(
            str(resolved), expected_identity=executable_identity
        )
    state.executable = str(resolved)
    state.grok_executable_identity = executable_identity


def _configure_grok_auth(
    repo_root: Path,
    root: Path,
    state: FullRunState,
    launch_env: dict[str, str],
    *,
    grant_grok_auth: bool,
) -> None:
    """Select one explicit noninteractive Grok auth strategy before spawning."""
    del repo_root, root
    api_key_granted = bool(launch_env.get("XAI_API_KEY"))
    oauth_requested = bool(
        grant_grok_auth or state.grok_auth_strategy == "oauth_shared_file"
    )
    if api_key_granted and oauth_requested:
        raise ValidationIssue(
            "full_run_grok_auth_ambiguous",
            "Choose either explicit XAI_API_KEY grant or --grant-grok-auth, not both",
        )
    if api_key_granted:
        if state.grok_auth_strategy not in {None, "xai_api_key"}:
            raise ValidationIssue(
                "full_run_grok_auth_strategy_changed",
                "Resume must preserve the originally selected Grok auth strategy",
            )
        _bind_state_grok_executable(state, require_auth_path=False)
        state.grok_auth_strategy = "xai_api_key"
        state.grok_auth_path_identity = None
        launch_env.pop("GROK_AUTH_PATH", None)
        return
    if not oauth_requested:
        raise ValidationIssue(
            "full_run_grok_auth_required",
            "Headless Grok requires explicit --grant-env XAI_API_KEY or --grant-grok-auth",
            hint="The OAuth option shares one validated host auth.json through Grok's native GROK_AUTH_PATH in trusted Lane A",
        )
    if state.grok_auth_strategy not in {None, "oauth_shared_file"}:
        raise ValidationIssue(
            "full_run_grok_auth_strategy_changed",
            "Resume must preserve the originally selected Grok auth strategy",
        )
    _bind_state_grok_executable(state, require_auth_path=True)
    if state.grok_auth_strategy == "oauth_shared_file":
        _revalidate_shared_grok_auth(state)
        assert state.grok_auth_path_identity is not None
        identity = state.grok_auth_path_identity
    else:
        _raw, identity = _read_host_grok_auth()
    state.grok_auth_strategy = "oauth_shared_file"
    state.grok_auth_path_identity = dict(identity)
    launch_env["GROK_AUTH_PATH"] = str(identity["path"])


def _devin_auth_source_files(
    parent_env: Mapping[str, str] | None = None,
) -> dict[str, Path]:
    """Resolve canonical host Devin CLI config and credentials paths.

    Respects XDG_CONFIG_HOME and XDG_DATA_HOME, then falls back to HOME.
    """
    parent = dict(parent_env if parent_env is not None else os.environ)
    config_home = str(parent.get("XDG_CONFIG_HOME") or "").strip()
    data_home = str(parent.get("XDG_DATA_HOME") or "").strip()
    host_home = str(parent.get("HOME") or "").strip()
    if config_home:
        config_dir = Path(config_home).expanduser().resolve(strict=False) / "devin"
    elif host_home:
        config_dir = Path(host_home).expanduser() / ".config" / "devin"
    else:
        raise ValidationIssue(
            "full_run_devin_auth_source_missing",
            "Devin auth requires HOME or XDG_CONFIG_HOME to locate config.json",
        )
    if data_home:
        data_dir = Path(data_home).expanduser().resolve(strict=False) / "devin"
    elif host_home:
        data_dir = Path(host_home).expanduser() / ".local" / "share" / "devin"
    else:
        raise ValidationIssue(
            "full_run_devin_auth_source_missing",
            "Devin auth requires HOME or XDG_DATA_HOME to locate credentials.toml",
        )
    return {
        "config": config_dir / DEVIN_CONFIG_FILE_NAME,
        "credentials": data_dir / DEVIN_CREDENTIALS_FILE_NAME,
    }


def _read_host_devin_file(
    source: Path,
    max_bytes: int,
    expected_name: str,
) -> tuple[bytes, dict[str, Any]]:
    """Read one Devin auth file with no symlink, owner-only, and bounded checks.

    Returns the raw bytes and an identity dict suitable for safe revalidation.
    Raw bytes are never stored in state or diagnostics.
    """
    if not source.is_absolute() or source.name != expected_name:
        raise ValidationIssue(
            "full_run_devin_auth_source_invalid",
            f"Devin auth path must be an absolute {expected_name} path",
        )
    try:
        canonical_parent = source.parent.resolve(strict=True)
    except OSError as exc:
        raise ValidationIssue(
            "full_run_devin_auth_source_missing",
            f"Devin auth {expected_name} parent directory is unavailable",
        ) from exc
    canonical = canonical_parent / expected_name
    file_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    dir_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
    parent_fd = -1
    source_fd = -1

    def _close() -> None:
        if source_fd >= 0:
            os.close(source_fd)
        if parent_fd >= 0:
            os.close(parent_fd)

    try:
        parent_fd = os.open(str(canonical_parent), dir_flags)
        parent_before = os.fstat(parent_fd)
        source_fd = os.open(expected_name, file_flags, dir_fd=parent_fd)
    except FileNotFoundError as exc:
        _close()
        raise ValidationIssue(
            "full_run_devin_auth_source_missing",
            f"Devin auth {expected_name} is unavailable",
        ) from exc
    except OSError as exc:
        _close()
        if exc.errno == getattr(errno, "ELOOP", None):
            raise ValidationIssue(
                "full_run_devin_auth_source_unsafe",
                f"Devin auth {expected_name} must not be a symlink",
            ) from exc
        raise ValidationIssue(
            "full_run_devin_auth_source_unavailable",
            f"Devin auth {expected_name} metadata is unavailable: {type(exc).__name__}",
        ) from exc
    try:
        before = os.fstat(source_fd)
        if stat.S_IMODE(before.st_mode) != 0o600:
            raise ValidationIssue(
                "full_run_devin_auth_source_unsafe",
                f"Devin auth {expected_name} must use owner-only mode 0o600",
                hint=f"Run: chmod 600 {source}",
            )
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or before.st_size <= 0
            or before.st_size > max_bytes
        ):
            raise ValidationIssue(
                "full_run_devin_auth_source_unsafe",
                f"Devin auth {expected_name} must be one owner-only regular file within the size limit",
            )
        with os.fdopen(source_fd, "rb", closefd=False) as handle:
            raw = handle.read(max_bytes + 1)
        after = os.fstat(source_fd)
        published = os.stat(
            expected_name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
        parent_after = os.fstat(parent_fd)
        if (
            len(raw) > max_bytes
            or (
                before.st_dev,
                before.st_ino,
                before.st_uid,
                before.st_nlink,
                stat.S_IMODE(before.st_mode),
                before.st_size,
                getattr(before, "st_mtime_ns", None),
                getattr(before, "st_ctime_ns", None),
            )
            != (
                after.st_dev,
                after.st_ino,
                after.st_uid,
                after.st_nlink,
                stat.S_IMODE(after.st_mode),
                after.st_size,
                getattr(after, "st_mtime_ns", None),
                getattr(after, "st_ctime_ns", None),
            )
            or len(raw) != after.st_size
            or (published.st_dev, published.st_ino) != (after.st_dev, after.st_ino)
            or (
                parent_before.st_dev,
                parent_before.st_ino,
                parent_before.st_uid,
                stat.S_IMODE(parent_before.st_mode),
            )
            != (
                parent_after.st_dev,
                parent_after.st_ino,
                parent_after.st_uid,
                stat.S_IMODE(parent_after.st_mode),
            )
        ):
            raise ValidationIssue(
                "full_run_devin_auth_source_changed",
                f"Devin auth {expected_name} changed while it was being read",
            )
    except ValidationIssue:
        _close()
        raise
    except OSError as exc:
        _close()
        raise ValidationIssue(
            "full_run_devin_auth_source_unavailable",
            f"Devin auth {expected_name} read failed: {type(exc).__name__}",
        ) from exc
    _close()
    _validate_devin_file_content(raw, expected_name)
    identity = {
        "path": str(canonical),
        "parent_dev": int(parent_before.st_dev),
        "parent_ino": int(parent_before.st_ino),
        "parent_uid": int(parent_before.st_uid),
        "parent_mode": int(stat.S_IMODE(parent_before.st_mode)),
        "dev": int(before.st_dev),
        "ino": int(before.st_ino),
        "uid": int(before.st_uid),
        "mode": int(stat.S_IMODE(before.st_mode)),
        "nlink": int(before.st_nlink),
        "size": int(before.st_size),
        "mtime_ns": int(getattr(before, "st_mtime_ns", 0) or 0),
        "ctime_ns": int(getattr(before, "st_ctime_ns", 0) or 0),
    }
    return raw, identity


def _validate_devin_file_content(raw: bytes, expected_name: str) -> None:
    """Parse Devin config/credentials without keeping or exposing values."""
    if expected_name == DEVIN_CONFIG_FILE_NAME:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationIssue(
                "full_run_devin_auth_source_invalid",
                "Devin config.json is not bounded valid JSON",
            ) from exc
        if not isinstance(payload, Mapping) or "devin" not in payload:
            raise ValidationIssue(
                "full_run_devin_auth_source_invalid",
                "Devin config.json is missing the required 'devin' section",
            )
        return
    if expected_name == DEVIN_CREDENTIALS_FILE_NAME:
        try:
            payload = _load_toml(raw.decode("utf-8"))
        except Exception as exc:
            raise ValidationIssue(
                "full_run_devin_auth_source_invalid",
                "Devin credentials.toml is not valid TOML",
            ) from exc
        if not isinstance(payload, Mapping) or not any(
            isinstance(payload.get(key), str) and bool(payload.get(key))
            for key in ("windsurf_api_key", "api_key")
        ):
            raise ValidationIssue(
                "full_run_devin_auth_source_invalid",
                "Devin credentials.toml has no recognized API key",
            )
        return
    raise ValidationIssue(
        "full_run_devin_auth_source_invalid",
        f"Unknown Devin auth file name: {expected_name}",
    )


def _project_devin_auth(
    root: Path,
    launch_env: dict[str, str],
    files: Mapping[str, tuple[bytes, dict[str, Any]]],
) -> dict[str, Path]:
    """Copy validated Devin auth files into the isolated worker HOME.

    Sets owner-only mode on directories and files. Returns the projected paths.
    """
    worker_home = root / "worker-home"
    rel_paths = {
        "config": Path(".config") / "devin" / DEVIN_CONFIG_FILE_NAME,
        "credentials": Path(".local") / "share" / "devin" / DEVIN_CREDENTIALS_FILE_NAME,
    }
    projected: dict[str, Path] = {}
    for key, (raw, _identity) in files.items():
        rel = rel_paths[key]
        target = worker_home / rel
        current = worker_home
        for component in rel.parent.parts:
            current = current / component
            current.mkdir(exist_ok=True)
            info = current.lstat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_nlink < 1
            ):
                raise ValidationIssue(
                    "full_run_devin_auth_projection_unsafe",
                    "Devin auth projection directory is not a private host-owned directory",
                )
            current.chmod(0o700)
        try:
            existing = target.lstat()
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if stat.S_ISDIR(existing.st_mode):
                raise ValidationIssue(
                    "full_run_devin_auth_projection_unsafe",
                    f"Projected Devin auth target {target.name} is a directory",
                )
            target.unlink()
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        fd = -1
        try:
            fd = os.open(target, flags, 0o600)
            offset = 0
            while offset < len(raw):
                written = os.write(fd, raw[offset:])
                if written <= 0:
                    raise OSError(errno.EIO, "short Devin auth projection write")
                offset += written
            os.fchmod(fd, 0o600)
            os.fsync(fd)
            published = os.fstat(fd)
            if (
                not stat.S_ISREG(published.st_mode)
                or published.st_uid != os.geteuid()
                or published.st_nlink != 1
                or stat.S_IMODE(published.st_mode) != 0o600
                or published.st_size != len(raw)
            ):
                raise OSError(errno.ESTALE, "projected Devin auth identity changed")
        except OSError as exc:
            try:
                target.unlink()
            except OSError:
                pass
            raise ValidationIssue(
                "full_run_devin_auth_projection_failed",
                f"Cannot create private projected Devin auth file: {type(exc).__name__}",
            ) from exc
        finally:
            if fd >= 0:
                os.close(fd)
        projected[key] = target
    return projected


def _read_and_validate_devin_auth(
    state: FullRunState,
    parent_env: Mapping[str, str] | None = None,
) -> tuple[dict[str, Path], dict[str, tuple[bytes, dict[str, Any]]]]:
    """Read host Devin auth files and verify they match any persisted identity.

    Returns the source paths and the validated file contents/identities.
    Callers decide whether to project into the isolated worker HOME.
    """
    sources = _devin_auth_source_files(parent_env)
    expected = state.devin_auth_identity
    files: dict[str, tuple[bytes, dict[str, Any]]] = {}
    for key, expected_name in (
        ("config", DEVIN_CONFIG_FILE_NAME),
        ("credentials", DEVIN_CREDENTIALS_FILE_NAME),
    ):
        source = sources[key]
        raw, identity = _read_host_devin_file(source, MAX_DEVIN_AUTH_BYTES, expected_name)
        if isinstance(expected, Mapping):
            prior = expected.get(f"{key}_identity")
            if isinstance(prior, Mapping) and dict(prior) != identity:
                raise ValidationIssue(
                    "full_run_devin_auth_identity_changed",
                    f"Devin auth {expected_name} canonical path identity changed",
                )
        files[key] = (raw, identity)
    return sources, files


def _configure_devin_auth(
    repo_root: Path,
    root: Path,
    state: FullRunState,
    launch_env: dict[str, str],
    *,
    grant_devin_auth: bool,
    parent_env: Mapping[str, str] | None = None,
) -> None:
    """Validate and project host Devin CLI auth into the isolated worker HOME."""
    if state.adapter != "devin-cli":
        if grant_devin_auth:
            raise ValidationIssue(
                "full_run_devin_auth_adapter_mismatch",
                "--grant-devin-auth is only valid with adapter=devin-cli",
            )
        return
    if state.devin_auth_strategy not in {None, "projected_files"}:
        raise ValidationIssue(
            "full_run_devin_auth_strategy_changed",
            "Resume must preserve the originally selected Devin auth strategy",
        )
    if state.devin_auth_strategy is None and not grant_devin_auth:
        raise ValidationIssue(
            "full_run_devin_auth_required",
            "Headless Devin requires --grant-devin-auth with validated host config/credentials",
        )
    sources, files = _read_and_validate_devin_auth(state, parent_env)
    projected = _project_devin_auth(root, launch_env, files)
    identities = {f"{key}_identity": identity for key, (_raw, identity) in files.items()}
    state.devin_auth_strategy = "projected_files"
    state.devin_auth_identity = {
        **identities,
        "config_path": str(sources["config"]),
        "credentials_path": str(sources["credentials"]),
        "projected_config_path": str(projected["config"]),
        "projected_credentials_path": str(projected["credentials"]),
    }
    state.notes.append("Devin auth projected into isolated worker HOME")


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


def _darwin_proc_pidpath(pid: int) -> str | None:
    """Return the kernel-reported absolute path for a live Darwin process."""
    if sys.platform != "darwin" or pid <= 0:
        return None
    try:
        lib_name = ctypes.util.find_library("c")
        if not lib_name:
            return None
        lib = ctypes.CDLL(lib_name, use_errno=True)
        buf = ctypes.create_string_buffer(4096)
        # int proc_pidpath(int pid, void *buffer, uint32_t buffersize);
        proc_pidpath = getattr(lib, "proc_pidpath", None)
        if proc_pidpath is None:
            return None
        proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        proc_pidpath.restype = ctypes.c_int
        written = int(proc_pidpath(int(pid), buf, ctypes.c_uint32(len(buf))))
        if written <= 0:
            return None
        path = buf.value.decode("utf-8", errors="surrogateescape").strip()
        return path or None
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _normalize_executable_path(path: str | None) -> str | None:
    """Normalize process paths so capture/verify comparisons stay stable.

    On macOS, ``sys.executable`` often points at a framework ``bin/pythonX.Y``
    shim while ``proc_pidpath``/``ps`` report the nested
    ``Python.app/Contents/MacOS/Python`` binary. Treat those as the same identity
    when they resolve under the same framework version root.
    """
    if not path:
        return None
    try:
        text = str(Path(str(path)).expanduser())
    except (OSError, RuntimeError, TypeError, ValueError):
        text = str(path)
    try:
        resolved = str(Path(text).resolve())
    except OSError:
        resolved = text
    # Collapse .../Python.framework/Versions/X/bin/python* and
    # .../Python.framework/Versions/X/Resources/Python.app/Contents/MacOS/Python
    # to the shared framework version directory when present.
    marker = "/Python.framework/Versions/"
    if marker in resolved:
        head, tail = resolved.split(marker, 1)
        version = tail.split("/", 1)[0]
        if version:
            return f"{head}{marker}{version}"
    return resolved


def _process_executable(pid: int) -> str | None:
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError:
        pass
    # Prefer the kernel path on Darwin. ``ps -o command=`` is only a fallback and
    # can disagree with ``sys.executable`` (framework shim vs Python.app binary).
    darwin_path = _darwin_proc_pidpath(pid)
    if darwin_path:
        return darwin_path
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
    # Brief retry so Darwin proc_pidpath/ps can observe a just-spawned child
    # before we fall back to the launcher's sys.executable hint.
    executable = _process_executable(pid)
    if not executable:
        for _ in range(5):
            time.sleep(0.02)
            executable = _process_executable(pid)
            if executable:
                break
    if not executable and executable_hint:
        executable = str(executable_hint)
    return ProcessFingerprint(
        pid=pid,
        pgid=pgid,
        start_time=_process_start_time(pid),
        executable=executable,
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
    # Darwin framework shims are normalized to the shared Versions/X root so a
    # launcher hint of bin/pythonX.Y still matches Python.app/Contents/MacOS/Python.
    expected_exe = _normalize_executable_path(fp.executable)
    observed_exe = _normalize_executable_path(current_exe)
    if not expected_exe or not observed_exe or expected_exe != observed_exe:
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
    """Signal only through a kernel-bound pidfd; numeric PID signaling is forbidden."""
    if not (
        sys.platform.startswith("linux")
        and hasattr(os, "pidfd_open")
        and hasattr(signal, "pidfd_send_signal")
    ):
        raise ValidationIssue(
            "full_run_atomic_signal_unavailable",
            "This platform has no kernel-bound process signal handle; use the private supervisor stop request",
        )
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
        try:
            pidfd = os.pidfd_open(pid, 0)
        except ProcessLookupError:
            return False
        except OSError as exc:
            raise ValidationIssue(
                "full_run_pidfd_unavailable",
                f"Cannot bind the live supervisor process handle: {type(exc).__name__}",
            ) from exc

        # Revalidate after acquiring the kernel-bound handle. This rejects reuse
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
            signal.pidfd_send_signal(pidfd, signum)
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
    if record.get("supervision_marker") != _descendant_supervision_marker(state):
        errors.append("exit record supervision marker mismatch")
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
import ctypes, ctypes.util, errno, hashlib, hmac, json, os, signal, stat, subprocess, sys, time
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
max_stop_request_bytes = int(sys.argv[7])
provider_executable_identity = json.loads(sys.argv[8])
expected_staged_packet_path = sys.argv[9]
expected_staged_packet_identity = json.loads(sys.argv[10])
expected_packet_sha256 = sys.argv[11]
expected_packet_size = int(sys.argv[12])
max_packet_bytes = int(sys.argv[13])
if max_stop_request_bytes <= 0 or max_stop_request_bytes > 64 * 1024:
    raise SystemExit(126)
if (
    not isinstance(provider_argv, list)
    or not provider_argv
    or any(not isinstance(value, str) or not value for value in provider_argv)
    or not isinstance(provider_executable_identity, dict)
    or not isinstance(expected_staged_packet_identity, dict)
    or not os.path.isabs(expected_staged_packet_path)
    or len(expected_packet_sha256) != 64
    or any(char not in "0123456789abcdef" for char in expected_packet_sha256)
    or expected_packet_size < 0
    or expected_packet_size > max_packet_bytes
    or max_packet_bytes <= 0
    or max_packet_bytes > 16 * 1024 * 1024
):
    raise SystemExit(126)
try:
    # The launcher supplies exactly one bounded host secret over an anonymous
    # pipe. Close fd 0 before provider spawn so neither it nor descendants can
    # inherit or recover the stop capability from argv, env, or open fds.
    supervision_secret_payload = sys.stdin.buffer.read(65)
finally:
    sys.stdin.close()
if (
    len(supervision_secret_payload) != 49
    or not supervision_secret_payload.endswith(b"\n")
):
    raise SystemExit(126)
try:
    supervision_secret = supervision_secret_payload[:-1].decode("ascii")
except UnicodeDecodeError:
    raise SystemExit(126)
if len(supervision_secret) != 48 or any(
    char not in "0123456789abcdef" for char in supervision_secret
):
    raise SystemExit(126)
descendant_marker = os.environ.get("ELVES_FULL_RUN_SUPERVISION_MARKER", "")
expected_marker = hmac.new(
    supervision_secret.encode("ascii"),
    ("descendant-marker\0%s\0%s" % (session_id, attempt)).encode("utf-8"),
    hashlib.sha256,
).hexdigest()
if not hmac.compare_digest(descendant_marker, expected_marker):
    raise SystemExit(126)
marker = "ELVES_FULL_RUN_SUPERVISION_MARKER=" + descendant_marker
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

def requested_stop_signal():
    request_fd = None
    try:
        request_fd = os.open(
            "stop_request.json",
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=runtime_fd,
        )
    except FileNotFoundError:
        return None
    except OSError:
        # Worker-created symlinks or other unsafe leaves are untrusted noise,
        # never authorization and never a reason to terminate a healthy run.
        return None
    try:
        info = os.fstat(request_fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_size > max_stop_request_bytes
        ):
            raise RuntimeError("unsafe_stop_request")
        raw = os.read(request_fd, max_stop_request_bytes + 1)
        if len(raw) > max_stop_request_bytes:
            raise RuntimeError("oversized_stop_request")
        request = json.loads(raw.decode("utf-8"))
        message = ("stop\0%s\0%s" % (session_id, attempt)).encode("utf-8")
        expected = hmac.new(
            supervision_secret.encode("ascii"),
            message,
            hashlib.sha256,
        ).hexdigest()
        if (
            not isinstance(request, dict)
            or request.get("session_id") != session_id
            or request.get("attempt") != attempt
            or not isinstance(request.get("authority"), str)
            or not hmac.compare_digest(request["authority"], expected)
        ):
            raise RuntimeError("unauthorized_stop_request")
        return signal.SIGTERM
    except Exception:
        # Malformed, oversized, non-regular, or unauthorized artifacts are
        # ignored. Only a capability-bearing request may alter run control; the
        # worker itself remains trusted by this authority model.
        return None
    finally:
        os.close(request_fd)

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
        if len(descendant_marker) != 64 or any(
            char not in "0123456789abcdef" for char in descendant_marker
        ):
            raise RuntimeError("invalid_supervision_marker")
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
    if not (
        sys.platform.startswith("linux")
        and hasattr(os, "pidfd_open")
        and hasattr(signal, "pidfd_send_signal")
    ):
        supervision_error = "atomic_process_signal_unavailable"
        return
    for pid in sorted(pids, reverse=True):
        if pid == os.getpid():
            continue
        expected_start = known_identities.get(pid)
        if expected_start is None:
            continue
        pidfd = None
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
            signal.pidfd_send_signal(pidfd, signum)
        except ProcessLookupError:
            pass
        except OSError as exc:
            supervision_error = "signal_failed:%s:%s" % (pid, exc)
        finally:
            if pidfd is not None:
                os.close(pidfd)

def terminate_descendants():
    global supervision_error
    alive = scan_alive()
    if sys.platform == "darwin":
        if alive:
            try:
                # The supervisor is the live session/group leader, so its own
                # current group cannot be numerically reused during this call.
                # Detached descendants are never signaled by reusable PID; they
                # remain explicit failure evidence for operator handling.
                os.killpg(os.getpgrp(), signal.SIGTERM)
            except OSError as exc:
                supervision_error = "group_signal_failed:%s" % exc
                return False
        deadline = time.monotonic() + 1.25
        while alive and time.monotonic() < deadline and supervision_error is None:
            time.sleep(0.03)
            alive = scan_alive()
        return not alive
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

MACH_O_MAGICS = {
    bytes.fromhex(value)
    for value in (
        "feedface", "cefaedfe", "feedfacf", "cffaedfe",
        "cafebabe", "bebafeca", "cafebabf", "bfbafeca",
    )
}

def native_executable_format(descriptor):
    header = os.pread(descriptor, 4, 0)
    if sys.platform == "darwin" and header in MACH_O_MAGICS:
        return "mach-o"
    if sys.platform.startswith("linux") and header == b"\x7fELF":
        return "elf"
    return None

def assert_no_extended_allow_acl(descriptor):
    if sys.platform != "darwin":
        return
    library = ctypes.CDLL(
        ctypes.util.find_library("System") or "/usr/lib/libSystem.B.dylib",
        use_errno=True,
    )
    acl_get_fd_np = library.acl_get_fd_np
    acl_get_fd_np.argtypes = [ctypes.c_int, ctypes.c_int]
    acl_get_fd_np.restype = ctypes.c_void_p
    acl_get_entry = library.acl_get_entry
    acl_get_entry.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)
    ]
    acl_get_entry.restype = ctypes.c_int
    acl_get_tag_type = library.acl_get_tag_type
    acl_get_tag_type.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)
    ]
    acl_get_tag_type.restype = ctypes.c_int
    acl_free = library.acl_free
    acl_free.argtypes = [ctypes.c_void_p]
    acl_free.restype = ctypes.c_int
    ctypes.set_errno(0)
    acl = acl_get_fd_np(descriptor, 0x00000100)
    if not acl:
        if ctypes.get_errno() == errno.ENOENT:
            return
        raise RuntimeError("provider_acl_inspection_failed")
    error = None
    saw_entry = False
    entry_id = 0
    try:
        while True:
            entry = ctypes.c_void_p()
            ctypes.set_errno(0)
            result = acl_get_entry(acl, entry_id, ctypes.byref(entry))
            entry_errno = ctypes.get_errno()
            if result == -1:
                if saw_entry and entry_errno == errno.EINVAL:
                    break
                error = RuntimeError("provider_acl_inspection_failed")
                break
            if result != 0 or not entry.value:
                error = RuntimeError("provider_acl_inspection_failed")
                break
            tag = ctypes.c_int()
            if acl_get_tag_type(entry, ctypes.byref(tag)) != 0:
                error = RuntimeError("provider_acl_inspection_failed")
                break
            if tag.value == 1:
                error = RuntimeError("provider_acl_allow_unsafe")
                break
            if tag.value != 2:
                error = RuntimeError("provider_acl_inspection_failed")
                break
            saw_entry = True
            entry_id = -1
    finally:
        if acl_free(acl) != 0:
            error = RuntimeError("provider_acl_inspection_failed")
    if error is not None:
        raise error

def provider_directory_identity(info):
    return {
        "dev": int(info.st_dev),
        "ino": int(info.st_ino),
        "uid": int(info.st_uid),
        "mode": int(stat.S_IMODE(info.st_mode)),
    }

def bind_shared_oauth_provider_executable():
    expected_path = provider_executable_identity.get("path")
    expected_chain = provider_executable_identity.get("parent_chain")
    required = {
        "path", "dev", "ino", "uid", "mode", "nlink", "size",
        "mtime_ns", "ctime_ns", "security_profile", "native_format",
        "parent_chain",
    }
    if (
        set(provider_executable_identity) != required
        or provider_executable_identity.get("security_profile")
        != "shared_oauth_native"
        or not isinstance(expected_path, str)
        or provider_argv[0] != expected_path
        or not os.path.isabs(expected_path)
        or os.path.realpath(expected_path) != expected_path
        or not isinstance(expected_chain, list)
        or not expected_chain
    ):
        raise RuntimeError("provider_identity_invalid")
    candidate = Path(expected_path)
    parent = candidate.parent
    anchor = Path(parent.anchor)
    parts = parent.relative_to(anchor).parts
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_DIRECTORY", 0)
    )
    file_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    bound_fds = []
    try:
        bound_fds.append(os.open(anchor, directory_flags))
        observed_chain = []
        for index, component in enumerate((None, *parts)):
            if component is not None:
                bound_fds.append(
                    os.open(component, directory_flags, dir_fd=bound_fds[-1])
                )
            info = os.fstat(bound_fds[-1])
            assert_no_extended_allow_acl(bound_fds[-1])
            mode = stat.S_IMODE(info.st_mode)
            is_final = index == len(parts)
            safe_sticky_root = bool(
                info.st_uid == 0
                and mode & stat.S_ISVTX
                and mode & (stat.S_IWGRP | stat.S_IWOTH)
            )
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid not in {0, os.geteuid()}
                or (
                    mode & (stat.S_IWGRP | stat.S_IWOTH)
                    and not safe_sticky_root
                )
                or (is_final and info.st_uid not in {0, os.geteuid()})
            ):
                raise RuntimeError("provider_parent_unsafe")
            observed_chain.append(provider_directory_identity(info))
        if observed_chain != expected_chain:
            raise RuntimeError("provider_parent_identity_changed")
        executable_fd = os.open(
            candidate.name, file_flags, dir_fd=bound_fds[-1]
        )
        bound_fds.append(executable_fd)
        info = os.fstat(executable_fd)
        assert_no_extended_allow_acl(executable_fd)
        mode = stat.S_IMODE(info.st_mode)
        native_format = native_executable_format(executable_fd)
        observed = {
            "path": expected_path,
            "dev": int(info.st_dev),
            "ino": int(info.st_ino),
            "uid": int(info.st_uid),
            "mode": mode,
            "nlink": int(info.st_nlink),
            "size": int(info.st_size),
            "mtime_ns": int(info.st_mtime_ns),
            "ctime_ns": int(info.st_ctime_ns),
            "security_profile": "shared_oauth_native",
            "native_format": native_format,
            "parent_chain": observed_chain,
        }
        published = os.stat(
            candidate.name,
            dir_fd=bound_fds[-2],
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid not in {0, os.geteuid()}
            or info.st_nlink != 1
            or mode & (stat.S_IWGRP | stat.S_IWOTH)
            or not (mode & 0o111)
            or native_format not in {"mach-o", "elf"}
            or observed != provider_executable_identity
            or (published.st_dev, published.st_ino)
            != (info.st_dev, info.st_ino)
        ):
            raise RuntimeError("provider_executable_identity_changed")
        return bound_fds
    except BaseException:
        for descriptor in reversed(bound_fds):
            os.close(descriptor)
        raise

def bind_staged_packet_snapshot():
    required = {
        "path", "dev", "ino", "uid", "mode", "nlink", "size",
        "mtime_ns", "ctime_ns",
    }
    if (
        set(expected_staged_packet_identity) != required
        or expected_staged_packet_identity.get("path")
        != expected_staged_packet_path
        or any(
            isinstance(expected_staged_packet_identity.get(key), bool)
            or not isinstance(expected_staged_packet_identity.get(key), int)
            for key in required - {"path"}
        )
        or provider_argv.count(expected_staged_packet_path) != 1
    ):
        raise RuntimeError("staged_packet_identity_invalid")
    source_fd = None
    snapshot_write_fd = None
    snapshot_fd = None
    snapshot_name = None
    try:
        source_fd = os.open(
            expected_staged_packet_path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        info = os.fstat(source_fd)
        observed = {
            "path": expected_staged_packet_path,
            "dev": int(info.st_dev),
            "ino": int(info.st_ino),
            "uid": int(info.st_uid),
            "mode": int(stat.S_IMODE(info.st_mode)),
            "nlink": int(info.st_nlink),
            "size": int(info.st_size),
            "mtime_ns": int(info.st_mtime_ns),
            "ctime_ns": int(info.st_ctime_ns),
        }
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) & 0o077
            or observed != expected_staged_packet_identity
            or info.st_size != expected_packet_size
        ):
            raise RuntimeError("staged_packet_identity_changed")
        chunks = []
        remaining = max_packet_bytes + 1
        while remaining > 0:
            chunk = os.read(source_fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if (
            len(raw) != expected_packet_size
            or len(raw) > max_packet_bytes
            or not hmac.compare_digest(
                hashlib.sha256(raw).hexdigest(), expected_packet_sha256
            )
        ):
            raise RuntimeError("staged_packet_digest_changed")
        published = os.stat(expected_staged_packet_path, follow_symlinks=False)
        if (
            published.st_dev != info.st_dev
            or published.st_ino != info.st_ino
            or published.st_size != info.st_size
            or published.st_mtime_ns != info.st_mtime_ns
            or published.st_ctime_ns != info.st_ctime_ns
        ):
            raise RuntimeError("staged_packet_path_changed")

        # The provider reads an unlinked, read-only snapshot inherited by fd.
        # Later in-place writes or atomic replacement of the staged path cannot
        # alter the bytes consumed after a delayed provider read.
        for nonce in range(16):
            snapshot_name = ".packet-snapshot.%s.%s.%s" % (
                os.getpid(), time.time_ns(), nonce
            )
            try:
                snapshot_write_fd = os.open(
                    snapshot_name,
                    os.O_RDWR
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=runtime_fd,
                )
                break
            except FileExistsError:
                snapshot_name = None
        if snapshot_write_fd is None or snapshot_name is None:
            raise RuntimeError("staged_packet_snapshot_create_failed")
        offset = 0
        while offset < len(raw):
            written = os.write(snapshot_write_fd, raw[offset:])
            if written <= 0:
                raise RuntimeError("staged_packet_snapshot_short_write")
            offset += written
        os.fsync(snapshot_write_fd)
        os.fchmod(snapshot_write_fd, 0o400)
        snapshot_fd = os.open(
            snapshot_name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=runtime_fd,
        )
        write_info = os.fstat(snapshot_write_fd)
        read_info = os.fstat(snapshot_fd)
        if (write_info.st_dev, write_info.st_ino) != (
            read_info.st_dev, read_info.st_ino
        ):
            raise RuntimeError("staged_packet_snapshot_identity_changed")
        os.close(snapshot_write_fd)
        snapshot_write_fd = None
        os.unlink(snapshot_name, dir_fd=runtime_fd)
        snapshot_name = None
        snapshot_info = os.fstat(snapshot_fd)
        if (
            not stat.S_ISREG(snapshot_info.st_mode)
            or snapshot_info.st_nlink != 0
            or snapshot_info.st_size != expected_packet_size
            or stat.S_IMODE(snapshot_info.st_mode) != 0o400
        ):
            raise RuntimeError("staged_packet_snapshot_invalid")
        rewritten = list(provider_argv)
        packet_index = rewritten.index(expected_staged_packet_path)
        rewritten[packet_index] = "/dev/fd/%s" % snapshot_fd
        return rewritten, [source_fd, snapshot_fd], snapshot_fd
    except BaseException:
        if snapshot_name is not None:
            try:
                os.unlink(snapshot_name, dir_fd=runtime_fd)
            except FileNotFoundError:
                pass
        for descriptor in (snapshot_fd, snapshot_write_fd, source_fd):
            if descriptor is not None:
                os.close(descriptor)
        raise

def provider_executable_identity_matches():
    if not provider_executable_identity:
        return True, []
    if provider_executable_identity.get("security_profile") == "shared_oauth_native":
        try:
            return True, bind_shared_oauth_provider_executable()
        except BaseException:
            return False, []
    required = {
        "path", "dev", "ino", "uid", "mode", "nlink", "size",
        "mtime_ns", "ctime_ns", "security_profile",
    }
    if set(provider_executable_identity) != required:
        return False, []
    expected_path = provider_executable_identity.get("path")
    if (
        provider_argv[0] != expected_path
        or not isinstance(expected_path, str)
        or not os.path.isabs(expected_path)
    ):
        return False, []
    try:
        info = os.stat(expected_path, follow_symlinks=False)
    except OSError:
        return False, []
    observed = {
        "path": expected_path,
        "dev": int(info.st_dev),
        "ino": int(info.st_ino),
        "uid": int(info.st_uid),
        "mode": int(stat.S_IMODE(info.st_mode)),
        "nlink": int(info.st_nlink),
        "size": int(info.st_size),
        "mtime_ns": int(info.st_mtime_ns),
        "ctime_ns": int(info.st_ctime_ns),
        "security_profile": "exact_path",
    }
    return stat.S_ISREG(info.st_mode) and observed == provider_executable_identity, []

provider_identity_matches, provider_binding_fds = provider_executable_identity_matches()
packet_binding_fds = []
packet_pass_fd = None
if not provider_identity_matches:
    supervision_error = "provider_executable_identity_mismatch"
    exit_code = 125
else:
    try:
        provider_argv, packet_binding_fds, packet_pass_fd = (
            bind_staged_packet_snapshot()
        )
    except BaseException:
        supervision_error = "staged_packet_binding_mismatch"
        exit_code = 125

if supervision_error is None:
    try:
        provider = subprocess.Popen(
            provider_argv,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            pass_fds=(packet_pass_fd,),
        )
        for descriptor in reversed(provider_binding_fds):
            os.close(descriptor)
        provider_binding_fds = []
        for descriptor in reversed(packet_binding_fds):
            os.close(descriptor)
        packet_binding_fds = []
        provider_pid = provider.pid
        scan_alive()
        while provider.poll() is None and stop_signal is None:
            requested = requested_stop_signal()
            if requested is not None:
                stop_signal = requested
                break
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
    finally:
        for descriptor in reversed(provider_binding_fds):
            os.close(descriptor)
        for descriptor in reversed(packet_binding_fds):
            os.close(descriptor)

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
    "supervision_marker": descendant_marker,
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
    staged_packet_path: str,
    staged_packet_identity: Mapping[str, Any],
    packet_sha256: str,
    packet_size: int,
    provider_executable_identity: Mapping[str, Any] | None = None,
) -> list[str]:
    """Build a parent supervisor without putting its signaling token on argv."""
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
        str(MAX_STOP_REQUEST_BYTES),
        json.dumps(dict(provider_executable_identity or {}), sort_keys=True),
        staged_packet_path,
        json.dumps(dict(staged_packet_identity), sort_keys=True),
        packet_sha256,
        str(packet_size),
        str(MAX_PACKET_BYTES),
    ]


def _handoff_supervision_secret(
    proc: subprocess.Popen[bytes],
    state: FullRunState,
) -> None:
    """Release a staged supervisor with one bounded secret, never a partial payload."""
    stream = proc.stdin
    if stream is None:
        try:
            proc.kill()
            proc.wait(timeout=1.0)
        except (OSError, subprocess.TimeoutExpired):
            pass
        raise _PreTransferHandoffError(
            RuntimeError("supervisor stdin channel missing")
        )
    try:
        payload = (_supervision_secret(state) + "\n").encode("ascii")
        descriptor = stream.fileno()
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("short supervision secret write")
            offset += written
    except BaseException as exc:
        # Until the complete payload and EOF arrive, the child is blocked before
        # provider spawn. Kill and reap that exact child before closing the pipe.
        try:
            proc.kill()
        except OSError:
            pass
        try:
            stream.close()
        except OSError:
            pass
        finally:
            proc.stdin = None
        try:
            proc.wait(timeout=1.0)
        except (OSError, subprocess.TimeoutExpired):
            pass
        raise _PreTransferHandoffError(exc) from exc
    try:
        # EOF is the commit point: only after the complete fixed-size payload is
        # written can closing stdin release the child into provider spawn.
        stream.close()
    except OSError:
        # Close/EOF delivery is ambiguous. Preserve durable ownership so monitor
        # or stop can observe whichever side of the commit point occurred.
        pass
    finally:
        proc.stdin = None


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


def _origin_push_auth_kind(origin_url: str) -> str:
    """Classify the one supported authenticated push boundary.

    Local/file remotes need no credential projection and remain useful for
    deterministic tests. GitHub HTTPS receives an explicit token-backed helper.
    Every other network transport fails closed because the isolated worker has
    neither host SSH-agent access nor host Git credential configuration.
    """
    raw = str(origin_url or "").strip()
    parsed = urlsplit(raw)
    if parsed.scheme == "https" and (parsed.hostname or "").lower() == "github.com":
        return "github_https"
    if parsed.scheme == "file":
        return "local"
    if not parsed.scheme and not re.match(r"^[^/@:]+@[^/:]+:", raw):
        return "local"
    return "unsupported"


def _read_host_git_identity_value(
    worktree: Path,
    key: str,
    *,
    parent_env: Mapping[str, str] | None = None,
) -> str:
    """Resolve one explicit host Git identity field without allowing guessing."""
    parent = dict(parent_env if parent_env is not None else os.environ)
    try:
        result = subprocess.run(
            ["git", "-C", str(worktree), "config", "--get", key],
            env=parent,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValidationIssue(
            "full_run_git_identity_unavailable",
            "Cannot resolve an explicit Git commit identity for the isolated worker",
        ) from exc
    raw = bytes(result.stdout or b"")
    value = raw.strip()
    if (
        result.returncode != 0
        or not value
        or len(raw) > MAX_GIT_IDENTITY_BYTES
        or any(byte < 32 or byte == 127 for byte in value)
    ):
        raise ValidationIssue(
            "full_run_git_identity_unavailable",
            "Trusted branch progress requires explicit Git user.name and user.email values",
            hint="Configure Git user.name and user.email before launching the worker",
        )
    try:
        decoded = value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationIssue(
            "full_run_git_identity_unavailable",
            "Configured Git commit identity has an invalid encoding",
        ) from exc
    if "<" in decoded or ">" in decoded:
        raise ValidationIssue(
            "full_run_git_identity_invalid",
            "Configured Git commit identity contains forbidden delimiters",
        )
    return decoded


def _configure_git_commit_identity(
    state: FullRunState,
    launch_env: dict[str, str],
    *,
    parent_env: Mapping[str, str] | None = None,
) -> None:
    """Bind the host's explicit author identity into an otherwise isolated env."""
    worktree = Path(state.worktree)
    name = _read_host_git_identity_value(
        worktree,
        "user.name",
        parent_env=parent_env,
    )
    email = _read_host_git_identity_value(
        worktree,
        "user.email",
        parent_env=parent_env,
    )
    launch_env["GIT_AUTHOR_NAME"] = name
    launch_env["GIT_AUTHOR_EMAIL"] = email
    launch_env["GIT_COMMITTER_NAME"] = name
    launch_env["GIT_COMMITTER_EMAIL"] = email


def _read_host_github_token(
    parent_env: Mapping[str, str] | None = None,
) -> str:
    """Read one host gh token privately; never include its bytes in diagnostics."""
    parent = dict(parent_env if parent_env is not None else os.environ)
    executable = shutil.which("gh", path=parent.get("PATH"))
    if not executable:
        raise ValidationIssue(
            "full_run_github_push_auth_unavailable",
            "Explicit GitHub push projection requires an authenticated gh CLI",
            hint="Run `gh auth login`, or explicitly grant GH_TOKEN/GITHUB_TOKEN by name",
        )
    try:
        result = subprocess.run(
            [executable, "auth", "token", "--hostname", "github.com"],
            env=parent,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValidationIssue(
            "full_run_github_push_auth_unavailable",
            "Host GitHub credential lookup failed",
        ) from exc
    raw = bytes(result.stdout or b"")
    token = raw.strip()
    if (
        result.returncode != 0
        or not token
        or len(raw) > MAX_GITHUB_TOKEN_BYTES
        or any(byte <= 32 or byte == 127 for byte in token)
    ):
        raise ValidationIssue(
            "full_run_github_push_auth_unavailable",
            "Host gh CLI did not return one bounded noninteractive credential",
            hint="Run `gh auth status --hostname github.com` before launching",
        )
    try:
        return token.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationIssue(
            "full_run_github_push_auth_unavailable",
            "Host gh CLI returned an invalid credential encoding",
        ) from exc


def _configure_github_push_auth(
    state: FullRunState,
    launch_env: dict[str, str],
    *,
    grant_github_push: bool,
    parent_env: Mapping[str, str] | None = None,
) -> None:
    """Project one explicit GitHub HTTPS push capability into isolated Lane A."""
    kind = _origin_push_auth_kind(str(state.origin_url or ""))
    if kind == "local":
        if state.github_push_auth_strategy is not None:
            raise ValidationIssue(
                "full_run_github_push_auth_strategy_changed",
                "Resume origin no longer matches the staged GitHub push strategy",
            )
        if grant_github_push:
            raise ValidationIssue(
                "full_run_github_push_auth_not_applicable",
                "GitHub push projection is only valid for a GitHub HTTPS origin",
            )
        return
    if kind != "github_https":
        raise ValidationIssue(
            "full_run_git_push_transport_unsupported",
            "Isolated branch-progress pushes support local remotes or GitHub HTTPS only",
            hint="Use a non-credentialed https://github.com origin for delegated GitHub pushes",
        )

    present_names = [
        name for name in _GITHUB_PUSH_TOKEN_NAMES if launch_env.get(name)
    ]
    strategy = state.github_push_auth_strategy
    if strategy is not None and strategy not in _GITHUB_PUSH_AUTH_STRATEGIES:
        raise ValidationIssue(
            "full_run_github_push_auth_strategy_changed",
            "Persisted GitHub push strategy is invalid",
        )

    if strategy == "host_gh_token":
        token = _read_host_github_token(parent_env)
        launch_env.pop("GITHUB_TOKEN", None)
        launch_env["GH_TOKEN"] = token
        state.credential_grant_names = sorted(
            {*state.credential_grant_names, "GH_TOKEN"}
        )
    elif strategy in {"env_gh_token", "env_github_token"}:
        if grant_github_push:
            raise ValidationIssue(
                "full_run_github_push_auth_strategy_changed",
                "Resume must preserve the explicitly granted GitHub token strategy",
            )
        expected_name = (
            "GH_TOKEN" if strategy == "env_gh_token" else "GITHUB_TOKEN"
        )
        if present_names != [expected_name]:
            raise ValidationIssue(
                "full_run_github_push_auth_required",
                "Resume requires the exact originally granted GitHub token name",
            )
    elif grant_github_push:
        if present_names:
            raise ValidationIssue(
                "full_run_github_push_auth_ambiguous",
                "Choose host gh projection or one explicit GitHub token grant, not both",
            )
        token = _read_host_github_token(parent_env)
        launch_env["GH_TOKEN"] = token
        state.credential_grant_names = sorted(
            {*state.credential_grant_names, "GH_TOKEN"}
        )
        state.github_push_auth_strategy = "host_gh_token"
    else:
        if len(present_names) != 1:
            raise ValidationIssue(
                (
                    "full_run_github_push_auth_ambiguous"
                    if present_names
                    else "full_run_github_push_auth_required"
                ),
                "GitHub HTTPS branch progress requires one explicit push credential route",
                hint=(
                    "Use --grant-github-push for host gh auth, or grant exactly one "
                    "of GH_TOKEN/GITHUB_TOKEN by name"
                ),
            )
        state.github_push_auth_strategy = (
            "env_gh_token"
            if present_names[0] == "GH_TOKEN"
            else "env_github_token"
        )

    # Reset any inherited/repository helper chain, then install one helper that
    # reads the token only from the private child environment. The helper text
    # contains no credential value and terminal prompting is disabled.
    launch_env["GIT_TERMINAL_PROMPT"] = "0"
    launch_env["GIT_CONFIG_COUNT"] = "2"
    launch_env["GIT_CONFIG_KEY_0"] = "credential.https://github.com.helper"
    launch_env["GIT_CONFIG_VALUE_0"] = ""
    launch_env["GIT_CONFIG_KEY_1"] = "credential.https://github.com.helper"
    launch_env["GIT_CONFIG_VALUE_1"] = _GITHUB_CREDENTIAL_HELPER


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


def _host_ephemeral_ref(ref_name: str) -> bool:
    """Return refs maintained by the host UI, outside worker authority.

    Codex turn-diff refs are local, ephemeral snapshots created by the host
    application while a task is active. They are neither worker progress nor a
    protected repository ref, and treating their ordinary creation as worker
    tampering produces false safety wakes.
    """
    return ref_name.startswith("refs/codex/turn-diffs/")


def snapshot_protected_refs(
    repo_root: Path,
    *,
    feature_branch: str | None = None,
    include_remote: bool = True,
) -> dict[str, str]:
    """Snapshot every ref namespace except the exact assigned feature refs.

    When ``include_remote`` is False, skip the uncached remote all-ref audit
    (used by incremental healthy polls). Terminal/safety paths always pass True.
    """
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
        if (
            len(parts) == 2
            and parts[0] not in excluded
            and not _host_ephemeral_ref(parts[0])
        ):
            snaps[parts[0]] = parts[1]
    if include_remote:
        remote = _remote_refs(repo_root)
        origin_url = _canonical_origin_url(repo_root) if remote else ""
        feature_remote = (
            f"remote::origin::refs/heads/{feature_branch}" if feature_branch else None
        )
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
    include_remote: bool = True,
) -> list[str]:
    """Any observed protected-ref movement blocks readiness (policy trust, not OS sandbox)."""
    errors: list[str] = []
    current = snapshot_protected_refs(
        repo_root, feature_branch=feature_branch, include_remote=include_remote
    )
    expected_now = {
        ref: tip
        for ref, tip in expected.items()
        if include_remote or not ref.startswith("remote::")
    }
    for ref in sorted(set(current) - set(expected_now)):
        errors.append(f"new protected ref created: {ref}")
    for ref, tip in expected_now.items():
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


def _read_full_run_packet(packet_path: Path) -> bytes:
    """Read one exact packet candidate through the bounded no-follow reader."""
    try:
        return _read_bounded_regular_bytes(
            packet_path,
            max_bytes=MAX_PACKET_BYTES,
            label="full-run packet",
        )
    except StorageError as exc:
        raise ValidationIssue("full_run_packet_unreadable", exc.message) from exc


def _acceptance_criteria_from_packet(
    raw: bytes,
    *,
    packet_path: Path,
) -> list[tuple[str, str]]:
    """Parse canonical acceptance definitions from the exact staged bytes.

    Outer whitespace is syntax and is stripped once. The resulting criterion
    text is the exact report contract; workers may not swap or paraphrase it.
    A list is retained here so duplicate IDs remain observable to prepare.
    """
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationIssue(
            "full_run_packet_encoding",
            "Full-run packet must be valid UTF-8",
            path=str(packet_path),
        ) from exc
    if packet_path.suffix.lower() == ".json":
        try:
            payload = _loads_bounded_json(text, label="JSON full-run packet")
            _assert_bounded_json_structure(payload, label="JSON full-run packet")
        except (
            StorageError,
            json.JSONDecodeError,
            ValueError,
            RecursionError,
        ) as exc:
            raise ValidationIssue(
                "full_run_packet_invalid_json",
                "JSON full-run packet must contain one bounded valid object",
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
        criteria: list[tuple[str, str]] = []
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
            criteria.append((acceptance_id.strip(), criterion.strip()))
        return criteria
    # Count only canonical definition rows. Inline references and the required
    # report example may repeat ids without defining a second criterion.
    rows, syntax_issues = parse_markdown_acceptance_rows(
        text,
        require_checkbox=False,
    )
    if syntax_issues:
        issue = syntax_issues[0]
        raise ValidationIssue(
            "full_run_acceptance_syntax",
            issue.message,
            path=str(packet_path),
            hint=issue.message,
        )
    return [(row.id, row.criterion) for row in rows]


def _staged_acceptance_criteria(packet_path: Path) -> list[tuple[str, str]]:
    """Read and parse one packet without splitting identity from content."""
    return _acceptance_criteria_from_packet(
        _read_full_run_packet(packet_path),
        packet_path=packet_path,
    )


def _staged_acceptance_ids(packet_path: Path) -> list[str]:
    """Compatibility projection for callers that only need stable IDs."""
    return [item[0] for item in _staged_acceptance_criteria(packet_path)]


def _resolve_acceptance_contract_path(
    repo_root: Path,
    raw: str | Path,
    *,
    base: Path,
    label: str,
) -> Path:
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(repo_root)
    except (OSError, ValueError) as exc:
        raise ValidationIssue(
            "full_run_acceptance_contract_path_invalid",
            f"{label} must be an existing file inside the repository",
            path=str(candidate),
        ) from exc
    if not resolved.is_file():
        raise ValidationIssue(
            "full_run_acceptance_contract_path_invalid",
            f"{label} must be a regular file",
            path=str(resolved),
        )
    return resolved


def _acceptance_contract_binding(
    repo_root: Path,
    *,
    session_path: str | Path,
    plan_path: str | Path | None,
    packet_rows: Sequence[tuple[str, str]],
) -> dict[str, str]:
    """Bind the canonical plan, session, and packet before worker launch."""

    repo = Path(repo_root).expanduser().resolve(strict=True)
    session = _resolve_acceptance_contract_path(
        repo,
        session_path,
        base=repo,
        label="Elves session",
    )
    try:
        session_raw = _read_bounded_regular_bytes(
            session,
            max_bytes=MAX_PACKET_BYTES,
            label="Elves session",
            repo_root=repo,
        )
        session_data = _loads_bounded_json(
            session_raw.decode("utf-8"),
            label="Elves session",
        )
        _assert_bounded_json_structure(session_data, label="Elves session")
    except (
        StorageError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        RecursionError,
    ) as exc:
        message = exc.message if isinstance(exc, StorageError) else str(exc)
        raise ValidationIssue(
            "full_run_acceptance_session_invalid",
            f"Elves session is not bounded valid JSON: {message}",
            path=str(session),
        ) from exc
    if not isinstance(session_data, Mapping):
        raise ValidationIssue(
            "full_run_acceptance_session_invalid",
            "Elves session must contain one JSON object",
            path=str(session),
        )
    recorded_plan = session_data.get("plan_path")
    if not isinstance(recorded_plan, str) or not recorded_plan.strip():
        raise ValidationIssue(
            "full_run_acceptance_plan_missing",
            "Elves session must record a non-empty plan_path before full-run prepare",
            path=str(session),
        )
    authoritative_plan = _resolve_acceptance_contract_path(
        repo,
        recorded_plan,
        base=repo,
        label="authoritative plan",
    )
    if plan_path is not None:
        explicit_plan = _resolve_acceptance_contract_path(
            repo,
            plan_path,
            base=repo,
            label="explicit plan",
        )
        if explicit_plan != authoritative_plan:
            raise ValidationIssue(
                "full_run_acceptance_plan_mismatch",
                "Explicit plan does not match session plan_path",
                path=str(explicit_plan),
            )
    try:
        plan_raw = _read_bounded_regular_bytes(
            authoritative_plan,
            max_bytes=MAX_PACKET_BYTES,
            label="authoritative plan",
            repo_root=repo,
        )
        plan_text = plan_raw.decode("utf-8")
    except (StorageError, UnicodeDecodeError) as exc:
        message = exc.message if isinstance(exc, StorageError) else str(exc)
        raise ValidationIssue(
            "full_run_acceptance_plan_invalid",
            f"Authoritative plan is not bounded UTF-8 text: {message}",
            path=str(authoritative_plan),
        ) from exc
    contract = parse_plan_acceptance_contract(plan_text)
    if contract.issues:
        first = contract.issues[0]
        raise ValidationIssue(
            "full_run_acceptance_contract_invalid",
            first.message,
            path=str(authoritative_plan),
            hint=first.message,
        )
    if not contract.rows:
        raise ValidationIssue(
            "full_run_acceptance_contract_invalid",
            "Production full-run requires stable B#-A#/M-A# rows in the authoritative plan",
            path=str(authoritative_plan),
        )
    mapping_issues = validate_contract_mapping(
        contract.rows,
        session_data,
        plan_batch_ids=contract.batch_ids,
        packet_rows=packet_rows,
    )
    if mapping_issues:
        first = mapping_issues[0]
        raise ValidationIssue(
            "full_run_acceptance_contract_mismatch",
            first.message,
            path=str(session),
            hint=(
                "Run the active Elves skill's `acceptance_contract.py sync-session "
                f"--repo-root {repo} --session {session} --write`, then update the "
                "packet from the same plan rows."
            ),
        )
    canonical_rows = {row.id: row.criterion for row in contract.rows}
    digest_payload = json.dumps(
        {
            "plan": str(authoritative_plan),
            "plan_sha256": hashlib.sha256(plan_raw).hexdigest(),
            "session": str(session),
            "session_sha256": hashlib.sha256(session_raw).hexdigest(),
            "acceptance": canonical_rows,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "plan_path": str(authoritative_plan),
        "plan_sha256": hashlib.sha256(plan_raw).hexdigest(),
        "session_path": str(session),
        "session_sha256": hashlib.sha256(session_raw).hexdigest(),
        "contract_sha256": hashlib.sha256(digest_payload).hexdigest(),
    }


def _high_risk_checkpoints_from_packet(
    raw: bytes,
    *,
    packet_path: Path,
) -> list[str]:
    """Parse explicitly staged checkpoint IDs from the exact packet bytes."""
    try:
        text = raw.decode("utf-8")
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
        rows = payload.get("high_risk_checkpoints", []) if isinstance(payload, Mapping) else []
        if not isinstance(rows, list) or any(
            not isinstance(item, str)
            or not _HIGH_RISK_CHECKPOINT_ID_RE.fullmatch(item)
            for item in rows
        ):
            raise ValidationIssue(
                "full_run_high_risk_checkpoints_invalid",
                "JSON packet high_risk_checkpoints must be an array of stable IDs",
                path=str(packet_path),
            )
        return list(rows)
    return [
        match.group("id")
        for match in _HIGH_RISK_CHECKPOINT_DEFINITION_RE.finditer(text)
    ]


def _packet_contract_digest(
    *,
    source_path: str,
    staged_packet_identity: Mapping[str, Any],
    packet_sha256: str,
    packet_size: int,
    acceptance_criteria: Mapping[str, str],
    high_risk_checkpoints: Sequence[str],
) -> str:
    payload = {
        "source_path": source_path,
        "staged_packet_identity": dict(staged_packet_identity),
        "packet_sha256": packet_sha256,
        "packet_size": packet_size,
        "acceptance_criteria": dict(sorted(acceptance_criteria.items())),
        "high_risk_checkpoints": sorted(high_risk_checkpoints),
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _private_staged_packet_path(root: Path, source_path: Path) -> Path:
    suffix = ".json" if source_path.suffix.lower() == ".json" else ".md"
    return root / f"staged-packet{suffix}"


_STAGED_PACKET_IDENTITY_KEYS = frozenset(
    {
        "path",
        "dev",
        "ino",
        "uid",
        "mode",
        "nlink",
        "size",
        "mtime_ns",
        "ctime_ns",
    }
)


def _private_staged_packet_identity(packet_path: Path) -> dict[str, Any]:
    """Return the exact private staged-packet identity without following links."""
    candidate = Path(packet_path)
    try:
        info = candidate.lstat()
    except OSError as exc:
        raise ValidationIssue(
            "full_run_staged_packet_changed",
            "Host-private staged packet copy is missing or unavailable",
            path=str(candidate),
        ) from exc
    if (
        not candidate.is_absolute()
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise ValidationIssue(
            "full_run_staged_packet_changed",
            "Host-private staged packet copy no longer has its private regular-file identity",
            path=str(candidate),
        )
    return {
        "path": str(candidate),
        "dev": int(info.st_dev),
        "ino": int(info.st_ino),
        "uid": int(info.st_uid),
        "mode": int(stat.S_IMODE(info.st_mode)),
        "nlink": int(info.st_nlink),
        "size": int(info.st_size),
        "mtime_ns": int(info.st_mtime_ns),
        "ctime_ns": int(info.st_ctime_ns),
    }


def _write_private_packet_copy(
    repo_root: Path,
    destination: Path,
    raw: bytes,
) -> None:
    """Write the already-validated UTF-8 packet into host-private runtime state."""
    text = raw.decode("utf-8")
    try:
        with open_repo_text(
            repo_root,
            destination,
            mode="w",
            permissions=0o600,
        ) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except StorageError as exc:
        raise ValidationIssue(
            "full_run_staged_packet_write_failed",
            "Cannot persist the host-private staged packet copy",
            path=str(destination),
        ) from exc


def _read_private_packet_copy(repo_root: Path, packet_path: Path) -> bytes:
    _private_staged_packet_identity(packet_path)
    try:
        return _read_bounded_regular_bytes(
            packet_path,
            max_bytes=MAX_PACKET_BYTES,
            label="staged full-run packet",
            repo_root=repo_root,
        )
    except StorageError as exc:
        raise ValidationIssue(
            "full_run_staged_packet_changed",
            "Host-private staged packet copy cannot be revalidated",
            path=str(packet_path),
        ) from exc


def _revalidate_staged_packet_binding(
    repo_root: Path,
    state: FullRunState,
) -> Path:
    """Bind launch/reconciliation to the exact source, copy, and criteria.

    States prepared before packet binding was introduced intentionally fail
    closed here. They remain readable for diagnosis and stop operations.
    """
    source_path = Path(state.packet_path)
    expected_staged_path = _private_staged_packet_path(
        full_run_root(repo_root, state.session_id), source_path
    )
    staged_path = Path(state.staged_packet_path or "")
    criteria = state.acceptance_criteria
    checkpoints = state.planned_high_risk_checkpoints
    staged_identity = state.staged_packet_identity
    if (
        not state.staged_packet_path
        or staged_path != expected_staged_path
        or not isinstance(staged_identity, dict)
        or set(staged_identity) != _STAGED_PACKET_IDENTITY_KEYS
        or staged_identity.get("path") != str(staged_path)
        or any(
            isinstance(staged_identity.get(key), bool)
            or not isinstance(staged_identity.get(key), int)
            for key in _STAGED_PACKET_IDENTITY_KEYS - {"path"}
        )
        or not isinstance(state.packet_sha256, str)
        or not _SHA256_RE.fullmatch(state.packet_sha256)
        or isinstance(state.packet_size, bool)
        or not isinstance(state.packet_size, int)
        or state.packet_size < 0
        or not isinstance(state.packet_contract_sha256, str)
        or not _SHA256_RE.fullmatch(state.packet_contract_sha256)
        or not isinstance(criteria, dict)
        or any(
            not isinstance(key, str)
            or not _ACCEPTANCE_ID_RE.fullmatch(key)
            or not isinstance(value, str)
            or not value
            for key, value in criteria.items()
        )
        or not isinstance(state.acceptance_ids, list)
        or any(not isinstance(item, str) for item in state.acceptance_ids)
        or sorted(state.acceptance_ids) != sorted(criteria)
        or not isinstance(checkpoints, list)
        or len(checkpoints) > MAX_HIGH_RISK_CHECKPOINTS
        or len(checkpoints) != len(set(checkpoints))
        or any(
            not isinstance(item, str)
            or not _HIGH_RISK_CHECKPOINT_ID_RE.fullmatch(item)
            for item in checkpoints
        )
    ):
        raise ValidationIssue(
            "full_run_packet_binding_missing",
            "Full-run state lacks a complete immutable staged-packet binding",
            hint="Prepare a fresh full-run session before launch or reconciliation",
        )

    expected_contract_digest = _packet_contract_digest(
        source_path=str(source_path),
        staged_packet_identity=staged_identity,
        packet_sha256=state.packet_sha256,
        packet_size=state.packet_size,
        acceptance_criteria=criteria,
        high_risk_checkpoints=checkpoints,
    )
    if not hmac.compare_digest(
        expected_contract_digest,
        state.packet_contract_sha256,
    ):
        raise ValidationIssue(
            "full_run_packet_binding_changed",
            "Staged packet contract metadata changed after preparation",
        )

    source_raw = _read_full_run_packet(source_path)
    source_digest = hashlib.sha256(source_raw).hexdigest()
    if (
        len(source_raw) != state.packet_size
        or not hmac.compare_digest(source_digest, state.packet_sha256)
    ):
        raise ValidationIssue(
            "full_run_packet_source_changed",
            "Full-run source packet content changed after preparation",
            path=str(source_path),
        )

    staged_raw = _read_private_packet_copy(repo_root, staged_path)
    observed_staged_identity = _private_staged_packet_identity(staged_path)
    if observed_staged_identity != staged_identity:
        raise ValidationIssue(
            "full_run_staged_packet_changed",
            "Host-private staged packet identity changed after preparation",
            path=str(staged_path),
        )
    staged_digest = hashlib.sha256(staged_raw).hexdigest()
    if (
        len(staged_raw) != state.packet_size
        or not hmac.compare_digest(staged_digest, state.packet_sha256)
        or not hmac.compare_digest(staged_digest, source_digest)
    ):
        raise ValidationIssue(
            "full_run_staged_packet_changed",
            "Host-private staged packet content changed after preparation",
            path=str(staged_path),
        )

    parsed_rows = _acceptance_criteria_from_packet(
        staged_raw,
        packet_path=source_path,
    )
    parsed_ids = [item[0] for item in parsed_rows]
    if len(set(parsed_ids)) != len(parsed_ids) or dict(parsed_rows) != criteria:
        raise ValidationIssue(
            "full_run_packet_binding_changed",
            "Staged packet acceptance contract no longer matches prepared state",
        )
    binding_values = (
        state.acceptance_plan_path,
        state.acceptance_plan_sha256,
        state.acceptance_session_path,
        state.acceptance_session_sha256,
        state.acceptance_contract_sha256,
    )
    if state.adapter != "fixture" and not any(
        value is not None for value in binding_values
    ):
        raise ValidationIssue(
            "full_run_acceptance_contract_binding_missing",
            "Production full-run state lacks the required plan/session acceptance binding",
            hint="Prepare a fresh full-run session from the canonical Elves session before launch or reconciliation",
        )
    if any(value is not None for value in binding_values):
        if any(
            not isinstance(value, str) or not value
            for value in binding_values
        ):
            raise ValidationIssue(
                "full_run_acceptance_contract_binding_missing",
                "Prepared plan/session acceptance binding is incomplete",
            )
        observed_binding = _acceptance_contract_binding(
            repo_root,
            session_path=state.acceptance_session_path or "",
            plan_path=state.acceptance_plan_path,
            packet_rows=parsed_rows,
        )
        expected_binding = {
            "plan_path": state.acceptance_plan_path,
            "plan_sha256": state.acceptance_plan_sha256,
            "session_path": state.acceptance_session_path,
            "session_sha256": state.acceptance_session_sha256,
            "contract_sha256": state.acceptance_contract_sha256,
        }
        if observed_binding != expected_binding:
            raise ValidationIssue(
                "full_run_acceptance_contract_changed",
                "Plan/session/packet Acceptance contract changed after preparation",
                hint="Re-run full-run-prepare after reconciling the canonical session.",
            )
    parsed_checkpoints = _high_risk_checkpoints_from_packet(
        staged_raw,
        packet_path=source_path,
    )
    if (
        len(parsed_checkpoints) != len(set(parsed_checkpoints))
        or sorted(parsed_checkpoints) != sorted(checkpoints)
    ):
        raise ValidationIssue(
            "full_run_packet_binding_changed",
            "Staged packet checkpoint contract no longer matches prepared state",
        )
    return staged_path


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


def _scan_linux_proc_supervision_pids(proc_root: Path, marker_value: str) -> set[int]:
    marker = f"ELVES_FULL_RUN_SUPERVISION_MARKER={marker_value}".encode("utf-8")
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


def _scan_bsd_ps_supervision_pids(executable: Path, marker_value: str) -> set[int]:
    marker = f"ELVES_FULL_RUN_SUPERVISION_MARKER={marker_value}"
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


def _scan_supervision_pids(executable: str | Path, marker_value: str) -> set[int]:
    if not re.fullmatch(r"[0-9a-f]{64}", str(marker_value or "")):
        raise ValidationIssue(
            "full_run_supervision_scan_failed",
            "Recursive supervision marker is missing or malformed",
        )
    qualified = _qualified_process_supervisor()
    observed = Path(executable).resolve()
    if observed != qualified:
        raise ValidationIssue(
            "full_run_supervision_executable_changed",
            "Recorded recursive supervision backend is not currently qualified",
        )
    if sys.platform.startswith("linux"):
        return _scan_linux_proc_supervision_pids(qualified, marker_value)
    return _scan_bsd_ps_supervision_pids(qualified, marker_value)


def _run_supervision_canary(executable: Path) -> bool:
    marker_value = secrets.token_hex(32)
    # The canary needs only process discovery, not provider/user state.  On
    # Darwin the qualified `ps e` backend necessarily observes its environment,
    # so inheriting the host environment would copy every credential into both
    # the child and the scan buffer.
    env = {
        name: os.environ[name]
        for name in ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ")
        if os.environ.get(name)
    }
    env.setdefault("PATH", os.defpath)
    env["ELVES_FULL_RUN_SUPERVISION_MARKER"] = marker_value
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
            if proc.pid in _scan_supervision_pids(executable, marker_value):
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
    session_path: str | Path | None = None,
    plan_path: str | Path | None = None,
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
    normalized_grant_names = _normalize_credential_grant_names(
        credential_grant_names
    )
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
    if adapter_name == "devin-cli" and (not model or model == DEFAULT_MODEL):
        model = "swe-1-7-lightning"
    elif not model:
        model = DEFAULT_MODEL

    git_metadata = _validate_full_run_git_contract(
        Path(repo_root),
        worktree=worktree,
        branch=branch,
        start_head=start_head,
        packet_path=packet_path,
        adapter=adapter_name,
        prepare_phase=True,
    )

    packet_source = Path(packet_path).expanduser().resolve()
    packet_raw = _read_full_run_packet(Path(packet_path).expanduser())
    staged_acceptance_rows = _acceptance_criteria_from_packet(
        packet_raw,
        packet_path=packet_source,
    )
    staged_high_risk_checkpoints = _high_risk_checkpoints_from_packet(
        packet_raw,
        packet_path=packet_source,
    )
    staged_acceptance_ids = [item[0] for item in staged_acceptance_rows]
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
            "Full-run packet contains duplicate stable acceptance definitions",
            path=str(packet_path),
        )
    if (
        len(staged_high_risk_checkpoints) > MAX_HIGH_RISK_CHECKPOINTS
        or len(staged_high_risk_checkpoints)
        != len(set(staged_high_risk_checkpoints))
    ):
        raise ValidationIssue(
            "full_run_high_risk_checkpoints_invalid",
            "Full-run packet checkpoints must be unique and within the bounded limit",
            path=str(packet_path),
        )
    if adapter_name != "fixture":
        if not staged_acceptance_ids:
            raise ValidationIssue(
                "full_run_acceptance_ids_required",
                "Production full-run packet requires canonical B#-A#/M-A# acceptance definition rows",
                path=str(packet_path),
            )

        if session_path is None:
            default_session = Path(repo_root).expanduser().resolve() / ".elves-session.json"
            if default_session.is_file():
                session_path = default_session
            else:
                raise ValidationIssue(
                    "full_run_acceptance_session_required",
                    "Production full-run prepare requires a canonical Elves session so plan/session/packet Acceptance can be reconciled before launch",
                    path=str(default_session),
                )

    if plan_path is not None and session_path is None:
        raise ValidationIssue(
            "full_run_acceptance_session_required",
            "--plan is an equality assertion and requires the canonical --session",
            path=str(plan_path),
        )
    acceptance_binding: dict[str, str] | None = None
    if session_path is not None:
        acceptance_binding = _acceptance_contract_binding(
            Path(repo_root),
            session_path=session_path,
            plan_path=plan_path,
            packet_rows=staged_acceptance_rows,
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
    staged_packet_path = _private_staged_packet_path(root, packet_source)
    _write_private_packet_copy(Path(repo_root), staged_packet_path, packet_raw)
    staged_packet_raw = _read_private_packet_copy(
        Path(repo_root), staged_packet_path
    )
    if staged_packet_raw != packet_raw:
        raise ValidationIssue(
            "full_run_staged_packet_changed",
            "Host-private staged packet copy does not match its source",
            path=str(staged_packet_path),
        )
    staged_packet_identity = _private_staged_packet_identity(staged_packet_path)
    packet_sha256 = hashlib.sha256(packet_raw).hexdigest()
    acceptance_criteria = dict(staged_acceptance_rows)
    packet_contract_sha256 = _packet_contract_digest(
        source_path=str(packet_source),
        staged_packet_identity=staged_packet_identity,
        packet_sha256=packet_sha256,
        packet_size=len(packet_raw),
        acceptance_criteria=acceptance_criteria,
        high_risk_checkpoints=staged_high_risk_checkpoints,
    )

    if adapter_name == "fixture":
        exe = sys.executable
    else:
        default_exe = (
            "devin" if adapter_name == "devin-cli" else DEFAULT_EXECUTABLE
        )
        exe = (executable or "").strip() or default_exe
    if adapter_name == "devin-cli" and model == DEFAULT_MODEL:
        model = "swe-1-7-lightning"
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
        packet_path=str(packet_source),
        staged_packet_path=str(staged_packet_path),
        staged_packet_identity=staged_packet_identity,
        packet_sha256=packet_sha256,
        packet_size=len(packet_raw),
        packet_contract_sha256=packet_contract_sha256,
        acceptance_criteria=acceptance_criteria,
        acceptance_plan_path=(
            acceptance_binding.get("plan_path") if acceptance_binding else None
        ),
        acceptance_plan_sha256=(
            acceptance_binding.get("plan_sha256") if acceptance_binding else None
        ),
        acceptance_session_path=(
            acceptance_binding.get("session_path") if acceptance_binding else None
        ),
        acceptance_session_sha256=(
            acceptance_binding.get("session_sha256") if acceptance_binding else None
        ),
        acceptance_contract_sha256=(
            acceptance_binding.get("contract_sha256") if acceptance_binding else None
        ),
        planned_high_risk_checkpoints=sorted(staged_high_risk_checkpoints),
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
        credential_grant_names=normalized_grant_names,
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
        runtime_dir=str(root),
        provider_session_id=None,
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
    # This is the host-only stop capability used to derive the public descendant
    # marker. It is not operator telemetry; retain it only in private state.
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
    repo_root: Path,
    expected_session_id: str | None = None,
    expected_branch: str | None = None,
    expected_high_risk_checkpoints: Sequence[str] | None = None,
    seen_terminal: bool = False,
) -> None:
    errors = validate_event(
        event,
        expected_session_id=expected_session_id,
        expected_branch=expected_branch,
        expected_high_risk_checkpoints=expected_high_risk_checkpoints,
        seen_terminal=seen_terminal,
    )
    if errors:
        raise ValidationIssue("full_run_event_invalid", "; ".join(errors))
    payload = json.dumps(dict(event), separators=(",", ":")) + "\n"
    with open_repo_text(repo_root, events_path, mode="a") as handle:
        handle.write(payload)


def load_state(repo_root: Path, session_id: str) -> FullRunState:
    root = full_run_root(repo_root, session_id)
    path = root / "state.json"
    try:
        exists = repo_regular_file_exists(Path(repo_root), path)
    except StorageError as exc:
        raise ValidationIssue(
            "full_run_state_storage_unsafe",
            f"Full-run state storage is unsafe ({exc.code})",
            path=str(root),
        ) from exc
    if not exists:
        raise ValidationIssue(
            "full_run_not_found",
            "No full-run state for the requested exact session",
            path=str(root),
        )
    try:
        data = read_json(path, repo_root=Path(repo_root))
    except StorageError as exc:
        raise ValidationIssue(
            "full_run_state_storage_unsafe",
            f"Full-run state could not be read safely ({exc.code})",
            path=str(root),
        ) from exc
    try:
        assert_embedded_id(data, session_id, id_field="session_id")
    except StorageError as exc:
        raise ValidationIssue(
            "full_run_embedded_id_mismatch",
            "Full-run state embedded id does not match the requested exact session",
            path=str(root),
        ) from exc
    try:
        return FullRunState.from_dict(data)
    except (
        AttributeError,
        KeyError,
        OverflowError,
        RecursionError,
        TypeError,
        ValueError,
    ) as exc:
        raise ValidationIssue(
            "full_run_state_malformed",
            f"Full-run state schema is malformed ({type(exc).__name__})",
            path=str(root),
        ) from exc


def save_state(repo_root: Path, state: FullRunState) -> Path:
    root = full_run_root(repo_root, state.session_id)
    ensure_private_dir(root, repo_root=Path(repo_root))
    path = root / "state.json"
    atomic_write_json(path, state.to_dict(), repo_root=Path(repo_root))
    return path


def build_full_run_argv(state: FullRunState) -> list[str]:
    """Adapter-aware argv. Fixture mode uses explicit python + script + packet."""
    from .implement import detect_native_grok_goal  # noqa: PLC0415

    launch_packet = state.staged_packet_path or state.packet_path
    if state.adapter == "fixture":
        if not state.fixture_script:
            raise ValidationIssue(
                "fixture_script_required",
                "fixture adapter requires fixture_script in state",
            )
        state.goal_launch_mode = "fixture"
        return [state.executable, state.fixture_script, launch_packet]
    if state.adapter == "devin-cli":
        state.goal_launch_mode = "devin_prompt_file"
        export_path = None
        if state.runtime_dir:
            export_path = str(Path(state.runtime_dir) / "devin-export.atif")
        return build_launch_argv(
            session_id=state.provider_session_id or None,
            packet=launch_packet,
            cwd=state.worktree,
            model=state.model,
            permission_mode=state.permission_mode,
            executable=state.executable,
            create=bool(state.create_session),
            effort=state.effort,
            yolo=bool(state.yolo),
            max_turns=state.max_turns,
            output_format=None,
            adapter="devin-cli",
            check=False,
            export_path=export_path,
        )
    detection = detect_native_grok_goal(state.executable)
    # Record actual capability; do not invent flags. Headless packet launch is
    # the compatible fallback when no public --goal entrypoint exists.
    state.goal_launch_mode = str(detection.get("mode") or "headless_compatible_fallback")
    return build_launch_argv(
        session_id=state.session_id,
        packet=launch_packet,
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
        native_goal=bool(detection.get("native_goal")),
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
        STOP_REQUEST_NAME,
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
    state.credential_grant_lengths = {}
    state.credential_grant_metadata_mac = None
    state.acknowledged_high_risk_checkpoints = []
    state.pending_high_risk_checkpoint = None
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


def _capture_devin_session_id(
    state: FullRunState,
    root: Path,
    repo_root: Path,
    launch_env: Mapping[str, str],
) -> str | None:
    """Capture the exact Devin provider session id for the current worktree.

    Runs ``devin list --format json`` using the exact isolated worker env
    (XDG_CONFIG_HOME/XDG_DATA_HOME), filters to the current working_directory,
    and either requires exactly one matching session or cross-checks the
    transport-authored ATIF export's top-level ``session_id``. Rejects
    zero/multiple/mismatched candidates with a bounded diagnostic event.
    """
    exe = (state.executable or "devin").strip() or "devin"
    worktree = Path(state.worktree).expanduser().resolve()
    argv = [exe, "list", "--format", "json"]
    events_path = root / "events.jsonl"

    def _event(summary: str, etype: str = "devin_capture_failed", **extra: Any) -> None:
        _append_event(
            events_path,
            {
                "timestamp": _utc_now(),
                "session_id": state.session_id,
                "branch": state.branch,
                "head": state.head or state.start_head,
                "batch": state.batch or 0,
                "type": etype,
                "summary": summary,
                **extra,
            },
            expected_session_id=state.session_id,
            expected_branch=state.branch,
            repo_root=repo_root,
        )

    try:
        result = subprocess.run(
            argv,
            cwd=str(worktree),
            capture_output=True,
            text=True,
            timeout=15,
            env=dict(launch_env),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _event(f"devin list failed: {type(exc).__name__}")
        return None
    if result.returncode != 0:
        _event(f"devin list exited {result.returncode}")
        return None
    try:
        sessions = json.loads(result.stdout)
    except json.JSONDecodeError:
        _event("devin list returned invalid JSON")
        return None
    if not isinstance(sessions, list):
        _event("devin list returned non-list")
        return None

    candidates = [
        s
        for s in sessions
        if isinstance(s, dict)
        and s.get("working_directory")
        and Path(str(s["working_directory"])).expanduser().resolve() == worktree
        and isinstance(s.get("id"), (str, int))
    ]

    export_path = Path(state.runtime_dir or root) / "devin-export.atif"
    atif_session_id: str | None = None
    if export_path.is_file():
        try:
            atif = json.loads(export_path.read_text(encoding="utf-8"))
            if isinstance(atif, Mapping):
                atif_session_id = str(atif.get("session_id") or "").strip() or None
        except (OSError, json.JSONDecodeError):
            pass

    if atif_session_id:
        matching = [c for c in candidates if str(c.get("id")) == atif_session_id]
        if len(matching) == 1:
            selected = matching[0]
            _event(
                "Devin session captured via ATIF cross-check",
                etype="devin_session_captured",
                provider_session_id=str(selected["id"]),
                candidate_count=1,
            )
            return str(selected["id"])
        if not candidates:
            # The worker exited before the first listing; the ATIF export in the
            # private runtime directory is bound to this full-run attempt.
            _event(
                "Devin session captured from ATIF after fast worker exit",
                etype="devin_session_captured",
                provider_session_id=atif_session_id,
                candidate_count=0,
            )
            return atif_session_id
        _event(
            "ATIF session_id does not match isolated listing",
            expected_session_id=atif_session_id,
            candidate_count=len(candidates),
        )
        return None

    if len(candidates) == 1:
        selected = candidates[0]
        _event(
            "Devin session captured from isolated listing",
            etype="devin_session_captured",
            provider_session_id=str(selected["id"]),
            candidate_count=1,
        )
        return str(selected["id"])
    _event(
        f"Expected exactly one Devin session for worktree, found {len(candidates)}",
        candidate_count=len(candidates),
    )
    return None


def _prepare_resume_attempt(repo_root: Path, state: FullRunState) -> str:
    supported_adapters = {"fixture", "grok-build", "devin-cli"}
    if state.adapter not in supported_adapters:
        raise ValidationIssue(
            "full_run_resume_adapter_unsupported",
            "Production full-run resume requires a supported production adapter",
        )
    if state.adapter == "grok-build" and state.grok_auth_strategy == "oauth_shared_file":
        # Validate the canonical refresh-token authority before archiving or
        # incrementing the prior attempt. Token bytes may rotate; path identity
        # and owner-private storage may not.
        _revalidate_shared_grok_auth(state)
    if state.adapter == "devin-cli" and not state.provider_session_id:
        raise ValidationIssue(
            "full_run_devin_resume_missing_provider_session",
            "Devin full-run resume requires a captured provider session id",
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
            "Resume requires a capability-authenticated prior interruption and closed identity",
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
    grant_grok_auth: bool = False,
    grant_devin_auth: bool = False,
    grant_github_push: bool = False,
    resume: bool = False,
) -> dict[str, Any]:
    """Background-launch Grok (or explicit fixture) for one exact session.

    Never accepts KEY=VALUE secrets on argv. Credential grants are by name only.
    """
    del background  # always non-blocking Popen
    state = load_state(repo_root, session_id)
    root = full_run_root(repo_root, session_id)
    if resume:
        _revalidate_staged_packet_binding(Path(repo_root), state)
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

    # This is intentionally the last packet source used before launch. The
    # worker consumes only the private staged copy; source or copy drift blocks
    # before credential setup and before any supervisor/provider spawn.
    _revalidate_staged_packet_binding(Path(repo_root), state)
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

    effective_grant_names = _normalize_credential_grant_names(
        state.credential_grant_names
        if credential_grant_names is None
        else credential_grant_names
    )
    state.credential_grant_names = effective_grant_names
    parent_env = dict(os.environ)
    launch_env = build_full_run_env(
        state=state,
        root=root,
        parent_env=parent_env,
        credential_grant_names=effective_grant_names,
    )
    granted_names: list[str] = []

    transcript = root / "transcript.log"
    for isolated_dir in (
        root / "worker-home",
        root / "worker-home" / ".config",
        root / "worker-home" / ".cache",
        root / "worker-home" / ".local",
        root / "worker-home" / ".local" / "share",
        root / GROK_HOME_REL,
        root / "worker-tmp",
        root / "worker-tmp" / "runtime",
    ):
        ensure_private_dir(isolated_dir, repo_root=Path(repo_root))
    for stale_path in (
        root / "exit_record.json",
        root / "supervisor.fingerprint.json",
        root / STOP_REQUEST_NAME,
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
    # Shared OAuth is configured only after every non-secret preflight passes.
    # The provider still receives an isolated HOME/GROK_HOME; only Grok's native
    # auth path points at the one validated canonical refresh-token authority.
    proc: subprocess.Popen[bytes] | None = None
    try:
        if state.adapter != "fixture":
            _configure_git_commit_identity(
                state,
                launch_env,
                parent_env=parent_env,
            )
            _configure_github_push_auth(
                state,
                launch_env,
                grant_github_push=grant_github_push,
                parent_env=parent_env,
            )
        elif grant_github_push:
            raise ValidationIssue(
                "full_run_github_push_auth_not_applicable",
                "GitHub push projection is unavailable in explicit fixture mode",
            )
        if state.adapter == "grok-build":
            _configure_grok_auth(
                Path(repo_root),
                root,
                state,
                launch_env,
                grant_grok_auth=grant_grok_auth,
            )
        if state.adapter == "devin-cli":
            _configure_devin_auth(
                Path(repo_root),
                root,
                state,
                launch_env,
                grant_devin_auth=grant_devin_auth,
                parent_env=parent_env,
            )

        # Never return credential values. Names are strict environment
        # identifiers and cannot smuggle KEY=VALUE material into state/output.
        # This snapshot occurs only after both explicit auth routes have added
        # any derived launch-scoped grant.
        granted_names = sorted(
            name
            for name in state.credential_grant_names
            if name in launch_env and launch_env[name]
        )
        state.credential_granted_names = list(granted_names)
        state.credential_grant_digests = {
            name: _credential_grant_digest(state, name, launch_env[name])
            for name in granted_names
        }
        state.credential_grant_lengths = {
            name: len(launch_env[name]) for name in granted_names
        }
        state.credential_grant_metadata_mac = (
            _credential_grant_metadata_mac(state) if granted_names else None
        )

        # Recheck at the final provider-argv boundary as well as during launch
        # preflight, closing the host-side window while auth/capability probes
        # ran. No provider process exists yet.
        _revalidate_staged_packet_binding(Path(repo_root), state)
        # Build argv only after Grok auth selection has resolved and bound the
        # exact executable. The supervisor receives that same path plus the
        # identity it must recheck immediately before provider spawn.
        provider_argv = build_full_run_argv(state)
        if resume and state.adapter != "fixture":
            try:
                resume_index = provider_argv.index("--resume")
            except ValueError as exc:
                raise ValidationIssue(
                    "full_run_resume_argv_ambiguous",
                    "resume argv must contain exact --resume <session-id>",
                ) from exc
            expected_resume_id = (
                state.provider_session_id
                if state.adapter == "devin-cli"
                else state.session_id
            )
            if (
                resume_index + 1 >= len(provider_argv)
                or provider_argv[resume_index + 1] != expected_resume_id
                or "--session-id" in provider_argv
            ):
                raise ValidationIssue(
                    "full_run_resume_argv_ambiguous",
                    "resume argv is not bound to the exact captured session id",
                )
        state.last_argv = list(provider_argv)
        supervisor_argv = _provider_supervisor_argv(
            root=root,
            session_id=session_id,
            provider_argv=provider_argv,
            attempt=state.attempt,
            supervisor_executable=state.supervisor_executable,
            staged_packet_path=str(state.staged_packet_path),
            staged_packet_identity=dict(state.staged_packet_identity or {}),
            packet_sha256=str(state.packet_sha256),
            packet_size=int(state.packet_size or 0),
            provider_executable_identity=(
                state.grok_executable_identity
                if state.adapter == "grok-build"
                else None
            ),
        )

        # Open transcript for inheritance, then close the parent descriptor so
        # separate CLI invocations do not retain handles or ResourceWarnings.
        with open_repo_text(Path(repo_root), transcript, mode="a") as stdout_handle:
            proc = subprocess.Popen(
                supervisor_argv,
                cwd=state.worktree if state.adapter != "fixture" else state.worktree,
                env=launch_env,
                stdout=stdout_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE,
                start_new_session=True,
                close_fds=True,
            )

        pgid = os.getpgid(proc.pid) if hasattr(os, "getpgid") else proc.pid
        # Brief settle so Darwin proc_pidpath/ps can observe the blocked
        # supervisor identity before capture falls back to sys.executable.
        time.sleep(0.1 if sys.platform == "darwin" else 0.05)
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
        state.exit_sidecar_pid = proc.pid
        state.exit_code = None

        # The supervisor is still blocked before provider spawn. Publish every
        # launcher-owned identity and state artifact before releasing it.
        atomic_write_json(
            root / "supervisor.fingerprint.json",
            fp.to_dict(),
            repo_root=Path(repo_root),
        )
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
                "summary": "Worker launch staged in background supervisor",
            },
            expected_session_id=session_id,
            expected_branch=state.branch,
            expected_high_risk_checkpoints=state.planned_high_risk_checkpoints,
            repo_root=Path(repo_root),
        )
        save_state(repo_root, state)

    except BaseException as launch_error:
        _cleanup_refused_launch(
            Path(repo_root),
            root,
            state,
            proc,
            cause=launch_error,
        )
        raise

    assert proc is not None
    # Durable state and stop authority now exist. Invoke the irreversible
    # ownership transfer outside refused-launch cleanup: an asynchronous
    # exception at or after the complete handoff must never erase the only
    # identity capable of stopping a provider that may already have started.
    try:
        _handoff_supervision_secret(proc, state)
    except _PreTransferHandoffError as handoff_error:
        _cleanup_refused_launch(
            Path(repo_root),
            root,
            state,
            proc,
            cause=handoff_error,
        )
        raise
    except BaseException:
        # State, fingerprint, and stop capability remain durable. Detach only
        # this local Popen wrapper so propagation cannot emit a misleading
        # ResourceWarning or close over lifecycle ownership.
        try:
            proc.stdout = None
            proc.stderr = None
            proc.stdin = None
            if proc.returncode is None:
                proc.returncode = 0
        except Exception:  # noqa: BLE001
            pass
        raise
    # Drop Popen without waiting: the durable fingerprint and supervisor exit
    # record now own lifecycle tracking. Suppress intentional detach warnings.
    try:
        proc.stdout = None
        proc.stderr = None
        proc.stdin = None
        if proc.returncode is None:
            proc.returncode = 0
    except Exception:  # noqa: BLE001
        pass
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
        "grok_auth_strategy": state.grok_auth_strategy,
        "github_push_auth_strategy": state.github_push_auth_strategy,
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
    return _scan_supervision_pids(qualified, _descendant_supervision_marker(state))


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
    expected_high_risk_checkpoints: Sequence[str] | None = None,
    exact_secret_values: frozenset[str] | set[str] | tuple[str, ...] | None = None,
    credential_grant_state: FullRunState | None = None,
    shared_oauth_safe_projection: bool = False,
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
    seen_high_risk_checkpoints: set[str] = set()
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
            event = _loads_bounded_json(line, label="event")
        except (json.JSONDecodeError, ValueError, RecursionError):
            errors.append(f"line {line_no}: malformed or over-budget json")
            continue
        if not isinstance(event, dict):
            errors.append(f"line {line_no}: event must be object")
            continue
        try:
            _assert_bounded_json_structure(event, label="event")
        except StorageError as exc:
            errors.append(f"line {line_no}: {exc.message}")
            continue
        if credential_grant_state and _contains_persisted_credential_grant(
            event, credential_grant_state
        ):
            errors.append(f"line {line_no}: event contains secret-shaped content")
            continue
        validation_event = event
        if shared_oauth_safe_projection:
            validation_event = {
                key: event[key]
                for key in _SHARED_OAUTH_PUBLIC_EVENT_FIELDS
                if key in event
            }
            # Presence remains part of the schema, but OAuth worker free text
            # is neither trusted nor retained after token rotation.
            if "summary" in event:
                validation_event["summary"] = "shared OAuth event"
        verrs = validate_event(
            validation_event,
            expected_session_id=expected_session_id,
            expected_branch=expected_branch,
            seen_terminal=seen_terminal,
            expected_high_risk_checkpoints=expected_high_risk_checkpoints,
            seen_high_risk_checkpoints=tuple(seen_high_risk_checkpoints),
            exact_secret_values=exact_secret_values,
            credential_grant_state=credential_grant_state,
        )
        if verrs:
            errors.extend(f"line {line_no}: {e}" for e in verrs)
            continue
        if event.get("type") in TERMINAL_EVENT_TYPES:
            seen_terminal = True
        if event.get("type") == "high_risk_checkpoint":
            seen_high_risk_checkpoints.add(str(event.get("checkpoint_id")))
        rows.append(
            {
                key: event[key]
                for key in _SHARED_OAUTH_PUBLIC_EVENT_FIELDS
                if key in event
            }
            if shared_oauth_safe_projection
            else event
        )
    return rows, errors


def _event_log_signature(repo_root: Path, events_path: Path) -> dict[str, Any]:
    """Return a cheap identity for an append-only event log.

    A matching signature lets an ordinary healthy poll reuse the already
    validated structural summary. Terminal, checkpoint-ack, and forced-full
    paths still re-read and validate the complete log.
    """
    candidate = guard_repo_path(repo_root, events_path)
    try:
        info = candidate.lstat()
    except FileNotFoundError:
        return {"exists": False}
    except OSError as exc:
        raise StorageError(
            "event_log_unavailable",
            f"event log metadata is unavailable: {type(exc).__name__}",
        ) from exc
    if not stat.S_ISREG(info.st_mode):
        raise StorageError(
            "event_log_not_regular",
            "event log must be a regular non-symlink file",
        )
    if info.st_size > MAX_EVENT_FILE_BYTES:
        raise StorageError(
            "event_log_too_large",
            f"event log exceeds {MAX_EVENT_FILE_BYTES} byte limit",
        )
    return {
        "exists": True,
        "dev": info.st_dev,
        "ino": info.st_ino,
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
        "ctime_ns": info.st_ctime_ns,
    }


_SHARED_OAUTH_PUBLIC_EVENT_FIELDS: tuple[str, ...] = (
    "timestamp",
    "session_id",
    "branch",
    "head",
    "batch",
    "type",
    "checkpoint_id",
    "change_id",
    "change_kind",
)


def _driver_visible_events(
    events: Sequence[Mapping[str, Any]],
    *,
    shared_oauth: bool,
) -> list[dict[str, Any]]:
    """Project OAuth events onto validated structural fields only.

    Free-text summaries cannot be safely redacted after a refresh-token
    rotation because a prior opaque value is intentionally no longer stored.
    """
    if not shared_oauth:
        return [dict(event) for event in events]
    return [
        {
            key: event[key]
            for key in _SHARED_OAUTH_PUBLIC_EVENT_FIELDS
            if key in event
        }
        for event in events
    ]


def format_follow_stream_line(
    event: Mapping[str, Any],
    *,
    shared_oauth: bool = False,
) -> str:
    """Format one sanitized human-readable follow-mode line (no model inference)."""
    etype = str(event.get("type") or "event")
    ts = str(event.get("timestamp") or "")
    batch = event.get("batch")
    head = str(event.get("head") or "")
    short_head = head[:12] if head else "-"
    batch_s = f"b{batch}" if batch is not None else "b?"
    if shared_oauth:
        # Never include free-text summary under shared OAuth.
        return f"{ts} [{batch_s}] {etype} @ {short_head}"
    summary = str(event.get("summary") or "").strip()
    if len(summary) > 200:
        summary = summary[:197] + "..."
    if summary:
        return f"{ts} [{batch_s}] {etype} @ {short_head} — {summary}"
    return f"{ts} [{batch_s}] {etype} @ {short_head}"


def follow_stream_lines(
    events: Sequence[Mapping[str, Any]],
    *,
    shared_oauth: bool = False,
    already_seen: int = 0,
) -> tuple[list[str], int]:
    """Return new follow lines since ``already_seen`` and the new cursor."""
    visible = _driver_visible_events(events, shared_oauth=shared_oauth)
    cursor = max(0, int(already_seen))
    new_events = visible[cursor:]
    lines = [
        format_follow_stream_line(ev, shared_oauth=shared_oauth) for ev in new_events
    ]
    return lines, len(visible)


def _all_follow_events(repo_root: Path, state: "FullRunState") -> list[dict[str, Any]]:
    """Read the validated event sequence for an absolute follow cursor.

    ``full-run-logs`` intentionally exposes only a bounded diagnostic tail.
    Follow mode must not use that rolling window as an index: once more than
    the tail size arrives between polls, a relative cursor silently skips
    events. The validated log is already capped at ``MAX_EVENT_LINES``, so an
    absolute cursor over the complete sequence remains bounded and lossless.
    """
    launch_grants_verified, exact_secret_values = _launch_evidence_context(state)
    if not launch_grants_verified:
        return []
    events, errors = _read_events(
        full_run_root(repo_root, state.session_id) / "events.jsonl",
        expected_session_id=state.session_id,
        expected_branch=state.branch,
        expected_high_risk_checkpoints=state.planned_high_risk_checkpoints,
        exact_secret_values=exact_secret_values,
        credential_grant_state=state,
        shared_oauth_safe_projection=(state.grok_auth_strategy == "oauth_shared_file"),
        allow_partial_final=bool(state.pid or state.pgid),
        repo_root=repo_root,
    )
    if errors:
        return []
    return _driver_visible_events(
        events,
        shared_oauth=state.grok_auth_strategy == "oauth_shared_file",
    )


# Follow mode is a deterministic stream, never a model-based watcher.
FOLLOW_MODE_MODEL_INFERENCE = False
FOLLOW_MODE_REPLACES_TIMED_CHAT = True


def _driver_visible_blocker(state: FullRunState) -> str | None:
    """Return a categorical OAuth blocker without echoing worker free text."""
    if not state.blocker or state.grok_auth_strategy != "oauth_shared_file":
        return state.blocker
    categories = {
        "driver_wake_blocker": "shared OAuth worker reported a blocked state",
        "driver_wake_safety_tripwire": "shared OAuth run triggered a safety tripwire",
        "driver_wake_stale_heartbeat": "shared OAuth worker heartbeat became stale",
        "driver_wake_error": "shared OAuth run requires driver error review",
        "stopped": "shared OAuth run was stopped",
    }
    return categories.get(
        str(state.next_action or ""),
        "shared OAuth run requires driver review",
    )


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
        if not state.acceptance_criteria:
            errors.append("staged acceptance criterion binding is missing")
        else:
            observed_criteria = {
                str(item.get("id") or ""): item.get("criterion")
                for item in report.get("acceptance") or []
                if isinstance(item, Mapping)
            }
            if set(observed_criteria) != set(state.acceptance_criteria):
                errors.append("report acceptance ids do not exactly match staged criteria")
            elif any(
                observed_criteria[acceptance_id]
                != state.acceptance_criteria[acceptance_id]
                for acceptance_id in state.acceptance_criteria
            ):
                errors.append(
                    "report acceptance criterion text does not exactly match staged criteria"
                )
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
    acknowledge_high_risk_checkpoint: str | None = None,
    depth: str | None = None,
    force_full: bool = False,
) -> dict[str, Any]:
    """Classify health using fingerprint + branch head + validated events/report.

    ``depth`` may be ``incremental`` or ``full``. When omitted, healthy polls use
    incremental reconciliation (liveness + local refs + events) and terminal or
    safety wakes force full remote-audit + deep Git reconciliation.
    """
    state = load_state(repo_root, session_id)
    initial_status = state.status
    initial_next_action = state.next_action
    initial_blocker = state.blocker
    initial_completed_at = state.completed_at
    initial_pending_checkpoint = state.pending_high_risk_checkpoint
    initial_acknowledged_checkpoints = tuple(
        state.acknowledged_high_risk_checkpoints
    )
    from .risk_policy import monitor_depth_for_status  # noqa: PLC0415

    cache = dict(state.monitor_cache or {})
    remote_audit_due = True
    last_remote = cache.get("last_remote_audit_at")
    if last_remote and isinstance(last_remote, str):
        try:
            last_dt = datetime.fromisoformat(last_remote.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - last_dt).total_seconds()
            # Bounded remote all-ref cadence: half the stale window, min 60s.
            cadence = max(60, max(0, stale_after_seconds) // 2)
            remote_audit_due = age >= cadence
        except ValueError:
            remote_audit_due = True
    resolved_depth = depth or monitor_depth_for_status(
        status=state.status,
        next_action=state.next_action,
        force_full=force_full,
        remote_audit_due=remote_audit_due,
    )
    # Always full when already terminal/safety or ack is present.
    if (
        force_full
        or acknowledge_high_risk_checkpoint is not None
        or state.status in {"complete", "failed", "blocked", "stopped", "stale"}
        or (state.next_action or "").startswith("driver_wake_")
    ):
        resolved_depth = "full"
    include_remote_audit = resolved_depth == "full"
    identity_retired = bool(
        state.closed_process_identity
        and state.pid is None
        and state.pgid is None
        and state.fingerprint is None
    )
    root = full_run_root(repo_root, session_id)

    # Devin CLI does not preallocate a session id; capture the exact provider
    # UUID using the isolated worker env. Capture is attempted even if a fast
    # worker has already exited, so long as an identity has not been retired.
    if (
        state.adapter == "devin-cli"
        and not state.provider_session_id
        and not identity_retired
    ):
        attempts = int(cache.get("devin_capture_attempts") or 0)
        last_attempt = cache.get("devin_capture_last_attempt")
        now = time.monotonic()
        backoff = min(5 + attempts * 2, 60)
        if last_attempt is None or (now - float(last_attempt)) >= backoff:
            launch_env = build_full_run_env(
                state=state,
                root=root,
                parent_env=os.environ,
            )
            captured = _capture_devin_session_id(
                state, root, Path(repo_root), launch_env
            )
            cache["devin_capture_attempts"] = attempts + 1
            cache["devin_capture_last_attempt"] = now
            if captured:
                state.provider_session_id = captured
                cache["devin_capture_succeeded_at"] = now
                state.monitor_cache = cache
                save_state(repo_root, state)

    grant_context_verified, exact_secret_values = _launch_evidence_context(state)
    events_path = root / "events.jsonl"
    events_reused = False
    event_signature: dict[str, Any] | None = None
    cached_event_summary = cache.get("event_summary")
    try:
        event_signature = _event_log_signature(Path(repo_root), events_path)
    except StorageError as exc:
        event_signature = None
        events, event_errors = [], [exc.message]
    else:
        events_reused = bool(
            resolved_depth == "incremental"
            and event_signature == cache.get("event_signature")
            and isinstance(cached_event_summary, Mapping)
        )
    if event_signature is not None and events_reused:
        events, event_errors = [], []
    elif event_signature is not None and grant_context_verified:
        events, event_errors = _read_events(
            events_path,
            expected_session_id=session_id,
            expected_branch=state.branch,
            expected_high_risk_checkpoints=state.planned_high_risk_checkpoints,
            exact_secret_values=exact_secret_values,
            credential_grant_state=state,
            shared_oauth_safe_projection=(
                state.grok_auth_strategy == "oauth_shared_file"
            ),
            allow_partial_final=not identity_retired and bool(state.pid or state.pgid),
            repo_root=Path(repo_root),
        )
    elif event_signature is not None:
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
                expected_acceptance_criteria=(
                    state.acceptance_criteria
                    if state.adapter != "fixture"
                    else None
                ),
                exact_secret_values=exact_secret_values,
                credential_grant_state=state,
            )
            redacted_report = _redact_full_run_structure(
                _redact_persisted_credential_grants(report, state),
                exact_values=exact_secret_values,
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
        if not fp_ok and state.pid:
            # In embedded/API use this process may still be the supervisor's
            # parent. Reap a dead exact child before probing its former process
            # group; otherwise a zombie can make killpg(..., 0) look live
            # indefinitely and strand lifecycle finalization.
            _reap_supervisor_if_child(state.pid)
    elif state.pid and not identity_retired:
        alive = _pid_alive(state.pid)
        fp_reason = "legacy pid without fingerprint"
    group_alive = False if identity_retired else _process_group_alive(state.pgid)
    supervised_pids: set[int] = set()
    supervision_scan_ok = bool(identity_retired)
    if not identity_retired:
        try:
            supervised_pids = _supervised_alive(state)
        except ValidationIssue as issue:
            exit_record_errors = [issue.message]
        else:
            exit_record_errors = []
            supervision_scan_ok = True
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
                    supervision_scan_ok = False
                else:
                    supervision_scan_ok = True
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

    last_type = (
        cached_event_summary.get("last_type")
        if events_reused and isinstance(cached_event_summary, Mapping)
        else None
    )
    saw_run_complete_event = bool(
        events_reused
        and isinstance(cached_event_summary, Mapping)
        and cached_event_summary.get("saw_run_complete")
    )
    observed_high_risk_checkpoints: list[str] = (
        [str(item) for item in cached_event_summary.get("high_risk_checkpoints", [])]
        if events_reused and isinstance(cached_event_summary, Mapping)
        else []
    )
    observed_material_change = bool(
        events_reused
        and isinstance(cached_event_summary, Mapping)
        and cached_event_summary.get("material_scope_or_assumption_change")
    )
    event_count = (
        int(cached_event_summary.get("count") or 0)
        if events_reused and isinstance(cached_event_summary, Mapping)
        else len(events)
    )
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
            state.blocker = (
                "worker reported a blocked event"
                if state.grok_auth_strategy == "oauth_shared_file"
                else str(ev.get("summary") or "blocked")
            )
            state.next_action = "driver_wake_blocker"
        if ev.get("type") == "run_complete":
            # Lone run_complete never establishes completion — needs validated report
            # or clean provider exit with feature-branch progress.
            saw_run_complete_event = True
        if ev.get("type") == "high_risk_checkpoint":
            observed_high_risk_checkpoints.append(str(ev.get("checkpoint_id")))
        if ev.get("type") == "material_scope_or_assumption_change":
            observed_material_change = True

    if not event_errors and not events_reused and event_signature is not None:
        cache["event_signature"] = event_signature
        cache["event_summary"] = {
            "count": len(events),
            "last_type": last_type,
            "saw_run_complete": saw_run_complete_event,
            "high_risk_checkpoints": list(observed_high_risk_checkpoints),
            "material_scope_or_assumption_change": observed_material_change,
        }

    if acknowledge_high_risk_checkpoint is not None:
        checkpoint_id = str(acknowledge_high_risk_checkpoint)
        if (
            event_errors
            or not _HIGH_RISK_CHECKPOINT_ID_RE.fullmatch(checkpoint_id)
            or state.pending_high_risk_checkpoint != checkpoint_id
            or checkpoint_id not in observed_high_risk_checkpoints
            or checkpoint_id in state.acknowledged_high_risk_checkpoints
        ):
            raise ValidationIssue(
                "full_run_checkpoint_ack_invalid",
                "Checkpoint acknowledgement must match the exact pending validated event",
            )
        state.acknowledged_high_risk_checkpoints = sorted(
            {*state.acknowledged_high_risk_checkpoints, checkpoint_id}
        )
        state.pending_high_risk_checkpoint = None

    unacknowledged_high_risk_checkpoints = [
        checkpoint_id
        for checkpoint_id in observed_high_risk_checkpoints
        if checkpoint_id not in state.acknowledged_high_risk_checkpoints
    ]
    if (
        state.pending_high_risk_checkpoint is not None
        and state.pending_high_risk_checkpoint
        not in observed_high_risk_checkpoints
    ):
        event_errors.append("pending checkpoint is missing from the event log")

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
    missing_high_risk_checkpoints = [
        checkpoint_id
        for checkpoint_id in state.planned_high_risk_checkpoints
        if checkpoint_id not in observed_high_risk_checkpoints
    ]
    if (
        exit_record is not None
        and state.exit_code == 0
        and report
        and report.get("status") == "complete"
        and missing_high_risk_checkpoints
    ):
        # A packet-declared checkpoint is part of the staged execution
        # contract. A worker cannot bypass the host wake gate by simply omitting
        # the event and racing directly to a complete report.
        report_errors.append(
            "complete run omitted one or more planned high-risk checkpoints"
        )

    # Protected refs: any movement blocks readiness (policy trust, not OS sandbox).
    # Incremental healthy polls verify local refs only; remote all-ref audit runs
    # on a bounded cadence and always at terminal/safety depth.
    try:
        protected_errors = verify_protected_refs_unchanged(
            Path(repo_root),
            state.protected_refs or {},
            feature_branch=state.branch,
            include_remote=include_remote_audit,
        )
    except ValidationIssue as issue:
        protected_errors = [issue.message]
    if include_remote_audit:
        cache["last_remote_audit_at"] = _utc_now()
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

    # Invalid worker evidence is wake-worthy. Exit-record and event corruption
    # always fail hard. Report validation failures also fail hard unless the
    # report is entirely missing after a clean exit (host reconcilable path).
    clean_provider_exit = exit_record is not None and state.exit_code == 0
    if event_errors or exit_record_errors:
        state.status = "failed"
        evidence_errors = event_errors + exit_record_errors
        state.blocker = "; ".join(evidence_errors[:4]) or "untrusted worker evidence"
        state.next_action = "driver_wake_error"
    elif report_errors and not (clean_provider_exit and not report):
        # Checkpoint/head/ancestry/security report failures remain hard failures.
        state.status = "failed"
        state.blocker = "; ".join(report_errors[:4]) or "untrusted worker evidence"
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
        elif clean_exit and not protected_errors and (
            not report or report.get("status") != "complete"
        ):
            # Missing or incomplete machine report is host-reconcilable.
            # A present complete report that failed kernel validation already
            # failed hard above via report_errors.
            state.status = "blocked"
            state.blocker = (
                "provider exited cleanly without a validated complete report; "
                "host may reconstruct independently provable fields"
            )
            state.next_action = "driver_wake_reconcile"
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
        elif state.launched_at:
            state.status = "failed"
            state.blocker = "supervisor disappeared without a validated exit record"
            state.next_action = "driver_wake_error"
        elif last_type == "blocked":
            state.status = "blocked"
            state.next_action = "driver_wake_blocker"
        elif saw_run_complete_event:
            state.status = "failed"
            state.blocker = "run_complete event without validated complete report and exit"
            state.next_action = "driver_wake_error"
        else:
            # A genuinely prepared-but-not-launched session remains pending.
            state.status = "pending"
            state.next_action = "launch"

    # Planned checkpoints gate both an active run and a cleanly completed
    # provider. This closes the race where the worker emits a checkpoint and a
    # complete report before the driver's next poll. Error, blocker, stale, and
    # safety outcomes still outrank the checkpoint wake path.
    if (
        state.status in {"healthy", "complete"}
        and unacknowledged_high_risk_checkpoints
    ):
        pending = state.pending_high_risk_checkpoint
        if pending not in unacknowledged_high_risk_checkpoints:
            pending = unacknowledged_high_risk_checkpoints[0]
        state.pending_high_risk_checkpoint = pending
        state.next_action = "driver_wake_high_risk_checkpoint"
    elif state.status == "complete":
        state.pending_high_risk_checkpoint = None
        if (
            state.next_action == "driver_wake_high_risk_checkpoint"
            or initial_next_action == "driver_wake_high_risk_checkpoint"
        ):
            state.next_action = "final_readiness"
    elif state.status != "healthy":
        state.pending_high_risk_checkpoint = None

    # A worker-discovered material contract change is an explicit hand-back,
    # not ordinary progress. Keep the process/result intact and wake the driver
    # so the changed scope or assumption can be resolved before readiness.
    if state.status in {"healthy", "complete"} and observed_material_change:
        state.next_action = "driver_wake_material_scope_or_assumption_change"

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
        if unacknowledged_high_risk_checkpoints:
            state.next_action = "driver_wake_high_risk_checkpoint"
        elif (
            initial_next_action == "driver_wake_high_risk_checkpoint"
            and acknowledge_high_risk_checkpoint is not None
        ):
            state.next_action = "final_readiness"
        else:
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
    from .behavior_policy import (  # noqa: PLC0415
        PARKED_MONITOR_UPDATE_POLICY,
        PARKED_MONITOR_USER_HEARTBEAT_SECONDS,
        PARKED_MONITOR_WAKE_CONDITIONS,
        parked_monitor_poll_after_seconds,
    )

    material_state_change = bool(
        state.status != initial_status
        or state.next_action != initial_next_action
        or state.blocker != initial_blocker
        or state.completed_at != initial_completed_at
        or state.pending_high_risk_checkpoint != initial_pending_checkpoint
        or tuple(state.acknowledged_high_risk_checkpoints)
        != initial_acknowledged_checkpoints
    )
    unchanged_healthy_poll_silent = bool(
        state.status == "healthy"
        and state.next_action == "parked_monitor"
        and not material_state_change
    )
    # Healthy batch progress is silent; wakes and terminal transitions chat.
    chat_update_recommended = bool(
        material_state_change
        and not (
            state.status == "healthy" and state.next_action == "parked_monitor"
        )
    )
    state.monitor_cache = cache
    if include_remote_audit:
        cache["last_depth"] = "full"
    else:
        cache["last_depth"] = "incremental"
        cache["skipped_full_event_rescan"] = events_reused
        cache["skipped_deep_git_reconciliation"] = True
        cache["skipped_remote_all_ref_audit"] = True
    save_state(repo_root, state)

    status = {
        "ok": state.status in {"healthy", "complete", "pending"}
        or state.next_action == "driver_wake_reconcile",
        "session_id": session_id,
        "state": state.status,
        "batch": state.batch,
        "head": state.head or state.start_head,
        "branch": state.branch,
        "heartbeat_at": state.heartbeat_at,
        "pid": state.pid,
        "pgid": state.pgid,
        "next_action": state.next_action,
        "blocker": _driver_visible_blocker(state),
        "driver_contract": "parked_monitor",
        "driver_monitor_mode": "parked_monitor",
        "poll_after_seconds": parked_monitor_poll_after_seconds(stale_after_seconds),
        "user_heartbeat_seconds": PARKED_MONITOR_USER_HEARTBEAT_SECONDS,
        "chat_update_policy": PARKED_MONITOR_UPDATE_POLICY,
        "chat_update_recommended": chat_update_recommended,
        "unchanged_healthy_poll_silent": unchanged_healthy_poll_silent,
        "material_transition": material_state_change
        or state.next_action != "parked_monitor"
        or state.status != "healthy",
        "monitor_depth": resolved_depth,
        "remote_all_ref_audit": include_remote_audit,
        "goal_launch_mode": state.goal_launch_mode,
        "report_provenance": state.report_provenance,
        "wake_conditions": sorted(PARKED_MONITOR_WAKE_CONDITIONS),
        "planned_high_risk_checkpoints": list(
            state.planned_high_risk_checkpoints
        ),
        "pending_high_risk_checkpoint": state.pending_high_risk_checkpoint,
        "acknowledged_high_risk_checkpoints": list(
            state.acknowledged_high_risk_checkpoints
        ),
        "check_summary": {
            "events": event_count,
            "events_reused": events_reused,
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
            "high_risk_checkpoints_observed": len(
                observed_high_risk_checkpoints
            ),
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
        _write_supervisor_stop_request(Path(repo_root), root, state)
        # Compatibility field: true now means the capability-authenticated supervisor stop
        # channel was engaged, not that a reusable numeric PID/PGID was signaled.
        signaled = True

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
        # A Linux pidfd can safely nudge a supervisor that did not consume its
        # request. Platforms without a kernel-bound process handle fail closed;
        # never fall back to a reusable numeric PID or PGID, and never SIGKILL
        # the supervisor out from under still-live descendants.
        if pid_alive and state.fingerprint and sys.platform.startswith("linux"):
            _signal_verified_supervisor(
                state.fingerprint,
                expected_session_id=session_id,
                signum=signal.SIGTERM,
            )
        retry_deadline = time.monotonic() + 1.0
        while time.monotonic() < retry_deadline:
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
            "supervised process domain remains alive after capability-authenticated stop request"
        )
    state.completed_at = _utc_now()
    if not still_alive:
        evidence = completed_record or {
            "authority": "host_stop",
            "stop_request_delivered": signaled,
            "observed_pid_dead": True,
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
            "summary": "Capability-authenticated supervisor stop requested through private runtime channel",
        },
        expected_session_id=session_id,
        expected_branch=state.branch,
        expected_high_risk_checkpoints=state.planned_high_risk_checkpoints,
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
    launch_grants_verified, exact_secret_values = _launch_evidence_context(state)
    if launch_grants_verified:
        events, errors = _read_events(
            root / "events.jsonl",
            expected_session_id=session_id,
            expected_branch=state.branch,
            expected_high_risk_checkpoints=state.planned_high_risk_checkpoints,
            exact_secret_values=exact_secret_values,
            credential_grant_state=state,
            shared_oauth_safe_projection=(
                state.grok_auth_strategy == "oauth_shared_file"
            ),
            allow_partial_final=bool(state.pid or state.pgid),
            repo_root=Path(repo_root),
        )
    else:
        events, errors = [], [
            "launch credential context cannot be verified for worker logs"
        ]
    visible_events = _driver_visible_events(
        events[-bounded_tail:] if bounded_tail else [],
        shared_oauth=state.grok_auth_strategy == "oauth_shared_file",
    )
    payload: dict[str, Any] = {
        "ok": not errors,
        "session_id": session_id,
        "events_tail": visible_events,
        "event_errors": errors[-20:],
        "transcript_included": False,
        "merge_authority": False,
    }
    if raw_tail and state.grok_auth_strategy == "oauth_shared_file":
        payload["transcript_error"] = (
            "raw transcript unavailable for shared OAuth runs"
        )
    elif raw_tail and not launch_grants_verified:
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
            elif _text_contains_persisted_credential_grant(raw_window, state):
                payload["transcript_tail"] = ["[REDACTED:credential_grant]"]
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
    _revalidate_staged_packet_binding(Path(repo_root), state)
    launch_grants_verified, exact_secret_values = _launch_evidence_context(state)
    if not launch_grants_verified:
        raise ValidationIssue(
            "full_run_credential_context_unverified",
            "Cannot accept worker report without the exact launch credential context",
        )
    errors = validate_run_report(
        report,
        expected_session_id=session_id,
        expected_branch=state.branch,
        expected_start_head=state.start_head,
        require_complete_acceptance=report.get("status") == "complete",
        expected_run_id=_expected_run_id(session_id),
        expected_attempt=state.attempt,
        expected_acceptance_criteria=(
            state.acceptance_criteria if state.adapter != "fixture" else None
        ),
        exact_secret_values=exact_secret_values,
        credential_grant_state=state,
    )
    if errors:
        raise ValidationIssue("full_run_report_invalid", "; ".join(errors))
    # Reports never grant merge authority.
    payload = dict(report)
    payload["merge_authority"] = False
    path = full_run_root(repo_root, session_id) / "report.json"
    atomic_write_json(path, payload, repo_root=Path(repo_root))
    return path


def await_full_run(
    repo_root: Path,
    *,
    session_id: str,
    stale_after_seconds: int = DEFAULT_STALE_SECONDS,
    timeout_seconds: float | None = None,
    sleep_fn=None,
    monotonic_fn=None,
    acknowledge_high_risk_checkpoint: str | None = None,
    follow: bool = True,
    quiet: bool = False,
    stream_writer=None,
) -> dict[str, Any]:
    """Block until a material monitor transition (or timeout).

    By default follows a sanitized human-readable worker stream (no model
    inference; replaces timed driver chat updates). Pass ``quiet=True`` or
    ``follow=False`` to opt out of stream emission while still parking.

    Returns the first monitor payload that is not an unchanged healthy park.
    Designed for one host tool call instead of model-turn polling.
    """
    import time as _time  # noqa: PLC0415

    sleep = sleep_fn or _time.sleep
    mono = monotonic_fn or _time.monotonic
    started = mono()
    follow_enabled = bool(follow) and not bool(quiet)
    seen_events = 0
    seen_attempt: int | None = None
    stream_lines: list[str] = []
    write = stream_writer
    while True:
        observed = monitor_full_run(
            repo_root,
            session_id=session_id,
            stale_after_seconds=stale_after_seconds,
            acknowledge_high_risk_checkpoint=acknowledge_high_risk_checkpoint,
        )
        if follow_enabled:
            try:
                state = load_state(repo_root, session_id)
                shared_oauth = state.grok_auth_strategy == "oauth_shared_file"
                if seen_attempt != state.attempt:
                    # A supervised resume archives and resets events.jsonl.
                    seen_events = 0
                    seen_attempt = state.attempt
                events_tail = _all_follow_events(Path(repo_root), state)
                if not events_tail:
                    # Fixture/direct monitor callers may already provide a
                    # validated projection without a staged launch-evidence
                    # context. Preserve that supported path; production uses
                    # the complete absolute sequence above.
                    events_tail = observed.get("events_tail") or observed.get("events") or []
                new_lines, seen_events = follow_stream_lines(
                    events_tail if isinstance(events_tail, list) else [],
                    shared_oauth=shared_oauth,
                    already_seen=seen_events,
                )
                for line in new_lines:
                    stream_lines.append(line)
                    if write is not None:
                        write(line)
            except Exception:  # noqa: BLE001 — stream is best-effort
                pass
        material = bool(
            observed.get("material_transition")
            or not observed.get("unchanged_healthy_poll_silent")
        )
        if material:
            result = dict(observed)
            result["awaited"] = True
            result["follow"] = follow_enabled
            result["follow_model_inference"] = FOLLOW_MODE_MODEL_INFERENCE
            result["follow_replaces_timed_chat"] = FOLLOW_MODE_REPLACES_TIMED_CHAT
            result["follow_stream_lines"] = list(stream_lines)
            result["merge_authority"] = False
            return result
        elapsed = mono() - started
        if timeout_seconds is not None and elapsed >= max(0.0, timeout_seconds):
            result = dict(observed)
            result["awaited"] = True
            result["await_timed_out"] = True
            result["follow"] = follow_enabled
            result["follow_model_inference"] = FOLLOW_MODE_MODEL_INFERENCE
            result["follow_replaces_timed_chat"] = FOLLOW_MODE_REPLACES_TIMED_CHAT
            result["follow_stream_lines"] = list(stream_lines)
            result["merge_authority"] = False
            return result
        delay = float(observed.get("poll_after_seconds") or 60)
        if timeout_seconds is not None:
            delay = min(delay, max(0.0, float(timeout_seconds) - elapsed))
        sleep(delay)


@_locked_full_run
def reconstruct_missing_report(
    repo_root: Path,
    *,
    session_id: str,
    host_tests_pass: bool,
    available_facts: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Host reconstruction after clean exit without a valid worker report.

    Only independently provable fields are filled. Provenance is always
    ``host_reconstructed``. Untrusted writer handoffs are refused.
    """
    from .risk_policy import (  # noqa: PLC0415
        build_reconstructed_report,
        plan_host_reconstruction,
    )

    state = load_state(repo_root, session_id)
    worktree = Path(state.worktree)
    try:
        protected_errors = verify_protected_refs_unchanged(
            Path(repo_root),
            state.protected_refs or {},
            feature_branch=state.branch,
            include_remote=True,
        )
    except ValidationIssue as issue:
        protected_errors = [issue.message]
    origin_ok = True
    try:
        if (
            _canonical_origin_url(Path(repo_root)) != state.origin_url
            or _origin_config_digest(Path(repo_root)) != state.origin_config_digest
        ):
            origin_ok = False
    except ValidationIssue:
        origin_ok = False
    try:
        _assert_clean_worktree(worktree)
        clean_worktree = True
    except ValidationIssue:
        clean_worktree = False
    tip = _git_head(worktree) or ""
    ancestry_ok = bool(
        tip and state.start_head and _is_ancestor(worktree, state.start_head, tip)
    )
    commits = _git_commit_chain(worktree, state.start_head, tip) if ancestry_ok else []
    if ancestry_ok and not commits:
        return {
            "ok": False,
            "session_id": session_id,
            "next_action": "driver_wake_error",
            "refused_reasons": ["no_feature_branch_progress"],
            "provenance": "host_reconstructed",
        }
    commit_rows: list[dict[str, str]] = []
    for sha in commits:
        subject_result = subprocess.run(
            ["git", "-C", str(worktree), "show", "-s", "--format=%s", sha],
            capture_output=True,
            text=True,
            check=False,
        )
        if subject_result.returncode != 0:
            return {
                "ok": False,
                "session_id": session_id,
                "next_action": "driver_wake_error",
                "refused_reasons": ["commit_subject_unavailable"],
                "provenance": "host_reconstructed",
            }
        commit_rows.append(
            {"sha": sha, "subject": (subject_result.stdout or "").strip()}
        )
    acceptance_rows = [
        {
            "id": aid,
            "criterion": crit,
            "met": True,
            "evidence": "host verified from exact staged contract, Git progress, and tests",
        }
        for aid, crit in (state.acceptance_criteria or {}).items()
    ]
    batch_ids = sorted(
        {
            aid.split("-A", 1)[0]
            for aid in state.acceptance_criteria
            if "-A" in aid and not aid.startswith("M-")
        }
    ) or ["full-run"]
    default_facts: dict[str, Any] = {
        "run_id": _expected_run_id(session_id),
        "session_id": session_id,
        "branch": state.branch,
        "start_head": state.start_head,
        "final_head": tip,
        "status": "complete",
        "commits": commit_rows,
        "acceptance": acceptance_rows,
        "batches": [
            {
                "id": batch_id,
                "status": "complete",
                "evidence": "host reconstructed from exact Git ancestry and acceptance proof",
            }
            for batch_id in batch_ids
        ],
        "blockers": [],
        "remaining_risks": [],
        "docs_changed": [],
        "tests": {"host_reconstructed": True},
        "security_notes": [
            "provenance host_reconstructed; worker-only claims unknown"
        ],
    }
    facts = dict(default_facts)
    facts.update(dict(available_facts or {}))
    # Identity and Git evidence are host-derived, never caller-overridable.
    facts.update(
        {
            "run_id": _expected_run_id(session_id),
            "session_id": session_id,
            "branch": state.branch,
            "start_head": state.start_head,
            "final_head": tip,
            "status": "complete",
            "commits": commit_rows,
        }
    )
    plan = plan_host_reconstruction(
        clean_exit=state.exit_code == 0,
        ancestry_ok=ancestry_ok,
        clean_worktree=clean_worktree,
        protected_refs_ok=not protected_errors,
        origin_ok=origin_ok,
        acceptance_bound=bool(state.acceptance_criteria),
        checkpoints_satisfied=not state.planned_high_risk_checkpoints
        or set(state.planned_high_risk_checkpoints).issubset(
            set(state.acknowledged_high_risk_checkpoints)
        ),
        host_tests_pass=host_tests_pass,
        untrusted_writer=False,
        missing_security_evidence=bool(protected_errors),
        available_facts=facts,
    )
    if not plan.allowed:
        return {
            "ok": False,
            "session_id": session_id,
            "next_action": "driver_wake_error",
            "refused_reasons": list(plan.refused_reasons),
            "provenance": "host_reconstructed",
        }
    report = build_reconstructed_report(plan, facts=facts)
    # Ensure required complete-report keys.
    report.setdefault("run_id", _expected_run_id(session_id))
    report.setdefault("session_id", session_id)
    report.setdefault("branch", state.branch)
    report.setdefault("start_head", state.start_head)
    report.setdefault("final_head", tip)
    report.setdefault("status", "complete")
    report.setdefault("attempt", state.attempt)
    report.setdefault("batches", default_facts["batches"])
    report.setdefault("commits", commit_rows)
    report.setdefault("blockers", [])
    report.setdefault("docs_changed", [])
    report.setdefault("tests", {"host_reconstructed": True})
    report.setdefault(
        "security_notes",
        ["provenance host_reconstructed; worker-only claims unknown"],
    )
    if "acceptance" not in report:
        report["acceptance"] = [
            {
                "id": aid,
                "criterion": crit,
                "met": True,
                "evidence": "host_reconstructed_from_git_and_session",
            }
            for aid, crit in (state.acceptance_criteria or {}).items()
        ]
    report["provenance"] = "host_reconstructed"
    report["merge_authority"] = False
    write_report(repo_root, session_id, report)
    state.report_provenance = "host_reconstructed"
    state.status = "complete"
    state.next_action = "final_readiness"
    state.head = tip
    state.completed_at = state.completed_at or _utc_now()
    state.blocker = None
    save_state(repo_root, state)
    return {
        "ok": True,
        "session_id": session_id,
        "next_action": "final_readiness",
        "provenance": "host_reconstructed",
        "report": report,
        "unknown_fields": list(plan.unknown_fields),
    }


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
    _revalidate_staged_packet_binding(Path(repo_root), state)
    launch_grants_verified, exact_secret_values = _launch_evidence_context(state)
    if not launch_grants_verified:
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
            expected_acceptance_criteria=(
                state.acceptance_criteria if state.adapter != "fixture" else None
            ),
            exact_secret_values=exact_secret_values,
            credential_grant_state=state,
        )
        redacted_report = _redact_full_run_structure(
            _redact_persisted_credential_grants(report, state),
            exact_values=exact_secret_values,
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
        expected_high_risk_checkpoints=state.planned_high_risk_checkpoints,
        exact_secret_values=exact_secret_values,
        credential_grant_state=state,
        shared_oauth_safe_projection=(
            state.grok_auth_strategy == "oauth_shared_file"
        ),
        allow_partial_final=False,
        repo_root=Path(repo_root),
    )
    observed_high_risk_checkpoints = {
        str(event.get("checkpoint_id"))
        for event in events
        if event.get("type") == "high_risk_checkpoint"
    }
    missing_high_risk_checkpoints = set(state.planned_high_risk_checkpoints) - (
        observed_high_risk_checkpoints
    )
    unacknowledged_high_risk_checkpoints = set(
        state.planned_high_risk_checkpoints
    ) - set(state.acknowledged_high_risk_checkpoints)
    if (
        missing_high_risk_checkpoints
        or unacknowledged_high_risk_checkpoints
        or state.pending_high_risk_checkpoint is not None
    ):
        raise ValidationIssue(
            "full_run_checkpoint_incomplete",
            "Final Git reconciliation requires every planned high-risk checkpoint "
            "to be emitted and explicitly acknowledged",
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
