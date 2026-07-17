# READ THIS FILE FIRST AFTER ANY COMPACTION OR RESTART

## Mission

Implement true trajectory-preserving prewalk for Codex and Claude supervised native workers, prove
it with deterministic fixtures and full repository verification, then open an unmerged PR from
`codex/true-prewalk`.

## Run Control

- **Run mode:** finite
- **E2E mode:** chat-to-work
- **User intent:** "Do not stop after planning. Continue until the PR is open, tested, self-reviewed, and ready for human review."
- **Checkpoint due by:** none
- **Checkpoint semantics:** none
- **May continue after checkpoint:** yes
- **Merge policy:** never-merge; user merges
- **Final-response policy:** disallowed until the reviewed PR is ready or a genuine blocker exists
- **Work driver:** host-native
- **Delegation scope:** none
- **Git mode:** host_only
- **Driver monitor mode:** n_a
- **Driver update policy:** concise material progress updates; no timed polling
- **Driver poll policy:** interactive
- **Driver review policy:** final independent cumulative review only; host self-review because live model calls are prohibited
- **Labor re-drive budget:** 3
- **Re-drive budget:** n/a for host-native execution
- **Continuation harness:** host-native
- **Continuation rule:** If work remains and Actual stop conditions are not met, continue without waiting for user acknowledgment.
- **Stop policy:** blocker-only until tested, self-reviewed PR is open
- **Actual stop conditions:** reviewed unmerged PR ready, explicit user stop, or genuine safety/authority blocker
- **Workspace ownership:** branch `codex/true-prewalk` in `/Users/john/aigora/dev/elves-true-prewalk`
- **Worktree path:** `/Users/john/aigora/dev/elves-true-prewalk`
- **Branch tip at start:** `206a625b68bbb42e7fd6e8283ac65945c0f73648`
- **Collision tripwire:** `206a625b68bbb42e7fd6e8283ac65945c0f73648`
- **Risk:** high
- **Trust mode:** trusted
- **Landing outcome:** landable_pr
- **Driver merge authorized:** no
- **Worker merge authority:** false
- **High-risk checkpoints:** exact-session identity, worktree/Git authority, post-edit recovery, host-parity release gate, and terminal readiness
- **Implementation lane:** host-native
- **Worker packet:** none; host-native runs legitimately skip delegated packets
- **Paid/live model calls:** prohibited by user; use deterministic fixtures and help-only probes
- **Forbidden worktree:** `/Users/john/aigora/dev/elves-explicit-prewalk-handoff`; do not read changes from, modify, or copy it
- **Re-read rule:** after every host-owned commit and push, re-read this guide before any next action
- **Batch completion rule:** Every completed batch must end with a commit and push after updating the execution log and survival guide.
- **Checkpoint rule:** No checkpoint is configured; any progress checkpoint is pushed and execution continues immediately.
- **Staging acceptance command:** `python3 scripts/acceptance_contract.py validate --repo-root . --session .elves-session.json`
- **Terminal landing command:** `python3 scripts/elves_landing_check.py --session .elves-session.json --repo-root .`

## Cobbler Session State

- **Cobbler default:** enabled for Elves coordination
- **Activated by:** Elves invocation
- **Execution route:** direct host-native because the specification is authoritative and model calls are prohibited
- **Scope:** run classification, staging, proof, and terminal synthesis
- **Behavior:** direct-agent override for implementation, with Cobbler-mediated run control and synthesis
- **Persistence:** survival guide and `.elves-session.json`
- **Exit phrases:** "Cobbler Mode: off", "leave Cobbler Mode", or "stop using Cobbler by default"
- **Authority:** advisory workflow only; Git/PR/merge/run memory remain host-owned

## Stop Gate

- **Planned batches remaining:** 2
- **Stop allowed right now:** no
- **Why:** implementation, proof, terminal review, push, and PR creation remain
- **Next required action:** complete B1 supervisor lifecycle, recovery, CLI, and host parity

## Current Phase

- **Status:** in progress
- **Active batch:** B1: Exact-session multi-phase supervisor and host parity
- **What was just finished:** B0 contracts, routing, preferences, capability truth, and focused proof
- **Single next action:** finish B1 lifecycle edge cases and commit the supervisor slice

## Active Compute

- No paid provider process, live canary, dev server, or remote job is active.

## Next Exact Batch

**Batch:** B1: Exact-session multi-phase supervisor and host parity

**Scope:**

- Complete reusable phase supervision, version-3 state, status history, and exact recovery.
- Prove Codex and Claude create/resume grammar, one packet, minimal continuation, and one follow stream.
- Preserve version-2 single-phase behavior and every existing environment/Git safety check.

**Acceptance criteria:**

- [ ] B1-A1: Fixture lifecycle traverses guide, transition, and execution with one packet/follow stream.
- [ ] B1-A2: Recovery and failure paths preserve state and forbid post-edit cold fallback.
- [ ] B1-A3: Codex and Claude exact resume pin distinct routes in the same CWD/session.
- [ ] B1-A4: Atomic completion is explicit and zero guide exit never implies normal completion.
- [ ] B1-A5: Existing version-2, redaction, environment, PID, Git, and no-push behavior remains green.

**Risk:** A phase transition bug could replay the packet, lose the session, or misreport completion.

**Rollback authority:** host-created B1 rollback ref at the acceptance-backed B0 Close tip.

## Effort Standard

- Work as hard as you can for the full run. Do not be lazy.
- Maintain the same effort on the last batch as on the first.
- Do not settle for the minimum acceptable change while deeper proof or planned work remains.
- When one task finishes, immediately take the next highest-value action from the plan.
- Do not broaden scope beyond the authoritative specification.

## Forbidden Stop Reasons

- A batch, progress commit, push, focused test, or documentation slice completed.
- A checkpoint was reached; checkpoints are not stop signals.
- A correctable focused test failed.
- The mechanism is implemented but documentation, parity proof, or full verification remains.
- The branch is pushed but the PR is not yet open and ready for review.

## Post-Checkpoint Control Loop

Every completed batch must end with a commit and push. After every host-owned commit and push,
re-read this survival guide before doing anything else. If the Stop Gate still say `Stop allowed right now: no`
or `.elves-session.json` has `continuation_guard.stop_allowed: false`,
execute the single next required action without waiting for acknowledgment. No delivery-time
checkpoint is configured; focused proof and progress commits are continuation points.

## After Any Compaction

Read the Run Control section and Stop Gate first, then `.elves-session.json` and its
`continuation_guard`, `docs/elves/learnings.md`,
`docs/plans/true-prewalk.md`, `docs/elves/execution-log-true-prewalk.md`, `.ai-docs/manifest.md`,
and any newly added constitution. Resume the single next action immediately.

## Launch Readiness

- [x] Full specification read before planning or code changes.
- [x] Stop Gate initialized with `Stop allowed right now: no`.
- [x] Latest `origin/main` fetched and exact starting tip recorded.
- [x] Dedicated requested branch/worktree created; forbidden prototype worktree untouched.
- [x] Run plan, survival guide, execution log, and session scaffold materialized.
- [x] Plan/session acceptance mappings synchronized and validated.
- [x] Baseline proof and preflight green; the release-only baseline exception is recorded.
- [x] Contract committed and pushed; survival guide re-read.
