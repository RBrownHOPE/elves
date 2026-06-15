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
    REPO_ROOT / ".ai-docs" / "context-index.md",
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
        '".github/ISSUE_TEMPLATE/**"',
        '".github/workflows/repo-consistency.yml"',
        '"aliases/**"',
        '"docs/cobbler.md"',
        "scripts/pr_portfolio_report.py",
        "scripts/preflight_worktree.py",
        "scripts/validate_survival_guide.py",
        "scripts/workspace_guard.py",
        "actions/checkout@v6",
        "actions/setup-python@v6",
    ],
}

REPO_CONSISTENCY_WORKFLOW_FORBIDDEN_PHRASES = {
    ".github/workflows/repo-consistency.yml": [
        "actions/checkout@v4",
        "actions/setup-python@v5",
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
        "./scripts/preflight.sh --create-worktree <branch> --base origin/main",
        "--dry-run",
        "branch, worktree path, base ref, and collision tripwire",
        "does not reuse, delete, or repair existing worktrees",
        "collision tripwire",
    ],
    "AGENTS.md": [
        "One run owns one branch and one checkout",
        "./scripts/preflight.sh --create-worktree <branch> --base origin/main",
        "--dry-run",
        "branch, worktree path, base ref, and collision tripwire",
        "does not reuse, delete, or repair existing worktrees",
        "collision tripwire",
    ],
    "README.md": [
        "One run owns one branch and one checkout",
        "./scripts/preflight.sh --create-worktree <branch> --base origin/main",
        "--dry-run",
        "branch, worktree path, base ref, and collision tripwire",
        "does not reuse, delete, or repair existing worktrees",
        "collision tripwire",
    ],
    "references/survival-guide-template.md": [
        "One run owns one branch and one checkout",
        "./scripts/preflight.sh --create-worktree <branch> --base origin/main",
        "--dry-run",
        "branch, worktree path, base ref, and collision tripwire",
        "does not reuse, delete, or repair existing worktrees",
        "collision tripwire",
    ],
    "references/kickoff-prompt-template.md": [
        "git worktree",
        "./scripts/preflight.sh --create-worktree <branch> --base origin/main",
        "--dry-run",
        "branch, worktree path, base ref, and collision tripwire",
        "does not reuse, delete, or repair existing worktrees",
    ],
    "scripts/preflight.sh": [
        "Workspace Ownership",
        "preflight_worktree.py",
        "--create-worktree",
        "git worktree list --porcelain",
        "Current branch is checked out in one worktree",
        "(current checkout)",
        "Recommended dedicated worktree",
        "Use one owned branch and checkout per Elves run before launching",
    ],
    "scripts/preflight_worktree.py": [
        "DEFAULT_BASE_REF = \"origin/main\"",
        "--create-worktree",
        "--worktree-dir",
        "--base",
        "--dry-run",
        "git worktree add",
        "Default base origin/main does not resolve; pass --base <ref>.",
        "Requested --worktree-dir already exists",
        "Created dedicated worktree",
        "Recommended dedicated worktree",
        "collision tripwire:",
    ],
}

OPERATOR_DOC_PHRASES = {
    ".ai-docs/manifest.md": [
        "curated layer above the run-specific `docs/elves/*` memory surfaces",
        "survival-guide.md`: active run brief",
        "execution-log.md`: chronological run record",
        "Promotion flow: `execution log -> learnings -> .ai-docs`",
    ],
    ".ai-docs/architecture.md": [
        "## Memory layers",
        "live run control, checkpoint semantics, active compute, next exact batch",
        "Live operator state belongs in the survival guide and should be rewritten in place.",
        "changes almost always cross multiple surfaces",
    ],
    ".ai-docs/conventions.md": [
        "Run control is live metadata",
        "survival guide's `Run Control` block",
        "survival guide's `Stop Gate`",
        "Active Compute",
        "Every completed batch must end with `update docs -> commit -> push -> re-read survival guide`",
        "Repo-only maintenance helpers stay in the checkout.",
        "pin it with a `*_PHRASES` map",
    ],
    ".ai-docs/gotchas.md": [
        "Stop Gate",
        "continuation_guard",
        "same working tree on the same branch",
        "dedicated `git worktree` per run",
        "Paid pods, remote jobs, and long-lived local services",
        "Local project installs can quietly shadow global installs.",
    ],
    ".github/ISSUE_TEMPLATE/overnight_run_report.md": [
        "- **Run mode:**",
        "- **Checkpoint semantics:**",
        "- **Active compute:**",
        "Run-control behavior",
        "What you changed in your setup afterward",
    ],
    "references/kickoff-prompt-template.md": [
        "Set `## Run Control` explicitly",
        "checkpoint semantics",
        "may-continue-after-checkpoint",
        "actual stop conditions",
        "`Active Compute` if relevant",
        "collision tripwire",
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

PUBLIC_API_SURFACE_SNAPSHOT_PHRASES = {
    ".gitignore": [
        ".elves/",
    ],
    "SKILL.md": [
        "Public API surface snapshots are optional regression evidence.",
        "Use existing structured sources before inventing scanners",
        "If no credible source exists, record `unavailable` with the reason instead of fabricating",
        "A missing snapshot source is not blocking unless `required: true` was explicitly set in the survival guide.",
        "`required: true` is valid only when explicitly set by the user or project survival guide.",
        "Do not infer required mode from project type, provider config, framework choice, or the presence of API files.",
        "Snapshot artifacts are run artifacts, not product docs",
        "Temporary snapshot artifacts should not remain in final product PR diffs unless the user explicitly",
        "Record shapes and field names, not secrets, bearer tokens, cookies, customer payloads, or production sample data.",
        "A snapshot proves public surface shape only; it is not a substitute for tests, E2E checks, review, or the human-owned constitution.",
        "public API surface delta when configured",
    ],
    "AGENTS.md": [
        "Public API surface snapshots are optional regression evidence.",
        "Use existing structured sources before inventing scanners",
        "If no credible source exists, record `unavailable` with the reason instead of fabricating",
        "A missing snapshot source is not blocking unless `required: true` was explicitly set in the survival guide.",
        "`required: true` is valid only when explicitly set by the user or project survival guide.",
        "Do not infer required mode from project type, provider config, framework choice, or the presence of API files.",
        "Snapshot artifacts are run artifacts, not product docs",
        "Temporary snapshot artifacts should not remain in final product PR diffs unless the user explicitly",
        "Record shapes and field names, not secrets, bearer tokens, cookies, customer payloads, or production sample data.",
        "A snapshot proves public surface shape only; it is not a substitute for tests, E2E checks, review, or the human-owned constitution.",
        "Public API surface delta",
    ],
    "README.md": [
        "Public API surface snapshots",
        "optional regression evidence",
        "`enabled: auto` stays advisory",
        "`required: true` is only an explicit survival-guide opt-in",
    ],
    "references/survival-guide-template.md": [
        "api-surface-snapshot:",
        "enabled: auto",
        "required: false",
        "Public API surface snapshots are optional regression evidence",
        "A missing snapshot source is not blocking unless required: true was explicitly set",
        "Snapshot artifacts are run artifacts, not product docs",
    ],
    "references/execution-log-template.md": [
        "Public API surface snapshot:",
        "N/A / unavailable reason / no delta / additive / planned breaking / unexpected breaking",
    ],
    "references/review-subagent.md": [
        "API surface snapshot artifacts when configured",
        "Public API surface snapshots",
        "optional regression evidence",
        "A missing snapshot source is not blocking unless `required: true` was explicitly set",
        "A snapshot proves public surface shape only; it is not a substitute",
    ],
    "references/kickoff-prompt-template.md": [
        "Configure optional public API surface snapshot behavior",
        "api-surface-snapshot.enabled: auto",
        "required: false",
        ".elves/api-surface/",
    ],
    "references/tool-config-examples.md": [
        "## Public API Surface Snapshot",
        "enabled: auto",
        "required: false",
        "Use existing structured sources before inventing scanners",
        "If no credible source exists, record `unavailable` with the reason instead of fabricating a snapshot",
        "A snapshot proves public surface shape only; it is not a substitute",
    ],
    "config.json.example": [
        '"api_surface_snapshot"',
        '"enabled": "auto"',
        '"required": false',
        '".elves/api-surface/baseline.json"',
        '"unexpected_breaking_change": "blocking"',
        "snapshots are regression evidence, not authority",
    ],
    "TODO.md": [
        "optional public API surface snapshots",
        "The helper/scanner remains deferred",
    ],
}

PUBLIC_API_SURFACE_SNAPSHOT_FORBIDDEN_PATTERNS = {
    label: [
        r"\bapi\s+snapshots?\s+(?:are|is)\s+required\b",
        r"\bmust\s+generate\s+an?\s+api\s+snapshot\b",
        r"\brequired:\s*true\s+by\s+default\b",
        r"(?<!do not )(?<!never )(?<!don't )(?<!should not )(?<!must not )\binfer\s+required\s+mode\b",
        r"\bsnapshots?\s+replace\s+(?:tests|the constitution|review)\b",
        r"(?<!do not )(?<!never )(?<!don't )(?<!should not )(?<!must not )\bcommit\s+snapshot\s+artifacts\b",
        r"(?<!do not )(?<!never )(?<!don't )(?<!should not )(?<!must not )\brecord\s+(?:raw\s+)?(?:secrets|bearer tokens|cookies|customer payloads|production sample data)\b",
        r"(?<!do not )(?<!never )(?<!don't )(?<!should not )(?<!must not )\bcapture\s+(?:raw\s+)?(?:secrets|bearer tokens|cookies|customer payloads|production sample data)\b",
        r"(?<!do not )(?<!never )(?<!don't )(?<!should not )(?<!must not )\binclude\s+(?:raw\s+)?(?:secrets|bearer tokens|cookies|customer payloads|production sample data)\b",
    ]
    for label in (
        "SKILL.md",
        "AGENTS.md",
        "README.md",
        "references/survival-guide-template.md",
        "references/execution-log-template.md",
        "references/review-subagent.md",
        "references/kickoff-prompt-template.md",
        "references/tool-config-examples.md",
        "config.json.example",
    )
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
        "default orchestration model",
        "Cobbler-first coordination is the default for Elves runs",
        "worker agents may edit the repo",
        "Quick Cobbler is the default one-off answer mode",
        "read-only",
        "stateless",
        "Codex subagents",
        "Claude Code subagents",
        "read-only lens analysis directly",
        "Optional model routing is role-scoped",
        "provider model such as `openrouter:<model-id>`",
        "resolve dissent by",
        "Recommendation",
        "Why this fits",
        "Strongest dissent",
        "Risks",
        "Next move",
        "Confidence",
        "run state",
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
        "default orchestration model",
        "Cobbler-first coordination is the default for Elves runs",
        "worker agents may edit the repo",
        "Quick Cobbler is the default one-off answer mode",
        "read-only",
        "stateless",
        "Codex subagents",
        "Claude Code subagents",
        "read-only lens analysis directly",
        "Optional model routing is role-scoped",
        "provider model such as `openrouter:<model-id>`",
        "resolve dissent by",
        "Recommendation",
        "Why this fits",
        "Strongest dissent",
        "Risks",
        "Next move",
        "Confidence",
        "run state",
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
        "default orchestration model",
        "Cobbler-first coordination is the default for Elves runs",
        "worker agents may edit the repo",
        "Quick Cobbler is the default one-off answer mode",
        "read-only",
        "stateless",
        "Codex subagents",
        "Claude Code subagents",
        "read-only analysis directly",
        "Optional model routing stays behind",
        "configured role routes like `openrouter:<model-id>`",
        "model prestige",
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
        "Cobbler is the default coordination layer",
        "Council is the compatibility path",
        "Claude Code primary: `/cobbler <task>`",
        "Codex primary: `$elves cobbler: <task>`",
        "Codex compatibility: `$elves council: <task>`",
        "Do not document Codex as having a top-level `/cobbler`",
        "Run Cobbler is the default coordination pattern inside an Elves run",
        "Worker agents may edit",
        "Quick Cobbler is the default one-off answer mode",
        "Quick Cobbler is the default one-off answer mode. It is always read-only and stateless",
        "Role agents do not see each other's reports before synthesis",
        "Run Cobbler reuses existing Elves memory surfaces",
        "Provider-backed council is not a third Cobbler mode",
        "use must not require OpenRouter",
        "Optional model routing is role-scoped",
        "fall back to native subagents",
        "model prestige",
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
        "Work scope",
        "worker edit with assigned files",
        "Run Cobbler is the default coordination",
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
        "Cobbler-first coordination is the default for Elves runs",
        "Normal Cobbler",
        "$elves cobbler: <task>",
        "use must work without",
        "Cobbler-first Elves runs and Quick Cobbler one-off answers need no provider configuration",
        "cobbler-coordination-default: cobbler-first",
        "cobbler-model-routing-policy: native-first",
        "provider:model-id",
        "fall back to native",
        "cobbler-provider-backed-required-env: []",
        "Do not make ordinary Cobbler or Council-compatible use",
        "Do not create a separate Council ledger",
    ],
    "references/codex-goals.md": [
        "Goals keeps Codex moving; Cobbler coordinates the Elves loop",
        "Codex Goals are not required for Quick Cobbler",
        "$elves cobbler: <task>",
        "Ask the Cobbler",
        "$elves council: <task>",
        "You only need a Quick Cobbler answer",
    ],
    "references/tool-config-examples.md": [
        "## Cobbler",
        "Default Cobbler coordination block for Elves runs",
        "Cobbler-first is the default orchestration",
        "Quick Cobbler requires no external provider",
        "cobbler-coordination-default: cobbler-first",
        "cobbler-default-backend: native-subagents",
        "cobbler-model-routing-policy: native-first",
        "cobbler-provider-backed-fallback: native-subagent-and-note",
        "openrouter:<model-id>",
        "low, medium, high, or xhigh",
        "cobbler-run-logging: existing-elves-memory",
        "cobbler-provider-backed-required-env: []",
        "Legacy `council-*` config keys remain compatibility aliases",
    ],
    "references/survival-guide-template.md": [
        "Coordination mode",
        "### Cobbler Coordination Defaults",
        "Cobbler-first coordination is the default for Elves runs",
        "Provider-backed council is optional",
        "cobbler-coordination-default: cobbler-first",
        "cobbler-default-backend: native-subagents",
        "cobbler-model-routing-policy: native-first",
        "cobbler-provider-backed-fallback: native-subagent-and-note",
        "openrouter:<model-id>",
        "low, medium, high, or xhigh",
        "cobbler-run-logging: existing-elves-memory",
        "cobbler-provider-backed-required-env: []",
        "Legacy `council-*` config keys remain compatibility aliases",
    ],
    "config.json.example": [
        '"cobbler"',
        '"council"',
        '"coordination_default": "cobbler-first"',
        '"default_for_elves_runs": true',
        '"primary_invocations"',
        '"default_answer_shape"',
        '"provider_backed_council"',
        '"compatibility_for": "cobbler"',
        '"precedence": "cobbler"',
        '"default_backend": "native-subagents"',
        '"quick_read_only": true',
        '"quick_stateless": true',
        '"run_logging": "existing-elves-memory"',
        '"model_routing_policy": "native-first"',
        '"fallback": "native-subagent-and-note"',
        '"external_route_example": "openrouter:<model-id>"',
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
        "default orchestration model",
        "Use the installed `elves` skill",
        "For one-off Quick Cobbler answers, stay read-only and stateless",
        "worker agents may edit scoped files",
        "Claude Code subagents",
        "Recommendation",
        "Why this fits",
        "Strongest dissent",
        "Next move",
        "must not require OpenRouter",
    ],
    "aliases/claude/cobbler-mode/SKILL.md": [
        CLAUDE_ALIAS_MARKER,
        "name: cobbler-mode",
        "/cobbler-mode",
        "Cobbler Mode",
        "default orchestration model",
        "current-thread",
        "Cobbler Mode: on",
        "Cobbler Mode: off",
        "not durable run state",
        "For one-off Quick Cobbler answers, stay read-only and stateless",
        "worker agents may edit scoped files",
        "Claude Code subagents",
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
        "default orchestration model",
        "installed `elves`",
        "For one-off Quick Cobbler answers, stay read-only and stateless",
        "worker agents may edit scoped files",
        "Claude Code subagents",
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
        "default orchestration model",
        "installed `elves`",
        "For one-off Quick Cobbler answers, stay read-only and stateless",
        "worker agents may edit scoped files",
        "Claude Code subagents",
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
        "default orchestration model",
        "installed `elves`",
        "For one-off Quick Cobbler answers, stay read-only and stateless",
        "worker agents may edit scoped files",
        "Claude Code subagents",
        "Recommendation",
        "Why this fits",
        "Strongest dissent",
        "Next move",
        "must not require OpenRouter",
    ],
}

CODEX_INSTALL_COBBLER_PHRASES = {
    "README.md": [
        "Codex installs the main skill bundle only",
        "It does not install the Claude Code slash aliases",
        "For Codex, the sync helper updates the main skill bundle only",
        "rather than a top-level slash alias",
    ],
}

COBBLER_CONFIG_PREFERENCE_PHRASES = {
    "README.md": [
        "Put new Cobbler preferences",
        "under the top-level `cobbler` block",
        "is for compatibility with older",
        "if both blocks are present, `cobbler` wins",
    ],
    "SKILL.md": [
        "Cobbler preferences belong under the top-level `cobbler` block",
        "if both blocks are present",
        "`cobbler` wins",
    ],
    "AGENTS.md": [
        "Cobbler preferences belong under top-level `cobbler`",
        "legacy `council` config remains for compatibility",
        "`cobbler` wins if both are present",
    ],
    "config.json.example": [
        '"precedence": "cobbler"',
        "If both blocks are present, cobbler wins",
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
        "Elves can also run Cobbler",
        "Cobbler is optional for Elves runs",
        "Cobbler only runs when invoked",
        "Run Cobbler is just Quick Cobbler inside a run",
        "Run Cobbler is Quick Cobbler inside an existing Elves run",
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
        "Elves can also run Cobbler",
        "Cobbler is optional for Elves runs",
        "Cobbler only runs when invoked",
        "Run Cobbler is just Quick Cobbler inside a run",
        "Run Cobbler is Quick Cobbler inside an existing Elves run",
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
        "Cobbler is optional for Elves runs",
        "Cobbler only runs when invoked",
        "Run Cobbler is just Quick Cobbler inside a run",
        "Run Cobbler is Quick Cobbler inside an existing Elves run",
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
        "Quick Cobbler is read-only and stateless unless",
        "Quick Cobbler is the default one-off answer mode. It is read-only and stateless unless",
        "### Provider-Backed Council",
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
        "Mode: [Quick Cobbler / Run Cobbler / Provider-backed council]",
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
        r"\bcobbler\s+is\s+optional\s+for\s+elves\s+runs\b",
        r"\bcobbler\s+only\s+runs\s+when\s+invoked\b",
        r"\brun\s+cobbler\s+is\s+(?:just\s+)?quick\s+cobbler\s+inside\s+(?:an?\s+)?(?:active\s+)?(?:elves\s+)?run\b",
        r"\bquick\s+cobbler\b[^.\n]*(?:unless|except)\b[^.\n]*(?:active\s+elves\s+run|implementation|edit|mutate)\b",
        r"\bcobbler\s+mode\s+persists\s+across\s+threads\b",
        r"\bcobbler\s+mode\s+starts\s+an?\s+elves\s+run\b",
        r"\bcobbler\s+mode\s+requires\s+codex\s+goals\b",
        r"\bcobbler\s+mode\s+can\s+edit\s+files\s+by\s+default\b",
        r"\buse\s+`?/cobbler-mode`?\s+in\s+codex\b",
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
        "aliases/claude/cobbler-mode/SKILL.md",
        "aliases/claude/council/SKILL.md",
        "aliases/claude/ec/SKILL.md",
        "aliases/claude/elves-council/SKILL.md",
    )
}

COBBLER_MODE_PHRASES = {
    "SKILL.md": [
        "Cobbler Mode is the lowest-friction way",
        "/cobbler-mode",
        "$elves cobbler-mode",
        "Cobbler Mode: on",
        "Cobbler Mode: off",
        "current-thread conversation state",
        "not durable run state",
        "daemon",
        "Codex slash command",
    ],
    "AGENTS.md": [
        "Cobbler Mode is the lowest-friction way",
        "/cobbler-mode",
        "$elves cobbler-mode",
        "Cobbler Mode: on",
        "Cobbler Mode: off",
        "current-thread conversation state",
        "not durable run state",
        "daemon",
        "Codex slash command",
    ],
    "README.md": [
        "Cobbler Mode is the lowest-friction way",
        "/cobbler-mode",
        "$elves cobbler-mode",
        "Cobbler Mode: on",
        "Cobbler Mode: off",
        "current-thread conversation state",
        "not durable run state",
        "daemon",
        "slash command",
    ],
    "references/council-workflow.md": [
        "### Cobbler Mode",
        "not a third Cobbler behavior mode",
        "/cobbler-mode",
        "$elves cobbler-mode",
        "Cobbler Mode: on",
        "Cobbler Mode: off",
        "current-thread conversation state",
        "not durable run state",
        "branch, PR, survival guide, execution log, Codex Goal",
    ],
    "aliases/claude/cobbler-mode/SKILL.md": [
        "name: cobbler-mode",
        "/cobbler-mode",
        "Cobbler Mode",
        "default orchestration model",
        "current-thread",
        "Cobbler Mode: on",
        "Cobbler Mode: off",
        "not durable run state",
        "daemon",
    ],
}

COBBLER_HARNESS_LOOP_PHRASES = {
    "SKILL.md": [
        "capability scan",
        "route and medium selection",
        "context packet",
        "execute agents/tools/skills",
        "collect evidence",
        "fit answer",
        "present/record",
        "reclassify",
    ],
    "AGENTS.md": [
        "capability scan",
        "route and medium selection",
        "context packet",
        "execute agents/tools/skills",
        "collect evidence",
        "fit answer",
        "present/record",
        "reclassify",
    ],
    "README.md": [
        "capability scan",
        "route and medium selection",
        "context packet",
        "execute agents/tools/skills",
        "collect evidence",
        "fit answer",
        "present/record",
        "reclassify",
    ],
    "docs/cobbler.md": [
        "capability scan",
        "Route and medium selection",
        "Context packet",
        "Execute agents/tools/skills",
        "Collect evidence",
        "Fit answer",
        "Present/record",
        "Reclassify",
        "does not copy Fable's model identity",
    ],
    "references/council-workflow.md": [
        "capability scan",
        "route and medium selection",
        "context packet",
        "execute agents/tools/skills",
        "collect evidence",
        "fit answer",
        "Present/record",
        "Reclassify",
    ],
    "references/council-prompts.md": [
        "Capability scan",
        "Route and medium",
        "Context packet",
        "Evidence",
        "Present/record",
        "Reclassify",
    ],
    "references/tool-config-examples.md": [
        "cobbler-harness-loop",
        "capability-scan",
        "route-and-medium-selection",
        "context-packet",
        "execute-agents-tools-skills",
        "collect-evidence",
        "fit-answer",
        "present-record",
        "reclassify",
    ],
    "references/survival-guide-template.md": [
        "cobbler-harness-loop",
        "capability-scan",
        "route-and-medium-selection",
        "context-packet",
        "execute-agents-tools-skills",
        "collect-evidence",
        "fit-answer",
        "present-record",
        "reclassify",
    ],
    "config.json.example": [
        '"harness_loop"',
        '"capability_scan"',
        '"route_and_medium_selection"',
        '"context_packet"',
        '"execute_agents_tools_skills"',
        '"collect_evidence"',
        '"fit_answer"',
        '"present_record"',
        '"reclassify"',
    ],
    "aliases/claude/cobbler/SKILL.md": [
        "capability scan",
        "route and medium selection",
        "context packet",
        "execute agents/tools/skills",
        "collect evidence",
        "fit answer",
        "present/record",
        "reclassify",
    ],
    "aliases/claude/cobbler-mode/SKILL.md": [
        "capability scan",
        "route and medium selection",
        "context packet",
        "execute agents/tools/skills",
        "collect evidence",
        "fit answer",
        "present/record",
        "reclassify",
    ],
    "aliases/claude/council/SKILL.md": [
        "capability scan",
        "route and medium selection",
        "context packet",
        "execute agents/tools/skills",
        "collect evidence",
        "fit answer",
        "present/record",
        "reclassify",
    ],
    "aliases/claude/ec/SKILL.md": [
        "capability scan",
        "route and medium selection",
        "context packet",
        "execute agents/tools/skills",
        "collect evidence",
        "fit answer",
        "present/record",
        "reclassify",
    ],
    "aliases/claude/elves-council/SKILL.md": [
        "capability scan",
        "route and medium selection",
        "context packet",
        "execute agents/tools/skills",
        "collect evidence",
        "fit answer",
        "present/record",
        "reclassify",
    ],
}

COBBLER_HARNESS_FORBIDDEN_PATTERNS = {
    label: [
        r"\bquick\s+cobbler\b[^.\n]*(?:edits|mutates|commits|pushes|opens\s+prs)\b",
        r"\bcontext\s+packet\b[^.\n]*(?:includes|contains)\s+(?:secrets|tokens|credentials|cookies)\b",
        r"\bprovider-backed\s+council\b[^.\n]*(?:required|default)\b",
        r"\breclassify\b[^.\n]*(?:by\s+changing\s+run\s+state|by\s+creating\s+a\s+new\s+run)\b",
    ]
    for label in COBBLER_HARNESS_LOOP_PHRASES
}

FULL_RUN_MODEL_ROUTING_PHRASES = {
    "SKILL.md": [
        "Full-run model routing is a separate optional staging preference",
        "`model-routing` phase preferences",
        "native-first by default",
        "requested route, actual route, and material fallback reason",
        "`requested_route`, `actual_route`, and `fallback_reason`",
        "Missing optional provider access",
        "`required: true`",
    ],
    "AGENTS.md": [
        "Full-run model routing is a separate optional staging preference",
        "`model-routing` phase preferences",
        "native-first by default",
        "requested route, actual route, and material fallback reason",
        "`model_routes` array",
        "`phase`, `requested_route`, `actual_route`, `fallback_reason`",
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
        "JSON keys: requested_route, actual_route, fallback_reason",
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
        "separate council ledger",
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
    errors.extend(
        find_forbidden_phrases(
            {
                label: read_text(REPO_ROOT / label)
                for label in REPO_CONSISTENCY_WORKFLOW_FORBIDDEN_PHRASES
            },
            REPO_CONSISTENCY_WORKFLOW_FORBIDDEN_PHRASES,
            "repo-consistency workflow",
        )
    )

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

    for label, phrases in OPERATOR_DOC_PHRASES.items():
        path = REPO_ROOT / label
        if not path.exists():
            errors.append(f"{label}: missing operator-doc file")
            continue
        text = read_text(path)
        for phrase in phrases:
            if phrase not in text:
                errors.append(f"{label}: missing operator-doc phrase `{phrase}`")

    for label, phrases in MATH_MODULE_PHRASES.items():
        path = REPO_ROOT / label
        if not path.exists():
            errors.append(f"{label}: missing math-module file")
            continue
        text = read_text(path)
        for phrase in phrases:
            if phrase not in text:
                errors.append(f"{label}: missing math-module phrase `{phrase}`")

    api_surface_texts = {
        label: read_text(REPO_ROOT / label)
        for label in set(PUBLIC_API_SURFACE_SNAPSHOT_PHRASES)
        | set(PUBLIC_API_SURFACE_SNAPSHOT_FORBIDDEN_PATTERNS)
    }
    errors.extend(
        find_missing_phrases(
            api_surface_texts,
            PUBLIC_API_SURFACE_SNAPSHOT_PHRASES,
            "public API surface snapshot",
        )
    )
    errors.extend(
        find_forbidden_patterns(
            api_surface_texts,
            PUBLIC_API_SURFACE_SNAPSHOT_FORBIDDEN_PATTERNS,
            "public API surface snapshot",
        )
    )

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
        | set(COBBLER_FORBIDDEN_PATTERNS)
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
    codex_install_texts = {label: council_texts[label] for label in CODEX_INSTALL_COBBLER_PHRASES}
    errors.extend(
        find_missing_phrases(
            codex_install_texts,
            CODEX_INSTALL_COBBLER_PHRASES,
            "Codex Cobbler install",
        )
    )
    cobbler_config_texts = {
        label: council_texts[label] for label in COBBLER_CONFIG_PREFERENCE_PHRASES
    }
    errors.extend(
        find_missing_phrases(
            cobbler_config_texts,
            COBBLER_CONFIG_PREFERENCE_PHRASES,
            "Cobbler config preference",
        )
    )
    cobbler_mode_texts = {
        label: read_text(REPO_ROOT / label)
        for label in set(COBBLER_MODE_PHRASES) | set(COBBLER_FORBIDDEN_PATTERNS)
    }
    errors.extend(
        find_missing_phrases(
            cobbler_mode_texts,
            COBBLER_MODE_PHRASES,
            "Cobbler Mode",
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
    cobbler_harness_texts = {
        label: read_text(REPO_ROOT / label)
        for label in set(COBBLER_HARNESS_LOOP_PHRASES) | set(COBBLER_HARNESS_FORBIDDEN_PATTERNS)
    }
    errors.extend(
        find_missing_phrases(
            cobbler_harness_texts,
            COBBLER_HARNESS_LOOP_PHRASES,
            "Cobbler harness loop",
        )
    )
    errors.extend(
        find_forbidden_patterns(
            cobbler_harness_texts,
            COBBLER_HARNESS_FORBIDDEN_PATTERNS,
            "Cobbler harness loop",
        )
    )

    full_run_routing_texts = {
        label: read_text(REPO_ROOT / label)
        for label in FULL_RUN_MODEL_ROUTING_PHRASES
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
    print("- Operator-facing docs are aligned")
    print("- Non-stop guardrails are aligned across runtime and template docs")
    print("- Effort guardrails are aligned across runtime and template docs")
    print("- Final readiness review guardrails are aligned")
    print("- Repo consistency workflow guardrails are aligned")
    print("- Strategic forgetting and memory hygiene guardrails are aligned")
    print("- Elves Report guardrails are aligned")
    print("- Math research workflow guardrails are aligned")
    print("- Public API surface snapshot guardrails are aligned")
    print("- Reviewed PR landing command guardrails are aligned")
    print("- Cobbler guardrails are aligned")
    print("- Cobbler harness loop guardrails are aligned")
    print("- Full-run model routing guardrails are aligned")
    print("- Public wording guardrails are aligned")
    print("- Claude Cobbler alias guardrails are aligned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
