"""Canonical Elves behavior policy and semantic scenario fixtures.

Structured policy (not phrase inventories) for:
- direct edit / bounded task / full Elves run
- single-kickoff E2E vs explicit legacy two-call
- chat-to-work vs chat-to-land
- trusted Grok full-run vs untrusted writer lease
- test-integrity and rollback naming

Scenario resolution is semantic: routing, continuation, landing, test integrity,
and rollback expectations are asserted by structured fields, not literal phrases.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


POLICY_VERSION = "2.1.0"

# Driver monitor wake conditions once a full-run is healthy.
PARKED_MONITOR_WAKE_CONDITIONS: frozenset[str] = frozenset(
    {
        "blocker",
        "error",
        "stale_heartbeat",
        "safety_tripwire",
        "high_risk_checkpoint",
        "user_input",
        "worker_exit",
        "final_readiness",
    }
)

# Forbidden wake triggers for parked full-run drivers.
FORBIDDEN_FULL_RUN_WAKE_TRIGGERS: frozenset[str] = frozenset(
    {
        "per_push",
        "per_tool_call",
        "per_batch_prompt",
        "resume_batch_required",
    }
)

# Quiet parked monitoring is part of the product contract, not a prose hint.
# The driver should use a host wait/monitor primitive when one exists. A host
# that must poll uses half the stale window, bounded so it neither spins nor
# sleeps past a practical safety check. Unchanged healthy polls never justify
# a chat message; nonterminal progress updates are coalesced separately.
PARKED_MONITOR_MIN_POLL_SECONDS = 60
PARKED_MONITOR_MAX_POLL_SECONDS = 300
PARKED_MONITOR_USER_HEARTBEAT_SECONDS = 900
PARKED_MONITOR_UPDATE_POLICY = "material_transition_or_coalesced_15m_heartbeat"


def parked_monitor_poll_after_seconds(stale_after_seconds: int) -> int:
    """Return the quiet polling interval derived from the configured stale window."""
    if isinstance(stale_after_seconds, bool) or not isinstance(stale_after_seconds, int):
        raise TypeError("stale_after_seconds must be an integer")
    half_window = max(1, stale_after_seconds) // 2
    return max(
        PARKED_MONITOR_MIN_POLL_SECONDS,
        min(PARKED_MONITOR_MAX_POLL_SECONDS, half_window),
    )


@dataclass(frozen=True)
class BehaviorDecision:
    """Resolved behavior for a scenario."""

    scenario_id: str
    handling_level: str  # direct_edit | bounded_task | full_run
    kickoff_mode: str  # single_kickoff | legacy_two_call | n_a
    work_driver: str  # host_native | grok_build | untrusted_writer | n_a
    delegation_scope: str  # none | batch | full_run
    git_mode: str  # host_only | branch_progress | detached_lease
    driver_monitor_mode: str  # interactive | parked_monitor | n_a
    landing_mode: str  # none | chat_to_work | chat_to_land
    test_integrity: str  # preserve_or_improve | forbid_weaken_for_green
    rollback_naming: str  # run_scoped | global_forbidden
    continuation: str  # same_session | resume_batch | host_reprompt | none
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BehaviorScenario:
    """Input scenario for policy resolution."""

    scenario_id: str
    intent: str
    signals: tuple[str, ...] = ()
    host: str = "either"  # claude | codex | either
    expected: BehaviorDecision | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "intent": self.intent,
            "signals": list(self.signals),
            "host": self.host,
            "expected": self.expected.to_dict() if self.expected else None,
        }


def _decision(**kwargs: Any) -> BehaviorDecision:
    return BehaviorDecision(**kwargs)


# Canonical scenario fixtures used by semantic consistency tests.
SCENARIOS: dict[str, BehaviorScenario] = {
    "direct_edit": BehaviorScenario(
        scenario_id="direct_edit",
        intent="Small local fix with the host agent editing directly",
        signals=("direct edit", "no overnight", "no external worker"),
        expected=_decision(
            scenario_id="direct_edit",
            handling_level="direct_edit",
            kickoff_mode="n_a",
            work_driver="host_native",
            delegation_scope="none",
            git_mode="host_only",
            driver_monitor_mode="interactive",
            landing_mode="none",
            test_integrity="preserve_or_improve",
            rollback_naming="run_scoped",
            continuation="none",
            notes=("Host edits in place; no full-run supervisor",),
        ),
    ),
    "bounded_task": BehaviorScenario(
        scenario_id="bounded_task",
        intent="One bounded host-native batch unless an external driver is explicit",
        signals=("bounded task", "one batch"),
        expected=_decision(
            scenario_id="bounded_task",
            handling_level="bounded_task",
            kickoff_mode="n_a",
            work_driver="host_native",
            delegation_scope="none",
            git_mode="host_only",
            driver_monitor_mode="interactive",
            landing_mode="none",
            test_integrity="preserve_or_improve",
            rollback_naming="run_scoped",
            continuation="none",
            notes=("Optional Grok requires an explicit work-driver signal",),
        ),
    ),
    "full_run_trusted_grok": BehaviorScenario(
        scenario_id="full_run_trusted_grok",
        intent="Trusted Grok owns the feature-branch labor loop for all batches",
        signals=("full run", "turn it over to grok", "do not stop", "overnight"),
        expected=_decision(
            scenario_id="full_run_trusted_grok",
            handling_level="full_run",
            kickoff_mode="single_kickoff",
            work_driver="grok_build",
            delegation_scope="full_run",
            git_mode="branch_progress",
            driver_monitor_mode="parked_monitor",
            landing_mode="chat_to_work",
            test_integrity="preserve_or_improve",
            rollback_naming="run_scoped",
            continuation="same_session",
            notes=(
                "One packet, one exact session, no per-batch driver prompt",
                "Driver parks; wakes only for enumerated conditions",
            ),
        ),
    ),
    "single_kickoff_e2e": BehaviorScenario(
        scenario_id="single_kickoff_e2e",
        intent="User pastes plan and says run now / chat-to-work",
        signals=("run now", "chat-to-work", "single kickoff"),
        expected=_decision(
            scenario_id="single_kickoff_e2e",
            handling_level="full_run",
            kickoff_mode="single_kickoff",
            work_driver="host_native",
            delegation_scope="full_run",
            git_mode="host_only",
            driver_monitor_mode="interactive",
            landing_mode="chat_to_work",
            test_integrity="preserve_or_improve",
            rollback_naming="run_scoped",
            continuation="same_session",
            notes=("Stage then execute without waiting for a second human launch",),
        ),
    ),
    "legacy_two_call": BehaviorScenario(
        scenario_id="legacy_two_call",
        intent="Explicit legacy stage-then-separate-launch path",
        signals=("legacy two-call", "stage only", "wait for launch prompt"),
        expected=_decision(
            scenario_id="legacy_two_call",
            handling_level="full_run",
            kickoff_mode="legacy_two_call",
            work_driver="host_native",
            delegation_scope="full_run",
            git_mode="host_only",
            driver_monitor_mode="interactive",
            landing_mode="chat_to_work",
            test_integrity="preserve_or_improve",
            rollback_naming="run_scoped",
            continuation="host_reprompt",
            notes=("Only when user explicitly chooses legacy two-call",),
        ),
    ),
    "chat_to_land": BehaviorScenario(
        scenario_id="chat_to_land",
        intent="Single kickoff through reviewed merge commit",
        signals=("chat-to-land", "merge on green", "reviewed pr landing"),
        expected=_decision(
            scenario_id="chat_to_land",
            handling_level="full_run",
            kickoff_mode="single_kickoff",
            work_driver="host_native",
            delegation_scope="full_run",
            git_mode="host_only",
            driver_monitor_mode="interactive",
            landing_mode="chat_to_land",
            test_integrity="preserve_or_improve",
            rollback_naming="run_scoped",
            continuation="same_session",
            notes=("Merge only after final readiness; regular merge commit only",),
        ),
    ),
    "untrusted_writer": BehaviorScenario(
        scenario_id="untrusted_writer",
        intent="Prove detached writer lease boundary",
        signals=("untrusted", "writer lease", "detached commits"),
        expected=_decision(
            scenario_id="untrusted_writer",
            handling_level="bounded_task",
            kickoff_mode="n_a",
            work_driver="untrusted_writer",
            delegation_scope="batch",
            git_mode="detached_lease",
            driver_monitor_mode="interactive",
            landing_mode="none",
            test_integrity="preserve_or_improve",
            rollback_naming="run_scoped",
            continuation="resume_batch",
            notes=(
                "Distinct from trusted branch_progress authority",
                "Host imports patches; worker never owns refs/push/PR",
            ),
        ),
    ),
    "test_integrity": BehaviorScenario(
        scenario_id="test_integrity",
        intent="Behavior-driven test updates vs green-only weakening",
        signals=("update tests", "behavior changed", "coverage"),
        expected=_decision(
            scenario_id="test_integrity",
            handling_level="direct_edit",
            kickoff_mode="n_a",
            work_driver="host_native",
            delegation_scope="none",
            git_mode="host_only",
            driver_monitor_mode="interactive",
            landing_mode="none",
            test_integrity="preserve_or_improve",
            rollback_naming="run_scoped",
            continuation="none",
            notes=(
                "Legitimate behavior-driven test changes allowed with evidence",
                "Never skip/delete/weaken tests merely to obtain green",
            ),
        ),
    ),
    "rollback_naming": BehaviorScenario(
        scenario_id="rollback_naming",
        intent="Rollback refs must be run/session scoped",
        signals=("rollback tag", "elves/pre-batch"),
        expected=_decision(
            scenario_id="rollback_naming",
            handling_level="full_run",
            kickoff_mode="single_kickoff",
            work_driver="host_native",
            delegation_scope="full_run",
            git_mode="host_only",
            driver_monitor_mode="interactive",
            landing_mode="chat_to_work",
            test_integrity="preserve_or_improve",
            rollback_naming="run_scoped",
            continuation="same_session",
            notes=("Global unscoped rollback tags are forbidden; use run/session scope",),
        ),
    ),
}


def list_scenarios() -> list[BehaviorScenario]:
    return [SCENARIOS[k] for k in sorted(SCENARIOS)]


def get_scenario(scenario_id: str) -> BehaviorScenario:
    if scenario_id not in SCENARIOS:
        raise KeyError(f"Unknown behavior scenario: {scenario_id}")
    return SCENARIOS[scenario_id]


def resolve_scenario(scenario_id: str) -> BehaviorDecision:
    scenario = get_scenario(scenario_id)
    if scenario.expected is None:
        raise RuntimeError(f"Scenario {scenario_id} has no expected decision")
    return scenario.expected


def resolve_from_signals(
    signals: Mapping[str, bool] | None = None,
    *,
    intent: str = "",
) -> BehaviorDecision:
    """Compose handling dimensions independently (not first-match overwrite).

    Kickoff, landing, trust/lane, delegation, and driver-monitor are combined so
    e.g. full_run+trusted_grok+chat_to_land keeps Grok parked-monitor *and*
    chat-to-land merge intent.
    """
    flags = {k: bool(v) for k, v in (signals or {}).items()}
    text = (intent or "").lower()

    def has(*keys: str) -> bool:
        for key in keys:
            phrase = key.replace("_", " ")
            if flags.get(key) or phrase in text:
                return True
            # This is the natural operator wording used in the full-run contract.
            if phrase == "turn over to grok" and "turn it over to grok" in text:
                return True
        return False

    # Base scenario for notes/scenario_id only.
    # full_run/overnight alone is host-native single-kickoff, not trusted Grok.
    if has("untrusted", "writer_lease", "detached_lease"):
        base = resolve_scenario("untrusted_writer")
    elif has("legacy_two_call", "stage_only"):
        base = resolve_scenario("legacy_two_call")
    elif has("trusted_grok", "turn_over_to_grok", "work_driver_grok") and has(
        "full_run", "overnight"
    ):
        base = resolve_scenario("full_run_trusted_grok")
    elif has("full_run", "overnight", "single_kickoff", "run_now", "chat_to_work"):
        base = resolve_scenario("single_kickoff_e2e")
    elif has("bounded_task", "one_batch"):
        base = resolve_scenario("bounded_task")
    elif has("test_integrity", "update_tests"):
        base = resolve_scenario("test_integrity")
    elif has("rollback", "rollback_tag"):
        base = resolve_scenario("rollback_naming")
    elif has("chat_to_land", "merge_on_green", "reviewed_pr_landing"):
        base = resolve_scenario("chat_to_land")
    else:
        base = resolve_scenario("direct_edit")

    # Compose dimensions independently on top of the base.
    handling_level = base.handling_level
    kickoff_mode = base.kickoff_mode
    work_driver = base.work_driver
    delegation_scope = base.delegation_scope
    git_mode = base.git_mode
    driver_monitor_mode = base.driver_monitor_mode
    landing_mode = base.landing_mode
    continuation = base.continuation
    notes = list(base.notes)

    if has("untrusted", "writer_lease", "detached_lease"):
        work_driver = "untrusted_writer"
        git_mode = "detached_lease"
        delegation_scope = "batch"
        driver_monitor_mode = "interactive"
        handling_level = "bounded_task"
    # Only explicit trusted-Grok / work-driver signals select Grok parked full-run.
    elif has("trusted_grok", "turn_over_to_grok", "work_driver_grok") and has(
        "full_run", "overnight"
    ):
        work_driver = "grok_build"
        delegation_scope = "full_run"
        git_mode = "branch_progress"
        driver_monitor_mode = "parked_monitor"
        handling_level = "full_run"
        continuation = "same_session"
        if kickoff_mode in {"n_a"} and not has("legacy_two_call"):
            kickoff_mode = "single_kickoff"
        notes.append("composed: trusted Grok full-run parked-monitor")
    elif has("full_run", "overnight"):
        work_driver = "host_native"
        handling_level = "full_run"
        if driver_monitor_mode == "parked_monitor":
            driver_monitor_mode = "interactive"
        notes.append("composed: full_run host-native (no trusted Grok signal)")

    if has("chat_to_land", "merge_on_green", "reviewed_pr_landing"):
        landing_mode = "chat_to_land"
        handling_level = "full_run"
        kickoff_mode = "single_kickoff" if kickoff_mode == "n_a" else kickoff_mode
        notes.append("composed: chat-to-land landing")
    elif has("chat_to_work", "run_now", "single_kickoff") and landing_mode == "none":
        landing_mode = "chat_to_work"

    if has("legacy_two_call", "stage_only"):
        kickoff_mode = "legacy_two_call"
        continuation = "host_reprompt"
        notes.append("composed: explicit legacy two-call")

    if has("direct_edit") and not has("full_run", "bounded_task", "trusted_grok"):
        handling_level = "direct_edit"
        work_driver = "host_native"
        delegation_scope = "none"
        driver_monitor_mode = "interactive"

    if has("bounded_task", "one_batch") and not has("full_run", "overnight"):
        handling_level = "bounded_task"
        driver_monitor_mode = "interactive"
        if has("trusted_grok", "turn_over_to_grok", "work_driver_grok"):
            work_driver = "grok_build"
            delegation_scope = "batch"
            git_mode = "branch_progress"
            continuation = "resume_batch"
            notes.append("composed: explicit Grok bounded task")
        else:
            work_driver = "host_native"
            delegation_scope = "none"
            git_mode = "host_only"
            continuation = "none"

    return BehaviorDecision(
        scenario_id=base.scenario_id,
        handling_level=handling_level,
        kickoff_mode=kickoff_mode,
        work_driver=work_driver,
        delegation_scope=delegation_scope,
        git_mode=git_mode,
        driver_monitor_mode=driver_monitor_mode,
        landing_mode=landing_mode,
        test_integrity=base.test_integrity,
        rollback_naming="run_scoped",
        continuation=continuation,
        notes=tuple(notes),
    )


def policy_snapshot() -> dict[str, Any]:
    return {
        "policy_version": POLICY_VERSION,
        "parked_monitor_wake_conditions": sorted(PARKED_MONITOR_WAKE_CONDITIONS),
        "forbidden_full_run_wake_triggers": sorted(FORBIDDEN_FULL_RUN_WAKE_TRIGGERS),
        "parked_monitor_quiet_policy": {
            "min_poll_seconds": PARKED_MONITOR_MIN_POLL_SECONDS,
            "max_poll_seconds": PARKED_MONITOR_MAX_POLL_SECONDS,
            "user_heartbeat_seconds": PARKED_MONITOR_USER_HEARTBEAT_SECONDS,
            "update_policy": PARKED_MONITOR_UPDATE_POLICY,
            "unchanged_healthy_poll_silent": True,
        },
        "scenarios": [s.to_dict() for s in list_scenarios()],
    }


def assert_decision_matches(actual: BehaviorDecision, expected: BehaviorDecision) -> list[str]:
    """Return field-level mismatches (empty means match)."""
    mismatches: list[str] = []
    for key, exp in expected.to_dict().items():
        if key == "notes":
            continue
        got = getattr(actual, key)
        if got != exp and asdict(actual).get(key) != exp:
            mismatches.append(f"{key}: expected={exp!r} got={got!r}")
    return mismatches
