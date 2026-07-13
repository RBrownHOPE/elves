"""Full-run supervisor for trusted delegated implementers (Lane A).

Provides prepare / background-launch / monitor / logs / stop against one exact
session. Deterministic and testable with a fixture executable — no live provider
required for unit tests.

Artifacts (mode 0600/0700 under .elves/runtime/implement/full-run/<session>/):
- state.json — supervisor state
- events.jsonl — bounded events
- report.json — machine-readable run report
- transcript.log — private raw transcript (not returned by default status)
- worker.pid / worker.pgid — process tracking for process-group cleanup
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .schema import ValidationIssue

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
MONITOR_STATES = frozenset(
    {"pending", "healthy", "complete", "failed", "blocked", "stale", "stopped"}
)
DEFAULT_STALE_SECONDS = 300
DEFAULT_HEARTBEAT_SECONDS = 30

# Status is bounded; raw transcript never included unless opt-in tail is requested.
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
    }
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def _atomic_write_json(path: Path, data: Mapping[str, Any], *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(dict(data), indent=2, sort_keys=True) + "\n"
    fd, tmp_name = __import__("tempfile").mkstemp(
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


def full_run_root(repo_root: Path, session_id: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in session_id)
    return Path(repo_root).resolve() / FULL_RUN_REL / safe


def validate_event(event: Mapping[str, Any]) -> list[str]:
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
    # Reject secret-shaped values in summary/head-level strings.
    lowered = summary.lower()
    for needle in ("api_key=", "bearer ", "authorization:", "-----begin"):
        if needle in lowered:
            errors.append("summary appears to contain secret-shaped content")
    return errors


def validate_run_report(report: Mapping[str, Any]) -> list[str]:
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
    acceptance = report.get("acceptance")
    if acceptance is not None and not isinstance(acceptance, list):
        errors.append("acceptance must be a list")
    elif isinstance(acceptance, list):
        for i, item in enumerate(acceptance):
            if not isinstance(item, dict):
                errors.append(f"acceptance[{i}] must be an object")
                continue
            for field_name in ("id", "criterion", "met", "evidence"):
                if field_name not in item:
                    errors.append(f"acceptance[{i}] missing {field_name}")
    return errors


@dataclass
class FullRunState:
    session_id: str
    branch: str
    start_head: str
    worktree: str
    executable: str
    packet_path: str
    status: str = "pending"
    batch: int | None = None
    head: str | None = None
    pid: int | None = None
    pgid: int | None = None
    heartbeat_at: str | None = None
    launched_at: str | None = None
    completed_at: str | None = None
    blocker: str | None = None
    next_action: str | None = None
    driver_contract: str = "parked-monitor"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FullRunState":
        known = set(cls.__dataclass_fields__)
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)  # type: ignore[arg-type]


def prepare_full_run(
    repo_root: Path,
    *,
    session_id: str,
    branch: str,
    start_head: str,
    worktree: str | Path,
    packet_path: str | Path,
    executable: str,
) -> dict[str, Any]:
    """Create private full-run artifact tree for one exact session."""
    if not session_id or session_id.strip().lower() in {"latest", "continue", "last"}:
        raise ValidationIssue(
            "full_run_session_required",
            "Exact session_id is required for full-run prepare",
            path="full_run.session_id",
        )
    root = full_run_root(repo_root, session_id)
    _ensure_private_dir(root)
    state = FullRunState(
        session_id=session_id.strip(),
        branch=branch,
        start_head=start_head,
        worktree=str(Path(worktree).resolve()),
        executable=executable,
        packet_path=str(Path(packet_path).resolve()),
        status="pending",
        head=start_head,
        next_action="launch",
        notes=["Trusted full-run supervisor prepared; host parks after launch"],
    )
    state_path = root / "state.json"
    events_path = root / "events.jsonl"
    report_path = root / "report.json"
    transcript_path = root / "transcript.log"
    _atomic_write_json(state_path, state.to_dict())
    if not events_path.exists():
        events_path.write_text("", encoding="utf-8")
        try:
            events_path.chmod(0o600)
        except OSError:
            pass
    if not transcript_path.exists():
        transcript_path.write_text("", encoding="utf-8")
        try:
            transcript_path.chmod(0o600)
        except OSError:
            pass
    report = {
        "run_id": f"full-run-{session_id}",
        "session_id": session_id,
        "branch": branch,
        "start_head": start_head,
        "final_head": start_head,
        "status": "running",
        "batches": [],
        "acceptance": [],
        "commits": [],
        "blockers": [],
        "docs_changed": [],
        "tests": {},
        "security_notes": ["transcript private; status bounded"],
        "remaining_risks": [],
    }
    errs = validate_run_report(report)
    if errs:
        raise ValidationIssue("full_run_report_invalid", "; ".join(errs))
    _atomic_write_json(report_path, report)
    _append_event(
        events_path,
        {
            "timestamp": _utc_now(),
            "session_id": session_id,
            "branch": branch,
            "head": start_head,
            "batch": 0,
            "type": "run_started",
            "summary": "Full-run supervisor prepared",
        },
    )
    return {
        "ok": True,
        "action": "full_run_prepare",
        "session_id": session_id,
        "runtime_dir": str(root),
        "state_path": str(state_path),
        "events_path": str(events_path),
        "report_path": str(report_path),
        "transcript_path": str(transcript_path),
        "state": state.to_dict(),
        "driver_contract": "parked-monitor",
        "model_calls_made": False,
    }


def _append_event(events_path: Path, event: Mapping[str, Any]) -> None:
    errors = validate_event(event)
    if errors:
        raise ValidationIssue("full_run_event_invalid", "; ".join(errors))
    with events_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(event), separators=(",", ":")) + "\n")
    try:
        events_path.chmod(0o600)
    except OSError:
        pass


def load_state(repo_root: Path, session_id: str) -> FullRunState:
    path = full_run_root(repo_root, session_id) / "state.json"
    if not path.is_file():
        raise ValidationIssue(
            "full_run_not_found",
            f"No full-run state for session `{session_id}`",
            path=str(path),
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    return FullRunState.from_dict(data)


def save_state(repo_root: Path, state: FullRunState) -> Path:
    root = full_run_root(repo_root, state.session_id)
    _ensure_private_dir(root)
    path = root / "state.json"
    _atomic_write_json(path, state.to_dict())
    return path


def launch_full_run(
    repo_root: Path,
    *,
    session_id: str,
    background: bool = True,
    env: Mapping[str, str] | None = None,
    extra_args: list[str] | None = None,
) -> dict[str, Any]:
    """Launch the worker executable non-blocking (default) for one session."""
    state = load_state(repo_root, session_id)
    root = full_run_root(repo_root, session_id)
    if state.pid and _pid_alive(state.pid):
        raise ValidationIssue(
            "full_run_already_running",
            f"Full-run session `{session_id}` already has live pid {state.pid}",
        )
    transcript = root / "transcript.log"
    events_path = root / "events.jsonl"
    argv = [state.executable, *list(extra_args or []), str(state.packet_path)]
    launch_env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(root / "worker-home"),
        "TMPDIR": str(root / "worker-tmp"),
        "ELVES_FULL_RUN_SESSION": session_id,
        "ELVES_FULL_RUN_EVENTS": str(events_path),
        "ELVES_FULL_RUN_REPORT": str(root / "report.json"),
        "ELVES_FULL_RUN_TRANSCRIPT": str(transcript),
        "ELVES_FULL_RUN_BRANCH": state.branch,
        "ELVES_FULL_RUN_START_HEAD": state.start_head,
        "ELVES_FULL_RUN_WORKTREE": state.worktree,
        "PYTHONUNBUFFERED": "1",
    }
    (root / "worker-home").mkdir(exist_ok=True)
    (root / "worker-tmp").mkdir(exist_ok=True)
    if env:
        # Only explicit grants — never inherit host credentials wholesale.
        for key, value in env.items():
            launch_env[str(key)] = str(value)

    stdout_handle = transcript.open("a", encoding="utf-8")
    try:
        if background:
            # New session so we can signal the process group on stop.
            proc = subprocess.Popen(
                argv,
                cwd=state.worktree,
                env=launch_env,
                stdout=stdout_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        else:
            proc = subprocess.Popen(
                argv,
                cwd=state.worktree,
                env=launch_env,
                stdout=stdout_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
    finally:
        stdout_handle.close()

    pgid = os.getpgid(proc.pid) if hasattr(os, "getpgid") else proc.pid
    state.pid = proc.pid
    state.pgid = pgid
    state.status = "healthy"
    state.launched_at = _utc_now()
    state.heartbeat_at = state.launched_at
    state.next_action = "monitor"
    save_state(repo_root, state)
    (root / "worker.pid").write_text(str(proc.pid) + "\n", encoding="utf-8")
    (root / "worker.pgid").write_text(str(pgid) + "\n", encoding="utf-8")
    _append_event(
        events_path,
        {
            "timestamp": state.launched_at,
            "session_id": session_id,
            "branch": state.branch,
            "head": state.head or state.start_head,
            "batch": state.batch or 0,
            "type": "heartbeat",
            "summary": "Worker launched in background",
        },
    )
    # Return promptly — do not wait for worker completion.
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
        "model_calls_made": False,
    }


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        # Reap zombie children so a killed worker is not reported as alive.
        waited, _status = os.waitpid(pid, os.WNOHANG)
        if waited == pid:
            return False
    except ChildProcessError:
        pass
    except OSError:
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    else:
        return True


def _read_events(events_path: Path) -> list[dict[str, Any]]:
    if not events_path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in events_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def monitor_full_run(
    repo_root: Path,
    *,
    session_id: str,
    stale_after_seconds: int = DEFAULT_STALE_SECONDS,
) -> dict[str, Any]:
    """Classify healthy / complete / failed / blocked / stale for one session."""
    state = load_state(repo_root, session_id)
    root = full_run_root(repo_root, session_id)
    events = _read_events(root / "events.jsonl")
    report_path = root / "report.json"
    report: dict[str, Any] = {}
    if report_path.is_file():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            report = {}

    alive = bool(state.pid and _pid_alive(state.pid))
    last_event_ts = None
    last_type = None
    for ev in events:
        last_event_ts = ev.get("timestamp") or last_event_ts
        last_type = ev.get("type") or last_type
        if ev.get("type") == "batch_started":
            try:
                state.batch = int(ev.get("batch") or state.batch or 0)
            except (TypeError, ValueError):
                pass
        if ev.get("type") in {"commit_pushed", "heartbeat", "batch_complete"}:
            state.head = str(ev.get("head") or state.head)
            state.heartbeat_at = str(ev.get("timestamp") or state.heartbeat_at)
        if ev.get("type") == "blocked":
            state.status = "blocked"
            state.blocker = str(ev.get("summary") or "blocked")
            state.next_action = "driver_wake_blocker"
        if ev.get("type") == "run_complete":
            state.status = "complete"
            state.completed_at = str(ev.get("timestamp") or _utc_now())
            state.next_action = "final_readiness"

    if state.status not in {"blocked", "complete", "failed", "stopped"}:
        if report.get("status") == "complete":
            state.status = "complete"
            state.completed_at = state.completed_at or _utc_now()
            state.next_action = "final_readiness"
        elif report.get("status") == "blocked":
            state.status = "blocked"
            state.blocker = state.blocker or "report status blocked"
            state.next_action = "driver_wake_blocker"
        elif report.get("status") == "failed":
            state.status = "failed"
            state.next_action = "driver_wake_error"
        elif alive:
            # Stale heartbeat detection.
            hb = state.heartbeat_at or state.launched_at
            if hb:
                try:
                    hb_dt = datetime.fromisoformat(hb.replace("Z", "+00:00"))
                    age = (datetime.now(timezone.utc) - hb_dt).total_seconds()
                    if age > stale_after_seconds:
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
            # Process exited without terminal event.
            if last_type == "run_complete" or report.get("status") == "complete":
                state.status = "complete"
            elif last_type == "blocked" or report.get("status") == "blocked":
                state.status = "blocked"
            else:
                state.status = "failed"
                state.next_action = "driver_wake_error"

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
            "report_status": report.get("status"),
        },
        "report_path": str(report_path),
        "events_path": str(root / "events.jsonl"),
        "transcript_private": True,
    }
    # Guarantee status never includes raw transcript content.
    assert "transcript" not in status
    assert set(status) <= STATUS_KEYS | {"ok"}
    return status


def stop_full_run(
    repo_root: Path,
    *,
    session_id: str,
    grace_seconds: float = 1.0,
) -> dict[str, Any]:
    """Terminate the recorded process group."""
    state = load_state(repo_root, session_id)
    root = full_run_root(repo_root, session_id)
    signaled = False
    pgid = state.pgid
    pid = state.pid
    if pgid:
        try:
            os.killpg(pgid, signal.SIGTERM)
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
        if pgid:
            try:
                os.killpg(pgid, signal.SIGKILL)
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
            "summary": "Supervisor stop requested; process group signaled",
        },
    )
    return {
        "ok": not still_alive,
        "action": "full_run_stop",
        "session_id": session_id,
        "signaled": signaled,
        "still_alive": still_alive,
        "status": state.status,
    }


def logs_full_run(
    repo_root: Path,
    *,
    session_id: str,
    raw_tail: bool = False,
    tail_lines: int = 40,
) -> dict[str, Any]:
    """Return bounded logs; raw transcript only with explicit opt-in."""
    root = full_run_root(repo_root, session_id)
    events = _read_events(root / "events.jsonl")
    payload: dict[str, Any] = {
        "ok": True,
        "session_id": session_id,
        "events_tail": events[-tail_lines:],
        "transcript_included": False,
    }
    if raw_tail:
        transcript = root / "transcript.log"
        if transcript.is_file():
            lines = transcript.read_text(encoding="utf-8", errors="replace").splitlines()
            payload["transcript_tail"] = lines[-tail_lines:]
            payload["transcript_included"] = True
    return payload


def write_report(repo_root: Path, session_id: str, report: Mapping[str, Any]) -> Path:
    errors = validate_run_report(report)
    if errors:
        raise ValidationIssue("full_run_report_invalid", "; ".join(errors))
    path = full_run_root(repo_root, session_id) / "report.json"
    _atomic_write_json(path, report)
    return path
