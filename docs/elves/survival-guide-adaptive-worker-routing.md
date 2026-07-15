# READ THIS FILE FIRST AFTER ANY COMPACTION OR RESTART

## Mission

Implement adaptive subscription-native worker routing, safe global preferences, optional Grok
selection, and a simpler user-first flow with Codex/Claude parity. The current Sol Ultra session
owns planning and final review; a separate same-model Sol worker at medium reasoning owns the full
implementation packet.

## Run Control

- **Run mode:** finite
- **Stop policy:** blocker-only until terminal reviewed PR
- **User intent:** “try using our native flow to do this work and see how it goes, including the plan and first step hand off to the less smart worker model”
- **Checkpoint due by:** none
- **Checkpoint semantics:** none
- **May continue after checkpoint:** yes
- **Actual stop conditions:** reviewed PR ready, explicit user stop, or a genuine safety/authority blocker
- **Workspace ownership:** branch `codex/adaptive-worker-routing` in `/Users/john/aigora/dev/elves`; one branch, one checkout
- **Branch tip at start:** `47190f24d237eea770c6210718f84fd0c686bb8c`
- **Merge policy:** user-merges; never merge this run
- **Final-response policy:** disallowed until the reviewed PR is ready or a genuine blocker exists
- **Coordination mode:** direct native worker trial with Elves contract
- **Batch completion rule:** the registered full-run worker commits and may push meaningful feature-branch slices; the parked host updates canonical run memory only at terminal or safety wake
- **Progress visibility rule:** worker commits use concrete Elves subjects; before parking, expose a
  capability-proven native agent view or the exact private `native-worker follow` command
- **Coordinator-to-implementer handoff:** one complete packet carries plan, rationale, owned/forbidden surfaces, acceptance, pitfalls, exact HEAD, session identity requirements, tests, and output format
- **Re-read rule:** immediately after every host-owned commit and push, re-read this survival guide before doing anything else; after worker terminal wake, re-read before cumulative review
- **Checkpoint rule:** no checkpoint is configured; ordinary commits and pushes are not stop signals
- **Continuation rule:** continue automatically while `continuation_guard.stop_allowed` is false
- **E2E mode:** chat-to-work
- **Work driver:** host-native Codex CLI worker
- **Implementation lane:** fast
- **Delegation scope:** full_run
- **Git mode:** branch_progress
- **Driver monitor mode:** parked_monitor
- **Driver update policy:** capability-bound native view or sanitized follow command; no timed driver chat; material wakes only
- **Driver poll policy:** supervised host wait primitive, bounded to maintain visibility
- **Driver review policy:** final independent cumulative review only
- **Follow mode:** proven native view or exact private follow log; otherwise report commit-only visibility
- **Risk posture:** high
- **Trust mode:** trusted
- **Landing outcome:** landable_pr
- **Driver merge authorized:** no
- **Worker merge authority:** false
- **High-risk checkpoints:** wake only for protected-ref/authority violation, repeated crash/stall, collision, or material scope departure
- **Re-drive budget:** one targeted re-drive before driver-owned repair
- **Continuation harness:** supervised exact Codex worker session and process; never ambiguous `--last`
- **Staging acceptance validation:** complete
- **Staging acceptance command:** `python3 scripts/acceptance_contract.py validate --repo-root . --session .elves-session.json`
- **Terminal landing command:** `python3 scripts/elves_landing_check.py --session .elves-session.json --repo-root .`

## Cobbler Session State

- **Cobbler default:** available but not invoked for routine implementation
- **Activated by:** explicit user request or a terminal reasoning need
- **Scope:** planning/review support only
- **Behavior:** advisory; never owns git, PR, merge, or canonical run memory
- **Persistence:** none for this worker trial
- **Exit phrases:** “Cobbler Mode: off” or natural equivalent

## Stop Gate

- **Planned batches remaining:** 0
- **Stop allowed right now:** yes
- **Why:** all plan acceptance, targeted revision, terminal review, and reviewed PR presentation are complete
- **Next required action:** none; wait for the user to decide whether and when to merge

## Effort Standard

Work as hard as you can for the full run. Do not be lazy, do not stop at the
minimum acceptable change, and take the next highest-value action until acceptance and terminal
review are complete.
This does not authorize broad speculative work or repetitive verification.

## Forbidden Stop Reasons

- A contract, implementation, or validation commit/push completed.
- A delivery checkpoint was reached or no checkpoint exists.
- The worker is making ordinary progress without needing driver commentary.
- One focused test failed and the failure can be corrected within scope.
- The worker omitted an Elves report; the host can reconstruct it from commits and evidence.

## Current Phase

- **Status:** complete
- **Active batch:** none
- **What was just finished:** final acceptance reconciliation and exact-tip terminal review
- **Single next action:** none; merge remains unauthorized

## Active Compute

- No worker process is active.
- Primary worker session `019f674a-53e6-7f21-bbaa-43082fe59541` and revision session
  `019f6766-7d3a-7972-977e-8c82395e538a` completed.
- The trial exposed two adapter defects now fixed: hidden JSONL was not user-visible, and exact
  Codex resume lost the writable sandbox unless explicitly rebound.

## Next Exact Batch

- None. B0, B1, B2, and Master Acceptance are complete with evidence.

## Post-Checkpoint Control Loop

Every completed batch must end with a commit and push by the registered branch-progress worker.
After a host-owned commit or push, re-read this survival guide before doing anything else. If the
Stop Gate still say `Stop allowed right now: no`, continue with the single next required action.
During the parked full run, do not shadow-review each worker batch.

## After Any Compaction

Read the Run Control section and Stop Gate first, then `.elves-session.json`, learnings, plan,
execution log, `.ai-docs/manifest.md`, and constitution. Honor `continuation_guard`; do not infer a
new stop condition from lost chat context.

## Launch Readiness

- [x] Dedicated branch/checkout and collision tripwire recorded.
- [x] Stop Gate initialized with `Stop allowed right now: no`.
- [x] Plan/session/packet acceptance mappings validated.
- [x] Contract committed and pushed; survival guide re-read.
- [x] Exact worker session registered and supervised.
