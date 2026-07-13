"""Fresh installed-bundle smokes and adapter registry identity tests."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class InstalledBundleSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.smoke = load_module(
            "installed_bundle_smoke_under_test",
            SCRIPTS / "installed_bundle_smoke.py",
        )

    def test_claude_and_codex_smokes_from_outside_source_tree(self) -> None:
        for host in ("claude", "codex"):
            with self.subTest(host=host):
                result = self.smoke.smoke_host(host, repo_root=REPO_ROOT)
                self.assertTrue(
                    result["ok"],
                    msg=f"{host} failures={result.get('failures')}",
                )
                self.assertGreater(int(result["py_module_count"]), 5)

    def test_openrouter_lens_is_shipped_with_bundle(self) -> None:
        # Stage via smoke helper's copy path and assert presence without full smoke.
        import sync_installed_skills as sync  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as tmpdir:
            dest = Path(tmpdir) / "elves"
            original_root = sync.REPO_ROOT
            original_targets = sync.TARGETS
            try:
                sync.REPO_ROOT = REPO_ROOT
                sync.TARGETS = sync.build_targets(REPO_ROOT)
                cfg = dict(sync.TARGETS["codex"])
                cfg["root"] = dest
                cfg.pop("alias_root", None)
                cfg["managed_aliases"] = []
                sync.TARGETS = {"codex": cfg}
                problems = sync.apply_target("codex")
                self.assertEqual(problems, [])
                self.assertTrue((dest / "scripts" / "openrouter_lens.py").is_file())
                self.assertTrue((dest / "scripts" / "cobbler_runtime" / "implement.py").is_file())
                self.assertTrue((dest / "scripts" / "cobbler_runtime" / "onboard.py").is_file())
                self.assertTrue((dest / "scripts" / "cobbler_runtime" / "executables.py").is_file())
                self.assertFalse((dest / "aliases").exists())
            finally:
                sync.REPO_ROOT = original_root
                sync.TARGETS = original_targets


class BuiltinAdapterRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        from cobbler_runtime.adapters import (  # noqa: PLC0415
            ADAPTER_CONTRACT_PAIRS,
            adapter_contract_pair,
            builtin_adapter_names,
            get_adapter,
            resolve_adapter_name,
        )
        from cobbler_runtime.schema import BUILTIN_ADAPTER_NAMES, ValidationIssue  # noqa: PLC0415

        self.get_adapter = get_adapter
        self.resolve = resolve_adapter_name
        self.contract = adapter_contract_pair
        self.pairs = ADAPTER_CONTRACT_PAIRS
        self.names = builtin_adapter_names
        self.ValidationIssue = ValidationIssue
        self.BUILTIN_ADAPTER_NAMES = BUILTIN_ADAPTER_NAMES

    def test_builtin_names_keep_identity(self) -> None:
        for name in (
            "gemini-cli",
            "antigravity-cli",
            "opencode-cli",
            "claude-code",
            "grok-build",
            "codex-fugu",
            "host-native",
        ):
            with self.subTest(name=name):
                adapter = self.get_adapter(name)
                self.assertEqual(adapter.name, name)
                self.assertEqual(self.resolve(name), name)
                pair = self.contract(name)
                self.assertEqual(pair, self.pairs[name])
                self.assertNotEqual(name, "custom-cli")

    def test_unknown_without_executable_fails_closed(self) -> None:
        with self.assertRaises(self.ValidationIssue):
            self.resolve("totally-unknown-adapter")

    def test_unknown_with_executable_maps_to_custom_cli(self) -> None:
        self.assertEqual(self.resolve("totally-unknown-adapter", executable="foo"), "custom-cli")


if __name__ == "__main__":
    unittest.main()
