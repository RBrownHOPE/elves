"""Focused components for a single council/implement attempt.

Extracted from the former monolithic ``_run_single_attempt`` path:
- transport/env scrubbing
- host-native executor binding
- subprocess launch + process-group cleanup
- artifact writes
- result/redaction assembly

``dispatch._run_single_attempt`` remains the thin coordinator.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .adapters import AdapterInvocation
from .context import EnvScrubResult, redact_structure, redact_text, scrub_environment
from .schema import EffectiveAttempt


@dataclass
class AttemptTransport:
    """Env scrub + exact secret values for redaction."""

    scrub: EnvScrubResult
    grants: tuple[str, ...]
    exact_secret_values: frozenset[str]


def prepare_transport(
    *,
    parent_env: Mapping[str, str] | None,
    env_extra_allowlist: tuple[str, ...] | list[str],
    grants: tuple[str, ...] | list[str],
) -> AttemptTransport:
    scrub = scrub_environment(
        parent_env,
        extra_allowlist=set(env_extra_allowlist),
        secret_grants=set(grants),
    )
    exact = frozenset(
        scrub.env[name] for name in grants if name in scrub.env and scrub.env[name]
    )
    return AttemptTransport(
        scrub=scrub,
        grants=tuple(grants),
        exact_secret_values=exact,
    )


def write_attempt_artifacts(
    attempt_dir: Path,
    *,
    packet_dict: Any,
    redacted_task: str,
    prompt_body: str | None,
    write_json_artifact,
    write_text_artifact,
) -> tuple[Path, Path]:
    """Write redacted packet/prompt artifacts under the attempt directory."""
    packet_path = write_json_artifact(attempt_dir / "packet.json", packet_dict)
    prompt_path = attempt_dir / "prompt.txt"
    if prompt_body is not None:
        write_text_artifact(prompt_path, prompt_body)
    elif not prompt_path.exists():
        write_text_artifact(prompt_path, redacted_task)
    return packet_path, prompt_path


def build_effective_contract(
    attempt: EffectiveAttempt,
    *,
    grants: tuple[str, ...],
    repo_root: Path,
    exact_secret_values: frozenset[str],
    qualified_capabilities: tuple[str, ...] | list[str],
) -> dict[str, Any]:
    return {
        "profile": attempt.profile,
        "adapter": attempt.adapter,
        "executable": attempt.executable,
        "requested_model": redact_text(
            str(attempt.requested_model or ""), exact_values=exact_secret_values
        ).text
        or attempt.requested_model,
        "extra_args": list(
            redact_structure(list(attempt.extra_args), exact_values=exact_secret_values)
        ),
        "input_contract": attempt.input_contract,
        "output_contract": attempt.output_contract,
        "capabilities": list(attempt.capabilities),
        "qualified_capabilities": list(
            attempt.qualified_capabilities or qualified_capabilities
        ),
        "env_grant_names": list(grants),
        "cwd": str(repo_root),
        "reason": attempt.reason,
        "required": attempt.required,
        "enabled": attempt.enabled,
        "source": attempt.source,
    }


def record_command_digests(
    contract: dict[str, Any],
    *,
    raw_command: list[str],
    exact_secret_values: frozenset[str],
    invocation: AdapterInvocation,
) -> list[str]:
    redacted_command = list(
        redact_structure(raw_command, exact_values=exact_secret_values)
    )
    contract["argv"] = redacted_command
    contract["argv_digest"] = hashlib.sha256(
        "\0".join(raw_command).encode()
    ).hexdigest()[:16]
    contract["decoder"] = invocation.decoder
    contract["input_mode"] = invocation.input_mode
    return redacted_command


def classify_failure(
    *,
    timeout: bool,
    exit_code: int | None,
    error: str,
) -> str:
    if timeout:
        return "timeout"
    lower = (error or "").lower()
    if "not found" in lower or "launch" in lower:
        return "launch_error"
    if "capability" in lower:
        return "capability"
    if "unavailable" in lower:
        return "unavailable"
    if exit_code not in (None, 0):
        return "execution_failure"
    return "execution_failure"


def monotonic_now() -> float:
    return time.monotonic()
