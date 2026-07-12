"""Cobbler external-agent runtime foundations.

Typed contracts, config resolution, capability records, read-only adapter
builders, context redaction, parallel council dispatch, exact session
registry/usage ledger, single external writer lease/audit, and setup UX for
local ignored preferences.
"""

from .schema import (
    BUILTIN_ADAPTER_NAMES,
    DEFAULT_ROLES,
    CapabilityStatus,
    ConfigSource,
    ContextSharingPolicy,
    EffectiveAttempt,
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
    "EffectiveAttempt",
    "HarnessProfile",
    "RoleName",
    "SessionMode",
    "ValidationIssue",
]
