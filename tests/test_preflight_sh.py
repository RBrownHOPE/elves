from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT_SCRIPT = REPO_ROOT / "scripts" / "preflight.sh"


class PreflightScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()

    def write_executable(self, name: str, body: str) -> Path:
        path = self.bin_dir / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def write_fake_gh(self) -> None:
        self.write_executable(
            "gh",
            """#!/usr/bin/env bash
if [ "$1 $2" = "auth status" ]; then
  printf 'Logged in to github.com account test-user\\n'
  exit 0
fi
if [ "$1" = "api" ] && [ "$2" = "repos/aigorahub/elves/releases/latest" ]; then
  printf '{"tag_name":"v1.15.0","html_url":"https://example.com/v1.15.0"}\\n'
  exit 0
fi
printf 'unexpected gh invocation: %s\\n' "$*" >&2
exit 1
""",
        )

    def base_env(self) -> dict[str, str]:
        env = os.environ.copy()
        for key in list(env):
            if key.startswith("ELVES_") or key.startswith("GIT_"):
                env.pop(key)
        env.update(
            {
                "PATH": f"{self.bin_dir}:{env.get('PATH', '')}",
                "HOME": str(self.home),
                "XDG_CONFIG_HOME": str(self.root / "config"),
                "XDG_CACHE_HOME": str(self.root / "cache"),
                "TMPDIR": str(self.root),
                "GIT_CONFIG_NOSYSTEM": "1",
            }
        )
        return env

    def env(self) -> dict[str, str]:
        env = self.base_env()
        env.update(
            {
                "OPENAI_CODEX": "1",
                "CI": "true",
                "DEBIAN_FRONTEND": "noninteractive",
                "HOMEBREW_NO_AUTO_UPDATE": "1",
                "NEXT_TELEMETRY_DISABLED": "1",
                "NUXT_TELEMETRY_DISABLED": "1",
                "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "NPM_CONFIG_YES": "true",
            }
        )
        return env

    def run_git(self, cwd: Path, *args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=self.base_env(),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def create_repo(self, with_remote: bool) -> Path:
        repo = self.root / "repo"
        repo.mkdir()
        self.run_git(repo, "init", "-b", "main")
        (repo / ".gitignore").write_text(".playwright-mcp/\ndocs/audit/\n.elves/\n")
        (repo / "README.md").write_text("test repo\n")
        self.run_git(repo, "add", ".")
        self.run_git(
            repo,
            "-c",
            "user.name=Elves Test",
            "-c",
            "user.email=elves@example.com",
            "commit",
            "-m",
            "initial",
        )
        if with_remote:
            remote = self.root / "remote.git"
            self.run_git(self.root, "init", "--bare", str(remote))
            self.run_git(repo, "remote", "add", "origin", str(remote))
            self.run_git(repo, "push", "-u", "origin", "main")
        return repo

    def run_preflight(self, repo: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(PREFLIGHT_SCRIPT)],
            cwd=repo,
            env=self.env(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def run_preflight_args(self, repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(PREFLIGHT_SCRIPT), *args],
            cwd=repo,
            env=self.env(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_preflight_passes_in_minimal_launch_ready_repo(self) -> None:
        self.write_fake_gh()
        repo = self.create_repo(with_remote=True)

        result = self.run_preflight(repo)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Remote origin:", result.stdout)
        self.assertIn("gh authenticated", result.stdout)
        self.assertIn("Can push to origin/main", result.stdout)
        self.assertIn("All known ephemeral directories are gitignored", result.stdout)
        self.assertIn("Sleep Prevention", result.stdout)
        self.assertIn("Project Type Detection", result.stdout)
        self.assertNotIn("Recommended dedicated worktree", result.stdout)

    def test_preflight_fails_when_origin_remote_is_missing(self) -> None:
        self.write_fake_gh()
        repo = self.create_repo(with_remote=False)

        result = self.run_preflight(repo)

        self.assertEqual(result.returncode, 1)
        self.assertIn("No git remote 'origin' found", result.stdout)
        self.assertIn("Elves Preflight Summary", result.stdout)

    def test_preflight_worktree_helper_dispatches_before_full_checklist(self) -> None:
        repo = self.create_repo(with_remote=True)

        result = self.run_preflight_args(
            repo,
            "--dry-run",
            "--create-worktree",
            "codex/wrapper-example",
            "--base",
            "origin/main",
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Dry run: would create dedicated worktree", result.stdout)
        self.assertIn("branch: codex/wrapper-example", result.stdout)
        self.assertNotIn("GitHub CLI (gh)", result.stdout)
        self.assertFalse((self.root / "repo-wrapper-example").exists())

    def test_preflight_worktree_helper_can_create_via_wrapper(self) -> None:
        repo = self.create_repo(with_remote=True)

        result = self.run_preflight_args(
            repo,
            "--create-worktree",
            "codex/wrapper-create",
            "--base",
            "origin/main",
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        created = self.root / "repo-wrapper-create"
        self.assertTrue(created.exists())
        self.assertIn("Created dedicated worktree", result.stdout)
        self.assertIn("branch: codex/wrapper-create", result.stdout)
        self.assertNotIn("GitHub CLI (gh)", result.stdout)
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=created,
            env=self.base_env(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        ).stdout.strip()
        self.assertEqual(branch, "codex/wrapper-create")


if __name__ == "__main__":
    unittest.main()
