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
    native_worker_paths,
    native_worker_profiles,
    native_worker_status,
    parse_codex_thread_id,
)
from cobbler_runtime.full_run import FullRunState, build_full_run_argv  # noqa: E402
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
    GrokCapabilityEvidence,
    GrokCapabilities,
    decide_worker_route,
    probe_grok_capabilities,
    discover_repository_worker_policy,
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
            for body in (
                {"version": 1, "future": {"always_approve": True}},
                {"version": 1, "future": {"permission_mode": "safe"}},
                {"version": 1, "future": {"mode": "bypassPermissions"}},
            ):
                path.write_text(json.dumps(body), encoding="utf-8")
                with self.subTest(body=body), self.assertRaises(ValidationIssue):
                    load_preferences(path)

    def test_relative_xdg_config_home_is_rejected(self) -> None:
        with self.assertRaises(ValidationIssue) as caught:
            global_preferences_path(env={"HOME": "/tmp/home", "XDG_CONFIG_HOME": ".config"})
        self.assertEqual(caught.exception.code, "relative_xdg_config_home")


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
            default_model=GROK_COMPOSER_MODEL,
            goal_entrypoint_advertised=True,
            goal_mode_behaviorally_verified=True,
            goal_behavioral_evidence="fixture:headless-goal-contract-v1",
        )
        for reasoning, expected in (("low", GROK_COMPOSER_MODEL), ("medium", GROK_COMPOSER_MODEL), ("high", GROK_COMPOSER_MODEL)):
            decision = self.decide(
                execution_reasoning=reasoning,
                explicit_intent={"worker": {"provider": "grok"}},
                grok=capabilities,
            )
            self.assertEqual(decision.provider, "grok")
            self.assertEqual(decision.worker_model, expected)
            self.assertTrue(decision.goal_mode)
        explicit_complex = self.decide(
            execution_reasoning="high",
            explicit_intent={"worker": {"provider": "grok", "grok_model": GROK_COMPLEX_MODEL}},
            grok=capabilities,
        )
        self.assertEqual(explicit_complex.worker_model, GROK_COMPLEX_MODEL)
        self.assertEqual(explicit_complex.worker_model_policy, "explicit_catalog_model_pin")

    def test_unavailable_and_repo_prohibited_fall_back_honestly(self) -> None:
        requested = {"worker": {"provider": "grok"}}
        unavailable = self.decide(explicit_intent=requested)
        self.assertEqual(unavailable.provider, "native")
        self.assertIn("unavailable", unavailable.fallback["reason"])
        prohibited = self.decide(
            explicit_intent=requested,
            repo_policy={"worker": {"allow_grok": False}},
            grok=GrokCapabilities(installed=True, authenticated=True, models=(GROK_COMPOSER_MODEL,), default_model=GROK_COMPOSER_MODEL, goal_entrypoint_advertised=True, goal_mode_behaviorally_verified=True, goal_behavioral_evidence="fixture:verified"),
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

    def test_explicit_native_beats_repository_auto_allow_while_veto_stays_absolute(self) -> None:
        decision = self.decide(
            global_preferences={"worker": {"provider": "grok", "native_effort": "low"}},
            explicit_intent={"worker": {"provider": "native", "native_effort": "medium"}},
            repo_policy={"worker": {"provider": "auto", "native_effort": "high", "allow_grok": True}},
        )
        self.assertEqual(decision.provider, "native")
        self.assertEqual(decision.worker_effort, "medium")
        self.assertEqual(decision.provenance["provider"], "explicit_run_intent")

    def test_global_grok_is_remembered_consent_but_repository_veto_wins(self) -> None:
        caps = GrokCapabilities(
            installed=True, authenticated=True, models=(GROK_COMPOSER_MODEL,), default_model=GROK_COMPOSER_MODEL,
            goal_mode_behaviorally_verified=True, goal_behavioral_evidence="fixture:verified",
        )
        selected = self.decide(global_preferences={"worker": {"provider": "grok"}}, grok=caps)
        self.assertEqual(selected.provider, "grok")
        self.assertEqual(selected.provenance["grok_consent"], "global_provider_preference")
        vetoed = self.decide(global_preferences={"worker": {"provider": "grok"}}, repo_policy={"worker": {"allow_grok": False}}, grok=caps)
        self.assertEqual(vetoed.provider, "native")
        self.assertEqual(vetoed.provenance["grok_safety_veto"], "repository_policy")

    def test_repository_discovery_reads_target_config_without_treating_allow_as_consent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.json").write_text(json.dumps({"worker": {"provider": "auto", "allow_grok": True}}), encoding="utf-8")
            policy, source = discover_repository_worker_policy(root)
            self.assertEqual(policy["worker"]["provider"], "auto")
            self.assertEqual(source, str((root / "config.json").resolve()))
            decision = self.decide(repo_policy=policy, grok=GrokCapabilities(installed=True, authenticated=True, models=(GROK_COMPOSER_MODEL,)))
            self.assertEqual(decision.provider, "native")

    @mock.patch("cobbler_runtime.worker_routing.shutil.which", return_value="/usr/bin/grok")
    def test_silent_grok_probe_separates_auth_models_and_goal_qualification(self, _which) -> None:
        def runner(argv, **_kwargs):
            if argv[-2:] == ["version", "--json"]:
                return subprocess.CompletedProcess(
                    argv, 0, '{"currentVersion":"0.2.101 (5bc4b5dfadcf)"}\n', ""
                )
            if argv[-1] == "models":
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    f"Default model: {GROK_COMPOSER_MODEL}\n"
                    "Available models:\n"
                    f"  * {GROK_COMPOSER_MODEL} (default)\n"
                    f"  * {GROK_COMPLEX_MODEL}\n",
                    "",
                )
            if argv[1:4] == ["agent", "stdio", "--help"]:
                return subprocess.CompletedProcess(argv, 0, "Run the agent over stdio\n", "")
            if argv[-1] == "/goal status":
                session_id = argv[argv.index("--session-id") + 1]
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    json.dumps({"type": "text", "data": "No goal is currently set. Use /goal <objective>."})
                    + "\n"
                    + json.dumps({"type": "end", "sessionId": session_id})
                    + "\n",
                    "",
                )
            return subprocess.CompletedProcess(
                argv,
                0,
                "Options:\n"
                "  --prompt-file <PATH>\n"
                "  --cwd <PATH>\n"
                "  --model <MODEL>\n"
                "  --permission-mode <MODE>\n"
                "  --always-approve\n"
                "  --reasoning-effort <EFFORT>\n"
                "  --max-turns <N>\n"
                "  --no-subagents\n"
                "  --no-memory\n"
                "  --disable-web-search\n"
                "  --check\n"
                "  --session-id <UUID>\n"
                "  --resume <UUID>\n"
                "  --output-format <text|streaming-json>\n"
                "  --json-schema <SCHEMA>\n"
                "  /goal TUI only\n",
                "",
            )
        with tempfile.TemporaryDirectory() as tmp:
            auth = Path(tmp) / "auth.json"
            auth.write_text("fixture-not-a-credential", encoding="utf-8")
            result = probe_grok_capabilities(runner=runner, goal_auth_path=auth)
        self.assertTrue(result.installed)
        self.assertTrue(result.authenticated)
        self.assertEqual(result.version, "0.2.101")
        self.assertEqual(result.installed_build_commit, "5bc4b5dfadcf")
        self.assertEqual(result.default_model, GROK_COMPOSER_MODEL)
        self.assertIn(GROK_COMPOSER_MODEL, result.models)
        self.assertFalse(result.goal_entrypoint_advertised)
        self.assertFalse(result.goal_mode_behaviorally_verified)
        snapshot = result.safe_snapshot()
        self.assertEqual(snapshot["capabilities"]["session_id"]["state"], "proven")
        self.assertEqual(snapshot["capabilities"]["new_session"]["state"], "refuted")
        self.assertEqual(snapshot["capabilities"]["goal_command_resolution"]["state"], "proven")
        self.assertEqual(snapshot["capabilities"]["goal_behavior"]["state"], "unavailable")
        self.assertEqual(snapshot["capabilities"]["acp"]["state"], "proven")
        self.assertNotIn("stdout", json.dumps(snapshot))

        with tempfile.TemporaryDirectory() as tmp:
            auth = Path(tmp) / "auth.json"
            auth.write_text("fixture-not-a-credential", encoding="utf-8")
            verified = probe_grok_capabilities(
                runner=runner,
                goal_auth_path=auth,
                goal_behavioral_evidence="canary:terminal:end:exact-session",
            )
        self.assertTrue(verified.goal_mode_behaviorally_verified)
        self.assertEqual(
            verified.goal_behavioral_evidence,
            "canary:terminal:end:exact-session",
        )

    @mock.patch("cobbler_runtime.worker_routing.shutil.which", return_value="/usr/bin/grok")
    def test_grok_snapshot_does_not_promote_network_fallback_or_auth_diagnostics(self, _which) -> None:
        secret = "oauth-secret-must-not-survive"

        def runner(argv, **_kwargs):
            if argv[-2:] == ["version", "--json"]:
                return subprocess.CompletedProcess(argv, 0, '{"currentVersion":"0.2.101"}', "")
            if argv[-1] == "models":
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    "You are logged in.\nDefault model: grok-build\n* grok-build (default)\n",
                    f"Failed to fetch models: network error; diagnostic={secret}",
                )
            if argv[1:4] == ["agent", "stdio", "--help"]:
                return subprocess.CompletedProcess(argv, 0, "Run the agent over stdio", "")
            return subprocess.CompletedProcess(argv, 0, "--session-id --resume --output-format", "")

        result = probe_grok_capabilities(runner=runner)
        snapshot = result.safe_snapshot()
        self.assertFalse(result.authenticated)
        self.assertEqual(result.models, ())
        self.assertIsNone(result.default_model)
        self.assertEqual(snapshot["capabilities"]["model_catalog"]["reason"], "live_catalog_unavailable")
        self.assertEqual(snapshot["capabilities"]["goal_command_resolution"]["reason"], "narrow_auth_projection_not_provided")
        self.assertEqual(snapshot["capabilities"]["goal_behavior"]["reason"], "terminal_objective_canary_not_recorded")
        self.assertNotIn(secret, json.dumps(snapshot))

    @mock.patch("cobbler_runtime.worker_routing.shutil.which", return_value="/usr/bin/grok")
    def test_goal_status_rejects_nested_positive_usage(self, _which) -> None:
        def runner(argv, **_kwargs):
            if argv[-2:] == ["version", "--json"]:
                return subprocess.CompletedProcess(argv, 0, '{"currentVersion":"0.2.101"}', "")
            if argv[-1] == "models":
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    "Default model: grok-build\nAvailable models:\n  * grok-build (default)\n",
                    "",
                )
            if argv[1:4] == ["agent", "stdio", "--help"]:
                return subprocess.CompletedProcess(argv, 0, "Run the agent over stdio", "")
            if argv[-1] == "/goal status":
                sid = argv[argv.index("--session-id") + 1]
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    json.dumps({"type": "text", "data": "No goal is currently set."})
                    + "\n"
                    + json.dumps({"type": "end", "sessionId": sid, "meta": {"usage": {"inputTokens": 1}}})
                    + "\n",
                    "",
                )
            return subprocess.CompletedProcess(argv, 0, "--session-id --resume", "")

        with tempfile.TemporaryDirectory() as tmp:
            auth = Path(tmp) / "auth.json"
            auth.write_text("fixture", encoding="utf-8")
            caps = probe_grok_capabilities(runner=runner, goal_auth_path=auth)
        self.assertEqual(
            caps.capability("goal_command_resolution").reason,
            "unexpected_model_events",
        )
        self.assertFalse(caps.goal_mode_behaviorally_verified)

    @mock.patch("cobbler_runtime.worker_routing.shutil.which", return_value="/usr/bin/grok")
    def test_catalog_parser_rejects_diagnostics_auth_text_and_uncontained_default(self, _which) -> None:
        outputs = (
            "You are not authenticated.\nDefault model: grok-build\nAvailable models:\n  * grok-build (default)\n",
            "Default model: absent\nAvailable models:\n  * grok-build\n",
            "Default model: grok-build\nAvailable models:\ndiagnostic mentions grok-build\n",
        )
        for output in outputs:
            with self.subTest(output=output):
                def runner(argv, **_kwargs):
                    if argv[-2:] == ["version", "--json"]:
                        return subprocess.CompletedProcess(argv, 0, '{"currentVersion":"0.2.101"}', "")
                    if argv[-1] == "models":
                        return subprocess.CompletedProcess(argv, 0, output, "")
                    if argv[1:4] == ["agent", "stdio", "--help"]:
                        return subprocess.CompletedProcess(argv, 0, "Run the agent over stdio", "")
                    return subprocess.CompletedProcess(argv, 0, "--session-id --resume", "")
                caps = probe_grok_capabilities(runner=runner)
                self.assertFalse(caps.authenticated)
                self.assertEqual(caps.models, ())
                self.assertIsNone(caps.default_model)

    def test_advertised_goal_is_not_behaviorally_verified(self) -> None:
        caps = GrokCapabilities(installed=True, authenticated=True, models=(GROK_COMPOSER_MODEL,), default_model=GROK_COMPOSER_MODEL, goal_entrypoint_advertised=True)
        decision = self.decide(explicit_intent={"worker": {"provider": "grok"}}, grok=caps)
        self.assertEqual(decision.provider, "grok")
        self.assertEqual(decision.fallback["reason"], "goal_mode_not_behaviorally_verified")
        self.assertEqual(decision.fallback["actual"], "grok_packet_prompt")
        self.assertFalse(decision.goal_mode)
        unrecorded = GrokCapabilities(
            installed=True, authenticated=True, models=(GROK_COMPOSER_MODEL,), default_model=GROK_COMPOSER_MODEL,
            goal_entrypoint_advertised=True, goal_mode_behaviorally_verified=True,
        )
        self.assertFalse(self.decide(explicit_intent={"worker": {"provider": "grok"}}, grok=unrecorded).goal_mode)

    def test_reduced_grok_install_falls_back_with_capability_reason(self) -> None:
        caps = GrokCapabilities(
            installed=True,
            authenticated=True,
            models=(GROK_COMPOSER_MODEL,),
            default_model=GROK_COMPOSER_MODEL,
            capability_ledger=(
                ("prompt_file", GrokCapabilityEvidence("proven", "fixture", "ok")),
                ("cwd", GrokCapabilityEvidence("proven", "fixture", "ok")),
                ("model", GrokCapabilityEvidence("proven", "fixture", "ok")),
                ("permission_mode", GrokCapabilityEvidence("proven", "fixture", "ok")),
                ("always_approve", GrokCapabilityEvidence("proven", "fixture", "ok")),
                ("reasoning_effort", GrokCapabilityEvidence("proven", "fixture", "ok")),
                ("max_turns", GrokCapabilityEvidence("proven", "fixture", "ok")),
                ("output_format", GrokCapabilityEvidence("proven", "fixture", "ok")),
                ("session_id", GrokCapabilityEvidence("refuted", "fixture", "not_advertised_by_help")),
                ("streaming_json", GrokCapabilityEvidence("proven", "fixture", "ok")),
            ),
        )
        decision = self.decide(
            explicit_intent={"worker": {"provider": "grok"}},
            grok=caps,
        )
        self.assertEqual(decision.provider, "native")
        self.assertEqual(
            decision.fallback["reason"],
            "capability_unavailable:session_id:not_advertised_by_help",
        )

    def test_catalog_selection_never_invents_auto_or_legacy_models(self) -> None:
        caps = GrokCapabilities(
            installed=True,
            authenticated=True,
            models=(GROK_COMPOSER_MODEL,),
            default_model=GROK_COMPOSER_MODEL,
        )
        for unavailable in ("auto", "grok-code-fast-1", GROK_COMPLEX_MODEL):
            with self.subTest(unavailable=unavailable):
                decision = self.decide(
                    explicit_intent={
                        "worker": {
                            "provider": "grok",
                            "grok_model": unavailable,
                        }
                    },
                    grok=caps,
                )
                self.assertEqual(decision.provider, "native")
                self.assertEqual(
                    decision.fallback["reason"],
                    f"model_unavailable:{unavailable}",
                )

    def test_route_model_reaches_production_full_run_argv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            packet = Path(tmp) / "packet.md"
            packet.write_text("fixture\n", encoding="utf-8")
            caps = GrokCapabilities(
                installed=True, authenticated=True, models=(GROK_COMPOSER_MODEL, GROK_COMPLEX_MODEL),
                default_model=GROK_COMPOSER_MODEL,
                goal_mode_behaviorally_verified=True, goal_behavioral_evidence="fixture:verified",
            )
            for reasoning, expected in (("medium", GROK_COMPOSER_MODEL), ("high", GROK_COMPLEX_MODEL)):
                intent = {"worker": {"provider": "grok"}}
                if reasoning == "high":
                    intent["worker"]["grok_model"] = GROK_COMPLEX_MODEL
                decision = self.decide(execution_reasoning=reasoning, explicit_intent=intent, grok=caps)
                state = FullRunState(session_id="11111111-1111-1111-1111-111111111111", branch="feature", start_head="a" * 40, worktree=tmp, packet_path=str(packet), model=decision.worker_model or "")
                with mock.patch("cobbler_runtime.implement.detect_native_grok_goal", return_value={"mode": "headless_compatible_fallback"}):
                    argv = build_full_run_argv(state)
                self.assertEqual(argv[argv.index("--model") + 1], expected)

    def test_full_run_state_grok_default_defers_to_live_catalog(self) -> None:
        state = FullRunState(
            session_id="exact-session", branch="feature", start_head="a" * 40,
            worktree=str(REPO_ROOT), packet_path=str(REPO_ROOT / "README.md"),
        )
        self.assertEqual(state.model, "auto")


class NativeWorkerGrammarTests(unittest.TestCase):
    def test_status_treats_missing_process_start_identity_as_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path, _ = native_worker_paths(root, "unknown-start")
            state_path.parent.mkdir(parents=True)
            state_path.write_text(
                json.dumps(
                    {
                        "status": "running",
                        "pid": 123,
                        "pid_start": None,
                        "supervisor_pid": 456,
                        "supervisor_pid_start": None,
                    }
                ),
                encoding="utf-8",
            )
            state_path.chmod(0o600)

            with (
                mock.patch("cobbler_runtime.native_worker._process_start", return_value=None),
                mock.patch("cobbler_runtime.native_worker.os.kill", return_value=None),
            ):
                status = native_worker_status(root, "unknown-start")

            self.assertEqual(status["status"], "running")
            self.assertIsNone(status["process_identity_matches"])
            self.assertIsNone(status["supervisor_identity_matches"])

    def test_status_detects_lost_processes_without_start_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path, _ = native_worker_paths(root, "unknown-start-lost")
            state_path.parent.mkdir(parents=True)
            state_path.write_text(
                json.dumps(
                    {
                        "status": "running",
                        "pid": 123,
                        "pid_start": None,
                        "supervisor_pid": 456,
                        "supervisor_pid_start": None,
                    }
                ),
                encoding="utf-8",
            )
            state_path.chmod(0o600)

            with (
                mock.patch("cobbler_runtime.native_worker._process_start", return_value=None),
                mock.patch(
                    "cobbler_runtime.native_worker.os.kill", side_effect=ProcessLookupError
                ),
            ):
                status = native_worker_status(root, "unknown-start-lost")

            self.assertEqual(status["status"], "failed")
            self.assertEqual(status["failure_reason"], "supervisor_and_child_identity_lost")

    def test_status_rereads_terminal_state_before_reporting_lost_processes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path, _ = native_worker_paths(root, "terminal-race")
            state_path.parent.mkdir(parents=True)
            running = {
                "status": "running",
                "pid": 123,
                "pid_start": "child-start",
                "supervisor_pid": 456,
                "supervisor_pid_start": "supervisor-start",
            }
            state_path.write_text(json.dumps(running), encoding="utf-8")
            state_path.chmod(0o600)
            original_read_text = Path.read_text
            reads = 0

            def terminal_after_brief_delay(path: Path, *args: object, **kwargs: object) -> str:
                nonlocal reads
                if path == state_path:
                    reads += 1
                    if reads == 3:
                        return json.dumps({**running, "status": "complete", "exit_code": 0})
                return original_read_text(path, *args, **kwargs)

            with (
                mock.patch("cobbler_runtime.native_worker._process_start", return_value=None),
                mock.patch("cobbler_runtime.native_worker.os.kill", side_effect=ProcessLookupError),
                mock.patch.object(Path, "read_text", terminal_after_brief_delay),
            ):
                status = native_worker_status(root, "terminal-race")

            self.assertEqual(status["status"], "complete")
            self.assertEqual(status["exit_code"], 0)

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
        self.assertEqual(resumed.argv[:2], ("codex", "exec"))
        self.assertIn("resume", resumed.argv)
        self.assertIn("thread-123", resumed.argv)
        self.assertNotIn("-C", resumed.argv)
        self.assertIn("--sandbox", resumed.argv)
        if resumed.git_write_roots:
            self.assertIn("--add-dir", resumed.argv)
            for root in resumed.git_write_roots:
                self.assertIn(root, resumed.argv)
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
        if resumed.git_write_roots:
            self.assertIn("--add-dir", resumed.argv)
            for root in resumed.git_write_roots:
                self.assertIn(root, resumed.argv)
        profiles = native_worker_profiles()
        self.assertEqual(profiles["codex"]["model_policy"], profiles["claude"]["model_policy"])
        self.assertFalse(profiles["codex"]["worker_merge_authority"])
        self.assertFalse(profiles["codex"]["visibility_ready"])
        self.assertNotIn("live_stream", profiles["codex"])

    def test_linked_worktree_adds_only_external_git_metadata_for_both_hosts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            main = Path(tmp) / "main"
            linked = Path(tmp) / "linked"
            subprocess.run(["git", "init", "-q", str(main)], check=True)
            subprocess.run(["git", "-C", str(main), "config", "user.name", "Elves Test"], check=True)
            subprocess.run(["git", "-C", str(main), "config", "user.email", "elves@example.invalid"], check=True)
            (main / "tracked.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(main), "add", "tracked.txt"], check=True)
            subprocess.run(["git", "-C", str(main), "commit", "-qm", "base"], check=True)
            subprocess.run(
                ["git", "-C", str(main), "worktree", "add", "-qb", "feat/linked", str(linked)],
                check=True,
            )
            expected_root = str((main / ".git").resolve())
            codex = build_native_worker_spec(
                host="codex",
                worktree=linked,
                effort="medium",
                requested_model="current-model",
                session_id="thread-123",
            )
            claude = build_native_worker_spec(
                host="claude",
                worktree=linked,
                effort="medium",
                requested_model="current-model",
                session_id="11111111-1111-1111-1111-111111111111",
            )
            for spec in (codex, claude):
                self.assertEqual(spec.git_write_roots, (expected_root,))
                self.assertIn("--add-dir", spec.argv)
                self.assertIn(expected_root, spec.argv)
                self.assertIn("workspace-write", spec.argv) if spec.host == "codex" else self.assertIn("acceptEdits", spec.argv)
            self.assertLess(codex.argv.index("--add-dir"), codex.argv.index("resume"))

    def test_standalone_checkout_needs_no_additional_git_write_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkout = Path(tmp) / "checkout"
            subprocess.run(["git", "init", "-q", str(checkout)], check=True)
            for host in ("codex", "claude"):
                spec = build_native_worker_spec(
                    host=host,
                    worktree=checkout,
                    effort="medium",
                    requested_model="current-model",
                )
                self.assertEqual(spec.git_write_roots, ())
                self.assertNotIn("--add-dir", spec.argv)

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

        grok = build_session_create_invocation(adapter="grok-build", profile="grok-build")
        self.assertIsNotNone(grok.session_id)
        self.assertIn("--session-id", grok.argv)
        self.assertIn(grok.session_id or "", grok.argv)
        self.assertNotIn("--new-session", grok.argv)
        self.assertEqual(len((grok.session_id or "").split("-")), 5)

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
            self.assertIn("grok_capabilities", payload)
            self.assertNotIn("stdout", json.dumps(payload["grok_capabilities"]))

    def test_fixture_supervisor_binds_private_follow_log_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = root / "worker.py"
            packet = root / "packet.md"
            fixture.write_text(
                "import sys, time\n"
                "print('{\"type\":\"thread.started\",\"thread_id\":\"fixture-thread\"}', flush=True)\n"
                "print('fixture stderr', file=sys.stderr, flush=True)\n"
                "assert sys.stdin.read() == 'packet body\\n'\n"
                "time.sleep(0.1)\n",
                encoding="utf-8",
            )
            packet.write_text("packet body\n", encoding="utf-8")
            cli = REPO_ROOT / "scripts" / "cobbler_agents.py"
            command = [
                sys.executable, str(cli), "native-worker", "launch", "--host", "fixture",
                "--worktree", str(root), "--effort", "low", "--model", "fixture-model",
                "--fixture-script", str(fixture), "--repo-root", str(root), "--run-id", "fixture-run",
                "--packet", str(packet), "--json",
            ]
            launched = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertEqual(launched.returncode, 0, launched.stderr)
            launch_payload = json.loads(launched.stdout)
            self.assertTrue(launch_payload["worker"]["visibility_ready"])
            self.assertIn("native-worker follow", launch_payload["worker"]["watcher_command"])
            status_payload = None
            for _ in range(50):
                status = subprocess.run(
                    [sys.executable, str(cli), "native-worker", "status", "--repo-root", str(root), "--run-id", "fixture-run", "--json"],
                    text=True, capture_output=True, check=False,
                )
                status_payload = json.loads(status.stdout)["worker"]
                if status_payload["status"] in {"complete", "failed"}:
                    break
                import time
                time.sleep(0.05)
            supervisor_detail = Path(status_payload["supervisor_log"]).read_text(encoding="utf-8")
            self.assertEqual(status_payload["status"], "complete", supervisor_detail)
            self.assertEqual(status_payload["worktree"], str(root.resolve()))
            self.assertIsNotNone(status_payload["pid"])
            log_path = Path(status_payload["follow_log"])
            self.assertEqual(log_path.stat().st_mode & 0o777, 0o600)
            followed = subprocess.run(
                [sys.executable, str(cli), "native-worker", "follow", "--repo-root", str(root), "--run-id", "fixture-run", "--no-wait"],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(followed.returncode, 0, followed.stderr)
            self.assertIn("fixture stderr", followed.stdout)


if __name__ == "__main__":
    unittest.main()
