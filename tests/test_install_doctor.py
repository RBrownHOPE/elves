from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "install_doctor.py"


def load_install_doctor_module():
    spec = importlib.util.spec_from_file_location("install_doctor_under_test", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load install_doctor module for tests")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class InstallDoctorCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.install_doctor = load_install_doctor_module()

    def write_skill(self, root: Path, version: str) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        (root / "SKILL.md").write_text(f'---\nversion: "{version}"\n---\n')
        return root

    def test_fetch_latest_release_refreshes_stale_cache_when_active_version_is_newer(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "install-doctor.json"
            cache_path.write_text(
                json.dumps(
                    {
                        "checked_at": datetime(2026, 4, 12, 18, 0, tzinfo=timezone.utc).isoformat(),
                        "latest_version": "1.6.1",
                        "latest_url": "https://example.com/v1.6.1",
                        "source": "gh-release",
                    }
                )
            )
            gh_fetch = mock.Mock(
                return_value={
                    "tag_name": "v1.7.0",
                    "html_url": "https://github.com/aigorahub/elves/releases/tag/v1.7.0",
                }
            )

            with mock.patch.object(self.install_doctor, "CACHE_PATH", cache_path), mock.patch.object(
                self.install_doctor, "fetch_json_with_gh", gh_fetch
            ), mock.patch.object(self.install_doctor, "fetch_json_with_http", return_value=None), mock.patch.object(
                self.install_doctor, "datetime"
            ) as fake_datetime:
                fake_datetime.now.return_value = datetime(2026, 4, 12, 20, 30, tzinfo=timezone.utc)
                fake_datetime.fromisoformat.side_effect = datetime.fromisoformat
                latest_release = self.install_doctor.fetch_latest_release(24, minimum_version="1.7.0")

            self.assertEqual(latest_release["latest_version"], "1.7.0")
            gh_fetch.assert_called_once_with("repos/aigorahub/elves/releases/latest")

    def test_version_comparison_handles_v_prefix_and_numeric_segments(self) -> None:
        self.assertTrue(self.install_doctor.version_is_newer("v1.10.0", "1.9.9"))
        self.assertFalse(self.install_doctor.version_is_newer("v1.9.9", "1.10.0"))
        self.assertFalse(self.install_doctor.version_is_newer("1.10.0", "v1.10.0"))
        self.assertFalse(self.install_doctor.version_is_newer("invalid", "1.0.0"))
        self.assertFalse(self.install_doctor.version_is_newer("v1.2.beta", "1.2.0"))
        self.assertFalse(self.install_doctor.version_is_newer("1.2.1", "invalid"))

    def test_read_version_returns_frontmatter_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = self.write_skill(Path(tmpdir) / "skill", "1.15.0")

            self.assertEqual(self.install_doctor.read_version(root), "1.15.0")

    def test_discover_installs_finds_global_project_local_and_legacy_installs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            active = self.write_skill(root / "repo", "1.15.0")
            claude_global = self.write_skill(root / "home" / ".claude" / "skills" / "elves", "1.14.0")
            codex_global = self.write_skill(root / "home" / ".codex" / "skills" / "elves", "1.15.0")
            codex_local = self.write_skill(root / "project" / ".codex" / "skills" / "elves", "1.13.0")
            codex_legacy = self.write_skill(root / "home" / ".agents" / "skills" / "elves", "1.12.0")
            codex_legacy_local = self.write_skill(
                root / "project" / ".agents" / "skills" / "elves",
                "1.11.0",
            )
            cwd = root / "project" / "src"
            cwd.mkdir(parents=True)

            with mock.patch.object(self.install_doctor, "ACTIVE_ROOT", active), mock.patch.object(
                self.install_doctor,
                "GLOBAL_INSTALLS",
                {"claude": claude_global, "codex": codex_global},
            ), mock.patch.object(
                self.install_doctor,
                "LOCAL_INSTALL_SUFFIXES",
                {
                    "claude": Path(".claude") / "skills" / "elves",
                    "codex": Path(".codex") / "skills" / "elves",
                },
            ), mock.patch.object(
                self.install_doctor,
                "LEGACY_INSTALLS",
                {
                    "codex": {
                        "global": codex_legacy,
                        "local_suffix": Path(".agents") / "skills" / "elves",
                    }
                },
            ):
                installs, active_install = self.install_doctor.discover_installs(cwd)

            observed = {
                (
                    install.platform,
                    install.scope,
                    install.path.resolve(),
                    install.version,
                    install.active,
                )
                for install in installs
            }
            expected = {
                ("unknown", "repo-checkout", active.resolve(), "1.15.0", True),
                ("claude", "global", claude_global.resolve(), "1.14.0", False),
                ("codex", "global", codex_global.resolve(), "1.15.0", False),
                ("codex", "project-local", codex_local.resolve(), "1.13.0", False),
                ("codex", "legacy-global", codex_legacy.resolve(), "1.12.0", False),
                (
                    "codex",
                    "legacy-project-local",
                    codex_legacy_local.resolve(),
                    "1.11.0",
                    False,
                ),
            }
            self.assertEqual(active_install.path, active)
            self.assertEqual(observed, expected)

    def test_build_recommendations_reports_updates_mismatch_legacy_and_sync_hint(self) -> None:
        active = self.install_doctor.Install(
            platform="unknown",
            scope="repo-checkout",
            path=Path("/repo"),
            version="1.15.0",
            active=True,
        )
        installs = [
            active,
            self.install_doctor.Install(
                platform="codex",
                scope="global",
                path=Path("/home/.codex/skills/elves"),
                version="1.15.0",
            ),
            self.install_doctor.Install(
                platform="codex",
                scope="project-local",
                path=Path("/project/.codex/skills/elves"),
                version="1.14.0",
            ),
            self.install_doctor.Install(
                platform="codex",
                scope="legacy-global",
                path=Path("/home/.agents/skills/elves"),
                version="1.12.0",
            ),
        ]

        notes = self.install_doctor.build_recommendations(
            installs,
            active,
            {
                "latest_version": "1.16.0",
                "latest_url": "https://github.com/aigorahub/elves/releases/tag/v1.16.0",
            },
        )

        self.assertTrue(any("Update available: v1.15.0 -> v1.16.0" in note for note in notes))
        self.assertTrue(any("project-local install v1.14.0" in note for note in notes))
        self.assertTrue(any("Legacy codex install detected" in note for note in notes))
        self.assertTrue(any("sync_installed_skills.py --apply" in note for note in notes))

    def test_fetch_latest_release_reuses_cache_when_it_matches_active_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "install-doctor.json"
            cache_path.write_text(
                json.dumps(
                    {
                        "checked_at": datetime(2026, 4, 12, 18, 0, tzinfo=timezone.utc).isoformat(),
                        "latest_version": "1.7.0",
                        "latest_url": "https://example.com/v1.7.0",
                        "source": "gh-release",
                    }
                )
            )
            gh_fetch = mock.Mock()

            with mock.patch.object(self.install_doctor, "CACHE_PATH", cache_path), mock.patch.object(
                self.install_doctor, "fetch_json_with_gh", gh_fetch
            ), mock.patch.object(self.install_doctor, "fetch_json_with_http", return_value=None), mock.patch.object(
                self.install_doctor, "datetime"
            ) as fake_datetime:
                fake_datetime.now.return_value = datetime(2026, 4, 12, 20, 30, tzinfo=timezone.utc)
                fake_datetime.fromisoformat.side_effect = datetime.fromisoformat
                latest_release = self.install_doctor.fetch_latest_release(24, minimum_version="1.7.0")

            self.assertEqual(latest_release["latest_version"], "1.7.0")
            gh_fetch.assert_not_called()

    def test_fetch_latest_release_reuses_recent_ahead_cache_without_refetching(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "install-doctor.json"
            cache_path.write_text(
                json.dumps(
                    {
                        "checked_at": datetime(2026, 4, 12, 20, 0, tzinfo=timezone.utc).isoformat(),
                        "latest_version": "1.6.1",
                        "latest_url": "https://example.com/v1.6.1",
                        "source": "gh-release",
                    }
                )
            )
            gh_fetch = mock.Mock()

            with mock.patch.object(self.install_doctor, "CACHE_PATH", cache_path), mock.patch.object(
                self.install_doctor, "fetch_json_with_gh", gh_fetch
            ), mock.patch.object(self.install_doctor, "fetch_json_with_http", return_value=None), mock.patch.object(
                self.install_doctor, "datetime"
            ) as fake_datetime:
                fake_datetime.now.return_value = datetime(2026, 4, 12, 20, 30, tzinfo=timezone.utc)
                fake_datetime.fromisoformat.side_effect = datetime.fromisoformat
                latest_release = self.install_doctor.fetch_latest_release(24, minimum_version="1.7.0")

            self.assertEqual(latest_release["latest_version"], "1.6.1")
            gh_fetch.assert_not_called()

    def test_fetch_latest_release_refreshes_stale_unavailable_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "install-doctor.json"
            cache_path.write_text(
                json.dumps(
                    {
                        "checked_at": datetime(2026, 4, 12, 18, 0, tzinfo=timezone.utc).isoformat(),
                        "latest_version": None,
                        "latest_url": None,
                        "source": "unavailable",
                    }
                )
            )
            gh_fetch = mock.Mock(
                return_value={
                    "tag_name": "v1.7.0",
                    "html_url": "https://github.com/aigorahub/elves/releases/tag/v1.7.0",
                }
            )

            with mock.patch.object(self.install_doctor, "CACHE_PATH", cache_path), mock.patch.object(
                self.install_doctor, "fetch_json_with_gh", gh_fetch
            ), mock.patch.object(self.install_doctor, "fetch_json_with_http", return_value=None), mock.patch.object(
                self.install_doctor, "datetime"
            ) as fake_datetime:
                fake_datetime.now.return_value = datetime(2026, 4, 12, 20, 30, tzinfo=timezone.utc)
                fake_datetime.fromisoformat.side_effect = datetime.fromisoformat
                latest_release = self.install_doctor.fetch_latest_release(24, minimum_version="1.7.0")

            self.assertEqual(latest_release["latest_version"], "1.7.0")
            gh_fetch.assert_called_once_with("repos/aigorahub/elves/releases/latest")


if __name__ == "__main__":
    unittest.main()
