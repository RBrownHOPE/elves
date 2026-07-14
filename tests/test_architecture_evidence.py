"""Batch 6: attempt components, preflight cache, evidence review, public API gate."""

from __future__ import annotations

import ast
import inspect
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cobbler_runtime import dispatch as dispatch_facade  # noqa: E402
from cobbler_runtime import dispatch_attempt as dispatch_attempt_mod  # noqa: E402
from cobbler_runtime import dispatch_external as dispatch_external_mod  # noqa: E402
from cobbler_runtime import dispatch_models as dispatch_models_mod  # noqa: E402
from cobbler_runtime import dispatch_results as dispatch_results_mod  # noqa: E402
from cobbler_runtime.dispatch_attempt import (  # noqa: E402
    build_effective_contract,
    classify_failure,
    prepare_transport,
    record_command_digests,
)
from cobbler_runtime.evidence_review import plan_review  # noqa: E402
from cobbler_runtime.preflight_cache import (  # noqa: E402
    compute_preflight_key,
    record_passing_preflight,
    reuse_preflight,
)
from cobbler_runtime.public_api_snapshot import (  # noqa: E402
    capture_snapshot,
    compatibility_gate,
    diff_snapshots,
)
from cobbler_runtime.schema import EffectiveAttempt  # noqa: E402
from cobbler_runtime.storage import StorageError  # noqa: E402
from cobbler_runtime.adapters import AdapterInvocation  # noqa: E402


class DispatchArchitectureBoundaryTests(unittest.TestCase):
    FACADE_MAX_LINES = 800
    SINGLE_ATTEMPT_MAX_LINES = 150
    FOCUSED_MODULES = (
        "dispatch_attempt.py",
        "dispatch_external.py",
        "dispatch_host_native.py",
        "dispatch_lane_attempt.py",
        "dispatch_models.py",
        "dispatch_results.py",
    )

    def test_dispatch_facade_reexports_single_owner_symbols(self) -> None:
        self.assertIs(dispatch_facade.LaneSpec, dispatch_models_mod.LaneSpec)
        self.assertIs(dispatch_facade.AttemptResult, dispatch_models_mod.AttemptResult)
        self.assertIs(dispatch_facade.LaneResult, dispatch_models_mod.LaneResult)
        self.assertIs(dispatch_facade.CouncilResult, dispatch_models_mod.CouncilResult)
        self.assertIs(
            dispatch_facade._build_failed_attempt,
            dispatch_results_mod.build_failed_attempt,
        )
        self.assertIs(
            dispatch_facade._assemble_external_result,
            dispatch_results_mod.assemble_external_result,
        )
        self.assertIs(
            dispatch_facade._classify_failure,
            dispatch_attempt_mod.classify_failure,
        )
        self.assertIs(
            dispatch_facade._attempt_env_grants,
            dispatch_attempt_mod.attempt_env_grants,
        )
        self.assertIs(
            dispatch_facade._check_capabilities,
            dispatch_attempt_mod.check_capabilities,
        )
        self.assertIs(
            dispatch_facade._summarize,
            dispatch_results_mod.summarize,
        )
        self.assertIs(
            dispatch_facade._terminate_process_group,
            dispatch_external_mod.terminate_process_group,
        )
        self.assertIs(dispatch_facade._pgid_alive, dispatch_external_mod.pgid_alive)

    def test_focused_dispatch_modules_never_import_facade(self) -> None:
        runtime = SCRIPTS / "cobbler_runtime"
        for filename in self.FOCUSED_MODULES:
            source = (runtime / filename).read_text()
            tree = ast.parse(source, filename=filename)
            facade_imports = []
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (
                    node.module or ""
                ).split(".")[-1] == "dispatch":
                    facade_imports.append(node)
                elif isinstance(node, ast.Import) and any(
                    alias.name.split(".")[-1] == "dispatch"
                    for alias in node.names
                ):
                    facade_imports.append(node)
            self.assertEqual(facade_imports, [], filename)

    def test_dispatch_facade_has_no_moved_bodies_and_stays_bounded(self) -> None:
        source = inspect.getsource(dispatch_facade)
        tree = ast.parse(source)
        definitions = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertTrue(
            {
                "LaneSpec",
                "AttemptResult",
                "LaneResult",
                "CouncilResult",
                "_build_failed_attempt",
                "_assemble_external_result",
                "_terminate_process_group",
                "_pgid_alive",
                "_classify_failure",
                "_attempt_env_grants",
                "_check_capabilities",
            }.isdisjoint(definitions)
        )
        self.assertLessEqual(len(source.splitlines()), self.FACADE_MAX_LINES)
        self.assertLessEqual(
            len(inspect.getsource(dispatch_facade._run_single_attempt).splitlines()),
            self.SINGLE_ATTEMPT_MAX_LINES,
        )

    def test_optional_external_attempt_is_skipped_without_sandbox(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            attempt = EffectiveAttempt(
                profile="custom-cli",
                adapter="custom-cli",
                executable="custom-cli",
                requested_model=None,
                extra_args=(),
                input_contract="json-stdio",
                output_contract="custom-json-envelope",
                capabilities=(),
                reason="optional isolation",
                required=False,
                enabled=True,
                source="test",
            )

            spec = dispatch_models_mod.LaneSpec(
                lane_id="optional-external",
                role="reviewer",
                adapter="custom-cli",
                profile="custom-cli",
                required=False,
            )

            with mock.patch(
                "cobbler_runtime.dispatch_external.resolve_fs_sandbox_backend",
                return_value=None,
            ), mock.patch.object(
                dispatch_external_mod,
                "create_tracked_snapshot",
            ) as create_snapshot:
                plan = dispatch_external_mod.prepare_external_launch(
                    spec=spec,
                    attempt=attempt,
                    attempt_index=0,
                    repo_root=root,
                    packet_path=root / "packet.json",
                    prompt_path=root / "prompt.txt",
                    packet_dict={},
                    redacted_task="review",
                    exact_secret_values=frozenset(),
                    grants=[],
                    scrub_env={"PATH": "/usr/bin:/bin"},
                    command_override=None,
                    parent_env=None,
                )

            self.assertTrue(plan.external_attempt_skipped)
            self.assertEqual(plan.isolation_meta.get("external_attempt"), "skipped")
            self.assertEqual(plan.isolation_meta.get("fallback_chain"), "continue")
            self.assertNotIn("host-native", str(plan.isolation_meta))
            self.assertIsNone(plan.isolated)
            self.assertEqual(plan.argv, [])
            create_snapshot.assert_not_called()


class DispatchAttemptComponentTests(unittest.TestCase):
    def test_prepare_transport_scrubs_secrets(self) -> None:
        transport = prepare_transport(
            parent_env={
                "PATH": "/bin",
                "OPENAI_API_KEY": "secret-value",
                "ALLOWED": "yes",
            },
            env_extra_allowlist=("ALLOWED",),
            grants=(),
        )
        self.assertNotIn("OPENAI_API_KEY", transport.scrub.env)
        self.assertIn("PATH", transport.scrub.env)

    def test_effective_contract_and_command_digests(self) -> None:
        attempt = EffectiveAttempt(
            profile="grok-build",
            adapter="grok-build",
            executable="grok",
            requested_model="grok-4.5",
            extra_args=(),
            input_contract="prompt-file",
            output_contract="grok-json",
            capabilities=("read",),
            reason="test",
            required=True,
            enabled=True,
            source="test",
        )
        contract = build_effective_contract(
            attempt,
            grants=(),
            repo_root=REPO_ROOT,
            exact_secret_values=frozenset(),
            qualified_capabilities=(),
        )
        self.assertEqual(contract["adapter"], "grok-build")
        inv = AdapterInvocation(
            adapter="grok-build",
            executable="grok",
            argv=("grok", "--help"),
            decoder="grok-json",
            input_mode="none",
        )
        redacted = record_command_digests(
            contract,
            raw_command=["grok", "--help"],
            exact_secret_values=frozenset(),
            invocation=inv,
        )
        self.assertEqual(redacted, ["grok", "--help"])
        self.assertIn("argv_digest", contract)

    def test_classify_failure_categories(self) -> None:
        self.assertEqual(classify_failure(timeout=True, exit_code=None, error=""), "timeout")
        self.assertEqual(
            classify_failure(timeout=False, exit_code=127, error="executable not found"),
            "launch_error",
        )


class PreflightCacheTests(unittest.TestCase):
    def test_reuse_and_invalidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            # Seed files used in default key.
            (repo / "SKILL.md").write_text("v\n")
            (repo / "AGENTS.md").write_text("v\n")
            (repo / "config.json.example").write_text("{}\n")
            (repo / "scripts").mkdir()
            (repo / "scripts" / "verify_repo.py").write_text("x\n")
            (repo / "scripts" / "check_repo_consistency.py").write_text("x\n")
            head = "abc123"
            record_passing_preflight(repo, head=head, gates={"unit": "ok"})
            decision = reuse_preflight(repo, head=head)
            self.assertTrue(decision["reuse"])
            self.assertFalse(decision["final_readiness_accepts_cache_alone"])
            # Config change invalidates.
            (repo / "SKILL.md").write_text("changed\n")
            decision2 = reuse_preflight(repo, head=head)
            self.assertFalse(decision2["reuse"])
            self.assertEqual(decision2["reason"], "key_mismatch")
            # Head change invalidates even if we re-record wrong.
            record_passing_preflight(repo, head="newhead")
            decision3 = reuse_preflight(repo, head=head)
            self.assertFalse(decision3["reuse"])

    def test_tool_replacement_at_same_path_invalidates_cache_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            tools = Path(tmp) / "tools"
            repo.mkdir()
            tools.mkdir()
            (repo / "SKILL.md").write_text("v\n", encoding="utf-8")
            (repo / "AGENTS.md").write_text("v\n", encoding="utf-8")
            (repo / "config.json.example").write_text("{}\n", encoding="utf-8")
            (repo / "scripts").mkdir()
            (repo / "scripts" / "verify_repo.py").write_text("x\n", encoding="utf-8")
            (repo / "scripts" / "check_repo_consistency.py").write_text(
                "x\n",
                encoding="utf-8",
            )
            for name in ("python3", "git", "gh", "bash"):
                path = tools / name
                path.write_text(f"{name}-version-one\n", encoding="utf-8")
                path.chmod(0o755)

            with mock.patch.dict(os.environ, {"PATH": str(tools)}, clear=False):
                first = compute_preflight_key(repo, head="same-head")
                (tools / "git").write_text("git-version-two\n", encoding="utf-8")
                (tools / "git").chmod(0o755)
                second = compute_preflight_key(repo, head="same-head")

            self.assertNotEqual(first, second)

    def test_arbitrary_tracked_source_change_invalidates_cache_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            source = repo / "scripts" / "cobbler_runtime" / "new_surface.py"
            source.parent.mkdir(parents=True)
            source.write_text("VALUE = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "scripts/cobbler_runtime/new_surface.py"], cwd=repo, check=True)

            first = compute_preflight_key(repo, head="same-head")
            source.write_text("VALUE = 2\n", encoding="utf-8")
            second = compute_preflight_key(repo, head="same-head")

            self.assertNotEqual(first, second)

    def test_untracked_source_and_tracked_fixture_changes_invalidate_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(
                ["git", "config", "user.email", "tests@example.invalid"],
                cwd=repo,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Elves Tests"],
                cwd=repo,
                check=True,
            )
            fixture = repo / "tests" / "fixture.txt"
            fixture.parent.mkdir()
            fixture.write_text("passing fixture\n", encoding="utf-8")
            subprocess.run(["git", "add", "tests/fixture.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            record_passing_preflight(repo, head=head)
            (repo / "new_untracked_test.py").write_text(
                "raise AssertionError('must invalidate')\n",
                encoding="utf-8",
            )
            self.assertFalse(reuse_preflight(repo, head=head)["reuse"])

            (repo / "new_untracked_test.py").unlink()
            record_passing_preflight(repo, head=head)
            fixture.write_text("changed fixture without a source suffix\n", encoding="utf-8")
            self.assertFalse(reuse_preflight(repo, head=head)["reuse"])

    def test_symlinked_elves_ancestor_fails_before_outside_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "repo"
            outside = base / "outside"
            repo.mkdir()
            outside.mkdir()
            sentinel = outside / "sentinel.txt"
            sentinel.write_text("unchanged\n", encoding="utf-8")
            (repo / ".elves").symlink_to(outside, target_is_directory=True)

            with self.assertRaises(StorageError) as ctx:
                record_passing_preflight(repo, head="abc123")
            self.assertEqual(ctx.exception.code, "symlink_component")
            self.assertFalse((outside / "runtime").exists())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged\n")

    def test_cache_leaf_symlink_fails_closed_for_read_and_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "repo"
            outside = base / "outside"
            repo.mkdir()
            outside.mkdir()
            record_passing_preflight(repo, head="first")
            cache = repo / ".elves" / "runtime" / "preflight-cache" / "latest.json"
            cache.unlink()
            target = outside / "latest.json"
            original = '{"sentinel": "unchanged"}\n'
            target.write_text(original, encoding="utf-8")
            cache.symlink_to(target)

            with self.assertRaises(StorageError):
                reuse_preflight(repo, head="second")
            with self.assertRaises(StorageError):
                record_passing_preflight(repo, head="second")
            self.assertEqual(target.read_text(encoding="utf-8"), original)


class EvidenceReviewTests(unittest.TestCase):
    def test_deterministic_selection_and_escalation(self) -> None:
        plan = plan_review(
            changed_paths=["scripts/cobbler_runtime/leases.py", "tests/test_x.py"]
        )
        self.assertIn("unit:runtime", plan.focused_checks)
        self.assertFalse(plan.broad_gate_required)
        self.assertTrue(plan.reasons)
        self.assertIn(plan.risk_level, {"medium", "high"})

        final = plan_review(changed_paths=["README.md"], is_final_readiness=True)
        self.assertTrue(final.broad_gate_required)
        self.assertIn("final_readiness_requires_broad_gate", final.reasons)

        secure = plan_review(changed_paths=["scripts/cobbler_runtime/isolation.py"])
        self.assertTrue(secure.broad_gate_required)
        self.assertEqual(secure.risk_level, "high")

        # Determinism
        a = plan_review(changed_paths=["a.py", "b.py"]).to_dict()
        b = plan_review(changed_paths=["a.py", "b.py"]).to_dict()
        self.assertEqual(a, b)

    def test_unmappable_test_fixture_and_unknown_source_stay_focused(self) -> None:
        fixture = plan_review(changed_paths=["tests/fixture.txt"])
        self.assertFalse(fixture.broad_gate_required)
        self.assertIn("tests_changed", fixture.reasons)
        self.assertIn("unit:focused", fixture.focused_checks)

        unknown = plan_review(changed_paths=["new_untracked_test.py"])
        self.assertFalse(unknown.broad_gate_required)
        self.assertIn("unmapped_or_nondoc_surface_changed", unknown.reasons)
        self.assertIn("unit:focused", unknown.focused_checks)

        docs = plan_review(changed_paths=["README.md"])
        self.assertFalse(docs.broad_gate_required)


class PublicApiSnapshotTests(unittest.TestCase):
    def test_capture_and_compat_gate(self) -> None:
        snap = capture_snapshot(REPO_ROOT)
        self.assertIn(snap.status, {"captured", "unavailable"})
        if snap.status == "captured":
            self.assertGreater(len(snap.entries), 0)
            self.assertEqual(snap.digest(), snap.digest())

        with tempfile.TemporaryDirectory() as tmp:
            # Use a tiny synthetic repo with export surface.
            root = Path(tmp)
            (root / "scripts" / "cobbler_runtime").mkdir(parents=True)
            (root / "scripts" / "cobbler_runtime" / "__init__.py").write_text(
                "class ValidationIssue(Exception):\n    pass\n\n"
                "class RoleName(str):\n    pass\n\n"
                '__all__ = ["ValidationIssue", "RoleName"]\n'
            )
            (root / "scripts" / "cobbler_agents.py").write_text(
                "import argparse\n\n"
                "def cmd_doctor(args):\n"
                "    payload = {'status': 'ok'}\n"
                "    print(payload)\n"
                "    return 0\n\n"
                "def build_parser():\n"
                "    parser = argparse.ArgumentParser()\n"
                "    sub = parser.add_subparsers(dest='command', required=True)\n"
                "    doctor = sub.add_parser('doctor')\n"
                "    doctor.set_defaults(func=cmd_doctor)\n"
                "    return parser\n"
            )
            first = compatibility_gate(root, required=False)
            self.assertTrue(first["ok"])
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.email", "tests@example.invalid"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Elves Tests"], cwd=root, check=True
            )
            subprocess.run(["git", "add", "scripts"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "baseline"], cwd=root, check=True)
            subprocess.run(["git", "switch", "-qc", "feature"], cwd=root, check=True)
            # Internal-only: required mode derives the baseline from main rather
            # than trusting the advisory candidate-local artifact.
            second = compatibility_gate(root, required=True)
            self.assertTrue(second["ok"], second)
            # Breaking: remove an export.
            (root / "scripts" / "cobbler_runtime" / "__init__.py").write_text(
                "class ValidationIssue(Exception):\n    pass\n\n"
                '__all__ = ["ValidationIssue"]\n'
            )
            third = compatibility_gate(root, required=True)
            self.assertFalse(third["ok"])
            self.assertTrue(third["breaking"])


if __name__ == "__main__":
    unittest.main()
