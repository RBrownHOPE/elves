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

# Operator model aliases for Grok Build implement labor.
# Alias names inspired by stdevMac/grok-in-claude + grok-in-codex (Apache-2.0) companion
# presets; slugs remain Elves-owned and should be re-checked against `grok models`.
MODEL_ALIASES: dict[str, dict[str, str]] = {
    "fast": {"model": "grok-composer-2.5-fast"},
    "deep": {"model": "grok-4.5", "effort": "high"},
}

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
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except OSError as exc:
        raise ValidationIssue(
            "implement_state_read_error",
            f"Unable to read implement state {path}: {exc}",
            path=str(path),
        ) from exc
    except json.JSONDecodeError as exc:
        raise ValidationIssue(
            "implement_state_invalid",
            f"Implement state is not valid JSON: {path} ({exc})",
            path=str(path),
        ) from exc
    if not isinstance(data, dict):
        raise ValidationIssue(
            "implement_state_invalid",
            f"Implement state is not a JSON object: {path}",
            path=str(path),
        )
    try:
        return ImplementState.from_dict(data)
    except (TypeError, ValueError, KeyError) as exc:
        raise ValidationIssue(
            "implement_state_invalid",
            f"Implement state has invalid fields: {path} ({exc})",
            path=str(path),
        ) from exc


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
    adapter: str = "grok-build",
) -> dict[str, Any]:
    """Record implementer metadata. Creates dirs mode 0700. No network."""
    wt = str(Path(worktree).expanduser().resolve()) if worktree else str(Path(repo_root).resolve())
    mode = (permission_mode or DEFAULT_PERMISSION_MODE).strip()
    if not mode:
        mode = DEFAULT_PERMISSION_MODE
    adapter_name = (adapter or "grok-build").strip().lower() or "grok-build"
    if adapter_name in {"opencode", "opencode-labor"}:
        adapter_name = "opencode-cli"
    is_opencode = adapter_name == "opencode-cli"
    if mode == FORBIDDEN_DEFAULT_PERMISSION and not is_opencode:
        # Explicit dontAsk is allowed only if the operator forces it; prepare still
        # warns by refusing to treat it as the lane default — block as product rule.
        raise ValidationIssue(
            "implement_dontask_forbidden",
            "permission_mode=dontAsk is forbidden for Lane A prepare (use auto or acceptEdits)",
            hint="Default is auto; never default headless to dontAsk",
        )

    existing = load_state(repo_root)
    now = _utc_now()
    default_model = (
        "openrouter/qwen/qwen3-max" if is_opencode else DEFAULT_MODEL
    )
    default_exe = "opencode" if is_opencode else DEFAULT_EXECUTABLE
    raw_model = (model or "").strip() or default_model
    resolved_model, _resolved_effort, alias_notes = resolve_implement_model(
        raw_model, adapter=adapter_name
    )
    model_value = resolved_model
    exe_value = (executable or "").strip() or default_exe
    state = ImplementState(
        lane=(lane or DEFAULT_LANE).strip() or DEFAULT_LANE,
        git_mode=(git_mode or DEFAULT_GIT_MODE).strip() or DEFAULT_GIT_MODE,
        adapter=adapter_name,
        model=model_value,
        permission_mode=mode,
        worktree=wt,
        branch=branch,
        session_id=(session_id.strip() if session_id else None)
        or (existing.session_id if existing else None),
        executable=exe_value,
        subagents=True,
        created_at=existing.created_at if existing and existing.created_at else now,
        updated_at=now,
        last_batch=existing.last_batch if existing else None,
        last_packet=existing.last_packet if existing else None,
        notes=[
            (
                "OpenCode implement labor (Claude Code–like agent; OpenRouter/other models)"
                if is_opencode
                else "Lane A fast implementer (default for optional Grok Build)"
            ),
            "Host/human launches; CLI prints argv unless --exec",
            (
                "OpenCode: exact --session preferred; never bare --continue; use --auto carefully"
                if is_opencode
                else "Never pass --no-subagents; never default permission to dontAsk"
            ),
            *alias_notes,
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


def resolve_implement_model(
    model: str | None,
    *,
    effort: str | None = None,
    adapter: str = "grok-build",
) -> tuple[str, str | None, list[str]]:
    """Resolve operator model input to (model_id, effort_or_None, notes).

    Grok aliases ``fast`` / ``deep`` expand to concrete slugs. OpenCode and
    explicit provider/model ids pass through unchanged. Alias idea adapted from
    stdevMac/grok-in-claude and grok-in-codex companion presets (Apache-2.0).

    When ``effort`` is ``None``, aliases may supply a default (e.g. deep → high);
    otherwise the caller-supplied effort wins.
    """
    notes: list[str] = []
    adapter_name = (adapter or "grok-build").strip().lower() or "grok-build"
    explicit_effort = (effort or "").strip() or None
    raw = (model or "").strip()
    if not raw:
        if adapter_name in {"opencode-cli", "opencode-labor", "opencode"}:
            return "openrouter/qwen/qwen3-max", explicit_effort, notes
        return DEFAULT_MODEL, explicit_effort or DEFAULT_EFFORT, notes

    if adapter_name in {"opencode-cli", "opencode-labor", "opencode"}:
        return raw, explicit_effort, notes

    key = raw.lower()
    alias = MODEL_ALIASES.get(key)
    if alias:
        resolved_model = alias["model"]
        resolved_effort = explicit_effort or alias.get("effort") or DEFAULT_EFFORT
        notes.append(
            f"Resolved model alias `{raw}` → model={resolved_model}"
            + (f", effort={resolved_effort}" if resolved_effort else "")
            + " (alias pattern credit: stdevMac/grok-in-claude, grok-in-codex)"
        )
        return resolved_model, resolved_effort, notes

    return raw, explicit_effort or DEFAULT_EFFORT, notes


def humanize_grok_failure(
    *,
    stderr: str | None = None,
    stdout: str | None = None,
    message: str | None = None,
    exit_code: int | None = None,
) -> str:
    """Map noisy Grok CLI / Rust dumps to a short operator message.

    Failure-mapping approach adapted from stdevMac/grok-in-claude and
    grok-in-codex ``humanizeGrokFailure`` (Apache-2.0); wording is Elves-owned.
    """
    parts = [message, stderr, stdout]
    blob = "\n".join(str(p).strip() for p in parts if p and str(p).strip())
    if not blob:
        if exit_code is not None and exit_code != 0:
            return f"Grok exited with code {exit_code}."
        return "Grok failed with no error details."

    compact = re.sub(r"\s+", " ", blob).strip()

    if re.search(r"RequirementError", blob, re.I) and re.search(
        r"run_terminal_cmd|background|--tools", blob, re.I
    ):
        return (
            "Grok CLI rejected the tool configuration while creating a session. "
            "On Grok Build ~0.2.93 prefer default tools + `--disallowed-tools` denylists "
            "for read-only/media modes; avoid `--tools` allowlists. "
            "Lane A implement still uses the default toolset + `--yolo`."
        )

    if re.search(r"RequirementError", blob, re.I):
        brief_match = re.search(r"RequirementError[:\s{]*([^}\n]{10,200})", blob, re.I)
        brief = (brief_match.group(1).strip() if brief_match else compact[:180])
        return (
            f"Grok CLI requirement error: {brief}. "
            "Check `grok version`, auth (`grok login`), and plan features."
        )

    if re.search(r"not logged in|unauthori[sz]ed|authentication required|auth.*fail", blob, re.I):
        return "Grok is not authenticated. Run `grok login`."

    if re.search(r"command not found|No such file or directory.*grok|Grok CLI not found", blob, re.I):
        return "Grok CLI not found. Install Grok Build and ensure `grok` is on PATH."

    if re.search(r"rate.?limit|too many requests|\b429\b", blob, re.I):
        return "Grok rate-limited the request. Wait and retry."

    if re.search(r"model .+ not found|unknown model|invalid model", blob, re.I):
        return (
            "Grok rejected the model id. Use a valid model (e.g. `grok-4.5`) "
            "or an implement alias (`fast` / `deep`)."
        )

    first_useful = None
    for line in blob.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.match(r"^\[stderr\]", line, re.I):
            continue
        if re.match(r"^thread '", line, re.I):
            continue
        if len(line) >= 400:
            continue
        first_useful = line
        break
    if not first_useful:
        first_useful = compact[:280]

    if exit_code is not None and exit_code != 0:
        return f"Grok failed (exit {exit_code}): {first_useful}"
    return first_useful


def build_launch_argv(
    *,
    session_id: str | None = None,
    packet: str | Path,
    cwd: str | Path,
    model: str = DEFAULT_MODEL,
    permission_mode: str = DEFAULT_PERMISSION_MODE,
    executable: str = DEFAULT_EXECUTABLE,
    create: bool = False,
    effort: str | None = None,
    yolo: bool = True,
    max_turns: int | None = 80,
    output_format: str | None = "json",
    adapter: str = "grok-build",
    check: bool = False,
) -> list[str]:
    """Build headless implementer argv for Grok Build (Lane A) or OpenCode.

    Grok Build dogfood (0.2.93):
    - ``--prompt-file`` or ``-p`` both trigger headless multi-turn with tools.
    - ``--yolo`` / ``--always-approve`` is required for unattended edits.
    - Exact session id only; never bare continue.
    - Optional ``--check`` asks Grok to verify before returning (CLI flag).

    OpenCode (opencode.ai):
    - ``opencode run`` with packet contents as message; ``--auto`` for unattended tools.
    - Model format ``provider/model`` (often via OpenRouter).
    - Exact ``--session <id>`` only (never bare ``--continue``).
    """
    packet_path = Path(packet).expanduser().resolve()
    if not packet_path.is_file():
        raise ValidationIssue(
            "packet_missing",
            f"Packet file not found: {packet_path}",
        )
    cwd_path = Path(cwd).expanduser().resolve()
    adapter_name = (adapter or "grok-build").strip().lower()
    sid = (session_id or "").strip()
    if sid.lower() in {"latest", "last", "continue", "most-recent", "most_recent"}:
        raise ValidationIssue(
            "ambiguous_session_id",
            f"Session id `{sid}` is ambiguous and forbidden for implement launch",
            hint="Use an exact UUID/session id from the registry",
        )

    if adapter_name in {"opencode-cli", "opencode-labor", "opencode"}:
        # Attach packet via --file to avoid ARG_MAX (do not stuff full packet into argv).
        exe = (executable or "opencode").strip() or "opencode"
        model_name, _, _ = resolve_implement_model(model, adapter=adapter_name)
        argv: list[str] = [
            exe,
            "run",
            "--dir",
            str(cwd_path),
            "--file",
            str(packet_path),
        ]
        if sid:
            argv.extend(["--session", sid])
        if model_name:
            argv.extend(["--model", model_name])
        if yolo:
            argv.append("--auto")
        argv.append(
            "Implement the attached task packet. Follow host packet constraints; "
            "prefer exact session continuity; do not invent secrets."
        )
        if "-c" in argv or "--continue" in argv:
            raise ValidationIssue(
                "ambiguous_session_flag",
                "OpenCode implement launch must not use bare --continue",
            )
        return argv

    # Default: Grok Build Lane A
    perm = _normalize_permission(permission_mode)
    exe = (executable or DEFAULT_EXECUTABLE).strip() or DEFAULT_EXECUTABLE
    model_name, effort_name, _alias_notes = resolve_implement_model(
        model, effort=effort, adapter="grok-build"
    )
    effort_name = (effort_name or DEFAULT_EFFORT).strip() or DEFAULT_EFFORT

    argv = [exe]
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
    if check:
        # Grok CLI post-work verification flag (also used by community companions).
        argv.append("--check")
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
    effort: str | None = None,
    check: bool = False,
) -> dict[str, Any]:
    """Build (and optionally exec) Lane A launch argv. Default is print-only."""
    state = load_state(repo_root)
    sid = (session_id or (state.session_id if state else None) or "").strip()
    adapter_name = (state.adapter if state else "grok-build") or "grok-build"
    # OpenCode may start without a pre-allocated session id (host captures after first run).
    if not sid and adapter_name not in {"opencode-cli", "opencode-labor", "opencode"}:
        raise ValidationIssue(
            "missing_session_id",
            "session_id required (pass --session-id or run prepare first)",
        )
    worktree = cwd or (state.worktree if state else None) or str(Path(repo_root).resolve())
    is_opencode = adapter_name in {"opencode-cli", "opencode-labor", "opencode"}
    raw_model = model or (state.model if state else None) or (
        "openrouter/qwen/qwen3-max" if is_opencode else DEFAULT_MODEL
    )
    model_name, effort_name, alias_notes = resolve_implement_model(
        raw_model, effort=effort, adapter=adapter_name
    )
    perm = permission_mode or (state.permission_mode if state else None) or DEFAULT_PERMISSION_MODE
    exe = executable or (state.executable if state else None) or (
        "opencode" if is_opencode else DEFAULT_EXECUTABLE
    )

    argv = build_launch_argv(
        session_id=sid or None,
        packet=packet,
        cwd=worktree,
        # Pass raw model so aliases resolve once (deep → high effort).
        model=raw_model,
        permission_mode=perm,
        executable=exe,
        create=create,
        effort=effort,
        yolo=True,
        max_turns=80,
        output_format="json",
        adapter=adapter_name,
        check=bool(check) and not is_opencode,
    )

    # Persist last launch pointers for status/resume. Store resolved model id.
    persist_model = model_name if not is_opencode else (model or model_name)
    if state is None:
        state = ImplementState(
            worktree=str(Path(worktree).expanduser().resolve()),
            adapter=adapter_name,
            model=persist_model,
            permission_mode=perm if is_opencode else _normalize_permission(perm),
            session_id=sid or None,
            executable=exe,
            created_at=_utc_now(),
        )
    else:
        state.session_id = sid or state.session_id
        state.worktree = str(Path(worktree).expanduser().resolve())
        state.model = persist_model
        state.adapter = adapter_name
        state.permission_mode = perm if is_opencode else _normalize_permission(perm)
        state.executable = exe
    state.last_packet = str(Path(packet).expanduser().resolve())
    if batch is not None:
        state.last_batch = int(batch)
    save_state(repo_root, state)

    notes = [
        "Default is print-only; pass --exec to spawn the process",
        "Grok Lane A: never dontAsk / no --no-subagents",
        "OpenCode labor: opencode run --auto; exact --session preferred; OpenRouter provider/model",
    ]
    notes.extend(alias_notes)
    if check and not is_opencode:
        notes.append("Grok --check enabled (post-work verification; higher latency/cost)")

    payload: dict[str, Any] = {
        "ok": True,
        "action": "launch",
        "session_id": sid or None,
        "adapter": adapter_name,
        "argv": argv,
        "argv_joined": " ".join(argv),
        "cwd": str(Path(worktree).expanduser().resolve()),
        "packet": str(Path(packet).expanduser().resolve()),
        "model": model_name if not is_opencode else raw_model,
        "effort": effort_name if not is_opencode else None,
        "check": bool(check) and not is_opencode,
        "permission_mode": perm if is_opencode else _normalize_permission(perm),
        "create": create,
        "launched": False,
        "mutated_repo": False,
        "model_calls_made": False,
        "notes": notes,
    }

    if exec_process:
        # Optional operator convenience; not the default host path.
        try:
            proc = subprocess.run(
                argv,
                cwd=str(Path(worktree).expanduser().resolve()),
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            raise ValidationIssue(
                "implement_launch_spawn_failed",
                f"Unable to spawn implementer argv {argv!r}: {exc}",
                path=str(worktree),
            ) from exc
        payload["launched"] = True
        payload["model_calls_made"] = True
        payload["exit_code"] = int(proc.returncode)
        payload["ok"] = proc.returncode == 0
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        if stdout:
            payload["stdout_tail"] = stdout[-4000:]
        if stderr:
            payload["stderr_tail"] = stderr[-4000:]
        if not payload["ok"] and not is_opencode:
            payload["error_human"] = humanize_grok_failure(
                stderr=stderr,
                stdout=stdout,
                exit_code=int(proc.returncode),
            )
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
    effort: str | None = None,
    check: bool = False,
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
        effort=effort,
        check=check,
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

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(work_cwd),
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise ValidationIssue(
            "implement_gate_spawn_failed",
            f"Unable to run gate command {cmd!r} in {work_cwd}: {exc}",
            path=str(work_cwd),
        ) from exc
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
