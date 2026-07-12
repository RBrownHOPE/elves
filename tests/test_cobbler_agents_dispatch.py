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
from cobbler_runtime.context import (  # noqa: E402
    build_context_packet,
    council_artifact_root,
    ensure_private_dir,
    redact_text,
    scrub_environment,
    write_json_artifact,
)


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


class DispatchPlaceholderTests(unittest.TestCase):
    """Import guards so later commits can grow dispatch tests in this module."""

    def test_context_module_exports_expected_symbols(self) -> None:
        for name in (
            "build_context_packet",
            "scrub_environment",
            "redact_text",
            "council_artifact_root",
            "ensure_private_dir",
        ):
            self.assertTrue(hasattr(context_mod, name))


if __name__ == "__main__":
    unittest.main()
