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

    def test_cobbler_and_council_aliases_are_required_on_user_facing_surfaces(self) -> None:
        for label in ("SKILL.md", "AGENTS.md", "README.md"):
            with self.subTest(label=label):
                self.assertIn(label, self.consistency.COUNCIL_MODULE_PHRASES)
                self.assertIn("/cobbler", self.consistency.COUNCIL_MODULE_PHRASES[label])
                self.assertIn(
                    "$elves cobbler: <task>",
                    self.consistency.COUNCIL_MODULE_PHRASES[label],
                )
                self.assertIn("/council", self.consistency.COUNCIL_MODULE_PHRASES[label])
                self.assertIn("/ec", self.consistency.COUNCIL_MODULE_PHRASES[label])
                self.assertIn("/elves-council", self.consistency.COUNCIL_MODULE_PHRASES[label])
                self.assertIn(
                    "$elves council: <task>",
                    self.consistency.COUNCIL_MODULE_PHRASES[label],
                )

    def test_codex_cobbler_guardrails_are_required(self) -> None:
        self.assertIn(
            "do not assume Codex has a top-level `/cobbler` command",
            self.consistency.COUNCIL_MODULE_PHRASES["SKILL.md"],
        )
        self.assertIn(
            "Codex users should not need or expect a top-level `/cobbler` command",
            self.consistency.COUNCIL_MODULE_PHRASES["README.md"],
        )
        self.assertIn(
            "Codex Goals are not required for Quick Cobbler",
            self.consistency.COUNCIL_MODULE_PHRASES["references/codex-goals.md"],
        )
        self.assertIn(
            "Do not document Codex as having a top-level `/cobbler`",
            self.consistency.COUNCIL_MODULE_PHRASES["references/council-workflow.md"],
        )

    def test_codex_cobbler_forbidden_phrases_catch_slash_command_drift(self) -> None:
        label = "README.md"
        stale = "Use `/cobbler` in Codex"

        self.assertIn(stale, self.consistency.COUNCIL_FORBIDDEN_PHRASES[label])

        errors = self.consistency.find_forbidden_phrases(
            {label: stale},
            self.consistency.COUNCIL_FORBIDDEN_PHRASES,
            "Cobbler",
        )

        self.assertEqual(errors, [f"{label}: stale Cobbler phrase `{stale}`"])

    def test_cobbler_reference_docs_require_fitted_answer_shape(self) -> None:
        for label in ("references/council-workflow.md", "references/council-prompts.md"):
            with self.subTest(label=label):
                self.assertIn(label, self.consistency.COUNCIL_MODULE_PHRASES)
                self.assertIn("Recommendation", self.consistency.COUNCIL_MODULE_PHRASES[label])
                self.assertIn("Why this fits", self.consistency.COUNCIL_MODULE_PHRASES[label])
                self.assertIn("Strongest dissent", self.consistency.COUNCIL_MODULE_PHRASES[label])
                self.assertIn("Next move", self.consistency.COUNCIL_MODULE_PHRASES[label])

    def test_cobbler_reference_docs_forbid_stale_council_primary_labels(self) -> None:
        label = "references/council-workflow.md"
        stale = "Quick Council is the default"

        self.assertIn(stale, self.consistency.COUNCIL_FORBIDDEN_PHRASES[label])

        errors = self.consistency.find_forbidden_phrases(
            {label: stale},
            self.consistency.COUNCIL_FORBIDDEN_PHRASES,
            "Cobbler",
        )

        self.assertEqual(errors, [f"{label}: stale Cobbler phrase `{stale}`"])

    def test_config_example_requires_cobbler_primary_and_council_compatibility(self) -> None:
        phrases = self.consistency.COUNCIL_MODULE_PHRASES["config.json.example"]

        self.assertIn('"cobbler"', phrases)
        self.assertIn('"default_answer_shape"', phrases)
        self.assertIn('"provider_backed_council"', phrases)
        self.assertIn('"compatibility_for": "cobbler"', phrases)
        self.assertIn('"precedence": "cobbler"', phrases)

    def test_claude_cobbler_alias_skill_files_are_required(self) -> None:
        expected_aliases = {
            "aliases/claude/cobbler/SKILL.md": "/cobbler",
            "aliases/claude/council/SKILL.md": "/council",
            "aliases/claude/ec/SKILL.md": "/ec",
            "aliases/claude/elves-council/SKILL.md": "/elves-council",
        }

        for label, alias in expected_aliases.items():
            with self.subTest(label=label):
                self.assertIn(label, self.consistency.CLAUDE_ALIAS_SKILL_PHRASES)
                self.assertIn(
                    self.consistency.CLAUDE_ALIAS_MARKER,
                    self.consistency.CLAUDE_ALIAS_SKILL_PHRASES[label],
                )
                self.assertIn(alias, self.consistency.CLAUDE_ALIAS_SKILL_PHRASES[label])
                self.assertIn("read-only", self.consistency.CLAUDE_ALIAS_SKILL_PHRASES[label])
                self.assertIn("stateless", self.consistency.CLAUDE_ALIAS_SKILL_PHRASES[label])

    def test_extract_markdown_section_limits_to_requested_heading_level(self) -> None:
        text = """# Title

## Math Research Workflows
OpenRouter

## Cobbler
Quick Cobbler is the default
read-only

### Nested Detail
dissent

## Strategic Forgetting
Quick Cobbler is the default outside the section
"""

        section = self.consistency.extract_markdown_section(text, "## Cobbler")

        self.assertIn("Quick Cobbler is the default", section)
        self.assertIn("### Nested Detail", section)
        self.assertIn("dissent", section)
        self.assertNotIn("## Strategic Forgetting", section)
        self.assertNotIn("outside the section", section)

    def test_cobbler_section_phrases_do_not_pass_from_other_sections(self) -> None:
        label = "SKILL.md"
        texts = {
            label: """# Elves

## Math Research Workflows
Quick Cobbler is the default

## Cobbler
Cobbler
""",
        }
        phrases = {label: ["Quick Cobbler is the default"]}
        headings = {label: "## Cobbler"}

        errors = self.consistency.find_missing_section_phrases(
            texts,
            phrases,
            headings,
            "Cobbler",
        )

        self.assertEqual(
            errors,
            ["SKILL.md: missing Cobbler phrase `Quick Cobbler is the default`"],
        )

    def test_cobbler_forbidden_phrases_catch_provider_requirement(self) -> None:
        label = "references/council-provider-config.md"
        stale = "normal `/council` requires OpenRouter"

        self.assertIn(label, self.consistency.COUNCIL_FORBIDDEN_PHRASES)
        self.assertIn(stale, self.consistency.COUNCIL_FORBIDDEN_PHRASES[label])

        errors = self.consistency.find_forbidden_phrases(
            {label: stale},
            self.consistency.COUNCIL_FORBIDDEN_PHRASES,
            "Cobbler",
        )

        self.assertEqual(
            errors,
            [f"{label}: stale Cobbler phrase `{stale}`"],
        )

    def test_cobbler_forbidden_patterns_catch_provider_requirement_variants(self) -> None:
        label = "README.md"
        text = "Quick Cobbler needs OpenRouter before it can answer."

        errors = self.consistency.find_forbidden_patterns(
            {label: text},
            self.consistency.COBBLER_FORBIDDEN_PATTERNS,
            "Cobbler",
        )

        self.assertEqual(
            errors,
            [
                (
                    "README.md: stale Cobbler pattern "
                    "`\\bquick\\s+cobbler\\s+(?:needs|requires)\\s+openrouter\\b`"
                )
            ],
        )

    def test_cobbler_forbidden_patterns_are_case_insensitive(self) -> None:
        label = "SKILL.md"
        text = "OPENROUTER_API_KEY is required for Cobbler."

        errors = self.consistency.find_forbidden_patterns(
            {label: text},
            self.consistency.COBBLER_FORBIDDEN_PATTERNS,
            "Cobbler",
        )

        self.assertEqual(
            errors,
            [
                (
                    "SKILL.md: stale Cobbler pattern "
                    "`\\bopenrouter_api_key\\b\\s+is\\s+required\\s+for\\s+cobbler\\b`"
                )
            ],
        )

    def test_repo_consistency_workflow_guards_alias_and_config_paths(self) -> None:
        phrases = self.consistency.REPO_CONSISTENCY_WORKFLOW_PHRASES[
            ".github/workflows/repo-consistency.yml"
        ]

        self.assertIn('"config.json.example"', phrases)
        self.assertIn('"aliases/**"', phrases)
        self.assertIn("scripts/validate_survival_guide.py", phrases)

    def test_workspace_isolation_guards_preflight_runtime_check(self) -> None:
        phrases = self.consistency.WORKSPACE_ISOLATION_PHRASES["scripts/preflight.sh"]

        self.assertIn("Workspace Ownership", phrases)
        self.assertIn("git worktree list --porcelain", phrases)
        self.assertIn("Current branch is checked out in one worktree", phrases)
        self.assertIn("(current checkout)", phrases)

    def test_public_wording_guardrails_catch_fable_framing(self) -> None:
        label = "README.md"
        stale = "Fable"

        self.assertIn(stale, self.consistency.PUBLIC_WORDING_FORBIDDEN_PHRASES)

        errors = self.consistency.find_forbidden_phrases(
            {label: stale},
            {label: self.consistency.PUBLIC_WORDING_FORBIDDEN_PHRASES},
            "public wording",
        )

        self.assertEqual(errors, [f"{label}: stale public wording phrase `{stale}`"])

    def test_public_wording_surfaces_include_references_and_aliases(self) -> None:
        surfaces = self.consistency.public_wording_texts()

        self.assertIn("SKILL.md", surfaces)
        self.assertIn("references/council-workflow.md", surfaces)
        self.assertIn("aliases/claude/cobbler/SKILL.md", surfaces)

    def test_public_wording_surfaces_exclude_live_run_artifacts(self) -> None:
        surfaces = self.consistency.public_wording_texts()

        self.assertNotIn("docs/plans/v1.15.0-cobbler.md", surfaces)
        self.assertNotIn("docs/elves/survival-guide.md", surfaces)
        self.assertNotIn("docs/elves/execution-log.md", surfaces)


if __name__ == "__main__":
    unittest.main()
