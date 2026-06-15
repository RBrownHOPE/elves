from __future__ import annotations

import contextlib
import importlib.util
import io
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "pr_portfolio_report.py"


def load_portfolio_module():
    spec = importlib.util.spec_from_file_location("pr_portfolio_report_under_test", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load pr_portfolio_report module for tests")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


PORTFOLIO = load_portfolio_module()


class PrPortfolioReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.portfolio = PORTFOLIO

    def test_parse_pr_selection_expands_ranges_and_dedupes(self) -> None:
        self.assertEqual(
            self.portfolio.parse_pr_selection("29-31, 31, 43"),
            [29, 30, 31, 43],
        )

    def test_parse_pr_selection_rejects_descending_ranges(self) -> None:
        with self.assertRaises(ValueError):
            self.portfolio.parse_pr_selection("43-29")

    def test_classify_checks_separates_pending_and_bad(self) -> None:
        pending, bad = self.portfolio.classify_checks(
            [
                {"name": "CodeQL", "status": "COMPLETED", "conclusion": "SUCCESS"},
                {"name": "Socket", "status": "COMPLETED", "conclusion": "NEUTRAL"},
                {"name": "Repo Consistency", "status": "IN_PROGRESS", "conclusion": ""},
                {"name": "Lint", "status": "COMPLETED", "conclusion": "FAILURE"},
                {"__typename": "StatusContext", "context": "legacy", "state": "SUCCESS"},
                {"__typename": "StatusContext", "context": "legacy-pending", "state": "PENDING"},
                {"__typename": "StatusContext", "context": "legacy-failure", "state": "FAILURE"},
            ]
        )

        self.assertEqual(pending, ["Repo Consistency", "legacy-pending"])
        self.assertEqual(bad, ["Lint:FAILURE", "legacy-failure:FAILURE"])

    def test_open_pr_numbers_uses_repo_and_explicit_limit(self) -> None:
        with mock.patch.object(
            self.portfolio,
            "gh_json",
            return_value=[{"number": 44}, {"number": 29}],
        ) as gh_json:
            numbers = self.portfolio.open_pr_numbers("aigorahub/elves")

        self.assertEqual(numbers, [29, 44])
        gh_json.assert_called_once_with(
            [
                "pr",
                "list",
                "--repo",
                "aigorahub/elves",
                "--state",
                "open",
                "--limit",
                "1000",
                "--json",
                "number",
            ]
        )

    def test_summarize_pr_uses_repo_for_view_metadata(self) -> None:
        calls: list[list[str]] = []

        def fake_gh_json(args: list[str]) -> object:
            calls.append(args)
            return {
                "number": 44,
                "url": "https://example.com/44",
                "isDraft": False,
                "mergeStateStatus": "CLEAN",
                "reviewDecision": "",
                "statusCheckRollup": [],
                "headRefName": "codex/pr-portfolio-helper",
            }

        with mock.patch.object(self.portfolio, "gh_json", side_effect=fake_gh_json), mock.patch.object(
            self.portfolio,
            "unresolved_thread_count",
            return_value=0,
        ):
            row = self.portfolio.summarize_pr("aigorahub/elves", 44)

        self.assertEqual(row.branch, "codex/pr-portfolio-helper")
        self.assertIn("--repo", calls[0])
        self.assertIn("aigorahub/elves", calls[0])

    def test_unresolved_thread_count_paginates_all_review_threads(self) -> None:
        responses = [
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"},
                                "nodes": [{"isResolved": True}, {"isResolved": False}],
                            }
                        }
                    }
                }
            },
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                "nodes": [{"isResolved": False}, {"isResolved": False}],
                            }
                        }
                    }
                }
            },
        ]
        calls: list[list[str]] = []

        def fake_gh_json(args: list[str]) -> object:
            calls.append(args)
            return responses.pop(0)

        with mock.patch.object(self.portfolio, "gh_json", side_effect=fake_gh_json):
            unresolved = self.portfolio.unresolved_thread_count("aigorahub/elves", 44)

        self.assertEqual(unresolved, 3)
        self.assertNotIn("after=cursor-1", calls[0])
        self.assertIn("after=cursor-1", calls[1])

    def test_row_needs_attention_tracks_actionable_states(self) -> None:
        clean = self.portfolio.PortfolioRow(
            number=1,
            draft=False,
            branch="codex/clean",
            merge_state="CLEAN",
            review_decision="",
            unresolved_threads=0,
            pending_checks=[],
            bad_checks=[],
            url="https://example.com/1",
        )
        requested_changes = self.portfolio.PortfolioRow(
            number=2,
            draft=False,
            branch="codex/changes",
            merge_state="CLEAN",
            review_decision="CHANGES_REQUESTED",
            unresolved_threads=0,
            pending_checks=[],
            bad_checks=[],
            url="https://example.com/2",
        )
        dirty_merge_state = self.portfolio.PortfolioRow(
            number=3,
            draft=False,
            branch="codex/conflict",
            merge_state="DIRTY",
            review_decision="",
            unresolved_threads=0,
            pending_checks=[],
            bad_checks=[],
            url="https://example.com/3",
        )

        self.assertFalse(self.portfolio.row_needs_attention(clean))
        self.assertTrue(self.portfolio.row_needs_attention(requested_changes))
        self.assertTrue(self.portfolio.row_needs_attention(dirty_merge_state))

    def test_main_fail_on_attention_returns_one_for_attention(self) -> None:
        row = self.portfolio.PortfolioRow(
            number=44,
            draft=False,
            branch="codex/pr-portfolio-helper",
            merge_state="DIRTY",
            review_decision="",
            unresolved_threads=0,
            pending_checks=[],
            bad_checks=[],
            url="https://example.com/44",
        )
        stdout = io.StringIO()

        with mock.patch.object(sys, "argv", ["pr_portfolio_report.py", "--fail-on-attention"]):
            with mock.patch.object(self.portfolio, "current_repo", return_value="aigorahub/elves"):
                with mock.patch.object(self.portfolio, "open_pr_numbers", return_value=[44]):
                    with mock.patch.object(self.portfolio, "summarize_pr", return_value=row):
                        with contextlib.redirect_stdout(stdout):
                            result = self.portfolio.main()

        self.assertEqual(result, 1)
        self.assertIn("#44", stdout.getvalue())

    def test_main_reports_runtime_errors_without_traceback(self) -> None:
        stderr = io.StringIO()

        with mock.patch.object(sys, "argv", ["pr_portfolio_report.py", "--repo", "bad-repo"]):
            with contextlib.redirect_stderr(stderr):
                result = self.portfolio.main()

        self.assertEqual(result, 2)
        self.assertIn("error: repository must be owner/name", stderr.getvalue())

    def test_gh_json_reports_called_process_errors_cleanly(self) -> None:
        failure = subprocess.CalledProcessError(
            returncode=1,
            cmd=["gh", "pr", "view"],
            stderr="authentication required",
        )

        with mock.patch.object(self.portfolio.subprocess, "check_output", side_effect=failure):
            with self.assertRaisesRegex(RuntimeError, "authentication required"):
                self.portfolio.gh_json(["pr", "view"])

    def test_gh_json_reports_missing_gh_cleanly(self) -> None:
        with mock.patch.object(
            self.portfolio.subprocess,
            "check_output",
            side_effect=FileNotFoundError("gh"),
        ):
            with self.assertRaisesRegex(RuntimeError, "GitHub CLI `gh` is not installed"):
                self.portfolio.gh_json(["pr", "view"])

    def test_gh_json_reports_invalid_json_cleanly(self) -> None:
        with mock.patch.object(self.portfolio.subprocess, "check_output", return_value="not json"):
            with self.assertRaisesRegex(RuntimeError, "returned invalid JSON"):
                self.portfolio.gh_json(["pr", "view"])

    def test_format_table_includes_branch_and_counts(self) -> None:
        row = self.portfolio.PortfolioRow(
            number=42,
            draft=True,
            branch="codex/example",
            merge_state="CLEAN",
            review_decision="",
            unresolved_threads=2,
            pending_checks=["CodeQL"],
            bad_checks=[],
            url="https://example.com/42",
        )

        table = self.portfolio.format_table([row])

        self.assertIn("#42", table)
        self.assertIn("codex/example", table)
        self.assertIn("CodeQL", table)


if __name__ == "__main__":
    unittest.main()
