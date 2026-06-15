#!/usr/bin/env python3
"""Summarize open GitHub PR health for Elves portfolio sweeps.

Usage:
  python3 scripts/pr_portfolio_report.py
  python3 scripts/pr_portfolio_report.py --prs 29-43
  python3 scripts/pr_portfolio_report.py --prs 29-43 --fail-on-attention
  python3 scripts/pr_portfolio_report.py --prs 29-43 --fail-on-attention --fail-on-draft
  python3 scripts/pr_portfolio_report.py --json

The helper is read-only. It uses `gh pr view`, `gh pr list`, and the GitHub GraphQL API to report
draft state, branch, merge state, pending/failing checks, and unresolved review-thread counts.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass


OK_CHECK_CONCLUSIONS = {"SUCCESS", "NEUTRAL", "SKIPPED"}
OK_STATUS_STATES = {"SUCCESS"}
OK_MERGE_STATES = {"CLEAN", "HAS_HOOKS"}


@dataclass
class PortfolioRow:
    number: int
    draft: bool
    branch: str
    merge_state: str
    review_decision: str
    unresolved_threads: int
    pending_checks: list[str]
    bad_checks: list[str]
    url: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only GitHub PR portfolio health summary.",
    )
    parser.add_argument(
        "--prs",
        help="Comma-separated PR numbers and ranges, for example `29-43,50`. Defaults to open PRs.",
    )
    parser.add_argument(
        "--repo",
        help="GitHub repository as owner/name. Defaults to `gh repo view` for the current checkout.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of a table.",
    )
    parser.add_argument(
        "--fail-on-attention",
        action="store_true",
        help=(
            "Exit 1 if any PR has unresolved threads, pending checks, failing checks, "
            "requested changes, or a non-clean merge state."
        ),
    )
    parser.add_argument(
        "--fail-on-draft",
        action="store_true",
        help=(
            "Exit 1 if any selected PR is a draft. Compose with --fail-on-attention "
            "for a landing-readiness gate."
        ),
    )
    return parser.parse_args()


def parse_pr_selection(value: str) -> list[int]:
    numbers: list[int] = []
    seen: set[int] = set()
    for raw_part in value.split(","):
        part = raw_part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start = int(start_text.strip())
            end = int(end_text.strip())
            if end < start:
                raise ValueError(f"invalid descending PR range: {part}")
            candidates = range(start, end + 1)
        else:
            candidates = [int(part)]
        for number in candidates:
            if number not in seen:
                seen.add(number)
                numbers.append(number)
    return numbers


def gh_json(args: list[str]) -> object:
    try:
        raw = subprocess.check_output(["gh", *args], text=True, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        message = f"`gh {' '.join(args)}` failed"
        if detail:
            message = f"{message}: {detail}"
        raise RuntimeError(message) from exc
    except FileNotFoundError as exc:
        raise RuntimeError("GitHub CLI `gh` is not installed or is not on PATH") from exc
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"`gh {' '.join(args)}` returned invalid JSON") from exc


def current_repo() -> str:
    data = gh_json(["repo", "view", "--json", "nameWithOwner"])
    if not isinstance(data, dict) or "nameWithOwner" not in data:
        raise RuntimeError("Unable to determine current GitHub repository with `gh repo view`")
    return str(data["nameWithOwner"])


def open_pr_numbers(repo: str) -> list[int]:
    data = gh_json(
        ["pr", "list", "--repo", repo, "--state", "open", "--limit", "1000", "--json", "number"]
    )
    if not isinstance(data, list):
        raise RuntimeError("Unexpected `gh pr list` response")
    return sorted(int(item["number"]) for item in data)


def split_repo(repo: str) -> tuple[str, str]:
    if "/" not in repo:
        raise ValueError(f"repository must be owner/name, got: {repo}")
    owner, name = repo.split("/", 1)
    if not owner or not name:
        raise ValueError(f"repository must be owner/name, got: {repo}")
    return owner, name


def classify_checks(checks: list[dict]) -> tuple[list[str], list[str]]:
    pending: list[str] = []
    bad: list[str] = []
    for check in checks:
        typename = check.get("__typename")
        if typename == "StatusContext" or "state" in check:
            name = str(check.get("context") or check.get("name") or "<unnamed>")
            state = str(check.get("state") or "")
            if state == "PENDING":
                pending.append(name)
            elif state not in OK_STATUS_STATES:
                bad.append(f"{name}:{state or 'UNKNOWN'}")
            continue

        name = str(check.get("name") or check.get("context") or "<unnamed>")
        status = str(check.get("status") or "")
        conclusion = str(check.get("conclusion") or "")
        if status != "COMPLETED":
            pending.append(name)
        elif conclusion not in OK_CHECK_CONCLUSIONS:
            bad.append(f"{name}:{conclusion or 'UNKNOWN'}")
    return pending, bad


def unresolved_thread_count(repo: str, pr_number: int) -> int:
    owner, name = split_repo(repo)
    query = """
query($owner:String!, $repo:String!, $number:Int!, $after:String) {
  repository(owner:$owner, name:$repo) {
    pullRequest(number:$number) {
      reviewThreads(first:100, after:$after) {
        pageInfo { hasNextPage endCursor }
        nodes { isResolved }
      }
    }
  }
}
"""
    unresolved = 0
    cursor: str | None = None
    while True:
        gh_args = [
            "api",
            "graphql",
            "-f",
            f"query={query}",
            "-F",
            f"owner={owner}",
            "-F",
            f"repo={name}",
            "-F",
            f"number={pr_number}",
        ]
        if cursor:
            gh_args.extend(["-F", f"after={cursor}"])
        data = gh_json(gh_args)
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected GraphQL response for #{pr_number}")
        try:
            review_threads = data["data"]["repository"]["pullRequest"]["reviewThreads"]
            nodes = review_threads["nodes"]
            page_info = review_threads["pageInfo"]
        except (KeyError, TypeError) as exc:
            raise RuntimeError(f"Unexpected GraphQL response shape for #{pr_number}") from exc
        unresolved += sum(1 for node in nodes if not node.get("isResolved"))
        if not page_info.get("hasNextPage"):
            return unresolved
        cursor = page_info.get("endCursor")
        if not cursor:
            raise RuntimeError(f"Missing GraphQL pagination cursor for #{pr_number}")


def summarize_pr(repo: str, pr_number: int) -> PortfolioRow:
    view = gh_json(
        [
            "pr",
            "view",
            str(pr_number),
            "--repo",
            repo,
            "--json",
            "number,url,isDraft,mergeStateStatus,reviewDecision,statusCheckRollup,headRefName",
        ]
    )
    if not isinstance(view, dict):
        raise RuntimeError(f"Unexpected `gh pr view` response for #{pr_number}")
    pending, bad = classify_checks(list(view.get("statusCheckRollup") or []))
    return PortfolioRow(
        number=int(view["number"]),
        draft=bool(view["isDraft"]),
        branch=str(view["headRefName"]),
        merge_state=str(view.get("mergeStateStatus") or ""),
        review_decision=str(view.get("reviewDecision") or ""),
        unresolved_threads=unresolved_thread_count(repo, pr_number),
        pending_checks=pending,
        bad_checks=bad,
        url=str(view.get("url") or ""),
    )


def row_needs_attention(row: PortfolioRow) -> bool:
    return bool(
        row.unresolved_threads
        or row.pending_checks
        or row.bad_checks
        or row.review_decision == "CHANGES_REQUESTED"
        or (row.merge_state and row.merge_state not in OK_MERGE_STATES)
    )


def format_list(values: list[str]) -> str:
    return ",".join(values) if values else "-"


def format_table(rows: list[PortfolioRow]) -> str:
    header = "PR  Draft  Merge     Review             Unres  Pending  Bad  Branch"
    lines = [header, "-" * len(header)]
    for row in rows:
        lines.append(
            f"#{row.number:<3} "
            f"{str(row.draft):<6} "
            f"{row.merge_state or '-':<9} "
            f"{row.review_decision or '-':<18} "
            f"{row.unresolved_threads:<6} "
            f"{format_list(row.pending_checks):<8} "
            f"{format_list(row.bad_checks):<4} "
            f"{row.branch}"
        )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    try:
        repo = args.repo or current_repo()
        split_repo(repo)
        numbers = parse_pr_selection(args.prs) if args.prs else open_pr_numbers(repo)
        rows = [summarize_pr(repo, number) for number in numbers]
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps([asdict(row) for row in rows], indent=2, sort_keys=True))
    elif rows:
        print(format_table(rows))
    else:
        print("No open pull requests found.")

    if args.fail_on_attention and any(row_needs_attention(row) for row in rows):
        return 1
    if args.fail_on_draft and any(row.draft for row in rows):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
