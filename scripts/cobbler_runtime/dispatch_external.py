"""Isolated external-lane subprocess lifecycle for council dispatch.

Creates the tracked-source snapshot *before* building adapter argv, rewrites
repo/CWD flags to the snapshot, optionally wraps with a platform FS sandbox,
and guarantees cleanup on every exit path. Optional isolation failure falls
back to host-native (never launches the external process in the original repo).
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .adapters import (
    ADAPTER_CONTRACT_PAIRS,
    AdapterInvocation,
    build_readonly_invocation,
    decode_adapter_output,
    default_decoder_for_adapter,
    validate_adapter_contract_pair,
    validate_extra_args,
)
from .context import (
    redact_structure,
    redact_text,
    write_text_artifact,
)
from .dispatch_attempt import record_command_digests
from .isolation import (
    IsolationSpec,
    IsolatedLane,
    create_tracked_snapshot,
    rewrite_argv_repo_paths,
    wrap_argv_with_sandbox,
)
from .schema import EffectiveAttempt, ValidationIssue


@dataclass
class ExternalLaunchPlan:
    argv: list[str]
    cwd: str
    env: dict[str, str]
    isolated: IsolatedLane | None
    isolation_meta: dict[str, Any]
    fallback_host_native: bool
    invocation: AdapterInvocation | None
    stdin_bytes: bytes | None


def prepare_external_launch(
    *,
    spec: Any,
    attempt: EffectiveAttempt,
    attempt_index: int,
    repo_root: Path,
    packet_path: Path,
    prompt_path: Path,
    packet_dict: Mapping[str, Any],
    redacted_task: str,
    exact_secret_values: frozenset[str],
    grants: Sequence[str],
    scrub_env: Mapping[str, str],
    command_override: tuple[str, ...] | None,
    parent_env: Mapping[str, str] | None,
) -> ExternalLaunchPlan:
    """Build isolated launch plan. Isolation happens before argv construction."""
    isolation_required = bool(getattr(spec, "require_isolation", False)) or bool(
        getattr(attempt, "require_isolation", False)
    )
    # command_override is a test/fixture path: skip isolation unless explicitly required.
    use_isolation = (
        attempt.adapter not in {"host-native"}
        and not bool(getattr(spec, "skip_isolation", False))
        and (command_override is None or isolation_required)
    )
    isolated: IsolatedLane | None = None
    isolation_meta: dict[str, Any] = {"enabled": False}
    launch_repo = Path(repo_root)

    if use_isolation:
        try:
            from .isolation import detect_fs_sandbox_backend  # noqa: PLC0415

            backend = detect_fs_sandbox_backend()
            # Absolute-path FS boundary needs sandbox-exec/bwrap. Required routes block when
            # missing; optional routes still get a tracked snapshot (relative-path safety) but
            # document that absolute host/sibling reads need a platform sandbox.
            if isolation_required and backend is None:
                raise ValidationIssue(
                    "isolation_sandbox_unavailable",
                    "Required filesystem sandbox backend not available "
                    "(sandbox-exec on macOS or bwrap on Linux)",
                )
            isolated = create_tracked_snapshot(
                IsolationSpec(
                    repo_root=Path(repo_root),
                    lane_id=str(getattr(spec, "lane_id", "lane")),
                    include_instructions_as_data=bool(
                        getattr(spec, "include_instructions_as_data", False)
                    ),
                    credential_grants={
                        name: scrub_env[name]
                        for name in grants
                        if name in scrub_env and scrub_env[name]
                    },
                    base_env={
                        "PATH": scrub_env.get("PATH", os.environ.get("PATH", "/usr/bin:/bin"))
                    },
                    # Only wrap with OS sandbox when available; required already gated above.
                    require_fs_sandbox=bool(isolation_required and backend is not None),
                )
            )
            launch_repo = isolated.snapshot
            isolation_meta = {
                "enabled": True,
                "snapshot": str(isolated.snapshot),
                "sandbox_backend": isolated.sandbox_backend,
                "instruction_data_files": list(isolated.instruction_data_files),
            }
        except Exception as exc:  # noqa: BLE001
            if isolation_required:
                raise ValidationIssue(
                    "required_isolation_failed",
                    f"Required isolation failed: {type(exc).__name__}: {exc}; "
                    "refusing repo-root external launch",
                ) from exc
            # Optional: fall back host-native — do not launch external in repo root.
            return ExternalLaunchPlan(
                argv=[],
                cwd=str(repo_root),
                env=dict(scrub_env),
                isolated=None,
                isolation_meta={
                    "enabled": False,
                    "fallback": "host-native",
                    "reason": f"{type(exc).__name__}: {exc}",
                },
                fallback_host_native=True,
                invocation=None,
                stdin_bytes=None,
            )

    # Build argv against snapshot as repo_root so --cd/--cwd embed snapshot paths.
    defaults = ADAPTER_CONTRACT_PAIRS.get(
        attempt.adapter, ("json-stdio", "custom-json-envelope")
    )
    attempt_input_contract = (attempt.input_contract or defaults[0]).strip()
    attempt_output_contract = (attempt.output_contract or defaults[1]).strip()
    if attempt_output_contract == "json-role-report":
        attempt_output_contract = "custom-json-envelope"

    validate_extra_args(attempt.adapter, attempt.extra_args)
    if command_override is None:
        validate_adapter_contract_pair(
            attempt.adapter,
            input_contract=attempt_input_contract,
            output_contract=attempt_output_contract,
        )

    if command_override is not None and attempt_index == 0:
        command = list(command_override)
        invocation = AdapterInvocation(
            adapter=attempt.adapter,
            executable=command[0] if command else "",
            argv=tuple(command),
            read_only=True,
            notes="command_override",
            input_mode="none",
            decoder=default_decoder_for_adapter(attempt.adapter)
            if attempt.adapter != "custom-cli"
            else "custom-json-envelope",
            cwd=str(launch_repo),
        )
    else:
        attempt_session = getattr(attempt, "session_id", None) or getattr(
            spec, "session_id", None
        )
        invocation = build_readonly_invocation(
            adapter=attempt.adapter,
            profile=attempt.profile,
            executable=attempt.executable,
            packet_path=packet_path,
            prompt_path=prompt_path,
            requested_model=attempt.requested_model,
            extra_args=attempt.extra_args,
            packet=dict(packet_dict),
            task=redacted_task,
            role=spec.role,
            input_contract=attempt_input_contract,
            output_contract=attempt_output_contract,
            repo_root=launch_repo,
            session_id=attempt_session,
            cwd=str(launch_repo),
        )
        command = list(invocation.argv)

    if invocation.unavailable:
        raise ValidationIssue(
            "adapter_unavailable",
            invocation.unavailable_reason or "adapter unavailable",
        )
    if not command:
        raise ValidationIssue("empty_command", "empty command")

    # Rewrite any residual original-repo paths and apply sandbox wrapper when present.
    if isolated is not None:
        command = rewrite_argv_repo_paths(
            command, original_repo=Path(repo_root), snapshot=isolated.snapshot
        )
        if isolated.sandbox_backend:
            command = wrap_argv_with_sandbox(command, isolated)
        child_env = dict(isolated.env)
        for key in ("PATH", "LANG", "LC_ALL", "TERM"):
            if key in scrub_env and key not in child_env:
                child_env[key] = scrub_env[key]
        child_cwd = str(isolated.snapshot)
    else:
        child_env = dict(scrub_env)
        child_cwd = str(invocation.cwd or repo_root)

    stdin_bytes = None
    if invocation.stdin_text is not None:
        stdin_bytes = redact_text(
            invocation.stdin_text, exact_values=exact_secret_values
        ).text.encode("utf-8")

    return ExternalLaunchPlan(
        argv=command,
        cwd=child_cwd,
        env=child_env,
        isolated=isolated,
        isolation_meta=isolation_meta,
        fallback_host_native=False,
        invocation=invocation,
        stdin_bytes=stdin_bytes,
    )


async def run_external_subprocess(
    *,
    plan: ExternalLaunchPlan,
    timeout_seconds: float,
    terminate_process_group,
    pgid_alive,
) -> dict[str, Any]:
    """Launch external process; always cleanup isolation. Returns process result dict."""
    isolated = plan.isolated

    def _cleanup() -> dict[str, Any]:
        meta = {"isolation_cleaned": True}
        if isolated is not None:
            root = isolated.root
            try:
                isolated.cleanup()
            except Exception:  # noqa: BLE001
                pass
            meta["isolation_cleaned"] = not root.exists()
            meta["isolation_root"] = str(root)
        return meta

    try:
        proc = await asyncio.create_subprocess_exec(
            *plan.argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE if plan.stdin_bytes is not None else None,
            env=plan.env,
            cwd=plan.cwd,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        meta = _cleanup()
        return {
            "ok": False,
            "failure_class": "launch_error",
            "reason": f"executable not found: {exc}",
            "cleanup": meta,
            "process_launched": False,
        }
    except OSError as exc:
        meta = _cleanup()
        return {
            "ok": False,
            "failure_class": "launch_error",
            "reason": f"launch error: {exc}",
            "cleanup": meta,
            "process_launched": False,
        }

    launched_pgid: int | None
    try:
        launched_pgid = os.getpgid(proc.pid) if proc.pid is not None else None
    except OSError:
        launched_pgid = None

    timed_out = False
    cleanup: dict[str, Any] = {"pgid": launched_pgid}
    stdout_b = b""
    stderr_b = b""
    try:
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(input=plan.stdin_bytes),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            timed_out = True
            cleanup = await terminate_process_group(proc, known_pgid=launched_pgid)
            stdout_b, stderr_b = b"", b""
        except asyncio.CancelledError:
            cleanup = await terminate_process_group(proc, known_pgid=launched_pgid)
            raise
        except Exception as exc:  # noqa: BLE001
            cleanup = await terminate_process_group(proc, known_pgid=launched_pgid)
            return {
                "ok": False,
                "failure_class": "execution_failure",
                "reason": f"execution_runtime_error: {type(exc).__name__}: {exc}",
                "cleanup": cleanup,
                "process_launched": True,
                "exit_code": proc.returncode,
                "stdout_raw": "",
                "stderr_raw": "",
            }

        if not timed_out and launched_pgid is not None and pgid_alive(launched_pgid):
            cleanup = await terminate_process_group(proc, known_pgid=launched_pgid)
            return {
                "ok": False,
                "failure_class": "execution_failure",
                "reason": f"descendant_process_group_still_alive:pgid={launched_pgid}",
                "cleanup": cleanup,
                "process_launched": True,
                "exit_code": proc.returncode,
                "stdout_raw": (stdout_b or b"").decode("utf-8", errors="replace"),
                "stderr_raw": (stderr_b or b"").decode("utf-8", errors="replace"),
            }

        stdout_raw = (stdout_b or b"").decode("utf-8", errors="replace")
        stderr_raw = (stderr_b or b"").decode("utf-8", errors="replace")
        if timed_out:
            return {
                "ok": False,
                "failure_class": "timeout",
                "reason": f"timeout after {timeout_seconds}s",
                "cleanup": cleanup,
                "process_launched": True,
                "exit_code": proc.returncode,
                "timeout": True,
                "stdout_raw": stdout_raw,
                "stderr_raw": stderr_raw,
            }
        return {
            "ok": True,
            "process_launched": True,
            "exit_code": proc.returncode,
            "stdout_raw": stdout_raw,
            "stderr_raw": stderr_raw,
            "cleanup": cleanup,
            "timeout": False,
        }
    finally:
        cleanup_meta = _cleanup()
        cleanup.update(cleanup_meta)
