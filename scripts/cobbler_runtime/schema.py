"""Typed contracts for Cobbler external-agent configuration.

These types are provider-neutral. Personal model IDs must never appear as public
defaults; only harness profile names and capability language belong here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ConfigSource(str, Enum):
    """Where a resolved route value came from."""

    SURVIVAL_GUIDE = "survival_guide"
    LOCAL_MODELS_TOML = "local_models_toml"
    USER_CONFIG_JSON = "user_config_json"
    NATIVE_DEFAULT = "native_default"


class CapabilityStatus(str, Enum):
    """Lifecycle of a claimed capability."""

    UNKNOWN = "unknown"
    ADVERTISED = "advertised"
    QUALIFIED = "qualified"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


class RoleName(str, Enum):
    """Configurable role slots for full-run routing."""

    PLANNING = "planning"
    IMPLEMENT = "implement"
    VALIDATE = "validate"
    REVIEW = "review"
    LIGHTWEIGHT_REVIEW = "lightweight_review"
    SYNTHESIZE = "synthesize"
    SCOUT = "scout"


class SessionMode(str, Enum):
    """Schema placeholders for later session runtime (Batch 3)."""

    EPHEMERAL = "ephemeral"
    PERSISTENT = "persistent"
    EXACT_RESUME = "exact_resume"


class ContextSharingPolicy(str, Enum):
    """Schema placeholders for context isolation between lanes."""

    INDEPENDENT = "independent"
    SHARED_PACKET = "shared_packet"
    REHYDRATE_FROM_DISK = "rehydrate_from_disk"


DEFAULT_ROLES: tuple[RoleName, ...] = (
    RoleName.PLANNING,
    RoleName.IMPLEMENT,
    RoleName.VALIDATE,
    RoleName.REVIEW,
    RoleName.LIGHTWEIGHT_REVIEW,
    RoleName.SYNTHESIZE,
    RoleName.SCOUT,
)

BUILTIN_ADAPTER_NAMES: tuple[str, ...] = (
    "claude-code",
    "grok-build",
    "codex-fugu",
    "custom-cli",
    "host-native",
)

NATIVE_PROFILE_NAME = "host-native"
NATIVE_ROUTE = "host-native"

# Operations that never dispatch model inference.
NON_MODEL_OPERATIONS: frozenset[str] = frozenset(
    {
        "git",
        "gh",
        "push",
        "pull",
        "fetch",
        "pr_create",
        "pr_comment",
        "pr_merge",
        "tag",
        "commit",
        "checkout",
        "status",
        "diff",
        "log",
    }
)


class ValidationIssue(Exception):
    """Actionable configuration validation error."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        path: str | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.path = path
        self.hint = hint

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.path is not None:
            payload["path"] = self.path
        if self.hint is not None:
            payload["hint"] = self.hint
        return payload


@dataclass(frozen=True)
class HarnessProfile:
    """Named harness profile without hardcoding prestige models."""

    name: str
    adapter: str
    executable: str | None = None
    notes: str = ""
    session_mode: SessionMode = SessionMode.EPHEMERAL
    context_sharing: ContextSharingPolicy = ContextSharingPolicy.INDEPENDENT

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CapabilityRecord:
    """Behaviorally classified capability for a harness profile."""

    name: str
    status: CapabilityStatus
    detail: str = ""
    qualified_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "detail": self.detail,
            "qualified_at": self.qualified_at,
        }


@dataclass(frozen=True)
class FallbackEntry:
    """One step in a deterministic fallback chain."""

    profile: str
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RoleRoute:
    """Resolved route for one role slot."""

    role: RoleName
    profile: str
    required: bool = False
    fallback_chain: tuple[FallbackEntry, ...] = ()
    source: ConfigSource = ConfigSource.NATIVE_DEFAULT
    session_mode: SessionMode = SessionMode.EPHEMERAL
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role.value,
            "profile": self.profile,
            "required": self.required,
            "fallback_chain": [entry.to_dict() for entry in self.fallback_chain],
            "source": self.source.value,
            "session_mode": self.session_mode.value,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class UsageRecord:
    """Observed usage with honest unknown remaining quota."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None
    remaining_quota: str | int | None = "unknown"
    quota_known: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
            "remaining_quota": self.remaining_quota if self.quota_known else "unknown",
            "quota_known": self.quota_known,
        }


@dataclass
class ResolvedConfig:
    """Fully resolved routing table with provenance."""

    roles: dict[str, RoleRoute] = field(default_factory=dict)
    profiles: dict[str, HarnessProfile] = field(default_factory=dict)
    issues: list[ValidationIssue] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sources_consulted: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "roles": {name: route.to_dict() for name, route in self.roles.items()},
            "profiles": {name: profile.to_dict() for name, profile in self.profiles.items()},
            "issues": [issue.to_dict() for issue in self.issues],
            "warnings": list(self.warnings),
            "sources_consulted": list(self.sources_consulted),
        }


def parse_role_name(raw: str) -> RoleName:
    normalized = raw.strip().lower().replace("-", "_")
    try:
        return RoleName(normalized)
    except ValueError as exc:
        raise ValidationIssue(
            "unknown_role",
            f"Unknown role `{raw}`",
            path=f"roles.{raw}",
            hint=f"Known roles: {', '.join(role.value for role in RoleName)}",
        ) from exc


def is_non_model_operation(operation: str) -> bool:
    """Return True when the operation must never dispatch model inference."""
    token = operation.strip().lower().replace("-", "_")
    if token in NON_MODEL_OPERATIONS:
        return True
    head = token.split()[0] if token else ""
    return head in NON_MODEL_OPERATIONS
