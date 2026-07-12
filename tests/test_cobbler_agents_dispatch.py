from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"


def _ensure_import_path() -> None:
    scripts = str(SCRIPTS)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)


_ensure_import_path()

from cobbler_runtime import context as context_mod  # noqa: E402
from cobbler_runtime import dispatch as dispatch_mod  # noqa: E402
from cobbler_runtime.adapters import (  # noqa: E402
    build_readonly_invocation,
    parse_role_report,
)
from cobbler_runtime.context import (  # noqa: E402
    build_context_packet,
    council_artifact_root,
    ensure_private_dir,
    redact_text,
    scrub_environment,
    write_json_artifact,
)
from cobbler_runtime.dispatch import (  # noqa: E402
    LaneSpec,
    evaluate_quorum,
    run_council_sync,
    run_lightweight_review_sync,
)
from cobbler_runtime.schema import ValidationIssue  # noqa: E402


def _write_fake_lane_script(
    path: Path,
    *,
    role: str,
    sleep_s: float = 0.15,
    actual_model: str = "fake-model",
    exit_code: int = 0,
    malformed: bool = False,
    stderr_warning: str | None = None,
    hang_s: float | None = None,
) -> Path:
    """Create an argv-safe fake lane executable that emits a role report."""
    report = {
        "role": role,
        "verdict": "pass",
        "confidence": "high",
        "key_findings": [f"finding-from-{role}"],
        "evidence": ["fixture"],
        "risks": [],
        "recommended_actions": ["host synthesis"],
        "open_questions": [],
        "actual_model": actual_model,
    }
    body = textwrap.dedent(
        f"""\
        #!/usr/bin/env python3
        import json, sys, time
        sleep_s = {sleep_s!r}
        hang_s = {hang_s!r}
        if hang_s is not None:
            time.sleep(float(hang_s))
        else:
            time.sleep(float(sleep_s))
        stderr_warning = {stderr_warning!r}
        if stderr_warning:
            print(stderr_warning, file=sys.stderr)
        malformed = {malformed!r}
        if malformed:
            print("not-json-at-all")
            sys.exit({exit_code})
        print(json.dumps({report!r}))
        sys.exit({exit_code})
        """
    )
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class ContextRedactionTests(unittest.TestCase):
    def test_redact_text_masks_secret_values_and_reports_pattern_names(self) -> None:
        raw = "Authorization: Bearer supersecrettoken123 and sk-abcdefghijklmnop"
        result = redact_text(raw)
        self.assertNotIn("supersecrettoken123", result.text)
        self.assertNotIn("sk-abcdefghijklmnop", result.text)
        self.assertIn("REDACTED", result.text)
        self.assertTrue(set(result.redacted_patterns) & {"bearer_token", "sk_token"})

    def test_build_context_packet_redacts_task_and_sets_forbidden_actions(self) -> None:
        packet = build_context_packet(
            task="use key sk-thisisnotarealkey0001 carefully",
            role="architect",
            plan_path="docs/plans/example.md",
            head_sha="abc123",
            relevant_files=["scripts/cobbler_agents.py"],
        )
        self.assertNotIn("sk-thisisnotarealkey0001", packet.task)
        self.assertIn("read-only", packet.scope.lower().replace("_", "-") + packet.mode)
        self.assertIn("git_push", packet.forbidden_actions)
        self.assertIn("role", packet.output_schema)
        self.assertEqual(packet.head_sha, "abc123")
        self.assertEqual(packet.plan_path, "docs/plans/example.md")
        payload = packet.to_dict()
        self.assertNotIn("sk-thisisnotarealkey0001", json.dumps(payload))

    def test_scrub_environment_strips_secret_names_keeps_allowlist(self) -> None:
        parent = {
            "PATH": "/usr/bin",
            "HOME": "/tmp/home",
            "OPENROUTER_API_KEY": "secret-value-must-not-appear",
            "GITHUB_TOKEN": "ghp_secretvalue",
            "AWS_SECRET_ACCESS_KEY": "aws-secret",
            "MY_CUSTOM_TOKEN": "tok",
            "LANG": "en_US.UTF-8",
            "UNRELATED_FOO": "bar",
        }
        result = scrub_environment(parent)
        self.assertIn("PATH", result.env)
        self.assertIn("HOME", result.env)
        self.assertIn("LANG", result.env)
        self.assertNotIn("OPENROUTER_API_KEY", result.env)
        self.assertNotIn("GITHUB_TOKEN", result.env)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", result.env)
        self.assertNotIn("MY_CUSTOM_TOKEN", result.env)
        self.assertNotIn("UNRELATED_FOO", result.env)
        # Names only — values never appear in metadata.
        meta = json.dumps(result.to_dict())
        self.assertNotIn("secret-value-must-not-appear", meta)
        self.assertNotIn("ghp_secretvalue", meta)
        self.assertNotIn("aws-secret", meta)
        self.assertIn("OPENROUTER_API_KEY", result.stripped_names)
        self.assertIn("GITHUB_TOKEN", result.stripped_names)

    def test_secret_name_not_kept_even_if_on_extra_allowlist(self) -> None:
        parent = {"OPENAI_API_KEY": "nope", "PATH": "/bin"}
        result = scrub_environment(parent, extra_allowlist={"OPENAI_API_KEY"})
        self.assertNotIn("OPENAI_API_KEY", result.env)
        self.assertIn("OPENAI_API_KEY", result.stripped_names)

    def test_artifact_paths_are_under_ignored_runtime_tree(self) -> None:
        root = council_artifact_root(REPO_ROOT, "run-test")
        self.assertTrue(str(root).endswith(".elves/runtime/council/run-test"))
        # Product commits must not require writing here; only path design is checked.
        self.assertIn(".elves", root.parts)

    def test_write_json_artifact_sets_owner_only_mode_when_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "packet.json"
            write_json_artifact(path, {"ok": True, "secret": "should-not-matter"})
            mode = stat.S_IMODE(path.stat().st_mode)
            # On POSIX, expect 0o600; some CI filesystems may broaden — assert owner bits.
            self.assertTrue(mode & stat.S_IRUSR)
            self.assertFalse(mode & stat.S_IROTH)


class QuorumPolicyTests(unittest.TestCase):
    def test_advisory_target_quorum_degrades_without_blocking(self) -> None:
        ok, verified, blocked, confidence, notes = evaluate_quorum(
            successful_count=1,
            target_quorum=3,
            required_quorum=None,
            phase_required=False,
        )
        self.assertTrue(ok)
        self.assertFalse(verified)
        self.assertFalse(blocked)
        self.assertEqual(confidence, "reduced")
        self.assertTrue(any("target_quorum" in note for note in notes))

    def test_required_quorum_blocks_when_unmet(self) -> None:
        ok, verified, blocked, confidence, notes = evaluate_quorum(
            successful_count=1,
            target_quorum=None,
            required_quorum=2,
            phase_required=True,
        )
        self.assertFalse(ok)
        self.assertFalse(verified)
        self.assertTrue(blocked)
        self.assertEqual(confidence, "blocked")
        self.assertTrue(any("required_quorum" in note for note in notes))

    def test_required_quorum_ignored_when_phase_not_required(self) -> None:
        # evaluate_quorum still accepts the args; run_council nulls it when phase_required=False.
        ok, verified, blocked, confidence, _notes = evaluate_quorum(
            successful_count=1,
            target_quorum=1,
            required_quorum=5,
            phase_required=False,
        )
        self.assertTrue(ok)
        self.assertTrue(verified)
        self.assertFalse(blocked)


class ParallelDispatchTests(unittest.TestCase):
    def test_three_lanes_overlap_in_wall_clock_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = []
            lanes = []
            for role in ("architect", "skeptic", "tester"):
                script = _write_fake_lane_script(
                    root / f"{role}.py",
                    role=role,
                    sleep_s=0.25,
                    actual_model=f"fake-{role}",
                )
                scripts.append(script)
                lanes.append(
                    LaneSpec(
                        lane_id=role,
                        role=role,
                        adapter="custom-cli",
                        profile=role,
                        requested_model=f"fake-{role}",
                        command_override=(sys.executable, str(script)),
                        timeout_seconds=5.0,
                    )
                )
            started = time.monotonic()
            result = run_council_sync(
                lanes,
                repo_root=root,
                task="overlap timing probe",
                target_quorum=3,
                phase_required=False,
                parent_env={"PATH": os.environ.get("PATH", "/usr/bin")},
            )
            elapsed = time.monotonic() - started

        self.assertTrue(result.ok)
        self.assertTrue(result.council_verified)
        self.assertEqual(len(result.successful_reports), 3)
        # Sequential would take ~0.75s+; parallel should be well under 0.65s.
        self.assertLess(
            elapsed,
            0.65,
            f"lanes appear sequential: elapsed={elapsed:.3f}s",
        )
        # Also assert pairwise start/end overlap evidence.
        spans = [(lane.start_time, lane.end_time) for lane in result.lane_results]
        self.assertEqual(len(spans), 3)
        # Each lane should start before the earliest end (true concurrency).
        earliest_end = min(end for _, end in spans)
        for start, _end in spans:
            self.assertLessEqual(start, earliest_end + 0.05)

    def test_optional_lane_failure_preserves_other_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = _write_fake_lane_script(
                root / "good.py", role="architect", sleep_s=0.05, actual_model="m1"
            )
            bad = _write_fake_lane_script(
                root / "bad.py",
                role="skeptic",
                sleep_s=0.05,
                malformed=True,
                actual_model="m2",
            )
            lanes = [
                LaneSpec(
                    lane_id="architect",
                    role="architect",
                    adapter="custom-cli",
                    profile="a",
                    requested_model="m1",
                    command_override=(sys.executable, str(good)),
                ),
                LaneSpec(
                    lane_id="skeptic",
                    role="skeptic",
                    adapter="custom-cli",
                    profile="b",
                    requested_model="m2",
                    command_override=(sys.executable, str(bad)),
                    required=False,
                ),
            ]
            result = run_council_sync(
                lanes,
                repo_root=root,
                task="optional failure",
                target_quorum=2,
                parent_env={"PATH": os.environ.get("PATH", "/usr/bin")},
            )
        self.assertEqual(len(result.successful_reports), 1)
        self.assertFalse(result.council_verified)
        self.assertFalse(result.blocked)
        self.assertEqual(result.confidence, "reduced")
        self.assertTrue(any(not lane.ok for lane in result.lane_results))

    def test_required_lane_failure_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad = _write_fake_lane_script(
                root / "bad.py", role="architect", malformed=True, sleep_s=0.02
            )
            lanes = [
                LaneSpec(
                    lane_id="architect",
                    role="architect",
                    adapter="custom-cli",
                    profile="a",
                    command_override=(sys.executable, str(bad)),
                    required=True,
                )
            ]
            result = run_council_sync(
                lanes,
                repo_root=root,
                task="required failure",
                phase_required=True,
                required_quorum=1,
                parent_env={"PATH": os.environ.get("PATH", "/usr/bin")},
            )
        self.assertTrue(result.blocked)
        self.assertFalse(result.ok)
        self.assertEqual(result.confidence, "blocked")

    def test_timeout_and_cancellation_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hang = _write_fake_lane_script(
                root / "hang.py",
                role="architect",
                hang_s=5.0,
                sleep_s=0.0,
                actual_model="slow",
            )
            lanes = [
                LaneSpec(
                    lane_id="architect",
                    role="architect",
                    adapter="custom-cli",
                    profile="a",
                    requested_model="slow",
                    command_override=(sys.executable, str(hang)),
                    timeout_seconds=0.2,
                )
            ]
            result = run_council_sync(
                lanes,
                repo_root=root,
                task="timeout",
                parent_env={"PATH": os.environ.get("PATH", "/usr/bin")},
            )
        self.assertEqual(len(result.lane_results), 1)
        self.assertTrue(result.lane_results[0].timeout)
        self.assertFalse(result.lane_results[0].ok)
        self.assertIn("timeout", result.lane_results[0].error or "")

    def test_malformed_json_fails_lane_not_exit_code_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad = _write_fake_lane_script(
                root / "bad.py",
                role="architect",
                malformed=True,
                exit_code=0,
                sleep_s=0.02,
            )
            lanes = [
                LaneSpec(
                    lane_id="architect",
                    role="architect",
                    adapter="custom-cli",
                    profile="a",
                    command_override=(sys.executable, str(bad)),
                )
            ]
            result = run_council_sync(
                lanes,
                repo_root=root,
                task="malformed",
                parent_env={"PATH": os.environ.get("PATH", "/usr/bin")},
            )
        self.assertFalse(result.lane_results[0].ok)
        self.assertEqual(result.lane_results[0].exit_code, 0)
        self.assertIn("JSON", result.lane_results[0].error or "")

    def test_nonzero_exit_with_parseable_stdout_is_not_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = _write_fake_lane_script(
                root / "nz.py",
                role="architect",
                actual_model="ok-model",
                exit_code=3,
                sleep_s=0.02,
            )
            lanes = [
                LaneSpec(
                    lane_id="architect",
                    role="architect",
                    adapter="custom-cli",
                    profile="a",
                    requested_model="ok-model",
                    command_override=(sys.executable, str(script)),
                )
            ]
            result = run_council_sync(
                lanes,
                repo_root=root,
                task="nonzero exit",
                parent_env={"PATH": os.environ.get("PATH", "/usr/bin")},
            )
        lane = result.lane_results[0]
        self.assertFalse(lane.ok)
        self.assertEqual(lane.exit_code, 3)
        self.assertIn("non-zero terminal status", lane.error or "")
        # Parsed report may still be attached for diagnostics.
        self.assertIsNotNone(lane.report)
        self.assertEqual(lane.report["verdict"], "pass")

    def test_actual_model_mismatch_fails_lane(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = _write_fake_lane_script(
                root / "m.py",
                role="architect",
                actual_model="other-model",
                sleep_s=0.02,
            )
            lanes = [
                LaneSpec(
                    lane_id="architect",
                    role="architect",
                    adapter="custom-cli",
                    profile="a",
                    requested_model="expected-model",
                    command_override=(sys.executable, str(script)),
                )
            ]
            result = run_council_sync(
                lanes,
                repo_root=root,
                task="model mismatch",
                parent_env={"PATH": os.environ.get("PATH", "/usr/bin")},
            )
        self.assertFalse(result.lane_results[0].ok)
        self.assertIn("actual_model", result.lane_results[0].error or "")

    def test_stderr_warning_with_successful_inference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = _write_fake_lane_script(
                root / "w.py",
                role="architect",
                actual_model="ok-model",
                sleep_s=0.02,
                stderr_warning="stale MCP OAuth warning: ignore me",
            )
            lanes = [
                LaneSpec(
                    lane_id="architect",
                    role="architect",
                    adapter="custom-cli",
                    profile="a",
                    requested_model="ok-model",
                    command_override=(sys.executable, str(script)),
                )
            ]
            result = run_council_sync(
                lanes,
                repo_root=root,
                task="stderr warning",
                parent_env={"PATH": os.environ.get("PATH", "/usr/bin")},
            )
        self.assertTrue(result.lane_results[0].ok)
        self.assertIn("MCP", result.lane_results[0].stderr_summary)

    def test_secret_parent_env_does_not_reach_child(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Child dumps sorted env names and fails if secret names/values appear.
            probe = root / "probe.py"
            probe.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import json, os, sys
                    secret_names = [n for n in os.environ if "TOKEN" in n or "KEY" in n or "SECRET" in n]
                    leaked_values = [
                        v for v in os.environ.values()
                        if v in ("super-secret-token", "openrouter-secret", "ghp_leaked")
                    ]
                    if secret_names or leaked_values:
                        print(json.dumps({
                            "role": "architect",
                            "verdict": "fail",
                            "confidence": "low",
                            "key_findings": ["leak"],
                            "evidence": [str(secret_names), str(leaked_values)],
                            "risks": ["env_leak"],
                            "recommended_actions": [],
                            "open_questions": [],
                            "actual_model": "probe",
                        }))
                        sys.exit(2)
                    print(json.dumps({
                        "role": "architect",
                        "verdict": "pass",
                        "confidence": "high",
                        "key_findings": ["clean-env"],
                        "evidence": [f"keys={len(os.environ)}"],
                        "risks": [],
                        "recommended_actions": [],
                        "open_questions": [],
                        "actual_model": "probe",
                    }))
                    """
                ),
                encoding="utf-8",
            )
            probe.chmod(probe.stat().st_mode | stat.S_IXUSR)
            parent = {
                "PATH": os.environ.get("PATH", "/usr/bin"),
                "HOME": str(root),
                "OPENROUTER_API_KEY": "openrouter-secret",
                "GITHUB_TOKEN": "ghp_leaked",
                "MY_TOKEN": "super-secret-token",
                "UNRELATED": "nope",
            }
            lanes = [
                LaneSpec(
                    lane_id="architect",
                    role="architect",
                    adapter="custom-cli",
                    profile="a",
                    requested_model="probe",
                    command_override=(sys.executable, str(probe)),
                )
            ]
            result = run_council_sync(
                lanes,
                repo_root=root,
                task="env scrub",
                parent_env=parent,
            )
        self.assertTrue(result.lane_results[0].ok, result.lane_results[0].error)
        self.assertIn("OPENROUTER_API_KEY", result.lane_results[0].stripped_env_names)
        # Secret values must not appear in summaries/errors/packets.
        blob = json.dumps(result.to_dict())
        self.assertNotIn("openrouter-secret", blob)
        self.assertNotIn("ghp_leaked", blob)
        self.assertNotIn("super-secret-token", blob)

    def test_native_only_host_lane_without_external_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lanes = [
                LaneSpec(
                    lane_id="host",
                    role="architect",
                    adapter="host-native",
                    profile="host-native",
                    requested_model=None,
                    timeout_seconds=10.0,
                )
            ]
            result = run_council_sync(
                lanes,
                repo_root=root,
                task="native only",
                target_quorum=1,
                parent_env={"PATH": os.environ.get("PATH", "/usr/bin"), "HOME": str(root)},
            )
        self.assertTrue(result.ok)
        self.assertEqual(len(result.successful_reports), 1)
        self.assertEqual(result.successful_reports[0]["actual_model"], "host-native")

    def test_required_quorum_met_with_host_lane(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = _write_fake_lane_script(
                root / "a.py", role="architect", actual_model="m1", sleep_s=0.02
            )
            lanes = [
                LaneSpec(
                    lane_id="architect",
                    role="architect",
                    adapter="custom-cli",
                    profile="a",
                    requested_model="m1",
                    command_override=(sys.executable, str(a)),
                ),
                LaneSpec(
                    lane_id="host",
                    role="tester",
                    adapter="host-native",
                    profile="host-native",
                ),
            ]
            result = run_council_sync(
                lanes,
                repo_root=root,
                task="required quorum",
                phase_required=True,
                required_quorum=2,
                parent_env={"PATH": os.environ.get("PATH", "/usr/bin"), "HOME": str(root)},
            )
        self.assertTrue(result.ok)
        self.assertTrue(result.council_verified)
        self.assertFalse(result.blocked)


class LightweightReviewTests(unittest.TestCase):
    def test_lightweight_review_independent_of_council_and_not_a_vote(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = _write_fake_lane_script(
                root / "lr.py",
                role="lightweight_review",
                actual_model="cheap",
                sleep_s=0.02,
            )
            result = run_lightweight_review_sync(
                repo_root=root,
                task="utility pass",
                adapter="custom-cli",
                profile="cheap",
                requested_model="cheap",
                command_override=(sys.executable, str(script)),
                parent_env={"PATH": os.environ.get("PATH", "/usr/bin")},
            )
        self.assertTrue(result.ok)
        self.assertEqual(result.role, "lightweight_review")
        # Not a council result — no council_verified field; single lane only.
        self.assertIsNotNone(result.report)
        self.assertEqual(result.report["role"], "lightweight_review")


class CliCouncilTests(unittest.TestCase):
    def test_council_cli_native_only_json(self) -> None:
        cli = REPO_ROOT / "scripts" / "cobbler_agents.py"
        with tempfile.TemporaryDirectory() as tmp:
            result = __import__("subprocess").run(
                [
                    sys.executable,
                    str(cli),
                    "council",
                    "--json",
                    "--repo-root",
                    tmp,
                    "--task",
                    "cli native council smoke",
                    "--roles",
                    "architect,skeptic",
                    "--target-quorum",
                    "2",
                    "--timeout",
                    "15",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["host_synthesis_only"])
        self.assertFalse(payload["mutated_repo"])
        self.assertEqual(payload["successful_count"], 2)

    def test_lightweight_review_cli_json(self) -> None:
        cli = REPO_ROOT / "scripts" / "cobbler_agents.py"
        with tempfile.TemporaryDirectory() as tmp:
            result = __import__("subprocess").run(
                [
                    sys.executable,
                    str(cli),
                    "lightweight-review",
                    "--json",
                    "--repo-root",
                    tmp,
                    "--task",
                    "utility smoke",
                    "--adapter",
                    "host-native",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["not_a_council_vote"])
        self.assertTrue(payload["cannot_close_high_risk_review"])


class AdapterBuilderTests(unittest.TestCase):
    PERMISSION_BYPASS_TOKENS = (
        "--dangerously-skip-permissions",
        "bypassPermissions",
        "--yolo",
        "--always-approve",
    )

    def test_readonly_builders_use_argv_not_shell_strings(self) -> None:
        packet = Path("/tmp/packet.json")
        prompt = Path("/tmp/prompt.txt")
        for adapter in ("claude-code", "grok-build", "codex-fugu", "host-native"):
            inv = build_readonly_invocation(
                adapter=adapter,
                profile=adapter,
                packet_path=packet,
                prompt_path=prompt,
                requested_model="example-model" if adapter != "host-native" else None,
            )
            self.assertTrue(inv.read_only)
            self.assertIsInstance(inv.argv, tuple)
            self.assertTrue(all(isinstance(part, str) for part in inv.argv))
            # No shell interpolation of task text.
            joined = " ".join(inv.argv)
            self.assertNotIn("$(", joined)
            self.assertNotIn("`", joined)

    def test_readonly_invocations_never_use_permission_bypass_tokens(self) -> None:
        packet = Path("/tmp/packet.json")
        prompt = Path("/tmp/prompt.txt")
        cases = [
            ("claude-code", "claude-code", None),
            ("grok-build", "grok-build", None),
            ("codex-fugu", "codex-fugu", None),
            ("host-native", "host-native", None),
            ("custom-cli", "worker", "my-agent"),
        ]
        for adapter, profile, executable in cases:
            with self.subTest(adapter=adapter):
                inv = build_readonly_invocation(
                    adapter=adapter,
                    profile=profile,
                    packet_path=packet,
                    prompt_path=prompt,
                    executable=executable,
                    requested_model="example-model" if adapter != "host-native" else None,
                )
                argv_text = " ".join(inv.argv)
                for token in self.PERMISSION_BYPASS_TOKENS:
                    self.assertNotIn(token, inv.argv)
                    self.assertNotIn(token, argv_text)
                if adapter == "claude-code":
                    self.assertIn("--permission-mode", inv.argv)
                    mode_idx = inv.argv.index("--permission-mode")
                    self.assertEqual(inv.argv[mode_idx + 1], "plan")
                    self.assertIn("permission-mode plan", inv.notes)
                    self.assertIn("no permission-bypass", inv.notes)

    def test_custom_cli_requires_executable(self) -> None:
        with self.assertRaises(ValidationIssue) as ctx:
            build_readonly_invocation(
                adapter="custom-cli",
                profile="worker",
                packet_path=Path("/tmp/p.json"),
                prompt_path=Path("/tmp/t.txt"),
            )
        self.assertEqual(ctx.exception.code, "missing_executable")

    def test_parse_role_report_success_and_malformed(self) -> None:
        good = json.dumps(
            {
                "role": "architect",
                "verdict": "pass",
                "confidence": "high",
                "key_findings": ["x"],
                "evidence": ["y"],
                "risks": [],
                "recommended_actions": [],
                "open_questions": [],
                "actual_model": "m",
            }
        )
        report = parse_role_report(good, expected_role="architect", requested_model="m")
        self.assertEqual(report["verdict"], "pass")

        with self.assertRaises(ValidationIssue) as ctx:
            parse_role_report("not json", expected_role="architect")
        self.assertEqual(ctx.exception.code, "malformed_json")

    def test_context_module_exports_expected_symbols(self) -> None:
        for name in (
            "build_context_packet",
            "scrub_environment",
            "redact_text",
            "council_artifact_root",
            "ensure_private_dir",
        ):
            self.assertTrue(hasattr(context_mod, name))
        self.assertTrue(hasattr(dispatch_mod, "run_council"))
        self.assertTrue(hasattr(dispatch_mod, "run_lightweight_review"))


if __name__ == "__main__":
    unittest.main()
