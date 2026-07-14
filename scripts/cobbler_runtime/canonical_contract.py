"""Elves 2.3 canonical workflow contract.

Defines once (not in prose forks):
- normal flow / state transitions
- actor authority
- wake conditions
- proof rules
- terminal outcomes
- risk (low|standard|high) independent of trust_mode (trusted|untrusted)
- safety-kernel inventory with canonical destinations and proving tests
- migration ledger entries for 2.2 → 2.3

This module is pure policy: no process I/O, no credentials, no network.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence


POLICY_VERSION = "2.3.0"

# ---------------------------------------------------------------------------
# Architecture: run states
# ---------------------------------------------------------------------------

RUN_STATES: tuple[str, ...] = (
    "staging",
    "executing",
    "reconciling",
    "reviewing",
    "revising",
    "ready",
    "terminal",
)

# Allowed transitions. reviewing <-> revising is the only cycle.
STATE_TRANSITIONS: dict[str, frozenset[str]] = {
    "staging": frozenset({"executing", "terminal"}),
    "executing": frozenset({"reconciling", "terminal"}),
    "reconciling": frozenset({"reviewing", "executing", "terminal"}),
    "reviewing": frozenset({"revising", "ready", "terminal"}),
    "revising": frozenset({"reviewing", "terminal"}),
    "ready": frozenset({"terminal"}),
    "terminal": frozenset(),
}

# ---------------------------------------------------------------------------
# Actors and authority (immutable matrix)
# ---------------------------------------------------------------------------

ACTORS: tuple[str, ...] = (
    "user",
    "driver",  # host Claude Code / Codex coordinator
    "worker",  # authority is narrowed by actor_may(..., trust_mode=...)
    "reviewer",  # independent terminal review (read-only)
)

# Capability → who may perform it. False for everyone except listed.
AUTHORITY_MATRIX: dict[str, frozenset[str]] = {
    "edit_product_code": frozenset({"driver", "worker"}),
    "commit_feature_branch": frozenset({"driver", "worker"}),
    "push_feature_branch": frozenset({"driver", "worker"}),
    "edit_run_memory": frozenset({"driver"}),
    "open_or_update_pr": frozenset({"driver"}),
    "grant_driver_merge_authorization": frozenset({"user", "driver"}),
    "perform_merge": frozenset({"user", "driver"}),
    "modify_protected_refs": frozenset({"user", "driver"}),
    "modify_landing_outcome": frozenset({"user", "driver"}),
    "attest_readiness": frozenset({"driver"}),
    "emit_worker_events": frozenset({"worker"}),
    "independent_terminal_review": frozenset({"reviewer", "driver"}),
    "follow_worker_stream": frozenset({"driver", "user"}),
}

# Capabilities the worker may never hold, even if a worker report claims them.
WORKER_FORBIDDEN_CAPABILITIES: frozenset[str] = frozenset(
    {
        "perform_merge",
        "modify_protected_refs",
        "modify_landing_outcome",
        "grant_driver_merge_authorization",
        "attest_readiness",
        "edit_run_memory",
        "open_or_update_pr",
        "create_tags",
    }
)

# Untrusted workers edit only their isolated checkout and may create an audited
# detached handoff commit. They never own or advance the feature branch/ref.
UNTRUSTED_WORKER_FORBIDDEN_CAPABILITIES: frozenset[str] = frozenset(
    {
        "commit_feature_branch",
        "push_feature_branch",
        "follow_worker_stream",
    }
)

# ---------------------------------------------------------------------------
# Risk and trust (independent axes)
# ---------------------------------------------------------------------------

RISK_LEVELS: tuple[str, ...] = ("low", "standard", "high")
TRUST_MODES: tuple[str, ...] = ("trusted", "untrusted")

# Legacy 2.2 four-tier names map onto the independent axes without loss.
LEGACY_TIER_TO_RISK_TRUST: dict[str, tuple[str, str]] = {
    "trivial_docs": ("low", "trusted"),
    "standard_trusted": ("standard", "trusted"),
    "high_risk_trusted": ("high", "trusted"),
    "untrusted": ("high", "untrusted"),
}

# Reverse: (risk, trust) → preferred legacy label for compatibility fixtures.
RISK_TRUST_TO_LEGACY_TIER: dict[tuple[str, str], str] = {
    ("low", "trusted"): "trivial_docs",
    ("standard", "trusted"): "standard_trusted",
    ("high", "trusted"): "high_risk_trusted",
    ("low", "untrusted"): "untrusted",
    ("standard", "untrusted"): "untrusted",
    ("high", "untrusted"): "untrusted",
}

# ---------------------------------------------------------------------------
# Wake conditions (deterministic; no model watcher)
# ---------------------------------------------------------------------------

DRIVER_WAKE_CONDITIONS: frozenset[str] = frozenset(
    {
        "worker_exit",
        "worker_death",
        "stale_heartbeat",
        "hang",
        "missing_or_malformed_completion",
        "safety_tripwire",
        "explicit_blocker",
        "material_scope_or_assumption_change",
        "high_risk_checkpoint",
        "user_input",
        "final_readiness",
        "reconcile",
        "error",
    }
)

FORBIDDEN_WAKE_TRIGGERS: frozenset[str] = frozenset(
    {
        "per_push",
        "per_tool_call",
        "per_batch_prompt",
        "timed_chat_update",
        "model_monitor_tick",
        "resume_batch_required",
    }
)

# ---------------------------------------------------------------------------
# Proof rules
# ---------------------------------------------------------------------------

PROOF_BUDGET_SLOGAN = "validate once, verify changes, attest final"

PROOF_RULES: dict[str, str] = {
    "per_batch_default": "touched_surfaces_via_impact_path",
    "impact_path": "changed_surface -> affected_consumer -> selected_test",
    "broad_required_at": "risk_checkpoint_or_terminal_readiness",
    "evidence_inputs": "path_mode_content_command_deps_runtime_env",
    "reuse": "identical_input_digest_and_scope",
    "cleanup_only": "operational_delete_set_reuses_product_proof",
    "test_integrity": "never_weaken_for_green",
    "readiness_head": "exact_head_attestation",
    "re_review": "delta_and_unresolved_blockers_only",
    "stop_rule": "sufficient_exact_tip_evidence_not_absence_of_suggestions",
}

# ---------------------------------------------------------------------------
# Terminal outcomes (host-owned; independent of readiness)
# ---------------------------------------------------------------------------

TERMINAL_OUTCOMES: tuple[str, ...] = (
    "landable_pr",  # complete-without-merge
    "complete_and_merge",  # regular merge commit when driver authorized
    "blocked",
    "failed",
    "stopped",
)

# Independence invariants: readiness and authorization never imply each other.
INDEPENDENCE_INVARIANTS: tuple[str, ...] = (
    "ready_true_never_grants_merge_permission",
    "driver_authorized_true_never_proves_readiness",
    "merge_requires_ready_and_driver_authorized_at_same_exact_head",
    "worker_evidence_cannot_grant_merge_or_change_landing_outcome",
)

# ---------------------------------------------------------------------------
# Safety kernel with destinations and proving tests
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SafetyKernelRequirement:
    id: str
    summary: str
    destinations: tuple[str, ...]
    proving_tests: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


SAFETY_KERNEL: tuple[SafetyKernelRequirement, ...] = (
    SafetyKernelRequirement(
        id="exact_plan_session_packet_acceptance_identity",
        summary=(
            "Exact plan/session/packet acceptance identity including B0/B1 "
            "and bare/bracketed stable IDs"
        ),
        destinations=(
            "scripts/acceptance_contract.py",
            "scripts/elves_landing_check.py",
            "scripts/cobbler_runtime/full_run.py",
            "references/schema-and-acceptance.md",
        ),
        proving_tests=(
            "tests/test_acceptance_contract.py",
            "tests/test_elves_landing_check.py",
            "tests/test_full_run_supervisor.py",
        ),
    ),
    SafetyKernelRequirement(
        id="credential_origin_branch_worktree_ancestry_clean_tip_protected_ref_redaction",
        summary=(
            "Credential, origin, branch, worktree, ancestry, clean-tip, "
            "protected-ref, and redaction guards"
        ),
        destinations=(
            "scripts/cobbler_runtime/full_run.py",
            "scripts/cobbler_runtime/isolation.py",
            "scripts/cobbler_runtime/storage.py",
            "scripts/cobbler_runtime/delegated_git.py",
            "scripts/workspace_guard.py",
            "references/runtime-security.md",
        ),
        proving_tests=(
            "tests/test_full_run_supervisor.py",
            "tests/test_dispatch_isolation.py",
            "tests/test_storage_isolation_git.py",
            "tests/test_workspace_guard.py",
        ),
    ),
    SafetyKernelRequirement(
        id="no_worker_merge_tag_protected_ref_pr_landing_authority",
        summary=(
            "Worker merge/tag/protected-ref/PR/landing-policy authority is always false"
        ),
        destinations=(
            "scripts/cobbler_runtime/canonical_contract.py",
            "scripts/cobbler_runtime/landing_authority.py",
            "scripts/cobbler_runtime/behavior_policy.py",
            "scripts/cobbler_runtime/full_run.py",
            "references/landing-authority.md",
        ),
        proving_tests=(
            "tests/test_joyful_runs_contract.py",
            "tests/test_landing_authority.py",
            "tests/test_full_run_supervisor.py",
        ),
    ),
    SafetyKernelRequirement(
        id="test_integrity_constitution_exact_head_terminal_review_final_ci",
        summary=(
            "Test integrity, constitution compliance, exact-HEAD readiness, "
            "independent terminal review, and required final CI"
        ),
        destinations=(
            "scripts/cobbler_runtime/canonical_contract.py",
            "scripts/cobbler_runtime/landing_authority.py",
            "scripts/cobbler_runtime/evidence_review.py",
            "scripts/elves_landing_check.py",
            "scripts/verify_repo.py",
            "references/proof-and-review.md",
        ),
        proving_tests=(
            "tests/test_joyful_runs_contract.py",
            "tests/test_landing_authority.py",
            "tests/test_faster_goal_runs_policy.py",
            "tests/test_elves_landing_check.py",
            "tests/test_verify_repo.py",
        ),
    ),
    SafetyKernelRequirement(
        id="strict_detached_import_evidence_for_untrusted_writers",
        summary="Strict detached/import evidence for untrusted writers",
        destinations=(
            "scripts/cobbler_runtime/leases.py",
            "scripts/cobbler_runtime/delegated_git.py",
            "scripts/cobbler_runtime/risk_policy.py",
            "references/untrusted-writer.md",
        ),
        proving_tests=(
            "tests/test_cobbler_agents_leases.py",
            "tests/test_faster_goal_runs_policy.py",
        ),
    ),
    SafetyKernelRequirement(
        id="native_claude_code_and_codex_without_optional_providers",
        summary=(
            "Native Claude Code and Codex operation remains valid without "
            "Grok or optional providers"
        ),
        destinations=(
            "scripts/cobbler_runtime/behavior_policy.py",
            "scripts/cobbler_runtime/canonical_contract.py",
            "SKILL.md",
            "AGENTS.md",
            "references/host-parity.md",
        ),
        proving_tests=(
            "tests/test_cobbler_native_only_fallback.py",
            "tests/test_installed_bundle_smoke.py",
            "tests/test_joyful_runs_contract.py",
            "tests/test_check_repo_consistency.py",
        ),
    ),
)

# ---------------------------------------------------------------------------
# Migration ledger (2.2 → 2.3): retained | changed | retired
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MigrationEntry:
    id: str
    disposition: str  # retained | changed | retired
    old_location: str
    new_location: str
    summary: str
    proof: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


MIGRATION_LEDGER: tuple[MigrationEntry, ...] = (
    MigrationEntry(
        id="safety-kernel-six",
        disposition="retained",
        old_location="scripts/cobbler_runtime/risk_policy.py::SAFETY_KERNEL",
        new_location="scripts/cobbler_runtime/canonical_contract.py::SAFETY_KERNEL",
        summary="Six thin safety-kernel requirements preserved with destinations",
        proof="tests/test_joyful_runs_contract.py",
    ),
    MigrationEntry(
        id="four-risk-tiers",
        disposition="changed",
        old_location="risk_policy RISK_TIERS trivial_docs|standard_trusted|high_risk_trusted|untrusted",
        new_location="canonical_contract RISK_LEVELS × TRUST_MODES; legacy map retained",
        summary="Risk is low|standard|high; trust_mode is independently trusted|untrusted",
        proof="tests/test_joyful_runs_contract.py::RiskTrustIndependenceTests",
    ),
    MigrationEntry(
        id="parked-monitor",
        disposition="retained",
        old_location="behavior_policy PARKED_MONITOR_*",
        new_location="behavior_policy + canonical_contract DRIVER_WAKE_CONDITIONS",
        summary="Quiet parked driver; wakes only on deterministic conditions",
        proof="tests/test_joyful_runs_contract.py",
    ),
    MigrationEntry(
        id="default-follow-stream",
        disposition="changed",
        old_location="full-run-await blocks without live stream",
        new_location="full-run-await default follow mode; --quiet opt-out",
        summary="Default sanitized human-readable worker stream; no model inference",
        proof="tests/test_follow_mode.py",
    ),
    MigrationEntry(
        id="merge-authority-false",
        disposition="retained",
        old_location="full_run merge_authority always false in worker surfaces",
        new_location="landing_authority host-owned; worker cannot grant",
        summary="Worker evidence never grants merge or changes landing outcome",
        proof="tests/test_landing_authority.py",
    ),
    MigrationEntry(
        id="exact-head-readiness",
        disposition="changed",
        old_location="landing check on session tip",
        new_location="landing_authority exact HEAD attestation with invalidation scope",
        summary="Readiness attested to exact HEAD; changed inputs invalidate affected proof",
        proof="tests/test_landing_authority.py",
    ),
    MigrationEntry(
        id="complete-without-and-with-merge-pipeline",
        disposition="changed",
        old_location="chat-to-work vs chat-to-land narrative",
        new_location="one readiness pipeline; landing_outcome differs only at terminal",
        summary="Both terminal outcomes share implement/review/revision/readiness",
        proof="tests/test_landing_authority.py",
    ),
    MigrationEntry(
        id="active-run-land-pr",
        disposition="changed",
        old_location="Reviewed PR Landing Command as separate path",
        new_location="land-pr grants driver_authorized without restarting readiness",
        summary="Active-run land-pr is host grant only",
        proof="tests/test_landing_authority.py",
    ),
    MigrationEntry(
        id="per-batch-driver-review",
        disposition="retired",
        old_location="Core Loop steps 7–13 mid-run for trusted full-run",
        new_location="one cumulative terminal review + delta re-review",
        summary="Healthy trusted full-run has no per-batch driver review ceremony",
        proof="tests/test_joyful_runs_contract.py",
    ),
    MigrationEntry(
        id="equal-time-quotas",
        disposition="retired",
        old_location="equal thirds implement/validate/review",
        new_location="proof budget + impact-selected verification",
        summary="Time quotas remain retired; impact path selects proof",
        proof="tests/test_evidence_impact.py",
    ),
    MigrationEntry(
        id="native-host-parity",
        disposition="retained",
        old_location="SKILL.md + AGENTS.md dual long mirrors",
        new_location="SKILL.md canonical; AGENTS.md thin Codex adapter",
        summary="Claude Code and Codex share identical workflow semantics",
        proof="tests/test_check_repo_consistency.py",
    ),
    MigrationEntry(
        id="native-only-without-providers",
        disposition="retained",
        old_location="native fallback paths",
        new_location="behavior_policy + implement fallbacks unchanged in spirit",
        summary="Optional providers never required for overnight runs",
        proof="tests/test_cobbler_native_only_fallback.py",
    ),
    MigrationEntry(
        id="acceptance-b0-b1-stable-ids",
        disposition="retained",
        old_location="acceptance_contract.py",
        new_location="acceptance_contract.py (unchanged semantics)",
        summary="B0/B1 and bare/bracketed stable IDs remain equivalent",
        proof="tests/test_acceptance_contract.py",
    ),
    MigrationEntry(
        id="shared-oauth-transcript-restrictions",
        disposition="retained",
        old_location="full_run _driver_visible_events + raw-tail refusal",
        new_location="same surfaces; follow mode uses sanitized projection only",
        summary="Shared OAuth raw-transcript restrictions and redaction intact",
        proof="tests/test_full_run_supervisor.py",
    ),
    MigrationEntry(
        id="progress-commit-subjects",
        disposition="retained",
        old_location="risk_policy.progress_commit_subject_ok",
        new_location="risk_policy.progress_commit_subject_ok + operator docs",
        summary="Workers commit/push meaningful concrete subjects",
        proof="tests/test_faster_goal_runs_policy.py",
    ),
    MigrationEntry(
        id="native-grok-goal-capability",
        disposition="retained",
        old_location="implement.detect_native_grok_goal",
        new_location="implement.detect_native_grok_goal (unchanged honesty)",
        summary="Native Grok goal only when capability-proven; else honest fallback",
        proof="tests/test_faster_goal_runs_policy.py",
    ),
    MigrationEntry(
        id="cleanup-only-proof-reuse",
        disposition="retained",
        old_location="risk_policy.cleanup_only_reuse_allowed + preflight_cache",
        new_location="same + evidence_review impact path",
        summary="Cleanup-only operational deletes reuse product proof",
        proof="tests/test_faster_goal_runs_policy.py",
    ),
    MigrationEntry(
        id="convergent-delta-review",
        disposition="changed",
        old_location="full review loop until no suggestions",
        new_location="evidence_review convergent rules: consolidate blockers; delta re-review",
        summary="Loop stops on sufficient exact-tip evidence; advisory does not delay",
        proof="tests/test_evidence_impact.py",
    ),
    MigrationEntry(
        id="codex-goals-vs-grok-goal",
        disposition="retained",
        old_location="references/codex-goals.md",
        new_location="references/codex-goals.md + host-parity docs",
        summary="Codex continuation Goals distinct from Grok Build goal mode",
        proof="tests/test_check_repo_consistency.py",
    ),
    MigrationEntry(
        id="optional-features-off-critical-path",
        disposition="retained",
        old_location="reports, providers, media, legacy bounded, untrusted",
        new_location="documented as optional outside normal critical path",
        summary="Useful optional surfaces stay non-default for happy path",
        proof="tests/test_joyful_runs_contract.py",
    ),
    MigrationEntry(
        id="finite-open-ended-stop-gate",
        disposition="retained",
        old_location="Run Mode, Stop Gate, continuation_guard",
        new_location="SKILL.md run control + session continuation_guard",
        summary="Finite/open-ended semantics and positive stop permission remain durable",
        proof="tests/test_goal_policy.py",
    ),
    MigrationEntry(
        id="run-memory-and-strategic-forgetting",
        disposition="retained",
        old_location="Plan, Survival Guide, execution log, learnings, strategic forgetting",
        new_location="SKILL.md memory contract + focused templates",
        summary="Canonical run memory and concise reactivation handoffs remain host-owned",
        proof="tests/test_check_repo_consistency.py",
    ),
    MigrationEntry(
        id="standalone-worker-packet",
        disposition="retained",
        old_location="Coordinator-to-Implementer Handoff Standard",
        new_location="SKILL.md worker contract + joyful-runs-contract reference",
        summary="Intent, rationale, Build On, owned/forbidden surfaces, proof, pitfalls, and identity remain required",
        proof="tests/test_worker_packet_contract.py",
    ),
    MigrationEntry(
        id="untrusted-detached-import-boundary",
        disposition="retained",
        old_location="leases + delegated_git detached writer path",
        new_location="same runtime + trust-aware canonical authority",
        summary="Untrusted writers never own feature refs, remotes, pushes, PRs, or landing",
        proof="tests/test_cobbler_agents_leases.py",
    ),
    MigrationEntry(
        id="exact-session-resume",
        disposition="retained",
        old_location="exact persistent session registry and full-run resume",
        new_location="full_run authenticated exact-session resume",
        summary="No ambiguous latest/continue resume token is introduced",
        proof="tests/test_full_run_supervisor.py",
    ),
    MigrationEntry(
        id="regular-merge-commit-and-user-authority",
        disposition="retained",
        old_location="default no-merge + Reviewed PR Landing Command",
        new_location="landing_authority + SKILL landing contract",
        summary="User owns merge permission; authorized landing uses a regular merge commit only",
        proof="tests/test_landing_authority.py",
    ),
    MigrationEntry(
        id="report-reconstruction",
        disposition="changed",
        old_location="worker-supplied Elves report required at closeout",
        new_location="host reconstruction from exact trusted commits/events/tests",
        summary="Missing report shape never discards otherwise provable worker work",
        proof="tests/test_full_run_supervisor.py",
    ),
    MigrationEntry(
        id="constitution-and-completeness-review",
        disposition="changed",
        old_location="legality judge and review during batch loop",
        new_location="one cumulative final review; checkpoint only for declared high risk",
        summary="Completeness and constitution remain required without routine mid-run review",
        proof="tests/test_evidence_impact.py",
    ),
    MigrationEntry(
        id="pr-feedback-and-required-checks",
        disposition="changed",
        old_location="poll comments and checks after every host push",
        new_location="terminal feedback read plus final-tip required-check wait",
        summary="All review surfaces remain required without waking a healthy worker per push",
        proof="tests/test_behavior_policy.py",
    ),
    MigrationEntry(
        id="release-docs-and-installed-parity",
        disposition="retained",
        old_location="version, changelog, README, installed Claude/Codex bundle checks",
        new_location="release/consistency checks and host-parity contract",
        summary="Release metadata and both installed hosts must agree",
        proof="tests/test_installed_bundle_smoke.py",
    ),
)

# Explicit v2.2 normative inventory. Adding/removing a migration entry without
# updating this list is a test-visible contract change, preventing silent loss.
V2_2_NORMATIVE_REQUIREMENT_IDS: tuple[str, ...] = (
    "safety-kernel-six",
    "four-risk-tiers",
    "parked-monitor",
    "default-follow-stream",
    "merge-authority-false",
    "exact-head-readiness",
    "complete-without-and-with-merge-pipeline",
    "active-run-land-pr",
    "per-batch-driver-review",
    "equal-time-quotas",
    "native-host-parity",
    "native-only-without-providers",
    "acceptance-b0-b1-stable-ids",
    "shared-oauth-transcript-restrictions",
    "progress-commit-subjects",
    "native-grok-goal-capability",
    "cleanup-only-proof-reuse",
    "convergent-delta-review",
    "codex-goals-vs-grok-goal",
    "optional-features-off-critical-path",
    "finite-open-ended-stop-gate",
    "run-memory-and-strategic-forgetting",
    "standalone-worker-packet",
    "untrusted-detached-import-boundary",
    "exact-session-resume",
    "regular-merge-commit-and-user-authority",
    "report-reconstruction",
    "constitution-and-completeness-review",
    "pr-feedback-and-required-checks",
    "release-docs-and-installed-parity",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def safety_kernel_ids() -> tuple[str, ...]:
    return tuple(item.id for item in SAFETY_KERNEL)


def safety_kernel_snapshot() -> dict[str, Any]:
    return {
        "policy_version": POLICY_VERSION,
        "safety_kernel": [item.to_dict() for item in SAFETY_KERNEL],
        "risk_levels": list(RISK_LEVELS),
        "trust_modes": list(TRUST_MODES),
        "run_states": list(RUN_STATES),
        "terminal_outcomes": list(TERMINAL_OUTCOMES),
        "independence_invariants": list(INDEPENDENCE_INVARIANTS),
        "proof_budget": PROOF_BUDGET_SLOGAN,
        "driver_wake_conditions": sorted(DRIVER_WAKE_CONDITIONS),
        "forbidden_wake_triggers": sorted(FORBIDDEN_WAKE_TRIGGERS),
    }


def migration_ledger_snapshot() -> dict[str, Any]:
    return {
        "policy_version": POLICY_VERSION,
        "entries": [item.to_dict() for item in MIGRATION_LEDGER],
        "counts": {
            "retained": sum(1 for e in MIGRATION_LEDGER if e.disposition == "retained"),
            "changed": sum(1 for e in MIGRATION_LEDGER if e.disposition == "changed"),
            "retired": sum(1 for e in MIGRATION_LEDGER if e.disposition == "retired"),
        },
    }


def actor_may(
    actor: str,
    capability: str,
    *,
    trust_mode: str = "trusted",
) -> bool:
    if actor == "worker" and capability in WORKER_FORBIDDEN_CAPABILITIES:
        return False
    if actor == "worker":
        normalized_trust = normalize_trust_mode(trust_mode)
        if (
            normalized_trust == "untrusted"
            and capability in UNTRUSTED_WORKER_FORBIDDEN_CAPABILITIES
        ):
            return False
    allowed = AUTHORITY_MATRIX.get(capability)
    if allowed is None:
        return False
    return actor in allowed


def transition_allowed(current: str, nxt: str) -> bool:
    if current not in STATE_TRANSITIONS:
        return False
    return nxt in STATE_TRANSITIONS[current]


def normalize_risk(raw: str | None) -> str:
    text = (raw or "standard").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "low": "low",
        "trivial": "low",
        "trivial_docs": "low",
        "docs": "low",
        "standard": "standard",
        "medium": "standard",
        "standard_trusted": "standard",
        "high": "high",
        "high_risk": "high",
        "high_risk_trusted": "high",
    }
    if text not in aliases:
        raise ValueError(f"unknown risk level: {raw!r}")
    return aliases[text]


def normalize_trust_mode(raw: str | None) -> str:
    text = (raw or "trusted").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "trusted": "trusted",
        "untrusted": "untrusted",
        "standard_trusted": "trusted",
        "high_risk_trusted": "trusted",
        "trivial_docs": "trusted",
    }
    if text not in aliases:
        raise ValueError(f"unknown trust mode: {raw!r}")
    return aliases[text]


def classify_risk_and_trust(
    *,
    risk: str | None = None,
    trust_mode: str | None = None,
    legacy_tier: str | None = None,
    is_untrusted_writer: bool = False,
    is_high_risk_checkpoint: bool = False,
    is_final_readiness: bool = False,
    batch_blast_radius: str | None = None,
    changed_paths: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Resolve independent risk and trust_mode, with legacy-tier compatibility."""
    if legacy_tier:
        if legacy_tier not in LEGACY_TIER_TO_RISK_TRUST:
            raise ValueError(f"unknown legacy tier: {legacy_tier!r}")
        risk_level, trust = LEGACY_TIER_TO_RISK_TRUST[legacy_tier]
        return {
            "risk": risk_level,
            "trust_mode": trust,
            "legacy_tier": legacy_tier,
            "source": "legacy_tier",
        }

    trust = "untrusted" if is_untrusted_writer else normalize_trust_mode(trust_mode)
    if risk is not None:
        risk_level = normalize_risk(risk)
    elif is_final_readiness or is_high_risk_checkpoint or batch_blast_radius == "high":
        risk_level = "high"
    else:
        paths = [p.replace("\\", "/") for p in (changed_paths or ())]
        docs_only = bool(paths) and all(
            p.lower().endswith((".md", ".rst", ".adoc", ".txt"))
            or p.startswith("docs/")
            or p.startswith("references/")
            for p in paths
        )
        if docs_only:
            risk_level = "low"
        elif batch_blast_radius == "medium":
            risk_level = "standard"
        else:
            risk_level = "standard"

    legacy = RISK_TRUST_TO_LEGACY_TIER[(risk_level, trust)]
    return {
        "risk": risk_level,
        "trust_mode": trust,
        "legacy_tier": legacy,
        "source": "axes",
    }


def broad_proof_required(
    *,
    risk: str,
    trust_mode: str,
    is_final_readiness: bool = False,
    is_high_risk_checkpoint: bool = False,
) -> bool:
    if is_final_readiness or is_high_risk_checkpoint:
        return True
    if trust_mode == "untrusted":
        return True
    return normalize_risk(risk) == "high"


def hosts_share_semantics() -> dict[str, Any]:
    """Claude Code and Codex share workflow semantics; only invocation surface differs."""
    return {
        "workflow_semantics_identical": True,
        "hosts": ("claude_code", "codex"),
        "invocation_surface_differs": True,
        "invocation": {
            "claude_code": "slash skills and managed aliases",
            "codex": "$elves skill forms and natural language; no invented top-level slashes",
        },
        "canonical_docs": {
            "workflow": "SKILL.md",
            "codex_adapter": "AGENTS.md",
            "operator": "README.md",
        },
        "optional_providers_required": False,
        "grok_required": False,
    }


def contract_snapshot() -> dict[str, Any]:
    return {
        "policy_version": POLICY_VERSION,
        "run_states": list(RUN_STATES),
        "state_transitions": {
            k: sorted(v) for k, v in STATE_TRANSITIONS.items()
        },
        "actors": list(ACTORS),
        "authority_matrix": {
            k: sorted(v) for k, v in AUTHORITY_MATRIX.items()
        },
        "worker_forbidden": sorted(WORKER_FORBIDDEN_CAPABILITIES),
        "risk_levels": list(RISK_LEVELS),
        "trust_modes": list(TRUST_MODES),
        "legacy_tier_map": dict(LEGACY_TIER_TO_RISK_TRUST),
        "driver_wake_conditions": sorted(DRIVER_WAKE_CONDITIONS),
        "forbidden_wake_triggers": sorted(FORBIDDEN_WAKE_TRIGGERS),
        "proof_rules": dict(PROOF_RULES),
        "proof_budget": PROOF_BUDGET_SLOGAN,
        "terminal_outcomes": list(TERMINAL_OUTCOMES),
        "independence_invariants": list(INDEPENDENCE_INVARIANTS),
        "safety_kernel": safety_kernel_snapshot(),
        "migration_ledger": migration_ledger_snapshot(),
        "host_parity": hosts_share_semantics(),
    }
