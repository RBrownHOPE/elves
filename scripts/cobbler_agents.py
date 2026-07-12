#!/usr/bin/env python3
"""Thin operator CLI for Cobbler external-agent configuration.

Commands:
  validate-config [--json]   Resolve config with provenance; no model calls
  doctor [--json]            Read-only inventory of adapters/capabilities/sessions
  council [--json]           Parallel read-only council fan-out (host synthesis)
  lightweight-review [--json]
                             Single bounded utility review (not a council vote)
  session list|probe|resume  Exact persistent session registry helpers

Writer leases land in later batches. This entry point stays thin; implementation
lives under scripts/cobbler_runtime/.
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

from cobbler_runtime.adapters import (  # noqa: E402
    build_session_resume_invocation,
    registry_snapshot,
)
from cobbler_runtime.capabilities import (  # noqa: E402
    doctor_inventory,
    summarize_capabilities,
)
from cobbler_runtime.config import (  # noqa: E402
    models_toml_is_local_only,
    resolve_from_repo,
)
from cobbler_runtime.dispatch import (  # noqa: E402
    LaneSpec,
    run_council_sync,
    run_lightweight_review_sync,
)
from cobbler_runtime.schema import ValidationIssue  # noqa: E402
from cobbler_runtime.sessions import SessionRegistry  # noqa: E402


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
    inventory = doctor_inventory(resolved.profiles)
    registry = SessionRegistry(repo_root)
    sessions = [rec.to_dict() for rec in registry.list_sessions()]
    payload: dict[str, Any] = {
        "ok": resolved.ok,
        "repo_root": str(repo_root),
        "model_calls_made": False,
        "mutated_repo": False,
        "adapters": inventory["adapters"],
        "adapter_registry": registry_snapshot(),
        "capabilities": summarize_capabilities(resolved.profiles),
        "roles": {name: route.to_dict() for name, route in resolved.roles.items()},
        "sessions": sessions,
        "session_count": len(sessions),
        "issues": [issue.to_dict() for issue in resolved.issues],
        "warnings": list(resolved.warnings),
        "local_models_toml": models_toml_is_local_only(repo_root),
        "notes": inventory.get("notes", [])
        + [
            "Doctor reports executable/version/auth/models/session_support separately.",
            "remaining_quota is unknown unless a harness exposes it.",
            "Exact session IDs only; bare --resume/--continue/--last are forbidden.",
            "Git and PR operations never dispatch model inference.",
        ],
    }
    if args.json:
        return _emit_json(payload, exit_code=0 if resolved.ok else 1)

    print("cobbler doctor")
    print(f"  repo_root: {repo_root}")
    print(f"  model_calls_made: false")
    print(f"  ok: {resolved.ok}")
    print(f"  session_count: {len(sessions)}")
    print("  adapters:")
    for name, info in sorted(payload["adapters"].items()):
        print(
            f"    - {name}: version={info.get('version')} auth={info.get('auth')} "
            f"session_support={info.get('session_support', {}).get('status')} "
            f"remaining_quota={info.get('remaining_quota')}"
        )
    if resolved.issues:
        print("  issues:")
        for issue in resolved.issues:
            print(f"    - [{issue.code}] {issue.message}")
    return 0 if resolved.ok else 1


def cmd_session(args: argparse.Namespace) -> int:
    """Exact session registry helpers (list / probe / resume argv)."""
    repo_root = _repo_root_from_args(args)
    registry = SessionRegistry(repo_root)
    action = args.session_action

    if action == "list":
        records = [rec.to_dict() for rec in registry.list_sessions()]
        payload = {
            "ok": True,
            "repo_root": str(repo_root),
            "sessions": records,
            "count": len(records),
            "mutated_repo": False,
        }
        if args.json:
            return _emit_json(payload, exit_code=0)
        print(f"session list: {len(records)} record(s)")
        for rec in records:
            print(
                f"  - {rec['session_id']} harness={rec['harness']} "
                f"lifecycle={rec['lifecycle']} parent={rec.get('parent_id')}"
            )
        return 0

    if action == "probe":
        try:
            rec = registry.get(args.session_id)
        except ValidationIssue as issue:
            payload = {"ok": False, "issues": [issue.to_dict()]}
            if args.json:
                return _emit_json(payload, exit_code=1)
            print(f"session probe: FAILED [{issue.code}] {issue.message}", file=sys.stderr)
            return 1
        payload = {"ok": True, "session": rec.to_dict(), "mutated_repo": False}
        if args.json:
            return _emit_json(payload, exit_code=0)
        print(f"session probe: {rec.session_id}")
        print(f"  lifecycle: {rec.lifecycle.value}")
        print(f"  harness: {rec.harness}")
        print(f"  actual_model: {rec.actual_model}")
        print(f"  parent_id: {rec.parent_id}")
        print(f"  cwd: {rec.cwd}")
        print(f"  write_reuse_blocked: {rec.write_reuse_blocked}")
        return 0

    if action == "resume":
        # Build exact resume argv only — does not launch paid inference.
        try:
            inv = build_session_resume_invocation(
                adapter=args.adapter,
                profile=args.profile or args.adapter,
                session_id=args.session_id,
                executable=args.executable,
                requested_model=args.model,
                cwd=args.cwd,
            )
        except ValidationIssue as issue:
            payload = {"ok": False, "issues": [issue.to_dict()]}
            if args.json:
                return _emit_json(payload, exit_code=1)
            print(f"session resume: FAILED [{issue.code}] {issue.message}", file=sys.stderr)
            return 1
        payload = {
            "ok": True,
            "session_id": args.session_id,
            "invocation": inv.to_dict(),
            "launched": False,
            "mutated_repo": False,
            "notes": [
                "Exact session resume argv only; no process was launched",
                "Verify CWD/worktree registration before any write role",
            ],
        }
        if args.json:
            return _emit_json(payload, exit_code=0)
        print("session resume: argv built (not launched)")
        print("  " + " ".join(inv.argv))
        return 0

    print(f"unknown session action: {action}", file=sys.stderr)
    return 2


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


def cmd_council(args: argparse.Namespace) -> int:
    """Parallel read-only council fan-out. Host still owns fitted synthesis."""
    repo_root = _repo_root_from_args(args)
    resolved = resolve_from_repo(repo_root)

    # Default lanes: host-native only unless profiles map planning/review externally.
    # Deterministic and network-free unless executables exist for mapped adapters.
    role_names = [name.strip() for name in (args.roles or "architect,skeptic,tester").split(",") if name.strip()]
    lanes: list[LaneSpec] = []
    for index, role in enumerate(role_names):
        # Prefer host-native for CLI smoke so no paid providers are required.
        profile_name = "host-native"
        adapter_name = "host-native"
        route = resolved.roles.get("review") or resolved.roles.get("planning")
        if route and route.profile in resolved.profiles and args.use_resolved_routes:
            profile = resolved.profiles[route.profile]
            profile_name = profile.name
            adapter_name = profile.adapter
        lanes.append(
            LaneSpec(
                lane_id=f"{role}-{index}",
                role=role,
                adapter=adapter_name,
                profile=profile_name,
                requested_model=None,
                timeout_seconds=float(args.timeout),
            )
        )

    target = args.target_quorum
    required = args.required_quorum
    phase_required = bool(args.phase_required)
    result = run_council_sync(
        lanes,
        repo_root=repo_root,
        task=args.task,
        phase=args.phase,
        phase_required=phase_required,
        target_quorum=target,
        required_quorum=required if phase_required else None,
        plan_path=args.plan_path,
        head_sha=args.head,
    )
    payload = result.to_dict()
    payload["model_calls_made"] = any(
        lane.adapter != "host-native" for lane in result.lane_results
    )
    payload["mutated_repo"] = False
    payload["host_synthesis_only"] = True
    if args.json:
        return _emit_json(payload, exit_code=0 if result.ok and not result.blocked else 1)

    print(f"council: {'OK' if result.ok and not result.blocked else 'FAILED'}")
    print(f"  run_id: {result.run_id}")
    print(f"  council_verified: {result.council_verified}")
    print(f"  blocked: {result.blocked}")
    print(f"  confidence: {result.confidence}")
    print(f"  successful_reports: {len(result.successful_reports)}")
    for note in result.notes:
        print(f"  note: {note}")
    return 0 if result.ok and not result.blocked else 1


def cmd_lightweight_review(args: argparse.Namespace) -> int:
    """Single bounded utility review; not a council vote and not high-risk close."""
    repo_root = _repo_root_from_args(args)
    result = run_lightweight_review_sync(
        repo_root=repo_root,
        task=args.task,
        adapter=args.adapter,
        profile=args.profile,
        executable=args.executable,
        requested_model=args.model,
        timeout_seconds=float(args.timeout),
        head_sha=args.head,
    )
    payload = result.to_dict()
    payload["not_a_council_vote"] = True
    payload["cannot_close_high_risk_review"] = True
    payload["mutated_repo"] = False
    if args.json:
        return _emit_json(payload, exit_code=0 if result.ok else 1)
    print(f"lightweight-review: {'OK' if result.ok else 'FAILED'}")
    print(f"  role: {result.role}")
    print(f"  adapter: {result.adapter}")
    if result.error:
        print(f"  error: {result.error}")
    return 0 if result.ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cobbler_agents.py",
        description=(
            "Cobbler external-agent operator CLI "
            "(config validation, doctor, read-only council)."
        ),
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

    council = sub.add_parser(
        "council",
        help="Parallel read-only council fan-out; host owns fitted synthesis",
    )
    _add_common_flags(council)
    council.add_argument("--task", required=True, help="Task text for every independent lane")
    council.add_argument(
        "--roles",
        default="architect,skeptic,tester",
        help="Comma-separated lens role names (default: architect,skeptic,tester)",
    )
    council.add_argument("--phase", default="review", help="Phase label for artifacts/quorum")
    council.add_argument(
        "--phase-required",
        action="store_true",
        help="Treat phase as required=true (enables required_quorum)",
    )
    council.add_argument("--target-quorum", type=int, default=None, help="Advisory quorum")
    council.add_argument(
        "--required-quorum",
        type=int,
        default=None,
        help="Hard quorum when --phase-required is set",
    )
    council.add_argument("--timeout", type=float, default=30.0, help="Per-lane timeout seconds")
    council.add_argument("--plan-path", default=None, help="Optional plan path for packets")
    council.add_argument("--head", default=None, help="Optional HEAD sha for packets")
    council.add_argument(
        "--use-resolved-routes",
        action="store_true",
        help="Use resolved review/planning profile adapters (may require external CLIs)",
    )
    council.set_defaults(func=cmd_council)

    light = sub.add_parser(
        "lightweight-review",
        help="Single bounded utility review; not a council vote",
    )
    _add_common_flags(light)
    light.add_argument("--task", required=True, help="Utility review task")
    light.add_argument("--adapter", default="host-native", help="Adapter name")
    light.add_argument("--profile", default="host-native", help="Profile name")
    light.add_argument("--executable", default=None, help="Optional executable override")
    light.add_argument("--model", default=None, help="Optional requested model")
    light.add_argument("--timeout", type=float, default=30.0, help="Timeout seconds")
    light.add_argument("--head", default=None, help="Optional HEAD sha for packets")
    light.set_defaults(func=cmd_lightweight_review)

    session = sub.add_parser(
        "session",
        help="Exact persistent session registry helpers (list|probe|resume)",
    )
    session_sub = session.add_subparsers(dest="session_action", required=True)

    session_list = session_sub.add_parser("list", help="List registry sessions")
    _add_common_flags(session_list)
    session_list.set_defaults(func=cmd_session)

    session_probe = session_sub.add_parser("probe", help="Probe one exact session id")
    _add_common_flags(session_probe)
    session_probe.add_argument("--session-id", required=True, help="Exact session id")
    session_probe.set_defaults(func=cmd_session)

    session_resume = session_sub.add_parser(
        "resume",
        help="Build exact resume argv (does not launch models)",
    )
    _add_common_flags(session_resume)
    session_resume.add_argument("--session-id", required=True, help="Exact session id")
    session_resume.add_argument("--adapter", default="claude-code", help="Adapter name")
    session_resume.add_argument("--profile", default=None, help="Profile name")
    session_resume.add_argument("--executable", default=None, help="Executable override")
    session_resume.add_argument("--model", default=None, help="Requested model")
    session_resume.add_argument("--cwd", default=None, help="Verified CWD/worktree path")
    session_resume.set_defaults(func=cmd_session)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
