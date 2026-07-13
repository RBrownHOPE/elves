"""Batch 3–5 tests: storage, isolation, redaction sentinels, delegated git, acceptance."""

from __future__ import annotations

import json
import errno
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
from datetime import datetime, timezone
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cobbler_runtime.delegated_git import (  # noqa: E402
    assert_action_allowed,
    assert_descendant,
    create_rollback_ref,
    parse_plan_acceptance,
    reconcile_worker_report,
    rollback_ref_name,
    validate_acceptance_mapping,
    DelegatedGitContract,
)
from cobbler_runtime.isolation import (  # noqa: E402
    IsolationSpec,
    assert_no_host_secrets,
    create_tracked_snapshot,
    implement_min_env,
    isolated_lane,
)
from cobbler_runtime.leases import LeaseStore  # noqa: E402
from cobbler_runtime.schema import ValidationIssue  # noqa: E402
from cobbler_runtime.sessions import SessionRegistry  # noqa: E402
from cobbler_runtime.storage import (  # noqa: E402
    StorageError,
    digest_key,
    directory_lock,
    qualify_write_evidence,
    record_filename,
    snapshot_path,
)
import cobbler_runtime.storage as storage_module  # noqa: E402
from cobbler_runtime.context import redact_structure, redact_text  # noqa: E402


class StoragePrimitiveTests(unittest.TestCase):
    def test_directory_lock_retries_eintr_and_releases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            flock = mock.Mock(
                side_effect=[OSError(errno.EINTR, "interrupted"), None, None]
            )
            with mock.patch.object(storage_module.fcntl, "flock", flock):
                with directory_lock(Path(tmp)):
                    pass
            self.assertEqual(flock.call_count, 3)

    def test_directory_lock_times_out_contention_and_fails_other_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                storage_module.fcntl,
                "flock",
                side_effect=BlockingIOError(errno.EAGAIN, "busy"),
            ):
                with self.assertRaises(StorageError) as ctx:
                    with directory_lock(Path(tmp), timeout=0):
                        pass
                self.assertEqual(ctx.exception.code, "lock_timeout")
            with mock.patch.object(
                storage_module.fcntl,
                "flock",
                side_effect=OSError(errno.EIO, "io failure"),
            ):
                with self.assertRaises(StorageError) as ctx:
                    with directory_lock(Path(tmp)):
                        pass
                self.assertEqual(ctx.exception.code, "lock_failed")
            with mock.patch.object(
                storage_module.fcntl,
                "flock",
                side_effect=[None, OSError(errno.EIO, "unlock failure")],
            ):
                with self.assertRaises(StorageError) as ctx:
                    with directory_lock(Path(tmp)):
                        pass
                self.assertEqual(ctx.exception.code, "lock_release_failed")

    def test_directory_lock_requires_unix_fcntl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            storage_module, "fcntl", None
        ):
            with self.assertRaises(StorageError) as ctx:
                with directory_lock(Path(tmp)):
                    pass
            self.assertEqual(ctx.exception.code, "lock_unsupported")

    def test_digest_keys_avoid_collision_and_traversal(self) -> None:
        a = digest_key("../etc/passwd")
        b = digest_key(".._etc_passwd")
        c = digest_key("../etc/passwd")
        self.assertEqual(a, c)
        self.assertNotEqual(a, b)
        self.assertNotIn("..", record_filename("../etc/passwd"))
        self.assertNotIn("/", record_filename("a/b/c"))

    def test_snapshot_path_stays_under_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "store"
            root.mkdir()
            path = snapshot_path(root, "../../escape", kind="sess")
            self.assertTrue(str(path).startswith(str(root.resolve())))
            self.assertIn("snapshots", path.parts)

    def test_session_embedded_id_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            reg = SessionRegistry(repo)
            rec = reg.create(
                session_id="real-id-1",
                harness="grok-build",
                profile="grok-build",
                role="implement",
            )
            path = reg._record_path("real-id-1")
            data = json.loads(path.read_text())
            data["session_id"] = "other-id"
            path.write_text(json.dumps(data))
            with self.assertRaises(ValidationIssue) as ctx:
                reg.get("real-id-1")
            self.assertEqual(ctx.exception.code, "session_embedded_id_mismatch")

    def test_readonly_list_does_not_create_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "empty-repo"
            repo.mkdir()
            reg = SessionRegistry.open_readonly(repo)
            self.assertEqual(reg.list_sessions(), [])
            self.assertFalse((repo / ".elves" / "runtime" / "sessions").exists())

    def test_qualification_fail_closed_and_success(self) -> None:
        ok, reasons = qualify_write_evidence(None)
        self.assertFalse(ok)
        ok, reasons = qualify_write_evidence(
            {
                "adapter": "grok-build",
                "model": "grok-4.5",
                "profile": "grok-build",
                "version": "0.2.93",
                "sandbox": "workspace",
                "worktree": "/wt",
                "cwd": "/wt",
                "parent": "p1",
                "source_head": "abc",
                "capabilities": {"write": True},
                "evidence_kind": "host_observed",
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "host_observed": True,
            }
        )
        self.assertFalse(ok)
        self.assertTrue(any("unsupported_sandbox" in r for r in reasons))

        ok, reasons = qualify_write_evidence(
            {
                "adapter": "grok-build",
                "model": "grok-4.5",
                "profile": "grok-build",
                "version": "0.2.93",
                "sandbox": "devbox",
                "worktree": "/wt",
                "cwd": "/wt",
                "parent": "p1",
                "source_head": "abc",
                "session_id": "sess-1",
                "capabilities": {"write": True},
                "evidence_kind": "host_observed",
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "host_observed": True,
            }
        )
        self.assertTrue(ok, reasons)

    def test_malformed_lease_blocks_exclusivity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            store = LeaseStore(repo)
            bad = store.root / "broken.json"
            bad.write_text("{not-json", encoding="utf-8")
            with self.assertRaises(ValidationIssue) as ctx:
                store.list_leases_strict()
            self.assertEqual(ctx.exception.code, "lease_record_malformed")

    def test_concurrent_saves_leave_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            reg = SessionRegistry(repo)
            errors: list[BaseException] = []

            def worker(i: int) -> None:
                try:
                    reg.create(
                        session_id=f"sess-concurrent-{i}",
                        harness="grok-build",
                        profile="p",
                        role="implement",
                    )
                except BaseException as exc:  # noqa: BLE001
                    errors.append(exc)

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(errors, [])
            records = reg.list_sessions_strict()
            self.assertEqual(len(records), 8)
            for rec in records:
                path = reg._record_path(rec.session_id)
                mode = path.stat().st_mode & 0o777
                self.assertEqual(mode & 0o077, 0)


class IsolationAndRedactionTests(unittest.TestCase):
    def test_hostile_fixture_cannot_read_secrets_or_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            # Minimal git repo with tracked + ignored secrets.
            os.system(f"git -C {repo} init -q")
            os.system(f"git -C {repo} config user.email t@t")
            os.system(f"git -C {repo} config user.name t")
            (repo / "src").mkdir()
            (repo / "src" / "app.py").write_text("print('ok')\n")
            (repo / "AGENTS.md").write_text("SECRET_INSTRUCTION\n")
            (repo / ".env").write_text("SECRET_SENTINEL=super-secret-value\n")
            (repo / ".env").chmod(0o600)
            (repo / ".elves").mkdir()
            (repo / ".elves" / "models.toml").write_text('token="SECRET"\n')
            os.system(f"git -C {repo} add src/app.py AGENTS.md")
            os.system(f"git -C {repo} commit -q -m init")

            host_home = Path(tmp) / "host-home"
            host_home.mkdir()
            (host_home / ".secret").write_text("HOST_HOME_SENTINEL\n")

            with isolated_lane(
                IsolationSpec(
                    repo_root=repo,
                    lane_id="lens1",
                    include_instructions_as_data=True,
                    credential_grants={"ALLOWED_KEY": "allowed-only"},
                )
            ) as lane:
                snap = Path(lane.env["ELVES_ISOLATED_SNAPSHOT"])
                self.assertTrue((snap / "src" / "app.py").is_file())
                self.assertFalse((snap / ".env").exists())
                self.assertFalse((snap / "AGENTS.md").exists())
                self.assertTrue(any("AGENTS" in p for p in lane.instruction_data_files))
                # Host home not mounted.
                self.assertNotEqual(lane.env["HOME"], str(host_home))
                self.assertFalse((Path(lane.env["HOME"]) / ".secret").exists())
                # Unrelated secrets absent.
                leaks = assert_no_host_secrets(
                    lane.env,
                    forbidden_keys=["SECRET_SENTINEL", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"],
                )
                self.assertEqual(leaks, [])
                self.assertIn("ALLOWED_KEY", lane.env)
            # Cleanup on exit.
            self.assertFalse(lane.root.exists())

    def test_implement_min_env_grants_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp) / "wt"
            wt.mkdir()
            env = implement_min_env(
                adapter="grok-build",
                worktree=wt,
                credential_grants={"XAI_API_KEY": "grant-only"},
            )
            self.assertEqual(env.get("XAI_API_KEY"), "grant-only")
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertNotIn("ANTHROPIC_API_KEY", env)

    def test_redaction_removes_secret_sentinels(self) -> None:
        secret = "sk-test-SECRET_SENTINEL_12345"
        text = f"Authorization: Bearer {secret} and token={secret}"
        redacted = redact_text(text, exact_values={secret}).text
        self.assertNotIn(secret, redacted)
        payload = {"token": secret, "nested": {"url": f"https://x?key={secret}"}}
        clean = redact_structure(payload, exact_values={secret})
        blob = json.dumps(clean)
        self.assertNotIn(secret, blob)


class DelegatedGitAndAcceptanceTests(unittest.TestCase):
    def test_protected_actions_fail_closed(self) -> None:
        contract = DelegatedGitContract(
            feature_branch="feat/x",
            base_branch="main",
            start_head="abc",
            session_id="s",
            run_id="r",
        )
        with self.assertRaises(ValidationIssue):
            assert_action_allowed(contract, "merge")
        with self.assertRaises(ValidationIssue):
            assert_action_allowed(contract, "force_push")
        assert_action_allowed(contract, "commit")

    def test_rollback_refs_are_run_scoped_and_distinct(self) -> None:
        a = rollback_ref_name(run_id="run-1", session_id="sess-a", batch=1)
        b = rollback_ref_name(run_id="run-2", session_id="sess-a", batch=1)
        self.assertNotEqual(a, b)
        self.assertIn("refs/elves/rollback/", a)
        self.assertNotEqual(a, "elves/pre-batch-1")

    def test_create_rollback_ref_creates_local_ref_before_push(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            remote = root / "remote.git"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
            (repo / "f.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "f.txt"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "base"], check=True)
            subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(remote)], check=True)

            result = create_rollback_ref(
                repo,
                run_id="run/with/slashes",
                session_id="session-1",
                batch=3,
                push_remote="origin",
            )
            local = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", result["ref"]],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            remote_tip = subprocess.run(
                ["git", f"--git-dir={remote}", "rev-parse", result["ref"]],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(local, result["head"])
            self.assertEqual(remote_tip, result["head"])
            self.assertTrue(result["pushed"])
            self.assertTrue(result["local_ref_created"])

            repeated = create_rollback_ref(
                repo,
                run_id="run/with/slashes",
                session_id="session-1",
                batch=3,
                push_remote="origin",
            )
            self.assertTrue(repeated["idempotent"])
            self.assertTrue(repeated["remote_idempotent"])
            self.assertFalse(repeated["local_ref_created"])
            self.assertFalse(repeated["pushed"])

            (repo / "f.txt").write_text("new tip\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "f.txt"], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-q", "-m", "new tip"],
                check=True,
            )
            with self.assertRaises(ValidationIssue) as ctx:
                create_rollback_ref(
                    repo,
                    run_id="run/with/slashes",
                    session_id="session-1",
                    batch=3,
                    push_remote="origin",
                )
            self.assertEqual(ctx.exception.code, "delegated_git_rollback_ref_collision")
            unchanged = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", result["ref"]],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(unchanged, result["head"])

    def test_report_reconciliation_preserves_host_controls(self) -> None:
        host = {
            "merge_on_green": False,
            "stop_allowed": False,
            "run_mode": "finite",
            "pr_number": 67,
            "batches": [],
        }
        worker = {
            "session_id": "sess-1",
            "branch": "feat/x",
            "start_head": "aaa",
            "final_head": "bbb",
            "status": "complete",
            "batches": [{"id": 1, "status": "complete"}],
            "merge_on_green": True,  # hostile rewrite attempt
            "stop_allowed": True,
        }
        merged = reconcile_worker_report(
            host,
            worker,
            expected_session_id="sess-1",
            expected_branch="feat/x",
            expected_start_head="aaa",
        )
        self.assertFalse(merged["merge_on_green"])
        self.assertFalse(merged["stop_allowed"])
        self.assertEqual(merged["pr_number"], 67)
        self.assertEqual(merged["final_head"], "bbb")
        with self.assertRaises(ValidationIssue):
            reconcile_worker_report(
                host,
                {**worker, "session_id": "other"},
                expected_session_id="sess-1",
                expected_branch="feat/x",
            )

    def test_acceptance_mapping_one_to_one(self) -> None:
        plan = """
### Acceptance
- [ ] B1-A1 — Fresh bundles work
- [ ] B1-A2 — Recursive package ships
- [ ] M-A1 — Trusted full-run parks driver
"""
        items = parse_plan_acceptance(plan)
        self.assertEqual(len(items), 3)
        evidence = [
            {
                "id": "B1-A1",
                "criterion": "Fresh bundles work",
                "met": True,
                "evidence": "smoke ok",
            },
            {
                "id": "B1-A2",
                "criterion": "Recursive package ships",
                "met": True,
                "evidence": "nested module present",
            },
            {
                "id": "M-A1",
                "criterion": "Trusted full-run parks driver",
                "met": True,
                "evidence": "parked-monitor",
            },
        ]
        self.assertEqual(validate_acceptance_mapping(items, evidence), [])

        # Unrelated green evidence fails.
        bad = evidence + [
            {"id": "tests-green", "criterion": "tests green", "met": True, "evidence": "ok"}
        ]
        errors = validate_acceptance_mapping(items, bad)
        self.assertTrue(any("unrelated" in e for e in errors))

        # Swapped criterion text fails.
        swapped = [
            {**evidence[0], "criterion": "wrong text"},
            evidence[1],
            evidence[2],
        ]
        errors = validate_acceptance_mapping(items, swapped)
        self.assertTrue(any("mismatch" in e for e in errors))

        with self.assertRaises(ValidationIssue):
            parse_plan_acceptance("no acceptance here")

    def test_bare_remote_feature_branch_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare = Path(tmp) / "remote.git"
            work = Path(tmp) / "work"
            os.system(f"git init --bare -q {bare}")
            os.system(f"git clone -q {bare} {work}")
            os.system(f"git -C {work} config user.email t@t")
            os.system(f"git -C {work} config user.name t")
            (work / "f.txt").write_text("1\n")
            os.system(f"git -C {work} add f.txt && git -C {work} commit -q -m init")
            os.system(f"git -C {work} branch -M main")
            os.system(f"git -C {work} push -q -u origin main")
            os.system(f"git -C {work} checkout -q -b feat/worker")
            start = subprocess.run(
                ["git", "-C", str(work), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            (work / "f.txt").write_text("2\n")
            os.system(f"git -C {work} add f.txt && git -C {work} commit -q -m w1")
            (work / "f.txt").write_text("3\n")
            os.system(f"git -C {work} add f.txt && git -C {work} commit -q -m w2")
            tip = subprocess.run(
                ["git", "-C", str(work), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            assert_descendant(work, ancestor=start, head=tip)
            contract = DelegatedGitContract(
                feature_branch="feat/worker",
                base_branch="main",
                start_head=start,
                session_id="s",
                run_id="r",
            )
            from cobbler_runtime.delegated_git import push_feature_branch  # noqa: PLC0415

            result = push_feature_branch(work, contract, previous_tip=start)
            self.assertTrue(result["ok"])
            # Base branch on remote still main tip = start
            remote_main = subprocess.run(
                ["git", f"--git-dir={bare}", "rev-parse", "refs/heads/main"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            remote_feat = subprocess.run(
                ["git", f"--git-dir={bare}", "rev-parse", "refs/heads/feat/worker"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(remote_main, start)
            self.assertEqual(remote_feat, tip)
            # Non-descendant fails.
            with self.assertRaises(ValidationIssue):
                assert_descendant(work, ancestor=tip, head=start)
            # Protected merge fails.
            with self.assertRaises(ValidationIssue):
                assert_action_allowed(contract, "merge")


if __name__ == "__main__":
    unittest.main()
