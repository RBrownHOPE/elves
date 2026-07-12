"""Single external writer lease: exclusivity, preflight, and write packets.

Exactly one live writer lease may exist. The host owns branch commits, push, PR,
and run memory. Workers may only edit/test (and optionally create detached
handoff commits) inside a verified detached worktree under an issued lease.

Lease state lives under ignored `.elves/runtime/leases/` and must not be
committed as product artifacts.
"""

from __future__ import annotations

import json
import stat
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

from .schema import ValidationIssue


class LeaseState(str, Enum):
    PREPARED = "prepared"
    ACTIVE = "active"
    AUDITING = "auditing"
    EXPORTED = "exported"
    INTEGRATED = "integrated"
    CLOSED = "closed"
    REJECTED = "rejected"


LIVE_LEASE_STATES: frozenset[LeaseState] = frozenset(
    {
        LeaseState.PREPARED,
        LeaseState.ACTIVE,
        LeaseState.AUDITING,
        LeaseState.EXPORTED,
    }
)

# Git actions a qualified detached writer may use when the lease permits commits.
DEFAULT_PERMITTED_GIT_ACTIONS: tuple[str, ...] = (
    "status",
    "diff",
    "log",
    "show",
    "rev-parse",
    "add",
    "commit",  # detached handoff only when lease.detached_commits_permitted
    "cat-file",
)

DEFAULT_FORBIDDEN_GIT_ACTIONS: tuple[str, ...] = (
    "push",
    "fetch",  # network; host owns remotes
    "pull",
    "checkout",
    "switch",
    "branch",
    "tag",
    "merge",
    "rebase",
    "cherry-pick",
    "reset",
    "clean",
    "remote",
    "config",
    "update-ref",
    "symbolic-ref",
    "worktree",
)

DEFAULT_FORBIDDEN_PATH_PREFIXES: tuple[str, ...] = (
    ".git/",
    ".elves/",
    "docs/elves/",
    "docs/plans/",
    ".elves-session.json",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def leases_root(repo_root: Path) -> Path:
    return Path(repo_root) / ".elves" / "runtime" / "leases"


def ensure_leases_dir(repo_root: Path) -> Path:
    path = leases_root(repo_root)
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(stat.S_IRWXU)
    except OSError:
        pass
    return path


def run_git(
    cwd: Path,
    args: Sequence[str],
    *,
    check: bool = True,
    text: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run git with argv only (shell=False)."""
    cmd = ["git", *args]
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        check=False,
        capture_output=True,
        text=text,
    )
    if check and result.returncode != 0:
        raise ValidationIssue(
            "git_command_failed",
            f"git {' '.join(args)} failed (exit {result.returncode}): "
            f"{(result.stderr or result.stdout or '').strip()}",
            path=str(cwd),
        )
    return result


@dataclass
class WriterLease:
    """One-writer lease contract and live state."""

    lease_id: str
    host_checkout: str
    worker_checkout: str
    session_id: str
    base_head: str
    adapter: str
    profile: str
    sandbox_profile: str = "devbox"
    allowed_paths: list[str] = field(default_factory=list)
    forbidden_path_prefixes: list[str] = field(
        default_factory=lambda: list(DEFAULT_FORBIDDEN_PATH_PREFIXES)
    )
    permitted_git_actions: list[str] = field(
        default_factory=lambda: list(DEFAULT_PERMITTED_GIT_ACTIONS)
    )
    forbidden_git_actions: list[str] = field(
        default_factory=lambda: list(DEFAULT_FORBIDDEN_GIT_ACTIONS)
    )
    detached_commits_permitted: bool = True
    require_detached: bool = True
    state: LeaseState = LeaseState.PREPARED
    created_at: str | None = None
    updated_at: str | None = None
    pre_status: str | None = None
    pre_refs_digest: str | None = None
    notes: str = ""
    rejection_reason: str | None = None
    worker_tip: str | None = None
    exported_patch_dir: str | None = None
    # Negative fixture: workspace sandbox cannot be assumed commit-capable.
    workspace_sandbox_commit_capable: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["state"] = self.state.value
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> WriterLease:
        return cls(
            lease_id=str(data["lease_id"]),
            host_checkout=str(data["host_checkout"]),
            worker_checkout=str(data["worker_checkout"]),
            session_id=str(data["session_id"]),
            base_head=str(data["base_head"]),
            adapter=str(data["adapter"]),
            profile=str(data["profile"]),
            sandbox_profile=str(data.get("sandbox_profile") or "devbox"),
            allowed_paths=list(data.get("allowed_paths") or []),
            forbidden_path_prefixes=list(
                data.get("forbidden_path_prefixes") or DEFAULT_FORBIDDEN_PATH_PREFIXES
            ),
            permitted_git_actions=list(
                data.get("permitted_git_actions") or DEFAULT_PERMITTED_GIT_ACTIONS
            ),
            forbidden_git_actions=list(
                data.get("forbidden_git_actions") or DEFAULT_FORBIDDEN_GIT_ACTIONS
            ),
            detached_commits_permitted=bool(data.get("detached_commits_permitted", True)),
            require_detached=bool(data.get("require_detached", True)),
            state=LeaseState(str(data.get("state") or LeaseState.PREPARED.value)),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            pre_status=data.get("pre_status"),
            pre_refs_digest=data.get("pre_refs_digest"),
            notes=str(data.get("notes") or ""),
            rejection_reason=data.get("rejection_reason"),
            worker_tip=data.get("worker_tip"),
            exported_patch_dir=data.get("exported_patch_dir"),
            workspace_sandbox_commit_capable=bool(
                data.get("workspace_sandbox_commit_capable", False)
            ),
        )


def build_write_task_packet(lease: WriterLease, *, task: str, contract: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Coordinator packet for a bounded worker turn (host-owned synthesis later)."""
    return {
        "lease_id": lease.lease_id,
        "session_id": lease.session_id,
        "task": task,
        "worker_checkout": lease.worker_checkout,
        "base_head": lease.base_head,
        "allowed_paths": list(lease.allowed_paths),
        "forbidden_path_prefixes": list(lease.forbidden_path_prefixes),
        "permitted_git_actions": list(lease.permitted_git_actions),
        "forbidden_git_actions": list(lease.forbidden_git_actions),
        "detached_commits_permitted": lease.detached_commits_permitted,
        "sandbox_profile": lease.sandbox_profile,
        "denials": {
            "branch": True,
            "tag": True,
            "ref_mutation": True,
            "push": True,
            "pr": True,
            "run_memory": True,
            "credentials": True,
            "checkout_other_worktree": True,
            "headless_worktree_resume_as_isolation": lease.adapter == "grok-build",
        },
        "contract": dict(contract or {}),
        "notes": [
            "Host owns branch commits, push, PR, and run memory",
            "Detached commits are untrusted handoff boundaries only when permitted",
            "Never bare-cherry-pick worker commits onto the owned branch",
        ],
    }


def is_path_allowed(rel_path: str, lease: WriterLease) -> bool:
    """Return True if a repo-relative path is within lease scope."""
    normalized = rel_path.replace("\\", "/").lstrip("./")
    for prefix in lease.forbidden_path_prefixes:
        p = prefix.replace("\\", "/")
        if normalized == p.rstrip("/") or normalized.startswith(p):
            return False
    if not lease.allowed_paths:
        # Empty allow-list means "all non-forbidden product paths".
        return True
    for allowed in lease.allowed_paths:
        a = allowed.replace("\\", "/").lstrip("./")
        if normalized == a or normalized.startswith(a.rstrip("/") + "/") or a == ".":
            return True
        # Directory allow entries.
        if a.endswith("/") and normalized.startswith(a):
            return True
    return False


def _git_symbolic_head(cwd: Path) -> str | None:
    result = run_git(cwd, ["symbolic-ref", "-q", "HEAD"], check=False)
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None


def _git_head(cwd: Path) -> str:
    return run_git(cwd, ["rev-parse", "HEAD"]).stdout.strip()


def _git_status_porcelain(cwd: Path) -> str:
    return run_git(cwd, ["status", "--porcelain"]).stdout


def _worktree_list_porcelain(cwd: Path) -> str:
    return run_git(cwd, ["worktree", "list", "--porcelain"]).stdout


def worktree_is_registered(worker_checkout: Path, *, git_cwd: Path | None = None) -> bool:
    """True when worker path appears in git worktree list --porcelain."""
    root = git_cwd or worker_checkout
    text = _worktree_list_porcelain(root)
    target = str(worker_checkout.resolve())
    for line in text.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree ") :].strip()
            try:
                if Path(path).resolve() == Path(target).resolve():
                    return True
            except OSError:
                if path == target:
                    return True
    # Single-checkout repos: the main worktree is the repo root itself.
    try:
        if worker_checkout.resolve() == Path(root).resolve():
            return True
    except OSError:
        pass
    return False


def preflight_worker_checkout(
    *,
    worker_checkout: Path,
    base_head: str,
    require_detached: bool = True,
    require_clean: bool = True,
    require_registered: bool = True,
) -> dict[str, Any]:
    """Fail closed unless worker checkout matches lease preconditions."""
    worker = Path(worker_checkout)
    if not worker.is_dir():
        raise ValidationIssue(
            "worker_checkout_missing",
            f"Worker checkout does not exist: {worker}",
        )
    head = _git_head(worker)
    status = _git_status_porcelain(worker)
    symbolic = _git_symbolic_head(worker)
    detached = symbolic is None
    registered = worktree_is_registered(worker)

    if require_registered and not registered:
        raise ValidationIssue(
            "worker_unregistered",
            f"Worker checkout is not a registered git worktree: {worker}",
            hint="Expand/canonicalize path and verify git worktree list --porcelain",
        )
    if require_clean and status.strip():
        raise ValidationIssue(
            "worker_dirty",
            "Worker checkout is dirty; refuse lease until clean",
            path=str(worker),
            hint=status.strip()[:400],
        )
    if require_detached and not detached:
        raise ValidationIssue(
            "worker_not_detached",
            f"Worker checkout is branch-attached ({symbolic}); detached HEAD required",
            path=str(worker),
        )
    if head != base_head:
        raise ValidationIssue(
            "worker_head_mismatch",
            f"Worker HEAD `{head}` does not match lease base `{base_head}`",
            path=str(worker),
        )
    return {
        "head": head,
        "detached": detached,
        "symbolic_ref": symbolic,
        "status_porcelain": status,
        "registered": registered,
        "clean": not bool(status.strip()),
    }


def refs_digest(cwd: Path) -> str:
    """Stable digest of refs for pre/post comparison (no network)."""
    import hashlib

    result = run_git(cwd, ["for-each-ref", "--format=%(refname) %(objectname)"])
    material = "\n".join(sorted((result.stdout or "").splitlines()))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class LeaseStore:
    """Disk-backed exclusive writer lease store."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = Path(repo_root)
        self.root = ensure_leases_dir(self.repo_root)

    def _path(self, lease_id: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in lease_id)
        return self.root / f"{safe}.json"

    def list_leases(self) -> list[WriterLease]:
        leases: list[WriterLease] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                leases.append(WriterLease.from_dict(data))
            except (OSError, json.JSONDecodeError, KeyError, ValueError):
                continue
        return leases

    def live_leases(self) -> list[WriterLease]:
        return [lease for lease in self.list_leases() if lease.state in LIVE_LEASE_STATES]

    def get(self, lease_id: str) -> WriterLease:
        path = self._path(lease_id)
        if not path.is_file():
            raise ValidationIssue(
                "lease_not_found",
                f"No lease record for `{lease_id}`",
                path=str(path),
            )
        return WriterLease.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def save(self, lease: WriterLease) -> WriterLease:
        lease.updated_at = _utc_now()
        if not lease.created_at:
            lease.created_at = lease.updated_at
        path = self._path(lease.lease_id)
        path.write_text(
            json.dumps(lease.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        try:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        return lease

    def prepare(
        self,
        *,
        lease_id: str,
        host_checkout: Path,
        worker_checkout: Path,
        session_id: str,
        base_head: str,
        adapter: str,
        profile: str,
        allowed_paths: Sequence[str] | None = None,
        sandbox_profile: str = "devbox",
        detached_commits_permitted: bool = True,
        require_detached: bool = True,
        write_profile_qualified: bool = True,
        used_headless_worktree_resume: bool = False,
        grok_version: str | None = None,
        notes: str = "",
    ) -> WriterLease:
        """Create the sole live lease after worker preflight and profile checks."""
        if not write_profile_qualified:
            raise ValidationIssue(
                "write_profile_unqualified",
                f"Write profile `{profile}` is not qualified for isolated write",
            )
        if used_headless_worktree_resume and adapter == "grok-build":
            from .sessions import assert_grok_worktree_isolation

            assert_grok_worktree_isolation(
                version=grok_version,
                cwd_verified=True,
                worktree_registered=True,
                used_headless_worktree_resume=True,
            )

        live = self.live_leases()
        if live:
            raise ValidationIssue(
                "lease_exclusivity",
                f"A live writer lease already exists: {live[0].lease_id} ({live[0].state.value})",
                hint="Only one external writer lease may be active at a time",
            )

        pre = preflight_worker_checkout(
            worker_checkout=Path(worker_checkout),
            base_head=base_head,
            require_detached=require_detached,
            require_clean=True,
            require_registered=True,
        )
        digest = refs_digest(Path(worker_checkout))
        # workspace sandbox is never assumed commit-capable (negative fixture policy).
        workspace_capable = sandbox_profile != "workspace"
        lease = WriterLease(
            lease_id=lease_id,
            host_checkout=str(Path(host_checkout).resolve()),
            worker_checkout=str(Path(worker_checkout).resolve()),
            session_id=session_id,
            base_head=base_head,
            adapter=adapter,
            profile=profile,
            sandbox_profile=sandbox_profile,
            allowed_paths=list(allowed_paths or []),
            detached_commits_permitted=detached_commits_permitted and workspace_capable,
            require_detached=require_detached,
            state=LeaseState.PREPARED,
            pre_status=pre["status_porcelain"],
            pre_refs_digest=digest,
            notes=notes,
            worker_tip=pre["head"],
            workspace_sandbox_commit_capable=workspace_capable,
        )
        if sandbox_profile == "workspace":
            lease.notes = (
                lease.notes + "\nworkspace sandbox: commits not assumed capable; "
                "detached_commits_permitted forced false"
            ).strip()
            lease.detached_commits_permitted = False
        return self.save(lease)

    def activate(self, lease_id: str) -> WriterLease:
        lease = self.get(lease_id)
        if lease.state != LeaseState.PREPARED:
            raise ValidationIssue(
                "invalid_lease_state",
                f"Cannot activate lease in state `{lease.state.value}`",
            )
        lease.state = LeaseState.ACTIVE
        return self.save(lease)

    def mark_auditing(self, lease_id: str) -> WriterLease:
        lease = self.get(lease_id)
        if lease.state not in {LeaseState.ACTIVE, LeaseState.AUDITING}:
            raise ValidationIssue(
                "invalid_lease_state",
                f"Cannot audit lease in state `{lease.state.value}`",
            )
        lease.state = LeaseState.AUDITING
        return self.save(lease)

    def mark_exported(self, lease_id: str, patch_dir: str) -> WriterLease:
        lease = self.get(lease_id)
        lease.state = LeaseState.EXPORTED
        lease.exported_patch_dir = patch_dir
        return self.save(lease)

    def mark_integrated(self, lease_id: str) -> WriterLease:
        lease = self.get(lease_id)
        if lease.state != LeaseState.EXPORTED:
            raise ValidationIssue(
                "invalid_lease_state",
                f"Cannot integrate lease before export (state={lease.state.value})",
            )
        lease.state = LeaseState.INTEGRATED
        return self.save(lease)

    def reject(self, lease_id: str, reason: str) -> WriterLease:
        lease = self.get(lease_id)
        lease.state = LeaseState.REJECTED
        lease.rejection_reason = reason
        return self.save(lease)

    def close(self, lease_id: str) -> WriterLease:
        lease = self.get(lease_id)
        lease.state = LeaseState.CLOSED
        return self.save(lease)

    def refresh_worker_to_tip(
        self,
        lease_id: str,
        *,
        new_tip: str,
        allow_if_states: Sequence[LeaseState] | None = None,
    ) -> dict[str, Any]:
        """After export+integration, move clean detached worker HEAD to new tip.

        Refuses dirty/ambiguous state. Does not delete worktrees or sessions.
        """
        lease = self.get(lease_id)
        allowed = set(allow_if_states or (LeaseState.INTEGRATED,))
        if lease.state not in allowed:
            raise ValidationIssue(
                "invalid_lease_state",
                f"Worker refresh requires state in {[s.value for s in allowed]} "
                f"(have {lease.state.value})",
            )
        worker = Path(lease.worker_checkout)
        status = _git_status_porcelain(worker)
        if status.strip():
            raise ValidationIssue(
                "worker_dirty_on_refresh",
                "Refuse refresh: worker checkout is dirty or ambiguous",
                hint=status.strip()[:400],
            )
        run_git(worker, ["update-ref", "HEAD", new_tip])
        # Detached HEAD stays detached; verify.
        head = _git_head(worker)
        if head != new_tip:
            raise ValidationIssue(
                "refresh_head_mismatch",
                f"After refresh HEAD `{head}` != requested tip `{new_tip}`",
            )
        lease.worker_tip = head
        lease.base_head = head
        self.save(lease)
        return {"lease_id": lease.lease_id, "worker_tip": head, "clean": True}
