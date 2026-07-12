"""Cobbler external-agent runtime foundations.

Batch 1 delivers typed contracts, config resolution, and capability/adapter
registry stubs. Dispatch, session lifecycle, and writer leases land later.
"""

from .schema import (
    BUILTIN_ADAPTER_NAMES,
    DEFAULT_ROLES,
    CapabilityStatus,
    ConfigSource,
    ContextSharingPolicy,
    HarnessProfile,
    RoleName,
    SessionMode,
    ValidationIssue,
)

__all__ = [
    "BUILTIN_ADAPTER_NAMES",
    "DEFAULT_ROLES",
    "CapabilityStatus",
    "ConfigSource",
    "ContextSharingPolicy",
    "HarnessProfile",
    "RoleName",
    "SessionMode",
    "ValidationIssue",
]
