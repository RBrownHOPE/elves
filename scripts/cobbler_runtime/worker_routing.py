"""Deterministic, model-free adaptive implementation-worker routing."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Mapping

from .schema import ValidationIssue


REASONING_LEVELS = ("low", "medium", "high")
REVIEW_RISKS = ("low", "standard", "high")
GROK_COMPOSER_MODEL = "grok-composer-2.5-fast"
GROK_COMPLEX_MODEL = "grok-4.5"
GROK_UPSTREAM_SOURCE_URL = "https://github.com/xai-org/grok-build"
GROK_UPSTREAM_SEMANTIC_COMMIT = "c1b5909ec707c069f1d21a93917af044e71da0d7"


@dataclass(frozen=True)
class GrokCapabilityEvidence:
    """Redaction-safe evidence for one installed-binary capability."""

    state: str
    source: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"state": self.state, "source": self.source, "reason": self.reason}


@dataclass(frozen=True)
class GrokCapabilities:
    installed: bool = False
    authenticated: bool = False
    models: tuple[str, ...] = ()
    goal_entrypoint_advertised: bool = False
    goal_mode_behaviorally_verified: bool = False
    goal_behavioral_evidence: str | None = None
    version: str | None = None
    installed_build_commit: str | None = None
    default_model: str | None = None
    capability_ledger: tuple[tuple[str, GrokCapabilityEvidence], ...] = ()

    def supports(self, model: str) -> bool:
        return self.installed and self.authenticated and model in self.models

    def capability(self, name: str) -> GrokCapabilityEvidence | None:
        return dict(self.capability_ledger).get(name)

    def core_launch_unavailable_reason(self) -> str | None:
        """Return the first concrete reduced-install reason, when a ledger exists."""
        if not self.capability_ledger:
            return None
        for name in (
            "read_only_controls",
            "always_approve",
            "session_id",
            "resume",
            "streaming_json",
        ):
            evidence = self.capability(name)
            if evidence is None or evidence.state != "proven":
                detail = evidence.reason if evidence is not None else "not_probed"
                return f"capability_unavailable:{name}:{detail}"
        return None

    def safe_snapshot(self) -> dict[str, Any]:
        """Return normalized facts only; never retain command output or auth diagnostics."""
        return {
            "schema_version": 1,
            "installed": self.installed,
            "version": self.version,
            "installed_build_commit": self.installed_build_commit,
            "authenticated": self.authenticated,
            "models": list(self.models),
            "default_model": self.default_model,
            "semantic_reference": {
                "url": GROK_UPSTREAM_SOURCE_URL,
                "commit": GROK_UPSTREAM_SEMANTIC_COMMIT,
                "authority": "semantic_reference_only",
            },
            "capabilities": {
                name: evidence.to_dict() for name, evidence in self.capability_ledger
            },
        }


_GROK_HELP_CAPABILITIES: tuple[tuple[str, str], ...] = (
    ("permission_mode", "--permission-mode"),
    ("always_approve", "--always-approve"),
    ("no_subagents", "--no-subagents"),
    ("no_memory", "--no-memory"),
    ("disable_web_search", "--disable-web-search"),
    ("check", "--check"),
    ("session_id", "--session-id"),
    ("resume", "--resume"),
    ("new_session", "--new-session"),
    ("streaming_json", "streaming-json"),
    ("json_schema", "--json-schema"),
)


def _probe_reason(result: Any | None, error: BaseException | None) -> str:
    if error is not None:
        return f"probe_error:{type(error).__name__}"
    return f"command_failed:exit_{getattr(result, 'returncode', 'unknown')}"


def probe_grok_goal_resolution(
    located: str,
    *,
    runner: Any = subprocess.run,
    auth_path: Path | None = None,
    api_key: str | None = None,
) -> tuple[GrokCapabilityEvidence, str | None]:
    """Resolve `/goal status` in an isolated home without retaining its output."""
    if auth_path is None and not api_key:
        return (
            GrokCapabilityEvidence(
                state="unavailable",
                source="isolated_model_free_probe",
                reason="narrow_auth_projection_not_provided",
            ),
            None,
        )
    resolved_auth: Path | None = None
    if auth_path is not None:
        try:
            resolved_auth = auth_path.expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            return (
                GrokCapabilityEvidence(
                    state="unavailable",
                    source="isolated_model_free_probe",
                    reason="narrow_auth_projection_unavailable",
                ),
                None,
            )
    with tempfile.TemporaryDirectory(prefix="elves-grok-goal-probe-") as tmp:
        root = Path(tmp)
        session_id = str(uuid.uuid4())
        env = {
            "HOME": str(root),
            "GROK_HOME": str(root / "grok"),
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_CACHE_HOME": str(root / "cache"),
            "XDG_DATA_HOME": str(root / "data"),
            "PATH": os.environ.get("PATH") or os.defpath,
            "LANG": os.environ.get("LANG") or "C.UTF-8",
        }
        if resolved_auth is not None:
            env["GROK_AUTH_PATH"] = str(resolved_auth)
        elif api_key:
            env["XAI_API_KEY"] = api_key
        for name in ("grok", "config", "cache", "data"):
            (root / name).mkdir(mode=0o700)
        argv = [
            located,
            "--session-id",
            session_id,
            "--permission-mode",
            "plan",
            "--no-subagents",
            "--no-memory",
            "--disable-web-search",
            "--output-format",
            "streaming-json",
            "--single",
            "/goal status",
        ]
        try:
            result = runner(
                argv,
                check=False,
                capture_output=True,
                text=True,
                timeout=8,
                env=env,
                cwd=tmp,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return (
                GrokCapabilityEvidence(
                    state="unavailable",
                    source="isolated_model_free_probe",
                    reason=f"probe_error:{type(exc).__name__}",
                ),
                None,
            )
    event_types: list[str] = []
    exact_session = False
    goal_status_resolved = False
    malformed = False
    for line in (result.stdout or "").splitlines():
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            malformed = True
            continue
        if not isinstance(event, Mapping):
            malformed = True
            continue
        event_type = str(event.get("type") or "")
        event_types.append(event_type)
        if event_type == "text" and "No goal is currently set" in str(event.get("data") or ""):
            goal_status_resolved = True
        if event_type == "end" and event.get("sessionId") == session_id:
            exact_session = True
    inference_events = {"thought", "tool", "tool_call", "usage"}.intersection(event_types)
    if (
        getattr(result, "returncode", 1) == 0
        and goal_status_resolved
        and exact_session
        and not inference_events
        and not malformed
    ):
        evidence_id = "isolated:/goal-status:text+end:exact-session:no-model-events"
        return (
            GrokCapabilityEvidence(
                state="proven",
                source="isolated_model_free_probe",
                reason="slash_command_resolved_without_model_events",
            ),
            evidence_id,
        )
    combined = ((result.stdout or "") + "\n" + (result.stderr or "")).lower()
    if "not signed in" in combined or "authenticate" in combined:
        reason = "authentication_required_before_command_resolution"
    elif malformed:
        reason = "malformed_streaming_probe_output"
    elif inference_events:
        reason = "unexpected_model_events"
    else:
        reason = f"goal_status_not_resolved:exit_{getattr(result, 'returncode', 'unknown')}"
    return (
        GrokCapabilityEvidence(
            state="unavailable",
            source="isolated_model_free_probe",
            reason=reason,
        ),
        None,
    )


def probe_grok_capabilities(
    executable: str = "grok",
    *,
    runner: Any = subprocess.run,
    goal_auth_path: Path | None = None,
    goal_api_key: str | None = None,
    command_env: Mapping[str, str] | None = None,
) -> GrokCapabilities:
    """Silently inventory Grok without launching an inference turn.

    `grok models` is the authentication/model qualification. Goal support is
    delegated to the repository's existing behavioral help probe; a TUI-only
    `/goal` mention is never upgraded into headless goal support.
    """
    located = shutil.which(executable)
    if not located:
        return GrokCapabilities(
            capability_ledger=(
                (
                    "install",
                    GrokCapabilityEvidence(
                        state="unavailable",
                        source="executable_lookup",
                        reason="executable_not_found",
                    ),
                ),
            )
        )
    common = {"check": False, "capture_output": True, "text": True, "timeout": 5}
    if command_env is not None:
        common["env"] = dict(command_env)
    results: dict[str, Any | None] = {}
    errors: dict[str, BaseException | None] = {}
    for name, argv in (
        ("version", [located, "version", "--json"]),
        ("models", [located, "models"]),
        ("help", [located, "--help"]),
        ("acp", [located, "agent", "stdio", "--help"]),
    ):
        try:
            results[name] = runner(argv, **common)
            errors[name] = None
        except (OSError, subprocess.SubprocessError) as exc:
            results[name] = None
            errors[name] = exc

    version_result = results["version"]
    models_result = results["models"]
    help_result = results["help"]
    version_text = ""
    if version_result is not None:
        version_text = (version_result.stdout or version_result.stderr or "").strip()
    version_match = re.search(r"\d+\.\d+(?:\.\d+)?", version_text)
    build_match = re.search(r"\(([0-9a-f]{7,40})\)", version_text, re.I)
    model_stdout = models_result.stdout or "" if models_result is not None else ""
    model_combined = (
        model_stdout + "\n" + (models_result.stderr or "")
        if models_result is not None
        else ""
    )
    model_lower = model_combined.lower()
    auth_failed = bool(
        re.search(r"not signed in|not logged in|unauthori[sz]ed|authentication required", model_lower)
    )
    catalog_failed = bool(
        re.search(r"failed to fetch models|model refresh failed|settings fetch failed", model_lower)
    )
    models_command_ok = models_result is not None and getattr(models_result, "returncode", 1) == 0
    models_available = models_command_ok and not auth_failed and not catalog_failed
    models = (
        tuple(sorted(set(re.findall(r"\bgrok-[a-z0-9][a-z0-9._-]*", model_stdout.lower()))))
        if models_available
        else ()
    )
    default_match = re.search(
        r"(?im)^\s*default model\s*:\s*(grok-[a-z0-9][a-z0-9._-]*)",
        model_stdout,
    )
    default_model = default_match.group(1).lower() if default_match and models_available else None
    ledger: list[tuple[str, GrokCapabilityEvidence]] = [
        (
            "install",
            GrokCapabilityEvidence(
                state="proven",
                source="executable_lookup",
                reason="installed_binary_resolved",
            ),
        )
    ]
    if version_match:
        ledger.append(
            (
                "version",
                GrokCapabilityEvidence(
                    state="proven",
                    source="installed_binary:version_json",
                    reason="semantic_version_parsed",
                ),
            )
        )
    else:
        ledger.append(
            (
                "version",
                GrokCapabilityEvidence(
                    state="unavailable",
                    source="installed_binary:version_json",
                    reason=(
                        "version_unparseable"
                        if version_result is not None and getattr(version_result, "returncode", 1) == 0
                        else _probe_reason(version_result, errors["version"])
                    ),
                ),
            )
        )
    if models_available:
        model_reason = "authenticated_live_catalog_returned"
        auth_state = "proven"
        catalog_state = "proven"
    elif auth_failed:
        model_reason = "authentication_rejected"
        auth_state = "refuted"
        catalog_state = "unavailable"
    elif catalog_failed:
        model_reason = "live_catalog_unavailable"
        auth_state = "unavailable"
        catalog_state = "unavailable"
    else:
        model_reason = _probe_reason(models_result, errors["models"])
        auth_state = "unavailable"
        catalog_state = "unavailable"
    ledger.extend(
        (
            (
                "authentication",
                GrokCapabilityEvidence(
                    state=auth_state,
                    source="installed_binary:models",
                    reason=model_reason,
                ),
            ),
            (
                "model_catalog",
                GrokCapabilityEvidence(
                    state=catalog_state,
                    source="installed_binary:models",
                    reason=model_reason,
                ),
            ),
        )
    )
    goal_advertised = False
    help_available = help_result is not None and getattr(help_result, "returncode", 1) == 0
    help_text = ""
    if help_available:
        help_text = help_result.stdout or help_result.stderr or ""
        from .implement import detect_native_grok_goal

        goal_advertised = bool(
            detect_native_grok_goal(help_text=help_text).get(
                "advertised_headless_entrypoint"
            )
        )
    for name, marker in _GROK_HELP_CAPABILITIES:
        if help_available:
            present = marker in help_text
            ledger.append(
                (
                    name,
                    GrokCapabilityEvidence(
                        state="proven" if present else "refuted",
                        source="installed_binary:--help",
                        reason="advertised_by_help" if present else "not_advertised_by_help",
                    ),
                )
            )
        else:
            ledger.append(
                (
                    name,
                    GrokCapabilityEvidence(
                        state="unavailable",
                        source="installed_binary:--help",
                        reason=_probe_reason(help_result, errors["help"]),
                    ),
                )
            )
    help_capabilities = {name: marker in help_text for name, marker in _GROK_HELP_CAPABILITIES}
    read_only_markers = ("permission_mode", "no_subagents", "no_memory", "disable_web_search")
    ledger.append(
        (
            "read_only_controls",
            GrokCapabilityEvidence(
                state=(
                    "proven"
                    if help_available and all(help_capabilities[name] for name in read_only_markers)
                    else "refuted" if help_available else "unavailable"
                ),
                source="installed_binary:--help",
                reason=(
                    "all_read_only_controls_advertised"
                    if help_available and all(help_capabilities[name] for name in read_only_markers)
                    else "read_only_controls_incomplete"
                    if help_available
                    else _probe_reason(help_result, errors["help"])
                ),
            ),
        )
    )
    acp_result = results["acp"]
    acp_available = (
        acp_result is not None
        and getattr(acp_result, "returncode", 1) == 0
        and "agent over stdio" in ((acp_result.stdout or "") + (acp_result.stderr or "")).lower()
    )
    goal_evidence, goal_behavioral_evidence = probe_grok_goal_resolution(
        located,
        runner=runner,
        auth_path=goal_auth_path,
        api_key=goal_api_key,
    )
    ledger.extend(
        (
            (
                "goal_behavior",
                goal_evidence,
            ),
            (
                "acp",
                GrokCapabilityEvidence(
                    state="proven" if acp_available else "unavailable",
                    source="installed_binary:agent_stdio_help",
                    reason=(
                        "agent_stdio_advertised"
                        if acp_available
                        else _probe_reason(acp_result, errors["acp"])
                    ),
                ),
            ),
        )
    )
    return GrokCapabilities(
        installed=True,
        authenticated=models_available,
        models=models,
        goal_entrypoint_advertised=goal_advertised,
        goal_mode_behaviorally_verified=goal_evidence.state == "proven",
        goal_behavioral_evidence=goal_behavioral_evidence,
        version=version_match.group(0) if version_match else None,
        installed_build_commit=build_match.group(1).lower() if build_match else None,
        default_model=default_model,
        capability_ledger=tuple(ledger),
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
    requested_grok_model, requested_grok_model_source = _choice(
        (
            ("explicit_run_intent", explicit_worker),
            ("repository_default", repo_worker),
            ("global_preferences", global_worker),
        ),
        "grok_model",
        None,
    )
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
            candidate = (
                str(requested_grok_model)
                if requested_grok_model is not None
                else grok_info.default_model
            )
            goal_qualified = bool(
                grok_info.goal_mode_behaviorally_verified
                and grok_info.goal_behavioral_evidence
            )
            core_unavailable = grok_info.core_launch_unavailable_reason()
            if candidate and grok_info.supports(candidate) and not core_unavailable:
                selected_provider = "grok"
                selected_model = candidate
                model_policy = (
                    "explicit_catalog_model_pin"
                    if requested_grok_model is not None
                    else "authenticated_live_catalog_default"
                )
                goal_mode = goal_qualified
                reasons.append("permitted_grok_capability_matches_plan")
                if not goal_qualified:
                    fallback = {
                        "requested": "grok_goal",
                        "actual": "grok_packet_prompt",
                        "reason": "goal_mode_not_behaviorally_verified",
                    }
                    reasons.append("compatible_one_packet_fallback")
            else:
                if not (grok_info.installed and grok_info.authenticated):
                    missing = "unavailable_or_unauthenticated"
                elif not candidate:
                    missing = "live_default_model_unavailable"
                elif not grok_info.supports(candidate):
                    missing = f"model_unavailable:{candidate}"
                elif core_unavailable:
                    missing = core_unavailable
                else:
                    missing = "grok_provider_unqualified"
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
            "grok_model": (
                requested_grok_model_source
                if requested_grok_model is not None
                else "authenticated_live_catalog_default"
            ),
        },
        fallback=fallback,
        advisory_driver_upgrade=advisory,
        goal_mode=goal_mode,
        reasons=tuple(reasons),
    )
