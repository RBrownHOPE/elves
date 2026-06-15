#!/usr/bin/env python3
"""Optional workspace ownership guard for Elves runs.

The guard is intentionally small and conservative. It never repairs git state, never installs hooks,
and defaults to advisory mode so a missing `.elves-session.json` cannot surprise-block a command.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


LOCAL_GIT_MUTATIONS = {
    "commit",
    "merge",
    "pull",
    "rebase",
    "cherry-pick",
    "revert",
    "checkout",
    "switch",
    "reset",
    "clean",
}
REMOTE_GIT_MUTATIONS = {"push"}
READ_ONLY_GIT_COMMANDS = {
    "status",
    "log",
    "diff",
    "show",
    "fetch",
    "rev-parse",
    "ls-remote",
}
READ_ONLY_GIT_REMOTE_SUBCOMMANDS = {"get-url", "show"}
READ_ONLY_GIT_REMOTE_FLAGS = {"-v", "--verbose"}
READ_ONLY_GIT_BRANCH_FLAGS = {"--show-current", "-a", "-r", "-v", "-vv", "--all", "--remotes"}
READ_ONLY_GIT_STASH_SUBCOMMANDS = {"list", "show"}
READ_ONLY_GIT_SYMBOLIC_REF_FLAGS = {"-q", "--quiet", "--short", "--no-recurse"}
MUTATING_GIT_STASH_SUBCOMMANDS = {"push", "pop", "apply", "clear", "drop", "branch", "store"}
LOCAL_SHELL_MUTATIONS = {"rm", "mv"}
GIT_GLOBAL_OPTIONS_WITH_VALUES = {
    "-C",
    "-c",
    "--config-env",
    "--exec-path",
    "--git-dir",
    "--namespace",
    "--super-prefix",
    "--work-tree",
}
GH_GLOBAL_OPTIONS_WITH_VALUES = {"-R", "--repo", "--hostname"}


@dataclass
class CommandProfile:
    category: str
    check_local: bool = False
    check_remote: bool = False
    reason: str = ""


@dataclass
class GuardState:
    enabled: bool = True
    branch: str | None = None
    start_tip: str | None = None
    allowed_head_tip: str | None = None
    remote_ref: str | None = None
    expected_remote_tip: str | None = None
    last_pushed_tip: str | None = None
    mode: str = "advisory"
    source: str = "none"


@dataclass
class GitSnapshot:
    branch: str | None
    head: str | None
    remote_tip: str | None = None
    remote_ref: str | None = None


@dataclass
class GuardDecision:
    exit_code: int
    allowed: bool
    messages: list[str] = field(default_factory=list)


class GuardConfigError(Exception):
    pass


class GitInspectionError(Exception):
    pass


def optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized or normalized.lower() == "null":
            return None
        return normalized
    return str(value)


def classify_command(command: str) -> CommandProfile:
    try:
        parts = shlex.split(command)
    except ValueError as exc:
        return CommandProfile("unknown", reason=f"could not parse command: {exc}")

    if not parts:
        return CommandProfile("read_only", reason="empty command")

    tool = parts[0]
    if tool == "git":
        return classify_git(parts[1:])
    if tool == "gh":
        return classify_gh(parts[1:])
    if tool in LOCAL_SHELL_MUTATIONS:
        return CommandProfile("local_mutation", check_local=True, reason=f"{tool} mutates files")
    return CommandProfile("unknown", reason="command is not in the protected command set")


def strip_global_options(
    args: list[str],
    options_with_values: set[str],
    attached_value_prefixes: tuple[str, ...] = (),
) -> list[str]:
    subcommand_index = 0
    while subcommand_index < len(args):
        arg = args[subcommand_index]
        if arg == "--":
            subcommand_index += 1
            break
        if not arg.startswith("-"):
            break
        if arg in options_with_values:
            subcommand_index += 2
            continue
        if any(arg.startswith(f"{option}=") for option in options_with_values if option.startswith("--")):
            subcommand_index += 1
            continue
        if any(arg.startswith(prefix) and arg != prefix for prefix in attached_value_prefixes):
            subcommand_index += 1
            continue
        subcommand_index += 1
    return args[subcommand_index:]


def classify_git(args: list[str]) -> CommandProfile:
    args = strip_global_options(args, GIT_GLOBAL_OPTIONS_WITH_VALUES, attached_value_prefixes=("-C", "-c"))
    if not args:
        return CommandProfile("read_only", reason="git with no subcommand")

    subcommand = args[0]
    if subcommand in READ_ONLY_GIT_COMMANDS:
        return CommandProfile("read_only", reason=f"git {subcommand} is diagnostic")
    if subcommand in LOCAL_GIT_MUTATIONS:
        return CommandProfile("local_mutation", check_local=True, reason=f"git {subcommand} mutates state")
    if subcommand == "update-ref":
        return CommandProfile("local_mutation", check_local=True, reason="git update-ref mutates refs")
    if subcommand == "remote":
        return classify_git_remote(args)
    if subcommand == "symbolic-ref":
        return classify_git_symbolic_ref(args)
    if subcommand == "tag":
        return classify_git_tag(args)
    if subcommand == "notes":
        return classify_git_notes(args)
    if subcommand == "stash":
        if len(args) > 1 and args[1] in READ_ONLY_GIT_STASH_SUBCOMMANDS:
            return CommandProfile("read_only", reason=f"git stash {args[1]} is diagnostic")
        if len(args) == 1 or args[1] in MUTATING_GIT_STASH_SUBCOMMANDS:
            return CommandProfile("local_mutation", check_local=True, reason="git stash mutates state")
        return CommandProfile("unknown", reason=f"git stash {args[1]} is not classified")
    if subcommand in REMOTE_GIT_MUTATIONS:
        return CommandProfile(
            "remote_mutation",
            check_local=True,
            check_remote=True,
            reason=f"git {subcommand} mutates remote state",
        )
    if subcommand == "worktree" and len(args) > 1 and args[1] in {"add", "remove", "move"}:
        return CommandProfile("local_mutation", check_local=True, reason=f"git worktree {args[1]}")
    if subcommand == "branch":
        if len(args) == 1 or all(arg in READ_ONLY_GIT_BRANCH_FLAGS for arg in args[1:]):
            return CommandProfile("read_only", reason="git branch inspection")
        return CommandProfile("local_mutation", check_local=True, reason="git branch mutates refs")

    return CommandProfile("unknown", reason=f"git {subcommand} is not classified")


def classify_git_remote(args: list[str]) -> CommandProfile:
    if len(args) == 1 or all(arg in READ_ONLY_GIT_REMOTE_FLAGS for arg in args[1:]):
        return CommandProfile("read_only", reason="git remote inspection")
    if args[1] in READ_ONLY_GIT_REMOTE_SUBCOMMANDS:
        return CommandProfile("read_only", reason=f"git remote {args[1]} is diagnostic")
    return CommandProfile("local_mutation", check_local=True, reason="git remote mutates repo config or refs")


def classify_git_symbolic_ref(args: list[str]) -> CommandProfile:
    value_count = 0
    index = 1
    while index < len(args):
        arg = args[index]
        if arg in {"--delete", "-d"}:
            return CommandProfile("local_mutation", check_local=True, reason="git symbolic-ref deletes refs")
        if arg == "-m":
            return CommandProfile("local_mutation", check_local=True, reason="git symbolic-ref mutates refs")
        if arg in READ_ONLY_GIT_SYMBOLIC_REF_FLAGS:
            index += 1
            continue
        if arg.startswith("-"):
            return CommandProfile("unknown", reason=f"git symbolic-ref option {arg} is not classified")
        value_count += 1
        index += 1
    if value_count <= 1:
        return CommandProfile("read_only", reason="git symbolic-ref inspection")
    return CommandProfile("local_mutation", check_local=True, reason="git symbolic-ref mutates refs")


def classify_git_tag(args: list[str]) -> CommandProfile:
    if len(args) == 1 or args[1] in {"-l", "--list"}:
        return CommandProfile("read_only", reason="git tag inspection")
    return CommandProfile("local_mutation", check_local=True, reason="git tag mutates refs")


def classify_git_notes(args: list[str]) -> CommandProfile:
    if len(args) == 1 or args[1] in {"list", "show"}:
        return CommandProfile("read_only", reason="git notes inspection")
    return CommandProfile("local_mutation", check_local=True, reason="git notes mutates notes refs")


def classify_gh(args: list[str]) -> CommandProfile:
    args = strip_global_options(args, GH_GLOBAL_OPTIONS_WITH_VALUES, attached_value_prefixes=("-R",))
    if not args:
        return CommandProfile("read_only", reason="gh with no subcommand")
    if args[:2] in (["pr", "view"], ["pr", "checks"], ["pr", "status"]):
        return CommandProfile("read_only", reason="gh PR inspection")
    if args[:2] == ["pr", "merge"]:
        return CommandProfile(
            "remote_mutation",
            check_local=True,
            check_remote=True,
            reason="gh pr merge mutates remote state",
        )
    if args[:2] == ["pr", "checkout"]:
        return CommandProfile("local_mutation", check_local=True, reason="gh pr checkout mutates checkout")
    if args[:2] == ["repo", "sync"]:
        return CommandProfile(
            "remote_mutation",
            check_local=True,
            check_remote=True,
            reason="gh repo sync mutates branch state",
        )
    return CommandProfile("unknown", reason="gh command is not in the protected command set")


def load_session_state(session_path: Path) -> tuple[dict[str, Any], GuardState]:
    if not session_path.exists():
        return {}, GuardState(source="none")
    try:
        document = json.loads(session_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GuardConfigError(f"invalid JSON in {session_path}: {exc}") from exc
    except OSError as exc:
        raise GuardConfigError(f"cannot read session file {session_path}: {exc}") from exc
    if not isinstance(document, dict):
        raise GuardConfigError(f"{session_path} must contain a JSON object")

    raw_guard = document.get("workspace_guard", {})
    if raw_guard is None:
        raw_guard = {}
    if not isinstance(raw_guard, dict):
        raise GuardConfigError("workspace_guard must be a JSON object")

    state = GuardState(
        enabled=bool(raw_guard.get("enabled", True)),
        branch=optional_text(raw_guard.get("branch")),
        start_tip=optional_text(raw_guard.get("start_tip")),
        allowed_head_tip=optional_text(raw_guard.get("allowed_head_tip")),
        remote_ref=optional_text(raw_guard.get("remote_ref")),
        expected_remote_tip=optional_text(raw_guard.get("expected_remote_tip")),
        last_pushed_tip=optional_text(raw_guard.get("last_pushed_tip")),
        mode=optional_text(raw_guard.get("mode")) or "advisory",
        source=str(session_path),
    )
    return document, state


def apply_overrides(state: GuardState, args: argparse.Namespace) -> GuardState:
    for arg_name, field_name in (
        ("branch", "branch"),
        ("start_tip", "start_tip"),
        ("allowed_head_tip", "allowed_head_tip"),
        ("remote_ref", "remote_ref"),
        ("expected_remote_tip", "expected_remote_tip"),
        ("last_pushed_tip", "last_pushed_tip"),
    ):
        raw_value = getattr(args, arg_name)
        if raw_value is not None:
            setattr(state, field_name, optional_text(raw_value))
    if args.mode:
        state.mode = args.mode
    return state


def run_git(repo_root: Path, args: list[str], allow_missing: bool = False) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except FileNotFoundError as exc:
        raise GitInspectionError("git executable not found in PATH") from exc
    if result.returncode == 0:
        return result.stdout.strip() or None
    if allow_missing and result.returncode == 1:
        return None
    stderr = result.stderr.strip() or "unknown git error"
    raise GitInspectionError(f"git {' '.join(args)} failed: {stderr}")


def inspect_git(repo_root: Path, remote_ref: str | None = None) -> GitSnapshot:
    branch = run_git(repo_root, ["branch", "--show-current"])
    head = run_git(repo_root, ["rev-parse", "HEAD"])
    remote_tip = None
    if remote_ref:
        remote_tip = run_git(
            repo_root,
            ["rev-parse", "--verify", "--quiet", f"{remote_ref}^{{commit}}"],
            allow_missing=True,
        )
    return GitSnapshot(branch=branch, head=head, remote_tip=remote_tip, remote_ref=remote_ref)


def missing_required(state: GuardState, profile: CommandProfile) -> list[str]:
    missing: list[str] = []
    if profile.check_local:
        if not state.branch:
            missing.append("branch")
        if not state.allowed_head_tip:
            missing.append("allowed_head_tip")
    if profile.check_remote and not state.remote_ref:
        missing.append("remote_ref")
    return missing


def mismatch_messages(state: GuardState, snapshot: GitSnapshot, profile: CommandProfile) -> list[str]:
    messages: list[str] = []
    if profile.check_local:
        if state.branch and snapshot.branch != state.branch:
            messages.append(f"branch mismatch: expected {state.branch}, observed {snapshot.branch or '<none>'}")
        if state.allowed_head_tip and snapshot.head != state.allowed_head_tip:
            messages.append(
                f"local HEAD mismatch: expected {state.allowed_head_tip}, observed {snapshot.head or '<none>'}"
            )

    if profile.check_remote:
        observed = snapshot.remote_tip
        expected = state.expected_remote_tip
        if expected is None:
            if observed is not None:
                messages.append(
                    f"remote tip mismatch: expected absent first-push ref, observed {observed}"
                )
        elif observed != expected:
            messages.append(f"remote tip mismatch: expected {expected}, observed {observed or '<absent>'}")
    return messages


def hard_stop_message() -> str:
    return (
        "Hard Stop: workspace guard detected unexpected branch movement. "
        "Do not merge, rebase, repair, or commit on top. "
        "Use read-only diagnostics and report the collision to the user."
    )


def decision_for(
    command: str,
    state: GuardState,
    snapshot: GitSnapshot | None,
    inspection_error: str | None = None,
    fail_on_error: bool = False,
) -> GuardDecision:
    profile = classify_command(command)
    mode = state.mode if state.mode in {"advisory", "strict"} else "advisory"

    if not state.enabled:
        return GuardDecision(0, True, ["workspace guard disabled"])
    if profile.category == "read_only":
        return GuardDecision(0, True, [f"allowed: {profile.reason}"])
    if profile.category == "unknown":
        message = f"workspace guard cannot classify command: {profile.reason}"
        if mode == "strict" or fail_on_error:
            unknown_profile = CommandProfile("unknown", check_local=True)
            if inspection_error:
                return GuardDecision(2, False, [f"workspace guard inspection warning: {inspection_error}"])
            missing = missing_required(state, unknown_profile)
            if missing:
                return GuardDecision(2, False, [f"workspace guard missing guard data: {', '.join(missing)}"])
            if snapshot is None:
                return GuardDecision(2, False, ["workspace guard could not inspect git state"])
            mismatches = mismatch_messages(state, snapshot, unknown_profile)
            if mismatches:
                return GuardDecision(1, False, [hard_stop_message(), *mismatches])
            return GuardDecision(2, False, [message, "Strict mode fails closed for unclassified commands."])
        return GuardDecision(0, True, [f"allowed: {profile.reason}"])

    if inspection_error:
        message = f"workspace guard inspection warning: {inspection_error}"
        if mode == "strict" or fail_on_error:
            return GuardDecision(2, False, [message])
        return GuardDecision(0, True, [message])

    missing = missing_required(state, profile)
    if missing:
        message = f"workspace guard missing guard data: {', '.join(missing)}"
        if mode == "strict" or fail_on_error:
            return GuardDecision(2, False, [message])
        return GuardDecision(0, True, [message])

    if snapshot is None:
        message = "workspace guard could not inspect git state"
        if mode == "strict" or fail_on_error:
            return GuardDecision(2, False, [message])
        return GuardDecision(0, True, [message])

    mismatches = mismatch_messages(state, snapshot, profile)
    if mismatches:
        if mode == "strict" or fail_on_error:
            return GuardDecision(1, False, [hard_stop_message(), *mismatches])
        return GuardDecision(0, True, ["workspace guard advisory warning:", *mismatches])

    if profile.check_remote and state.expected_remote_tip is None and snapshot.remote_tip is None:
        return GuardDecision(0, True, ["allowed: first push remote ref is absent"])
    return GuardDecision(0, True, [f"allowed: {profile.reason}"])


def update_workspace_guard(session_path: Path, updates: dict[str, Any]) -> None:
    document, _ = load_session_state(session_path)
    guard = document.setdefault("workspace_guard", {})
    if not isinstance(guard, dict):
        raise GuardConfigError("workspace_guard must be a JSON object")
    guard.update(updates)
    guard.setdefault("enabled", True)
    guard.setdefault("mode", "advisory")
    session_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def record_local_head(session_path: Path, snapshot: GitSnapshot) -> list[str]:
    if not snapshot.head:
        raise GitInspectionError("current HEAD is unavailable")
    updates: dict[str, Any] = {"allowed_head_tip": snapshot.head}
    if snapshot.branch:
        updates["branch"] = snapshot.branch
    _, state = load_session_state(session_path)
    if not state.start_tip:
        updates["start_tip"] = snapshot.head
    update_workspace_guard(session_path, updates)
    return [f"recorded allowed_head_tip={snapshot.head}"]


def record_pushed_tip(session_path: Path, snapshot: GitSnapshot) -> list[str]:
    if not snapshot.head:
        raise GitInspectionError("current HEAD is unavailable")
    update_workspace_guard(
        session_path,
        {
            "expected_remote_tip": snapshot.head,
            "last_pushed_tip": snapshot.head,
        },
    )
    return [f"recorded expected_remote_tip={snapshot.head}", f"recorded last_pushed_tip={snapshot.head}"]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check or update Elves workspace guard state.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check-command", help="Command text to classify and guard.")
    action.add_argument("--record-local-head", action="store_true", help="Record current HEAD as allowed_head_tip.")
    action.add_argument("--record-pushed-tip", action="store_true", help="Record current HEAD as pushed remote tip.")
    parser.add_argument("--session-path", default=".elves-session.json", help="Path to .elves-session.json.")
    parser.add_argument("--repo-root", default=".", help="Git repository root or working tree.")
    parser.add_argument("--mode", choices=("advisory", "strict"), help="Override guard mode.")
    parser.add_argument("--fail-on-error", action="store_true", help="Make advisory warnings exit non-zero.")
    parser.add_argument("--branch")
    parser.add_argument("--start-tip")
    parser.add_argument("--allowed-head-tip")
    parser.add_argument("--remote-ref")
    parser.add_argument("--expected-remote-tip")
    parser.add_argument("--last-pushed-tip")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    session_path = Path(args.session_path)
    if not session_path.is_absolute():
        session_path = repo_root / session_path

    try:
        _, state = load_session_state(session_path)
        state = apply_overrides(state, args)

        if args.record_local_head:
            messages = record_local_head(session_path, inspect_git(repo_root))
            print("\n".join(messages))
            return 0
        if args.record_pushed_tip:
            messages = record_pushed_tip(session_path, inspect_git(repo_root))
            print("\n".join(messages))
            return 0

        profile = classify_command(args.check_command)
        snapshot = None
        inspection_error = None
        strict_unknown = profile.category == "unknown" and (
            (state.mode if state.mode in {"advisory", "strict"} else "advisory") == "strict" or args.fail_on_error
        )
        if profile.check_local or profile.check_remote or strict_unknown:
            try:
                snapshot = inspect_git(repo_root, state.remote_ref if profile.check_remote else None)
            except GitInspectionError as exc:
                inspection_error = str(exc)
        decision = decision_for(
            args.check_command,
            state,
            snapshot,
            inspection_error=inspection_error,
            fail_on_error=args.fail_on_error,
        )
    except (GuardConfigError, GitInspectionError) as exc:
        message = f"workspace guard configuration warning: {exc}"
        if args.record_local_head or args.record_pushed_tip or args.mode == "strict" or args.fail_on_error:
            print(message, file=sys.stderr)
            return 2
        print(message)
        return 0

    stream = sys.stdout if decision.allowed else sys.stderr
    for message in decision.messages:
        print(message, file=stream)
    return decision.exit_code


if __name__ == "__main__":
    sys.exit(main())
