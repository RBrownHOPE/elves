from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


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


class PrPortfolioReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.portfolio = load_portfolio_module()

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
            ]
        )

        self.assertEqual(pending, ["Repo Consistency"])
        self.assertEqual(bad, ["Lint:FAILURE"])

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

        self.assertFalse(self.portfolio.row_needs_attention(clean))
        self.assertTrue(self.portfolio.row_needs_attention(requested_changes))

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
