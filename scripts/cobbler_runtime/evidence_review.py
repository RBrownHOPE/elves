"""Deterministic evidence-aware focused review selection.

Chooses focused checks from changed-surface and risk evidence. High-risk and
final readiness always escalate to the broad cumulative gate. Reasons are logged.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence


HIGH_RISK_PATH_MARKERS: tuple[str, ...] = (
    "auth",
    "billing",
    "lease",
    "session",
    "credential",
    "secret",
    "dispatch",
    "audit",
    "security",
    "payment",
    ".github/workflows",
    "scripts/cobbler_runtime",
)

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
) -> ReviewPlan:
    paths = _paths(changed_paths)
    reasons: list[str] = []
    focused: list[str] = []
    skipped: list[str] = []
    risk = "low"
    hints = [h.lower() for h in (risk_hints or [])]

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
    high_risk_hit = any(any(m in p.lower() for m in HIGH_RISK_PATH_MARKERS) for p in paths)

    if security_hit:
        risk = "high"
        focused.extend(["secret_redaction", "isolation_smoke", "unit:security"])
        reasons.append("security_surface_changed")
    if runtime_hit:
        if risk == "low":
            risk = "medium"
        focused.extend(["unit:runtime", "compileall_scripts", "installed_bundle_smoke"])
        reasons.append("runtime_or_scripts_changed")
    if any(p.startswith("tests/") for p in paths):
        focused.append("unit:focused")
        reasons.append("tests_changed")
    if any(p.endswith(".md") for p in paths) and not runtime_hit:
        focused.append("docs_consistency")
        reasons.append("docs_only_or_docs_touched")
    if any(p.startswith(".github/") for p in paths):
        focused.append("ci_workflow")
        reasons.append("ci_workflow_changed")
        risk = "high" if risk != "high" else risk

    if high_risk_hit and risk != "high":
        risk = "high"
        reasons.append("high_risk_path_marker")

    # Deduplicate focused checks preserving order.
    seen: set[str] = set()
    ordered: list[str] = []
    for item in focused:
        if item not in seen:
            seen.add(item)
            ordered.append(item)

    broad = risk == "high" or security_hit or is_final_readiness
    if broad:
        reasons.append("escalate_to_broad_gate")
        if "full_unittest" not in ordered:
            ordered.append("full_unittest")
    else:
        skipped.append("full_unittest_deferred_to_entropy_or_final")

    if not ordered:
        ordered = ["unit:focused"]
        reasons.append("default_focused_unit")

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
