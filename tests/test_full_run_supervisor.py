"""Full-run supervisor and behavior policy tests (no live provider)."""

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
    assert_decision_matches,
    list_scenarios,
    resolve_from_signals,
    resolve_scenario,
)
from cobbler_runtime.full_run import (  # noqa: E402
    launch_full_run,
    logs_full_run,
    monitor_full_run,
    prepare_full_run,
    stop_full_run,
    validate_event,
    validate_run_report,
)


FAKE_WORKER = r'''#!/usr/bin/env python3
"""Fixture multi-batch full-run worker (no network)."""
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
}, indent=2) + "\n")
emit("run_complete", 3, "fake multi-batch run complete", head=head)
'''


class BehaviorPolicyTests(unittest.TestCase):
    def test_all_scenarios_resolve_semantically(self) -> None:
        for scenario in list_scenarios():
            with self.subTest(scenario=scenario.scenario_id):
                decision = resolve_scenario(scenario.scenario_id)
                self.assertEqual(decision.scenario_id, scenario.scenario_id)
                self.assertEqual(
                    assert_decision_matches(decision, scenario.expected),  # type: ignore[arg-type]
                    [],
                )

    def test_signal_resolution_routes_full_run_and_untrusted(self) -> None:
        full = resolve_from_signals({"full_run": True, "trusted_grok": True})
        self.assertEqual(full.delegation_scope, "full_run")
        self.assertEqual(full.driver_monitor_mode, "parked_monitor")
        self.assertEqual(full.continuation, "same_session")
        self.assertNotIn("resume_batch", full.continuation)

        untrusted = resolve_from_signals({"untrusted": True})
        self.assertEqual(untrusted.git_mode, "detached_lease")
        self.assertEqual(untrusted.work_driver, "untrusted_writer")

    def test_parked_monitor_forbids_per_push_reentry(self) -> None:
        self.assertIn("blocker", PARKED_MONITOR_WAKE_CONDITIONS)
        self.assertIn("worker_exit", PARKED_MONITOR_WAKE_CONDITIONS)
        self.assertIn("per_push", FORBIDDEN_FULL_RUN_WAKE_TRIGGERS)
        self.assertIn("resume_batch_required", FORBIDDEN_FULL_RUN_WAKE_TRIGGERS)

    def test_contradictory_single_kickoff_vs_legacy(self) -> None:
        single = resolve_scenario("single_kickoff_e2e")
        legacy = resolve_scenario("legacy_two_call")
        self.assertEqual(single.kickoff_mode, "single_kickoff")
        self.assertEqual(legacy.kickoff_mode, "legacy_two_call")
        self.assertNotEqual(single.continuation, legacy.continuation)


class FullRunSupervisorTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        try:
            stop_full_run(self.repo, session_id=self.session, grace_seconds=0.1)
        except Exception:
            pass
        self.tmp.cleanup()

    def test_fake_worker_multi_batch_one_session(self) -> None:
        prep = prepare_full_run(
            self.repo,
            session_id=self.session,
            branch=self.branch,
            start_head=self.start_head,
            worktree=self.repo,
            packet_path=self.packet,
            executable=sys.executable,
        )
        self.assertTrue(prep["ok"])
        launch = launch_full_run(
            self.repo,
            session_id=self.session,
            extra_args=[str(self.worker)],
        )
        self.assertTrue(launch["ok"])
        self.assertTrue(launch["returned_promptly"])
        self.assertEqual(launch["driver_contract"], "parked-monitor")

        # Wait for fake worker to finish without resume-batch.
        deadline = time.time() + 10
        status = monitor_full_run(self.repo, session_id=self.session, stale_after_seconds=60)
        while status.get("state") not in {"complete", "failed", "blocked"} and time.time() < deadline:
            time.sleep(0.05)
            status = monitor_full_run(self.repo, session_id=self.session, stale_after_seconds=60)

        self.assertEqual(status["state"], "complete", status)
        self.assertEqual(status["driver_contract"], "parked-monitor")
        self.assertTrue(status["transcript_private"])
        self.assertNotIn("transcript", status)
        # Secret-shaped keys must not appear in status values.
        blob = json.dumps(status)
        self.assertNotIn("api_key=", blob.lower())

        report = json.loads(Path(status["report_path"]).read_text(encoding="utf-8"))
        self.assertEqual(validate_run_report(report), [])
        self.assertEqual(report["status"], "complete")
        self.assertEqual(len(report["batches"]), 3)
        self.assertEqual(len(report["acceptance"]), 3)

        events = [
            json.loads(line)
            for line in Path(status["events_path"]).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        types = [e["type"] for e in events]
        self.assertIn("run_started", types)
        self.assertIn("batch_complete", types)
        self.assertIn("run_complete", types)
        self.assertEqual(types.count("batch_complete"), 3)
        for event in events:
            self.assertEqual(validate_event(event), [])

        # Private permissions on transcript/events.
        transcript = Path(prep["transcript_path"])
        mode = transcript.stat().st_mode & 0o777
        self.assertEqual(mode & 0o077, 0, f"transcript not private: {oct(mode)}")

        logs = logs_full_run(self.repo, session_id=self.session, raw_tail=False)
        self.assertFalse(logs["transcript_included"])
        logs_raw = logs_full_run(self.repo, session_id=self.session, raw_tail=True)
        self.assertTrue(logs_raw["transcript_included"])

    def test_stop_terminates_process_group(self) -> None:
        # Long-running sleeper as process group leader.
        sleeper = Path(self.tmp.name) / "sleeper.py"
        sleeper.write_text(
            "import time\n"
            "import os\n"
            "open(os.environ['ELVES_FULL_RUN_TRANSCRIPT'],'a').write('sleeping\\n')\n"
            "time.sleep(30)\n",
            encoding="utf-8",
        )
        prepare_full_run(
            self.repo,
            session_id=self.session,
            branch=self.branch,
            start_head=self.start_head,
            worktree=self.repo,
            packet_path=self.packet,
            executable=sys.executable,
        )
        launch = launch_full_run(
            self.repo,
            session_id=self.session,
            extra_args=[str(sleeper)],
        )
        self.assertTrue(launch["ok"])
        time.sleep(0.1)
        status = monitor_full_run(self.repo, session_id=self.session, stale_after_seconds=60)
        self.assertIn(status["state"], {"healthy", "pending", "stale"})
        stop = stop_full_run(self.repo, session_id=self.session, grace_seconds=0.2)
        self.assertTrue(stop["ok"] or not stop.get("still_alive"))
        # Process should be gone.
        pid = launch["pid"]
        alive = True
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            alive = False
        except PermissionError:
            alive = True
        self.assertFalse(alive)

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


if __name__ == "__main__":
    unittest.main()
