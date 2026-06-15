from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "workspace_guard.py"


def load_workspace_guard_module():
    spec = importlib.util.spec_from_file_location("workspace_guard_under_test", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load workspace_guard module for tests")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class WorkspaceGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.guard = load_workspace_guard_module()

    def test_classifies_mutating_and_read_only_commands(self) -> None:
        cases = {
            "git status": ("read_only", False, False),
            "git commit -m test": ("local_mutation", True, False),
            "git push": ("remote_mutation", True, True),
            "git stash pop": ("local_mutation", True, False),
            "git stash show": ("read_only", False, False),
            "git worktree add ../x": ("local_mutation", True, False),
            "gh pr view 12": ("read_only", False, False),
            "gh pr merge 12 --merge": ("remote_mutation", True, True),
            "gh pr checkout 12": ("local_mutation", True, False),
        }

        for command, expected in cases.items():
            with self.subTest(command=command):
                profile = self.guard.classify_command(command)
                self.assertEqual(
                    (profile.category, profile.check_local, profile.check_remote),
                    expected,
                )

    def test_cli_overrides_can_clear_optional_tips(self) -> None:
        state = self.guard.GuardState(expected_remote_tip="remote-owned")
        args = self.guard.parse_args(
            [
                "--check-command",
                "git push",
                "--expected-remote-tip",
                "null",
            ]
        )

        updated = self.guard.apply_overrides(state, args)

        self.assertIsNone(updated.expected_remote_tip)

    def test_advisory_mode_allows_missing_guard_data_for_mutating_command(self) -> None:
        decision = self.guard.decision_for(
            "git commit -m test",
            self.guard.GuardState(mode="advisory"),
            self.guard.GitSnapshot(branch="feature", head="abc123"),
        )

        self.assertEqual(decision.exit_code, 0)
        self.assertTrue(decision.allowed)
        self.assertIn("missing guard data", "\n".join(decision.messages))

    def test_strict_mode_fails_missing_guard_data_as_configuration_error(self) -> None:
        decision = self.guard.decision_for(
            "git commit -m test",
            self.guard.GuardState(mode="strict"),
            self.guard.GitSnapshot(branch="feature", head="abc123"),
        )

        self.assertEqual(decision.exit_code, 2)
        self.assertFalse(decision.allowed)
        self.assertIn("allowed_head_tip", "\n".join(decision.messages))

    def test_strict_mode_blocks_local_head_mismatch_with_hard_stop(self) -> None:
        decision = self.guard.decision_for(
            "git commit -m test",
            self.guard.GuardState(
                mode="strict",
                branch="feature",
                allowed_head_tip="owned",
            ),
            self.guard.GitSnapshot(branch="feature", head="foreign"),
        )

        self.assertEqual(decision.exit_code, 1)
        self.assertFalse(decision.allowed)
        text = "\n".join(decision.messages)
        self.assertIn("Hard Stop", text)
        self.assertIn("Do not merge, rebase, repair, or commit on top", text)
        self.assertIn("local HEAD mismatch", text)

    def test_branch_mismatch_is_reported_separately(self) -> None:
        decision = self.guard.decision_for(
            "git switch other",
            self.guard.GuardState(
                mode="strict",
                branch="feature",
                allowed_head_tip="owned",
            ),
            self.guard.GitSnapshot(branch="other", head="owned"),
        )

        self.assertEqual(decision.exit_code, 1)
        self.assertIn("branch mismatch", "\n".join(decision.messages))

    def test_strict_mode_blocks_remote_tip_mismatch(self) -> None:
        decision = self.guard.decision_for(
            "git push",
            self.guard.GuardState(
                mode="strict",
                branch="feature",
                allowed_head_tip="owned",
                remote_ref="origin/feature",
                expected_remote_tip="remote-owned",
            ),
            self.guard.GitSnapshot(
                branch="feature",
                head="owned",
                remote_ref="origin/feature",
                remote_tip="remote-foreign",
            ),
        )

        self.assertEqual(decision.exit_code, 1)
        self.assertIn("remote tip mismatch", "\n".join(decision.messages))

    def test_first_push_is_allowed_when_expected_remote_tip_is_null_and_ref_absent(self) -> None:
        decision = self.guard.decision_for(
            "git push",
            self.guard.GuardState(
                mode="strict",
                branch="feature",
                allowed_head_tip="owned",
                remote_ref="origin/feature",
                expected_remote_tip=None,
            ),
            self.guard.GitSnapshot(
                branch="feature",
                head="owned",
                remote_ref="origin/feature",
                remote_tip=None,
            ),
        )

        self.assertEqual(decision.exit_code, 0)
        self.assertTrue(decision.allowed)
        self.assertIn("first push", "\n".join(decision.messages))

    def test_record_local_head_updates_owned_tip_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session_path = Path(tmpdir) / ".elves-session.json"
            session_path.write_text(
                json.dumps({"workspace_guard": {"branch": "feature", "start_tip": "base"}}),
                encoding="utf-8",
            )

            self.guard.record_local_head(
                session_path,
                self.guard.GitSnapshot(branch="feature", head="new-owned"),
            )

            data = json.loads(session_path.read_text(encoding="utf-8"))
            guard = data["workspace_guard"]
            self.assertEqual(guard["start_tip"], "base")
            self.assertEqual(guard["allowed_head_tip"], "new-owned")
            self.assertEqual(guard["branch"], "feature")

    def test_record_local_head_initializes_start_tip_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session_path = Path(tmpdir) / ".elves-session.json"

            self.guard.record_local_head(
                session_path,
                self.guard.GitSnapshot(branch="feature", head="initial"),
            )

            guard = json.loads(session_path.read_text(encoding="utf-8"))["workspace_guard"]
            self.assertEqual(guard["start_tip"], "initial")
            self.assertEqual(guard["allowed_head_tip"], "initial")

    def test_record_pushed_tip_updates_expected_and_last_pushed_tip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session_path = Path(tmpdir) / ".elves-session.json"
            session_path.write_text(
                json.dumps({"workspace_guard": {"branch": "feature"}}),
                encoding="utf-8",
            )

            self.guard.record_pushed_tip(
                session_path,
                self.guard.GitSnapshot(branch="feature", head="pushed"),
            )

            guard = json.loads(session_path.read_text(encoding="utf-8"))["workspace_guard"]
            self.assertEqual(guard["expected_remote_tip"], "pushed")
            self.assertEqual(guard["last_pushed_tip"], "pushed")

    def test_main_advisory_invalid_json_exits_zero_for_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session_path = Path(tmpdir) / ".elves-session.json"
            session_path.write_text("{not json", encoding="utf-8")
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                result = self.guard.main(
                    [
                        "--session-path",
                        str(session_path),
                        "--check-command",
                        "git commit -m test",
                    ]
                )

            self.assertEqual(result, 0)
            self.assertIn("configuration warning", stdout.getvalue())

    def test_main_strict_invalid_json_exits_two_for_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session_path = Path(tmpdir) / ".elves-session.json"
            session_path.write_text("{not json", encoding="utf-8")
            stderr = io.StringIO()

            with contextlib.redirect_stderr(stderr):
                result = self.guard.main(
                    [
                        "--session-path",
                        str(session_path),
                        "--mode",
                        "strict",
                        "--check-command",
                        "git commit -m test",
                    ]
                )

            self.assertEqual(result, 2)
            self.assertIn("configuration warning", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
