#!/usr/bin/env python3
"""Validate or scaffold Elves plan/session acceptance before worker launch."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from cobbler_runtime.acceptance import (
    AcceptanceIssue,
    parse_plan_acceptance_contract,
    session_batch_numbers,
    sync_session_acceptance,
    validate_contract_mapping,
)
from cobbler_runtime.storage import StorageError, atomic_write_json


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate stable Acceptance syntax and plan/session criterion parity "
            "before an Elves worker launch."
        )
    )
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("validate", "sync-session"):
        command = sub.add_parser(name)
        command.add_argument("--repo-root", default=".")
        command.add_argument("--session", default=None)
        command.add_argument(
            "--plan",
            default=None,
            help=(
                "Authoritative plan path. With --session this is an equality "
                "assertion against session plan_path."
            ),
        )
        command.add_argument("--json", action="store_true")
    sync = sub.choices["sync-session"]
    sync.add_argument(
        "--write",
        action="store_true",
        help="Atomically update the session; otherwise print the derived JSON.",
    )
    return parser


def _inside_repo(path: Path, repo_root: Path, *, label: str) -> Path:
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(repo_root)
    except ValueError as exc:
        raise ValueError(f"{label} must stay inside the repository: {resolved}") from exc
    if not resolved.is_file():
        raise ValueError(f"{label} must be a regular file: {resolved}")
    return resolved


def _load_json(path: Path) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, child in pairs:
            if key in value:
                raise ValueError(f"duplicate JSON object key: {key}")
            value[key] = child
        return value

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"Session is not readable JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Session must contain one JSON object: {path}")
    return value


def _resolve_inputs(
    args: argparse.Namespace,
) -> tuple[Path, Path, dict[str, Any] | None, Path | None]:
    repo_root = Path(args.repo_root).expanduser().resolve(strict=True)
    session_path: Path | None = None
    session: dict[str, Any] | None = None
    if args.session:
        session_path = _inside_repo(Path(args.session), repo_root, label="session")
        session = _load_json(session_path)

    explicit_plan = (
        _inside_repo(Path(args.plan), repo_root, label="plan")
        if args.plan
        else None
    )
    recorded_plan: Path | None = None
    if session is not None:
        raw = session.get("plan_path")
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("Session must record a non-empty plan_path before launch")
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = session_path.parent / candidate  # type: ignore[union-attr]
        recorded_plan = _inside_repo(candidate, repo_root, label="recorded plan")
        if explicit_plan is not None and explicit_plan != recorded_plan:
            raise ValueError(
                f"Explicit plan {explicit_plan} does not match session plan_path {recorded_plan}"
            )
    plan_path = explicit_plan or recorded_plan
    if plan_path is None:
        raise ValueError("Pass --plan, or pass --session with a recorded plan_path")
    return repo_root, plan_path, session, session_path


def _emit(
    *,
    ok: bool,
    action: str,
    plan_path: Path | None,
    session_path: Path | None,
    issues: list[AcceptanceIssue],
    as_json: bool,
) -> int:
    payload = {
        "ok": ok,
        "action": action,
        "plan": str(plan_path) if plan_path is not None else None,
        "session": str(session_path) if session_path is not None else None,
        "issues": [
            {"code": issue.code, "message": issue.message, "line": issue.line}
            for issue in issues
        ],
    }
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif ok:
        print("Elves acceptance staging check OK")
        print(f"- Plan: {plan_path}")
        if session_path is not None:
            print(f"- Session: {session_path}")
    else:
        print("Elves acceptance staging check FAILED", file=sys.stderr)
        for issue in issues:
            print(f"- [{issue.code}] {issue.message}", file=sys.stderr)
    return 0 if ok else 1


def _session_has_acceptance_ids(session: dict[str, Any]) -> bool:
    batches = session.get("batches")
    if isinstance(batches, list):
        for batch in batches:
            if not isinstance(batch, dict):
                continue
            acceptance = batch.get("acceptance")
            if isinstance(acceptance, list) and any(
                isinstance(item, dict) and bool(str(item.get("id") or "").strip())
                for item in acceptance
            ):
                return True
    master = session.get("master_acceptance")
    return isinstance(master, list) and any(
        isinstance(item, dict) and bool(str(item.get("id") or "").strip())
        for item in master
    )


def _legacy_session_container_issues(
    session: dict[str, Any],
    *,
    expected_batch_ids: tuple[int, ...],
) -> list[AcceptanceIssue]:
    """Validate legacy session containers without requiring stable row IDs."""

    _, issues = session_batch_numbers(
        session,
        expected_batch_ids=expected_batch_ids,
    )
    batches = session.get("batches")
    if not isinstance(batches, list):
        return issues
    for batch_index, batch in enumerate(batches):
        if not isinstance(batch, dict):
            continue
        acceptance = batch.get("acceptance")
        if acceptance is not None and not isinstance(acceptance, list):
            issues.append(
                AcceptanceIssue(
                    "session_acceptance_invalid",
                    f"Session batches[{batch_index}].acceptance must be an array.",
                )
            )
        elif isinstance(acceptance, list):
            for row_index, row in enumerate(acceptance):
                if not isinstance(row, dict):
                    issues.append(
                        AcceptanceIssue(
                            "session_acceptance_invalid",
                            f"Session batches[{batch_index}].acceptance[{row_index}] must be an object.",
                        )
                    )
                    continue
                criterion = row.get("criterion")
                if not isinstance(criterion, str) or not criterion.strip():
                    issues.append(
                        AcceptanceIssue(
                            "acceptance_criterion_missing",
                            f"Session batches[{batch_index}].acceptance[{row_index}] requires a non-empty criterion.",
                        )
                    )
    master = session.get("master_acceptance")
    if "master_acceptance" in session:
        if not isinstance(master, list):
            issues.append(
                AcceptanceIssue(
                    "session_master_acceptance_invalid",
                    "Session master_acceptance must be an array when present.",
                )
            )
        else:
            for row_index, row in enumerate(master):
                if not isinstance(row, dict):
                    issues.append(
                        AcceptanceIssue(
                            "session_master_acceptance_invalid",
                            f"Session master_acceptance[{row_index}] must be an object.",
                        )
                    )
                    continue
                criterion = row.get("criterion")
                if not isinstance(criterion, str) or not criterion.strip():
                    issues.append(
                        AcceptanceIssue(
                            "acceptance_criterion_missing",
                            f"Session master_acceptance[{row_index}] requires a non-empty criterion.",
                        )
                    )
    return issues


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        repo_root, plan_path, session, session_path = _resolve_inputs(args)
        plan_text = plan_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError) as exc:
        return _emit(
            ok=False,
            action=args.action,
            plan_path=None,
            session_path=None,
            issues=[AcceptanceIssue("acceptance_input_invalid", str(exc))],
            as_json=bool(args.json),
        )

    contract = parse_plan_acceptance_contract(plan_text)
    issues = list(contract.issues)
    if not contract.rows and not issues:
        # Legacy unlabelled plans remain supported. Their evidence mapping is
        # checked by the final landing path after deterministic aliasing.
        legacy_session_issues = (
            _legacy_session_container_issues(
                session,
                expected_batch_ids=contract.batch_ids,
            )
            if session is not None
            else []
        )
        issues.extend(legacy_session_issues)
        if args.action == "sync-session":
            issues.append(
                AcceptanceIssue(
                    "acceptance_ids_required",
                    "sync-session requires stable B#-A#/M-A# plan rows.",
                )
            )
        elif (
            not legacy_session_issues
            and session is not None
            and _session_has_acceptance_ids(session)
        ):
            issues.append(
                AcceptanceIssue(
                    "acceptance_plan_ids_required",
                    "Session contains stable acceptance ids but the authoritative plan does not. "
                    "Persist matching B#-A#/M-A# rows in the plan before launch so criterion drift "
                    "can be checked deterministically.",
                )
            )
    elif args.action == "validate" and session is not None:
        issues.extend(
            validate_contract_mapping(
                contract.rows,
                session,
                plan_batch_ids=contract.batch_ids,
            )
        )

    if args.action == "validate" or issues:
        return _emit(
            ok=not issues,
            action=args.action,
            plan_path=plan_path,
            session_path=session_path,
            issues=issues,
            as_json=bool(args.json),
        )

    if session is None or session_path is None:
        return _emit(
            ok=False,
            action=args.action,
            plan_path=plan_path,
            session_path=session_path,
            issues=[
                AcceptanceIssue(
                    "session_required",
                    "sync-session requires --session with a recorded plan_path.",
                )
            ],
            as_json=bool(args.json),
        )

    updated, sync_issues = sync_session_acceptance(session, contract.rows)
    if sync_issues:
        return _emit(
            ok=False,
            action=args.action,
            plan_path=plan_path,
            session_path=session_path,
            issues=sync_issues,
            as_json=bool(args.json),
        )
    if args.write:
        try:
            atomic_write_json(session_path, updated, repo_root=repo_root)
        except StorageError as exc:
            return _emit(
                ok=False,
                action=args.action,
                plan_path=plan_path,
                session_path=session_path,
                issues=[AcceptanceIssue(exc.code, exc.message)],
                as_json=bool(args.json),
            )
        return _emit(
            ok=True,
            action=args.action,
            plan_path=plan_path,
            session_path=session_path,
            issues=[],
            as_json=bool(args.json),
        )

    print(json.dumps(updated, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
