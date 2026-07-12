#!/usr/bin/env python3
"""Thin operator CLI for Cobbler external-agent configuration.

Batch 1 commands:
  validate-config [--json]   Resolve config with provenance; no model calls
  doctor [--json]            Read-only inventory of adapters/capabilities

Later batches add council/session/worker subcommands. This entry point stays thin;
implementation lives under scripts/cobbler_runtime/.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from cobbler_runtime.adapters import registry_snapshot  # noqa: E402
from cobbler_runtime.capabilities import summarize_capabilities  # noqa: E402
from cobbler_runtime.config import (  # noqa: E402
    models_toml_is_local_only,
    resolve_from_repo,
)
from cobbler_runtime.schema import ValidationIssue  # noqa: E402


def _repo_root_from_args(args: argparse.Namespace) -> Path:
    if args.repo_root:
        return Path(args.repo_root).expanduser().resolve()
    return Path.cwd().resolve()


def _emit_json(payload: dict[str, Any], *, exit_code: int) -> int:
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return exit_code


def _emit_text_validate(payload: dict[str, Any]) -> int:
    if payload["ok"]:
        print("validate-config: OK")
        for role, route in sorted(payload["roles"].items()):
            print(
                f"  {role}: profile={route['profile']} "
                f"source={route['source']} required={route['required']}"
            )
        for warning in payload.get("warnings") or []:
            print(f"warning: {warning}")
        return 0
    print("validate-config: FAILED", file=sys.stderr)
    for issue in payload.get("issues") or []:
        path = f" ({issue['path']})" if issue.get("path") else ""
        print(f"  [{issue['code']}]{path} {issue['message']}", file=sys.stderr)
        if issue.get("hint"):
            print(f"    hint: {issue['hint']}", file=sys.stderr)
    return 1


def cmd_validate_config(args: argparse.Namespace) -> int:
    repo_root = _repo_root_from_args(args)
    try:
        resolved = resolve_from_repo(repo_root)
    except ValidationIssue as issue:
        payload = {
            "ok": False,
            "issues": [issue.to_dict()],
            "warnings": [],
            "roles": {},
            "profiles": {},
            "sources_consulted": [],
            "mutated_repo": False,
            "model_calls_made": False,
            "repo_root": str(repo_root),
        }
        if args.json:
            return _emit_json(payload, exit_code=1)
        return _emit_text_validate(payload)

    payload = resolved.to_dict()
    payload["mutated_repo"] = False
    payload["model_calls_made"] = False
    payload["repo_root"] = str(repo_root)
    payload["local_models_toml"] = models_toml_is_local_only(repo_root)
    if args.json:
        return _emit_json(payload, exit_code=0 if resolved.ok else 1)
    return _emit_text_validate(payload)


def cmd_doctor(args: argparse.Namespace) -> int:
    """Read-only inventory. Never launches paid model turns."""
    repo_root = _repo_root_from_args(args)
    resolved = resolve_from_repo(repo_root)
    payload: dict[str, Any] = {
        "ok": resolved.ok,
        "repo_root": str(repo_root),
        "model_calls_made": False,
        "mutated_repo": False,
        "adapters": registry_snapshot(),
        "capabilities": summarize_capabilities(resolved.profiles),
        "roles": {name: route.to_dict() for name, route in resolved.roles.items()},
        "issues": [issue.to_dict() for issue in resolved.issues],
        "warnings": list(resolved.warnings),
        "local_models_toml": models_toml_is_local_only(repo_root),
        "notes": [
            "Doctor reports advertised/unknown capabilities without launching models.",
            "Qualification probes land in later batches.",
            "Git and PR operations never dispatch model inference.",
        ],
    }
    if args.json:
        return _emit_json(payload, exit_code=0 if resolved.ok else 1)

    print("cobbler doctor")
    print(f"  repo_root: {repo_root}")
    print(f"  model_calls_made: false")
    print(f"  ok: {resolved.ok}")
    print("  adapters:")
    for name in sorted(payload["adapters"]):
        print(f"    - {name}")
    if resolved.issues:
        print("  issues:")
        for issue in resolved.issues:
            print(f"    - [{issue.code}] {issue.message}")
    return 0 if resolved.ok else 1


def _add_common_flags(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "--repo-root",
        default=None,
        help="Repository root for config discovery (default: cwd)",
    )
    subparser.add_argument(
        "--json",
        action="store_true",
        help="Emit stable machine-readable JSON",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cobbler_agents.py",
        description="Cobbler external-agent operator CLI (config validation and doctor).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser(
        "validate-config",
        help="Resolve role routes with provenance; no model inference or repo mutation",
    )
    _add_common_flags(validate)
    validate.set_defaults(func=cmd_validate_config)

    doctor = sub.add_parser(
        "doctor",
        help="Read-only adapter/capability inventory; never launches model turns",
    )
    _add_common_flags(doctor)
    doctor.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
