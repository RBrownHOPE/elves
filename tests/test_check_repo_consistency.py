from __future__ import annotations

import importlib.util
import io
import sys
import unittest
from contextlib import redirect_stdout
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

    def test_codex_cobbler_install_guidance_is_required(self) -> None:
        label = "README.md"
        phrase = "Codex installs the main skill bundle only"

        self.assertIn(label, self.consistency.CODEX_INSTALL_COBBLER_PHRASES)
        self.assertIn(phrase, self.consistency.CODEX_INSTALL_COBBLER_PHRASES[label])

        errors = self.consistency.find_missing_phrases(
            {label: "Codex install docs without the Cobbler reminder"},
            {label: [phrase]},
            "Codex Cobbler install",
        )

        self.assertIn(
            f"{label}: missing Codex Cobbler install phrase `{phrase}`",
            errors,
        )

    def test_cobbler_config_precedence_guidance_is_required(self) -> None:
        label = "SKILL.md"
        phrase = "Cobbler preferences belong under the top-level `cobbler` block"

        self.assertIn(label, self.consistency.COBBLER_CONFIG_PREFERENCE_PHRASES)
        self.assertIn(phrase, self.consistency.COBBLER_CONFIG_PREFERENCE_PHRASES[label])

        errors = self.consistency.find_missing_phrases(
            {label: "Persistent Preferences without Cobbler precedence"},
            {label: [phrase]},
            "Cobbler config preference",
        )

        self.assertIn(
            f"{label}: missing Cobbler config preference phrase `{phrase}`",
            errors,
        )

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
        self.assertIn('"model_routing_policy": "native-first"', phrases)
        self.assertIn('"fallback": "native-subagent-and-note"', phrases)
        self.assertIn('"external_route_example": "openrouter:<model-id>"', phrases)
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

    def test_full_run_model_routing_guardrails_are_required(self) -> None:
        for label in ("SKILL.md", "AGENTS.md", "README.md", "config.json.example"):
            with self.subTest(label=label):
                self.assertIn(label, self.consistency.FULL_RUN_MODEL_ROUTING_PHRASES)

        phrases = self.consistency.FULL_RUN_MODEL_ROUTING_PHRASES
        self.assertIn(
            "Full-run model routing is a separate optional staging preference",
            phrases["SKILL.md"],
        )
        self.assertIn(
            "explicit survival-guide opt-in",
            phrases["README.md"],
        )
        self.assertIn('"policy": "native-first"', phrases["config.json.example"])

    def test_full_run_model_routing_forbidden_phrases_catch_required_provider_drift(self) -> None:
        label = "references/tool-config-examples.md"
        stale = "model-routing requires OpenRouter"

        self.assertIn(stale, self.consistency.FULL_RUN_MODEL_ROUTING_FORBIDDEN_PHRASES[label])

        errors = self.consistency.find_forbidden_phrases(
            {label: stale},
            self.consistency.FULL_RUN_MODEL_ROUTING_FORBIDDEN_PHRASES,
            "full-run model routing",
        )

        self.assertEqual(errors, [f"{label}: stale full-run model routing phrase `{stale}`"])

    def test_full_run_model_routing_forbidden_patterns_catch_required_defaults(self) -> None:
        label = "README.md"
        text = "For this feature, required: true is the default."

        errors = self.consistency.find_forbidden_patterns(
            {label: text},
            self.consistency.FULL_RUN_MODEL_ROUTING_FORBIDDEN_PATTERNS,
            "full-run model routing",
        )

        self.assertEqual(
            errors,
            [
                (
                    "README.md: stale full-run model routing pattern "
                    "`\\brequired:\\s*true\\s+is\\s+(?:the\\s+)?default\\b`"
                )
            ],
        )

    def test_full_run_model_routing_forbidden_patterns_allow_negated_provider_default(self) -> None:
        label = "references/council-provider-config.md"
        text = "Do not make implementation provider-backed by default."

        errors = self.consistency.find_forbidden_patterns(
            {label: text},
            self.consistency.FULL_RUN_MODEL_ROUTING_FORBIDDEN_PATTERNS,
            "full-run model routing",
        )

        self.assertEqual(errors, [])

    def test_full_run_model_routing_forbidden_patterns_catch_positive_provider_default(self) -> None:
        label = "references/council-provider-config.md"
        text = "Users can make implementation provider-backed by default."

        errors = self.consistency.find_forbidden_patterns(
            {label: text},
            self.consistency.FULL_RUN_MODEL_ROUTING_FORBIDDEN_PATTERNS,
            "full-run model routing",
        )

        self.assertEqual(
            errors,
            [
                (
                    "references/council-provider-config.md: stale full-run model routing pattern "
                    "`(?<!do not )\\bmake\\s+implementation\\s+provider-backed\\s+by\\s+default\\b`"
                )
            ],
        )

    def test_math_workflow_remains_allowed_to_be_openrouter_first(self) -> None:
        label = "references/math-provider-config.md"

        self.assertNotIn(label, self.consistency.FULL_RUN_MODEL_ROUTING_FORBIDDEN_PHRASES)
        self.assertIn("OPENROUTER_API_KEY", self.consistency.MATH_MODULE_PHRASES[label])

    def test_repo_consistency_workflow_guards_alias_and_config_paths(self) -> None:
        phrases = self.consistency.REPO_CONSISTENCY_WORKFLOW_PHRASES[
            ".github/workflows/repo-consistency.yml"
        ]

        self.assertIn('"config.json.example"', phrases)
        self.assertIn('".github/ISSUE_TEMPLATE/**"', phrases)
        self.assertIn('"aliases/**"', phrases)
        self.assertIn("scripts/pr_portfolio_report.py", phrases)
        self.assertIn("scripts/validate_survival_guide.py", phrases)
        self.assertIn("scripts/workspace_guard.py", phrases)

    def test_repo_consistency_workflow_requires_node24_action_majors(self) -> None:
        label = ".github/workflows/repo-consistency.yml"
        phrases = self.consistency.REPO_CONSISTENCY_WORKFLOW_PHRASES[label]
        forbidden = self.consistency.REPO_CONSISTENCY_WORKFLOW_FORBIDDEN_PHRASES[label]

        self.assertIn("actions/checkout@v6", phrases)
        self.assertIn("actions/setup-python@v6", phrases)
        self.assertIn("actions/checkout@v4", forbidden)
        self.assertIn("actions/setup-python@v5", forbidden)

    def test_repo_consistency_workflow_rejects_stale_node20_action_majors(self) -> None:
        label = ".github/workflows/repo-consistency.yml"
        stale = "actions/setup-python@v5"

        errors = self.consistency.find_forbidden_phrases(
            {label: stale},
            self.consistency.REPO_CONSISTENCY_WORKFLOW_FORBIDDEN_PHRASES,
            "repo-consistency workflow",
        )

        self.assertEqual(
            errors,
            [f"{label}: stale repo-consistency workflow phrase `{stale}`"],
        )

    def test_main_rejects_stale_repo_consistency_workflow_action_majors(self) -> None:
        label = ".github/workflows/repo-consistency.yml"
        workflow_path = self.consistency.REPO_ROOT / label
        original_read_text = self.consistency.read_text

        def fake_read_text(path: Path) -> str:
            text = original_read_text(path)
            if path == workflow_path:
                return (
                    text
                    + "\n# stale examples for regression coverage\n"
                    + "# actions/checkout@v4\n"
                    + "# actions/setup-python@v5\n"
                )
            return text

        self.consistency.read_text = fake_read_text
        output = io.StringIO()
        try:
            with redirect_stdout(output):
                exit_code = self.consistency.main()
        finally:
            self.consistency.read_text = original_read_text

        self.assertEqual(exit_code, 1)
        self.assertIn(
            f"{label}: stale repo-consistency workflow phrase `actions/checkout@v4`",
            output.getvalue(),
        )
        self.assertIn(
            f"{label}: stale repo-consistency workflow phrase `actions/setup-python@v5`",
            output.getvalue(),
        )

    def test_operator_doc_guardrails_cover_durable_docs_and_run_report_template(self) -> None:
        expected = {
            ".ai-docs/manifest.md",
            ".ai-docs/architecture.md",
            ".ai-docs/conventions.md",
            ".ai-docs/gotchas.md",
            ".github/ISSUE_TEMPLATE/overnight_run_report.md",
            "references/kickoff-prompt-template.md",
        }

        self.assertTrue(expected.issubset(self.consistency.OPERATOR_DOC_PHRASES))
        self.assertIn(
            "- **Run mode:**",
            self.consistency.OPERATOR_DOC_PHRASES[
                ".github/ISSUE_TEMPLATE/overnight_run_report.md"
            ],
        )
        self.assertIn(
            "`Active Compute` if relevant",
            self.consistency.OPERATOR_DOC_PHRASES["references/kickoff-prompt-template.md"],
        )

    def test_operator_doc_guardrails_report_missing_phrase(self) -> None:
        label = ".ai-docs/conventions.md"

        errors = self.consistency.find_missing_phrases(
            {label: "Run control is live metadata"},
            {label: ["Run control is live metadata", "Stop Gate"]},
            "operator-doc",
        )

        self.assertEqual(errors, [f"{label}: missing operator-doc phrase `Stop Gate`"])

    def test_main_reports_missing_operator_doc_phrase(self) -> None:
        label = "README.md"
        target_path = self.consistency.REPO_ROOT / label
        original_phrases = self.consistency.OPERATOR_DOC_PHRASES
        original_read_text = self.consistency.read_text

        def fake_read_text(path: Path) -> str:
            if path == target_path:
                return "present operator phrase"
            return original_read_text(path)

        self.consistency.OPERATOR_DOC_PHRASES = {
            label: ["present operator phrase", "missing operator phrase"]
        }
        self.consistency.read_text = fake_read_text
        output = io.StringIO()
        try:
            with redirect_stdout(output):
                exit_code = self.consistency.main()
        finally:
            self.consistency.OPERATOR_DOC_PHRASES = original_phrases
            self.consistency.read_text = original_read_text

        self.assertEqual(exit_code, 1)
        self.assertIn(
            f"{label}: missing operator-doc phrase `missing operator phrase`",
            output.getvalue(),
        )

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

    def test_context_index_is_a_required_durable_doc(self) -> None:
        relative_docs = {
            path.relative_to(self.consistency.REPO_ROOT).as_posix()
            for path in self.consistency.DURABLE_DOCS
        }

        self.assertIn(".ai-docs/context-index.md", relative_docs)

    def test_api_surface_snapshot_guardrails_are_required(self) -> None:
        for label in ("SKILL.md", "AGENTS.md"):
            with self.subTest(label=label):
                phrases = self.consistency.PUBLIC_API_SURFACE_SNAPSHOT_PHRASES[label]
                self.assertIn(
                    "Public API surface snapshots are optional regression evidence.",
                    phrases,
                )
                self.assertIn(
                    (
                        "A missing snapshot source is not blocking unless `required: true` "
                        "was explicitly set in the survival guide."
                    ),
                    phrases,
                )
                self.assertIn(
                    (
                        "A snapshot proves public surface shape only; it is not a substitute "
                        "for tests, E2E checks, review, or the human-owned constitution."
                    ),
                    phrases,
                )

    def test_api_surface_snapshot_config_defaults_are_advisory(self) -> None:
        phrases = self.consistency.PUBLIC_API_SURFACE_SNAPSHOT_PHRASES["config.json.example"]

        self.assertIn('"api_surface_snapshot"', phrases)
        self.assertIn('"enabled": "auto"', phrases)
        self.assertIn('"required": false', phrases)
        self.assertIn("snapshots are regression evidence, not authority", phrases)

    def test_api_surface_snapshot_forbidden_patterns_catch_required_by_default(self) -> None:
        label = "README.md"
        text = "API snapshots are required, with required: true by default."

        errors = self.consistency.find_forbidden_patterns(
            {label: text},
            self.consistency.PUBLIC_API_SURFACE_SNAPSHOT_FORBIDDEN_PATTERNS,
            "public API surface snapshot",
        )

        self.assertEqual(
            errors,
            [
                (
                    "README.md: stale public API surface snapshot pattern "
                    "`\\bapi\\s+snapshots?\\s+(?:are|is)\\s+required\\b`"
                ),
                (
                    "README.md: stale public API surface snapshot pattern "
                    "`\\brequired:\\s*true\\s+by\\s+default\\b`"
                ),
            ],
        )

    def test_api_surface_snapshot_forbidden_patterns_allow_negated_required_inference(
        self,
    ) -> None:
        label = "SKILL.md"
        text = "Never infer required mode from project type, provider config, or API files."

        errors = self.consistency.find_forbidden_patterns(
            {label: text},
            self.consistency.PUBLIC_API_SURFACE_SNAPSHOT_FORBIDDEN_PATTERNS,
            "public API surface snapshot",
        )

        self.assertEqual(errors, [])

    def test_api_surface_snapshot_forbidden_patterns_allow_negated_artifact_and_secret_rules(
        self,
    ) -> None:
        label = "SKILL.md"
        text = (
            "Do not commit snapshot artifacts. "
            "Do not record secrets. "
            "Never capture bearer tokens. "
            "Must not include customer payloads."
        )

        errors = self.consistency.find_forbidden_patterns(
            {label: text},
            self.consistency.PUBLIC_API_SURFACE_SNAPSHOT_FORBIDDEN_PATTERNS,
            "public API surface snapshot",
        )

        self.assertEqual(errors, [])

    def test_api_surface_snapshot_forbidden_patterns_catch_authority_drift(self) -> None:
        label = "AGENTS.md"
        text = "Snapshots replace tests and should include bearer tokens for debugging."

        errors = self.consistency.find_forbidden_patterns(
            {label: text},
            self.consistency.PUBLIC_API_SURFACE_SNAPSHOT_FORBIDDEN_PATTERNS,
            "public API surface snapshot",
        )

        self.assertEqual(
            errors,
            [
                (
                    "AGENTS.md: stale public API surface snapshot pattern "
                    "`\\bsnapshots?\\s+replace\\s+(?:tests|the constitution|review)\\b`"
                ),
                (
                    "AGENTS.md: stale public API surface snapshot pattern "
                    "`(?<!do not )(?<!never )(?<!don't )(?<!should not )"
                    "(?<!must not )\\binclude\\s+(?:raw\\s+)?(?:secrets|bearer tokens|cookies|customer payloads|production sample data)\\b`"
                ),
            ],
        )


if __name__ == "__main__":
    unittest.main()
