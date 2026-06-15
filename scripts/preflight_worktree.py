#!/usr/bin/env python3
"""Create or recommend a dedicated Elves git worktree.

The default preflight checklist is advisory. This helper mutates only when the
operator passes --create-worktree explicitly.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path


DEFAULT_BASE_REF = "origin/main"
MAX_SLUG_LENGTH = 60
MAX_PATH_CANDIDATES = 99
COMMON_BRANCH_PREFIXES = ("codex/", "claude/", "feat/", "feature/", "fix/", "chore/")


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


def ensure_clean(root: Path) -> None:
    result = run_git(["status", "--porcelain"], cwd=root, check=True)
    if result.stdout.strip():
        raise WorktreeError(
            "Current checkout has uncommitted changes; commit, stash, or use --dry-run."
        )


def ensure_origin(root: Path) -> None:
    result = run_git(["remote", "get-url", "origin"], cwd=root)
    if result.returncode != 0 or not result.stdout.strip():
        raise WorktreeError("No git remote 'origin' found; add origin before creating a worktree.")


def resolve_base(root: Path, base_ref: str, explicit: bool) -> str:
    result = run_git(["rev-parse", "--verify", f"{base_ref}^{{commit}}"], cwd=root)
    if result.returncode != 0:
        if explicit:
            raise WorktreeError(f"Base ref {base_ref!r} does not resolve to a commit.")
        raise WorktreeError("Default base origin/main does not resolve; pass --base <ref>.")
    return result.stdout.strip()


def local_branch_exists(root: Path, branch: str) -> bool:
    result = run_git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=root)
    return result.returncode == 0


def remote_branch_exists(root: Path, branch: str) -> bool:
    result = run_git(["ls-remote", "--exit-code", "--heads", "origin", branch], cwd=root)
    if result.returncode == 0:
        return True
    if result.returncode == 2:
        return False
    detail = (result.stderr or result.stdout).strip()
    raise WorktreeError(f"Could not check origin/{branch}: {detail or 'git ls-remote failed'}")


def remote_branches_matching(root: Path, pattern: str) -> set[str]:
    result = run_git(["ls-remote", "--heads", "origin", f"refs/heads/{pattern}"], cwd=root)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise WorktreeError(f"Could not check remote branches: {detail or 'git ls-remote failed'}")

    branches: set[str] = set()
    prefix = "refs/heads/"
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        ref = parts[1]
        if ref.startswith(prefix):
            branches.add(ref[len(prefix) :])
    return branches


def parse_worktrees(root: Path) -> tuple[set[Path], set[str]]:
    result = run_git(["worktree", "list", "--porcelain"], cwd=root, check=True)
    paths: set[Path] = set()
    branches: set[str] = set()
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            paths.add(Path(line[len("worktree ") :]).resolve())
        elif line.startswith("branch refs/heads/"):
            branches.add(line[len("branch refs/heads/") :])
    return paths, branches


def slug_for_branch(branch: str) -> str:
    source = branch
    for prefix in COMMON_BRANCH_PREFIXES:
        if source.startswith(prefix):
            source = source[len(prefix) :]
            break
    source = source.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", source).strip("-")
    if not slug:
        slug = "worktree"
    return slug[:MAX_SLUG_LENGTH].strip("-") or "worktree"


def generated_worktree_dir(root: Path, branch: str, occupied_paths: set[Path]) -> Path:
    base = root.parent / f"{root.name}-{slug_for_branch(branch)}"
    candidates = [
        base,
        *[
            root.parent / f"{base.name}-{index}"
            for index in range(2, MAX_PATH_CANDIDATES + 1)
        ],
    ]
    for candidate in candidates:
        resolved = candidate.resolve()
        if not resolved.exists() and resolved not in occupied_paths:
            return resolved
    raise WorktreeError(
        f"Could not find an unused generated worktree path after {MAX_PATH_CANDIDATES} candidates."
    )


def explicit_worktree_dir(path: str, root: Path, occupied_paths: set[Path]) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if resolved.exists():
        raise WorktreeError(f"Requested --worktree-dir already exists: {resolved}")
    if resolved in occupied_paths:
        raise WorktreeError(
            f"Requested --worktree-dir is already registered as a worktree: {resolved}"
        )
    return resolved


def validate_branch_name(branch: str) -> None:
    if not branch or branch.startswith("-"):
        raise WorktreeError("Branch name is required and must not start with '-'.")
    result = run_git(["check-ref-format", "--branch", branch])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise WorktreeError(f"Invalid branch name {branch!r}: {detail or 'git rejected it'}")


def unique_recommendation_branch(root: Path, current_branch: str) -> str:
    base_slug = slug_for_branch(f"{current_branch}-isolated")
    candidate = f"codex/{base_slug}"
    remote_matches = remote_branches_matching(root, f"{candidate}*")
    for index in range(1, MAX_PATH_CANDIDATES + 1):
        branch = candidate if index == 1 else f"{candidate}-{index}"
        if local_branch_exists(root, branch):
            continue
        if branch in remote_matches:
            continue
        return branch
    raise WorktreeError("Could not find an unused recommended branch name.")


def command_for(branch: str, worktree_dir: Path, base_ref: str) -> list[str]:
    return ["git", "worktree", "add", "-b", branch, str(worktree_dir), base_ref]


def print_plan(
    branch: str,
    worktree_dir: Path,
    base_ref: str,
    command: list[str],
    tripwire: str,
) -> None:
    print(f"branch: {branch}")
    print(f"worktree path: {worktree_dir}")
    print(f"base ref: {base_ref}")
    print(f"command: {shlex.join(command)}")
    print(f"collision tripwire: {tripwire}")


def choose_worktree_dir(root: Path, branch: str, requested_dir: str | None) -> Path:
    occupied_paths, occupied_branches = parse_worktrees(root)
    if branch in occupied_branches:
        raise WorktreeError(f"Target branch {branch!r} is already checked out in a worktree.")
    if requested_dir:
        return explicit_worktree_dir(requested_dir, root, occupied_paths)
    return generated_worktree_dir(root, branch, occupied_paths)


def handle_create(args: argparse.Namespace) -> int:
    root = repo_root()
    branch = args.create_worktree
    validate_branch_name(branch)
    if not args.dry_run:
        ensure_clean(root)
    ensure_origin(root)
    base_ref = args.base or DEFAULT_BASE_REF
    base_commit = resolve_base(root, base_ref, explicit=args.base is not None)
    if local_branch_exists(root, branch):
        raise WorktreeError(f"Target branch {branch!r} already exists locally.")
    if remote_branch_exists(root, branch):
        raise WorktreeError(f"Target branch {branch!r} already exists on origin.")
    worktree_dir = choose_worktree_dir(root, branch, args.worktree_dir)
    command = command_for(branch, worktree_dir, base_ref)

    if args.dry_run:
        print("Dry run: would create dedicated worktree")
        print_plan(branch, worktree_dir, base_ref, command, base_commit)
        return 0

    result = run_git(command[1:], cwd=root)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise WorktreeError(detail or "git worktree add failed")
    tripwire = run_git(
        ["-C", str(worktree_dir), "rev-parse", "HEAD"],
        cwd=root,
        check=True,
    ).stdout.strip()
    print("Created dedicated worktree")
    print_plan(branch, worktree_dir, base_ref, command, tripwire)
    print(f"next: cd {shlex.quote(str(worktree_dir))} && ./scripts/preflight.sh")
    return 0


def handle_recommend(args: argparse.Namespace) -> int:
    root = repo_root()
    ensure_origin(root)
    base_ref = args.base or DEFAULT_BASE_REF
    base_commit = resolve_base(root, base_ref, explicit=args.base is not None)
    branch = unique_recommendation_branch(root, args.recommend_from_current)
    worktree_dir = choose_worktree_dir(root, branch, args.worktree_dir)
    command = command_for(branch, worktree_dir, base_ref)
    print("Recommended dedicated worktree:")
    print_plan(branch, worktree_dir, base_ref, command, base_commit)
    print(
        "next: ./scripts/preflight.sh "
        f"--create-worktree {shlex.quote(branch)} --base {shlex.quote(base_ref)}"
    )
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create or recommend an isolated Elves git worktree.",
    )
    parser.add_argument(
        "--create-worktree",
        metavar="BRANCH",
        help="Create a new branch in a dedicated worktree.",
    )
    parser.add_argument("--recommend-from-current", metavar="BRANCH", help=argparse.SUPPRESS)
    parser.add_argument(
        "--worktree-dir",
        help="Use this exact worktree directory instead of the generated sibling path.",
    )
    parser.add_argument("--base", help=f"Base ref for the new branch (default: {DEFAULT_BASE_REF}).")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print checks and command without creating anything.",
    )
    args = parser.parse_args(argv)
    if bool(args.create_worktree) == bool(args.recommend_from_current):
        parser.error("pass exactly one of --create-worktree or --recommend-from-current")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.create_worktree:
            return handle_create(args)
        return handle_recommend(args)
    except WorktreeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("error: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
