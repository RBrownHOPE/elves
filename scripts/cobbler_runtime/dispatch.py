"""Parallel read-only council dispatch with quorum and ordered fallbacks.

Lanes launch concurrently. Inside each lane, primary then fallback attempts run
sequentially after every material failure class. Transport model evidence is
adapter-specific. Host synthesis remains the only fitted-answer step.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import signal
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence, Union

# Challenge packet -> evidence mapping (sync or async).
HostExecutor = Callable[[Mapping[str, Any]], Union[Mapping[str, Any], Awaitable[Mapping[str, Any]]]]

from .adapters import (
    ADAPTER_CONTRACT_PAIRS,
    AdapterInvocation,
    build_readonly_invocation,
    decode_adapter_output,
    default_decoder_for_adapter,
    validate_adapter_contract_pair,
    validate_extra_args,
    validate_role_report,
)
from .context import (
    ContextPacket,
    EnvScrubResult,
    build_context_packet,
    create_exclusive_artifact_root,
    ensure_private_dir,
    new_run_id,
    redact_structure,
    redact_text,
    resolve_contained_path,
    safe_path_component,
    scrub_environment,
    write_json_artifact,
    write_text_artifact,
)
from .schema import EffectiveAttempt, ValidationIssue


LaneRunner = Callable[["LaneSpec", ContextPacket, Path], Awaitable["LaneResult"]]

PROCESS_GROUP_GRACE_SECONDS = 0.5
PROCESS_GROUP_VERIFY_POLL_SECONDS = 0.05
PROCESS_GROUP_VERIFY_ATTEMPTS = 20


@dataclass(frozen=True)
class LaneSpec:
    """One independent read-only council lane."""

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
    command_override: tuple[str, ...] | None = None
    attempts: tuple[EffectiveAttempt, ...] = ()
    injected_host_evidence: dict[str, Any] | None = None
    # Trusted host executor callback: (challenge: dict) -> evidence dict.
    # Static injected_host_evidence alone cannot count as a verified vote.
    host_executor: HostExecutor | None = None
    # Trusted qualified capability names for this lane (fixture/host evidence).
    qualified_capabilities: tuple[str, ...] = ()
    # When True, lane grants come only from attempt.env_grants (no LaneSpec inherit).
    isolate_attempt_env_grants: bool = True
    # Exact external chat/session id for plan→review continuity (never latest/continue).
    session_id: str | None = None


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
    effective_contract: dict[str, Any] = field(default_factory=dict)
    process_launched: bool = False
    model_call_made: bool = False

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
    execution_id: str | None = None
    process_launched: bool = False
    # Explicit call/mutation truth (not inferred from path substrings).
    model_call_made: bool = False
    mutated_repo: bool = False

    @property
    def wall_seconds(self) -> float:
        return max(0.0, self.end_time - self.start_time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
    model_calls_made: bool = False
    mutated_repo: bool = False
    successful_execution_ids: list[str] = field(default_factory=list)

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
            "successful_count": len(self.successful_execution_ids),
            "model_calls_made": self.model_calls_made,
            "mutated_repo": self.mutated_repo,
            "successful_execution_ids": list(self.successful_execution_ids),
        }


def _summarize(
    text: str,
    *,
    exact_values: frozenset[str] | set[str] | None = None,
    limit: int = 400,
) -> str:
    redacted = redact_text(text or "", exact_values=exact_values).text
    compact = " ".join(redacted.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def validate_quorum_value(
    value: int | None,
    *,
    name: str,
    lane_count: int,
) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationIssue(
            "invalid_quorum",
            f"{name} must be a positive integer, not {type(value).__name__}",
        )
    if value <= 0:
        raise ValidationIssue(
            "invalid_quorum",
            f"{name} must be a positive integer (got {value})",
        )
    if lane_count >= 0 and value > max(lane_count, 0):
        raise ValidationIssue(
            "invalid_quorum",
            f"{name}={value} exceeds distinct eligible lane count {lane_count}",
        )
    return value


def evaluate_quorum(
    *,
    successful_count: int,
    target_quorum: int | None,
    required_quorum: int | None,
    phase_required: bool,
    required_lane_failures: Sequence[str] = (),
    lane_count: int | None = None,
) -> tuple[bool, bool, bool, str, list[str]]:
    """Return (ok, council_verified, blocked, confidence, notes)."""
    notes: list[str] = []
    blocked = False
    council_verified = False
    confidence = "high"

    if required_lane_failures:
        blocked = True
        notes.append("Required lane failure(s): " + ", ".join(required_lane_failures))

    # Zero reports can never verify a council.
    if successful_count <= 0:
        council_verified = False

    if phase_required and required_quorum is not None:
        if successful_count < required_quorum or successful_count <= 0:
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
        if successful_count >= target_quorum and successful_count > 0:
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


def _binding_digest(*, run_id: str, lane_id: str, role: str, task: str) -> str:
    payload = f"{run_id}|{lane_id}|{role}|{task}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _pgid_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but not signalable as us — treat as still present.
        return True
    except OSError:
        return False


async def _terminate_process_group(
    proc: asyncio.subprocess.Process,
    *,
    grace_seconds: float = PROCESS_GROUP_GRACE_SECONDS,
    known_pgid: int | None = None,
) -> dict[str, Any]:
    """Terminate entire process group; verify absence before success."""
    cleanup: dict[str, Any] = {
        "signaled_group": False,
        "sigterm_sent": False,
        "sigkill_sent": False,
        "reaped": False,
        "group_absent": False,
        "pid": proc.pid,
        "pgid": known_pgid,
        "error": None,
    }
    pgid = known_pgid
    if pgid is None and proc.pid is not None:
        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            try:
                await proc.wait()
            except Exception:
                pass
            cleanup["reaped"] = True
            # Leader gone — still try known_pgid path below if provided.
            pgid = None
        except OSError as exc:
            cleanup["error"] = f"getpgid_failed:{exc}"
            pgid = None
    elif proc.pid is None and pgid is None:
        cleanup["error"] = "no_pid"
        cleanup["group_absent"] = True
        return cleanup

    cleanup["pgid"] = pgid
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
    except (asyncio.TimeoutError, ProcessLookupError):
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

    # Verify group members are gone — do not succeed while descendants live.
    if pgid is not None:
        for _ in range(PROCESS_GROUP_VERIFY_ATTEMPTS):
            if not _pgid_alive(pgid):
                cleanup["group_absent"] = True
                break
            # Residual members: escalate hard kill.
            _kill_group(signal.SIGKILL)
            cleanup["sigkill_sent"] = True
            await asyncio.sleep(PROCESS_GROUP_VERIFY_POLL_SECONDS)
        if not cleanup["group_absent"]:
            cleanup["error"] = (
                (cleanup.get("error") or "")
                + f";process_group_still_alive:pgid={pgid}"
            ).lstrip(";")
    else:
        cleanup["group_absent"] = cleanup.get("reaped", False)
    return cleanup


def _classify_failure(*, timeout: bool, exit_code: int | None, error: str | None) -> str:
    if timeout:
        return "timeout"
    text = (error or "").lower()
    if (
        "unsafe_extra_args" in text
        or "duplicate_extra_args" in text
        or "may not override reserved" in text
        or "duplicate extra_args" in text
    ):
        return "unsafe_arguments"
    if "capability" in text:
        return "capability"
    if "not found" in text or "executable not found" in text or "launch" in text:
        return "launch_error"
    if "disabled" in text or "unavailable" in text or "host_native" in text:
        return "unavailable"
    if "actual_model" in text or "untrusted_model" in text:
        return "model_evidence"
    if (
        "json" in text
        or "malformed" in text
        or "missing_report" in text
        or "role_mismatch" in text
        or "invalid_verdict" in text
        or "invalid_confidence" in text
        or "invalid_report" in text
    ):
        return "malformed_output"
    if exit_code not in (None, 0):
        return "execution_failure"
    if error:
        return "execution_failure"
    return "unknown"


def _attempt_env_grants(spec: LaneSpec, attempt: EffectiveAttempt) -> tuple[str, ...]:
    """Each attempt uses only its own grants; empty means no secrets (no inherit)."""
    if attempt.env_grants:
        return tuple(attempt.env_grants)
    # Primary-only convenience when attempts not expanded: use LaneSpec grants
    # only for the synthetic primary when attempts tuple is empty.
    if not spec.attempts and attempt.reason == "primary":
        return tuple(spec.env_grants)
    return ()


def _check_capabilities(
    attempt: EffectiveAttempt,
    *,
    lane_qualified: tuple[str, ...] = (),
) -> tuple[str | None, list[str], list[str]]:
    """Compare required capabilities to a trusted qualified snapshot.

    Returns (error_or_None, required_list, missing_list). Config preference text
    cannot self-certify qualification; only attempt.qualified_capabilities and
    lane_qualified snapshots count.
    """
    required = [name for name in (attempt.capabilities or ()) if name]
    if not required:
        return None, [], []
    if attempt.adapter == "host-native":
        return None, required, []
    qualified = set(attempt.qualified_capabilities or ()) | set(lane_qualified or ())
    missing = [name for name in required if name not in qualified]
    if missing:
        return (
            "capability_mismatch: required capabilities not qualified for attempt: "
            + ", ".join(missing),
            required,
            missing,
        )
    return None, required, []


class _HostEvidenceLedger:
    """Issue per-lane challenges and bind real host executor invocations."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._used_execution_ids: set[str] = set()
        self._lane_tokens: dict[str, str] = {}
        self._lane_execution_ids: dict[str, str] = {}
        self._invoked_lanes: set[str] = set()

    def issue_token(self, lane_id: str) -> str:
        token = f"host-exec-{uuid.uuid4().hex}"
        self._lane_tokens[lane_id] = token
        self._lane_execution_ids[lane_id] = f"host-exec-id-{uuid.uuid4().hex}"
        return token

    def token_for(self, lane_id: str) -> str | None:
        return self._lane_tokens.get(lane_id)

    def execution_id_for(self, lane_id: str) -> str | None:
        return self._lane_execution_ids.get(lane_id)

    def challenge_packet(
        self,
        *,
        lane_id: str,
        role: str,
        task: str,
        packet: Mapping[str, Any],
    ) -> dict[str, Any]:
        token = self._lane_tokens.get(lane_id)
        execution_id = self._lane_execution_ids.get(lane_id)
        if not token or not execution_id:
            raise ValidationIssue(
                "host_challenge_missing",
                f"No host challenge issued for lane `{lane_id}`",
            )
        binding = _binding_digest(
            run_id=self.run_id, lane_id=lane_id, role=role, task=task
        )
        return {
            "run_id": self.run_id,
            "lane_id": lane_id,
            "role": role,
            "task": task,
            "challenge_token": token,
            "execution_id": execution_id,
            "binding": binding,
            "packet": dict(packet),
        }

    def bind_executor_result(
        self,
        *,
        lane_id: str,
        role: str,
        task: str,
        challenge: Mapping[str, Any],
        evidence: Mapping[str, Any],
    ) -> tuple[dict[str, Any], str]:
        if lane_id in self._invoked_lanes:
            raise ValidationIssue(
                "host_executor_replay",
                f"host executor already invoked for lane `{lane_id}`",
            )
        issued_token = self._lane_tokens.get(lane_id)
        issued_exec = self._lane_execution_ids.get(lane_id)
        token = str(challenge.get("challenge_token") or "").strip()
        if not issued_token or token != issued_token:
            raise ValidationIssue(
                "host_evidence_challenge_mismatch",
                "host executor challenge_token does not match issued token",
            )
        if not evidence:
            raise ValidationIssue(
                "host_evidence_missing",
                "host executor returned empty evidence",
            )
        # Runtime-owned identity — ignore caller-forged execution_id.
        execution_id = issued_exec or f"host-exec-id-{uuid.uuid4().hex}"
        if execution_id in self._used_execution_ids:
            raise ValidationIssue(
                "host_evidence_replay",
                f"execution_id `{execution_id}` already consumed in this run",
            )
        expected_binding = _binding_digest(
            run_id=self.run_id, lane_id=lane_id, role=role, task=task
        )
        if str(challenge.get("binding") or "") != expected_binding:
            raise ValidationIssue(
                "host_evidence_binding_mismatch",
                "host executor challenge binding mismatch",
            )
        executor_id = str(
            evidence.get("executor_id")
            or (evidence.get("adapter_metadata") or {}).get("executor_id")
            or ""
        ).strip()
        if not executor_id:
            raise ValidationIssue(
                "host_evidence_incomplete",
                "host executor evidence requires executor_id",
            )
        bound = dict(evidence)
        bound["execution_id"] = execution_id
        bound["challenge_token"] = token
        bound["bound_lane_id"] = lane_id
        bound["bound_run_id"] = self.run_id
        bound["binding"] = expected_binding
        bound["executor_id"] = executor_id
        self._used_execution_ids.add(execution_id)
        self._invoked_lanes.add(lane_id)
        return bound, execution_id


async def _run_single_attempt(
    *,
    spec: LaneSpec,
    attempt: EffectiveAttempt,
    attempt_index: int,
    packet: ContextPacket,
    work_dir: Path,
    parent_env: Mapping[str, str] | None,
    command_override: tuple[str, ...] | None,
    repo_root: Path,
    host_ledger: _HostEvidenceLedger | None,
    task: str,
) -> tuple[AttemptResult, LaneResult]:
    """Thin coordinator over transport/native/subprocess/artifact/result components."""
    from .dispatch_attempt import (  # noqa: PLC0415
        build_effective_contract,
        prepare_transport,
        record_command_digests,
        write_attempt_artifacts,
    )

    launch_time = time.monotonic()
    attempt_dir = resolve_contained_path(
        work_dir,
        f"attempt-{attempt_index}-{safe_path_component(attempt.profile, field='profile')}",
    )
    ensure_private_dir(attempt_dir)

    # Build scrub/exact redaction set BEFORE any packet/prompt/artifact write.
    grants = _attempt_env_grants(spec, attempt)
    transport = prepare_transport(
        parent_env=parent_env,
        env_extra_allowlist=tuple(spec.env_extra_allowlist),
        grants=tuple(grants),
    )
    scrub = transport.scrub
    exact_secret_values = transport.exact_secret_values

    raw_packet_dict = packet.to_dict()
    redacted_task = redact_text(task, exact_values=exact_secret_values).text
    packet_dict = redact_structure(raw_packet_dict, exact_values=exact_secret_values)
    if isinstance(packet_dict, dict):
        packet_dict["task"] = redacted_task
    packet_path, prompt_path = write_attempt_artifacts(
        attempt_dir,
        packet_dict=packet_dict,
        redacted_task=redacted_task,
        prompt_body=None,
        write_json_artifact=write_json_artifact,
        write_text_artifact=write_text_artifact,
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
        effective_contract=build_effective_contract(
            attempt,
            grants=tuple(grants),
            repo_root=repo_root,
            exact_secret_values=exact_secret_values,
            qualified_capabilities=tuple(spec.qualified_capabilities or ()),
        ),
    )

    def _fail(
        *,
        reason: str,
        failure_class: str,
        command: list[str] | None = None,
        process_launched: bool = False,
        exit_code: int | None = None,
        timeout: bool = False,
        cleanup: dict[str, Any] | None = None,
        stdout: str = "",
        stderr: str = "",
        model_call_made: bool | None = None,
    ) -> tuple[AttemptResult, LaneResult]:
        end = time.monotonic()
        red_reason = redact_text(reason, exact_values=exact_secret_values).text
        call_made = (
            model_call_made
            if model_call_made is not None
            else (process_launched and attempt.adapter != "host-native")
        )
        attempt_result.end_time = end
        attempt_result.status = "failed"
        attempt_result.failure_class = failure_class
        attempt_result.reason = red_reason
        attempt_result.ok = False
        attempt_result.timeout = timeout
        attempt_result.exit_code = exit_code
        attempt_result.command = list(command or [])
        attempt_result.cleanup = dict(cleanup or {})
        attempt_result.process_launched = process_launched
        attempt_result.model_call_made = call_made
        lane = LaneResult(
            lane_id=spec.lane_id,
            role=spec.role,
            adapter=attempt.adapter,
            profile=attempt.profile,
            ok=False,
            launch_time=launch_time,
            start_time=attempt_result.start_time,
            end_time=end,
            timeout=timeout,
            exit_code=exit_code,
            error=red_reason,
            requested_model=(
                redact_text(str(attempt.requested_model), exact_values=exact_secret_values).text
                if attempt.requested_model is not None
                else None
            ),
            stripped_env_names=list(scrub.stripped_names),
            granted_env_names=list(grants),
            command=list(command or []),
            artifact_dir=str(attempt_dir),
            failure_class=failure_class,
            stdout_summary=_summarize(stdout, exact_values=exact_secret_values),
            stderr_summary=_summarize(stderr, exact_values=exact_secret_values),
            process_launched=process_launched,
            model_call_made=call_made,
            mutated_repo=False,
        )
        attempt_result.requested_model = (
            redact_text(str(attempt.requested_model), exact_values=exact_secret_values).text
            if attempt.requested_model is not None
            else None
        )
        return attempt_result, lane

    if not attempt.enabled:
        return _fail(
            reason=f"profile `{attempt.profile}` is disabled",
            failure_class="unavailable",
        )

    cap_err, cap_required, cap_missing = _check_capabilities(
        attempt, lane_qualified=spec.qualified_capabilities
    )
    attempt_result.effective_contract["required_capabilities"] = list(cap_required)
    attempt_result.effective_contract["missing_capabilities"] = list(cap_missing)
    if cap_err:
        return _fail(reason=cap_err, failure_class="capability")

    # Host-native path: trusted host_executor callback only (no static forgeries).
    if attempt.adapter == "host-native" and command_override is None:
        if host_ledger is None:
            return _fail(
                reason="host ledger unavailable for host-native lane",
                failure_class="unavailable",
            )
        if spec.host_executor is None:
            return _fail(
                reason=(
                    "host_native_requires_executor: static injected evidence cannot "
                    "count as a verified host vote without a host_executor callback"
                ),
                failure_class="unavailable",
            )
        host_call_made = False
        try:
            challenge = host_ledger.challenge_packet(
                lane_id=spec.lane_id,
                role=spec.role,
                task=task,
                packet=packet_dict if isinstance(packet_dict, dict) else {},
            )
            raw_evidence = spec.host_executor(challenge)
            if inspect.isawaitable(raw_evidence):
                raw_evidence = await raw_evidence
            # Invocation itself is a model/agent call by contract.
            host_call_made = True
            attempt_result.model_call_made = True
            if not isinstance(raw_evidence, Mapping):
                raise ValidationIssue(
                    "host_executor_invalid",
                    "host_executor must return a mapping evidence object",
                )
            evidence, execution_id = host_ledger.bind_executor_result(
                lane_id=spec.lane_id,
                role=spec.role,
                task=task,
                challenge=challenge,
                evidence=raw_evidence,
            )
            role_report = evidence.get("role_report") or evidence.get("report")
            if not isinstance(role_report, dict):
                raise ValidationIssue(
                    "host_evidence_incomplete",
                    "host executor evidence missing role_report object",
                )
            report = validate_role_report(role_report, expected_role=spec.role)
            report = redact_structure(report, exact_values=exact_secret_values)
            metadata = evidence.get("adapter_metadata") or {}
            actual = None
            source = "host-executor"
            if isinstance(metadata, dict) and metadata.get("actual_model"):
                actual = str(metadata["actual_model"])
            elif evidence.get("actual_model"):
                actual = str(evidence["actual_model"])
            if attempt.requested_model is not None:
                if actual is None:
                    raise ValidationIssue(
                        "actual_model_missing",
                        "host executor evidence missing actual_model for exact route",
                    )
                if str(actual) != str(attempt.requested_model):
                    raise ValidationIssue(
                        "actual_model_mismatch",
                        f"host actual_model `{actual}` != `{attempt.requested_model}`",
                    )
            if actual is None:
                actual = "host-native"
            actual = redact_text(str(actual), exact_values=exact_secret_values).text
        except ValidationIssue as issue:
            fail = _fail(
                reason=issue.message,
                failure_class=_classify_failure(
                    timeout=False, exit_code=None, error=issue.message
                ),
            )
            if host_call_made:
                fail[0].model_call_made = True
                fail[1].model_call_made = True
            return fail
        except Exception as exc:  # noqa: BLE001 — convert host executor crashes
            fail = _fail(
                reason=f"host_executor_error: {type(exc).__name__}",
                failure_class="execution_failure",
            )
            # Callback was invoked (or failed mid-await after start) — still a call.
            fail[0].model_call_made = True
            fail[1].model_call_made = True
            return fail

        end = time.monotonic()
        attempt_result.end_time = end
        attempt_result.ok = True
        attempt_result.status = "success"
        attempt_result.actual_model = actual
        attempt_result.model_evidence_source = source
        attempt_result.model_call_made = True
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
            execution_id=execution_id,
            process_launched=False,
            model_call_made=True,
            mutated_repo=False,
        )
        return attempt_result, lane

    # Validate contracts/extra args before launch.
    defaults = ADAPTER_CONTRACT_PAIRS.get(
        attempt.adapter, ("json-stdio", "custom-json-envelope")
    )
    attempt_input_contract = (attempt.input_contract or defaults[0]).strip()
    attempt_output_contract = (attempt.output_contract or defaults[1]).strip()
    if attempt_output_contract == "json-role-report":
        attempt_output_contract = "custom-json-envelope"
    try:
        validate_extra_args(attempt.adapter, attempt.extra_args)
        # command_override is a test/fake path — skip built-in pair enforcement.
        if command_override is None:
            validate_adapter_contract_pair(
                attempt.adapter,
                input_contract=attempt_input_contract,
                output_contract=attempt_output_contract,
            )
    except ValidationIssue as issue:
        return _fail(
            reason=issue.message,
            failure_class=_classify_failure(timeout=False, exit_code=None, error=issue.message),
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
            cwd=str(repo_root),
        )
    else:
        try:
            # Prefer attempt-level session if present, else lane session (plan→review continuity).
            attempt_session = getattr(attempt, "session_id", None) or spec.session_id
            invocation = build_readonly_invocation(
                adapter=attempt.adapter,
                profile=attempt.profile,
                executable=attempt.executable,
                packet_path=packet_path,
                prompt_path=prompt_path,
                requested_model=attempt.requested_model,
                extra_args=attempt.extra_args,
                packet=packet_dict if isinstance(packet_dict, dict) else {},
                task=redacted_task,
                role=spec.role,
                input_contract=attempt_input_contract,
                output_contract=attempt_output_contract,
                repo_root=repo_root,
                session_id=attempt_session,
            )
        except ValidationIssue as issue:
            return _fail(
                reason=issue.message,
                failure_class=_classify_failure(timeout=False, exit_code=None, error=issue.message),
            )
        command = list(invocation.argv)

    # Persist only redacted prompt bodies.
    if invocation.prompt_file_body is not None:
        write_text_artifact(
            prompt_path,
            redact_text(invocation.prompt_file_body, exact_values=exact_secret_values).text,
        )
    elif not prompt_path.exists():
        write_text_artifact(prompt_path, redacted_task)

    # Keep raw argv only in local memory for launch; persist digest + redacted form.
    raw_command = list(command)
    redacted_command = record_command_digests(
        attempt_result.effective_contract,
        raw_command=raw_command,
        exact_secret_values=exact_secret_values,
        invocation=invocation,
    )

    if invocation.unavailable:
        return _fail(
            reason=invocation.unavailable_reason or "adapter unavailable",
            failure_class="unavailable",
            command=redacted_command,
        )

    if not raw_command:
        return _fail(reason="empty command", failure_class="launch_error")

    stdin_bytes = None
    if invocation.stdin_text is not None:
        # Child may need unredacted secrets in env only; stdin is redacted packet/task.
        stdin_bytes = redact_text(
            invocation.stdin_text, exact_values=exact_secret_values
        ).text.encode("utf-8")

    child_cwd = invocation.cwd or str(repo_root)
    try:
        proc = await asyncio.create_subprocess_exec(
            *raw_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE if stdin_bytes is not None else None,
            env=scrub.env,
            cwd=child_cwd,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        return _fail(
            reason=f"executable not found: {exc}",
            failure_class="launch_error",
            command=redacted_command,
        )
    except OSError as exc:
        return _fail(
            reason=f"launch error: {exc}",
            failure_class="launch_error",
            command=redacted_command,
        )

    process_launched = True
    # Capture PGID immediately after launch for all exit paths.
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
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(input=stdin_bytes),
            timeout=spec.timeout_seconds,
        )
    except asyncio.TimeoutError:
        timed_out = True
        cleanup = await _terminate_process_group(proc, known_pgid=launched_pgid)
        stdout_b, stderr_b = b"", b""
    except asyncio.CancelledError:
        cleanup = await _terminate_process_group(proc, known_pgid=launched_pgid)
        raise
    except Exception as exc:  # noqa: BLE001
        cleanup = await _terminate_process_group(proc, known_pgid=launched_pgid)
        stdout = ""
        stderr = ""
        return _fail(
            reason=redact_text(
                f"execution_runtime_error: {type(exc).__name__}: {exc}",
                exact_values=exact_secret_values,
            ).text,
            failure_class="execution_failure",
            command=redacted_command,
            process_launched=True,
            exit_code=proc.returncode,
            cleanup=cleanup,
            stdout=stdout,
            stderr=stderr,
        )

    # After normal exit, prove the process group is empty (no surviving descendants).
    # A leader that returns success while leaving group members is a failed attempt.
    if not timed_out and launched_pgid is not None and _pgid_alive(launched_pgid):
        cleanup = await _terminate_process_group(proc, known_pgid=launched_pgid)
        stdout_raw = (stdout_b or b"").decode("utf-8", errors="replace")
        stderr_raw = (stderr_b or b"").decode("utf-8", errors="replace")
        stdout = redact_text(stdout_raw, exact_values=exact_secret_values).text
        stderr = redact_text(stderr_raw, exact_values=exact_secret_values).text
        write_text_artifact(attempt_dir / "stdout.txt", stdout)
        write_text_artifact(attempt_dir / "stderr.txt", stderr)
        return _fail(
            reason=(
                f"descendant_process_group_still_alive:pgid={launched_pgid}; "
                f"cleanup={cleanup}"
            ),
            failure_class="execution_failure",
            command=redacted_command,
            process_launched=True,
            exit_code=proc.returncode,
            cleanup=cleanup,
            stdout=stdout,
            stderr=stderr,
        )

    stdout_raw = (stdout_b or b"").decode("utf-8", errors="replace")
    stderr_raw = (stderr_b or b"").decode("utf-8", errors="replace")
    stdout = redact_text(stdout_raw, exact_values=exact_secret_values).text
    stderr = redact_text(stderr_raw, exact_values=exact_secret_values).text
    write_text_artifact(attempt_dir / "stdout.txt", stdout)
    write_text_artifact(attempt_dir / "stderr.txt", stderr)

    if timed_out:
        reason = f"timeout after {spec.timeout_seconds}s; process_group_cleanup={cleanup}"
        if cleanup and not cleanup.get("group_absent", False):
            reason += "; descendant_process_group_not_cleared"
        return _fail(
            reason=reason,
            failure_class="timeout",
            command=redacted_command,
            process_launched=True,
            exit_code=proc.returncode,
            timeout=True,
            cleanup=cleanup,
            stdout=stdout,
            stderr=stderr,
        )
    # Use redacted command in subsequent failure paths.
    command = redacted_command

    decoder = invocation.decoder or default_decoder_for_adapter(attempt.adapter)
    try:
        decoded = decode_adapter_output(
            stdout_raw,  # decode raw then re-redact summaries; values already scrubbed for artifacts
            decoder=decoder,
            expected_role=spec.role,
            requested_model=attempt.requested_model,
            require_model=attempt.requested_model is not None,
        )
        # Recursively redact entire report/transport-derived fields.
        report = redact_structure(decoded.role_report, exact_values=exact_secret_values)
        if decoded.actual_model is not None:
            decoded = type(decoded)(
                role_report=report,
                actual_model=redact_text(
                    str(decoded.actual_model), exact_values=exact_secret_values
                ).text,
                model_evidence_source=decoded.model_evidence_source,
                session_id=decoded.session_id,
                transport_notes=decoded.transport_notes,
            )
        else:
            decoded = type(decoded)(
                role_report=report,
                actual_model=None,
                model_evidence_source=decoded.model_evidence_source,
                session_id=decoded.session_id,
                transport_notes=decoded.transport_notes,
            )
    except ValidationIssue as issue:
        msg = redact_text(issue.message, exact_values=exact_secret_values).text
        if proc.returncode != 0:
            msg = f"non-zero terminal status: exit_code={proc.returncode}; {msg}"
        return _fail(
            reason=msg,
            failure_class=_classify_failure(
                timeout=False, exit_code=proc.returncode, error=issue.message
            ),
            command=command,
            process_launched=True,
            exit_code=proc.returncode,
            stdout=stdout,
            stderr=stderr,
        )

    end_time = time.monotonic()
    attempt_result.end_time = end_time
    attempt_result.exit_code = proc.returncode
    attempt_result.command = list(command)
    attempt_result.actual_model = decoded.actual_model
    attempt_result.model_evidence_source = decoded.model_evidence_source
    attempt_result.process_launched = True

    if proc.returncode != 0:
        # Keep parsed report for diagnostics, but never mark ok.
        attempt_result.status = "failed"
        attempt_result.failure_class = "execution_failure"
        attempt_result.reason = f"non-zero terminal status: exit_code={proc.returncode}"
        attempt_result.ok = False
        lane = LaneResult(
            lane_id=spec.lane_id,
            role=spec.role,
            adapter=attempt.adapter,
            profile=attempt.profile,
            ok=False,
            launch_time=launch_time,
            start_time=attempt_result.start_time,
            end_time=end_time,
            exit_code=proc.returncode,
            error=attempt_result.reason,
            requested_model=attempt.requested_model,
            actual_model=decoded.actual_model,
            model_evidence_source=decoded.model_evidence_source,
            stdout_summary=_summarize(stdout, exact_values=exact_secret_values),
            stderr_summary=_summarize(stderr, exact_values=exact_secret_values),
            report=decoded.role_report,
            stripped_env_names=list(scrub.stripped_names),
            granted_env_names=[n for n in grants if n in scrub.kept_names],
            command=command,
            artifact_dir=str(attempt_dir),
            failure_class="execution_failure",
            process_launched=True,
            model_call_made=attempt.adapter != "host-native",
        )
        attempt_result.model_call_made = attempt.adapter != "host-native"
        return attempt_result, lane

    attempt_result.ok = True
    attempt_result.status = "success"
    attempt_result.model_call_made = attempt.adapter != "host-native"
    execution_id = f"proc-{spec.lane_id}-{attempt_index}-{uuid.uuid4().hex[:12]}"
    lane = LaneResult(
        lane_id=spec.lane_id,
        role=spec.role,
        adapter=attempt.adapter,
        profile=attempt.profile,
        ok=True,
        launch_time=launch_time,
        start_time=attempt_result.start_time,
        end_time=end_time,
        exit_code=proc.returncode,
        requested_model=(
            redact_text(str(attempt.requested_model), exact_values=exact_secret_values).text
            if attempt.requested_model is not None
            else None
        ),
        actual_model=decoded.actual_model,
        model_evidence_source=decoded.model_evidence_source,
        stdout_summary=_summarize(stdout, exact_values=exact_secret_values),
        stderr_summary=_summarize(stderr, exact_values=exact_secret_values),
        report=decoded.role_report,
        stripped_env_names=list(scrub.stripped_names),
        granted_env_names=[n for n in grants if n in scrub.kept_names],
        command=command,
        artifact_dir=str(attempt_dir),
        successful_attempt_index=attempt_index,
        execution_id=execution_id,
        process_launched=True,
        model_call_made=attempt.adapter != "host-native",
        mutated_repo=False,  # set by caller when under council artifact root
    )
    attempt_result.requested_model = (
        redact_text(str(attempt.requested_model), exact_values=exact_secret_values).text
        if attempt.requested_model is not None
        else None
    )
    return attempt_result, lane


async def _run_lane_with_fallbacks(
    spec: LaneSpec,
    packet: ContextPacket,
    work_dir: Path,
    *,
    parent_env: Mapping[str, str] | None = None,
    repo_root: Path,
    host_ledger: _HostEvidenceLedger | None,
    task: str,
) -> LaneResult:
    launch_time = time.monotonic()
    ensure_private_dir(work_dir)
    attempts = _ordered_attempts(spec)
    attempt_results: list[AttemptResult] = []
    last_lane: LaneResult | None = None

    for index, attempt in enumerate(attempts):
        override = spec.command_override if index == 0 else None
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
            extra={
                **dict(packet.extra or {}),
                "execution_identity": {
                    "run_id": packet.run_id,
                    "lane_id": spec.lane_id,
                    "attempt_index": index,
                    "profile": attempt.profile,
                },
            },
        )
        attempt_result, lane = await _run_single_attempt(
            spec=spec,
            attempt=attempt,
            attempt_index=index,
            packet=attempt_packet,
            work_dir=work_dir,
            parent_env=parent_env,
            command_override=override,
            repo_root=repo_root,
            host_ledger=host_ledger,
            task=task,
        )
        # Persist redacted attempt contract snapshot.
        contract_path = Path(lane.artifact_dir or work_dir) / "effective-contract.json"
        write_json_artifact(contract_path, attempt_result.effective_contract)
        attempt_results.append(attempt_result)
        last_lane = lane
        if lane.ok:
            lane.attempts = attempt_results
            lane.launch_time = launch_time
            if index > 0:
                lane.fallback_used = attempt.profile
            return lane

    assert last_lane is not None
    last_lane.attempts = attempt_results
    last_lane.launch_time = launch_time
    last_lane.start_time = attempt_results[0].start_time if attempt_results else launch_time
    last_lane.end_time = attempt_results[-1].end_time if attempt_results else time.monotonic()
    last_lane.process_launched = any(a.process_launched for a in attempt_results)
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
    create_artifacts: bool | None = None,
) -> CouncilResult:
    """Launch independent read-only lanes concurrently and collect reports."""
    lane_list = list(lanes)
    lane_count = len(lane_list)

    if required_quorum is not None and not phase_required:
        required_quorum = None

    try:
        target_quorum = validate_quorum_value(
            target_quorum, name="target_quorum", lane_count=lane_count
        )
        required_quorum = validate_quorum_value(
            required_quorum, name="required_quorum", lane_count=lane_count
        )
    except ValidationIssue as issue:
        rid = run_id or new_run_id(prefix=f"council-{phase}")
        return CouncilResult(
            run_id=rid,
            ok=False,
            council_verified=False,
            blocked=True,
            confidence="blocked",
            notes=[issue.message],
            target_quorum=target_quorum if isinstance(target_quorum, int) else None,
            required_quorum=required_quorum if isinstance(required_quorum, int) else None,
            phase_required=phase_required,
        )

    rid = run_id or new_run_id(prefix=f"council-{phase}")

    # Reject duplicate lane IDs before concurrent directory creation.
    seen_lane_ids: set[str] = set()
    normalized_lane_keys: set[str] = set()
    for spec in lane_list:
        if spec.lane_id in seen_lane_ids:
            return CouncilResult(
                run_id=rid,
                ok=False,
                council_verified=False,
                blocked=True,
                confidence="blocked",
                notes=[f"duplicate_lane_id: `{spec.lane_id}`"],
                phase_required=phase_required,
            )
        seen_lane_ids.add(spec.lane_id)
        norm = safe_path_component(spec.lane_id, field="lane_id")
        if norm in normalized_lane_keys:
            return CouncilResult(
                run_id=rid,
                ok=False,
                council_verified=False,
                blocked=True,
                confidence="blocked",
                notes=[f"duplicate_normalized_lane_id: `{spec.lane_id}` -> `{norm}`"],
                phase_required=phase_required,
            )
        normalized_lane_keys.add(norm)

    external_planned = any(
        spec.adapter != "host-native"
        or spec.command_override is not None
        or spec.host_executor is not None
        or any(a.adapter != "host-native" for a in (spec.attempts or ()))
        for spec in lane_list
    )
    # Host-executor-only native council still should not create .elves if no external
    # process is planned and create_artifacts is not forced.
    process_external = any(
        spec.adapter != "host-native" or spec.command_override is not None
        or any(a.adapter != "host-native" for a in (spec.attempts or ()))
        for spec in lane_list
    )
    should_create_artifacts = (
        create_artifacts if create_artifacts is not None else process_external
    )

    root: Path | None = None
    if should_create_artifacts:
        root = create_exclusive_artifact_root(repo_root, rid)

    host_ledger = _HostEvidenceLedger(rid)
    for spec in lane_list:
        if spec.adapter == "host-native" or any(
            a.adapter == "host-native" for a in (spec.attempts or ())
        ):
            host_ledger.issue_token(spec.lane_id)

    async def _one(spec: LaneSpec) -> LaneResult:
        safe_lane = safe_path_component(spec.lane_id, field="lane_id")
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
            extra={
                "host_challenge_token": host_ledger.token_for(spec.lane_id),
                "binding": _binding_digest(
                    run_id=rid, lane_id=spec.lane_id, role=spec.role, task=task
                ),
            },
        )
        ephemeral_dirs: list[Path] = []
        if root is not None:
            work_dir = ensure_private_dir(resolve_contained_path(root, safe_lane))
        else:
            # Scoped ephemeral dir with guaranteed cleanup (success/fail/timeout).
            import tempfile

            work_dir = Path(tempfile.mkdtemp(prefix=f"cobbler-lane-{safe_lane}-"))
            ephemeral_dirs.append(work_dir)
        try:
            return await _run_lane_with_fallbacks(
                spec,
                packet,
                work_dir,
                parent_env=parent_env,
                repo_root=Path(repo_root),
                host_ledger=host_ledger,
                task=task,
            )
        finally:
            import shutil

            for path in ephemeral_dirs:
                shutil.rmtree(path, ignore_errors=True)

    results = list(await asyncio.gather(*[_one(spec) for spec in lane_list]))

    successful_reports: list[dict[str, Any]] = []
    successful_execution_ids: list[str] = []
    required_failures: list[str] = []
    model_calls = False
    for lane, spec in zip(results, lane_list):
        # Explicit per-lane/per-attempt call truth (host executor + subprocess).
        if lane.model_call_made or any(a.model_call_made for a in lane.attempts):
            model_calls = True
        # Artifact roots under the council run mean runtime mutation of ignored state.
        if root is not None:
            lane.mutated_repo = True
            for attempt in lane.attempts:
                # keep attempt-level truth separate; mutation is lane/run scoped
                pass
        if lane.ok and lane.report is not None and lane.execution_id:
            if lane.execution_id not in successful_execution_ids:
                successful_execution_ids.append(lane.execution_id)
                successful_reports.append(lane.report)
        elif spec.required:
            required_failures.append(spec.lane_id)

    ok, verified, blocked, confidence, notes = evaluate_quorum(
        successful_count=len(successful_execution_ids),
        target_quorum=target_quorum,
        required_quorum=required_quorum,
        phase_required=phase_required,
        required_lane_failures=required_failures,
        lane_count=lane_count,
    )
    notes.append("lanes_independent=true; host_synthesis_only=true")
    notes.append("host_native_requires_host_executor_callback=true")
    notes.append("quorum_counts_distinct_execution_ids=true")

    mutated = root is not None  # exclusive council artifact root created
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
        artifact_root=str(root) if root is not None else None,
        model_calls_made=model_calls,
        mutated_repo=mutated,
        successful_execution_ids=successful_execution_ids,
    )
    if root is not None:
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
    host_executor: HostExecutor | None = None,
) -> LaneResult:
    """Single bounded ephemeral read-only utility review."""
    rid = run_id or new_run_id(prefix="lightweight-review")
    external = adapter != "host-native" or command_override is not None
    root: Path | None = None
    if external:
        root = create_exclusive_artifact_root(repo_root, rid)

    # Do not fabricate host identities for static evidence. Verified host-native
    # reviews require a host_executor callback.
    _ = injected_host_evidence  # compatibility diagnostics only; not a vote.

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
        host_executor=host_executor,
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
    host_ledger = _HostEvidenceLedger(rid)
    if adapter == "host-native":
        host_ledger.issue_token(spec.lane_id)

    ephemeral: Path | None = None
    if root is not None:
        work_dir = ensure_private_dir(resolve_contained_path(root, "lightweight_review"))
    else:
        import tempfile

        ephemeral = Path(tempfile.mkdtemp(prefix="cobbler-lightweight-"))
        work_dir = ephemeral
    try:
        result = await _run_lane_with_fallbacks(
            spec,
            packet,
            work_dir,
            parent_env=parent_env,
            repo_root=Path(repo_root),
            host_ledger=host_ledger,
            task=task,
        )
        # Explicit mutation truth from whether an external artifact root was created.
        result.mutated_repo = root is not None
        if root is not None:
            write_json_artifact(root / "lightweight-review-result.json", result.to_dict())
            if result.artifact_dir is None:
                result.artifact_dir = str(root)
        return result
    finally:
        if ephemeral is not None:
            import shutil

            shutil.rmtree(ephemeral, ignore_errors=True)


def host_evidence_binding(*, run_id: str, lane_id: str, role: str, task: str) -> str:
    """Public helper so tests/hosts can bind injected evidence correctly."""
    return _binding_digest(run_id=run_id, lane_id=lane_id, role=role, task=task)


def run_council_sync(*args: Any, **kwargs: Any) -> CouncilResult:
    return asyncio.run(run_council(*args, **kwargs))


def run_lightweight_review_sync(*args: Any, **kwargs: Any) -> LaneResult:
    return asyncio.run(run_lightweight_review(*args, **kwargs))
