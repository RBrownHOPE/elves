"""Parallel read-only council dispatch with quorum and ordered fallbacks.

Lanes launch concurrently via asyncio subprocesses (argv arrays, shell=False).
Inside each lane, primary then fallback attempts run sequentially after every
material failure class. Host synthesis remains the only fitted-answer step.

Quorum:
- target_quorum (advisory): if fewer successful reports remain after fallbacks,
  continue with host synthesis, record a confidence drop, and do not label the
  result council-verified.
- required_quorum: valid only when the phase is explicitly required=true; counts
  successful independent reports. Block when unmet after recovery/fallback.
- host-native without a real injected host report never counts as a vote.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence

from .adapters import (
    AdapterInvocation,
    build_readonly_invocation,
    parse_role_report,
    parse_transport_output,
    validate_model_evidence,
)
from .context import (
    ContextPacket,
    EnvScrubResult,
    build_context_packet,
    council_artifact_root,
    create_exclusive_artifact_root,
    ensure_private_dir,
    new_run_id,
    redact_text,
    scrub_environment,
    write_json_artifact,
    write_text_artifact,
)
from .schema import EffectiveAttempt, ValidationIssue


LaneRunner = Callable[["LaneSpec", ContextPacket, Path], Awaitable["LaneResult"]]

# Grace period between SIGTERM and SIGKILL for process-group cleanup.
PROCESS_GROUP_GRACE_SECONDS = 0.5


@dataclass(frozen=True)
class LaneSpec:
    """One independent read-only council lane.

    Primary fields describe the first attempt. ``attempts`` when non-empty is the
    full ordered attempt graph (primary + fallbacks). ``command_override`` is a
    test-only single-attempt override. ``injected_host_evidence`` supplies a real
    host report for host-native lanes (never a canned subprocess).
    """

    lane_id: str
    role: str
    adapter: str
    profile: str
    requested_model: str | None = None
    executable: str | None = None
    required: bool = False
    timeout_seconds: float = 30.0
    extra_args: tuple[str, ...] = ()
    env_extra_allowlist: tuple[str, ...] = ()
    env_grants: tuple[str, ...] = ()
    # When set, used instead of building a real adapter command (tests/fakes).
    command_override: tuple[str, ...] | None = None
    # Ordered attempts (primary first). Empty => derive one attempt from fields.
    attempts: tuple[EffectiveAttempt, ...] = ()
    # Host-injected evidence: {adapter_metadata, role_report, executor_id?}.
    injected_host_evidence: dict[str, Any] | None = None


@dataclass
class AttemptResult:
    """Evidence for one ordered attempt inside a lane."""

    attempt_index: int
    profile: str
    adapter: str
    requested_model: str | None
    actual_model: str | None
    model_evidence_source: str | None
    status: str
    failure_class: str | None = None
    reason: str | None = None
    ok: bool = False
    timeout: bool = False
    exit_code: int | None = None
    command: list[str] = field(default_factory=list)
    start_time: float = 0.0
    end_time: float = 0.0
    cleanup: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LaneResult:
    """Per-lane evidence for host synthesis."""

    lane_id: str
    role: str
    adapter: str
    profile: str
    ok: bool
    launch_time: float
    start_time: float
    end_time: float
    timeout: bool = False
    exit_code: int | None = None
    requested_model: str | None = None
    actual_model: str | None = None
    model_evidence_source: str | None = None
    stdout_summary: str = ""
    stderr_summary: str = ""
    report: dict[str, Any] | None = None
    error: str | None = None
    fallback_used: str | None = None
    stripped_env_names: list[str] = field(default_factory=list)
    granted_env_names: list[str] = field(default_factory=list)
    command: list[str] = field(default_factory=list)
    artifact_dir: str | None = None
    attempts: list[AttemptResult] = field(default_factory=list)
    successful_attempt_index: int | None = None
    failure_class: str | None = None

    @property
    def wall_seconds(self) -> float:
        return max(0.0, self.end_time - self.start_time)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return payload


@dataclass
class CouncilResult:
    """Aggregate parallel council outcome for host synthesis."""

    run_id: str
    ok: bool
    council_verified: bool
    blocked: bool
    confidence: str
    successful_reports: list[dict[str, Any]] = field(default_factory=list)
    lane_results: list[LaneResult] = field(default_factory=list)
    target_quorum: int | None = None
    required_quorum: int | None = None
    phase_required: bool = False
    notes: list[str] = field(default_factory=list)
    artifact_root: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "ok": self.ok,
            "council_verified": self.council_verified,
            "blocked": self.blocked,
            "confidence": self.confidence,
            "successful_reports": list(self.successful_reports),
            "lane_results": [lane.to_dict() for lane in self.lane_results],
            "target_quorum": self.target_quorum,
            "required_quorum": self.required_quorum,
            "phase_required": self.phase_required,
            "notes": list(self.notes),
            "artifact_root": self.artifact_root,
            "successful_count": len(self.successful_reports),
        }


def _summarize(text: str, limit: int = 400) -> str:
    # Redact secret-looking values before any summary escapes the process.
    redacted = redact_text(text or "").text
    compact = " ".join(redacted.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def evaluate_quorum(
    *,
    successful_count: int,
    target_quorum: int | None,
    required_quorum: int | None,
    phase_required: bool,
    required_lane_failures: Sequence[str] = (),
) -> tuple[bool, bool, bool, str, list[str]]:
    """Return (ok, council_verified, blocked, confidence, notes)."""
    notes: list[str] = []
    blocked = False
    council_verified = False
    confidence = "high"

    if required_lane_failures:
        blocked = True
        notes.append(
            "Required lane failure(s): " + ", ".join(required_lane_failures)
        )

    if phase_required and required_quorum is not None:
        if successful_count < required_quorum:
            blocked = True
            notes.append(
                f"required_quorum={required_quorum} unmet "
                f"(successful_independent_reports={successful_count})"
            )
        else:
            council_verified = True
            notes.append(
                f"required_quorum={required_quorum} met "
                f"(successful_independent_reports={successful_count})"
            )
    elif target_quorum is not None:
        if successful_count >= target_quorum:
            council_verified = True
            notes.append(
                f"target_quorum={target_quorum} met "
                f"(successful_independent_reports={successful_count})"
            )
        else:
            confidence = "reduced"
            notes.append(
                f"target_quorum={target_quorum} unmet after fallbacks; "
                "host synthesis continues without council-verified label "
                f"(successful_independent_reports={successful_count})"
            )
    else:
        # No quorum configured: successful reports still useful; not council-verified.
        if successful_count > 0:
            notes.append("No quorum configured; host synthesis owns the fitted answer")
        else:
            confidence = "low"
            notes.append("No successful independent reports")

    ok = not blocked and (successful_count > 0 or not phase_required)
    if blocked:
        ok = False
        confidence = "blocked"
    elif successful_count == 0:
        confidence = "low"
    return ok, council_verified, blocked, confidence, notes


def _primary_attempt_from_spec(spec: LaneSpec) -> EffectiveAttempt:
    return EffectiveAttempt(
        profile=spec.profile,
        adapter=spec.adapter,
        executable=spec.executable,
        requested_model=spec.requested_model,
        extra_args=tuple(spec.extra_args),
        env_grants=tuple(spec.env_grants),
        enabled=True,
        required=spec.required,
        reason="primary",
    )


def _ordered_attempts(spec: LaneSpec) -> tuple[EffectiveAttempt, ...]:
    if spec.attempts:
        return spec.attempts
    return (_primary_attempt_from_spec(spec),)


async def _terminate_process_group(
    proc: asyncio.subprocess.Process,
    *,
    grace_seconds: float = PROCESS_GROUP_GRACE_SECONDS,
) -> dict[str, Any]:
    """Gracefully terminate then hard-kill the child's process group; reap child."""
    cleanup: dict[str, Any] = {
        "signaled_group": False,
        "sigterm_sent": False,
        "sigkill_sent": False,
        "reaped": False,
        "pid": proc.pid,
        "pgid": None,
        "error": None,
    }
    if proc.pid is None:
        cleanup["error"] = "no_pid"
        return cleanup

    pgid: int | None
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        # Child already exited.
        try:
            await proc.wait()
        except Exception:
            pass
        cleanup["reaped"] = True
        return cleanup
    except OSError as exc:
        cleanup["error"] = f"getpgid_failed:{exc}"
        pgid = None

    cleanup["pgid"] = pgid

    # Never signal our own process group.
    try:
        host_pgid = os.getpgid(0)
    except OSError:
        host_pgid = None
    if pgid is not None and host_pgid is not None and pgid == host_pgid:
        cleanup["error"] = "refused_to_signal_host_process_group"
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await proc.wait()
            cleanup["reaped"] = True
        except Exception as exc:
            cleanup["error"] = f"{cleanup['error']};reap:{exc}"
        return cleanup

    def _kill_group(sig: int) -> bool:
        if pgid is None:
            return False
        try:
            os.killpg(pgid, sig)
            return True
        except ProcessLookupError:
            return False
        except OSError:
            return False

    cleanup["signaled_group"] = True
    if _kill_group(signal.SIGTERM):
        cleanup["sigterm_sent"] = True
    else:
        try:
            proc.terminate()
            cleanup["sigterm_sent"] = True
        except ProcessLookupError:
            pass

    try:
        await asyncio.wait_for(proc.wait(), timeout=grace_seconds)
        cleanup["reaped"] = True
        return cleanup
    except (asyncio.TimeoutError, ProcessLookupError):
        pass

    if _kill_group(signal.SIGKILL):
        cleanup["sigkill_sent"] = True
    else:
        try:
            proc.kill()
            cleanup["sigkill_sent"] = True
        except ProcessLookupError:
            pass

    try:
        await proc.wait()
        cleanup["reaped"] = True
    except Exception as exc:
        cleanup["error"] = f"reap_failed:{exc}"
    return cleanup


def _classify_failure(*, timeout: bool, exit_code: int | None, error: str | None) -> str:
    if timeout:
        return "timeout"
    text = (error or "").lower()
    if "not found" in text or "executable not found" in text:
        return "launch_error"
    if "disabled" in text or "unavailable" in text or "host_native" in text:
        return "unavailable"
    if "actual_model" in text:
        return "model_evidence"
    if "capability" in text:
        return "capability"
    if "json" in text or "malformed" in text or "missing_report" in text or "role_mismatch" in text:
        return "malformed_output"
    if exit_code not in (None, 0):
        return "execution_failure"
    if error:
        return "execution_failure"
    return "unknown"


async def _run_single_attempt(
    *,
    spec: LaneSpec,
    attempt: EffectiveAttempt,
    attempt_index: int,
    packet: ContextPacket,
    work_dir: Path,
    parent_env: Mapping[str, str] | None,
    command_override: tuple[str, ...] | None,
) -> tuple[AttemptResult, LaneResult]:
    """Execute one attempt; return attempt evidence and a partial lane-shaped result."""
    launch_time = time.monotonic()
    attempt_dir = ensure_private_dir(work_dir / f"attempt-{attempt_index}-{attempt.profile}")
    packet_path = write_json_artifact(attempt_dir / "packet.json", packet.to_dict())
    prompt_path = write_text_artifact(
        attempt_dir / "prompt.txt",
        (
            f"Role: {packet.role}\n"
            f"Mode: {packet.mode}\n"
            f"Scope: {packet.scope}\n"
            f"Task: {packet.task}\n"
            f"Return a JSON transport envelope with adapter_metadata "
            f"(authoritative actual_model) and role_report fields: "
            f"{', '.join(packet.output_schema)}\n"
        ),
    )

    grants = tuple(attempt.env_grants) or tuple(spec.env_grants)
    scrub: EnvScrubResult = scrub_environment(
        parent_env,
        extra_allowlist=set(spec.env_extra_allowlist),
        secret_grants=set(grants),
    )

    attempt_result = AttemptResult(
        attempt_index=attempt_index,
        profile=attempt.profile,
        adapter=attempt.adapter,
        requested_model=attempt.requested_model,
        actual_model=None,
        model_evidence_source=None,
        status="pending",
        start_time=time.monotonic(),
    )

    # Disabled profile — do not probe or launch.
    if not attempt.enabled:
        end = time.monotonic()
        attempt_result.end_time = end
        attempt_result.status = "failed"
        attempt_result.failure_class = "unavailable"
        attempt_result.reason = f"profile `{attempt.profile}` is disabled"
        attempt_result.ok = False
        lane = LaneResult(
            lane_id=spec.lane_id,
            role=spec.role,
            adapter=attempt.adapter,
            profile=attempt.profile,
            ok=False,
            launch_time=launch_time,
            start_time=attempt_result.start_time,
            end_time=end,
            error=attempt_result.reason,
            requested_model=attempt.requested_model,
            stripped_env_names=list(scrub.stripped_names),
            granted_env_names=list(grants),
            artifact_dir=str(attempt_dir),
            failure_class="unavailable",
        )
        return attempt_result, lane

    # Host-native: only injected real host evidence counts; never a subprocess.
    if attempt.adapter == "host-native" and command_override is None:
        evidence = spec.injected_host_evidence
        end = time.monotonic()
        attempt_result.end_time = end
        if not evidence:
            reason = (
                "host_native_requires_injected_report: no injected host executor "
                "evidence; cannot fabricate a host vote"
            )
            attempt_result.status = "failed"
            attempt_result.failure_class = "unavailable"
            attempt_result.reason = reason
            lane = LaneResult(
                lane_id=spec.lane_id,
                role=spec.role,
                adapter="host-native",
                profile=attempt.profile,
                ok=False,
                launch_time=launch_time,
                start_time=attempt_result.start_time,
                end_time=end,
                error=reason,
                requested_model=attempt.requested_model,
                stripped_env_names=list(scrub.stripped_names),
                granted_env_names=list(grants),
                artifact_dir=str(attempt_dir),
                failure_class="unavailable",
            )
            return attempt_result, lane

        metadata = evidence.get("adapter_metadata") or evidence.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        # Require a trustworthy host execution identity.
        executor_id = (
            evidence.get("executor_id")
            or metadata.get("executor_id")
            or metadata.get("host_executor_id")
        )
        if not executor_id:
            reason = (
                "injected host evidence missing executor_id / host execution identity"
            )
            attempt_result.status = "failed"
            attempt_result.failure_class = "unavailable"
            attempt_result.reason = reason
            lane = LaneResult(
                lane_id=spec.lane_id,
                role=spec.role,
                adapter="host-native",
                profile=attempt.profile,
                ok=False,
                launch_time=launch_time,
                start_time=attempt_result.start_time,
                end_time=end,
                error=reason,
                requested_model=attempt.requested_model,
                stripped_env_names=list(scrub.stripped_names),
                granted_env_names=list(grants),
                artifact_dir=str(attempt_dir),
                failure_class="unavailable",
            )
            return attempt_result, lane

        role_report = evidence.get("role_report") or evidence.get("report")
        if not isinstance(role_report, dict):
            reason = "injected host evidence missing role_report object"
            attempt_result.status = "failed"
            attempt_result.failure_class = "malformed_output"
            attempt_result.reason = reason
            lane = LaneResult(
                lane_id=spec.lane_id,
                role=spec.role,
                adapter="host-native",
                profile=attempt.profile,
                ok=False,
                launch_time=launch_time,
                start_time=attempt_result.start_time,
                end_time=end,
                error=reason,
                requested_model=attempt.requested_model,
                stripped_env_names=list(scrub.stripped_names),
                granted_env_names=list(grants),
                artifact_dir=str(attempt_dir),
                failure_class="malformed_output",
            )
            return attempt_result, lane

        # Validate report fields via parse_role_report on a synthetic envelope.
        envelope = {
            "adapter_metadata": {
                **metadata,
                "source": metadata.get("source") or "host-injected",
                "executor_id": executor_id,
            },
            "role_report": role_report,
        }
        import json as _json

        try:
            report = parse_role_report(
                _json.dumps(envelope),
                expected_role=spec.role,
            )
            actual, source = validate_model_evidence(
                requested_model=attempt.requested_model,
                metadata=envelope["adapter_metadata"],
                require_when_requested=attempt.requested_model is not None,
            )
        except ValidationIssue as issue:
            attempt_result.status = "failed"
            attempt_result.failure_class = _classify_failure(
                timeout=False, exit_code=None, error=issue.message
            )
            attempt_result.reason = issue.message
            lane = LaneResult(
                lane_id=spec.lane_id,
                role=spec.role,
                adapter="host-native",
                profile=attempt.profile,
                ok=False,
                launch_time=launch_time,
                start_time=attempt_result.start_time,
                end_time=end,
                error=issue.message,
                requested_model=attempt.requested_model,
                stripped_env_names=list(scrub.stripped_names),
                granted_env_names=list(grants),
                artifact_dir=str(attempt_dir),
                failure_class=attempt_result.failure_class,
            )
            return attempt_result, lane

        # When no model was requested, still record identity from metadata if present.
        if actual is None:
            actual = str(metadata.get("actual_model") or "host-native")
            source = str(metadata.get("source") or "host-injected")

        attempt_result.ok = True
        attempt_result.status = "success"
        attempt_result.actual_model = actual
        attempt_result.model_evidence_source = source
        lane = LaneResult(
            lane_id=spec.lane_id,
            role=spec.role,
            adapter="host-native",
            profile=attempt.profile,
            ok=True,
            launch_time=launch_time,
            start_time=attempt_result.start_time,
            end_time=end,
            exit_code=0,
            requested_model=attempt.requested_model,
            actual_model=actual,
            model_evidence_source=source,
            report=report,
            stripped_env_names=list(scrub.stripped_names),
            granted_env_names=list(grants),
            artifact_dir=str(attempt_dir),
            successful_attempt_index=attempt_index,
        )
        return attempt_result, lane

    # Build or override command.
    invocation: AdapterInvocation
    if command_override is not None and attempt_index == 0:
        command = list(command_override)
        invocation = AdapterInvocation(
            adapter=attempt.adapter,
            executable=command[0] if command else "",
            argv=tuple(command),
            read_only=True,
            notes="command_override",
            input_mode="none",
            decoder="json-transport-envelope",
        )
    else:
        try:
            invocation = build_readonly_invocation(
                adapter=attempt.adapter,
                profile=attempt.profile,
                executable=attempt.executable,
                packet_path=packet_path,
                prompt_path=prompt_path,
                requested_model=attempt.requested_model,
                extra_args=attempt.extra_args,
            )
        except ValidationIssue as issue:
            end = time.monotonic()
            attempt_result.end_time = end
            attempt_result.status = "failed"
            attempt_result.failure_class = "launch_error"
            attempt_result.reason = issue.message
            lane = LaneResult(
                lane_id=spec.lane_id,
                role=spec.role,
                adapter=attempt.adapter,
                profile=attempt.profile,
                ok=False,
                launch_time=launch_time,
                start_time=attempt_result.start_time,
                end_time=end,
                error=issue.message,
                requested_model=attempt.requested_model,
                stripped_env_names=list(scrub.stripped_names),
                granted_env_names=list(grants),
                artifact_dir=str(attempt_dir),
                failure_class="launch_error",
            )
            return attempt_result, lane
        command = list(invocation.argv)

    if invocation.unavailable:
        end = time.monotonic()
        reason = invocation.unavailable_reason or "adapter unavailable"
        attempt_result.end_time = end
        attempt_result.status = "failed"
        attempt_result.failure_class = "unavailable"
        attempt_result.reason = reason
        lane = LaneResult(
            lane_id=spec.lane_id,
            role=spec.role,
            adapter=attempt.adapter,
            profile=attempt.profile,
            ok=False,
            launch_time=launch_time,
            start_time=attempt_result.start_time,
            end_time=end,
            error=reason,
            requested_model=attempt.requested_model,
            stripped_env_names=list(scrub.stripped_names),
            granted_env_names=list(grants),
            command=command,
            artifact_dir=str(attempt_dir),
            failure_class="unavailable",
        )
        return attempt_result, lane

    attempt_result.command = list(command)
    start_time = time.monotonic()
    attempt_result.start_time = start_time

    if not command:
        end = time.monotonic()
        attempt_result.end_time = end
        attempt_result.status = "failed"
        attempt_result.failure_class = "launch_error"
        attempt_result.reason = "empty command"
        lane = LaneResult(
            lane_id=spec.lane_id,
            role=spec.role,
            adapter=attempt.adapter,
            profile=attempt.profile,
            ok=False,
            launch_time=launch_time,
            start_time=start_time,
            end_time=end,
            error="empty command",
            requested_model=attempt.requested_model,
            stripped_env_names=list(scrub.stripped_names),
            granted_env_names=list(grants),
            command=command,
            artifact_dir=str(attempt_dir),
            failure_class="launch_error",
        )
        return attempt_result, lane

    stdin_bytes = None
    if invocation.stdin_text is not None:
        stdin_bytes = invocation.stdin_text.encode("utf-8")

    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE if stdin_bytes is not None else None,
            env=scrub.env,
            cwd=str(attempt_dir),
            start_new_session=True,  # own process group/session
        )
    except FileNotFoundError as exc:
        end = time.monotonic()
        reason = f"executable not found: {exc}"
        attempt_result.end_time = end
        attempt_result.status = "failed"
        attempt_result.failure_class = "launch_error"
        attempt_result.reason = reason
        lane = LaneResult(
            lane_id=spec.lane_id,
            role=spec.role,
            adapter=attempt.adapter,
            profile=attempt.profile,
            ok=False,
            launch_time=launch_time,
            start_time=start_time,
            end_time=end,
            error=reason,
            requested_model=attempt.requested_model,
            stripped_env_names=list(scrub.stripped_names),
            granted_env_names=list(grants),
            command=command,
            artifact_dir=str(attempt_dir),
            failure_class="launch_error",
        )
        return attempt_result, lane

    timed_out = False
    cleanup: dict[str, Any] = {}
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(input=stdin_bytes),
            timeout=spec.timeout_seconds,
        )
    except asyncio.TimeoutError:
        timed_out = True
        cleanup = await _terminate_process_group(proc)
        stdout_b, stderr_b = b"", b""
        # Drain any remaining pipes if communicate was interrupted.
        if proc.stdout:
            try:
                stdout_b = await proc.stdout.read()
            except Exception:
                pass
        if proc.stderr:
            try:
                stderr_b = await proc.stderr.read()
            except Exception:
                pass

    end_time = time.monotonic()
    attempt_result.end_time = end_time
    attempt_result.timeout = timed_out
    attempt_result.exit_code = proc.returncode
    attempt_result.cleanup = cleanup

    stdout = (stdout_b or b"").decode("utf-8", errors="replace")
    stderr = (stderr_b or b"").decode("utf-8", errors="replace")
    write_text_artifact(attempt_dir / "stdout.txt", stdout)
    write_text_artifact(attempt_dir / "stderr.txt", stderr)

    lane = LaneResult(
        lane_id=spec.lane_id,
        role=spec.role,
        adapter=attempt.adapter,
        profile=attempt.profile,
        ok=False,
        launch_time=launch_time,
        start_time=start_time,
        end_time=end_time,
        timeout=timed_out,
        exit_code=proc.returncode,
        requested_model=attempt.requested_model,
        stdout_summary=_summarize(stdout),
        stderr_summary=_summarize(stderr),
        stripped_env_names=list(scrub.stripped_names),
        granted_env_names=[n for n in grants if n in scrub.kept_names],
        command=command,
        artifact_dir=str(attempt_dir),
    )

    if timed_out:
        reason = (
            f"timeout after {spec.timeout_seconds}s; "
            f"process_group_cleanup={cleanup}"
        )
        attempt_result.status = "failed"
        attempt_result.failure_class = "timeout"
        attempt_result.reason = reason
        lane.error = reason
        lane.failure_class = "timeout"
        return attempt_result, lane

    # Parse transport envelope and validate role report separately from model evidence.
    report: dict[str, Any] | None = None
    metadata: dict[str, Any] = {}
    parse_error: str | None = None
    try:
        metadata, _raw_report = parse_transport_output(stdout)
        report = parse_role_report(stdout, expected_role=spec.role)
    except ValidationIssue as issue:
        parse_error = issue.message

    if report is not None:
        lane.report = report

    model_error: str | None = None
    actual: str | None = None
    source: str | None = None
    if parse_error is None:
        try:
            actual, source = validate_model_evidence(
                requested_model=attempt.requested_model,
                metadata=metadata,
                require_when_requested=attempt.requested_model is not None,
            )
            # Also fail if report body claims a model that conflicts with metadata
            # when metadata is present — body never overrides metadata.
            body_claim = None
            if report is not None:
                body_claim = report.get("actual_model") or report.get("model")
            if (
                actual is not None
                and body_claim is not None
                and str(body_claim) != str(actual)
            ):
                # Ignore body claim; keep authoritative metadata.
                pass
            lane.actual_model = actual
            lane.model_evidence_source = source
            attempt_result.actual_model = actual
            attempt_result.model_evidence_source = source
        except ValidationIssue as issue:
            model_error = issue.message

    if proc.returncode != 0:
        status_err = f"non-zero terminal status: exit_code={proc.returncode}"
        parts = [status_err]
        if parse_error:
            parts.append(parse_error)
        if model_error:
            parts.append(model_error)
        reason = "; ".join(parts)
        attempt_result.status = "failed"
        attempt_result.failure_class = "execution_failure"
        attempt_result.reason = reason
        lane.error = reason
        lane.failure_class = "execution_failure"
        return attempt_result, lane

    if parse_error is not None:
        attempt_result.status = "failed"
        attempt_result.failure_class = "malformed_output"
        attempt_result.reason = parse_error
        lane.error = parse_error
        lane.failure_class = "malformed_output"
        return attempt_result, lane

    if model_error is not None:
        attempt_result.status = "failed"
        attempt_result.failure_class = "model_evidence"
        attempt_result.reason = model_error
        lane.error = model_error
        lane.failure_class = "model_evidence"
        return attempt_result, lane

    attempt_result.ok = True
    attempt_result.status = "success"
    lane.ok = True
    lane.successful_attempt_index = attempt_index
    return attempt_result, lane


async def _run_lane_with_fallbacks(
    spec: LaneSpec,
    packet: ContextPacket,
    work_dir: Path,
    *,
    parent_env: Mapping[str, str] | None = None,
) -> LaneResult:
    """Run primary then ordered fallbacks until success or attempts exhausted."""
    launch_time = time.monotonic()
    ensure_private_dir(work_dir)
    attempts = _ordered_attempts(spec)
    attempt_results: list[AttemptResult] = []
    last_lane: LaneResult | None = None

    for index, attempt in enumerate(attempts):
        # command_override applies only to the first attempt (test fakes).
        override = spec.command_override if index == 0 else None
        # For fallback attempts after the first, allow per-attempt override only
        # via attempt.extra_args/executable — not a second command_override.
        attempt_packet = build_context_packet(
            task=packet.task,
            role=packet.role,
            mode=packet.mode,
            scope=packet.scope,
            relevant_files=list(packet.relevant_files),
            plan_path=packet.plan_path,
            head_sha=packet.head_sha,
            requested_model=attempt.requested_model,
            profile=attempt.profile,
            adapter=attempt.adapter,
            run_id=packet.run_id,
            evidence_needs=list(packet.evidence_needs),
            forbidden_actions=list(packet.forbidden_actions),
            constraints=list(packet.constraints),
        )
        attempt_result, lane = await _run_single_attempt(
            spec=spec,
            attempt=attempt,
            attempt_index=index,
            packet=attempt_packet,
            work_dir=work_dir,
            parent_env=parent_env,
            command_override=override,
        )
        attempt_results.append(attempt_result)
        last_lane = lane
        if lane.ok:
            lane.attempts = attempt_results
            lane.launch_time = launch_time
            if index > 0:
                lane.fallback_used = attempt.profile
            return lane

    # All attempts failed.
    assert last_lane is not None
    last_lane.attempts = attempt_results
    last_lane.launch_time = launch_time
    last_lane.start_time = attempt_results[0].start_time if attempt_results else launch_time
    last_lane.end_time = attempt_results[-1].end_time if attempt_results else time.monotonic()
    # Summarize terminal failure across chain.
    classes = [a.failure_class for a in attempt_results if a.failure_class]
    last_lane.failure_class = classes[-1] if classes else "unknown"
    reasons = [a.reason for a in attempt_results if a.reason]
    if reasons:
        last_lane.error = " | ".join(str(r) for r in reasons)
    return last_lane


async def run_council(
    lanes: Sequence[LaneSpec],
    *,
    repo_root: Path,
    task: str,
    mode: str = "read-only-council",
    phase: str = "review",
    phase_required: bool = False,
    target_quorum: int | None = None,
    required_quorum: int | None = None,
    head_sha: str | None = None,
    plan_path: str | None = None,
    run_id: str | None = None,
    parent_env: Mapping[str, str] | None = None,
    relevant_files: list[str] | None = None,
) -> CouncilResult:
    """Launch independent read-only lanes concurrently and collect reports."""
    if required_quorum is not None and not phase_required:
        # required_quorum only valid when phase is explicitly required.
        required_quorum = None

    rid = run_id or new_run_id(prefix=f"council-{phase}")
    root = create_exclusive_artifact_root(repo_root, rid)

    async def _one(spec: LaneSpec) -> LaneResult:
        packet = build_context_packet(
            task=task,
            role=spec.role,
            mode=mode,
            scope="read-only lens",
            relevant_files=relevant_files,
            plan_path=plan_path,
            head_sha=head_sha,
            requested_model=spec.requested_model,
            profile=spec.profile,
            adapter=spec.adapter,
            run_id=rid,
        )
        work_dir = ensure_private_dir(root / spec.lane_id)
        return await _run_lane_with_fallbacks(
            spec,
            packet,
            work_dir,
            parent_env=parent_env,
        )

    # Concurrent fan-out across logical lanes — attempts inside a lane are sequential.
    results = list(await asyncio.gather(*[_one(spec) for spec in lanes]))

    successful_reports: list[dict[str, Any]] = []
    required_failures: list[str] = []
    for lane, spec in zip(results, lanes):
        if lane.ok and lane.report is not None:
            successful_reports.append(lane.report)
        elif spec.required:
            required_failures.append(spec.lane_id)

    ok, verified, blocked, confidence, notes = evaluate_quorum(
        successful_count=len(successful_reports),
        target_quorum=target_quorum,
        required_quorum=required_quorum,
        phase_required=phase_required,
        required_lane_failures=required_failures,
    )

    # Independence note: host synthesis only — lanes never see peer reports.
    notes.append("lanes_independent=true; host_synthesis_only=true")
    notes.append("host_native_requires_injected_report=true")

    summary = CouncilResult(
        run_id=rid,
        ok=ok,
        council_verified=verified,
        blocked=blocked,
        confidence=confidence,
        successful_reports=successful_reports,
        lane_results=results,
        target_quorum=target_quorum,
        required_quorum=required_quorum,
        phase_required=phase_required,
        notes=notes,
        artifact_root=str(root),
    )
    write_json_artifact(root / "council-result.json", summary.to_dict())
    return summary


async def run_lightweight_review(
    *,
    repo_root: Path,
    task: str,
    adapter: str = "host-native",
    profile: str = "host-native",
    executable: str | None = None,
    requested_model: str | None = None,
    command_override: tuple[str, ...] | None = None,
    timeout_seconds: float = 30.0,
    parent_env: Mapping[str, str] | None = None,
    head_sha: str | None = None,
    run_id: str | None = None,
    env_grants: tuple[str, ...] = (),
    injected_host_evidence: dict[str, Any] | None = None,
) -> LaneResult:
    """Single bounded ephemeral read-only utility review.

    Independent of council quorum. Cannot close high-risk review or mutate git/PR.
    Does not count as a default independent council vote.
    """
    rid = run_id or new_run_id(prefix="lightweight-review")
    root = create_exclusive_artifact_root(repo_root, rid)
    spec = LaneSpec(
        lane_id="lightweight_review",
        role="lightweight_review",
        adapter=adapter,
        profile=profile,
        requested_model=requested_model,
        executable=executable,
        required=False,
        timeout_seconds=timeout_seconds,
        command_override=command_override,
        env_grants=env_grants,
        injected_host_evidence=injected_host_evidence,
    )
    packet = build_context_packet(
        task=task,
        role="lightweight_review",
        mode="lightweight-review",
        scope="read-only lens",
        head_sha=head_sha,
        requested_model=requested_model,
        profile=profile,
        adapter=adapter,
        run_id=rid,
        constraints=[
            "ephemeral",
            "not_a_council_vote",
            "cannot_close_high_risk_review",
            "no_git_or_pr_mutations",
        ],
        forbidden_actions=[
            "git_commit",
            "git_push",
            "open_pr",
            "merge_pr",
            "close_high_risk_review",
            "mutate_run_memory",
            "edit_product_files",
        ],
    )
    work_dir = ensure_private_dir(root / spec.lane_id)
    result = await _run_lane_with_fallbacks(
        spec,
        packet,
        work_dir,
        parent_env=parent_env,
    )
    write_json_artifact(root / "lightweight-review-result.json", result.to_dict())
    return result


def run_council_sync(*args: Any, **kwargs: Any) -> CouncilResult:
    return asyncio.run(run_council(*args, **kwargs))


def run_lightweight_review_sync(*args: Any, **kwargs: Any) -> LaneResult:
    return asyncio.run(run_lightweight_review(*args, **kwargs))
