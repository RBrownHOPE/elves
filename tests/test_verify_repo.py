"""Tests for the canonical repository verification command."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"


def load_verify():
    path = SCRIPTS / "verify_repo.py"
    spec = importlib.util.spec_from_file_location("verify_repo_under_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load verify_repo")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class VerifyRepoUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.verify = load_verify()

    def test_compile_scripts_succeeds_on_repo(self) -> None:
        ok, message = self.verify.compile_scripts(REPO_ROOT)
        self.assertTrue(ok, message)
        self.assertIn("compileall ok", message)

    def test_shell_and_json_gates(self) -> None:
        ok, message = self.verify.check_shell(REPO_ROOT)
        self.assertTrue(ok, message)
        ok, message = self.verify.check_json(REPO_ROOT)
        self.assertTrue(ok, message)

    def test_unit_test_failure_preserves_bounded_traceback_tail(self) -> None:
        failure = "\n".join(
            [f"noise {index}" for index in range(80)]
            + [
                "Traceback (most recent call last):",
                '  File "tests/test_example.py", line 12, in test_failure',
                '    raise ValueError("boom")',
                "ValueError: boom",
                "FAILED (errors=1)",
            ]
        )
        proc = subprocess.CompletedProcess(
            args=[sys.executable, "-m", "unittest"],
            returncode=1,
            stdout="\n".join(f"post-failure stdout {index}" for index in range(100)),
            stderr=failure,
        )
        with mock.patch.object(self.verify, "_run", return_value=proc):
            ok, message = self.verify.check_unit_tests(REPO_ROOT)

        self.assertFalse(ok)
        self.assertIn("bounded unittest failure tail", message)
        self.assertIn("Traceback (most recent call last):", message)
        self.assertIn("ValueError: boom", message)
        self.assertLess(len(message), self.verify.UNIT_TEST_FAILURE_MAX_CHARS + 500)

    def test_verification_child_does_not_inherit_host_secret_environment(self) -> None:
        sentinel = "verification-env-sentinel-27f40d"
        with mock.patch.dict(
            os.environ,
            {"ELVES_VERIFICATION_SECRET": sentinel},
            clear=False,
        ):
            proc = self.verify._run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import os; "
                        "print(os.environ.get('ELVES_VERIFICATION_SECRET', 'absent'))"
                    ),
                ],
                cwd=REPO_ROOT,
            )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "absent")
        self.assertNotIn(sentinel, proc.stdout + proc.stderr)

    def test_verification_failure_tail_redacts_exact_and_shared_secrets(self) -> None:
        sentinel = "arbitrary-verification-sentinel-c927a1"
        proc = subprocess.CompletedProcess(
            args=[sys.executable, "-m", "unittest"],
            returncode=1,
            stdout="",
            stderr=f"failure {sentinel} Bearer abcdefghijklmnop\nFAILED (errors=1)\n",
        )
        with (
            mock.patch.dict(
                os.environ,
                {"ELVES_VERIFICATION_SECRET": sentinel},
                clear=False,
            ),
            mock.patch.object(self.verify, "_run", return_value=proc),
        ):
            ok, message = self.verify.check_unit_tests(REPO_ROOT)

        self.assertFalse(ok)
        self.assertNotIn(sentinel, message)
        self.assertNotIn("Bearer abcdefghijklmnop", message)
        self.assertIn("[REDACTED:exact_grant]", message)
        self.assertIn("[REDACTED:bearer_token]", message)

    def test_final_readiness_rejects_skip_flags(self) -> None:
        for flag in ("--skip-tests", "--skip-smokes"):
            with self.subTest(flag=flag), self.assertRaises(SystemExit) as caught:
                self.verify.main(
                    [
                        "--repo-root",
                        str(REPO_ROOT),
                        "--final-readiness",
                        "--version",
                        "2.1.0",
                        flag,
                    ]
                )
            self.assertEqual(caught.exception.code, 2)

    def test_final_readiness_requires_explicit_session(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            self.verify.main(
                [
                    "--repo-root",
                    str(REPO_ROOT),
                    "--final-readiness",
                    "--version",
                    "2.1.0",
                ]
            )
        self.assertEqual(caught.exception.code, 2)

    def test_strict_modes_require_version(self) -> None:
        for mode in ("--ci", "--final-readiness"):
            with self.subTest(mode=mode), self.assertRaises(SystemExit) as caught:
                self.verify.main(["--repo-root", str(REPO_ROOT), mode])
            self.assertEqual(caught.exception.code, 2)

    def test_landing_gate_passes_explicit_session_and_optional_plan(self) -> None:
        proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="{}", stderr="")
        session = REPO_ROOT / ".elves-session.json"
        plan = REPO_ROOT / "docs/plans/v2.1.0-delegated-worker-stabilization.md"
        provenance = [
            (True, session, "ok"),
            (True, plan, "ok"),
            (True, plan, "ok"),
        ]
        with (
            mock.patch.object(
                self.verify, "_verified_repo_file", side_effect=provenance
            ),
            mock.patch.object(
                self.verify, "_verify_session_identity", return_value=(True, "ok")
            ),
            mock.patch.object(self.verify, "_run", return_value=proc) as run,
        ):
            ok, message = self.verify.check_landing(
                REPO_ROOT,
                session_path=Path(".elves-session.json"),
                plan_path=Path("docs/plans/v2.1.0-delegated-worker-stabilization.md"),
            )

        self.assertTrue(ok, message)
        command = run.call_args.args[0]
        self.assertIn("--session", command)
        self.assertIn("--plan", command)
        self.assertIn("--repo-root", command)
        self.assertIn("--json", command)

    def test_final_readiness_executes_landing_gate(self) -> None:
        success = (True, "ok")
        with (
            mock.patch.object(self.verify, "compile_scripts", return_value=success),
            mock.patch.object(self.verify, "check_shell", return_value=success),
            mock.patch.object(self.verify, "check_json", return_value=success),
            mock.patch.object(self.verify, "check_evidence_review_plan", return_value=success),
            mock.patch.object(self.verify, "check_consistency", return_value=success),
            mock.patch.object(self.verify, "check_release", return_value=success),
            mock.patch.object(self.verify, "check_public_api", return_value=success),
            mock.patch.object(self.verify, "check_landing", return_value=success) as landing,
            mock.patch.object(self.verify, "check_markdown_links", return_value=success),
            mock.patch.object(self.verify, "check_secret_patterns", return_value=success),
            mock.patch.object(self.verify, "check_unit_tests", return_value=success),
            mock.patch.object(self.verify, "check_installed_smokes", return_value=success),
            mock.patch.object(self.verify, "check_git_diff", return_value=success),
            mock.patch.object(self.verify, "check_preflight_cache", return_value=success),
        ):
            code = self.verify.main(
                [
                    "--repo-root",
                    str(REPO_ROOT),
                    "--final-readiness",
                    "--version",
                    "2.1.0",
                    "--session",
                    ".elves-session.json",
                    "--plan",
                    "docs/plans/plan.md",
                    "--json",
                ]
            )

        self.assertEqual(code, 0)
        landing.assert_called_once_with(
            REPO_ROOT,
            session_path=Path(".elves-session.json"),
            plan_path=Path("docs/plans/plan.md"),
        )

    def test_ci_mode_is_strict_but_does_not_run_landing(self) -> None:
        success = (True, "ok")
        with (
            mock.patch.object(self.verify, "compile_scripts", return_value=success),
            mock.patch.object(self.verify, "check_shell", return_value=success),
            mock.patch.object(self.verify, "check_json", return_value=success),
            mock.patch.object(self.verify, "check_evidence_review_plan", return_value=success),
            mock.patch.object(self.verify, "check_consistency", return_value=success),
            mock.patch.object(self.verify, "check_release", return_value=success),
            mock.patch.object(self.verify, "check_public_api", return_value=success) as api,
            mock.patch.object(self.verify, "check_landing", return_value=success) as landing,
            mock.patch.object(self.verify, "check_markdown_links", return_value=success) as links,
            mock.patch.object(self.verify, "check_secret_patterns", return_value=success) as secrets,
            mock.patch.object(self.verify, "check_unit_tests", return_value=success),
            mock.patch.object(self.verify, "check_installed_smokes", return_value=success) as smokes,
            mock.patch.object(self.verify, "check_git_diff", return_value=success) as git_diff,
            mock.patch.object(self.verify, "check_preflight_cache", return_value=success) as cache,
        ):
            code = self.verify.main(
                [
                    "--repo-root",
                    str(REPO_ROOT),
                    "--ci",
                    "--version",
                    "2.1.0",
                    "--base-ref",
                    "base-before-sha",
                    "--json",
                ]
            )

        self.assertEqual(code, 0)
        landing.assert_not_called()
        api.assert_called_once_with(
            REPO_ROOT, required=True, base_ref="base-before-sha"
        )
        links.assert_called_once_with(REPO_ROOT)
        secrets.assert_called_once_with(REPO_ROOT)
        smokes.assert_called_once_with(REPO_ROOT)
        git_diff.assert_called_once_with(
            REPO_ROOT,
            final_readiness=False,
            cumulative_required=True,
            base_ref="base-before-sha",
        )
        cache.assert_called_once_with(REPO_ROOT, final_readiness=True)

    def test_final_evidence_review_executes_concrete_mapped_checks(self) -> None:
        cache: dict[str, tuple[bool, str]] = {}
        with (
            mock.patch.object(
                self.verify,
                "_cumulative_changed_paths",
                return_value=(True, ["README.md"], "origin/main...HEAD"),
            ),
            mock.patch.object(
                self.verify, "check_unit_tests", return_value=(True, "tests ok")
            ) as unit,
            mock.patch.object(
                self.verify, "check_consistency", return_value=(True, "consistency ok")
            ) as consistency,
            mock.patch.object(
                self.verify, "check_release", return_value=(True, "release ok")
            ) as release,
            mock.patch.object(
                self.verify,
                "check_installed_smokes",
                return_value=(True, "smokes ok"),
            ) as smokes,
        ):
            ok, message = self.verify.check_evidence_review_plan(
                REPO_ROOT,
                execute_focused=True,
                final_readiness=True,
                release_version="2.0.0",
                result_cache=cache,
            )

        self.assertTrue(ok, message)
        unit.assert_called_once_with(REPO_ROOT)
        consistency.assert_called_once_with(REPO_ROOT)
        release.assert_called_once_with(REPO_ROOT, "2.0.0")
        smokes.assert_called_once_with(REPO_ROOT)
        self.assertEqual(
            set(cache), {"unit-tests", "consistency", "release", "installed-smokes"}
        )
        self.assertIn("executed=4", message)

    def test_final_diff_check_uses_default_branch_merge_base_range(self) -> None:
        proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with (
            mock.patch.object(
                self.verify, "_resolve_default_branch_ref", return_value="origin/main"
            ),
            mock.patch.object(self.verify, "_merge_base", return_value="abc123"),
            mock.patch.object(self.verify, "_run", return_value=proc) as run,
        ):
            ok, message = self.verify.check_git_diff(
                REPO_ROOT,
                final_readiness=True,
            )

        self.assertTrue(ok, message)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(["git", "diff", "--check", "abc123..HEAD"], commands)
        self.assertIn(["git", "diff", "--check", "--cached"], commands)
        self.assertIn(["git", "diff", "--check"], commands)
        self.assertIn(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            commands,
        )

    def test_final_diff_rejects_staged_and_untracked_work(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.email", "tests@example.invalid"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Elves Tests"], cwd=root, check=True
            )
            tracked = root / "tracked.txt"
            tracked.write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
            subprocess.run(["git", "switch", "-qc", "feature"], cwd=root, check=True)

            tracked.write_text("staged trailing whitespace   \n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
            ok, message = self.verify.check_git_diff(root, final_readiness=True)
            self.assertFalse(ok)
            self.assertIn("index", message)

            subprocess.run(["git", "reset", "-q", "HEAD", "tracked.txt"], cwd=root, check=True)
            tracked.write_text("base\n", encoding="utf-8")
            (root / "untracked.txt").write_text("clean contents\n", encoding="utf-8")
            ok, message = self.verify.check_git_diff(root, final_readiness=True)
            self.assertFalse(ok)
            self.assertIn("not clean", message)

    def test_ci_diff_rejects_committed_trailing_whitespace_without_requiring_clean(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "tests@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Elves Tests"], cwd=root, check=True)
            path = root / "tracked.txt"
            path.write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
            subprocess.run(["git", "switch", "-qc", "feature"], cwd=root, check=True)
            path.write_text("committed trailing whitespace   \n", encoding="utf-8")
            subprocess.run(["git", "commit", "-qam", "bad whitespace"], cwd=root, check=True)

            ok, message = self.verify.check_git_diff(
                root,
                final_readiness=False,
                cumulative_required=True,
            )

            self.assertFalse(ok)
            self.assertIn("cumulative", message)

    def test_cumulative_paths_include_index_worktree_and_untracked(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.email", "tests@example.invalid"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Elves Tests"], cwd=root, check=True
            )
            (root / "base.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "base.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
            subprocess.run(["git", "switch", "-qc", "feature"], cwd=root, check=True)
            (root / "base.txt").write_text("staged\n", encoding="utf-8")
            subprocess.run(["git", "add", "base.txt"], cwd=root, check=True)
            (root / "untracked.txt").write_text("new\n", encoding="utf-8")

            ok, paths, message = self.verify._cumulative_changed_paths(root)

            self.assertTrue(ok, message)
            self.assertEqual(paths, ["base.txt", "untracked.txt"])
            self.assertIn("index/worktree/untracked", message)

    def test_secret_scan_does_not_allow_file_wide_pattern_words(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            scripts = root / "scripts"
            scripts.mkdir()
            (scripts / "leak.py").write_text(
                'API_KEY="real-secret"\n# unrelated pattern and sentinel docs\n',
                encoding="utf-8",
            )
            ok, message = self.verify.check_secret_patterns(root)
            self.assertFalse(ok)
            self.assertIn("leak.py:1", message)

            (scripts / "leak.py").write_text(
                'PATTERN = "API_KEY="\nOPENROUTER_API_KEY=[REDACTED:value]\n',
                encoding="utf-8",
            )
            ok, message = self.verify.check_secret_patterns(root)
            self.assertTrue(ok, message)

    def test_secret_scan_catches_assignments_provider_tokens_cloud_keys_and_pem(self) -> None:
        cases = {
            "spaced assignment": 'OPENROUTER_API_KEY = "real-secret-value"\n',
            "yaml list assignment": "- API_KEY: real-secret-value\n",
            "hyphenated assignment": "API-KEY = real-secret-value\n",
            "sk project": "sk-proj-abcdefghijklmnopqrstuvwxyz\n",
            "sk service": "sk-svcacct-abcdefghijklmnopqrstuvwxyz\n",
            "github pat": "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456\n",
            "github oauth": "gho_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456\n",
            "github fine-grained": "github_pat_11ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890abcdefghijklmnopqrstuvwxyz\n",
            "github server token": "ghs_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456\n",
            "aws": "AKIAABCDEFGHIJKLMNOP\n",
            "bearer": "Authorization: Bearer abcdefghijklmnop\n",
            "pem": (
                "-----BEGIN RSA PRIVATE KEY-----\n"
                "MIIEowIBAAKCAQEAsecretmaterial\n"
                "-----END RSA PRIVATE KEY-----\n"
            ),
        }
        for label, leaked in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                scripts = root / "scripts"
                scripts.mkdir()
                # The scanner imports its shared patterns from the real scripts
                # package already loaded by the test module.
                (scripts / "leak.md").write_text(leaked, encoding="utf-8")
                with mock.patch.dict(sys.modules, clear=False):
                    ok, message = self.verify.check_secret_patterns(root)
                self.assertFalse(ok, message)

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            scripts = root / "scripts"
            scripts.mkdir()
            (scripts / "safe.md").write_text(
                "OPENROUTER_API_KEY = ${YOUR_API_KEY}\n"
                "TOKEN = [REDACTED:value]\n"
                "PATTERN = `API_KEY=`\n"
                "Authorization: Bearer YOUR_TOKEN_HERE\n"
                "AKIAIOSFODNN7EXAMPLE\n"
                "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxx\n"
                "github_pat_xxxxxxxxxxxxxxxxxxxxxxxxxxxx\n"
                "sk-proj-YOUR_API_KEY_PLACEHOLDER\n"
                "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n",
                encoding="utf-8",
            )
            ok, message = self.verify.check_secret_patterns(root)
            self.assertTrue(ok, message)

    def test_secret_scan_includes_tracked_env_templates_but_not_ignored_local_env(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
            (root / ".gitignore").write_text(".env.local\n", encoding="utf-8")
            template = root / ".env.example"
            template.write_text('OPENAI_API_KEY = "real-secret-value"\n', encoding="utf-8")
            (root / ".env.local").write_text(
                'OPENAI_API_KEY = "expected-local-secret"\n', encoding="utf-8"
            )
            subprocess.run(
                ["git", "add", ".gitignore", ".env.example"], cwd=root, check=True
            )

            ok, message = self.verify.check_secret_patterns(root)
            self.assertFalse(ok, message)
            self.assertIn(".env.example:1", message)

            template.write_text("OPENAI_API_KEY=${YOUR_API_KEY}\n", encoding="utf-8")
            ok, message = self.verify.check_secret_patterns(root)
            self.assertTrue(ok, message)

    def test_markdown_links_require_tracked_targets_and_real_anchors(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "tests@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Elves Tests"], cwd=root, check=True)
            docs = root / "docs"
            docs.mkdir()
            target = docs / "guide.md"
            target.write_text(
                "# Setup\n\n# Setup\n\n<a id=\"custom-anchor\"></a>\n",
                encoding="utf-8",
            )
            readme = root / "README.md"
            readme.write_text(
                "[first](docs/guide.md#setup) [duplicate](docs/guide.md#setup-1) "
                "[custom](docs/guide.md#custom-anchor)\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "README.md", "docs/guide.md"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "docs"], cwd=root, check=True)

            ok, message = self.verify.check_markdown_links(root)
            self.assertTrue(ok, message)

            readme.write_text("[missing](docs/guide.md#does-not-exist)\n", encoding="utf-8")
            ok, message = self.verify.check_markdown_links(root)
            self.assertFalse(ok)
            self.assertIn("missing anchor", message)

            untracked = docs / "untracked.md"
            untracked.write_text("# Exists\n", encoding="utf-8")
            readme.write_text(
                "[untracked][local]\n\n[local]: docs/untracked.md\n",
                encoding="utf-8",
            )
            ok, message = self.verify.check_markdown_links(root)
            self.assertFalse(ok)
            self.assertIn("not shipped/tracked", message)

            target.write_text(
                "# Setup\n\n```md\n# Fake Anchor\n```\n"
                "<!-- # Commented Anchor -->\n",
                encoding="utf-8",
            )
            readme.write_text("[fake](docs/guide.md#fake-anchor)\n", encoding="utf-8")
            ok, message = self.verify.check_markdown_links(root)
            self.assertFalse(ok)
            self.assertIn("missing anchor", message)

            (root / "README.md").write_text(
                "setup: API_KEY=real-secret\n",
                encoding="utf-8",
            )
            ok, message = self.verify.check_secret_patterns(root)
            self.assertFalse(ok)
            self.assertIn("README.md:1", message)

            (root / "README.md").write_text(
                "setup: `API_KEY=` followed by `${YOUR_API_KEY}`\n",
                encoding="utf-8",
            )
            ok, message = self.verify.check_secret_patterns(root)
            self.assertTrue(ok, message)

    def test_failed_continue_run_never_records_passing_preflight(self) -> None:
        success = (True, "ok")
        with (
            mock.patch.object(self.verify, "compile_scripts", return_value=(False, "bad")),
            mock.patch.object(self.verify, "check_shell", return_value=success),
            mock.patch.object(self.verify, "check_json", return_value=success),
            mock.patch.object(self.verify, "check_evidence_review_plan", return_value=success),
            mock.patch.object(self.verify, "check_consistency", return_value=success),
            mock.patch.object(self.verify, "check_release", return_value=success),
            mock.patch.object(self.verify, "check_public_api", return_value=success),
            mock.patch.object(self.verify, "check_git_diff", return_value=success),
            mock.patch.object(self.verify, "check_preflight_cache", return_value=success) as cache,
        ):
            code = self.verify.main(
                [
                    "--repo-root",
                    str(REPO_ROOT),
                    "--continue-on-error",
                    "--skip-tests",
                    "--skip-smokes",
                    "--json",
                ]
            )

        self.assertEqual(code, 1)
        cache.assert_not_called()

    def test_session_and_plan_flags_remain_optional_outside_final_mode(self) -> None:
        success = (True, "ok")
        with (
            mock.patch.object(self.verify, "compile_scripts", return_value=success),
            mock.patch.object(self.verify, "check_shell", return_value=success),
            mock.patch.object(self.verify, "check_json", return_value=success),
            mock.patch.object(self.verify, "check_evidence_review_plan", return_value=success),
            mock.patch.object(self.verify, "check_consistency", return_value=success),
            mock.patch.object(self.verify, "check_release", return_value=success),
            mock.patch.object(self.verify, "check_public_api", return_value=success),
            mock.patch.object(self.verify, "check_git_diff", return_value=success),
            mock.patch.object(self.verify, "check_preflight_cache", return_value=success),
            mock.patch.object(self.verify, "check_landing", return_value=success) as landing,
        ):
            code = self.verify.main(
                [
                    "--repo-root",
                    str(REPO_ROOT),
                    "--session",
                    "missing-session.json",
                    "--plan",
                    "missing-plan.md",
                    "--skip-tests",
                    "--skip-smokes",
                    "--json",
                ]
            )

        self.assertEqual(code, 0)
        landing.assert_not_called()


if __name__ == "__main__":
    unittest.main()
