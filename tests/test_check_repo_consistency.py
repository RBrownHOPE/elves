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

    def test_reviewed_pr_forbidden_corpus_catches_kickoff_merge_policy(self) -> None:
        label = "references/kickoff-prompt-template.md"
        stale = "merge policy (default: you never merge; opt-in: merge-commit-on-green)"

        self.assertIn(label, self.consistency.REVIEWED_PR_LANDING_FORBIDDEN_PHRASES)
        self.assertIn(stale, self.consistency.REVIEWED_PR_LANDING_FORBIDDEN_PHRASES[label])

        errors = self.consistency.find_forbidden_phrases(
            {label: stale},
            self.consistency.REVIEWED_PR_LANDING_FORBIDDEN_PHRASES,
            "reviewed-PR landing",
        )

        self.assertEqual(
            errors,
            [f"{label}: stale reviewed-PR landing phrase `{stale}`"],
        )

    def test_reviewed_pr_forbidden_corpus_catches_kickoff_final_readiness_drift(self) -> None:
        label = "references/kickoff-prompt-template.md"
        stale = "only if the user explicitly set a merge-on-green preference"

        self.assertIn(label, self.consistency.REVIEWED_PR_LANDING_FORBIDDEN_PHRASES)
        self.assertIn(stale, self.consistency.REVIEWED_PR_LANDING_FORBIDDEN_PHRASES[label])

        errors = self.consistency.find_forbidden_phrases(
            {label: stale},
            self.consistency.REVIEWED_PR_LANDING_FORBIDDEN_PHRASES,
            "reviewed-PR landing",
        )

        self.assertEqual(
            errors,
            [f"{label}: stale reviewed-PR landing phrase `{stale}`"],
        )

    def test_reviewed_pr_landing_aliases_are_required_on_user_facing_surfaces(self) -> None:
        for label in ("SKILL.md", "AGENTS.md", "README.md"):
            with self.subTest(label=label):
                self.assertIn(label, self.consistency.REVIEWED_PR_LANDING_PHRASES)
                self.assertIn("\\land-pr", self.consistency.REVIEWED_PR_LANDING_PHRASES[label])
                self.assertIn("/land-pr", self.consistency.REVIEWED_PR_LANDING_PHRASES[label])

    def test_council_aliases_are_required_on_user_facing_surfaces(self) -> None:
        for label in ("SKILL.md", "AGENTS.md", "README.md"):
            with self.subTest(label=label):
                self.assertIn(label, self.consistency.COUNCIL_MODULE_PHRASES)
                self.assertIn("/council", self.consistency.COUNCIL_MODULE_PHRASES[label])
                self.assertIn("/ec", self.consistency.COUNCIL_MODULE_PHRASES[label])
                self.assertIn("/elves-council", self.consistency.COUNCIL_MODULE_PHRASES[label])

    def test_extract_markdown_section_limits_to_requested_heading_level(self) -> None:
        text = """# Title

## Math Research Workflows
OpenRouter

## Elves Council
Quick Council is the default
read-only

### Nested Detail
dissent

## Strategic Forgetting
Quick Council is the default outside the section
"""

        section = self.consistency.extract_markdown_section(text, "## Elves Council")

        self.assertIn("Quick Council is the default", section)
        self.assertIn("### Nested Detail", section)
        self.assertIn("dissent", section)
        self.assertNotIn("## Strategic Forgetting", section)
        self.assertNotIn("outside the section", section)

    def test_council_section_phrases_do_not_pass_from_other_sections(self) -> None:
        label = "SKILL.md"
        texts = {
            label: """# Elves

## Math Research Workflows
Quick Council is the default

## Elves Council
Elves Council
""",
        }
        phrases = {label: ["Quick Council is the default"]}
        headings = {label: "## Elves Council"}

        errors = self.consistency.find_missing_section_phrases(
            texts,
            phrases,
            headings,
            "Elves Council",
        )

        self.assertEqual(
            errors,
            ["SKILL.md: missing Elves Council phrase `Quick Council is the default`"],
        )

    def test_council_forbidden_phrases_catch_provider_requirement(self) -> None:
        label = "references/council-provider-config.md"
        stale = "normal `/council` requires OpenRouter"

        self.assertIn(label, self.consistency.COUNCIL_FORBIDDEN_PHRASES)
        self.assertIn(stale, self.consistency.COUNCIL_FORBIDDEN_PHRASES[label])

        errors = self.consistency.find_forbidden_phrases(
            {label: stale},
            self.consistency.COUNCIL_FORBIDDEN_PHRASES,
            "Elves Council",
        )

        self.assertEqual(
            errors,
            [f"{label}: stale Elves Council phrase `{stale}`"],
        )


if __name__ == "__main__":
    unittest.main()
