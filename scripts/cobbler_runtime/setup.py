"""Setup UX for Cobbler external-agent preferences.

Inventory executables/capabilities without printing credentials or launching paid
model turns unless the caller explicitly opts into a smoke. Generate or update
the intentionally ignored local `.elves/models.toml`. Never stage that file.

Public default remains native-only with zero external tools or keys.
"""

from __future__ import annotations

import re
import shutil
import stat
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence  # Any used by PROFILE_RECIPES

from .adapters import default_profiles
from .capabilities import doctor_inventory
from .config import models_toml_is_local_only, resolve_config
from .schema import NATIVE_PROFILE_NAME, RoleName, ValidationIssue


# Role slots users can prefer during setup (operations, not model identities).
SETUP_ROLE_SLOTS: tuple[str, ...] = (
    "planning",
    "implement",
    "lightweight_review",
    "validate",
    "review",
    "synthesize",
    "scout",
)

# Env var *names* only — never values.
OPTIONAL_ENV_NAMES: tuple[str, ...] = (
    "OPENROUTER_API_KEY",
    "META_API_KEY",
    "MODEL_API_KEY",
    "ANTHROPIC_API_KEY",
    "XAI_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "EXA_API_KEY",
)

_SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*=\s*[\"']?[^$\"'\s#]+"),
    re.compile(r"\bsk-[A-Za-z0-9]{10,}"),
    re.compile(r"\bxai-[A-Za-z0-9]{10,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}"),
    re.compile(r"/Users/[^\s\"']+"),
    re.compile(r"/home/[^\s\"']+"),
)


@dataclass
class ToolInventoryItem:
    adapter: str
    executable: str | None
    present: bool
    version: str | None = None
    auth: str = "unknown"
    session_support: str = "unknown"
    write_qualified: bool = False
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SetupPreferences:
    """User/role preferences collected by setup (non-secret)."""

    roles: dict[str, str] = field(default_factory=dict)
    fallbacks: dict[str, list[str]] = field(default_factory=dict)
    required_roles: list[str] = field(default_factory=list)
    session_mode: str = "ephemeral"
    sharing_policy: str = "local-only"
    document_owner: str = "host-coordinator"
    usage_budget_warning: int | None = None
    run_smoke: bool = False
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SetupResult:
    ok: bool
    inventory: list[ToolInventoryItem] = field(default_factory=list)
    preferences: SetupPreferences = field(default_factory=SetupPreferences)
    models_toml_path: str | None = None
    models_toml_written: bool = False
    models_toml_ignored: bool = True
    survival_guide_snapshot: dict[str, Any] = field(default_factory=dict)
    recommendations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    issues: list[dict[str, Any]] = field(default_factory=list)
    smoke_ran: bool = False
    credentials_printed: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "inventory": [item.to_dict() for item in self.inventory],
            "preferences": self.preferences.to_dict(),
            "models_toml_path": self.models_toml_path,
            "models_toml_written": self.models_toml_written,
            "models_toml_ignored": self.models_toml_ignored,
            "survival_guide_snapshot": self.survival_guide_snapshot,
            "recommendations": list(self.recommendations),
            "warnings": list(self.warnings),
            "issues": list(self.issues),
            "smoke_ran": self.smoke_ran,
            "credentials_printed": self.credentials_printed,
            "notes": list(self.notes),
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def which_executable(name: str | None) -> str | None:
    if not name or name.startswith("("):
        return None
    return shutil.which(name)


# Named profiles beyond bare adapter names: planning-quality vs labor tiers, Google plan/review.
PROFILE_RECIPES: dict[str, dict[str, Any]] = {
    NATIVE_PROFILE_NAME: {"adapter": NATIVE_PROFILE_NAME},
    "claude-code": {"adapter": "claude-code"},
    "claude-code-planning": {
        "adapter": "claude-code",
        "notes": "High-quality Claude for planning/review (set requested_model in TOML if desired)",
        "tier": "planning",
    },
    "claude-code-labor": {
        "adapter": "claude-code",
        "notes": "Volume Claude for implement labor (cheaper/faster model via requested_model)",
        "tier": "labor",
    },
    "grok-build": {"adapter": "grok-build"},
    "codex-fugu": {"adapter": "codex-fugu"},
    "codex-fugu-planning": {
        "adapter": "codex-fugu",
        "notes": "High-quality Codex for planning/review (set requested_model in TOML if desired)",
        "tier": "planning",
    },
    "codex-fugu-labor": {
        "adapter": "codex-fugu",
        "notes": "Volume Codex for implement labor (cheaper/faster model via requested_model)",
        "tier": "labor",
    },
    "gemini-cli": {
        "adapter": "gemini-cli",
        "executable": "gemini",
        "notes": "Google Gemini CLI — plan/review only; not recommended for bulk implement",
        "plan_review_only": True,
    },
    "antigravity-cli": {
        "adapter": "antigravity-cli",
        "executable": "antigravity",
        "notes": "Google Antigravity CLI — plan/review only; not recommended for bulk implement",
        "plan_review_only": True,
    },
    "custom-cli": {"adapter": "custom-cli"},
}


def inventory_tools(
    *,
    fake_presence: Mapping[str, bool] | None = None,
    fake_versions: Mapping[str, str | None] | None = None,
    fake_auth: Mapping[str, str] | None = None,
) -> list[ToolInventoryItem]:
    """Inventory built-in adapters without reading secret values."""
    fake_presence = fake_presence or {}
    fake_versions = fake_versions or {}
    fake_auth = fake_auth or {}
    items: list[ToolInventoryItem] = []
    for name, profile in sorted(default_profiles().items()):
        if name == NATIVE_PROFILE_NAME:
            items.append(
                ToolInventoryItem(
                    adapter=name,
                    executable=None,
                    present=True,
                    version="host",
                    auth="n/a",
                    session_support="unavailable",
                    write_qualified=True,
                    notes="Host coordinator always available; no external key required",
                )
            )
            continue
        exe = profile.executable
        if name in fake_presence:
            present = bool(fake_presence[name])
        else:
            present = which_executable(exe) is not None
            if name == "antigravity-cli" and not present:
                present = which_executable("agy") is not None
                if present:
                    exe = "agy"
        version = fake_versions.get(name)
        auth = fake_auth.get(name, "unknown" if present else "missing")
        # Never infer auth from env var *presence of values* — only name existence as hint.
        write_qualified = False
        if name == "grok-build" and present:
            write_qualified = True  # still requires lease/devbox qualification at runtime
        session = "advertised" if present else "unavailable"
        notes = ""
        if name == "grok-build":
            notes = "Headless worktree-resume broken on 0.2.93; use exact child + registered worktree"
        if name == "custom-cli":
            notes = "User-defined wrapper; qualify capabilities before write roles"
        if name in {"gemini-cli", "antigravity-cli"}:
            notes = "Google subscription CLI — prefer planning/review; avoid bulk implement cost"
        items.append(
            ToolInventoryItem(
                adapter=name,
                executable=exe,
                present=present,
                version=version,
                auth=auth,
                session_support=session,
                write_qualified=write_qualified,
                notes=notes,
            )
        )
    return items


def recommend_routes(inventory: Sequence[ToolInventoryItem]) -> list[str]:
    """Capability-first recommendations with dates — not prestige model names."""
    present = {item.adapter for item in inventory if item.present}
    recs: list[str] = [
        f"Generated {_utc_now()}: prefer host-native for validate/synthesize by default.",
        "Setup is optional; native-only Elves needs no external tools or keys.",
        "Commit/push/PR are host operations, not model roles.",
        "Within a family, prefer a high-quality model for plan/review and a labor model for implement.",
    ]
    if "claude-code" in present:
        recs.append(
            "claude-code present: use claude-code-planning for plan/review and "
            "claude-code-labor for implement volume when you want a tier split; "
            "set requested_model on each profile in models.toml (no public prestige defaults)."
        )
    if "grok-build" in present:
        recs.append(
            "grok-build present: candidate for isolated implementation under a writer lease "
            "with verified detached worktree; never treat headless worktree-resume as isolation on 0.2.93."
        )
    if "codex-fugu" in present:
        recs.append(
            "codex-fugu present: use codex-fugu-planning vs codex-fugu-labor for plan/review vs "
            "implement volume; MCP OAuth warnings are not inference failures."
        )
    if "gemini-cli" in present or "antigravity-cli" in present:
        recs.append(
            "Google Gemini CLI / Antigravity CLI present: good optional plan/review lenses; "
            "usually not cost-effective for the main implement batch."
        )
    if present <= {NATIVE_PROFILE_NAME}:
        recs.append("No external CLIs detected: stay fully native-host for all roles.")
    else:
        recs.append(
            "Map optional external adapters by capability (session/write/read-only), "
            "and keep native fallbacks for every optional role."
        )
    recs.append(
        "OpenRouter/API-only routes (when configured) are optional read-only breadth; "
        "they cannot edit worktrees unless a qualified wrapper proves write/isolation."
    )
    return recs


def assert_toml_has_no_secrets(text: str) -> None:
    for pattern in _SECRET_VALUE_PATTERNS:
        if pattern.search(text):
            raise ValidationIssue(
                "secret_or_path_in_toml",
                "Generated or provided models.toml appears to contain a secret or personal path",
                hint="Use environment variable names only; never paste key values or /Users/... paths",
            )


def render_models_toml(preferences: SetupPreferences) -> str:
    """Render a local models.toml from preferences (no credentials)."""
    lines: list[str] = [
        "# Local Cobbler external-agent preferences (machine-local; do not stage)",
        f"# Generated by cobbler_agents setup at {_utc_now()}",
        "# Precedence: Survival Guide > this file > config.json > native defaults",
        "# Never put raw credentials or personal absolute paths in this file.",
        "# optional env var names only, e.g. OPENROUTER_API_KEY",
        "",
        f'sharing_policy = "{preferences.sharing_policy}"',
        f'document_owner = "{preferences.document_owner}"',
        f'session_mode_default = "{preferences.session_mode}"',
        "",
    ]
    if preferences.usage_budget_warning is not None:
        lines.append(f"usage_budget_warning_tokens = {int(preferences.usage_budget_warning)}")
        lines.append("")

    # Profiles referenced by roles
    profiles_needed: set[str] = set()
    for profile in preferences.roles.values():
        profiles_needed.add(profile)
    for chain in preferences.fallbacks.values():
        profiles_needed.update(chain)
    profiles_needed.discard("")

    for profile_name in sorted(profiles_needed):
        recipe = PROFILE_RECIPES.get(profile_name)
        if recipe is None:
            adapter = "custom-cli"
            executable = None
            notes = "Unknown profile name; treat as custom-cli and set executable"
        else:
            adapter = str(recipe.get("adapter") or "custom-cli")
            executable = recipe.get("executable")
            notes = recipe.get("notes")
        lines.append(f"[profiles.{profile_name}]")
        lines.append(f'adapter = "{adapter}"')
        if executable:
            lines.append(f'executable = "{executable}"')
        if adapter == "custom-cli" and profile_name != "custom-cli" and not executable:
            lines.append('executable = "my-coding-agent"')
            notes = notes or "Replace executable for your wrapper"
        if notes:
            # Escape quotes in notes for TOML single-line strings
            safe = str(notes).replace('"', "'")
            lines.append(f'notes = "{safe}"')
        # Tier profiles: leave requested_model commented for the user to pin a local model id.
        if recipe and recipe.get("tier") in {"planning", "labor"}:
            lines.append(
                f'# requested_model = "…"  # optional: pin high-quality vs labor model for this tier'
            )
        if recipe and recipe.get("plan_review_only"):
            lines.append(
                '# Prefer this profile on planning/review/scout roles only — not bulk implement.'
            )
        lines.append("")

    for role in SETUP_ROLE_SLOTS:
        profile = preferences.roles.get(role, NATIVE_PROFILE_NAME)
        required = role in preferences.required_roles
        lines.append(f"[roles.{role}]")
        lines.append(f'profile = "{profile}"')
        lines.append(f"required = {'true' if required else 'false'}")
        chain = preferences.fallbacks.get(role) or []
        if chain:
            lines.append("fallback_chain = [")
            for entry in chain:
                lines.append(
                    f'  {{ profile = "{entry}", reason = "setup-configured fallback" }},'
                )
            lines.append("]")
        lines.append("")

    lines.append("# Optional env var names for provider-backed breadth (values live in the environment):")
    lines.append("# optional_env = [")
    for name in OPTIONAL_ENV_NAMES:
        lines.append(f'#   "{name}",')
    lines.append("# ]")
    lines.append("")
    text = "\n".join(lines)
    assert_toml_has_no_secrets(text)
    return text


def write_models_toml(
    repo_root: Path,
    text: str,
    *,
    force: bool = False,
    preserve_existing_if_unknown_sections: bool = True,
) -> tuple[Path, bool]:
    """Write ignored local models.toml. Refuses lossy overwrite when unknown sections exist."""
    assert_toml_has_no_secrets(text)
    path = Path(repo_root) / ".elves" / "models.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and not force:
        existing = path.read_text(encoding="utf-8")
        # Heuristic: unknown top-level sections beyond profiles/roles/comments
        if preserve_existing_if_unknown_sections and re.search(
            r"^\[(?!profiles\.|roles\.)[^\]]+\]", existing, re.M
        ):
            raise ValidationIssue(
                "models_toml_unknown_sections",
                f"`{path}` has unknown sections; refusing lossy rewrite",
                path=str(path),
                hint="Pass force=True only after reviewing the file, or merge manually",
            )
    path.write_text(text, encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return path, True


def survival_guide_route_snapshot(
    preferences: SetupPreferences,
    *,
    inventory: Sequence[ToolInventoryItem],
) -> dict[str, Any]:
    """Committed-route-snapshot *shape* for Survival Guide (host writes the file)."""
    present = sorted(item.adapter for item in inventory if item.present)
    return {
        "document_owner": preferences.document_owner,
        "sharing_policy": preferences.sharing_policy,
        "session_mode_default": preferences.session_mode,
        "roles": {
            role: {
                "profile": preferences.roles.get(role, NATIVE_PROFILE_NAME),
                "required": role in preferences.required_roles,
                "fallback_chain": list(preferences.fallbacks.get(role) or []),
                "source": "setup_preferences",
            }
            for role in SETUP_ROLE_SLOTS
        },
        "inventory_present": present,
        "notes": [
            "Host coordinator should paste/adapt this snapshot into the Survival Guide "
            "model-routing block during staging for reviewable provenance.",
            "Local .elves/models.toml remains ignored machine preference.",
        ],
        "generated_at": _utc_now(),
    }


def preferences_from_flags(
    *,
    implement: str | None = None,
    review: str | None = None,
    planning: str | None = None,
    lightweight_review: str | None = None,
    validate: str | None = None,
    synthesize: str | None = None,
    scout: str | None = None,
    required: Sequence[str] | None = None,
    session_mode: str = "ephemeral",
    sharing_policy: str = "local-only",
    native_fallback: bool = True,
) -> SetupPreferences:
    """Deterministic non-interactive preference builder."""
    roles = {role: NATIVE_PROFILE_NAME for role in SETUP_ROLE_SLOTS}
    mapping = {
        "implement": implement,
        "review": review,
        "planning": planning,
        "lightweight_review": lightweight_review,
        "validate": validate,
        "synthesize": synthesize,
        "scout": scout,
    }
    for role, value in mapping.items():
        if value:
            roles[role] = value
    fallbacks: dict[str, list[str]] = {}
    if native_fallback:
        for role, profile in roles.items():
            if profile != NATIVE_PROFILE_NAME:
                fallbacks[role] = [NATIVE_PROFILE_NAME]
    required_roles = [r for r in (required or []) if r in SETUP_ROLE_SLOTS]
    # validate/synthesize default required for safety of host ownership messaging
    return SetupPreferences(
        roles=roles,
        fallbacks=fallbacks,
        required_roles=required_roles,
        session_mode=session_mode,
        sharing_policy=sharing_policy,
        document_owner="host-coordinator",
    )


def run_setup(
    repo_root: Path,
    *,
    preferences: SetupPreferences | None = None,
    write_toml: bool = True,
    force_toml: bool = False,
    run_smoke: bool = False,
    smoke_executor: Any | None = None,
    dry_run: bool = False,
    fake_presence: Mapping[str, bool] | None = None,
    fake_versions: Mapping[str, str | None] | None = None,
    fake_auth: Mapping[str, str] | None = None,
) -> SetupResult:
    """Run setup inventory + optional local models.toml generation.

    Never prints credentials. ``run_smoke`` is opt-in and sets ``smoke_ran`` only
    when ``smoke_executor`` returns a valid non-empty model response. Dry-run and
    default paths write nothing unless ``write_toml`` is True and not dry-run.
    """
    root = Path(repo_root)
    inventory = inventory_tools(
        fake_presence=fake_presence,
        fake_versions=fake_versions,
        fake_auth=fake_auth,
    )
    prefs = preferences or preferences_from_flags()
    result = SetupResult(
        ok=True,
        inventory=inventory,
        preferences=prefs,
        recommendations=recommend_routes(inventory),
        notes=[
            "Never stage .elves/models.toml",
            "Never paste API keys into TOML, chat, or Survival Guide",
            "Commit/push/PR are host operations, not model roles",
            "Setup is optional for native-only Elves",
        ],
        credentials_printed=False,
        smoke_ran=False,
    )

    if run_smoke:
        if smoke_executor is None:
            result.warnings.append(
                "Smoke requested but no smoke_executor was provided; smoked=false "
                "(acknowledgment alone is not a model response)"
            )
        else:
            try:
                smoke_result = smoke_executor(inventory=inventory, preferences=prefs)
            except Exception as exc:  # noqa: BLE001 — surface as setup issue, not crash
                result.ok = False
                result.issues.append(
                    {
                        "code": "smoke_failed",
                        "message": f"Smoke executor failed: {type(exc).__name__}: {exc}",
                    }
                )
                smoke_result = None
            if isinstance(smoke_result, Mapping):
                text = str(
                    smoke_result.get("text")
                    or smoke_result.get("content")
                    or smoke_result.get("message")
                    or ""
                ).strip()
                model = smoke_result.get("actual_model") or smoke_result.get("model")
                if text and model:
                    result.smoke_ran = True
                    result.notes.append(
                        f"Smoke succeeded with actual_model={model} (credentials not printed)"
                    )
                else:
                    result.warnings.append(
                        "Smoke executor returned no valid model response; smoked=false"
                    )
            elif smoke_result:
                result.warnings.append(
                    "Smoke executor returned a non-mapping result; smoked=false"
                )

    # Validate required roles against inventory presence.
    present = {item.adapter for item in inventory if item.present}
    for role in prefs.required_roles:
        profile = prefs.roles.get(role, NATIVE_PROFILE_NAME)
        if profile != NATIVE_PROFILE_NAME and profile not in present:
            result.ok = False
            result.issues.append(
                {
                    "code": "required_route_unavailable",
                    "message": (
                        f"Required role `{role}` maps to `{profile}` but that tool is not present"
                    ),
                }
            )

    meta = models_toml_is_local_only(root)
    result.models_toml_ignored = bool(meta.get("ignored_by_gitignore", True))
    result.models_toml_path = str(root / ".elves" / "models.toml")

    should_write = write_toml and result.ok and not dry_run
    if dry_run:
        result.notes.append("Dry-run: no files written")
    if should_write:
        try:
            text = render_models_toml(prefs)
            path, written = write_models_toml(root, text, force=force_toml)
            result.models_toml_written = written
            result.models_toml_path = str(path)
            # Prove generated TOML resolves (when tomllib available).
            try:
                import tomllib as _tomllib  # noqa: PLC0415

                parsed = _tomllib.loads(text)
                resolved = resolve_config(models_toml=parsed)
                if not resolved.ok:
                    result.warnings.append(
                        "Generated models.toml has validation issues: "
                        + "; ".join(i.message for i in resolved.issues)
                    )
            except ModuleNotFoundError:
                result.warnings.append(
                    "tomllib unavailable; generated file written but not parse-validated on this Python"
                )
        except ValidationIssue as issue:
            result.ok = False
            result.issues.append(issue.to_dict())

    result.survival_guide_snapshot = survival_guide_route_snapshot(
        prefs, inventory=inventory
    )
    # Doctor inventory shape available for richer reports without secrets.
    _ = doctor_inventory(default_profiles())
    return result
