"""Unit tests for model onboarding (plan / show / apply / probe)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
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
    probe_routes,
    show_onboarding,
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
                if o["route"] == "openrouter"
            ]
            self.assertTrue(openrouter_opts[0]["available_hint"])
            # Google routes offered for review; gemini available when inventory says so
            gemini_opts = [
                o
                for q in packet.questions
                if q["purpose_id"] == "review"
                for o in q["options"]
                if o["route"] == "gemini-cli"
            ]
            self.assertTrue(gemini_opts[0]["available_hint"])
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


if __name__ == "__main__":
    unittest.main()
