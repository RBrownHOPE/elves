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
It verifies that self-certified batch completion is backed by one-to-one
acceptance evidence in session JSON and the authoritative plan. Execution-log
and evidence-directory checks remain optional additional surfaces.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
import re
import subprocess
import sys
import unicodedata
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
    r"^###?\s+Batch\s+\[?(\d+)\]?\s*[:.\-–—]?\s*(.*)$",
    re.IGNORECASE | re.MULTILINE,
)
ACCEPTANCE_SECTION = re.compile(
    r"(?ims)^[ ]{0,3}\*\*Acceptance criteria:\*\*(.*?)(?=\n[ ]{0,3}\*\*[A-Z]|\n### |\n## |\Z)"
)
CHECKBOX = re.compile(r"^[ ]{0,3}[\-\*]\s+\[([ xX])\]\s+(.+)$", re.MULTILINE)
STABLE_ACCEPTANCE_CHECKBOX = re.compile(
    r"^[ ]{0,3}[-*]\s+\[([ xX])\]\s+((?:B\d+-A\d+|M-A\d+))\s*[—–:-]\s*(.+?)\s*$",
    re.MULTILINE,
)
MASTER_ACCEPTANCE_HEADING = re.compile(
    r"(?im)^(#{1,6})\s+Master\s+Acceptance\s*$"
)
MARKDOWN_HEADING = re.compile(r"(?m)^(#{1,6})\s+.+$")
STABLE_BATCH_ACCEPTANCE_ID = re.compile(r"^B(\d+)-A\d+$")
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
        help="Plan markdown path (required; defaults to session plan_path when present).",
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
    parser.add_argument(
        "--repo-root",
        default=None,
        help=(
            "Repository root for strict landing provenance. When set, the session "
            "and plan must be ordinary, tracked files in this worktree and session "
            "branch/run identity is verified against Git."
        ),
    )
    return parser.parse_args(argv)


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"Session file could not be read: {path}: {exc}") from exc
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
    for index, item in enumerate(batches):
        if not isinstance(item, dict):
            raise SystemExit(f"Session `batches[{index}]` must be an object")
        out.append(item)
    return out


def batch_id(batch: dict[str, Any]) -> str:
    raw = batch.get("id", batch.get("name", "?"))
    return str(raw)


def numeric_batch_id(batch: dict[str, Any]) -> int | None:
    """Resolve canonical ``B#`` or legacy positive-integer batch identities."""
    raw = batch.get("id")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw > 0 else None
    if not isinstance(raw, str):
        return None
    match = re.fullmatch(r"(?:B)?([1-9][0-9]*)", raw)
    if match is None:
        return None
    return int(match.group(1))


def acceptance_items(batch: dict[str, Any]) -> list[dict[str, Any]]:
    raw = batch.get("acceptance")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise SystemExit(f"Batch {batch_id(batch)} field `acceptance` must be an array")
    out: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise SystemExit(
                f"Batch {batch_id(batch)} `acceptance[{index}]` must be an object"
            )
        out.append(item)
    return out


def master_acceptance_items(session: dict[str, Any]) -> list[dict[str, Any]]:
    """Return canonical branch-level Master Acceptance evidence rows."""
    raw = session.get("master_acceptance")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise SystemExit("Session field `master_acceptance` must be an array")
    out: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise SystemExit(
                f"Session `master_acceptance[{index}]` must be an object"
            )
        out.append(item)
    return out


def check_session_batches(session: dict[str, Any], report: Report) -> None:
    batches = as_batches(session)
    if not batches:
        report.error(
            "no_batches",
            "Session has no `batches` array. Landing requires per-batch status and acceptance evidence.",
        )
        return

    seen_batch_ids: set[int] = set()
    for batch in batches:
        bid = batch_id(batch)
        numeric_id = numeric_batch_id(batch)
        if numeric_id is None:
            report.error(
                "batch_id_invalid",
                f"Batch {bid!r} must have a canonical `B#` or legacy positive-integer "
                "`id` matching a plan Batch heading.",
            )
        elif numeric_id in seen_batch_ids:
            report.error("batch_id_duplicate", f"Duplicate session batch id: {numeric_id}")
        else:
            seen_batch_ids.add(numeric_id)
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
            criterion = str(item.get("criterion") or "").strip()
            evidence = str(item.get("evidence") or "").strip()
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
        criteria_text = " ".join(str(i.get("criterion") or "") for i in items)
        evidence_text = " ".join(str(i.get("evidence") or "") for i in items)
        blob = f"{criteria_text} {evidence_text}"
        if LOCK_ONLY_PATTERNS.search(blob) and not CHARACTERIZATION_ALLOW_PATTERNS.search(blob):
            if SPLIT_ACCEPTANCE_PATTERNS.search(blob) or any(
                SPLIT_ACCEPTANCE_PATTERNS.search(str(i.get("criterion") or "")) for i in items
            ):
                # Has both lock language and split language — OK if met with real evidence.
                pass
            else:
                # All acceptance looks lock/structure-only with no split metric.
                only_lock = all(
                    LOCK_ONLY_PATTERNS.search(str(i.get("criterion") or ""))
                    or LOCK_ONLY_PATTERNS.search(str(i.get("evidence") or ""))
                    or not SPLIT_ACCEPTANCE_PATTERNS.search(str(i.get("criterion") or ""))
                    for i in items
                )
                if only_lock and not any(
                    SPLIT_ACCEPTANCE_PATTERNS.search(str(i.get("criterion") or "")) for i in items
                ):
                    # Soft when criteria are ordinary (not god-file related).
                    # Hard when every criterion is lock-only style.
                    if all(
                        LOCK_ONLY_PATTERNS.search(str(i.get("criterion") or ""))
                        or LOCK_ONLY_PATTERNS.search(str(i.get("evidence") or ""))
                        for i in items
                    ):
                        report.error(
                            "god_file_lock_only",
                            f"Batch {bid}: structure/regex lock evidence alone must not complete "
                            "a split/god-file batch unless plan Acceptance explicitly allows "
                            "characterization-only. Add LOC/facade/size evidence or a hard-stop note.",
                        )


def check_session_master_acceptance(session: dict[str, Any], report: Report) -> None:
    """Validate canonical top-level Master Acceptance evidence values."""
    for index, item in enumerate(master_acceptance_items(session)):
        criterion = str(item.get("criterion") or "").strip()
        evidence = str(item.get("evidence") or "").strip()
        met = item.get("met")
        label = criterion or f"item[{index}]"
        if met is not True:
            report.error(
                "master_acceptance_not_met",
                f"Master acceptance {label!r}: met must be true (got {met!r}).",
            )
        if not criterion:
            report.error(
                "master_acceptance_no_criterion",
                f"Master acceptance item {index} is missing `criterion`.",
            )
        if not evidence:
            report.error(
                "master_acceptance_no_evidence",
                f"Master acceptance {label!r} is missing `evidence` "
                "(path, command transcript, metric, or commit SHA).",
            )


def normalize_criterion(value: Any) -> str:
    """Normalize legacy criterion text without erasing semantic punctuation."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("—", "-").replace("–", "-")
    return re.sub(r"\s+", " ", text).strip().casefold()


def _blank_markdown_span(value: str) -> str:
    return "".join("\n" if char == "\n" else " " for char in value)


def active_markdown(plan_text: str) -> str:
    """Blank fenced code and HTML comments while preserving source offsets."""
    without_comments = re.sub(
        r"<!--.*?(?:-->|\Z)",
        lambda match: _blank_markdown_span(match.group(0)),
        plan_text,
        flags=re.DOTALL,
    )
    rendered: list[str] = []
    fence_char: str | None = None
    fence_length = 0
    for line in without_comments.splitlines(keepends=True):
        match = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if fence_char is None:
            if match is None:
                rendered.append(line)
                continue
            marker = match.group(1)
            fence_char = marker[0]
            fence_length = len(marker)
            rendered.append(_blank_markdown_span(line))
            continue
        rendered.append(_blank_markdown_span(line))
        if match is not None:
            marker = match.group(1)
            if marker[0] == fence_char and len(marker) >= fence_length:
                fence_char = None
                fence_length = 0
    return "".join(rendered)


def _parse_checkboxes(section: str) -> list[dict[str, Any]]:
    return [
        {
            "checked": box.group(1).lower() == "x",
            "text": box.group(2).strip(),
        }
        for box in CHECKBOX.finditer(section)
    ]


def _parse_stable_checkboxes(section: str) -> list[dict[str, Any]]:
    """Parse stable-ID checkboxes, including wrapped criterion continuation lines."""
    starts = list(STABLE_ACCEPTANCE_CHECKBOX.finditer(section))
    items: list[dict[str, Any]] = []
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(section)
        continuation: list[str] = []
        for raw_line in section[match.end() : end].splitlines():
            stripped = raw_line.strip()
            if not stripped:
                continue
            if stripped.startswith(("#", "**")) or re.match(
                r"^[-*]\s+\[[ xX]\]", stripped
            ):
                break
            if raw_line[:1].isspace():
                continuation.append(stripped)
                continue
            break
        criterion = " ".join([match.group(3).strip(), *continuation]).strip()
        items.append(
            {
                "checked": match.group(1).lower() == "x",
                "id": match.group(2),
                "criterion": criterion,
            }
        )
    return items


def parse_master_acceptance(plan_text: str) -> tuple[bool, list[dict[str, Any]]]:
    """Parse global Master Acceptance checkboxes outside per-batch sections."""
    found = False
    acceptance: list[dict[str, Any]] = []
    for match in MASTER_ACCEPTANCE_HEADING.finditer(plan_text):
        found = True
        level = len(match.group(1))
        end = len(plan_text)
        for heading in MARKDOWN_HEADING.finditer(plan_text, match.end()):
            if len(heading.group(1)) <= level:
                end = heading.start()
                break
        acceptance.extend(_parse_checkboxes(plan_text[match.end() : end]))
    return found, acceptance


def parse_master_stable_acceptance(plan_text: str) -> tuple[bool, list[dict[str, Any]]]:
    """Parse stable-ID rows only from explicit Master Acceptance sections."""
    found = False
    acceptance: list[dict[str, Any]] = []
    for match in MASTER_ACCEPTANCE_HEADING.finditer(plan_text):
        found = True
        level = len(match.group(1))
        end = len(plan_text)
        for heading in MARKDOWN_HEADING.finditer(plan_text, match.end()):
            if len(heading.group(1)) <= level:
                end = heading.start()
                break
        acceptance.extend(_parse_stable_checkboxes(plan_text[match.end() : end]))
    return found, acceptance


def parse_plan_batches(plan_text: str) -> dict[int, dict[str, Any]]:
    """Return parsed Batch headings and their explicit Acceptance sections."""
    matches = list(BATCH_HEADING.finditer(plan_text))
    result: dict[int, dict[str, Any]] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(plan_text)
        body = plan_text[start:end]
        bid = int(match.group(1))
        title = match.group(2).strip()
        section_match = ACCEPTANCE_SECTION.search(body)
        acceptance_section = section_match.group(1) if section_match else ""
        acceptance = _parse_checkboxes(acceptance_section) if section_match else []
        result[bid] = {
            "title": title,
            "acceptance": acceptance,
            "stable_acceptance": _parse_stable_checkboxes(acceptance_section),
            "acceptance_section": acceptance_section,
            "has_acceptance_section": section_match is not None,
            "body": body,
        }
    return result


def check_legacy_acceptance_mapping(
    plan_batches: dict[int, dict[str, Any]],
    master_acceptance: list[dict[str, Any]],
    session_master_acceptance: list[dict[str, Any]],
    session: dict[str, Any],
    report: Report,
) -> None:
    """Require one evidence row per normalized legacy plan criterion."""
    session_by_id = {
        numeric_batch_id(batch): batch
        for batch in as_batches(session)
        if numeric_batch_id(batch) is not None
    }
    remaining: Counter[str] = Counter()
    labels: dict[str, str] = {}

    for bid, info in sorted(plan_batches.items()):
        expected = Counter(normalize_criterion(item["text"]) for item in info["acceptance"])
        for item in info["acceptance"]:
            labels.setdefault(normalize_criterion(item["text"]), item["text"])
        duplicates = [labels[key] for key, count in expected.items() if count > 1]
        for criterion in duplicates:
            report.error(
                "plan_acceptance_duplicate_criterion",
                f"Plan Batch {bid} repeats legacy Acceptance criterion {criterion!r}.",
            )

        batch = session_by_id.get(bid)
        observed = Counter()
        if batch is not None:
            observed.update(
                normalize_criterion(item.get("criterion"))
                for item in acceptance_items(batch)
            )
        for key, count in expected.items():
            matched = min(count, observed[key])
            if matched < count:
                report.error(
                    "acceptance_criterion_missing",
                    f"Plan Batch {bid} criterion {labels[key]!r} has no one-to-one "
                    "session evidence row.",
                )
            observed[key] -= matched
            if observed[key] <= 0:
                observed.pop(key, None)
        remaining.update(observed)

    for item in session_master_acceptance:
        key = normalize_criterion(item.get("criterion"))
        labels.setdefault(key, str(item.get("criterion") or ""))
        remaining[key] += 1

    master_expected = Counter(
        normalize_criterion(item["text"]) for item in master_acceptance
    )
    for item in master_acceptance:
        labels.setdefault(normalize_criterion(item["text"]), item["text"])
    for key, count in master_expected.items():
        if count > 1:
            report.error(
                "master_acceptance_duplicate_criterion",
                f"Master Acceptance repeats criterion {labels[key]!r}.",
            )
        matched = min(count, remaining[key])
        if matched < count:
            report.error(
                "master_acceptance_evidence_missing",
                f"Master Acceptance criterion {labels[key]!r} has no one-to-one "
                "session evidence row.",
            )
        remaining[key] -= matched
        if remaining[key] <= 0:
            remaining.pop(key, None)

    for key, count in sorted(remaining.items()):
        report.error(
            "acceptance_evidence_unrelated",
            f"Session contains {count} unrelated legacy evidence row(s) for "
            f"{labels.get(key, key)!r} that do not map to plan Acceptance.",
        )


def check_plan(plan_path: Path, session: dict[str, Any], report: Report) -> None:
    if not plan_path.exists():
        report.error("plan_missing", f"Plan file not found: {plan_path}")
        return
    try:
        text = plan_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        report.error("plan_unparseable", f"Plan file could not be parsed: {plan_path}: {exc}")
        return
    text = active_markdown(text)
    plan_batches = parse_plan_batches(text)
    if not plan_batches:
        report.error(
            "plan_no_batch_headings",
            f"Could not parse Batch headings from {plan_path}. "
            "Expected `### Batch N: Name` with Acceptance criteria checkboxes.",
        )
        return

    heading_ids = [int(match.group(1)) for match in BATCH_HEADING.finditer(text)]
    if len(heading_ids) != len(set(heading_ids)):
        report.error(
            "plan_duplicate_batch_heading",
            f"Plan contains duplicate Batch heading ids: {heading_ids}",
        )

    master_present, master_acceptance = parse_master_acceptance(text)
    if master_present and not master_acceptance:
        report.error(
            "master_acceptance_unparseable",
            "Master Acceptance heading has no parseable checkboxes.",
        )
    for item in master_acceptance:
        if not item["checked"]:
            report.error(
                "plan_acceptance_open",
                f"Master Acceptance is unchecked: {item['text'][:120]}",
            )

    session_batches = {
        numeric: batch
        for batch in as_batches(session)
        if (numeric := numeric_batch_id(batch)) is not None
    }
    plan_batch_ids = set(plan_batches)
    session_batch_ids = set(session_batches)
    for bid in sorted(session_batch_ids - plan_batch_ids):
        report.error(
            "session_batch_missing_in_plan",
            f"Session Batch {bid} has no matching Batch heading in the authoritative plan.",
        )
    for bid, info in sorted(plan_batches.items()):
        if not info["has_acceptance_section"]:
            report.error(
                "plan_acceptance_section_missing",
                f"Plan Batch {bid} has no explicit `**Acceptance criteria:**` section.",
            )
        elif not info["acceptance"]:
            report.error(
                "plan_acceptance_unparseable",
                f"Plan Batch {bid} has no parseable Acceptance criteria checkboxes.",
            )
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

        sb = session_batches.get(bid)
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

    # Stable acceptance ID one-to-one mapping (B#-A# / M-A#). Parse only
    # explicit batch Acceptance and Master Acceptance sections so task lists or
    # prose cannot be mistaken for landing evidence.
    session_master = master_acceptance_items(session)
    all_evidence_items: list[
        tuple[dict[str, Any] | None, dict[str, Any]]
    ] = [
        (batch, item)
        for batch in as_batches(session)
        for item in acceptance_items(batch)
    ]
    all_evidence_items.extend((None, item) for item in session_master)
    global_id_mentions = re.findall(r"\b(?:B\d+-A\d+|M-A\d+)\b", text)
    evidence_has_ids = any(str(item.get("id") or "").strip() for _, item in all_evidence_items)
    stable_mode = bool(global_id_mentions) or evidence_has_ids
    if not stable_mode:
        check_legacy_acceptance_mapping(
            plan_batches,
            master_acceptance,
            session_master,
            session,
            report,
        )
        return

    _, master_stable = parse_master_stable_acceptance(text)
    plan_items: list[dict[str, Any]] = []
    for bid, info in sorted(plan_batches.items()):
        parsed = info["stable_acceptance"]
        if len(parsed) != len(info["acceptance"]):
            report.error(
                "plan_acceptance_id_missing",
                f"Stable-ID mode requires every Acceptance checkbox in Batch {bid} "
                "to have a B#-A# id and separator.",
            )
        section_mentions = re.findall(
            r"\b(?:B\d+-A\d+|M-A\d+)\b", info["acceptance_section"]
        )
        if section_mentions and len(parsed) != len(section_mentions):
            report.error(
                "plan_acceptance_unparseable",
                f"Plan Batch {bid} mentions stable Acceptance IDs that are not all "
                "parseable checked-list rows with an ID separator.",
            )
        for item in parsed:
            aid = item["id"]
            match = STABLE_BATCH_ACCEPTANCE_ID.fullmatch(aid)
            if match is None:
                report.error(
                    "plan_acceptance_wrong_scope",
                    f"Master acceptance id {aid} must appear under `## Master Acceptance`, "
                    f"not Batch {bid}.",
                )
            elif int(match.group(1)) != bid:
                report.error(
                    "plan_acceptance_wrong_batch",
                    f"Plan acceptance {aid} appears under Batch {bid}; it belongs under "
                    f"Batch {match.group(1)}.",
                )
            plan_items.append({**item, "batch_id": bid})

    if not master_present:
        report.error(
            "master_acceptance_missing",
            "Stable-ID plans require an explicit `## Master Acceptance` section.",
        )
    elif not master_stable:
        report.error(
            "master_acceptance_unparseable",
            "Stable-ID plans require at least one parseable M-A# row under Master Acceptance.",
        )
    if len(master_stable) != len(master_acceptance):
        report.error(
            "master_acceptance_id_missing",
            "Stable-ID mode requires every Master Acceptance checkbox to have an M-A# "
            "id and separator.",
        )
    for item in master_stable:
        aid = item["id"]
        if not aid.startswith("M-A"):
            report.error(
                "plan_acceptance_wrong_scope",
                f"Batch acceptance id {aid} must appear in its Batch Acceptance section, "
                "not Master Acceptance.",
            )
        plan_items.append({**item, "batch_id": None})

    if global_id_mentions and not plan_items:
        report.error(
            "plan_acceptance_unparseable",
            "Plan mentions stable Acceptance IDs but none were parsed from explicit "
            "Batch or Master Acceptance sections.",
        )

    plan_by_id: dict[str, dict[str, Any]] = {}
    for item in plan_items:
        aid = item["id"]
        if not item["checked"]:
            report.error(
                "plan_acceptance_open",
                f"Plan stable Acceptance {aid} is unchecked: {item['criterion']}",
            )
        if aid in plan_by_id:
            report.error(
                "plan_acceptance_duplicate_id",
                f"Duplicate plan acceptance id {aid}.",
            )
        else:
            plan_by_id[aid] = item

    canonical_master_present = "master_acceptance" in session
    legacy_master_rows = [
        (batch, item)
        for batch, item in all_evidence_items
        if batch is not None and str(item.get("id") or "").strip().startswith("M-A")
    ]
    if legacy_master_rows and not canonical_master_present:
        report.warn(
            "legacy_master_acceptance_location",
            "Session stores M-A# evidence inside a batch `acceptance` array. "
            "This remains readable for compatibility; new sessions should use the "
            "top-level `master_acceptance` array.",
        )

    evidence_by_id: dict[
        str, tuple[dict[str, Any] | None, dict[str, Any]]
    ] = {}
    for batch, item in all_evidence_items:
        aid = str(item.get("id") or "").strip()
        if not aid:
            scope = (
                "top-level master_acceptance"
                if batch is None
                else f"Batch {batch_id(batch)}"
            )
            report.error(
                "acceptance_id_missing",
                f"Stable-ID plan requires an id on every evidence row in {scope}.",
            )
            continue
        if not re.fullmatch(r"(?:B\d+-A\d+|M-A\d+)", aid):
            report.error(
                "acceptance_id_invalid",
                f"Session evidence id {aid!r} is not a B#-A# or M-A# stable id.",
            )
        batch_match = STABLE_BATCH_ACCEPTANCE_ID.fullmatch(aid)
        if batch_match is not None:
            if batch is None:
                report.error(
                    "acceptance_id_wrong_scope",
                    f"Batch evidence {aid} must be stored in Batch "
                    f"{batch_match.group(1)} `acceptance`, not top-level "
                    "`master_acceptance`.",
                )
            else:
                expected_batch = numeric_batch_id(batch)
                if expected_batch is None or int(batch_match.group(1)) != expected_batch:
                    report.error(
                        "acceptance_id_wrong_batch",
                        f"Evidence {aid} is stored in session Batch {batch_id(batch)}; "
                        f"B{batch_match.group(1)} evidence must stay in Batch "
                        f"{batch_match.group(1)}.",
                    )
        elif aid.startswith("M-A") and batch is not None and canonical_master_present:
            report.error(
                "acceptance_id_wrong_scope",
                f"Master evidence {aid} must be stored in top-level "
                "`master_acceptance`, not Batch {batch_id(batch)} `acceptance`.",
            )
        if aid in evidence_by_id:
            report.error(
                "acceptance_evidence_duplicate_id",
                f"Session contains duplicate evidence rows for {aid}.",
            )
        else:
            evidence_by_id[aid] = (batch, item)

    for aid, plan_item in sorted(plan_by_id.items()):
        observed = evidence_by_id.get(aid)
        if observed is None:
            report.error(
                "acceptance_evidence_missing",
                f"Plan acceptance {aid} has no one-to-one session evidence row.",
            )
            continue
        _, evidence_item = observed
        if normalize_criterion(evidence_item.get("criterion")) != normalize_criterion(
            plan_item["criterion"]
        ):
            report.error(
                "acceptance_criterion_mismatch",
                f"Session evidence criterion for {aid} does not exactly match the "
                "authoritative plan criterion.",
            )

    for aid in sorted(set(evidence_by_id) - set(plan_by_id)):
        report.error(
            "acceptance_evidence_unrelated",
            f"Session evidence {aid} does not map to an authoritative plan Acceptance row.",
        )


def check_execution_log(
    log_path: Path,
    report: Report,
    *,
    expected_batch_ids: set[int] | None = None,
) -> None:
    if not log_path.exists():
        report.warn("execution_log_missing", f"Execution log not found: {log_path}")
        return
    text = log_path.read_text(encoding="utf-8")

    if MULTI_BATCH_CLOSE.search(text):
        # Multi-batch closes require explicit, labeled Validate sections. Batch
        # headings alone are navigation, not validation evidence.
        validate_hits = VALIDATE_SECTION.findall(text)
        # findall with one group returns list of group contents (batch ids or '')
        batch_ids_with_validate = {int(v) for v in validate_hits if v}
        expected = set(expected_batch_ids or set())
        missing = sorted(expected - batch_ids_with_validate) if expected else []
        if len(batch_ids_with_validate) < 2 or missing:
            suffix = f" Missing labeled batches: {missing}." if missing else ""
            report.error(
                "multi_batch_close",
                "Execution log mentions multi-batch close / close remaining without "
                "separate `**Validate for batch N:**` sections per completed batch."
                + suffix,
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


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        text=True,
        capture_output=True,
        check=False,
    )


def _strict_repo_file(
    raw: str | Path,
    *,
    base: Path,
    repo_root: Path,
    label: str,
    report: Report,
) -> Path | None:
    """Resolve an ordinary tracked file without allowing symlink indirection."""
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    lexical = Path(os.path.abspath(candidate))
    try:
        resolved = lexical.resolve(strict=True)
        relative = resolved.relative_to(repo_root)
    except (OSError, ValueError):
        report.error(
            f"{label}_outside_repo",
            f"{label.capitalize()} path must stay inside the repository: {lexical}",
        )
        return None

    cursor = lexical
    while cursor != cursor.parent:
        if cursor.is_symlink():
            report.error(
                f"{label}_symlink",
                f"{label.capitalize()} path must not use a symlink: {cursor}",
            )
            return None
        try:
            at_repo_root = cursor.resolve(strict=False) == repo_root
        except OSError:
            at_repo_root = False
        if at_repo_root:
            break
        cursor = cursor.parent
    if not lexical.is_file():
        report.error(
            f"{label}_not_regular",
            f"{label.capitalize()} must be an existing regular file: {lexical}",
        )
        return None
    rel_text = relative.as_posix()
    tracked = _git(repo_root, "ls-files", "--error-unmatch", "--", rel_text)
    if tracked.returncode != 0:
        report.error(
            f"{label}_untracked",
            f"{label.capitalize()} must be tracked by Git: {rel_text}",
        )
        return None
    committed = _git(repo_root, "cat-file", "-e", f"HEAD:{rel_text}")
    if committed.returncode != 0:
        report.error(
            f"{label}_not_committed",
            f"{label.capitalize()} must exist in the current HEAD tree: {rel_text}",
        )
        return None
    return resolved


def _check_session_git_identity(
    session: dict[str, Any], repo_root: Path, report: Report
) -> None:
    run_id = session.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        report.error(
            "session_run_id_missing",
            "Final landing session must record a non-empty `run_id`.",
        )

    branch = session.get("branch")
    current = _git(repo_root, "branch", "--show-current")
    current_branch = current.stdout.strip() if current.returncode == 0 else ""
    if not isinstance(branch, str) or not branch.strip():
        report.error(
            "session_branch_missing",
            "Final landing session must record the active `branch`.",
        )
    elif not current_branch or branch != current_branch:
        report.error(
            "session_branch_mismatch",
            f"Session branch {branch!r} does not match active branch {current_branch!r}.",
        )

    start_head = session.get("start_head")
    if not isinstance(start_head, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", start_head):
        report.error(
            "session_start_head_invalid",
            "Final landing session must record an exact 40-character `start_head` commit.",
        )
        return
    resolved = _git(repo_root, "rev-parse", "--verify", f"{start_head}^{{commit}}")
    if resolved.returncode != 0 or resolved.stdout.strip().lower() != start_head.lower():
        report.error(
            "session_start_head_missing",
            f"Session start_head is not an exact commit in this repository: {start_head}",
        )
        return
    ancestor = _git(repo_root, "merge-base", "--is-ancestor", start_head, "HEAD")
    if ancestor.returncode != 0:
        report.error(
            "session_start_head_not_ancestor",
            f"Session start_head {start_head} is not an ancestor of current HEAD.",
        )


def run_checks(args: argparse.Namespace) -> Report:
    report = Report()
    repo_root = Path(args.repo_root).expanduser().resolve() if args.repo_root else None
    if repo_root is not None:
        if _git(repo_root, "rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
            report.error(
                "repo_root_invalid",
                f"Strict landing root is not a Git worktree: {repo_root}",
            )
            return report
        session_path = _strict_repo_file(
            args.session,
            base=repo_root,
            repo_root=repo_root,
            label="session",
            report=report,
        )
        if session_path is None:
            return report
    else:
        session_path = Path(args.session).expanduser().resolve()
    session = load_json(session_path)
    base = session_path.parent

    if repo_root is not None:
        _check_session_git_identity(session, repo_root, report)

    check_session_batches(session, report)
    check_session_master_acceptance(session, report)

    recorded_plan_raw = session.get("plan_path")
    if repo_root is not None and not recorded_plan_raw:
        report.error(
            "session_plan_path_missing",
            "Final landing session must record its authoritative `plan_path`.",
        )
        plan_path = None
    elif repo_root is not None:
        recorded_plan = _strict_repo_file(
            str(recorded_plan_raw),
            base=base,
            repo_root=repo_root,
            label="plan",
            report=report,
        )
        explicit_plan = None
        if args.plan is not None:
            explicit_plan = _strict_repo_file(
                args.plan,
                base=repo_root,
                repo_root=repo_root,
                label="plan",
                report=report,
            )
            if (
                recorded_plan is not None
                and explicit_plan is not None
                and recorded_plan != explicit_plan
            ):
                report.error(
                    "plan_path_mismatch",
                    f"Explicit --plan {explicit_plan} does not exactly match session "
                    f"plan_path {recorded_plan}.",
                )
        plan_path = explicit_plan if args.plan is not None else recorded_plan
    else:
        plan_raw = args.plan or recorded_plan_raw
        plan_path = resolve_path(str(plan_raw) if plan_raw else None, base)
    if plan_path is not None:
        check_plan(plan_path, session, report)
    else:
        report.error(
            "no_plan_path",
            "No plan path provided and session has no plan_path; landing requires the "
            "authoritative plan Acceptance walk.",
        )

    log_raw = args.execution_log or session.get("execution_log_path")
    log_path = resolve_path(str(log_raw) if log_raw else None, base)
    if log_path is not None:
        complete_batch_ids = {
            numeric
            for batch in as_batches(session)
            if str(batch.get("status", "")).strip().lower() == "complete"
            if (numeric := numeric_batch_id(batch)) is not None
        }
        check_execution_log(
            log_path,
            report,
            expected_batch_ids=complete_batch_ids,
        )

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
