# READ THIS FILE FIRST AFTER ANY COMPACTION OR RESTART

## Mission

Turn the unpublished explicit-handoff validator work into a backward-compatible v2.8 extension.
Preserve advisory behavior unless a session opts into handoff v1, then validate its state strictly
for both Markdown and JSON packets. Finish at a landable PR; do not merge.

## Run Control

- **Run mode:** finite
- **Stop policy:** blocker-only until a landable PR exists
- **User intent:** “I put you on a worktree where we have some (maybe out of date) work. Can you clean this all up?”
- **Checkpoint due by:** none
- **Checkpoint semantics:** none
- **May continue after checkpoint:** yes
- **Actual stop conditions:** a clean landable PR or a genuine blocker requiring new user authority
- **Workspace ownership:** dedicated worktree `/Users/john/aigora/dev/elves-explicit-prewalk-handoff`
- **Branch tip at start:** `6dff5957d2781a2274a7f8300879785636fdf3b6`
- **Merge policy:** user-merges; no merge authorization granted
- **Final-response policy:** disallowed until the PR is landable or a genuine blocker exists
- **Coordination mode:** Cobbler-first; host-native direct implementation because subagent delegation is unavailable for this run
- **Batch completion rule:** update run memory, commit/push meaningful slices, and close only with acceptance proof
- **Progress visibility rule:** concrete `[codex/explicit-prewalk-handoff · Batch N/total · Phase] outcome` subjects
- **Coordinator-to-implementer handoff:** not applicable; this run is host-native
- **Worker packet:** n/a — host-native
- **Re-read rule:** immediately re-read this guide after every host-owned commit and push
- **E2E mode:** chat-to-work
- **Work driver:** host-native
- **Implementation lane:** fast
- **Delegation scope:** none
- **Git mode:** host_only
- **Driver monitor mode:** interactive
- **Driver update policy:** direct host-native progress updates; no parked worker stream
- **Driver review policy:** final cumulative review
- **Risk posture:** standard
- **Trust mode:** trusted
- **Landing outcome:** landable_pr
- **Driver merge authorized:** no
- **Worker merge authority:** false
- **Staging acceptance validation:** PASS — Contract commit `629a1d8` pushed
- **High-risk checkpoints:** none
- **Re-drive budget:** n/a — host-native
- **Continuation harness:** host-native
- **Continuation rule:** continue without waiting while the Stop Gate is closed
- **Checkpoint rule:** no checkpoint is configured; if one is later added as delivery-only, log it and continue

## Stop Gate

- **Planned batches remaining:** 1
- **Stop allowed right now:** no
- **Why:** validator reconciliation, docs, broad proof, PR creation, and cleanup remain
- **Next required action:** commit and push the canonical docs/consistency slice, re-read this guide, then run broad verification and cumulative review

## Effort Standard

Do not be lazy. Work as hard as you can through the final cleanup tip; do not settle for the
minimum acceptable change. After each proof or review step, take the next highest-value action.
Prefer exact compatibility and category proof over a smaller speculative patch.

## Forbidden Stop Reasons

- A focused test passing while broader compatibility or docs remain unresolved.
- A commit or push completing while the Stop Gate remains closed.
- Reaching a checkpoint while planned work and the Stop Gate remain open.
- A PR existing before exact-tip checks and feedback are clean.
- The original unpublished implementation appearing plausible without cumulative review.

## Current Phase

- **Status:** documentation and consistency slice ready
- **Active batch:** B0
- **What was just finished:** canonical schema/workflow/changelog/durable docs plus 129 focused tests
- **Single next action:** commit and push the docs/consistency slice

## Active Compute

None. All work is host-native in this checkout; no worker, provider, monitor, or background test is
running.

## Next Exact Batch

- **Batch:** B0 — reconcile and harden explicit handoff v1
- **Scope:** backward-compatible explicit handoff state validation, packet-format parity, docs, and proof
- **Acceptance criteria:** B0-A1 through B0-A5 in the plan and session
- **Risk:** standard; staging safety behavior must not regress v2.8 compatibility

## Post-Checkpoint Control Loop

There is no checkpoint stop. Every completed batch must end with a commit and push. After each
host-owned commit and push, re-read this survival guide before doing anything else. If the Stop Gate still say `Stop allowed right now: no`, run the next required validation/review action and continue
until the branch is a clean landable PR.

## After Any Compaction

Read the Run Control section and Stop Gate in this guide first, then `.elves-session.json` and its
`continuation_guard`, learnings, the plan, the execution log, and `.ai-docs/manifest.md`. Resume the
single `Next Exact Action` without restaging completed work.

## Launch Readiness

- [x] Stop Gate initialized with `Stop allowed right now: no`.
- [x] Plan/session stable acceptance identity validates.
- [x] Worktree ownership, branch, start tip, and merge policy are recorded.
- [x] Baseline focused tests and evidence-selected verification are green.
- [x] Contract commit pushed and guide re-read.

## Cobbler Session State

- **Cobbler default:** on
- **Activated by:** Elves invocation
- **Scope:** current Elves run
- **Behavior:** use direct host-native execution while preserving Cobbler risk/contract synthesis
- **Persistence:** this guide and `.elves-session.json`
- **Exit phrases:** “Cobbler Mode: off”, “leave Cobbler Mode”, “stop using Cobbler by default”

## Current State

- Contract commit `629a1d8` is pushed on the branch.
- Implementation commit `2a38193` is pushed on the branch.
- The validator now preserves undeclared-session advisory behavior and strictly validates declared
  handoff v1 state for Markdown and JSON packets.
- Canonical docs, changelog, durable guidance, and consistency pins are aligned; 129 focused tests
  and the consistency checker pass. Broad proof and cumulative review remain.

## Next Exact Action

Commit and push the canonical docs/consistency slice, re-read this guide, then run broad terminal
verification and review the cumulative `origin/main...HEAD` diff.

## Recovery Order

1. This guide (Run Control and Stop Gate first)
2. `.elves-session.json`
3. `docs/elves/learnings.md`
4. `docs/plans/explicit-handoff-contract-cleanup.md`
5. `docs/elves/execution-log-explicit-handoff-cleanup.md`
6. `.ai-docs/manifest.md`
