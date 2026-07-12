"""Parallel read-only council dispatch with quorum and fallback policy.

Lanes launch concurrently via asyncio subprocesses (argv arrays, shell=False).
One optional lane failure never erases successful independent evidence. Host
synthesis remains the only fitted-answer step.

Quorum:
- target_quorum (advisory): if fewer successful reports remain after fallbacks,
  continue with host synthesis, record a confidence drop, and do not label the
  result council-verified.
- required_quorum: valid only when the phase is explicitly required=true; counts
  successful independent reports (including a fresh host lane). Block when unmet
  after recovery/fallback.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence

from .adapters import (
    AdapterInvocation,
    build_readonly_invocation,
    parse_role_report,
)
from .context import (
    ContextPacket,
    EnvScrubResult,
    build_context_packet,
    council_artifact_root,
    ensure_private_dir,
    new_run_id,
    scrub_environment,
    write_json_artifact,
    write_text_artifact,
)
from .schema import ValidationIssue


LaneRunner = Callable[["LaneSpec", ContextPacket, Path], Awaitable["LaneResult"]]


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
    # When set, used instead of building a real adapter command (tests/fakes).
    command_override: tuple[str, ...] | None = None


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
    stdout_summary: str = ""
    stderr_summary: str = ""
    report: dict[str, Any] | None = None
    error: str | None = None
    fallback_used: str | None = None
    stripped_env_names: list[str] = field(default_factory=list)
    command: list[str] = field(default_factory=list)
    artifact_dir: str | None = None

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
    compact = " ".join((text or "").split())
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


async def _run_subprocess_lane(
    spec: LaneSpec,
    packet: ContextPacket,
    work_dir: Path,
    *,
    parent_env: Mapping[str, str] | None = None,
) -> LaneResult:
    launch_time = time.monotonic()
    ensure_private_dir(work_dir)
    packet_path = write_json_artifact(work_dir / "packet.json", packet.to_dict())
    prompt_path = write_text_artifact(
        work_dir / "prompt.txt",
        (
            f"Role: {packet.role}\n"
            f"Mode: {packet.mode}\n"
            f"Scope: {packet.scope}\n"
            f"Task: {packet.task}\n"
            f"Return JSON role report with fields: "
            f"{', '.join(packet.output_schema)}\n"
        ),
    )

    scrub: EnvScrubResult = scrub_environment(
        parent_env,
        extra_allowlist=set(spec.env_extra_allowlist),
    )

    if spec.command_override is not None:
        command = list(spec.command_override)
        invocation = AdapterInvocation(
            adapter=spec.adapter,
            executable=command[0] if command else "",
            argv=tuple(command),
            read_only=True,
            notes="command_override",
        )
    else:
        invocation = build_readonly_invocation(
            adapter=spec.adapter,
            profile=spec.profile,
            executable=spec.executable,
            packet_path=packet_path,
            prompt_path=prompt_path,
            requested_model=spec.requested_model,
            extra_args=spec.extra_args,
        )
        command = list(invocation.argv)

    start_time = time.monotonic()
    if not command:
        end_time = time.monotonic()
        return LaneResult(
            lane_id=spec.lane_id,
            role=spec.role,
            adapter=spec.adapter,
            profile=spec.profile,
            ok=False,
            launch_time=launch_time,
            start_time=start_time,
            end_time=end_time,
            error="empty command",
            requested_model=spec.requested_model,
            stripped_env_names=list(scrub.stripped_names),
            command=command,
            artifact_dir=str(work_dir),
        )

    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=scrub.env,
            cwd=str(work_dir),
        )
    except FileNotFoundError as exc:
        end_time = time.monotonic()
        return LaneResult(
            lane_id=spec.lane_id,
            role=spec.role,
            adapter=spec.adapter,
            profile=spec.profile,
            ok=False,
            launch_time=launch_time,
            start_time=start_time,
            end_time=end_time,
            error=f"executable not found: {exc}",
            requested_model=spec.requested_model,
            stripped_env_names=list(scrub.stripped_names),
            command=command,
            artifact_dir=str(work_dir),
        )

    timed_out = False
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(),
            timeout=spec.timeout_seconds,
        )
    except asyncio.TimeoutError:
        timed_out = True
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            stdout_b, stderr_b = await proc.communicate()
        except Exception:
            stdout_b, stderr_b = b"", b""

    end_time = time.monotonic()
    stdout = (stdout_b or b"").decode("utf-8", errors="replace")
    stderr = (stderr_b or b"").decode("utf-8", errors="replace")
    write_text_artifact(work_dir / "stdout.txt", stdout)
    write_text_artifact(work_dir / "stderr.txt", stderr)

    result = LaneResult(
        lane_id=spec.lane_id,
        role=spec.role,
        adapter=spec.adapter,
        profile=spec.profile,
        ok=False,
        launch_time=launch_time,
        start_time=start_time,
        end_time=end_time,
        timeout=timed_out,
        exit_code=proc.returncode,
        requested_model=spec.requested_model,
        stdout_summary=_summarize(stdout),
        stderr_summary=_summarize(stderr),
        stripped_env_names=list(scrub.stripped_names),
        command=command,
        artifact_dir=str(work_dir),
    )

    if timed_out:
        result.error = f"timeout after {spec.timeout_seconds}s"
        return result

    # Do not treat exit 0 alone as success — require structured report.
    try:
        report = parse_role_report(
            stdout,
            expected_role=spec.role,
            requested_model=spec.requested_model,
        )
    except ValidationIssue as issue:
        result.error = issue.message
        # Stderr warnings alone do not fail inference when structured output is valid;
        # here parsing failed.
        return result

    result.report = report
    result.actual_model = report.get("actual_model") or report.get("model")
    result.ok = True
    # Preserve stderr summary even on success (warnings with successful inference).
    return result


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
    root = ensure_private_dir(council_artifact_root(repo_root, rid))

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
        return await _run_subprocess_lane(
            spec,
            packet,
            work_dir,
            parent_env=parent_env,
        )

    # Concurrent fan-out — do not launch sequentially.
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
) -> LaneResult:
    """Single bounded ephemeral read-only utility review.

    Independent of council quorum. Cannot close high-risk review or mutate git/PR.
    Does not count as a default independent council vote.
    """
    rid = run_id or new_run_id(prefix="lightweight-review")
    root = ensure_private_dir(council_artifact_root(repo_root, rid))
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
    result = await _run_subprocess_lane(
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
