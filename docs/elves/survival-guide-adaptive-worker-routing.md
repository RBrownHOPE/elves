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
- **Progress visibility rule:** worker commits use concrete Elves subjects; raw supervised worker output is the user's live window
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
- **Driver update policy:** default raw/sanitized follow stream; no timed driver chat; material wakes only
- **Driver poll policy:** supervised host wait primitive, bounded to maintain visibility
- **Driver review policy:** final independent cumulative review only
- **Follow mode:** default worker stream
- **Risk posture:** high
- **Trust mode:** trusted
- **Landing outcome:** landable_pr
- **Driver merge authorized:** no
- **Worker merge authority:** false
- **High-risk checkpoints:** wake only for protected-ref/authority violation, repeated crash/stall, collision, or material scope departure
- **Re-drive budget:** one targeted re-drive before driver-owned repair
- **Continuation harness:** supervised exact Codex worker session and process; never ambiguous `--last`
- **Staging acceptance validation:** pending until contract commit validation
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

- **Planned batches remaining:** 3
- **Stop allowed right now:** no
- **Why:** contract staging, full-run implementation, cumulative review, and reviewed PR remain
- **Next required action:** validate and commit the run contract, then launch the exact medium-effort Sol worker

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

- **Status:** staging
- **Active batch:** contract for B0–B2
- **What was just finished:** design research and branch isolation
- **Single next action:** validate and commit the authoritative plan/session/run docs

## Active Compute

- No worker process is active yet.
- Planned worker: exact model `gpt-5.6-sol`, reasoning `medium`, supervised `codex exec` session.
- Host behavior after launch: park; surface the stream; inspect only on material wake or terminal exit.

## Next Exact Batch

- **Batch:** B0 — Safe preferences and routing policy
- **Scope:** XDG global preferences, safe precedence/provenance, deterministic routing decisions
- **Acceptance criteria:** B0-A1 through B0-A4 in `docs/plans/adaptive-worker-routing.md`
- **Risk:** high — preference must never become authority

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
- [ ] Plan/session/packet acceptance mappings validated.
- [ ] Contract committed and pushed; survival guide re-read.
- [ ] Exact worker session registered and supervised.
