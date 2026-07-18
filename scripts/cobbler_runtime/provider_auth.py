"""Provider credential and launch-auth hardening (extracted from full_run.py, plan B7).

Grok auth-chain validation (owner/mode/ACL/executable identity), Devin auth
projection, credential-grant digests/MACs and redaction, launch-evidence
context, and full-run env construction. Behavior is unchanged from the
pre-extraction full_run.py; full_run re-exports every name defined here.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import hashlib
import hmac
import json
import mmap
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from .schema import ValidationIssue
from .storage import (
    StorageError,
    _assert_directory_fd_identity,
    _open_repo_directory,
    guard_repo_path,
)
from .toml_compat import loads as _load_toml

if TYPE_CHECKING:  # pragma: no cover — annotation-only; avoids a runtime cycle
    from .full_run import FullRunState


# Lazily populated ctypes ACL API cache (see _darwin_acl_api).
_DARWIN_ACL_API: tuple[Any, Any, Any, Any] | None = None


_MATERIAL_CHANGE_KINDS = frozenset({"scope", "assumption"})


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
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

# Worker confidence signal (review triage only, never authority). Optional on
# batch_complete/run_complete events and report batches[] rows: absent stays
# valid; when present the fields are validated fail-closed. An empty
# unsure_about list is a valid, complete answer, not a lazy default.
CONFIDENCE_LEVELS = frozenset({"high", "medium", "low"})
MAX_UNSURE_ABOUT_ITEMS = 16
MAX_UNSURE_ABOUT_ITEM_CHARS = 500

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
    "confidence_signal": {
        "optional": ["confidence", "unsure_about"],
        "confidence": sorted(CONFIDENCE_LEVELS),
        "unsure_about": {
            "type": "array_of_nonempty_strings",
            "max_items": MAX_UNSURE_ABOUT_ITEMS,
            "max_item_chars": MAX_UNSURE_ABOUT_ITEM_CHARS,
            "empty_list_is_valid_complete_answer": True,
        },
        "semantics": "review_triage_only_never_authority",
    },
}
_FULL_RUN_SECRET_KEY_RE = re.compile(
    r"(?i)(?:api[_-]?key|token|jwt|bearer|authorization|auth|password|passwd|"
    r"secret|credentials?|cookie|private[_-]?key)(?:_value|_header)?$"
)
MAX_GROK_AUTH_BYTES = 64 * 1024
STOP_REQUEST_NAME = "stop_request.json"
GROK_HOME_REL = Path("worker-grok-home")
GROK_AUTH_FILE_NAME = "auth.json"
GROK_AUTH_PATH_MIN_VERSION = (0, 2, 93)
MAX_GROK_EXECUTABLE_PROBE_BYTES = 512 * 1024 * 1024
MAX_DEVIN_AUTH_BYTES = 64 * 1024
DEVIN_CONFIG_FILE_NAME = "config.json"
DEVIN_CREDENTIALS_FILE_NAME = "credentials.toml"
_GROK_VERSION_RE = re.compile(r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?!\d)")
_GITHUB_PUSH_AUTH_STRATEGIES = frozenset(
    {"host_gh_token", "env_gh_token", "env_github_token"}
)
_DARWIN_ACL_TYPE_EXTENDED = 0x00000100
_DARWIN_ACL_FIRST_ENTRY = 0
_DARWIN_ACL_NEXT_ENTRY = -1
_DARWIN_ACL_EXTENDED_ALLOW = 1
_DARWIN_ACL_EXTENDED_DENY = 2
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
    # Lazy on purpose: full_run imports this module (real cycle).
    from .full_run import save_state  # noqa: PLC0415
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
    # Lazy on purpose: full_run imports this module (real cycle).
    from .full_run import _normalize_persisted_credential_grant_names  # noqa: PLC0415
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
    # Lazy on purpose: full_run imports this module (real cycle).
    from .full_run import _collision_safe_mapping_keys  # noqa: PLC0415
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
    # Lazy on purpose: full_run imports this module (real cycle).
    from .full_run import _utc_now  # noqa: PLC0415
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
    # Lazy on purpose: full_run imports this module (real cycle).
    from .full_run import _normalize_persisted_credential_grant_names  # noqa: PLC0415
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
    # Lazy on purpose: full_run imports this module (real cycle).
    from .full_run import _expected_run_id, _normalize_credential_grant_names  # noqa: PLC0415
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
