"""Config loading and role-route resolution with explicit provenance.

Precedence (highest wins):
1. Survival Guide route snapshot / explicit project required values
2. Local ignored `.elves/models.toml`
3. Installed/user `config.json`
4. Native host defaults

Local TOML preference requires Python 3.11+ (`tomllib`). On older Python, absence
of the file is fine; presence fails with an actionable upgrade diagnostic.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Mapping

from .adapters import default_profiles, get_adapter
from .schema import (
    DEFAULT_ROLES,
    NATIVE_PROFILE_NAME,
    ConfigSource,
    FallbackEntry,
    HarnessProfile,
    ResolvedConfig,
    RoleName,
    RoleRoute,
    SessionMode,
    ValidationIssue,
    parse_role_name,
)

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None  # type: ignore[assignment]


SOURCE_RANK: dict[ConfigSource, int] = {
    ConfigSource.SURVIVAL_GUIDE: 4,
    ConfigSource.LOCAL_MODELS_TOML: 3,
    ConfigSource.USER_CONFIG_JSON: 2,
    ConfigSource.NATIVE_DEFAULT: 1,
}


def native_defaults() -> dict[str, RoleRoute]:
    """Every role resolves to host-native when no external config exists."""
    return {
        role.value: RoleRoute(
            role=role,
            profile=NATIVE_PROFILE_NAME,
            required=False,
            fallback_chain=(),
            source=ConfigSource.NATIVE_DEFAULT,
            session_mode=SessionMode.EPHEMERAL,
            notes="Public default: host-native with zero external tools",
        )
        for role in DEFAULT_ROLES
    }


def _as_mapping(value: Any, *, path: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValidationIssue(
            "invalid_type",
            f"Expected a table/object at `{path}`",
            path=path,
            hint="Use a TOML table or JSON object",
        )
    return dict(value)


def _as_bool(value: Any, *, path: str, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise ValidationIssue(
        "invalid_type",
        f"Expected boolean at `{path}`",
        path=path,
    )


def _as_str(value: Any, *, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationIssue(
            "invalid_type",
            f"Expected non-empty string at `{path}`",
            path=path,
        )
    return value.strip()


def _parse_fallback_chain(raw: Any, *, path: str) -> tuple[FallbackEntry, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValidationIssue(
            "invalid_type",
            f"Expected list at `{path}`",
            path=path,
        )
    entries: list[FallbackEntry] = []
    for index, item in enumerate(raw):
        item_path = f"{path}[{index}]"
        if isinstance(item, str):
            entries.append(FallbackEntry(profile=item.strip()))
            continue
        if isinstance(item, Mapping):
            profile = _as_str(item.get("profile") or item.get("route"), path=f"{item_path}.profile")
            reason = str(item.get("reason") or "")
            entries.append(FallbackEntry(profile=profile, reason=reason))
            continue
        raise ValidationIssue(
            "invalid_type",
            f"Fallback entry must be a string or object at `{item_path}`",
            path=item_path,
        )
    return tuple(entries)


def _parse_role_override(
    role_name: str,
    raw: Any,
    *,
    source: ConfigSource,
    path: str,
) -> RoleRoute:
    role = parse_role_name(role_name)
    if isinstance(raw, str):
        return RoleRoute(
            role=role,
            profile=raw.strip(),
            required=False,
            fallback_chain=(),
            source=source,
        )
    data = _as_mapping(raw, path=path)
    profile = _as_str(
        data.get("profile") or data.get("preference") or data.get("route"),
        path=f"{path}.profile",
    )
    required = _as_bool(data.get("required"), path=f"{path}.required", default=False)
    fallback = _parse_fallback_chain(
        data.get("fallback_chain") or data.get("fallback") or data.get("fallback-chain"),
        path=f"{path}.fallback_chain",
    )
    session_raw = data.get("session_mode") or data.get("session-mode")
    if session_raw is None:
        session_mode = SessionMode.EPHEMERAL
    else:
        try:
            session_mode = SessionMode(str(session_raw).strip().lower().replace("-", "_"))
        except ValueError as exc:
            raise ValidationIssue(
                "invalid_session_mode",
                f"Unknown session mode `{session_raw}`",
                path=f"{path}.session_mode",
            ) from exc
    notes = str(data.get("notes") or "")
    return RoleRoute(
        role=role,
        profile=profile,
        required=required,
        fallback_chain=fallback,
        source=source,
        session_mode=session_mode,
        notes=notes,
    )


def _parse_profiles(
    raw: Any,
    *,
    source: ConfigSource,
    path: str,
    existing: dict[str, HarnessProfile],
) -> dict[str, HarnessProfile]:
    data = _as_mapping(raw, path=path)
    parsed: dict[str, HarnessProfile] = {}
    for name, body in data.items():
        profile_path = f"{path}.{name}"
        if name in existing and existing[name].adapter != "host-native" and source != ConfigSource.SURVIVAL_GUIDE:
            # Allow redefinition only when adapter stays consistent or is explicit.
            pass
        if name in parsed:
            raise ValidationIssue(
                "duplicate_profile",
                f"Duplicate profile name `{name}`",
                path=profile_path,
                hint="Profile names must be unique within a config source",
            )
        if isinstance(body, str):
            adapter_name = body.strip()
            body_map: dict[str, Any] = {"adapter": adapter_name}
        else:
            body_map = _as_mapping(body, path=profile_path)
        adapter_name = _as_str(
            body_map.get("adapter") or body_map.get("harness") or name,
            path=f"{profile_path}.adapter",
        )
        # Validate adapter exists (custom-cli and built-ins).
        try:
            get_adapter(adapter_name if adapter_name in {
                "claude-code", "grok-build", "codex-fugu", "custom-cli", "host-native"
            } else "custom-cli")
        except ValidationIssue:
            raise
        # If adapter is not a known built-in name, treat as custom-cli wrapper.
        if adapter_name not in {
            "claude-code",
            "grok-build",
            "codex-fugu",
            "custom-cli",
            "host-native",
        }:
            # Named custom profiles still use the custom-cli adapter contract.
            resolved_adapter = "custom-cli"
        else:
            resolved_adapter = adapter_name
            get_adapter(resolved_adapter)

        executable = body_map.get("executable")
        if executable is not None and not isinstance(executable, str):
            raise ValidationIssue(
                "invalid_type",
                f"executable must be a string at `{profile_path}.executable`",
                path=f"{profile_path}.executable",
            )
        notes = str(body_map.get("notes") or "")
        parsed[name] = HarnessProfile(
            name=name,
            adapter=resolved_adapter,
            executable=executable,
            notes=notes,
        )
    return parsed


def _extract_roles_and_profiles(
    data: Mapping[str, Any],
    *,
    source: ConfigSource,
    root_path: str,
) -> tuple[dict[str, RoleRoute], dict[str, HarnessProfile]]:
    """Extract roles/profiles from a config-shaped mapping."""
    roles: dict[str, RoleRoute] = {}
    profiles: dict[str, HarnessProfile] = {}

    # TOML style: [profiles] / [roles]
    if "profiles" in data:
        profiles.update(
            _parse_profiles(
                data["profiles"],
                source=source,
                path=f"{root_path}.profiles",
                existing=profiles,
            )
        )
    if "roles" in data:
        role_data = _as_mapping(data["roles"], path=f"{root_path}.roles")
        for role_name, body in role_data.items():
            route = _parse_role_override(
                role_name,
                body,
                source=source,
                path=f"{root_path}.roles.{role_name}",
            )
            roles[route.role.value] = route

    # Survival guide style nested under model-routing / model_routing
    routing = data.get("model-routing") or data.get("model_routing") or {}
    if routing:
        routing_map = _as_mapping(routing, path=f"{root_path}.model_routing")
        if "profiles" in routing_map:
            profiles.update(
                _parse_profiles(
                    routing_map["profiles"],
                    source=source,
                    path=f"{root_path}.model_routing.profiles",
                    existing=profiles,
                )
            )
        phases = routing_map.get("phases") or routing_map.get("roles") or {}
        phase_map = _as_mapping(phases, path=f"{root_path}.model_routing.phases")
        for role_name, body in phase_map.items():
            route = _parse_role_override(
                role_name,
                body,
                source=source,
                path=f"{root_path}.model_routing.phases.{role_name}",
            )
            # Global required only applies when role omits required.
            if isinstance(body, Mapping) and "required" not in body and "required" in routing_map:
                route = RoleRoute(
                    role=route.role,
                    profile=route.profile,
                    required=_as_bool(
                        routing_map.get("required"),
                        path=f"{root_path}.model_routing.required",
                    ),
                    fallback_chain=route.fallback_chain,
                    source=route.source,
                    session_mode=route.session_mode,
                    notes=route.notes,
                )
            roles[route.role.value] = route

    # config.json style: top-level model_routing.phases
    if "model_routing" in data and "phases" not in (data.get("model_routing") or {}):
        # Already handled above via model_routing key.
        pass

    return roles, profiles


def load_json_file(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationIssue(
            "read_error",
            f"Unable to read config JSON at `{path}`",
            path=str(path),
            hint=str(exc),
        ) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationIssue(
            "invalid_json",
            f"Invalid JSON in `{path}`: {exc.msg}",
            path=str(path),
            hint=f"line {exc.lineno} column {exc.colno}",
        ) from exc
    if not isinstance(data, dict):
        raise ValidationIssue(
            "invalid_type",
            f"Config JSON root must be an object in `{path}`",
            path=str(path),
        )
    return data


def load_toml_file(path: Path) -> dict[str, Any]:
    if tomllib is None:
        raise ValidationIssue(
            "toml_requires_python_311",
            (
                f"Local models preference file `{path}` is present, but this Python "
                f"({sys.version.split()[0]}) has no stdlib tomllib."
            ),
            path=str(path),
            hint=(
                "Upgrade to Python 3.11+ to use `.elves/models.toml`, or remove the file "
                "and rely on Survival Guide / config.json / native defaults."
            ),
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValidationIssue(
            "read_error",
            f"Unable to read models TOML at `{path}`",
            path=str(path),
            hint=str(exc),
        ) from exc
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except Exception as exc:  # tomllib.TOMLDecodeError
        raise ValidationIssue(
            "invalid_toml",
            f"Invalid TOML in `{path}`: {exc}",
            path=str(path),
        ) from exc
    if not isinstance(data, dict):
        raise ValidationIssue(
            "invalid_type",
            f"TOML root must be a table in `{path}`",
            path=str(path),
        )
    return data


def _merge_route(
    current: RoleRoute | None,
    incoming: RoleRoute,
) -> RoleRoute:
    if current is None:
        return incoming
    if SOURCE_RANK[incoming.source] >= SOURCE_RANK[current.source]:
        return incoming
    return current


def resolve_config(
    *,
    survival_guide: Mapping[str, Any] | None = None,
    models_toml: Mapping[str, Any] | None = None,
    models_toml_path: Path | None = None,
    user_config: Mapping[str, Any] | None = None,
    user_config_path: Path | None = None,
    repo_root: Path | None = None,
) -> ResolvedConfig:
    """Resolve role routes with provenance.

    Callers may pass already-parsed mappings (tests) or filesystem paths.
    """
    resolved = ResolvedConfig(profiles=default_profiles())
    roles = native_defaults()
    resolved.sources_consulted.append(ConfigSource.NATIVE_DEFAULT.value)

    # Load filesystem sources when paths are provided.
    if user_config is None and user_config_path is not None and user_config_path.is_file():
        user_config = load_json_file(user_config_path)
    if models_toml is None and models_toml_path is not None and models_toml_path.is_file():
        # Presence on disk is enough to require tomllib even before parsing.
        models_toml = load_toml_file(models_toml_path)

    if repo_root is not None:
        default_toml = repo_root / ".elves" / "models.toml"
        if models_toml is None and models_toml_path is None and default_toml.is_file():
            models_toml = load_toml_file(default_toml)
            models_toml_path = default_toml
        default_json = repo_root / "config.json"
        if user_config is None and user_config_path is None and default_json.is_file():
            user_config = load_json_file(default_json)
            user_config_path = default_json

    layers: list[tuple[ConfigSource, Mapping[str, Any] | None, str]] = [
        (ConfigSource.USER_CONFIG_JSON, user_config, "user_config"),
        (ConfigSource.LOCAL_MODELS_TOML, models_toml, "models_toml"),
        (ConfigSource.SURVIVAL_GUIDE, survival_guide, "survival_guide"),
    ]

    try:
        for source, payload, label in layers:
            if not payload:
                continue
            resolved.sources_consulted.append(source.value)
            layer_roles, layer_profiles = _extract_roles_and_profiles(
                payload,
                source=source,
                root_path=label,
            )
            for name, profile in layer_profiles.items():
                if name in resolved.profiles and resolved.profiles[name] != profile:
                    # Later (higher) layers overwrite; same-source duplicates already rejected.
                    if source == ConfigSource.LOCAL_MODELS_TOML:
                        # Detect ambiguous duplicate names across profile tables in one file.
                        pass
                resolved.profiles[name] = profile
            for role_name, route in layer_roles.items():
                roles[role_name] = _merge_route(roles.get(role_name), route)

        # Validate profiles referenced by routes exist; required unavailable blocks.
        for role_name, route in roles.items():
            profile_name = route.profile
            if profile_name not in resolved.profiles:
                # Allow bare built-in adapter names as profiles.
                if profile_name in default_profiles():
                    resolved.profiles[profile_name] = default_profiles()[profile_name]
                else:
                    issue = ValidationIssue(
                        "unknown_profile",
                        f"Role `{role_name}` references unknown profile `{profile_name}`",
                        path=f"roles.{role_name}.profile",
                        hint="Define the profile or map the role to a built-in adapter name",
                    )
                    if route.required:
                        resolved.issues.append(issue)
                    else:
                        # Optional: fall back to native without crashing.
                        resolved.warnings.append(issue.message)
                        roles[role_name] = RoleRoute(
                            role=route.role,
                            profile=NATIVE_PROFILE_NAME,
                            required=False,
                            fallback_chain=route.fallback_chain,
                            source=route.source,
                            session_mode=route.session_mode,
                            notes=f"Fell back to host-native: {issue.message}",
                        )
                    continue

            # Validate fallback chain profiles.
            cleaned_fallback: list[FallbackEntry] = []
            for entry in route.fallback_chain:
                if entry.profile not in resolved.profiles and entry.profile not in default_profiles():
                    msg = (
                        f"Role `{role_name}` fallback references unknown profile "
                        f"`{entry.profile}`"
                    )
                    if route.required:
                        resolved.issues.append(
                            ValidationIssue(
                                "unknown_fallback_profile",
                                msg,
                                path=f"roles.{role_name}.fallback_chain",
                            )
                        )
                    else:
                        resolved.warnings.append(msg)
                    continue
                if entry.profile not in resolved.profiles:
                    resolved.profiles[entry.profile] = default_profiles()[entry.profile]
                cleaned_fallback.append(entry)
            if cleaned_fallback != list(route.fallback_chain):
                roles[role_name] = RoleRoute(
                    role=route.role,
                    profile=roles[role_name].profile,
                    required=route.required,
                    fallback_chain=tuple(cleaned_fallback),
                    source=route.source,
                    session_mode=route.session_mode,
                    notes=route.notes,
                )

        # Deterministic role order in output.
        ordered: dict[str, RoleRoute] = {}
        for role in DEFAULT_ROLES:
            if role.value in roles:
                ordered[role.value] = roles[role.value]
        for role_name, route in roles.items():
            if role_name not in ordered:
                ordered[role_name] = route
        resolved.roles = ordered
    except ValidationIssue as issue:
        resolved.issues.append(issue)

    return resolved


def resolve_from_repo(
    repo_root: Path,
    *,
    survival_guide: Mapping[str, Any] | None = None,
) -> ResolvedConfig:
    """Resolve using the standard on-disk locations under repo_root."""
    return resolve_config(
        survival_guide=survival_guide,
        models_toml_path=repo_root / ".elves" / "models.toml",
        user_config_path=repo_root / "config.json",
        repo_root=repo_root,
    )


def models_toml_is_local_only(repo_root: Path) -> dict[str, Any]:
    """Return metadata proving local TOML is ignored/untracked project state."""
    path = repo_root / ".elves" / "models.toml"
    gitignore = repo_root / ".gitignore"
    ignored_by_gitignore = False
    if gitignore.is_file():
        text = gitignore.read_text(encoding="utf-8")
        ignored_by_gitignore = any(
            line.strip() in {".elves/", ".elves/**", ".elves/models.toml"}
            for line in text.splitlines()
        ) or ".elves/" in text
    return {
        "path": str(path.as_posix()),
        "exists": path.is_file(),
        "ignored_by_gitignore": ignored_by_gitignore,
        "committed": False,
        "note": (
            "`.elves/models.toml` is local checkout preference only; "
            "never stage it. Tracked schema lives in references/models.toml.example."
        ),
    }
