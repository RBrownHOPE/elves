"""Model onboarding: plan, show, apply, probe.

Host agents (Claude Code or Codex) walk the user through purpose→route choices.
This module is deterministic CLI support: inventory, preference I/O, and probes.
Never print credential values. Paid smokes are opt-in only.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .adapters import default_profiles
from .setup import (
    OPTIONAL_ENV_NAMES,
    SETUP_ROLE_SLOTS,
    inventory_tools,
    preferences_from_flags,
    recommend_routes,
    run_setup,
)


# Purposes the user assigns tools to. Core map to setup role slots.
# Google subscription CLIs are plan/review-oriented (usually not cost-effective for bulk implement).
# Claude/Codex support planning vs labor profile tiers within the same family.
PURPOSE_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "id": "planning",
        "role_slot": "planning",
        "label": "Planning / design",
        "description": "Contracts, batch design, architecture choices (prefer high-quality model)",
        "default_route": "host-native",
        "suggested_routes": (
            "host-native",
            "claude-code-planning",
            "codex-fugu-planning",
            "claude-code",
            "codex-fugu",
            "gemini-cli",
            "antigravity-cli",
        ),
        "required": False,
    },
    {
        "id": "implement",
        "role_slot": "implement",
        "label": "Implementation (labor)",
        "description": (
            "Writing code for a batch — prefer host-native, labor-tier Claude/Codex, or Grok; "
            "Google Gemini/Antigravity CLIs are usually not cost-effective for the main batch"
        ),
        "default_route": "host-native",
        "suggested_routes": (
            "host-native",
            "claude-code-labor",
            "codex-fugu-labor",
            "grok-build",
            "claude-code",
            "codex-fugu",
        ),
        "required": False,
    },
    {
        "id": "review",
        "role_slot": "review",
        "label": "Independent review",
        "description": "Read-only critique (prefer high-quality model / optional Google lenses)",
        "default_route": "host-native",
        "suggested_routes": (
            "host-native",
            "claude-code-planning",
            "codex-fugu-planning",
            "claude-code",
            "codex-fugu",
            "gemini-cli",
            "antigravity-cli",
            "openrouter",
            "meta-muse",
        ),
        "required": False,
    },
    {
        "id": "lightweight_review",
        "role_slot": "lightweight_review",
        "label": "Quick utility review",
        "description": "Fast single-lens checks",
        "default_route": "host-native",
        "suggested_routes": (
            "host-native",
            "claude-code-labor",
            "codex-fugu-labor",
            "claude-code",
            "codex-fugu",
            "gemini-cli",
        ),
        "required": False,
    },
    {
        "id": "scout",
        "role_slot": "scout",
        "label": "Scouting / discovery",
        "description": "Breadth search, math Discovery Sprint lanes",
        "default_route": "host-native",
        "suggested_routes": (
            "host-native",
            "claude-code",
            "gemini-cli",
            "antigravity-cli",
            "openrouter",
            "meta-muse",
        ),
        "required": False,
    },
    {
        "id": "validate",
        "role_slot": "validate",
        "label": "Validation ownership",
        "description": "Who owns gates (prefer host-native)",
        "default_route": "host-native",
        "suggested_routes": ("host-native",),
        "required": True,
    },
    {
        "id": "synthesize",
        "role_slot": "synthesize",
        "label": "Synthesis / fitted answer",
        "description": "Who fits one answer (prefer host-native)",
        "default_route": "host-native",
        "suggested_routes": ("host-native",),
        "required": True,
    },
    {
        "id": "math_evolutionary_search",
        "role_slot": None,
        "label": "Math evolutionary search (AlphaEvolve)",
        "description": "Optional numerical examples / counterexample search when GCP runner exists",
        "default_route": "off",
        "suggested_routes": ("off", "alphaevolve"),
        "required": False,
        "optional_tool": "alphaevolve",
    },
)

# Extended env names (presence only).
ONBOARD_ENV_NAMES: tuple[str, ...] = tuple(
    dict.fromkeys(
        (
            *OPTIONAL_ENV_NAMES,
            "META_API_KEY",
            "MODEL_API_KEY",
            "EXA_API_KEY",
        )
    )
)

ROUTE_HELP: dict[str, str] = {
    "host-native": "Current host agent (Claude Code or Codex) — default, always available",
    "claude-code": "Claude Code CLI (default model for the install)",
    "claude-code-planning": "Claude Code high-quality tier for plan/review (pin requested_model in TOML)",
    "claude-code-labor": "Claude Code labor tier for implement volume (pin requested_model in TOML)",
    "grok-build": "Grok Build CLI for optional external implement batches",
    "codex-fugu": "Codex/Fugu CLI (default model for the install)",
    "codex-fugu-planning": "Codex high-quality tier for plan/review (pin requested_model in TOML)",
    "codex-fugu-labor": "Codex labor tier for implement volume (pin requested_model in TOML)",
    "gemini-cli": "Google Gemini CLI — plan/review/scout; usually not cost-effective for bulk implement",
    "antigravity-cli": "Google Antigravity CLI — plan/review; usually not cost-effective for bulk implement",
    "openrouter": "OpenRouter models via project wrapper + OPENROUTER_API_KEY (read-only breadth)",
    "meta-muse": "Meta Muse Spark 1.1 via project wrapper + META_API_KEY (read-only plan/review)",
    "alphaevolve": "Google Cloud AlphaEvolve for evolutionary example search (math module)",
    "off": "Disabled for this purpose",
    "custom-cli": "User wrapper executable (qualify before write roles)",
}

# Map onboard route labels → inventory adapter / presence check.
_ROUTE_PRESENCE_ADAPTER: dict[str, str] = {
    "claude-code": "claude-code",
    "claude-code-planning": "claude-code",
    "claude-code-labor": "claude-code",
    "grok-build": "grok-build",
    "codex-fugu": "codex-fugu",
    "codex-fugu-planning": "codex-fugu",
    "codex-fugu-labor": "codex-fugu",
    "gemini-cli": "gemini-cli",
    "antigravity-cli": "antigravity-cli",
    "custom-cli": "custom-cli",
}


@dataclass
class ProbeResult:
    route: str
    purpose: str | None
    status: str  # pass | warn | fail | skip
    detail: str
    kind: str = "structural"  # structural | live_smoke

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OnboardPacket:
    """Payload the host agent uses to interview the user and apply choices."""

    generated_at: str
    host_hints: dict[str, str]
    inventory: list[dict[str, Any]] = field(default_factory=list)
    env_present: dict[str, bool] = field(default_factory=dict)
    current_roles: dict[str, str] = field(default_factory=dict)
    purposes: list[dict[str, Any]] = field(default_factory=list)
    questions: list[dict[str, Any]] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def env_name_presence(
    names: Sequence[str] = ONBOARD_ENV_NAMES,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, bool]:
    """Report whether env var *names* are set. Never returns values."""
    env = environ if environ is not None else os.environ
    out: dict[str, bool] = {}
    for name in names:
        val = env.get(name)
        out[name] = bool(val and str(val).strip())
    return out


def load_role_profiles_from_models_toml(repo_root: Path) -> dict[str, str]:
    """Read current role→profile map from ignored models.toml if present."""
    path = Path(repo_root) / ".elves" / "models.toml"
    roles = {role: "host-native" for role in SETUP_ROLE_SLOTS}
    if not path.is_file():
        return roles
    try:
        import tomllib
    except ModuleNotFoundError:
        return roles
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return roles
    for role, body in (data.get("roles") or {}).items():
        if role in roles and isinstance(body, dict) and body.get("profile"):
            roles[role] = str(body["profile"])
    return roles


def build_onboarding_packet(
    repo_root: Path,
    *,
    fake_presence: Mapping[str, bool] | None = None,
    environ: Mapping[str, str] | None = None,
) -> OnboardPacket:
    inventory = inventory_tools(fake_presence=fake_presence)
    env_present = env_name_presence(environ=environ)
    current = load_role_profiles_from_models_toml(repo_root)
    present_adapters = sorted(i.adapter for i in inventory if i.present)

    questions: list[dict[str, Any]] = []
    for purpose in PURPOSE_CATALOG:
        options = []
        for route in purpose["suggested_routes"]:
            available = True
            adapter_key = _ROUTE_PRESENCE_ADAPTER.get(route)
            if adapter_key:
                available = adapter_key in present_adapters
            if route == "openrouter":
                available = env_present.get("OPENROUTER_API_KEY", False)
            if route == "meta-muse":
                available = env_present.get("META_API_KEY", False) or env_present.get(
                    "MODEL_API_KEY", False
                )
            if route == "alphaevolve":
                available = shutil.which("gcloud") is not None
            if route == "host-native":
                available = True
            options.append(
                {
                    "route": route,
                    "help": ROUTE_HELP.get(route, route),
                    "available_hint": available,
                }
            )
        current_route = purpose["default_route"]
        slot = purpose.get("role_slot")
        if slot and slot in current:
            current_route = current[slot]
        questions.append(
            {
                "purpose_id": purpose["id"],
                "prompt": (
                    f"For **{purpose['label']}** ({purpose['description']}), "
                    f"which route do you want? Current/default: `{current_route}`."
                ),
                "options": options,
                "current": current_route,
                "allow_custom": True,
            }
        )

    notes = [
        "Native-only is always valid: leave everything host-native and skip optional tools.",
        "Never paste API keys into chat, models.toml, or the Survival Guide.",
        "Commit/push/PR are host operations, not model roles.",
        "After apply, run `onboard probe` (and optional `--smoke`) to verify routes.",
        "Update anytime: re-run onboarding or `onboard apply` with new flags.",
        "Claude Code: /setup-cobbler or natural language. Codex: $elves setup-cobbler / natural language.",
        "Tier split: high-quality Claude/Codex for plan+review, labor model for implement "
        "(claude-code-planning / claude-code-labor, codex-fugu-planning / codex-fugu-labor).",
        "Google Gemini CLI / Antigravity CLI: good for plan/review; usually not for bulk implement.",
    ]
    if env_present.get("OPENROUTER_API_KEY"):
        notes.append("OPENROUTER_API_KEY is set: OpenRouter models can be offered for review/scout.")
    if env_present.get("META_API_KEY") or env_present.get("MODEL_API_KEY"):
        notes.append("Meta API key name is set: Muse Spark can be offered for plan/review breadth.")
    if shutil.which("gcloud"):
        notes.append("gcloud present: AlphaEvolve may be available if the project has a runner.")

    return OnboardPacket(
        generated_at=_utc_now(),
        host_hints={
            "claude_code": "/setup-cobbler or natural language: set up my model routes",
            "codex": "$elves setup-cobbler or natural language (not a top-level Codex slash)",
            "cli_plan": "python3 scripts/cobbler_agents.py onboard plan --json",
            "cli_apply": "python3 scripts/cobbler_agents.py onboard apply --planning … --review …",
            "cli_show": "python3 scripts/cobbler_agents.py onboard show --json",
            "cli_probe": "python3 scripts/cobbler_agents.py onboard probe --json",
            "cli_probe_smoke": "python3 scripts/cobbler_agents.py onboard probe --json --smoke",
        },
        inventory=[i.to_dict() for i in inventory],
        env_present=env_present,
        current_roles=current,
        purposes=list(PURPOSE_CATALOG),
        questions=questions,
        recommendations=recommend_routes(inventory),
        notes=notes,
    )


def apply_onboarding(
    repo_root: Path,
    *,
    role_flags: Mapping[str, str | None],
    required: Sequence[str] | None = None,
    force: bool = False,
    dry_run: bool = False,
    run_smoke: bool = False,
    smoke_executor: Any | None = None,
    fake_presence: Mapping[str, bool] | None = None,
) -> dict[str, Any]:
    """Write role preferences (setup). Returns setup result dict + onboarding meta."""
    prefs = preferences_from_flags(
        implement=role_flags.get("implement"),
        review=role_flags.get("review"),
        planning=role_flags.get("planning"),
        lightweight_review=role_flags.get("lightweight_review"),
        validate=role_flags.get("validate"),
        synthesize=role_flags.get("synthesize"),
        scout=role_flags.get("scout"),
        required=required,
        native_fallback=True,
    )
    # Keep host-native for validate/synthesize unless user explicitly overrode.
    result = run_setup(
        Path(repo_root),
        preferences=prefs,
        write_toml=not dry_run,
        force_toml=force,
        run_smoke=run_smoke,
        smoke_executor=smoke_executor,
        dry_run=dry_run,
        fake_presence=fake_presence,
    )
    payload = result.to_dict()
    payload["onboarding"] = {
        "action": "apply",
        "updated_roles": {k: v for k, v in prefs.roles.items()},
        "next": [
            "python3 scripts/cobbler_agents.py onboard show --json",
            "python3 scripts/cobbler_agents.py onboard probe --json",
            "Optional paid smoke: onboard probe --json --smoke (host supplies smoke_executor)",
        ],
    }
    return payload


def _probe_executable(name: str | None) -> ProbeResult:
    if not name:
        return ProbeResult(
            route="unknown",
            purpose=None,
            status="skip",
            detail="No executable name",
        )
    path = shutil.which(name)
    if not path:
        return ProbeResult(
            route=name,
            purpose=None,
            status="fail",
            detail=f"Executable `{name}` not found on PATH",
        )
    try:
        proc = subprocess.run(
            [path, "--help"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        # Many CLIs return 0 or 2 for --help; any output means the binary runs.
        out = (proc.stdout or "") + (proc.stderr or "")
        if out.strip() or proc.returncode in (0, 1, 2):
            return ProbeResult(
                route=name,
                purpose=None,
                status="pass",
                detail=f"`{name}` runs (--help ok, exit={proc.returncode})",
            )
        return ProbeResult(
            route=name,
            purpose=None,
            status="warn",
            detail=f"`{name}` found but --help produced no output (exit={proc.returncode})",
        )
    except subprocess.TimeoutExpired:
        return ProbeResult(
            route=name,
            purpose=None,
            status="warn",
            detail=f"`{name}` --help timed out",
        )
    except OSError as exc:
        return ProbeResult(
            route=name,
            purpose=None,
            status="fail",
            detail=f"`{name}` failed to launch: {exc}",
        )


def probe_routes(
    repo_root: Path,
    *,
    live_smoke: bool = False,
    smoke_executor: Any | None = None,
    fake_presence: Mapping[str, bool] | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Structural probes for configured routes; optional live smoke.

    Structural: executable on PATH + --help, env *names* present for API routes.
    Live smoke: only when live_smoke and smoke_executor provided (never invents responses).
    """
    roles = load_role_profiles_from_models_toml(repo_root)
    inventory = inventory_tools(fake_presence=fake_presence)
    by_adapter = {i.adapter: i for i in inventory}
    env_present = env_name_presence(environ=environ)
    probes: list[ProbeResult] = []

    # Always confirm host-native.
    probes.append(
        ProbeResult(
            route="host-native",
            purpose="always",
            status="pass",
            detail="Host coordinator path requires no external tool",
        )
    )

    profiles = default_profiles()
    seen_profiles: set[str] = set()
    for role, profile in roles.items():
        seen_profiles.add(profile)
        if profile == "host-native":
            probes.append(
                ProbeResult(
                    route=profile,
                    purpose=role,
                    status="pass",
                    detail="host-native",
                )
            )
            continue
        # Tier profiles share an underlying adapter (e.g. claude-code-planning → claude-code).
        adapter_key = _ROUTE_PRESENCE_ADAPTER.get(profile, profile)
        item = by_adapter.get(adapter_key) or by_adapter.get(profile)
        if item is None:
            # custom or unknown profile name — try as executable
            probes.append(_probe_executable(profile))
            probes[-1].purpose = role
            continue
        if not item.present:
            probes.append(
                ProbeResult(
                    route=profile,
                    purpose=role,
                    status="fail",
                    detail=f"Adapter `{adapter_key}` not present on PATH (profile `{profile}`)",
                )
            )
            continue
        exe = item.executable or (
            profiles.get(adapter_key).executable if adapter_key in profiles else None
        )
        pr = _probe_executable(exe)
        pr.purpose = role
        pr.route = profile
        probes.append(pr)

    # Optional API routes (env presence only unless smoke).
    if env_present.get("OPENROUTER_API_KEY"):
        probes.append(
            ProbeResult(
                route="openrouter",
                purpose="provider_breadth",
                status="pass",
                detail="OPENROUTER_API_KEY name is set (value not inspected)",
            )
        )
    else:
        probes.append(
            ProbeResult(
                route="openrouter",
                purpose="provider_breadth",
                status="skip",
                detail="OPENROUTER_API_KEY not set — optional OpenRouter routes unavailable",
            )
        )

    if env_present.get("META_API_KEY") or env_present.get("MODEL_API_KEY"):
        probes.append(
            ProbeResult(
                route="meta-muse",
                purpose="provider_breadth",
                status="pass",
                detail="META_API_KEY or MODEL_API_KEY name is set (value not inspected)",
            )
        )
    else:
        probes.append(
            ProbeResult(
                route="meta-muse",
                purpose="provider_breadth",
                status="skip",
                detail="No Meta API key name set — Muse optional routes unavailable",
            )
        )

    if shutil.which("gcloud"):
        probes.append(
            ProbeResult(
                route="alphaevolve",
                purpose="math_evolutionary_search",
                status="pass",
                detail="gcloud on PATH (project must still provide AlphaEvolve runner + evaluator)",
            )
        )
    else:
        probes.append(
            ProbeResult(
                route="alphaevolve",
                purpose="math_evolutionary_search",
                status="skip",
                detail="gcloud not on PATH — AlphaEvolve optional",
            )
        )

    smoke: dict[str, Any] = {"requested": live_smoke, "ran": False, "detail": ""}
    if live_smoke:
        if smoke_executor is None:
            smoke["detail"] = (
                "Live smoke requested but no smoke_executor provided; "
                "host agent should run a real tiny completion per external route and re-invoke"
            )
            probes.append(
                ProbeResult(
                    route="live_smoke",
                    purpose=None,
                    status="warn",
                    detail=smoke["detail"],
                    kind="live_smoke",
                )
            )
        else:
            try:
                out = smoke_executor(roles=roles, inventory=inventory)
            except Exception as exc:  # noqa: BLE001
                smoke["detail"] = f"Smoke executor failed: {type(exc).__name__}: {exc}"
                probes.append(
                    ProbeResult(
                        route="live_smoke",
                        purpose=None,
                        status="fail",
                        detail=smoke["detail"],
                        kind="live_smoke",
                    )
                )
            else:
                if isinstance(out, Mapping) and (
                    out.get("text") or out.get("content") or out.get("results")
                ):
                    smoke["ran"] = True
                    smoke["detail"] = "Smoke executor returned a non-empty response (credentials not printed)"
                    probes.append(
                        ProbeResult(
                            route="live_smoke",
                            purpose=None,
                            status="pass",
                            detail=smoke["detail"],
                            kind="live_smoke",
                        )
                    )
                else:
                    smoke["detail"] = "Smoke executor returned empty or invalid payload"
                    probes.append(
                        ProbeResult(
                            route="live_smoke",
                            purpose=None,
                            status="fail",
                            detail=smoke["detail"],
                            kind="live_smoke",
                        )
                    )

    fails = sum(1 for p in probes if p.status == "fail")
    warns = sum(1 for p in probes if p.status == "warn")
    return {
        "ok": fails == 0,
        "generated_at": _utc_now(),
        "roles": roles,
        "env_present": env_present,
        "probes": [p.to_dict() for p in probes],
        "summary": {"pass": sum(1 for p in probes if p.status == "pass"), "warn": warns, "fail": fails},
        "smoke": smoke,
        "credentials_printed": False,
        "notes": [
            "Structural probes do not spend model tokens.",
            "Live smoke is opt-in and must use a host-provided smoke_executor or follow-up host turns.",
            "Never print API key values.",
        ],
    }


def show_onboarding(repo_root: Path) -> dict[str, Any]:
    roles = load_role_profiles_from_models_toml(repo_root)
    path = Path(repo_root) / ".elves" / "models.toml"
    return {
        "ok": True,
        "models_toml_path": str(path),
        "models_toml_exists": path.is_file(),
        "roles": roles,
        "purposes": list(PURPOSE_CATALOG),
        "env_present": env_name_presence(),
        "update_hint": (
            "Re-run onboard plan (host interviews user) then onboard apply, "
            "or pass flags to onboard apply / setup directly."
        ),
        "credentials_printed": False,
    }
