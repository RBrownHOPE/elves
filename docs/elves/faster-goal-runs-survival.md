# READ THIS FILE FIRST — Faster trusted runs

## Run Control

- Mode: finite
- Branch: `codex/faster-goal-runs`
- Base/stack dependency: `codex/b0-acceptance-contracts` / PR #68
- Merge: not authorized; leave a landable draft PR
- Implementation lane: trusted Grok Build native `/goal`
- Driver posture: parked after healthy goal launch; wake only for material transition or terminal review
- Test budget: targeted while changing; one broad local suite per unchanged product/test input tree

## Mission

Implement `docs/plans/faster-goal-runs.md` without weakening its safety kernel. Prefer deleting or
coalescing ceremony over adding another orchestration layer.

## Current Phase

Staging and launch.

## Stop Gate

- Stop allowed right now: no
- Stop when: every master acceptance row has proof, the branch is pushed, independent review is clean, and required checks are green; or a genuine blocker has no safe workaround.

## Next Exact Batch

Launch one native Grok `/goal` for the complete plan. The worker owns B0-B3 internally and pushes
progress. The host stays parked, then performs one cumulative acceptance/review/readiness pass.

## Cobbler Session State

- default_for_session: true
- route: trusted Grok goal for implementation; native Codex subagents for terminal review

## Non-negotiables

- Preserve the plan's safety kernel and untrusted-writer strictness.
- Never merge, alter protected refs, print credentials, or weaken tests for green.
- Do not make Grok, `/goal`, image/video, or any provider required for native Claude Code/Codex.
- Do not rerun the broad suite for docs-only or operational-metadata-only changes.

## Effort Standard

Complete the full plan with the same care in the last batch as the first, but spend effort only on
evidence or changes that can affect acceptance, safety, or release readiness.

## Forbidden Stop Reasons

A commit, push, PR, worker checkpoint, passing targeted test, or tidy summary is not completion.
Do not stop merely because CI is pending while useful implementation work remains.

## Active Compute

- Native Grok `/goal`: pending launch in this worktree.
- No other paid or long-running jobs are expected.

## Post-Checkpoint Control Loop

For ordinary worker progress, remain parked. On a material safety/checkpoint wake, inspect only the
trigger and resume the same goal when safe. On worker exit, perform one cumulative reconciliation,
review, and readiness pass.

## After Any Compaction

Read this guide, `.elves-session.json`, the plan, then the execution log. If the Grok goal is still
healthy, resume observation rather than starting another worker or repeating completed proof.

## Run Paths

- Plan: `docs/plans/faster-goal-runs.md`
- Survival guide: `docs/elves/faster-goal-runs-survival.md`
- Execution log: `docs/elves/faster-goal-runs-execution.md`
- Learnings: `docs/elves/faster-goal-runs-learnings.md`
- Session: `.elves-session.json`
