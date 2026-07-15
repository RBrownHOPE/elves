"""Host-native worker profiles and exact-session launch specifications."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .schema import ValidationIssue


@dataclass(frozen=True)
class NativeWorkerSpec:
    host: str
    profile: str
    effort: str
    model_policy: str
    requested_model: str | None
    separate_session: bool
    cwd: str
    argv: tuple[str, ...]
    stdin_packet: bool
    session_id_source: str
    resume_argv: tuple[str, ...] | None = None
    cache_handoff: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["argv"] = list(self.argv)
        payload["resume_argv"] = list(self.resume_argv) if self.resume_argv else None
        return payload


def _exact_session_id(session_id: str) -> str:
    value = session_id.strip()
    if not value or value in {"last", "latest", "--last"} or value.startswith("-"):
        raise ValidationIssue("invalid_exact_session_id", "An exact worker session id is required")
    return value


def build_native_worker_spec(
    *,
    host: str,
    worktree: Path,
    effort: str,
    requested_model: str | None = None,
    session_id: str | None = None,
) -> NativeWorkerSpec:
    host_token = host.strip().lower().replace("_", "-")
    effort_token = effort.strip().lower()
    if effort_token not in {"low", "medium", "high"}:
        raise ValidationIssue("invalid_worker_effort", f"Invalid worker effort `{effort}`")
    cwd = str(worktree.resolve())
    model_policy = "explicit_model_pin" if requested_model else "inherit_live_driver_model"

    if host_token == "codex":
        common = [
            "codex", "exec", "--json", "--ignore-user-config", "--ignore-rules",
            "--sandbox", "workspace-write", "-c", f'model_reasoning_effort="{effort_token}"',
        ]
        if requested_model:
            common.extend(["--model", requested_model])
        if session_id is None:
            argv = (*common, "-C", cwd, "-")
            resume = None
        else:
            sid = _exact_session_id(session_id)
            # `exec resume` has no -C; the supervisor must set the OS cwd exactly.
            argv = tuple(["codex", "exec", "resume", "--json", "--ignore-user-config", "--ignore-rules", "-c", f'model_reasoning_effort="{effort_token}"'] + (["--model", requested_model] if requested_model else []) + [sid, "-"])
            resume = argv
        return NativeWorkerSpec(
            host="codex", profile="elves-native-worker", effort=effort_token,
            model_policy=model_policy, requested_model=requested_model,
            separate_session=True, cwd=cwd, argv=tuple(argv), stdin_packet=True,
            session_id_source="thread.started.thread_id", resume_argv=resume,
        )

    if host_token in {"claude", "claude-code"}:
        common = [
            "claude", "--print", "--output-format", "stream-json", "--input-format", "text",
            "--effort", effort_token, "--permission-mode", "acceptEdits",
        ]
        if requested_model:
            common.extend(["--model", requested_model])
        if session_id is None:
            # Claude accepts a caller-generated UUID, providing exact identity before launch.
            import uuid
            sid = str(uuid.uuid4())
            argv = tuple(common + ["--session-id", sid, "-"])
        else:
            sid = _exact_session_id(session_id)
            argv = tuple(common + ["--resume", sid, "-"])
        return NativeWorkerSpec(
            host="claude-code", profile="elves-native-worker", effort=effort_token,
            model_policy=model_policy, requested_model=requested_model,
            separate_session=True, cwd=cwd, argv=argv, stdin_packet=True,
            session_id_source="requested_session_id", resume_argv=argv if session_id else None,
        )
    raise ValidationIssue("unsupported_host", f"Unsupported native worker host `{host}`")


def parse_codex_thread_id(jsonl: str) -> str:
    """Capture the exact Codex worker thread id from structured launch output."""
    for line in jsonl.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thread.started" and isinstance(event.get("thread_id"), str):
            return _exact_session_id(event["thread_id"])
    raise ValidationIssue("worker_session_id_missing", "Codex output did not contain thread.started.thread_id")
