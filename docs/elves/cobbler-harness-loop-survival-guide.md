# READ THIS FILE FIRST AFTER ANY COMPACTION OR RESTART

This is the live operator brief for the `v1.17.0 Cobbler Harness Loop` run. Trust this file over
conversation memory.

## Mission

Upgrade Cobbler so it captures the full Fable-inspired harness loop in spirit: intent
classification, capability scan, route and output-medium selection, context packets, agents/tools
and skills, evidence collection, fitted synthesis, record-or-present behavior, and reclassification
when evidence changes. Keep Elves native to Codex and Claude Code, and keep external providers
optional.

## Run Control

- **Run mode:** finite
- **Stop policy:** complete requested scope, land PR, then bump and publish GitHub release
- **User intent:** "use the true cobbler to plan this as an $elves run and then run it and then land the pr and bump the version of github after you merge"
- **Checkpoint due by:** none
- **Checkpoint semantics:** none
- **May continue after checkpoint:** yes
- **Actual stop conditions:** stop only after the PR is merged, the version is bumped on `main`, the GitHub release tag is published, and final verification is clean
- **Workspace ownership:** owned branch and main checkout
- **Branch tip at start (collision tripwire):** `96753d1050935c18665a0f865135f9f56af7bf0b`
- **Merge policy:** merge-commit-on-green, explicit user opt-in for this run
- **Final-response policy:** disallowed until stop conditions are met or a true blocker repeats with no viable workaround
- **Coordination mode:** Cobbler-first
- **Batch completion rule:** Every completed batch ends with `update execution log -> update survival guide -> commit -> push` unless it is the final cleanup commit.
- **Re-read rule:** After each commit and push, re-read this survival guide before doing anything else.
- **Checkpoint rule:** If a checkpoint happens, it is a delivery target only. Log it, push it, and continue immediately.
- **Continuation rule:** If work remains and actual stop conditions are not met, continue without waiting for user acknowledgment.

## Session Budget

- **Started:** 2026-06-15 18:15 EDT
- **User returns:** not specified
- **Checkpoint expectation:** none
- **Time budget:** until completion
- **Average batch time so far:** N/A
- **Batches remaining:** 3 of 3

## Stop Gate

- **Planned batches remaining:** 3
- **Stop allowed right now:** no
- **Why:** the PR has not been implemented, merged, version-bumped, or released
- **Next required action:** finish staging, open the PR, then run Batch 1

## Effort Standard

- Work as hard as you can for the full run. Do not be lazy.
- Do not settle for the minimum acceptable change, the first green check, or a shallow pass when deeper verification remains.
- When one task is complete, immediately take the next highest-value action from the plan, review queue, or release checklist.
- Preserve the strongest dissent from Cobbler lenses and turn real gaps into docs or checks.
- Do not stop at a clean commit, PR creation, green local tests, or green CI while release work remains.

## Forbidden Stop Reasons

These are not valid reasons to stop while work remains:

- A checkpoint time was reached
- A clean commit or push succeeded
- CI is green
- A PR exists
- The current batch is complete but later batches remain
- A useful summary was written
- The remaining work feels large
- This feels like a natural place to pause

If one of these happens, update the run docs, commit, push, re-read this file, and continue.

## Post-Checkpoint Control Loop

There is no delivery checkpoint in this run. After every push:

1. Every completed batch must end with a commit and push.
2. re-read this survival guide before doing anything else.
3. Check whether the Stop Gate still say `Stop allowed right now: no`. If yes, continue.
4. Check active compute. It should remain "none" unless a command or PR check is actively running.
5. Poll PR comments and checks when a PR exists.
6. Fix blockers, update session state, commit, push, and continue.

## After Any Compaction

Read these in order:

1. `docs/elves/cobbler-harness-loop-survival-guide.md`
2. `.elves-session.json`
3. `docs/elves/learnings.md`
4. `docs/plans/v1.17.0-cobbler-harness-loop.md`
5. `docs/elves/cobbler-harness-loop-execution-log.md`
6. `.ai-docs/manifest.md`, if it exists
7. PR comments and checks, if the PR exists

Read the Run Control section and Stop Gate before deciding whether to stop. Then read the
`continuation_guard` in `.elves-session.json`. Resume from the `Next Exact Batch` section. Do not
redo completed work.

## Memory Surfaces

- **Plan:** `docs/plans/v1.17.0-cobbler-harness-loop.md`
- **Survival guide:** `docs/elves/cobbler-harness-loop-survival-guide.md`
- **Learnings:** `docs/elves/learnings.md`
- **Execution log:** `docs/elves/cobbler-harness-loop-execution-log.md`
- **Session JSON:** `.elves-session.json`

## Non-Negotiables

- Cobbler remains the default Elves coordinator, not a separate product.
- Normal Cobbler and compatibility aliases must not require OpenRouter or provider keys.
- Do not copy Fable vendor identity, persona, policy, or safety framing.
- Keep Codex invocation honest: `$elves cobbler: <task>` or natural language, not assumed slash commands.
- Keep Claude Code alias skills consistent with the main skill.
- Use a regular merge commit when landing the PR.
- Bump and publish the GitHub release only after the PR lands on `main`.

## Launch Readiness

- [x] Plan cleaned and saved to disk
- [x] Survival guide updated from the current plan
- [x] Learnings file identified
- [x] Execution log initialized with batch breakdown
- [x] Branch created or confirmed
- [ ] PR opened or existing PR recorded
- [ ] Preflight run and critical failures cleared
- [x] Run mode, stop policy, non-negotiables, and merge policy recorded
- [x] Stop Gate initialized with `Stop allowed right now: no`
- [x] Launch prompt prepared

## Current Phase

**Status:** Staging

**Active batch:** Batch 0: session setup

**What was just finished:** Created the branch and drafted the run plan, survival guide, and session log.

**Single next action:** Run preflight, commit staging docs, push, and open the PR.

## Active Compute

**No active paid or long-running compute.**

## Next Exact Batch

After staging and PR creation, start Batch 1: Product Loop Wording.

**Scope:** Add the named Cobbler harness loop to human-facing and agent-facing docs.

**Acceptance criteria:** `docs/cobbler.md`, `README.md`, `SKILL.md`, and `AGENTS.md` explain
capability scan, route and medium selection, context packet, evidence collection, fitted answer,
present/record behavior, and reclassification.

**Risk:** The wording can become jargon or make Cobbler sound like a separate runtime. Keep it
plain and Elves-native.

## Launch Prompt

Continue the Elves run from `docs/elves/cobbler-harness-loop-survival-guide.md`,
`.elves-session.json`, `docs/elves/learnings.md`,
`docs/plans/v1.17.0-cobbler-harness-loop.md`, and
`docs/elves/cobbler-harness-loop-execution-log.md`. Work Cobbler-first, preserve dissent, validate
locally, read PR checks and comments after each push, merge with a regular merge commit when the
final readiness review is clean, then bump and publish the next GitHub release tag from `main`.
