from __future__ import annotations

import os
import subprocess
import sys
import shutil
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER_SCRIPT = REPO_ROOT / "scripts" / "worktree_gc.py"


class WorktreeGcTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()

    def env(self) -> dict[str, str]:
        env = os.environ.copy()
        for key in list(env):
            if key.startswith("GIT_"):
                env.pop(key)
        env.update(
            {
                "HOME": str(self.home),
                "GIT_CONFIG_NOSYSTEM": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        return env

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
        self.run_git(repo, "config", "user.name", "Elves Test")
        self.run_git(repo, "config", "user.email", "elves@example.com")
        (repo / "README.md").write_text("test repo\n")
        self.run_git(repo, "add", "README.md")
        self.run_git(repo, "commit", "-m", "initial")
        remote = self.root / "remote.git"
        self.run_git(self.root, "init", "--bare", str(remote))
        self.run_git(repo, "remote", "add", "origin", f"file://{remote}")
        if push_main:
            self.run_git(repo, "push", "-u", "origin", "main")
        return repo

    def add_worktree(self, repo: Path, name: str, branch: str) -> Path:
        path = self.root / name
        self.run_git(repo, "worktree", "add", "-b", branch, str(path), "main")
        return path

    def commit_in(self, cwd: Path, filename: str, message: str) -> None:
        (cwd / filename).write_text(f"{message}\n")
        self.run_git(cwd, "add", filename)
        self.run_git(cwd, "commit", "-m", message)

    def make_merged_worktree(self, repo: Path, name: str, branch: str) -> Path:
        """Clean worktree whose branch is pushed and fully merged into origin/main."""
        worktree = self.add_worktree(repo, name, branch)
        self.commit_in(worktree, f"{name}.txt", f"work in {name}")
        self.run_git(worktree, "push", "-u", "origin", branch)
        self.run_git(repo, "push", "origin", f"{branch}:main")
        self.run_git(repo, "fetch", "origin")
        self.run_git(repo, "merge", "--ff-only", "origin/main")
        return worktree

    def run_helper(
        self, cwd: Path, *args: str, timeout: int = 60
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(HELPER_SCRIPT), *args],
            cwd=cwd,
            env=self.env(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            input="this input should not be read\n",
            timeout=timeout,
            check=False,
        )

    def report_sections(self, stdout: str) -> dict[str, list[str]]:
        sections: dict[str, list[str]] = {"candidates": [], "kept": [], "siblings": []}
        current: str | None = None
        for line in stdout.splitlines():
            if line.startswith("candidates ("):
                current = "candidates"
                continue
            if line.startswith("kept (refused):"):
                current = "kept"
                continue
            if line.startswith("unregistered sibling directories"):
                current = "siblings"
                continue
            if not line.strip():
                continue
            if line.startswith("  ") and current is not None:
                sections[current].append(line.strip())
            else:
                current = None
        return sections

    def worktree_paths(self, repo: Path) -> str:
        return self.run_git(repo, "worktree", "list", "--porcelain").stdout

    def repo_state_snapshot(self, repo: Path) -> tuple[str, str, list[str]]:
        worktrees = self.worktree_paths(repo)
        refs = self.run_git(repo, "for-each-ref").stdout
        listing = sorted(entry.name for entry in self.root.iterdir())
        return worktrees, refs, listing

    # ------------------------------------------------------------------
    # Report shape and candidate selection
    # ------------------------------------------------------------------

    def test_report_lists_clean_merged_pushed_worktree_as_candidate(self) -> None:
        repo = self.create_repo()
        worktree = self.make_merged_worktree(repo, "repo-merged", "codex/merged")

        result = self.run_helper(repo)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Worktree gc report", result.stdout)
        self.assertIn("mode: report (read-only; pass --apply to remove candidates)", result.stdout)
        sections = self.report_sections(result.stdout)
        self.assertTrue(
            any(str(worktree) in line and "codex/merged" in line for line in sections["candidates"]),
            result.stdout,
        )
        self.assertTrue(
            any(str(repo) in line and "main worktree" in line for line in sections["kept"]),
            result.stdout,
        )

    # ------------------------------------------------------------------
    # B2-A1: refusal predicate, clause by clause
    # ------------------------------------------------------------------

    def test_refuses_dirty_tracked_worktree(self) -> None:
        repo = self.create_repo()
        worktree = self.make_merged_worktree(repo, "repo-dirty", "codex/dirty")
        (worktree / "repo-dirty.txt").write_text("tracked modification\n")

        result = self.run_helper(repo)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        sections = self.report_sections(result.stdout)
        self.assertFalse(any(str(worktree) in line for line in sections["candidates"]))
        dirty_lines = [line for line in sections["kept"] if str(worktree) in line]
        self.assertEqual(len(dirty_lines), 1, result.stdout)
        self.assertIn("dirty (uncommitted or untracked changes)", dirty_lines[0])

    def test_refuses_worktree_with_untracked_file(self) -> None:
        repo = self.create_repo()
        worktree = self.make_merged_worktree(repo, "repo-untracked", "codex/untracked")
        (worktree / "scratch.txt").write_text("untracked\n")

        result = self.run_helper(repo)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        sections = self.report_sections(result.stdout)
        self.assertFalse(any(str(worktree) in line for line in sections["candidates"]))
        kept_lines = [line for line in sections["kept"] if str(worktree) in line]
        self.assertEqual(len(kept_lines), 1, result.stdout)
        self.assertIn("dirty (uncommitted or untracked changes)", kept_lines[0])

    def test_refuses_unmerged_branch(self) -> None:
        repo = self.create_repo()
        worktree = self.add_worktree(repo, "repo-unmerged", "codex/unmerged")
        self.commit_in(worktree, "unmerged.txt", "unmerged work")
        self.run_git(worktree, "push", "-u", "origin", "codex/unmerged")

        result = self.run_helper(repo)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        sections = self.report_sections(result.stdout)
        self.assertFalse(any(str(worktree) in line for line in sections["candidates"]))
        kept_lines = [line for line in sections["kept"] if str(worktree) in line]
        self.assertEqual(len(kept_lines), 1, result.stdout)
        self.assertIn("not merged into origin/main", kept_lines[0])

    def test_refuses_clean_but_unpushed_worktree(self) -> None:
        repo = self.create_repo()
        worktree = self.add_worktree(repo, "repo-local", "codex/local-only")
        self.commit_in(worktree, "local.txt", "never pushed")

        report = self.run_helper(repo)
        apply_result = self.run_helper(repo, "--apply")

        self.assertEqual(report.returncode, 0, report.stdout + report.stderr)
        sections = self.report_sections(report.stdout)
        self.assertFalse(any(str(worktree) in line for line in sections["candidates"]))
        self.assertTrue(any(str(worktree) in line for line in sections["kept"]), report.stdout)
        self.assertEqual(apply_result.returncode, 0, apply_result.stdout + apply_result.stderr)
        self.assertNotIn("removed worktree", apply_result.stdout)
        self.assertTrue(worktree.exists())

    def test_refuses_worktree_ahead_of_upstream_even_when_merged(self) -> None:
        repo = self.create_repo()
        worktree = self.add_worktree(repo, "repo-ahead", "codex/ahead")
        self.run_git(worktree, "push", "-u", "origin", "codex/ahead")
        self.commit_in(repo, "advance.txt", "main advance")
        self.run_git(repo, "push", "origin", "main")
        self.run_git(worktree, "merge", "--ff-only", "origin/main")

        result = self.run_helper(repo)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        sections = self.report_sections(result.stdout)
        self.assertFalse(any(str(worktree) in line for line in sections["candidates"]))
        kept_lines = [line for line in sections["kept"] if str(worktree) in line]
        self.assertEqual(len(kept_lines), 1, result.stdout)
        self.assertIn("ahead of upstream", kept_lines[0])
        self.assertIn("(unpushed)", kept_lines[0])
        self.assertNotIn("not merged", kept_lines[0])

    def test_refuses_invoking_directory(self) -> None:
        repo = self.create_repo()
        worktree = self.make_merged_worktree(repo, "repo-invoking", "codex/invoking")

        result = self.run_helper(worktree)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        sections = self.report_sections(result.stdout)
        self.assertFalse(any(str(worktree) in line for line in sections["candidates"]))
        kept_lines = [line for line in sections["kept"] if str(worktree) in line]
        self.assertEqual(len(kept_lines), 1, result.stdout)
        self.assertIn("invoking directory (never a candidate)", kept_lines[0])

    def test_refuses_main_and_invoking_worktrees_even_with_apply(self) -> None:
        repo = self.create_repo()
        worktree = self.make_merged_worktree(repo, "repo-self", "codex/self")

        result = self.run_helper(worktree, "--apply")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("removed worktree", result.stdout)
        self.assertTrue(repo.exists())
        self.assertTrue(worktree.exists())
        sections = self.report_sections(result.stdout)
        main_lines = [line for line in sections["kept"] if line.startswith(f"{repo}  branch:")]
        self.assertEqual(len(main_lines), 1, result.stdout)
        self.assertIn("main worktree (never a candidate)", main_lines[0])
        self_lines = [line for line in sections["kept"] if line.startswith(f"{worktree}  branch:")]
        self.assertEqual(len(self_lines), 1, result.stdout)
        self.assertIn("invoking directory (never a candidate)", self_lines[0])

    def test_refuses_locked_worktree(self) -> None:
        repo = self.create_repo()
        worktree = self.make_merged_worktree(repo, "repo-locked", "codex/locked")
        self.run_git(repo, "worktree", "lock", str(worktree))

        result = self.run_helper(repo, "--apply")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("removed worktree", result.stdout)
        self.assertTrue(worktree.exists())
        sections = self.report_sections(result.stdout)
        kept_lines = [line for line in sections["kept"] if str(worktree) in line]
        self.assertEqual(len(kept_lines), 1, result.stdout)
        self.assertIn("locked", kept_lines[0])

    def test_refuses_detached_head_worktree(self) -> None:
        repo = self.create_repo()
        path = self.root / "repo-detached"
        self.run_git(repo, "worktree", "add", "--detach", str(path), "main")

        result = self.run_helper(repo, "--apply")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("removed worktree", result.stdout)
        self.assertTrue(path.exists())
        sections = self.report_sections(result.stdout)
        kept_lines = [line for line in sections["kept"] if str(path) in line]
        self.assertEqual(len(kept_lines), 1, result.stdout)
        self.assertIn("detached HEAD (no branch)", kept_lines[0])

    def test_unregistered_sibling_directory_listed_never_deleted(self) -> None:
        repo = self.create_repo()
        self.make_merged_worktree(repo, "repo-removable", "codex/removable")
        orphan = self.root / "repo-orphan"
        orphan.mkdir()
        (orphan / "keep.txt").write_text("operator data\n")

        report = self.run_helper(repo)
        apply_result = self.run_helper(repo, "--apply")

        self.assertEqual(report.returncode, 0, report.stdout + report.stderr)
        self.assertIn(
            "unregistered sibling directories (operator-owned; listed only, never deleted):",
            report.stdout,
        )
        sections = self.report_sections(report.stdout)
        self.assertTrue(any(str(orphan) in line for line in sections["siblings"]), report.stdout)
        self.assertFalse(any(str(orphan) in line for line in sections["candidates"]))

        self.assertEqual(apply_result.returncode, 0, apply_result.stdout + apply_result.stderr)
        self.assertIn("removed worktree", apply_result.stdout)
        self.assertTrue(orphan.exists())
        self.assertTrue((orphan / "keep.txt").exists())

    # ------------------------------------------------------------------
    # B2-A2: successful removal
    # ------------------------------------------------------------------

    def test_apply_removes_clean_merged_worktree_and_branch(self) -> None:
        repo = self.create_repo()
        worktree = self.make_merged_worktree(repo, "repo-merged", "codex/merged")
        keeper = self.add_worktree(repo, "repo-keeper", "codex/keeper")
        self.commit_in(keeper, "keeper.txt", "unmerged keeper work")
        self.run_git(keeper, "push", "-u", "origin", "codex/keeper")

        result = self.run_helper(repo, "--apply")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"removed worktree {worktree}; deleted branch codex/merged", result.stdout)
        self.assertFalse(worktree.exists())
        registry = self.worktree_paths(repo)
        self.assertNotIn(str(worktree), registry)
        self.assertIn(str(keeper), registry)
        self.assertTrue(keeper.exists())
        branches = self.run_git(repo, "branch", "--list", "codex/merged").stdout.strip()
        self.assertEqual(branches, "")
        keeper_branch = self.run_git(repo, "branch", "--list", "codex/keeper").stdout.strip()
        self.assertNotEqual(keeper_branch, "")

    def test_path_filter_limits_apply_to_named_worktree(self) -> None:
        repo = self.create_repo()
        first = self.make_merged_worktree(repo, "repo-first", "codex/first")
        second = self.make_merged_worktree(repo, "repo-second", "codex/second")

        result = self.run_helper(repo, "--apply", "--path", str(first))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(first.exists())
        self.assertTrue(second.exists())
        registry = self.worktree_paths(repo)
        self.assertNotIn(str(first), registry)
        self.assertIn(str(second), registry)

    def test_path_filter_rejects_unregistered_directory(self) -> None:
        repo = self.create_repo()
        orphan = self.root / "repo-orphan"
        orphan.mkdir()

        result = self.run_helper(repo, "--path", str(orphan))

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("not a registered worktree", result.stderr)

    # ------------------------------------------------------------------
    # B2-A3: report mode performs zero mutations
    # ------------------------------------------------------------------

    def test_report_mode_performs_zero_mutations(self) -> None:
        repo = self.create_repo()
        candidate = self.make_merged_worktree(repo, "repo-candidate", "codex/candidate")
        dirty = self.make_merged_worktree(repo, "repo-zoo-dirty", "codex/zoo-dirty")
        (dirty / "scratch.txt").write_text("untracked\n")
        unmerged = self.add_worktree(repo, "repo-zoo-unmerged", "codex/zoo-unmerged")
        self.commit_in(unmerged, "zoo.txt", "unmerged zoo work")
        prunable = self.add_worktree(repo, "repo-zoo-prunable", "codex/zoo-prunable")
        shutil.rmtree(prunable)
        orphan = self.root / "repo-orphan"
        orphan.mkdir()

        before = self.repo_state_snapshot(repo)
        result = self.run_helper(repo)
        after = self.repo_state_snapshot(repo)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        sections = self.report_sections(result.stdout)
        self.assertTrue(any(str(candidate) in line for line in sections["candidates"]))
        self.assertTrue(any(str(prunable) in line for line in sections["kept"]), result.stdout)
        self.assertEqual(before, after)
        self.assertIn(str(prunable), self.worktree_paths(repo))
        self.assertTrue(candidate.exists())
        self.assertTrue(orphan.exists())

    def test_apply_without_successful_removals_does_not_prune(self) -> None:
        repo = self.create_repo()
        dirty = self.make_merged_worktree(repo, "repo-only-dirty", "codex/only-dirty")
        (dirty / "scratch.txt").write_text("untracked\n")
        prunable = self.add_worktree(repo, "repo-stale", "codex/stale")
        shutil.rmtree(prunable)

        result = self.run_helper(repo, "--apply")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("pruned", result.stdout)
        self.assertIn(str(prunable), self.worktree_paths(repo))

    def test_apply_prunes_registrations_only_after_successful_removal(self) -> None:
        repo = self.create_repo()
        candidate = self.make_merged_worktree(repo, "repo-good", "codex/good")
        prunable = self.add_worktree(repo, "repo-stale", "codex/stale")
        shutil.rmtree(prunable)

        result = self.run_helper(repo, "--apply")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("pruned stale worktree registrations (git worktree prune)", result.stdout)
        registry = self.worktree_paths(repo)
        self.assertNotIn(str(candidate), registry)
        self.assertNotIn(str(prunable), registry)

    # ------------------------------------------------------------------
    # Failure handling and base resolution
    # ------------------------------------------------------------------

    def test_apply_reports_branch_delete_refusal_and_keeps_registry_consistent(self) -> None:
        repo = self.create_repo()
        worktree = self.add_worktree(repo, "repo-gone", "codex/gone")
        self.commit_in(worktree, "gone.txt", "merged then upstream deleted")
        self.run_git(worktree, "push", "-u", "origin", "codex/gone")
        self.run_git(repo, "push", "origin", "codex/gone:main")
        self.run_git(repo, "fetch", "origin")
        self.run_git(repo, "push", "origin", "--delete", "codex/gone")
        self.run_git(repo, "fetch", "--prune", "origin")
        # Local main is intentionally left behind origin/main so `git branch -d`
        # (which falls back to HEAD containment when the upstream is gone) refuses.

        result = self.run_helper(repo, "--apply")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"removed worktree {worktree}", result.stdout)
        self.assertIn("error: failed to delete branch codex/gone", result.stdout)
        self.assertFalse(worktree.exists())
        self.assertNotIn(str(worktree), self.worktree_paths(repo))
        branches = self.run_git(repo, "branch", "--list", "codex/gone").stdout.strip()
        self.assertNotEqual(branches, "")

    def test_missing_default_base_fails_with_base_guidance(self) -> None:
        repo = self.create_repo(push_main=False)

        result = self.run_helper(repo)

        self.assertEqual(result.returncode, 1)
        self.assertIn("Default base origin/main does not resolve; pass --base <ref>.", result.stderr)

    def test_explicit_base_changes_merge_target(self) -> None:
        repo = self.create_repo()
        worktree = self.add_worktree(repo, "repo-develop", "codex/develop-work")
        self.commit_in(worktree, "develop.txt", "develop work")
        self.run_git(worktree, "push", "-u", "origin", "codex/develop-work")
        self.run_git(repo, "push", "origin", "codex/develop-work:develop")
        self.run_git(repo, "fetch", "origin")

        default_result = self.run_helper(repo)
        develop_result = self.run_helper(repo, "--base", "origin/develop")

        self.assertEqual(default_result.returncode, 0)
        default_sections = self.report_sections(default_result.stdout)
        self.assertFalse(any(str(worktree) in line for line in default_sections["candidates"]))

        self.assertEqual(develop_result.returncode, 0, develop_result.stdout + develop_result.stderr)
        self.assertIn("merged into origin/develop", develop_result.stdout)
        develop_sections = self.report_sections(develop_result.stdout)
        self.assertTrue(
            any(str(worktree) in line for line in develop_sections["candidates"]),
            develop_result.stdout,
        )


if __name__ == "__main__":
    unittest.main()
