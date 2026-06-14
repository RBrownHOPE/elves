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

- **Run mode:** finite
- **Stop policy:** stage-only for this call; after launch, stop only at final completion, an
  explicit user stop, or a genuine blocker with no reasonable workaround
- **User intent:** "sounds great, adjust the [$elves] run so it's staged. i'll kick it off with the next prompt"
- **Checkpoint due by:** not specified; default to 8 hours from launch unless the launch prompt
  states a different return time
- **Checkpoint semantics:** staging handoff now; after launch, finite completion unless the launch
  prompt says a checkpoint is delivery-only or a hard stop
- **May continue after checkpoint:** after launch, follow the launch prompt; default finite mode may
  continue until plan completion unless near a hard-stop deadline
- **Actual stop conditions:** during staging, stop when launch-ready; after launch, stop only when
  all batches are complete and final readiness is clean, the user explicitly stops the run, or a
  true blocker prevents safe progress
- **Workspace ownership:** owned branch in the main checkout; no other active agent should write
  this checkout or branch
- **Branch tip at start (collision tripwire):** `6fe775e334d3af446de75587957ac11b029258a3`
- **Merge policy:** user-merges by default; never merge unless the user explicitly sets
  merge-on-green or invokes `/land-pr` / `\land-pr`; any opt-in landing uses a regular merge commit,
  never squash or rebase
- **Final-response policy:** allowed for staging handoff only; after launch, disallowed until the
  Stop Gate says stopping is allowed or a true blocker forces it
- **Batch completion rule:** Every completed batch ends with `update execution log -> update
  survival guide -> commit -> push`.
- **Re-read rule:** Immediately after every commit and push, re-read this survival guide before
  doing anything else.
- **Continuation rule:** After launch, if work remains and actual stop conditions are not met,
  continue without waiting for user acknowledgment.

---

## Session Budget

- **Started:** 2026-06-14 16:07 EDT
- **User returns:** not specified; default is ~8 hours after launch unless launch prompt overrides
- **Checkpoint expectation:** staging call creates branch, plan, run memory, PR, and preflight
  evidence; launch call starts Batch 1
- **Time budget:** staging only in this call; default finite run budget is ~8 hours after launch
- **Average batch time so far:** N/A - not launched
- **Batches remaining:** 5 of 5

---

## Stop Gate

> Rewrite this section in place. This is the explicit answer to "may I stop now?" Do not infer it.

- **Planned batches remaining:** 5
- **Stop allowed right now:** yes, for staging handoff only
- **Why:** The user explicitly asked to stage the run now and launch from the next prompt.
- **Next required action:** Wait for the user's launch prompt, then start Batch 1 after reading the
  durable memory stack.

After launch, rewrite this to `Stop allowed right now: no` until the plan is complete, the user
stops the run, or a true blocker is reached.

---

## Effort Standard

- Work as hard as you can for the full launched run. Do not be lazy.
- Maintain the same level of effort on the last batch as on the first.
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
- Preserve `/council`, `/ec`, and `/elves-council` as compatibility aliases while making
  `/cobbler` and `$elves cobbler: ...` the primary user experience.
- Never run destructive git commands: `git reset --hard`, `git checkout .`, `git clean -fd`,
  `git push --force`, or shared-branch rebases.
- Never merge by default. The user merges unless they explicitly opt into merge-on-green or invoke
  the reviewed-PR landing command.

---

## Launch Readiness

- [x] Plan cleaned and saved to disk
- [x] Survival guide updated from the current plan
- [x] Learnings file initialized or refreshed
- [x] Execution log initialized with batch breakdown and preflight notes
- [x] Branch created or confirmed
- [x] Branch and checkout ownership confirmed; no other agent should share this branch
- [ ] PR opened or existing PR recorded
- [ ] Preflight run and critical failures cleared
- [x] Run mode, return time default, and non-negotiables recorded
- [x] Stop Gate initialized for staging handoff
- [ ] Launch prompt prepared for the next call

---

## Current Phase

**Status:** Staging

**Active batch:** Batch 0: staging

**What was just finished:** The Cobbler plan and live run memory were created; PR and preflight are
still being finalized.

**Single next action:** Commit and push staging docs, open the PR, run preflight, then mark the run
launch-ready.

---

## Active Compute

**No active paid or long-running compute.**

---

## Next Exact Batch

**Batch:** 1: Cobbler Product Hierarchy

**Scope:**
- Bump canonical version metadata to `1.15.0`.
- Rework `SKILL.md`, `AGENTS.md`, README, and CHANGELOG so Cobbler is the coordinator and Council
  is a read-only gathering mechanism.
- Preserve Council compatibility aliases while adding primary Cobbler language.

**Acceptance criteria:**
- [ ] `SKILL.md`, `AGENTS.md`, README, and CHANGELOG all present Cobbler as the user-facing
      coordinator.
- [ ] Council remains documented as a read-only compatibility path.
- [ ] Version metadata and latest changelog agree on `1.15.0`.
- [ ] Forbidden public wording search passes.

**Risk:** Wording drift into two competing products. Keep Cobbler as the coordinator and Council as
the mechanism.

**Rollback tag:** `elves/pre-batch-1`

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

---

## Post-Checkpoint Control Loop

Every completed launched batch must end with a commit and push. Immediately after every commit and
push, re-read this survival guide before doing anything else.

After every commit and push, answer:

1. What unfinished batch or task am I starting right now?
2. What paid compute or long-running resources are active right now?
3. Did the user change stop behavior, checkpoint meaning, priorities, or scope?
4. Does the Stop Gate say stopping is allowed?
5. If stopping is not allowed, what exact next work starts now?

---

## Documentation Triggers

- **Behavior changed:** update `SKILL.md`, `AGENTS.md`, README, CHANGELOG, and relevant references.
- **Config changed:** update `config.json.example`, `references/tool-config-examples.md`, and
  consistency checks.
- **Alias behavior changed:** update README install docs, sync script behavior, and tests.
- **Durable lesson discovered:** update `docs/elves/learnings.md`; promote to `.ai-docs/*` only if
  it becomes a stable repo truth.
