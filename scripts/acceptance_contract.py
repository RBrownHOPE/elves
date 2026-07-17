#!/usr/bin/env python3
"""Validate or scaffold Elves plan/session acceptance before worker launch."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from cobbler_runtime.acceptance import (
    AcceptanceIssue,
    AcceptanceRow,
    STABLE_ACCEPTANCE_ID_RE,
    normalize_batch_id,
    parse_markdown_acceptance_rows,
    parse_plan_acceptance_contract,
    session_batch_numbers,
    sync_session_acceptance,
    validate_contract_mapping,
)
from cobbler_runtime.landing_authority import EXACT_COMMIT_RE
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
    try:
        return _decode_json_object(path.read_text(encoding="utf-8"), label=str(path))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"Session is not readable JSON: {path}: {exc}") from exc


def _decode_json_object(text: str, *, label: str) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, child in pairs:
            if key in value:
                raise ValueError(f"duplicate JSON object key: {key}")
            value[key] = child
        return value

    value = json.loads(text, object_pairs_hook=reject_duplicate_keys)
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain one JSON object")
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
            candidate = repo_root / candidate
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
    warnings: list[AcceptanceIssue] | None = None,
) -> int:
    advisory = list(warnings or [])
    payload = {
        "ok": ok,
        "action": action,
        "plan": str(plan_path) if plan_path is not None else None,
        "session": str(session_path) if session_path is not None else None,
        "issues": [
            {"code": issue.code, "message": issue.message, "line": issue.line}
            for issue in issues
        ],
        "warnings": [
            {"code": warning.code, "message": warning.message}
            for warning in advisory
        ],
    }
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif ok:
        print("Elves acceptance staging check OK")
        print(f"- Plan: {plan_path}")
        if session_path is not None:
            print(f"- Session: {session_path}")
        for warning in advisory:
            print(f"- WARN [{warning.code}]: {warning.message}")
    else:
        print("Elves acceptance staging check FAILED", file=sys.stderr)
        for issue in issues:
            print(f"- [{issue.code}] {issue.message}", file=sys.stderr)
        for warning in advisory:
            print(f"- WARN [{warning.code}]: {warning.message}", file=sys.stderr)
    return 0 if ok else 1


# Work-driver spellings: hyphen and underscore forms are equivalent
# (`grok-build` == `grok_build`); the canonical mapping lives in
# references/schema-and-acceptance.md. These drivers implement in a separate
# session and therefore expect a staged coordinator→implementer packet.
_HOST_NATIVE_WORK_DRIVERS: frozenset[str] = frozenset({"", "host-native", "n-a"})
_DELEGATED_SCOPES: frozenset[str] = frozenset({"batch", "full-run"})
_HANDOFF_MODES: frozenset[str] = frozenset({"fresh_start", "resume_active_batch"})
_MAX_WORKER_PACKET_BYTES = 1_000_000
_MARKDOWN_HANDOFF_PREFIX = "<!-- elves-handoff-v1\n"
_MARKDOWN_HANDOFF_SUFFIX = "\n-->"
_HANDOFF_FIELDS: frozenset[str] = frozenset(
    {
        "schema_version",
        "mode",
        "active_batch",
        "product_implementation_started",
        "coordinator_completed_slices",
        "worker_owned_acceptance_ids",
        "coordinator_owned_acceptance_ids",
        "next_exact_action",
    }
)
_HANDOFF_SLICE_FIELDS: frozenset[str] = frozenset(
    {"description", "evidence", "commit"}
)
_PACKET_CAPSULE_FIELDS: frozenset[str] = frozenset(
    {"schema_version", "run_id", "branch", "launch_head", "handoff"}
)


def _normalize_work_driver(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower().replace("_", "-")


def _is_delegated_run(session: dict[str, Any]) -> bool:
    driver = _normalize_work_driver(session.get("work_driver"))
    scope = _normalize_work_driver(session.get("delegation_scope"))
    if driver in {"host-native", "n-a"}:
        return False
    return bool(driver) or scope in _DELEGATED_SCOPES


def _worker_packet_warnings(session: dict[str, Any]) -> list[AcceptanceIssue]:
    """Preserve the v2.8 advisory for delegable sessions without explicit v1 state."""

    if not _is_delegated_run(session) or "handoff" in session:
        return []
    packet = session.get("worker_packet_path")
    if isinstance(packet, str) and packet.strip():
        return []
    driver = _normalize_work_driver(session.get("work_driver")) or "unspecified"
    return [
        AcceptanceIssue(
            "worker_packet_missing",
            f"Session records delegated work_driver `{driver}` but no `worker_packet_path`. "
            "Record the consolidated packet before launch (advisory only); declare a "
            "session `handoff` object to opt into strict handoff-v1 validation.",
        )
    ]


def _read_worker_packet(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            raw = handle.read(_MAX_WORKER_PACKET_BYTES + 1)
    except OSError as exc:
        raise ValueError(f"worker packet is not readable: {path}: {exc}") from exc
    if len(raw) > _MAX_WORKER_PACKET_BYTES:
        raise ValueError(
            f"worker packet exceeds {_MAX_WORKER_PACKET_BYTES} bytes: {path}"
        )
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"worker packet must be valid UTF-8: {path}") from exc


def _session_acceptance_state(session: dict[str, Any]) -> dict[str, bool]:
    state: dict[str, bool] = {}
    containers = [session.get("batches"), session.get("master_acceptance")]
    for container in containers:
        if not isinstance(container, list):
            continue
        for entry in container:
            if not isinstance(entry, dict):
                continue
            rows = entry.get("acceptance") if "acceptance" in entry else [entry]
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                acceptance_id = row.get("id")
                if isinstance(acceptance_id, str) and acceptance_id.strip():
                    state[acceptance_id] = row.get("met") is True
    return state


def _handoff_id_list(
    handoff: dict[str, Any],
    field: str,
    *,
    issues: list[AcceptanceIssue],
) -> list[str] | None:
    value = handoff.get(field)
    if not isinstance(value, list) or any(
        not isinstance(item, str)
        or STABLE_ACCEPTANCE_ID_RE.fullmatch(item.strip()) is None
        for item in value
    ):
        issues.append(
            AcceptanceIssue(
                "delegated_handoff_ownership_invalid",
                f"Session handoff `{field}` must be an array of canonical B#-A#/M-A# ids.",
            )
        )
        return None
    normalized = [item.strip() for item in value]
    if len(set(normalized)) != len(normalized):
        issues.append(
            AcceptanceIssue(
                "delegated_handoff_ownership_duplicate",
                f"Session handoff `{field}` must not repeat an acceptance id.",
            )
        )
    return normalized


def _json_packet_acceptance_rows(
    packet: Mapping[str, Any],
) -> tuple[list[AcceptanceRow], list[AcceptanceIssue]]:
    raw_rows = packet.get("acceptance")
    if raw_rows is None:
        return [], []
    if not isinstance(raw_rows, list):
        return [], [
            AcceptanceIssue(
                "worker_packet_acceptance_invalid",
                "JSON worker packet `acceptance` must be an array of definition objects.",
            )
        ]
    rows: list[AcceptanceRow] = []
    issues: list[AcceptanceIssue] = []
    for index, raw in enumerate(raw_rows):
        if not isinstance(raw, dict):
            issues.append(
                AcceptanceIssue(
                    "worker_packet_acceptance_invalid",
                    f"JSON worker packet acceptance[{index}] must be an object.",
                )
            )
            continue
        acceptance_id = raw.get("id")
        criterion = raw.get("criterion")
        if (
            not isinstance(acceptance_id, str)
            or STABLE_ACCEPTANCE_ID_RE.fullmatch(acceptance_id.strip()) is None
            or not isinstance(criterion, str)
            or not criterion.strip()
        ):
            issues.append(
                AcceptanceIssue(
                    "worker_packet_acceptance_invalid",
                    f"JSON worker packet acceptance[{index}] requires a canonical id and non-empty criterion.",
                )
            )
            continue
        rows.append(
            AcceptanceRow(
                id=acceptance_id.strip(),
                criterion=criterion.strip(),
                checked=None,
                line=index + 1,
            )
        )
    return rows, issues


def _packet_json(
    packet_text: str,
    *,
    label: str,
) -> tuple[dict[str, Any] | None, list[AcceptanceIssue]]:
    try:
        return _decode_json_object(packet_text, label=label), []
    except (json.JSONDecodeError, ValueError) as exc:
        return None, [
            AcceptanceIssue(
                "worker_packet_invalid_json",
                f"JSON worker packet is invalid: {exc}",
            )
        ]


def _packet_acceptance_issues(
    packet_text: str,
    packet_path: Path,
    plan_rows: Sequence[AcceptanceRow],
) -> list[AcceptanceIssue]:
    if packet_path.suffix.lower() == ".json":
        packet, issues = _packet_json(packet_text, label="JSON worker packet")
        if packet is None:
            return issues
        packet_rows, parse_issues = _json_packet_acceptance_rows(packet)
        issues.extend(parse_issues)
    else:
        packet_rows, parse_issues = parse_markdown_acceptance_rows(
            packet_text,
            require_checkbox=False,
        )
        issues = [
            AcceptanceIssue(
                "worker_packet_acceptance_invalid",
                f"Worker packet acceptance row is invalid: {issue.message}",
                issue.line,
            )
            for issue in parse_issues
        ]
    expected = {row.id: row.criterion for row in plan_rows}
    observed: dict[str, str] = {}
    duplicates: set[str] = set()
    for row in packet_rows:
        if row.id in observed:
            duplicates.add(row.id)
        observed[row.id] = row.criterion
    if duplicates:
        issues.append(
            AcceptanceIssue(
                "worker_packet_acceptance_duplicate",
                "Worker packet repeats acceptance ids: "
                + ", ".join(sorted(duplicates)),
            )
        )
    missing = sorted(set(expected) - set(observed))
    extra = sorted(set(observed) - set(expected))
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unexpected " + ", ".join(extra))
        issues.append(
            AcceptanceIssue(
                "worker_packet_acceptance_mismatch",
                "Worker packet acceptance ids do not match the plan: " + "; ".join(details),
            )
        )
    drifted = sorted(
        acceptance_id
        for acceptance_id in set(expected) & set(observed)
        if expected[acceptance_id] != observed[acceptance_id]
    )
    if drifted:
        issues.append(
            AcceptanceIssue(
                "worker_packet_acceptance_text_mismatch",
                "Worker packet criterion text differs from the plan for: "
                + ", ".join(drifted),
            )
        )
    return issues


def _packet_state_capsule(
    packet_text: str,
    packet_path: Path,
) -> tuple[dict[str, Any] | None, list[AcceptanceIssue]]:
    if packet_path.suffix.lower() == ".json":
        packet, issues = _packet_json(packet_text, label="JSON worker packet")
        if packet is None:
            return None, issues
        capsule = packet.get("elves_handoff")
        if not isinstance(capsule, dict):
            return None, [
                AcceptanceIssue(
                    "worker_packet_state_capsule_missing",
                    "JSON worker packet must contain a top-level `elves_handoff` object.",
                )
            ]
        return capsule, []

    if not packet_text.startswith(_MARKDOWN_HANDOFF_PREFIX):
        code = (
            "worker_packet_state_capsule_position_invalid"
            if "elves-handoff-v1" in packet_text
            else "worker_packet_state_capsule_missing"
        )
        return None, [
            AcceptanceIssue(
                code,
                "Markdown worker packet must begin exactly with an `elves-handoff-v1` JSON comment capsule.",
            )
        ]
    end = packet_text.find(
        _MARKDOWN_HANDOFF_SUFFIX,
        len(_MARKDOWN_HANDOFF_PREFIX),
    )
    if end < 0:
        return None, [
            AcceptanceIssue(
                "worker_packet_state_capsule_invalid",
                "Markdown worker packet handoff capsule is missing its closing `-->` marker.",
            )
        ]
    body = packet_text[len(_MARKDOWN_HANDOFF_PREFIX) : end]
    try:
        return _decode_json_object(body, label="worker packet state capsule"), []
    except (json.JSONDecodeError, ValueError) as exc:
        return None, [
            AcceptanceIssue(
                "worker_packet_state_capsule_invalid",
                f"Worker packet state capsule is invalid JSON: {exc}",
            )
        ]


def _git_head(repo_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            text=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    head = result.stdout.strip().lower()
    return head if result.returncode == 0 and EXACT_COMMIT_RE.fullmatch(head) else None


def _git_branch(repo_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
            cwd=repo_root,
            text=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    branch = result.stdout.strip()
    return branch if result.returncode == 0 and branch else None


def _git_commit_is_ancestor(repo_root: Path, commit: str, head: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", commit, head],
            cwd=repo_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _explicit_handoff_issues(
    session: dict[str, Any],
    *,
    repo_root: Path,
    plan_rows: Sequence[AcceptanceRow],
    plan_batch_ids: tuple[int, ...],
) -> list[AcceptanceIssue]:
    """Strictly validate handoff v1 only after a session explicitly declares it."""

    if "handoff" not in session:
        return []
    issues: list[AcceptanceIssue] = []
    if not plan_rows:
        return [
            AcceptanceIssue(
                "delegated_handoff_requires_stable_ids",
                "Explicit handoff v1 requires stable B#-A#/M-A# plan rows so every pending item has one owner.",
            )
        ]

    packet_raw = session.get("worker_packet_path")
    packet_path: Path | None = None
    packet_text: str | None = None
    if not isinstance(packet_raw, str) or not packet_raw.strip():
        issues.append(
            AcceptanceIssue(
                "worker_packet_missing",
                "Explicit handoff v1 requires a non-empty `worker_packet_path`; staging is blocked until the packet exists.",
            )
        )
    else:
        try:
            packet_path = _inside_repo(Path(packet_raw), repo_root, label="worker packet")
            packet_text = _read_worker_packet(packet_path)
        except ValueError as exc:
            issues.append(AcceptanceIssue("worker_packet_invalid", str(exc)))

    handoff = session.get("handoff")
    if not isinstance(handoff, dict):
        issues.append(
            AcceptanceIssue(
                "delegated_handoff_invalid",
                "Declared session `handoff` must be one machine-readable object.",
            )
        )
        return issues
    unexpected_handoff_fields = sorted(set(handoff) - _HANDOFF_FIELDS)
    if unexpected_handoff_fields:
        issues.append(
            AcceptanceIssue(
                "delegated_handoff_fields_invalid",
                "Session handoff contains unsupported fields: "
                + ", ".join(unexpected_handoff_fields),
            )
        )
    if handoff.get("schema_version") != 1:
        issues.append(
            AcceptanceIssue(
                "delegated_handoff_version_invalid",
                "Session handoff `schema_version` must be 1.",
            )
        )

    recorded_branch = session.get("branch")
    current_branch = _git_branch(repo_root)
    if (
        not isinstance(recorded_branch, str)
        or not recorded_branch.strip()
        or current_branch is None
        or recorded_branch.strip() != current_branch
    ):
        issues.append(
            AcceptanceIssue(
                "delegated_handoff_branch_mismatch",
                "Session `branch` must equal the repository's current symbolic branch for explicit handoff v1.",
            )
        )

    mode = handoff.get("mode")
    if mode not in _HANDOFF_MODES:
        issues.append(
            AcceptanceIssue(
                "delegated_handoff_mode_invalid",
                "Session handoff `mode` must be `fresh_start` or `resume_active_batch`.",
            )
        )
    started = handoff.get("product_implementation_started")
    if not isinstance(started, bool):
        issues.append(
            AcceptanceIssue(
                "delegated_handoff_started_invalid",
                "Session handoff `product_implementation_started` must be a boolean.",
            )
        )
    elif mode == "fresh_start" and started:
        issues.append(
            AcceptanceIssue(
                "delegated_handoff_started_mismatch",
                "`fresh_start` requires `product_implementation_started: false`.",
            )
        )
    elif mode == "resume_active_batch" and not started:
        issues.append(
            AcceptanceIssue(
                "delegated_handoff_started_mismatch",
                "`resume_active_batch` requires `product_implementation_started: true`.",
            )
        )

    active_batch = handoff.get("active_batch")
    active_number = normalize_batch_id(active_batch)
    if (
        not isinstance(active_batch, str)
        or active_number is None
        or active_batch != f"B{active_number}"
        or active_number not in plan_batch_ids
    ):
        issues.append(
            AcceptanceIssue(
                "delegated_handoff_active_batch_invalid",
                "Session handoff `active_batch` must be one canonical pending plan batch such as `B1`.",
            )
        )

    completed_slices = handoff.get("coordinator_completed_slices")
    if not isinstance(completed_slices, list):
        issues.append(
            AcceptanceIssue(
                "delegated_handoff_slices_invalid",
                "Session handoff `coordinator_completed_slices` must be an array.",
            )
        )
        completed_slices = []
    slice_fields_valid = True
    current_head = _git_head(repo_root)
    for index, item in enumerate(completed_slices):
        if not isinstance(item, dict):
            slice_fields_valid = False
            issues.append(
                AcceptanceIssue(
                    "delegated_handoff_slice_invalid",
                    f"Session handoff coordinator_completed_slices[{index}] must be an object.",
                )
            )
            continue
        unexpected_slice_fields = sorted(set(item) - _HANDOFF_SLICE_FIELDS)
        if unexpected_slice_fields:
            slice_fields_valid = False
            issues.append(
                AcceptanceIssue(
                    "delegated_handoff_slice_invalid",
                    f"Session handoff coordinator_completed_slices[{index}] contains unsupported fields: "
                    + ", ".join(unexpected_slice_fields),
                )
            )
        for field in ("description", "evidence", "commit"):
            value = item.get(field)
            valid = isinstance(value, str) and bool(value.strip())
            if field == "commit":
                valid = valid and EXACT_COMMIT_RE.fullmatch(value.strip()) is not None
            if not valid:
                slice_fields_valid = False
                issues.append(
                    AcceptanceIssue(
                        "delegated_handoff_slice_invalid",
                        f"Session handoff coordinator_completed_slices[{index}].{field} is invalid.",
                    )
                )
        commit = item.get("commit")
        if (
            isinstance(commit, str)
            and EXACT_COMMIT_RE.fullmatch(commit.strip()) is not None
            and (
                current_head is None
                or not _git_commit_is_ancestor(
                    repo_root,
                    commit.strip().lower(),
                    current_head,
                )
            )
        ):
            slice_fields_valid = False
            issues.append(
                AcceptanceIssue(
                    "delegated_handoff_slice_commit_unproven",
                    f"Session handoff coordinator_completed_slices[{index}].commit must be an ancestor of current HEAD.",
                )
            )
    if mode == "fresh_start" and completed_slices:
        issues.append(
            AcceptanceIssue(
                "delegated_handoff_slices_mismatch",
                "`fresh_start` requires an empty `coordinator_completed_slices` array.",
            )
        )
    elif mode == "resume_active_batch" and not completed_slices and slice_fields_valid:
        issues.append(
            AcceptanceIssue(
                "delegated_handoff_slices_mismatch",
                "`resume_active_batch` requires at least one evidenced coordinator-completed slice.",
            )
        )

    next_action = handoff.get("next_exact_action")
    if not isinstance(next_action, str) or not next_action.strip():
        issues.append(
            AcceptanceIssue(
                "delegated_handoff_next_action_missing",
                "Session handoff `next_exact_action` must name one concrete worker action.",
            )
        )

    worker_ids = _handoff_id_list(
        handoff,
        "worker_owned_acceptance_ids",
        issues=issues,
    )
    coordinator_ids = _handoff_id_list(
        handoff,
        "coordinator_owned_acceptance_ids",
        issues=issues,
    )
    acceptance_state = _session_acceptance_state(session)
    expected_ids = {row.id for row in plan_rows}
    completed_ids = {
        acceptance_id
        for acceptance_id, met in acceptance_state.items()
        if met and acceptance_id in expected_ids
    }
    pending_ids = expected_ids - completed_ids
    if worker_ids is not None and coordinator_ids is not None:
        worker_set = set(worker_ids)
        coordinator_set = set(coordinator_ids)
        overlap = sorted(worker_set & coordinator_set)
        if overlap:
            issues.append(
                AcceptanceIssue(
                    "delegated_handoff_ownership_overlap",
                    "Acceptance ids have both worker and coordinator ownership: "
                    + ", ".join(overlap),
                )
            )
        assigned = worker_set | coordinator_set
        missing = sorted(pending_ids - assigned)
        unexpected = sorted(assigned - pending_ids)
        if missing or unexpected:
            details: list[str] = []
            if missing:
                details.append("unowned pending ids " + ", ".join(missing))
            if unexpected:
                details.append("completed or unknown ids assigned " + ", ".join(unexpected))
            issues.append(
                AcceptanceIssue(
                    "delegated_handoff_ownership_mismatch",
                    "Delegated handoff ownership must cover every pending acceptance id exactly once: "
                    + "; ".join(details),
                )
            )
        if not worker_set:
            issues.append(
                AcceptanceIssue(
                    "delegated_handoff_worker_scope_empty",
                    "A delegated run must assign at least one pending acceptance id to the worker.",
                )
            )
        if (
            isinstance(active_batch, str)
            and not any(item.startswith(f"{active_batch}-A") for item in worker_set)
        ):
            issues.append(
                AcceptanceIssue(
                    "delegated_handoff_active_batch_unowned",
                    "The worker must own at least one pending acceptance id in `active_batch`.",
                )
            )

    if packet_text is None or packet_path is None:
        return issues
    packet_state, capsule_issues = _packet_state_capsule(packet_text, packet_path)
    issues.extend(capsule_issues)
    if packet_state is not None:
        unexpected_capsule_fields = sorted(set(packet_state) - _PACKET_CAPSULE_FIELDS)
        if unexpected_capsule_fields:
            issues.append(
                AcceptanceIssue(
                    "worker_packet_state_capsule_invalid",
                    "Worker packet state capsule contains unsupported fields: "
                    + ", ".join(unexpected_capsule_fields),
                )
            )
        if packet_state.get("schema_version") != 1:
            issues.append(
                AcceptanceIssue(
                    "worker_packet_state_capsule_invalid",
                    "Worker packet state capsule `schema_version` must be 1.",
                )
            )
        if packet_state.get("run_id") != session.get("run_id"):
            issues.append(
                AcceptanceIssue(
                    "worker_packet_run_mismatch",
                    "Worker packet state capsule `run_id` must match the session.",
                )
            )
        if packet_state.get("branch") != session.get("branch"):
            issues.append(
                AcceptanceIssue(
                    "worker_packet_branch_mismatch",
                    "Worker packet state capsule `branch` must match the session.",
                )
            )
        if packet_state.get("handoff") != handoff:
            issues.append(
                AcceptanceIssue(
                    "worker_packet_handoff_mismatch",
                    "Worker packet state capsule `handoff` must exactly match the session handoff object.",
                )
            )
        launch_head = packet_state.get("launch_head")
        if (
            not isinstance(launch_head, str)
            or EXACT_COMMIT_RE.fullmatch(launch_head) is None
            or current_head is None
            or launch_head.lower() != current_head
        ):
            issues.append(
                AcceptanceIssue(
                    "worker_packet_launch_head_mismatch",
                    "Worker packet `launch_head` must equal the repository's exact current HEAD at validation time.",
                )
            )
    issues.extend(_packet_acceptance_issues(packet_text, packet_path, plan_rows))
    return issues


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


def _reconcile_session_identity(
    session: dict[str, Any],
    *,
    derive_start_head: bool,
) -> tuple[dict[str, Any], list[AcceptanceIssue]]:
    """Validate staging identity and migrate an exact legacy tripwire safely.

    ``start_head`` is the canonical machine-readable collision tripwire.  A
    session that already records the older ``collision_tripwire`` field may
    copy it during explicit synchronization, but only when it is an exact
    commit-shaped value.  The landing check still proves repository existence
    and ancestry at the committed evidence tip.
    """

    updated = dict(session)
    issues: list[AcceptanceIssue] = []

    run_id = updated.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        issues.append(
            AcceptanceIssue(
                "session_run_id_missing",
                "Session staging requires a non-empty `run_id` before worker launch.",
            )
        )

    start_head = updated.get("start_head")
    collision_tripwire = updated.get("collision_tripwire")
    tripwire_is_exact = (
        isinstance(collision_tripwire, str)
        and EXACT_COMMIT_RE.fullmatch(collision_tripwire) is not None
    )
    if collision_tripwire is not None and not tripwire_is_exact:
        issues.append(
            AcceptanceIssue(
                "session_collision_tripwire_invalid",
                "Session `collision_tripwire`, when present, must be an exact "
                "40-character commit. New sessions should record that value as `start_head`.",
            )
        )

    if start_head is None and derive_start_head and tripwire_is_exact:
        start_head = collision_tripwire
        updated["start_head"] = collision_tripwire

    if not isinstance(start_head, str) or EXACT_COMMIT_RE.fullmatch(start_head) is None:
        if start_head is None and tripwire_is_exact and not derive_start_head:
            message = (
                "Session must record the exact 40-character collision tripwire as `start_head`. "
                "Run `acceptance_contract.py sync-session --write` to copy the existing "
                "`collision_tripwire` safely."
            )
            code = "session_start_head_missing"
        else:
            message = (
                "Session staging requires `start_head` to be an exact 40-character "
                "commit before worker launch."
            )
            code = "session_start_head_invalid"
        issues.append(AcceptanceIssue(code, message))
    elif tripwire_is_exact and start_head.lower() != collision_tripwire.lower():
        issues.append(
            AcceptanceIssue(
                "session_collision_tripwire_mismatch",
                "Session `start_head` and legacy `collision_tripwire` must identify "
                "the same exact commit.",
            )
        )

    return updated, issues


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

    if session is not None:
        session, identity_issues = _reconcile_session_identity(
            session,
            derive_start_head=args.action == "sync-session",
        )
        issues.extend(identity_issues)
        if args.action == "validate":
            issues.extend(
                _explicit_handoff_issues(
                    session,
                    repo_root=repo_root,
                    plan_rows=contract.rows,
                    plan_batch_ids=contract.batch_ids,
                )
            )

    if args.action == "validate" or issues:
        warnings = (
            _worker_packet_warnings(session)
            if args.action == "validate" and session is not None
            else []
        )
        return _emit(
            ok=not issues,
            action=args.action,
            plan_path=plan_path,
            session_path=session_path,
            issues=issues,
            as_json=bool(args.json),
            warnings=warnings,
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
