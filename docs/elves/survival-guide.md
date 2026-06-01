# READ THIS FILE FIRST AFTER ANY COMPACTION OR RESTART

## Mission

Add a configurable beta math-research module to Elves. The module should support rough-goal
discovery, subfield scouting, cross-field synthesis, proof review, source audit, manuscript
drafting, and human verification as a portable documentation-driven workflow kit.

## Run Control

- **Run mode:** finite
- **Stop policy:** plan-complete-or-blocker
- **User intent:** "PLEASE IMPLEMENT THIS PLAN"
- **Checkpoint due by:** none
- **Checkpoint semantics:** none
- **May continue after checkpoint:** yes
- **Actual stop conditions:** stop only when all five planned batches are complete, validated, and PR-reviewed, or a genuine blocker has no workaround.
- **Workspace ownership:** owned branch + main checkout
- **Branch tip at start (collision tripwire):** `8d326f5a27308237c86b23c9a33b015a2b10c5cc`
- **Merge policy:** user-merges
- **Final-response policy:** allowed after final readiness review
- **Batch completion rule:** every completed batch ends with execution-log update, survival-guide update, commit, push, and re-read.
- **Re-read rule:** immediately after every commit and push, re-read this survival guide before doing anything else.

## Session Budget

- **Started:** 2026-06-01 15:12 EDT
- **User returns:** not specified
- **Checkpoint expectation:** PR-ready math module
- **Time budget:** finite run, no hard deadline
- **Average batch time so far:** 4m
- **Batches remaining:** 0 of 5

## Stop Gate

- **Planned batches remaining:** 0
- **Stop allowed right now:** yes
- **Why:** all planned batches are complete and final validation is clean.
- **Next required action:** user reviews PR #24.

## Effort Standard

- Work through the full plan, not just staging.
- Keep the module portable, configurable, and honest about mathematical verification.
- Update all mirrored docs together; this repo fails by documentation drift more often than code failure.

## Memory Surfaces

- **Plan:** `docs/plans/v1.12.0-math-module.md`
- **Survival guide:** `docs/elves/survival-guide.md`
- **Learnings:** `docs/elves/learnings.md`
- **Execution log:** `docs/elves/execution-log.md`
- **Durable docs:** `.ai-docs/*`

## Current Phase

All planned batches are complete. PR #24 is ready for human review.

## Active Compute

No paid compute, model jobs, local servers, or remote jobs are active.

## Next Exact Batch

No further implementation batch remains. Wait for user review; do not merge.

## Tool Configuration

```yaml
lint:
typecheck:
build:
test:
review: github-pr-comments
notification: pr-comment
consistency: python3 scripts/check_repo_consistency.py
install-doctor: python3 scripts/install_doctor.py --doctor
sync-check: python3 scripts/sync_installed_skills.py --check
```

## Math Module Guardrails

- Model outputs are ideas, checks, or drafts until a human verifies the mathematics.
- Discovery comes before proof writing when the goal is uncertain.
- OpenRouter is the baseline provider; native Gemini, Claude, xAI, OpenAI, and Exa integrations are optional.
- Provider configuration is role-based and user-editable.
