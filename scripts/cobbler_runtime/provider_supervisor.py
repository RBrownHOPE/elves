"""Standalone provider supervisor program (extracted from full_run.py, plan B7).

This file is BOTH a real module (lintable, compilable, testable) and the exact
program text that full_run embeds via `sys.executable -c <text>`: full_run
loads this file's source at launch, so the child process runs byte-identical
code to what is committed here. It must stay stdlib-only and self-contained —
it executes outside the cobbler_runtime package.

Argv contract (positional): exit_record_path, fingerprint_path, session_id,
provider_argv_json, attempt, supervisor_executable, max_stop_request_bytes,
provider_executable_identity_json, staged_packet_path,
staged_packet_identity_json, ... (see build_full_run_supervisor_argv).
"""

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
