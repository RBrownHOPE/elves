#!/usr/bin/env python3
"""Pre-land / readiness check: green CI + status:complete is not enough.

Policy: landable means plan Acceptance with proof.

Usage:
  python3 scripts/elves_landing_check.py
  python3 scripts/elves_landing_check.py --session .elves-session.json
  python3 scripts/elves_landing_check.py --session path/to/.elves-session.json \\
      --plan docs/plans/my-plan.md --execution-log docs/elves/execution-log.md

Exit codes:
  0 — all checks pass (or advisory-only warnings when --advisory)
  1 — blocking failures
  2 — usage / IO error

This script is intentionally narrow. It does not run tests or inspect PR checks.
It only verifies that self-certified batch completion is backed by acceptance
evidence in session JSON (and optionally plan + execution-log surfaces).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_SESSION = ".elves-session.json"

# Structure/regex "lock" language that must not alone complete a split/god-file batch
# unless the plan Acceptance explicitly allows characterization-only completion.
LOCK_ONLY_PATTERNS = re.compile(
    r"(structure\s+already\s+exists|"
    r"characterization[\s-]?only|"
    r"regex\s+lock|"
    r"source[\s-]regex|"
    r"structure[\s-]only|"
    r"lock\s+behavior|"
    r"behavioral\s+lock)",
    re.IGNORECASE,
)

# Plan Acceptance criteria that look like real split/god-file outcomes.
SPLIT_ACCEPTANCE_PATTERNS = re.compile(
    r"(\bloc\b|"
    r"lines?\s+of\s+code|"
    r"facade|"
    r"extract|"
    r"split\s+(?:the\s+)?(?:file|module|god)|"
    r"under\s+\d+\s*(?:loc|lines)|"
    r"<=?\s*\d+\s*(?:loc|lines)|"
    r"max(?:imum)?\s+\d+\s*(?:loc|lines))",
    re.IGNORECASE,
)

CHARACTERIZATION_ALLOW_PATTERNS = re.compile(
    r"(characterization[\s-]?only|"
    r"structure[\s-]only\s+allowed|"
    r"lock[\s-]only\s+allowed|"
    r"explicitly\s+allows?\s+characterization)",
    re.IGNORECASE,
)

BATCH_HEADING = re.compile(
    r"^###?\s+Batch\s+(\d+)\s*[:.\-–—]?\s*(.*)$",
    re.IGNORECASE | re.MULTILINE,
)
ACCEPTANCE_SECTION = re.compile(
    r"(?is)\*\*Acceptance criteria:\*\*(.*?)(?=\n\*\*[A-Z]|\n### |\n## |\Z)"
)
CHECKBOX = re.compile(r"^[\-\*]\s+\[([ xX])\]\s+(.+)$", re.MULTILINE)
VALIDATE_SECTION = re.compile(
    r"(?im)^\*\*Validate(?:\s+section)?(?:\s+for\s+batch\s+(\d+))?:\*\*"
)
MULTI_BATCH_CLOSE = re.compile(
    r"(?i)(close\s+remaining|batches?\s+\d+\s*[-–—,/&]\s*\d+|multi[\s-]?batch\s+close)"
)
GATE_NAMES = ("typecheck", "lint", "test", "build")


@dataclass
class Finding:
    severity: str  # ERROR | WARN
    code: str
    message: str


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    def error(self, code: str, message: str) -> None:
        self.findings.append(Finding("ERROR", code, message))

    def warn(self, code: str, message: str) -> None:
        self.findings.append(Finding("WARN", code, message))

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "ERROR"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "WARN"]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Check that Elves batch completion is backed by plan Acceptance evidence. "
            "Green CI + status:complete alone is not landable."
        )
    )
    parser.add_argument(
        "--session",
        default=DEFAULT_SESSION,
        help=f"Path to .elves-session.json (default: {DEFAULT_SESSION})",
    )
    parser.add_argument(
        "--plan",
        default=None,
        help="Optional plan markdown path. Defaults to session plan_path when present.",
    )
    parser.add_argument(
        "--execution-log",
        default=None,
        help="Optional execution log path. Defaults to session execution_log_path when present.",
    )
    parser.add_argument(
        "--evidence-root",
        default=None,
        help=(
            "Optional SCRATCH/evidence root. When set, expects "
            "{root}/batch-N/{typecheck,lint,test,build} for each complete batch."
        ),
    )
    parser.add_argument(
        "--require-evidence-dirs",
        action="store_true",
        help="Treat missing gate evidence dirs as errors when --evidence-root is set.",
    )
    parser.add_argument(
        "--advisory",
        action="store_true",
        help="Print findings but always exit 0 when the session file is readable.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON report on stdout.",
    )
    return parser.parse_args(argv)


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"Session file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Session file is not valid JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"Session file must be a JSON object: {path}")
    return data


def as_batches(session: dict[str, Any]) -> list[dict[str, Any]]:
    batches = session.get("batches")
    if batches is None:
        return []
    if not isinstance(batches, list):
        raise SystemExit("Session field `batches` must be an array")
    out: list[dict[str, Any]] = []
    for item in batches:
        if isinstance(item, dict):
            out.append(item)
    return out


def batch_id(batch: dict[str, Any]) -> str:
    raw = batch.get("id", batch.get("name", "?"))
    return str(raw)


def acceptance_items(batch: dict[str, Any]) -> list[dict[str, Any]]:
    raw = batch.get("acceptance")
    if raw is None:
        return []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def check_session_batches(session: dict[str, Any], report: Report) -> None:
    batches = as_batches(session)
    if not batches:
        report.error(
            "no_batches",
            "Session has no `batches` array. Landing requires per-batch status and acceptance evidence.",
        )
        return

    for batch in batches:
        bid = batch_id(batch)
        status = str(batch.get("status", "")).strip().lower()
        if status != "complete":
            report.error(
                "batch_incomplete",
                f"Batch {bid} status is {status!r}, not 'complete'.",
            )
            continue

        items = acceptance_items(batch)
        if not items:
            report.error(
                "missing_acceptance",
                f"Batch {bid} is status=complete but `acceptance` is missing or empty. "
                "Record plan Acceptance criteria with evidence before marking complete.",
            )
            continue

        for index, item in enumerate(items):
            criterion = str(item.get("criterion", "")).strip()
            evidence = str(item.get("evidence", "")).strip()
            met = item.get("met")
            label = criterion or f"item[{index}]"

            if met is not True:
                report.error(
                    "acceptance_not_met",
                    f"Batch {bid} acceptance {label!r}: met must be true (got {met!r}).",
                )
            if not criterion:
                report.error(
                    "acceptance_no_criterion",
                    f"Batch {bid} acceptance item {index} is missing `criterion`.",
                )
            if not evidence:
                report.error(
                    "acceptance_no_evidence",
                    f"Batch {bid} acceptance {label!r} is missing `evidence` "
                    "(path, command transcript, metric, or commit SHA).",
                )

        # God-file / structure-lock rule: lock-only acceptance cannot alone complete
        # a split batch unless characterization-only is explicitly allowed.
        criteria_text = " ".join(str(i.get("criterion", "")) for i in items)
        evidence_text = " ".join(str(i.get("evidence", "")) for i in items)
        blob = f"{criteria_text} {evidence_text}"
        if LOCK_ONLY_PATTERNS.search(blob) and not CHARACTERIZATION_ALLOW_PATTERNS.search(blob):
            if SPLIT_ACCEPTANCE_PATTERNS.search(blob) or any(
                SPLIT_ACCEPTANCE_PATTERNS.search(str(i.get("criterion", ""))) for i in items
            ):
                # Has both lock language and split language — OK if met with real evidence.
                pass
            else:
                # All acceptance looks lock/structure-only with no split metric.
                only_lock = all(
                    LOCK_ONLY_PATTERNS.search(str(i.get("criterion", "")))
                    or LOCK_ONLY_PATTERNS.search(str(i.get("evidence", "")))
                    or not SPLIT_ACCEPTANCE_PATTERNS.search(str(i.get("criterion", "")))
                    for i in items
                )
                if only_lock and not any(
                    SPLIT_ACCEPTANCE_PATTERNS.search(str(i.get("criterion", ""))) for i in items
                ):
                    # Soft when criteria are ordinary (not god-file related).
                    # Hard when every criterion is lock-only style.
                    if all(
                        LOCK_ONLY_PATTERNS.search(str(i.get("criterion", "")))
                        or LOCK_ONLY_PATTERNS.search(str(i.get("evidence", "")))
                        for i in items
                    ):
                        report.error(
                            "god_file_lock_only",
                            f"Batch {bid}: structure/regex lock evidence alone must not complete "
                            "a split/god-file batch unless plan Acceptance explicitly allows "
                            "characterization-only. Add LOC/facade/size evidence or a hard-stop note.",
                        )


def parse_plan_batches(plan_text: str) -> dict[int, dict[str, Any]]:
    """Return {batch_id: {title, acceptance: [{text, checked}], body}}."""
    matches = list(BATCH_HEADING.finditer(plan_text))
    result: dict[int, dict[str, Any]] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(plan_text)
        body = plan_text[start:end]
        bid = int(match.group(1))
        title = match.group(2).strip()
        acceptance: list[dict[str, Any]] = []
        section_match = ACCEPTANCE_SECTION.search(body)
        section = section_match.group(1) if section_match else body
        for box in CHECKBOX.finditer(section if section_match else ""):
            acceptance.append(
                {
                    "checked": box.group(1).lower() == "x",
                    "text": box.group(2).strip(),
                }
            )
        # Fallback: any unchecked Acceptance-looking boxes near "Acceptance"
        if not acceptance and "acceptance" in body.lower():
            for box in CHECKBOX.finditer(body):
                acceptance.append(
                    {
                        "checked": box.group(1).lower() == "x",
                        "text": box.group(2).strip(),
                    }
                )
        result[bid] = {"title": title, "acceptance": acceptance, "body": body}
    return result


def check_plan(plan_path: Path, session: dict[str, Any], report: Report) -> None:
    if not plan_path.exists():
        report.error("plan_missing", f"Plan file not found: {plan_path}")
        return
    text = plan_path.read_text(encoding="utf-8")
    plan_batches = parse_plan_batches(text)
    if not plan_batches:
        report.warn(
            "plan_no_batch_headings",
            f"Could not parse Batch headings from {plan_path}. "
            "Expected `### Batch N: Name` with Acceptance criteria checkboxes.",
        )
        return

    session_batches = {str(b.get("id")): b for b in as_batches(session)}
    for bid, info in sorted(plan_batches.items()):
        open_boxes = [a for a in info["acceptance"] if not a["checked"]]
        if open_boxes:
            titles = "; ".join(a["text"][:80] for a in open_boxes[:5])
            report.error(
                "plan_acceptance_open",
                f"Plan Batch {bid} has unchecked Acceptance criteria: {titles}",
            )

        # God-file targets still open: if acceptance mentions LOC/facade and is checked
        # only via structure language in session, the session checker handles it.
        # Here: if plan still has open LOC-style boxes, already covered by open_boxes.

        sb = session_batches.get(str(bid))
        if sb is None:
            # Plan batch with no session entry is only an error if we expected full land.
            report.error(
                "plan_batch_missing_in_session",
                f"Plan Batch {bid} has no matching entry in session `batches`.",
            )
            continue

        # If plan acceptance requires split metrics, session evidence must not be lock-only only.
        plan_needs_split = any(
            SPLIT_ACCEPTANCE_PATTERNS.search(a["text"]) for a in info["acceptance"]
        )
        plan_allows_char = CHARACTERIZATION_ALLOW_PATTERNS.search(info["body"]) or any(
            CHARACTERIZATION_ALLOW_PATTERNS.search(a["text"]) for a in info["acceptance"]
        )
        if plan_needs_split and not plan_allows_char:
            items = acceptance_items(sb)
            if items and all(
                LOCK_ONLY_PATTERNS.search(str(i.get("criterion", "")))
                or LOCK_ONLY_PATTERNS.search(str(i.get("evidence", "")))
                for i in items
            ):
                report.error(
                    "god_file_plan_mismatch",
                    f"Plan Batch {bid} requires LOC/facade/split Acceptance, but session "
                    "acceptance evidence is structure/regex-lock only.",
                )


def check_execution_log(log_path: Path, report: Report) -> None:
    if not log_path.exists():
        report.warn("execution_log_missing", f"Execution log not found: {log_path}")
        return
    text = log_path.read_text(encoding="utf-8")

    if MULTI_BATCH_CLOSE.search(text):
        # Require either separate Validate sections per batch id, or distinct Batch headings.
        validate_hits = VALIDATE_SECTION.findall(text)
        # findall with one group returns list of group contents (batch ids or '')
        batch_ids_with_validate = {v for v in validate_hits if v}
        multi_mentions = MULTI_BATCH_CLOSE.findall(text)
        if multi_mentions and len(batch_ids_with_validate) < 2:
            # Also accept multiple ## Batch N headings in the log near the close.
            batch_headings = {int(m.group(1)) for m in BATCH_HEADING.finditer(text)}
            if len(batch_headings) < 2:
                report.error(
                    "multi_batch_close",
                    "Execution log mentions multi-batch close / close remaining without "
                    "separate Validate sections per batch id. Prefer one batch per close commit, "
                    "or record **Validate:** sections labeled per batch.",
                )
            else:
                report.warn(
                    "multi_batch_close_soft",
                    "Execution log mentions multi-batch close. Ensure each batch has its own "
                    "Validate evidence even if close commits were combined.",
                )


def check_evidence_dirs(
    root: Path,
    session: dict[str, Any],
    report: Report,
    *,
    required: bool,
) -> None:
    if not root.exists():
        msg = f"Evidence root does not exist: {root}"
        if required:
            report.error("evidence_root_missing", msg)
        else:
            report.warn("evidence_root_missing", msg)
        return

    for batch in as_batches(session):
        if str(batch.get("status", "")).strip().lower() != "complete":
            continue
        bid = batch_id(batch)
        batch_dir = root / f"batch-{bid}"
        if not batch_dir.is_dir():
            # also accept batch_N
            alt = root / f"batch_{bid}"
            batch_dir = alt if alt.is_dir() else batch_dir
        if not batch_dir.is_dir():
            msg = f"Missing evidence dir for batch {bid}: expected {root}/batch-{bid}/"
            if required:
                report.error("evidence_dir_missing", msg)
            else:
                report.warn("evidence_dir_missing", msg)
            continue
        for gate in GATE_NAMES:
            gate_path = batch_dir / gate
            # Accept file or directory (transcripts often live as gate.log or gate/stdout.txt)
            has = gate_path.exists() or (batch_dir / f"{gate}.log").exists() or (
                batch_dir / f"{gate}.txt"
            ).exists()
            if not has:
                msg = (
                    f"Batch {bid} evidence missing gate `{gate}` under {batch_dir} "
                    f"(expected `{gate}`, `{gate}.log`, or `{gate}.txt`)."
                )
                if required:
                    report.error("evidence_gate_missing", msg)
                else:
                    report.warn("evidence_gate_missing", msg)


def resolve_path(raw: str | None, base: Path) -> Path | None:
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (base / path).resolve()
    return path


def run_checks(args: argparse.Namespace) -> Report:
    session_path = Path(args.session).expanduser().resolve()
    session = load_json(session_path)
    base = session_path.parent
    report = Report()

    check_session_batches(session, report)

    plan_raw = args.plan or session.get("plan_path")
    plan_path = resolve_path(str(plan_raw) if plan_raw else None, base)
    if plan_path is not None:
        check_plan(plan_path, session, report)
    else:
        report.warn(
            "no_plan_path",
            "No plan path provided and session has no plan_path; skipped plan Acceptance walk.",
        )

    log_raw = args.execution_log or session.get("execution_log_path")
    log_path = resolve_path(str(log_raw) if log_raw else None, base)
    if log_path is not None:
        check_execution_log(log_path, report)

    if args.evidence_root:
        evidence_root = resolve_path(args.evidence_root, base)
        if evidence_root is not None:
            check_evidence_dirs(
                evidence_root,
                session,
                report,
                required=args.require_evidence_dirs,
            )

    # One-line policy reminder when anything failed
    if report.errors:
        report.warn(
            "policy",
            "Green CI + status:complete is not landable; landable is plan Acceptance with proof.",
        )

    return report


def print_human(report: Report, session_path: Path) -> None:
    errors = report.errors
    warnings = report.warnings
    if not errors and not warnings:
        print("Elves landing check OK")
        print(f"- Session: {session_path}")
        print("- Every complete batch has acceptance evidence with met:true")
        print("- Policy: plan Acceptance with proof (not green CI alone)")
        return

    status = "FAILED" if errors else "WARNINGS"
    print(f"Elves landing check {status}")
    print(f"- Session: {session_path}")
    for finding in report.findings:
        print(f"- {finding.severity} [{finding.code}]: {finding.message}")


def print_json(report: Report, session_path: Path) -> None:
    payload = {
        "session": str(session_path),
        "ok": not report.errors,
        "errors": [
            {"code": f.code, "message": f.message} for f in report.errors
        ],
        "warnings": [
            {"code": f.code, "message": f.message} for f in report.warnings
        ],
        "policy": (
            "Green CI + status:complete is not landable; "
            "landable is plan Acceptance with proof."
        ),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    session_path = Path(args.session).expanduser().resolve()
    try:
        report = run_checks(args)
    except SystemExit as exc:
        # load_json / validation usage exits
        message = str(exc) if exc.args else "usage error"
        if message.isdigit():
            return int(message)
        print(f"Elves landing check ERROR\n- {message}", file=sys.stderr)
        return 2

    if args.json:
        print_json(report, session_path)
    else:
        print_human(report, session_path)

    if args.advisory:
        return 0
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
