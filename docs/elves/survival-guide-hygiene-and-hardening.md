# READ THIS FILE FIRST AFTER ANY COMPACTION OR RESTART

Run: hygiene-and-hardening (2026-07-16). This file is the run's memory. Trust it over chat recall.

## Mission

Execute `docs/plans/hygiene-and-hardening.md` end to end: fix the redaction exact-value bug (B1),
add the worktree gc lifecycle (B2), make the worker packet a staging deliverable (B3), consolidate
security-critical duplicate helpers (B4), restructure README + glossary (B5), shrink the
consistency engine (B6), and extract phase 1 of the full_run.py split (B7). Then cumulative
review, readiness, and a user-authorized merge.

## Run Control

- **Run mode:** finite
- **Run id:** hygiene-and-hardening-2026-07-16
- **Branch:** elves/hygiene-and-hardening
- **Worktree (this checkout):** /Users/john/aigora/dev/elves-hygiene-and-hardening
- **Main checkout (forbidden surface):** /Users/john/aigora/dev/elves
- **Base:** origin/main
- **START_TIP / collision tripwire:** d9862a46fbbcf59759d9b7bb9230494db88d5dec
- **PR:** #78 (record on open; update if it changes)
- **Work driver:** host-native (subscription-native Claude worker sessions — one separate worker
  session per batch, each launched with a per-batch packet derived from the consolidated packet)
- **Worker packet (consolidated, the prewalk):** `.elves/runtime/worker-packet-hygiene-and-hardening.md`
  (gitignored operational artifact; per-batch packets are derived from it verbatim)
- **Provider routes:** native only. No Grok/OpenRouter/Devin configured or probed for this run.
- **Merge policy:** chat-to-land. The user explicitly authorized landing this PR in the current
  session ("stage it as an /elves run … review when its done and land the pr", 2026-07-16).
  `driver_authorized=true` by that authorization; merge still requires independent readiness at the
  exact final HEAD. Regular merge commit only, never squash.
- **Batch order:** B1 → B2 → B3 → B4 → B5 → B6 → B7 (B5 after B3; B6 after B5; B7 after B4)
- **Version decision:** stay at 2.6.0 through batches; at terminal readiness run
  `scripts/release_checklist.py` and promote to v2.7.0 in the final Close if the checklist
  requires promotion of new user-facing behavior (expected: yes — gc helper + packet staging).
- **Checkpoint semantics:** no timed checkpoints; wake points are worker completion/failure only.
- **May continue after checkpoint:** yes (finite completion is the only self-stop).

## Cobbler Session State

- `cobbler.default_for_session: true` (recorded in `.elves-session.json`)
- Coordination is native-subagent-first; independent review lenses at terminal review; no
  provider-backed council. Writes, git, PR, and durable memory stay with the driver.

## Stop Gate

- Stop allowed right now: **no**
- Allowed stop conditions: (1) Final Completion after landing, (2) hard blocker after documented
  recovery attempts, (3) explicit user stop.
- `continuation_guard.stop_allowed: false` until readiness + merge or blocker.

## Effort Standard

Full effort every batch; the last batch gets the same rigor as the first. Prefer deeper verified
progress over minimum acceptable change.

## Forbidden Stop Reasons

Long output, many batches remaining, context pressure (re-read this guide and continue), waiting
on nothing, "checkpoint reached", partial green.

## Memory Surfaces

1. This survival guide (Stop Gate + Run Control first)
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

- [x] Plan committed at docs/plans/hygiene-and-hardening.md
- [x] Session seeded + sync-session --write + validate OK
- [x] Dedicated worktree created; tripwire recorded
- [x] Consolidated worker packet written (prewalk) and path recorded in session + here
- [x] Rollback ref b0 created (refs/elves/rollback/hygiene-and-hardening-2026-07-16/s1/b0)
- [x] Initial commit pushed; PR opened and recorded
- [x] Full preflight green in this worktree

## Current Phase

executing (B1 next)

## Active Compute

None (no paid/external compute; native subagents only).

## Next Exact Batch

B1 — Redaction exact-value minimum-length guard. Launch a native worker session with the B1
packet section; owned surfaces `scripts/cobbler_runtime/context.py`,
`scripts/cobbler_runtime/implement.py`, `scripts/cobbler_agents.py`, matching tests. Acceptance
B1-A1..A3 in the plan; evidence recorded to `.elves-session.json` at Close.

## Post-Checkpoint Control Loop

After each worker completes: verify gates on the tip (focused tests + targeted checks), reconcile
commits (subjects follow the schema), record acceptance evidence in session JSON, update this
guide's Current Phase / Next Exact Batch, push, re-read this guide, launch next batch. No user
acknowledgment awaited.

## Documentation Triggers

- B2/B3 touch SKILL.md/templates → keep consistency pins updated in the same commit
- Any helper move → update `.ai-docs/context-index.md`
- Every batch Close → CHANGELOG entry under the pending release heading

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

1. Re-read this guide top to bottom (Stop Gate + Run Control first).
2. Read `.elves-session.json` — batch statuses and `continuation_guard` are authoritative.
3. Read `docs/elves/learnings.md`, the plan, then the execution log tail.
4. Confirm the branch tip is an ancestor chain from START_TIP (collision check); if the tip moved
   outside this run's pushes, Hard Stop and report.
5. Resume the single next required action from `## Next Exact Batch` immediately. Do not wait for
   user acknowledgment.

## Tool Configuration

```yaml
test: python3 -m unittest discover -s tests
test-env-sensitivity: CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH=1 python3 -m unittest discover -s tests
verify: python3 scripts/verify_repo.py --version 2.6.0
verify-ci: python3 scripts/verify_repo.py --ci --version <ver> --base-ref origin/main
lint: none configured
typecheck: none configured
build: none
```
