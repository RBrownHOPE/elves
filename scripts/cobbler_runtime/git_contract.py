"""Git contract checks for full runs (extracted from full_run.py, plan B7).

Origin identity, protected-ref snapshot/verification, ancestry, clean-worktree,
and head/branch helpers. Everything here goes through the canonical hardened
``run_git`` from :mod:`.leases`.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Mapping, Sequence
from urllib.parse import urlsplit

from .leases import run_git
from .schema import ValidationIssue

if TYPE_CHECKING:  # pragma: no cover — annotation-only; avoids a runtime cycle
    from .full_run import FullRunState


def _origin_present(repo_root: Path) -> bool:
    result = run_git(
        repo_root,
        ["remote", "get-url", "origin"],
        check=False,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def _canonical_origin_url(repo_root: Path) -> str:
    result = run_git(
        repo_root,
        ["remote", "get-url", "--all", "origin"],
        check=False,
    )
    urls = [row.strip() for row in result.stdout.splitlines() if row.strip()]
    if result.returncode != 0 or len(urls) != 1:
        raise ValidationIssue(
            "full_run_origin_required",
            "Production full-run requires exactly one canonical origin URL",
        )
    url = urls[0]
    if any(ord(ch) < 32 for ch in url) or re.search(r"https?://[^/\s]*@", url, re.I):
        raise ValidationIssue(
            "full_run_origin_url_credentialed",
            "Origin URL must be non-credentialed and free of control characters",
        )
    push = run_git(
        repo_root,
        ["remote", "get-url", "--push", "--all", "origin"],
        check=False,
    )
    push_urls = [row.strip() for row in push.stdout.splitlines() if row.strip()]
    if push.returncode != 0 or push_urls != [url]:
        raise ValidationIssue(
            "full_run_origin_push_url_mismatch",
            "Origin fetch and push URLs must resolve to the same canonical non-credentialed URL",
        )
    return url



def _origin_config_digest(repo_root: Path) -> str:
    result = run_git(
        repo_root,
        ["config", "--local", "--get-regexp", r"^remote\.origin\."],
        check=False,
    )
    if result.returncode not in {0, 1}:
        raise ValidationIssue(
            "full_run_origin_config_unavailable",
            (result.stderr or "Cannot inspect origin config").strip(),
        )
    return hashlib.sha256(result.stdout.encode("utf-8")).hexdigest()


def _remote_refs(
    repo_root: Path,
    *,
    patterns: Sequence[str] = (),
) -> dict[str, str]:
    if not _origin_present(repo_root):
        return {}
    try:
        result = run_git(
            repo_root,
            ["ls-remote", "origin", *patterns],
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValidationIssue(
            "full_run_remote_refs_unavailable",
            f"Cannot inspect origin refs safely: {exc}",
        ) from exc
    if result.returncode != 0:
        raise ValidationIssue(
            "full_run_remote_refs_unavailable",
            (result.stderr or result.stdout or "git ls-remote origin failed").strip(),
        )
    refs: dict[str, str] = {}
    for row in result.stdout.splitlines():
        parts = row.split(None, 1)
        if len(parts) == 2:
            refs[f"remote::origin::{parts[1].strip()}"] = parts[0].strip()
    return refs


def _github_provider_managed_ref(origin_url: str, ref_name: str) -> bool:
    """GitHub owns refs/pull/*; feature pushes legitimately regenerate them."""
    raw = str(origin_url or "").strip()
    host = ""
    if "://" in raw:
        host = str(urlsplit(raw).hostname or "")
    else:
        # Git's SCP-style transport: [user@]host:path. Absolute/relative local
        # paths and file:// URLs must never gain provider-managed exemptions.
        match = re.fullmatch(r"(?:[^@/\s]+@)?(?P<host>[^:/\s]+):.+", raw)
        if match:
            host = match.group("host")
    github_origin = host.rstrip(".").lower() == "github.com"
    return github_origin and ref_name.startswith("refs/pull/")


def _host_ephemeral_ref(ref_name: str) -> bool:
    """Return refs maintained by the host UI, outside worker authority.

    Codex turn-diff refs are local, ephemeral snapshots created by the host
    application while a task is active. They are neither worker progress nor a
    protected repository ref, and treating their ordinary creation as worker
    tampering produces false safety wakes.
    """
    return ref_name.startswith("refs/codex/turn-diffs/")


def snapshot_protected_refs(
    repo_root: Path,
    *,
    feature_branch: str | None = None,
    include_remote: bool = True,
) -> dict[str, str]:
    """Snapshot every ref namespace except the exact assigned feature refs.

    When ``include_remote`` is False, skip the uncached remote all-ref audit
    (used by incremental healthy polls). Terminal/safety paths always pass True.
    """
    result = run_git(
        repo_root,
        [
            "for-each-ref",
            "--format=%(refname) %(objectname)",
            "refs",
        ],
        check=False,
    )
    if result.returncode != 0:
        raise ValidationIssue(
            "full_run_ref_snapshot_failed",
            (result.stderr or result.stdout or "git for-each-ref failed").strip(),
        )
    excluded = set()
    if feature_branch:
        excluded = {
            f"refs/heads/{feature_branch}",
            f"refs/remotes/origin/{feature_branch}",
        }
    snaps: dict[str, str] = {}
    for row in result.stdout.splitlines():
        parts = row.split(None, 1)
        if (
            len(parts) == 2
            and parts[0] not in excluded
            and not _host_ephemeral_ref(parts[0])
        ):
            snaps[parts[0]] = parts[1]
    if include_remote:
        remote = _remote_refs(repo_root)
        origin_url = _canonical_origin_url(repo_root) if remote else ""
        feature_remote = (
            f"remote::origin::refs/heads/{feature_branch}" if feature_branch else None
        )
        for key, value in remote.items():
            remote_ref = key.removeprefix("remote::origin::")
            if key != feature_remote and not _github_provider_managed_ref(
                origin_url, remote_ref
            ):
                snaps[key] = value
    return snaps


def verify_protected_refs_unchanged(
    repo_root: Path,
    expected: Mapping[str, str],
    *,
    feature_branch: str | None = None,
    include_remote: bool = True,
) -> list[str]:
    """Any observed protected-ref movement blocks readiness (policy trust, not OS sandbox)."""
    errors: list[str] = []
    current = snapshot_protected_refs(
        repo_root, feature_branch=feature_branch, include_remote=include_remote
    )
    expected_now = {
        ref: tip
        for ref, tip in expected.items()
        if include_remote or not ref.startswith("remote::")
    }
    for ref in sorted(set(current) - set(expected_now)):
        errors.append(f"new protected ref created: {ref}")
    for ref, tip in expected_now.items():
        now = current.get(ref)
        if now is None:
            errors.append(f"protected ref missing at finalization: {ref}")
        elif now != tip:
            errors.append(f"protected ref moved: {ref} was {tip[:12]} now {now[:12]}")
    return errors


def _git_head(cwd: Path) -> str | None:
    try:
        result = run_git(
            cwd,
            ["rev-parse", "HEAD"],
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    tip = (result.stdout or "").strip()
    return tip or None


def _git_branch(cwd: Path) -> str | None:
    try:
        result = run_git(
            cwd,
            ["branch", "--show-current"],
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None


def _is_ancestor(cwd: Path, ancestor: str, tip: str) -> bool:
    try:
        result = run_git(
            cwd,
            ["merge-base", "--is-ancestor", ancestor, tip],
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0


def _git_common_dir(cwd: Path) -> Path | None:
    result = run_git(
        cwd,
        ["rev-parse", "--git-common-dir"],
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    path = Path(result.stdout.strip())
    if not path.is_absolute():
        path = Path(cwd) / path
    return path.resolve()


def _assert_clean_worktree(worktree: Path) -> None:
    result = run_git(
        worktree,
        [
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ],
        check=False,
    )
    if result.returncode != 0:
        raise ValidationIssue(
            "full_run_worktree_status_failed",
            (result.stderr or result.stdout or "git status failed").strip(),
        )
    dirty_rows = []
    for row in result.stdout.splitlines():
        path_text = row[3:].strip() if len(row) >= 4 else row.strip()
        # Host-owned ignored runtime state is allowed even in repos whose local
        # ignore file has not yet been conditioned. Product/untracked files are not.
        if path_text.startswith(".elves/runtime/"):
            continue
        dirty_rows.append(row)
    if dirty_rows:
        raise ValidationIssue(
            "full_run_worktree_dirty",
            "Production full-run requires a clean tracked and untracked worktree",
            hint="\n".join(dirty_rows)[:500],
        )


def _feature_remote_tip(repo_root: Path, branch: str) -> str | None:
    key = f"remote::origin::refs/heads/{branch}"
    return _remote_refs(repo_root, patterns=[f"refs/heads/{branch}"]).get(key)


def _assert_origin_binding(
    repo_root: Path,
    state: FullRunState,
    *,
    expected_feature_tip: str | None,
) -> str | None:
    url = _canonical_origin_url(repo_root)
    digest = _origin_config_digest(repo_root)
    if state.origin_url != url or state.origin_config_digest != digest:
        raise ValidationIssue(
            "full_run_origin_binding_changed",
            "Origin URL/config changed after full-run preparation",
        )
    remote_tip = _feature_remote_tip(repo_root, state.branch)
    if remote_tip != expected_feature_tip:
        raise ValidationIssue(
            "full_run_remote_feature_mismatch",
            "Origin feature tip does not equal the required safe checkpoint",
        )
    return remote_tip


