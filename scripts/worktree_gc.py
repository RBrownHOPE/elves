#!/usr/bin/env python3
"""Report or reclaim fully merged, fully pushed Elves git worktrees.

This is the reclaim side of the worktree lifecycle and deliberately a separate
helper from the create helper (preflight_worktree.py), which does not reuse,
delete, or repair existing worktrees. Report mode is the default and strictly
read-only; removal happens only with an explicit --apply and only through
git's own guarded commands: `git worktree remove` (never --force),
`git branch -d` (never -D), and `git worktree prune` only after at least one
successful removal. Unregistered sibling directories are operator-owned: they
are listed in the report and never deleted.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


DEFAULT_BASE_REF = "origin/main"


class WorktreeError(Exception):
    """Expected user-facing helper failure."""


def run_git(
    args: list[str],
    cwd: Path | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise WorktreeError(detail or f"git {' '.join(args)} failed")
    return result


def repo_root() -> Path:
    result = run_git(["rev-parse", "--show-toplevel"])
    if result.returncode != 0:
        raise WorktreeError("Not inside a git repository.")
    return Path(result.stdout.strip()).resolve()


def resolve_base(root: Path, base_ref: str, explicit: bool) -> str:
    result = run_git(["rev-parse", "--verify", f"{base_ref}^{{commit}}"], cwd=root)
    if result.returncode != 0:
        if explicit:
            raise WorktreeError(f"Base ref {base_ref!r} does not resolve to a commit.")
        raise WorktreeError("Default base origin/main does not resolve; pass --base <ref>.")
    return result.stdout.strip()


@dataclass
class Worktree:
    path: Path
    head: str = ""
    branch: str = ""
    is_main: bool = False
    is_bare: bool = False
    is_detached: bool = False
    locked: bool = False
    prunable: bool = False


def parse_worktrees(root: Path) -> list[Worktree]:
    result = run_git(["worktree", "list", "--porcelain"], cwd=root, check=True)
    worktrees: list[Worktree] = []
    current: Worktree | None = None
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            current = Worktree(path=Path(line[len("worktree ") :]).resolve())
            current.is_main = not worktrees
            worktrees.append(current)
        elif current is None:
            continue
        elif line.startswith("HEAD "):
            current.head = line[len("HEAD ") :]
        elif line.startswith("branch refs/heads/"):
            current.branch = line[len("branch refs/heads/") :]
        elif line == "bare":
            current.is_bare = True
        elif line == "detached":
            current.is_detached = True
        elif line == "locked" or line.startswith("locked "):
            current.locked = True
        elif line == "prunable" or line.startswith("prunable "):
            current.prunable = True
    return worktrees


def worktree_is_clean(worktree: Worktree) -> bool:
    result = run_git(
        ["--no-optional-locks", "status", "--porcelain"],
        cwd=worktree.path,
        check=True,
    )
    return not result.stdout.strip()


def is_ancestor(root: Path, commit: str, base_commit: str) -> bool:
    result = run_git(["merge-base", "--is-ancestor", commit, base_commit], cwd=root)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    detail = (result.stderr or result.stdout).strip()
    raise WorktreeError(detail or "git merge-base --is-ancestor failed")


def upstream_ref(root: Path, branch: str) -> str | None:
    result = run_git(
        ["rev-parse", "--abbrev-ref", "--verify", "--quiet", f"{branch}@{{upstream}}"],
        cwd=root,
    )
    if result.returncode != 0:
        return None
    upstream = result.stdout.strip()
    return upstream or None


def commits_ahead(root: Path, upstream: str, head: str) -> int:
    result = run_git(["rev-list", "--count", f"{upstream}..{head}"], cwd=root, check=True)
    return int(result.stdout.strip() or "0")


def refusal_reasons(
    worktree: Worktree,
    invoking_dir: Path,
    root: Path,
    base_label: str,
    base_commit: str,
) -> list[str]:
    """Every clause of the candidate predicate, as human-readable refusals.

    An empty return value means the worktree is a removal candidate: a
    registered linked worktree that is not the main worktree, not the invoking
    directory, clean (tracked and untracked), fully merged into the base ref,
    and carrying zero commits ahead of its upstream.
    """
    if worktree.prunable:
        return [
            "prunable registration (directory missing or moved) - "
            "inspect with: git worktree prune --dry-run"
        ]

    vetoes: list[str] = []
    if worktree.is_main:
        vetoes.append("main worktree (never a candidate)")
    if worktree.is_bare:
        vetoes.append("bare repository entry")
    if worktree.path == invoking_dir or worktree.path in invoking_dir.parents:
        vetoes.append("invoking directory (never a candidate)")
    if worktree.locked:
        vetoes.append("locked")
    if worktree.is_detached or not worktree.branch:
        vetoes.append("detached HEAD (no branch)")
    if vetoes:
        return vetoes

    reasons: list[str] = []
    if not worktree.path.exists():
        return ["directory missing (registration is stale)"]
    if not worktree_is_clean(worktree):
        reasons.append("dirty (uncommitted or untracked changes)")
    if not is_ancestor(root, worktree.head, base_commit):
        reasons.append(f"not merged into {base_label}")
    upstream = upstream_ref(root, worktree.branch)
    if upstream is not None:
        ahead = commits_ahead(root, upstream, worktree.head)
        if ahead:
            reasons.append(f"{ahead} commit(s) ahead of upstream {upstream} (unpushed)")
    return reasons


def unregistered_siblings(main_worktree_path: Path, registered: set[Path]) -> list[Path]:
    parent = main_worktree_path.parent
    prefix = f"{main_worktree_path.name}-"
    siblings: list[Path] = []
    try:
        entries = sorted(parent.iterdir())
    except OSError:
        return []
    for entry in entries:
        if not entry.name.startswith(prefix):
            continue
        try:
            if not entry.is_dir():
                continue
            resolved = entry.resolve()
        except OSError:
            continue
        if resolved in registered:
            continue
        siblings.append(resolved)
    return siblings


def select_worktrees(
    worktrees: list[Worktree], requested_paths: list[str], invoking_dir: Path
) -> list[Worktree]:
    if not requested_paths:
        return worktrees
    by_path = {worktree.path: worktree for worktree in worktrees}
    selected: dict[Path, Worktree] = {}
    for raw in requested_paths:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = invoking_dir / candidate
        resolved = candidate.resolve()
        worktree = by_path.get(resolved)
        if worktree is None:
            raise WorktreeError(f"--path is not a registered worktree of this repository: {resolved}")
        selected[resolved] = worktree
    return list(selected.values())


def print_report(
    root: Path,
    main_worktree_path: Path,
    invoking_dir: Path,
    base_label: str,
    base_commit: str,
    apply_mode: bool,
    candidates: list[Worktree],
    kept: list[tuple[Worktree, list[str]]],
    siblings: list[Path],
) -> None:
    print("Worktree gc report")
    print(f"repo root: {root}")
    print(f"main worktree: {main_worktree_path}")
    print(f"invoking directory: {invoking_dir}")
    print(f"base ref: {base_label} ({base_commit})")
    if apply_mode:
        print("mode: apply (removing candidates with git worktree remove + git branch -d)")
    else:
        print("mode: report (read-only; pass --apply to remove candidates)")
    print()
    print(f"candidates (registered, clean, merged into {base_label}, nothing unpushed):")
    for worktree in candidates:
        print(f"  {worktree.path}  branch: {worktree.branch}")
    if not candidates:
        print("  (none)")
    print()
    print("kept (refused):")
    for worktree, reasons in kept:
        branch = worktree.branch or "(detached)"
        print(f"  {worktree.path}  branch: {branch}  reason: {'; '.join(reasons)}")
    if not kept:
        print("  (none)")
    print()
    print("unregistered sibling directories (operator-owned; listed only, never deleted):")
    for sibling in siblings:
        print(f"  {sibling}")
    if not siblings:
        print("  (none)")


def apply_removals(root: Path, base_label: str, candidates: list[Worktree]) -> int:
    failures = 0
    removed_any = False
    for worktree in candidates:
        result = run_git(["worktree", "remove", str(worktree.path)], cwd=root)
        if result.returncode != 0:
            failures += 1
            detail = (result.stderr or result.stdout).strip()
            print(f"error: failed to remove worktree {worktree.path}: {detail}")
            continue
        removed_any = True
        delete = run_git(["branch", "-d", worktree.branch], cwd=root)
        if delete.returncode != 0:
            failures += 1
            detail = (delete.stderr or delete.stdout).strip()
            print(f"removed worktree {worktree.path}")
            print(
                f"error: failed to delete branch {worktree.branch}: {detail} "
                f"(the branch is merged into {base_label}; fast-forward your local "
                "default branch and retry - never use -D)"
            )
        else:
            print(f"removed worktree {worktree.path}; deleted branch {worktree.branch}")
    if removed_any:
        prune = run_git(["worktree", "prune"], cwd=root)
        if prune.returncode != 0:
            failures += 1
            detail = (prune.stderr or prune.stdout).strip()
            print(f"error: git worktree prune failed: {detail}")
        else:
            print("pruned stale worktree registrations (git worktree prune)")
    return failures


def handle_gc(args: argparse.Namespace) -> int:
    root = repo_root()
    invoking_dir = Path.cwd().resolve()
    base_ref = args.base or DEFAULT_BASE_REF
    base_commit = resolve_base(root, base_ref, explicit=args.base is not None)
    worktrees = parse_worktrees(root)
    if not worktrees:
        raise WorktreeError("git worktree list reported no worktrees.")
    main_worktree_path = worktrees[0].path
    registered = {worktree.path for worktree in worktrees}
    selected = select_worktrees(worktrees, args.path, invoking_dir)

    candidates: list[Worktree] = []
    kept: list[tuple[Worktree, list[str]]] = []
    for worktree in selected:
        reasons = refusal_reasons(worktree, invoking_dir, root, base_ref, base_commit)
        if reasons:
            kept.append((worktree, reasons))
        else:
            candidates.append(worktree)

    siblings = unregistered_siblings(main_worktree_path, registered)
    print_report(
        root,
        main_worktree_path,
        invoking_dir,
        base_ref,
        base_commit,
        args.apply,
        candidates,
        kept,
        siblings,
    )
    if not args.apply:
        return 0
    print()
    failures = apply_removals(root, base_ref, candidates)
    return 1 if failures else 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Report or remove fully merged, fully pushed Elves git worktrees. "
            "Report mode (the default) is read-only."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Remove candidate worktrees (default is a read-only report).",
    )
    parser.add_argument(
        "--base",
        help=f"Merge target ref for the ancestor check (default: {DEFAULT_BASE_REF}).",
    )
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        metavar="WORKTREE_DIR",
        help=(
            "Only consider this registered worktree (repeatable), e.g. the run's "
            "recorded worktree_path from .elves-session.json."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return handle_gc(args)
    except WorktreeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("error: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
