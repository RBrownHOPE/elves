from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "sync_installed_skills.py"


def load_sync_module():
    spec = importlib.util.spec_from_file_location("sync_installed_skills_under_test", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load sync_installed_skills module for tests")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SyncInstalledSkillsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sync = load_sync_module()

    def configure_temp_repo(self, tmpdir: str) -> tuple[Path, Path]:
        root = Path(tmpdir)
        repo = root / "repo"
        home = root / "home"
        repo.mkdir()

        (repo / "SKILL.md").write_text('---\nversion: "1.15.0"\n---\n')
        (repo / "AGENTS.md").write_text('---\nversion: "1.15.0"\n---\n')
        (repo / "config.json.example").write_text('{"cobbler": {"enabled": true}}\n')
        (repo / "references").mkdir()
        (repo / "references" / "guide.md").write_text("guide\n")
        (repo / "scripts").mkdir()
        for relative in self.sync.RUNTIME_SCRIPT_PATHS + self.sync.REPO_ONLY_SCRIPT_PATHS:
            path = repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{relative}\n")

        for alias_name in self.sync.CLAUDE_ALIAS_NAMES:
            alias_dir = repo / "aliases" / "claude" / alias_name
            alias_dir.mkdir(parents=True)
            alias_dir.joinpath("SKILL.md").write_text(
                f"---\nname: {alias_name}\n---\n\n{self.sync.CLAUDE_ALIAS_MARKER}\n",
            )

        self.sync.REPO_ROOT = repo
        self.sync.TARGETS = {
            "claude": {
                "root": home / ".claude" / "skills" / "elves",
                "managed_paths": [
                    "SKILL.md",
                    "config.json.example",
                    "references",
                    *self.sync.RUNTIME_SCRIPT_PATHS,
                ],
                "cleanup_paths": self.sync.REPO_ONLY_SCRIPT_PATHS,
                "alias_root": home / ".claude" / "skills",
                "managed_aliases": self.sync.CLAUDE_ALIAS_NAMES,
            },
            "codex": {
                "root": home / ".codex" / "skills" / "elves",
                "managed_paths": [
                    "SKILL.md",
                    "AGENTS.md",
                    "config.json.example",
                    "references",
                    *self.sync.RUNTIME_SCRIPT_PATHS,
                ],
                "cleanup_paths": self.sync.REPO_ONLY_SCRIPT_PATHS,
            },
        }
        return repo, home

    def test_apply_creates_missing_claude_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            _, home = self.configure_temp_repo(tmpdir)

            problems = self.sync.apply_target("claude")

            self.assertEqual(problems, [])
            for alias_name in self.sync.CLAUDE_ALIAS_NAMES:
                skill_path = home / ".claude" / "skills" / alias_name / "SKILL.md"
                self.assertTrue(skill_path.exists(), alias_name)
                self.assertIn(self.sync.CLAUDE_ALIAS_MARKER, skill_path.read_text())

    def test_apply_refuses_unmarked_user_owned_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            _, home = self.configure_temp_repo(tmpdir)
            alias_path = home / ".claude" / "skills" / "cobbler" / "SKILL.md"
            alias_path.parent.mkdir(parents=True)
            alias_path.write_text("user-owned alias\n")

            problems = self.sync.apply_target("claude")

            self.assertEqual(alias_path.read_text(), "user-owned alias\n")
            self.assertIn(
                f"alias conflict: {alias_path.parent} exists without Elves managed alias marker",
                problems,
            )

    def test_apply_updates_marked_stale_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo, home = self.configure_temp_repo(tmpdir)
            alias_path = home / ".claude" / "skills" / "cobbler" / "SKILL.md"
            alias_path.parent.mkdir(parents=True)
            alias_path.write_text(f"{self.sync.CLAUDE_ALIAS_MARKER}\nstale\n")

            problems = self.sync.apply_target("claude")

            self.assertEqual(problems, [])
            self.assertEqual(
                alias_path.read_text(),
                (repo / "aliases" / "claude" / "cobbler" / "SKILL.md").read_text(),
            )

    def test_check_reports_unmarked_alias_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            _, home = self.configure_temp_repo(tmpdir)
            self.sync.apply_target("claude")
            alias_path = home / ".claude" / "skills" / "council" / "SKILL.md"
            alias_path.write_text("user-owned council alias\n")

            ok, problems = self.sync.check_target("claude")

            self.assertFalse(ok)
            self.assertIn(
                f"alias conflict: {alias_path.parent} exists without Elves managed alias marker",
                problems,
            )

    def test_codex_apply_does_not_create_claude_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            _, home = self.configure_temp_repo(tmpdir)

            problems = self.sync.apply_target("codex")

            self.assertEqual(problems, [])
            self.assertFalse((home / ".claude" / "skills" / "cobbler").exists())

    def test_apply_removes_repo_only_release_helper_from_installed_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            _, home = self.configure_temp_repo(tmpdir)
            installed_helper = (
                home / ".codex" / "skills" / "elves" / "scripts" / "release_checklist.py"
            )
            installed_helper.parent.mkdir(parents=True)
            installed_helper.write_text("stale repo-only helper\n")

            problems = self.sync.apply_target("codex")

            self.assertEqual(problems, [])
            self.assertFalse(installed_helper.exists())

    def test_apply_installs_config_template_for_all_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            _, home = self.configure_temp_repo(tmpdir)

            self.assertEqual(self.sync.apply_target("claude"), [])
            self.assertEqual(self.sync.apply_target("codex"), [])

            self.assertTrue(
                (home / ".claude" / "skills" / "elves" / "config.json.example").exists()
            )
            self.assertTrue(
                (home / ".codex" / "skills" / "elves" / "config.json.example").exists()
            )

    def test_check_all_without_installed_targets_is_advisory_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self.configure_temp_repo(tmpdir)
            stdout = io.StringIO()

            with mock.patch.object(sys, "argv", ["sync_installed_skills.py", "--check"]):
                with contextlib.redirect_stdout(stdout):
                    result = self.sync.main()

            self.assertEqual(result, 0)
            self.assertIn("No installed Elves skill copies were detected.", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
