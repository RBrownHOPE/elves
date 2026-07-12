"""Native-only fallback: every external profile disabled still resolves usable routes."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.cobbler_runtime.config import resolve_config
from scripts.cobbler_runtime.schema import RoleName


class NativeOnlyFallbackTests(unittest.TestCase):
    def test_empty_config_resolves_all_roles_to_host_native(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            resolved = resolve_config(
                survival_guide=None,
                models_toml_path=root / ".elves" / "models.toml",
                user_config_path=root / "config.json",
                repo_root=root,
            )
            self.assertTrue(resolved.ok)
            for role in RoleName:
                route = resolved.roles[role.value]
                self.assertEqual(route.profile, "host-native")
                self.assertFalse(route.required)
                self.assertEqual(route.source, "native_default")
            # Resolving defaults must not create .elves state.
            self.assertFalse((root / ".elves").exists())

    def test_disabled_routing_forces_host_native_without_probing(self) -> None:
        resolved = resolve_config(
            models_toml={
                "model_routing": {"enabled": False},
                "profiles": {
                    "grok-build": {"adapter": "grok-build", "executable": "grok"},
                },
                "roles": {
                    "review": {"profile": "grok-build", "required": False},
                },
            }
        )
        self.assertTrue(resolved.ok)
        self.assertFalse(resolved.external_routing_enabled)
        for route in resolved.roles.values():
            self.assertEqual(route.profile, "host-native")

    def test_validate_config_cli_native_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc = subprocess.run(
                [
                    sys.executable,
                    "scripts/cobbler_agents.py",
                    "validate-config",
                    "--json",
                    "--repo-root",
                    str(root),
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertTrue(payload["ok"])
            self.assertIn("host-native", proc.stdout)
            self.assertFalse(payload["model_calls_made"])
            # Native-only validate-config must not create .elves state.
            self.assertFalse((root / ".elves").exists())


if __name__ == "__main__":
    unittest.main()
