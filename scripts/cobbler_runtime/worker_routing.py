"""Deterministic, model-free adaptive implementation-worker routing."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Mapping

from .schema import ValidationIssue


REASONING_LEVELS = ("low", "medium", "high")
REVIEW_RISKS = ("low", "standard", "high")
GROK_COMPOSER_MODEL = "grok-composer-2.5-fast"
GROK_COMPLEX_MODEL = "grok-4.5"


@dataclass(frozen=True)
class GrokCapabilities:
    installed: bool = False
    authenticated: bool = False
    models: tuple[str, ...] = ()
    goal_entrypoint_advertised: bool = False
    goal_mode_behaviorally_verified: bool = False
    goal_behavioral_evidence: str | None = None
    version: str | None = None

    def supports(self, model: str) -> bool:
        return self.installed and self.authenticated and model in self.models


def probe_grok_capabilities(
    executable: str = "grok",
    *,
    runner: Any = subprocess.run,
) -> GrokCapabilities:
    """Silently inventory Grok without launching an inference turn.

    `grok models` is the authentication/model qualification. Goal support is
    delegated to the repository's existing behavioral help probe; a TUI-only
    `/goal` mention is never upgraded into headless goal support.
    """
    located = shutil.which(executable)
    if not located:
        return GrokCapabilities()
    common = {"check": False, "capture_output": True, "text": True, "timeout": 5}
    try:
        version_result = runner([located, "--version"], **common)
        models_result = runner([located, "models"], **common)
        help_result = runner([located, "--help"], **common)
    except (OSError, subprocess.SubprocessError):
        return GrokCapabilities(installed=True)
    version_text = (version_result.stdout or version_result.stderr or "").strip()
    version_match = re.search(r"\d+\.\d+(?:\.\d+)?", version_text)
    model_text = models_result.stdout or ""
    models = tuple(sorted(set(re.findall(r"\bgrok-[a-z0-9][a-z0-9._-]*", model_text.lower()))))
    goal_advertised = False
    if getattr(help_result, "returncode", 1) == 0:
        from .implement import detect_native_grok_goal

        goal_advertised = bool(
            detect_native_grok_goal(help_text=help_result.stdout or help_result.stderr or "").get(
                "advertised_headless_entrypoint"
            )
        )
    return GrokCapabilities(
        installed=True,
        authenticated=getattr(models_result, "returncode", 1) == 0,
        models=models,
        goal_entrypoint_advertised=goal_advertised,
        goal_mode_behaviorally_verified=False,
        version=version_match.group(0) if version_match else None,
    )


@dataclass(frozen=True)
class RouteDecision:
    provider: str
    worker_transport: str
    worker_model_policy: str
    worker_model: str | None
    worker_effort: str
    execution_reasoning: str
    review_risk: str
    provenance: dict[str, str]
    fallback: dict[str, str] | None
    advisory_driver_upgrade: str | None
    goal_mode: bool
    reasons: tuple[str, ...]
    model_calls_made: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["reasons"] = list(self.reasons)
        return payload


def _choice(layers: tuple[tuple[str, Mapping[str, Any] | None], ...], key: str, default: Any) -> tuple[Any, str]:
    for source, layer in layers:
        if layer is not None and key in layer and layer[key] is not None:
            return layer[key], source
    return default, "built_in_default"


def discover_repository_worker_policy(
    repo_root: Path, *, override_path: Path | None = None
) -> tuple[dict[str, Any], str]:
    """Discover target-repository worker defaults/vetoes from established config files."""
    from .config import load_json_file, load_toml_file

    candidates = [override_path] if override_path else [repo_root / "config.json", repo_root / ".elves" / "models.toml"]
    merged: dict[str, Any] = {}
    sources: list[str] = []
    for candidate in candidates:
        if candidate is None or not candidate.is_file():
            continue
        data = load_toml_file(candidate) if candidate.suffix == ".toml" else load_json_file(candidate)
        worker = data.get("worker", {}) if isinstance(data, Mapping) else {}
        if not isinstance(worker, Mapping):
            raise ValidationIssue("invalid_route_policy", "Repository `worker` policy must be an object", path=str(candidate))
        merged.update(worker)
        sources.append(str(candidate.resolve()))
    return ({"worker": merged} if merged else {}), (" > ".join(sources) if sources else "none")


def decide_worker_route(
    *,
    host: str,
    execution_reasoning: str,
    review_risk: str,
    global_preferences: Mapping[str, Any] | None = None,
    explicit_intent: Mapping[str, Any] | None = None,
    repo_policy: Mapping[str, Any] | None = None,
    grok: GrokCapabilities | None = None,
    driver_effort: str | None = None,
) -> RouteDecision:
    """Return an inspectable route. Repository policy is the final safety veto."""
    host_token = host.strip().lower().replace("_", "-")
    if host_token not in {"codex", "claude", "claude-code"}:
        raise ValidationIssue("unsupported_host", f"Unsupported host `{host}`", path="host")
    execution = execution_reasoning.strip().lower()
    risk = review_risk.strip().lower()
    if execution not in REASONING_LEVELS:
        raise ValidationIssue("invalid_execution_reasoning", f"Unknown execution reasoning `{execution}`")
    if risk not in REVIEW_RISKS:
        raise ValidationIssue("invalid_review_risk", f"Unknown review risk `{risk}`")

    global_worker = (global_preferences or {}).get("worker", {})
    if not isinstance(global_worker, Mapping):
        global_worker = {}
    explicit_worker = (explicit_intent or {}).get("worker", explicit_intent or {})
    repo_worker = (repo_policy or {}).get("worker", repo_policy or {})
    if not isinstance(explicit_worker, Mapping) or not isinstance(repo_worker, Mapping):
        raise ValidationIssue("invalid_route_policy", "Worker intent/policy must be objects")

    provider, provider_source = _choice(
        (
            ("explicit_run_intent", explicit_worker),
            ("repository_default", repo_worker),
            ("global_preferences", global_worker),
        ),
        "provider",
        "auto",
    )
    effort, effort_source = _choice(
        (
            ("explicit_run_intent", explicit_worker),
            ("repository_default", repo_worker),
            ("global_preferences", global_worker),
        ),
        "native_effort",
        "auto",
    )
    provider = str(provider).lower()
    effort_is_auto = effort == "auto"
    effort = execution if effort_is_auto else str(effort).lower()
    if provider not in {"auto", "native", "grok"}:
        raise ValidationIssue("invalid_provider_preference", f"Unknown worker provider `{provider}`")
    if effort not in REASONING_LEVELS:
        raise ValidationIssue("invalid_worker_effort", f"Unknown worker effort `{effort}`")

    grok_info = grok or GrokCapabilities()
    prohibited = repo_worker.get("allow_grok") is False
    consent_source = "none"
    if explicit_worker.get("provider") == "grok":
        consent_source = "explicit_run_provider"
    elif explicit_worker.get("allow_grok") is True:
        consent_source = "explicit_run_allow_grok"
    elif global_worker.get("provider") == "grok":
        consent_source = "global_provider_preference"
    permitted = consent_source != "none"
    reasons: list[str] = []
    fallback: dict[str, str] | None = None

    wants_grok = provider == "grok" or (provider == "auto" and permitted)
    selected_provider = "native"
    selected_model: str | None = None
    model_policy = "inherit_live_driver_model"
    goal_mode = False
    if wants_grok:
        if prohibited:
            fallback = {"requested": "grok", "actual": "native", "reason": "repository_policy_prohibits_grok"}
        elif not permitted:
            fallback = {"requested": "grok", "actual": "native", "reason": "grok_not_explicitly_permitted"}
        else:
            candidate = GROK_COMPLEX_MODEL if execution == "high" else GROK_COMPOSER_MODEL
            if grok_info.supports(candidate):
                selected_provider = "grok"
                selected_model = candidate
                model_policy = "explicit_grok_model_pin"
                goal_mode = bool(
                    grok_info.goal_mode_behaviorally_verified
                    and grok_info.goal_behavioral_evidence
                )
                reasons.append("permitted_grok_capability_matches_plan")
            else:
                missing = "unavailable_or_unauthenticated" if not (grok_info.installed and grok_info.authenticated) else f"model_unavailable:{candidate}"
                fallback = {"requested": "grok", "actual": "native", "reason": missing}

    if selected_provider == "native":
        reasons.append("subscription_native_default")
    if fallback:
        reasons.append(f"honest_fallback:{fallback['reason']}")

    advisory = None
    if risk == "high" and driver_effort in {"low", "medium"}:
        advisory = "consider_high_effort_driver_for_terminal_review"

    return RouteDecision(
        provider=selected_provider,
        worker_transport=("codex_exec" if host_token == "codex" else "claude_code") if selected_provider == "native" else "grok_build",
        worker_model_policy=model_policy,
        worker_model=selected_model,
        worker_effort=effort,
        execution_reasoning=execution,
        review_risk=risk,
        provenance={
            "provider": provider_source,
            "worker_effort": "plan_execution_reasoning" if effort_is_auto else effort_source,
            "grok_consent": consent_source,
            "grok_safety_veto": "repository_policy" if prohibited else "none",
            "grok_goal_evidence": grok_info.goal_behavioral_evidence or "none",
        },
        fallback=fallback,
        advisory_driver_upgrade=advisory,
        goal_mode=goal_mode,
        reasons=tuple(reasons),
    )
