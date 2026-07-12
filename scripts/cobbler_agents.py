#!/usr/bin/env python3
"""Thin operator CLI for Cobbler external-agent configuration.

Commands:
  validate-config [--json]   Resolve config with provenance; no model calls
  doctor [--json]            Read-only inventory of adapters/capabilities/sessions
  council [--json]           Parallel read-only council fan-out (host synthesis)
  lightweight-review [--json]
                             Single bounded utility review (not a council vote)
  session list|probe|resume  Exact persistent session registry helpers
  worker prepare|audit|export|refresh
                             Single external writer lease lifecycle (host-owned)
  setup [--json]             Inventory tools and write local .elves/models.toml

This entry point stays thin; implementation lives under scripts/cobbler_runtime/.
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
    build_write_resume_invocation,
    grok_write_profile,
    registry_snapshot,
    workspace_sandbox_write_profile,
)
from cobbler_runtime.audit import (  # noqa: E402
    audit_lease_turn,
    export_binary_patches,
    host_apply_check,
    pre_turn_snapshots,
)
from cobbler_runtime.capabilities import (  # noqa: E402
    doctor_inventory,
    summarize_capabilities,
)
from cobbler_runtime.config import (  # noqa: E402
    lanes_from_resolved,
    models_toml_is_local_only,
    resolve_from_repo,
)
from cobbler_runtime.dispatch import (  # noqa: E402
    LaneSpec,
    run_council_sync,
    run_lightweight_review_sync,
)
from cobbler_runtime.leases import LeaseStore, build_write_task_packet  # noqa: E402
from cobbler_runtime.schema import ValidationIssue  # noqa: E402
from cobbler_runtime.sessions import SessionRegistry  # noqa: E402
from cobbler_runtime.setup import (  # noqa: E402
    preferences_from_flags,
    run_setup,
)


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


def cmd_setup(args: argparse.Namespace) -> int:
    """Inventory tools and optionally write ignored local models.toml."""
    repo_root = _repo_root_from_args(args)
    required = [r.strip() for r in (args.required or "").split(",") if r.strip()]
    prefs = preferences_from_flags(
        implement=args.implement,
        review=args.review,
        planning=args.planning,
        lightweight_review=args.lightweight_review,
        validate=args.validate,
        synthesize=args.synthesize,
        scout=args.scout,
        required=required,
        session_mode=args.session_mode,
        sharing_policy=args.sharing_policy,
        native_fallback=not args.no_native_fallback,
    )
    try:
        result = run_setup(
            repo_root,
            preferences=prefs,
            write_toml=not args.dry_run,
            force_toml=bool(args.force),
            run_smoke=bool(args.smoke),
        )
    except ValidationIssue as issue:
        payload = {"ok": False, "issues": [issue.to_dict()], "credentials_printed": False}
        if args.json:
            return _emit_json(payload, exit_code=1)
        print(f"setup: FAILED [{issue.code}] {issue.message}", file=sys.stderr)
        return 1

    payload = result.to_dict()
    payload["mutated_repo"] = False
    payload["staged_models_toml"] = False
    if args.json:
        return _emit_json(payload, exit_code=0 if result.ok else 1)

    print(f"setup: {'OK' if result.ok else 'FAILED'}")
    print(f"  models_toml_written: {result.models_toml_written}")
    print(f"  models_toml_ignored: {result.models_toml_ignored}")
    print(f"  smoke_ran: {result.smoke_ran}")
    print(f"  credentials_printed: {result.credentials_printed}")
    for rec in result.recommendations:
        print(f"  recommend: {rec}")
    for warning in result.warnings:
        print(f"  warning: {warning}")
    for issue in result.issues:
        print(f"  issue: [{issue.get('code')}] {issue.get('message')}")
    print("  note: never stage .elves/models.toml; snapshot routes into Survival Guide when staging")
    return 0 if result.ok else 1


def cmd_worker(args: argparse.Namespace) -> int:
    """Writer lease prepare/audit/export/refresh — host-owned lifecycle."""
    repo_root = _repo_root_from_args(args)
    store = LeaseStore(repo_root)
    action = args.worker_action

    try:
        if action == "prepare":
            profile = grok_write_profile(args.grok_version)
            if args.sandbox_profile == "workspace":
                profile = workspace_sandbox_write_profile()
            lease = store.prepare(
                lease_id=args.lease_id,
                host_checkout=Path(args.host_checkout),
                worker_checkout=Path(args.worker_checkout),
                session_id=args.session_id,
                base_head=args.base_head,
                adapter=args.adapter,
                profile=args.profile,
                allowed_paths=list(args.allowed_path or []),
                sandbox_profile=args.sandbox_profile,
                detached_commits_permitted=profile.detached_commits_permitted,
                write_profile_qualified=profile.qualified and not args.unqualified,
                grok_version=args.grok_version,
            )
            store.activate(lease.lease_id)
            # Capture pre-turn snapshots beside lease record for later audit.
            snaps = pre_turn_snapshots(Path(lease.worker_checkout))
            snap_path = Path(repo_root) / ".elves" / "runtime" / "leases" / f"{lease.lease_id}.pre.json"
            snap_path.write_text(json.dumps(snaps, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            inv = None
            if args.adapter == "grok-build":
                inv = build_write_resume_invocation(
                    adapter="grok-build",
                    session_id=args.session_id,
                    cwd=args.worker_checkout,
                    version=args.grok_version,
                ).to_dict()
            payload = {
                "ok": True,
                "lease": store.get(lease.lease_id).to_dict(),
                "write_resume_invocation": inv,
                "pre_snapshots": snaps,
                "mutated_repo": False,
            }
            if args.json:
                return _emit_json(payload, exit_code=0)
            print(f"worker prepare: {lease.lease_id} ACTIVE")
            return 0

        if action == "audit":
            lease = store.get(args.lease_id)
            store.mark_auditing(lease.lease_id)
            pre_path = (
                Path(repo_root) / ".elves" / "runtime" / "leases" / f"{args.lease_id}.pre.json"
            )
            pre = {}
            if pre_path.is_file():
                pre = json.loads(pre_path.read_text(encoding="utf-8"))
            result = audit_lease_turn(
                store.get(args.lease_id),
                pre_refs_digest=pre.get("refs_digest"),
                pre_remotes=pre.get("remotes"),
                pre_config=pre.get("config"),
                pre_hooks=pre.get("hooks"),
                observed_commands=list(args.observed_command or []),
            )
            if not result.ok:
                store.reject(args.lease_id, "; ".join(result.reasons))
            payload = {"ok": result.ok, "audit": result.to_dict(), "mutated_repo": False}
            if args.json:
                return _emit_json(payload, exit_code=0 if result.ok else 1)
            print(f"worker audit: {'OK' if result.ok else 'FAILED'}")
            for reason in result.reasons:
                print(f"  - {reason}")
            return 0 if result.ok else 1

        if action == "export":
            lease = store.get(args.lease_id)
            out = Path(args.output_dir)
            audit = audit_lease_turn(lease)
            if not audit.ok:
                store.reject(args.lease_id, "; ".join(audit.reasons))
                payload = {"ok": False, "audit": audit.to_dict()}
                if args.json:
                    return _emit_json(payload, exit_code=1)
                print("worker export: FAILED audit", file=sys.stderr)
                return 1
            patches = export_binary_patches(
                lease,
                output_dir=out,
                chain=audit.commit_chain,
            )
            store.mark_exported(args.lease_id, str(out))
            apply_result = None
            if args.host_apply_check:
                apply_result = host_apply_check(
                    Path(lease.host_checkout),
                    patches,
                    base_head=lease.base_head,
                )
            payload = {
                "ok": True,
                "patches": [str(p) for p in patches],
                "audit": audit.to_dict(),
                "host_apply_check": apply_result,
                "mutated_repo": False,
                "note": "Host creates sanitized branch commits after apply-check",
            }
            if args.json:
                return _emit_json(payload, exit_code=0)
            print(f"worker export: {len(patches)} patch(es) -> {out}")
            return 0

        if action == "refresh":
            store.mark_integrated(args.lease_id)
            result = store.refresh_worker_to_tip(args.lease_id, new_tip=args.new_tip)
            store.close(args.lease_id)
            payload = {"ok": True, "refresh": result, "mutated_repo": False}
            if args.json:
                return _emit_json(payload, exit_code=0)
            print(f"worker refresh: tip={result['worker_tip']}")
            return 0

        if action == "packet":
            lease = store.get(args.lease_id)
            packet = build_write_task_packet(lease, task=args.task)
            if args.json:
                return _emit_json({"ok": True, "packet": packet}, exit_code=0)
            print(json.dumps(packet, indent=2, sort_keys=True))
            return 0

    except ValidationIssue as issue:
        payload = {"ok": False, "issues": [issue.to_dict()]}
        if args.json:
            return _emit_json(payload, exit_code=1)
        print(f"worker {action}: FAILED [{issue.code}] {issue.message}", file=sys.stderr)
        return 1

    print(f"unknown worker action: {action}", file=sys.stderr)
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

    if not resolved.ok:
        payload = {
            "ok": False,
            "blocked": True,
            "council_verified": False,
            "issues": [issue.to_dict() for issue in resolved.issues],
            "warnings": list(resolved.warnings),
            "model_calls_made": False,
            "mutated_repo": False,
            "host_synthesis_only": True,
            "external_routing_enabled": resolved.external_routing_enabled,
            "notes": ["resolved config is not ok; refusing council launch"],
        }
        if args.json:
            return _emit_json(payload, exit_code=1)
        print("council: FAILED (resolved config not ok)", file=sys.stderr)
        for issue in resolved.issues:
            print(f"  [{issue.code}] {issue.message}", file=sys.stderr)
        return 1

    # Build lanes from each resolved role/profile without dropping fields.
    # Survival-guide required routes remain even when --use-resolved-routes is off.
    role_names = [
        name.strip()
        for name in (args.roles or "architect,skeptic,tester").split(",")
        if name.strip()
    ]
    lanes: list[LaneSpec] = lanes_from_resolved(
        resolved,
        role_names=role_names,
        timeout_seconds=float(args.timeout),
        use_resolved_routes=bool(args.use_resolved_routes),
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
    # Prefer runtime-truthful counters from dispatch.
    payload["model_calls_made"] = bool(result.model_calls_made)
    payload["mutated_repo"] = bool(result.mutated_repo)
    payload["host_synthesis_only"] = True
    payload["external_routing_enabled"] = resolved.external_routing_enabled
    if args.json:
        return _emit_json(payload, exit_code=0 if result.ok and not result.blocked else 1)

    print(f"council: {'OK' if result.ok and not result.blocked else 'FAILED'}")
    print(f"  run_id: {result.run_id}")
    print(f"  council_verified: {result.council_verified}")
    print(f"  blocked: {result.blocked}")
    print(f"  confidence: {result.confidence}")
    print(f"  successful_reports: {len(result.successful_reports)}")
    print(f"  model_calls_made: {result.model_calls_made}")
    print(f"  mutated_repo: {result.mutated_repo}")
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
    # Explicit lane-level truth from dispatch (not path-substring inference).
    payload["mutated_repo"] = bool(result.mutated_repo)
    payload["model_calls_made"] = bool(result.model_call_made) or any(
        a.model_call_made for a in (result.attempts or [])
    )
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

    setup = sub.add_parser(
        "setup",
        help="Inventory tools and write local ignored .elves/models.toml (no secrets)",
    )
    _add_common_flags(setup)
    setup.add_argument("--implement", default=None, help="Profile for implement role")
    setup.add_argument("--review", default=None, help="Profile for review role")
    setup.add_argument("--planning", default=None, help="Profile for planning role")
    setup.add_argument(
        "--lightweight-review",
        default=None,
        help="Profile for lightweight_review role",
    )
    setup.add_argument("--validate", default=None, help="Profile for validate role")
    setup.add_argument("--synthesize", default=None, help="Profile for synthesize role")
    setup.add_argument("--scout", default=None, help="Profile for scout role")
    setup.add_argument(
        "--required",
        default="",
        help="Comma-separated roles that are required (explicit opt-in)",
    )
    setup.add_argument(
        "--session-mode",
        default="ephemeral",
        choices=["ephemeral", "persistent", "exact_resume"],
        help="Default session mode preference",
    )
    setup.add_argument(
        "--sharing-policy",
        default="local-only",
        help="models.toml sharing policy (local-only by default; never team-shared automatically)",
    )
    setup.add_argument(
        "--no-native-fallback",
        action="store_true",
        help="Do not auto-add host-native fallbacks for external profiles",
    )
    setup.add_argument(
        "--dry-run",
        action="store_true",
        help="Inventory and recommendations only; do not write models.toml",
    )
    setup.add_argument(
        "--force",
        action="store_true",
        help="Overwrite models.toml even when unknown sections exist",
    )
    setup.add_argument(
        "--smoke",
        action="store_true",
        help="Opt into smoke acknowledgment (still does not print secrets or require paid turns)",
    )
    setup.set_defaults(func=cmd_setup)

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

    worker = sub.add_parser(
        "worker",
        help="Single external writer lease lifecycle (host-owned import)",
    )
    worker_sub = worker.add_subparsers(dest="worker_action", required=True)

    w_prepare = worker_sub.add_parser("prepare", help="Create exclusive writer lease after preflight")
    _add_common_flags(w_prepare)
    w_prepare.add_argument("--lease-id", required=True)
    w_prepare.add_argument("--host-checkout", required=True)
    w_prepare.add_argument("--worker-checkout", required=True)
    w_prepare.add_argument("--session-id", required=True)
    w_prepare.add_argument("--base-head", required=True)
    w_prepare.add_argument("--adapter", default="grok-build")
    w_prepare.add_argument("--profile", default="grok-build-write")
    w_prepare.add_argument("--sandbox-profile", default="devbox")
    w_prepare.add_argument("--allowed-path", action="append", default=[])
    w_prepare.add_argument("--grok-version", default="0.2.93")
    w_prepare.add_argument(
        "--unqualified",
        action="store_true",
        help="Mark write profile unqualified (should fail)",
    )
    w_prepare.set_defaults(func=cmd_worker)

    w_audit = worker_sub.add_parser("audit", help="Post-turn audit of worker checkout")
    _add_common_flags(w_audit)
    w_audit.add_argument("--lease-id", required=True)
    w_audit.add_argument(
        "--observed-command",
        action="append",
        default=[],
        help="Command observed during the turn (repeatable)",
    )
    w_audit.set_defaults(func=cmd_worker)

    w_export = worker_sub.add_parser(
        "export",
        help="Export binary patches and optional host apply --check",
    )
    _add_common_flags(w_export)
    w_export.add_argument("--lease-id", required=True)
    w_export.add_argument("--output-dir", required=True)
    w_export.add_argument(
        "--host-apply-check",
        action="store_true",
        help="Run git apply --check --index on host checkout",
    )
    w_export.set_defaults(func=cmd_worker)

    w_refresh = worker_sub.add_parser(
        "refresh",
        help="After integration, move clean detached worker to new host tip",
    )
    _add_common_flags(w_refresh)
    w_refresh.add_argument("--lease-id", required=True)
    w_refresh.add_argument("--new-tip", required=True)
    w_refresh.set_defaults(func=cmd_worker)

    w_packet = worker_sub.add_parser(
        "packet",
        help="Emit write-task packet JSON for an existing lease",
    )
    _add_common_flags(w_packet)
    w_packet.add_argument("--lease-id", required=True)
    w_packet.add_argument("--task", required=True)
    w_packet.set_defaults(func=cmd_worker)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
