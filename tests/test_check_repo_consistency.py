from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_repo_consistency.py"


def load_consistency_module():
    spec = importlib.util.spec_from_file_location("check_repo_consistency_under_test", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load check_repo_consistency module for tests")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ConsistencyPhraseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.consistency = load_consistency_module()

    def test_find_missing_phrases_reports_required_reviewed_landing_phrase(self) -> None:
        errors = self.consistency.find_missing_phrases(
            {"SKILL.md": "Reviewed PR Landing Command"},
            {"SKILL.md": ["Reviewed PR Landing Command", "gh pr merge --merge"]},
            "reviewed-PR landing",
        )

        self.assertEqual(
            errors,
            ["SKILL.md: missing reviewed-PR landing phrase `gh pr merge --merge`"],
        )

    def test_find_forbidden_phrases_reports_stale_merge_policy(self) -> None:
        stale = (
            "Only if the user has set a merge-on-green preference in Run Control "
            "do you merge yourself"
        )

        errors = self.consistency.find_forbidden_phrases(
            {"SKILL.md": stale},
            {"SKILL.md": [stale]},
            "reviewed-PR landing",
        )

        self.assertEqual(
            errors,
            [f"SKILL.md: stale reviewed-PR landing phrase `{stale}`"],
        )


if __name__ == "__main__":
    unittest.main()
