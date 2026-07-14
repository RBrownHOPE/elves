# Elves Run Brief: Joyful Runs Rewrite

## Mission

Implement `docs/plans/v2.3.0-joyful-runs-rewrite.md` as Elves 2.3.0. Rewrite the prose/control plane
fresh, preserve the proven safety runtime, and make the trusted-worker path fast, visible, and
pleasant without weakening final quality.

## Run Control

- Run mode: finite
- Stop policy: completion, explicit user stop, or genuine blocker; resumed by user on 2026-07-14
- User intent: stage and execute this as an Elves run using the new faster principles
- Checkpoint due by: none
- Checkpoint semantics: none
- May continue after checkpoint: yes
- Actual stop conditions: final readiness, explicit user stop, or genuine blocker
- Workspace ownership: dedicated branch and worktree recorded below
- Branch tip at start: `6ec138f1c22a5d9c309a2d3bdcf42c07691a018f`
- Merge policy: user-merges; no driver authorization recorded
- Final-response policy: disallowed while final revision/readiness remains
- E2E mode: chat-to-work
- Work driver: Grok Build
- Implementation lane: fast
- Trust mode: trusted
- Delegation scope: full_run
- Git mode: branch_progress
- Driver monitor mode: parked_monitor
- Driver update policy: no timed chat updates; bounded worker events only
- Driver review policy: one independent cumulative terminal review
- High-risk checkpoints: none planned; deterministic safety wakes remain active
- Continuation harness: Grok full-run session with goal-compatible one-packet fallback
- Driver posture: parked_monitor; no routine mid-run review or timed chat updates
- Driver wake conditions: worker exit, stale heartbeat, failure/blocker, safety tripwire, material
  scope/assumption change, or terminal completion
- Landing outcome: landable_pr
- Driver merge authorized: no
- Worker merge authority: false
- Branch: `codex/joyful-elves-runs`
- Dedicated worktree: `/Users/john/aigora/dev/elves-joyful-elves-runs`
- Start HEAD: `6ec138f1c22a5d9c309a2d3bdcf42c07691a018f`
- Merge method if later authorized: regular merge commit only
- Stop conditions: all plan acceptance is complete and final readiness is clean, explicit user
  stop, or a genuine blocker without a safe recovery
- Re-drive budget: 3, preserving verified pushed progress
- Coordination: Cobbler-first for material planning and final synthesis; no model monitoring
- Validation: affected tests during implementation; one broad local suite at final unchanged tip
- Batch completion rule: the worker commits and pushes meaningful acceptance-backed progress; the
  parked driver does not shadow batches
- Re-read rule: the driver re-reads this brief once at a safety or terminal wake
- Checkpoint rule: checkpoints are not completion and do not stop healthy execution
- Continuation rule: continue through final readiness; do not merge without new authorization

## Cobbler Session State

- Cobbler default: on
- Activated by: Elves invocation
- Scope: this Elves run
- Behavior: planning and final synthesis; no routine monitoring inference
- Persistence: survival guide and `.elves-session.json`
- Exit phrases: Cobbler Mode off; leave Cobbler Mode; stop using Cobbler by default

## Stop Gate

- Planned batches remaining: final revision/readiness only
- Stop allowed right now: no
- Why: the user explicitly resumed and final revision/readiness remains
- Next required action: when resumed, finish the consolidated follow/sentinel fix, run delta review,
  then the one final broad gate and leave PR #70 landable without merging

## Effort Standard

- Work as hard as you can while favoring implementation progress over ceremony.
- Do not be lazy or stop at convenient intermediate milestones.
- Do not settle for the minimum acceptable change when acceptance still requires substantive work.
- After each completed item, take the next highest-value action from the plan.
- Use focused proof while work changes and one strong cumulative review at terminal.
- Do not stop at batch boundaries or wait for routine acknowledgement.

## Forbidden Stop Reasons

- A worker commit, push, batch boundary, green focused test, or quiet user is not completion.
- A checkpoint, PR creation, or passing CI is not completion while acceptance remains open.
- Routine uncertainty is resolved with judgment; only genuine blockers or safety conflicts wake or
  stop the run.

## Current Phase

- Status: resumed consolidated final revision
- Active batch: B5 review/revision
- What was just finished: Grok implementation, cumulative review, landing integration fix, and
  most consolidated documentation/authority fixes; follow/sentinel revision was interrupted
- Single next action: resume the follow/sentinel fix, then delta-only review and final proof

## Active Compute

- One targeted local follow/sentinel fix agent; PR #70 remains open and unmerged.

## Next Exact Batch

- Batch: B0
- Scope: canonical contract and migration ledger, followed autonomously by B1-B5
- Acceptance criteria: B0-A1 through B0-A4 in the authoritative plan
- Risk: high omission risk; preserve the safety kernel before deleting old prose

## Post-Checkpoint Control Loop

- There is no planned human checkpoint. On a deterministic safety or terminal wake, re-read run
  memory, reconcile once, and either recover/re-park or enter cumulative readiness.
- Every completed batch must end with a commit and push by the trusted worker.
- At a host wake, re-read this survival guide before doing anything else.
- If the Stop Gate still say `Stop allowed right now: no`, continue or recover immediately.

## After Any Compaction

- Read this brief, `.elves-session.json`, the plan, then the execution log. Preserve the exact
  worker session, branch tip, acceptance map, landing outcome, and next action.
- Read the Run Control section and Stop Gate before acting; trust `continuation_guard` over memory.

## Launch Readiness

- [x] Plan is self-contained and acceptance syntax is valid.
- [x] Session acceptance rows match the plan.
- [x] Dedicated branch and worktree are owned by this run.
- [x] Stop Gate initialized with `Stop allowed right now: no`.
- [x] Focused staging tests are green.
- [x] Staging commit pushed and PR recorded.
- [ ] Production full-run prepare and launch validation pass.

## Authority

- Worker may edit product code, tests, core docs, references, templates, and release metadata on the
  assigned feature branch and may commit/push meaningful progress.
- Worker may not edit this brief, the execution log, `.elves-session.json`, `.elves/runtime`,
  credentials, other worktrees, protected refs, PR state, or landing policy.
- Worker never merges, tags, opens another PR, or presents self-review as independent review.
- Driver owns canonical run memory, final review/revision, readiness, PR actions, and any later
  explicitly authorized merge.

## Worker Guidance

- Treat the plan as a contract, not choreography. Use judgment and extend existing modules.
- Work through all batches without waiting for host prompts.
- Commit and push independently reviewable progress with concrete subjects.
- Use focused proof while building; do not repeatedly run the entire suite.
- Be especially careful around authority, credentials, protected refs, telemetry redaction,
  exact-HEAD proof, and review invalidation.
- Report material assumption or scope changes through bounded events; ordinary implementation
  decisions do not require host approval.

## Current State

- Phase: staging
- Planned batches remaining: 6
- Stop allowed: no
- Next action: validate plan/session/packet identity, commit staging, push, open PR, and launch one
  trusted full-run worker
- Active compute: none

## Recovery Read Order

1. This brief
2. `.elves-session.json`
3. `docs/plans/v2.3.0-joyful-runs-rewrite.md`
4. `docs/elves/execution-log-joyful-runs.md`
5. `docs/elves/learnings.md`
