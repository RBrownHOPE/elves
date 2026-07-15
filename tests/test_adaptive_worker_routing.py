from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cobbler_runtime.adapters import (  # noqa: E402
    build_session_create_invocation,
    build_session_resume_invocation,
)
from cobbler_runtime.native_worker import (  # noqa: E402
    build_native_worker_spec,
    native_worker_profiles,
    parse_codex_thread_id,
)
from cobbler_runtime.preferences import (  # noqa: E402
    global_preferences_path,
    load_preferences,
    reset_preferences,
    set_preference,
)
from cobbler_runtime.schema import ValidationIssue  # noqa: E402
from cobbler_runtime.worker_routing import (  # noqa: E402
    GROK_COMPLEX_MODEL,
    GROK_COMPOSER_MODEL,
    GrokCapabilities,
    decide_worker_route,
    probe_grok_capabilities,
)


class GlobalPreferencesTests(unittest.TestCase):
    def test_both_hosts_share_isolated_xdg_path_and_atomic_updates_preserve_unknowns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {"HOME": str(Path(tmp) / "home"), "XDG_CONFIG_HOME": str(Path(tmp) / "xdg")}
            path = global_preferences_path(env=env)
            self.assertEqual(path, Path(tmp) / "xdg" / "elves" / "config.json")
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps({"version": 1, "worker": {"provider": "auto"}, "future_safe": {"color": "blue"}}),
                encoding="utf-8",
            )
            set_preference("worker.provider", "grok", path=path)
            data = load_preferences(path)
            self.assertEqual(data["worker"]["provider"], "grok")
            self.assertEqual(data["future_safe"], {"color": "blue"})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(path.parent.glob(f".{path.name}.*")), [])

    def test_management_rejects_authority_and_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"version": 1, "merge_authority": True}), encoding="utf-8")
            with self.assertRaises(ValidationIssue) as caught:
                load_preferences(path)
            self.assertEqual(caught.exception.code, "unsafe_global_preference")
            reset_preferences(path=path)
            with self.assertRaises(ValidationIssue):
                set_preference("credentials.api_key", "secret", path=path)
            path.write_text(
                json.dumps({"version": 1, "future": {"github_token_value": "secret"}}),
                encoding="utf-8",
            )
            with self.assertRaises(ValidationIssue):
                load_preferences(path)


class RouteDecisionMatrixTests(unittest.TestCase):
    def decide(self, **overrides):
        params = {
            "host": "codex",
            "execution_reasoning": "medium",
            "review_risk": "standard",
        }
        params.update(overrides)
        return decide_worker_route(**params)

    def test_native_only_and_reasoning_matrix(self) -> None:
        for reasoning in ("low", "medium", "high"):
            with self.subTest(reasoning=reasoning):
                decision = self.decide(execution_reasoning=reasoning)
                self.assertEqual(decision.provider, "native")
                self.assertEqual(decision.worker_effort, reasoning)
                self.assertEqual(decision.worker_model_policy, "inherit_live_driver_model")
                self.assertFalse(decision.model_calls_made)

    def test_explicit_grok_regular_and_complex_model_policy(self) -> None:
        capabilities = GrokCapabilities(
            installed=True,
            authenticated=True,
            models=(GROK_COMPOSER_MODEL, GROK_COMPLEX_MODEL),
            goal_mode_qualified=True,
        )
        for reasoning, expected in (("low", GROK_COMPOSER_MODEL), ("medium", GROK_COMPOSER_MODEL), ("high", GROK_COMPLEX_MODEL)):
            decision = self.decide(
                execution_reasoning=reasoning,
                explicit_intent={"worker": {"provider": "grok", "allow_grok": True}},
                grok=capabilities,
            )
            self.assertEqual(decision.provider, "grok")
            self.assertEqual(decision.worker_model, expected)
            self.assertTrue(decision.goal_mode)

    def test_unavailable_and_repo_prohibited_fall_back_honestly(self) -> None:
        requested = {"worker": {"provider": "grok", "allow_grok": True}}
        unavailable = self.decide(explicit_intent=requested)
        self.assertEqual(unavailable.provider, "native")
        self.assertIn("unavailable", unavailable.fallback["reason"])
        prohibited = self.decide(
            explicit_intent=requested,
            repo_policy={"worker": {"allow_grok": False}},
            grok=GrokCapabilities(True, True, (GROK_COMPOSER_MODEL,), True),
        )
        self.assertEqual(prohibited.provider, "native")
        self.assertEqual(prohibited.fallback["reason"], "repository_policy_prohibits_grok")
        self.assertFalse(prohibited.goal_mode)

    def test_explicit_intent_beats_global_preference_and_reports_review_advisory(self) -> None:
        decision = self.decide(
            review_risk="high",
            driver_effort="medium",
            global_preferences={"worker": {"provider": "grok", "native_effort": "low"}},
            explicit_intent={"worker": {"provider": "native", "native_effort": "high"}},
        )
        self.assertEqual(decision.provider, "native")
        self.assertEqual(decision.worker_effort, "high")
        self.assertEqual(decision.provenance["provider"], "explicit_run_intent")
        self.assertIsNotNone(decision.advisory_driver_upgrade)

    def test_repository_policy_overrides_explicit_convenience_fields(self) -> None:
        decision = self.decide(
            global_preferences={"worker": {"provider": "grok", "native_effort": "low"}},
            explicit_intent={"worker": {"provider": "grok", "native_effort": "medium", "allow_grok": True}},
            repo_policy={"worker": {"provider": "native", "native_effort": "high", "allow_grok": False}},
        )
        self.assertEqual(decision.provider, "native")
        self.assertEqual(decision.worker_effort, "high")
        self.assertEqual(decision.provenance["provider"], "repository_policy")
        self.assertEqual(decision.provenance["worker_effort"], "repository_policy")

    @mock.patch("cobbler_runtime.worker_routing.shutil.which", return_value="/usr/bin/grok")
    def test_silent_grok_probe_separates_auth_models_and_goal_qualification(self, _which) -> None:
        def runner(argv, **_kwargs):
            if argv[-1] == "--version":
                return subprocess.CompletedProcess(argv, 0, "Grok Build 0.2.101\n", "")
            if argv[-1] == "models":
                return subprocess.CompletedProcess(argv, 0, f"{GROK_COMPOSER_MODEL}\n{GROK_COMPLEX_MODEL}\n", "")
            return subprocess.CompletedProcess(argv, 0, "Options:\n  /goal TUI only\n", "")
        result = probe_grok_capabilities(runner=runner)
        self.assertTrue(result.installed)
        self.assertTrue(result.authenticated)
        self.assertEqual(result.version, "0.2.101")
        self.assertIn(GROK_COMPOSER_MODEL, result.models)
        self.assertFalse(result.goal_mode_qualified)


class NativeWorkerGrammarTests(unittest.TestCase):
    def test_codex_create_resume_and_thread_capture_are_exact(self) -> None:
        spec = build_native_worker_spec(
            host="codex", worktree=REPO_ROOT, effort="low", requested_model="current-model"
        )
        self.assertEqual(spec.argv[:3], ("codex", "exec", "--json"))
        self.assertIn("-C", spec.argv)
        self.assertNotIn("--last", spec.argv)
        thread = parse_codex_thread_id('{"type":"thread.started","thread_id":"thread-123"}\n')
        resumed = build_native_worker_spec(
            host="codex", worktree=REPO_ROOT, effort="medium", requested_model="current-model", session_id=thread
        )
        self.assertEqual(resumed.argv[:3], ("codex", "exec", "resume"))
        self.assertIn("thread-123", resumed.argv)
        self.assertNotIn("-C", resumed.argv)
        self.assertEqual(resumed.cwd, str(REPO_ROOT.resolve()))

    def test_claude_create_resume_profiles_are_separate_and_exact(self) -> None:
        created = build_native_worker_spec(
            host="claude", worktree=REPO_ROOT, effort="low", requested_model="current-model"
        )
        self.assertIn("--session-id", created.argv)
        self.assertNotIn("-", created.argv)
        self.assertIn("--effort", created.argv)
        sid = created.argv[created.argv.index("--session-id") + 1]
        resumed = build_native_worker_spec(
            host="claude", worktree=REPO_ROOT, effort="high", requested_model="current-model", session_id=sid
        )
        self.assertIn("--resume", resumed.argv)
        self.assertNotIn("--continue", resumed.argv)
        self.assertTrue(resumed.separate_session)
        self.assertFalse(resumed.cache_handoff)
        profiles = native_worker_profiles()
        self.assertEqual(profiles["codex"]["model_policy"], profiles["claude"]["model_policy"])
        self.assertFalse(profiles["codex"]["worker_merge_authority"])

    def test_supervised_fallback_refuses_to_infer_the_driver_model(self) -> None:
        with self.assertRaises(ValidationIssue) as caught:
            build_native_worker_spec(host="codex", worktree=REPO_ROOT, effort="low")
        self.assertEqual(caught.exception.code, "current_worker_model_required")

    def test_generic_session_builders_match_supported_native_grammar(self) -> None:
        claude = build_session_create_invocation(adapter="claude-code", profile="claude-code")
        self.assertIn("--session-id", claude.argv)
        self.assertNotIn("--session-create", claude.argv)
        codex = build_session_create_invocation(adapter="codex-fugu", profile="codex-fugu")
        self.assertNotIn("--session-create", codex.argv)
        resumed = build_session_resume_invocation(
            adapter="codex-fugu", profile="codex-fugu", session_id="exact-123", cwd=str(REPO_ROOT)
        )
        self.assertEqual(resumed.argv[:3], ("codex", "exec", "resume"))
        self.assertNotIn("--cwd", resumed.argv)
        self.assertEqual(resumed.cwd, str(REPO_ROOT))

    def test_cli_preferences_and_route_are_isolated_and_inspectable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["XDG_CONFIG_HOME"] = tmp
            cli = REPO_ROOT / "scripts" / "cobbler_agents.py"
            set_result = subprocess.run(
                [sys.executable, str(cli), "preferences", "set", "worker.provider", "native", "--json"],
                env=env, text=True, capture_output=True, check=False,
            )
            self.assertEqual(set_result.returncode, 0, set_result.stderr)
            route = subprocess.run(
                [sys.executable, str(cli), "route-worker", "--host", "claude", "--execution-reasoning", "high", "--review-risk", "high", "--json"],
                env=env, text=True, capture_output=True, check=False,
            )
            self.assertEqual(route.returncode, 0, route.stderr)
            payload = json.loads(route.stdout)
            self.assertEqual(payload["decision"]["provider"], "native")
            self.assertEqual(payload["decision"]["worker_transport"], "claude_code")


if __name__ == "__main__":
    unittest.main()
