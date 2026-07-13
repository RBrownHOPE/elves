"""Single-attempt lifecycle for council dispatch (extracted coordinator body).

Owns transport setup, host-native handoff, isolated external launch, and
decode/result assembly wiring. ``dispatch._run_single_attempt`` remains a thin
public coordinator (≤150 lines).
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Mapping

from .context import (
    ContextPacket,
    ensure_private_dir,
    redact_structure,
    redact_text,
    resolve_contained_path,
    safe_path_component,
    write_json_artifact,
    write_text_artifact,
)
from .dispatch_attempt import (
    attempt_env_grants,
    build_effective_contract,
    check_capabilities,
    classify_failure,
    prepare_transport,
    record_command_digests,
    write_attempt_artifacts,
)
from .dispatch_external import prepare_external_launch, run_external_subprocess
from .dispatch_host_native import run_host_native_attempt
from .dispatch_models import AttemptResult, LaneResult, LaneSpec
from .dispatch_results import assemble_external_result, build_failed_attempt
from .schema import EffectiveAttempt, ValidationIssue


async def run_single_attempt(
    *,
    spec: LaneSpec,
    attempt: EffectiveAttempt,
    attempt_index: int,
    packet: ContextPacket,
    work_dir: Path,
    parent_env: Mapping[str, str] | None,
    command_override: tuple[str, ...] | None,
    repo_root: Path,
    host_ledger: Any,
    task: str,
) -> tuple[AttemptResult, LaneResult]:
    """Full attempt lifecycle. Returns (AttemptResult, LaneResult)."""
    launch_time = time.monotonic()
    attempt_dir = resolve_contained_path(
        work_dir,
        f"attempt-{attempt_index}-{safe_path_component(attempt.profile, field='profile')}",
    )
    ensure_private_dir(attempt_dir)
    grants = attempt_env_grants(spec, attempt)
    transport = prepare_transport(
        parent_env=parent_env,
        env_extra_allowlist=tuple(spec.env_extra_allowlist),
        grants=tuple(grants),
    )
    scrub, exact_secret_values = transport.scrub, transport.exact_secret_values
    packet_dict = redact_structure(packet.to_dict(), exact_values=exact_secret_values)
    redacted_task = redact_text(task, exact_values=exact_secret_values).text
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
        requested_model=(
            redact_text(str(attempt.requested_model), exact_values=exact_secret_values).text
            if attempt.requested_model is not None
            else None
        ),
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

    def _fail(**kwargs: Any) -> tuple[AttemptResult, LaneResult]:
        return build_failed_attempt(
            attempt_result=attempt_result,
            spec=spec,
            attempt=attempt,
            launch_time=launch_time,
            scrub=scrub,
            grants=list(grants),
            exact_secret_values=exact_secret_values,
            attempt_dir=str(attempt_dir),
            **kwargs,
        )

    if not attempt.enabled:
        return _fail(
            reason=f"profile `{attempt.profile}` is disabled",
            failure_class="unavailable",
        )
    cap_err, cap_required, cap_missing = check_capabilities(
        attempt, lane_qualified=spec.qualified_capabilities
    )
    attempt_result.effective_contract["required_capabilities"] = list(cap_required)
    attempt_result.effective_contract["missing_capabilities"] = list(cap_missing)
    if cap_err:
        return _fail(reason=cap_err, failure_class="capability")
    if attempt.adapter == "host-native" and command_override is None:
        return await run_host_native_attempt(
            spec=spec,
            attempt=attempt,
            attempt_index=attempt_index,
            attempt_result=attempt_result,
            attempt_dir=str(attempt_dir),
            packet_dict=packet_dict if isinstance(packet_dict, dict) else {},
            exact_secret_values=exact_secret_values,
            scrub=scrub,
            grants=list(grants),
            host_ledger=host_ledger,
            task=task,
            launch_time=launch_time,
            fail_fn=_fail,
        )
    prepare_task = asyncio.create_task(
        asyncio.to_thread(
            prepare_external_launch,
            spec=spec,
            attempt=attempt,
            attempt_index=attempt_index,
            repo_root=Path(repo_root),
            packet_path=packet_path,
            prompt_path=prompt_path,
            packet_dict=packet_dict if isinstance(packet_dict, dict) else {},
            redacted_task=redacted_task,
            exact_secret_values=exact_secret_values,
            grants=list(grants),
            scrub_env=scrub.env,
            command_override=command_override,
            parent_env=parent_env,
        )
    )
    try:
        plan = await asyncio.shield(prepare_task)
    except asyncio.CancelledError as cancelled:
        # A worker thread cannot be force-cancelled safely. Acquire its result,
        # remove any completed snapshot, then propagate cancellation.
        try:
            cancelled_plan = await asyncio.shield(prepare_task)
        except BaseException:
            raise cancelled
        if cancelled_plan.isolated is not None:
            cancelled_plan.isolated.cleanup()
        raise
    except ValidationIssue as issue:
        return _fail(
            reason=issue.message,
            failure_class=(
                "isolation_failure"
                if "isolation" in (issue.code or "")
                else classify_failure(
                    timeout=False,
                    exit_code=None,
                    error=issue.message,
                )
            ),
        )
    attempt_result.effective_contract["isolation"] = plan.isolation_meta
    if plan.external_attempt_skipped:
        return _fail(
            reason=(
                "external_attempt_skipped_fallback_chain_continues: "
                + str(plan.isolation_meta.get("reason") or "isolation failed")
            ),
            failure_class="unavailable",
        )
    try:
        invocation = plan.invocation
        raw_command = list(plan.argv)
        redacted_command = record_command_digests(
            attempt_result.effective_contract,
            raw_command=raw_command,
            exact_secret_values=exact_secret_values,
            invocation=invocation,
        )
        if invocation and invocation.prompt_file_body is not None:
            write_text_artifact(
                prompt_path,
                redact_text(
                    invocation.prompt_file_body,
                    exact_values=exact_secret_values,
                ).text,
            )
        elif not prompt_path.exists():
            write_text_artifact(prompt_path, redacted_task)
        proc_result = await run_external_subprocess(
            plan=plan,
            timeout_seconds=spec.timeout_seconds,
        )
    except BaseException:
        # run_external_subprocess owns cleanup once called; this covers artifact
        # and command-evidence failures between plan creation and launch.
        if plan.isolated is not None:
            plan.isolated.cleanup()
        raise
    return assemble_external_result(
        proc_result=proc_result,
        invocation=invocation,
        redacted_command=redacted_command,
        attempt_result=attempt_result,
        attempt=attempt,
        spec=spec,
        attempt_index=attempt_index,
        attempt_dir=attempt_dir,
        exact_secret_values=exact_secret_values,
        scrub=scrub,
        grants=list(grants),
        launch_time=launch_time,
        fail_fn=_fail,
    )
