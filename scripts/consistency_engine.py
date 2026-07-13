"""Generic repo-consistency engine (phrase matching helpers).

Policy inventory lives in consistency_policy.py. This module stays small and
behavior-focused — preferred phrases alone must not certify product behavior.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

from consistency_policy import *  # noqa: F403

REPO_ROOT = Path(__file__).resolve().parent.parent

def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def read_frontmatter_version(path: Path) -> str | None:
    match = re.search(r'^\s*version:\s*"([^"]+)"\s*$', read_text(path), re.MULTILINE)
    return match.group(1) if match else None


def read_latest_changelog_version(path: Path) -> str | None:
    match = re.search(r"^## \[([^\]]+)\] - ", read_text(path), re.MULTILINE)
    return match.group(1) if match else None


def verify_order(label: str, text: str, tokens: list[str], errors: list[str]) -> None:
    lower = text.lower()
    cursor = 0
    for token in tokens:
        index = lower.find(token.lower(), cursor)
        if index == -1:
            errors.append(f"{label}: missing recovery-order token `{token}`")
            return
        cursor = index + len(token)


def find_missing_phrases(
    texts: dict[str, str],
    phrase_map: dict[str, list[str]],
    category: str,
) -> list[str]:
    errors: list[str] = []
    for label, phrases in phrase_map.items():
        text = texts.get(label, "")
        for phrase in phrases:
            if phrase not in text:
                errors.append(f"{label}: missing {category} phrase `{phrase}`")
    return errors


def find_forbidden_phrases(
    texts: dict[str, str],
    phrase_map: dict[str, list[str]],
    category: str,
) -> list[str]:
    errors: list[str] = []
    for label, phrases in phrase_map.items():
        text = texts.get(label, "")
        for phrase in phrases:
            if phrase in text:
                errors.append(f"{label}: stale {category} phrase `{phrase}`")
    return errors


def find_forbidden_patterns(
    texts: dict[str, str],
    pattern_map: dict[str, list[str]],
    category: str,
) -> list[str]:
    errors: list[str] = []
    for label, patterns in pattern_map.items():
        text = texts.get(label, "")
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                errors.append(f"{label}: stale {category} pattern `{pattern}`")
    return errors


def extract_markdown_section(text: str, heading: str) -> str:
    lines = text.splitlines()
    heading_level = len(heading) - len(heading.lstrip("#"))
    start_index: int | None = None
    for index, line in enumerate(lines):
        if line.strip() == heading:
            start_index = index
            break
    if start_index is None:
        return ""

    section: list[str] = []
    next_heading_re = re.compile(rf"^#{{1,{heading_level}}}\s+")
    for line in lines[start_index:]:
        if section and next_heading_re.match(line):
            break
        section.append(line)
    return "\n".join(section)


def public_wording_texts() -> dict[str, str]:
    paths = [
        *PUBLIC_WORDING_FILES,
        *sorted((REPO_ROOT / "references").glob("*.md")),
        *sorted((REPO_ROOT / "aliases" / "claude").glob("*/SKILL.md")),
    ]
    return {
        path.relative_to(REPO_ROOT).as_posix(): read_text(path)
        for path in paths
        if path.exists()
    }


def find_missing_section_phrases(
    texts: dict[str, str],
    phrase_map: dict[str, list[str]],
    headings: dict[str, str],
    category: str,
) -> list[str]:
    errors: list[str] = []
    for label, heading in headings.items():
        text = texts.get(label, "")
        section = extract_markdown_section(text, heading)
        if not section:
            errors.append(f"{label}: missing {category} section `{heading}`")
            continue
        for phrase in phrase_map.get(label, []):
            if phrase not in section:
                errors.append(f"{label}: missing {category} phrase `{phrase}`")
    return errors


def validate_config_domain_workflow() -> list[str]:
    errors: list[str] = []
    config = json.loads(read_text(REPO_ROOT / "config.json.example"))
    math_config = config.get("math")
    if not isinstance(math_config, dict):
        return ["config.json.example: missing `math` config object"]

    if math_config.get("coordination") != "cobbler-managed-domain-workflow":
        errors.append(
            "config.json.example: `math.coordination` must be `cobbler-managed-domain-workflow`"
        )

    if math_config.get("provider_policy") == "openrouter-first":
        errors.append("config.json.example: `math.provider_policy` must not be `openrouter-first`")

    if math_config.get("provider_policy") != "native-first-with-optional-external-routes":
        errors.append(
            "config.json.example: `math.provider_policy` must be "
            "`native-first-with-optional-external-routes`"
        )

    required_env = math_config.get("required_env")
    if required_env not in ([], None):
        errors.append("config.json.example: `math.required_env` must be empty by default")

    optional_env = math_config.get("optional_env")
    if not isinstance(optional_env, list) or "OPENROUTER_API_KEY" not in optional_env:
        errors.append("config.json.example: `OPENROUTER_API_KEY` should be optional for math")

    role_models = math_config.get("role_models", {})
    if isinstance(role_models, dict):
        openrouter_defaults = [
            role for role, route in role_models.items() if str(route).startswith("openrouter:")
        ]
        if openrouter_defaults:
            roles = ", ".join(sorted(openrouter_defaults))
            errors.append(
                "config.json.example: math role defaults must be native/direct, not "
                f"OpenRouter (`{roles}`)"
            )
    else:
        errors.append("config.json.example: `math.role_models` must be an object")

    external_examples = math_config.get("external_route_examples", {})
    if not isinstance(external_examples, dict) or not any(
        str(route).startswith("openrouter:") for route in external_examples.values()
    ):
        errors.append(
            "config.json.example: optional OpenRouter math examples belong under "
            "`math.external_route_examples`"
        )

    return errors


