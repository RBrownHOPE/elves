"""Host-native council attempt path (trusted host_executor only)."""

from __future__ import annotations

import inspect
import time
from typing import Any, Mapping

from .adapters import validate_role_report
from .context import redact_structure, redact_text
from .dispatch_attempt import classify_failure
from .dispatch_models import AttemptResult, LaneResult, LaneSpec
from .schema import EffectiveAttempt, ValidationIssue


async def run_host_native_attempt(
    *,
    spec: LaneSpec,
    attempt: EffectiveAttempt,
    attempt_index: int,
    attempt_result: AttemptResult,
    attempt_dir: str,
    packet_dict: Mapping[str, Any],
    exact_secret_values: frozenset[str],
    scrub: Any,
    grants: list[str],
    host_ledger: Any,
    task: str,
    launch_time: float,
    fail_fn,
) -> tuple[AttemptResult, LaneResult]:
    """Execute host-native path. Returns (attempt_result, lane_result)."""
    if host_ledger is None:
        return fail_fn(
            reason="host ledger unavailable for host-native lane",
            failure_class="unavailable",
        )
    if spec.host_executor is None:
        return fail_fn(
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
        fail = fail_fn(
            reason=issue.message,
            failure_class=classify_failure(
                timeout=False, exit_code=None, error=issue.message
            ),
        )
        if host_call_made:
            fail[0].model_call_made = True
            fail[1].model_call_made = True
        return fail
    except Exception as exc:  # noqa: BLE001
        fail = fail_fn(
            reason=f"host_executor_error: {type(exc).__name__}",
            failure_class="execution_failure",
        )
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
