"""Pre/post writer-lease audit, binary patch export, and host apply-check.

Accepted detached commits are untrusted handoff boundaries. Export binary
patches, audit the chain, then `git apply --check --index` in the owned
worktree. Never bare-cherry-pick worker commits.

Uses `workspace_guard.classify_command` for action classification rather than
duplicating git classifiers.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .context import (
    redact_structure,
    redact_text,
    validate_credential_grant_names,
)
from .leases import (
    LeaseStore,
    WriterLease,
    hardened_git_env,
    is_path_allowed,
    run_git,
)
from .schema import ValidationIssue
from .storage import StorageError, _open_repo_directory


_AUDIT_SCAN_MAX_FILE_BYTES = 64 * 1024 * 1024
_AUDIT_SCAN_MAX_TOTAL_BYTES = 256 * 1024 * 1024
_PATCH_MANIFEST_MAX_BYTES = 4 * 1024 * 1024
_PATCH_NAME_RE = re.compile(r"^(?P<order>[0-9]{4})-.+\.patch$")
_PATCH_FROM_RE = re.compile(br"^From ([0-9a-f]{40}) ")
_AUDIT_EVIDENCE_TOKEN = object()
_HOST_APPLY_EVIDENCE_TOKEN = object()
_WORKER_GRANT_CONTEXT_VERSION = 1
_WORKER_PRE_SNAPSHOT_VERSION = 2
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_AUDITED_GIT_SURFACE_KEYS = {
    "authority",
    "git_dir",
    "git_common_dir",
    "worktree_config",
    "common_config",
    "worktree_hooks",
    "common_hooks",
    "ref_storage",
    "static_control",
    "runtime_control",
    "refs_digest",
    "remotes_digest",
}
_PRE_SNAPSHOT_KEYS = {
    "version",
    "lease_id",
    "session_id",
    "base_head",
    "worker_checkout",
    "refs_digest",
    "remotes",
    "config",
    "hooks",
    "common_config",
    "common_hooks",
    "ref_storage",
    "git_dir",
    "git_common_dir",
    "head",
    "status_porcelain",
    "authority",
    "static_control",
}
_GIT_SURFACE_MAX_ENTRIES = 100_000
_GIT_EXECUTABLE = shutil.which("git", path=os.defpath) or "/usr/bin/git"


def _hardened_git_env(
    *,
    work_tree: Path | None = None,
    git_dir: Path | None = None,
    common_dir: Path | None = None,
) -> dict[str, str]:
    """Share the one minimal Git environment with lease-state operations."""
    return hardened_git_env(
        work_tree=work_tree,
        git_dir=git_dir,
        common_dir=common_dir,
    )


def _run_git(
    cwd: Path,
    args: Sequence[str],
    *,
    check: bool = True,
    text: bool = True,
    git_dir: Path | None = None,
    common_dir: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return run_git(
        cwd,
        args,
        check=check,
        text=text,
        env=_hardened_git_env(
            work_tree=cwd if git_dir is not None else None,
            git_dir=git_dir,
            common_dir=common_dir,
        ),
    )


def _safe_git_head(
    cwd: Path,
    *,
    git_dir: Path | None = None,
    common_dir: Path | None = None,
) -> str:
    return (
        _run_git(
            cwd,
            ["rev-parse", "HEAD"],
            git_dir=git_dir,
            common_dir=common_dir,
        ).stdout
        or ""
    ).strip()


def _safe_git_status_porcelain(
    cwd: Path,
    *,
    git_dir: Path | None = None,
    common_dir: Path | None = None,
) -> str:
    return _run_git(
        cwd,
        [
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--ignore-submodules=all",
        ],
        git_dir=git_dir,
        common_dir=common_dir,
    ).stdout or ""


def _safe_refs_digest(
    cwd: Path,
    *,
    git_dir: Path | None = None,
    common_dir: Path | None = None,
) -> str:
    result = _run_git(
        cwd,
        ["for-each-ref", "--format=%(refname) %(objectname)"],
        git_dir=git_dir,
        common_dir=common_dir,
    )
    material = "\n".join(sorted((result.stdout or "").splitlines()))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def normalize_worker_credential_grant_names(
    names: Sequence[str] | None,
) -> list[str]:
    """Return one deterministic exact set of CLI environment-name grants."""
    if names is None:
        return []
    if isinstance(names, (str, bytes)):
        raise ValidationIssue(
            "worker_credential_grant_name_invalid",
            "Worker credential grants must be a sequence of environment names",
            hint="Use --grant-env XAI_API_KEY, never KEY=VALUE",
        )
    validated = validate_credential_grant_names(
        names,
        code="worker_isolation_control_grant_forbidden",
        path="worker.credential_grant_names",
    )
    return sorted(set(validated))


def _worker_grant_hmac(key: bytes, name: str, value: str) -> str:
    material = f"{name}\0{value}".encode("utf-8")
    return hmac.new(key, material, hashlib.sha256).hexdigest()


def _worker_grant_context_mac(payload: Mapping[str, Any], key: bytes) -> str:
    canonical = {
        field: payload[field]
        for field in ("version", "lease_id", "names", "lengths", "digests")
    }
    encoded = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hmac.new(
        key,
        b"elves-worker-grant-context-v1\0" + encoded,
        hashlib.sha256,
    ).hexdigest()


def worker_credential_grant_context_digest(payload: Mapping[str, Any]) -> str:
    """Digest the complete private context without exposing any grant value."""
    return hashlib.sha256(
        json.dumps(
            dict(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def build_worker_credential_grant_context(
    lease_id: str,
    names: Sequence[str] | None,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Capture private HMAC+length authority for named prepare-time grants."""
    normalized = normalize_worker_credential_grant_names(names)
    source = os.environ if environ is None else environ
    missing = [name for name in normalized if not source.get(name)]
    if missing:
        raise ValidationIssue(
            "worker_credential_grant_missing",
            "Every requested worker credential grant must be present and non-empty at prepare",
            path="worker.credential_grant_names",
        )
    key = secrets.token_bytes(32)
    values = {name: str(source[name]) for name in normalized}
    payload: dict[str, Any] = {
        "version": _WORKER_GRANT_CONTEXT_VERSION,
        "lease_id": lease_id,
        "names": normalized,
        "lengths": {name: len(values[name]) for name in normalized},
        "digests": {
            name: _worker_grant_hmac(key, name, values[name]) for name in normalized
        },
        # Private host-only authority; never returned by a public CLI payload.
        "hmac_key": key.hex(),
    }
    payload["metadata_mac"] = _worker_grant_context_mac(payload, key)
    return payload


def verify_worker_credential_grant_context(
    lease: WriterLease,
    payload: Mapping[str, Any],
    supplied_names: Sequence[str] | None,
    *,
    environ: Mapping[str, str] | None = None,
) -> frozenset[str]:
    """Verify audit-time grants against prepare authority and return exact values."""
    expected_names = normalize_worker_credential_grant_names(
        lease.credential_grant_names
    )
    actual_names = normalize_worker_credential_grant_names(supplied_names)
    if actual_names != expected_names:
        raise ValidationIssue(
            "worker_credential_grant_set_mismatch",
            "Audit must supply the exact credential grant name set recorded at prepare",
            path="worker.credential_grant_names",
        )
    expected_context_digest = str(lease.credential_grant_context_digest or "")
    if not _SHA256_RE.fullmatch(expected_context_digest) or not hmac.compare_digest(
        worker_credential_grant_context_digest(payload),
        expected_context_digest,
    ):
        raise ValidationIssue(
            "worker_credential_context_digest_mismatch",
            "Private worker credential context does not match the lease authority",
        )
    if set(payload) != {
        "version",
        "lease_id",
        "names",
        "lengths",
        "digests",
        "hmac_key",
        "metadata_mac",
    }:
        raise ValidationIssue(
            "worker_credential_context_malformed",
            "Private worker credential context fields are malformed",
        )
    if (
        payload.get("version") != _WORKER_GRANT_CONTEXT_VERSION
        or payload.get("lease_id") != lease.lease_id
        or payload.get("names") != expected_names
    ):
        raise ValidationIssue(
            "worker_credential_context_mismatch",
            "Private worker credential context identity does not match the lease",
        )
    key_hex = payload.get("hmac_key")
    if not isinstance(key_hex, str) or not _SHA256_RE.fullmatch(key_hex):
        raise ValidationIssue(
            "worker_credential_context_malformed",
            "Private worker credential HMAC authority is malformed",
        )
    key = bytes.fromhex(key_hex)
    supplied_mac = payload.get("metadata_mac")
    if not isinstance(supplied_mac, str) or not _SHA256_RE.fullmatch(supplied_mac):
        raise ValidationIssue(
            "worker_credential_context_malformed",
            "Private worker credential metadata authority is malformed",
        )
    if not hmac.compare_digest(supplied_mac, _worker_grant_context_mac(payload, key)):
        raise ValidationIssue(
            "worker_credential_context_mac_mismatch",
            "Private worker credential metadata failed authentication",
        )
    lengths = payload.get("lengths")
    digests = payload.get("digests")
    if (
        not isinstance(lengths, dict)
        or not isinstance(digests, dict)
        or set(lengths) != set(expected_names)
        or set(digests) != set(expected_names)
    ):
        raise ValidationIssue(
            "worker_credential_context_malformed",
            "Private worker credential verifier sets are malformed",
        )
    source = os.environ if environ is None else environ
    exact_values: set[str] = set()
    for name in expected_names:
        value = source.get(name)
        if not isinstance(value, str) or not value:
            raise ValidationIssue(
                "worker_credential_grant_missing_at_audit",
                "A prepare-time credential grant is unavailable at audit",
                path="worker.credential_grant_names",
            )
        length = lengths.get(name)
        digest = digests.get(name)
        if (
            isinstance(length, bool)
            or not isinstance(length, int)
            or length != len(value)
            or not isinstance(digest, str)
            or not _SHA256_RE.fullmatch(digest)
            or not hmac.compare_digest(digest, _worker_grant_hmac(key, name, value))
        ):
            raise ValidationIssue(
                "worker_credential_grant_mismatch_at_audit",
                "An audit-time credential grant does not match its prepare-time value",
                path="worker.credential_grant_names",
            )
        exact_values.add(value)
    return frozenset(exact_values)


class _HostApplyEvidence(dict[str, Any]):
    """Process-local proof that :func:`host_apply_check` completed.

    A plain mapping is deliberately insufficient to promote a lease.  The
    private constructor token keeps the authority attached to the concrete
    check that produced the payload while preserving ordinary dict/JSON APIs
    for CLI output.
    """

    __slots__ = ("__payload_digest", "__verification_token")

    def __init__(self, payload: dict[str, Any], *, _token: object) -> None:
        if _token is not _HOST_APPLY_EVIDENCE_TOKEN:
            raise TypeError("apply evidence can only be created by host_apply_check")
        super().__init__(payload)
        self.__payload_digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self.__verification_token = _token

    def _is_verified(self) -> bool:
        if self.__verification_token is not _HOST_APPLY_EVIDENCE_TOKEN:
            return False
        try:
            payload = json.dumps(
                self,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError):
            return False
        current = hashlib.sha256(payload).hexdigest()
        return current == self.__payload_digest


def _is_verified_host_apply_evidence(value: object) -> bool:
    """Return whether ``value`` is the live result of ``host_apply_check``."""
    return isinstance(value, _HostApplyEvidence) and value._is_verified()


class _AuditEvidence(dict[str, Any]):
    """Process-local proof produced only from a sealed live audit result."""

    __slots__ = ("__payload_digest", "__verification_token")

    def __init__(self, payload: dict[str, Any], *, _token: object) -> None:
        if _token is not _AUDIT_EVIDENCE_TOKEN:
            raise TypeError("audit evidence can only be created from audit_lease_turn")
        super().__init__(payload)
        self.__payload_digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self.__verification_token = _token

    def _is_verified(self) -> bool:
        if self.__verification_token is not _AUDIT_EVIDENCE_TOKEN:
            return False
        try:
            payload = json.dumps(
                self,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError):
            return False
        return hashlib.sha256(payload).hexdigest() == self.__payload_digest


def _is_verified_audit_evidence(value: object) -> bool:
    """Return whether evidence is an unmodified result of the live audit path."""
    return isinstance(value, _AuditEvidence) and value._is_verified()


def _remove_disposable_apply_checkout(path: Path) -> None:
    """Remove a private apply-check tree and fail closed on any residue."""
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise ValidationIssue(
            "host_disposable_cleanup_failed",
            "Unable to remove the disposable apply-check checkout",
            path=str(path),
            hint=f"{type(exc).__name__}: {exc}",
        ) from exc
    if os.path.lexists(path):
        raise ValidationIssue(
            "host_disposable_cleanup_failed",
            "Disposable apply-check checkout remains after cleanup",
            path=str(path),
        )


def _normalize_export_directory_path(path: Path) -> Path:
    """Normalize root-owned macOS temp aliases without following deeper paths."""
    candidate = Path(os.path.abspath(os.path.normpath(os.fspath(path.expanduser()))))
    # Normalize only the two root-owned compatibility aliases commonly returned
    # by macOS temp APIs. Deeper caller-controlled symlinks remain visible to the
    # no-follow storage traversal and are rejected.
    for alias in (Path("/tmp"), Path("/var")):
        try:
            relative = candidate.relative_to(alias)
        except ValueError:
            continue
        try:
            alias_info = alias.lstat()
            if stat.S_ISLNK(alias_info.st_mode):
                candidate = alias.resolve(strict=True) / relative
        except OSError:
            pass
        break
    return candidate


def _prepare_exclusive_export_directory(
    path: Path,
) -> tuple[Path, int, tuple[int, int]]:
    """Create and hold one private export directory without following symlinks.

    The returned descriptor is the only write authority used during export.
    Keeping it open prevents a caller from redirecting later writes by swapping
    the inspected pathname for a symlink.
    """
    candidate = _normalize_export_directory_path(path)
    try:
        existing = candidate.lstat()
    except FileNotFoundError:
        existing = None
    except OSError as exc:
        raise ValidationIssue(
            "export_dir_unsafe",
            "Cannot inspect the requested patch export directory",
            path=str(candidate),
        ) from exc
    if existing is not None and (
        stat.S_ISLNK(existing.st_mode) or not stat.S_ISDIR(existing.st_mode)
    ):
        code = (
            "export_dir_symlink"
            if stat.S_ISLNK(existing.st_mode)
            else "export_dir_unsafe"
        )
        raise ValidationIssue(
            code,
            "Patch export requires a real exclusive directory",
            path=str(candidate),
        )
    dir_fd = -1
    try:
        # Root-anchored descriptor traversal closes the ancestor-swap window
        # that a later path-based open would leave after inspection.
        candidate, dir_fd = _open_repo_directory(
            Path("/"),
            candidate,
            create=True,
            mode=0o700,
        )
        info = os.fstat(dir_fd)
    except (StorageError, OSError) as exc:
        if dir_fd >= 0:
            os.close(dir_fd)
        raise ValidationIssue(
            "export_dir_unsafe",
            "Cannot create the patch export directory without following symlinks",
            path=str(candidate),
        ) from exc
    if not stat.S_ISDIR(info.st_mode):
        os.close(dir_fd)
        raise ValidationIssue(
            "export_dir_unsafe",
            "Patch export destination is not a private regular directory",
            path=str(candidate),
        )
    try:
        path_info = candidate.lstat()
        if (
            stat.S_ISLNK(path_info.st_mode)
            or (path_info.st_dev, path_info.st_ino) != (info.st_dev, info.st_ino)
        ):
            raise ValidationIssue(
                "export_dir_identity_changed",
                "Patch export directory identity changed during preparation",
                path=str(candidate),
            )
        if os.listdir(dir_fd):
            raise ValidationIssue(
                "export_dir_not_empty",
                "Patch export directory must be exclusive and empty",
                path=str(candidate),
            )
    except ValidationIssue:
        os.close(dir_fd)
        raise
    except OSError as exc:
        os.close(dir_fd)
        raise ValidationIssue(
            "export_dir_unsafe",
            "Cannot inspect the patch export directory",
            path=str(candidate),
        ) from exc
    return candidate, dir_fd, (info.st_dev, info.st_ino)


def _write_export_file(
    dir_fd: int,
    name: str,
    data: bytes,
) -> None:
    """Write one new private regular file relative to a held directory fd."""
    if not name or Path(name).name != name or name in {".", ".."}:
        raise ValidationIssue(
            "export_filename_unsafe",
            "Patch export filenames must be plain basenames",
            path=name,
        )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    created = False
    fd = -1
    try:
        fd = os.open(name, flags, stat.S_IRUSR | stat.S_IWUSR, dir_fd=dir_fd)
        created = True
    except OSError as exc:
        raise ValidationIssue(
            "export_file_unsafe",
            "Cannot create an exclusive patch export file",
            path=name,
        ) from exc
    try:
        offset = 0
        while offset < len(data):
            written = os.write(fd, data[offset:])
            if written <= 0:  # pragma: no cover - defensive OS invariant
                raise OSError("short write while exporting patch")
            offset += written
        os.fsync(fd)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValidationIssue(
                "export_file_unsafe",
                "Patch export must remain a single-link regular file",
                path=name,
            )
        closing_fd = fd
        fd = -1
        os.close(closing_fd)
    except ValidationIssue as primary:
        if created:
            try:
                os.unlink(name, dir_fd=dir_fd)
            except OSError:
                raise ValidationIssue(
                    "export_cleanup_failed",
                    "Patch export file failed validation and could not be removed",
                    path=name,
                ) from primary
        raise
    except OSError as exc:
        if created:
            try:
                os.unlink(name, dir_fd=dir_fd)
            except OSError:
                raise ValidationIssue(
                    "export_cleanup_failed",
                    "Patch export write failed and partial output could not be removed",
                    path=name,
                ) from exc
        raise ValidationIssue(
            "export_file_write_failed",
            "Cannot write the patch export file",
            path=name,
        ) from exc
    except BaseException as primary:
        # Cancellation and interpreter-level failures are still failure paths:
        # once O_EXCL succeeds no partial leaf may escape this helper untracked.
        if created:
            try:
                os.unlink(name, dir_fd=dir_fd)
            except OSError:
                raise ValidationIssue(
                    "export_cleanup_failed",
                    "Interrupted patch export could not remove partial output",
                    path=name,
                ) from primary
        raise
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                # The primary write/validation failure is authoritative.  The
                # leaf has already been unlinked or cleanup failed closed.
                pass


def _format_patch_bytes(
    worker: Path,
    commit_sha: str,
    *,
    git_dir: Path | None = None,
    common_dir: Path | None = None,
) -> bytes:
    """Render exactly one binary-safe commit patch to stdout, never to a path."""
    if git_dir is None and common_dir is None:
        authority = snapshot_git_authority(worker)
        worker, git_dir, common_dir = _authority_paths(authority)
        snapshot_config(git_dir)
        snapshot_config(common_dir)
        snapshot_static_git_controls(git_dir, common_dir)
        snapshot_runtime_git_controls(git_dir, common_dir)
    elif git_dir is None or common_dir is None:
        raise ValidationIssue(
            "audit_git_authority_malformed",
            "format-patch requires both git-dir and common-dir authority",
        )
    proc = subprocess.run(
        [
            _GIT_EXECUTABLE,
            "format-patch",
            "--binary",
            "--no-ext-diff",
            "--no-textconv",
            "--stdout",
            "-1",
            commit_sha,
        ],
        cwd=str(worker),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_hardened_git_env(
            work_tree=worker if git_dir is not None else None,
            git_dir=git_dir,
            common_dir=common_dir,
        ),
    )
    if proc.returncode != 0:
        detail = redact_text(
            (proc.stderr or proc.stdout or b"").decode("utf-8", errors="replace")
        ).text
        raise ValidationIssue(
            "patch_format_failed",
            "git format-patch --stdout failed for the audited commit",
            hint=detail.strip()[:400],
        )
    data = bytes(proc.stdout or b"")
    header = data.splitlines(keepends=False)[0] if data else b""
    match = _PATCH_FROM_RE.match(header)
    if match is None or match.group(1).decode("ascii") != commit_sha:
        raise ValidationIssue(
            "patch_format_commit_mismatch",
            "Formatted patch header does not match the audited commit",
        )
    return data


def _read_patch_transport_bytes(
    path: Path,
    *,
    expected_digest: str | None,
) -> bytes:
    """Descriptor-read one bounded no-follow patch and verify sealed bytes."""
    candidate = _normalize_export_directory_path(Path(path))
    if candidate.name in {"", ".", ".."}:
        raise ValidationIssue(
            "apply_patch_transport_unsafe",
            "Apply patch transport path has no safe leaf name",
        )
    parent_fd = -1
    patch_fd = -1
    try:
        _parent, parent_fd = _open_repo_directory(
            Path("/"),
            candidate.parent,
            create=False,
        )
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        patch_fd = os.open(candidate.name, flags, dir_fd=parent_fd)
        info = os.fstat(patch_fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_size > _AUDIT_SCAN_MAX_FILE_BYTES
        ):
            raise ValidationIssue(
                "apply_patch_transport_unsafe",
                "Apply patch transport must be a bounded single-link regular file",
                path=str(candidate),
            )
        chunks: list[bytes] = []
        remaining = _AUDIT_SCAN_MAX_FILE_BYTES + 1
        while remaining > 0:
            chunk = os.read(patch_fd, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > _AUDIT_SCAN_MAX_FILE_BYTES:
            raise ValidationIssue(
                "apply_patch_transport_unsafe",
                "Apply patch transport exceeded the bounded read limit",
                path=str(candidate),
            )
        digest = hashlib.sha256(data).hexdigest()
        if expected_digest is not None and not hmac.compare_digest(
            digest,
            expected_digest,
        ):
            raise ValidationIssue(
                "apply_patch_transport_digest_mismatch",
                "Apply patch bytes do not match sealed audit transport authority",
                path=str(candidate),
            )
        return data
    except ValidationIssue:
        raise
    except (OSError, StorageError) as exc:
        raise ValidationIssue(
            "apply_patch_transport_unsafe",
            "Cannot descriptor-read apply patch transport without following links",
            path=str(candidate),
        ) from exc
    finally:
        if patch_fd >= 0:
            os.close(patch_fd)
        if parent_fd >= 0:
            os.close(parent_fd)


def _read_export_leaf_at(
    directory_fd: int,
    name: str,
    *,
    output_dir: Path,
    max_bytes: int,
    code: str,
) -> bytes:
    """Read one export leaf exactly once through a held no-follow dirfd."""
    if not name or Path(name).name != name or name in {".", ".."}:
        raise ValidationIssue(code, "Export leaf name is unsafe", path=name)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = -1
    try:
        fd = os.open(name, flags, dir_fd=directory_fd)
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > max_bytes
        ):
            raise ValidationIssue(
                code,
                "Export leaf must be a bounded single-link regular file",
                path=str(output_dir / name),
            )
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(fd, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(fd)
        published = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if (
            len(data) > max_bytes
            or len(data) != before.st_size
            or _git_surface_stat_tuple(before) != _git_surface_stat_tuple(after)
            or (before.st_dev, before.st_ino, before.st_mode)
            != (published.st_dev, published.st_ino, published.st_mode)
        ):
            raise ValidationIssue(
                code,
                "Export leaf changed during descriptor read",
                path=str(output_dir / name),
            )
        return data
    except ValidationIssue:
        raise
    except OSError as exc:
        raise ValidationIssue(
            code,
            "Cannot descriptor-read export leaf without following links",
            path=str(output_dir / name),
        ) from exc
    finally:
        if fd >= 0:
            os.close(fd)


def _manifest_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        if key in value:
            raise ValueError("duplicate manifest key")
        value[key] = child
    return value


def _manifest_int(raw: str) -> int:
    if len(raw.lstrip("-")) > 128:
        raise ValueError("manifest integer exceeds numeric budget")
    return int(raw)


def _load_patch_manifest_bytes(raw: bytes, path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            parse_int=_manifest_int,
            parse_float=lambda _raw: (_ for _ in ()).throw(
                ValueError("manifest floats are forbidden")
            ),
            parse_constant=lambda _raw: (_ for _ in ()).throw(
                ValueError("manifest constants are forbidden")
            ),
            object_pairs_hook=_manifest_object_pairs,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ValidationIssue(
            "patch_manifest_malformed",
            "chain.json must contain one bounded canonical JSON object",
            path=str(path),
        ) from exc
    if not isinstance(value, dict):
        raise ValidationIssue(
            "patch_manifest_malformed",
            "chain.json must contain a JSON object",
            path=str(path),
        )
    stack: list[tuple[Any, int]] = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if depth > 32 or nodes > 20_000:
            raise ValidationIssue(
                "patch_manifest_malformed",
                "chain.json exceeds structural limits",
                path=str(path),
            )
        if isinstance(item, dict):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
    return value


def _read_export_bundle_bytes(
    output_dir: Path,
    *,
    expected_directory_identity: tuple[int, int] | None = None,
) -> tuple[Path, tuple[int, int], bytes, dict[str, Any], tuple[tuple[str, bytes], ...]]:
    """Descriptor-read a manifest and every named patch once from one directory."""
    out = _normalize_export_directory_path(Path(output_dir))
    directory_fd = -1
    try:
        _out, directory_fd = _open_repo_directory(Path("/"), out, create=False)
        before = os.fstat(directory_fd)
        identity = (before.st_dev, before.st_ino)
        if not stat.S_ISDIR(before.st_mode) or (
            expected_directory_identity is not None
            and identity != expected_directory_identity
        ):
            raise ValidationIssue(
                "patch_manifest_dir_unsafe",
                "Patch export directory identity is unsafe or changed",
                path=str(out),
            )
        manifest_bytes = _read_export_leaf_at(
            directory_fd,
            "chain.json",
            output_dir=out,
            max_bytes=_PATCH_MANIFEST_MAX_BYTES,
            code="patch_manifest_unsafe",
        )
        manifest = _load_patch_manifest_bytes(manifest_bytes, out / "chain.json")
        patch_names = manifest.get("patches")
        if not isinstance(patch_names, list) or not all(
            isinstance(name, str) for name in patch_names
        ):
            raise ValidationIssue(
                "patch_manifest_malformed",
                "Manifest patches must be strings",
            )
        actual_names = sorted(os.listdir(directory_fd))
        expected_names = sorted(["chain.json", *patch_names])
        if actual_names != expected_names:
            raise ValidationIssue(
                "patch_manifest_extra_or_missing_files",
                "Patch export contains missing, duplicate, or extra files",
            )
        patch_rows: list[tuple[str, bytes]] = []
        total_bytes = 0
        for name in patch_names:
            data = _read_export_leaf_at(
                directory_fd,
                name,
                output_dir=out,
                max_bytes=_AUDIT_SCAN_MAX_FILE_BYTES,
                code="patch_manifest_patch_unsafe",
            )
            total_bytes += len(data)
            if total_bytes > _AUDIT_SCAN_MAX_TOTAL_BYTES:
                raise ValidationIssue(
                    "patch_manifest_patch_unsafe",
                    "Patch export exceeds the cumulative byte limit",
                )
            patch_rows.append((name, data))
        after = os.fstat(directory_fd)
        published = out.stat(follow_symlinks=False)
        if (
            _git_surface_stat_tuple(before) != _git_surface_stat_tuple(after)
            or (before.st_dev, before.st_ino, before.st_mode)
            != (published.st_dev, published.st_ino, published.st_mode)
        ):
            raise ValidationIssue(
                "patch_manifest_dir_unsafe",
                "Patch export directory changed during descriptor read",
                path=str(out),
            )
        return out, identity, manifest_bytes, manifest, tuple(patch_rows)
    except ValidationIssue:
        raise
    except (OSError, StorageError) as exc:
        code = (
            "patch_manifest_dir_missing"
            if isinstance(exc, StorageError) and exc.code == "not_found"
            else "patch_manifest_dir_unsafe"
        )
        raise ValidationIssue(
            code,
            "Cannot open patch export through no-follow descriptor traversal",
            path=str(out),
        ) from exc
    finally:
        if directory_fd >= 0:
            os.close(directory_fd)


def _assert_export_directory_identity(path: Path, identity: tuple[int, int]) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ValidationIssue(
            "export_dir_identity_changed",
            "Patch export directory disappeared during production",
            path=str(path),
        ) from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or (info.st_dev, info.st_ino) != identity
    ):
        raise ValidationIssue(
            "export_dir_identity_changed",
            "Patch export directory identity changed during production",
            path=str(path),
        )


def _load_workspace_guard():
    """Import workspace_guard without requiring package install."""
    scripts_dir = Path(__file__).resolve().parents[1]
    path = scripts_dir / "workspace_guard.py"
    spec = importlib.util.spec_from_file_location("workspace_guard_for_audit", path)
    if spec is None or spec.loader is None:
        raise ValidationIssue(
            "workspace_guard_missing",
            f"Unable to load workspace_guard from {path}",
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@dataclass
class CommitInfo:
    sha: str
    parents: list[str]
    tree: str
    subject: str
    author: str
    paths: list[str]

    def to_dict(self) -> dict[str, Any]:
        payload = redact_structure(asdict(self))
        return dict(payload) if isinstance(payload, dict) else asdict(self)


@dataclass(frozen=True)
class VerifiedPatchBundle:
    """One descriptor-read manifest and its exact retained patch transports."""

    output_dir: Path
    directory_identity: tuple[int, int]
    manifest: dict[str, Any]
    manifest_bytes: bytes
    patches: tuple[tuple[str, bytes], ...]

    @property
    def paths(self) -> list[Path]:
        return [self.output_dir / name for name, _data in self.patches]


@dataclass
class AuditResult:
    ok: bool
    lease_id: str
    base_head: str
    worker_tip: str
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    commit_chain: list[CommitInfo] = field(default_factory=list)
    status_porcelain: str = ""
    staged: bool = False
    refs_digest_before: str | None = None
    refs_digest_after: str | None = None
    refs_changed: bool = False
    remotes_changed: bool = False
    config_changed: bool = False
    hooks_changed: bool = False
    symlink_escapes: list[str] = field(default_factory=list)
    out_of_scope_paths: list[str] = field(default_factory=list)
    forbidden_actions_seen: list[str] = field(default_factory=list)
    process_leaks: list[str] = field(default_factory=list)
    patch_paths: list[str] = field(default_factory=list)
    audited_git_surfaces: dict[str, Any] = field(default_factory=dict)
    patch_transport_digests: list[dict[str, str]] = field(default_factory=list)
    _proof_digest: str | None = field(default=None, init=False, repr=False, compare=False)
    _proof_token: object | None = field(default=None, init=False, repr=False, compare=False)

    def _raw_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "lease_id": self.lease_id,
            "base_head": self.base_head,
            "worker_tip": self.worker_tip,
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "commit_chain": [asdict(c) for c in self.commit_chain],
            "status_porcelain": self.status_porcelain,
            "staged": self.staged,
            "refs_digest_before": self.refs_digest_before,
            "refs_digest_after": self.refs_digest_after,
            "refs_changed": self.refs_changed,
            "remotes_changed": self.remotes_changed,
            "config_changed": self.config_changed,
            "hooks_changed": self.hooks_changed,
            "symlink_escapes": list(self.symlink_escapes),
            "out_of_scope_paths": list(self.out_of_scope_paths),
            "forbidden_actions_seen": list(self.forbidden_actions_seen),
            "process_leaks": list(self.process_leaks),
            "patch_paths": list(self.patch_paths),
            "audited_git_surfaces": dict(self.audited_git_surfaces),
            "patch_transport_digests": [
                dict(item) for item in self.patch_transport_digests
            ],
            "commit_count": len(self.commit_chain),
        }

    def to_dict(
        self,
        *,
        exact_secret_values: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Return a recursively redacted public/persisted audit payload."""
        payload = redact_structure(
            self._raw_dict(),
            exact_values=tuple(exact_secret_values or ()),
        )
        if not isinstance(payload, dict):  # defensive invariant; never return raw data
            raise ValidationIssue(
                "audit_redaction_failed",
                "Audit payload redaction did not produce a mapping",
            )
        return dict(payload)

    def redact_in_place(
        self,
        *,
        exact_secret_values: Sequence[str] | None = None,
    ) -> "AuditResult":
        """Sanitize fields also consumed before ``to_dict`` serialization."""
        payload = self.to_dict(exact_secret_values=exact_secret_values)
        self.lease_id = str(payload["lease_id"])
        self.base_head = str(payload["base_head"])
        self.worker_tip = str(payload["worker_tip"])
        self.reasons = list(payload["reasons"])
        self.warnings = list(payload["warnings"])
        self.status_porcelain = str(payload["status_porcelain"])
        self.refs_digest_before = (
            str(payload["refs_digest_before"])
            if payload["refs_digest_before"] is not None
            else None
        )
        self.refs_digest_after = (
            str(payload["refs_digest_after"])
            if payload["refs_digest_after"] is not None
            else None
        )
        self.symlink_escapes = list(payload["symlink_escapes"])
        self.out_of_scope_paths = list(payload["out_of_scope_paths"])
        self.forbidden_actions_seen = list(payload["forbidden_actions_seen"])
        self.process_leaks = list(payload["process_leaks"])
        self.patch_paths = list(payload["patch_paths"])
        self.audited_git_surfaces = dict(payload["audited_git_surfaces"])
        self.patch_transport_digests = [
            dict(item) for item in payload["patch_transport_digests"]
        ]
        self.commit_chain = [CommitInfo(**item) for item in payload["commit_chain"]]
        return self

    def _seal(self) -> "AuditResult":
        payload = json.dumps(
            self._raw_dict(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self._proof_digest = hashlib.sha256(payload).hexdigest()
        self._proof_token = _AUDIT_EVIDENCE_TOKEN
        return self

    def _is_verified(self) -> bool:
        if self._proof_token is not _AUDIT_EVIDENCE_TOKEN or not self._proof_digest:
            return False
        try:
            payload = json.dumps(
                self._raw_dict(),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError):
            return False
        return hashlib.sha256(payload).hexdigest() == self._proof_digest


def build_audit_evidence(
    result: AuditResult,
    *,
    pre_snapshots: dict[str, Any] | None = None,
    exact_secret_values: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Bind promotion evidence to one exact untampered successful live audit."""
    if not isinstance(result, AuditResult) or not result._is_verified():
        raise ValidationIssue(
            "audit_result_unverified",
            "Audit evidence must come directly from an untampered audit_lease_turn result",
        )
    if result.ok is not True:
        raise ValidationIssue(
            "audit_evidence_not_ok",
            "A failed audit cannot create promotion authority",
        )
    exact_values = tuple(exact_secret_values or ())
    payload = result.to_dict(exact_secret_values=exact_values)
    if pre_snapshots is not None:
        redacted = redact_structure(pre_snapshots, exact_values=exact_values)
        if not isinstance(redacted, dict):
            raise ValidationIssue(
                "audit_snapshot_redaction_failed",
                "Audit pre-snapshot redaction did not produce a mapping",
            )
        payload["pre_snapshots"] = dict(redacted)
    canonical = {
        key: value for key, value in payload.items() if key != "evidence_digest"
    }
    payload["evidence_digest"] = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return _AuditEvidence(payload, _token=_AUDIT_EVIDENCE_TOKEN)


def _git_surface_identity(info: os.stat_result) -> dict[str, int]:
    """Return identity and type-relevant metadata for one opened Git surface."""
    return {
        "dev": int(info.st_dev),
        "ino": int(info.st_ino),
        "mode": int(info.st_mode),
        "nlink": int(info.st_nlink),
        "size": int(info.st_size),
        "mtime_ns": int(info.st_mtime_ns),
        "ctime_ns": int(info.st_ctime_ns),
    }


def _git_surface_stat_tuple(info: os.stat_result) -> tuple[int, ...]:
    return tuple(_git_surface_identity(info).values())


def _raise_git_surface_unsafe(path: Path, detail: str) -> ValidationIssue:
    return ValidationIssue(
        "git_surface_unsafe",
        f"Git control surface is unsafe: {detail}",
        path=str(path),
    )


def _stable_directory_identity(info: os.stat_result) -> dict[str, int]:
    """Identity fields that remain stable while a checkout legitimately changes."""
    return {
        "dev": int(info.st_dev),
        "ino": int(info.st_ino),
        "mode": int(info.st_mode),
        "uid": int(info.st_uid),
    }


def _snapshot_directory_authority(path: Path) -> dict[str, Any]:
    """Open one absolute directory without following any component."""
    directory_fd = -1
    try:
        _opened, directory_fd = _open_repo_directory(Path("/"), path, create=False)
        opened = os.fstat(directory_fd)
        published = path.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or stat.S_ISLNK(published.st_mode)
            or (opened.st_dev, opened.st_ino, opened.st_mode)
            != (published.st_dev, published.st_ino, published.st_mode)
        ):
            raise _raise_git_surface_unsafe(path, "directory authority changed")
        return {
            "type": "directory",
            "identity": _stable_directory_identity(opened),
        }
    except ValidationIssue:
        raise
    except (OSError, StorageError) as exc:
        raise _raise_git_surface_unsafe(
            path,
            "cannot open directory authority without following links",
        ) from exc
    finally:
        if directory_fd >= 0:
            os.close(directory_fd)


def _read_optional_git_surface_file(path: Path) -> tuple[dict[str, Any], bytes]:
    """Descriptor-read an optional metadata file and return snapshot plus bytes."""
    parent_fd = -1
    try:
        _parent, parent_fd = _open_repo_directory(Path("/"), path.parent, create=False)
        try:
            info = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return {"type": "missing"}, b""
        if stat.S_ISLNK(info.st_mode):
            raise _raise_git_surface_unsafe(path, "symbolic links are forbidden")
        return _read_git_surface_file_at(
            parent_fd,
            path.name,
            path,
            {"entries": 0, "bytes": 0},
        )
    except ValidationIssue:
        raise
    except StorageError as exc:
        if exc.code == "not_found":
            return {"type": "missing"}, b""
        raise _raise_git_surface_unsafe(
            path,
            "cannot traverse the parent directory without following links",
        ) from exc
    except OSError as exc:
        raise _raise_git_surface_unsafe(
            path,
            "cannot traverse the parent directory without following links",
        ) from exc
    finally:
        if parent_fd >= 0:
            os.close(parent_fd)


def _lexical_absolute(base: Path, raw: str, *, path: Path) -> Path:
    if not raw or "\x00" in raw:
        raise _raise_git_surface_unsafe(path, "locator target is empty or malformed")
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = base / candidate
    return Path(os.path.abspath(os.fspath(candidate)))


def _decode_one_line(data: bytes, *, path: Path) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _raise_git_surface_unsafe(path, "locator must be valid UTF-8") from exc
    stripped = text.rstrip("\r\n")
    if not stripped or "\n" in stripped or "\r" in stripped or "\x00" in stripped:
        raise _raise_git_surface_unsafe(path, "locator must contain exactly one line")
    return stripped


def snapshot_git_authority(worker_checkout: Path) -> dict[str, Any]:
    """Bind checkout, .git locator, git-dir, common-dir, and backlink files.

    This function performs no Git invocation.  It is therefore safe to run as
    the first post-worker operation, before any potentially worker-controlled
    config, attribute, hook, locator, or ref can affect a subprocess.
    """
    try:
        worker = Path(worker_checkout).resolve(strict=True)
    except OSError as exc:
        raise _raise_git_surface_unsafe(
            Path(worker_checkout),
            "checkout root is unavailable",
        ) from exc
    worker_root = _snapshot_directory_authority(worker)
    dot_git = worker / ".git"
    try:
        dot_git_info = dot_git.stat(follow_symlinks=False)
    except OSError as exc:
        raise _raise_git_surface_unsafe(dot_git, "checkout has no safe .git locator") from exc
    if stat.S_ISLNK(dot_git_info.st_mode):
        raise _raise_git_surface_unsafe(dot_git, ".git may not be a symbolic link")
    if stat.S_ISDIR(dot_git_info.st_mode):
        git_locator = _snapshot_directory_authority(dot_git)
        git_dir = dot_git
    elif stat.S_ISREG(dot_git_info.st_mode):
        git_locator, locator_bytes = _read_optional_git_surface_file(dot_git)
        locator = _decode_one_line(locator_bytes, path=dot_git)
        prefix = "gitdir: "
        if not locator.lower().startswith(prefix):
            raise _raise_git_surface_unsafe(dot_git, "invalid gitdir locator syntax")
        git_dir = _lexical_absolute(worker, locator[len(prefix) :], path=dot_git)
    else:
        raise _raise_git_surface_unsafe(dot_git, ".git must be a file or directory")

    git_dir_root = _snapshot_directory_authority(git_dir)
    commondir_path = git_dir / "commondir"
    commondir_locator, commondir_bytes = _read_optional_git_surface_file(commondir_path)
    if commondir_locator["type"] == "file":
        common_dir = _lexical_absolute(
            git_dir,
            _decode_one_line(commondir_bytes, path=commondir_path),
            path=commondir_path,
        )
    else:
        common_dir = git_dir
    common_dir_root = _snapshot_directory_authority(common_dir)

    gitdir_path = git_dir / "gitdir"
    gitdir_locator, gitdir_bytes = _read_optional_git_surface_file(gitdir_path)
    if stat.S_ISREG(dot_git_info.st_mode):
        if commondir_locator["type"] != "file" or gitdir_locator["type"] != "file":
            raise _raise_git_surface_unsafe(
                git_dir,
                "linked-worktree locator files are incomplete",
            )
        backlink = _lexical_absolute(
            git_dir,
            _decode_one_line(gitdir_bytes, path=gitdir_path),
            path=gitdir_path,
        )
        if backlink != dot_git:
            raise _raise_git_surface_unsafe(
                gitdir_path,
                "gitdir backlink does not identify this checkout",
            )

    return {
        "worker_checkout": str(worker),
        "worker_root": worker_root,
        "git_locator": git_locator,
        "git_dir": str(git_dir),
        "git_dir_root": git_dir_root,
        "common_dir": str(common_dir),
        "common_dir_root": common_dir_root,
        "commondir_locator": commondir_locator,
        "gitdir_locator": gitdir_locator,
    }


def _authority_paths(authority: Mapping[str, Any]) -> tuple[Path, Path, Path]:
    try:
        work_tree = Path(str(authority["worker_checkout"]))
        git_dir = Path(str(authority["git_dir"]))
        common_dir = Path(str(authority["common_dir"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationIssue(
            "audit_git_authority_malformed",
            "Git authority paths are incomplete",
        ) from exc
    if not all(path.is_absolute() for path in (work_tree, git_dir, common_dir)):
        raise ValidationIssue(
            "audit_git_authority_malformed",
            "Git authority paths must be absolute",
        )
    return work_tree, git_dir, common_dir


def _read_git_surface_file_at(
    parent_fd: int,
    name: str,
    path: Path,
    budget: dict[str, int],
) -> tuple[dict[str, Any], bytes]:
    """Descriptor-read one bounded, single-link Git metadata file."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = -1
    try:
        fd = os.open(name, flags, dir_fd=parent_fd)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise _raise_git_surface_unsafe(
                path,
                "expected a single-link regular file",
            )
        if before.st_size > _AUDIT_SCAN_MAX_FILE_BYTES:
            raise _raise_git_surface_unsafe(path, "file exceeds the bounded byte limit")
        budget["entries"] += 1
        budget["bytes"] += int(before.st_size)
        if (
            budget["entries"] > _GIT_SURFACE_MAX_ENTRIES
            or budget["bytes"] > _AUDIT_SCAN_MAX_TOTAL_BYTES
        ):
            raise _raise_git_surface_unsafe(path, "surface exceeds the scan budget")
        chunks: list[bytes] = []
        remaining = _AUDIT_SCAN_MAX_FILE_BYTES + 1
        while remaining > 0:
            chunk = os.read(fd, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(fd)
        published = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            len(data) > _AUDIT_SCAN_MAX_FILE_BYTES
            or len(data) != before.st_size
            or _git_surface_stat_tuple(before) != _git_surface_stat_tuple(after)
            or (before.st_dev, before.st_ino, before.st_mode)
            != (published.st_dev, published.st_ino, published.st_mode)
        ):
            raise _raise_git_surface_unsafe(path, "file changed during descriptor read")
        return (
            {
                "type": "file",
                "identity": _git_surface_identity(before),
                "sha256": hashlib.sha256(data).hexdigest(),
            },
            data,
        )
    except ValidationIssue:
        raise
    except OSError as exc:
        raise _raise_git_surface_unsafe(
            path,
            "cannot open the file without following links",
        ) from exc
    finally:
        if fd >= 0:
            os.close(fd)


def _snapshot_git_surface_file(
    path: Path,
    *,
    reject_config_includes: bool = False,
) -> dict[str, Any]:
    """Snapshot an optional Git metadata file through its held parent fd."""
    parent_fd = -1
    try:
        _parent, parent_fd = _open_repo_directory(
            Path("/"),
            path.parent,
            create=False,
        )
        try:
            info = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return {"type": "missing"}
        if stat.S_ISLNK(info.st_mode):
            raise _raise_git_surface_unsafe(path, "symbolic links are forbidden")
        row, data = _read_git_surface_file_at(
            parent_fd,
            path.name,
            path,
            {"entries": 0, "bytes": 0},
        )
        if reject_config_includes:
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise _raise_git_surface_unsafe(
                    path,
                    "Git config must be valid UTF-8 for include validation",
                ) from exc
            _validate_nonexecuting_git_config(text, path=path)
        return row
    except ValidationIssue:
        raise
    except StorageError as exc:
        if exc.code == "not_found":
            return {"type": "missing"}
        raise _raise_git_surface_unsafe(
            path,
            "cannot traverse the parent directory without following links",
        ) from exc
    except OSError as exc:
        raise _raise_git_surface_unsafe(
            path,
            "cannot traverse the parent directory without following links",
        ) from exc
    finally:
        if parent_fd >= 0:
            os.close(parent_fd)


def _validate_nonexecuting_git_config(text: str, *, path: Path) -> None:
    """Reject config delegation and command-bearing filter/diff drivers."""
    section = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        section_match = re.match(
            r'^\[\s*([A-Za-z0-9.-]+)(?:\s+"(?:[^"\\]|\\.)*")?\s*\]'
            r'(?:\s*[#;].*)?$',
            line,
        )
        if section_match:
            section = section_match.group(1).lower()
            if section in {"include", "includeif"}:
                raise _raise_git_surface_unsafe(
                    path,
                    "Git config include/includeIf delegation is forbidden",
                )
            if section == "filter" or section.startswith("filter."):
                raise _raise_git_surface_unsafe(
                    path,
                    "Git clean/smudge/process filters are forbidden",
                )
            continue
        if line.startswith("["):
            raise _raise_git_surface_unsafe(
                path,
                "Git config contains an unparseable section header",
            )
        key = re.split(r"\s*=\s*|\s+", line, maxsplit=1)[0].lower()
        if (section == "diff" or section.startswith("diff.")) and key in {
            "command",
            "textconv",
            "external",
        }:
            raise _raise_git_surface_unsafe(
                path,
                "executable Git diff drivers are forbidden",
            )


def _snapshot_git_surface_directory_fd(
    directory_fd: int,
    path: Path,
    budget: dict[str, int],
) -> dict[str, Any]:
    before = os.fstat(directory_fd)
    if not stat.S_ISDIR(before.st_mode):
        raise _raise_git_surface_unsafe(path, "expected a directory")
    budget["entries"] += 1
    if budget["entries"] > _GIT_SURFACE_MAX_ENTRIES:
        raise _raise_git_surface_unsafe(path, "surface exceeds the entry budget")
    entries: dict[str, Any] = {}
    try:
        names = sorted(os.listdir(directory_fd))
    except OSError as exc:
        raise _raise_git_surface_unsafe(path, "cannot enumerate directory") from exc
    for name in names:
        child_path = path / name
        try:
            info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except OSError as exc:
            raise _raise_git_surface_unsafe(child_path, "cannot inspect entry") from exc
        if stat.S_ISLNK(info.st_mode):
            raise _raise_git_surface_unsafe(child_path, "symbolic links are forbidden")
        if stat.S_ISREG(info.st_mode):
            entries[name], _data = _read_git_surface_file_at(
                directory_fd,
                name,
                child_path,
                budget,
            )
            continue
        if not stat.S_ISDIR(info.st_mode):
            raise _raise_git_surface_unsafe(
                child_path,
                "only regular files and directories are permitted",
            )
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_DIRECTORY", 0)
        child_fd = -1
        try:
            child_fd = os.open(name, flags, dir_fd=directory_fd)
            opened = os.fstat(child_fd)
            if (opened.st_dev, opened.st_ino, opened.st_mode) != (
                info.st_dev,
                info.st_ino,
                info.st_mode,
            ):
                raise _raise_git_surface_unsafe(child_path, "directory identity changed")
            entries[name] = _snapshot_git_surface_directory_fd(
                child_fd,
                child_path,
                budget,
            )
        except ValidationIssue:
            raise
        except OSError as exc:
            raise _raise_git_surface_unsafe(
                child_path,
                "cannot open directory without following links",
            ) from exc
        finally:
            if child_fd >= 0:
                os.close(child_fd)
    after = os.fstat(directory_fd)
    published = path.stat(follow_symlinks=False)
    if (
        _git_surface_stat_tuple(before) != _git_surface_stat_tuple(after)
        or (before.st_dev, before.st_ino, before.st_mode)
        != (published.st_dev, published.st_ino, published.st_mode)
    ):
        raise _raise_git_surface_unsafe(path, "directory changed during snapshot")
    return {
        "type": "directory",
        "identity": _git_surface_identity(before),
        "entries": entries,
    }


def _snapshot_git_surface_directory(path: Path) -> dict[str, Any]:
    """Snapshot an optional Git metadata tree with no-follow traversal."""
    try:
        info = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return {"type": "missing"}
    except OSError as exc:
        raise _raise_git_surface_unsafe(path, "cannot inspect directory") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise _raise_git_surface_unsafe(path, "expected a non-symlink directory")
    directory_fd = -1
    try:
        _directory, directory_fd = _open_repo_directory(Path("/"), path, create=False)
        return _snapshot_git_surface_directory_fd(
            directory_fd,
            path,
            {"entries": 0, "bytes": 0},
        )
    except ValidationIssue:
        raise
    except (OSError, StorageError) as exc:
        raise _raise_git_surface_unsafe(
            path,
            "cannot traverse directory without following links",
        ) from exc
    finally:
        if directory_fd >= 0:
            os.close(directory_fd)


def snapshot_hooks(git_dir: Path) -> dict[str, Any]:
    """Bind the hook directory, entry types, identities, and exact bytes."""
    return _snapshot_git_surface_directory(git_dir / "hooks")


def snapshot_config(git_dir: Path) -> dict[str, Any]:
    """Bind both ordinary and per-worktree Git config control files."""
    return {
        "config": _snapshot_git_surface_file(
            git_dir / "config",
            reject_config_includes=True,
        ),
        "config.worktree": _snapshot_git_surface_file(
            git_dir / "config.worktree",
            reject_config_includes=True,
        ),
    }


def snapshot_static_git_controls(git_dir: Path, common_dir: Path) -> dict[str, Any]:
    """Bind object redirection, shallow, attribute, exclude, and sparse controls."""
    relative_paths = (
        "shallow",
        "info/grafts",
        "info/attributes",
        "info/exclude",
        "info/sparse-checkout",
        "objects/info/alternates",
        "objects/info/http-alternates",
    )

    def one_root(root: Path) -> dict[str, Any]:
        return {
            relative: _snapshot_git_surface_file(root / relative)
            for relative in relative_paths
        }

    return {"worktree": one_root(git_dir), "common": one_root(common_dir)}


def _snapshot_shared_indexes(git_dir: Path) -> dict[str, Any]:
    directory_fd = -1
    try:
        _opened, directory_fd = _open_repo_directory(Path("/"), git_dir, create=False)
        names = sorted(
            name for name in os.listdir(directory_fd) if name.startswith("sharedindex.")
        )
        if len(names) > _GIT_SURFACE_MAX_ENTRIES:
            raise _raise_git_surface_unsafe(
                git_dir,
                "split-index authority exceeds the entry budget",
            )
        if any(name.endswith(".lock") for name in names):
            raise _raise_git_surface_unsafe(
                git_dir,
                "split-index lock files are present",
            )
        budget = {"entries": 0, "bytes": 0}
        return {
            name: _read_git_surface_file_at(
                directory_fd,
                name,
                git_dir / name,
                budget,
            )[0]
            for name in names
        }
    except ValidationIssue:
        raise
    except (OSError, StorageError) as exc:
        raise _raise_git_surface_unsafe(
            git_dir,
            "cannot snapshot split-index authority",
        ) from exc
    finally:
        if directory_fd >= 0:
            os.close(directory_fd)


def _snapshot_runtime_git_controls_exact(
    git_dir: Path,
    common_dir: Path,
) -> dict[str, Any]:
    """Descriptor-bind mutable index bytes for an in-operation race check."""
    index = _snapshot_git_surface_file(git_dir / "index")
    if index.get("type") != "file":
        raise _raise_git_surface_unsafe(git_dir / "index", "index must be a regular file")
    lock_paths = {
        "index.lock": git_dir / "index.lock",
        "HEAD.lock": git_dir / "HEAD.lock",
        "config.lock": git_dir / "config.lock",
        "config.worktree.lock": git_dir / "config.worktree.lock",
        "shallow.lock": git_dir / "shallow.lock",
        "common.config.lock": common_dir / "config.lock",
        "common.HEAD.lock": common_dir / "HEAD.lock",
        "common.packed-refs.lock": common_dir / "packed-refs.lock",
        "common.shallow.lock": common_dir / "shallow.lock",
    }
    for pseudoref in (
        "ORIG_HEAD",
        "MERGE_HEAD",
        "CHERRY_PICK_HEAD",
        "REVERT_HEAD",
        "BISECT_HEAD",
        "AUTO_MERGE",
        "REBASE_HEAD",
    ):
        lock_paths[f"worktree.{pseudoref}.lock"] = git_dir / f"{pseudoref}.lock"
        lock_paths[f"common.{pseudoref}.lock"] = common_dir / f"{pseudoref}.lock"
    locks = {name: _snapshot_git_surface_file(path) for name, path in lock_paths.items()}
    present = [name for name, node in locks.items() if node.get("type") != "missing"]
    if present:
        raise _raise_git_surface_unsafe(
            git_dir,
            "Git lock files are present: " + ", ".join(present),
        )
    return {
        "index": index,
        "shared_indexes": _snapshot_shared_indexes(git_dir),
        "locks": locks,
    }


def _runtime_control_contract(exact: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "index": {"type": "file"},
        "shared_indexes": {
            name: {"type": "file", "sha256": node["sha256"]}
            for name, node in exact["shared_indexes"].items()
        },
        "locks": {name: {"type": "missing"} for name in exact["locks"]},
    }


def _snapshot_index_semantics(
    worker: Path,
    git_dir: Path,
    common_dir: Path,
) -> dict[str, Any]:
    """Seal index flags that can make tracked worktree changes invisible."""
    outputs: dict[str, bytes] = {}
    entry_count: int | None = None
    for label, flag in (("assume_skip", "-v"), ("fsmonitor", "-f")):
        proc = _run_git(
            worker,
            ["ls-files", flag, "-z", "--"],
            text=False,
            git_dir=git_dir,
            common_dir=common_dir,
        )
        raw = bytes(proc.stdout or b"")
        records = [record for record in raw.split(b"\0") if record]
        if entry_count is None:
            entry_count = len(records)
        elif len(records) != entry_count:
            raise ValidationIssue(
                "git_index_flags_unsafe",
                "Git index flag views disagree on the tracked entry count",
            )
        for record in records:
            if len(record) < 3 or record[1:2] != b" " or record[:1] != b"H":
                raise ValidationIssue(
                    "git_index_flags_unsafe",
                    "Git index contains hidden, sparse, conflicted, or fsmonitor-valid entries",
                )
        outputs[label] = raw
    return {
        "version": 1,
        "entries": entry_count or 0,
        "assume_skip_sha256": hashlib.sha256(outputs["assume_skip"]).hexdigest(),
        "fsmonitor_sha256": hashlib.sha256(outputs["fsmonitor"]).hexdigest(),
    }


def snapshot_runtime_git_controls(git_dir: Path, common_dir: Path) -> dict[str, Any]:
    """Return the durable type contract; semantic index tree is added by Git."""
    return _runtime_control_contract(
        _snapshot_runtime_git_controls_exact(git_dir, common_dir)
    )


def snapshot_ref_storage(
    git_dir: Path,
    common_dir: Path,
    *,
    include_pseudorefs: bool = True,
) -> dict[str, Any]:
    """Bind ref backends and pseudorefs without trusting Git's path reads."""
    pseudorefs = (
        "HEAD",
        "ORIG_HEAD",
        "MERGE_HEAD",
        "CHERRY_PICK_HEAD",
        "REVERT_HEAD",
        "BISECT_HEAD",
        "AUTO_MERGE",
        "REBASE_HEAD",
    )

    def one_root(root: Path) -> dict[str, Any]:
        payload = {
            "packed-refs": _snapshot_git_surface_file(root / "packed-refs"),
            "packed-refs.lock": _snapshot_git_surface_file(root / "packed-refs.lock"),
            "refs": _snapshot_git_surface_directory(root / "refs"),
            "reftable": _snapshot_git_surface_directory(root / "reftable"),
        }
        if include_pseudorefs:
            payload["pseudorefs"] = {
                name: _snapshot_git_surface_file(root / name) for name in pseudorefs
            }
        return payload

    snapshot = {
        "worktree": one_root(git_dir),
        "common": one_root(common_dir),
    }
    for root_name, root in snapshot.items():
        if root["packed-refs.lock"].get("type") != "missing":
            raise _raise_git_surface_unsafe(
                (git_dir if root_name == "worktree" else common_dir)
                / "packed-refs.lock",
                "Git ref lock is present",
            )

        def reject_nested_locks(node: Mapping[str, Any], prefix: Path) -> None:
            if node.get("type") != "directory":
                return
            for name, child in node.get("entries", {}).items():
                child_path = prefix / name
                if name.endswith(".lock"):
                    raise _raise_git_surface_unsafe(child_path, "Git ref lock is present")
                reject_nested_locks(child, child_path)

        base = git_dir if root_name == "worktree" else common_dir
        reject_nested_locks(root["refs"], base / "refs")
        reject_nested_locks(root["reftable"], base / "reftable")
    return snapshot


def snapshot_remotes(
    cwd: Path,
    *,
    git_dir: Path | None = None,
    common_dir: Path | None = None,
) -> str:
    result = _run_git(
        cwd,
        ["remote", "-v"],
        check=False,
        git_dir=git_dir,
        common_dir=common_dir,
    )
    return hashlib.sha256((result.stdout or "").encode("utf-8")).hexdigest()


def git_dir_for(cwd: Path) -> Path:
    return Path(str(snapshot_git_authority(cwd)["git_dir"]))


def git_common_dir_for(cwd: Path) -> Path:
    """Return the shared git common directory (hooks/config/refs root for linked worktrees)."""
    return Path(str(snapshot_git_authority(cwd)["common_dir"]))


def snapshot_common_repo_surfaces(cwd: Path) -> dict[str, Any]:
    """Snapshot worktree-local and common-dir git surfaces for linked-worktree audits."""
    authority = snapshot_git_authority(cwd)
    work_tree, git_dir, common = _authority_paths(authority)
    worktree_config = snapshot_config(git_dir)
    common_config = snapshot_config(common)
    worktree_hooks = snapshot_hooks(git_dir)
    common_hooks = snapshot_hooks(common)
    ref_storage = snapshot_ref_storage(git_dir, common)
    static_control = snapshot_static_git_controls(git_dir, common)
    runtime_control = {
        **snapshot_runtime_git_controls(git_dir, common),
        "index_semantics": _snapshot_index_semantics(work_tree, git_dir, common),
    }
    return {
        "authority": authority,
        "git_dir": str(git_dir),
        "git_common_dir": str(common),
        "worktree_config": worktree_config,
        "common_config": common_config,
        "worktree_hooks": worktree_hooks,
        "common_hooks": common_hooks,
        "ref_storage": ref_storage,
        "static_control": static_control,
        "runtime_control": runtime_control,
        "refs_digest": _safe_refs_digest(
            work_tree,
            git_dir=git_dir,
            common_dir=common,
        ),
        "remotes_digest": snapshot_remotes(
            work_tree,
            git_dir=git_dir,
            common_dir=common,
        ),
    }


def assert_audited_git_surfaces(
    worker_checkout: Path,
    evidence: Mapping[str, Any],
    *,
    allow_shared_refs_changed: bool = False,
) -> dict[str, Any]:
    """Require every Git surface to match the exact sealed audit snapshot."""
    expected = evidence.get("audited_git_surfaces")
    if not isinstance(expected, dict) or set(expected) != _AUDITED_GIT_SURFACE_KEYS:
        raise ValidationIssue(
            "audit_evidence_git_surfaces_malformed",
            "Audit evidence lacks the complete Git surface snapshot",
        )
    if not all(isinstance(expected[key], str) for key in {
        "git_dir",
        "git_common_dir",
        "refs_digest",
        "remotes_digest",
    }):
        raise ValidationIssue(
            "audit_evidence_git_surfaces_malformed",
            "Audit Git surface identity and digest fields must be strings",
        )
    if not all(isinstance(expected[key], dict) for key in {
        "authority",
        "worktree_config",
        "common_config",
        "worktree_hooks",
        "common_hooks",
        "ref_storage",
        "static_control",
        "runtime_control",
    }):
        raise ValidationIssue(
            "audit_evidence_git_surfaces_malformed",
            "Audit Git control surfaces require canonical descriptor snapshots",
        )
    expected_runtime = expected["runtime_control"]
    if set(expected_runtime) != {
        "index",
        "shared_indexes",
        "locks",
        "index_semantics",
    }:
        raise ValidationIssue(
            "audit_evidence_git_surfaces_malformed",
            "Audit runtime control fields are not canonical",
        )
    expected_index_semantics = expected_runtime["index_semantics"]
    if (
        not isinstance(expected_index_semantics, dict)
        or set(expected_index_semantics)
        != {
            "version",
            "entries",
            "assume_skip_sha256",
            "fsmonitor_sha256",
        }
        or expected_index_semantics.get("version") != 1
        or not isinstance(expected_index_semantics.get("entries"), int)
        or expected_index_semantics["entries"] < 0
        or any(
            not isinstance(expected_index_semantics.get(key), str)
            or _SHA256_RE.fullmatch(expected_index_semantics[key]) is None
            for key in ("assume_skip_sha256", "fsmonitor_sha256")
        )
    ):
        raise ValidationIssue(
            "audit_evidence_git_surfaces_malformed",
            "Audit index semantic authority is not canonical",
        )
    current_authority = snapshot_git_authority(Path(worker_checkout))
    if current_authority != expected["authority"]:
        raise ValidationIssue(
            "audit_evidence_git_surfaces_mismatch",
            "Worker checkout or Git locator authority changed after audit",
        )
    trusted_worker, trusted_git_dir, trusted_common_dir = _authority_paths(
        current_authority
    )
    if (
        str(trusted_git_dir) != expected["git_dir"]
        or str(trusted_common_dir) != expected["git_common_dir"]
    ):
        raise ValidationIssue(
            "audit_evidence_git_surfaces_mismatch",
            "Worker Git directory authority disagrees with sealed evidence",
        )
    # Descriptor-validate every control file before any Git-derived query. This
    # prevents a changed fsmonitor/include/helper config from observing the host
    # environment before the audit rejects it.
    runtime_exact_before = _snapshot_runtime_git_controls_exact(
        trusted_git_dir,
        trusted_common_dir,
    )
    descriptor_runtime = _runtime_control_contract(runtime_exact_before)
    descriptor_actual = {
        "authority": current_authority,
        "git_dir": str(trusted_git_dir),
        "git_common_dir": str(trusted_common_dir),
        "worktree_config": snapshot_config(trusted_git_dir),
        "common_config": snapshot_config(trusted_common_dir),
        "worktree_hooks": snapshot_hooks(trusted_git_dir),
        "common_hooks": snapshot_hooks(trusted_common_dir),
        "ref_storage": snapshot_ref_storage(trusted_git_dir, trusted_common_dir),
        "static_control": snapshot_static_git_controls(
            trusted_git_dir,
            trusted_common_dir,
        ),
        "runtime_control": descriptor_runtime,
    }
    descriptor_expected = dict(expected)
    descriptor_expected["runtime_control"] = {
        key: expected_runtime[key]
        for key in ("index", "shared_indexes", "locks")
    }
    descriptor_keys = set(descriptor_actual)
    if allow_shared_refs_changed:
        descriptor_keys.remove("ref_storage")
        expected_refs = descriptor_expected["ref_storage"]
        actual_refs = descriptor_actual["ref_storage"]
        if not isinstance(expected_refs.get("common"), dict):
            raise ValidationIssue(
                "audit_evidence_git_surfaces_malformed",
                "Audit common ref authority is not canonical",
            )
        refs_match = (
            actual_refs.get("worktree") == expected_refs.get("worktree")
            and actual_refs.get("common", {}).get("packed-refs.lock")
            == {"type": "missing"}
        )
    else:
        refs_match = True
    if not refs_match or any(
        descriptor_actual[key] != descriptor_expected[key]
        for key in descriptor_keys
    ):
        raise ValidationIssue(
            "audit_evidence_git_surfaces_mismatch",
            "Worker Git config, hooks, or ref storage changed after audit",
        )
    index_semantics = _snapshot_index_semantics(
        trusted_worker,
        trusted_git_dir,
        trusted_common_dir,
    )
    actual = {
        **descriptor_actual,
        "runtime_control": {
            **descriptor_runtime,
            "index_semantics": index_semantics,
        },
        "refs_digest": _safe_refs_digest(
            trusted_worker,
            git_dir=trusted_git_dir,
            common_dir=trusted_common_dir,
        ),
        "remotes_digest": snapshot_remotes(
            trusted_worker,
            git_dir=trusted_git_dir,
            common_dir=trusted_common_dir,
        ),
    }
    if runtime_exact_before != _snapshot_runtime_git_controls_exact(
        trusted_git_dir,
        trusted_common_dir,
    ):
        raise ValidationIssue(
            "audit_evidence_git_surfaces_mismatch",
            "Worker index changed during post-audit validation",
        )
    comparison_keys = set(actual)
    if allow_shared_refs_changed:
        comparison_keys -= {"ref_storage", "refs_digest"}
    if any(actual[key] != expected[key] for key in comparison_keys):
        raise ValidationIssue(
            "audit_evidence_git_surfaces_mismatch",
            "Worker Git refs, remotes, config, or hooks changed after audit",
        )
    return actual


def validated_patch_transport_digests(
    evidence: Mapping[str, Any],
    chain: Sequence[CommitInfo],
) -> list[dict[str, str]]:
    """Return the sealed ordered per-commit patch transport digest contract."""
    supplied = evidence.get("patch_transport_digests")
    if not isinstance(supplied, list) or len(supplied) != len(chain):
        raise ValidationIssue(
            "audit_patch_transport_digests_malformed",
            "Audit evidence patch transport count does not match the commit chain",
        )
    validated: list[dict[str, str]] = []
    for item, commit in zip(supplied, chain):
        if not isinstance(item, dict) or set(item) != {"commit", "sha256"}:
            raise ValidationIssue(
                "audit_patch_transport_digests_malformed",
                "Audit patch transport entries require commit and sha256 only",
            )
        digest = item.get("sha256")
        if item.get("commit") != commit.sha or not isinstance(
            digest, str
        ) or not _SHA256_RE.fullmatch(digest):
            raise ValidationIssue(
                "audit_patch_transport_digests_mismatch",
                "Audit patch transport digest order does not match the commit chain",
            )
        validated.append({"commit": commit.sha, "sha256": digest})
    return validated


def list_commit_chain(
    cwd: Path,
    base_head: str,
    tip: str,
    *,
    git_dir: Path | None = None,
    common_dir: Path | None = None,
) -> list[CommitInfo]:
    """Return direct-descendant commits from base (exclusive) to tip (inclusive)."""
    if base_head == tip:
        return []
    # Ensure tip is descendant of base.
    merge_base = _run_git(
        cwd,
        ["merge-base", base_head, tip],
        git_dir=git_dir,
        common_dir=common_dir,
    ).stdout.strip()
    if merge_base != base_head:
        raise ValidationIssue(
            "commit_chain_not_descendant",
            f"Tip `{tip}` is not a direct descendant of base `{base_head}` "
            f"(merge-base={merge_base})",
        )
    rev_list = _run_git(
        cwd,
        ["rev-list", "--reverse", f"{base_head}..{tip}"],
        git_dir=git_dir,
        common_dir=common_dir,
    ).stdout.split()
    commits: list[CommitInfo] = []
    prev = base_head
    for sha in rev_list:
        parents = _run_git(
            cwd,
            ["show", "-s", "--format=%P", sha],
            git_dir=git_dir,
            common_dir=common_dir,
        ).stdout.strip().split()
        tree = _run_git(
            cwd,
            ["show", "-s", "--format=%T", sha],
            git_dir=git_dir,
            common_dir=common_dir,
        ).stdout.strip()
        subject = _run_git(
            cwd,
            ["show", "-s", "--format=%s", sha],
            git_dir=git_dir,
            common_dir=common_dir,
        ).stdout.strip()
        author = _run_git(
            cwd,
            ["show", "-s", "--format=%an <%ae>", sha],
            git_dir=git_dir,
            common_dir=common_dir,
        ).stdout.strip()
        paths = _run_git(
            cwd,
            [
                "diff-tree",
                "--no-ext-diff",
                "--no-textconv",
                "--no-commit-id",
                "--name-only",
                "-r",
                sha,
            ],
            git_dir=git_dir,
            common_dir=common_dir,
        ).stdout.splitlines()
        # Expect single parent direct descendant.
        if len(parents) != 1:
            raise ValidationIssue(
                "unexpected_merge_or_root",
                f"Commit `{sha}` has parents {parents}; only single-parent commits permitted",
            )
        if parents[0] != prev:
            raise ValidationIssue(
                "chain_parent_mismatch",
                f"Commit `{sha}` parent `{parents[0]}` != expected previous `{prev}`",
            )
        commits.append(
            CommitInfo(
                sha=sha,
                parents=parents,
                tree=tree,
                subject=subject,
                author=author,
                paths=paths,
            )
        )
        prev = sha
    return commits


def detect_symlink_escapes(cwd: Path, paths: Sequence[str], lease: WriterLease) -> list[str]:
    """Reject every worker-owned symlink, not only targets outside the repo.

    An in-repo target can still point at host-only `.elves`, `.git`, or ignored
    credentials after import. Untrusted handoff patches therefore may not change
    or own symlinks at all.
    """
    escapes: list[str] = []
    root = cwd.resolve()
    for rel in paths:
        lexical = cwd / rel
        if lexical.is_symlink():
            escapes.append(rel)
            continue
        full = lexical.resolve()
        try:
            full.relative_to(root)
        except ValueError:
            escapes.append(rel)
            continue
        if not is_path_allowed(rel, lease):
            # tracked separately as out_of_scope; still note symlink escapes only for links
            pass
    return escapes


def _tree_path_is_symlink(
    cwd: Path,
    revision: str,
    rel_path: str,
    *,
    git_dir: Path | None = None,
    common_dir: Path | None = None,
) -> bool:
    result = _run_git(
        cwd,
        ["ls-tree", revision, "--", rel_path],
        check=False,
        git_dir=git_dir,
        common_dir=common_dir,
    )
    if result.returncode != 0:
        return False
    row = (result.stdout or "").strip()
    return bool(row and row.split(None, 1)[0] == "120000")


def _validate_candidate_tree_inert(
    cwd: Path,
    chain: Sequence[CommitInfo],
    *,
    git_dir: Path,
    common_dir: Path,
) -> list[str]:
    """Return command-bearing attribute/submodule paths changed by the worker."""
    unsafe: set[str] = set()
    for commit in chain:
        for rel_path in sorted(set(commit.paths)):
            tree_row = _run_git(
                cwd,
                ["ls-tree", commit.sha, "--", rel_path],
                check=False,
                git_dir=git_dir,
                common_dir=common_dir,
            )
            row = (tree_row.stdout or "").strip()
            if row and row.split(None, 1)[0] == "160000":
                unsafe.add(rel_path)
                continue
            name = Path(rel_path).name
            if name == ".gitmodules":
                unsafe.add(rel_path)
                continue
            if name != ".gitattributes" or not row:
                continue
            blob = _run_git(
                cwd,
                ["cat-file", "blob", f"{commit.sha}:{rel_path}"],
                text=False,
                git_dir=git_dir,
                common_dir=common_dir,
            ).stdout
            raw = bytes(blob) if not isinstance(blob, bytes) else blob
            if len(raw) > _AUDIT_SCAN_MAX_FILE_BYTES:
                unsafe.add(rel_path)
                continue
            text = raw.decode("utf-8", errors="surrogateescape")
            for attr_line in text.splitlines():
                content = attr_line.strip()
                if not content or content.startswith("#"):
                    continue
                for token in content.split()[1:]:
                    attribute = token.lstrip("-!").split("=", 1)[0].lower()
                    if attribute in {"filter", "diff"}:
                        unsafe.add(rel_path)
                        break
                if rel_path in unsafe:
                    break
    return sorted(unsafe)


def classify_worker_command(command: str) -> dict[str, Any]:
    """Use workspace_guard classification for a worker command string."""
    guard = _load_workspace_guard()
    profile = guard.classify_command(command)
    return {
        "category": profile.category,
        "check_local": profile.check_local,
        "check_remote": profile.check_remote,
        "reason": profile.reason,
    }


def _contains_secret_shaped_text(
    value: str,
    *,
    exact_secret_values: Sequence[str] | None,
) -> bool:
    return redact_text(
        value,
        exact_values=tuple(exact_secret_values or ()),
    ).text != value


def _scan_audited_export_for_secrets(
    lease: WriterLease,
    chain: Sequence[CommitInfo],
    *,
    exact_secret_values: Sequence[str] | None,
    git_dir: Path,
    common_dir: Path,
) -> tuple[list[str], list[dict[str, str]]]:
    """Scan and bind the exact in-memory candidate patch transport series.

    Applyable patches cannot be redacted without changing their meaning.  The
    safe boundary is therefore fail-closed: generate every patch in memory,
    scan metadata, transport bytes, and changed tip blobs, and retain only its
    ordered digest before AUDITED_PASS is possible.
    """
    findings: set[str] = set()
    transport_digests: list[dict[str, str]] = []
    for commit in chain:
        metadata = [commit.subject, commit.author, *commit.paths]
        if any(
            _contains_secret_shaped_text(
                value,
                exact_secret_values=exact_secret_values,
            )
            for value in metadata
        ):
            findings.add("commit_metadata")

    if not chain:
        return sorted(findings), transport_digests

    total_bytes = 0
    for commit in chain:
        data = _format_patch_bytes(
            Path(lease.worker_checkout),
            commit.sha,
            git_dir=git_dir,
            common_dir=common_dir,
        )
        transport_digests.append(
            {
                "commit": commit.sha,
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
        size = len(data)
        total_bytes += size
        if (
            size > _AUDIT_SCAN_MAX_FILE_BYTES
            or total_bytes > _AUDIT_SCAN_MAX_TOTAL_BYTES
        ):
            findings.add("patch_scan_limit")
            continue
        text = data.decode("utf-8", errors="surrogateescape")
        if _contains_secret_shaped_text(
            text,
            exact_secret_values=exact_secret_values,
        ):
            findings.add("patch_content")

    # Scan each commit's resulting changed blobs, not only the final tip. Binary
    # format-patch payloads encode content, so a credential introduced in one
    # binary commit and removed by a later commit would otherwise disappear from
    # both literal transport scanning and the final tree.
    for commit in chain:
        for rel_path in sorted(set(commit.paths)):
            object_name = f"{commit.sha}:{rel_path}"
            size_result = _run_git(
                Path(lease.worker_checkout),
                ["cat-file", "-s", object_name],
                check=False,
                git_dir=git_dir,
                common_dir=common_dir,
            )
            if size_result.returncode != 0:
                continue  # deleted path in this commit
            try:
                size = int((size_result.stdout or "").strip())
            except ValueError:
                findings.add("changed_blob_scan_error")
                continue
            total_bytes += size
            if (
                size > _AUDIT_SCAN_MAX_FILE_BYTES
                or total_bytes > _AUDIT_SCAN_MAX_TOTAL_BYTES
            ):
                findings.add("changed_blob_scan_limit")
                continue
            blob = _run_git(
                Path(lease.worker_checkout),
                ["cat-file", "blob", object_name],
                text=False,
                git_dir=git_dir,
                common_dir=common_dir,
            ).stdout
            raw = bytes(blob) if not isinstance(blob, bytes) else blob
            if len(raw) != size:
                findings.add("changed_blob_scan_error")
                continue
            text = raw.decode("utf-8", errors="surrogateescape")
            if _contains_secret_shaped_text(
                text,
                exact_secret_values=exact_secret_values,
            ):
                findings.add("changed_blob_content")
    return sorted(findings), transport_digests


def audit_lease_turn(
    lease: WriterLease,
    *,
    process_baseline: Sequence[str] | None = None,
    process_observed: Sequence[str] | None = None,
    observed_commands: Sequence[str] | None = None,
    pre_refs_digest: str | None = None,
    pre_remotes: str | None = None,
    pre_config: Mapping[str, Any] | None = None,
    pre_hooks: Mapping[str, Any] | None = None,
    pre_common_config: Mapping[str, Any] | None = None,
    pre_common_hooks: Mapping[str, Any] | None = None,
    pre_ref_storage: Mapping[str, Any] | None = None,
    pre_git_dir: str | None = None,
    pre_git_common_dir: str | None = None,
    pre_authority: Mapping[str, Any] | None = None,
    pre_static_control: Mapping[str, Any] | None = None,
    exact_secret_values: Sequence[str] | None = None,
) -> AuditResult:
    """Post-turn audit of the worker checkout against the lease contract."""
    worker = Path(lease.worker_checkout)
    current_authority = snapshot_git_authority(worker)
    trusted_worker, trusted_git_dir, trusted_common_dir = _authority_paths(
        current_authority
    )
    current_config = snapshot_config(trusted_git_dir)
    current_hooks = snapshot_hooks(trusted_git_dir)
    current_common_config = snapshot_config(trusted_common_dir)
    current_common_hooks = snapshot_hooks(trusted_common_dir)
    current_ref_storage = snapshot_ref_storage(
        trusted_git_dir,
        trusted_common_dir,
    )
    if (
        current_ref_storage["worktree"]["pseudorefs"]["HEAD"].get("type")
        != "file"
    ):
        raise _raise_git_surface_unsafe(
            trusted_git_dir / "HEAD",
            "HEAD must be a single-link regular file",
        )
    current_stable_refs = snapshot_ref_storage(
        trusted_git_dir,
        trusted_common_dir,
        include_pseudorefs=False,
    )
    current_static_control = snapshot_static_git_controls(
        trusted_git_dir,
        trusted_common_dir,
    )
    current_runtime_exact = _snapshot_runtime_git_controls_exact(
        trusted_git_dir,
        trusted_common_dir,
    )
    current_index_semantics = _snapshot_index_semantics(
        trusted_worker,
        trusted_git_dir,
        trusted_common_dir,
    )
    pre_boundary_values = (
        pre_config,
        pre_hooks,
        pre_common_config,
        pre_common_hooks,
        pre_ref_storage,
        pre_git_dir,
        pre_git_common_dir,
        pre_authority,
        pre_static_control,
    )
    full_boundary_requested = any(
        value is not None
        for value in (
            pre_git_dir,
            pre_git_common_dir,
            pre_authority,
            pre_static_control,
        )
    )
    if full_boundary_requested:
        if any(value is None for value in pre_boundary_values):
            raise ValidationIssue(
                "audit_pre_snapshot_incomplete",
                "Descriptor pre-boundary fields must be supplied as one canonical set",
            )
        if (
            current_authority != pre_authority
            or str(trusted_git_dir) != pre_git_dir
            or str(trusted_common_dir) != pre_git_common_dir
            or current_config != pre_config
            or current_hooks != pre_hooks
            or current_common_config != pre_common_config
            or current_common_hooks != pre_common_hooks
            or current_stable_refs != pre_ref_storage
            or current_static_control != pre_static_control
        ):
            raise ValidationIssue(
                "audit_pre_git_surface_mismatch",
                "Git locator, config, hooks, refs, or object controls changed during the lease",
            )
    tip = _safe_git_head(
        trusted_worker,
        git_dir=trusted_git_dir,
        common_dir=trusted_common_dir,
    )
    status = _safe_git_status_porcelain(
        trusted_worker,
        git_dir=trusted_git_dir,
        common_dir=trusted_common_dir,
    )
    staged = any(line and line[0] in "MADRCT" for line in status.splitlines())
    result = AuditResult(
        ok=True,
        lease_id=lease.lease_id,
        base_head=lease.base_head,
        worker_tip=tip,
        status_porcelain=(
            ""
            if not status
            else "[REDACTED:untrusted_status] "
            f"entries={len(status.splitlines())} "
            f"sha256={hashlib.sha256(status.encode('utf-8')).hexdigest()}"
        ),
        staged=staged,
        refs_digest_before=pre_refs_digest or lease.pre_refs_digest,
    )

    # Dirty index / uncommitted changes fail (expected handoff is clean commit chain).
    if status.strip():
        result.ok = False
        result.reasons.append("worker checkout is dirty after turn (uncommitted changes)")

    # Commit chain
    try:
        chain = list_commit_chain(
            trusted_worker,
            lease.base_head,
            tip,
            git_dir=trusted_git_dir,
            common_dir=trusted_common_dir,
        )
        result.commit_chain = chain
    except ValidationIssue as issue:
        result.ok = False
        result.reasons.append(issue.message)
        chain = []

    if chain and not lease.detached_commits_permitted:
        result.ok = False
        result.reasons.append(
            "detached commits present but lease.detached_commits_permitted is false "
            f"(sandbox={lease.sandbox_profile})"
        )

    # Path scope
    out_of_scope: list[str] = []
    all_paths: list[str] = []
    for commit in chain:
        all_paths.extend(commit.paths)
        for path in commit.paths:
            if not is_path_allowed(path, lease):
                out_of_scope.append(path)
    # Also scan untracked/modified paths from status
    for line in status.splitlines():
        if not line.strip():
            continue
        path = line[3:].strip() if len(line) > 3 else line.strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        all_paths.append(path)
        if not is_path_allowed(path, lease):
            out_of_scope.append(path)
    result.out_of_scope_paths = sorted(set(out_of_scope))
    if result.out_of_scope_paths:
        result.ok = False
        result.reasons.append(
            "out-of-scope paths: " + ", ".join(result.out_of_scope_paths[:20])
        )

    changed_symlinks: set[str] = set()
    for commit in chain:
        for path in commit.paths:
            if _tree_path_is_symlink(
                trusted_worker,
                commit.sha,
                path,
                git_dir=trusted_git_dir,
                common_dir=trusted_common_dir,
            ) or any(
                _tree_path_is_symlink(
                    trusted_worker,
                    parent,
                    path,
                    git_dir=trusted_git_dir,
                    common_dir=trusted_common_dir,
                )
                for parent in commit.parents
            ):
                changed_symlinks.add(path)
    changed_symlinks.update(
        detect_symlink_escapes(trusted_worker, sorted(set(all_paths)), lease)
    )
    result.symlink_escapes = sorted(changed_symlinks)
    if result.symlink_escapes:
        result.ok = False
        result.reasons.append(
            "symlink paths are forbidden in untrusted handoffs: "
            + ", ".join(result.symlink_escapes)
        )

    unsafe_tree_controls = _validate_candidate_tree_inert(
        trusted_worker,
        chain,
        git_dir=trusted_git_dir,
        common_dir=trusted_common_dir,
    )
    if unsafe_tree_controls:
        result.ok = False
        result.reasons.append(
            "command-bearing .gitattributes or submodule controls are forbidden: "
            + ", ".join(unsafe_tree_controls)
        )

    # Refs / remotes / config / hooks
    after_refs = _safe_refs_digest(
        trusted_worker,
        git_dir=trusted_git_dir,
        common_dir=trusted_common_dir,
    )
    result.refs_digest_after = after_refs
    if result.refs_digest_before and after_refs != result.refs_digest_before:
        # HEAD movement for detached commits updates some reflogs but for-each-ref
        # should stay stable if no branch/tag created. Flag any change.
        result.refs_changed = True
        result.ok = False
        result.reasons.append("refs digest changed (new branch/tag/ref mutation)")

    gdir = trusted_git_dir
    common = trusted_common_dir
    after_remotes = snapshot_remotes(
        trusted_worker,
        git_dir=gdir,
        common_dir=common,
    )
    if pre_remotes is not None and after_remotes != pre_remotes:
        result.remotes_changed = True
        result.ok = False
        result.reasons.append("git remotes changed during lease")
    after_config_wt = snapshot_config(gdir)
    after_config_common = snapshot_config(common)
    if pre_config is not None and after_config_wt != pre_config:
        result.config_changed = True
        result.ok = False
        result.reasons.append("git config changed during lease")
    if pre_common_config is not None and after_config_common != pre_common_config:
        result.config_changed = True
        result.ok = False
        result.reasons.append("common-dir git config changed during lease")
    after_hooks_wt = snapshot_hooks(gdir)
    after_hooks_common = snapshot_hooks(common)
    after_ref_storage = snapshot_ref_storage(gdir, common)
    after_stable_ref_storage = snapshot_ref_storage(
        gdir,
        common,
        include_pseudorefs=False,
    )
    after_static_control = snapshot_static_git_controls(gdir, common)
    after_runtime_exact = _snapshot_runtime_git_controls_exact(gdir, common)
    after_index_semantics = _snapshot_index_semantics(trusted_worker, gdir, common)
    after_runtime_control = {
        **_runtime_control_contract(after_runtime_exact),
        "index_semantics": after_index_semantics,
    }
    if pre_hooks is not None and after_hooks_wt != pre_hooks:
        result.hooks_changed = True
        result.ok = False
        result.reasons.append("git hooks changed during lease")
    if pre_common_hooks is not None and after_hooks_common != pre_common_hooks:
        result.hooks_changed = True
        result.ok = False
        result.reasons.append("common-dir git hooks changed during lease")
    if pre_ref_storage is not None and after_stable_ref_storage != pre_ref_storage:
        result.refs_changed = True
        result.ok = False
        result.reasons.append("git ref storage changed during lease")
    if pre_static_control is not None and after_static_control != pre_static_control:
        result.config_changed = True
        result.ok = False
        result.reasons.append("git object, attribute, exclude, or sparse controls changed")
    if (
        current_authority != snapshot_git_authority(trusted_worker)
        or current_config != after_config_wt
        or current_common_config != after_config_common
        or current_hooks != after_hooks_wt
        or current_common_hooks != after_hooks_common
        or current_ref_storage != after_ref_storage
        or current_static_control != after_static_control
        or current_runtime_exact != after_runtime_exact
        or current_index_semantics != after_index_semantics
    ):
        raise ValidationIssue(
            "audit_git_surface_race",
            "Worker Git control surfaces changed while the audit was running",
        )
    result.audited_git_surfaces = {
        "authority": current_authority,
        "git_dir": str(gdir),
        "git_common_dir": str(common),
        "worktree_config": after_config_wt,
        "common_config": after_config_common,
        "worktree_hooks": after_hooks_wt,
        "common_hooks": after_hooks_common,
        "ref_storage": after_ref_storage,
        "static_control": after_static_control,
        "runtime_control": after_runtime_control,
        "refs_digest": after_refs,
        "remotes_digest": after_remotes,
    }

    # Observed commands are untrusted evidence.  Preserve only categorical
    # violations: persisting the raw command would turn the audit log, lease
    # rejection reason, and CLI JSON into an exfiltration channel for URL
    # credentials or other command-line secrets.
    for command in observed_commands or []:
        profile = classify_worker_command(command)
        # Extract subcommand roughly
        parts = command.split()
        action = parts[1] if len(parts) > 1 and parts[0] == "git" else (parts[0] if parts else "")
        if action in lease.forbidden_git_actions or profile["category"] == "remote_mutation":
            if action in lease.forbidden_git_actions:
                evidence = f"git:{action}"
            else:
                evidence = "category:remote_mutation"
            if evidence not in result.forbidden_actions_seen:
                result.forbidden_actions_seen.append(evidence)
    if result.forbidden_actions_seen:
        result.ok = False
        result.reasons.append(
            "forbidden actions observed: " + "; ".join(result.forbidden_actions_seen[:10])
        )

    # Process leak: any observed pid/command not in baseline
    baseline = set(process_baseline or [])
    observed = list(process_observed or [])
    leaks = [item for item in observed if item not in baseline]
    result.process_leaks = [
        "process:sha256=" + hashlib.sha256(item.encode("utf-8")).hexdigest()
        for item in leaks
    ]
    if result.process_leaks:
        result.ok = False
        result.reasons.append(
            "process leak(s) remain after turn: "
            + ", ".join(result.process_leaks[:10])
        )

    if result.ok:
        secret_findings, transport_digests = _scan_audited_export_for_secrets(
            lease,
            chain,
            exact_secret_values=exact_secret_values,
            git_dir=trusted_git_dir,
            common_dir=trusted_common_dir,
        )
        result.patch_transport_digests = transport_digests
        if secret_findings:
            result.ok = False
            result.reasons.append(
                "secret-shaped data detected in untrusted handoff; refuse audit: "
                + ", ".join(secret_findings)
            )

    return result.redact_in_place(
        exact_secret_values=exact_secret_values,
    )._seal()


def export_binary_patches(
    lease: WriterLease,
    *,
    output_dir: Path,
    chain: Sequence[CommitInfo] | None = None,
    require_audited_pass: bool = True,
    audit_evidence: dict[str, Any] | None = None,
) -> list[Path]:
    """Export binary-safe per-commit patches via format-patch.

    When ``require_audited_pass`` is true (default), the lease must already be in
    ``AUDITED_PASS`` (or later export states). Export never implicitly promotes
    audit state. The export directory must be exclusive and empty.
    """
    if require_audited_pass and lease.state.value not in {
        "audited_pass",
        "exported",
        "apply_checked",
    }:
        raise ValidationIssue(
            "export_requires_audited_pass",
            f"Export refused from lease state `{lease.state.value}`",
            path=f"leases.{lease.lease_id}.state",
        )
    persisted_evidence: dict[str, Any] | None = None
    if require_audited_pass:
        persisted_evidence = LeaseStore(
            Path(lease.host_checkout)
        )._read_verified_audit_evidence(lease)
    if audit_evidence is not None:
        if not audit_evidence.get("ok"):
            raise ValidationIssue(
                "export_stale_or_failed_audit",
                "Audit evidence is not ok; refusing export",
            )
        evidence_tip = audit_evidence.get("worker_tip")
        # Prefer audit evidence tip when lease still holds pre-turn tip; reject only
        # when both tips are set and disagree after an audit-updated lease tip.
        if (
            evidence_tip
            and lease.worker_tip
            and evidence_tip != lease.worker_tip
            and lease.worker_tip != lease.base_head
        ):
            raise ValidationIssue(
                "export_stale_audit_tip",
                "Audit evidence worker_tip does not match lease worker_tip",
            )
        caller_evidence_digest = audit_evidence.get("evidence_digest")
        if (
            persisted_evidence is not None
            and caller_evidence_digest is not None
            and caller_evidence_digest != persisted_evidence.get("evidence_digest")
        ):
            raise ValidationIssue(
                "export_stale_audit_evidence",
                "Caller audit evidence does not match persisted lease authority",
            )

    worker = Path(lease.worker_checkout)
    if persisted_evidence is not None:
        live_surfaces = assert_audited_git_surfaces(worker, persisted_evidence)
        authority = live_surfaces["authority"]
    else:
        authority = snapshot_git_authority(worker)
        unchecked_worker, unchecked_git_dir, unchecked_common_dir = _authority_paths(
            authority
        )
        # Even compatibility/test exports that do not require an audited lease
        # must reject command-bearing config and unsafe mutable metadata first.
        snapshot_config(unchecked_git_dir)
        snapshot_config(unchecked_common_dir)
        snapshot_static_git_controls(unchecked_git_dir, unchecked_common_dir)
        snapshot_runtime_git_controls(unchecked_git_dir, unchecked_common_dir)
        _snapshot_index_semantics(
            unchecked_worker,
            unchecked_git_dir,
            unchecked_common_dir,
        )
    worker, trusted_git_dir, trusted_common_dir = _authority_paths(authority)
    if chain is None:
        tip = _safe_git_head(
            worker,
            git_dir=trusted_git_dir,
            common_dir=trusted_common_dir,
        )
        chain = list_commit_chain(
            worker,
            lease.base_head,
            tip,
            git_dir=trusted_git_dir,
            common_dir=trusted_common_dir,
        )
    chain = list(chain)
    if not chain:
        return []

    expected_transports = (
        validated_patch_transport_digests(persisted_evidence, chain)
        if persisted_evidence is not None
        else []
    )
    rendered: list[tuple[str, bytes, str]] = []
    total_bytes = 0
    for index, commit in enumerate(chain, start=1):
        data = _format_patch_bytes(
            worker,
            commit.sha,
            git_dir=trusted_git_dir,
            common_dir=trusted_common_dir,
        )
        total_bytes += len(data)
        if (
            len(data) > _AUDIT_SCAN_MAX_FILE_BYTES
            or total_bytes > _AUDIT_SCAN_MAX_TOTAL_BYTES
        ):
            raise ValidationIssue(
                "patch_transport_limit_exceeded",
                "Regenerated patch transport exceeds the audited byte limits",
            )
        digest = hashlib.sha256(data).hexdigest()
        if expected_transports and digest != expected_transports[index - 1]["sha256"]:
            raise ValidationIssue(
                "patch_transport_digest_mismatch",
                "Regenerated patch bytes do not match the sealed audit transport",
            )
        rendered.append((f"{index:04d}-{commit.sha[:12]}.patch", data, digest))

    if persisted_evidence is not None:
        # Recheck after rendering; after this point Git is never consulted and
        # only already-verified bytes cross the held directory-fd boundary.
        assert_audited_git_surfaces(worker, persisted_evidence)

    out, out_fd, out_identity = _prepare_exclusive_export_directory(Path(output_dir))

    created_names: list[str] = []
    try:
        def write_one(name: str, data: bytes) -> None:
            try:
                _write_export_file(out_fd, name, data)
            except ValidationIssue as exc:
                # This code is emitted only after O_EXCL succeeded and the
                # helper's immediate unlink failed, so an outer retry cannot
                # delete a file created by another process after an open race.
                if exc.code == "export_cleanup_failed":
                    created_names.append(name)
                raise
            else:
                created_names.append(name)

        patch_names: list[str] = []
        patch_digests: dict[str, str] = {}
        for name, data, digest in rendered:
            write_one(name, data)
            patch_names.append(name)
            patch_digests[name] = digest

        manifest = {
            "lease_id": lease.lease_id,
            "base_head": lease.base_head,
            "worker_tip": chain[-1].sha,
            "audit_evidence_digest": lease.audit_evidence_digest,
            "commits": [c.to_dict() for c in chain],
            "patches": patch_names,
            "patch_digests": patch_digests,
            "audited_patch_transport_digests": (
                expected_transports
                if persisted_evidence is not None
                else [
                    {"commit": commit.sha, "sha256": digest}
                    for commit, (_name, _data, digest) in zip(chain, rendered)
                ]
            ),
        }
        manifest["manifest_digest"] = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        write_one(
            "chain.json",
            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )
        os.fsync(out_fd)
        _assert_export_directory_identity(out, out_identity)
        patches = [out / name for name in patch_names]
        if require_audited_pass:
            return _read_verified_patch_bundle(
                lease,
                output_dir=out,
                expected_directory_identity=out_identity,
            ).paths
        return patches
    except BaseException as primary:
        cleanup_errors: list[str] = []
        for name in reversed(created_names):
            try:
                os.unlink(name, dir_fd=out_fd)
            except FileNotFoundError:
                continue
            except OSError as exc:
                cleanup_errors.append(f"{name}:{type(exc).__name__}")
        try:
            os.fsync(out_fd)
        except OSError as exc:
            cleanup_errors.append(f"directory:{type(exc).__name__}")
        try:
            residue = sorted(os.listdir(out_fd))
        except OSError as exc:
            cleanup_errors.append(f"directory-list:{type(exc).__name__}")
            residue = []
        if residue:
            cleanup_errors.append("residue:" + ",".join(residue[:20]))
        if cleanup_errors:
            raise ValidationIssue(
                "export_cleanup_failed",
                "Patch export failed and private partial output could not be removed",
                path=str(out),
                hint=", ".join(cleanup_errors),
            ) from primary
        raise
    finally:
        os.close(out_fd)


def _read_verified_patch_bundle(
    lease: WriterLease,
    *,
    output_dir: Path,
    expected_directory_identity: tuple[int, int] | None = None,
) -> VerifiedPatchBundle:
    """Verify and retain one immutable ordered export from descriptor reads."""
    persisted_evidence = LeaseStore(
        Path(lease.host_checkout)
    )._read_verified_audit_evidence(lease)
    live_surfaces = assert_audited_git_surfaces(
        Path(lease.worker_checkout),
        persisted_evidence,
    )
    worker, trusted_git_dir, trusted_common_dir = _authority_paths(
        live_surfaces["authority"]
    )
    out, directory_identity, manifest_bytes, manifest, patch_rows = (
        _read_export_bundle_bytes(
            output_dir,
            expected_directory_identity=expected_directory_identity,
        )
    )
    expected_keys = {
        "lease_id",
        "base_head",
        "worker_tip",
        "audit_evidence_digest",
        "commits",
        "patches",
        "patch_digests",
        "audited_patch_transport_digests",
        "manifest_digest",
    }
    if set(manifest) != expected_keys:
        raise ValidationIssue(
            "patch_manifest_fields_mismatch",
            "chain.json fields do not match the canonical manifest schema",
        )
    supplied_manifest_digest = str(manifest.get("manifest_digest") or "")
    canonical = {k: v for k, v in manifest.items() if k != "manifest_digest"}
    expected_manifest_digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if supplied_manifest_digest != expected_manifest_digest:
        raise ValidationIssue(
            "patch_manifest_digest_mismatch",
            "chain.json digest does not match its canonical payload",
        )
    if str(manifest.get("lease_id") or "") != lease.lease_id:
        raise ValidationIssue("patch_manifest_lease_mismatch", "Manifest lease id mismatch")
    if str(manifest.get("base_head") or "") != lease.base_head:
        raise ValidationIssue("patch_manifest_base_mismatch", "Manifest base head mismatch")
    if str(manifest.get("worker_tip") or "") != str(lease.worker_tip or ""):
        raise ValidationIssue("patch_manifest_tip_mismatch", "Manifest audited tip mismatch")
    if str(manifest.get("audit_evidence_digest") or "") != str(
        lease.audit_evidence_digest or ""
    ):
        raise ValidationIssue(
            "patch_manifest_audit_digest_mismatch",
            "Manifest is not bound to the lease's immutable audit evidence",
        )

    patches = manifest.get("patches")
    digests = manifest.get("patch_digests")
    commits = manifest.get("commits")
    if not isinstance(patches, list) or not all(isinstance(v, str) for v in patches):
        raise ValidationIssue("patch_manifest_malformed", "Manifest patches must be strings")
    if not isinstance(digests, dict) or set(digests) != set(patches):
        raise ValidationIssue(
            "patch_manifest_digest_set_mismatch",
            "Patch digest keys must match the ordered patch list exactly",
        )
    if not isinstance(commits, list) or len(commits) != len(patches):
        raise ValidationIssue(
            "patch_manifest_commit_count_mismatch",
            "Manifest commit and patch counts must match",
        )
    expected_chain = list_commit_chain(
        worker,
        lease.base_head,
        str(lease.worker_tip or ""),
        git_dir=trusted_git_dir,
        common_dir=trusted_common_dir,
    )
    expected_transports = validated_patch_transport_digests(
        persisted_evidence,
        expected_chain,
    )
    if manifest.get("audited_patch_transport_digests") != expected_transports:
        raise ValidationIssue(
            "patch_manifest_transport_authority_mismatch",
            "Manifest patch transports do not match persisted audit authority",
        )
    expected_commits = [commit.to_dict() for commit in expected_chain]
    if commits != expected_commits:
        raise ValidationIssue(
            "patch_manifest_commit_chain_mismatch",
            "Manifest commit chain does not match the audited worker tip",
        )
    for index, ((name, data), commit) in enumerate(
        zip(patch_rows, expected_chain),
        start=1,
    ):
        match = _PATCH_NAME_RE.fullmatch(name)
        if (
            match is None
            or int(match.group("order")) != index
            or Path(name).name != name
        ):
            raise ValidationIssue(
                "patch_manifest_order_mismatch",
                "Patch filenames must encode the audited commit order",
            )
        path = out / name
        digest = hashlib.sha256(data).hexdigest()
        if str(digests.get(name) or "") != digest:
            raise ValidationIssue(
                "patch_manifest_patch_digest_mismatch",
                "Patch bytes do not match chain.json",
                path=str(path),
            )
        if digest != expected_transports[index - 1]["sha256"]:
            raise ValidationIssue(
                "patch_manifest_audited_transport_mismatch",
                "Patch bytes do not match the sealed audit transport digest",
                path=str(path),
            )
        header = data.splitlines(keepends=False)[0] if data else b""
        from_match = _PATCH_FROM_RE.match(header)
        if from_match is None or from_match.group(1).decode("ascii") != commit.sha:
            raise ValidationIssue(
                "patch_manifest_patch_commit_mismatch",
                "Patch header does not match the audited commit order",
                path=str(path),
            )
    return VerifiedPatchBundle(
        output_dir=out,
        directory_identity=directory_identity,
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        patches=patch_rows,
    )


def verify_patch_manifest(
    lease: WriterLease,
    *,
    output_dir: Path,
) -> list[Path]:
    """Verify one ordered export and return non-authoritative display paths."""
    return _read_verified_patch_bundle(lease, output_dir=output_dir).paths


def host_apply_check(
    host_checkout: Path,
    patch_paths: Sequence[Path],
    *,
    base_head: str | None = None,
    cumulative: bool = True,
    disposable: bool = False,
    lease: WriterLease | None = None,
    manifest_dir: Path | None = None,
    _verified_bundle: VerifiedPatchBundle | None = None,
) -> dict[str, Any]:
    """Run git apply --check --index for each patch without committing.

    Host checkout should be clean and at the expected base when provided.
    When ``cumulative`` is true, patches are checked in order and each subsequent
    patch is validated against the prior chain (reversed order fails).
    Cumulative validation always uses a temporary clone because later patches
    must see the prior patch's indexed tree without mutating the host. When a
    lease and manifest directory are supplied together, the consumer verifies
    the complete audited export again inside this function immediately before
    checking it.
    """
    host_authority = snapshot_git_authority(Path(host_checkout))
    host, host_git_dir, host_common_dir = _authority_paths(host_authority)
    host_static_control = snapshot_static_git_controls(
        host_git_dir,
        host_common_dir,
    )
    snapshot_runtime_git_controls(host_git_dir, host_common_dir)
    manifest_digest: str | None = None
    manifest_verified = False
    expected_worker_tree: str | None = None
    patch_transport_authority_digest: str | None = None
    persisted_audit: dict[str, Any] | None = None
    bundle: VerifiedPatchBundle | None = None
    if (lease is None) != (manifest_dir is None):
        raise ValidationIssue(
            "apply_check_manifest_arguments",
            "lease and manifest_dir must be supplied together for manifest-bound apply-check",
        )
    if lease is not None and manifest_dir is not None:
        if not cumulative:
            raise ValidationIssue(
                "apply_check_requires_cumulative_tree_proof",
                "Audited apply-check requires cumulative application and final tree proof",
            )
        bundle = _verified_bundle or _read_verified_patch_bundle(
            lease,
            output_dir=manifest_dir,
        )
        if bundle.output_dir != _normalize_export_directory_path(Path(manifest_dir)):
            raise ValidationIssue(
                "apply_check_manifest_arguments",
                "Retained patch bundle does not match the requested manifest directory",
            )
        patch_paths = bundle.paths
        manifest = bundle.manifest
        manifest_digest = str(manifest.get("manifest_digest") or "")
        patch_transport_authority_digest = hashlib.sha256(
            json.dumps(
                manifest.get("audited_patch_transport_digests"),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        commits = manifest.get("commits")
        if not isinstance(commits, list) or not commits or not isinstance(commits[-1], dict):
            raise ValidationIssue(
                "apply_check_expected_tree_missing",
                "Audited patch manifest does not contain a final candidate tree",
            )
        persisted_audit = LeaseStore(
            Path(lease.host_checkout)
        )._read_verified_audit_evidence(lease)
        audited_commits = persisted_audit.get("commit_chain")
        if (
            not isinstance(audited_commits, list)
            or not audited_commits
            or not isinstance(audited_commits[-1], dict)
        ):
            raise ValidationIssue(
                "apply_check_expected_tree_missing",
                "Persisted audit evidence does not contain a final candidate tree",
            )
        expected_worker_tree = str(audited_commits[-1].get("tree") or "")
        if str(commits[-1].get("tree") or "") != expected_worker_tree:
            raise ValidationIssue(
                "apply_check_expected_tree_mismatch",
                "Manifest candidate tree does not match persisted audit evidence",
            )
        manifest_verified = True
    patch_inputs: list[tuple[str, bytes]] = []
    total_patch_bytes = 0
    if bundle is not None:
        patch_inputs = list(bundle.patches)
    else:
        for patch in patch_paths:
            patch_path = Path(patch)
            data = _read_patch_transport_bytes(
                patch_path,
                expected_digest=None,
            )
            patch_inputs.append((patch_path.name, data))
    for _patch_name, data in patch_inputs:
        total_patch_bytes += len(data)
        if total_patch_bytes > _AUDIT_SCAN_MAX_TOTAL_BYTES:
            raise ValidationIssue(
                "apply_patch_transport_unsafe",
                "Cumulative apply patch bytes exceed the bounded audit limit",
            )
    work = host
    work_authority = host_authority
    work_static_control = host_static_control
    temp_dir: Path | None = None
    used_disposable = bool(disposable or cumulative)
    try:
        if used_disposable:
            import tempfile

            temp_dir = Path(tempfile.mkdtemp(prefix="elves-apply-check-"))
            # A failed shared clone may leave its destination behind, so the
            # non-shared fallback uses a separate path inside the same cleanup
            # authority root.
            shared_work = temp_dir / "work-shared"
            clone = subprocess.run(
                [
                    _GIT_EXECUTABLE,
                    "clone",
                    "--shared",
                    "--no-checkout",
                    str(host),
                    str(shared_work),
                ],
                capture_output=True,
                text=True,
                check=False,
                env=_hardened_git_env(),
            )
            if clone.returncode == 0:
                work = shared_work
            else:
                fallback_work = temp_dir / "work"
                clone = subprocess.run(
                    [
                        _GIT_EXECUTABLE,
                        "clone",
                        "--no-checkout",
                        str(host),
                        str(fallback_work),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    env=_hardened_git_env(),
                )
                if clone.returncode != 0:
                    raise ValidationIssue(
                        "host_disposable_clone_failed",
                        "Unable to create a disposable checkout for cumulative apply-check",
                        hint=(clone.stderr or clone.stdout or "").strip()[:400],
                    )
                work = fallback_work
            work_authority = snapshot_git_authority(work)
            work, work_git_dir, work_common_dir = _authority_paths(work_authority)
            snapshot_config(work_git_dir)
            snapshot_config(work_common_dir)
            work_static_control = snapshot_static_git_controls(
                work_git_dir,
                work_common_dir,
            )
            checkout_target = base_head or _safe_git_head(
                host,
                git_dir=host_git_dir,
                common_dir=host_common_dir,
            )
            checkout = subprocess.run(
                    [_GIT_EXECUTABLE, "checkout", "--force", checkout_target],
                    cwd=str(work),
                    capture_output=True,
                    text=True,
                    check=False,
                    env=_hardened_git_env(
                        work_tree=work,
                        git_dir=work_git_dir,
                        common_dir=work_common_dir,
                    ),
                )
            if checkout.returncode != 0:
                raise ValidationIssue(
                    "host_base_checkout_failed",
                    "Disposable apply-check could not check out the expected host base",
                    hint=(checkout.stderr or checkout.stdout or "").strip()[:400],
                )
            snapshot_runtime_git_controls(work_git_dir, work_common_dir)

        if not used_disposable:
            work_git_dir = host_git_dir
            work_common_dir = host_common_dir
        if base_head is not None and not used_disposable:
            head = _safe_git_head(
                work,
                git_dir=work_git_dir,
                common_dir=work_common_dir,
            )
            if head != base_head:
                raise ValidationIssue(
                    "host_base_mismatch",
                    f"Host HEAD `{head}` != expected base `{base_head}` for apply-check",
                )
        if not used_disposable:
            status = _safe_git_status_porcelain(
                work,
                git_dir=work_git_dir,
                common_dir=work_common_dir,
            )
            if status.strip():
                raise ValidationIssue(
                    "host_dirty",
                    "Host checkout must be clean before apply-check",
                    hint=status.strip()[:400],
                )

        checked: list[str] = []
        # Cumulative ordered check: check and then index each patch in the private
        # tree so the next patch is proved against its predecessor.
        for patch_name, patch_data in patch_inputs:
            result = subprocess.run(
                [_GIT_EXECUTABLE, "apply", "--check", "--index", "-"],
                cwd=str(work),
                check=False,
                input=patch_data,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=_hardened_git_env(
                    work_tree=work,
                    git_dir=work_git_dir,
                    common_dir=work_common_dir,
                ),
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or b"").decode(
                    "utf-8",
                    errors="replace",
                ).strip()
                raise ValidationIssue(
                    "apply_check_failed",
                    f"git apply --check --index failed for {patch_name}: {detail}",
                    path=patch_name,
                )
            if cumulative:
                applied = subprocess.run(
                    [_GIT_EXECUTABLE, "apply", "--index", "-"],
                    cwd=str(work),
                    check=False,
                    input=patch_data,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=_hardened_git_env(
                        work_tree=work,
                        git_dir=work_git_dir,
                        common_dir=work_common_dir,
                    ),
                )
                if applied.returncode != 0:
                    detail = (applied.stderr or applied.stdout or b"").decode(
                        "utf-8",
                        errors="replace",
                    ).strip()
                    raise ValidationIssue(
                        "cumulative_apply_failed",
                        f"cumulative apply failed for {patch_name}: {detail}",
                        path=patch_name,
                    )
            checked.append(patch_name)
        resulting_tree: str | None = None
        if cumulative:
            resulting_tree = _run_git(
                work,
                ["write-tree"],
                git_dir=work_git_dir,
                common_dir=work_common_dir,
            ).stdout.strip()
        if expected_worker_tree is not None and resulting_tree != expected_worker_tree:
            raise ValidationIssue(
                "apply_check_tree_mismatch",
                "Applied patch tree does not match the audited worker candidate tree",
                hint=(
                    f"expected tree {expected_worker_tree}; "
                    f"applied tree {resulting_tree or '<missing>'}"
                ),
            )
        if (
            snapshot_git_authority(work) != work_authority
            or snapshot_static_git_controls(work_git_dir, work_common_dir)
            != work_static_control
        ):
            raise ValidationIssue(
                "apply_check_git_surface_changed",
                "Disposable apply-check Git authority changed during validation",
            )
        snapshot_runtime_git_controls(work_git_dir, work_common_dir)
        return _HostApplyEvidence(
            {
                "ok": True,
                "checked": checked,
                "host_head": _safe_git_head(
                    host,
                    git_dir=host_git_dir,
                    common_dir=host_common_dir,
                ),
                "cumulative": cumulative,
                "disposable": used_disposable,
                "manifest_verified": manifest_verified,
                "manifest_digest": manifest_digest,
                "lease_id": lease.lease_id if lease is not None else None,
                "base_head": base_head,
                "worker_tip": lease.worker_tip if lease is not None else None,
                "audit_evidence_digest": (
                    lease.audit_evidence_digest if lease is not None else None
                ),
                "patch_transport_authority_digest": (
                    patch_transport_authority_digest
                ),
                "expected_worker_tree": expected_worker_tree,
                "resulting_tree": resulting_tree,
                "note": "apply-check only; host creates sanitized commits separately",
            },
            _token=_HOST_APPLY_EVIDENCE_TOKEN,
        )
    finally:
        if temp_dir is not None:
            _remove_disposable_apply_checkout(temp_dir)


def host_import_patches(
    lease: WriterLease,
    *,
    manifest_dir: Path,
) -> dict[str, Any]:
    """Import one audited bundle into the clean host via retained stdin bytes.

    The explicit import surface is separate from check-only export. It reads the
    manifest and patches once, proves those same retained bytes in disposable
    state, then applies the same bytes to the host and verifies the final tree.
    """
    if lease.state.value != "apply_checked":
        raise ValidationIssue(
            "host_import_requires_apply_checked",
            "Real host import requires an APPLY_CHECKED lease",
        )
    host_authority = snapshot_git_authority(Path(lease.host_checkout))
    host, host_git_dir, host_common_dir = _authority_paths(host_authority)
    host_static_control = snapshot_static_git_controls(
        host_git_dir,
        host_common_dir,
    )
    snapshot_runtime_git_controls(host_git_dir, host_common_dir)
    if _safe_git_head(
        host,
        git_dir=host_git_dir,
        common_dir=host_common_dir,
    ) != lease.base_head:
        raise ValidationIssue(
            "host_import_base_mismatch",
            "Host HEAD must remain at the audited base before import",
        )
    status = _safe_git_status_porcelain(
        host,
        git_dir=host_git_dir,
        common_dir=host_common_dir,
    )
    if status.strip():
        raise ValidationIssue(
            "host_import_dirty",
            "Host checkout must be clean before audited import",
            hint=redact_text(status.strip()).text[:400],
        )
    bundle = _read_verified_patch_bundle(lease, output_dir=manifest_dir)
    check = host_apply_check(
        host,
        bundle.paths,
        base_head=lease.base_head,
        cumulative=True,
        disposable=True,
        lease=lease,
        manifest_dir=manifest_dir,
        _verified_bundle=bundle,
    )
    expected_tree = str(check.get("expected_worker_tree") or "")
    if not expected_tree or check.get("resulting_tree") != expected_tree:
        raise ValidationIssue(
            "host_import_tree_proof_missing",
            "Disposable proof did not establish the audited candidate tree",
        )
    # Recheck immediately before mutation; check-only work ran in a disposable
    # clone and must not authorize import after concurrent host changes.
    if (
        _safe_git_head(
            host,
            git_dir=host_git_dir,
            common_dir=host_common_dir,
        )
        != lease.base_head
        or _safe_git_status_porcelain(
            host,
            git_dir=host_git_dir,
            common_dir=host_common_dir,
        ).strip()
    ):
        raise ValidationIssue(
            "host_import_host_changed",
            "Host checkout changed between disposable proof and real import",
        )
    if (
        snapshot_git_authority(host) != host_authority
        or snapshot_static_git_controls(host_git_dir, host_common_dir)
        != host_static_control
    ):
        raise ValidationIssue(
            "host_import_git_surface_changed",
            "Host Git authority changed between proof and import",
        )
    combined = b"\n".join(data for _name, data in bundle.patches)
    checked = subprocess.run(
        [_GIT_EXECUTABLE, "apply", "--check", "--index", "-"],
        cwd=str(host),
        check=False,
        input=combined,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_hardened_git_env(
            work_tree=host,
            git_dir=host_git_dir,
            common_dir=host_common_dir,
        ),
    )
    if checked.returncode != 0:
        detail = (checked.stderr or checked.stdout or b"").decode(
            "utf-8",
            errors="replace",
        ).strip()
        raise ValidationIssue(
            "host_import_check_failed",
            "Retained audited bundle no longer applies cleanly to the host",
            hint=redact_text(detail).text[:400],
        )
    applied = subprocess.run(
        [_GIT_EXECUTABLE, "apply", "--index", "-"],
        cwd=str(host),
        check=False,
        input=combined,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_hardened_git_env(
            work_tree=host,
            git_dir=host_git_dir,
            common_dir=host_common_dir,
        ),
    )
    if applied.returncode != 0:
        detail = (applied.stderr or applied.stdout or b"").decode(
            "utf-8",
            errors="replace",
        ).strip()
        raise ValidationIssue(
            "host_import_apply_failed",
            "Audited host import failed without promotion",
            hint=redact_text(detail).text[:400],
        )
    resulting_tree = _run_git(
        host,
        ["write-tree"],
        git_dir=host_git_dir,
        common_dir=host_common_dir,
    ).stdout.strip()
    if (
        _safe_git_head(
            host,
            git_dir=host_git_dir,
            common_dir=host_common_dir,
        )
        != lease.base_head
        or resulting_tree != expected_tree
    ):
        raise ValidationIssue(
            "host_import_tree_mismatch",
            "Real host import did not produce the audited candidate tree",
            hint=f"expected tree {expected_tree}; imported tree {resulting_tree}",
        )
    if (
        snapshot_git_authority(host) != host_authority
        or snapshot_static_git_controls(host_git_dir, host_common_dir)
        != host_static_control
    ):
        raise ValidationIssue(
            "host_import_git_surface_changed",
            "Host Git authority changed during audited import",
        )
    return {
        "ok": True,
        "mutated_repo": True,
        "lease_id": lease.lease_id,
        "base_head": lease.base_head,
        "worker_tip": lease.worker_tip,
        "checked": [name for name, _data in bundle.patches],
        "manifest_digest": bundle.manifest.get("manifest_digest"),
        "expected_worker_tree": expected_tree,
        "resulting_tree": resulting_tree,
        "note": "host index/worktree now contain the audited tree; host must validate and commit",
    }


def pre_turn_snapshots(worker_checkout: Path) -> dict[str, Any]:
    """Capture canonical raw pre-turn surfaces for post comparison."""
    authority = snapshot_git_authority(worker_checkout)
    worker, gdir, common = _authority_paths(authority)
    config = snapshot_config(gdir)
    hooks = snapshot_hooks(gdir)
    common_config = snapshot_config(common)
    common_hooks = snapshot_hooks(common)
    ref_storage = snapshot_ref_storage(
        gdir,
        common,
        include_pseudorefs=False,
    )
    static_control = snapshot_static_git_controls(gdir, common)
    runtime_before = _snapshot_runtime_git_controls_exact(gdir, common)
    _snapshot_index_semantics(worker, gdir, common)
    refs_digest = _safe_refs_digest(worker, git_dir=gdir, common_dir=common)
    remotes = snapshot_remotes(worker, git_dir=gdir, common_dir=common)
    head = _safe_git_head(worker, git_dir=gdir, common_dir=common)
    status_porcelain = _safe_git_status_porcelain(
        worker,
        git_dir=gdir,
        common_dir=common,
    )
    if runtime_before != _snapshot_runtime_git_controls_exact(gdir, common):
        raise ValidationIssue(
            "audit_pre_snapshot_race",
            "Worker index changed while preparing the lease snapshot",
        )
    return {
        "refs_digest": refs_digest,
        "remotes": remotes,
        "config": config,
        "hooks": hooks,
        "common_config": common_config,
        "common_hooks": common_hooks,
        "ref_storage": ref_storage,
        "git_dir": str(gdir),
        "git_common_dir": str(common),
        "head": head,
        "status_porcelain": status_porcelain,
        "authority": authority,
        "static_control": static_control,
    }


def build_worker_pre_snapshot(lease: WriterLease) -> dict[str, Any]:
    """Build the exact versioned prepare snapshot bound to one lease identity."""
    payload = pre_turn_snapshots(Path(lease.worker_checkout))
    payload.update(
        {
            "version": _WORKER_PRE_SNAPSHOT_VERSION,
            "lease_id": lease.lease_id,
            "session_id": lease.session_id,
            "base_head": lease.base_head,
            "worker_checkout": str(Path(lease.worker_checkout).resolve()),
        }
    )
    return validate_worker_pre_snapshot(lease, payload)


def _validate_git_surface_identity(value: Any) -> None:
    keys = {"dev", "ino", "mode", "nlink", "size", "mtime_ns", "ctime_ns"}
    if not isinstance(value, dict) or set(value) != keys or any(
        isinstance(item, bool) or not isinstance(item, int) or item < 0
        for item in value.values()
    ):
        raise ValidationIssue(
            "audit_pre_snapshot_malformed",
            "Git surface identity fields are not canonical non-negative integers",
        )


def _validate_git_surface_node(value: Any) -> None:
    if not isinstance(value, dict) or not isinstance(value.get("type"), str):
        raise ValidationIssue(
            "audit_pre_snapshot_malformed",
            "Git surface snapshot nodes must be canonical objects",
        )
    node_type = value["type"]
    if node_type == "missing":
        if value != {"type": "missing"}:
            raise ValidationIssue(
                "audit_pre_snapshot_malformed",
                "Missing Git surface nodes may not carry extra fields",
            )
        return
    if node_type == "file":
        if set(value) != {"type", "identity", "sha256"}:
            raise ValidationIssue(
                "audit_pre_snapshot_malformed",
                "File Git surface nodes have non-canonical fields",
            )
        _validate_git_surface_identity(value["identity"])
        if not isinstance(value["sha256"], str) or not _SHA256_RE.fullmatch(
            value["sha256"]
        ):
            raise ValidationIssue(
                "audit_pre_snapshot_malformed",
                "File Git surface digest is malformed",
            )
        return
    if node_type == "directory":
        if set(value) != {"type", "identity", "entries"}:
            raise ValidationIssue(
                "audit_pre_snapshot_malformed",
                "Directory Git surface nodes have non-canonical fields",
            )
        _validate_git_surface_identity(value["identity"])
        entries = value["entries"]
        if not isinstance(entries, dict) or not all(
            isinstance(name, str) and name not in {"", ".", ".."} and "/" not in name
            for name in entries
        ):
            raise ValidationIssue(
                "audit_pre_snapshot_malformed",
                "Directory Git surface entries are malformed",
            )
        for child in entries.values():
            _validate_git_surface_node(child)
        return
    raise ValidationIssue(
        "audit_pre_snapshot_malformed",
        "Unknown Git surface snapshot node type",
    )


def _validate_config_surface(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {"config", "config.worktree"}:
        raise ValidationIssue(
            "audit_pre_snapshot_malformed",
            "Git config snapshot fields are not canonical",
        )
    _validate_git_surface_node(value["config"])
    _validate_git_surface_node(value["config.worktree"])


def _validate_stable_ref_surface(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {"worktree", "common"}:
        raise ValidationIssue(
            "audit_pre_snapshot_malformed",
            "Git ref snapshot roots are not canonical",
        )
    keys = {"packed-refs", "packed-refs.lock", "refs", "reftable"}
    for root in value.values():
        if not isinstance(root, dict) or set(root) != keys:
            raise ValidationIssue(
                "audit_pre_snapshot_malformed",
                "Git ref snapshot fields are not canonical",
            )
        for node in root.values():
            _validate_git_surface_node(node)


def _validate_stable_directory_node(value: Any) -> None:
    identity_keys = {"dev", "ino", "mode", "uid"}
    if (
        not isinstance(value, dict)
        or set(value) != {"type", "identity"}
        or value.get("type") != "directory"
        or not isinstance(value.get("identity"), dict)
        or set(value["identity"]) != identity_keys
        or any(
            isinstance(item, bool) or not isinstance(item, int) or item < 0
            for item in value["identity"].values()
        )
    ):
        raise ValidationIssue(
            "audit_pre_snapshot_malformed",
            "Git directory authority is not canonical",
        )


def _validate_git_authority_surface(value: Any) -> None:
    keys = {
        "worker_checkout",
        "worker_root",
        "git_locator",
        "git_dir",
        "git_dir_root",
        "common_dir",
        "common_dir_root",
        "commondir_locator",
        "gitdir_locator",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise ValidationIssue(
            "audit_pre_snapshot_malformed",
            "Git authority fields are not canonical",
        )
    for key in ("worker_checkout", "git_dir", "common_dir"):
        if not isinstance(value[key], str) or not Path(value[key]).is_absolute():
            raise ValidationIssue(
                "audit_pre_snapshot_malformed",
                "Git authority paths must be absolute strings",
            )
    for key in ("worker_root", "git_dir_root", "common_dir_root"):
        _validate_stable_directory_node(value[key])
    git_locator = value["git_locator"]
    if isinstance(git_locator, dict) and git_locator.get("type") == "directory":
        _validate_stable_directory_node(git_locator)
    else:
        _validate_git_surface_node(git_locator)
    _validate_git_surface_node(value["commondir_locator"])
    _validate_git_surface_node(value["gitdir_locator"])


def _validate_static_control_surface(value: Any) -> None:
    relative_paths = {
        "shallow",
        "info/grafts",
        "info/attributes",
        "info/exclude",
        "info/sparse-checkout",
        "objects/info/alternates",
        "objects/info/http-alternates",
    }
    if not isinstance(value, dict) or set(value) != {"worktree", "common"}:
        raise ValidationIssue(
            "audit_pre_snapshot_malformed",
            "Git static control roots are not canonical",
        )
    for root in value.values():
        if not isinstance(root, dict) or set(root) != relative_paths:
            raise ValidationIssue(
                "audit_pre_snapshot_malformed",
                "Git static control fields are not canonical",
            )
        for node in root.values():
            _validate_git_surface_node(node)


def validate_worker_pre_snapshot(
    lease: WriterLease,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate exact pre.json schema, types, and lease identity fail-closed."""
    if not isinstance(payload, dict) or set(payload) != _PRE_SNAPSHOT_KEYS:
        raise ValidationIssue(
            "audit_pre_snapshot_malformed",
            "pre.json fields do not match the canonical versioned schema",
        )
    expected_identity = {
        "version": _WORKER_PRE_SNAPSHOT_VERSION,
        "lease_id": lease.lease_id,
        "session_id": lease.session_id,
        "base_head": lease.base_head,
        "worker_checkout": str(Path(lease.worker_checkout).resolve()),
        "head": lease.base_head,
        "status_porcelain": "",
    }
    if any(payload.get(key) != value for key, value in expected_identity.items()):
        raise ValidationIssue(
            "audit_pre_snapshot_identity_mismatch",
            "pre.json identity does not match the prepared clean lease",
        )
    for key in ("refs_digest", "remotes"):
        value = payload.get(key)
        if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
            raise ValidationIssue(
                "audit_pre_snapshot_malformed",
                f"pre.json {key} must be a canonical SHA-256 digest",
            )
    for key in ("git_dir", "git_common_dir"):
        if not isinstance(payload.get(key), str) or not payload[key]:
            raise ValidationIssue(
                "audit_pre_snapshot_malformed",
                f"pre.json {key} must be a non-empty string",
            )
    if any(not Path(payload[key]).is_absolute() for key in ("git_dir", "git_common_dir")):
        raise ValidationIssue(
            "audit_pre_snapshot_malformed",
            "pre.json Git directory identities must be absolute paths",
        )
    _validate_config_surface(payload["config"])
    _validate_config_surface(payload["common_config"])
    _validate_git_surface_node(payload["hooks"])
    _validate_git_surface_node(payload["common_hooks"])
    _validate_stable_ref_surface(payload["ref_storage"])
    _validate_git_authority_surface(payload["authority"])
    if (
        payload["authority"]["worker_checkout"] != expected_identity["worker_checkout"]
        or payload["authority"]["git_dir"] != payload["git_dir"]
        or payload["authority"]["common_dir"] != payload["git_common_dir"]
    ):
        raise ValidationIssue(
            "audit_pre_snapshot_identity_mismatch",
            "Git authority does not match the prepared lease paths",
        )
    _validate_static_control_surface(payload["static_control"])
    return dict(payload)
