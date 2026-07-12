"""Adapter protocol and built-in registry stubs.

Batch 1 registers harness names only. Command builders and live invocation land
in later batches.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .schema import BUILTIN_ADAPTER_NAMES, HarnessProfile, NATIVE_PROFILE_NAME, ValidationIssue


class Adapter(Protocol):
    """Minimal adapter surface for later dispatch/session batches."""

    name: str

    def describe(self) -> dict[str, object]:
        """Return a stable machine-readable description."""


@dataclass(frozen=True)
class StubAdapter:
    """Built-in adapter placeholder with no side effects."""

    name: str
    executable_hint: str
    supports_persistent_sessions: bool = False
    supports_isolated_write: bool = False

    def describe(self) -> dict[str, object]:
        return {
            "name": self.name,
            "executable_hint": self.executable_hint,
            "supports_persistent_sessions": self.supports_persistent_sessions,
            "supports_isolated_write": self.supports_isolated_write,
            "status": "stub",
            "note": "Command builders and live probes are implemented in later batches",
        }


_BUILTIN: dict[str, StubAdapter] = {
    "host-native": StubAdapter(
        name="host-native",
        executable_hint="(host coordinator)",
        supports_persistent_sessions=False,
        supports_isolated_write=True,
    ),
    "claude-code": StubAdapter(
        name="claude-code",
        executable_hint="claude",
        supports_persistent_sessions=True,
        supports_isolated_write=True,
    ),
    "grok-build": StubAdapter(
        name="grok-build",
        executable_hint="grok",
        supports_persistent_sessions=True,
        supports_isolated_write=True,
    ),
    "codex-fugu": StubAdapter(
        name="codex-fugu",
        executable_hint="codex",
        supports_persistent_sessions=True,
        supports_isolated_write=False,
    ),
    "custom-cli": StubAdapter(
        name="custom-cli",
        executable_hint="(user-defined)",
        supports_persistent_sessions=False,
        supports_isolated_write=False,
    ),
}


def builtin_adapter_names() -> tuple[str, ...]:
    return BUILTIN_ADAPTER_NAMES


def get_adapter(name: str) -> StubAdapter:
    adapter = _BUILTIN.get(name)
    if adapter is None:
        raise ValidationIssue(
            "unknown_adapter",
            f"Unknown adapter `{name}`",
            path=f"adapters.{name}",
            hint=f"Built-in adapters: {', '.join(BUILTIN_ADAPTER_NAMES)}",
        )
    return adapter


def default_profiles() -> dict[str, HarnessProfile]:
    """Return built-in profiles without personal model defaults."""
    profiles: dict[str, HarnessProfile] = {}
    for name, adapter in _BUILTIN.items():
        profiles[name] = HarnessProfile(
            name=name,
            adapter=adapter.name,
            executable=None if name == NATIVE_PROFILE_NAME else adapter.executable_hint,
            notes=f"Built-in {adapter.name} profile",
        )
    return profiles


def registry_snapshot() -> dict[str, dict[str, object]]:
    return {name: adapter.describe() for name, adapter in sorted(_BUILTIN.items())}
