"""Deterministic evidence-aware focused review selection.

Ordinary trusted batches verify the changed surface without reopening the whole
run.  Declared high-risk checkpoints, security tripwires, untrusted writers, and
terminal readiness escalate to the broad cumulative gate. Reasons are logged.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from .risk_policy import classify_risk_tier, proof_budget_for_tier


SECURITY_MARKERS: tuple[str, ...] = (
    "secret",
    "redact",
    "credential",
    "token",
    "isolation",
    "sandbox",
)


@dataclass(frozen=True)
class ReviewPlan:
    focused_checks: tuple[str, ...]
    broad_gate_required: bool
    reasons: tuple[str, ...]
    skipped: tuple[str, ...] = ()
    risk_level: str = "low"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _paths(changed_paths: Sequence[str]) -> list[str]:
    return [p.replace("\\", "/") for p in changed_paths]


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
    test_surface_hit = any(p.startswith("tests/") for p in paths)
    docs_only = bool(paths) and all(
        p.lower().endswith((".md", ".rst", ".adoc")) for p in paths
    )

    if is_final_readiness:
        reasons.append("final_readiness_requires_broad_gate")
        return ReviewPlan(
            focused_checks=("full_unittest", "consistency", "release", "installed_smokes"),
            broad_gate_required=True,
            reasons=tuple(reasons),
            risk_level="high",
        )

    if batch_blast_radius in {"medium", "high"}:
        risk = batch_blast_radius
        reasons.append(f"blast_radius={batch_blast_radius}")

    security_hit = any(
        any(m in p.lower() for m in SECURITY_MARKERS) for p in paths
    ) or any(m in hints for m in SECURITY_MARKERS)
    runtime_hit = any("scripts/cobbler_runtime" in p or p.startswith("scripts/") for p in paths)
    if security_hit:
        risk = "high"
        focused.extend(["secret_redaction", "isolation_smoke", "unit:security"])
        reasons.append("security_surface_changed")
    if runtime_hit:
        if risk == "low":
            risk = "medium"
        focused.extend(["unit:runtime", "compileall_scripts", "installed_bundle_smoke"])
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

    # Unknown code/config/assets still receive a concrete focused check. They do
    # not silently turn an ordinary trusted batch receipt into cumulative review;
    # planning must declare a high-risk checkpoint when broad mid-run proof is
    # actually required.
    unmapped_nondoc_hit = bool(paths) and not docs_only and not runtime_hit
    if unmapped_nondoc_hit:
        if risk == "low":
            risk = "medium"
        focused.append("unit:focused")
        reasons.append("unmapped_or_nondoc_surface_changed")

    # Deduplicate focused checks preserving order.
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
    reasons.append(f"risk_tier={tier.tier};proof={tier.proof_mode}")

    return ReviewPlan(
        focused_checks=tuple(ordered),
        broad_gate_required=broad,
        reasons=tuple(reasons),
        skipped=tuple(skipped),
        risk_level=risk,
    )


def plan_from_diff_stat(diff_stat: Mapping[str, int] | Sequence[str], **kwargs: Any) -> ReviewPlan:
    if isinstance(diff_stat, Mapping):
        paths = list(diff_stat.keys())
    else:
        paths = list(diff_stat)
    return plan_review(changed_paths=paths, **kwargs)
