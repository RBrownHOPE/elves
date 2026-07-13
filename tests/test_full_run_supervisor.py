"""Full-run supervisor product-boundary tests (no live provider)."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


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
    write_report,
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
    head = f"{batch:040x}"
    emit("commit_pushed", batch, f"batch {batch} commit", head=head)
    emit("gate_result", batch, f"batch {batch} gates ok")
    emit("batch_complete", batch, f"batch {batch} complete", head=head)
    batches.append({
        "id": f"batch-{batch}",
        "status": "complete",
        "evidence": f"focused gates passed at {head}",
    })
    commits.append(head)
    acceptance.append({
        "id": f"B{batch}-A1",
        "criterion": f"batch {batch} complete",
        "met": True,
        "evidence": f"commit {head}",
    })
    emit("heartbeat", batch, f"heartbeat after batch {batch}", head=head)

report.write_text(json.dumps({
    "run_id": os.environ["ELVES_FULL_RUN_RUN_ID"],
    "attempt": int(os.environ["ELVES_FULL_RUN_ATTEMPT"]),
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

COMMIT_WITHOUT_REPORT = r'''#!/usr/bin/env python3
import subprocess
from pathlib import Path

worktree = Path(__import__("os").environ["ELVES_FULL_RUN_WORKTREE"])
(worktree / "f.txt").write_text("progress without report\n", encoding="utf-8")
subprocess.run(["git", "-C", str(worktree), "add", "f.txt"], check=True)
subprocess.run(
    ["git", "-C", str(worktree), "commit", "-m", "progress without report"],
    check=True,
)
'''

COMPLETE_WITH_LINGERING_CHILD = FAKE_WORKER + r'''
import subprocess, sys
subprocess.Popen(
    [sys.executable, "-c", "import os,time; os.setsid(); child=os.fork(); "
     "os._exit(0) if child else time.sleep(30)"],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
'''

GROK_COMMIT_AND_WAIT = r'''#!/usr/bin/env python3
import os, subprocess, sys, time
from pathlib import Path

args = sys.argv[1:]
cwd = Path(args[args.index("--cwd") + 1])
if "--resume" not in args:
    (cwd / "resume-checkpoint.txt").write_text("checkpoint\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(cwd), "add", "resume-checkpoint.txt"], check=True)
    subprocess.run(["git", "-C", str(cwd), "commit", "-q", "-m", "checkpoint"], check=True)
    subprocess.run(
        ["git", "-C", str(cwd), "push", "-q", "origin", "HEAD:refs/heads/" + os.environ["ELVES_FULL_RUN_BRANCH"]],
        check=True,
    )
time.sleep(30)
'''


def _init_feature_repo(path: Path, branch: str = "feat/x") -> str:
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "full-run@example.test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Full Run Test"],
        check=True,
    )
    (path / ".gitignore").write_text(".elves/\n", encoding="utf-8")
    (path / "tracked.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "base"], check=True)
    current_branch = subprocess.run(
        ["git", "-C", str(path), "branch", "--show-current"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if current_branch != branch:
        subprocess.run(
            ["git", "-C", str(path), "checkout", "-q", "-b", branch], check=True
        )
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _attach_origin(repo: Path, remote: Path, branch: str) -> None:
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", str(remote)], check=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "push", "-q", "-u", "origin", branch], check=True
    )


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
            ({"bounded_task": True}, "host_native"),
            ({"bounded_task": True, "work_driver_grok": True}, "grok_build"),
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

    def test_natural_turn_it_over_to_grok_routes_full_run(self) -> None:
        decision = resolve_from_signals(
            {}, intent="Please turn it over to Grok for the full run"
        )
        self.assertEqual(decision.work_driver, "grok_build")
        self.assertEqual(decision.delegation_scope, "full_run")
        self.assertEqual(decision.driver_monitor_mode, "parked_monitor")


class FullRunReportValidationTests(unittest.TestCase):
    def _complete(self) -> dict:
        return {
            "run_id": "run-1",
            "session_id": "session-1",
            "branch": "feat/x",
            "start_head": "a" * 40,
            "final_head": "b" * 40,
            "status": "complete",
            "batches": [
                {"id": "batch-1", "status": "complete", "evidence": "gates passed"}
            ],
            "acceptance": [
                {
                    "id": "B1-A1",
                    "criterion": "verified",
                    "met": True,
                    "evidence": "test output",
                }
            ],
            "commits": ["b" * 40],
        }

    def test_complete_requires_nonempty_batches_commits_and_exact_acceptance(self) -> None:
        for mutation in ("batches", "commits"):
            with self.subTest(mutation=mutation):
                report = self._complete()
                report[mutation] = []
                errors = validate_run_report(
                    report, require_complete_acceptance=True, expected_run_id="run-1"
                )
                self.assertTrue(any(mutation in error for error in errors), errors)

        for field, value in (
            ("id", ""),
            ("criterion", ""),
            ("met", False),
            ("evidence", ""),
        ):
            with self.subTest(field=field):
                report = self._complete()
                report["acceptance"][0][field] = value
                errors = validate_run_report(
                    report, require_complete_acceptance=True, expected_run_id="run-1"
                )
                self.assertTrue(errors)

    def test_event_v1_rejects_bad_types_timestamp_sha_and_batch(self) -> None:
        valid = {
            "timestamp": "2026-07-13T05:14:00Z",
            "session_id": "session-1",
            "branch": "feat/x",
            "head": "a" * 40,
            "batch": 2,
            "type": "heartbeat",
            "summary": "healthy",
        }
        self.assertEqual(validate_event(valid), [])
        for field, value in (
            ("timestamp", "2026-07-13T05:14:00-04:00"),
            ("head", "abc123"),
            ("batch", True),
            ("batch", -1),
            ("summary", 7),
            ("session_id", 7),
        ):
            with self.subTest(field=field, value=value):
                event = {**valid, field: value}
                self.assertTrue(validate_event(event))

    def test_report_v1_rejects_malformed_batch_and_commit_records(self) -> None:
        valid = self._complete()
        self.assertEqual(
            validate_run_report(
                valid, require_complete_acceptance=True, expected_run_id="run-1"
            ),
            [],
        )
        malformed = (
            {"batches": ["batch-1"]},
            {"batches": [{"id": 1, "status": "complete", "evidence": "ok"}]},
            {"batches": [{"id": "batch-1", "status": "running", "evidence": "ok"}]},
            {"batches": [{"id": "batch-1", "status": "complete", "evidence": ""}]},
            {"commits": ["short"]},
            {"commits": [{"sha": "b" * 40}]},
            {"commits": [{"sha": "short", "subject": "change"}]},
        )
        for mutation in malformed:
            with self.subTest(mutation=mutation):
                report = {**valid, **mutation}
                self.assertTrue(
                    validate_run_report(
                        report,
                        require_complete_acceptance=True,
                        expected_run_id="run-1",
                    )
                )


class FullRunGrokArgvTests(unittest.TestCase):
    def test_production_requires_origin_and_clean_bound_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            packet = root / "packet.md"
            packet.write_text("packet\n", encoding="utf-8")
            head = _init_feature_repo(repo)
            with self.assertRaises(ValidationIssue) as ctx:
                prepare_full_run(
                    repo,
                    session_id="prod-no-origin",
                    branch="feat/x",
                    start_head=head,
                    worktree=repo,
                    packet_path=packet,
                    adapter="grok-build",
                    executable="grok",
                )
            self.assertEqual(ctx.exception.code, "full_run_origin_required")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            packet = root / "packet.md"
            packet.write_text("packet\n", encoding="utf-8")
            head = _init_feature_repo(repo)
            _attach_origin(repo, root / "origin.git", "feat/x")
            (repo / "untracked.txt").write_text("dirty\n", encoding="utf-8")
            with self.assertRaises(ValidationIssue) as ctx:
                prepare_full_run(
                    repo,
                    session_id="prod-dirty",
                    branch="feat/x",
                    start_head=head,
                    worktree=repo,
                    packet_path=packet,
                    adapter="grok-build",
                    executable="grok",
                )
            self.assertEqual(ctx.exception.code, "full_run_worktree_dirty")

    def test_production_launch_rechecks_cleanliness_and_origin_config(self) -> None:
        for mutation in ("dirty", "origin"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                repo = root / "repo"
                repo.mkdir()
                packet = root / "packet.md"
                packet.write_text("packet\n", encoding="utf-8")
                head = _init_feature_repo(repo)
                _attach_origin(repo, root / "origin.git", "feat/x")
                prepare_full_run(
                    repo,
                    session_id=f"prod-launch-{mutation}",
                    branch="feat/x",
                    start_head=head,
                    worktree=repo,
                    packet_path=packet,
                    adapter="grok-build",
                    executable="grok",
                )
                if mutation == "dirty":
                    (repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")
                    expected = "full_run_worktree_dirty"
                else:
                    subprocess.run(
                        ["git", "-C", str(repo), "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/changed/*"],
                        check=True,
                    )
                    expected = "full_run_origin_binding_changed"
                with self.assertRaises(ValidationIssue) as ctx:
                    launch_full_run(repo, session_id=f"prod-launch-{mutation}")
                self.assertEqual(ctx.exception.code, expected)

    def test_grok_create_and_resume_argv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            packet = repo / "packet.md"
            packet.write_text("# packet\n")
            start_head = _init_feature_repo(repo)
            remote = Path(tmp) / "origin.git"
            subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
            subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(remote)], check=True)
            subprocess.run(["git", "-C", str(repo), "push", "-q", "-u", "origin", "feat/x"], check=True)
            worktree = repo
            prep = prepare_full_run(
                repo,
                session_id="11111111-1111-1111-1111-111111111111",
                branch="feat/x",
                start_head=start_head,
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
            start_head = _init_feature_repo(repo)
            wt = repo
            prepare_full_run(
                repo,
                session_id="sess-env-1",
                branch="feat/x",
                start_head=start_head,
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
            start_head = _init_feature_repo(repo, branch="feat")
            wt = repo
            sid = "a/b"
            prepare_full_run(
                repo,
                session_id=sid,
                branch="feat",
                start_head=start_head,
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
                    start_head=start_head,
                    worktree=wt,
                    packet_path=packet,
                    adapter="fixture",
                    fixture_script=repo / "n.py",
                )

    def test_prepare_preflight_rejects_wrong_branch_head_protected_branch_and_symlink_packet(
        self,
    ) -> None:
        cases = ("branch", "head", "protected", "symlink")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                branch = "main" if case == "protected" else "feat/x"
                packet_target = repo / "packet-target.md"
                packet_target.write_text("packet\n", encoding="utf-8")
                packet = repo / "packet.md"
                if case == "symlink":
                    packet.symlink_to(packet_target)
                else:
                    packet.write_text("packet\n", encoding="utf-8")
                start = _init_feature_repo(repo, branch=branch)
                staged_branch = "feat/other" if case == "branch" else branch
                staged_head = "0" * 40 if case == "head" else start
                with self.assertRaises(ValidationIssue):
                    prepare_full_run(
                        repo,
                        session_id=f"preflight-{case}",
                        branch=staged_branch,
                        start_head=staged_head,
                        worktree=repo,
                        packet_path=packet,
                        adapter="fixture",
                        fixture_script=repo / "fixture.py",
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
        self.start_head = subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

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
        self.assertEqual(status["driver_monitor_mode"], "parked_monitor")
        self.assertEqual(status["driver_contract"], "parked_monitor")
        self.assertNotIn("transcript", status)
        logs = logs_full_run(self.repo, session_id=self.session, raw_tail=False)
        self.assertFalse(logs["transcript_included"])
        self.assertEqual(status["check_summary"]["exit_code"], 0)
        self.assertTrue(status["check_summary"]["exit_record"])
        bounded = logs_full_run(
            self.repo, session_id=self.session, raw_tail=True, tail_lines=10000
        )
        self.assertLessEqual(len(bounded["events_tail"]), 100)
        self.assertLessEqual(len(bounded["transcript_tail"]), 100)
        self.assertTrue(all(len(line) <= 1000 for line in bounded["transcript_tail"]))

    def test_real_resume_archives_attempt_after_committed_pushed_checkpoint(self) -> None:
        remote = Path(self.tmp.name) / "resume-origin.git"
        _attach_origin(self.repo, remote, self.branch)
        grok = Path(self.tmp.name) / "fake_grok.py"
        grok.write_text(GROK_COMMIT_AND_WAIT, encoding="utf-8")
        grok.chmod(grok.stat().st_mode | stat.S_IXUSR)
        prepare_full_run(
            self.repo,
            session_id=self.session,
            branch=self.branch,
            start_head=self.start_head,
            worktree=self.repo,
            packet_path=self.packet,
            adapter="grok-build",
            executable=str(grok),
        )
        launch_full_run(self.repo, session_id=self.session)
        deadline = time.time() + 8
        checkpoint = self.start_head
        while checkpoint == self.start_head and time.time() < deadline:
            time.sleep(0.05)
            checkpoint = subprocess.run(
                ["git", "-C", str(self.repo), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        self.assertNotEqual(checkpoint, self.start_head)
        stopped = stop_full_run(self.repo, session_id=self.session, grace_seconds=1.0)
        self.assertTrue(stopped["ok"], stopped)
        closed = load_state(self.repo, self.session)
        self.assertIsNone(closed.pid)
        self.assertIsNone(closed.pgid)
        self.assertIsNone(closed.fingerprint)
        self.assertIsNotNone(closed.interruption_evidence)

        resumed = launch_full_run(self.repo, session_id=self.session, resume=True)
        self.assertIn("--resume", resumed["argv"])
        index = resumed["argv"].index("--resume")
        self.assertEqual(resumed["argv"][index + 1], self.session)
        self.assertNotIn("--session-id", resumed["argv"])
        state = load_state(self.repo, self.session)
        self.assertEqual(state.attempt, 2)
        self.assertEqual(state.start_head, self.start_head)
        self.assertEqual(state.launch_start_head, self.start_head)
        self.assertEqual(state.head, checkpoint)
        archive = full_run_root(self.repo, self.session) / "attempts" / "attempt-0001"
        for name in (
            "events.jsonl",
            "report.json",
            "exit_record.json",
            "supervisor.fingerprint.json",
            "worker.fingerprint.json",
        ):
            self.assertTrue((archive / name).is_file(), name)
        second_stop = stop_full_run(self.repo, session_id=self.session, grace_seconds=1.0)
        self.assertTrue(second_stop["ok"], second_stop)

    def test_complete_report_waits_for_real_clean_exit(self) -> None:
        delayed = Path(self.tmp.name) / "delayed_worker.py"
        delayed.write_text(FAKE_WORKER + "\ntime.sleep(0.6)\n", encoding="utf-8")
        delayed.chmod(delayed.stat().st_mode | stat.S_IXUSR)
        prepare_full_run(
            self.repo,
            session_id=self.session,
            branch=self.branch,
            start_head=self.start_head,
            worktree=self.repo,
            packet_path=self.packet,
            adapter="fixture",
            fixture_script=delayed,
        )
        launch_full_run(self.repo, session_id=self.session)
        deadline = time.time() + 2
        status = monitor_full_run(self.repo, session_id=self.session, stale_after_seconds=60)
        while status["check_summary"]["report_status"] != "complete" and time.time() < deadline:
            time.sleep(0.02)
            status = monitor_full_run(self.repo, session_id=self.session, stale_after_seconds=60)
        self.assertEqual(status["check_summary"]["report_status"], "complete", status)
        self.assertIn(status["state"], {"healthy", "pending"}, status)
        self.assertFalse(status["check_summary"]["exit_record"])

        while status["state"] not in {"complete", "failed"} and time.time() < deadline:
            time.sleep(0.02)
            status = monitor_full_run(self.repo, session_id=self.session, stale_after_seconds=60)
        self.assertEqual(status["state"], "complete", status)
        self.assertEqual(status["check_summary"]["exit_code"], 0)

    def test_terminal_retired_identity_is_never_reprobed_or_resignaled(self) -> None:
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
        launch_full_run(self.repo, session_id=self.session)
        deadline = time.time() + 5
        status = monitor_full_run(self.repo, session_id=self.session)
        while status["state"] not in {"complete", "failed"} and time.time() < deadline:
            time.sleep(0.03)
            status = monitor_full_run(self.repo, session_id=self.session)
        self.assertEqual(status["state"], "complete", status)
        retired = load_state(self.repo, self.session)
        self.assertIsNone(retired.pid)
        self.assertIsNone(retired.pgid)
        self.assertIsNone(retired.fingerprint)
        self.assertIsNotNone(retired.closed_process_identity)
        with (
            mock.patch("cobbler_runtime.full_run._pid_alive", side_effect=AssertionError("historical pid probed")),
            mock.patch("cobbler_runtime.full_run._process_group_alive", side_effect=AssertionError("historical pgid probed")),
            mock.patch("cobbler_runtime.full_run._scan_supervision_pids", side_effect=AssertionError("historical marker scanned")),
        ):
            repeated = monitor_full_run(self.repo, session_id=self.session)
            stopped = stop_full_run(self.repo, session_id=self.session)
        self.assertEqual(repeated["state"], "complete")
        self.assertTrue(stopped["ok"])
        self.assertFalse(stopped["signaled"])

    def test_nonzero_exit_overrides_complete_worker_report(self) -> None:
        failing = Path(self.tmp.name) / "failing_worker.py"
        failing.write_text(FAKE_WORKER + "\nraise SystemExit(7)\n", encoding="utf-8")
        failing.chmod(failing.stat().st_mode | stat.S_IXUSR)
        prepare_full_run(
            self.repo,
            session_id=self.session,
            branch=self.branch,
            start_head=self.start_head,
            worktree=self.repo,
            packet_path=self.packet,
            adapter="fixture",
            fixture_script=failing,
        )
        launch_full_run(self.repo, session_id=self.session)
        deadline = time.time() + 3
        status = monitor_full_run(self.repo, session_id=self.session, stale_after_seconds=60)
        while status["state"] not in {"complete", "failed"} and time.time() < deadline:
            time.sleep(0.02)
            status = monitor_full_run(self.repo, session_id=self.session, stale_after_seconds=60)
        self.assertEqual(status["state"], "failed", status)
        self.assertEqual(status["check_summary"]["exit_code"], 7)
        self.assertIn("nonzero exit", status["blocker"])

    def test_clean_exit_with_git_progress_but_no_complete_report_fails(self) -> None:
        worker = Path(self.tmp.name) / "progress_only.py"
        worker.write_text(COMMIT_WITHOUT_REPORT, encoding="utf-8")
        prepare_full_run(
            self.repo,
            session_id=self.session,
            branch=self.branch,
            start_head=self.start_head,
            worktree=self.repo,
            packet_path=self.packet,
            adapter="fixture",
            fixture_script=worker,
        )
        launch_full_run(self.repo, session_id=self.session)
        deadline = time.time() + 5
        status = monitor_full_run(self.repo, session_id=self.session)
        while status["state"] not in {"complete", "failed"} and time.time() < deadline:
            time.sleep(0.03)
            status = monitor_full_run(self.repo, session_id=self.session)
        self.assertEqual(status["state"], "failed", status)
        self.assertEqual(status["check_summary"]["exit_code"], 0)
        self.assertIn("without a validated complete report", status["blocker"])

    def test_recursive_supervisor_reaps_setsid_double_fork_before_completion(self) -> None:
        worker = Path(self.tmp.name) / "lingering_child.py"
        worker.write_text(COMPLETE_WITH_LINGERING_CHILD, encoding="utf-8")
        prepare_full_run(
            self.repo,
            session_id=self.session,
            branch=self.branch,
            start_head=self.start_head,
            worktree=self.repo,
            packet_path=self.packet,
            adapter="fixture",
            fixture_script=worker,
        )
        launch_full_run(self.repo, session_id=self.session)
        deadline = time.time() + 5
        status = monitor_full_run(self.repo, session_id=self.session)
        while status["state"] not in {"complete", "failed"} and time.time() < deadline:
            time.sleep(0.03)
            status = monitor_full_run(self.repo, session_id=self.session)
        self.assertEqual(status["state"], "complete", status)
        record = json.loads(
            (full_run_root(self.repo, self.session) / "exit_record.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertTrue(record["descendants_absent"], record)
        self.assertTrue(record["supervised_pids"], record)

    def test_malformed_exit_record_fails_and_wakes_driver(self) -> None:
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
        (full_run_root(self.repo, self.session) / "exit_record.json").write_text(
            "{malformed", encoding="utf-8"
        )
        status = monitor_full_run(self.repo, session_id=self.session)
        self.assertEqual(status["state"], "failed", status)
        self.assertEqual(status["next_action"], "driver_wake_error")
        self.assertGreater(status["check_summary"]["exit_record_errors"], 0)

    def test_concurrent_launch_allows_exactly_one_supervisor(self) -> None:
        sleeper = Path(self.tmp.name) / "concurrent_sleeper.py"
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
        barrier = threading.Barrier(3)
        outcomes: list[str] = []

        def launch() -> None:
            barrier.wait()
            try:
                launch_full_run(self.repo, session_id=self.session)
                outcomes.append("launched")
            except ValidationIssue as issue:
                outcomes.append(issue.code)

        threads = [threading.Thread(target=launch) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(outcomes.count("launched"), 1, outcomes)
        self.assertEqual(outcomes.count("full_run_already_running"), 1, outcomes)
        stopped = stop_full_run(self.repo, session_id=self.session, grace_seconds=0.2)
        self.assertTrue(stopped["ok"], stopped)

    def test_new_tag_is_a_protected_ref_safety_tripwire(self) -> None:
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
        subprocess.run(
            ["git", "-C", str(self.repo), "tag", "worker-created-tag"], check=True
        )
        status = monitor_full_run(self.repo, session_id=self.session)
        self.assertEqual(status["state"], "failed", status)
        self.assertIn("new protected ref", status["blocker"])

    def test_all_local_ref_namespaces_are_protected(self) -> None:
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
        replace_source = subprocess.run(
            ["git", "-C", str(self.repo), "hash-object", "-w", "--stdin"],
            input="unused source\n",
            text=True,
            check=True,
            capture_output=True,
        ).stdout.strip()
        replace_target = subprocess.run(
            ["git", "-C", str(self.repo), "hash-object", "-w", "--stdin"],
            input="unused target\n",
            text=True,
            check=True,
            capture_output=True,
        ).stdout.strip()
        refs = (
            "refs/notes/full-run-sentinel",
            f"refs/replace/{replace_source}",
            "refs/b0/full-run-sentinel",
        )
        for ref in refs:
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(self.repo),
                    "update-ref",
                    ref,
                    replace_target if ref.startswith("refs/replace/") else self.start_head,
                ],
                check=True,
            )
        status = monitor_full_run(self.repo, session_id=self.session)
        self.assertEqual(status["state"], "failed", status)
        for ref in refs:
            self.assertIn(ref, status["blocker"])

    def test_all_remote_ref_namespaces_are_protected(self) -> None:
        remote = Path(self.tmp.name) / "protected-origin.git"
        _attach_origin(self.repo, remote, self.branch)
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
        subprocess.run(
            [
                "git",
                "-C",
                str(self.repo),
                "push",
                "-q",
                "origin",
                f"{self.start_head}:refs/notes/remote-sentinel",
            ],
            check=True,
        )
        status = monitor_full_run(self.repo, session_id=self.session)
        self.assertEqual(status["state"], "failed", status)
        self.assertIn(
            "remote::origin::refs/notes/remote-sentinel", status["blocker"]
        )

    def test_reconcile_requires_origin_feature_tip_to_equal_local(self) -> None:
        remote = Path(self.tmp.name) / "origin.git"
        subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "remote", "add", "origin", str(remote)],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repo), "push", "-q", "-u", "origin", self.branch],
            check=True,
        )
        prepare_full_run(
            self.repo,
            session_id=self.session,
            branch=self.branch,
            start_head=self.start_head,
            worktree=self.repo,
            packet_path=self.packet,
            adapter="grok-build",
            executable="grok",
        )
        (self.repo / "f.txt").write_text("local only\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "f.txt"], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "commit", "-q", "-m", "local only"],
            check=True,
        )
        tip = subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        baseline = json.loads(
            (full_run_root(self.repo, self.session) / "report.json").read_text(
                encoding="utf-8"
            )
        )
        write_report(
            self.repo,
            self.session,
            {
                **baseline,
                "final_head": tip,
                "status": "complete",
                "batches": [
                    {"id": "batch-1", "status": "complete", "evidence": "gates passed"}
                ],
                "acceptance": [
                    {
                        "id": "B1-A1",
                        "criterion": "local change",
                        "met": True,
                        "evidence": tip,
                    }
                ],
                "commits": [tip],
            },
        )
        with self.assertRaises(ValidationIssue) as ctx:
            reconcile_full_run_with_git(self.repo, session_id=self.session)
        self.assertEqual(ctx.exception.code, "full_run_remote_feature_mismatch")

    def test_reconcile_binds_commit_chain_events_and_staged_acceptance_ids(self) -> None:
        remote = Path(self.tmp.name) / "evidence-origin.git"
        _attach_origin(self.repo, remote, self.branch)
        self.packet.write_text("Acceptance: B1-A1 and B1-A2\n", encoding="utf-8")
        prepare_full_run(
            self.repo,
            session_id=self.session,
            branch=self.branch,
            start_head=self.start_head,
            worktree=self.repo,
            packet_path=self.packet,
            adapter="grok-build",
            executable="grok",
        )
        (self.repo / "f.txt").write_text("evidence\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "f.txt"], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "commit", "-q", "-m", "evidence"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repo), "push", "-q", "origin", self.branch],
            check=True,
        )
        tip = subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        baseline = json.loads(
            (full_run_root(self.repo, self.session) / "report.json").read_text(
                encoding="utf-8"
            )
        )
        write_report(
            self.repo,
            self.session,
            {
                **baseline,
                "final_head": tip,
                "status": "complete",
                "batches": [{"id": "batch-1", "status": "complete", "evidence": tip}],
                "acceptance": [
                    {"id": "B1-A1", "criterion": "first", "met": True, "evidence": tip}
                ],
                # Exact SHA shape, but intentionally not the start..final chain.
                "commits": [self.start_head],
            },
        )
        tree = subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD^{tree}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        unrelated = subprocess.run(
            ["git", "-C", str(self.repo), "commit-tree", tree, "-m", "unrelated"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        with (full_run_root(self.repo, self.session) / "events.jsonl").open(
            "a", encoding="utf-8"
        ) as handle:
            handle.write(
                json.dumps(
                    {
                        "timestamp": "2026-07-13T00:00:00+00:00",
                        "session_id": self.session,
                        "branch": self.branch,
                        "head": unrelated,
                        "batch": 1,
                        "type": "heartbeat",
                        "summary": "unrelated head",
                    }
                )
                + "\n"
            )
        with self.assertRaises(ValidationIssue) as ctx:
            reconcile_full_run_with_git(self.repo, session_id=self.session)
        self.assertEqual(ctx.exception.code, "full_run_git_evidence_mismatch")
        self.assertIn("commit", ctx.exception.message)
        self.assertIn("acceptance", ctx.exception.message)
        self.assertIn("event", ctx.exception.message)

    def test_stopped_terminal_state_does_not_regress_on_monitor(self) -> None:
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
        stopped = stop_full_run(self.repo, session_id=self.session)
        self.assertEqual(stopped["status"], "stopped")
        status = monitor_full_run(self.repo, session_id=self.session)
        self.assertEqual(status["state"], "stopped", status)

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
        self.assertTrue(stop["ok"], stop)
        self.assertFalse(stop["still_alive"], stop)

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

    def test_launch_never_clears_unverifiable_live_or_dead_identity(self) -> None:
        import signal
        from cobbler_runtime.full_run import save_state

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
        sleeper = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            start_new_session=True,
        )
        try:
            state = load_state(self.repo, self.session)
            fp = capture_fingerprint(
                pid=sleeper.pid,
                pgid=os.getpgid(sleeper.pid),
                session_id=self.session,
                executable_hint=sys.executable,
            ).to_dict()
            fp["start_time"] = "forged-start"
            state.pid = sleeper.pid
            state.pgid = os.getpgid(sleeper.pid)
            state.fingerprint = fp
            save_state(self.repo, state)
            with self.assertRaises(ValidationIssue) as ctx:
                launch_full_run(self.repo, session_id=self.session)
            self.assertEqual(ctx.exception.code, "full_run_already_running")
            self.assertEqual(load_state(self.repo, self.session).fingerprint, fp)
        finally:
            try:
                os.killpg(sleeper.pid, signal.SIGKILL)
            except OSError:
                pass
            sleeper.wait(timeout=5)

        state = load_state(self.repo, self.session)
        state.pid = 99999999
        state.pgid = 99999999
        state.fingerprint = {
            "pid": 99999999,
            "pgid": 99999999,
            "start_time": "dead",
            "executable": sys.executable,
            "session_id": self.session,
        }
        save_state(self.repo, state)
        with self.assertRaises(ValidationIssue) as ctx:
            launch_full_run(self.repo, session_id=self.session)
        self.assertEqual(ctx.exception.code, "full_run_relaunch_unauthenticated")

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
