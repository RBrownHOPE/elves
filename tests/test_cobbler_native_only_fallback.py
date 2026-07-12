"""Native-only fallback: every external profile disabled still resolves usable routes."""

from __future__ import annotations

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

    def test_validate_config_cli_native_only(self) -> None:
        import subprocess
        import sys

        proc = subprocess.run(
            [sys.executable, "scripts/cobbler_agents.py", "validate-config", "--json"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('"ok": true', proc.stdout)
        self.assertIn("host-native", proc.stdout)
        self.assertIn('"model_calls_made": false', proc.stdout)


if __name__ == "__main__":
    unittest.main()
