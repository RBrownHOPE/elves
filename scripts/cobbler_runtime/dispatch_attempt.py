"""Attempt policy, transport, and artifact primitives for council dispatch.

Process ownership lives in ``dispatch_external`` and result ownership lives in
``dispatch_results``.  Keeping those layers separate lets the lane lifecycle
depend only on focused modules rather than importing the dispatch facade.
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


def attempt_env_grants(spec: Any, attempt: EffectiveAttempt) -> tuple[str, ...]:
    """Resolve grants without allowing a fallback to inherit primary secrets."""
    if attempt.env_grants:
        return tuple(attempt.env_grants)
    # Primary-only convenience when attempts have not already been expanded.
    if not spec.attempts and attempt.reason == "primary":
        return tuple(spec.env_grants)
    return ()


def check_capabilities(
    attempt: EffectiveAttempt,
    *,
    lane_qualified: tuple[str, ...] = (),
) -> tuple[str | None, list[str], list[str]]:
    """Compare requirements with a trusted qualification snapshot."""
    required = [name for name in (attempt.capabilities or ()) if name]
    if not required:
        return None, [], []
    if attempt.adapter == "host-native":
        return None, required, []
    qualified = set(attempt.qualified_capabilities or ()) | set(lane_qualified or ())
    missing = [name for name in required if name not in qualified]
    if missing:
        return (
            "capability_mismatch: required capabilities not qualified for attempt: "
            + ", ".join(missing),
            required,
            missing,
        )
    return None, required, []


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
    error: str | None,
) -> str:
    if timeout:
        return "timeout"
    text = (error or "").lower()
    if (
        "unsafe_extra_args" in text
        or "duplicate_extra_args" in text
        or "may not override reserved" in text
        or "duplicate extra_args" in text
    ):
        return "unsafe_arguments"
    if "capability" in text:
        return "capability"
    if "not found" in text or "executable not found" in text or "launch" in text:
        return "launch_error"
    if "disabled" in text or "unavailable" in text or "host_native" in text:
        return "unavailable"
    if "actual_model" in text or "untrusted_model" in text:
        return "model_evidence"
    if (
        "json" in text
        or "malformed" in text
        or "missing_report" in text
        or "role_mismatch" in text
        or "invalid_verdict" in text
        or "invalid_confidence" in text
        or "invalid_report" in text
    ):
        return "malformed_output"
    if exit_code not in (None, 0):
        return "execution_failure"
    if error:
        return "execution_failure"
    return "unknown"


def monotonic_now() -> float:
    return time.monotonic()
