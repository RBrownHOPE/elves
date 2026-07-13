from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"


def _ensure_import_path() -> None:
    scripts = str(SCRIPTS)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)


_ensure_import_path()

from cobbler_runtime.adapters import (  # noqa: E402
    build_write_resume_invocation,
    grok_write_profile,
    workspace_sandbox_write_profile,
)
from cobbler_runtime.audit import (  # noqa: E402
    audit_lease_turn,
    export_binary_patches,
    host_apply_check,
    list_commit_chain,
    pre_turn_snapshots,
)
from cobbler_runtime.leases import (  # noqa: E402
    host_qualification_evidence,
    LeaseState,
    LeaseStore,
    WriterLease,
    build_write_task_packet,
    is_path_allowed,
    preflight_worker_checkout,
)
from cobbler_runtime.schema import ValidationIssue  # noqa: E402


def _run(cwd: Path, args: list[str]) -> None:
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True, text=True)


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _init_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    _run(path, ["git", "init"])
    _run(path, ["git", "config", "user.email", "lease@example.test"])
    _run(path, ["git", "config", "user.name", "Lease Test"])
    (path / ".gitignore").write_text(".elves/\n", encoding="utf-8")
    (path / "README.md").write_text("base\n", encoding="utf-8")
    (path / "src").mkdir()
    (path / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    _run(path, ["git", "add", "-A"])
    _run(path, ["git", "commit", "-m", "base"])
    return _git(path, "rev-parse", "HEAD")


def _detached_worktree(main: Path, worktree: Path, head: str) -> None:
    _run(main, ["git", "worktree", "add", "--detach", str(worktree), head])



def _register_session(
    host: Path,
    session_id: str,
    *,
    worker: Path,
    head: str,
    adapter: str = "grok-build",
    profile: str | None = None,
) -> None:
    """Register an exact session so lease qualification can require it."""
    from cobbler_runtime.sessions import SessionRecord, SessionRegistry

    reg = SessionRegistry(host)
    rec = SessionRecord(
        session_id=session_id,
        harness=adapter,
        profile=profile or ("grok-build-write" if adapter == "grok-build" else adapter),
        role="implementer",
        actual_model="grok-4.5",
        requested_model="grok-4.5",
        cwd=str(Path(worker).resolve()),
        worktree=str(Path(worker).resolve()),
        parent_id="host-parent",
        source_head=head,
    )
    try:
        reg.save(rec)
        reg.activate(session_id)
    except Exception:
        # Idempotent for tests that register twice.
        try:
            existing = reg.get(session_id)
            if existing.lifecycle.value == "new":
                reg.activate(session_id)
        except Exception:
            raise


def _qual(
    worker,
    head,
    session_id="sess",
    adapter="grok-build",
    sandbox="devbox",
    profile=None,
    version="0.2.93",
    **overrides,
):
    evidence = host_qualification_evidence(
        adapter=adapter,
        model="grok-4.5",
        profile=profile or ("grok-build-write" if adapter == "grok-build" else adapter),
        version=version,
        sandbox=sandbox,
        worktree=str(Path(worker).resolve()),
        cwd=str(Path(worker).resolve()),
        parent="host-parent",
        source_head=head,
        session_id=session_id,
    )
    evidence.update(overrides)
    return evidence


class PathScopeTests(unittest.TestCase):
    def test_forbidden_and_allowed_paths(self) -> None:
        from cobbler_runtime.leases import WriterLease, normalize_repo_rel_path

        lease = WriterLease(
            lease_id="L",
            host_checkout="/host",
            worker_checkout="/worker",
            session_id="s",
            base_head="h",
            adapter="grok-build",
            profile="p",
            allowed_paths=["src/", "scripts/"],
        )
        self.assertTrue(is_path_allowed("src/app.py", lease))
        self.assertTrue(is_path_allowed("./scripts/x.py", lease))
        self.assertFalse(is_path_allowed(".elves/session.json", lease))
        self.assertFalse(is_path_allowed(".elves/secret.json", lease))
        self.assertFalse(is_path_allowed(".elves-session.json", lease))
        self.assertFalse(is_path_allowed("docs/elves/log.md", lease))
        self.assertFalse(is_path_allowed("other/x.py", lease))
        self.assertFalse(is_path_allowed("../escape.py", lease))
        self.assertFalse(is_path_allowed("/abs/path.py", lease))
        # Empty allow-list fails closed.
        empty = WriterLease(
            lease_id="E",
            host_checkout="/host",
            worker_checkout="/worker",
            session_id="s",
            base_head="h",
            adapter="grok-build",
            profile="p",
            allowed_paths=[],
        )
        self.assertFalse(is_path_allowed("src/app.py", empty))
        self.assertFalse(is_path_allowed(".elves/secret.json", empty))
        # lstrip("./") would have turned this into elves/ and escaped; must stay blocked.
        sneaky = WriterLease(
            lease_id="S",
            host_checkout="/host",
            worker_checkout="/worker",
            session_id="s",
            base_head="h",
            adapter="grok-build",
            profile="p",
            allowed_paths=["elves/"],
        )
        self.assertFalse(is_path_allowed(".elves/secret.json", sneaky))
        self.assertEqual(normalize_repo_rel_path(".elves/secret.json"), ".elves/secret.json")
        self.assertEqual(normalize_repo_rel_path("./src/app.py"), "src/app.py")


class LeaseExclusivityTests(unittest.TestCase):
    def test_host_and_worker_checkout_must_be_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            host = Path(tmp) / "host"
            head = _init_repo(host)
            with self.assertRaises(ValidationIssue) as ctx:
                LeaseStore(host).prepare(
                    lease_id="same-checkout",
                    host_checkout=host,
                    worker_checkout=host,
                    session_id="session",
                    base_head=head,
                    adapter="grok-build",
                    profile="grok-build-write",
                    allowed_paths=["src/"],
                    qualification_evidence={},
                )
            self.assertEqual(ctx.exception.code, "worker_checkout_not_isolated")

    def test_second_live_lease_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = root / "host"
            worker = root / "worker"
            head = _init_repo(host)
            _detached_worktree(host, worker, head)
            store = LeaseStore(host)
            _register_session(host, "sess-1", worker=worker, head=head)
            store.prepare(
                lease_id="lease-1",
                host_checkout=host,
                worker_checkout=worker,
                session_id="sess-1",
                base_head=head,
                adapter="grok-build",
                profile="grok-build-write",
                allowed_paths=["src/"],
                qualification_evidence=_qual(worker, head, session_id="sess-1", adapter="grok-build"),
            )
            with self.assertRaises(ValidationIssue) as ctx:
                _register_session(host, "sess-2", worker=worker, head=head)
                store.prepare(
                    lease_id="lease-2",
                    host_checkout=host,
                    worker_checkout=worker,
                    session_id="sess-2",
                    base_head=head,
                    adapter="grok-build",
                    profile="grok-build-write",
                qualification_evidence=_qual(worker, head, session_id="sess-2", adapter="grok-build"),
                )
            self.assertEqual(ctx.exception.code, "lease_exclusivity")


class QualificationIdentityTests(unittest.TestCase):
    def test_registered_identity_mismatches_and_stale_evidence_fail_closed(self) -> None:
        cases = {
            "model": "other-model",
            "profile": "other-profile",
            "parent": "other-parent",
            "source_head": "0" * 40,
            "cwd": "/tmp/not-the-worker",
            "worktree": "/tmp/not-the-worker",
            "version": "0.0.0",
            "preference_declared": True,
            "observed_at": (
                datetime.now(timezone.utc) - timedelta(hours=1)
            ).isoformat(),
        }
        for field_name, bad_value in cases.items():
            with self.subTest(field=field_name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                host = root / "host"
                worker = root / "worker"
                head = _init_repo(host)
                _detached_worktree(host, worker, head)
                _register_session(host, "sess", worker=worker, head=head)
                evidence = _qual(worker, head, **{field_name: bad_value})
                with self.assertRaises(ValidationIssue):
                    LeaseStore(host).prepare(
                        lease_id=f"lease-{field_name}",
                        host_checkout=host,
                        worker_checkout=worker,
                        session_id="sess",
                        base_head=head,
                        adapter="grok-build",
                        profile="grok-build-write",
                        allowed_paths=["src/"],
                        grok_version="0.2.93",
                        qualification_evidence=evidence,
                    )

    def test_dirty_and_branch_attached_and_head_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = root / "host"
            worker = root / "worker"
            head = _init_repo(host)
            _detached_worktree(host, worker, head)

            # Dirty
            (worker / "src" / "app.py").write_text("dirty\n", encoding="utf-8")
            with self.assertRaises(ValidationIssue) as ctx:
                preflight_worker_checkout(
                    worker_checkout=worker,
                    base_head=head,
                )
            self.assertEqual(ctx.exception.code, "worker_dirty")

            # Reset dirty via checkout of file
            _run(worker, ["git", "checkout", "--", "src/app.py"])

            # HEAD mismatch
            with self.assertRaises(ValidationIssue) as ctx:
                preflight_worker_checkout(
                    worker_checkout=worker,
                    base_head="0" * 40,
                )
            self.assertEqual(ctx.exception.code, "worker_head_mismatch")

            # Branch-attached main checkout fails detached requirement
            with self.assertRaises(ValidationIssue) as ctx:
                preflight_worker_checkout(
                    worker_checkout=host,
                    base_head=head,
                    require_detached=True,
                    require_registered=True,
                )
            self.assertEqual(ctx.exception.code, "worker_not_detached")

    def test_write_packet_denies_push_pr_run_memory(self) -> None:
        lease = LeaseStore.__new__(LeaseStore)  # unused
        from cobbler_runtime.leases import WriterLease

        wl = WriterLease(
            lease_id="L",
            host_checkout="/h",
            worker_checkout="/w",
            session_id="s",
            base_head="abc",
            adapter="grok-build",
            profile="p",
        )
        packet = build_write_task_packet(wl, task="implement batch")
        self.assertTrue(packet["denials"]["push"])
        self.assertTrue(packet["denials"]["pr"])
        self.assertTrue(packet["denials"]["run_memory"])
        self.assertTrue(packet["denials"]["branch"])
        self.assertIn("detached_commits_permitted", packet)


class AuditAndPatchTests(unittest.TestCase):
    def test_untrusted_audit_rejects_in_repo_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = root / "host"
            worker = root / "worker"
            head = _init_repo(host)
            _detached_worktree(host, worker, head)
            store = LeaseStore(host)
            _register_session(host, "sess", worker=worker, head=head)
            lease = store.prepare(
                lease_id="lease-symlink",
                host_checkout=host,
                worker_checkout=worker,
                session_id="sess",
                base_head=head,
                adapter="grok-build",
                profile="grok-build-write",
                allowed_paths=["src/"],
                qualification_evidence=_qual(worker, head),
            )
            store.activate(lease.lease_id)
            (worker / "src" / "link.py").symlink_to("app.py")
            _run(worker, ["git", "add", "--", "src/link.py"])
            _run(worker, ["git", "commit", "-m", "add in-repo symlink", "--", "src/link.py"])
            audit = audit_lease_turn(store.get(lease.lease_id))
            self.assertFalse(audit.ok)
            self.assertEqual(audit.symlink_escapes, ["src/link.py"])
            self.assertTrue(any("symlink paths are forbidden" in reason for reason in audit.reasons))

    def test_lease_revision_zero_cannot_overwrite_existing_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = root / "host"
            worker = root / "worker"
            head = _init_repo(host)
            _detached_worktree(host, worker, head)
            store = LeaseStore(host)
            _register_session(host, "sess", worker=worker, head=head)
            lease = store.prepare(
                lease_id="lease-cas-zero",
                host_checkout=host,
                worker_checkout=worker,
                session_id="sess",
                base_head=head,
                adapter="grok-build",
                profile="grok-build-write",
                allowed_paths=["src/"],
                qualification_evidence=_qual(worker, head),
            )
            stale = WriterLease.from_dict(lease.to_dict())
            stale.revision = 0
            with self.assertRaises(ValidationIssue) as ctx:
                store.save(stale)
            self.assertEqual(ctx.exception.code, "lease_revision_conflict")

            stale = WriterLease.from_dict(lease.to_dict())
            stale.revision = 0
            with self.assertRaises(ValidationIssue) as ctx:
                store.save(stale, expected_revision=lease.revision)
            self.assertEqual(ctx.exception.code, "lease_revision_conflict")

    def test_terminal_lease_id_is_immutable_and_cannot_be_reused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = root / "host"
            worker = root / "worker"
            head = _init_repo(host)
            _detached_worktree(host, worker, head)
            store = LeaseStore(host)
            _register_session(host, "sess", worker=worker, head=head)
            kwargs = dict(
                lease_id="lease-terminal-id",
                host_checkout=host,
                worker_checkout=worker,
                session_id="sess",
                base_head=head,
                adapter="grok-build",
                profile="grok-build-write",
                allowed_paths=["src/"],
                qualification_evidence=_qual(worker, head),
            )
            store.prepare(**kwargs)
            store.close("lease-terminal-id")
            with self.assertRaises(ValidationIssue) as ctx:
                store.prepare(**kwargs)
            self.assertEqual(ctx.exception.code, "lease_id_immutable")

    def test_lease_prepare_rejects_active_session_with_pending_rehydration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = root / "host"
            worker = root / "worker"
            head = _init_repo(host)
            _detached_worktree(host, worker, head)
            _register_session(host, "sess", worker=worker, head=head)
            from cobbler_runtime.sessions import SessionRegistry

            registry = SessionRegistry(host)
            session = registry.get("sess")
            session.pending_context_digest = "pending"
            registry.save(session)
            with self.assertRaises(ValidationIssue) as ctx:
                LeaseStore(host).prepare(
                    lease_id="lease-pending",
                    host_checkout=host,
                    worker_checkout=worker,
                    session_id="sess",
                    base_head=head,
                    adapter="grok-build",
                    profile="grok-build-write",
                    allowed_paths=["src/"],
                    qualification_evidence=_qual(worker, head),
                )
            self.assertEqual(ctx.exception.code, "write_qualification_session_blocked")

    def test_allowed_detached_chain_export_and_apply_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = root / "host"
            worker = root / "worker"
            head = _init_repo(host)
            _detached_worktree(host, worker, head)
            store = LeaseStore(host)
            _register_session(host, "sess", worker=worker, head=head)
            lease = store.prepare(
                lease_id="lease-ok",
                host_checkout=host,
                worker_checkout=worker,
                session_id="sess",
                base_head=head,
                adapter="grok-build",
                profile="grok-build-write",
                allowed_paths=["src/"],
                qualification_evidence=_qual(worker, head, session_id="sess", adapter="grok-build"),
            )
            store.activate("lease-ok")
            pre = pre_turn_snapshots(worker)

            (worker / "src" / "app.py").write_text("print('v2')\n", encoding="utf-8")
            _run(worker, ["git", "add", "--", "src/app.py"])
            _run(
                worker,
                [
                    "git",
                    "commit",
                    "-m",
                    "[grok-worker · Batch 4/6 · Implement] Update app",
                    "--",
                    "src/app.py",
                ],
            )
            (worker / "src" / "util.py").write_text("x=1\n", encoding="utf-8")
            _run(worker, ["git", "add", "--", "src/util.py"])
            _run(
                worker,
                [
                    "git",
                    "commit",
                    "-m",
                    "[grok-worker · Batch 4/6 · Implement] Add util",
                    "--",
                    "src/util.py",
                ],
            )

            lease = store.get("lease-ok")
            audit = audit_lease_turn(
                lease,
                pre_refs_digest=pre["refs_digest"],
                pre_remotes=pre["remotes"],
                pre_config=pre["config"],
                pre_hooks=pre["hooks"],
                process_baseline=["pid-1"],
                process_observed=["pid-1"],
            )
            self.assertTrue(audit.ok, audit.reasons)
            self.assertEqual(len(audit.commit_chain), 2)
            self.assertEqual(audit.commit_chain[0].parents[0], head)

            store.mark_auditing("lease-ok")
            store.mark_audited_pass("lease-ok", evidence={"ok": True, "lease_id": "lease-ok", "worker_tip": audit.worker_tip, "base_head": head, "commit_chain": [c.to_dict() for c in audit.commit_chain], "evidence_digest": "test-digest"})
            lease = store.get("lease-ok")
            patch_dir = root / "patches"
            patches = export_binary_patches(
                lease,
                output_dir=patch_dir,
                chain=audit.commit_chain,
                audit_evidence=audit.to_dict(),
            )
            self.assertGreaterEqual(len(patches), 2)
            self.assertTrue((patch_dir / "chain.json").is_file())

            # Host still at base; apply-check should pass without committing.
            checked = host_apply_check(host, patches, base_head=head)
            self.assertTrue(checked["ok"])
            self.assertEqual(_git(host, "rev-parse", "HEAD"), head)
            self.assertEqual(_git(host, "status", "--porcelain"), "")

    def test_out_of_scope_elves_path_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = root / "host"
            worker = root / "worker"
            head = _init_repo(host)
            _detached_worktree(host, worker, head)
            store = LeaseStore(host)
            _register_session(host, "sess", worker=worker, head=head)
            lease = store.prepare(
                lease_id="lease-bad-path",
                host_checkout=host,
                worker_checkout=worker,
                session_id="sess",
                base_head=head,
                adapter="grok-build",
                profile="grok-build-write",
                allowed_paths=["src/"],
                qualification_evidence=_qual(worker, head, session_id="sess", adapter="grok-build"),
            )
            store.activate(lease.lease_id)
            elves = worker / ".elves"
            elves.mkdir()
            (elves / "secret.json").write_text("{}\n", encoding="utf-8")
            _run(worker, ["git", "add", "-f", "--", ".elves/secret.json"])
            _run(worker, ["git", "commit", "-m", "bad elves edit", "--", ".elves/secret.json"])
            audit = audit_lease_turn(store.get(lease.lease_id))
            self.assertFalse(audit.ok)
            self.assertTrue(any("out-of-scope" in r for r in audit.reasons))

    def test_unexpected_merge_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = root / "host"
            worker = root / "worker"
            head = _init_repo(host)
            _detached_worktree(host, worker, head)
            store = LeaseStore(host)
            _register_session(host, "sess", worker=worker, head=head)
            lease = store.prepare(
                lease_id="lease-merge",
                host_checkout=host,
                worker_checkout=worker,
                session_id="sess",
                base_head=head,
                adapter="grok-build",
                profile="grok-build-write",
                allowed_paths=["src/"],
                qualification_evidence=_qual(worker, head, session_id="sess", adapter="grok-build"),
            )
            # Create two divergent commits and merge
            _run(worker, ["git", "checkout", "-b", "side-a"])
            (worker / "src" / "a.py").write_text("a\n", encoding="utf-8")
            _run(worker, ["git", "add", "--", "src/a.py"])
            _run(worker, ["git", "commit", "-m", "a", "--", "src/a.py"])
            _run(worker, ["git", "checkout", "-b", "side-b", head])
            (worker / "src" / "b.py").write_text("b\n", encoding="utf-8")
            _run(worker, ["git", "add", "--", "src/b.py"])
            _run(worker, ["git", "commit", "-m", "b", "--", "src/b.py"])
            _run(worker, ["git", "merge", "--no-ff", "side-a", "-m", "merge sides"])
            # Detach at merge tip for audit
            tip = _git(worker, "rev-parse", "HEAD")
            _run(worker, ["git", "checkout", "--detach", tip])
            # Update lease base is still original head - chain will fail merge parent
            lease = store.get(lease.lease_id)
            lease.base_head = head
            store.save(lease)
            with self.assertRaises(ValidationIssue) as ctx:
                list_commit_chain(worker, head, tip)
            self.assertIn(ctx.exception.code, {"unexpected_merge_or_root", "commit_chain_not_descendant", "chain_parent_mismatch"})

    def test_new_ref_fails_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = root / "host"
            worker = root / "worker"
            head = _init_repo(host)
            _detached_worktree(host, worker, head)
            store = LeaseStore(host)
            _register_session(host, "sess", worker=worker, head=head)
            lease = store.prepare(
                lease_id="lease-ref",
                host_checkout=host,
                worker_checkout=worker,
                session_id="sess",
                base_head=head,
                adapter="grok-build",
                profile="grok-build-write",
                allowed_paths=["src/"],
                qualification_evidence=_qual(worker, head, session_id="sess", adapter="grok-build"),
            )
            pre = pre_turn_snapshots(worker)
            (worker / "src" / "app.py").write_text("v\n", encoding="utf-8")
            _run(worker, ["git", "add", "--", "src/app.py"])
            _run(worker, ["git", "commit", "-m", "ok", "--", "src/app.py"])
            _run(worker, ["git", "tag", "evil-tag"])
            audit = audit_lease_turn(
                store.get(lease.lease_id),
                pre_refs_digest=pre["refs_digest"],
            )
            self.assertFalse(audit.ok)
            self.assertTrue(audit.refs_changed)

    def test_push_attempt_and_process_leak(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = root / "host"
            worker = root / "worker"
            head = _init_repo(host)
            _detached_worktree(host, worker, head)
            store = LeaseStore(host)
            _register_session(host, "sess", worker=worker, head=head)
            lease = store.prepare(
                lease_id="lease-push",
                host_checkout=host,
                worker_checkout=worker,
                session_id="sess",
                base_head=head,
                adapter="grok-build",
                profile="grok-build-write",
                allowed_paths=["src/"],
                qualification_evidence=_qual(worker, head, session_id="sess", adapter="grok-build"),
            )
            audit = audit_lease_turn(
                store.get(lease.lease_id),
                observed_commands=["git push origin HEAD"],
                process_baseline=["worker-main"],
                process_observed=["worker-main", "lingering-paid-job"],
            )
            self.assertFalse(audit.ok)
            self.assertTrue(any("forbidden" in r for r in audit.reasons))
            self.assertTrue(any("process leak" in r for r in audit.reasons))

    def test_dirty_index_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = root / "host"
            worker = root / "worker"
            head = _init_repo(host)
            _detached_worktree(host, worker, head)
            store = LeaseStore(host)
            _register_session(host, "sess", worker=worker, head=head)
            lease = store.prepare(
                lease_id="lease-dirty",
                host_checkout=host,
                worker_checkout=worker,
                session_id="sess",
                base_head=head,
                adapter="grok-build",
                profile="grok-build-write",
                allowed_paths=["src/"],
                qualification_evidence=_qual(worker, head, session_id="sess", adapter="grok-build"),
            )
            (worker / "src" / "app.py").write_text("staged?\n", encoding="utf-8")
            _run(worker, ["git", "add", "--", "src/app.py"])
            audit = audit_lease_turn(store.get(lease.lease_id))
            self.assertFalse(audit.ok)
            self.assertTrue(audit.staged)

    def test_workspace_sandbox_rejects_commits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = root / "host"
            worker = root / "worker"
            head = _init_repo(host)
            _detached_worktree(host, worker, head)
            store = LeaseStore(host)
            _register_session(host, "sess", worker=worker, head=head, profile="workspace")
            # Unsupported sandbox_profile cannot qualify or enable detached commits.
            with self.assertRaises(ValidationIssue) as ctx:
                store.prepare(
                    lease_id="lease-workspace",
                    host_checkout=host,
                    worker_checkout=worker,
                    session_id="sess",
                    base_head=head,
                    adapter="grok-build",
                    profile="workspace",
                    sandbox_profile="workspace",
                    allowed_paths=["src/"],
                    qualification_evidence=_qual(
                        worker,
                        head,
                        session_id="sess",
                        adapter="grok-build",
                        sandbox="workspace",
                        profile="workspace",
                    ),
                )
            self.assertIn("sandbox", ctx.exception.message.lower())
            # Evidence/profile mismatch fails closed.
            with self.assertRaises(ValidationIssue):
                store.prepare(
                    lease_id="lease-workspace-mismatch",
                    host_checkout=host,
                    worker_checkout=worker,
                    session_id="sess",
                    base_head=head,
                    adapter="grok-build",
                    profile="workspace",
                    sandbox_profile="workspace",
                    allowed_paths=["src/"],
                    qualification_evidence=_qual(
                        worker,
                        head,
                        session_id="sess",
                        adapter="grok-build",
                        sandbox="devbox",
                        profile="workspace",
                    ),
                )
            # Explicit detached_commits_permitted=False with supported sandbox.
            lease = store.prepare(
                lease_id="lease-no-detach",
                host_checkout=host,
                worker_checkout=worker,
                session_id="sess",
                base_head=head,
                adapter="grok-build",
                profile="workspace",
                sandbox_profile="devbox",
                allowed_paths=["src/"],
                detached_commits_permitted=False,
                qualification_evidence=_qual(
                    worker,
                    head,
                    session_id="sess",
                    adapter="grok-build",
                    sandbox="devbox",
                    profile="workspace",
                ),
            )
            self.assertFalse(lease.detached_commits_permitted)
            store.activate(lease.lease_id)
            (worker / "src" / "app.py").write_text("x\n", encoding="utf-8")
            _run(worker, ["git", "add", "--", "src/app.py"])
            _run(worker, ["git", "commit", "-m", "should fail policy", "--", "src/app.py"])
            audit = audit_lease_turn(store.get(lease.lease_id))
            self.assertFalse(audit.ok)
            self.assertTrue(any("detached_commits_permitted" in r for r in audit.reasons))

    def test_cleanup_refresh_after_integration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = root / "host"
            worker = root / "worker"
            head = _init_repo(host)
            _detached_worktree(host, worker, head)
            store = LeaseStore(host)
            _register_session(host, "sess", worker=worker, head=head)
            lease = store.prepare(
                lease_id="lease-refresh",
                host_checkout=host,
                worker_checkout=worker,
                session_id="sess",
                base_head=head,
                adapter="grok-build",
                profile="grok-build-write",
                allowed_paths=["src/"],
                qualification_evidence=_qual(worker, head, session_id="sess", adapter="grok-build"),
            )
            store.activate(lease.lease_id)
            # Worker produces the audited tree; host independently imports the
            # same tree before integration can be claimed.
            (worker / "src" / "app.py").write_text("host v2\n", encoding="utf-8")
            _run(worker, ["git", "add", "--", "src/app.py"])
            _run(worker, ["git", "commit", "-m", "worker change", "--", "src/app.py"])
            worker_tip = _git(worker, "rev-parse", "HEAD")
            (host / "src" / "app.py").write_text("host v2\n", encoding="utf-8")
            _run(host, ["git", "add", "--", "src/app.py"])
            _run(host, ["git", "commit", "-m", "host integrate", "--", "src/app.py"])
            new_tip = _git(host, "rev-parse", "HEAD")
            store.mark_auditing(lease.lease_id)
            store.mark_audited_pass(lease.lease_id, evidence={"ok": True, "lease_id": lease.lease_id, "worker_tip": worker_tip, "base_head": head, "commit_chain": [], "evidence_digest": "test-digest"})
            store.mark_exported(lease.lease_id, str(root / "patches"))
            with self.assertRaises(ValidationIssue) as ctx:
                store.mark_integrated(lease.lease_id, new_tip=new_tip)
            self.assertEqual(ctx.exception.code, "integrate_requires_apply_checked")
            store.mark_apply_checked(lease.lease_id)
            (worker / "src" / "app.py").write_text("dirty\n", encoding="utf-8")
            with self.assertRaises(ValidationIssue):
                store.mark_integrated(lease.lease_id, new_tip=new_tip)
            self.assertEqual(store.get(lease.lease_id).state, LeaseState.APPLY_CHECKED)
            _run(worker, ["git", "checkout", "--", "src/app.py"])
            store.mark_integrated(lease.lease_id, new_tip=new_tip)
            result = store.refresh_worker_to_tip(lease.lease_id, new_tip=new_tip)
            self.assertEqual(result["worker_tip"], new_tip)
            self.assertEqual(_git(worker, "rev-parse", "HEAD"), new_tip)

    def test_mark_integrated_rejects_host_tree_that_differs_from_audited_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = root / "host"
            worker = root / "worker"
            head = _init_repo(host)
            _detached_worktree(host, worker, head)
            store = LeaseStore(host)
            _register_session(host, "sess", worker=worker, head=head)
            lease = store.prepare(
                lease_id="lease-tree-mismatch",
                host_checkout=host,
                worker_checkout=worker,
                session_id="sess",
                base_head=head,
                adapter="grok-build",
                profile="grok-build-write",
                allowed_paths=["src/"],
                qualification_evidence=_qual(worker, head),
            )
            store.activate(lease.lease_id)
            (worker / "src" / "app.py").write_text("worker tree\n", encoding="utf-8")
            _run(worker, ["git", "add", "--", "src/app.py"])
            _run(worker, ["git", "commit", "-m", "worker tree", "--", "src/app.py"])
            worker_tip = _git(worker, "rev-parse", "HEAD")
            store.mark_auditing(lease.lease_id)
            store.mark_audited_pass(
                lease.lease_id,
                evidence={
                    "ok": True,
                    "lease_id": lease.lease_id,
                    "worker_tip": worker_tip,
                    "base_head": head,
                    "commit_chain": [],
                    "evidence_digest": "test-digest",
                },
            )
            store.mark_exported(lease.lease_id, str(root / "patches"))
            store.mark_apply_checked(lease.lease_id)
            (host / "src" / "app.py").write_text("different host tree\n", encoding="utf-8")
            _run(host, ["git", "add", "--", "src/app.py"])
            _run(host, ["git", "commit", "-m", "wrong import", "--", "src/app.py"])
            host_tip = _git(host, "rev-parse", "HEAD")
            with self.assertRaises(ValidationIssue) as ctx:
                store.mark_integrated(lease.lease_id, new_tip=host_tip)
            self.assertEqual(ctx.exception.code, "integration_tree_mismatch")
            self.assertEqual(store.get(lease.lease_id).state, LeaseState.APPLY_CHECKED)


class GrokWriteProfileTests(unittest.TestCase):
    def test_headless_worktree_resume_forbidden(self) -> None:
        profile = grok_write_profile("0.2.93")
        self.assertTrue(profile.forbid_headless_worktree_resume)
        self.assertTrue(profile.qualified)
        with self.assertRaises(ValidationIssue) as ctx:
            build_write_resume_invocation(
                adapter="grok-build",
                session_id="child-id",
                cwd="/verified/worktree",
                version="0.2.93",
                use_headless_worktree_resume=True,
            )
        self.assertEqual(ctx.exception.code, "grok_headless_worktree_resume_forbidden")

    def test_write_resume_requires_cwd_and_exact_id(self) -> None:
        inv = build_write_resume_invocation(
            adapter="grok-build",
            session_id="exact-child",
            cwd="/verified/worktree",
            version="0.2.93",
        )
        self.assertIn("exact-child", inv.argv)
        self.assertIn("/verified/worktree", inv.argv)
        self.assertNotIn("--worktree", inv.argv)
        with self.assertRaises(ValidationIssue):
            build_write_resume_invocation(
                adapter="grok-build",
                session_id="exact-child",
                cwd="",
                version="0.2.93",
            )

    def test_workspace_sandbox_not_commit_capable(self) -> None:
        profile = workspace_sandbox_write_profile()
        self.assertFalse(profile.qualified)
        self.assertFalse(profile.detached_commits_permitted)


class UnqualifiedWriteTests(unittest.TestCase):
    def test_unqualified_profile_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = root / "host"
            worker = root / "worker"
            head = _init_repo(host)
            _detached_worktree(host, worker, head)
            store = LeaseStore(host)
            with self.assertRaises(ValidationIssue) as ctx:
                _register_session(host, "sess", worker=worker, head=head)
                store.prepare(
                    lease_id="lease-unqual",
                    host_checkout=host,
                    worker_checkout=worker,
                    session_id="sess",
                    base_head=head,
                    adapter="grok-build",
                    profile="unqualified",
                    write_profile_qualified=False,
                qualification_evidence=_qual(worker, head, session_id="sess", adapter="grok-build"),
                )
            self.assertEqual(ctx.exception.code, "write_profile_unqualified")


if __name__ == "__main__":
    unittest.main()
