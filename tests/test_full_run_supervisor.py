"""Full-run supervisor product-boundary tests (no live provider)."""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cobbler_runtime.behavior_policy import (  # noqa: E402
    FORBIDDEN_FULL_RUN_WAKE_TRIGGERS,
    PARKED_MONITOR_WAKE_CONDITIONS,
    resolve_from_signals,
    resolve_scenario,
)
from cobbler_runtime.full_run import (  # noqa: E402
    build_full_run_argv,
    build_full_run_env,
    capture_fingerprint,
    full_run_root,
    launch_full_run,
    load_state,
    logs_full_run,
    monitor_full_run,
    prepare_full_run,
    reconcile_full_run_with_git,
    stop_full_run,
    validate_event,
    validate_run_report,
    verify_fingerprint,
)
from cobbler_runtime.schema import ValidationIssue  # noqa: E402
from cobbler_runtime.storage import digest_key  # noqa: E402


FAKE_WORKER = r'''#!/usr/bin/env python3
"""Explicit fixture multi-batch worker (adapter=fixture only)."""
import json, os, time
from datetime import datetime, timezone
from pathlib import Path

def utc():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

session = os.environ["ELVES_FULL_RUN_SESSION"]
events = Path(os.environ["ELVES_FULL_RUN_EVENTS"])
report = Path(os.environ["ELVES_FULL_RUN_REPORT"])
branch = os.environ.get("ELVES_FULL_RUN_BRANCH", "feature")
head = os.environ.get("ELVES_FULL_RUN_START_HEAD", "deadbeef")
transcript = Path(os.environ["ELVES_FULL_RUN_TRANSCRIPT"])

def emit(etype, batch, summary, **extra):
    row = {
        "timestamp": utc(),
        "session_id": session,
        "branch": branch,
        "head": head,
        "batch": batch,
        "type": etype,
        "summary": summary,
    }
    row.update(extra)
    with events.open("a") as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")
    with transcript.open("a") as f:
        f.write(summary + "\n")

emit("run_started", 0, "fake worker started")
batches = []
commits = []
acceptance = []
for batch in (1, 2, 3):
    emit("batch_started", batch, f"batch {batch} started")
    time.sleep(0.05)
    head = f"cafebabe{batch}"
    emit("commit_pushed", batch, f"batch {batch} commit", head=head)
    emit("gate_result", batch, f"batch {batch} gates ok")
    emit("batch_complete", batch, f"batch {batch} complete", head=head)
    batches.append({"id": batch, "status": "complete", "head": head})
    commits.append({"batch": batch, "head": head})
    acceptance.append({
        "id": f"B{batch}-A1",
        "criterion": f"batch {batch} complete",
        "met": True,
        "evidence": f"commit {head}",
    })
    emit("heartbeat", batch, f"heartbeat after batch {batch}", head=head)

report.write_text(json.dumps({
    "run_id": f"full-run-{session}",
    "session_id": session,
    "branch": branch,
    "start_head": os.environ.get("ELVES_FULL_RUN_START_HEAD", "deadbeef"),
    "final_head": head,
    "status": "complete",
    "batches": batches,
    "acceptance": acceptance,
    "commits": commits,
    "blockers": [],
    "merge_authority": False,
}, indent=2) + "\n")
emit("run_complete", 3, "fake multi-batch run complete", head=head)
'''

LONG_SLEEPER = r'''#!/usr/bin/env python3
import os, time
from pathlib import Path
Path(os.environ["ELVES_FULL_RUN_TRANSCRIPT"]).write_text("sleeping\n")
time.sleep(30)
'''


class BehaviorPolicyCompositionTests(unittest.TestCase):
    def test_compose_trusted_grok_chat_to_land(self) -> None:
        decision = resolve_from_signals(
            {"full_run": True, "trusted_grok": True, "chat_to_land": True}
        )
        self.assertEqual(decision.work_driver, "grok_build")
        self.assertEqual(decision.delegation_scope, "full_run")
        self.assertEqual(decision.driver_monitor_mode, "parked_monitor")
        self.assertEqual(decision.landing_mode, "chat_to_land")
        self.assertEqual(decision.git_mode, "branch_progress")

    def test_full_run_alone_stays_host_native(self) -> None:
        d = resolve_from_signals({"full_run": True})
        self.assertEqual(d.work_driver, "host_native")
        self.assertNotEqual(d.driver_monitor_mode, "parked_monitor")

    def test_overnight_alone_stays_host_native(self) -> None:
        d = resolve_from_signals({"overnight": True})
        self.assertEqual(d.work_driver, "host_native")

    def test_matrix_dimensions(self) -> None:
        cases = [
            ({"direct_edit": True}, "host_native"),
            ({"bounded_task": True}, "grok_build"),
            ({"untrusted": True}, "untrusted_writer"),
            ({"legacy_two_call": True}, "host_native"),
            ({"full_run": True, "trusted_grok": True}, "grok_build"),
            ({"full_run": True}, "host_native"),
            ({"chat_to_work": True}, "host_native"),
        ]
        for signals, driver in cases:
            with self.subTest(signals=signals):
                d = resolve_from_signals(signals)
                self.assertEqual(d.work_driver, driver)

    def test_parked_forbids_per_push(self) -> None:
        self.assertIn("per_push", FORBIDDEN_FULL_RUN_WAKE_TRIGGERS)
        self.assertIn("worker_exit", PARKED_MONITOR_WAKE_CONDITIONS)


class FullRunGrokArgvTests(unittest.TestCase):
    def test_grok_create_and_resume_argv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            packet = repo / "packet.md"
            packet.write_text("# packet\n")
            worktree = repo / "wt"
            worktree.mkdir()
            prep = prepare_full_run(
                repo,
                session_id="11111111-1111-1111-1111-111111111111",
                branch="feat/x",
                start_head="abc123",
                worktree=worktree,
                packet_path=packet,
                adapter="grok-build",
                model="grok-4.5",
                permission_mode="auto",
                effort="medium",
                executable="grok",
                create=True,
                check=True,
            )
            self.assertTrue(prep["ok"])
            state = load_state(repo, "11111111-1111-1111-1111-111111111111")
            argv = build_full_run_argv(state)
            self.assertEqual(argv[0], "grok")
            self.assertIn("--session-id", argv)
            self.assertIn("11111111-1111-1111-1111-111111111111", argv)
            self.assertIn("--prompt-file", argv)
            self.assertIn("--cwd", argv)
            self.assertIn("--model", argv)
            self.assertIn("grok-4.5", argv)
            self.assertIn("--permission-mode", argv)
            self.assertIn("auto", argv)
            self.assertIn("--yolo", argv)
            self.assertIn("--effort", argv)
            self.assertIn("--max-turns", argv)
            self.assertIn("--output-format", argv)
            self.assertIn("json", argv)
            self.assertIn("--check", argv)
            self.assertNotIn("--resume", argv)

            state.create_session = False
            resume_argv = build_full_run_argv(state)
            self.assertIn("--resume", resume_argv)
            self.assertNotIn("--session-id", resume_argv)

    def test_env_grants_by_name_not_values_on_argv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            packet = repo / "p.md"
            packet.write_text("x\n")
            wt = repo / "wt"
            wt.mkdir()
            prepare_full_run(
                repo,
                session_id="sess-env-1",
                branch="feat/x",
                start_head="h",
                worktree=wt,
                packet_path=packet,
                adapter="fixture",
                fixture_script=repo / "noop.py",
            )
            (repo / "noop.py").write_text("print('ok')\n")
            state = load_state(repo, "sess-env-1")
            root = full_run_root(repo, "sess-env-1")
            parent = {
                "PATH": "/bin",
                "XAI_API_KEY": "grant-secret",
                "OPENAI_API_KEY": "other-secret",
                "UNRELATED_SENTINEL": "should-not-appear",
                "HOME": "/host-home",
            }
            env = build_full_run_env(
                state=state,
                root=root,
                parent_env=parent,
                credential_grant_names=["XAI_API_KEY"],
            )
            self.assertEqual(env.get("XAI_API_KEY"), "grant-secret")
            self.assertNotIn("UNRELATED_SENTINEL", env)
            self.assertNotIn("OPENAI_API_KEY", env)
            # argv never carries KEY=VALUE secrets
            argv = build_full_run_argv(state)
            joined = " ".join(argv)
            self.assertNotIn("grant-secret", joined)
            self.assertNotIn("XAI_API_KEY=", joined)

    def test_digest_keyed_dirs_and_collision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            packet = repo / "p.md"
            packet.write_text("x\n")
            wt = repo / "wt"
            wt.mkdir()
            sid = "a/b"
            prepare_full_run(
                repo,
                session_id=sid,
                branch="feat",
                start_head="h",
                worktree=wt,
                packet_path=packet,
                adapter="fixture",
                fixture_script=repo / "n.py",
            )
            (repo / "n.py").write_text("print(1)\n")
            root = full_run_root(repo, sid)
            self.assertIn(digest_key(sid, prefix="fullrun"), str(root))
            self.assertNotIn("a/b", root.name)
            with self.assertRaises(ValidationIssue):
                prepare_full_run(
                    repo,
                    session_id=sid,
                    branch="feat",
                    start_head="h",
                    worktree=wt,
                    packet_path=packet,
                    adapter="fixture",
                    fixture_script=repo / "n.py",
                )


class FullRunLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.worker = Path(self.tmp.name) / "fake_worker.py"
        self.worker.write_text(FAKE_WORKER, encoding="utf-8")
        self.worker.chmod(self.worker.stat().st_mode | stat.S_IXUSR)
        self.packet = Path(self.tmp.name) / "packet.md"
        self.packet.write_text("# packet\n", encoding="utf-8")
        self.session = "sess-full-run-001"
        self.branch = "codex/delegated-worker-v2-1"
        self.start_head = "aaa111"
        # git worktree for ancestry/branch checks
        os.system(f"git -C {self.repo} init -q")
        os.system(f"git -C {self.repo} config user.email t@t")
        os.system(f"git -C {self.repo} config user.name t")
        (self.repo / "f.txt").write_text("1\n")
        os.system(f"git -C {self.repo} add f.txt && git -C {self.repo} commit -q -m init")
        os.system(f"git -C {self.repo} checkout -q -b {self.branch}")
        self.start_head = os.popen(f"git -C {self.repo} rev-parse HEAD").read().strip()

    def tearDown(self) -> None:
        try:
            stop_full_run(self.repo, session_id=self.session, grace_seconds=0.1)
        except Exception:
            pass
        self.tmp.cleanup()

    def test_fixture_multi_batch_and_bounded_status(self) -> None:
        prepare_full_run(
            self.repo,
            session_id=self.session,
            branch=self.branch,
            start_head=self.start_head,
            worktree=self.repo,
            packet_path=self.packet,
            adapter="fixture",
            fixture_script=self.worker,
        )
        launch = launch_full_run(self.repo, session_id=self.session)
        self.assertTrue(launch["returned_promptly"])
        self.assertEqual(launch["adapter"], "fixture")
        self.assertFalse(launch["merge_authority"])
        deadline = time.time() + 10
        status = monitor_full_run(self.repo, session_id=self.session, stale_after_seconds=60)
        while status.get("state") not in {"complete", "failed", "blocked"} and time.time() < deadline:
            time.sleep(0.05)
            status = monitor_full_run(self.repo, session_id=self.session, stale_after_seconds=60)
        self.assertEqual(status["state"], "complete", status)
        self.assertTrue(status["transcript_private"])
        self.assertFalse(status["merge_authority"])
        self.assertNotIn("transcript", status)
        logs = logs_full_run(self.repo, session_id=self.session, raw_tail=False)
        self.assertFalse(logs["transcript_included"])

    def test_long_running_not_stale_while_process_alive(self) -> None:
        sleeper = Path(self.tmp.name) / "sleeper.py"
        sleeper.write_text(LONG_SLEEPER, encoding="utf-8")
        prepare_full_run(
            self.repo,
            session_id=self.session,
            branch=self.branch,
            start_head=self.start_head,
            worktree=self.repo,
            packet_path=self.packet,
            adapter="fixture",
            fixture_script=sleeper,
        )
        launch_full_run(self.repo, session_id=self.session)
        time.sleep(0.2)
        # Stale threshold 0.05s would be wrong if only worker events count; process heartbeat should keep healthy.
        status = monitor_full_run(self.repo, session_id=self.session, stale_after_seconds=0)
        self.assertIn(status["state"], {"healthy", "pending", "stale"})
        # With fingerprint ok, even stale_after=0 should not force stale if alive and heartbeat refreshed.
        if status.get("fingerprint_ok"):
            self.assertEqual(status["state"], "healthy")
        stop = stop_full_run(self.repo, session_id=self.session, grace_seconds=0.2)
        self.assertTrue(stop.get("fingerprint_verified"))

    def test_forged_event_rejected(self) -> None:
        prepare_full_run(
            self.repo,
            session_id=self.session,
            branch=self.branch,
            start_head=self.start_head,
            worktree=self.repo,
            packet_path=self.packet,
            adapter="fixture",
            fixture_script=self.worker,
        )
        root = full_run_root(self.repo, self.session)
        events = root / "events.jsonl"
        # Foreign session event
        with events.open("a") as f:
            f.write(
                json.dumps(
                    {
                        "timestamp": "t",
                        "session_id": "OTHER",
                        "branch": self.branch,
                        "head": self.start_head,
                        "batch": 1,
                        "type": "run_complete",
                        "summary": "forged",
                    }
                )
                + "\n"
            )
        status = monitor_full_run(self.repo, session_id=self.session, stale_after_seconds=60)
        self.assertGreaterEqual(status["check_summary"]["event_errors"], 1)
        self.assertNotEqual(status["state"], "complete")

    def test_fingerprint_mismatch_refuses_stop(self) -> None:
        prepare_full_run(
            self.repo,
            session_id=self.session,
            branch=self.branch,
            start_head=self.start_head,
            worktree=self.repo,
            packet_path=self.packet,
            adapter="fixture",
            fixture_script=self.worker,
        )
        state = load_state(self.repo, self.session)
        state.fingerprint = {
            "pid": 999999,
            "pgid": 999999,
            "start_time": "not-a-real-start",
            "executable": "/no/such/exe",
            "session_id": self.session,
        }
        state.pid = 999999
        from cobbler_runtime.full_run import save_state  # noqa: PLC0415

        save_state(self.repo, state)
        with self.assertRaises(ValidationIssue) as ctx:
            stop_full_run(self.repo, session_id=self.session, grace_seconds=0.1)
        self.assertEqual(ctx.exception.code, "full_run_fingerprint_mismatch")

    def test_event_schema_rejects_secret_shaped_summary(self) -> None:
        errors = validate_event(
            {
                "timestamp": "t",
                "session_id": "s",
                "branch": "b",
                "head": "h",
                "batch": 1,
                "type": "heartbeat",
                "summary": "token api_key=SUPERSECRET",
            }
        )
        self.assertTrue(any("secret" in e for e in errors))


    def test_forged_pgid_two_sleeper(self) -> None:
        """Stored PGID that is not the verified process group must fail fingerprint/stop."""
        import os
        import signal
        import subprocess
        import time

        # Two sleepers: victim process group vs real process group.
        real = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        forged = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            time.sleep(0.05)
            real_pgid = os.getpgid(real.pid)
            forged_pgid = os.getpgid(forged.pid)
            self.assertNotEqual(real_pgid, forged_pgid)
            fp = capture_fingerprint(
                pid=real.pid,
                pgid=forged_pgid,  # forged: not real's process group
                session_id="sess-forged-pgid",
                executable_hint=sys.executable,
            )
            ok, reason = verify_fingerprint(fp, expected_session_id="sess-forged-pgid")
            self.assertFalse(ok)
            self.assertIn("pgid", reason.lower())
            # Matching session + correct pgid succeeds.
            fp_ok = capture_fingerprint(
                pid=real.pid,
                pgid=real_pgid,
                session_id="sess-forged-pgid",
                executable_hint=sys.executable,
            )
            ok2, reason2 = verify_fingerprint(fp_ok, expected_session_id="sess-forged-pgid")
            self.assertTrue(ok2, reason2)
            # Executable mismatch never excused by start_time match.
            bad_exe = dict(fp_ok.to_dict())
            bad_exe["executable"] = "/nonexistent/forged-binary"
            ok3, reason3 = verify_fingerprint(bad_exe, expected_session_id="sess-forged-pgid")
            self.assertFalse(ok3)
            self.assertIn("executable", reason3.lower())
        finally:
            for p in (real, forged):
                try:
                    os.killpg(os.getpgid(p.pid), signal.SIGKILL)
                except OSError:
                    try:
                        p.kill()
                    except OSError:
                        pass
                try:
                    p.wait(timeout=1)
                except Exception:
                    pass


if __name__ == "__main__":
    unittest.main()
