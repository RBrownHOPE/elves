from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cobbler_runtime.prewalk import (  # noqa: E402
    PREWALK_CONTINUATION_INPUT,
    advertised_prewalk_capabilities,
    guide_prompt,
    load_and_validate_transition_artifacts,
    load_prewalk_capability_evidence,
    prewalk_paths,
    validate_checkpoint_artifact,
    validate_meaningful_edit,
    validate_todo_artifact,
)
from cobbler_runtime.schema import ValidationIssue  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _todo(*, run_id: str = "run-1", session_id: str = "session-1") -> dict[str, object]:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "session_id": session_id,
        "created_at": _now(),
        "updated_at": _now(),
        "items": [
            {
                "id": "PW-01",
                "description": "Start the implementation",
                "acceptance": "The first task behavior changes",
                "validation": "python3 -m unittest tests.test_example",
                "status": "complete",
            },
            {
                "id": "PW-02",
                "description": "Finish the implementation",
                "acceptance": "The full behavior is present",
                "validation": "python3 scripts/verify_repo.py --version Unreleased",
                "status": "in_progress",
            },
        ],
    }


def _checkpoint(
    *,
    run_id: str = "run-1",
    session_id: str = "session-1",
    path: str = "src/example.py",
    kind: str = "first_meaningful_edit",
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "session_id": session_id,
        "kind": kind,
        "todo_id": "PW-01",
        "changed_paths": [path],
        "summary": "Started the task behavior",
        "validation_attempted": [{"command": "python3 -m unittest tests.test_example", "exit_code": 1}],
        "ready_for_execution_model": True,
        "created_at": _now(),
    }


class PrewalkArtifactTests(unittest.TestCase):
    def test_valid_bounded_todo_and_checkpoint(self) -> None:
        todo = validate_todo_artifact(_todo(), run_id="run-1", session_id="session-1")
        checkpoint = validate_checkpoint_artifact(
            _checkpoint(), run_id="run-1", session_id="session-1", todo=todo
        )
        self.assertEqual(checkpoint["todo_id"], "PW-01")
        self.assertEqual(PREWALK_CONTINUATION_INPUT, "Continue.")

    def test_todo_rejects_empty_duplicate_reordered_and_missing_validation(self) -> None:
        mutations = []
        empty = _todo()
        empty["items"] = []
        mutations.append(empty)
        duplicate = _todo()
        duplicate["items"][1]["id"] = "PW-01"  # type: ignore[index]
        mutations.append(duplicate)
        reordered = _todo()
        reordered["items"][0]["id"] = "PW-02"  # type: ignore[index]
        mutations.append(reordered)
        missing = _todo()
        missing["items"][0]["validation"] = ""  # type: ignore[index]
        mutations.append(missing)
        for data in mutations:
            with self.subTest(data=data), self.assertRaises(ValidationIssue) as caught:
                validate_todo_artifact(data, run_id="run-1", session_id="session-1")
            self.assertEqual(caught.exception.code, "prewalk_todo_invalid")

    def test_todo_rejects_limit_and_multiple_in_progress(self) -> None:
        too_many = _todo()
        too_many["items"] = [
            {
                "id": f"PW-{index:02d}",
                "description": f"item {index}",
                "acceptance": "observable",
                "validation": "test command",
                "status": "pending",
            }
            for index in range(1, 11)
        ]
        with self.assertRaises(ValidationIssue) as caught:
            validate_todo_artifact(too_many, run_id="run-1", session_id="session-1", todo_limit=5)
        self.assertEqual(caught.exception.code, "prewalk_todo_limit_exceeded")
        multiple = _todo()
        multiple["items"][0]["status"] = "in_progress"  # type: ignore[index]
        with self.assertRaises(ValidationIssue) as caught:
            validate_todo_artifact(multiple, run_id="run-1", session_id="session-1")
        self.assertEqual(caught.exception.code, "prewalk_todo_invalid")

    def test_all_complete_requires_task_complete_checkpoint(self) -> None:
        data = _todo()
        data["items"][1]["status"] = "complete"  # type: ignore[index]
        with self.assertRaises(ValidationIssue):
            validate_todo_artifact(data, run_id="run-1", session_id="session-1")
        todo = validate_todo_artifact(
            data, run_id="run-1", session_id="session-1", allow_all_complete=True
        )
        checkpoint = _checkpoint(kind="task_complete")
        self.assertEqual(
            validate_checkpoint_artifact(
                checkpoint, run_id="run-1", session_id="session-1", todo=todo
            )["kind"],
            "task_complete",
        )

    def test_checkpoint_rejects_identity_and_unsafe_path(self) -> None:
        todo = validate_todo_artifact(_todo(), run_id="run-1", session_id="session-1")
        mismatch = _checkpoint(session_id="other")
        with self.assertRaises(ValidationIssue) as caught:
            validate_checkpoint_artifact(
                mismatch, run_id="run-1", session_id="session-1", todo=todo
            )
        self.assertEqual(caught.exception.code, "prewalk_checkpoint_invalid")
        escape = _checkpoint(path="../outside.py")
        with self.assertRaises(ValidationIssue) as caught:
            validate_checkpoint_artifact(
                escape, run_id="run-1", session_id="session-1", todo=todo
            )
        self.assertEqual(caught.exception.code, "prewalk_checkpoint_invalid")

    def test_artifact_loader_rejects_paths_outside_runtime_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = prewalk_paths(root, "run-1")
            escaped = deepcopy(paths)
            object.__setattr__(escaped, "todo", str(root / "outside.json"))
            with self.assertRaises(ValidationIssue) as caught:
                load_and_validate_transition_artifacts(
                    paths=escaped, run_id="run-1", session_id="session-1", todo_limit=10
                )
            self.assertEqual(caught.exception.code, "prewalk_checkpoint_missing")

    def test_prompt_names_artifacts_and_minimal_later_input(self) -> None:
        paths = prewalk_paths(REPO_ROOT, "prompt-run")
        text = guide_prompt(run_id="prompt-run", paths=paths, todo_limit=10)
        self.assertIn(paths.todo, text)
        self.assertIn(paths.checkpoint, text)
        self.assertIn("sent exactly once", text)
        self.assertIn("only `Continue.`", text)


class MeaningfulEditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", "-b", "feat/prewalk", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "Elves Test"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "elves@example.invalid"], check=True)
        (self.root / "README.md").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "README.md"], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "base"], check=True)
        self.start = subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.todo = validate_todo_artifact(_todo(), run_id="run-1", session_id="session-1")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _validate(self, path: str, **kwargs):
        checkpoint = validate_checkpoint_artifact(
            _checkpoint(path=path), run_id="run-1", session_id="session-1", todo=self.todo
        )
        return validate_meaningful_edit(
            worktree=self.root,
            start_head=self.start,
            assigned_branch="feat/prewalk",
            todo=self.todo,
            checkpoint=checkpoint,
            starting_worktree_clean=True,
            **kwargs,
        )

    def test_accepts_source_test_first_and_product_documentation_edits(self) -> None:
        for path in ("src/example.py", "tests/test_example.py", "docs/operator.md"):
            with self.subTest(path=path):
                target = self.root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("first task edit\n", encoding="utf-8")
                result = self._validate(path)
                self.assertIn(path, result.changed_paths)
                self.assertEqual(len(result.diff_sha256), 64)
                target.unlink()
                parent = target.parent
                if parent != self.root:
                    parent.rmdir()

    def test_accepts_expected_failing_validation_without_judging_correctness(self) -> None:
        target = self.root / "tests" / "test_regression.py"
        target.parent.mkdir()
        target.write_text("raise AssertionError('reproduced')\n", encoding="utf-8")
        result = self._validate("tests/test_regression.py")
        self.assertTrue(result.meaningful_edit_valid)

    def test_rejects_empty_runtime_only_and_plan_only_deltas(self) -> None:
        with self.assertRaises(ValidationIssue) as caught:
            self._validate("src/example.py")
        self.assertEqual(caught.exception.code, "prewalk_meaningful_edit_missing")
        for path in (
            ".elves/runtime/prewalk/run/todo.json",
            "docs/plans/task.md",
            "docs/elves/execution-log-task.md",
        ):
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("staging only\n", encoding="utf-8")
        with self.assertRaises(ValidationIssue) as caught:
            self._validate("docs/plans/task.md")
        self.assertEqual(caught.exception.code, "prewalk_meaningful_edit_missing")

    def test_rejects_forbidden_surface_and_authority_errors(self) -> None:
        target = self.root / "src" / "example.py"
        target.parent.mkdir()
        target.write_text("edit\n", encoding="utf-8")
        with self.assertRaises(ValidationIssue) as caught:
            self._validate("src/example.py", forbidden_paths=("src/",))
        self.assertEqual(caught.exception.code, "prewalk_changed_path_forbidden")
        with self.assertRaises(ValidationIssue) as caught:
            self._validate("src/example.py", authority_errors=("protected ref moved",))
        self.assertEqual(caught.exception.code, "prewalk_changed_path_forbidden")

    def test_rejects_wrong_worktree_branch_and_dirty_start(self) -> None:
        target = self.root / "src" / "example.py"
        target.parent.mkdir()
        target.write_text("edit\n", encoding="utf-8")
        for changes in (
            {"assigned_branch": "other/branch"},
            {"starting_worktree_clean": False},
            {"worktree": self.root / "src"},
        ):
            checkpoint = validate_checkpoint_artifact(
                _checkpoint(), run_id="run-1", session_id="session-1", todo=self.todo
            )
            params = {
                "worktree": self.root,
                "start_head": self.start,
                "assigned_branch": "feat/prewalk",
                "todo": self.todo,
                "checkpoint": checkpoint,
                "starting_worktree_clean": True,
            }
            params.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValidationIssue) as caught:
                validate_meaningful_edit(**params)
            self.assertEqual(caught.exception.code, "prewalk_worktree_continuity_violation")


class CapabilityEvidenceTests(unittest.TestCase):
    def test_help_is_advertised_only_not_behavioral_proof(self) -> None:
        codex = advertised_prewalk_capabilities(
            host="codex",
            version="0.144.1",
            create_help="--model -c model_reasoning_effort resume",
            resume_help="Usage: resume SESSION_ID --model -c model_reasoning_effort",
        )
        claude = advertised_prewalk_capabilities(
            host="claude",
            version="2.1.207",
            create_help="--session-id --model --effort --resume",
            resume_help="--resume --model --effort",
        )
        for caps in (codex, claude):
            self.assertTrue(caps.advertised_exact_resume)
            self.assertTrue(caps.advertised_route_override_on_resume)
            self.assertFalse(caps.behaviorally_verified_session_continuity)
            self.assertFalse(caps.qualified())
            self.assertEqual(caps.instruction_fidelity, "unsupported")

    def test_bounded_behavioral_artifact_qualifies_retained_safe_continuity(self) -> None:
        advertised = advertised_prewalk_capabilities(
            host="codex",
            version="0.144.1",
            create_help="--model -c model_reasoning_effort resume",
            resume_help="Usage: resume SESSION_ID --model -c model_reasoning_effort",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "qualification.json"
            payload = {
                "artifact_type": "native_prewalk_behavioral_qualification",
                "schema_version": 1,
                "host": "codex",
                "installed_version": "0.144.1",
                "session_id": "exact-session-1",
                "create_exit_code": 0,
                "resume_exit_code": 0,
                "same_session_id": True,
                "same_worktree": True,
                "unique_guide_fact_observed": True,
                "packet_replayed": False,
                "stream_identity_verified": True,
                "instruction_fidelity": "retained_safe",
                "guide_prompt_sha256": hashlib.sha256(b"guide").hexdigest(),
                "continuation_sha256": hashlib.sha256(b"Continue.").hexdigest(),
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            path.chmod(0o600)
            qualified = load_prewalk_capability_evidence(
                path,
                host="codex",
                installed_version="0.144.1",
                advertised=advertised,
            )
        self.assertTrue(qualified.qualified())
        self.assertFalse(qualified.behaviorally_verified_instruction_pruning)
        self.assertEqual(qualified.instruction_fidelity, "retained_safe")


if __name__ == "__main__":
    unittest.main()
