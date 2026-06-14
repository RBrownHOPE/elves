from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "release_checklist.py"


def load_release_checklist_module():
    spec = importlib.util.spec_from_file_location("release_checklist_under_test", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load release_checklist module for tests")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ReleaseChecklistTests(unittest.TestCase):
    def setUp(self) -> None:
        self.release_checklist = load_release_checklist_module()

    def configure_temp_repo(self, tmpdir: str, version: str = "1.15.0") -> Path:
        repo = Path(tmpdir)
        (repo / "SKILL.md").write_text(f'---\nmetadata:\n  version: "{version}"\n---\n')
        (repo / "AGENTS.md").write_text(f'---\nversion: "{version}"\n---\n')
        (repo / "CHANGELOG.md").write_text(
            "# Changelog\n\n"
            "## [Unreleased]\n\n"
            f"## [{version}] - 2026-06-14\n\n"
            "- Released.\n"
        )
        tests_dir = repo / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_sync_installed_skills.py").write_text(
            f'fixture current version "{version}"\n'
        )
        return repo

    def test_release_checklist_passes_when_release_surfaces_are_aligned(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = self.configure_temp_repo(tmpdir)

            result = self.release_checklist.build_release_checklist(repo, base_ref=None)

        self.assertTrue(result.ok)
        self.assertEqual(result.failures, [])

    def test_release_checklist_fails_when_changelog_latest_release_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = self.configure_temp_repo(tmpdir, version="1.16.0")
            (repo / "CHANGELOG.md").write_text(
                "# Changelog\n\n"
                "## [Unreleased]\n\n"
                "## [1.15.0] - 2026-06-14\n\n"
                "- Released.\n"
            )

            result = self.release_checklist.build_release_checklist(repo, base_ref=None)

        self.assertFalse(result.ok)
        self.assertIn(
            "CHANGELOG.md: latest release `1.15.0` does not match `1.16.0`",
            result.failures,
        )

    def test_release_checklist_fails_when_unreleased_content_was_not_promoted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = self.configure_temp_repo(tmpdir)
            changelog = (repo / "CHANGELOG.md").read_text()
            (repo / "CHANGELOG.md").write_text(
                changelog.replace("## [Unreleased]\n\n", "## [Unreleased]\n\n- Pending.\n\n")
            )

            result = self.release_checklist.build_release_checklist(repo, base_ref=None)

        self.assertFalse(result.ok)
        self.assertIn(
            (
                "CHANGELOG.md: `## [Unreleased]` still has content; "
                "promote it under the release heading"
            ),
            result.failures,
        )

    def test_release_checklist_can_warn_for_unreleased_content_on_development_branches(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = self.configure_temp_repo(tmpdir)
            changelog = (repo / "CHANGELOG.md").read_text()
            (repo / "CHANGELOG.md").write_text(
                changelog.replace("## [Unreleased]\n\n", "## [Unreleased]\n\n- Pending.\n\n")
            )

            result = self.release_checklist.build_release_checklist(
                repo,
                base_ref=None,
                allow_unreleased=True,
            )

        self.assertTrue(result.ok)
        self.assertIn(
            (
                "CHANGELOG.md: `## [Unreleased]` still has content; "
                "promote it under the release heading"
            ),
            result.warnings,
        )

    def test_release_checklist_fails_when_current_version_example_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = self.configure_temp_repo(tmpdir, version="1.16.0")
            (repo / "tests" / "test_sync_installed_skills.py").write_text(
                'fixture stale version "1.15.0"\n'
            )

            result = self.release_checklist.build_release_checklist(repo, base_ref=None)

        self.assertFalse(result.ok)
        self.assertIn(
            "tests/test_sync_installed_skills.py: missing current version example `1.16.0`",
            result.failures,
        )

    def test_parse_name_status_uses_new_path_for_renames(self) -> None:
        changes = self.release_checklist.parse_name_status(
            "M\tREADME.md\nA\treferences/new-guide.md\nR100\told.md\treferences/new-name.md\n"
        )

        self.assertEqual(
            [(change.status, change.path) for change in changes],
            [
                ("M", "README.md"),
                ("A", "references/new-guide.md"),
                ("R100", "references/new-name.md"),
            ],
        )

    def test_release_checklist_warns_for_added_human_facing_surfaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = self.configure_temp_repo(tmpdir)
            git_changes = [
                self.release_checklist.NameStatusChange("A", "references/new-guide.md"),
                self.release_checklist.NameStatusChange("M", "scripts/internal.py"),
            ]

            with mock.patch.object(
                self.release_checklist,
                "changed_files_since",
                return_value=(git_changes, None),
            ):
                result = self.release_checklist.build_release_checklist(
                    repo,
                    base_ref="origin/main",
                )

        self.assertTrue(result.ok)
        self.assertIn(
            (
                "Review newly added human-facing surfaces for README, changelog, and "
                "repo-consistency coverage: references/new-guide.md"
            ),
            result.warnings,
        )
        self.assertIn(
            "Human-facing surfaces changed since `origin/main`: A references/new-guide.md",
            result.notes,
        )


if __name__ == "__main__":
    unittest.main()
