"""Adapter protocol, registry stubs, and read-only command builders.

Provider command construction lives here — not in the dispatcher. Batch 2 adds
read-only builders and structured-output parsing. Live paid smoke is not required;
fake executables are the deterministic gate.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .context import ROLE_REPORT_SCHEMA_FIELDS
from .schema import BUILTIN_ADAPTER_NAMES, HarnessProfile, NATIVE_PROFILE_NAME, ValidationIssue


class Adapter(Protocol):
    """Minimal adapter surface for dispatch/session batches."""

    name: str

    def describe(self) -> dict[str, object]:
        """Return a stable machine-readable description."""


@dataclass(frozen=True)
class StubAdapter:
    """Built-in adapter metadata with no side effects."""

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
            "status": "readonly-builder",
            "note": "Read-only command builders available; write leases land later",
        }


@dataclass(frozen=True)
class AdapterInvocation:
    """Argv-safe read-only invocation (never a shell string)."""

    adapter: str
    executable: str
    argv: tuple[str, ...]
    read_only: bool = True
    tool_scope: str = "read-only"
    sandbox_scope: str = "ephemeral"
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter,
            "executable": self.executable,
            "argv": list(self.argv),
            "read_only": self.read_only,
            "tool_scope": self.tool_scope,
            "sandbox_scope": self.sandbox_scope,
            "notes": self.notes,
        }


_BUILTIN: dict[str, StubAdapter] = {
    "host-native": StubAdapter(
        name="host-native",
        executable_hint="python3",
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

# Host-native read-only lane emits a structured report from the packet without
# external provider CLIs. Implemented as `python3 -c <script>` with argv only.
_HOST_NATIVE_READONLY_SCRIPT = r"""
import json, pathlib, sys
packet_path = pathlib.Path(sys.argv[1])
packet = json.loads(packet_path.read_text(encoding="utf-8"))
report = {
    "role": packet.get("role") or "host-native",
    "verdict": "pass",
    "confidence": "medium",
    "key_findings": ["host-native read-only analysis"],
    "evidence": [f"packet={packet_path.name}", f"head={packet.get('head_sha')}"],
    "risks": [],
    "recommended_actions": ["host synthesis"],
    "open_questions": [],
    "actual_model": "host-native",
    "requested_model": packet.get("requested_model"),
}
print(json.dumps(report))
"""


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


def build_readonly_invocation(
    *,
    adapter: str,
    profile: str,
    packet_path: Path,
    prompt_path: Path,
    executable: str | None = None,
    requested_model: str | None = None,
    extra_args: tuple[str, ...] | list[str] = (),
) -> AdapterInvocation:
    """Build an argv-safe read-only command for a known adapter.

    Task text is never interpolated into a shell string. Prompt and packet are
    delivered as file paths.
    """
    name = adapter.strip().lower()
    if name not in _BUILTIN and name != "custom-cli":
        # Unknown adapter names are treated as custom-cli wrappers when executable is set.
        if not executable:
            raise ValidationIssue(
                "unknown_adapter",
                f"Unknown adapter `{adapter}` and no executable provided",
                path=f"adapters.{adapter}",
            )
        name = "custom-cli"

    meta = _BUILTIN.get(name, _BUILTIN["custom-cli"])
    exe = executable or meta.executable_hint
    packet_s = str(packet_path)
    prompt_s = str(prompt_path)
    extras = tuple(extra_args)

    if name == "host-native":
        py = executable or "python3"
        argv = (py, "-c", _HOST_NATIVE_READONLY_SCRIPT.strip(), packet_s)
        return AdapterInvocation(
            adapter="host-native",
            executable=py,
            argv=argv,
            read_only=True,
            tool_scope="read-only",
            sandbox_scope="host",
            notes="stdlib host-native structured report from packet",
        )

    if name == "claude-code":
        # Explicit structural read-only / plan-scoped invocation. Never use permission
        # bypass flags. Exact CLI flags may vary by version; callers may override via
        # extra_args or command_override in tests.
        argv = [
            exe,
            "-p",
            "--output-format",
            "json",
            "--permission-mode",
            "plan",
        ]
        if requested_model:
            argv.extend(["--model", requested_model])
        argv.extend(["--packet", packet_s, "--prompt-file", prompt_s])
        argv.extend(extras)
        return AdapterInvocation(
            adapter="claude-code",
            executable=exe,
            argv=tuple(argv),
            read_only=True,
            tool_scope="read-only",
            sandbox_scope="ephemeral",
            notes=(
                "structural read-only scope via --permission-mode plan; "
                "no permission-bypass flags"
            ),
        )

    if name == "grok-build":
        argv = [
            exe,
            "--prompt-file",
            prompt_s,
            "--packet",
            packet_s,
            "--readonly",
        ]
        if requested_model:
            argv.extend(["--model", requested_model])
        argv.extend(extras)
        return AdapterInvocation(
            adapter="grok-build",
            executable=exe,
            argv=tuple(argv),
            read_only=True,
            tool_scope="read-only",
            sandbox_scope="ephemeral",
            notes="read-only; never use headless worktree-resume as isolation here",
        )

    if name == "codex-fugu":
        argv = [
            exe,
            "exec",
            "--json",
            "--sandbox",
            "read-only",
            "--packet",
            packet_s,
            "--prompt-file",
            prompt_s,
        ]
        if requested_model:
            argv.extend(["--model", requested_model])
        argv.extend(extras)
        return AdapterInvocation(
            adapter="codex-fugu",
            executable=exe,
            argv=tuple(argv),
            read_only=True,
            tool_scope="read-only",
            sandbox_scope="read-only",
            notes="read-only sandbox scope; MCP warnings are not inference failure",
        )

    # custom-cli
    if not exe or exe == "(user-defined)":
        raise ValidationIssue(
            "missing_executable",
            f"custom-cli profile `{profile}` requires an executable",
            path=f"profiles.{profile}.executable",
        )
    argv = [exe, "--packet", packet_s, "--prompt-file", prompt_s, "--readonly"]
    if requested_model:
        argv.extend(["--model", requested_model])
    argv.extend(extras)
    return AdapterInvocation(
        adapter="custom-cli",
        executable=exe,
        argv=tuple(argv),
        read_only=True,
        tool_scope="read-only",
        sandbox_scope="ephemeral",
        notes="user-defined wrapper; argv only, shell=False",
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from stdout (raw or fenced)."""
    stripped = text.strip()
    if not stripped:
        raise ValidationIssue(
            "empty_output",
            "Adapter produced empty stdout; structured report required",
        )
    # Fenced ```json ... ```
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.S)
    if fence:
        stripped = fence.group(1)
    else:
        # Prefer the last JSON object-looking span.
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start != -1 and end != -1 and end > start:
            stripped = stripped[start : end + 1]
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValidationIssue(
            "malformed_json",
            f"Adapter stdout is not valid JSON: {exc.msg}",
            hint="role reports must be a single JSON object",
        ) from exc
    if not isinstance(data, dict):
        raise ValidationIssue(
            "invalid_report_type",
            "Role report JSON root must be an object",
        )
    return data


def parse_role_report(
    stdout: str,
    *,
    expected_role: str | None = None,
    requested_model: str | None = None,
) -> dict[str, Any]:
    """Validate bounded role-report schema. Exit code alone is never enough."""
    data = _extract_json_object(stdout)
    missing = [field for field in ROLE_REPORT_SCHEMA_FIELDS if field not in data]
    if missing:
        raise ValidationIssue(
            "missing_report_fields",
            "Role report missing required fields: " + ", ".join(missing),
            hint=f"required fields: {', '.join(ROLE_REPORT_SCHEMA_FIELDS)}",
        )

    if expected_role is not None:
        reported_role = str(data.get("role") or "")
        if reported_role and reported_role != expected_role:
            raise ValidationIssue(
                "role_mismatch",
                f"Report role `{reported_role}` does not match expected `{expected_role}`",
            )

    if requested_model is not None:
        actual = data.get("actual_model") or data.get("model")
        # Allow reports that omit actual_model only when no requested model was set.
        if actual is not None and str(actual) != str(requested_model):
            # Mismatch is a hard validation failure for required lanes; callers decide.
            raise ValidationIssue(
                "actual_model_mismatch",
                f"actual_model `{actual}` does not match requested_model `{requested_model}`",
            )

    # Normalize list-ish fields.
    for key in (
        "key_findings",
        "evidence",
        "risks",
        "recommended_actions",
        "open_questions",
    ):
        value = data.get(key)
        if value is None:
            data[key] = []
        elif isinstance(value, str):
            data[key] = [value]
        elif not isinstance(value, list):
            raise ValidationIssue(
                "invalid_report_field_type",
                f"Role report field `{key}` must be a list or string",
            )

    return data


# --- Exact session create/resume builders (Batch 3) -----------------------

# Ambiguous session-selection forms that must never appear in generated argv.
AMBIGUOUS_SESSION_FLAG_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(^|\s)--continue(\s|$)"),
    re.compile(r"(^|\s)--last(\s|$)"),
    re.compile(r"(^|\s)--resume(\s|$)"),  # bare --resume without following ID
    re.compile(r"(^|\s)-c(\s|$)"),  # bare continue shorthand where used as session selector
)


def assert_no_ambiguous_session_flags(argv: tuple[str, ...] | list[str]) -> None:
    """Fail if argv uses ambiguous session selection.

    Bare ``--resume`` without a following non-flag token is forbidden. Exact
    forms like ``--resume <session-id>`` or ``--session-id <id>`` are required.
    """
    tokens = list(argv)
    joined = " ".join(tokens)
    # Detect bare --resume / --continue / --last as standalone tokens without value.
    for index, token in enumerate(tokens):
        if token in {"--continue", "--last"}:
            raise ValidationIssue(
                "ambiguous_session_flag",
                f"Forbidden ambiguous session flag `{token}` in argv",
                hint="Use exact session IDs only",
            )
        if token == "--resume":
            nxt = tokens[index + 1] if index + 1 < len(tokens) else None
            if nxt is None or nxt.startswith("-"):
                raise ValidationIssue(
                    "ambiguous_session_flag",
                    "Bare `--resume` without an exact session id is forbidden",
                    hint="Use --resume <exact-session-id> or --session-id <id>",
                )
    # Extra pattern sweep on joined string for --continue/--last.
    for pattern in AMBIGUOUS_SESSION_FLAG_PATTERNS:
        if pattern.pattern.startswith(r"(^|\s)--resume"):
            continue  # handled above with value check
        if pattern.search(joined):
            raise ValidationIssue(
                "ambiguous_session_flag",
                f"Forbidden ambiguous session selection pattern in: {joined}",
            )


def build_session_create_invocation(
    *,
    adapter: str,
    profile: str,
    executable: str | None = None,
    requested_model: str | None = None,
    extra_args: tuple[str, ...] | list[str] = (),
) -> AdapterInvocation:
    """Build argv for creating a new exact session (no ambiguous selectors)."""
    name = adapter.strip().lower()
    meta = _BUILTIN.get(name, _BUILTIN["custom-cli"])
    exe = executable or meta.executable_hint
    extras = tuple(extra_args)

    if name == "host-native":
        raise ValidationIssue(
            "host_native_no_external_session",
            "host-native does not create external provider sessions",
        )

    if name == "claude-code":
        argv = [exe or "claude", "--print", "--output-format", "json"]
        if requested_model:
            argv.extend(["--model", requested_model])
        argv.extend(["--session-create"])
        argv.extend(extras)
        inv = AdapterInvocation(
            adapter="claude-code",
            executable=argv[0],
            argv=tuple(argv),
            read_only=True,
            notes="exact session create; no --continue/--last",
        )
        assert_no_ambiguous_session_flags(inv.argv)
        return inv

    if name == "grok-build":
        argv = [exe or "grok", "--new-session"]
        if requested_model:
            argv.extend(["--model", requested_model])
        argv.extend(extras)
        inv = AdapterInvocation(
            adapter="grok-build",
            executable=argv[0],
            argv=tuple(argv),
            read_only=True,
            notes="exact create; worktree child IDs are discovered after fork",
        )
        assert_no_ambiguous_session_flags(inv.argv)
        return inv

    if name == "codex-fugu":
        argv = [exe or "codex", "exec", "--json", "--session-create"]
        if requested_model:
            argv.extend(["--model", requested_model])
        argv.extend(extras)
        inv = AdapterInvocation(
            adapter="codex-fugu",
            executable=argv[0],
            argv=tuple(argv),
            read_only=True,
            notes="exact session create for fugu/codex path",
        )
        assert_no_ambiguous_session_flags(inv.argv)
        return inv

    # custom-cli
    if not exe or exe == "(user-defined)":
        raise ValidationIssue(
            "missing_executable",
            f"custom-cli profile `{profile}` requires an executable for session create",
            path=f"profiles.{profile}.executable",
        )
    argv = [exe, "--session-create"]
    if requested_model:
        argv.extend(["--model", requested_model])
    argv.extend(extras)
    inv = AdapterInvocation(
        adapter="custom-cli",
        executable=exe,
        argv=tuple(argv),
        read_only=True,
        notes="custom exact session create",
    )
    assert_no_ambiguous_session_flags(inv.argv)
    return inv


def build_session_resume_invocation(
    *,
    adapter: str,
    profile: str,
    session_id: str,
    executable: str | None = None,
    requested_model: str | None = None,
    cwd: str | None = None,
    extra_args: tuple[str, ...] | list[str] = (),
) -> AdapterInvocation:
    """Build argv that resumes an exact session ID (never bare --resume/--continue/--last)."""
    sid = (session_id or "").strip()
    if not sid:
        raise ValidationIssue(
            "missing_session_id",
            "Exact session_id is required for resume",
            hint="Never omit the id or use last/continue selection",
        )

    name = adapter.strip().lower()
    meta = _BUILTIN.get(name, _BUILTIN["custom-cli"])
    exe = executable or meta.executable_hint
    extras = tuple(extra_args)

    if name == "host-native":
        raise ValidationIssue(
            "host_native_no_external_session",
            "host-native does not resume external provider sessions",
        )

    if name == "claude-code":
        argv = [exe or "claude", "--print", "--output-format", "json", "--resume", sid]
        if requested_model:
            argv.extend(["--model", requested_model])
        if cwd:
            argv.extend(["--cwd", cwd])
        argv.extend(extras)
        inv = AdapterInvocation(
            adapter="claude-code",
            executable=argv[0],
            argv=tuple(argv),
            read_only=True,
            notes="exact --resume <session-id>",
        )
        assert_no_ambiguous_session_flags(inv.argv)
        return inv

    if name == "grok-build":
        # Exact child resume from verified worktree CWD. Never headless
        # --worktree --resume as isolation for broken versions.
        argv = [exe or "grok", "--resume", sid]
        if requested_model:
            argv.extend(["--model", requested_model])
        if cwd:
            argv.extend(["--cwd", cwd])
        argv.extend(extras)
        inv = AdapterInvocation(
            adapter="grok-build",
            executable=argv[0],
            argv=tuple(argv),
            read_only=True,
            notes=(
                "exact child resume; verify CWD/worktree registration; "
                "do not use headless worktree-resume as isolation on 0.2.93"
            ),
        )
        assert_no_ambiguous_session_flags(inv.argv)
        return inv

    if name == "codex-fugu":
        argv = [exe or "codex", "exec", "--json", "--session-id", sid]
        if requested_model:
            argv.extend(["--model", requested_model])
        if cwd:
            argv.extend(["--cwd", cwd])
        argv.extend(extras)
        inv = AdapterInvocation(
            adapter="codex-fugu",
            executable=argv[0],
            argv=tuple(argv),
            read_only=True,
            notes="exact --session-id <id>",
        )
        assert_no_ambiguous_session_flags(inv.argv)
        return inv

    if not exe or exe == "(user-defined)":
        raise ValidationIssue(
            "missing_executable",
            f"custom-cli profile `{profile}` requires an executable for session resume",
            path=f"profiles.{profile}.executable",
        )
    argv = [exe, "--session-id", sid]
    if requested_model:
        argv.extend(["--model", requested_model])
    if cwd:
        argv.extend(["--cwd", cwd])
    argv.extend(extras)
    inv = AdapterInvocation(
        adapter="custom-cli",
        executable=exe,
        argv=tuple(argv),
        read_only=True,
        notes="custom exact session resume",
    )
    assert_no_ambiguous_session_flags(inv.argv)
    return inv
