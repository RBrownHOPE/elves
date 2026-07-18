# READ THIS FILE FIRST AFTER ANY COMPACTION OR RESTART

> This is the Survival Guide for the audit-follow-ups run. After any compaction event, read this
> file before touching any code. If the information here contradicts what you think you remember,
> trust this file.
>
> Recommended read order after any compaction: survival guide -> `.elves-session.json` ->
> learnings -> plan -> execution log -> `.ai-docs/manifest.md`.

---

## Mission

Implement the 2026-07 audit follow-ups from `docs/reviews/2026-07-repo-audit-grok-prewalk.md`
(PR #82): runtime correctness/robustness fixes, a real host-profile registry with a feature-gated
Grok Build prewalk host arm plus qualification tooling, and the contract/glossary/changelog
amendments — while keeping prewalk actual mode `off` for every host and claiming no new
qualification. Land on the fork's `main` (RBrownHOPE/elves) via merge commit once the final
readiness review is green.

---

## Run Control

- **Run mode:** finite
- **Stop policy:** blocker-only
- **User intent:** "plan and stage an /elves run to make these updates, including setting up the
  prewalk, then hand off to Fable medium to do the work, then review the work and land the pr"
- **Checkpoint due by:** none
- **Checkpoint semantics:** none
- **May continue after checkpoint:** yes
- **Actual stop conditions:** all batches complete, final readiness review clean, PR merged to
  fork main — or a genuine blocker with no workaround.
- **Workspace ownership:** dedicated worktree `/Users/ruthbrown-ennis/research/dev/elves-audit-follow-ups`
  created with `./scripts/preflight.sh --create-worktree feat/audit-follow-ups --base origin/main`
- **Branch tip at start (collision tripwire):** `f32ce0dd6bc0ce37b605551ad1336526181b1796`
- **Merge policy:** merge-commit-on-green (explicit user chat-to-land authorization in this
  session: "then review the work and land the pr"). Regular merge commit into the fork's `main`
  on `RBrownHOPE/elves`, never squash, only after the Final Readiness Review passes. Upstream
  `aigorahub/elves` is READ-only for this user; never attempt to merge there.
- **Final-response policy:** disallowed until stop conditions met
- **Coordination mode:** Cobbler-first
- **Batch completion rule:** host-native; every completed batch ends with
  `update execution log -> update survival guide -> commit -> push`.
- **Progress visibility rule:** commit subjects use
  `[feat/audit-follow-ups · Batch N/4 · Contract|Implement|Validate|Review|Close] <concrete outcome>`;
  vague subjects forbidden; `Close` requires acceptance evidence.
- **Coordinator-to-implementer handoff:** consolidated standalone packet at
  `.elves/runtime/packets/audit-follow-ups.md`; per-batch scope handed to the worker with the
  packet each launch.
- **Worker packet:** `.elves/runtime/packets/audit-follow-ups.md` (also `worker_packet_path` in
  `.elves-session.json`)
- **Handoff validation:** v2.8 advisory path
- **Re-read rule:** immediately after every host-owned commit and push, re-read this survival
  guide before doing anything else.
- **Checkpoint rule:** n/a (no checkpoint)

- **E2E mode:** chat-to-land
- **Work driver:** host-native (in-session native subagent worker pinned to `claude-fable-5`,
  effort `medium`; the standalone `claude` CLI binary is not installed on this machine, so the
  supervised `native-worker` CLI transport is unavailable — honest fallback recorded)
- **Implementation lane:** n/a (host-native)
- **Delegation scope:** batch
- **Git mode:** host_only (driver owns all commits/pushes; worker subagents edit files only)
- **Driver monitor mode:** interactive
- **Driver update policy:** default
- **Driver poll policy:** host wait primitive
- **Driver review policy:** per-batch plus final cumulative
- **Follow mode:** n/a (host-native subagent)
- **Risk posture:** standard (B2 high blast radius — regression review pass required)
- **Trust mode:** trusted
- **Landing outcome:** complete_and_merge
- **Driver merge authorized:** yes via run control (chat-to-land recorded above)
- **Worker merge authority:** false
- **Stable plan IDs:** batches `B1`-`B4`; acceptance `B#-A#`; Master `M-A1`-`M-A3`
- **Acceptance row syntax:** bare `- [ ] B1-A1: criterion` rows in the plan
- **Batch helper syntax:** `--batch 1` and `--batch B1` equivalent
- **Staging acceptance validation:** run `acceptance_contract.py validate` at staging; record
  result in the execution log
- **Staging acceptance command:** `python3 scripts/acceptance_contract.py validate --repo-root .
  --session .elves-session.json`
- **High-risk checkpoints:** none staged (driver reviews every batch interactively)
- **GitHub push auth route:** host `gh` (authenticated as RBrownHOPE)
- **Re-drive budget:** 2 substantive worker re-drives per batch
- **Continuation harness:** host-native
- **Continuation rule:** if work remains and stop conditions are not met, continue without
  waiting for user acknowledgment.

---

## Cobbler Session State

- **Cobbler default:** on
- **Activated by:** Elves invocation
- **Scope:** current Elves run
- **Behavior:** treat follow-up prompts as Cobbler-mediated by default
- **Persistence:** survival guide and `.elves-session.json`
- **Exit phrases:** "Cobbler Mode: off", "leave Cobbler Mode", "stop using Cobbler by default"

---

## Session Budget

- **Started:** 2026-07-18 ~23:00 local
- **User returns:** unspecified (assume ~8 hours)
- **Checkpoint expectation:** landed PR on fork main with all four batches complete
- **Time budget:** ~8 hours
- **Average batch time so far:** n/a
- **Batches remaining:** 4 of 4

---

## Stop Gate

- **Planned batches remaining:** 4
- **Stop allowed right now:** no
- **Why:** staging just completed; no implementation batch has run.
- **Next required action:** launch the B1 worker (fable, medium) with the packet.

---

## Effort Standard

- Work as hard as you can for the full run. Maintain the same effort on the last batch as the
  first. When one task is complete, immediately take the next highest-value action.

---

## Forbidden Stop Reasons

- A commit or push succeeded; CI is green; a PR exists; the user is silent; a useful summary was
  written; the current batch is complete but later batches remain; the remaining work feels like
  a lot for one turn; this feels like a natural place to check in. None of these permit stopping.

---

## Non-Negotiables

- Never modify a test merely to obtain green; the 3.9 fixes are explicit version gates with clear
  messages, never weakened assertions.
- Prewalk actual mode stays `off` for every host; no doc or code may claim Grok prewalk
  availability or behavioral qualification; `allow_grok=false` stays an absolute veto.
- No breaking changes to existing exported names (public API snapshot gate; `full_run` re-export
  doctrine).
- Never merge to upstream `aigorahub/elves`; landing target is fork `RBrownHOPE/elves` `main`
  only, regular merge commit, never squash.
- **Never run destructive git commands:** `git reset --hard`, `git checkout .`, `git clean -fd`,
  `git push --force`, `git rebase` on shared branches. Never.
- **One run owns one branch and one checkout:** this run owns `feat/audit-follow-ups` in the
  dedicated worktree; any unexpected tip move is a collision — stop.
- Consistency pins (`consistency_policy.py`) update in the same batch as every edited normative
  sentence.

---

## Launch Readiness

- [x] Plan cleaned and saved to disk (`docs/plans/audit-follow-ups.md`)
- [x] Survival guide updated from the current plan
- [x] Learnings file initialized
- [x] Execution log initialized with batch breakdown and preflight notes
- [x] Branch created (`feat/audit-follow-ups` in dedicated worktree)
- [x] Branch and checkout ownership confirmed; no other agent shares this branch
- [ ] PR opened (fill number below after creation)
- [x] Preflight run; critical failures cleared (see execution log: python floor + missing claude
  CLI recorded as environment constraints, not blockers)
- [x] Run mode, return time, and non-negotiables recorded
- [x] Stop Gate initialized with `Stop allowed right now: no`
- [x] Single-kickoff continues after staging (explicit user authorization for the full arc)

---

## Current Phase

**Status:** Staging

**Active batch:** none (B1 next)

**What was just finished:** Staging artifacts written; worktree and branch created.

**Single next action:** Commit staging docs, push, open PR against fork main, then launch B1
worker (fable, medium).

---

## Active Compute

**No active paid or long-running compute.** Worker subagents run per-batch and end with the
batch.

---

## Next Exact Batch

**Batch:** B1: Runtime correctness and robustness fixes

**Scope:**
- Python >= 3.10 floor guard + version-gated tests (sync_installed_skills, installed_bundle_smoke)
- Event-typed session identity capture in native_worker
- Transient-failure markers scoped to stderr/provider errors
- Shared forbidden-session-token set incl. `continue`
- Supervisor TimeoutExpired handling, torn-line-tolerant follower, stderr_tail preservation
- PREWALK_FAILURE_CODES enforcement + guide-recovery misclassification fix

**Acceptance criteria:**
- [ ] B1-A1 through B1-A6 (see plan)

**Risk:** identity/transient classification changes touch every native-worker launch; fixture
tests must pass unchanged.

**Rollback authority:** host rollback tag `elves/pre-batch-1` before the batch.

---

## Post-Checkpoint Control Loop

After every host-owned commit and push: identify the next unfinished task; verify no idle
resources; check whether the user changed scope; confirm the Stop Gate still says no; continue
immediately.

---

## Documentation Triggers

- Behavior changed -> README/CHANGELOG `[Unreleased]`/reference docs
- Architecture shifted (host registry) -> `.ai-docs/architecture.md`
- New pattern -> `.ai-docs/conventions.md`; new trap -> `.ai-docs/gotchas.md`
- Reusable lesson -> `docs/elves/learnings.md`

---

## Memory and Resource Hygiene

Standard checklist; this run is expected to fit in one context with subagent delegation.

---

## Elves Report

- **Generate Elves Report:** yes (substantial finite run)
- **Default path:** `/tmp/elves-report-elves-audit-follow-ups-2026-07-18.html`
- **Commit report:** no
- **Template:** `references/elves-report-template.html`

---

## Acceptance Checks

**Policy:** Green CI + `status: complete` is not landable. Landable is plan Acceptance with proof.

- [ ] Staging acceptance validation passed before any worker launch
- [ ] All validation gates pass per batch (suite on 3.9 green-with-skips; CI matrix authoritative)
- [ ] Plan `B#-A#` criteria met with evidence recorded in `.elves-session.json`
- [ ] `M-A#` reconciled before branch readiness
- [ ] PR review performed each batch; blocking findings resolved
- [ ] Landing check passes before operational-artifact cleanup

---

## Tool Configuration

```yaml
lint: python3 -m compileall -q scripts
typecheck:   # none configured for this repo
build:       # not applicable (skill repo)
test: python3 -m unittest discover -s tests -t .
e2e:         # not applicable
smoke: python3 scripts/check_repo_consistency.py
review: github-pr-comments
notification: pr-comment
api-surface-snapshot:
  enabled: auto
  required: false
```

Full verification: `python3 scripts/verify_repo.py --version Unreleased` (evidence gate is known
red on local 3.9 until B1 lands; CI is authoritative for the broad matrix).

Model routing (user-directed): implement -> native subagent pinned `claude-fable-5` effort
`medium`; validate -> host; review -> independent native subagent lens (session default effort);
synthesize -> coordinator. Requested transport was the supervised `native-worker` CLI; actual is
in-session native subagents; fallback reason: `claude` CLI binary not installed on PATH.
Prewalk for this run's worker: requested `auto`, actual `off`
(`prewalk_capability_unavailable:supervised_cli_transport_unavailable`).

---

## Plan and Log Paths

- **Plan:** `docs/plans/audit-follow-ups.md`
- **Learnings:** `docs/elves/learnings.md`
- **Execution log:** `docs/elves/execution-log.md`
- **Durable docs manifest:** `.ai-docs/manifest.md`
- **Branch:** `feat/audit-follow-ups`
- **PR number:** _(fill in after PR creation)_
- **Plan hash at session start:** _(fill in after commit)_

---

## After Any Compaction

Follow the template protocol: survival guide -> Run Control/Stop Gate -> `.elves-session.json` ->
learnings -> plan -> execution log -> `.ai-docs` -> Active Compute -> continuation guard -> next
action -> clock -> resume immediately.

---

# READ THIS FILE FIRST AFTER ANY COMPACTION OR RESTART
