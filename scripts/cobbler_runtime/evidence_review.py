"""Risk-directed proof and convergent review selection (Elves 2.3).

Impact path: changed surface → affected consumer → selected test.
Evidence records inputs and invalidation scope so unchanged proof is reused.
Convergent terminal review consolidates blockers; re-review is delta-only.
Advisory findings never delay readiness. Cleanup-only operational changes
do not invalidate product proof.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from .risk_policy import (
    OPERATIONAL_PATH_PREFIXES,
    RUNTIME_SECURITY_PATH_MARKERS,
    classify_risk_tier,
    is_cleanup_only_diff,
    proof_budget_for_tier,
)


SECURITY_MARKERS: tuple[str, ...] = (
    "secret",
    "redact",
    "credential",
    "token",
    "isolation",
    "sandbox",
)

PUBLIC_INTERFACE_MARKERS: tuple[str, ...] = (
    "SKILL.md",
    "AGENTS.md",
    "README.md",
    "config.json.example",
    "scripts/cobbler_agents.py",
    "api-break-approvals.json",
)

# Categories that admit a *new* re-review blocker (B3-A6).
NEW_BLOCKER_CATEGORIES: frozenset[str] = frozenset(
    {
        "serious_regression",
        "acceptance_breach",
        "constitution_breach",
        "security",
        "data_integrity",
        "revision_introduced_failure",
    }
)

CUMULATIVE_REVIEW_CHECKS: tuple[str, ...] = (
    "completeness_vs_plan_acceptance",
    "constitution_compliance",
    "declared_risks",
    "concrete_regressions",
)


@dataclass(frozen=True)
class ReviewPlan:
    focused_checks: tuple[str, ...]
    broad_gate_required: bool
    reasons: tuple[str, ...]
    skipped: tuple[str, ...] = ()
    risk_level: str = "low"
    impact_path: tuple[dict[str, Any], ...] = ()
    evidence_inputs_digest: str | None = None
    invalidation_scopes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceRecord:
    """Proof with recorded inputs so unchanged work can be reused."""

    gate_id: str
    status: str  # pass | fail | skipped
    inputs_digest: str
    invalidation_scope: tuple[str, ...]
    head: str | None = None
    selected_tests: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ConvergentReviewPlan:
    mode: str  # cumulative | delta_rereview | stop
    checks: tuple[str, ...]
    blocking: tuple[str, ...]
    advisory: tuple[str, ...]
    last_reviewed_sha: str | None
    target_sha: str | None
    stop: bool
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _paths(changed_paths: Sequence[str]) -> list[str]:
    return [p.replace("\\", "/") for p in changed_paths]


def _digest(payload: Mapping[str, Any]) -> str:
    material = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def map_surface_to_consumers(path: str) -> tuple[str, ...]:
    """Conservative static consumer map for impact selection."""
    p = path.replace("\\", "/")
    consumers: list[str] = []
    if p.startswith("scripts/cobbler_runtime/"):
        consumers.extend(
            [
                "scripts/cobbler_agents.py",
                "tests/test_full_run_supervisor.py",
                "tests/test_faster_goal_runs_policy.py",
                "tests/test_joyful_runs_contract.py",
            ]
        )
    if p.startswith("scripts/") and p.endswith(".py"):
        name = p.rsplit("/", 1)[-1]
        stem = name[:-3] if name.endswith(".py") else name
        consumers.append(f"tests/test_{stem}.py")
    if p in {"SKILL.md", "AGENTS.md", "README.md"}:
        consumers.extend(
            [
                "scripts/consistency_policy.py",
                "tests/test_check_repo_consistency.py",
            ]
        )
    if p.startswith("references/") or p.startswith("docs/"):
        consumers.append("tests/test_check_repo_consistency.py")
    if p.startswith("tests/"):
        consumers.append(p)
    if any(m in p.lower() for m in SECURITY_MARKERS):
        consumers.extend(
            [
                "tests/test_full_run_supervisor.py",
                "tests/test_dispatch_isolation.py",
                "tests/test_storage_isolation_git.py",
            ]
        )
    # Always include the path itself as a surface under test.
    consumers.append(p)
    # Dedup preserve order
    seen: set[str] = set()
    ordered: list[str] = []
    for c in consumers:
        if c not in seen:
            seen.add(c)
            ordered.append(c)
    return tuple(ordered)


def select_tests_for_consumers(consumers: Sequence[str]) -> tuple[str, ...]:
    tests: list[str] = []
    for c in consumers:
        if c.startswith("tests/") and c.endswith(".py"):
            tests.append(c)
        elif c.endswith(".py") and c.startswith("scripts/"):
            stem = c.rsplit("/", 1)[-1][:-3]
            tests.append(f"tests/test_{stem}.py")
    seen: set[str] = set()
    ordered: list[str] = []
    for t in tests:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    if not ordered:
        ordered = ["unit:focused"]
    return tuple(ordered)


def build_impact_path(changed_paths: Sequence[str]) -> tuple[dict[str, Any], ...]:
    """Explicit changed-surface → affected-consumer → selected-test path."""
    rows: list[dict[str, Any]] = []
    for path in _paths(changed_paths):
        consumers = map_surface_to_consumers(path)
        tests = select_tests_for_consumers(consumers)
        rows.append(
            {
                "changed_surface": path,
                "affected_consumers": list(consumers),
                "selected_tests": list(tests),
            }
        )
    return tuple(rows)


def invalidation_scopes_for_paths(changed_paths: Sequence[str]) -> tuple[str, ...]:
    scopes: set[str] = set()
    paths = _paths(changed_paths)
    if not paths:
        return ()
    if is_cleanup_only_diff(
        paths,
        recorded_operational_paths=paths,
    ) or all(
        any(
            path == prefix.rstrip("/") or path.startswith(prefix)
            for prefix in OPERATIONAL_PATH_PREFIXES
        )
        or path in {".elves-session.json"}
        for path in paths
    ):
        # Pure operational paths: do not invalidate product proof.
        return ("operational_metadata",)

    for p in paths:
        lower = p.lower()
        if any(m in lower for m in SECURITY_MARKERS) or any(
            m in lower for m in RUNTIME_SECURITY_PATH_MARKERS
        ):
            scopes.update({"runtime", "security", "tests"})
        if p.startswith("scripts/"):
            scopes.update({"runtime", "tests"})
        if p.startswith("tests/"):
            scopes.add("tests")
        if p.startswith(".github/"):
            scopes.add("ci")
        if p.endswith((".md", ".rst", ".adoc")):
            scopes.add("docs")
        if any(p == m or p.endswith("/" + m) for m in PUBLIC_INTERFACE_MARKERS):
            scopes.add("public_interface")
        if "migration" in lower or "requirements" in lower or lower.endswith(
            (".lock", "go.mod", "cargo.toml", "package.json")
        ):
            scopes.update({"dependency", "runtime"})
        if not scopes:
            scopes.add("product")
    return tuple(sorted(scopes))


def record_evidence(
    *,
    gate_id: str,
    status: str,
    changed_paths: Sequence[str],
    selected_tests: Sequence[str] | None = None,
    head: str | None = None,
    command: str | None = None,
    extra_inputs: Mapping[str, Any] | None = None,
) -> EvidenceRecord:
    scopes = invalidation_scopes_for_paths(changed_paths)
    inputs = {
        "gate_id": gate_id,
        "paths": sorted(_paths(changed_paths)),
        "tests": list(selected_tests or ()),
        "command": command or "",
        "extra": dict(sorted((extra_inputs or {}).items())),
        "scopes": list(scopes),
    }
    return EvidenceRecord(
        gate_id=gate_id,
        status=status,
        inputs_digest=_digest(inputs),
        invalidation_scope=scopes,
        head=head,
        selected_tests=tuple(selected_tests or ()),
        notes=("impact_path_recorded",),
    )


def can_reuse_evidence(
    record: EvidenceRecord,
    *,
    current_digest: str,
    current_head: str | None = None,
    require_same_head: bool = False,
) -> dict[str, Any]:
    if record.status != "pass":
        return {"reuse": False, "reason": "cached_not_pass"}
    if require_same_head and record.head and current_head and record.head != current_head:
        return {"reuse": False, "reason": "head_changed"}
    if record.inputs_digest != current_digest:
        return {"reuse": False, "reason": "input_digest_mismatch"}
    return {
        "reuse": True,
        "reason": "identical_input_digest",
        "invalidation_scope": list(record.invalidation_scope),
    }


def cleanup_only_preserves_product_proof(
    changed_paths: Sequence[str],
    *,
    recorded_operational_paths: Sequence[str],
) -> bool:
    return is_cleanup_only_diff(
        changed_paths, recorded_operational_paths=recorded_operational_paths
    )


def plan_review(
    *,
    changed_paths: Sequence[str],
    is_final_readiness: bool = False,
    risk_hints: Sequence[str] | None = None,
    batch_blast_radius: str | None = None,
    is_high_risk_checkpoint: bool = False,
    is_untrusted_writer: bool = False,
) -> ReviewPlan:
    paths = _paths(changed_paths)
    reasons: list[str] = []
    focused: list[str] = []
    skipped: list[str] = []
    risk = "low"
    hints = [h.lower() for h in (risk_hints or [])]
    impact = build_impact_path(paths)
    scopes = invalidation_scopes_for_paths(paths)
    test_surface_hit = any(p.startswith("tests/") for p in paths)
    docs_only = bool(paths) and all(
        p.lower().endswith((".md", ".rst", ".adoc")) for p in paths
    )

    if is_final_readiness:
        reasons.append("final_readiness_requires_broad_gate")
        return ReviewPlan(
            focused_checks=(
                "full_unittest",
                "consistency",
                "release",
                "installed_smokes",
            ),
            broad_gate_required=True,
            reasons=tuple(reasons),
            risk_level="high",
            impact_path=impact,
            evidence_inputs_digest=_digest(
                {"paths": paths, "final": True, "scopes": list(scopes)}
            ),
            invalidation_scopes=scopes or ("all",),
        )

    if batch_blast_radius in {"medium", "high"}:
        risk = batch_blast_radius
        reasons.append(f"blast_radius={batch_blast_radius}")

    security_hit = any(
        any(m in p.lower() for m in SECURITY_MARKERS) for p in paths
    ) or any(m in hints for m in SECURITY_MARKERS)
    runtime_hit = any(
        "scripts/cobbler_runtime" in p or p.startswith("scripts/") for p in paths
    )
    if security_hit:
        risk = "high"
        focused.extend(["secret_redaction", "isolation_smoke", "unit:security"])
        reasons.append("security_surface_changed")
    if runtime_hit:
        if risk == "low":
            risk = "medium"
        focused.extend(
            ["unit:runtime", "compileall_scripts", "installed_bundle_smoke"]
        )
        reasons.append("runtime_or_scripts_changed")
    if test_surface_hit:
        focused.append("unit:focused")
        reasons.append("tests_changed")
        if risk == "low":
            risk = "medium"
    if any(p.endswith(".md") for p in paths) and not runtime_hit:
        focused.append("docs_consistency")
        reasons.append("docs_only_or_docs_touched")
    if any(p.startswith(".github/") for p in paths):
        focused.append("ci_workflow")
        reasons.append("ci_workflow_changed")
        risk = "high" if risk != "high" else risk

    # Fold impact-selected unit tests into focused checks.
    for row in impact:
        for test in row.get("selected_tests") or ():
            if isinstance(test, str) and test.startswith("tests/"):
                label = f"unit:{test}"
                if label not in focused:
                    focused.append(label)

    unmapped_nondoc_hit = bool(paths) and not docs_only and not runtime_hit
    if unmapped_nondoc_hit:
        if risk == "low":
            risk = "medium"
        focused.append("unit:focused")
        reasons.append("unmapped_or_nondoc_surface_changed")

    seen: set[str] = set()
    ordered: list[str] = []
    for item in focused:
        if item not in seen:
            seen.add(item)
            ordered.append(item)

    tier = classify_risk_tier(
        changed_paths=paths,
        is_final_readiness=is_final_readiness,
        is_high_risk_checkpoint=(is_high_risk_checkpoint or security_hit),
        is_untrusted_writer=is_untrusted_writer,
        batch_blast_radius=batch_blast_radius,
        risk_hints=hints,
    )
    budget = proof_budget_for_tier(tier.tier, is_final_readiness=is_final_readiness)
    broad = bool(budget["broad_required_now"])
    if budget["broad_required_now"]:
        if "full_unittest" not in ordered:
            ordered.append("full_unittest")
        reasons.append("escalate_to_broad_gate")
    else:
        skipped.append("full_unittest_deferred_to_terminal")

    if not ordered:
        ordered = ["unit:focused"]
        reasons.append("default_focused_unit")
    reasons.append(
        f"risk={tier.risk};trust={tier.trust_mode};legacy_tier={tier.tier};"
        f"proof={tier.proof_mode}"
    )
    reasons.append("impact_path_applied")

    return ReviewPlan(
        focused_checks=tuple(ordered),
        broad_gate_required=broad,
        reasons=tuple(reasons),
        skipped=tuple(skipped),
        risk_level=risk,
        impact_path=impact,
        evidence_inputs_digest=_digest(
            {
                "paths": paths,
                "checks": ordered,
                "scopes": list(scopes),
            }
        ),
        invalidation_scopes=scopes,
    )


def plan_from_diff_stat(
    diff_stat: Mapping[str, int] | Sequence[str], **kwargs: Any
) -> ReviewPlan:
    if isinstance(diff_stat, Mapping):
        paths = list(diff_stat.keys())
    else:
        paths = list(diff_stat)
    return plan_review(changed_paths=paths, **kwargs)


def consolidate_findings(
    findings: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Split findings into blocking vs advisory; advisory never delays readiness."""
    blocking: list[dict[str, Any]] = []
    advisory: list[dict[str, Any]] = []
    for item in findings:
        severity = str(item.get("severity") or item.get("level") or "").lower()
        is_blocking = bool(item.get("blocking")) or severity in {
            "blocking",
            "block",
            "error",
            "fail",
            "critical",
        }
        row = dict(item)
        if is_blocking:
            blocking.append(row)
        else:
            advisory.append(row)
    return {
        "blocking": blocking,
        "advisory": advisory,
        "advisory_delays_readiness": False,
        "must_consolidate_before_revision": bool(blocking),
    }


def admit_new_rereview_blocker(
    finding: Mapping[str, Any],
    *,
    introduced_by_revision: bool = False,
) -> dict[str, Any]:
    """New re-review blockers require a concrete serious category (B3-A6)."""
    category = str(finding.get("category") or finding.get("kind") or "").lower()
    if introduced_by_revision and category in {"", "regression", "failure"}:
        category = "revision_introduced_failure"
    admitted = category in NEW_BLOCKER_CATEGORIES
    return {
        "admitted": admitted,
        "category": category or None,
        "reason": (
            "concrete_serious_category"
            if admitted
            else "rejected_non_serious_or_style_suggestion"
        ),
    }


def plan_cumulative_review(*, target_sha: str | None = None) -> ConvergentReviewPlan:
    """One cumulative review: completeness, constitution, risks, regressions."""
    return ConvergentReviewPlan(
        mode="cumulative",
        checks=CUMULATIVE_REVIEW_CHECKS,
        blocking=(),
        advisory=(),
        last_reviewed_sha=None,
        target_sha=target_sha,
        stop=False,
        reasons=("one_cumulative_terminal_review",),
    )


def plan_delta_rereview(
    *,
    last_reviewed_sha: str,
    target_sha: str,
    unresolved_blocker_ids: Sequence[str],
    new_findings: Sequence[Mapping[str, Any]] | None = None,
    revision_paths: Sequence[str] | None = None,
) -> ConvergentReviewPlan:
    """Re-review verifies revision delta + unresolved blockers only."""
    admitted_blocking: list[str] = []
    advisory: list[str] = []
    for item in new_findings or ():
        decision = admit_new_rereview_blocker(
            item,
            introduced_by_revision=bool(item.get("introduced_by_revision")),
        )
        fid = str(item.get("id") or item.get("summary") or "finding")
        if decision["admitted"] and (
            bool(item.get("blocking"))
            or str(item.get("severity") or "").lower()
            in {"blocking", "block", "error", "fail", "critical"}
        ):
            admitted_blocking.append(fid)
        else:
            advisory.append(fid)

    checks = [
        "revision_delta",
        "unresolved_blockers",
        *(f"blocker:{b}" for b in unresolved_blocker_ids),
    ]
    if revision_paths:
        checks.append("impact_path_on_revision_paths")

    remaining = list(unresolved_blocker_ids) + admitted_blocking
    stop = len(remaining) == 0
    reasons = [
        "delta_only_rereview",
        "no_rescan_of_settled_untouched_work",
    ]
    if stop:
        reasons.append("sufficient_exact_tip_evidence")
    else:
        reasons.append("blockers_remain")

    return ConvergentReviewPlan(
        mode="stop" if stop else "delta_rereview",
        checks=tuple(checks),
        blocking=tuple(admitted_blocking),
        advisory=tuple(advisory),
        last_reviewed_sha=last_reviewed_sha,
        target_sha=target_sha,
        stop=stop,
        reasons=tuple(reasons),
    )


def should_stop_review_loop(
    *,
    exact_tip_evidence_sufficient: bool,
    unresolved_blockers: Sequence[str],
    reviewer_still_has_suggestions: bool = False,
) -> dict[str, Any]:
    """Stop on sufficient exact-tip evidence, not absence of suggestions (B3-A8)."""
    if unresolved_blockers:
        return {
            "stop": False,
            "reason": "unresolved_blockers",
            "blockers": list(unresolved_blockers),
        }
    if exact_tip_evidence_sufficient:
        return {
            "stop": True,
            "reason": "sufficient_exact_tip_evidence",
            "ignored_advisory_suggestions": bool(reviewer_still_has_suggestions),
        }
    return {
        "stop": False,
        "reason": "exact_tip_evidence_insufficient",
    }
