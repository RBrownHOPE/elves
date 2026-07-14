"""Risk-tiered execution policy for Elves 2.2 faster trusted runs.

Pure helpers (no process I/O) for:
- thin safety kernel inventory
- four risk tiers (trivial/docs, standard trusted, high-risk trusted, untrusted)
- proof budget (validate once / verify changes / attest final)
- mid-run vs terminal PR feedback
- bug-category expansion bounds
- gate input digests and cleanup-only reuse eligibility
- host reconstruction provenance constraints
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence


POLICY_VERSION = "2.2.0"

# Thin safety kernel — must not weaken. Operator docs and guards pin these names.
SAFETY_KERNEL: tuple[str, ...] = (
    "exact_plan_session_packet_acceptance_identity",
    "credential_protected_ref_origin_branch_worktree_ancestry_clean_tip",
    "explicit_host_ack_high_risk_checkpoints",
    "no_worker_merge_or_protected_ref_authority",
    "test_integrity_one_live_broad_proof_independent_terminal_review_required_final_ci",
    "strict_detached_import_evidence_for_untrusted_writers",
)

RISK_TIERS: tuple[str, ...] = (
    "trivial_docs",
    "standard_trusted",
    "high_risk_trusted",
    "untrusted",
)

PROOF_BUDGET_SLOGAN = "validate once, verify changes, attest final"

# Mid-run PR feedback is nonblocking; terminal readiness waits for required checks.
PR_FEEDBACK_MID_RUN = "nonblocking_new_unresolved_only"
PR_FEEDBACK_TERMINAL = "wait_required_checks_and_reviewers"

# Reconstruction may only derive independently provable fields.
WORKER_ONLY_CLAIM_KEYS: frozenset[str] = frozenset(
    {
        "worker_internal_notes",
        "worker_private_rationale",
        "untrusted_audit_handoff",
        "lease_audit_chain",
        "secret_values",
        "credential_material",
    }
)

RECONSTRUCTABLE_FIELDS: frozenset[str] = frozenset(
    {
        "run_id",
        "session_id",
        "branch",
        "start_head",
        "final_head",
        "status",
        "commits",
        "acceptance",
        "batches",
        "provenance",
        "merge_authority",
        "docs_changed",
        "tests",
        "security_notes",
        "blockers",
    }
)

# Paths treated as operational/run-metadata only for cleanup reuse.
OPERATIONAL_PATH_PREFIXES: tuple[str, ...] = (
    ".elves-session.json",
    "docs/elves/",
    ".elves/runtime/",
)

# Paths that invalidate runtime/security gate evidence when they change.
RUNTIME_SECURITY_PATH_MARKERS: tuple[str, ...] = (
    "scripts/",
    "tests/",
    ".github/workflows/",
    "pyproject.toml",
    "requirements",
    "package.json",
    "package-lock.json",
    "Cargo.toml",
    "go.mod",
    "auth",
    "credential",
    "secret",
    "isolation",
    "lease",
)


@dataclass(frozen=True)
class RiskTierDecision:
    tier: str
    broad_proof_required: bool
    proof_mode: str  # touched | checkpoint_broad | terminal_broad
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BugCategoryDisposition:
    blocking: tuple[str, ...]
    advisory: tuple[str, ...]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PrFeedbackPolicy:
    phase: str  # mid_run | terminal
    mode: str
    wait_for_required_checks: bool
    fetch_new_unresolved_only: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GateReuseDecision:
    reuse: bool
    reason: str
    input_digest: str
    final_readiness_accepts_cache_alone: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReconstructionPlan:
    allowed: bool
    provenance: str
    fields: tuple[str, ...]
    unknown_fields: tuple[str, ...]
    refused_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def safety_kernel_snapshot() -> dict[str, Any]:
    return {
        "policy_version": POLICY_VERSION,
        "safety_kernel": list(SAFETY_KERNEL),
        "risk_tiers": list(RISK_TIERS),
        "proof_budget": PROOF_BUDGET_SLOGAN,
        "pr_feedback": {
            "mid_run": PR_FEEDBACK_MID_RUN,
            "terminal": PR_FEEDBACK_TERMINAL,
        },
    }


def classify_risk_tier(
    *,
    changed_paths: Sequence[str] | None = None,
    is_final_readiness: bool = False,
    is_high_risk_checkpoint: bool = False,
    is_untrusted_writer: bool = False,
    batch_blast_radius: str | None = None,
    risk_hints: Sequence[str] | None = None,
) -> RiskTierDecision:
    """Classify the four-tier model from change surface and route signals."""
    paths = [p.replace("\\", "/") for p in (changed_paths or ())]
    hints = [h.lower() for h in (risk_hints or ())]
    reasons: list[str] = []

    if is_untrusted_writer or "untrusted" in hints:
        return RiskTierDecision(
            tier="untrusted",
            broad_proof_required=True,
            proof_mode="terminal_broad",
            reasons=("untrusted_writer_requires_strict_detached_import_evidence",),
        )

    if is_final_readiness:
        return RiskTierDecision(
            tier="high_risk_trusted" if is_high_risk_checkpoint else "standard_trusted",
            broad_proof_required=True,
            proof_mode="terminal_broad",
            reasons=("terminal_readiness_requires_broad_proof",),
        )

    if is_high_risk_checkpoint or batch_blast_radius == "high" or "high-risk" in hints:
        return RiskTierDecision(
            tier="high_risk_trusted",
            broad_proof_required=True,
            proof_mode="checkpoint_broad",
            reasons=("high_risk_checkpoint_or_blast_radius",),
        )

    docs_only = bool(paths) and all(
        p.lower().endswith((".md", ".rst", ".adoc", ".txt"))
        or p.startswith("docs/")
        or p.startswith("references/")
        for p in paths
    )
    if docs_only or (not paths and "docs" in hints):
        return RiskTierDecision(
            tier="trivial_docs",
            broad_proof_required=False,
            proof_mode="touched",
            reasons=("docs_or_trivial_surface",),
        )

    if batch_blast_radius == "medium" or any(
        any(m in p.lower() for m in RUNTIME_SECURITY_PATH_MARKERS) for p in paths
    ):
        reasons.append("runtime_or_medium_blast")
        return RiskTierDecision(
            tier="standard_trusted",
            broad_proof_required=False,
            proof_mode="touched",
            reasons=tuple(reasons) or ("standard_trusted_default",),
        )

    return RiskTierDecision(
        tier="standard_trusted",
        broad_proof_required=False,
        proof_mode="touched",
        reasons=("standard_trusted_default",),
    )


def proof_budget_for_tier(tier: str, *, is_final_readiness: bool = False) -> dict[str, Any]:
    """Return proof defaults: touched per batch; broad at checkpoints/terminal."""
    if is_final_readiness or tier in {"high_risk_trusted", "untrusted"}:
        return {
            "slogan": PROOF_BUDGET_SLOGAN,
            "per_batch_default": "touched_surfaces",
            "broad_required_now": True,
            "broad_triggers": ["risk_checkpoint", "terminal_readiness"],
        }
    return {
        "slogan": PROOF_BUDGET_SLOGAN,
        "per_batch_default": "touched_surfaces",
        "broad_required_now": False,
        "broad_triggers": ["risk_checkpoint", "terminal_readiness"],
    }


def pr_feedback_policy(*, is_terminal_readiness: bool) -> PrFeedbackPolicy:
    if is_terminal_readiness:
        return PrFeedbackPolicy(
            phase="terminal",
            mode=PR_FEEDBACK_TERMINAL,
            wait_for_required_checks=True,
            fetch_new_unresolved_only=False,
        )
    return PrFeedbackPolicy(
        phase="mid_run",
        mode=PR_FEEDBACK_MID_RUN,
        wait_for_required_checks=False,
        fetch_new_unresolved_only=True,
    )


def dispose_bug_category_findings(
    findings: Sequence[Mapping[str, Any]],
    *,
    owned_or_affected_paths: Sequence[str],
) -> BugCategoryDisposition:
    """Block only confirmed same-root failures on owned/affected shared surfaces.

    Unrelated sibling findings become advisory follow-up work.
    """
    owned = {p.replace("\\", "/") for p in owned_or_affected_paths}
    blocking: list[str] = []
    advisory: list[str] = []
    reasons: list[str] = []
    for item in findings:
        finding_id = str(item.get("id") or item.get("summary") or "finding")
        confirmed = bool(item.get("confirmed_same_root"))
        paths = [
            str(p).replace("\\", "/")
            for p in (item.get("paths") or item.get("surfaces") or ())
        ]
        on_owned = (not paths) or any(
            p in owned or any(p.startswith(o.rstrip("/") + "/") for o in owned)
            for p in paths
        )
        if confirmed and on_owned:
            blocking.append(finding_id)
        else:
            advisory.append(finding_id)
            if not confirmed:
                reasons.append(f"{finding_id}:not_confirmed_same_root")
            elif not on_owned:
                reasons.append(f"{finding_id}:outside_owned_or_affected")
    return BugCategoryDisposition(
        blocking=tuple(blocking),
        advisory=tuple(advisory),
        reasons=tuple(reasons),
    )


def compute_gate_input_digest(
    *,
    relevant_paths: Mapping[str, str] | None = None,
    command: str | None = None,
    dependency_digest: str | None = None,
    runtime_identity: Mapping[str, Any] | None = None,
    material_env: Mapping[str, str] | None = None,
) -> str:
    """Digest gate inputs independent of full HEAD when docs-only commits land."""
    payload = {
        "paths": dict(sorted((relevant_paths or {}).items())),
        "command": command or "",
        "dependency_digest": dependency_digest or "",
        "runtime_identity": dict(sorted((runtime_identity or {}).items())),
        "material_env": dict(sorted((material_env or {}).items())),
    }
    material = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def gate_reuse_decision(
    *,
    cached_digest: str | None,
    current_digest: str,
    cached_status: str | None = "pass",
) -> GateReuseDecision:
    if not cached_digest:
        return GateReuseDecision(
            reuse=False,
            reason="no_cache",
            input_digest=current_digest,
        )
    if cached_status != "pass":
        return GateReuseDecision(
            reuse=False,
            reason="cached_not_pass",
            input_digest=current_digest,
        )
    if cached_digest != current_digest:
        return GateReuseDecision(
            reuse=False,
            reason="input_digest_mismatch",
            input_digest=current_digest,
        )
    return GateReuseDecision(
        reuse=True,
        reason="identical_input_digest",
        input_digest=current_digest,
        final_readiness_accepts_cache_alone=False,
    )


def paths_invalidate_runtime_proof(changed_paths: Sequence[str]) -> bool:
    for path in changed_paths:
        lower = path.replace("\\", "/").lower()
        if any(marker in lower for marker in RUNTIME_SECURITY_PATH_MARKERS):
            return True
        if lower.endswith((".py", ".sh", ".yml", ".yaml", ".toml", ".lock")):
            if lower.startswith("docs/") or lower.startswith("references/"):
                continue
            return True
    return False


def is_cleanup_only_diff(
    changed_paths: Sequence[str],
    *,
    recorded_operational_paths: Sequence[str],
) -> bool:
    """True when the tip diff deletes exactly the recorded operational set."""
    changed = {p.replace("\\", "/") for p in changed_paths}
    recorded = {p.replace("\\", "/") for p in recorded_operational_paths}
    if not changed or not recorded:
        return False
    if changed != recorded:
        return False
    return all(
        any(
            path == prefix.rstrip("/") or path.startswith(prefix)
            for prefix in OPERATIONAL_PATH_PREFIXES
        )
        for path in changed
    )


def cleanup_only_reuse_allowed(
    *,
    parent_tip: str,
    proven_tip: str,
    changed_paths: Sequence[str],
    recorded_operational_paths: Sequence[str],
    product_test_input_digest_unchanged: bool,
) -> dict[str, Any]:
    if parent_tip != proven_tip:
        return {
            "reuse": False,
            "reason": "cleanup_parent_is_not_proven_tip",
            "force_live_proof": True,
        }
    if not is_cleanup_only_diff(
        changed_paths, recorded_operational_paths=recorded_operational_paths
    ):
        return {
            "reuse": False,
            "reason": "non_operational_or_incomplete_delete_set",
            "force_live_proof": True,
        }
    if not product_test_input_digest_unchanged:
        return {
            "reuse": False,
            "reason": "product_test_input_digest_changed",
            "force_live_proof": True,
        }
    return {
        "reuse": True,
        "reason": "cleanup_only_operational_delete_set",
        "force_live_proof": False,
    }


def plan_host_reconstruction(
    *,
    clean_exit: bool,
    ancestry_ok: bool,
    clean_worktree: bool,
    protected_refs_ok: bool,
    origin_ok: bool,
    acceptance_bound: bool,
    checkpoints_satisfied: bool,
    host_tests_pass: bool,
    untrusted_writer: bool = False,
    missing_security_evidence: bool = False,
    available_facts: Mapping[str, Any] | None = None,
) -> ReconstructionPlan:
    """Host may reconstruct only independently provable trusted fields."""
    refused: list[str] = []
    if untrusted_writer:
        refused.append("untrusted_writer_handoffs_are_never_reconstructed")
    if missing_security_evidence:
        refused.append("missing_security_or_audit_evidence")
    if not clean_exit:
        refused.append("provider_exit_not_clean")
    if not ancestry_ok:
        refused.append("ancestry_not_proven")
    if not clean_worktree:
        refused.append("worktree_not_clean")
    if not protected_refs_ok:
        refused.append("protected_refs_not_ok")
    if not origin_ok:
        refused.append("origin_not_ok")
    if not acceptance_bound:
        refused.append("acceptance_identity_not_bound")
    if not checkpoints_satisfied:
        refused.append("checkpoints_not_satisfied")
    if not host_tests_pass:
        refused.append("host_tests_not_green")

    if refused:
        return ReconstructionPlan(
            allowed=False,
            provenance="host_reconstructed",
            fields=(),
            unknown_fields=tuple(sorted(WORKER_ONLY_CLAIM_KEYS)),
            refused_reasons=tuple(refused),
        )

    facts = dict(available_facts or {})
    fields: list[str] = []
    unknown: list[str] = sorted(WORKER_ONLY_CLAIM_KEYS)
    for key in sorted(RECONSTRUCTABLE_FIELDS):
        if key in facts and key not in WORKER_ONLY_CLAIM_KEYS:
            fields.append(key)
        elif key not in facts and key not in ("provenance", "merge_authority"):
            # leave worker-only and unknown empty; still allow provenance injection
            pass
    # Always inject provenance and deny merge authority.
    if "provenance" not in fields:
        fields.append("provenance")
    if "merge_authority" not in fields:
        fields.append("merge_authority")
    return ReconstructionPlan(
        allowed=True,
        provenance="host_reconstructed",
        fields=tuple(fields),
        unknown_fields=tuple(unknown),
        refused_reasons=(),
    )


def build_reconstructed_report(
    plan: ReconstructionPlan,
    *,
    facts: Mapping[str, Any],
) -> dict[str, Any]:
    """Materialize a reconstructed report or raise ValueError if refused."""
    if not plan.allowed:
        raise ValueError(
            "reconstruction refused: " + "; ".join(plan.refused_reasons)
        )
    report: dict[str, Any] = {}
    for key in plan.fields:
        if key == "provenance":
            report["provenance"] = "host_reconstructed"
            continue
        if key == "merge_authority":
            report["merge_authority"] = False
            continue
        if key in WORKER_ONLY_CLAIM_KEYS:
            continue
        if key in facts:
            report[key] = facts[key]
    # Never invent worker-only claims.
    for forbidden in WORKER_ONLY_CLAIM_KEYS:
        report.pop(forbidden, None)
    report["provenance"] = "host_reconstructed"
    report["merge_authority"] = False
    return report


def progress_commit_subject_ok(subject: str) -> bool:
    """Trusted workers must use concrete progress subjects, not vague WIP."""
    text = (subject or "").strip()
    if not text:
        return False
    lower = text.lower()
    vague = (
        "updates",
        "progress",
        "wip",
        "fixes",
        "more changes",
        "working on",
        "continue implementation",
    )
    # Bare vague words or trailing-only vague outcomes fail.
    body = text
    if "]" in text:
        body = text.split("]", 1)[-1].strip()
    body_l = body.lower().lstrip("· ").strip()
    if body_l in vague or body_l in {f"· {v}" for v in vague}:
        return False
    if any(body_l == v or body_l.endswith(f" {v}") and len(body_l) < len(v) + 8 for v in vague):
        # Explicit anti-patterns from operator docs.
        if body_l in vague:
            return False
    for v in vague:
        if body_l == v:
            return False
    # Require a concrete outcome longer than a single vague token.
    if len(body_l) < 8:
        return False
    return True


def monitor_depth_for_status(
    *,
    status: str,
    next_action: str | None,
    force_full: bool = False,
    remote_audit_due: bool = False,
) -> str:
    """Return 'incremental' or 'full' reconciliation depth for a monitor tick."""
    if force_full:
        return "full"
    terminal_or_safety = status in {
        "complete",
        "failed",
        "blocked",
        "stopped",
        "stale",
    } or (next_action or "").startswith("driver_wake_")
    if terminal_or_safety:
        return "full"
    if remote_audit_due:
        return "full"
    return "incremental"
