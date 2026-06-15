#!/usr/bin/env python3
"""Check high-value consistency rules for the Elves repo.

This is intentionally narrow and opinionated: it only checks the specific cross-file drift that
already caused review churn in `v1.7.0` and `v1.8.0`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent

VERSION_FILES = {
    "SKILL.md": REPO_ROOT / "SKILL.md",
    "AGENTS.md": REPO_ROOT / "AGENTS.md",
}

CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"

RECOVERY_ORDER_FILES = {
    "SKILL.md": REPO_ROOT / "SKILL.md",
    "AGENTS.md": REPO_ROOT / "AGENTS.md",
    "README.md": REPO_ROOT / "README.md",
    "references/kickoff-prompt-template.md": REPO_ROOT / "references" / "kickoff-prompt-template.md",
    "references/survival-guide-template.md": REPO_ROOT / "references" / "survival-guide-template.md",
}

RECOVERY_ORDER_TOKENS = [
    "survival guide",
    ".elves-session.json",
    "learnings",
    "plan",
    "execution log",
    ".ai-docs/manifest.md",
]

PENDING_DOCS_FILES = {
    "SKILL.md": REPO_ROOT / "SKILL.md",
    "AGENTS.md": REPO_ROOT / "AGENTS.md",
    "references/review-subagent.md": REPO_ROOT / "references" / "review-subagent.md",
}

DURABLE_DOCS = [
    REPO_ROOT / "docs" / "elves" / "learnings.md",
    REPO_ROOT / ".ai-docs" / "manifest.md",
    REPO_ROOT / ".ai-docs" / "architecture.md",
    REPO_ROOT / ".ai-docs" / "conventions.md",
    REPO_ROOT / ".ai-docs" / "gotchas.md",
    REPO_ROOT / "references" / "learnings-template.md",
]

NONSTOP_GUARDRAIL_PHRASES = {
    "SKILL.md": [
        "Stop Gate",
        "continuation_guard",
        "After every commit and push, re-read the survival guide before doing anything else.",
        "Do not wait for user acknowledgment",
    ],
    "AGENTS.md": [
        "Stop Gate",
        "continuation_guard",
        "After every commit and push, re-read the survival guide before doing anything else.",
        "Do not wait for user acknowledgment",
    ],
    "references/survival-guide-template.md": [
        "## Stop Gate",
        "## Forbidden Stop Reasons",
        "Stop allowed right now",
        "Every completed batch must end with a commit and push",
        "continue without waiting for user acknowledgment",
    ],
    "references/kickoff-prompt-template.md": [
        "Every completed batch must end with a commit and push before you start anything else.",
        "Immediately after every commit and push, re-read the survival guide before any other action.",
        "Do not send a final response unless the survival guide Stop Gate says stopping is allowed or a true blocker forces it.",
    ],
    "references/open-ended-guide.md": [
        "## Stop Gate Pattern",
        "## Forbidden Stop Reasons",
        "continuation_guard.stop_allowed: false",
    ],
}

EFFORT_GUARDRAIL_PHRASES = {
    "SKILL.md": [
        "## Effort Standard",
        "Do not be lazy.",
        "Work as hard as you can for",
    ],
    "AGENTS.md": [
        "## Effort Standard",
        "Do not be lazy.",
        "Work as hard as you can for",
    ],
    "references/survival-guide-template.md": [
        "## Effort Standard",
        "Do not be lazy.",
        "Work as hard as you can for the full run.",
    ],
    "references/kickoff-prompt-template.md": [
        "Do not be lazy. Work as hard as you can for the entire run.",
        "Do not coast after the first success, first green check, or first useful checkpoint.",
    ],
    "references/open-ended-guide.md": [
        "## Sustain Effort",
        "Do not be lazy.",
        "Work as hard as you can for the full",
    ],
}

FINAL_READINESS_REVIEW_PHRASES = {
    "SKILL.md": [
        "Final Readiness Review",
        "git diff <default-branch>...HEAD",
        "review subagent",
    ],
    "AGENTS.md": [
        "Final Readiness Review",
        "git diff <default-branch>...HEAD",
        "review subagent",
    ],
    "README.md": [
        "Final readiness review",
        "git diff <default-branch>...HEAD",
    ],
    "references/review-subagent.md": [
        "## Final Readiness Review",
        "git diff [DEFAULT_BRANCH]...HEAD",
    ],
    "references/kickoff-prompt-template.md": [
        "Require a final readiness review",
        "git diff <default-branch>...HEAD",
    ],
}

REPO_CONSISTENCY_WORKFLOW_PHRASES = {
    ".github/workflows/repo-consistency.yml": [
        '"config.json.example"',
        '".github/workflows/repo-consistency.yml"',
        '"aliases/**"',
        "scripts/validate_survival_guide.py",
    ],
}

REVIEWED_PR_LANDING_PHRASES = {
    "SKILL.md": [
        "## Reviewed PR Landing Command",
        "\\land-pr",
        "/land-pr",
        "gh pr merge --merge",
        "default when bots are expected",
    ],
    "AGENTS.md": [
        "## Reviewed PR Landing Command",
        "\\land-pr",
        "/land-pr",
        "gh pr merge --merge",
        "default when bots are expected",
    ],
    "README.md": [
        "### Reviewed PR landing command",
        "\\land-pr",
        "/land-pr",
        "gh pr merge --merge",
        "review the diff from main",
    ],
    "references/review-subagent.md": [
        "### Reviewed PR Landing Command",
        "\\land-pr",
        "/land-pr",
        "gh pr merge --merge",
        "one-off merge opt-in",
    ],
    "references/survival-guide-template.md": [
        "reviewed-pr-landing-command",
        "\\land-pr",
        "/land-pr",
        "one-off explicit merge opt-in",
    ],
    "references/kickoff-prompt-template.md": [
        "reviewed-PR landing command",
        "\\land-pr",
        "/land-pr",
        "gh pr merge --merge",
    ],
    "references/plan-template.md": [
        "reviewed-PR landing command",
        "\\land-pr",
        "/land-pr",
    ],
}

REVIEWED_PR_LANDING_FORBIDDEN_PHRASES = {
    "SKILL.md": [
        "Only if the user has set a merge-on-green preference in Run Control do you merge yourself",
        "A merge is requested and the user has not set a merge-on-green preference.",
    ],
    "AGENTS.md": [
        "Only if the user has set a merge-on-green preference in Run Control do you merge yourself",
        "A merge is requested and the user has not set a merge-on-green preference.",
        "that gate stays with the user unless they set a merge-on-green preference.",
    ],
    "references/kickoff-prompt-template.md": [
        "merge policy (default: you never merge; opt-in: merge-commit-on-green)",
        "only if the user explicitly set a merge-on-green preference",
    ],
}

MEMORY_HYGIENE_PHRASES = {
    "SKILL.md": [
        "## Strategic Forgetting",
        "chats are for execution",
        "memory and resource hygiene",
    ],
    "AGENTS.md": [
        "## Strategic Forgetting",
        "Chats are for execution",
        "memory and resource hygiene",
    ],
    "README.md": [
        "strategic forgetting",
        "handoff docs are for memory",
    ],
    "references/survival-guide-template.md": [
        "## Strategic Forgetting",
        "## Memory and Resource Hygiene",
    ],
    "references/autonomy-guide.md": [
        "## Memory Pressure And Strategic Forgetting",
        "Local app maintenance",
    ],
}

ELVES_REPORT_PHRASES = {
    "SKILL.md": [
        "## Elves Report",
        "problems found",
        "lessons learned",
        "/tmp/elves-report-<repo-slug>-<yyyy-mm-dd>.html",
        "references/elves-report-template.html",
        "collapsible `<details>` sections",
        "committed examples and reusable templates non-identifying",
        "Elves Report path",
    ],
    "AGENTS.md": [
        "## Elves Report",
        "problems found",
        "lessons learned",
        "/tmp/elves-report-<repo-slug>-<yyyy-mm-dd>.html",
        "references/elves-report-template.html",
        "collapsible batch `<details>` sections",
        "committed examples and reusable templates non-identifying",
        "Elves Report path",
    ],
    "README.md": [
        "### Elves Reports",
        "problems found",
        "lessons learned",
        "collapsible sections",
        "docs/elves-report-proof-of-concept.html",
        "references/elves-report-template.html",
        "Committed examples should use non-identifying sample content",
    ],
    "references/survival-guide-template.md": [
        "## Elves Report",
        "problems found",
        "lessons learned",
        "/tmp/elves-report-<repo-slug>-<yyyy-mm-dd>.html",
        "references/elves-report-template.html",
    ],
    "references/execution-log-template.md": [
        "**Elves Report:**",
        "**Problems found:**",
        "**Lessons learned:**",
    ],
    "references/elves-report-template.html": [
        "Elves Report",
        "Problems Found",
        "Lessons Learned",
        "Batch Ledger",
        "<details class=\"batch\"",
    ],
    "docs/elves-report-proof-of-concept.html": [
        "Elves Report",
        "assets/elves-banner.jpeg",
        "Problems found",
        "Lessons learned",
        "Batch Ledger",
        "<details class=\"batch\"",
    ],
}

WORKSPACE_ISOLATION_PHRASES = {
    "SKILL.md": [
        "One run owns one branch and one checkout",
        "collision tripwire",
    ],
    "AGENTS.md": [
        "One run owns one branch and one checkout",
        "collision tripwire",
    ],
    "README.md": [
        "One run owns one branch and one checkout",
        "collision tripwire",
    ],
    "references/survival-guide-template.md": [
        "One run owns one branch and one checkout",
        "collision tripwire",
    ],
    "references/kickoff-prompt-template.md": [
        "git worktree",
    ],
}

MATH_MODULE_PHRASES = {
    "SKILL.md": [
        "## Math Research Workflows",
        "Discovery Sprint",
        "OpenRouter",
        "Never treat model output as mathematical authority",
    ],
    "AGENTS.md": [
        "## Math Research Workflows",
        "Discovery Sprint",
        "OpenRouter",
        "Never treat model output as mathematical authority",
    ],
    "README.md": [
        "### Math research workflows",
        "Discovery Sprint",
        "references/math-workflow.md",
        "references/math-provider-config.md",
        "references/math-artifact-ledgers.md",
    ],
    "references/survival-guide-template.md": [
        "### Math Configuration (optional)",
        "math-provider-policy: openrouter-first",
        "subfield_scout: openrouter:<model-id>",
        "formalization_scout: openrouter:<model-id>",
    ],
    "references/tool-config-examples.md": [
        "## Math Research Workflow",
        "math-provider-policy: openrouter-first",
        "OPENROUTER_API_KEY",
        "math-ledger-dir: docs/math",
    ],
    "references/math-workflow.md": [
        "## The Discovery Sprint",
        "## Cross-Pollination",
        "## Claim Lifecycle",
        "math-artifact-ledgers.md",
    ],
    "references/math-plan-template.md": [
        "## Batch 1: Discovery Sprint",
        "algebraic/combinatorial analogs",
        "Every `quick_win` item has a plausible proof path",
    ],
    "references/math-provider-config.md": [
        "## Role Slots",
        "OPENROUTER_API_KEY",
        "record-before-switching-provider",
    ],
    "references/math-review-prompts.md": [
        "## Subfield Scout",
        "## Proof Critic",
        "## Source Auditor",
        "## Formalization Scout",
    ],
    "references/math-artifact-ledgers.md": [
        "## Claim Ledger",
        "## Source Ledger",
        "## Model-Call Ledger",
        "## Human-Verification Ledger",
    ],
    "config.json.example": [
        '"math"',
        '"provider_policy": "openrouter-first"',
        '"subfield_scout"',
        '"fallback_policy": "record-before-switching-provider"',
    ],
}

COUNCIL_MODULE_PHRASES = {
    "SKILL.md": [
        "## Cobbler",
        "Cobbler",
        "/cobbler",
        "$elves cobbler: <task>",
        "/council",
        "/ec",
        "/elves-council",
        "$elves council: <task>",
        "Host honesty matters",
        "do not assume Codex has a top-level `/cobbler` command",
        "Codex Goals are optional continuation plumbing",
        "not required for a Quick Cobbler answer",
        "Quick Cobbler is the default",
        "read-only",
        "stateless",
        "Codex subagents",
        "Claude Code subagents",
        "read-only analysis directly",
        "Recommendation",
        "Why this fits",
        "Strongest dissent",
        "Risks",
        "Next move",
        "Confidence",
        "mutate run state",
        "Provider-backed council is optional",
        "must not require",
        "vendor identity",
    ],
    "AGENTS.md": [
        "## Cobbler",
        "Cobbler",
        "/cobbler",
        "$elves cobbler: <task>",
        "/council",
        "/ec",
        "/elves-council",
        "$elves council: <task>",
        "Host honesty matters",
        "do not assume Codex has a top-level `/cobbler` command",
        "Codex Goals are optional continuation plumbing",
        "not required for a Quick Cobbler answer",
        "Quick Cobbler is the default",
        "read-only",
        "stateless",
        "Codex subagents",
        "Claude Code subagents",
        "read-only analysis directly",
        "Recommendation",
        "Why this fits",
        "Strongest dissent",
        "Risks",
        "Next move",
        "Confidence",
        "mutate run state",
        "Provider-backed council is optional",
        "must not require",
        "vendor identity",
    ],
    "README.md": [
        "### Cobbler",
        "Cobbler",
        "/cobbler",
        "$elves cobbler: <task>",
        "/council",
        "/ec",
        "/elves-council",
        "$elves council: <task>",
        "Host honesty matters",
        "Codex users should not need or expect a top-level `/cobbler` command",
        "Goals are for full Elves runs, not Quick Cobbler",
        "Quick Cobbler is the default",
        "read-only",
        "stateless",
        "Codex subagents",
        "Claude Code subagents",
        "read-only analysis directly",
        "Recommendation",
        "Why this fits",
        "Strongest dissent",
        "Risks",
        "Next move",
        "Confidence",
        "require no OpenRouter",
        "vendor identity",
        "references/council-workflow.md",
        "references/council-prompts.md",
        "references/council-provider-config.md",
    ],
    "references/council-workflow.md": [
        "# Cobbler Workflow",
        "Council is the compatibility path and gathering mechanism",
        "Claude Code primary: `/cobbler <task>`",
        "Codex primary: `$elves cobbler: <task>`",
        "Codex compatibility: `$elves council: <task>`",
        "Do not document Codex as having a top-level `/cobbler`",
        "Quick Cobbler is the default",
        "Role agents do not see each other's reports before synthesis",
        "Run Cobbler reuses existing Elves memory surfaces",
        "Provider-backed council is optional",
        "use must not require OpenRouter or any external provider key",
        "Do not make Quick Cobbler require OpenRouter",
        "Recommendation",
        "Why this fits",
        "Strongest dissent",
        "Risks",
        "Next move",
        "Confidence",
        "Do not create a separate PR, branch, survival guide, execution log, or ledger",
    ],
    "references/council-prompts.md": [
        "# Cobbler Prompt Templates",
        "Cobbler roles are lenses with obligations, not theatrical personas",
        "Do not read or rely on other role reports",
        "Work read-only",
        "Return one fitted answer",
        "Recommendation",
        "Why this fits",
        "Strongest dissent",
        "Next move",
        "why_this_fits",
        "strongest_dissent",
        "next_move",
    ],
    "references/council-provider-config.md": [
        "# Cobbler Provider-Backed Council Configuration",
        "Normal Cobbler",
        "$elves cobbler: <task>",
        "use must work without",
        "Quick Cobbler needs no provider configuration",
        "cobbler-provider-backed-required-env: []",
        "Do not make ordinary Cobbler or Council-compatible use",
        "Do not create a separate Council ledger",
    ],
    "references/codex-goals.md": [
        "Codex Goals are not required for Quick Cobbler",
        "$elves cobbler: <task>",
        "Ask the Cobbler",
        "$elves council: <task>",
        "You only need a Quick Cobbler answer",
    ],
    "references/tool-config-examples.md": [
        "## Cobbler",
        "Quick Cobbler requires no external provider key",
        "cobbler-default-backend: native-subagents",
        "cobbler-run-logging: existing-elves-memory",
        "cobbler-provider-backed-required-env: []",
        "Legacy `council-*` config keys remain compatibility aliases",
    ],
    "references/survival-guide-template.md": [
        "### Cobbler Configuration (optional)",
        "Provider-backed council is optional advanced plumbing",
        "cobbler-default-backend: native-subagents",
        "cobbler-run-logging: existing-elves-memory",
        "cobbler-provider-backed-required-env: []",
        "Legacy `council-*` config keys remain compatibility aliases",
    ],
    "config.json.example": [
        '"cobbler"',
        '"council"',
        '"primary_invocations"',
        '"default_answer_shape"',
        '"provider_backed_council"',
        '"compatibility_for": "cobbler"',
        '"precedence": "cobbler"',
        '"default_backend": "native-subagents"',
        '"quick_read_only": true',
        '"quick_stateless": true',
        '"run_logging": "existing-elves-memory"',
        '"required_env": []',
        '"provider_policy": "optional-external-providers"',
    ],
}

COUNCIL_SECTION_HEADINGS = {
    "SKILL.md": "## Cobbler",
    "AGENTS.md": "## Cobbler",
    "README.md": "### Cobbler",
}

CLAUDE_ALIAS_MARKER = "<!-- elves-managed-alias: claude-skill-alias v1 -->"

CLAUDE_ALIAS_SKILL_PHRASES = {
    "aliases/claude/cobbler/SKILL.md": [
        CLAUDE_ALIAS_MARKER,
        "name: cobbler",
        "/cobbler",
        "installed `elves` skill's `## Cobbler` instructions",
        "read-only",
        "stateless",
        "Claude Code subagents",
        "Do not edit files",
        "Recommendation",
        "Why this fits",
        "Strongest dissent",
        "Next move",
        "must not require OpenRouter",
    ],
    "aliases/claude/council/SKILL.md": [
        CLAUDE_ALIAS_MARKER,
        "name: council",
        "/council",
        "compatibility alias",
        "installed `elves` skill's `## Cobbler` instructions",
        "read-only",
        "stateless",
        "Claude Code subagents",
        "Do not edit files",
        "Recommendation",
        "Why this fits",
        "Strongest dissent",
        "Next move",
        "must not require OpenRouter",
    ],
    "aliases/claude/ec/SKILL.md": [
        CLAUDE_ALIAS_MARKER,
        "name: ec",
        "/ec",
        "compatibility alias",
        "installed `elves` skill's `## Cobbler` instructions",
        "read-only",
        "stateless",
        "Claude Code subagents",
        "Do not edit files",
        "Recommendation",
        "Why this fits",
        "Strongest dissent",
        "Next move",
        "must not require OpenRouter",
    ],
    "aliases/claude/elves-council/SKILL.md": [
        CLAUDE_ALIAS_MARKER,
        "name: elves-council",
        "/elves-council",
        "compatibility alias",
        "installed `elves` skill's `## Cobbler` instructions",
        "read-only",
        "stateless",
        "Claude Code subagents",
        "Do not edit files",
        "Recommendation",
        "Why this fits",
        "Strongest dissent",
        "Next move",
        "must not require OpenRouter",
    ],
}

COUNCIL_FORBIDDEN_PHRASES = {
    "SKILL.md": [
        "ordinary `/council` requires OpenRouter",
        "normal `/council` requires OpenRouter",
        "ordinary Cobbler requires OpenRouter",
        "normal Cobbler requires OpenRouter",
        "Cobbler requires `OPENROUTER_API_KEY`",
        "Council requires `OPENROUTER_API_KEY`",
        "Cobbler can edit files",
        "Cobbler can create branches",
        "Cobbler can open PRs",
        "Council can edit files",
        "Council can create branches",
        "Council can open PRs",
        "Use `/cobbler` in Codex",
        "Use `/council` in Codex",
        "Quick Cobbler requires Codex Goals",
        "Cobbler requires `/goal`",
    ],
    "AGENTS.md": [
        "ordinary `/council` requires OpenRouter",
        "normal `/council` requires OpenRouter",
        "ordinary Cobbler requires OpenRouter",
        "normal Cobbler requires OpenRouter",
        "Cobbler requires `OPENROUTER_API_KEY`",
        "Council requires `OPENROUTER_API_KEY`",
        "Cobbler can edit files",
        "Cobbler can create branches",
        "Cobbler can open PRs",
        "Council can edit files",
        "Council can create branches",
        "Council can open PRs",
        "Use `/cobbler` in Codex",
        "Use `/council` in Codex",
        "Quick Cobbler requires Codex Goals",
        "Cobbler requires `/goal`",
    ],
    "README.md": [
        "ordinary `/council` requires OpenRouter",
        "normal `/council` requires OpenRouter",
        "ordinary Cobbler requires OpenRouter",
        "normal Cobbler requires OpenRouter",
        "Cobbler requires `OPENROUTER_API_KEY`",
        "Council requires `OPENROUTER_API_KEY`",
        "Cobbler can edit files",
        "Cobbler can create branches",
        "Cobbler can open PRs",
        "Council can edit files",
        "Council can create branches",
        "Council can open PRs",
        "Use `/cobbler` in Codex",
        "Use `/council` in Codex",
        "Quick Cobbler requires Codex Goals",
        "Cobbler requires `/goal`",
    ],
    "references/codex-goals.md": [
        "Quick Cobbler requires Codex Goals",
        "Cobbler requires `/goal`",
        "Use `/cobbler` in Codex",
        "Use `/council` in Codex",
    ],
    "references/council-workflow.md": [
        "ordinary `/council` requires OpenRouter",
        "normal `/council` requires OpenRouter",
        "Council requires `OPENROUTER_API_KEY`",
        "Council can edit files",
        "Council can create branches",
        "Council can open PRs",
        "# Elves Council Workflow",
        "Quick Council is the default",
        "Run Council reuses existing Elves memory surfaces",
        "Deep Council is optional",
        "Do not make Quick Council require OpenRouter",
    ],
    "references/council-provider-config.md": [
        "ordinary `/council` requires OpenRouter",
        "normal `/council` requires OpenRouter",
        "Council requires `OPENROUTER_API_KEY`",
        "# Council Provider Configuration",
        "Quick Council needs no provider configuration",
        "Deep Council is opt-in",
        "council-deep-required-env",
    ],
    "references/council-prompts.md": [
        "# Elves Council Prompts",
        "Council roles are lenses with obligations",
        "You are synthesizing independent Elves Council reports",
    ],
    "references/tool-config-examples.md": [
        "## Elves Council",
        "Quick Council requires no external provider key",
        "Optional Deep Council provider diversity",
        "council-deep-required-env",
    ],
    "references/survival-guide-template.md": [
        "### Elves Council Configuration (optional)",
        "External providers are optional Deep Council",
        "Optional Deep Council provider diversity",
        "council-deep-required-env",
    ],
}

COBBLER_FORBIDDEN_PATTERNS = {
    label: [
        r"\bquick\s+cobbler\s+(?:needs|requires)\s+openrouter\b",
        r"\bnormal\s+cobbler\s+requires\s+(?:an\s+)?external\s+provider\s+key\b",
        r"\bcobbler\s+requires\s+openrouter\b",
        r"\bcouncil-compatible\s+use\s+requires\s+openrouter\b",
        r"\bopenrouter_api_key\b\s+is\s+required\s+for\s+cobbler\b",
    ]
    for label in (
        "SKILL.md",
        "AGENTS.md",
        "README.md",
        "references/council-workflow.md",
        "references/council-provider-config.md",
        "references/tool-config-examples.md",
        "references/survival-guide-template.md",
        "config.json.example",
        "aliases/claude/cobbler/SKILL.md",
        "aliases/claude/council/SKILL.md",
        "aliases/claude/ec/SKILL.md",
        "aliases/claude/elves-council/SKILL.md",
    )
}

FULL_RUN_MODEL_ROUTING_PHRASES = {
    "SKILL.md": [
        "Full-run model routing is a separate optional staging preference",
        "`model-routing` phase preferences",
        "native-first by default",
        "requested route, actual route, and material fallback reason",
        "Missing optional provider access",
        "`required: true`",
    ],
    "AGENTS.md": [
        "Full-run model routing is a separate optional staging preference",
        "`model-routing` phase preferences",
        "native-first by default",
        "requested route, actual route, and material fallback reason",
        "Missing optional provider access",
        "`required: true`",
    ],
    "README.md": [
        "the Cobbler can prefer different elves for different phases",
        "`model-routing` preferences",
        "advisory unless the host",
        "missing optional provider access falls back to",
        "`required: true`",
        "explicit survival-guide opt-in",
    ],
    "references/survival-guide-template.md": [
        "### Full-Run Model Routing (optional)",
        "policy: native-first",
        "fallback: host-native",
        "implement-model: strongest-host-native",
        "requested-route: review independent-lens",
    ],
    "references/execution-log-template.md": [
        "**Phase routing (optional):**",
        "Requested route",
        "Actual route",
        "Fallback reason",
    ],
    "references/review-subagent.md": [
        "## Phase Route Context",
        "requested route, actual route, and fallback reason",
        "required: true",
        "Missing optional provider access is not a",
    ],
    "references/tool-config-examples.md": [
        "## Full-Run Model Routing",
        "model-routing:",
        "native-subagent, host-default",
        "openrouter:<model-id>",
        "Do not treat bare aliases as provider model IDs",
    ],
    "references/council-workflow.md": [
        "Run Cobbler is not the same thing as full-run model routing",
        "requested route, actual route, and material fallback reason",
        "not in a separate council ledger",
    ],
    "references/council-prompts.md": [
        "Full-run model routing belongs to Elves run control",
        "not the Quick Cobbler role selector",
    ],
    "references/council-provider-config.md": [
        "## Full-Run Phase Routes",
        "Provider-backed council slots may satisfy read-only full-run model-routing phases",
        "Do not make implementation provider-backed by default",
        "route, actual route, and fallback reason",
    ],
    "config.json.example": [
        '"model_routing"',
        '"policy": "native-first"',
        '"fallback": "host-native"',
        '"required": false',
        '"provider_backed_allowed": false',
        '"log_material_fallbacks": true',
    ],
}

FULL_RUN_MODEL_ROUTING_FORBIDDEN_PHRASES = {
    label: [
        "ordinary Elves requires OpenRouter",
        "normal Elves requires OpenRouter",
        "model-routing requires OpenRouter",
        "full-run model routing requires OpenRouter",
        "required: true is the default",
        "route mismatch is always blocking",
        "implementation routes to external providers by default",
        "Quick Cobbler uses model-routing",
    ]
    for label in (
        "SKILL.md",
        "AGENTS.md",
        "README.md",
        "references/survival-guide-template.md",
        "references/execution-log-template.md",
        "references/review-subagent.md",
        "references/tool-config-examples.md",
        "references/council-workflow.md",
        "references/council-prompts.md",
        "references/council-provider-config.md",
        "config.json.example",
    )
}

FULL_RUN_MODEL_ROUTING_FORBIDDEN_PATTERNS = {
    label: [
        r"\bfull-run\s+model\s+routing\s+requires\s+(?:an\s+)?external\s+provider\s+key\b",
        r"\bmodel-routing\s+requires\s+openrouter\b",
        r"\brequired:\s*true\s+is\s+(?:the\s+)?default\b",
        r"(?<!do not )\bmake\s+implementation\s+provider-backed\s+by\s+default\b",
    ]
    for label in FULL_RUN_MODEL_ROUTING_FORBIDDEN_PHRASES
}

PUBLIC_WORDING_FILES = [
    REPO_ROOT / "SKILL.md",
    REPO_ROOT / "AGENTS.md",
    REPO_ROOT / "README.md",
    REPO_ROOT / "CHANGELOG.md",
    REPO_ROOT / "config.json.example",
]

PUBLIC_WORDING_FORBIDDEN_PHRASES = [
    "Fable",
    "Fable-like",
    "Fable-style",
    "inspired by Fable",
    "cobbled together",
    "cobbled-together",
]


def read_text(path: Path) -> str:
    return path.read_text()


def read_frontmatter_version(path: Path) -> str | None:
    match = re.search(r'^\s*version:\s*"([^"]+)"\s*$', read_text(path), re.MULTILINE)
    return match.group(1) if match else None


def read_latest_changelog_version(path: Path) -> str | None:
    match = re.search(r"^## \[([^\]]+)\] - ", read_text(path), re.MULTILINE)
    return match.group(1) if match else None


def verify_order(label: str, text: str, tokens: list[str], errors: list[str]) -> None:
    lower = text.lower()
    cursor = 0
    for token in tokens:
        index = lower.find(token.lower(), cursor)
        if index == -1:
            errors.append(f"{label}: missing recovery-order token `{token}`")
            return
        cursor = index + len(token)


def find_missing_phrases(
    texts: dict[str, str],
    phrase_map: dict[str, list[str]],
    category: str,
) -> list[str]:
    errors: list[str] = []
    for label, phrases in phrase_map.items():
        text = texts.get(label, "")
        for phrase in phrases:
            if phrase not in text:
                errors.append(f"{label}: missing {category} phrase `{phrase}`")
    return errors


def find_forbidden_phrases(
    texts: dict[str, str],
    phrase_map: dict[str, list[str]],
    category: str,
) -> list[str]:
    errors: list[str] = []
    for label, phrases in phrase_map.items():
        text = texts.get(label, "")
        for phrase in phrases:
            if phrase in text:
                errors.append(f"{label}: stale {category} phrase `{phrase}`")
    return errors


def find_forbidden_patterns(
    texts: dict[str, str],
    pattern_map: dict[str, list[str]],
    category: str,
) -> list[str]:
    errors: list[str] = []
    for label, patterns in pattern_map.items():
        text = texts.get(label, "")
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                errors.append(f"{label}: stale {category} pattern `{pattern}`")
    return errors


def extract_markdown_section(text: str, heading: str) -> str:
    lines = text.splitlines()
    heading_level = len(heading) - len(heading.lstrip("#"))
    start_index: int | None = None
    for index, line in enumerate(lines):
        if line.strip() == heading:
            start_index = index
            break
    if start_index is None:
        return ""

    section: list[str] = []
    next_heading_re = re.compile(rf"^#{{1,{heading_level}}}\s+")
    for line in lines[start_index:]:
        if section and next_heading_re.match(line):
            break
        section.append(line)
    return "\n".join(section)


def public_wording_texts() -> dict[str, str]:
    paths = [
        *PUBLIC_WORDING_FILES,
        *sorted((REPO_ROOT / "references").glob("*.md")),
        *sorted((REPO_ROOT / "aliases" / "claude").glob("*/SKILL.md")),
    ]
    return {
        path.relative_to(REPO_ROOT).as_posix(): read_text(path)
        for path in paths
        if path.exists()
    }


def find_missing_section_phrases(
    texts: dict[str, str],
    phrase_map: dict[str, list[str]],
    headings: dict[str, str],
    category: str,
) -> list[str]:
    errors: list[str] = []
    for label, heading in headings.items():
        text = texts.get(label, "")
        section = extract_markdown_section(text, heading)
        if not section:
            errors.append(f"{label}: missing {category} section `{heading}`")
            continue
        for phrase in phrase_map.get(label, []):
            if phrase not in section:
                errors.append(f"{label}: missing {category} phrase `{phrase}`")
    return errors


def main() -> int:
    errors: list[str] = []

    versions = {label: read_frontmatter_version(path) for label, path in VERSION_FILES.items()}
    changelog_version = read_latest_changelog_version(CHANGELOG_PATH)

    for label, version in versions.items():
        if version is None:
            errors.append(f"{label}: missing frontmatter version")

    if changelog_version is None:
        errors.append("CHANGELOG.md: missing release heading")

    if not errors:
        expected = next(iter(versions.values()))
        for label, version in versions.items():
            if version != expected:
                errors.append(
                    f"{label}: version `{version}` does not match repo skill version `{expected}`"
                )
        if changelog_version != expected:
            errors.append(
                f"CHANGELOG.md: latest release `{changelog_version}` does not match repo skill version `{expected}`"
            )

    for label, path in RECOVERY_ORDER_FILES.items():
        verify_order(label, read_text(path), RECOVERY_ORDER_TOKENS, errors)

    for label, path in PENDING_DOCS_FILES.items():
        if "PENDING-DOCS" not in read_text(path):
            errors.append(f"{label}: missing `PENDING-DOCS` guidance")

    for path in DURABLE_DOCS:
        if not path.exists():
            errors.append(f"missing durable doc: {path.relative_to(REPO_ROOT)}")

    for label, phrases in NONSTOP_GUARDRAIL_PHRASES.items():
        path = REPO_ROOT / label
        text = read_text(path)
        for phrase in phrases:
            if phrase not in text:
                errors.append(f"{label}: missing non-stop guardrail phrase `{phrase}`")

    for label, phrases in EFFORT_GUARDRAIL_PHRASES.items():
        path = REPO_ROOT / label
        text = read_text(path)
        for phrase in phrases:
            if phrase not in text:
                errors.append(f"{label}: missing effort guardrail phrase `{phrase}`")

    for label, phrases in FINAL_READINESS_REVIEW_PHRASES.items():
        path = REPO_ROOT / label
        text = read_text(path)
        for phrase in phrases:
            if phrase not in text:
                errors.append(f"{label}: missing final-readiness-review phrase `{phrase}`")

    for label, phrases in REPO_CONSISTENCY_WORKFLOW_PHRASES.items():
        path = REPO_ROOT / label
        text = read_text(path)
        for phrase in phrases:
            if phrase not in text:
                errors.append(f"{label}: missing repo-consistency workflow phrase `{phrase}`")

    for label, phrases in MEMORY_HYGIENE_PHRASES.items():
        path = REPO_ROOT / label
        text = read_text(path)
        for phrase in phrases:
            if phrase not in text:
                errors.append(f"{label}: missing memory-hygiene phrase `{phrase}`")

    for label, phrases in ELVES_REPORT_PHRASES.items():
        path = REPO_ROOT / label
        if not path.exists():
            errors.append(f"{label}: missing Elves Report file")
            continue
        text = read_text(path)
        for phrase in phrases:
            if phrase not in text:
                errors.append(f"{label}: missing Elves Report phrase `{phrase}`")

    for label, phrases in WORKSPACE_ISOLATION_PHRASES.items():
        path = REPO_ROOT / label
        text = read_text(path)
        for phrase in phrases:
            if phrase not in text:
                errors.append(f"{label}: missing workspace-isolation phrase `{phrase}`")

    for label, phrases in MATH_MODULE_PHRASES.items():
        path = REPO_ROOT / label
        if not path.exists():
            errors.append(f"{label}: missing math-module file")
            continue
        text = read_text(path)
        for phrase in phrases:
            if phrase not in text:
                errors.append(f"{label}: missing math-module phrase `{phrase}`")

    reviewed_pr_texts = {
        label: read_text(REPO_ROOT / label)
        for label in set(REVIEWED_PR_LANDING_PHRASES) | set(REVIEWED_PR_LANDING_FORBIDDEN_PHRASES)
    }
    errors.extend(
        find_missing_phrases(
            reviewed_pr_texts,
            REVIEWED_PR_LANDING_PHRASES,
            "reviewed-PR landing",
        )
    )
    errors.extend(
        find_forbidden_phrases(
            reviewed_pr_texts,
            REVIEWED_PR_LANDING_FORBIDDEN_PHRASES,
            "reviewed-PR landing",
        )
    )

    council_texts = {
        label: read_text(REPO_ROOT / label)
        for label in set(COUNCIL_MODULE_PHRASES)
        | set(COUNCIL_FORBIDDEN_PHRASES)
        | set(COUNCIL_SECTION_HEADINGS)
    }
    council_section_phrases = {
        label: COUNCIL_MODULE_PHRASES[label] for label in COUNCIL_SECTION_HEADINGS
    }
    council_file_phrases = {
        label: phrases
        for label, phrases in COUNCIL_MODULE_PHRASES.items()
        if label not in COUNCIL_SECTION_HEADINGS
    }
    errors.extend(
        find_missing_section_phrases(
            council_texts,
            council_section_phrases,
            COUNCIL_SECTION_HEADINGS,
            "Cobbler",
        )
    )
    errors.extend(
        find_missing_phrases(
            council_texts,
            council_file_phrases,
            "Cobbler",
        )
    )
    errors.extend(
        find_forbidden_phrases(
            council_texts,
            COUNCIL_FORBIDDEN_PHRASES,
            "Cobbler",
        )
    )
    errors.extend(
        find_forbidden_patterns(
            council_texts,
            COBBLER_FORBIDDEN_PATTERNS,
            "Cobbler",
        )
    )

    full_run_routing_texts = {
        label: read_text(REPO_ROOT / label)
        for label in set(FULL_RUN_MODEL_ROUTING_PHRASES)
        | set(FULL_RUN_MODEL_ROUTING_FORBIDDEN_PHRASES)
    }
    errors.extend(
        find_missing_phrases(
            full_run_routing_texts,
            FULL_RUN_MODEL_ROUTING_PHRASES,
            "full-run model routing",
        )
    )
    errors.extend(
        find_forbidden_phrases(
            full_run_routing_texts,
            FULL_RUN_MODEL_ROUTING_FORBIDDEN_PHRASES,
            "full-run model routing",
        )
    )
    errors.extend(
        find_forbidden_patterns(
            full_run_routing_texts,
            FULL_RUN_MODEL_ROUTING_FORBIDDEN_PATTERNS,
            "full-run model routing",
        )
    )

    public_texts = public_wording_texts()
    errors.extend(
        find_forbidden_phrases(
            public_texts,
            {label: PUBLIC_WORDING_FORBIDDEN_PHRASES for label in public_texts},
            "public wording",
        )
    )

    alias_texts = {label: read_text(REPO_ROOT / label) for label in CLAUDE_ALIAS_SKILL_PHRASES}
    errors.extend(
        find_missing_phrases(
            alias_texts,
            CLAUDE_ALIAS_SKILL_PHRASES,
            "Claude Cobbler alias",
        )
    )

    if errors:
        print("Repo consistency check FAILED")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Repo consistency check OK")
    print(f"- Version: {next(iter(versions.values()))}")
    print("- Recovery order is aligned across repo docs")
    print("- `PENDING-DOCS` guidance is present where expected")
    print("- Durable docs and learnings surfaces exist")
    print("- Workspace-isolation guidance is present across docs")
    print("- Non-stop guardrails are aligned across runtime and template docs")
    print("- Effort guardrails are aligned across runtime and template docs")
    print("- Final readiness review guardrails are aligned")
    print("- Repo consistency workflow guardrails are aligned")
    print("- Strategic forgetting and memory hygiene guardrails are aligned")
    print("- Elves Report guardrails are aligned")
    print("- Math research workflow guardrails are aligned")
    print("- Reviewed PR landing command guardrails are aligned")
    print("- Cobbler guardrails are aligned")
    print("- Full-run model routing guardrails are aligned")
    print("- Public wording guardrails are aligned")
    print("- Claude Cobbler alias guardrails are aligned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
