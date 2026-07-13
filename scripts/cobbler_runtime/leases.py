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
from .storage import (
    StorageError,
    assert_embedded_id,
    atomic_write_json,
    directory_lock,
    qualify_write_evidence,
    record_filename,
    snapshot_path as storage_snapshot_path,
)


def host_qualification_evidence(
    *,
    adapter: str,
    model: str,
    sandbox: str = "devbox",
    worktree: str,
    cwd: str,
    parent: str,
    source_head: str,
    session_id: str,
    capabilities: Mapping[str, Any] | None = None,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Build a host-issued qualification record (test and host helpers)."""
    return {
        "adapter": adapter,
        "model": model,
        "sandbox": sandbox,
        "worktree": worktree,
        "cwd": cwd,
        "parent": parent,
        "source_head": source_head,
        "session_id": session_id,
        "capabilities": dict(capabilities or {"write": True}),
        "evidence_kind": "host_observed",
        "observed_at": observed_at or _utc_now(),
        "host_observed": True,
        "stale": False,
        "preference_declared": False,
    }


class LeaseState(str, Enum):
    PREPARED = "prepared"
    ACTIVE = "active"
    AUDITING = "auditing"
    AUDITED_PASS = "audited_pass"
    EXPORTED = "exported"
    APPLY_CHECKED = "apply_checked"
    INTEGRATED = "integrated"
    CLOSED = "closed"
    REJECTED = "rejected"


LIVE_LEASE_STATES: frozenset[LeaseState] = frozenset(
    {
        LeaseState.PREPARED,
        LeaseState.ACTIVE,
        LeaseState.AUDITING,
        LeaseState.AUDITED_PASS,
        LeaseState.EXPORTED,
        LeaseState.APPLY_CHECKED,
    }
)

# Explicit compare-and-swap lifecycle. Rejected is terminal.
_ALLOWED_LEASE_TRANSITIONS: dict[LeaseState, frozenset[LeaseState]] = {
    LeaseState.PREPARED: frozenset(
        {LeaseState.PREPARED, LeaseState.ACTIVE, LeaseState.REJECTED, LeaseState.CLOSED}
    ),
    LeaseState.ACTIVE: frozenset(
        {LeaseState.ACTIVE, LeaseState.AUDITING, LeaseState.REJECTED, LeaseState.CLOSED}
    ),
    LeaseState.AUDITING: frozenset(
        {
            LeaseState.AUDITING,
            LeaseState.AUDITED_PASS,
            LeaseState.REJECTED,
            LeaseState.CLOSED,
        }
    ),
    LeaseState.AUDITED_PASS: frozenset(
        {LeaseState.AUDITED_PASS, LeaseState.EXPORTED, LeaseState.REJECTED, LeaseState.CLOSED}
    ),
    LeaseState.EXPORTED: frozenset(
        {
            LeaseState.EXPORTED,
            LeaseState.APPLY_CHECKED,
            LeaseState.REJECTED,
            LeaseState.CLOSED,
        }
    ),
    LeaseState.APPLY_CHECKED: frozenset(
        {
            LeaseState.APPLY_CHECKED,
            LeaseState.INTEGRATED,
            LeaseState.REJECTED,
            LeaseState.CLOSED,
        }
    ),
    LeaseState.INTEGRATED: frozenset({LeaseState.INTEGRATED, LeaseState.CLOSED}),
    LeaseState.CLOSED: frozenset({LeaseState.CLOSED}),
    LeaseState.REJECTED: frozenset({LeaseState.REJECTED}),
}


def transition_lease_state(current: LeaseState, target: LeaseState) -> LeaseState:
    allowed = _ALLOWED_LEASE_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise ValidationIssue(
            "invalid_lease_transition",
            f"Cannot transition lease from `{current.value}` to `{target.value}`",
            path="lease.state",
            hint=f"Allowed from {current.value}: {', '.join(sorted(s.value for s in allowed))}",
        )
    return target

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
    revision: int = 0
    sandbox_profile: str = "devbox"
    qualification_digest: str | None = None
    audit_evidence_digest: str | None = None
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
            revision=int(data.get("revision") or 0),
            sandbox_profile=str(data.get("sandbox_profile") or "devbox"),
            qualification_digest=data.get("qualification_digest"),
            audit_evidence_digest=data.get("audit_evidence_digest"),
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


def normalize_repo_rel_path(rel_path: str) -> str:
    """Normalize a repository-relative path without stripping leading dots.

    ``str.lstrip("./")`` is unsafe: it treats the argument as a *set* of
    characters, so ``.elves/secret.json`` becomes ``elves/secret.json`` and can
    escape forbidden-prefix checks. Only strip explicit ``./`` segments.
    """
    if rel_path is None:
        raise ValidationIssue(
            "invalid_path",
            "Path is required",
            path="lease.path",
        )
    text = str(rel_path).replace("\\", "/").strip()
    if not text or text in {".", ".."}:
        raise ValidationIssue(
            "invalid_path",
            f"Path `{rel_path}` is empty or not repository-relative",
            path="lease.path",
        )
    if text.startswith("/") or re_is_absolute_drive(text):
        raise ValidationIssue(
            "absolute_path_forbidden",
            f"Absolute path `{rel_path}` is not allowed in a writer lease",
            path="lease.path",
        )
    while text.startswith("./"):
        text = text[2:]
    parts: list[str] = []
    for part in text.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise ValidationIssue(
                "path_escape",
                f"Path `{rel_path}` contains `..` segments",
                path="lease.path",
            )
        parts.append(part)
    if not parts:
        raise ValidationIssue(
            "invalid_path",
            f"Path `{rel_path}` resolves empty",
            path="lease.path",
        )
    return "/".join(parts)


def re_is_absolute_drive(text: str) -> bool:
    """True for Windows-style drive paths like ``C:/foo``."""
    return len(text) >= 2 and text[0].isalpha() and text[1] == ":"


def is_path_allowed(rel_path: str, lease: WriterLease) -> bool:
    """Return True if a repo-relative path is within lease scope.

    Empty allow-lists fail closed (nothing is permitted). Forbidden prefixes are
    checked against a safe normalization that preserves leading dots in names
    like ``.elves/``.
    """
    try:
        normalized = normalize_repo_rel_path(rel_path)
    except ValidationIssue:
        return False

    for prefix in lease.forbidden_path_prefixes:
        p = str(prefix).replace("\\", "/")
        while p.startswith("./"):
            p = p[2:]
        p_stripped = p.rstrip("/")
        if not p_stripped:
            continue
        if normalized == p_stripped or normalized.startswith(p_stripped + "/"):
            return False
        # Exact file forbids such as ``.elves-session.json``.
        if not p.endswith("/") and normalized == p:
            return False

    if not lease.allowed_paths:
        return False

    for allowed in lease.allowed_paths:
        try:
            a = normalize_repo_rel_path(allowed.rstrip("/")) if allowed not in {".", "./"} else ""
        except ValidationIssue:
            continue
        if allowed in {".", "./"} or a == "":
            # Explicit whole-repo allow only when non-forbidden.
            return True
        if normalized == a or normalized.startswith(a + "/"):
            return True
        if str(allowed).replace("\\", "/").endswith("/") and (
            normalized == a or normalized.startswith(a + "/")
        ):
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

    def __init__(self, repo_root: Path, *, create: bool = True) -> None:
        self.repo_root = Path(repo_root)
        self._create = create
        if create:
            self.root = ensure_leases_dir(self.repo_root)
        else:
            self.root = leases_root(self.repo_root)
        self.malformed_records: list[dict[str, str]] = []

    @classmethod
    def open_readonly(cls, repo_root: Path) -> "LeaseStore":
        return cls(Path(repo_root), create=False)

    def _path(self, lease_id: str) -> Path:
        return self.root / record_filename(lease_id, prefix="lease")

    def _legacy_path(self, lease_id: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in lease_id)
        return self.root / f"{safe}.json"

    def list_leases(self) -> list[WriterLease]:
        leases: list[WriterLease] = []
        self.malformed_records = []
        if not self.root.is_dir():
            return leases
        for path in sorted(self.root.glob("*.json")):
            if path.name in {"index.json", "store.lock"}:
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                lease = WriterLease.from_dict(data)
                assert_embedded_id(data, lease.lease_id, id_field="lease_id")
                leases.append(lease)
            except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError, StorageError) as exc:
                self.malformed_records.append(
                    {"path": str(path), "error": f"{type(exc).__name__}: {exc}"}
                )
        return leases

    def list_leases_strict(self) -> list[WriterLease]:
        leases = self.list_leases()
        if self.malformed_records:
            detail = "; ".join(
                f"{item['path']}: {item['error']}" for item in self.malformed_records
            )
            raise ValidationIssue(
                "lease_record_malformed",
                f"Malformed lease records block operations: {detail}",
                path=str(self.root),
            )
        return leases

    def live_leases(self) -> list[WriterLease]:
        return [lease for lease in self.list_leases_strict() if lease.state in LIVE_LEASE_STATES]

    def get(self, lease_id: str) -> WriterLease:
        path = self._path(lease_id)
        legacy = self._legacy_path(lease_id)
        chosen = path if path.is_file() else legacy if legacy.is_file() else None
        if chosen is None:
            raise ValidationIssue(
                "lease_not_found",
                f"No lease record for `{lease_id}`",
                path=str(path),
            )
        data = json.loads(chosen.read_text(encoding="utf-8"))
        try:
            assert_embedded_id(data, lease_id, id_field="lease_id")
        except StorageError as exc:
            raise ValidationIssue(
                "lease_embedded_id_mismatch",
                exc.message,
                path=str(chosen),
            ) from exc
        return WriterLease.from_dict(data)

    def save(self, lease: WriterLease, *, expected_revision: int | None = None) -> WriterLease:
        if not self._create:
            raise ValidationIssue(
                "lease_store_read_only",
                "Lease store was opened read-only; refusing mutation",
                path=str(self.root),
            )
        with directory_lock(self.root):
            path = self._path(lease.lease_id)
            if path.is_file():
                try:
                    current = json.loads(path.read_text(encoding="utf-8"))
                    current_rev = int(current.get("revision") or 0)
                except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                    raise ValidationIssue(
                        "lease_record_malformed",
                        f"Cannot CAS-save over unreadable lease: {exc}",
                        path=str(path),
                    ) from exc
                if expected_revision is not None and current_rev != int(expected_revision):
                    raise ValidationIssue(
                        "lease_revision_conflict",
                        f"Stale lease write: expected revision {expected_revision}, "
                        f"disk has {current_rev}",
                        path=str(path),
                    )
                if expected_revision is None and int(lease.revision or 0) not in {0, current_rev}:
                    if int(lease.revision or 0) < current_rev:
                        raise ValidationIssue(
                            "lease_revision_conflict",
                            f"Stale lease write: in-memory revision {lease.revision} "
                            f"< disk {current_rev}",
                            path=str(path),
                        )
            lease.revision = int(lease.revision or 0) + 1
            lease.updated_at = _utc_now()
            if not lease.created_at:
                lease.created_at = lease.updated_at
            atomic_write_json(path, lease.to_dict(), mode=stat.S_IRUSR | stat.S_IWUSR)
            return lease

    def snapshot_dir(self, lease_id: str) -> Path:
        return storage_snapshot_path(self.root, lease_id, kind="lease")

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
        qualification_evidence: Mapping[str, Any] | None = None,
    ) -> WriterLease:
        """Create the sole live lease after worker preflight and profile checks."""
        if not write_profile_qualified:
            raise ValidationIssue(
                "write_profile_unqualified",
                f"Write profile `{profile}` is not qualified for isolated write",
            )
        # Host-issued observed qualification is required (not optional).
        if qualification_evidence is None:
            raise ValidationIssue(
                "write_qualification_required",
                "Writer lease prepare requires host-issued qualification evidence",
                path="lease.qualification_evidence",
                hint="Pass --qualification-file or --qualification-id from host-owned evidence",
            )
        ok, reasons = qualify_write_evidence(qualification_evidence)
        if not ok:
            raise ValidationIssue(
                "write_qualification_failed",
                "Write qualification evidence failed closed: " + ", ".join(reasons),
                path="lease.qualification_evidence",
            )
        # Enforce exact registered session when provided by evidence.
        evidence_session = str(qualification_evidence.get("session_id") or session_id)
        if evidence_session != session_id:
            raise ValidationIssue(
                "write_qualification_session_mismatch",
                f"Qualification session `{evidence_session}` != lease session `{session_id}`",
            )
        if str(qualification_evidence.get("adapter") or "") not in {"", adapter}:
            if str(qualification_evidence.get("adapter")) != adapter:
                raise ValidationIssue(
                    "write_qualification_adapter_mismatch",
                    "Qualification adapter does not match lease adapter",
                )
        import hashlib

        qual_digest = hashlib.sha256(
            json.dumps(dict(qualification_evidence), sort_keys=True).encode("utf-8")
        ).hexdigest()
        if used_headless_worktree_resume and adapter == "grok-build":
            from .sessions import assert_grok_worktree_isolation

            assert_grok_worktree_isolation(
                version=grok_version,
                cwd_verified=True,
                worktree_registered=True,
                used_headless_worktree_resume=True,
            )

        with directory_lock(self.root):
            # Malformed records block exclusivity checks rather than disappearing.
            live = [
                lease
                for lease in self.list_leases_strict()
                if lease.state in LIVE_LEASE_STATES
            ]
            if live:
                raise ValidationIssue(
                    "lease_exclusivity",
                    f"A live writer lease already exists: {live[0].lease_id} ({live[0].state.value})",
                    hint="Only one external writer lease may be active at a time",
                )

            paths = list(allowed_paths or [])
            if not paths:
                raise ValidationIssue(
                    "empty_path_allowlist",
                    "Writer leases require a non-empty explicit allowed_paths list",
                    hint="Refuse broad empty allow-lists; name product paths the worker may touch",
                )
            for rel in paths:
                normalize_repo_rel_path(rel)

            pre = preflight_worker_checkout(
                worker_checkout=Path(worker_checkout),
                base_head=base_head,
                require_detached=require_detached,
                require_clean=True,
                require_registered=True,
            )
            digest = refs_digest(Path(worker_checkout))
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
                allowed_paths=paths,
                detached_commits_permitted=detached_commits_permitted and workspace_capable,
                require_detached=require_detached,
                state=LeaseState.PREPARED,
                pre_status=pre["status_porcelain"],
                pre_refs_digest=digest,
                notes=notes,
                worker_tip=pre["head"],
                workspace_sandbox_commit_capable=workspace_capable,
                qualification_digest=qual_digest,
            )
            if sandbox_profile == "workspace":
                lease.notes = (
                    lease.notes + "\nworkspace sandbox: commits not assumed capable; "
                    "detached_commits_permitted forced false"
                ).strip()
                lease.detached_commits_permitted = False
            lease.updated_at = _utc_now()
            lease.created_at = lease.updated_at
            path = self._path(lease.lease_id)
            atomic_write_json(path, lease.to_dict(), mode=stat.S_IRUSR | stat.S_IWUSR)
            return lease

    def activate(self, lease_id: str) -> WriterLease:
        lease = self.get(lease_id)
        lease.state = transition_lease_state(lease.state, LeaseState.ACTIVE)
        return self.save(lease)

    def mark_auditing(self, lease_id: str) -> WriterLease:
        lease = self.get(lease_id)
        lease.state = transition_lease_state(lease.state, LeaseState.AUDITING)
        return self.save(lease)

    def mark_audited_pass(
        self,
        lease_id: str,
        *,
        evidence: Mapping[str, Any] | None = None,
    ) -> WriterLease:
        """Record immutable audit success. Requires proof evidence; never bare promote."""
        if evidence is None or not isinstance(evidence, Mapping):
            raise ValidationIssue(
                "audit_evidence_required",
                "mark_audited_pass requires a complete audit evidence object",
                path=f"leases.{lease_id}",
            )
        if not evidence.get("ok"):
            raise ValidationIssue(
                "audit_evidence_not_ok",
                "Cannot promote to audited_pass when evidence.ok is not true",
            )
        required_fields = (
            "lease_id",
            "worker_tip",
            "base_head",
            "commit_chain",
            "evidence_digest",
        )
        missing = [f for f in required_fields if f not in evidence]
        if missing:
            raise ValidationIssue(
                "audit_evidence_incomplete",
                "Audit evidence missing fields: " + ", ".join(missing),
            )
        lease = self.get(lease_id)
        if str(evidence.get("lease_id")) != lease.lease_id:
            raise ValidationIssue(
                "audit_evidence_lease_mismatch",
                "Audit evidence lease_id does not match store lease",
            )
        lease.state = transition_lease_state(lease.state, LeaseState.AUDITED_PASS)
        lease.worker_tip = str(evidence.get("worker_tip") or lease.worker_tip)
        lease.audit_evidence_digest = str(evidence.get("evidence_digest"))
        # Persist evidence under store-owned snapshot path.
        evidence_dir = self.snapshot_dir(lease_id)
        evidence_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(evidence_dir / "audit_evidence.json", dict(evidence))
        return self.save(lease)

    def mark_exported(self, lease_id: str, patch_dir: str) -> WriterLease:
        lease = self.get(lease_id)
        # Export is legal only from AUDITED_PASS — never implicitly promote.
        if lease.state != LeaseState.AUDITED_PASS and lease.state != LeaseState.EXPORTED:
            raise ValidationIssue(
                "export_requires_audited_pass",
                f"Export refused from state `{lease.state.value}`; require audited_pass",
                path=f"leases.{lease_id}.state",
            )
        lease.state = transition_lease_state(lease.state, LeaseState.EXPORTED)
        lease.exported_patch_dir = patch_dir
        return self.save(lease)

    def mark_apply_checked(self, lease_id: str) -> WriterLease:
        lease = self.get(lease_id)
        lease.state = transition_lease_state(lease.state, LeaseState.APPLY_CHECKED)
        return self.save(lease)

    def mark_integrated(self, lease_id: str) -> WriterLease:
        lease = self.get(lease_id)
        # Allow exported -> apply_checked -> integrated, or direct exported when
        # host applied checks in the same step (recorded as APPLY_CHECKED first).
        if lease.state == LeaseState.EXPORTED:
            lease.state = transition_lease_state(lease.state, LeaseState.APPLY_CHECKED)
        lease.state = transition_lease_state(lease.state, LeaseState.INTEGRATED)
        return self.save(lease)

    def reject(self, lease_id: str, reason: str) -> WriterLease:
        lease = self.get(lease_id)
        lease.state = transition_lease_state(lease.state, LeaseState.REJECTED)
        lease.rejection_reason = reason
        return self.save(lease)

    def close(self, lease_id: str) -> WriterLease:
        lease = self.get(lease_id)
        # Closing is allowed from most non-rejected live/terminal success states.
        if lease.state == LeaseState.REJECTED:
            raise ValidationIssue(
                "invalid_lease_transition",
                "Rejected leases are terminal and cannot be closed into a success path",
            )
        if lease.state != LeaseState.CLOSED:
            # Prefer explicit close transition when allowed; otherwise go via integrate/reject.
            try:
                lease.state = transition_lease_state(lease.state, LeaseState.CLOSED)
            except ValidationIssue:
                # From exported/apply_checked, require integrate first.
                raise
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
