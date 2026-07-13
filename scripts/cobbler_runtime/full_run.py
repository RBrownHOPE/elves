"""Full-run supervisor for trusted delegated implementers (Lane A / Grok Build).

Uses adapter-aware ``implement.build_launch_argv`` for real Grok create/resume.
Fixture mode is explicit (``adapter=fixture``) for unit tests only.

Artifacts live under digest-keyed private paths. Worker events enrich telemetry;
liveness also comes from process fingerprint + observed feature-branch HEAD.
A worker report is evidence only — never merge authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

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
    digest_key,
    ensure_private_dir,
    read_json,
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


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def full_run_root(repo_root: Path, session_id: str) -> Path:
    """Digest-keyed collision-free runtime directory (not raw session path)."""
    key = digest_key(session_id, prefix="fullrun")
    return Path(repo_root).resolve() / FULL_RUN_REL / key


def validate_event(
    event: Mapping[str, Any],
    *,
    expected_session_id: str | None = None,
    expected_branch: str | None = None,
    expected_start_head: str | None = None,
    seen_terminal: bool = False,
) -> list[str]:
    errors: list[str] = []
    for key in ("timestamp", "session_id", "branch", "head", "batch", "type", "summary"):
        if key not in event:
            errors.append(f"missing event field: {key}")
    etype = event.get("type")
    if etype not in EVENT_TYPES:
        errors.append(f"invalid event type: {etype!r}")
    summary = str(event.get("summary") or "")
    if len(summary) > 500:
        errors.append("summary exceeds 500 chars")
    lowered = summary.lower()
    for needle in ("api_key=", "bearer ", "authorization:", "-----begin"):
        if needle in lowered:
            errors.append("summary appears to contain secret-shaped content")
    if expected_session_id and event.get("session_id") != expected_session_id:
        errors.append("event session_id mismatch")
    if expected_branch and event.get("branch") != expected_branch:
        errors.append("event branch mismatch")
    if seen_terminal and etype in TERMINAL_EVENT_TYPES:
        errors.append("duplicate terminal event")
    return errors


def validate_run_report(
    report: Mapping[str, Any],
    *,
    expected_session_id: str | None = None,
    expected_branch: str | None = None,
    expected_start_head: str | None = None,
    require_complete_acceptance: bool = False,
) -> list[str]:
    errors: list[str] = []
    required = (
        "run_id",
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
    status = report.get("status")
    if status not in {"running", "complete", "blocked", "failed", "stopped"}:
        errors.append(f"invalid report status: {status!r}")
    if expected_session_id and report.get("session_id") != expected_session_id:
        errors.append("report session_id mismatch")
    if expected_branch and report.get("branch") != expected_branch:
        errors.append("report branch mismatch")
    if expected_start_head and report.get("start_head") != expected_start_head:
        errors.append("report start_head mismatch")
    acceptance = report.get("acceptance")
    if acceptance is not None and not isinstance(acceptance, list):
        errors.append("acceptance must be a list")
    elif isinstance(acceptance, list):
        if require_complete_acceptance and status == "complete" and not acceptance:
            errors.append("complete report requires non-empty acceptance")
        for i, item in enumerate(acceptance):
            if not isinstance(item, dict):
                errors.append(f"acceptance[{i}] must be an object")
                continue
            for field_name in ("id", "criterion", "met", "evidence"):
                if field_name not in item:
                    errors.append(f"acceptance[{i}] missing {field_name}")
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
    driver_contract: str = "parked-monitor"
    notes: list[str] = field(default_factory=list)
    last_argv: list[str] = field(default_factory=list)
    # Fixture-only: path to python fixture script (never masquerades as Grok).
    fixture_script: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FullRunState":
        known = set(cls.__dataclass_fields__)
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)  # type: ignore[arg-type]


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
    # Supervisor paths for fixture mode only (never secret).
    if state.adapter == "fixture":
        env["ELVES_FULL_RUN_SESSION"] = state.session_id
        env["ELVES_FULL_RUN_EVENTS"] = str(root / "events.jsonl")
        env["ELVES_FULL_RUN_REPORT"] = str(root / "report.json")
        env["ELVES_FULL_RUN_TRANSCRIPT"] = str(root / "transcript.log")
        env["ELVES_FULL_RUN_BRANCH"] = state.branch
        env["ELVES_FULL_RUN_START_HEAD"] = state.start_head
        env["ELVES_FULL_RUN_WORKTREE"] = state.worktree
    return env


def _process_start_time(pid: int) -> str | None:
    """Best-effort process start fingerprint (macOS/Linux)."""
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
    try:
        stat_path = Path(f"/proc/{pid}/stat")
        if stat_path.is_file():
            # field 22 is starttime (jiffies) on Linux
            fields = stat_path.read_text().split()
            if len(fields) >= 22:
                return fields[21]
    except OSError:
        pass
    return None


def _process_executable(pid: int) -> str | None:
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
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError:
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


def verify_fingerprint(fp: ProcessFingerprint | Mapping[str, Any]) -> tuple[bool, str]:
    if isinstance(fp, Mapping):
        try:
            fp = ProcessFingerprint.from_dict(fp)
        except (KeyError, TypeError, ValueError) as exc:
            return False, f"invalid fingerprint: {exc}"
    if fp.pid <= 0:
        return False, "invalid pid"
    try:
        os.kill(fp.pid, 0)
    except ProcessLookupError:
        return False, "pid not alive"
    except PermissionError:
        # Alive but not owned — still verify start time if possible.
        pass
    current_start = _process_start_time(fp.pid)
    if fp.start_time and current_start and current_start != fp.start_time:
        return False, "pid start_time mismatch (reused PID)"
    current_exe = _process_executable(fp.pid)
    if fp.executable and current_exe:
        # Compare basenames to tolerate path resolution differences.
        if Path(fp.executable).name != Path(current_exe).name:
            # Allow fixture python vs script path differences when start_time matches.
            if fp.start_time and current_start == fp.start_time:
                pass
            else:
                return False, "pid executable mismatch"
    return True, "ok"


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

    root = full_run_root(repo_root, sid)
    state_path = root / "state.json"
    if state_path.is_file() and not allow_overwrite:
        existing = read_json(state_path)
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

    ensure_private_dir(root)
    ensure_private_dir(root / "worker-home")
    ensure_private_dir(root / "worker-tmp")

    exe = (executable or DEFAULT_EXECUTABLE).strip() or DEFAULT_EXECUTABLE
    if adapter_name == "fixture":
        exe = sys.executable

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
        notes=[
            "Trusted full-run supervisor prepared; host parks after launch",
            f"adapter={adapter_name}",
            "Worker report is evidence only; never merge authority",
        ],
    )
    atomic_write_json(state_path, state.to_dict())
    for name in ("events.jsonl", "transcript.log"):
        path = root / name
        if not path.exists():
            path.write_text("", encoding="utf-8")
            try:
                path.chmod(0o600)
            except OSError:
                pass
    report = {
        "run_id": f"full-run-{digest_key(sid, prefix='run')}",
        "session_id": sid,
        "branch": branch,
        "start_head": start_head,
        "final_head": start_head,
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
    }
    errs = validate_run_report(
        report,
        expected_session_id=sid,
        expected_branch=branch,
        expected_start_head=start_head,
    )
    if errs:
        raise ValidationIssue("full_run_report_invalid", "; ".join(errs))
    report_path = root / "report.json"
    atomic_write_json(report_path, report)
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
    )
    return {
        "ok": True,
        "action": "full_run_prepare",
        "session_id": sid,
        "runtime_dir": str(root),
        "state_path": str(state_path),
        "events_path": str(root / "events.jsonl"),
        "report_path": str(report_path),
        "transcript_path": str(root / "transcript.log"),
        "state": state.to_dict(),
        "driver_contract": "parked-monitor",
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
) -> None:
    errors = validate_event(
        event,
        expected_session_id=expected_session_id,
        expected_branch=expected_branch,
        seen_terminal=seen_terminal,
    )
    if errors:
        raise ValidationIssue("full_run_event_invalid", "; ".join(errors))
    with events_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(event), separators=(",", ":")) + "\n")
    try:
        events_path.chmod(0o600)
    except OSError:
        pass


def load_state(repo_root: Path, session_id: str) -> FullRunState:
    root = full_run_root(repo_root, session_id)
    path = root / "state.json"
    if not path.is_file():
        raise ValidationIssue(
            "full_run_not_found",
            f"No full-run state for session `{session_id}`",
            path=str(path),
        )
    data = read_json(path)
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
    ensure_private_dir(root)
    path = root / "state.json"
    atomic_write_json(path, state.to_dict())
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
    if state.fingerprint:
        ok, reason = verify_fingerprint(state.fingerprint)
        if ok:
            raise ValidationIssue(
                "full_run_already_running",
                f"Full-run session `{session_id}` already has verified live process",
            )
        # Stale fingerprint: allow relaunch.
        state.notes.append(f"cleared stale fingerprint: {reason}")

    if resume:
        state.create_session = False

    argv = build_full_run_argv(state)
    state.last_argv = list(argv)
    launch_env = build_full_run_env(
        state=state,
        root=root,
        credential_grant_names=credential_grant_names or state.credential_grant_names,
    )
    # Never return credential values.
    granted_names = [
        n
        for n in (credential_grant_names or state.credential_grant_names or [])
        if n in launch_env
    ]

    transcript = root / "transcript.log"
    ensure_private_dir(root / "worker-home")
    ensure_private_dir(root / "worker-tmp")
    stdout_handle = transcript.open("a", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            argv,
            cwd=state.worktree if state.adapter != "fixture" else state.worktree,
            env=launch_env,
            stdout=stdout_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        stdout_handle.close()

    pgid = os.getpgid(proc.pid) if hasattr(os, "getpgid") else proc.pid
    # Brief settle so ps can observe the process.
    time.sleep(0.05)
    fp = capture_fingerprint(
        pid=proc.pid,
        pgid=pgid,
        session_id=session_id,
        executable_hint=argv[0],
    )
    state.pid = proc.pid
    state.pgid = pgid
    state.fingerprint = fp.to_dict()
    state.status = "healthy"
    state.launched_at = _utc_now()
    state.heartbeat_at = state.launched_at
    state.next_action = "monitor"
    if resume:
        state.create_session = False
    save_state(repo_root, state)
    (root / "worker.pid").write_text(str(proc.pid) + "\n", encoding="utf-8")
    (root / "worker.pgid").write_text(str(pgid) + "\n", encoding="utf-8")
    atomic_write_json(root / "worker.fingerprint.json", fp.to_dict())
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
    )
    return {
        "ok": True,
        "action": "full_run_launch",
        "session_id": session_id,
        "pid": proc.pid,
        "pgid": pgid,
        "status": state.status,
        "driver_contract": "parked-monitor",
        "returned_promptly": True,
        "argv": argv,
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


def _read_events(
    events_path: Path,
    *,
    expected_session_id: str,
    expected_branch: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    if not events_path.is_file():
        return [], []
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    seen_terminal = False
    for line_no, line in enumerate(events_path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
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
        )
        if verrs:
            errors.extend(f"line {line_no}: {e}" for e in verrs)
            continue
        if event.get("type") in TERMINAL_EVENT_TYPES:
            seen_terminal = True
        rows.append(event)
    return rows, errors


def monitor_full_run(
    repo_root: Path,
    *,
    session_id: str,
    stale_after_seconds: int = DEFAULT_STALE_SECONDS,
) -> dict[str, Any]:
    """Classify health using fingerprint + branch head + validated events/report."""
    state = load_state(repo_root, session_id)
    root = full_run_root(repo_root, session_id)
    events, event_errors = _read_events(
        root / "events.jsonl",
        expected_session_id=session_id,
        expected_branch=state.branch,
    )
    report_path = root / "report.json"
    report: dict[str, Any] = {}
    report_errors: list[str] = []
    if report_path.is_file():
        try:
            report = read_json(report_path)
            report_errors = validate_run_report(
                report,
                expected_session_id=session_id,
                expected_branch=state.branch,
                expected_start_head=state.start_head,
                require_complete_acceptance=report.get("status") == "complete",
            )
            if report_errors:
                report = {}
        except StorageError as exc:
            report_errors = [exc.message]
            report = {}

    # Process fingerprint is primary liveness for long Grok turns.
    fp_ok = False
    fp_reason = "no fingerprint"
    alive = False
    if state.fingerprint:
        fp_ok, fp_reason = verify_fingerprint(state.fingerprint)
        alive = fp_ok
    elif state.pid:
        alive = _pid_alive(state.pid)
        fp_reason = "legacy pid without fingerprint"

    # Observed feature-branch head (supervisor heartbeat source).
    observed_head = _git_head(Path(state.worktree))
    observed_branch = _git_branch(Path(state.worktree))
    if observed_head:
        state.head = observed_head
        # Automatic supervisor heartbeat while process is alive.
        if alive:
            state.heartbeat_at = _utc_now()
            state.status = "healthy"
            state.next_action = "parked_monitor"

    last_type = None
    for ev in events:
        last_type = ev.get("type") or last_type
        if ev.get("type") == "batch_started":
            try:
                state.batch = int(ev.get("batch") or state.batch or 0)
            except (TypeError, ValueError):
                pass
        if ev.get("type") in {"commit_pushed", "heartbeat", "batch_complete"}:
            if ev.get("head"):
                state.head = str(ev.get("head"))
            state.heartbeat_at = str(ev.get("timestamp") or state.heartbeat_at)
        if ev.get("type") == "blocked":
            state.status = "blocked"
            state.blocker = str(ev.get("summary") or "blocked")
            state.next_action = "driver_wake_blocker"
        if ev.get("type") == "run_complete":
            state.status = "complete"
            state.completed_at = str(ev.get("timestamp") or _utc_now())
            state.next_action = "final_readiness"

    # Report is evidence only after validation.
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
            if not report_errors:
                state.status = "complete"
                state.completed_at = state.completed_at or _utc_now()
                state.next_action = "final_readiness"
                if final_head:
                    state.head = final_head
        elif report.get("status") == "blocked":
            state.status = "blocked"
            state.blocker = state.blocker or "report status blocked"
            state.next_action = "driver_wake_blocker"
        elif report.get("status") == "failed":
            state.status = "failed"
            state.next_action = "driver_wake_error"

    if event_errors or report_errors:
        # Foreign/malformed evidence does not complete the run.
        if state.status == "complete" and (event_errors or report_errors):
            state.status = "failed"
            state.blocker = "untrusted worker events/report"
            state.next_action = "driver_wake_error"

    if state.status not in {"blocked", "complete", "failed", "stopped"}:
        if alive:
            hb = state.heartbeat_at or state.launched_at
            if hb:
                try:
                    hb_dt = datetime.fromisoformat(hb.replace("Z", "+00:00"))
                    age = (datetime.now(timezone.utc) - hb_dt).total_seconds()
                    # Supervisor heartbeat updates while alive, so age should stay small.
                    # Only stale if process fingerprint fails or heartbeat cannot refresh.
                    if age > stale_after_seconds and not fp_ok:
                        state.status = "stale"
                        state.next_action = "driver_wake_stale_heartbeat"
                    else:
                        state.status = "healthy"
                        state.next_action = "parked_monitor"
                except ValueError:
                    state.status = "healthy"
            else:
                state.status = "healthy"
        else:
            if last_type == "run_complete" and not event_errors and not report_errors:
                state.status = "complete"
            elif last_type == "blocked":
                state.status = "blocked"
            elif state.status not in {"complete", "blocked"}:
                state.status = "failed"
                state.next_action = "driver_wake_error"

    # Branch mismatch is a safety signal.
    if observed_branch and observed_branch != state.branch:
        state.status = "failed"
        state.blocker = f"worktree branch `{observed_branch}` != staged `{state.branch}`"
        state.next_action = "driver_wake_safety_tripwire"

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
        "driver_contract": "parked-monitor",
        "wake_conditions": sorted(PARKED_MONITOR_WAKE_CONDITIONS),
        "check_summary": {
            "events": len(events),
            "last_event_type": last_type,
            "alive": alive,
            "fingerprint_reason": fp_reason,
            "report_status": report.get("status") if report else None,
            "event_errors": len(event_errors),
            "report_errors": len(report_errors),
            "observed_branch": observed_branch,
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
    return status


def stop_full_run(
    repo_root: Path,
    *,
    session_id: str,
    grace_seconds: float = 1.0,
) -> dict[str, Any]:
    """Terminate the recorded process group only after fingerprint verification."""
    state = load_state(repo_root, session_id)
    root = full_run_root(repo_root, session_id)
    if not state.fingerprint and not state.pid:
        state.status = "stopped"
        state.next_action = "stopped"
        state.completed_at = _utc_now()
        save_state(repo_root, state)
        return {
            "ok": True,
            "action": "full_run_stop",
            "session_id": session_id,
            "signaled": False,
            "still_alive": False,
            "status": state.status,
            "reason": "no process recorded",
        }

    if state.fingerprint:
        ok, reason = verify_fingerprint(state.fingerprint)
        if not ok:
            raise ValidationIssue(
                "full_run_fingerprint_mismatch",
                f"Refusing stop/killpg: {reason}",
                path=str(root / "worker.fingerprint.json"),
                hint="PID may have been reused; investigate before signaling",
            )
        pid = int(state.fingerprint.get("pid") or state.pid or 0)
        pgid = state.fingerprint.get("pgid") or state.pgid
    else:
        pid = int(state.pid or 0)
        pgid = state.pgid

    signaled = False
    if pgid:
        try:
            os.killpg(int(pgid), signal.SIGTERM)
            signaled = True
        except (ProcessLookupError, PermissionError, OSError):
            if pid:
                try:
                    os.kill(pid, signal.SIGTERM)
                    signaled = True
                except (ProcessLookupError, PermissionError, OSError):
                    pass
    elif pid:
        try:
            os.kill(pid, signal.SIGTERM)
            signaled = True
        except (ProcessLookupError, PermissionError, OSError):
            pass

    if grace_seconds > 0:
        time.sleep(min(grace_seconds, 2.0))

    still_alive = bool(pid and _pid_alive(pid))
    if still_alive:
        # Re-verify fingerprint before SIGKILL.
        if state.fingerprint:
            ok, reason = verify_fingerprint(state.fingerprint)
            if not ok:
                raise ValidationIssue(
                    "full_run_fingerprint_mismatch",
                    f"Refusing SIGKILL: {reason}",
                )
        if pgid:
            try:
                os.killpg(int(pgid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                if pid:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError, OSError):
                        pass
        elif pid:
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        time.sleep(0.05)
        still_alive = bool(pid and _pid_alive(pid))

    state.status = "stopped"
    state.next_action = "stopped"
    state.completed_at = _utc_now()
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
            "summary": "Supervisor stop requested; verified process group signaled",
        },
        expected_session_id=session_id,
        expected_branch=state.branch,
    )
    return {
        "ok": not still_alive,
        "action": "full_run_stop",
        "session_id": session_id,
        "signaled": signaled,
        "still_alive": still_alive,
        "status": state.status,
        "fingerprint_verified": True,
    }


def logs_full_run(
    repo_root: Path,
    *,
    session_id: str,
    raw_tail: bool = False,
    tail_lines: int = 40,
) -> dict[str, Any]:
    root = full_run_root(repo_root, session_id)
    events, errors = _read_events(
        root / "events.jsonl",
        expected_session_id=session_id,
        expected_branch=load_state(repo_root, session_id).branch,
    )
    payload: dict[str, Any] = {
        "ok": not errors,
        "session_id": session_id,
        "events_tail": events[-tail_lines:],
        "event_errors": errors[-20:],
        "transcript_included": False,
        "merge_authority": False,
    }
    if raw_tail:
        transcript = root / "transcript.log"
        if transcript.is_file():
            lines = transcript.read_text(encoding="utf-8", errors="replace").splitlines()
            # Bounded only — still private artifact.
            payload["transcript_tail"] = lines[-tail_lines:]
            payload["transcript_included"] = True
    return payload


def write_report(repo_root: Path, session_id: str, report: Mapping[str, Any]) -> Path:
    state = load_state(repo_root, session_id)
    errors = validate_run_report(
        report,
        expected_session_id=session_id,
        expected_branch=state.branch,
        expected_start_head=state.start_head,
        require_complete_acceptance=report.get("status") == "complete",
    )
    if errors:
        raise ValidationIssue("full_run_report_invalid", "; ".join(errors))
    # Reports never grant merge authority.
    payload = dict(report)
    payload["merge_authority"] = False
    path = full_run_root(repo_root, session_id) / "report.json"
    atomic_write_json(path, payload)
    return path


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
    worktree = Path(state.worktree)
    assert_feature_branch(worktree, state.branch)
    tip = assert_descendant(worktree, ancestor=state.start_head)
    contract = DelegatedGitContract(
        feature_branch=state.branch,
        base_branch="main",
        start_head=state.start_head,
        session_id=session_id,
        run_id=f"full-run-{session_id}",
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
    if report_path.is_file():
        report = read_json(report_path)
        errs = validate_run_report(
            report,
            expected_session_id=session_id,
            expected_branch=state.branch,
            expected_start_head=state.start_head,
        )
        if errs:
            raise ValidationIssue("full_run_report_invalid", "; ".join(errs))

    host_state = {
        "merge_on_green": False,
        "stop_allowed": False,
        "run_mode": "finite",
        "pr_number": None,
        "driver_monitor_mode": "parked-monitor",
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
    return {
        "ok": True,
        "session_id": session_id,
        "branch": state.branch,
        "start_head": state.start_head,
        "final_head": tip,
        "merged_host_state": {
            k: merged.get(k)
            for k in ("merge_on_green", "stop_allowed", "driver_monitor_mode", "final_head")
        },
        "merge_authority": False,
    }
