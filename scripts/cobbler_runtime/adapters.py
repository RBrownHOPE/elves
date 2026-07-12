"""Adapter protocol, registry stubs, and read-only command builders.

Provider command construction lives here — not in the dispatcher. Batch 2 adds
read-only builders and structured-output parsing. Live paid smoke is not required;
fake executables are the deterministic gate.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
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
    """Argv-safe read-only invocation (never a shell string).

    Task/packet text travels via ``stdin_text`` or an explicitly supported
    prompt-file path in argv — never shell interpolation and never invented
    flags. ``unavailable`` marks host-native / unusable contracts without a
    subprocess.
    """

    adapter: str
    executable: str
    argv: tuple[str, ...]
    read_only: bool = True
    tool_scope: str = "read-only"
    sandbox_scope: str = "ephemeral"
    notes: str = ""
    stdin_text: str | None = None
    input_mode: str = "none"  # none | stdin | prompt-file
    decoder: str = "json-role-report"
    unavailable: bool = False
    unavailable_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter,
            "executable": self.executable,
            "argv": list(self.argv),
            "read_only": self.read_only,
            "tool_scope": self.tool_scope,
            "sandbox_scope": self.sandbox_scope,
            "notes": self.notes,
            "input_mode": self.input_mode,
            "decoder": self.decoder,
            "unavailable": self.unavailable,
            "unavailable_reason": self.unavailable_reason,
            # Never include stdin payload (may contain task text) in logs by default.
            "has_stdin": bool(self.stdin_text),
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

# Invented v1.20.0 flags that must never appear in generated argv.
FORBIDDEN_INVENTED_FLAGS: frozenset[str] = frozenset(
    {
        "--packet",
        "--readonly",
        "--session-create",  # reserved; session builders are versioned separately
    }
)

# Supported help families used by contract tests (probed, not inferred).
SUPPORTED_HELP_FAMILIES: dict[str, frozenset[str]] = {
    "claude-code": frozenset(
        {
            "--print",
            "-p",
            "--output-format",
            "--permission-mode",
            "--model",
            "--json-schema",
            "--no-session-persistence",
        }
    ),
    "grok-build": frozenset(
        {
            "--prompt-file",
            "--output-format",
            "--json-schema",
            "--model",
            "--reasoning-effort",
            "--permission-mode",
            "--sandbox",
            "--no-subagents",
            "--no-memory",
            "--disable-web-search",
        }
    ),
    "codex-fugu": frozenset(
        {
            "exec",
            "--json",
            "--sandbox",
            "--model",
            "-m",
            "--cd",
            "-C",
            "-",
        }
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


def _read_prompt_body(prompt_path: Path, packet_path: Path) -> str:
    """Compose stdin/prompt body from prompt + packet paths without shell interpolation."""
    prompt_text = ""
    packet_text = ""
    try:
        if prompt_path.is_file():
            prompt_text = prompt_path.read_text(encoding="utf-8")
    except OSError:
        prompt_text = f"(prompt path: {prompt_path})"
    try:
        if packet_path.is_file():
            packet_text = packet_path.read_text(encoding="utf-8")
    except OSError:
        packet_text = f"(packet path: {packet_path})"
    return (
        f"{prompt_text.rstrip()}\n\n"
        f"--- context packet ({packet_path.name}) ---\n"
        f"{packet_text}\n"
    )


def _assert_no_invented_flags(argv: tuple[str, ...] | list[str]) -> None:
    for token in argv:
        if token in FORBIDDEN_INVENTED_FLAGS:
            raise ValidationIssue(
                "invented_adapter_flag",
                f"Generated argv contains forbidden invented flag `{token}`",
                hint="Use version-aware supported CLI flags only",
            )


def build_readonly_invocation(
    *,
    adapter: str,
    profile: str,
    packet_path: Path,
    prompt_path: Path,
    executable: str | None = None,
    requested_model: str | None = None,
    extra_args: tuple[str, ...] | list[str] = (),
    cwd: str | None = None,
) -> AdapterInvocation:
    """Build an argv-safe read-only command for a known adapter.

    Task text is never interpolated into a shell string. Prompt content is
    delivered via ``--prompt-file`` or stdin (``-``), never via invented
    ``--packet`` / ``--readonly`` flags.
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
    prompt_s = str(prompt_path)
    extras = tuple(extra_args)
    prompt_body = _read_prompt_body(prompt_path, packet_path)

    if name == "host-native":
        # No canned PASS subprocess. Host must inject a real report for a vote.
        return AdapterInvocation(
            adapter="host-native",
            executable="",
            argv=(),
            read_only=True,
            tool_scope="read-only",
            sandbox_scope="host",
            notes="host-native has no subprocess; requires injected host report",
            input_mode="none",
            decoder="json-role-report",
            unavailable=True,
            unavailable_reason=(
                "host_native_requires_injected_report: standalone council cannot "
                "fabricate a host vote or count host-native toward quorum"
            ),
        )

    if name == "claude-code":
        # Claude Code 2.1.207: --print, --output-format json, --permission-mode plan.
        # Prompt via stdin; no --packet invention.
        argv_list = [
            exe,
            "--print",
            "--output-format",
            "json",
            "--permission-mode",
            "plan",
            "--no-session-persistence",
        ]
        if requested_model:
            argv_list.extend(["--model", requested_model])
        argv_list.extend(extras)
        argv = tuple(argv_list)
        _assert_no_invented_flags(argv)
        return AdapterInvocation(
            adapter="claude-code",
            executable=exe,
            argv=argv,
            read_only=True,
            tool_scope="read-only",
            sandbox_scope="ephemeral",
            notes=(
                "structural read-only scope via --permission-mode plan; "
                "no permission-bypass flags; prompt on stdin"
            ),
            stdin_text=prompt_body,
            input_mode="stdin",
            decoder="json-role-report",
        )

    if name == "grok-build":
        # Grok Build 0.2.93: --prompt-file, --output-format json, --permission-mode plan.
        # No --packet / --readonly inventions.
        argv_list = [
            exe,
            "--prompt-file",
            prompt_s,
            "--output-format",
            "json",
            "--permission-mode",
            "plan",
            "--no-subagents",
            "--no-memory",
            "--disable-web-search",
        ]
        if requested_model:
            argv_list.extend(["--model", requested_model])
        argv_list.extend(extras)
        argv = tuple(argv_list)
        _assert_no_invented_flags(argv)
        return AdapterInvocation(
            adapter="grok-build",
            executable=exe,
            argv=argv,
            read_only=True,
            tool_scope="read-only",
            sandbox_scope="ephemeral",
            notes=(
                "read-only permission-mode plan; never use headless worktree-resume "
                "as isolation here"
            ),
            input_mode="prompt-file",
            decoder="json-role-report",
        )

    if name == "codex-fugu":
        # Codex 0.144.1: codex exec --json --sandbox read-only [-m model] [-C dir] -
        work_cd = cwd or str(packet_path.parent)
        argv_list = [
            exe,
            "exec",
            "--json",
            "--sandbox",
            "read-only",
            "--cd",
            work_cd,
        ]
        if requested_model:
            argv_list.extend(["--model", requested_model])
        argv_list.append("-")  # prompt on stdin
        argv_list.extend(extras)
        argv = tuple(argv_list)
        _assert_no_invented_flags(argv)
        return AdapterInvocation(
            adapter="codex-fugu",
            executable=exe,
            argv=argv,
            read_only=True,
            tool_scope="read-only",
            sandbox_scope="read-only",
            notes="read-only sandbox; prompt on stdin via '-'; MCP warnings are not inference failure",
            stdin_text=prompt_body,
            input_mode="stdin",
            decoder="json-role-report",
        )

    # custom-cli — provider-neutral wrapper; prompt-file + optional model.
    if not exe or exe == "(user-defined)":
        raise ValidationIssue(
            "missing_executable",
            f"custom-cli profile `{profile}` requires an executable",
            path=f"profiles.{profile}.executable",
        )
    argv_list = [exe, "--prompt-file", prompt_s]
    if requested_model:
        argv_list.extend(["--model", requested_model])
    argv_list.extend(extras)
    argv = tuple(argv_list)
    _assert_no_invented_flags(argv)
    return AdapterInvocation(
        adapter="custom-cli",
        executable=exe,
        argv=argv,
        read_only=True,
        tool_scope="read-only",
        sandbox_scope="ephemeral",
        notes="user-defined wrapper; argv only, shell=False; transport envelope expected",
        input_mode="prompt-file",
        decoder="json-transport-envelope",
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


def parse_transport_output(stdout: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split adapter transport metadata from model-authored role report body.

    Preferred envelope::

        {
          "adapter_metadata": {"actual_model": "...", "source": "..."},
          "role_report": { ... schema fields ... }
        }

    Flat role-report JSON is accepted for transport parsing but yields empty
    metadata — model-authored ``actual_model`` is never treated as proof.
    """
    data = _extract_json_object(stdout)
    if isinstance(data.get("role_report"), dict):
        metadata = data.get("adapter_metadata") or data.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        return dict(metadata), dict(data["role_report"])
    # Flat report: no authoritative adapter metadata.
    return {}, dict(data)


def extract_authoritative_model(
    metadata: Mapping[str, Any] | None,
) -> tuple[str | None, str | None]:
    """Return (actual_model, evidence_source) from adapter/CLI metadata only.

    Never reads model-authored role-report fields.
    """
    if not metadata:
        return None, None
    model = metadata.get("actual_model")
    if model is None:
        model = metadata.get("model")
    # Nested shapes used by some CLI JSON envelopes.
    if model is None and isinstance(metadata.get("result"), dict):
        nested = metadata["result"]
        model = nested.get("actual_model") or nested.get("model")
    source = metadata.get("source") or metadata.get("evidence_source") or "adapter_metadata"
    if model is None or str(model).strip() == "":
        return None, str(source)
    return str(model), str(source)


def parse_role_report(
    stdout: str,
    *,
    expected_role: str | None = None,
    requested_model: str | None = None,
) -> dict[str, Any]:
    """Validate bounded role-report schema. Exit code alone is never enough.

    ``requested_model`` is accepted for API compatibility but is **not** used to
    validate model identity from the model-authored report body. Callers must
    use :func:`extract_authoritative_model` on transport metadata instead.
    """
    _ = requested_model  # intentionally unused for identity (model-authored untrusted)
    _metadata, data = parse_transport_output(stdout)
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


def validate_model_evidence(
    *,
    requested_model: str | None,
    metadata: Mapping[str, Any] | None,
    require_when_requested: bool = True,
) -> tuple[str | None, str | None]:
    """Validate authoritative model evidence for an attempt.

    Returns ``(actual_model, evidence_source)``. Raises ValidationIssue on
    missing/mismatched evidence when a model was requested or exact
    qualification is required.
    """
    actual, source = extract_authoritative_model(metadata)
    if requested_model is not None and require_when_requested:
        if actual is None:
            raise ValidationIssue(
                "actual_model_missing",
                (
                    f"requested_model `{requested_model}` requires authoritative "
                    "adapter/CLI actual_model metadata; model-authored report fields "
                    "are not proof"
                ),
                hint="Provide adapter_metadata.actual_model outside the role_report body",
            )
        if str(actual) != str(requested_model):
            raise ValidationIssue(
                "actual_model_mismatch",
                (
                    f"authoritative actual_model `{actual}` does not match "
                    f"requested_model `{requested_model}`"
                ),
            )
    return actual, source



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


# --- Write-capable profiles (Batch 4) -------------------------------------

GROK_WRITE_FORBIDDEN_ARGV_TOKENS: tuple[str, ...] = (
    "--dangerously-skip-permissions",
    "bypassPermissions",
    "--yolo",
    "--always-approve",
)


@dataclass(frozen=True)
class WriteCapabilityProfile:
    """Versioned write capability profile for an adapter."""

    adapter: str
    profile_name: str
    version: str | None
    qualified: bool
    detached_commits_permitted: bool
    require_detached_worktree: bool
    require_cwd_verification: bool
    require_worktree_registration: bool
    forbid_headless_worktree_resume: bool
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def grok_write_profile(version: str | None = "0.2.93") -> WriteCapabilityProfile:
    """Qualified Grok write profile with fail-closed isolation rules."""
    broken = version in {"0.2.93"} or version is None
    return WriteCapabilityProfile(
        adapter="grok-build",
        profile_name="grok-build-write",
        version=version,
        qualified=True,
        detached_commits_permitted=True,
        require_detached_worktree=True,
        require_cwd_verification=True,
        require_worktree_registration=True,
        forbid_headless_worktree_resume=broken or True,  # always forbid as isolation claim
        notes=(
            "Never use headless --worktree --resume as isolation for Grok Build 0.2.93; "
            "discover child session id and resume from registered detached worktree. "
            "Detached commits are untrusted handoff boundaries only."
        ),
    )


def workspace_sandbox_write_profile() -> WriteCapabilityProfile:
    """Negative fixture: workspace sandbox is not assumed commit-capable."""
    return WriteCapabilityProfile(
        adapter="grok-build",
        profile_name="grok-build-workspace-sandbox",
        version="0.2.93",
        qualified=False,
        detached_commits_permitted=False,
        require_detached_worktree=True,
        require_cwd_verification=True,
        require_worktree_registration=True,
        forbid_headless_worktree_resume=True,
        notes=(
            "workspace sandbox linked worktree cannot be assumed commit-capable; "
            "use devbox (or equivalent) for detached commit handoff"
        ),
    )


def build_write_resume_invocation(
    *,
    adapter: str,
    session_id: str,
    cwd: str,
    version: str | None = None,
    executable: str | None = None,
    requested_model: str | None = None,
    use_headless_worktree_resume: bool = False,
) -> AdapterInvocation:
    """Build a write-role resume argv with structural deny rules."""
    if adapter != "grok-build":
        # Reuse exact resume for other adapters; write qualification is lease-side.
        return build_session_resume_invocation(
            adapter=adapter,
            profile=adapter,
            session_id=session_id,
            executable=executable,
            requested_model=requested_model,
            cwd=cwd,
        )

    profile = grok_write_profile(version)
    if use_headless_worktree_resume and profile.forbid_headless_worktree_resume:
        raise ValidationIssue(
            "grok_headless_worktree_resume_forbidden",
            (
                f"Grok write profile forbids headless --worktree --resume as isolation "
                f"(version={version or 'unknown'})"
            ),
            hint="Resume exact child id from the registered worktree CWD only",
        )
    if not cwd or not str(cwd).strip():
        raise ValidationIssue(
            "write_cwd_required",
            "Write resume requires verified worker CWD/worktree path",
        )

    inv = build_session_resume_invocation(
        adapter="grok-build",
        profile=profile.profile_name,
        session_id=session_id,
        executable=executable,
        requested_model=requested_model,
        cwd=cwd,
    )
    joined = " ".join(inv.argv)
    for token in GROK_WRITE_FORBIDDEN_ARGV_TOKENS:
        if token in inv.argv or token in joined:
            raise ValidationIssue(
                "write_permission_bypass_forbidden",
                f"Write invocation contains forbidden token `{token}`",
            )
    # Explicitly reject worktree+resume combo in argv for isolation claims.
    if "--worktree" in inv.argv and "--resume" in inv.argv:
        raise ValidationIssue(
            "grok_headless_worktree_resume_forbidden",
            "Combined --worktree and --resume is not permitted as isolation",
        )
    return inv
