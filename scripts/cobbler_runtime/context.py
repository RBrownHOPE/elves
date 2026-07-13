"""Context packets, secret redaction, and minimal child environments.

Council lanes receive the same redacted packet shape. Private prompt/result
artifacts belong under ignored `.elves/runtime/council/<run-id>/` and must never
be committed as product state.
"""

from __future__ import annotations

import json
import os
import re
import stat
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .schema import ValidationIssue


# Names (not values) that must be stripped from child environments by default.
_SECRET_NAME_MARKERS: tuple[str, ...] = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "AUTHORIZATION",
    "AUTH",
    "COOKIE",
    "SESSION",
    "PRIVATE",
    "AWS_SECRET",
    "AWS_ACCESS",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "OPENROUTER",
    "ANTHROPIC",
    "OPENAI",
    "GEMINI",
    "XAI_",
    "GROK_",
    "API_KEY",
    "APIKEY",
    "BEARER",
)

# Minimal runtime discovery allowlist. extra_allowlist may add non-secret names
# only. Secret-looking names require an explicit profile secret_grants entry.
DEFAULT_ENV_ALLOWLIST: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TMPDIR",
        "TEMP",
        "TMP",
        "TERM",
        "TZ",
        "PWD",
        "SHELL",
        "XDG_RUNTIME_DIR",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "PYTHONPATH",
        "VIRTUAL_ENV",
        "SYSTEMROOT",
        "COMSPEC",
    }
)

# Patterns that look like secret values in free text. Matched spans are redacted;
# only the pattern *name* is reported, never the captured value.
SECRET_VALUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("bearer_token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-+=/]{8,}")),
    (
        "sk_token",
        re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{10,}"),
    ),
    ("xai_token", re.compile(r"\bxai-[A-Za-z0-9]{10,}")),
    ("github_pat", re.compile(r"\bghp_[A-Za-z0-9]{20,}")),
    ("github_oauth", re.compile(r"\bgho_[A-Za-z0-9]{20,}")),
    ("github_token", re.compile(r"\bgh[usr]_[A-Za-z0-9]{20,}")),
    ("github_fine_grained_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("pem_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)),
)

# Backward-compatible private alias for callers from earlier releases.  New
# security gates should use the public name so redaction and release scanning
# cannot silently drift apart.
_VALUE_PATTERNS = SECRET_VALUE_PATTERNS

ROLE_REPORT_SCHEMA_FIELDS: tuple[str, ...] = (
    "role",
    "verdict",
    "confidence",
    "key_findings",
    "evidence",
    "risks",
    "recommended_actions",
    "open_questions",
)


@dataclass(frozen=True)
class RedactionResult:
    """Redacted text plus the pattern names that fired (never secret values)."""

    text: str
    redacted_patterns: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "redacted_patterns": list(self.redacted_patterns),
        }


@dataclass(frozen=True)
class EnvScrubResult:
    """Minimal child environment and stripped variable names."""

    env: dict[str, str]
    stripped_names: tuple[str, ...] = ()
    kept_names: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "stripped_names": list(self.stripped_names),
            "kept_names": list(self.kept_names),
            # Never include env values in serializations used for logs/packets.
            "kept_count": len(self.kept_names),
            "stripped_count": len(self.stripped_names),
        }


@dataclass
class ContextPacket:
    """Bounded state every independent council lane receives."""

    task: str
    role: str
    mode: str
    scope: str
    relevant_files: list[str] = field(default_factory=list)
    plan_path: str | None = None
    survival_guide_path: str | None = None
    execution_log_path: str | None = None
    session_json_path: str | None = None
    head_sha: str | None = None
    current_date: str = field(default_factory=lambda: date.today().isoformat())
    output_schema: list[str] = field(
        default_factory=lambda: list(ROLE_REPORT_SCHEMA_FIELDS)
    )
    evidence_needs: list[str] = field(default_factory=list)
    forbidden_actions: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    requested_model: str | None = None
    profile: str | None = None
    adapter: str | None = None
    run_id: str | None = None
    redacted_patterns: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        # Drop empty extra for stable serialization.
        if not payload.get("extra"):
            payload.pop("extra", None)
        return payload


def is_secret_env_name(name: str) -> bool:
    upper = name.upper()
    if upper in DEFAULT_ENV_ALLOWLIST:
        return False
    for marker in _SECRET_NAME_MARKERS:
        if marker in upper:
            return True
    return False


def redact_text(
    text: str,
    *,
    exact_values: frozenset[str] | set[str] | tuple[str, ...] | None = None,
) -> RedactionResult:
    """Redact secret-looking values and exact granted secret values from free text.

    Pattern matches report pattern *names* only. Exact value redaction is required
    because regex shape matching alone cannot cover arbitrary API token values.
    """
    if not text:
        return RedactionResult(text="")
    redacted = text
    fired: list[str] = []
    # Exact values first (longest first to avoid partial overlaps).
    values = sorted(
        {v for v in (exact_values or ()) if isinstance(v, str) and v},
        key=len,
        reverse=True,
    )
    for value in values:
        if value and value in redacted:
            fired.append("exact_grant")
            redacted = redacted.replace(value, "[REDACTED:exact_grant]")
    for name, pattern in SECRET_VALUE_PATTERNS:
        if pattern.search(redacted):
            fired.append(name)
            redacted = pattern.sub(f"[REDACTED:{name}]", redacted)
    return RedactionResult(text=redacted, redacted_patterns=tuple(dict.fromkeys(fired)))


def redact_structure(
    value: Any,
    *,
    exact_values: frozenset[str] | set[str] | tuple[str, ...] | None = None,
) -> Any:
    """Recursively redact strings inside mappings/lists; leave non-strings intact."""
    if isinstance(value, str):
        return redact_text(value, exact_values=exact_values).text
    if isinstance(value, Mapping):
        return {
            str(k): redact_structure(v, exact_values=exact_values) for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact_structure(item, exact_values=exact_values) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_structure(item, exact_values=exact_values) for item in value)
    return value


def scrub_environment(
    parent_env: Mapping[str, str] | None = None,
    *,
    allowlist: frozenset[str] | None = None,
    extra_allowlist: frozenset[str] | set[str] | None = None,
    secret_grants: frozenset[str] | set[str] | tuple[str, ...] | None = None,
) -> EnvScrubResult:
    """Build a minimal child env. Report stripped *names*, never values.

    ``extra_allowlist`` may add non-secret discovery names. Secret-looking names
    remain stripped even when listed on ``extra_allowlist``. Only names listed
    in ``secret_grants`` (profile-declared) may enter the child environment when
    present in the parent. Values are never serialized in EnvScrubResult.
    """
    source = dict(parent_env if parent_env is not None else os.environ)
    allowed = set(allowlist if allowlist is not None else DEFAULT_ENV_ALLOWLIST)
    if extra_allowlist:
        allowed |= {name for name in extra_allowlist}
    grants = {name for name in (secret_grants or ()) if name}

    kept: dict[str, str] = {}
    stripped: list[str] = []
    for name, value in source.items():
        if name in grants:
            # Explicit profile grant — name only is recorded in metadata.
            kept[name] = value
            continue
        # Always strip secrets unless explicitly granted above.
        if is_secret_env_name(name) or name not in allowed:
            stripped.append(name)
            continue
        kept[name] = value

    return EnvScrubResult(
        env=kept,
        stripped_names=tuple(sorted(set(stripped))),
        kept_names=tuple(sorted(kept)),
    )


def default_forbidden_actions(*, read_only: bool = True) -> list[str]:
    actions = [
        "expose_secrets",
        "print_credentials",
        "inherit_full_parent_environment",
        "shell_interpolate_untrusted_task_text",
        "mutate_run_memory",
        "edit_docs_elves",
        "edit_elves_session_json",
    ]
    if read_only:
        actions.extend(
            [
                "edit_product_files",
                "git_commit",
                "git_push",
                "create_branch",
                "create_tag",
                "open_pr",
                "merge_pr",
                "install_packages",
            ]
        )
    return actions


def build_context_packet(
    *,
    task: str,
    role: str,
    mode: str = "read-only-council",
    scope: str = "read-only lens",
    relevant_files: list[str] | None = None,
    plan_path: str | None = None,
    survival_guide_path: str | None = None,
    execution_log_path: str | None = None,
    session_json_path: str | None = None,
    head_sha: str | None = None,
    current_date: str | None = None,
    evidence_needs: list[str] | None = None,
    forbidden_actions: list[str] | None = None,
    constraints: list[str] | None = None,
    requested_model: str | None = None,
    profile: str | None = None,
    adapter: str | None = None,
    run_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> ContextPacket:
    """Build a redacted context packet suitable for every independent lane."""
    redacted_task = redact_text(task)
    return ContextPacket(
        task=redacted_task.text,
        role=role,
        mode=mode,
        scope=scope,
        relevant_files=list(relevant_files or []),
        plan_path=plan_path,
        survival_guide_path=survival_guide_path,
        execution_log_path=execution_log_path,
        session_json_path=session_json_path,
        head_sha=head_sha,
        current_date=current_date or date.today().isoformat(),
        output_schema=list(ROLE_REPORT_SCHEMA_FIELDS),
        evidence_needs=list(evidence_needs or ["repo_facts", "tests", "docs"]),
        forbidden_actions=list(
            forbidden_actions
            if forbidden_actions is not None
            else default_forbidden_actions(read_only=True)
        ),
        constraints=list(constraints or []),
        requested_model=requested_model,
        profile=profile,
        adapter=adapter,
        run_id=run_id,
        redacted_patterns=list(redacted_task.redacted_patterns),
        extra=dict(extra or {}),
    )


def council_artifact_root(repo_root: Path, run_id: str) -> Path:
    """Return ignored runtime path `.elves/runtime/council/<run-id>/`."""
    return Path(repo_root) / ".elves" / "runtime" / "council" / run_id


_SAFE_PATH_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@+-]{0,127}$")


def safe_path_component(raw: str, *, field: str = "path_component") -> str:
    """Validate a single path component; encode traversal/unsafe forms."""
    value = (raw or "").strip()
    if not value:
        raise ValidationIssue(
            "invalid_path_component",
            f"Invalid empty {field}",
            path=field,
        )
    if (
        value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
        or not _SAFE_PATH_COMPONENT.match(value)
    ):
        # Encode unsafe names into a stable hex digest form rather than accepting them.
        digest = __import__("hashlib").sha256(value.encode("utf-8", errors="replace")).hexdigest()[:24]
        return f"enc_{digest}"
    return value


def resolve_contained_path(root: Path, *parts: str) -> Path:
    """Join parts under root and prove the resolved path stays contained."""
    root_resolved = Path(root).resolve()
    safe_parts = [safe_path_component(part, field=f"part[{idx}]") for idx, part in enumerate(parts)]
    candidate = root_resolved.joinpath(*safe_parts).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ValidationIssue(
            "path_escape",
            f"Resolved path escapes artifact root: {candidate}",
            path=str(candidate),
        ) from exc
    return candidate


def ensure_private_dir(path: Path) -> Path:
    """Create a directory with owner-only permissions when the OS supports it."""
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(stat.S_IRWXU)  # 0o700
    except OSError:
        # Non-POSIX filesystems may ignore mode bits; still usable.
        pass
    return path


def _assert_no_symlink_escape(path: Path, *, repo_root: Path) -> None:
    """Reject path components that are symlinks escaping the canonical repo root."""
    repo = Path(repo_root).resolve()
    current = Path(path)
    # Walk from repo toward path, creating nothing; check existing components.
    try:
        relative = current.resolve(strict=False).relative_to(repo)
    except ValueError as exc:
        raise ValidationIssue(
            "artifact_root_escape",
            f"Artifact path escapes repository root: {path}",
            path=str(path),
        ) from exc
    cursor = repo
    for part in relative.parts:
        cursor = cursor / part
        if cursor.exists() or cursor.is_symlink():
            if cursor.is_symlink():
                target = cursor.resolve()
                try:
                    target.relative_to(repo)
                except ValueError as exc:
                    raise ValidationIssue(
                        "artifact_root_symlink_escape",
                        f"Symlink component `{cursor}` escapes repository root",
                        path=str(cursor),
                    ) from exc


def create_exclusive_artifact_root(repo_root: Path, run_id: str) -> Path:
    """Create a council artifact root atomically; refuse reuse of an existing id.

    Fails closed if ``run_id`` already has a directory so stale evidence cannot
    be silently overwritten or shared across concurrent runs. Rejects symlink
    parents that escape the canonical repository root.
    """
    repo = Path(repo_root).resolve()
    rid = safe_path_component((run_id or "").strip(), field="run_id")
    root = council_artifact_root(repo, rid)
    # Ensure intermediate parents (.elves, runtime, council) are not escape symlinks.
    for parent in [repo / ".elves", repo / ".elves" / "runtime", repo / ".elves" / "runtime" / "council"]:
        if parent.exists() or parent.is_symlink():
            _assert_no_symlink_escape(parent, repo_root=repo)
    parent = root.parent
    parent.mkdir(parents=True, exist_ok=True)
    _assert_no_symlink_escape(parent, repo_root=repo)
    try:
        parent.chmod(stat.S_IRWXU)
    except OSError:
        pass
    try:
        # Exclusive create: raises FileExistsError if the path already exists.
        os.mkdir(root, mode=0o700)
    except FileExistsError as exc:
        raise ValidationIssue(
            "artifact_root_exists",
            f"Council artifact root already exists for run_id `{rid}`; refuse reuse",
            path=str(root),
            hint="Allocate a fresh collision-resistant run_id",
        ) from exc
    except OSError as exc:
        raise ValidationIssue(
            "artifact_root_create_failed",
            f"Unable to create exclusive artifact root for run_id `{rid}`: {exc}",
            path=str(root),
        ) from exc
    try:
        root.chmod(stat.S_IRWXU)
    except OSError:
        pass
    resolved = root.resolve()
    try:
        resolved.relative_to(repo)
    except ValueError as exc:
        raise ValidationIssue(
            "artifact_root_escape",
            f"Created artifact root escapes repository: {resolved}",
            path=str(resolved),
        ) from exc
    return resolved


def write_json_artifact(path: Path, payload: Mapping[str, Any]) -> Path:
    """Write JSON with restrictive file permissions (owner read/write)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(dict(payload), indent=2, sort_keys=True) + "\n"
    path.write_text(data, encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0o600
    except OSError:
        pass
    return path


def write_text_artifact(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return path


def new_run_id(prefix: str = "council") -> str:
    """Return a collision-resistant run id (timestamp + uuid4 fragment)."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    unique = uuid.uuid4().hex
    safe_prefix = (prefix or "council").replace("/", "-").replace(" ", "-")
    return f"{safe_prefix}-{stamp}-{unique}"
