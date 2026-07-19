# READ THIS FILE FIRST AFTER ANY COMPACTION OR RESTART

> Survival Guide for the issue-86 review-fixes run. After any compaction, read this before
> touching code. Read order: this file -> `.elves-session.json` -> `docs/elves/learnings.md` ->
> `docs/plans/v2.10.2-issue-86-review-fixes.md` ->
> `docs/elves/execution-log-issue-86-review-fixes.md` -> `.ai-docs/manifest.md` -> `TODO.md`.
> Helper commands `python3 scripts/...` are source-checkout shorthand; supervision helpers resolve
> from the active installed skill root (`~/.claude/skills/elves`) with this worktree as cwd.

---

## Mission

Fix issue #86 items 1–16 (confidence-review attribution bugs, effort explicitness, event salvage,
policy guards, supervisor hardening) and release v2.10.2, via a trusted separate
subscription-native Claude worker at `claude-fable-5` effort `low`, then land the reviewed PR with
a regular merge commit. Items 17–20 deferred with recorded rationale.

---

## Run Control

- **Run mode:** finite
- **Stop policy:** blocker-only until Final Completion
- **User intent:** "please review and plan an /elves run where you will hand off to fable low to
  do the work. then stage the run, do the prewalk, make the hand off, monitor the run, and land
  the pr. do everything you agree with" (2026-07-19, kickoff)
- **Checkpoint due by:** none
- **Checkpoint semantics:** none
- **May continue after checkpoint:** yes
- **Actual stop conditions:** Final Completion after merge + teardown, explicit user stop, or a
  true blocker
- **Workspace ownership:** dedicated worktree `/Users/john/aigora/dev/elves-issue-86-review-fixes`
  created with `./scripts/preflight.sh --create-worktree claude/issue-86-review-fixes --base
  origin/main`; never shared with another active agent
- **Branch tip at start (collision tripwire):** `0e58fde77baa9c5a256f081dde07d2ca2c10a72a`
  (START_TIP; the only expected advances are the exact registered trusted native worker session
  moving `claude/issue-86-review-fixes` to a descendant, and host staging/reconcile commits)
- **Merge policy:** merge-commit-on-green — explicit chat-to-land opt-in in the kickoff ("land
  the pr", current session, 2026-07-19). Regular merge commit only, never squash, only after
  Final Readiness passes.
- **Final-response policy:** disallowed until Stop Gate allows
- **Coordination mode:** Cobbler-first (default)
- **Batch completion rule:** trusted parked full-run — the worker closes internal batches with
  acceptance evidence plus meaningful feature-branch commits/pushes; the host updates canonical
  run memory once at terminal/safety wake.
- **Progress visibility rule:** worker commits use
  `[claude/issue-86-review-fixes · Batch N/6 · Contract|Implement|Validate|Review|Close]
  <concrete outcome>`; ≥1 pushed non-Close slice per batch before its single acceptance-backed
  Close; vague subjects forbidden; Close commits carry a `Confidence:` trailer (level plus
  `— unsure: <items>` when non-empty; an empty unsure list is a positive assertion).
- **Coordinator-to-implementer handoff:** consolidated packet is the staging deliverable (path
  below); incomplete handoff = blocking coordinator defect.
- **Worker packet:** `.elves/runtime/worker-packet-issue-86-review-fixes.md` (also
  `worker_packet_path` in `.elves-session.json`)
- **Handoff validation:** explicit-v1 declared in session + leading packet capsule (strict); the
  capsule is a cold-handoff contract, never prewalk evidence
- **Prewalk:** requested `auto`; **actual `off`** — read-only probe reports the Claude transport
  behaviorally unqualified (`prewalk_exact_resume_unqualified`, instruction fidelity
  `unsupported`, installed claude 2.1.207, evidence `installed_help_only`). Honest one-packet
  cold handoff; no post-edit cold fallback question arises because no prewalk activates.
- **Re-read rule:** after every host-owned commit and push, re-read this guide. During
  `parked_monitor`, worker pushes do not wake the host; re-read once on safety/blocked/terminal
  wake before cumulative review.

- **E2E mode:** chat-to-land
- **Work driver:** claude-native-worker (separate subscription-native Claude Code session; exact
  route `claude-fable-5` / effort `low`; same-model/lower-effort delegation default for a Fable 5
  driver)
- **Implementation lane:** fast
- **Delegation scope:** full_run
- **Git mode:** branch_progress (worker may commit/push only `claude/issue-86-review-fixes`)
- **Driver monitor mode:** parked_monitor
- **Driver update policy:** default sanitized follow stream; no timed driver chat; material wakes
  only
- **Driver poll policy:** host wait primitive; watchdog health check on silence (near-zero CPU
  against long wall time = hang signature); bounded 60–300s polls
- **Driver review policy:** final independent review only
- **Follow mode:** default sanitized stream
- **Risk posture:** standard
- **Trust mode:** trusted
- **Landing outcome:** complete_and_merge
- **Driver merge authorized:** yes — explicit user kickoff this session ("land the pr")
- **Worker merge authority:** false
- **Stable plan IDs:** batches `B1`–`B6`; criteria `B#-A#`; master `M-A#`
- **Staging acceptance validation:** run `acceptance_contract.py validate` before launch; PASS
  required (record result in execution log)
- **Staging acceptance command:** `python3 "$HOME/.claude/skills/elves/scripts/acceptance_contract.py"
  validate --repo-root . --session .elves-session.json`
- **High-risk checkpoints:** none
- **GitHub push auth route:** ambient same-user credentials (subscription-native worker is the
  user's own Claude Code session; no credential grant projection needed)
- **Re-drive budget:** 3 substantive re-drives; transient provider errors retry the same worker
  with 5m/10m/20m backoff and never consume the budget
- **Continuation harness:** host-native (Elves finite mode + Stop Gate)
- **Continuation rule:** if work remains and stop conditions are unmet, continue without waiting
  for acknowledgment.

---

## Cobbler Session State

- **Cobbler default:** on
- **Activated by:** Elves invocation (kickoff 2026-07-19)
- **Scope:** current Elves run
- **Behavior:** Cobbler-mediated for non-trivial planning/review decisions; direct execution for
  mechanical steps
- **Persistence:** survival guide and `.elves-session.json` (`cobbler.default_for_session: true`)

---

## Session Budget

- **Started:** 2026-07-19 ~14:00 UTC
- **User returns:** unknown (offline run; land when ready)
- **Checkpoint expectation:** landed PR + Elves report at return
- **Time budget:** ~unbounded within finite scope (6 worker batches + terminal review + landing)
- **Average batch time so far:** n/a (worker-internal)
- **Batches remaining:** 6 of 6 (worker-internal)

---

## Stop Gate

- **Planned batches remaining:** 6 (worker) + terminal review + landing ceremony
- **Stop allowed right now:** no
- **Why:** staging/launch/monitor/review/landing all outstanding
- **Next required action:** finish staging (session JSON + packet), validate, rollback ref, push,
  open PR, launch worker

---

## Effort Standard

- Work as hard as you can for the full run; same effort on the last batch as the first.
- Do not settle for minimum acceptable change; prefer deeper verified progress.

---

## Forbidden Stop Reasons

Checkpoint reached; commit/push succeeded; CI green; PR exists; user silent; summary written;
current batch complete while later batches remain; feeling unsure; "a lot for one turn"; "natural
place to pause". If one occurs: update docs, commit, push, re-read this file, continue.

---

## Memory Surfaces

- **Plan:** `docs/plans/v2.10.2-issue-86-review-fixes.md` (authoritative scope/acceptance)
- **Survival guide:** this file (live brief)
- **Learnings:** `docs/elves/learnings.md` (durable)
- **Execution log:** `docs/elves/execution-log-issue-86-review-fixes.md` (chronological proof)
- **Durable docs:** `.ai-docs/` (`manifest.md`, `context-index.md`)

Promotion: execution log -> learnings -> `.ai-docs/*`.

---

## Non-Negotiables

- Never weaken, skip, or delete a test merely to obtain green.
- Thin safety kernel untouched: worker merge authority false everywhere; shared-OAuth projection
  keeps worker free text hidden; redaction semantics unchanged.
- Do not begin the full_run.py monitor/await decomposition (issue item 17).
- No AI co-author trailers, "Generated with" lines, or any AI attribution in commits, PR text, or
  docs. (Confidence trailers are required and are not attribution.)
- Merge only as recorded in Run Control: regular merge commit after Final Readiness; never squash.
- Never run destructive git commands (`reset --hard`, `checkout .`, `clean -fd`, force push,
  rebase on shared branches). Stop on any unexpected tip move (collision) outside the registered
  worker exception.
- One run owns one branch and one checkout (`claude/issue-86-review-fixes` in the dedicated
  worktree). Do not operate in `/Users/john/aigora/dev/elves` (main checkout) for run work.
- Stage specific files; never `git add -A` blindly.

---

## Launch Readiness

- [x] Plan cleaned and saved to disk
- [x] Survival guide updated from the current plan
- [x] Learnings file present (`docs/elves/learnings.md`, shared durable file)
- [x] Execution log initialized with batch breakdown and preflight notes
- [x] Branch created (`claude/issue-86-review-fixes` @ START_TIP `0e58fde`)
- [x] Branch/checkout ownership confirmed (dedicated worktree; no other agent shares it)
- [x] PR opened and recorded (#87, draft until Final Readiness)
- [x] Preflight run and critical failures cleared (origin `aigorahub/elves`, gh auth
  `john-aigora`, session tracked, acceptance validate PASS pre- and post-commit)
- [x] Run mode, return time, and non-negotiables recorded
- [x] Stop Gate initialized (`Stop allowed right now: no`)
- [x] Single-kickoff E2E: continue into launch without a second human call

---

## Current Phase

**Status:** Launch-ready -> launching worker

**Active batch:** B1 (worker-owned; host parks after launch)

**What was just finished:** Staging complete — plan + run docs + session contract committed
(`0521668`) and pushed; PR #87 opened (draft); worker session UUID
`fff5a5a1-1ae6-43c5-b57c-c2508f31b3c4` minted; acceptance validate PASS.

**Single next action:** Mint b0 rollback ref at the staged tip, finalize the packet capsule at
that HEAD, re-validate, launch the `claude-fable-5`/low native worker
(`native-worker launch --host claude`), verify identity readiness, then park with watchdog.

---

## Active Compute

| Resource | Purpose | Current status | Last verified | Stop / repurpose trigger |
| --- | --- | --- | --- | --- |
| Native Claude worker session (planned) | Implement B1–B6 | not yet launched | 2026-07-19 | terminal wake, malformed completion, or user stop |

No other paid or long-running compute.

---

## Next Exact Batch

**Batch:** B1: Confidence attribution and projection unification (worker-owned; host parks)

**Scope:** see plan B1 tasks (normalizer-based attribution, shared projection helper, count
validation, overflow/truncation honesty, order-insensitive conflicts, regression tests)

**Acceptance criteria:** plan B1-A1 … B1-A6

**Risk:** standard — review-context builder feeds terminal review; silent misroute is the hazard

**Rollback authority:** host-created `b0` ref before handoff (`refs/elves/rollback/<run-id>/...`)
plus worker commit SHAs for internal rollback

---

## Post-Checkpoint Control Loop

Trusted parked full-run: the worker records and pushes internal batch progress; the host consumes
bounded telemetry only and reconciles once on safety/blocked/terminal wake. After every host-owned
commit/push (staging, reconcile, cleanup): answer the six control questions (unfinished work?
active compute? idle resources? user changes? Stop Gate/continuation_guard still no-stop? allowed
to stop?) and continue immediately unless a hard stop applies.

---

## Documentation Triggers

- Behavior changed -> CHANGELOG (B6), reference docs (B4)
- New trap or hidden dependency -> `.ai-docs/gotchas.md` at reconcile if warranted
- Reusable lesson -> `docs/elves/learnings.md` at reconcile
- If none apply at a close, record that explicitly.

---

## Elves Report

- **Generate:** yes (substantial finite run)
- **Default path:** `/tmp/elves-report-elves-2026-07-19.html` (regenerate date-stamped at Final
  Completion; template `references/elves-report-template.html`)
- **Commit report:** no
- **Deliver:** surface the path in the final notification after Final Readiness; merge is
  authorized per Run Control.

---

## Acceptance Checks

Landable is plan Acceptance with proof, recorded per stable id in `.elves-session.json`
(`acceptance: [{id, criterion, met, evidence}]`), master rows reconciled, one batch per Close
commit, staging validation PASS before launch, landing check green at the committed evidence tip
(`python3 "$HOME/.claude/skills/elves/scripts/elves_landing_check.py" --session
.elves-session.json --repo-root .`), then operational cleanup + post-cleanup tip attestation.
Green CI + `status: complete` alone is not landable.

---

## Tool Configuration

```yaml
lint: # none configured (no repo linter)
typecheck: # none configured
build: # none (pure Python scripts + Markdown)
test: python3 -m unittest discover -s tests
consistency: python3 scripts/check_repo_consistency.py
release-checklist: python3 scripts/release_checklist.py --version 2.10.2
aggregate-verifier: python3 scripts/verify_repo.py --version 2.10.2  # repo-provided; terminal gate
review: github-pr-comments
notification: pr-comment
api-surface-snapshot:
  enabled: auto
  required: false
```

Model routing: driver = this session (Fable 5); implement route = exact `claude-fable-5` effort
`low` native worker (explicit user choice); review = driver + independent review subagent;
provider-backed routes: none.

---

## Rollback and Safety Rules

1. One host-owned `b0` rollback ref before launch:
   `python3 "$HOME/.claude/skills/elves/scripts/cobbler_agents.py" implement rollback-ref --json
   --run-id issue-86-review-fixes-2026-07-19 --session-id <worker-session-uuid> --batch 0
   --head <staged-head> --push`
2. Never force-push; never rebase the run branch; never merge outside Run Control policy.
3. If something goes badly wrong: recovery branch from the rollback ref; document; stop.
4. Unexpected tip move outside the registered worker = collision: stop.

---

## Plan and Log Paths

- **Plan:** `docs/plans/v2.10.2-issue-86-review-fixes.md`
- **Learnings:** `docs/elves/learnings.md`
- **Execution log:** `docs/elves/execution-log-issue-86-review-fixes.md`
- **Durable docs manifest:** `.ai-docs/manifest.md`
- **Branch:** `claude/issue-86-review-fixes`
- **PR number:** #87 (draft)
- **Run id:** `issue-86-review-fixes-2026-07-19`
- **Worker session:** `fff5a5a1-1ae6-43c5-b57c-c2508f31b3c4` (exact `claude-fable-5` / `low`)

---

## After Any Compaction

1. Read this file; obey Run Control + Stop Gate.
2. Read `.elves-session.json` (`continuation_guard`, batches, worker session identity).
3. Learnings -> plan -> execution log -> `.ai-docs/manifest.md` -> TODO.
4. Check Active Compute (is the worker session alive? `native-worker status` from the installed
   skill root with this worktree as cwd).
5. Resume the single next required action immediately. Do not redo completed work; do not
   re-launch a healthy worker.

# READ THIS FILE FIRST AFTER ANY COMPACTION OR RESTART

<!-- v2.3 risk/proof policy pins -->
- thin safety kernel; risk low|standard|high independent of trust trusted|untrusted
- validate once, verify changes, attest final
- impact-selected proof during work; broad proof once at terminal readiness and explicit high-risk checkpoints
- mid-run nonblocking new/unresolved PR feedback; terminal waits for required checks
