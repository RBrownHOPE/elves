"""Pre/post writer-lease audit, binary patch export, and host apply-check.

Accepted detached commits are untrusted handoff boundaries. Export binary
patches, audit the chain, then `git apply --check --index` in the owned
worktree. Never bare-cherry-pick worker commits.

Uses `workspace_guard.classify_command` for action classification rather than
duplicating git classifiers.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import stat
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

from .leases import (
    WriterLease,
    _git_head,
    _git_status_porcelain,
    is_path_allowed,
    refs_digest,
    run_git,
)
from .schema import ValidationIssue


def _load_workspace_guard():
    """Import workspace_guard without requiring package install."""
    scripts_dir = Path(__file__).resolve().parents[1]
    path = scripts_dir / "workspace_guard.py"
    spec = importlib.util.spec_from_file_location("workspace_guard_for_audit", path)
    if spec is None or spec.loader is None:
        raise ValidationIssue(
            "workspace_guard_missing",
            f"Unable to load workspace_guard from {path}",
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@dataclass
class CommitInfo:
    sha: str
    parents: list[str]
    tree: str
    subject: str
    author: str
    paths: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AuditResult:
    ok: bool
    lease_id: str
    base_head: str
    worker_tip: str
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    commit_chain: list[CommitInfo] = field(default_factory=list)
    status_porcelain: str = ""
    staged: bool = False
    refs_digest_before: str | None = None
    refs_digest_after: str | None = None
    refs_changed: bool = False
    remotes_changed: bool = False
    config_changed: bool = False
    hooks_changed: bool = False
    symlink_escapes: list[str] = field(default_factory=list)
    out_of_scope_paths: list[str] = field(default_factory=list)
    forbidden_actions_seen: list[str] = field(default_factory=list)
    process_leaks: list[str] = field(default_factory=list)
    patch_paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "lease_id": self.lease_id,
            "base_head": self.base_head,
            "worker_tip": self.worker_tip,
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "commit_chain": [c.to_dict() for c in self.commit_chain],
            "status_porcelain": self.status_porcelain,
            "staged": self.staged,
            "refs_digest_before": self.refs_digest_before,
            "refs_digest_after": self.refs_digest_after,
            "refs_changed": self.refs_changed,
            "remotes_changed": self.remotes_changed,
            "config_changed": self.config_changed,
            "hooks_changed": self.hooks_changed,
            "symlink_escapes": list(self.symlink_escapes),
            "out_of_scope_paths": list(self.out_of_scope_paths),
            "forbidden_actions_seen": list(self.forbidden_actions_seen),
            "process_leaks": list(self.process_leaks),
            "patch_paths": list(self.patch_paths),
            "commit_count": len(self.commit_chain),
        }


def snapshot_hooks(git_dir: Path) -> dict[str, str]:
    hooks = git_dir / "hooks"
    digests: dict[str, str] = {}
    if not hooks.is_dir():
        return digests
    for path in sorted(hooks.iterdir()):
        if path.is_file() and not path.name.endswith(".sample"):
            digests[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


def snapshot_config(git_dir: Path) -> str | None:
    config = git_dir / "config"
    if not config.is_file():
        return None
    return hashlib.sha256(config.read_bytes()).hexdigest()


def snapshot_remotes(cwd: Path) -> str:
    result = run_git(cwd, ["remote", "-v"], check=False)
    return hashlib.sha256((result.stdout or "").encode("utf-8")).hexdigest()


def git_dir_for(cwd: Path) -> Path:
    out = run_git(cwd, ["rev-parse", "--git-dir"]).stdout.strip()
    path = Path(out)
    if not path.is_absolute():
        path = (cwd / path).resolve()
    return path


def list_commit_chain(cwd: Path, base_head: str, tip: str) -> list[CommitInfo]:
    """Return direct-descendant commits from base (exclusive) to tip (inclusive)."""
    if base_head == tip:
        return []
    # Ensure tip is descendant of base.
    merge_base = run_git(cwd, ["merge-base", base_head, tip]).stdout.strip()
    if merge_base != base_head:
        raise ValidationIssue(
            "commit_chain_not_descendant",
            f"Tip `{tip}` is not a direct descendant of base `{base_head}` "
            f"(merge-base={merge_base})",
        )
    rev_list = run_git(
        cwd,
        ["rev-list", "--reverse", f"{base_head}..{tip}"],
    ).stdout.split()
    commits: list[CommitInfo] = []
    prev = base_head
    for sha in rev_list:
        parents = run_git(cwd, ["show", "-s", "--format=%P", sha]).stdout.strip().split()
        tree = run_git(cwd, ["show", "-s", "--format=%T", sha]).stdout.strip()
        subject = run_git(cwd, ["show", "-s", "--format=%s", sha]).stdout.strip()
        author = run_git(cwd, ["show", "-s", "--format=%an <%ae>", sha]).stdout.strip()
        paths = run_git(
            cwd,
            ["diff-tree", "--no-commit-id", "--name-only", "-r", sha],
        ).stdout.splitlines()
        # Expect single parent direct descendant.
        if len(parents) != 1:
            raise ValidationIssue(
                "unexpected_merge_or_root",
                f"Commit `{sha}` has parents {parents}; only single-parent commits permitted",
            )
        if parents[0] != prev:
            raise ValidationIssue(
                "chain_parent_mismatch",
                f"Commit `{sha}` parent `{parents[0]}` != expected previous `{prev}`",
            )
        commits.append(
            CommitInfo(
                sha=sha,
                parents=parents,
                tree=tree,
                subject=subject,
                author=author,
                paths=paths,
            )
        )
        prev = sha
    return commits


def detect_symlink_escapes(cwd: Path, paths: Sequence[str], lease: WriterLease) -> list[str]:
    escapes: list[str] = []
    root = cwd.resolve()
    for rel in paths:
        full = (cwd / rel).resolve()
        try:
            full.relative_to(root)
        except ValueError:
            escapes.append(rel)
            continue
        if full.is_symlink():
            target = full.resolve()
            try:
                target.relative_to(root)
            except ValueError:
                escapes.append(rel)
                continue
        if not is_path_allowed(rel, lease):
            # tracked separately as out_of_scope; still note symlink escapes only for links
            pass
    return escapes


def classify_worker_command(command: str) -> dict[str, Any]:
    """Use workspace_guard classification for a worker command string."""
    guard = _load_workspace_guard()
    profile = guard.classify_command(command)
    return {
        "category": profile.category,
        "check_local": profile.check_local,
        "check_remote": profile.check_remote,
        "reason": profile.reason,
    }


def audit_lease_turn(
    lease: WriterLease,
    *,
    process_baseline: Sequence[str] | None = None,
    process_observed: Sequence[str] | None = None,
    observed_commands: Sequence[str] | None = None,
    pre_refs_digest: str | None = None,
    pre_remotes: str | None = None,
    pre_config: str | None = None,
    pre_hooks: dict[str, str] | None = None,
) -> AuditResult:
    """Post-turn audit of the worker checkout against the lease contract."""
    worker = Path(lease.worker_checkout)
    tip = _git_head(worker)
    status = _git_status_porcelain(worker)
    staged = any(line and line[0] in "MADRCT" for line in status.splitlines())
    result = AuditResult(
        ok=True,
        lease_id=lease.lease_id,
        base_head=lease.base_head,
        worker_tip=tip,
        status_porcelain=status,
        staged=staged,
        refs_digest_before=pre_refs_digest or lease.pre_refs_digest,
    )

    # Dirty index / uncommitted changes fail (expected handoff is clean commit chain).
    if status.strip():
        result.ok = False
        result.reasons.append("worker checkout is dirty after turn (uncommitted changes)")

    # Commit chain
    try:
        chain = list_commit_chain(worker, lease.base_head, tip)
        result.commit_chain = chain
    except ValidationIssue as issue:
        result.ok = False
        result.reasons.append(issue.message)
        chain = []

    if chain and not lease.detached_commits_permitted:
        result.ok = False
        result.reasons.append(
            "detached commits present but lease.detached_commits_permitted is false "
            f"(sandbox={lease.sandbox_profile})"
        )

    # Path scope
    out_of_scope: list[str] = []
    all_paths: list[str] = []
    for commit in chain:
        all_paths.extend(commit.paths)
        for path in commit.paths:
            if not is_path_allowed(path, lease):
                out_of_scope.append(path)
    # Also scan untracked/modified paths from status
    for line in status.splitlines():
        if not line.strip():
            continue
        path = line[3:].strip() if len(line) > 3 else line.strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        all_paths.append(path)
        if not is_path_allowed(path, lease):
            out_of_scope.append(path)
    result.out_of_scope_paths = sorted(set(out_of_scope))
    if result.out_of_scope_paths:
        result.ok = False
        result.reasons.append(
            "out-of-scope paths: " + ", ".join(result.out_of_scope_paths[:20])
        )

    result.symlink_escapes = detect_symlink_escapes(worker, sorted(set(all_paths)), lease)
    if result.symlink_escapes:
        result.ok = False
        result.reasons.append("symlink escape paths: " + ", ".join(result.symlink_escapes))

    # Refs / remotes / config / hooks
    after_refs = refs_digest(worker)
    result.refs_digest_after = after_refs
    if result.refs_digest_before and after_refs != result.refs_digest_before:
        # HEAD movement for detached commits updates some reflogs but for-each-ref
        # should stay stable if no branch/tag created. Flag any change.
        result.refs_changed = True
        result.ok = False
        result.reasons.append("refs digest changed (new branch/tag/ref mutation)")

    gdir = git_dir_for(worker)
    after_remotes = snapshot_remotes(worker)
    if pre_remotes is not None and after_remotes != pre_remotes:
        result.remotes_changed = True
        result.ok = False
        result.reasons.append("git remotes changed during lease")
    after_config = snapshot_config(gdir)
    if pre_config is not None and after_config != pre_config:
        result.config_changed = True
        result.ok = False
        result.reasons.append("git config changed during lease")
    after_hooks = snapshot_hooks(gdir)
    if pre_hooks is not None and after_hooks != pre_hooks:
        result.hooks_changed = True
        result.ok = False
        result.reasons.append("git hooks changed during lease")

    # Observed commands classification
    for command in observed_commands or []:
        profile = classify_worker_command(command)
        # Extract subcommand roughly
        parts = command.split()
        action = parts[1] if len(parts) > 1 and parts[0] == "git" else (parts[0] if parts else "")
        if action in lease.forbidden_git_actions or profile["category"] == "remote_mutation":
            result.forbidden_actions_seen.append(command)
    if result.forbidden_actions_seen:
        result.ok = False
        result.reasons.append(
            "forbidden actions observed: " + "; ".join(result.forbidden_actions_seen[:10])
        )

    # Process leak: any observed pid/command not in baseline
    baseline = set(process_baseline or [])
    observed = list(process_observed or [])
    leaks = [item for item in observed if item not in baseline]
    result.process_leaks = leaks
    if leaks:
        result.ok = False
        result.reasons.append("process leak(s) remain after turn: " + ", ".join(leaks[:10]))

    return result


def export_binary_patches(
    lease: WriterLease,
    *,
    output_dir: Path,
    chain: Sequence[CommitInfo] | None = None,
) -> list[Path]:
    """Export binary-safe per-commit patches via format-patch."""
    worker = Path(lease.worker_checkout)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    try:
        out.chmod(stat.S_IRWXU)
    except OSError:
        pass

    if chain is None:
        tip = _git_head(worker)
        chain = list_commit_chain(worker, lease.base_head, tip)
    if not chain:
        return []

    # format-patch writes files into out/
    run_git(
        worker,
        [
            "format-patch",
            "--binary",
            "-o",
            str(out),
            f"{lease.base_head}..{chain[-1].sha}",
        ],
    )
    patches = sorted(out.glob("*.patch"))
    # Also write a chain manifest for host import.
    manifest = {
        "lease_id": lease.lease_id,
        "base_head": lease.base_head,
        "worker_tip": chain[-1].sha if chain else lease.base_head,
        "commits": [c.to_dict() for c in chain],
        "patches": [p.name for p in patches],
    }
    (out / "chain.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    try:
        (out / "chain.json").chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return patches


def host_apply_check(
    host_checkout: Path,
    patch_paths: Sequence[Path],
    *,
    base_head: str | None = None,
) -> dict[str, Any]:
    """Run git apply --check --index for each patch without committing.

    Host checkout should be clean and at the expected base when provided.
    """
    host = Path(host_checkout)
    if base_head is not None:
        head = _git_head(host)
        if head != base_head:
            raise ValidationIssue(
                "host_base_mismatch",
                f"Host HEAD `{head}` != expected base `{base_head}` for apply-check",
            )
    status = _git_status_porcelain(host)
    if status.strip():
        raise ValidationIssue(
            "host_dirty",
            "Host checkout must be clean before apply-check",
            hint=status.strip()[:400],
        )

    checked: list[str] = []
    for patch in patch_paths:
        result = subprocess.run(
            ["git", "apply", "--check", "--index", str(patch)],
            cwd=str(host),
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise ValidationIssue(
                "apply_check_failed",
                f"git apply --check --index failed for {patch.name}: "
                f"{(result.stderr or result.stdout or '').strip()}",
                path=str(patch),
            )
        checked.append(patch.name)
    return {
        "ok": True,
        "checked": checked,
        "host_head": _git_head(host),
        "note": "apply-check only; host creates sanitized commits separately",
    }


def pre_turn_snapshots(worker_checkout: Path) -> dict[str, Any]:
    """Capture pre-turn digests for post comparison."""
    worker = Path(worker_checkout)
    gdir = git_dir_for(worker)
    return {
        "refs_digest": refs_digest(worker),
        "remotes": snapshot_remotes(worker),
        "config": snapshot_config(gdir),
        "hooks": snapshot_hooks(gdir),
        "head": _git_head(worker),
        "status_porcelain": _git_status_porcelain(worker),
    }
