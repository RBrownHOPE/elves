"""Capability records for harness profiles.

Batch 1 records capability shape only. Live paid model probes are intentionally
out of scope; doctor reports advertised/unknown status without launching models.
"""

from __future__ import annotations

from .schema import CapabilityRecord, CapabilityStatus, HarnessProfile


# Core capability names used by later batches.
CORE_CAPABILITIES: tuple[str, ...] = (
    "availability",
    "version",
    "authentication",
    "model_discovery",
    "read_only_repo",
    "local_commands",
    "file_edit",
    "structured_output",
    "actual_model_reporting",
    "persistent_session",
    "parent_child_lineage",
    "parallel_read_only",
    "isolated_write",
    "worktree",
    "permission_policy",
    "credential_isolation",
    "network_egress",
    "usage_reporting",
    "remaining_quota",
)


def default_capabilities_for(profile: HarnessProfile) -> list[CapabilityRecord]:
    """Return non-probed capability records for a profile.

    Host-native is always available for coordination without external tools.
    External adapters start as advertised/unknown until a later batch qualifies
    them with fixtures or live probes.
    """
    if profile.adapter == "host-native":
        return [
            CapabilityRecord(
                name="availability",
                status=CapabilityStatus.QUALIFIED,
                detail="Host coordinator path requires no external executable",
            ),
            CapabilityRecord(
                name="read_only_repo",
                status=CapabilityStatus.QUALIFIED,
                detail="Host can inspect the owned checkout",
            ),
            CapabilityRecord(
                name="local_commands",
                status=CapabilityStatus.QUALIFIED,
                detail="Host runs validation gates directly",
            ),
            CapabilityRecord(
                name="file_edit",
                status=CapabilityStatus.QUALIFIED,
                detail="Host edits the owned checkout",
            ),
            CapabilityRecord(
                name="remaining_quota",
                status=CapabilityStatus.UNKNOWN,
                detail="Host subscription quota is not tracked by Elves",
            ),
        ]

    records: list[CapabilityRecord] = []
    for name in CORE_CAPABILITIES:
        if name == "remaining_quota":
            status = CapabilityStatus.UNKNOWN
            detail = "Remaining quota is unknown unless a harness exposes it"
        elif name in {"availability", "version", "authentication", "model_discovery"}:
            status = CapabilityStatus.ADVERTISED
            detail = "Requires doctor inventory; not probed in Batch 1"
        else:
            status = CapabilityStatus.UNKNOWN
            detail = "Unqualified until adapter probe or fixture proves behavior"
        records.append(CapabilityRecord(name=name, status=status, detail=detail))
    return records


def summarize_capabilities(
    profiles: dict[str, HarnessProfile],
) -> dict[str, list[dict[str, object]]]:
    """Return JSON-ready capability inventory for doctor --json."""
    return {
        name: [record.to_dict() for record in default_capabilities_for(profile)]
        for name, profile in sorted(profiles.items())
    }
