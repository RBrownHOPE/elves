from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cobbler_runtime.prewalk import (  # noqa: E402
    PREWALK_CONTINUATION_INPUT,
    advertised_prewalk_capabilities,
    fixture_prewalk_capabilities,
    guide_prompt,
    load_and_validate_transition_artifacts,
    load_prewalk_capability_evidence,
    prewalk_paths,
    probe_installed_prewalk_capabilities,
    validate_checkpoint_artifact,
    validate_meaningful_edit,
    validate_todo_artifact,
)
from cobbler_runtime.native_worker import (  # noqa: E402
    build_native_worker_prewalk_spec,
    native_worker_paths,
    native_worker_status,
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
                    paths=escaped,
                    run_id="run-1",
                    session_id="session-1",
                    todo_limit=10,
                    worktree=root,
                )
            self.assertEqual(caught.exception.code, "prewalk_checkpoint_missing")

    def test_runtime_paths_reject_symlinked_worktree_components(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            outside = Path(tmp) / "outside"
            root.mkdir()
            outside.mkdir()
            (root / ".elves").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValidationIssue) as caught:
                prewalk_paths(root, "run-1")
            self.assertEqual(
                caught.exception.code, "prewalk_worktree_continuity_violation"
            )

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
            ".elves-session.json",
            "docs/plans/task.md",
            "docs/elves/learnings.md",
            "docs/elves/execution-log-task.md",
        ):
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("staging only\n", encoding="utf-8")
        with self.assertRaises(ValidationIssue) as caught:
            self._validate("docs/plans/task.md")
        self.assertEqual(caught.exception.code, "prewalk_changed_path_forbidden")

    def test_rejects_changed_symlink_that_resolves_outside_worktree(self) -> None:
        outside = self.root.parent / "outside.txt"
        outside.write_text("outside\n", encoding="utf-8")
        target = self.root / "src" / "escape.txt"
        target.parent.mkdir()
        target.symlink_to(outside)
        with self.assertRaises(ValidationIssue) as caught:
            self._validate("src/escape.txt")
        self.assertEqual(caught.exception.code, "prewalk_changed_path_forbidden")

    def test_rejects_mixed_product_and_driver_owned_run_memory_edits(self) -> None:
        product = self.root / "src" / "example.py"
        product.parent.mkdir()
        product.write_text("edit\n", encoding="utf-8")
        for path in (".elves-session.json", "docs/plans/active-run.md"):
            with self.subTest(path=path):
                memory = self.root / path
                memory.parent.mkdir(parents=True, exist_ok=True)
                memory.write_text("driver memory\n", encoding="utf-8")
                with self.assertRaises(ValidationIssue) as caught:
                    self._validate("src/example.py")
                self.assertEqual(
                    caught.exception.code, "prewalk_changed_path_forbidden"
                )
                memory.unlink()

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
    def test_versioned_installed_help_fixtures_prove_advertised_grammar_only(self) -> None:
        fixtures = REPO_ROOT / "tests" / "fixtures"
        codex = advertised_prewalk_capabilities(
            host="codex",
            version="0.144.1",
            create_help=(fixtures / "codex-0.144.1-exec-help.txt").read_text(),
            resume_help=(
                fixtures / "codex-0.144.1-exec-resume-help.txt"
            ).read_text(),
        )
        claude_help = (fixtures / "claude-2.1.207-help.txt").read_text()
        claude = advertised_prewalk_capabilities(
            host="claude",
            version="2.1.207",
            create_help=claude_help,
            resume_help=claude_help,
        )
        for capabilities in (codex, claude):
            self.assertTrue(capabilities.advertised_exact_resume)
            self.assertTrue(capabilities.advertised_route_override_on_resume)
            self.assertFalse(capabilities.behaviorally_verified_session_continuity)
            self.assertFalse(capabilities.model_calls_made)
            self.assertFalse(capabilities.qualified())

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
                "transport": "codex_exec",
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
                "guide_route": {"model": "guide-model", "effort": "high"},
                "execution_route": {"model": "execution-model", "effort": "low"},
                "model_calls_made": True,
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
            payload["model_calls_made"] = False
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValidationIssue) as caught:
                load_prewalk_capability_evidence(
                    path,
                    host="codex",
                    installed_version="0.144.1",
                    advertised=advertised,
                )
            self.assertEqual(caught.exception.code, "prewalk_capability_unavailable")
            payload["model_calls_made"] = True
            payload["continuation_sha256"] = hashlib.sha256(b"Keep going").hexdigest()
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValidationIssue) as caught:
                load_prewalk_capability_evidence(
                    path,
                    host="codex",
                    installed_version="0.144.1",
                    advertised=advertised,
                )
            self.assertEqual(caught.exception.code, "prewalk_capability_unavailable")
            payload["continuation_sha256"] = hashlib.sha256(b"Continue.").hexdigest()
            payload["installed_version"] = None
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValidationIssue) as caught:
                load_prewalk_capability_evidence(
                    path,
                    host="codex",
                    installed_version=None,
                    advertised=advertised,
                )
            self.assertEqual(caught.exception.code, "prewalk_capability_unavailable")
        self.assertTrue(qualified.qualified())
        self.assertFalse(qualified.behaviorally_verified_instruction_pruning)
        self.assertEqual(qualified.instruction_fidelity, "retained_safe")
        self.assertTrue(qualified.model_calls_made)
        self.assertTrue(
            qualified.route_matches(
                guide_model="guide-model",
                guide_effort="high",
                execution_model="execution-model",
                execution_effort="low",
            )
        )
        self.assertFalse(
            qualified.route_matches(
                guide_model="other-guide",
                guide_effort="high",
                execution_model="execution-model",
                execution_effort="low",
            )
        )
        for fidelity in ("pruned", "turn_scoped", "unsupported"):
            with self.subTest(fidelity=fidelity):
                unavailable = replace(qualified, instruction_fidelity=fidelity)
                self.assertFalse(unavailable.qualified())
                self.assertEqual(
                    unavailable.unavailable_reason(),
                    "prewalk_instruction_pruning_unqualified",
                )
        with self.assertRaises(ValidationIssue) as caught:
            build_native_worker_prewalk_spec(
                host="codex",
                worktree=REPO_ROOT,
                guide_effort="high",
                execution_effort="low",
                guide_model="other-guide",
                execution_model="execution-model",
                capabilities=qualified,
                requested_mode="required",
            )
        self.assertEqual(caught.exception.code, "prewalk_route_change_unqualified")

    def test_failed_help_commands_cannot_advertise_or_qualify_prewalk(self) -> None:
        def failed_runner(argv, **_kwargs):
            return subprocess.CompletedProcess(
                argv,
                1,
                "Usage: resume SESSION_ID --session-id --resume --model --effort -c",
                "",
            )

        with mock.patch(
            "cobbler_runtime.prewalk.shutil.which", return_value="/usr/bin/codex"
        ):
            capabilities = probe_installed_prewalk_capabilities(
                "codex", runner=failed_runner
            )
        self.assertFalse(capabilities.advertised_exact_resume)
        self.assertFalse(capabilities.advertised_route_override_on_resume)
        self.assertFalse(capabilities.qualified())


class NativeTransportParityTests(unittest.TestCase):
    def _spec(self, host: str):
        return build_native_worker_prewalk_spec(
            host=host,
            worktree=REPO_ROOT,
            guide_effort="high",
            execution_effort="low",
            guide_model="guide-model",
            execution_model="execution-model",
            capabilities=fixture_prewalk_capabilities(host),
            requested_mode="required",
        )

    def test_codex_create_and_exact_resume_pin_distinct_routes(self) -> None:
        prewalk = self._spec("codex")
        self.assertIn("-C", prewalk.guide.argv)
        self.assertIn("guide-model", prewalk.guide.argv)
        self.assertIn('model_reasoning_effort="high"', prewalk.guide.argv)
        resumed = prewalk.execution_spec("thread-exact-123")
        self.assertIn("execution-model", resumed.argv)
        self.assertIn('model_reasoning_effort="low"', resumed.argv)
        self.assertIn("resume", resumed.argv)
        self.assertEqual(resumed.argv[resumed.argv.index("resume") + 1], "thread-exact-123")
        self.assertNotIn("--last", resumed.argv)
        self.assertLess(resumed.argv.index("--sandbox"), resumed.argv.index("resume"))
        for root in resumed.git_write_roots:
            self.assertLess(resumed.argv.index(root), resumed.argv.index("resume"))
        self.assertEqual(resumed.cwd, str(REPO_ROOT.resolve()))

    def test_claude_create_and_exact_resume_pin_distinct_routes(self) -> None:
        prewalk = self._spec("claude")
        self.assertIn("--session-id", prewalk.guide.argv)
        self.assertIn("guide-model", prewalk.guide.argv)
        self.assertEqual(prewalk.guide.argv[prewalk.guide.argv.index("--effort") + 1], "high")
        exact = prewalk.guide.session_id
        self.assertIsNotNone(exact)
        resumed = prewalk.execution_spec(exact or "")
        self.assertIn("--resume", resumed.argv)
        self.assertEqual(resumed.argv[resumed.argv.index("--resume") + 1], exact)
        self.assertIn("execution-model", resumed.argv)
        self.assertEqual(resumed.argv[resumed.argv.index("--effort") + 1], "low")
        self.assertNotIn("--continue", resumed.argv)
        self.assertIn("--safe-mode", resumed.argv)
        self.assertIn("--verbose", resumed.argv)
        self.assertEqual(resumed.argv[resumed.argv.index("--permission-mode") + 1], "auto")

    def test_shared_semantic_contract_is_table_driven_for_both_hosts(self) -> None:
        for host in ("codex", "claude"):
            with self.subTest(host=host):
                prewalk = self._spec(host)
                session_id = prewalk.guide.session_id or "captured-thread-id"
                resumed = prewalk.execution_spec(session_id)
                self.assertTrue(prewalk.guide.separate_session)
                self.assertEqual(prewalk.guide.cwd, resumed.cwd)
                self.assertEqual(resumed.session_id, session_id)
                self.assertEqual(prewalk.guide.requested_model, "guide-model")
                self.assertEqual(resumed.requested_model, "execution-model")
                self.assertFalse(prewalk.guide.cache_handoff)
                self.assertFalse(resumed.cache_handoff)
                self.assertEqual(prewalk.capabilities.instruction_fidelity, "retained_safe")
                self.assertTrue(prewalk.capabilities.stream_identity_verified)


class PrewalkSupervisorLifecycleTests(unittest.TestCase):
    def _repo(self, root: Path) -> tuple[Path, Path]:
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "feat/prewalk", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Elves Test"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "elves@example.invalid"], check=True)
        (repo / ".gitignore").write_text(".elves/\n", encoding="utf-8")
        (repo / "product.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", ".gitignore", "product.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
        packet = repo / ".elves" / "runtime" / "packet.md"
        packet.parent.mkdir(parents=True)
        packet.write_text("packet body\n", encoding="utf-8")
        return repo, packet

    def _fixtures(self, root: Path) -> tuple[Path, Path]:
        fixture_root = root / "fixtures"
        fixture_root.mkdir()
        guide = fixture_root / "guide.py"
        execution = fixture_root / "execution.py"
        guide.write_text(
            "from datetime import datetime, timezone\n"
            "import json, os, pathlib, sys\n"
            "scenario = os.environ.get('PW_FIXTURE_SCENARIO', 'success')\n"
            "record = pathlib.Path(os.environ['PW_FIXTURE_RECORD_DIR'])\n"
            "record.mkdir(parents=True, exist_ok=True)\n"
            "received = sys.stdin.read()\n"
            "identity = json.loads(pathlib.Path(os.environ['ELVES_PREWALK_SESSION_PATH']).read_text())\n"
            "sid = identity['session_id']\n"
            "phase = os.environ['ELVES_NATIVE_WORKER_PHASE']\n"
            "guide_record = {'packet_count': received.count('packet body\\n'), 'phase': phase}\n"
            "(record / 'guide.json').write_text(json.dumps(guide_record))\n"
            "(record / ('guide-' + phase + '.json')).write_text(json.dumps(guide_record))\n"
            "if scenario != 'guide_no_id' and not (scenario == 'recovery_no_initial_id' and phase == 'prewalk'): print(json.dumps({'type':'thread.started','thread_id':sid}), flush=True)\n"
            "if scenario == 'guide_mismatch': print(json.dumps({'type':'turn.started','session_id':'different-session'}), flush=True)\n"
            "if scenario == 'clean_branch_drift':\n"
            "    if phase == 'prewalk':\n"
            "        import subprocess\n"
            "        subprocess.run(['git', 'switch', '-q', '-c', 'drift/clean-fallback'], check=True)\n"
            "    raise SystemExit(7)\n"
            "if scenario == 'clean_head_drift':\n"
            "    if phase == 'prewalk':\n"
            "        import subprocess\n"
            "        pathlib.Path('product.txt').write_text('temporary committed edit\\n')\n"
            "        subprocess.run(['git', 'add', 'product.txt'], check=True)\n"
            "        subprocess.run(['git', 'commit', '-qm', 'temporary edit'], check=True)\n"
            "        pathlib.Path('product.txt').write_text('base\\n')\n"
            "        subprocess.run(['git', 'add', 'product.txt'], check=True)\n"
            "        subprocess.run(['git', 'commit', '-qm', 'restore content'], check=True)\n"
            "    raise SystemExit(7)\n"
            "if scenario == 'recovery_no_initial_id' and phase == 'prewalk': raise SystemExit(7)\n"
            "if scenario == 'recovery_success' and phase == 'prewalk': raise SystemExit(7)\n"
            "if scenario == 'clean_fallback': raise SystemExit(7)\n"
            "if scenario == 'post_edit_failure':\n"
            "    pathlib.Path('product.txt').write_text('uncheckpointed guide edit\\n')\n"
            "    raise SystemExit(7)\n"
            "if scenario == 'missing': raise SystemExit(0)\n"
            "pathlib.Path('product.txt').write_text('guide edit\\n')\n"
            "now = datetime.now(timezone.utc).isoformat()\n"
            "all_complete = scenario == 'tiny'\n"
            "todo = {'schema_version':1,'run_id':os.environ['ELVES_PREWALK_RUN_ID'],'session_id':sid,'created_at':now,'updated_at':now,'items':[{'id':'PW-01','description':'make first edit','acceptance':'product changed','validation':'inspect product.txt','status':'complete'}]}\n"
            "if not all_complete: todo['items'].append({'id':'PW-02','description':'finish task','acceptance':'execution completed','validation':'inspect final product.txt','status':'in_progress'})\n"
            "pathlib.Path(os.environ['ELVES_PREWALK_TODO_PATH']).write_text(json.dumps(todo))\n"
            "checkpoint = {'schema_version':1,'run_id':os.environ['ELVES_PREWALK_RUN_ID'],'session_id':sid,'kind':'task_complete' if all_complete else 'first_meaningful_edit','todo_id':'PW-01','changed_paths':['product.txt'],'summary':'first task edit','validation_attempted':[{'command':'inspect product.txt','exit_code':0}],'ready_for_execution_model':True,'created_at':now}\n"
            "pathlib.Path(os.environ['ELVES_PREWALK_CHECKPOINT_PATH']).write_text(json.dumps(checkpoint))\n"
            "if scenario == 'malformed': pathlib.Path(os.environ['ELVES_PREWALK_TODO_PATH']).write_text('{')\n"
            "if scenario == 'branch_drift':\n"
            "    import subprocess\n"
            "    subprocess.run(['git', 'switch', '-q', '-c', 'drift/prewalk'], check=True)\n",
            encoding="utf-8",
        )
        execution.write_text(
            "from datetime import datetime, timezone\n"
            "import json, os, pathlib, sys\n"
            "scenario = os.environ.get('PW_FIXTURE_SCENARIO', 'success')\n"
            "record = pathlib.Path(os.environ['PW_FIXTURE_RECORD_DIR'])\n"
            "received = sys.stdin.read()\n"
            "count_path = record / 'execution-count.txt'\n"
            "attempt = int(count_path.read_text()) + 1 if count_path.exists() else 1\n"
            "count_path.write_text(str(attempt))\n"
            "(record / 'execution.json').write_text(json.dumps({'input': received, 'phase': os.environ['ELVES_NATIVE_WORKER_PHASE']}))\n"
            "(record / ('execution-' + str(attempt) + '.json')).write_text(json.dumps({'input': received, 'phase': os.environ['ELVES_NATIVE_WORKER_PHASE']}))\n"
            "if scenario == 'clean_fallback':\n"
            "    pathlib.Path('product.txt').write_text('single phase fallback edit\\n')\n"
            "    raise SystemExit(0)\n"
            "identity = json.loads(pathlib.Path(os.environ['ELVES_PREWALK_SESSION_PATH']).read_text())\n"
            "sid = 'different-session' if scenario == 'mismatch' else identity['session_id']\n"
            "if scenario != 'execution_no_id': print(json.dumps({'type':'thread.started','thread_id':sid}), flush=True)\n"
            "if scenario == 'transient_recovery' and attempt == 1:\n"
            "    print('provider overloaded: 503 temporarily unavailable', file=sys.stderr, flush=True)\n"
            "    raise SystemExit(7)\n"
            "if scenario == 'execution_fail': raise SystemExit(7)\n"
            "todo_path = pathlib.Path(os.environ['ELVES_PREWALK_TODO_PATH'])\n"
            "todo = json.loads(todo_path.read_text())\n"
            "for item in todo['items']: item['status'] = 'complete'\n"
            "todo['updated_at'] = datetime.now(timezone.utc).isoformat()\n"
            "todo_path.write_text(json.dumps(todo))\n"
            "pathlib.Path('product.txt').write_text('guide edit\\nexecution edit\\n')\n"
            "checkpoint = {'schema_version':1,'run_id':os.environ['ELVES_PREWALK_RUN_ID'],'session_id':identity['session_id'],'kind':'task_complete','todo_id':'PW-01','changed_paths':['product.txt'],'summary':'task complete','validation_attempted':[{'command':'inspect final product.txt','exit_code':0}],'ready_for_execution_model':True,'created_at':datetime.now(timezone.utc).isoformat()}\n"
            "pathlib.Path(os.environ['ELVES_PREWALK_CHECKPOINT_PATH']).write_text(json.dumps(checkpoint))\n",
            encoding="utf-8",
        )
        return guide, execution

    def _launch(
        self,
        *,
        scenario: str = "success",
        mode: str = "required",
        forbidden_paths: tuple[str, ...] = (),
    ) -> tuple[dict[str, object], Path, Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        repo, packet = self._repo(root)
        guide, execution = self._fixtures(root)
        record = root / "record"
        env = os.environ.copy()
        env["PW_FIXTURE_SCENARIO"] = scenario
        env["PW_FIXTURE_RECORD_DIR"] = str(record)
        cli = REPO_ROOT / "scripts" / "cobbler_agents.py"
        run_id = f"lifecycle-{scenario}"
        command = [
            sys.executable,
            str(cli),
            "native-worker",
            "launch",
            "--host",
            "fixture",
            "--worktree",
            str(repo),
            "--prewalk",
            mode,
            "--guide-model",
            "guide-model",
            "--guide-effort",
            "high",
            "--execution-model",
            "execution-model",
            "--execution-effort",
            "low",
            "--fixture-script",
            str(guide),
            "--execution-fixture-script",
            str(execution),
            "--repo-root",
            str(repo),
            "--run-id",
            run_id,
            "--packet",
            str(packet),
            "--json",
        ]
        for path in forbidden_paths:
            command.extend(("--forbidden-path", path))
        launched = subprocess.run(command, env=env, text=True, capture_output=True, check=False)
        self.assertEqual(launched.returncode, 0, launched.stderr or launched.stdout)
        state: dict[str, object] = json.loads(launched.stdout)["worker"]
        for _ in range(100):
            status = subprocess.run(
                [
                    sys.executable,
                    str(cli),
                    "native-worker",
                    "status",
                    "--repo-root",
                    str(repo),
                    "--run-id",
                    run_id,
                    "--json",
                ],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(status.returncode, 0, status.stderr)
            state = json.loads(status.stdout)["worker"]
            if state["status"] in {"complete", "failed"}:
                break
            time.sleep(0.05)
        return state, repo, record

    def test_two_phase_fixture_preserves_trajectory_and_one_follow_stream(self) -> None:
        state, _repo, record = self._launch()
        supervisor_log = Path(state["supervisor_log"]).read_text(encoding="utf-8")
        self.assertEqual(state["status"], "complete", supervisor_log)
        self.assertEqual(state["version"], 3)
        self.assertEqual(state["mode"], "prewalk")
        self.assertEqual(state["packet"]["sent_count"], 1)
        self.assertEqual(state["execution"]["resume_input"], "Continue.")
        self.assertTrue(state["transition"]["session_continuity"])
        self.assertTrue(state["transition"]["worktree_continuity"])
        self.assertEqual(state["transition"]["packet_sent_count"], 1)
        guide_record = json.loads((record / "guide.json").read_text())
        execution_record = json.loads((record / "execution.json").read_text())
        self.assertEqual(guide_record["packet_count"], 1)
        self.assertEqual(execution_record["input"], "Continue.")
        self.assertEqual(guide_record["phase"], "prewalk")
        self.assertEqual(execution_record["phase"], "execution")
        statuses = [entry["status"] for entry in state["status_history"]]
        for expected in (
            "staged",
            "launching_prewalk",
            "prewalking",
            "transition_ready",
            "launching_execution",
            "executing",
            "complete",
        ):
            self.assertIn(expected, statuses)
        follow = Path(state["follow_log"]).read_text(encoding="utf-8")
        self.assertIn('"phase": "prewalk"', follow)
        self.assertIn('"phase": "execution"', follow)

    def test_atomic_task_completes_without_execution_turn(self) -> None:
        state, _repo, record = self._launch(scenario="tiny")
        self.assertEqual(state["status"], "complete")
        self.assertTrue(state["transition"]["task_complete"])
        self.assertFalse((record / "execution.json").exists())
        statuses = [entry["status"] for entry in state["status_history"]]
        self.assertNotIn("executing", statuses)

    def test_missing_transition_artifacts_fail_before_execution(self) -> None:
        state, _repo, record = self._launch(scenario="missing")
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["failure_reason"], "prewalk_checkpoint_missing")
        self.assertFalse((record / "execution.json").exists())

    def test_guide_recovery_resumes_exact_session_without_replaying_packet(self) -> None:
        state, _repo, record = self._launch(scenario="recovery_success")
        self.assertEqual(state["status"], "complete")
        self.assertEqual(state["prewalk"]["recovery_attempts"], 1)
        self.assertEqual(state["packet"]["sent_count"], 1)
        initial = json.loads((record / "guide-prewalk.json").read_text())
        recovery = json.loads((record / "guide-prewalk_recovery.json").read_text())
        self.assertEqual(initial["packet_count"], 1)
        self.assertEqual(recovery["packet_count"], 0)
        recovered_without_initial_event, _repo, _record = self._launch(
            scenario="recovery_no_initial_id"
        )
        self.assertEqual(recovered_without_initial_event["status"], "complete")
        self.assertEqual(
            recovered_without_initial_event["prewalk"]["recovery_attempts"], 1
        )

    def test_auto_fallback_is_explicitly_fresh_and_only_allowed_while_clean(self) -> None:
        state, repo, record = self._launch(scenario="clean_fallback", mode="auto")
        self.assertEqual(state["status"], "complete")
        self.assertEqual(state["mode"], "single_phase_fallback")
        self.assertFalse(state["transition"]["prewalk_claimed"])
        self.assertEqual(state["packet"]["sent_count"], 2)
        self.assertNotEqual(state["abandoned_prewalk_session_id"], state["session_id"])
        execution = json.loads((record / "execution.json").read_text())
        self.assertEqual(execution["input"], "packet body\n")
        self.assertEqual((repo / "product.txt").read_text(), "single phase fallback edit\n")

    def test_post_edit_cold_fallback_is_forbidden_and_preserves_edit(self) -> None:
        state, repo, record = self._launch(scenario="post_edit_failure", mode="auto")
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["failure_reason"], "prewalk_post_edit_cold_fallback_forbidden")
        self.assertEqual(state["packet"]["sent_count"], 1)
        self.assertFalse((record / "execution.json").exists())
        self.assertEqual((repo / "product.txt").read_text(), "uncheckpointed guide edit\n")

    def test_clean_auto_fallback_rejects_branch_and_head_authority_drift(self) -> None:
        for scenario in ("clean_branch_drift", "clean_head_drift"):
            with self.subTest(scenario=scenario):
                state, _repo, record = self._launch(scenario=scenario, mode="auto")
                self.assertEqual(state["status"], "failed")
                self.assertEqual(
                    state["failure_reason"],
                    "prewalk_worktree_continuity_violation",
                )
                self.assertEqual(state["packet"]["sent_count"], 1)
                self.assertFalse((record / "execution.json").exists())

    def test_malformed_forbidden_and_branch_drift_transitions_fail_closed(self) -> None:
        malformed, _repo, _record = self._launch(scenario="malformed")
        self.assertEqual(malformed["failure_reason"], "prewalk_todo_invalid")
        forbidden, _repo, _record = self._launch(forbidden_paths=("product.txt",))
        self.assertEqual(forbidden["failure_reason"], "prewalk_changed_path_forbidden")
        drifted, _repo, _record = self._launch(scenario="branch_drift")
        self.assertEqual(drifted["failure_reason"], "prewalk_worktree_continuity_violation")

    def test_session_mismatch_and_execution_failure_fail_closed(self) -> None:
        guide_no_id, _repo, _record = self._launch(scenario="guide_no_id")
        self.assertEqual(
            guide_no_id["failure_reason"],
            "prewalk_session_continuity_violation",
        )
        execution_no_id, _repo, _record = self._launch(scenario="execution_no_id")
        self.assertEqual(
            execution_no_id["failure_reason"],
            "prewalk_session_continuity_violation",
        )
        guide_mismatch, _repo, guide_record = self._launch(scenario="guide_mismatch")
        self.assertEqual(guide_mismatch["status"], "failed")
        self.assertEqual(
            guide_mismatch["failure_reason"],
            "prewalk_session_continuity_violation",
        )
        self.assertFalse((guide_record / "execution.json").exists())
        mismatch, _repo, _record = self._launch(scenario="mismatch")
        self.assertEqual(mismatch["status"], "failed")
        self.assertEqual(mismatch["failure_reason"], "prewalk_session_continuity_violation")
        failed, _repo, record = self._launch(scenario="execution_fail")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["failure_reason"], "prewalk_execution_resume_failed")
        self.assertTrue((record / "execution.json").is_file())
        self.assertEqual(failed["packet"]["sent_count"], 1)

    def test_transient_execution_failure_backs_off_and_resumes_same_route(self) -> None:
        state, _repo, record = self._launch(scenario="transient_recovery")
        self.assertEqual(state["status"], "complete")
        self.assertEqual(state["execution"]["transient_retries"], 1)
        self.assertEqual(state["execution"]["retry_backoff_seconds"], [300])
        self.assertEqual(len(state["execution"]["attempts"]), 2)
        self.assertEqual(state["execution"]["attempts"][0]["phase"], "execution")
        self.assertEqual(
            state["execution"]["attempts"][1]["phase"], "execution_retry_1"
        )
        first = json.loads((record / "execution-1.json").read_text())
        second = json.loads((record / "execution-2.json").read_text())
        self.assertEqual(first["input"], "Continue.")
        self.assertEqual(second["input"], "Continue.")
        self.assertEqual(state["packet"]["sent_count"], 1)
        statuses = [entry["status"] for entry in state["status_history"]]
        self.assertIn("execution_backoff", statuses)

    def test_orphaned_childless_backoff_state_fails_status_instead_of_hanging(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            state_path, _log_path = native_worker_paths(repo, "orphaned-backoff")
            state_path.parent.mkdir(parents=True)
            state_path.write_text(
                json.dumps(
                    {
                        "status": "execution_backoff",
                        "pid": None,
                        "pid_start": None,
                        "supervisor_pid": 999_999_999,
                        "supervisor_pid_start": "not-running",
                    }
                ),
                encoding="utf-8",
            )
            state_path.chmod(0o600)
            state = native_worker_status(repo, "orphaned-backoff")
            self.assertEqual(state["status"], "failed")
            self.assertEqual(
                state["failure_reason"],
                "native_worker_supervisor_identity_lost",
            )

    def test_live_child_is_not_reported_terminal_when_supervisor_is_lost(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            state_path, _log_path = native_worker_paths(repo, "orphaned-live-child")
            state_path.parent.mkdir(parents=True)
            process_start = subprocess.run(
                ["ps", "-o", "lstart=", "-p", str(os.getpid())],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            state_path.write_text(
                json.dumps(
                    {
                        "status": "executing",
                        "pid": os.getpid(),
                        "pid_start": process_start,
                        "supervisor_pid": 999_999_999,
                        "supervisor_pid_start": "not-running",
                    }
                ),
                encoding="utf-8",
            )
            state_path.chmod(0o600)
            state = native_worker_status(repo, "orphaned-live-child")
            self.assertEqual(state["status"], "executing")
            self.assertTrue(state["process_identity_matches"])
            self.assertFalse(state["supervisor_identity_matches"])


if __name__ == "__main__":
    unittest.main()
