from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import stat
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER_SCRIPT = REPO_ROOT / "scripts" / "preflight_worktree.py"


class PreflightWorktreeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()

    def env(self) -> dict[str, str]:
        env = os.environ.copy()
        for key in list(env):
            if key.startswith("GIT_"):
                env.pop(key)
        env.update(
            {
                "HOME": str(self.home),
                "PATH": f"{self.bin_dir}:{env.get('PATH', '')}",
                "GIT_CONFIG_NOSYSTEM": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        return env

    def write_executable(self, name: str, body: str) -> Path:
        path = self.bin_dir / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def real_git_path(self) -> str:
        path = shutil.which("git", path=os.environ.get("PATH", ""))
        if path is None:
            self.fail("git executable not found for test fixture")
        return path

    def run_git(self, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=self.env(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

    def create_repo(self, push_main: bool = True) -> Path:
        repo = self.root / "repo"
        repo.mkdir()
        self.run_git(repo, "init", "-b", "main")
        (repo / "README.md").write_text("test repo\n")
        self.run_git(repo, "add", "README.md")
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
        remote = self.root / "remote.git"
        self.run_git(self.root, "init", "--bare", str(remote))
        self.run_git(repo, "remote", "add", "origin", str(remote))
        if push_main:
            self.run_git(repo, "push", "-u", "origin", "main")
        return repo

    def run_helper(self, repo: Path, *args: str, timeout: int = 10) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(HELPER_SCRIPT), *args],
            cwd=repo,
            env=self.env(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            input="this input should not be read\n",
            timeout=timeout,
            check=False,
        )

    def test_dry_run_prints_command_and_creates_nothing(self) -> None:
        repo = self.create_repo()

        result = self.run_helper(repo, "--dry-run", "--create-worktree", "codex/example")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Dry run: would create dedicated worktree", result.stdout)
        self.assertIn("branch: codex/example", result.stdout)
        self.assertIn(f"worktree path: {self.root / 'repo-example'}", result.stdout)
        self.assertIn("base ref: origin/main", result.stdout)
        self.assertIn("command: git worktree add -b codex/example", result.stdout)
        self.assertIn("collision tripwire:", result.stdout)
        self.assertFalse((self.root / "repo-example").exists())

    def test_missing_default_base_fails_with_base_guidance(self) -> None:
        repo = self.create_repo(push_main=False)

        result = self.run_helper(repo, "--dry-run", "--create-worktree", "codex/example")

        self.assertEqual(result.returncode, 1)
        self.assertIn("Default base origin/main does not resolve; pass --base <ref>.", result.stderr)

    def test_explicit_base_is_printed_and_used_for_create(self) -> None:
        repo = self.create_repo(push_main=False)

        result = self.run_helper(repo, "--create-worktree", "codex/from-head", "--base", "HEAD")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("base ref: HEAD", result.stdout)
        self.assertIn("command: git worktree add -b codex/from-head", result.stdout)
        created = self.root / "repo-from-head"
        self.assertTrue(created.exists())
        branch = self.run_git(created, "branch", "--show-current").stdout.strip()
        self.assertEqual(branch, "codex/from-head")

    def test_dirty_checkout_blocks_create_but_not_dry_run(self) -> None:
        repo = self.create_repo()
        (repo / "dirty.txt").write_text("uncommitted\n")

        create_result = self.run_helper(repo, "--create-worktree", "codex/dirty")
        dry_run_result = self.run_helper(
            repo,
            "--dry-run",
            "--create-worktree",
            "codex/dirty",
        )

        self.assertEqual(create_result.returncode, 1)
        self.assertIn("Current checkout has uncommitted changes", create_result.stderr)
        self.assertEqual(dry_run_result.returncode, 0, dry_run_result.stdout + dry_run_result.stderr)
        self.assertFalse((self.root / "repo-dirty").exists())

    def test_existing_local_branch_fails_before_create(self) -> None:
        repo = self.create_repo()
        self.run_git(repo, "branch", "codex/example")

        result = self.run_helper(repo, "--create-worktree", "codex/example")

        self.assertEqual(result.returncode, 1)
        self.assertIn("already exists locally", result.stderr)
        self.assertFalse((self.root / "repo-example").exists())

    def test_existing_remote_branch_fails_before_create(self) -> None:
        repo = self.create_repo()
        self.run_git(repo, "push", "origin", "HEAD:codex/remote-example")

        result = self.run_helper(repo, "--create-worktree", "codex/remote-example")

        self.assertEqual(result.returncode, 1)
        self.assertIn("already exists on origin", result.stderr)
        self.assertFalse((self.root / "repo-remote-example").exists())

    def test_generated_path_skips_existing_sibling(self) -> None:
        repo = self.create_repo()
        (self.root / "repo-example").mkdir()

        result = self.run_helper(repo, "--dry-run", "--create-worktree", "codex/example")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"worktree path: {self.root / 'repo-example-2'}", result.stdout)
        self.assertFalse((self.root / "repo-example-2").exists())

    def test_explicit_existing_worktree_dir_fails(self) -> None:
        repo = self.create_repo()
        existing = self.root / "chosen"
        existing.mkdir()

        result = self.run_helper(
            repo,
            "--create-worktree",
            "codex/example",
            "--worktree-dir",
            str(existing),
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("Requested --worktree-dir already exists", result.stderr)

    def test_valid_create_outputs_tripwire_and_next_preflight(self) -> None:
        repo = self.create_repo()

        result = self.run_helper(repo, "--create-worktree", "codex/example")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        created = self.root / "repo-example"
        self.assertTrue(created.exists())
        self.assertIn("Created dedicated worktree", result.stdout)
        self.assertIn("branch: codex/example", result.stdout)
        self.assertIn(f"worktree path: {created}", result.stdout)
        self.assertIn("base ref: origin/main", result.stdout)
        self.assertIn("collision tripwire:", result.stdout)
        self.assertIn("next: cd", result.stdout)
        branch = self.run_git(created, "branch", "--show-current").stdout.strip()
        self.assertEqual(branch, "codex/example")

    def test_target_branch_already_checked_out_in_worktree_fails(self) -> None:
        repo = self.create_repo()
        branch = "codex/in-use"
        self.run_git(repo, "branch", branch)
        self.run_git(repo, "worktree", "add", str(self.root / "repo-in-use"), branch)

        result = self.run_helper(repo, "--dry-run", "--create-worktree", branch)

        self.assertEqual(result.returncode, 1)
        self.assertIn("already exists locally", result.stderr)

    def test_git_prompt_suppression_overrides_interactive_caller_env(self) -> None:
        capture_path = self.root / "git-env.txt"
        real_git = self.real_git_path()
        repo = self.create_repo()
        self.write_executable(
            "git",
            f"""#!/usr/bin/env bash
printf '%s %s\\n' "$GIT_TERMINAL_PROMPT" "$GCM_INTERACTIVE" >> {capture_path}
exec {real_git} "$@"
""",
        )
        env = self.env()
        env["GIT_TERMINAL_PROMPT"] = "1"
        env["GCM_INTERACTIVE"] = "always"

        result = subprocess.run(
            [sys.executable, str(HELPER_SCRIPT), "--dry-run", "--create-worktree", "codex/env"],
            cwd=repo,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(capture_path.exists())
        self.assertTrue(
            all(line == "0 never" for line in capture_path.read_text().splitlines()),
            capture_path.read_text(),
        )

    def test_recommendation_fails_when_remote_collision_check_is_unknown(self) -> None:
        repo = self.create_repo()
        real_git = self.real_git_path()
        self.write_executable(
            "git",
            f"""#!/usr/bin/env bash
if [ "$1 $2" = "ls-remote --exit-code" ]; then
  printf 'simulated remote check failure\\n' >&2
  exit 128
fi
exec {real_git} "$@"
""",
        )

        result = self.run_helper(repo, "--recommend-from-current", "main")

        self.assertEqual(result.returncode, 1)
        self.assertIn("Could not check origin/", result.stderr)
        self.assertNotIn("command: git worktree add", result.stdout)


if __name__ == "__main__":
    unittest.main()
