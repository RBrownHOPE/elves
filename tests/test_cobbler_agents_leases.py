from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
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



def _qual(worker, head, session_id="sess", adapter="grok-build"):
    return host_qualification_evidence(
        adapter=adapter,
        model="grok-4.5",
        sandbox="devbox",
        worktree=str(worker),
        cwd=str(worker),
        parent="host-parent",
        source_head=head,
        session_id=session_id,
    )


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
    def test_second_live_lease_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = root / "host"
            worker = root / "worker"
            head = _init_repo(host)
            _detached_worktree(host, worker, head)
            store = LeaseStore(host)
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
    def test_allowed_detached_chain_export_and_apply_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = root / "host"
            worker = root / "worker"
            head = _init_repo(host)
            _detached_worktree(host, worker, head)
            store = LeaseStore(host)
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
            lease = store.prepare(
                lease_id="lease-bad-path",
                host_checkout=host,
                worker_checkout=worker,
                session_id="sess",
                base_head=head,
                adapter="grok-build",
                profile="p",
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
            lease = store.prepare(
                lease_id="lease-merge",
                host_checkout=host,
                worker_checkout=worker,
                session_id="sess",
                base_head=head,
                adapter="grok-build",
                profile="p",
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
            lease = store.prepare(
                lease_id="lease-ref",
                host_checkout=host,
                worker_checkout=worker,
                session_id="sess",
                base_head=head,
                adapter="grok-build",
                profile="p",
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
            lease = store.prepare(
                lease_id="lease-push",
                host_checkout=host,
                worker_checkout=worker,
                session_id="sess",
                base_head=head,
                adapter="grok-build",
                profile="p",
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
            lease = store.prepare(
                lease_id="lease-dirty",
                host_checkout=host,
                worker_checkout=worker,
                session_id="sess",
                base_head=head,
                adapter="grok-build",
                profile="p",
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
            lease = store.prepare(
                lease_id="lease-workspace",
                host_checkout=host,
                worker_checkout=worker,
                session_id="sess",
                base_head=head,
                adapter="grok-build",
                profile="workspace",
                sandbox_profile="workspace",
                allowed_paths=["src/"],
                qualification_evidence=_qual(worker, head, session_id="sess", adapter="grok-build"),
            )
            self.assertFalse(lease.detached_commits_permitted)
            self.assertFalse(lease.workspace_sandbox_commit_capable)
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
            lease = store.prepare(
                lease_id="lease-refresh",
                host_checkout=host,
                worker_checkout=worker,
                session_id="sess",
                base_head=head,
                adapter="grok-build",
                profile="p",
                allowed_paths=["src/"],
                qualification_evidence=_qual(worker, head, session_id="sess", adapter="grok-build"),
            )
            store.activate(lease.lease_id)
            # Simulate host advanced tip
            (host / "src" / "app.py").write_text("host v2\n", encoding="utf-8")
            _run(host, ["git", "add", "--", "src/app.py"])
            _run(host, ["git", "commit", "-m", "host integrate", "--", "src/app.py"])
            new_tip = _git(host, "rev-parse", "HEAD")
            # Worker still at old tip, clean
            store.mark_auditing(lease.lease_id)
            store.mark_audited_pass(lease.lease_id, evidence={"ok": True, "lease_id": lease.lease_id, "worker_tip": lease.worker_tip or head, "base_head": head, "commit_chain": [], "evidence_digest": "test-digest"})
            store.mark_exported(lease.lease_id, str(root / "patches"))
            store.mark_integrated(lease.lease_id)
            result = store.refresh_worker_to_tip(lease.lease_id, new_tip=new_tip)
            self.assertEqual(result["worker_tip"], new_tip)
            self.assertEqual(_git(worker, "rev-parse", "HEAD"), new_tip)


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
