"""Tests for the canonical repository verification command."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
