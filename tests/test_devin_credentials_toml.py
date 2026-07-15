"""Regression coverage for Devin credentials.toml validation on Python 3.10."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cobbler_runtime import full_run as full_run_module  # noqa: E402
from cobbler_runtime import toml_compat  # noqa: E402
from cobbler_runtime.schema import ValidationIssue  # noqa: E402


class DevinCredentialsTomlValidationTests(unittest.TestCase):
    def _validate(self, raw: bytes) -> None:
        full_run_module._validate_devin_file_content(
            raw,
            full_run_module.DEVIN_CREDENTIALS_FILE_NAME,
        )

    def test_windsurf_api_key_passes_without_tomllib(self) -> None:
        raw = b'windsurf_api_key = "devin-test-key"\n'
        with mock.patch.object(toml_compat, "_tomllib", None):
            self._validate(raw)

    def test_api_key_passes_without_tomllib(self) -> None:
        raw = b'api_key = "devin-test-key"\n'
        with mock.patch.object(toml_compat, "_tomllib", None):
            self._validate(raw)

    def test_malformed_toml_raises_without_tomllib(self) -> None:
        raw = b'windsurf_api_key = "unterminated\n'
        with mock.patch.object(toml_compat, "_tomllib", None):
            with self.assertRaises(ValidationIssue) as ctx:
                self._validate(raw)
        self.assertEqual(ctx.exception.code, "full_run_devin_auth_source_invalid")
        self.assertIn("not valid TOML", str(ctx.exception))

    def test_missing_api_key_raises_without_tomllib(self) -> None:
        raw = b'api_server_url = "https://server.codeium.com"\n'
        with mock.patch.object(toml_compat, "_tomllib", None):
            with self.assertRaises(ValidationIssue) as ctx:
                self._validate(raw)
        self.assertEqual(ctx.exception.code, "full_run_devin_auth_source_invalid")
        self.assertIn("no recognized API key", str(ctx.exception))

    def test_valid_credentials_pass_with_tomllib_available(self) -> None:
        if toml_compat._tomllib is None:
            self.skipTest("tomllib unavailable on this interpreter")
        raw = (
            b'windsurf_api_key = "devin-test-key"\n'
            b'api_server_url = "https://server.codeium.com"\n'
        )
        self._validate(raw)


class RepoConsistencyWorkflowVersionScopeTests(unittest.TestCase):
    def test_workflow_selects_development_or_exact_release_scope(self) -> None:
        workflow = (
            REPO_ROOT / ".github" / "workflows" / "repo-consistency.yml"
        ).read_text()
        self.assertIn('VERIFY_VERSION="Unreleased"', workflow)
        self.assertIn("scripts/release_checklist.py", workflow)
        self.assertIn("read_frontmatter_version", workflow)
        self.assertIn(
            'python3 scripts/verify_repo.py --ci --version "$VERIFY_VERSION"',
            workflow,
        )
        self.assertNotIn('VERIFY_VERSION="2.3.0"', workflow)


if __name__ == "__main__":
    unittest.main()
