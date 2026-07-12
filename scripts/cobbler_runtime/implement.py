"""Lane A (fast) implementer operator helpers.

Host-owned commands for prepare / launch argv / gate / resume-batch / status.
Does not run paid model inference unless the operator explicitly passes --exec
to launch (or resume-batch --exec). Network is never required for prepare/status.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schema import ValidationIssue

DEFAULT_MODEL = "grok-4.5"
DEFAULT_PERMISSION_MODE = "auto"
DEFAULT_LANE = "fast"
DEFAULT_GIT_MODE = "branch_progress"
DEFAULT_EXECUTABLE = "grok"
DEFAULT_EFFORT = "medium"
FORBIDDEN_DEFAULT_PERMISSION = "dontAsk"
# Empirically required for unattended headless tool use (Grok Build 0.2.93 docs + dogfood).
# --permission-mode auto alone does not auto-approve writes; --yolo / --always-approve does.

RUNTIME_REL = Path(".elves") / "runtime" / "implement"
STATE_NAME = "state.json"
GATES_DIRNAME = "gates"
DONE_DIRNAME = "done"
PACKETS_REL = Path(".elves") / "runtime" / "packets"

_RAN_RE = re.compile(r"^Ran\s+(\d+)\s+tests?\b", re.MULTILINE)
_FAIL_RE = re.compile(
    r"FAILED\s*\((?:[^)]*failures=(\d+))?[^)]*(?:errors=(\d+))?[^)]*\)"
)
_SKIP_RE = re.compile(r"(?:skipped=(\d+)|OK\s*\([^)]*skipped=(\d+))", re.IGNORECASE)


@dataclass
class ImplementState:
    """Persisted implementer metadata under .elves/runtime/implement/."""

    lane: str = DEFAULT_LANE
    git_mode: str = DEFAULT_GIT_MODE
    adapter: str = "grok-build"
    model: str = DEFAULT_MODEL
    permission_mode: str = DEFAULT_PERMISSION_MODE
    worktree: str = ""
    branch: str | None = None
    session_id: str | None = None
    executable: str = DEFAULT_EXECUTABLE
    subagents: bool = True
    created_at: str = ""
    updated_at: str = ""
    last_batch: int | None = None
    last_packet: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ImplementState:
        known = set(cls.__dataclass_fields__)
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def implement_root(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / RUNTIME_REL


def state_path(repo_root: Path) -> Path:
    return implement_root(repo_root) / STATE_NAME


def gates_dir(repo_root: Path) -> Path:
    return implement_root(repo_root) / GATES_DIRNAME


def done_dir(repo_root: Path) -> Path:
    return implement_root(repo_root) / DONE_DIRNAME


def _ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        # Best-effort on platforms that reject chmod (e.g. some Windows mounts).
        pass


def ensure_implement_dirs(repo_root: Path) -> Path:
    """Create implement runtime tree with mode 0700. No network."""
    root = implement_root(repo_root)
    # Ensure parent .elves/runtime chain exists and is private when we create it.
    for part in (
        Path(repo_root).resolve() / ".elves",
        Path(repo_root).resolve() / ".elves" / "runtime",
        root,
        gates_dir(repo_root),
        done_dir(repo_root),
    ):
        _ensure_private_dir(part)
    return root


def load_state(repo_root: Path) -> ImplementState | None:
    path = state_path(repo_root)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValidationIssue(
            "implement_state_invalid",
            f"Implement state is not a JSON object: {path}",
        )
    return ImplementState.from_dict(data)


def save_state(repo_root: Path, state: ImplementState) -> Path:
    ensure_implement_dirs(repo_root)
    path = state_path(repo_root)
    state.updated_at = _utc_now()
    path.write_text(
        json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def prepare_implement(
    repo_root: Path,
    *,
    worktree: str | Path | None = None,
    model: str = DEFAULT_MODEL,
    session_id: str | None = None,
    branch: str | None = None,
    lane: str = DEFAULT_LANE,
    git_mode: str = DEFAULT_GIT_MODE,
    permission_mode: str = DEFAULT_PERMISSION_MODE,
    executable: str = DEFAULT_EXECUTABLE,
) -> dict[str, Any]:
    """Record implementer metadata. Creates dirs mode 0700. No network."""
    wt = str(Path(worktree).expanduser().resolve()) if worktree else str(Path(repo_root).resolve())
    mode = (permission_mode or DEFAULT_PERMISSION_MODE).strip()
    if not mode:
        mode = DEFAULT_PERMISSION_MODE
    if mode == FORBIDDEN_DEFAULT_PERMISSION:
        # Explicit dontAsk is allowed only if the operator forces it; prepare still
        # warns by refusing to treat it as the lane default — block as product rule.
        raise ValidationIssue(
            "implement_dontask_forbidden",
            "permission_mode=dontAsk is forbidden for Lane A prepare (use auto or acceptEdits)",
            hint="Default is auto; never default headless to dontAsk",
        )

    existing = load_state(repo_root)
    now = _utc_now()
    state = ImplementState(
        lane=(lane or DEFAULT_LANE).strip() or DEFAULT_LANE,
        git_mode=(git_mode or DEFAULT_GIT_MODE).strip() or DEFAULT_GIT_MODE,
        adapter="grok-build",
        model=(model or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        permission_mode=mode,
        worktree=wt,
        branch=branch,
        session_id=(session_id.strip() if session_id else None)
        or (existing.session_id if existing else None),
        executable=(executable or DEFAULT_EXECUTABLE).strip() or DEFAULT_EXECUTABLE,
        subagents=True,
        created_at=existing.created_at if existing and existing.created_at else now,
        updated_at=now,
        last_batch=existing.last_batch if existing else None,
        last_packet=existing.last_packet if existing else None,
        notes=[
            "Lane A fast implementer (default for 'have Grok run it')",
            "Host/human launches Grok; CLI prints argv unless --exec",
            "Never pass --no-subagents; never default permission to dontAsk",
        ],
    )
    path = save_state(repo_root, state)
    return {
        "ok": True,
        "action": "prepare",
        "repo_root": str(Path(repo_root).resolve()),
        "runtime_dir": str(implement_root(repo_root)),
        "state_path": str(path),
        "state": state.to_dict(),
        "mutated_repo": False,
        "model_calls_made": False,
        "network_required": False,
    }


def _normalize_permission(mode: str | None) -> str:
    value = (mode or DEFAULT_PERMISSION_MODE).strip() or DEFAULT_PERMISSION_MODE
    if value == FORBIDDEN_DEFAULT_PERMISSION:
        raise ValidationIssue(
            "implement_dontask_forbidden",
            "permission_mode=dontAsk is forbidden for Lane A launch defaults",
            hint="Use auto or acceptEdits; never default to dontAsk",
        )
    return value


def build_launch_argv(
    *,
    session_id: str | None = None,
    packet: str | Path,
    cwd: str | Path,
    model: str = DEFAULT_MODEL,
    permission_mode: str = DEFAULT_PERMISSION_MODE,
    executable: str = DEFAULT_EXECUTABLE,
    create: bool = False,
    effort: str = DEFAULT_EFFORT,
    yolo: bool = True,
    max_turns: int | None = 80,
    output_format: str | None = "json",
) -> list[str]:
    """Build exact grok argv for Lane A headless implementer turns.

    Dogfood findings (Grok Build 0.2.93):
    - ``--prompt-file`` or ``-p`` both trigger headless multi-turn with tools.
    - ``--yolo`` / ``--always-approve`` is required for unattended edits (not
      ``--permission-mode auto`` alone).
    - ``--effort high`` roughly doubles tiny-task latency; default ``medium``.
    - Do not pass ``-p`` and ``--prompt-file`` together (CLI rejects).
    - Prefer whole-batch packets; host gates between batches, not breaths.
    - Interactive TUI (positional prompt, no ``-p``) remains valid for humans;
      this builder targets the scripted/host path.
    """
    packet_path = Path(packet).expanduser().resolve()
    if not packet_path.is_file():
        raise ValidationIssue(
            "packet_missing",
            f"Packet file not found: {packet_path}",
        )
    cwd_path = Path(cwd).expanduser().resolve()
    perm = _normalize_permission(permission_mode)
    exe = (executable or DEFAULT_EXECUTABLE).strip() or DEFAULT_EXECUTABLE
    model_name = (model or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    effort_name = (effort or DEFAULT_EFFORT).strip() or DEFAULT_EFFORT

    argv: list[str] = [exe]
    sid = (session_id or "").strip()
    if create:
        if not sid:
            raise ValidationIssue(
                "missing_session_id",
                "create=True requires an exact new session UUID",
            )
        argv.extend(["--session-id", sid])
    elif sid:
        argv.extend(["--resume", sid])
    # Headless packet delivery (mutually exclusive with -p).
    argv.extend(["--prompt-file", str(packet_path)])
    argv.extend(
        [
            "--cwd",
            str(cwd_path),
            "--model",
            model_name,
            "--permission-mode",
            perm,
            "--effort",
            effort_name,
        ]
    )
    if yolo:
        argv.append("--yolo")
    if max_turns is not None and int(max_turns) > 0:
        argv.extend(["--max-turns", str(int(max_turns))])
    if output_format:
        argv.extend(["--output-format", str(output_format)])
    # Product invariants: no crippling flags.
    joined = " ".join(argv)
    if "--no-subagents" in argv or "--no-subagents" in joined:
        raise ValidationIssue(
            "implement_no_subagents_forbidden",
            "Lane A launch argv must not include --no-subagents",
        )
    if FORBIDDEN_DEFAULT_PERMISSION in argv:
        raise ValidationIssue(
            "implement_dontask_forbidden",
            "Lane A launch argv must not use permission-mode dontAsk",
        )
    if "-p" in argv or "--single" in argv:
        raise ValidationIssue(
            "implement_prompt_conflict",
            "Lane A launch uses --prompt-file only; do not also pass -p/--single",
        )
    return argv


def launch_payload(
    repo_root: Path,
    *,
    session_id: str | None = None,
    packet: str | Path,
    cwd: str | Path | None = None,
    model: str | None = None,
    permission_mode: str | None = None,
    executable: str | None = None,
    create: bool = False,
    batch: int | None = None,
    exec_process: bool = False,
) -> dict[str, Any]:
    """Build (and optionally exec) Lane A launch argv. Default is print-only."""
    state = load_state(repo_root)
    sid = (session_id or (state.session_id if state else None) or "").strip()
    if not sid:
        raise ValidationIssue(
            "missing_session_id",
            "session_id required (pass --session-id or run prepare first)",
        )
    worktree = cwd or (state.worktree if state else None) or str(Path(repo_root).resolve())
    model_name = model or (state.model if state else None) or DEFAULT_MODEL
    perm = permission_mode or (state.permission_mode if state else None) or DEFAULT_PERMISSION_MODE
    exe = executable or (state.executable if state else None) or DEFAULT_EXECUTABLE

    argv = build_launch_argv(
        session_id=sid,
        packet=packet,
        cwd=worktree,
        model=model_name,
        permission_mode=perm,
        executable=exe,
        create=create,
        effort=DEFAULT_EFFORT,
        yolo=True,
        max_turns=80,
        output_format="json",
    )

    # Persist last launch pointers for status/resume.
    if state is None:
        state = ImplementState(
            worktree=str(Path(worktree).expanduser().resolve()),
            model=model_name,
            permission_mode=_normalize_permission(perm),
            session_id=sid,
            executable=exe,
            created_at=_utc_now(),
        )
    else:
        state.session_id = sid
        state.worktree = str(Path(worktree).expanduser().resolve())
        state.model = model_name
        state.permission_mode = _normalize_permission(perm)
        state.executable = exe
    state.last_packet = str(Path(packet).expanduser().resolve())
    if batch is not None:
        state.last_batch = int(batch)
    save_state(repo_root, state)

    payload: dict[str, Any] = {
        "ok": True,
        "action": "launch",
        "session_id": sid,
        "argv": argv,
        "argv_joined": " ".join(argv),
        "cwd": str(Path(worktree).expanduser().resolve()),
        "packet": str(Path(packet).expanduser().resolve()),
        "model": model_name,
        "permission_mode": _normalize_permission(perm),
        "create": create,
        "launched": False,
        "mutated_repo": False,
        "model_calls_made": False,
        "notes": [
            "Default is print-only; pass --exec to spawn the process",
            "Never defaults to dontAsk; does not pass --no-subagents",
        ],
    }

    if exec_process:
        # Optional operator convenience; not the default host path.
        proc = subprocess.run(argv, cwd=str(Path(worktree).expanduser().resolve()))
        payload["launched"] = True
        payload["model_calls_made"] = True
        payload["exit_code"] = int(proc.returncode)
        payload["ok"] = proc.returncode == 0
        return payload

    return payload


def resume_batch_payload(
    repo_root: Path,
    *,
    batch: int,
    packet: str | Path,
    session_id: str | None = None,
    cwd: str | Path | None = None,
    model: str | None = None,
    permission_mode: str | None = None,
    executable: str | None = None,
    exec_process: bool = False,
) -> dict[str, Any]:
    """Print launch argv for the next batch packet (same session, resume)."""
    payload = launch_payload(
        repo_root,
        session_id=session_id,
        packet=packet,
        cwd=cwd,
        model=model,
        permission_mode=permission_mode,
        executable=executable,
        create=False,
        batch=batch,
        exec_process=exec_process,
    )
    payload["action"] = "resume-batch"
    payload["batch"] = int(batch)
    return payload


def _git_rev_parse(cwd: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(cwd),
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    tip = (result.stdout or "").strip()
    return tip or None


def parse_unittest_output(text: str) -> dict[str, int]:
    """Parse `unittest` summary lines into counts."""
    ran_match = _RAN_RE.search(text or "")
    total = int(ran_match.group(1)) if ran_match else 0
    failures = 0
    errors = 0
    skipped = 0
    fail_match = _FAIL_RE.search(text or "")
    if fail_match:
        if fail_match.group(1):
            failures = int(fail_match.group(1))
        if fail_match.group(2):
            errors = int(fail_match.group(2))
    # Also handle "FAILED (failures=1)" without errors= and "OK (skipped=N)"
    alt_fail = re.search(r"failures=(\d+)", text or "")
    alt_err = re.search(r"errors=(\d+)", text or "")
    if alt_fail:
        failures = int(alt_fail.group(1))
    if alt_err:
        errors = int(alt_err.group(1))
    skip_match = re.search(r"skipped=(\d+)", text or "", re.IGNORECASE)
    if skip_match:
        skipped = int(skip_match.group(1))
    failed = failures + errors
    passed = max(total - failed - skipped, 0)
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
    }


def run_gate(
    repo_root: Path,
    *,
    batch: int,
    focused: bool = False,
    test_command: list[str] | None = None,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    """Run tests, record tip + counts under gates/batch-N.json. Non-zero on fail."""
    ensure_implement_dirs(repo_root)
    work_cwd = Path(cwd).expanduser().resolve() if cwd else Path(repo_root).resolve()

    if test_command:
        cmd = list(test_command)
    elif focused:
        cmd = [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-p",
            "test_cobbler_agents_implement.py",
        ]
    else:
        cmd = [sys.executable, "-m", "unittest", "discover", "-s", "tests"]

    proc = subprocess.run(
        cmd,
        cwd=str(work_cwd),
        capture_output=True,
        text=True,
    )
    combined = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    counts = parse_unittest_output(combined)
    tip = _git_rev_parse(work_cwd)

    warnings: list[str] = []
    done_path = done_dir(repo_root) / f"batch-{int(batch)}.json"
    done_report: dict[str, Any] | None = None
    if done_path.is_file():
        try:
            done_report = json.loads(done_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            warnings.append(f"done report present but invalid JSON: {exc}")
    else:
        warnings.append(
            f"done report missing (non-fatal for dogfood): {done_path}"
        )

    record = {
        "ok": proc.returncode == 0 and counts["failed"] == 0,
        "action": "gate",
        "batch": int(batch),
        "tip": tip,
        "tests": counts,
        "exit_code": int(proc.returncode),
        "command": cmd,
        "focused": focused,
        "cwd": str(work_cwd),
        "done_report_path": str(done_path),
        "done_report_present": done_path.is_file(),
        "done_report": done_report,
        "warnings": warnings,
        "stdout_tail": (proc.stdout or "")[-2000:],
        "stderr_tail": (proc.stderr or "")[-2000:],
        "recorded_at": _utc_now(),
        "mutated_repo": False,
        "model_calls_made": False,
    }

    gate_path = gates_dir(repo_root) / f"batch-{int(batch)}.json"
    gate_path.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    record["gate_path"] = str(gate_path)

    state = load_state(repo_root)
    if state is not None:
        state.last_batch = int(batch)
        save_state(repo_root, state)

    return record


def status_payload(repo_root: Path) -> dict[str, Any]:
    """Show implement runtime state if present."""
    root = implement_root(repo_root)
    state = load_state(repo_root)
    gate_files = sorted(gates_dir(repo_root).glob("batch-*.json")) if root.is_dir() else []
    done_files = sorted(done_dir(repo_root).glob("batch-*.json")) if root.is_dir() else []
    return {
        "ok": True,
        "action": "status",
        "present": state is not None,
        "repo_root": str(Path(repo_root).resolve()),
        "runtime_dir": str(root),
        "state": state.to_dict() if state else None,
        "gates": [str(p) for p in gate_files],
        "done_reports": [str(p) for p in done_files],
        "mutated_repo": False,
        "model_calls_made": False,
    }
