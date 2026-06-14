# READ THIS FILE FIRST AFTER ANY COMPACTION OR RESTART

> This is the Survival Guide for the `v1.15.0 Cobbler` Elves run. Trust this file over memory after
> compaction. Read order: this file -> `.elves-session.json` -> `docs/elves/learnings.md` ->
> `docs/plans/v1.15.0-cobbler.md` -> `docs/elves/execution-log.md` -> `.ai-docs/manifest.md` and
> linked durable docs.

---

## Mission

Ship `v1.15.0 Cobbler`, reframing the `v1.14.0` Elves Council work around Cobbler as the
user-facing coordinator inside Elves. Cobbler should let a user ask once and get one fitted answer
from host-native independent lenses: direct answer, specialist elves, or a read-only council when
several perspectives help. Normal Cobbler use must not require OpenRouter or any external provider
key.

---

## Run Control

- **Run mode:** open-ended
- **Stop policy:** do not stop. Continue through the five planned batches, final readiness,
  reviewed-PR landing, GitHub version release/tag, and X announcement draft, then continue with the
  next highest-value release-hardening or follow-up task until the user explicitly stops or a
  genuine blocker prevents safe progress.
- **User intent:** The user launched the staged run and added: when complete, run the Elves
  land-PR protocol, make all documentation fully current, bump the version everywhere including
  GitHub, then write an X announcement post that explains Cobbler as an attempt to recreate the
  public Fable orchestration insight. Latest update: use Cobbler's own method while doing the work;
  get ideas and review from Gemini 3.1 Pro, Gemini 3.5 Flash, Opus 4.8, Grok 4.3, and Qwen 3.7 Max
  with high/deep thinking where available; use keys/syntax from this repo or the local
  geometry-exploration project if needed; keep going and do not stop.
- **Checkpoint due by:** 2026-06-15 02:18 EDT, default 8 hours from launch
- **Checkpoint semantics:** delivery budget only, not a stop boundary
- **May continue after checkpoint:** yes
- **Actual stop conditions:** stop only when the user explicitly stops the run or when a true
  blocker prevents safe progress. Completion of the planned v1.15.0 deliverables becomes a
  checkpoint, not a stop boundary.
- **Workspace ownership:** owned branch in the main checkout; `git worktree list` shows only
  `/Users/john/aigora/dev/elves` on `codex/v1.15.0-cobbler`
- **Branch tip at start (collision tripwire):** `6fe775e334d3af446de75587957ac11b029258a3`
- **Merge policy:** reviewed-pr-landing-command one-off opt-in for PR #28; land only with a regular
  merge commit after final readiness is clean, never squash or rebase
- **Final-response policy:** disallowed while the Stop Gate says stopping is not allowed. Because
  the run is now open-ended, final completion is disabled unless the user explicitly stops or a
  true blocker forces it.
- **Batch completion rule:** Every completed batch ends with `update execution log -> update
  survival guide -> commit -> push`.
- **Re-read rule:** Immediately after every commit and push, re-read this survival guide before
  doing anything else.
- **Checkpoint rule:** The 8-hour checkpoint is delivery-only. Log it, push current state, and
  continue immediately if work remains.
- **Continuation rule:** After launch, if work remains and actual stop conditions are not met,
  continue without waiting for user acknowledgment.
- **Cobbler self-application rule:** Use Cobbler-style orchestration for this run: coordinator
  judgment first, independent lenses where useful, visible dissent/risk synthesis in the execution
  log, and external provider-backed model review when explicitly requested by the user.

---

## Session Budget

- **Started:** 2026-06-14 18:18 EDT
- **User returns:** not specified; checkpoint default is 2026-06-15 02:18 EDT
- **Checkpoint expectation:** best review-ready state by checkpoint; continue after checkpoint if
  required work remains
- **Time budget:** default 8-hour checkpoint budget; objective remains active across turns
- **Average batch time so far:** Batches 1-5 each completed in short focused passes after
  launch-state setup
- **Batches remaining:** 0 of 5, then final deliverables and open-ended follow-up/release-hardening
  work until stopped

---

## Stop Gate

> Rewrite this section in place. This is the explicit answer to "may I stop now?" Do not infer it.

- **Planned batches remaining:** 0 plus final deliverables and open-ended follow-up
- **Stop allowed right now:** no
- **Why:** The user explicitly said to keep going and not stop. All five planned batches are
  complete, but final readiness, landing, GitHub release/tag, announcement drafting, and follow-up
  release-hardening work remain.
- **Next required action:** Commit and push Batch 5, then begin final readiness review and
  reviewed-PR landing.

---

## Effort Standard

- Work as hard as you can for the full launched run. Do not be lazy.
- Maintain the same level of effort on the last batch as on the first.
- Do not settle for the minimum acceptable change when deeper verification or a better user
  experience is still clearly in scope.
- Do not settle for the minimum acceptable wording pass; this release is about user experience and
  cross-surface coherence.
- When one task is complete, immediately take the next highest-value action from the plan, review
  queue, or validation findings.

---

## Forbidden Stop Reasons

These are not valid reasons to stop the launched run while work remains:

- A checkpoint time was reached
- A commit or push succeeded
- CI or local validation is green
- A PR exists
- The user is silent or offline
- A useful summary has been written
- The current batch is complete but later batches remain
- The remaining work feels like a lot for one turn
- The current moment feels like a natural place to check in

If one of these happens after launch, update docs, commit, push, re-read this file, and continue.

---

## Memory Surfaces

- **Plan:** `docs/plans/v1.15.0-cobbler.md`
- **Survival guide:** `docs/elves/survival-guide.md`
- **Learnings:** `docs/elves/learnings.md`
- **Execution log:** `docs/elves/execution-log.md`
- **Durable docs:** `.ai-docs/manifest.md`, `.ai-docs/architecture.md`,
  `.ai-docs/conventions.md`, `.ai-docs/gotchas.md`

Promotion flow: `execution log -> learnings -> .ai-docs`

---

## Strategic Forgetting

- Rewrite live survival-guide sections in place.
- Keep chronology in the execution log.
- Promote only stable, reusable lessons into `docs/elves/learnings.md` or `.ai-docs/*`.
- Do not mutate local Codex/Claude app state, installed skills, plugins, automations, or databases
  unless the plan or user explicitly requires it.
- If installed-skill syncing is needed, use the repo script and safe checks. Never overwrite
  user-owned alias skills without a clear managed-by-Elves marker or explicit user action.

---

## Non-Negotiables

- User experience comes first: Cobbler must feel like asking one coordinator, not operating a
  provider router.
- Normal Cobbler use must not require OpenRouter, `OPENROUTER_API_KEY`, or any external provider
  key.
- Cobbler must use host-native subagents first when available: Claude Code subagents in Claude
  Code, Codex subagents in Codex, direct read-only lens analysis otherwise.
- Public docs must not advertise or copy external vendor identity, persona, policy, safety framing,
  or "Fable-like" wording.
- `SKILL.md` and `AGENTS.md` must move together for behavior changes.
- Preserve `/council`, `/ec`, `/elves-council`, and `$elves council: <task>` as compatibility
  aliases while making `/cobbler` and `$elves cobbler: ...` the primary user experience.
- Never run destructive git commands: `git reset --hard`, `git checkout .`, `git clean -fd`,
  `git push --force`, or shared-branch rebases.
- Never merge by default. This run has an explicit reviewed-PR landing command opt-in recorded in
  Run Control, so landing is allowed only after final readiness is clean and only with a regular
  merge commit.

---

## Launch Readiness

- [x] Plan cleaned and saved to disk
- [x] Survival guide updated from the current plan
- [x] Learnings file initialized or refreshed
- [x] Execution log initialized with batch breakdown and preflight notes
- [x] Branch created or confirmed
- [x] Branch and checkout ownership confirmed; no other agent should share this branch
- [x] PR opened or existing PR recorded: <https://github.com/aigorahub/elves/pull/28>
- [x] Preflight run and critical failures cleared
- [x] Run mode, return time default, and non-negotiables recorded
- [x] Stop Gate initialized for staging handoff
- [x] Stop Gate initialized with `Stop allowed right now: no` after launch; staging is the only
      temporary exception because the user explicitly requested a staged handoff
- [x] Launch prompt prepared for the next call

---

## Current Phase

**Status:** In progress

**Active batch:** Between planned batches; next is final readiness review and reviewed-PR landing

**What was just finished:** Batch 5 hardened CI path filters, public-wording checks,
provider-policy forbidden patterns, README install guidance, config precedence notes, changelog
release wording, and the new v1.15 plan's public framing. Local validation passed with 31 tests,
installed Claude/Codex copies were synced to `1.15.0`, and preflight passed with advisory warnings
only.

**Single next action:** Commit and push Batch 5, re-read this file, then poll PR checks/comments and
begin final readiness review.

---

## Active Compute

**No active paid or long-running compute.**

---

## Final Deliverables

- Complete all five planned Cobbler batches.
- Run final readiness review on the cumulative diff and all PR feedback.
- Run the reviewed-PR landing protocol for PR #28 as an explicit one-off merge opt-in.
- Bump version everywhere and publish `v1.15.0` on GitHub after the merge.
- Draft an X announcement post about Cobbler and the Fable orchestration framing. Keep that Fable
  framing in the announcement/final handoff, not in the core repo docs.

---

## Next Exact Batch

**Batch:** Final readiness review and reviewed-PR landing

**Scope:**
- Commit and push Batch 5.
- Re-read this survival guide after push.
- Reply to or resolve addressed PR feedback.
- Run final cumulative review of `git diff main...HEAD`, commits, PR comments, checks, plan, and
  docs.
- If clean, execute the reviewed-PR landing protocol for PR #28 using a regular merge commit.
- After merge, publish GitHub tag/release `v1.15.0` and draft the X announcement post.

**Acceptance criteria:**
- [ ] Batch 5 commit is pushed and PR checks are green on the new head.
- [ ] PR feedback is addressed or explicitly dispositioned with replies/resolution where possible.
- [ ] Final cumulative review finds no blockers.
- [ ] PR #28 lands with a regular merge commit if final readiness is clean.
- [ ] GitHub release/tag `v1.15.0` is published after merge.
- [ ] X announcement draft is prepared.

**Risk:** The branch is close to release, so false stop signals are especially tempting. Keep the
loop moving through landing, release, announcement, and follow-up hardening.

**Rollback tag:** `elves/pre-batch-5-cobbler` for the completed hardening batch; final landing uses
GitHub PR merge/release history rather than a batch rollback tag.

---

## Validation Gates

Run these at staging and after relevant batches:

- `ELVES_SURVIVAL_GUIDE_PATH=docs/elves/survival-guide.md ./scripts/preflight.sh`
- `python3 scripts/check_repo_consistency.py`
- `python3 -m unittest discover -s tests -p 'test_*.py'`
- `python3 -m py_compile scripts/check_repo_consistency.py scripts/install_doctor.py scripts/sync_installed_skills.py scripts/validate_survival_guide.py`
- `python3 -m json.tool config.json.example >/dev/null`
- `python3 -m json.tool .elves-session.json >/dev/null`
- `python3 scripts/sync_installed_skills.py --check`
- `git diff --check`

Staging result on 2026-06-14 16:11 EDT:

- `ELVES_SURVIVAL_GUIDE_PATH=docs/elves/survival-guide.md ./scripts/preflight.sh` passed with two
  advisory warnings: no package-managed project type is expected for this repo, and the current
  shell lacks some recommended non-interactive env vars that preflight dry-runs with safe defaults.
- `python3 scripts/check_repo_consistency.py` passed at repo version `1.14.0`.
- `python3 -m unittest discover -s tests -p 'test_*.py'` passed: 13 tests.
- `python3 -m py_compile scripts/check_repo_consistency.py scripts/install_doctor.py scripts/sync_installed_skills.py scripts/validate_survival_guide.py` passed.
- `python3 scripts/sync_installed_skills.py --check` passed for installed Claude and Codex copies
  at `1.14.0`.
- JSON validation and `git diff --check` passed.
- PR checks for #28 passed: GitHub Actions analyze jobs, CodeQL, and Socket Security checks.

Batch 1 result on 2026-06-14 18:28 EDT:

- `python3 scripts/check_repo_consistency.py` -> PASS, repo version `1.15.0`.
- `python3 -m unittest discover -s tests -p 'test_*.py'` -> PASS, 13 tests.
- `python3 -m py_compile scripts/check_repo_consistency.py scripts/install_doctor.py scripts/sync_installed_skills.py scripts/validate_survival_guide.py` -> PASS.
- `python3 -m json.tool config.json.example >/dev/null` and
  `python3 -m json.tool .elves-session.json >/dev/null` -> PASS.
- `git diff --check` -> PASS.
- `python3 scripts/sync_installed_skills.py --check` -> PASS after syncing installed Claude and
  Codex skill copies to repo version `1.15.0`.
- `OPENAI_CODEX=1 ELVES_SURVIVAL_GUIDE_PATH=docs/elves/survival-guide.md ./scripts/preflight.sh`
  -> PASS with advisory warnings only.
- PR checks for #28 at commit `5f4d356` passed: GitHub Actions analyze jobs, CodeQL, Socket
  Security, and repo check.

Batch 2 result on 2026-06-14 18:38 EDT:

- `python3 -m unittest discover -s tests -p 'test_*.py'` -> PASS, 20 tests.
- `python3 scripts/check_repo_consistency.py` -> PASS, including Claude Cobbler alias guardrails.
- `python3 -m py_compile scripts/check_repo_consistency.py scripts/install_doctor.py scripts/sync_installed_skills.py scripts/validate_survival_guide.py` -> PASS.
- `python3 -m json.tool config.json.example >/dev/null` and
  `python3 -m json.tool .elves-session.json >/dev/null` -> PASS.
- `python3 scripts/sync_installed_skills.py --check` -> PASS after applying managed aliases.
- `git diff --check` -> PASS.
- `OPENAI_CODEX=1 ELVES_SURVIVAL_GUIDE_PATH=docs/elves/survival-guide.md ./scripts/preflight.sh`
  -> PASS with advisory warnings only.

Batch 3 result on 2026-06-14 18:43 EDT:

- `python3 scripts/check_repo_consistency.py` -> PASS, including Codex Cobbler host-boundary
  guardrails.
- `python3 -m unittest discover -s tests -p 'test_*.py'` -> PASS, 22 tests.
- `python3 -m py_compile scripts/check_repo_consistency.py scripts/install_doctor.py scripts/sync_installed_skills.py scripts/validate_survival_guide.py` -> PASS.
- `python3 -m json.tool config.json.example >/dev/null` and
  `python3 -m json.tool .elves-session.json >/dev/null` -> PASS.
- `python3 scripts/sync_installed_skills.py --check` -> PASS after syncing installed Claude and
  Codex copies.
- `git diff --check` -> PASS.
- `OPENAI_CODEX=1 ELVES_SURVIVAL_GUIDE_PATH=docs/elves/survival-guide.md ./scripts/preflight.sh`
  -> PASS with advisory warnings only.

Batch 4 result on 2026-06-14 18:49 EDT:

- `python3 scripts/check_repo_consistency.py` -> PASS, including Cobbler-first reference/config
  guardrails.
- `python3 -m unittest discover -s tests -p 'test_*.py'` -> PASS, 25 tests.
- `python3 -m json.tool config.json.example >/dev/null` and
  `python3 -m json.tool .elves-session.json >/dev/null` -> PASS.
- `python3 -m py_compile scripts/check_repo_consistency.py scripts/install_doctor.py scripts/sync_installed_skills.py scripts/validate_survival_guide.py` -> PASS.
- `git diff --check` -> PASS.
- `python3 scripts/sync_installed_skills.py --check` -> PASS after syncing installed Claude and
  Codex copies.
- `OPENAI_CODEX=1 ELVES_SURVIVAL_GUIDE_PATH=docs/elves/survival-guide.md ./scripts/preflight.sh`
  -> PASS with advisory warnings only.

---

## Post-Checkpoint Control Loop

Every completed batch must end with a commit and push. Immediately after every commit and push,
re-read this survival guide before doing anything else.

After every commit and push, answer:

1. What unfinished batch or task am I starting right now?
2. What paid compute or long-running resources are active right now?
3. Did the user change stop behavior, checkpoint meaning, priorities, or scope?
4. Does the Stop Gate say stopping is allowed?
5. Does the Stop Gate still say `Stop allowed right now: no`, or does `.elves-session.json` still
   say `continuation_guard.stop_allowed: false`?
6. If stopping is not allowed, what exact next work starts now?

---

## After Any Compaction

Read this file first. Trust the Run Control section and Stop Gate over memory, then read
`.elves-session.json` and its `continuation_guard`, then `docs/elves/learnings.md`, then
`docs/plans/v1.15.0-cobbler.md`, then `docs/elves/execution-log.md`, then `.ai-docs/manifest.md`
and linked durable docs as needed.

If the run has been launched and the Stop Gate or `continuation_guard` says stopping is not
allowed, continue the next exact batch after rehydrating. If this file says staging is complete and
the user has not launched yet, stop with the launch prompt rather than starting implementation.

---

## Documentation Triggers

- **Behavior changed:** update `SKILL.md`, `AGENTS.md`, README, CHANGELOG, and relevant references.
- **Config changed:** update `config.json.example`, `references/tool-config-examples.md`, and
  consistency checks.
- **Alias behavior changed:** update README install docs, sync script behavior, and tests.
- **Durable lesson discovered:** update `docs/elves/learnings.md`; promote to `.ai-docs/*` only if
  it becomes a stable repo truth.
