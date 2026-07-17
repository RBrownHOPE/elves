# READ THIS FILE FIRST AFTER ANY COMPACTION OR RESTART

## Mission

Promote true exact-session prewalk as Elves v2.8.0 with complete documentation and Codex/Claude
parity, land PR #80 through a regular merge commit, and publish the verified merge on GitHub.

## Run Control

- **Run mode:** finite
- **E2E mode:** chat-to-land
- **User intent:** update all docs including changelog and guide, prove Claude/Codex parity, bump the version, land PR #80, and publish the GitHub version
- **Checkpoint due by:** none
- **Checkpoint semantics:** none
- **May continue after checkpoint:** yes
- **Merge policy:** reviewed-pr-landing-command; regular merge commit only
- **Driver merge authorized:** yes
- **Worker merge authority:** false
- **Final-response policy:** disallowed until PR #80 is merged, v2.8.0 is published from the merge commit, and post-landing verification completes
- **Stop policy:** blocker-only until the merge, release, deployment verification, and cleanup complete
- **Actual stop conditions:** v2.8.0 verified on GitHub from the merge commit, explicit user stop, or genuine safety/authority blocker
- **Work driver:** host-native
- **Delegation scope:** none
- **Implementation lane:** host-native
- **Git mode:** host_only
- **Driver monitor mode:** n_a
- **Driver update policy:** concise material progress updates and bounded check polling
- **Driver review policy:** cumulative host review plus live PR feedback and exact-tip gates
- **High-risk checkpoints:** version identity, host parity, exact pre-merge tip, merge SHA, tag/release target, and cleanup
- **Re-drive budget:** n/a for host-native release work
- **Continuation harness:** host-native
- **Continuation rule:** If work remains and Actual stop conditions are not met, continue without waiting for user acknowledgment.
- **Paid/live model calls:** prohibited; deterministic fixtures and read-only help probes only
- **Workspace ownership:** branch `codex/true-prewalk` in `/Users/john/aigora/dev/elves-true-prewalk`
- **Worktree path:** `/Users/john/aigora/dev/elves-true-prewalk`
- **Branch tip at start:** `4e948e22e8c5bb9001366ae19f61e06112673ca8`
- **Collision tripwire:** `4e948e22e8c5bb9001366ae19f61e06112673ca8`
- **Release target:** `v2.8.0`, created only from the verified regular merge commit
- **Forbidden worktree:** `/Users/john/aigora/dev/elves-explicit-prewalk-handoff`; do not access or modify it
- **Re-read rule:** after every host-owned commit and push, re-read this guide before any next action
- **Batch completion rule:** Every completed batch must end with a commit and push after updating the execution log and survival guide.
- **Checkpoint rule:** No checkpoint is configured; any progress checkpoint is pushed and execution continues immediately.
- **Staging acceptance command:** `python3 scripts/acceptance_contract.py validate --repo-root . --session .elves-session.json`
- **Terminal landing command:** `python3 scripts/elves_landing_check.py --session .elves-session.json --repo-root .`

## Cobbler Session State

- **Cobbler default:** enabled for this Elves continuation
- **Activated by:** the Elves reviewed-PR landing continuation
- **Execution route:** host-native release audit and landing; no model-backed delegation
- **Scope:** release planning, documentation reconciliation, proof, landing, publication, and synthesis
- **Behavior:** direct host execution with Cobbler run control; no delegated implementation packet
- **Persistence:** this guide, `.elves-session.json`, the plan, and execution log
- **Exit phrases:** "Cobbler Mode: off", "leave Cobbler Mode", or "stop using Cobbler by default"
- **Authority:** driver owns version, PR, merge, tag, release, and cleanup

## Stop Gate

- **Planned batches remaining:** 1
- **Stop allowed right now:** no
- **Why:** v2.8.0 promotion, retained-safe documentation reconciliation, exact-tip gates, merge, GitHub release, deployment verification, and cleanup remain
- **Next required action:** commit the B3 release contract, then promote and audit every version/documentation surface

## Current Phase

- **Status:** in progress
- **Active batch:** B3 v2.8.0 release promotion and authorized landing
- **What was just finished:** PR #80 reached green landable state for the original B0-B2 scope
- **Single next action:** commit the release-extension contract and begin the v2.8.0 documentation promotion

## Active Compute

- No model call, canary, dev server, or release job is active.

## Next Exact Batch

**Batch:** B3 v2.8.0 release promotion and authorized landing

**Scope:** align versions, changelog, guide, canonical and durable docs; prove parity and release
readiness; merge; tag and publish; verify Pages and release identity; reclaim only this worktree.

**Acceptance criteria:**

- [ ] B3-A1: every current version marker and release note identifies 2.8.0 and the release checklist passes
- [ ] B3-A2: Codex/Claude docs and fixtures agree, including retained-safe-only activation today
- [ ] B3-A3: focused, installed, strict CI, landing, PR feedback, and required checks are green at exact HEAD

**Risk:** Public version/tag drift or a host-parity overclaim would make the release invalid.

**Rollback authority:** `refs/elves/rollback/true-prewalk-2026-07-17/release-v2.8.0`.

## Effort Standard

- Work as hard as you can for the full release run. Do not be lazy.
- Do not settle for the minimum acceptable change while deeper proof or release work remains.
- When one task finishes, immediately take the next highest-value action from the plan.
- Keep release claims narrower than proof; treat stale docs or host asymmetry as blockers.
- Never move an existing tag or publish from an unverified commit.

## Forbidden Stop Reasons

- A version edit or documentation slice is complete.
- A checkpoint, commit, or push completed.
- Local tests are green but PR checks or release identity remain.
- The PR merged but the tag, GitHub release, guide deployment, or cleanup remains.

## Post-Checkpoint Control Loop

Every completed batch must end with a commit and push. After every host-owned commit and push,
re-read this survival guide before doing anything else. If the Stop Gate still say `Stop allowed right now: no`
or `.elves-session.json` has `continuation_guard.stop_allowed: false`,
execute the single next action. Merge only after exact-tip readiness and green asynchronous checks.

## After Any Compaction

Read the Run Control section and Stop Gate first, then `.elves-session.json` and its
`continuation_guard`, `docs/elves/learnings.md`, the plan, execution log, `.ai-docs/manifest.md`,
PR #80 state, and the live GitHub release/tag state.

## Launch Readiness

- [x] User explicitly authorized landing and GitHub version publication.
- [x] Stop Gate initialized with `Stop allowed right now: no`.
- [x] PR #80 exists, is open, and was green before the release extension.
- [x] Release target selected as v2.8.0 because the repository is already 2.7.0 and prewalk is a new feature.
- [x] No live model calls or behavioral canaries are authorized.
