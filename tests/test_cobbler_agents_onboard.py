"""Unit tests for model onboarding (plan / show / apply / probe)."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"


def _ensure_import_path() -> None:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))


_ensure_import_path()

from cobbler_runtime.onboard import (  # noqa: E402
    apply_onboarding,
    build_onboarding_packet,
    env_name_presence,
    load_models_toml_state,
    probe_routes,
    show_onboarding,
)
from cobbler_runtime.setup import (  # noqa: E402
    preferences_from_flags,
    render_models_toml,
    run_setup,
)


class EnvPresenceTests(unittest.TestCase):
    def test_never_returns_values(self) -> None:
        present = env_name_presence(
            ("OPENROUTER_API_KEY", "META_API_KEY"),
            environ={"OPENROUTER_API_KEY": "sk-secret-value", "META_API_KEY": ""},
        )
        self.assertTrue(present["OPENROUTER_API_KEY"])
        self.assertFalse(present["META_API_KEY"])
        # ensure no secret leak in representation
        self.assertNotIn("sk-secret", str(present))

    def test_env_local_name_scan_without_process_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env.local").write_text(
                "OPENROUTER_API_KEY=sk-should-never-appear\nMETA_API_KEY=\n# comment\n",
                encoding="utf-8",
            )
            present = env_name_presence(
                ("OPENROUTER_API_KEY", "META_API_KEY", "XAI_API_KEY"),
                environ={},
                repo_root=root,
            )
            self.assertTrue(present["OPENROUTER_API_KEY"])
            # empty assignment does not count as present (mirrors process-env non-empty rule)
            self.assertFalse(present["META_API_KEY"])
            self.assertFalse(present["XAI_API_KEY"])
            self.assertNotIn("sk-should", json.dumps(present))


class OnboardPacketTests(unittest.TestCase):
    def test_plan_packet_has_questions_and_host_hints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            packet = build_onboarding_packet(
                Path(tmp),
                fake_presence={
                    "claude-code": True,
                    "grok-build": False,
                    "codex-fugu": False,
                    "gemini-cli": True,
                    "antigravity-cli": False,
                },
                environ={"OPENROUTER_API_KEY": "x"},
            )
            self.assertGreaterEqual(len(packet.questions), 5)
            self.assertIn("claude_code", packet.host_hints)
            self.assertIn("codex", packet.host_hints)
            self.assertTrue(packet.env_present["OPENROUTER_API_KEY"])
            openrouter_opts = [
                o
                for q in packet.questions
                if q["purpose_id"] == "review"
                for o in q["options"]
                if o["route"] == "openrouter-lens"
            ]
            self.assertTrue(openrouter_opts[0]["available_hint"])
            self.assertTrue(openrouter_opts[0]["apply_ready"])
            self.assertIn(
                "or-qwen-max",
                {
                    o["route"]
                    for q in packet.questions
                    if q["purpose_id"] == "review"
                    for o in q["options"]
                },
            )
            # Google routes offered for review; gemini available when inventory says so
            gemini_opts = [
                o
                for q in packet.questions
                if q["purpose_id"] == "review"
                for o in q["options"]
                if o["route"] == "gemini-cli"
            ]
            self.assertTrue(gemini_opts[0]["available_hint"])
            self.assertTrue(gemini_opts[0]["apply_ready"])
            # Implement should not push gemini as a default labor route
            implement = next(q for q in packet.questions if q["purpose_id"] == "implement")
            implement_routes = {o["route"] for o in implement["options"]}
            self.assertNotIn("gemini-cli", implement_routes)
            self.assertIn("claude-code-labor", implement_routes)
            planning = next(q for q in packet.questions if q["purpose_id"] == "planning")
            planning_routes = {o["route"] for o in planning["options"]}
            self.assertIn("claude-code-planning", planning_routes)
            self.assertIn("antigravity-cli", planning_routes)


class ApplyAndProbeTests(unittest.TestCase):
    def test_apply_writes_toml_and_probe_passes_native(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = apply_onboarding(
                root,
                role_flags={
                    "planning": "host-native",
                    "review": "host-native",
                    "implement": "host-native",
                },
                force=True,
                fake_presence={"claude-code": False, "grok-build": False},
            )
            self.assertTrue(result["ok"])
            self.assertTrue(result["models_toml_written"])
            self.assertTrue((root / ".elves" / "models.toml").is_file())
            shown = show_onboarding(root)
            self.assertEqual(shown["roles"]["planning"], "host-native")
            probe = probe_routes(root, fake_presence={"claude-code": False})
            self.assertTrue(probe["ok"])
            self.assertFalse(probe["credentials_printed"])
            self.assertEqual(probe["summary"]["fail"], 0)

    def test_probe_fails_missing_required_external(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apply_onboarding(
                root,
                role_flags={"review": "claude-code"},
                force=True,
                fake_presence={"claude-code": True},
            )
            # Pretend claude-code disappeared
            probe = probe_routes(root, fake_presence={"claude-code": False})
            self.assertFalse(probe["ok"])
            fails = [p for p in probe["probes"] if p["status"] == "fail"]
            self.assertTrue(any(p["route"] == "claude-code" for p in fails))

    def test_live_smoke_without_executor_is_warn_not_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            probe = probe_routes(Path(tmp), live_smoke=True)
            smoke_probes = [p for p in probe["probes"] if p.get("kind") == "live_smoke"]
            self.assertEqual(len(smoke_probes), 1)
            self.assertEqual(smoke_probes[0]["status"], "warn")
            self.assertNotIn("sk-", json.dumps(probe))

    def test_apply_openrouter_blocked_no_placeholder(self) -> None:
        """B1: bare openrouter/meta-muse must fail closed — no my-coding-agent write."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for blocked in ("openrouter", "meta-muse", "alphaevolve"):
                with self.subTest(blocked=blocked):
                    result = apply_onboarding(
                        root,
                        role_flags={"review": blocked},
                        force=True,
                        fake_presence={"claude-code": True},
                    )
                    self.assertFalse(result["ok"], blocked)
                    self.assertTrue(
                        any(i.get("code") == "apply_blocked_profile" for i in result["issues"]),
                        result["issues"],
                    )
                    # Should not write a successful models.toml with placeholder
                    if result.get("models_toml_written"):
                        text = (root / ".elves" / "models.toml").read_text(encoding="utf-8")
                        self.assertNotIn("my-coding-agent", text)

    def test_partial_apply_merges_existing_roles(self) -> None:
        """Partial apply must not clobber previously chosen routes or required flags."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = apply_onboarding(
                root,
                role_flags={"implement": "claude-code-labor", "review": "claude-code"},
                required=["review"],
                force=True,
                fake_presence={"claude-code": True},
            )
            self.assertTrue(first["ok"])
            text1 = (root / ".elves" / "models.toml").read_text(encoding="utf-8")
            self.assertIn("required = true", text1)
            second = apply_onboarding(
                root,
                role_flags={"review": "gemini-cli"},
                force=True,
                fake_presence={"claude-code": True, "gemini-cli": True},
            )
            self.assertTrue(second["ok"])
            shown = show_onboarding(root)
            self.assertEqual(shown["roles"]["implement"], "claude-code-labor")
            self.assertEqual(shown["roles"]["review"], "gemini-cli")
            text2 = (root / ".elves" / "models.toml").read_text(encoding="utf-8")
            # required=true on review must survive partial apply without --required
            self.assertRegex(text2, r"\[roles\.review\][\s\S]*?required = true")
            # reset should wipe unspecified back to host-native
            third = apply_onboarding(
                root,
                role_flags={"review": "host-native"},
                force=True,
                merge_existing=False,
                fake_presence={"claude-code": True},
            )
            self.assertTrue(third["ok"])
            shown2 = show_onboarding(root)
            self.assertEqual(shown2["roles"]["implement"], "host-native")
            self.assertEqual(shown2["roles"]["review"], "host-native")

    def test_partial_apply_preserves_profile_bodies_and_top_level_preferences(self) -> None:
        """Unrelated role updates must not erase model pins or custom wrapper config."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            elves = root / ".elves"
            elves.mkdir()
            (elves / "models.toml").write_text(
                """
sharing_policy = "private-machine"
document_owner = "custom-host"
session_mode_default = "exact_resume"
usage_budget_warning_tokens = 1234

[profiles.claude-code-planning]
adapter = "claude-code"
executable = "claude"
requested_model = "claude-opus-user-pin"
extra_args = ["--user-preserved-flag"]

[profiles.my-wrapper]
adapter = "custom-cli"
executable = "scripts/my-wrapper"
requested_model = "user/model"

[roles.planning]
profile = "claude-code-planning"
required = false

[roles.review]
profile = "host-native"
required = true
""".lstrip(),
                encoding="utf-8",
            )

            result = apply_onboarding(
                root,
                role_flags={"implement": "grok-build"},
                fake_presence={"grok-build": True},
            )
            self.assertTrue(result["ok"], result)
            text = (elves / "models.toml").read_text(encoding="utf-8")
            self.assertIn('requested_model = "claude-opus-user-pin"', text)
            self.assertIn('extra_args = ["--user-preserved-flag"]', text)
            self.assertIn("[profiles.my-wrapper]", text)
            self.assertIn('executable = "scripts/my-wrapper"', text)
            self.assertIn('sharing_policy = "private-machine"', text)
            self.assertIn('document_owner = "custom-host"', text)
            self.assertIn('session_mode_default = "exact_resume"', text)
            self.assertIn("usage_budget_warning_tokens = 1234", text)

            shown = show_onboarding(root)
            self.assertEqual(shown["required_roles"], ["review"])
            self.assertEqual(shown["session_mode_default"], "exact_resume")
            self.assertEqual(shown["sharing_policy"], "private-machine")

    def test_required_tier_profile_resolves_to_adapter(self) -> None:
        """B2: claude-code-planning + --required review succeeds when claude-code present."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = apply_onboarding(
                root,
                role_flags={"review": "claude-code-planning"},
                required=["review"],
                force=True,
                fake_presence={"claude-code": True},
            )
            self.assertTrue(result["ok"], result.get("issues"))
            self.assertTrue(result["models_toml_written"])
            text = (root / ".elves" / "models.toml").read_text(encoding="utf-8")
            self.assertIn("[profiles.claude-code-planning]", text)
            self.assertIn('adapter = "claude-code"', text)
            self.assertNotIn("my-coding-agent", text)

    def test_required_tier_unavailable_when_cli_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = apply_onboarding(
                root,
                role_flags={"review": "claude-code-planning"},
                required=["review"],
                force=True,
                fake_presence={"claude-code": False},
            )
            self.assertFalse(result["ok"])
            self.assertTrue(
                any(i.get("code") == "required_route_unavailable" for i in result["issues"])
            )

    def test_plan_review_only_on_implement_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = apply_onboarding(
                root,
                role_flags={"implement": "gemini-cli"},
                force=True,
                fake_presence={"gemini-cli": True},
            )
            self.assertTrue(result["ok"])
            self.assertTrue(any("plan/review-only" in w for w in result.get("warnings") or []))

    def test_probe_uses_custom_profile_executable(self) -> None:
        """Custom-cli wrapper profiles must probe the declared executable, not the profile name."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            wrapper = bin_dir / "fake-muse-wrapper"
            wrapper.write_text(
                "#!/bin/sh\necho 'muse wrapper help'\nexit 0\n",
                encoding="utf-8",
            )
            wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
            elves = root / ".elves"
            elves.mkdir()
            (elves / "models.toml").write_text(
                textwrap.dedent(
                    """\
                    sharing_policy = "local-only"
                    document_owner = "host-coordinator"
                    session_mode_default = "ephemeral"

                    [profiles.muse_spark]
                    adapter = "custom-cli"
                    executable = "fake-muse-wrapper"

                    [profiles.host-native]
                    adapter = "host-native"

                    [roles.review]
                    profile = "muse_spark"
                    required = false

                    [roles.planning]
                    profile = "host-native"
                    required = false

                    [roles.implement]
                    profile = "host-native"
                    required = false

                    [roles.lightweight_review]
                    profile = "host-native"
                    required = false

                    [roles.validate]
                    profile = "host-native"
                    required = false

                    [roles.synthesize]
                    profile = "host-native"
                    required = false

                    [roles.scout]
                    profile = "host-native"
                    required = false
                    """
                ),
                encoding="utf-8",
            )
            old_path = os.environ.get("PATH", "")
            try:
                os.environ["PATH"] = f"{bin_dir}{os.pathsep}{old_path}"
                probe = probe_routes(root)
            finally:
                os.environ["PATH"] = old_path
            self.assertTrue(probe["ok"], probe)
            review_probes = [
                p for p in probe["probes"] if p.get("purpose") == "review" and p["route"] == "muse_spark"
            ]
            self.assertEqual(len(review_probes), 1)
            self.assertEqual(review_probes[0]["status"], "pass")
            self.assertIn("fake-muse-wrapper", review_probes[0]["detail"])

    def test_probe_resolves_relative_executable_against_repo_root(self) -> None:
        """Relative profile executables must probe against repo_root, not process cwd."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / "scripts"
            scripts.mkdir()
            wrapper = scripts / "relative-lens.py"
            wrapper.write_text(
                "#!/bin/sh\necho 'relative lens help'\nexit 0\n",
                encoding="utf-8",
            )
            wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
            elves = root / ".elves"
            elves.mkdir()
            (elves / "models.toml").write_text(
                textwrap.dedent(
                    """\
                    sharing_policy = "local-only"
                    document_owner = "host-coordinator"
                    session_mode_default = "ephemeral"

                    [profiles.openrouter-lens]
                    adapter = "custom-cli"
                    executable = "scripts/relative-lens.py"

                    [profiles.host-native]
                    adapter = "host-native"

                    [roles.review]
                    profile = "openrouter-lens"
                    required = false

                    [roles.planning]
                    profile = "host-native"
                    required = false

                    [roles.implement]
                    profile = "host-native"
                    required = false

                    [roles.lightweight_review]
                    profile = "host-native"
                    required = false

                    [roles.validate]
                    profile = "host-native"
                    required = false

                    [roles.synthesize]
                    profile = "host-native"
                    required = false

                    [roles.scout]
                    profile = "host-native"
                    required = false
                    """
                ),
                encoding="utf-8",
            )
            # cwd outside repo root: which() alone would miss scripts/relative-lens.py
            outside = Path(tmp).parent
            old_cwd = Path.cwd()
            try:
                os.chdir(outside)
                probe = probe_routes(root)
            finally:
                os.chdir(old_cwd)
            self.assertTrue(probe["ok"], probe)
            review_probes = [
                p
                for p in probe["probes"]
                if p.get("purpose") == "review" and p["route"] == "openrouter-lens"
            ]
            self.assertEqual(len(review_probes), 1, probe)
            self.assertEqual(review_probes[0]["status"], "pass", review_probes[0])
            self.assertIn("relative-lens", review_probes[0]["detail"])

    def test_corrupt_toml_surfaces_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            elves = root / ".elves"
            elves.mkdir()
            (elves / "models.toml").write_text("[[[not valid toml", encoding="utf-8")
            state = load_models_toml_state(root)
            self.assertFalse(state.parse_ok)
            self.assertTrue(state.warnings)
            shown = show_onboarding(root)
            self.assertTrue(shown["warnings"])
            # roles fall back to host-native
            self.assertEqual(shown["roles"]["review"], "host-native")

    def test_render_unknown_profile_no_my_coding_agent(self) -> None:
        prefs = preferences_from_flags(review="totally-unknown-profile-xyz")
        text = render_models_toml(prefs)
        self.assertNotIn("my-coding-agent", text)
        self.assertIn("executable =", text)  # commented placeholder only
        self.assertIn("# executable", text)


class OnboardCliTests(unittest.TestCase):
    def test_cli_plan_show_probe_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for action in ("plan", "show", "probe"):
                proc = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPTS / "cobbler_agents.py"),
                        "onboard",
                        action,
                        "--json",
                        "--repo-root",
                        str(root),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    cwd=str(REPO_ROOT),
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                payload = json.loads(proc.stdout)
                self.assertIn("ok", payload)
                self.assertFalse(payload.get("credentials_printed", False))

    def test_cli_apply_then_show(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apply = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "cobbler_agents.py"),
                    "onboard",
                    "apply",
                    "--json",
                    "--planning",
                    "host-native",
                    "--review",
                    "host-native",
                    "--force",
                    "--repo-root",
                    str(root),
                ],
                capture_output=True,
                text=True,
                check=False,
                cwd=str(REPO_ROOT),
            )
            self.assertEqual(apply.returncode, 0, apply.stderr)
            payload = json.loads(apply.stdout)
            self.assertTrue(payload["ok"])
            self.assertTrue((root / ".elves" / "models.toml").is_file())

    def test_cli_apply_openrouter_exits_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "cobbler_agents.py"),
                    "onboard",
                    "apply",
                    "--json",
                    "--review",
                    "openrouter",
                    "--force",
                    "--repo-root",
                    str(root),
                ],
                capture_output=True,
                text=True,
                check=False,
                cwd=str(REPO_ROOT),
            )
            self.assertNotEqual(proc.returncode, 0)
            payload = json.loads(proc.stdout)
            self.assertFalse(payload["ok"])
            self.assertNotIn("my-coding-agent", proc.stdout)


class SetupRequiredTierTests(unittest.TestCase):
    def test_run_setup_required_tier_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".gitignore").write_text(".elves/\n", encoding="utf-8")
            result = run_setup(
                root,
                preferences=preferences_from_flags(
                    review="claude-code-planning",
                    required=["review"],
                ),
                write_toml=True,
                force_toml=True,
                fake_presence={"claude-code": True},
            )
            self.assertTrue(result.ok, result.issues)


if __name__ == "__main__":
    unittest.main()
