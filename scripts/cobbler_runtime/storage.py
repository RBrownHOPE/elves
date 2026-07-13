"""Shared storage primitives for session/lease/audit records.

- Collision-free digest-based record keys
- Embedded-ID verification on read
- Atomic mode-0600 JSON writes
- Revision-aware updates
- Common-directory locking (fcntl when available)
- Safe snapshot path resolution (no raw ID path traversal)
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import secrets
import stat
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator, Mapping, TextIO


try:
    import fcntl
except ImportError:  # pragma: no cover - non-Unix
    fcntl = None  # type: ignore[assignment]


DEFAULT_JSON_MAX_BYTES = 4 * 1024 * 1024
DEFAULT_TAIL_MAX_BYTES = 256 * 1024
DEFAULT_TAIL_MAX_LINES = 100

_LINUX_RENAME_NOREPLACE = 1
_DARWIN_RENAME_EXCL = 0x00000004


class StorageError(Exception):
    """Fail-closed storage boundary error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@lru_cache(maxsize=1)
def _load_atomic_noreplace_rename() -> tuple[Any, Any, int, str] | None:
    """Load one descriptor-relative native no-replace rename primitive.

    Keep the library object in the cached tuple so the typed function pointer
    remains valid. Unsupported platforms or missing libc symbols fail closed;
    callers must never emulate this with a separate existence check.
    """
    if sys.platform.startswith("linux"):
        symbol = "renameat2"
        flag = _LINUX_RENAME_NOREPLACE
    elif sys.platform == "darwin":
        symbol = "renameatx_np"
        flag = _DARWIN_RENAME_EXCL
    else:
        return None
    try:
        library = ctypes.CDLL(None, use_errno=True)
        function = getattr(library, symbol)
    except (AttributeError, OSError):
        return None
    function.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    function.restype = ctypes.c_int
    return library, function, flag, symbol


def _atomic_rename_noreplace_at(
    source_parent_fd: int,
    source_name: str,
    destination_parent_fd: int,
    destination_name: str,
) -> None:
    """Atomically rename one leaf without replacement, relative to open parents."""
    native = _load_atomic_noreplace_rename()
    if native is None:
        raise StorageError(
            "atomic_noreplace_unsupported",
            "This platform has no supported atomic no-replace rename primitive",
        )
    _library, function, flag, symbol = native
    source_bytes = os.fsencode(source_name)
    destination_bytes = os.fsencode(destination_name)
    if b"\0" in source_bytes or b"\0" in destination_bytes:
        raise StorageError(
            "invalid_leaf_name",
            "Atomic no-replace rename leaf names must not contain NUL",
        )

    ctypes.set_errno(0)
    result = function(
        source_parent_fd,
        source_bytes,
        destination_parent_fd,
        destination_bytes,
        flag,
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number == errno.EEXIST:
        raise StorageError(
            "destination_exists",
            "Atomic no-replace move destination already exists",
        )
    if error_number == errno.EXDEV:
        raise StorageError(
            "cross_device_move",
            "Atomic no-replace store moves require one filesystem",
        )
    unsupported_errors = {
        errno.ENOSYS,
        errno.EINVAL,
        getattr(errno, "ENOTSUP", errno.EINVAL),
        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
    }
    if error_number in unsupported_errors:
        raise StorageError(
            "atomic_noreplace_unsupported",
            f"{symbol} does not support atomic no-replace rename here",
        )
    detail = os.strerror(error_number) if error_number else "unknown native error"
    raise StorageError(
        "atomic_noreplace_failed",
        f"{symbol} failed; refusing a non-atomic fallback: {detail}",
    )


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


def guard_repo_path(repo_root: Path, path: Path) -> Path:
    """Return a lexical repo-contained path after rejecting existing symlink components.

    ``Path.resolve()`` is deliberately not applied to ``path``: resolving an attacker-
    controlled ``.elves`` symlink would turn an escape into an apparently valid target.
    The canonical repository root is the authority boundary; every existing component
    below it must be a non-symlink before callers may read or mutate the store.
    """
    raw_root = Path(repo_root).expanduser()
    lexical_root = Path(os.path.abspath(os.path.normpath(os.fspath(raw_root))))
    try:
        root = raw_root.resolve(strict=True)
    except OSError as exc:
        raise StorageError(
            "repo_root_unavailable",
            f"Repository root is unavailable: {repo_root}: {exc}",
        ) from exc
    if not root.is_dir():
        raise StorageError("repo_root_not_directory", f"Repository root is not a directory: {root}")

    raw = Path(path).expanduser()
    if not raw.is_absolute():
        raw = root / raw
    candidate = Path(os.path.abspath(os.path.normpath(os.fspath(raw))))
    # macOS commonly exposes /var as a symlink to /private/var, and operators may
    # intentionally enter a checkout through a symlink. Map only a lexically
    # contained candidate onto the canonical anchor; never resolve candidate
    # components below the repository.
    try:
        lexical_relative = candidate.relative_to(lexical_root)
    except ValueError:
        pass
    else:
        candidate = root / lexical_relative
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise StorageError(
            "path_escape",
            f"Store path escapes repository root: {candidate} (root: {root})",
        ) from exc

    cursor = root
    parts = relative.parts
    for index, part in enumerate(parts):
        cursor = cursor / part
        try:
            info = os.lstat(cursor)
        except FileNotFoundError:
            # Descendants cannot exist lexically once an ancestor is absent.
            break
        except OSError as exc:
            raise StorageError(
                "path_inspection_failed",
                f"Cannot inspect store path component {cursor}: {exc}",
            ) from exc
        if stat.S_ISLNK(info.st_mode):
            raise StorageError(
                "symlink_component",
                f"Store path contains a symlink component: {cursor}",
            )
        if index < len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
            raise StorageError(
                "non_directory_component",
                f"Store path ancestor is not a directory: {cursor}",
            )
    return candidate


def _repo_open_flags(*, directory: bool = False) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    return flags


def _assert_directory_fd_identity(
    repo_root: Path,
    path: Path,
    directory_fd: int,
) -> None:
    """Require an opened directory to remain published at its guarded path."""
    candidate = guard_repo_path(repo_root, path)
    try:
        opened = os.fstat(directory_fd)
        published = os.stat(candidate, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise StorageError(
            "directory_identity_changed",
            f"Store directory disappeared while open: {candidate}",
        ) from exc
    except OSError as exc:
        raise StorageError(
            "path_inspection_failed",
            f"Cannot verify open store directory {candidate}: {exc}",
        ) from exc
    if not stat.S_ISDIR(opened.st_mode) or not stat.S_ISDIR(published.st_mode):
        raise StorageError(
            "directory_identity_changed",
            f"Store directory is no longer a regular directory: {candidate}",
        )
    if (opened.st_dev, opened.st_ino) != (published.st_dev, published.st_ino):
        raise StorageError(
            "directory_identity_changed",
            f"Store directory identity changed while open: {candidate}",
        )


def _open_repo_directory(
    repo_root: Path,
    path: Path,
    *,
    create: bool,
    mode: int = 0o700,
) -> tuple[Path, int]:
    """Open a guarded directory chain with ``openat``-style no-follow traversal."""
    candidate = guard_repo_path(repo_root, path)
    root = Path(repo_root).expanduser().resolve(strict=True)
    relative = candidate.relative_to(root)
    current_fd = os.open(root, _repo_open_flags(directory=True))
    try:
        for index, part in enumerate(relative.parts):
            if create:
                try:
                    os.mkdir(part, mode=mode, dir_fd=current_fd)
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise StorageError(
                        "directory_create_failed",
                        f"Cannot create private store directory {candidate}: {exc}",
                    ) from exc
            try:
                next_fd = os.open(part, _repo_open_flags(directory=True), dir_fd=current_fd)
            except FileNotFoundError as exc:
                raise StorageError("not_found", f"Missing store directory: {candidate}") from exc
            except OSError as exc:
                code = (
                    "symlink_component"
                    if exc.errno == errno.ELOOP
                    else "unsafe_path_component"
                )
                raise StorageError(
                    code,
                    f"Cannot open store directory component without following links: {candidate}: {exc}",
                ) from exc
            component = root.joinpath(*relative.parts[: index + 1])
            try:
                _assert_directory_fd_identity(repo_root, component, next_fd)
            except Exception:
                os.close(next_fd)
                raise
            os.close(current_fd)
            current_fd = next_fd
        if create and relative.parts:
            try:
                os.fchmod(current_fd, mode)
            except OSError as exc:
                raise StorageError(
                    "private_dir_mode_failed",
                    f"Cannot enforce private mode on store directory {candidate}: {exc}",
                ) from exc
        return candidate, current_fd
    except Exception:
        os.close(current_fd)
        raise


def _assert_safe_regular_leaf(parent_fd: int, name: str, *, display_path: Path) -> os.stat_result | None:
    try:
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise StorageError(
            "path_inspection_failed",
            f"Cannot inspect store leaf {display_path}: {exc}",
        ) from exc
    if stat.S_ISLNK(info.st_mode):
        raise StorageError("symlink_leaf", f"Store leaf must not be a symlink: {display_path}")
    if not stat.S_ISREG(info.st_mode):
        raise StorageError(
            "unsafe_file_type",
            f"Store leaf must be a regular file: {display_path}",
        )
    if info.st_nlink != 1:
        raise StorageError(
            "unsafe_link_count",
            f"Store leaf must have exactly one hard link: {display_path}",
        )
    return info


def ensure_private_dir(
    path: Path,
    *,
    mode: int = 0o700,
    repo_root: Path | None = None,
) -> Path:
    if repo_root is not None:
        candidate, directory_fd = _open_repo_directory(
            repo_root,
            path,
            create=True,
            mode=mode,
        )
        os.close(directory_fd)
        return candidate
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
    repo_root: Path | None = None,
) -> None:
    """Write JSON via temp file + os.replace with private permissions."""
    payload = json.dumps(dict(data), indent=2, sort_keys=True) + "\n"
    if repo_root is not None:
        candidate = guard_repo_path(repo_root, path)
        if candidate == Path(repo_root).expanduser().resolve(strict=True):
            raise StorageError("invalid_store_leaf", "Repository root cannot be a JSON store leaf")
        parent, parent_fd = _open_repo_directory(
            repo_root,
            candidate.parent,
            create=True,
        )
        del parent
        leaf = candidate.name
        _assert_safe_regular_leaf(parent_fd, leaf, display_path=candidate)
        temporary_name = f".{leaf}.{secrets.token_hex(12)}.tmp"
        temporary_fd: int | None = None
        try:
            temporary_fd = os.open(
                temporary_name,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                mode,
                dir_fd=parent_fd,
            )
            os.fchmod(temporary_fd, mode)
            with os.fdopen(temporary_fd, "w", encoding="utf-8") as handle:
                temporary_fd = None
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            # Recheck immediately before replacement. Replacing a raced symlink is
            # outside-safe, but a symlink observed here is still a fail-closed error.
            _assert_safe_regular_leaf(parent_fd, leaf, display_path=candidate)
            os.replace(
                temporary_name,
                leaf,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
        finally:
            if temporary_fd is not None:
                os.close(temporary_fd)
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            finally:
                os.close(parent_fd)
        return

    path.parent.mkdir(parents=True, exist_ok=True)
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


def _validate_size_limit(max_bytes: int) -> int:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0:
        raise StorageError(
            "invalid_size_limit",
            f"max_bytes must be a non-negative integer, got {max_bytes!r}",
        )
    return max_bytes


def _read_unanchored_bounded_bytes(path: Path, *, max_bytes: int) -> bytes:
    """Compatibility path for callers without a repository authority anchor."""
    limit = _validate_size_limit(max_bytes)
    try:
        with path.open("rb") as handle:
            info = os.fstat(handle.fileno())
            if info.st_size > limit:
                raise StorageError(
                    "record_too_large",
                    f"Store record exceeds {limit} bytes: {path}",
                )
            payload = handle.read(limit + 1)
    except StorageError:
        raise
    except FileNotFoundError as exc:
        raise StorageError("not_found", f"Missing record: {path}") from exc
    except OSError as exc:
        raise StorageError(
            "read_failed",
            f"Cannot read store record {path}: {type(exc).__name__}: {exc}",
        ) from exc
    if len(payload) > limit:
        raise StorageError(
            "record_too_large",
            f"Store record exceeds {limit} bytes: {path}",
        )
    return payload


def read_json(
    path: Path,
    *,
    repo_root: Path | None = None,
    max_bytes: int = DEFAULT_JSON_MAX_BYTES,
) -> dict[str, Any]:
    """Read one bounded UTF-8 JSON object with stable storage errors."""
    candidate = Path(path)
    try:
        if repo_root is not None:
            raw = read_repo_regular_bytes(
                repo_root,
                candidate,
                max_bytes=max_bytes,
            )
            display_path = guard_repo_path(repo_root, candidate)
        else:
            raw = _read_unanchored_bounded_bytes(candidate, max_bytes=max_bytes)
            display_path = candidate
    except StorageError:
        raise
    except OSError as exc:
        raise StorageError(
            "read_failed",
            f"Cannot read store record {candidate}: {type(exc).__name__}: {exc}",
        ) from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StorageError(
            "invalid_utf8",
            f"Store record is not valid UTF-8: {display_path}",
        ) from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StorageError(
            "malformed_json",
            f"Malformed JSON at {display_path}: {exc}",
        ) from exc
    if not isinstance(data, dict):
        raise StorageError("malformed_json", f"JSON object required at {display_path}")
    return data


def read_repo_regular_bytes(
    repo_root: Path,
    path: Path,
    *,
    max_bytes: int,
) -> bytes:
    """Read a bounded repo-owned regular file through no-follow descriptors.

    Stable boundary errors are ``invalid_size_limit``, ``not_found``,
    ``unsafe_store_leaf``, ``unsafe_file_type``, ``unsafe_link_count``, and
    ``record_too_large``.
    Existing symlink components are rejected earlier as ``symlink_component``.
    """
    limit = _validate_size_limit(max_bytes)
    candidate = guard_repo_path(repo_root, path)
    root = Path(repo_root).expanduser().resolve(strict=True)
    if candidate == root:
        raise StorageError("unsafe_file_type", f"Store leaf must be a regular file: {candidate}")
    try:
        _parent, parent_fd = _open_repo_directory(
            repo_root,
            candidate.parent,
            create=False,
        )
    except StorageError as exc:
        if exc.code == "not_found":
            raise StorageError("not_found", f"Missing record: {candidate}") from exc
        raise
    file_fd = -1
    try:
        _assert_directory_fd_identity(repo_root, candidate.parent, parent_fd)
        before = _assert_safe_regular_leaf(
            parent_fd,
            candidate.name,
            display_path=candidate,
        )
        if before is None:
            raise StorageError("not_found", f"Missing record: {candidate}")
        try:
            file_fd = os.open(candidate.name, _repo_open_flags(), dir_fd=parent_fd)
        except FileNotFoundError as exc:
            raise StorageError("not_found", f"Missing record: {candidate}") from exc
        except OSError as exc:
            raise StorageError(
                "unsafe_store_leaf",
                f"Cannot open store record without following links: {candidate}: {exc}",
            ) from exc
        opened = os.fstat(file_fd)
        if not stat.S_ISREG(opened.st_mode):
            raise StorageError(
                "unsafe_file_type",
                f"Store record must be a regular file: {candidate}",
            )
        if opened.st_nlink != 1:
            raise StorageError(
                "unsafe_link_count",
                f"Store record must have exactly one hard link: {candidate}",
            )
        published = _assert_safe_regular_leaf(
            parent_fd,
            candidate.name,
            display_path=candidate,
        )
        if published is None or (opened.st_dev, opened.st_ino) != (
            published.st_dev,
            published.st_ino,
        ) or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise StorageError(
                "file_identity_changed",
                f"Store record identity changed while opening: {candidate}",
            )
        if opened.st_size > limit:
            raise StorageError(
                "record_too_large",
                f"Store record exceeds {limit} bytes: {candidate}",
            )
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining > 0:
            try:
                chunk = os.read(file_fd, min(64 * 1024, remaining))
            except OSError as exc:
                raise StorageError(
                    "read_failed",
                    f"Cannot read store record {candidate}: {type(exc).__name__}: {exc}",
                ) from exc
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > limit:
            raise StorageError(
                "record_too_large",
                f"Store record exceeds {limit} bytes: {candidate}",
            )
        after = os.fstat(file_fd)
        published_after = _assert_safe_regular_leaf(
            parent_fd,
            candidate.name,
            display_path=candidate,
        )
        if (
            not stat.S_ISREG(after.st_mode)
            or after.st_nlink != 1
            or published_after is None
            or (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
            or (published_after.st_dev, published_after.st_ino)
            != (opened.st_dev, opened.st_ino)
        ):
            raise StorageError(
                "file_identity_changed",
                f"Store record identity changed while reading: {candidate}",
            )
        _assert_directory_fd_identity(repo_root, candidate.parent, parent_fd)
        return payload
    except StorageError:
        raise
    except OSError as exc:
        raise StorageError(
            "read_failed",
            f"Cannot read store record {candidate}: {type(exc).__name__}: {exc}",
        ) from exc
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        os.close(parent_fd)


def read_repo_text_tail(
    repo_root: Path,
    path: Path,
    *,
    max_bytes: int = DEFAULT_TAIL_MAX_BYTES,
    max_lines: int = DEFAULT_TAIL_MAX_LINES,
) -> list[str]:
    """Read a bounded UTF-8 suffix through repo-anchored no-follow descriptors.

    A nonzero suffix offset may begin mid-line; that fragment is discarded.
    Tail output is diagnostic text, so invalid or boundary-split UTF-8 is
    replacement-decoded rather than treated as a record-format error.
    """
    byte_limit = _validate_size_limit(max_bytes)
    if isinstance(max_lines, bool) or not isinstance(max_lines, int) or max_lines < 0:
        raise StorageError(
            "invalid_line_limit",
            f"max_lines must be a non-negative integer, got {max_lines!r}",
        )
    candidate = guard_repo_path(repo_root, path)
    root = Path(repo_root).expanduser().resolve(strict=True)
    if candidate == root:
        raise StorageError("unsafe_file_type", f"Store leaf must be a regular file: {candidate}")
    try:
        _parent, parent_fd = _open_repo_directory(
            repo_root,
            candidate.parent,
            create=False,
        )
    except StorageError as exc:
        if exc.code == "not_found":
            raise StorageError("not_found", f"Missing record: {candidate}") from exc
        raise
    file_fd = -1
    try:
        _assert_directory_fd_identity(repo_root, candidate.parent, parent_fd)
        before = _assert_safe_regular_leaf(
            parent_fd,
            candidate.name,
            display_path=candidate,
        )
        if before is None:
            raise StorageError("not_found", f"Missing record: {candidate}")
        try:
            file_fd = os.open(candidate.name, _repo_open_flags(), dir_fd=parent_fd)
        except FileNotFoundError as exc:
            raise StorageError("not_found", f"Missing record: {candidate}") from exc
        except OSError as exc:
            raise StorageError(
                "unsafe_store_leaf",
                f"Cannot open store tail without following links: {candidate}: {exc}",
            ) from exc
        opened = os.fstat(file_fd)
        published = _assert_safe_regular_leaf(
            parent_fd,
            candidate.name,
            display_path=candidate,
        )
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or published is None
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or (published.st_dev, published.st_ino)
            != (opened.st_dev, opened.st_ino)
        ):
            raise StorageError(
                "file_identity_changed",
                f"Store tail identity changed while opening: {candidate}",
            )
        start = max(0, opened.st_size - byte_limit)
        os.lseek(file_fd, start, os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = byte_limit
        while remaining > 0:
            chunk = os.read(file_fd, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(file_fd)
        published_after = _assert_safe_regular_leaf(
            parent_fd,
            candidate.name,
            display_path=candidate,
        )
        if (
            not stat.S_ISREG(after.st_mode)
            or after.st_nlink != 1
            or published_after is None
            or (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
            or (published_after.st_dev, published_after.st_ino)
            != (opened.st_dev, opened.st_ino)
        ):
            raise StorageError(
                "file_identity_changed",
                f"Store tail identity changed while reading: {candidate}",
            )
        _assert_directory_fd_identity(repo_root, candidate.parent, parent_fd)
    except StorageError:
        raise
    except OSError as exc:
        raise StorageError(
            "tail_read_failed",
            f"Cannot read store tail {candidate}: {type(exc).__name__}: {exc}",
        ) from exc
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        os.close(parent_fd)

    if max_lines == 0 or byte_limit == 0:
        return []
    lines = raw.splitlines()
    if start and lines:
        lines = lines[1:]
    return [line.decode("utf-8", errors="replace") for line in lines[-max_lines:]]


def move_repo_regular_file(
    repo_root: Path,
    source: Path,
    destination: Path,
    *,
    replace: bool = False,
) -> Path:
    """Atomically move one regular single-link leaf within the same repository."""
    lexical_root = Path(repo_root).expanduser()
    source_path = guard_repo_path(lexical_root, source)
    destination_path = guard_repo_path(lexical_root, destination)
    root = lexical_root.resolve(strict=True)
    if source_path == destination_path:
        raise StorageError("same_path", f"Source and destination are identical: {source_path}")
    if source_path == root or destination_path == root:
        raise StorageError("unsafe_file_type", "Repository root cannot be moved as a store leaf")

    try:
        _source_parent, source_parent_fd = _open_repo_directory(
            root,
            source_path.parent,
            create=False,
        )
    except StorageError as exc:
        if exc.code == "not_found":
            raise StorageError("not_found", f"Missing move source: {source_path}") from exc
        raise
    destination_parent_fd = -1
    source_fd = -1
    try:
        try:
            _destination_parent, destination_parent_fd = _open_repo_directory(
                root,
                destination_path.parent,
                create=False,
            )
        except StorageError as exc:
            if exc.code == "not_found":
                raise StorageError(
                    "not_found",
                    f"Missing move destination directory: {destination_path.parent}",
                ) from exc
            raise

        _assert_directory_fd_identity(root, source_path.parent, source_parent_fd)
        _assert_directory_fd_identity(root, destination_path.parent, destination_parent_fd)
        source_before = _assert_safe_regular_leaf(
            source_parent_fd,
            source_path.name,
            display_path=source_path,
        )
        if source_before is None:
            raise StorageError("not_found", f"Missing move source: {source_path}")
        destination_before = _assert_safe_regular_leaf(
            destination_parent_fd,
            destination_path.name,
            display_path=destination_path,
        )
        if destination_before is not None and not replace:
            raise StorageError(
                "destination_exists",
                f"Move destination already exists: {destination_path}",
            )
        try:
            source_fd = os.open(
                source_path.name,
                _repo_open_flags(),
                dir_fd=source_parent_fd,
            )
        except FileNotFoundError as exc:
            raise StorageError("not_found", f"Missing move source: {source_path}") from exc
        except OSError as exc:
            raise StorageError(
                "unsafe_store_leaf",
                f"Cannot open move source without following links: {source_path}: {exc}",
            ) from exc
        source_opened = os.fstat(source_fd)
        source_published = _assert_safe_regular_leaf(
            source_parent_fd,
            source_path.name,
            display_path=source_path,
        )
        if (
            not stat.S_ISREG(source_opened.st_mode)
            or source_opened.st_nlink != 1
            or source_published is None
            or (source_opened.st_dev, source_opened.st_ino)
            != (source_before.st_dev, source_before.st_ino)
            or (source_published.st_dev, source_published.st_ino)
            != (source_opened.st_dev, source_opened.st_ino)
        ):
            raise StorageError(
                "file_identity_changed",
                f"Move source identity changed while opening: {source_path}",
            )

        destination_now = _assert_safe_regular_leaf(
            destination_parent_fd,
            destination_path.name,
            display_path=destination_path,
        )
        if destination_before is None and destination_now is not None:
            raise StorageError(
                "destination_identity_changed",
                f"Move destination appeared during validation: {destination_path}",
            )
        if destination_before is not None and (
            destination_now is None
            or (destination_now.st_dev, destination_now.st_ino)
            != (destination_before.st_dev, destination_before.st_ino)
        ):
            raise StorageError(
                "destination_identity_changed",
                f"Move destination identity changed during validation: {destination_path}",
            )
        _assert_directory_fd_identity(root, source_path.parent, source_parent_fd)
        _assert_directory_fd_identity(root, destination_path.parent, destination_parent_fd)
        if replace:
            os.replace(
                source_path.name,
                destination_path.name,
                src_dir_fd=source_parent_fd,
                dst_dir_fd=destination_parent_fd,
            )
        else:
            # The native syscall is the only publication boundary. It either
            # moves the source name or reports EEXIST while leaving both names
            # untouched; there is no racy precheck+rename or link+unlink path.
            _atomic_rename_noreplace_at(
                source_parent_fd,
                source_path.name,
                destination_parent_fd,
                destination_path.name,
            )

        _assert_directory_fd_identity(root, source_path.parent, source_parent_fd)
        _assert_directory_fd_identity(root, destination_path.parent, destination_parent_fd)
        destination_after = _assert_safe_regular_leaf(
            destination_parent_fd,
            destination_path.name,
            display_path=destination_path,
        )
        source_after = _assert_safe_regular_leaf(
            source_parent_fd,
            source_path.name,
            display_path=source_path,
        )
        source_opened_after = os.fstat(source_fd)
        if (
            destination_after is None
            or source_after is not None
            or source_opened_after.st_nlink != 1
            or (destination_after.st_dev, destination_after.st_ino)
            != (source_opened.st_dev, source_opened.st_ino)
        ):
            raise StorageError(
                "move_verification_failed",
                f"Moved store leaf could not be verified: {source_path} -> {destination_path}",
            )
        return destination_path
    except StorageError:
        raise
    except OSError as exc:
        raise StorageError(
            "move_failed",
            f"Cannot move store leaf {source_path} -> {destination_path}: "
            f"{type(exc).__name__}: {exc}",
        ) from exc
    finally:
        if source_fd >= 0:
            os.close(source_fd)
        if destination_parent_fd >= 0:
            os.close(destination_parent_fd)
        os.close(source_parent_fd)


@contextmanager
def open_repo_text(
    repo_root: Path,
    path: Path,
    *,
    mode: str = "r",
    permissions: int = 0o600,
) -> Iterator[TextIO]:
    """Open a repo-owned regular UTF-8 text leaf for safe read/append/truncate.

    The accepted modes are exactly ``r``, ``a``, and ``w``. Writable opens
    create missing private parents/leaves, validate the published inode, and
    truncate only *after* identity validation.
    """
    if mode not in {"r", "a", "w"}:
        raise StorageError(
            "invalid_open_mode",
            f"Repo text mode must be one of 'r', 'a', or 'w', got {mode!r}",
        )
    if (
        isinstance(permissions, bool)
        or not isinstance(permissions, int)
        or permissions < 0
        or permissions > 0o777
    ):
        raise StorageError(
            "invalid_permissions",
            f"permissions must be an integer mode, got {permissions!r}",
        )
    candidate = guard_repo_path(repo_root, path)
    root = Path(repo_root).expanduser().resolve(strict=True)
    if candidate == root:
        raise StorageError("unsafe_file_type", f"Store leaf must be a regular file: {candidate}")
    create = mode in {"a", "w"}
    try:
        _parent, parent_fd = _open_repo_directory(
            repo_root,
            candidate.parent,
            create=create,
        )
    except StorageError as exc:
        if exc.code == "not_found":
            raise StorageError("not_found", f"Missing record: {candidate}") from exc
        raise

    file_fd = -1
    handle: TextIO | None = None
    try:
        before = _assert_safe_regular_leaf(parent_fd, candidate.name, display_path=candidate)
        flags = getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        if mode == "r":
            flags |= os.O_RDONLY
        else:
            flags |= os.O_WRONLY | os.O_CREAT
            if mode == "a":
                flags |= os.O_APPEND
        try:
            file_fd = os.open(candidate.name, flags, permissions, dir_fd=parent_fd)
        except FileNotFoundError as exc:
            raise StorageError("not_found", f"Missing record: {candidate}") from exc
        except OSError as exc:
            raise StorageError(
                "unsafe_store_leaf",
                f"Cannot open store text without following links: {candidate}: {exc}",
            ) from exc
        opened = os.fstat(file_fd)
        if not stat.S_ISREG(opened.st_mode):
            raise StorageError(
                "unsafe_file_type",
                f"Store text leaf must be a regular file: {candidate}",
            )
        published = _assert_safe_regular_leaf(parent_fd, candidate.name, display_path=candidate)
        if published is None or (opened.st_dev, opened.st_ino) != (
            published.st_dev,
            published.st_ino,
        ):
            raise StorageError(
                "file_identity_changed",
                f"Store text identity changed while opening: {candidate}",
            )
        if before is not None and (before.st_dev, before.st_ino) != (
            opened.st_dev,
            opened.st_ino,
        ):
            raise StorageError(
                "file_identity_changed",
                f"Store text identity changed while opening: {candidate}",
            )
        if mode != "r":
            os.fchmod(file_fd, permissions)
        if mode == "w":
            os.ftruncate(file_fd, 0)
            os.lseek(file_fd, 0, os.SEEK_SET)
        handle = os.fdopen(file_fd, mode, encoding="utf-8")
        file_fd = -1
        yield handle
        if mode != "r":
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if handle is not None:
            handle.close()
        elif file_fd >= 0:
            os.close(file_fd)
        os.close(parent_fd)


def repo_regular_file_exists(repo_root: Path, path: Path) -> bool:
    """Check a repo-owned leaf without following ancestors or the leaf itself."""
    candidate = guard_repo_path(repo_root, path)
    try:
        _parent, parent_fd = _open_repo_directory(
            repo_root,
            candidate.parent,
            create=False,
        )
    except StorageError as exc:
        if exc.code == "not_found":
            return False
        raise
    try:
        return _assert_safe_regular_leaf(parent_fd, candidate.name, display_path=candidate) is not None
    finally:
        os.close(parent_fd)


def list_repo_store_files(
    repo_root: Path,
    directory: Path,
    *,
    suffix: str = "",
) -> list[Path]:
    """List regular store leaves from a no-follow directory descriptor."""
    candidate = guard_repo_path(repo_root, directory)
    try:
        _opened, directory_fd = _open_repo_directory(
            repo_root,
            candidate,
            create=False,
        )
    except StorageError as exc:
        if exc.code == "not_found":
            return []
        raise
    try:
        result: list[Path] = []
        for name in sorted(os.listdir(directory_fd)):
            if suffix and not name.endswith(suffix):
                continue
            display_path = candidate / name
            info = _assert_safe_regular_leaf(directory_fd, name, display_path=display_path)
            if info is not None:
                result.append(display_path)
        return result
    finally:
        os.close(directory_fd)


def snapshot_path(
    store_root: Path,
    record_id: str,
    *,
    kind: str = "snapshot",
    repo_root: Path | None = None,
) -> Path:
    """Store-owned snapshot directory; never interpolate raw ids into parent paths."""
    key = digest_key(record_id, prefix=kind)
    if repo_root is not None:
        root = guard_repo_path(repo_root, store_root)
        return guard_repo_path(repo_root, root / "snapshots" / key)
    path = (store_root / "snapshots" / key).resolve()
    root = store_root.resolve()
    if root not in path.parents and path != root:
        raise StorageError("path_escape", f"Snapshot path escapes store root: {path}")
    return path


@contextmanager
def directory_lock(
    store_root: Path,
    *,
    name: str = "store.lock",
    timeout: float = 10.0,
    repo_root: Path | None = None,
) -> Iterator[Path]:
    """Exclusive Unix ``flock`` with bounded contention and fail-closed errors."""
    if fcntl is None:
        raise StorageError(
            "lock_unsupported",
            "Directory locking requires Unix fcntl.flock support",
        )
    if not name or Path(name).name != name or name in {".", ".."}:
        raise StorageError("invalid_lock_name", f"Lock name must be one path component: {name!r}")
    directory_fd: int | None = None
    if repo_root is not None:
        guarded_root, directory_fd = _open_repo_directory(
            repo_root,
            store_root,
            create=True,
        )
        lock_path = guard_repo_path(repo_root, guarded_root / name)
        _assert_safe_regular_leaf(directory_fd, name, display_path=lock_path)
        try:
            base_flags = (
                os.O_RDWR
                | os.O_APPEND
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            for _attempt in range(16):
                try:
                    lock_fd = os.open(name, base_flags, dir_fd=directory_fd)
                    break
                except FileNotFoundError:
                    try:
                        lock_fd = os.open(
                            name,
                            base_flags | os.O_CREAT | os.O_EXCL,
                            0o600,
                            dir_fd=directory_fd,
                        )
                        break
                    except FileExistsError:
                        # Another host thread/process won the safe publication race.
                        continue
            else:
                raise StorageError(
                    "lock_identity_changed",
                    f"Lock leaf changed repeatedly while opening: {lock_path}",
                )
        except StorageError:
            os.close(directory_fd)
            raise
        except OSError as exc:
            os.close(directory_fd)
            raise StorageError(
                "unsafe_lock_leaf",
                f"Cannot open lock without following links: {lock_path}: {exc}",
            ) from exc
        opened = os.fstat(lock_fd)
        published = _assert_safe_regular_leaf(directory_fd, name, display_path=lock_path)
        if published is None or (opened.st_dev, opened.st_ino) != (published.st_dev, published.st_ino):
            os.close(lock_fd)
            os.close(directory_fd)
            raise StorageError("lock_identity_changed", f"Lock identity changed while opening: {lock_path}")
        handle = os.fdopen(lock_fd, "a+", encoding="utf-8")
    else:
        ensure_private_dir(store_root)
        lock_path = store_root / name
        handle = lock_path.open("a+", encoding="utf-8")
    deadline = time.monotonic() + max(0.0, float(timeout))
    locked = False
    try:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError as exc:
                if exc.errno == errno.EINTR:
                    continue
                if isinstance(exc, BlockingIOError) or exc.errno in {
                    errno.EACCES,
                    errno.EAGAIN,
                }:
                    if time.monotonic() >= deadline:
                        raise StorageError(
                            "lock_timeout", f"Timed out locking {lock_path}"
                        ) from exc
                    time.sleep(min(0.02, max(0.0, deadline - time.monotonic())))
                    continue
                raise StorageError(
                    "lock_failed",
                    f"Failed locking {lock_path}: [errno {exc.errno}] {exc}",
                ) from exc
        yield lock_path
    finally:
        try:
            if locked:
                while True:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                        break
                    except OSError as exc:
                        if exc.errno == errno.EINTR:
                            continue
                        raise StorageError(
                            "lock_release_failed",
                            f"Failed unlocking {lock_path}: [errno {exc.errno}] {exc}",
                        ) from exc
        finally:
            handle.close()
            if directory_fd is not None:
                os.close(directory_fd)


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
    "profile",
    "version",
    "sandbox",
    "worktree",
    "cwd",
    "parent",
    "source_head",
    "capabilities",
    "evidence_kind",
    "observed_at",
)

QUALIFICATION_MAX_AGE_SECONDS = 15 * 60


def qualify_write_evidence(evidence: Mapping[str, Any] | None) -> tuple[bool, list[str]]:
    """Host-issued observed write qualification. Missing/mismatch/stale/unsupported fail closed."""
    reasons: list[str] = []
    if not evidence or not isinstance(evidence, Mapping):
        return False, ["missing qualification evidence object"]

    required = (
        "adapter",
        "model",
        "profile",
        "version",
        "sandbox",
        "worktree",
        "cwd",
        "parent",
        "source_head",
        "session_id",
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
        # Arbitrary sandbox strings fail closed — cannot enable detached commits.
        reasons.append(f"unsupported_sandbox:{sandbox}")

    if evidence.get("preference_declared") is True:
        reasons.append("preference_declared_not_qualification")

    if evidence.get("host_observed") is not True:
        reasons.append("host_observed_required")

    stale = evidence.get("stale")
    if stale is True:
        reasons.append("stale_evidence")

    observed_at = str(evidence.get("observed_at") or "")
    if observed_at:
        try:
            observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
            if observed.tzinfo is None:
                raise ValueError("timezone required")
            age = (datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds()
            if age > QUALIFICATION_MAX_AGE_SECONDS:
                reasons.append("stale_observed_at")
            elif age < -60:
                reasons.append("future_observed_at")
        except ValueError:
            reasons.append("invalid_observed_at")

    caps = evidence.get("capabilities")
    if isinstance(caps, Mapping):
        if caps.get("write") is not True:
            reasons.append("capabilities.write_not_true")
    elif caps is not None:
        reasons.append("capabilities_must_be_object")

    return (not reasons), reasons
