# READ THIS FILE FIRST AFTER ANY COMPACTION OR RESTART

Run: hygiene-and-hardening (2026-07-16). This file is the run's memory. Trust it over chat recall.

## Mission

Execute `docs/plans/hygiene-and-hardening.md` end to end: fix the redaction exact-value bug (B1),
add the worktree gc lifecycle (B2), make the worker packet a staging deliverable (B3), consolidate
security-critical duplicate helpers (B4), restructure README + glossary (B5), shrink the
consistency engine (B6), extract phase 1 of the full_run.py split (B7), and codify the worker
failure recovery policy (B8). Then cumulative
review, readiness, and a user-authorized merge.

## Run Control

- **Run mode:** finite
- **Stop policy:** blocker-only (plus Final Completion after authorized landing)
- **User intent:** "stage it as an /elves run, including doing the prewalk, then kick off the
  worker agent to do the work, then review when its done and land the pr" (2026-07-16)
- **Checkpoint due by:** none
- **Checkpoint semantics:** none
- **May continue after checkpoint:** yes
- **Actual stop conditions:** PR #78 merged after clean readiness at exact HEAD; or a hard blocker
  after documented recovery attempts; or explicit user stop.
- **Workspace ownership:** dedicated worktree created with `./scripts/preflight.sh
  --create-worktree elves/hygiene-and-hardening --base origin/main` at
  `/Users/john/aigora/dev/elves-hygiene-and-hardening` — never shared with another active agent;
  the main checkout `/Users/john/aigora/dev/elves` is a forbidden surface for this run.
- **Branch tip at start (collision tripwire):** `d9862a46fbbcf59759d9b7bb9230494db88d5dec`; the tip
  advances only through this run's own host/worker commits on `elves/hygiene-and-hardening`; every
  other move is a collision → Hard Stop.
- **Merge policy:** The user owns whether Elves may merge: reviewed-pr-landing-command — the user's
  kickoff message explicitly authorizes landing PR #78 in this session (chat-to-land). Regular
  merge commit after the final readiness review passes, never squash.
- **Final-response policy:** disallowed until stop conditions are met
- **Coordination mode:** Cobbler-first (native-subagent lenses at review; direct execution for
  mechanical steps)
- **Batch completion rule:** Host-native and legacy bounded batches end with `update execution log
  -> update survival guide -> commit -> push`. Worker sessions close their batch with acceptance
  evidence plus meaningful feature-branch commits/pushes; the host verifies and records canonical
  memory after each worker completes. Every completed batch must end with a commit and push.
- **Progress visibility rule:** meaningful slices pushed with
  `[elves/hygiene-and-hardening · Batch N/7 · Contract|Implement|Validate|Review|Close] <concrete outcome>`;
  vague subjects forbidden; `Close` requires acceptance evidence; protected refs, PR operations,
  and merge stay host-owned (Git/PR ops never dispatch model inference).
- **Coordinator-to-implementer handoff:** Every worker packet carries intent/why, non-obvious
  rationale, Build On targets, owned surfaces, forbidden surfaces, acceptance evidence, failure
  modes/pitfalls, and HEAD/run-doc paths/route-session identity/output format. Incomplete handoffs
  are blocking coordinator defects. Consolidated packet (the prewalk):
  `.elves/runtime/worker-packet-hygiene-and-hardening.md`; recorded in `.elves-session.json` as
  `worker_packet_path`.
- **Re-read rule:** Immediately after every host-owned commit and push, re-read this survival
  guide before doing anything else.
- **Checkpoint rule:** no checkpoints scheduled; if one appears, log it, push, continue
  immediately.
- **E2E mode:** chat-to-land
- **Work driver:** host-native (from B4 close: ALL remaining batches driver-implemented — user directive 2026-07-16; worker sessions retired for this run)
- **Implementation lane:** fast
- **Delegation scope:** batch
- **Git mode:** branch_progress
- **Driver monitor mode:** interactive
- **Driver update policy:** material wakes only; no timed driver chat
- **Driver poll policy:** host wait primitive
- **Driver review policy:** final independent review only
- **Follow mode:** n/a (native worker sessions report on completion)
- **Risk posture:** standard (B4/B7 individually high — extra characterization tests there)
- **Trust mode:** trusted
- **Landing outcome:** complete_and_merge
- **Driver merge authorized:** yes via land-pr (user kickoff message, this session)
- **Worker merge authority:** false
- **Stable plan IDs:** batches `B1`–`B7`; batch acceptance `B#-A#`; Master Acceptance `M-A1`–`M-A5`
- **Acceptance row syntax:** bare and bracketed forms equivalent; duplicate ids invalid
- **Batch helper syntax:** `--batch 1` and `--batch B1` equivalent
- **Staging acceptance validation:** PASS — plan parsed; session and packet id/text mappings match
- **Staging acceptance command:** `python3 scripts/acceptance_contract.py validate --repo-root .
  --session .elves-session.json`
- **High-risk checkpoints:** after B4 (full suite + env-sensitivity run before B5); after B7
  (characterization + full suite before terminal review)
- **GitHub push auth route:** host `gh` projection
- **Re-drive budget:** 2 substantive worker re-drives per batch, then host-native takeover with a
  Decisions entry; transient provider errors (overload/rate-limit/network) retry with escalating
  backoff (5m → 10m → 20m) and never consume the budget (operational since B4; codified by B8)
- **Continuation harness:** host-native
- **Continuation rule:** If work remains and `Actual stop conditions` are not met, continue
  without waiting for user acknowledgment.
- **Version decision:** stay at 2.6.0 through batches; at terminal run
  `scripts/release_checklist.py` and promote to v2.7.0 in the final Close if required (expected:
  yes — new user-facing behavior in B2/B3/B5).

## Cobbler Session State

- **Cobbler default:** on
- **Activated by:** Elves invocation (this run's kickoff)
- **Scope:** current Elves run
- **Behavior:** treat follow-up prompts as Cobbler-mediated by default; direct execution for
  mechanical steps; independent lenses at terminal review
- **Persistence:** survival guide and `.elves-session.json` (`cobbler.default_for_session: true`)
- **Exit phrases:** "Cobbler Mode: off", "leave Cobbler Mode", "stop using Cobbler by default"

## Session Budget

- **Started:** 2026-07-16 13:10 local
- **User returns:** unspecified (user is present in chat; run proceeds unattended between wakes)
- **Checkpoint expectation:** landable, reviewed, merged PR #78 with Elves report path surfaced
- **Time budget:** unlimited (finite scope, no deadline given)
- **Average batch time so far:** ~65m (B4 ran long: 4 transient worker crashes, split, host-native finish)
- **Batches remaining:** 5 of 9 (B8, B9 added, user-directed; all remaining work driver host-native per user directive)

## Stop Gate

- **Planned batches remaining:** 5
- **Stop allowed right now:** no
- **Why:** B5–B9 unimplemented; landing not attempted; user asked for end-to-end delivery and landing.
- **Next required action:** implement B8+B9 host-native (driver), then B5, B6, B7.

## Effort Standard

- Work as hard as you can for the full run. Do not be lazy.
- Maintain the same level of effort on the last batch as on the first.
- Do not settle for the minimum acceptable change, the first green check, or a shallow pass when
  deeper verification or the next planned task remains.
- When one task is complete, immediately take the next highest-value action from the plan, review
  queue, or scout work.

## Forbidden Stop Reasons

- "Checkpoint reached" — checkpoints are wake points, never completion; continue after every
  checkpoint.
- "Committed and pushed" — commits and pushes are progress, not permission to stop; re-read this
  guide and continue.
- Long output, many batches remaining, context pressure (re-read this guide and continue),
  waiting on nothing, partial green.

## Memory Surfaces

1. This survival guide (Run Control + Stop Gate first)
2. `.elves-session.json` (acceptance evidence per batch; trust after compaction)
3. `docs/elves/learnings.md` (shared, durable)
4. `docs/plans/hygiene-and-hardening.md` (authoritative scope + acceptance)
5. `docs/elves/execution-log-hygiene-and-hardening.md` (chronological proof)
6. `.ai-docs/context-index.md` (repo map)

## Strategic Forgetting

Rewrite live sections of this guide in place; append execution log; promote reusable lessons to
learnings at Close of each batch; archive nothing mid-run.

## Non-Negotiables

- Never weaken pattern-based redaction; min-length guard applies only to exact-value matching
  (mirrors `_secret_env_values` len>=8 precedent).
- Worktree gc: never `--force`; never deletes unregistered directories; never touches dirty or
  unmerged worktrees; report mode never mutates.
- Never weaken, delete, or skip a test merely to obtain green.
- Repo-consistency CI + `verify_repo.py` strict checks green at every batch Close.
- User owns merge; this run carries explicit chat-to-land authorization (see Run Control); merge
  only at exact readiness HEAD with a regular merge commit.
- Forbidden commands per SKILL.md (no reset --hard, no checkout ., no clean -fd, no force push, no
  rebase on shared branch, no rm -rf outside scope, never operate on the main checkout at
  /Users/john/aigora/dev/elves).

## Launch Readiness

- [x] Plan cleaned and saved to disk (`docs/plans/hygiene-and-hardening.md`)
- [x] Survival guide updated from the current plan
- [x] Learnings file initialized or refreshed (shared `docs/elves/learnings.md` present)
- [x] Execution log initialized with batch breakdown and preflight notes
- [x] Branch created or confirmed (`elves/hygiene-and-hardening`)
- [x] Branch and checkout ownership confirmed (dedicated worktree if other agents may touch the repo); no other agent shares this branch
- [x] PR opened or existing PR recorded (#78)
- [x] Preflight run and critical failures cleared (3 advisories, no criticals)
- [x] Run mode, return time, and non-negotiables recorded
- [x] Stop Gate initialized with `Stop allowed right now: no` unless a real stop condition already applies
- [x] Single-kickoff continues after staging (legacy two-call only if explicit); launch prompt only for legacy path

## Current Phase

**Status:** In progress

**Active batch:** Batch 8 + Batch 9 (driver host-native)

**What was just finished:** B4 complete at 816ba3b (worker stopped by user after 4th silent
transient death; driver finished host-native): one hardened run_git (stdin closed, timeout param)
serving all of full_run's 16 former raw sites; single session basename; two real import cycles
documented; suite 1,062/0 both shapes; verify --ci zero FAILs.

**Single next action:** Implement B8 (failure recovery policy docs), then B9 (hermetic bounded
gates), both driver host-native per user directive; then B5-B7.

## Active Compute

No active paid or long-running compute.

## Next Exact Batch

**Batch:** B4: Consolidate security-critical duplicate helpers

**Scope:**
- One canonical hardened `run_git` (leases.py variant) exported once; delegated_git/audit delegate;
  full_run.py's 16 raw git subprocess calls + `_git_head`/`_git_common_dir` twins migrate with
  per-call env/cwd preserved (bytes-mode snapshot reader may stay, documented)
- Delete weak `context.ensure_private_dir`; callers on `storage.ensure_private_dir`; api-break
  approval entry for the removal (and refresh/remove the stale
  `cli:cobbler_agents implement full-run-prepare` entry surfaced by B1)
- `.elves-session.json` filename as one shared constant across the six modules
- Remove function-local imports guarding non-existent cycles (~13 sites) unless a test proves need

**Acceptance criteria:**
- [ ] [B4-A1] Exactly one run_git definition body remains (delegators allowed); zero raw
  subprocess git call sites left in full_run.py
- [ ] [B4-A2] context.ensure_private_dir gone; api-break-approvals.json entry passes strict
  verify_repo --ci validation
- [ ] [B4-A3] Full suite green; test_storage_isolation_git, test_dispatch_isolation,
  test_full_run_supervisor specifically green (regression preservation)
- [ ] [B4-A4] Session filename literal appears exactly once in cobbler_runtime/

**Risk:** high — wide mechanical change through the largest module's git plumbing; env choice per
call site must be preserved and reviewed.

**Rollback authority:** host-created `b0` ref
(`refs/elves/rollback/hygiene-and-hardening-2026-07-16/s1/b0`) plus per-batch worker commit SHAs.

## Post-Checkpoint Control Loop

Every completed batch must end with a commit and push, followed by a re-read of this guide. A
pushed checkpoint is proof of progress, not permission to stop.

After every host-owned commit and push — or after a worker session completes — answer these before
doing anything else:

1. What unfinished batch or task am I starting right now?
2. What paid compute or long-running resources are active right now?
3. What is each active resource doing? If any resource is idle, stale, or ambiguous, shut it down
   or pause it now.
4. Did the user change stop behavior, checkpoint meaning, priorities, or scope since the survival
   guide was last rewritten? If yes, rewrite `## Run Control`, `## Current Phase`, `## Stop Gate`,
   and `## Next Exact Batch` now.
5. Does the Stop Gate still say `Stop allowed right now: no`, or does `.elves-session.json` still
   say `continuation_guard.stop_allowed: false`? If yes, continue immediately.
6. Am I allowed to stop? If the answer is anything other than a clear hard stop, explicit user
   stop, or true blocker, continue immediately.

Then: re-read this survival guide before doing anything else.

## Documentation Triggers

- **Behavior changed:** update README/config docs/CHANGELOG in the same batch (B2/B3/B5
  especially); keep consistency pins updated in the same commit.
- **Architecture shifted:** update `.ai-docs/context-index.md` (B4/B7 helper moves).
- **New repeatable pattern or policy:** update `.ai-docs/conventions.md` if present.
- **New trap or hidden dependency:** update `.ai-docs/gotchas.md` if present.
- **Reusable lesson from the run:** update `docs/elves/learnings.md` at each Close.
- If none apply, record that no durable doc updates were needed.

## Process Tuning Triggers

If a worker session dies twice on the same batch, split the batch or take it host-native in this
session and document the decision. 5+ fruitless edits → stop and reframe.

## Memory and Resource Hygiene

Between batches: prune stale scratch files under `.elves/runtime/` created by this run's workers;
keep gate/done reports. Post-merge: dogfood the new gc helper on this run's own worktree.

## Elves Report

At terminal: `/tmp/elves-report-elves-2026-07-16.html` (problems found, lessons, batch timeline,
verification proof, residual risks, next steps). Surface the path in the final notification.

## Acceptance Checks

Authoritative rows live in the plan; evidence in `.elves-session.json`. Test baseline at staging:
1,027 tests, 1 known failure (`test_cli_gate_failure_exit_code` — the B1 target, fails only with
Claude Code env flags exported), 12 skips, ~3.3 min. B1 must flip that to 0 failures.

## Evidence / SCRATCH Layout

- Worker gate/done reports: `.elves/runtime/implement/…` (ignored)
- Driver scratch: `/private/tmp/claude-501/-Users-john-aigora-dev-elves/48e943fc-562d-44ac-81a8-606b1b7ca67e/scratchpad/`
- Dogfood transcripts for B2: paste into execution log + session evidence

## After Any Compaction

When you restart after a compaction, do these steps in order. No shortcuts.

1. Read this file (survival guide). You are doing this now.
2. **Read the Run Control section and Stop Gate above.** Confirm the run mode, stop policy,
   checkpoint semantics, actual stop conditions, and whether stopping is currently allowed.
3. Read `.elves-session.json`. Confirm current batch, PR number, test baseline, and
   `continuation_guard`.
4. Read `docs/elves/learnings.md`.
5. Read the plan. Confirm scope unchanged.
6. Read the execution log. Find the last completed batch and the last **Decisions made** entry.
7. Read `.ai-docs/context-index.md` if present.
8. Read the Active Compute section. Know what live resources exist.
9. Read the `continuation_guard`. If `stop_allowed` is `false`, continue without re-deciding
   whether the run should end.
10. Identify the first incomplete batch or the single next action (Current Phase, Stop Gate,
    Next Exact Batch).
11. Check the clock against Session Budget.
12. Resume immediately. Don't ask for help. Don't redo completed work.

The execution log is proof of what is done. If something appears there as complete, it is
complete. Don't re-implement it.

## Tool Configuration

```yaml
test: python3 -m unittest discover -s tests -t .
test-env-sensitivity: CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH=1 python3 -m unittest discover -s tests -t .
verify: python3 scripts/verify_repo.py --version 2.6.0
verify-ci: python3 scripts/verify_repo.py --ci --version <ver> --base-ref origin/main
consistency: python3 scripts/check_repo_consistency.py
lint: none configured
typecheck: none configured
build: none
```
