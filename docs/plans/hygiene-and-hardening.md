# Plan: Hygiene and Hardening — redaction fix, worktree lifecycle, staging packet, drift reduction

## Mission

Fix a real output-corruption bug in the redaction layer, close the resource-lifecycle gap that
litters the machine with dead git worktrees, make the coordinator→implementer packet a staging
deliverable instead of a launch-time afterthought, consolidate security-critical duplicated
helpers, and shrink the documentation drift surface. Done means: the full test suite is green when
run from inside a Claude Code session, merged-run worktrees are reclaimed by a safe gc helper,
staging cannot silently skip the worker packet for delegable runs, and each safety contract has
exactly one authoritative home.

---

## Scope

### In Scope
- `scripts/cobbler_runtime/implement.py` exact-value redaction guard and secret-value collection
- New worktree gc helper (script + `preflight.sh` surface), config, lifecycle docs, one-time dogfood sweep
- `SKILL.md` Staging checklist, survival-guide template Run Control, `schema-and-acceptance.md`
  session schema, plan template handoff wording, kickoff template staging list,
  `acceptance_contract.py` soft warning
- Consolidation of duplicated git/secret/path helpers (`run_git`, `ensure_private_dir`,
  secret-env collection, `.elves-session.json` filename constant)
- `README.md` restructure, `references/glossary.md`, consistency-engine slimming
- Extraction of the embedded provider-supervisor script and the git-contract section out of
  `scripts/cobbler_runtime/full_run.py` (phase 1 of the god-file split only)

### Out of Scope
- Any change to the safety kernel semantics (landing authority, ready≠authorized, no-worker-merge)
- The full six-way decomposition of `full_run.py` (later plan; only phase 1 here)
- Rewriting `guide/index.html` (it already works; only link/coined-term touch-ups if B5 renames terms)
- New providers, adapters, or Cobbler behavior changes
- `.elves/runtime/` retention policy (noted as follow-up; do not implement here)
- Deleting or modifying the unregistered sibling directories on the dev machine (report-only)

---

## Batches

### Batch 1 [B1]: Redaction exact-value minimum-length guard

**Coordinator-to-implementer handoff (required when an external or less-capable worker implements):**
- **Intent / why:** `_inherited_secret_values()` in `scripts/cobbler_runtime/implement.py` collects
  every env var with a secret-shaped name and no minimum value length. In a Claude Code session,
  flags like `CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH=1` register the exact value `"1"`, and
  `redact_text` then replaces every literal `1` in gate payloads with `[REDACTED:exact_grant]`,
  corrupting `gate_path`, timestamps, and version strings. This is why
  `test_cli_gate_failure_exit_code` fails locally but passes in CI.
- **Non-obvious rationale:** `_secret_env_values()` in `scripts/cobbler_agents.py` already applies
  `len(value) >= 8`. The fix is one shared collector, not a second inline guard, so the two
  surfaces cannot diverge again.
- **Build On targets:** `cobbler_runtime/context.py` (`is_secret_env_name`, `redact_text`);
  reuse the existing len>=8 precedent.
- **Owned surfaces:** `scripts/cobbler_runtime/context.py`, `scripts/cobbler_runtime/implement.py`,
  `scripts/cobbler_agents.py`, `tests/` for these modules.
- **Forbidden surfaces:** `SECRET_VALUE_PATTERNS` and all pattern-based redaction;
  `scripts/cobbler_runtime/isolation.py`; run memory; `.git`.
- **Acceptance evidence:** repro before/after — `implement gate --json` run with
  `CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH=1` in the environment emits an unmangled `gate_path` that
  exists on disk.
- **Failure modes / pitfalls:** do not raise the threshold so high that short real tokens leak;
  8 matches the existing CLI guard. Guard applies to exact-value matching only — pattern-based
  redaction must remain untouched.
- **HEAD / run-doc paths / route-session identity / output format:** recorded at staging.

**Tasks:**
- [ ] Add one shared secret-env-value collector in `cobbler_runtime/context.py` with a minimum
  exact-value length (default 8), used by both `implement.py:_inherited_secret_values` and
  `cobbler_agents.py:_secret_env_values`
- [ ] Regression test: gate JSON with short-valued secret-named env vars present is not mangled;
  a >=8-char secret-named value still redacts
- [ ] Confirm `test_cli_gate_failure_exit_code` passes with the Claude Code env flags set

**Acceptance criteria:**
- [ ] [B1-A1] `python3 -m unittest tests.test_cobbler_agents_implement` passes with
  `CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH=1 CLAUDE_CODE_CHILD_SESSION=1` exported
- [ ] [B1-A2] New regression test proves exact-value redaction still fires for a secret-named env
  var with a >=8-char value (old behavior preserved)
- [ ] [B1-A3] Exactly one env-derived exact-secret collector remains; both call sites import it

**Docs likely touched:** none expected (CHANGELOG entry only)

**Risk:** `standard` — redaction is a security surface; the guard must narrow only exact-value
matching, never pattern redaction.
**Caution:** `redact_structure` key-collision behavior depends on redaction outcomes; run its tests.
**Affected surfaces:** `context.py`, `implement.py`, `cobbler_agents.py`
**Constitution impacts:** none
**Review focus:** that no genuinely secret value class lost redaction coverage
**Focused tests:** `tests/test_cobbler_agents_implement.py`, redaction/context tests
**Depends on:** none

---

### Batch 2 [B2]: Worktree gc helper and lifecycle hook

**Coordinator-to-implementer handoff (required when an external or less-capable worker implements):**
- **Intent / why:** Elves creates dedicated worktrees (`preflight.sh --create-worktree`, backed by
  `scripts/preflight_worktree.py`) but nothing ever removes them. Measured on the primary dev
  machine 2026-07-16: 23 littered sibling directories (~13 GB) across four repos; in this repo,
  5 of 10 registered worktrees belong to branches already merged into `origin/main`.
- **Non-obvious rationale:** the create helper's pinned sentence "does not reuse, delete, or
  repair existing worktrees" stays true — gc is a **separate** helper, so the five files pinning
  that phrase (see `WORKSPACE_ISOLATION_PHRASES` in `scripts/consistency_policy.py`) do not churn.
- **Build On targets:** `preflight_worktree.py` (`run_git`, `parse_worktrees`, plan-print style,
  advisory-by-default posture); `preflight.sh` flag dispatch; `config.json.example` `cleanup` block.
- **Owned surfaces:** new `scripts/worktree_gc.py` (or equivalent), `scripts/preflight.sh`,
  `config.json.example`, `SKILL.md` (Final Completion + Reviewed PR Landing), kickoff template,
  new tests.
- **Forbidden surfaces:** `git worktree remove --force`; deletion of unregistered directories;
  any mutation in report mode; other agents' checkouts.
- **Acceptance evidence:** fixture-repo tests proving every safety predicate; dogfood transcript
  on this machine removing exactly the merged five.
- **Failure modes / pitfalls:** a worktree can be clean but carry unpushed commits — check
  ahead-count against upstream, not just `status --porcelain`; prunable-but-locked worktrees;
  the main worktree must never be a candidate; never gc the invoking checkout.
- **HEAD / run-doc paths / route-session identity / output format:** recorded at staging.

**Tasks:**
- [ ] gc helper: report-by-default, `--apply` to mutate. Candidate = registered linked worktree,
  not the main worktree, not the current directory, clean (tracked + untracked), branch fully
  merged into `origin/main` (ancestor check), zero unpushed commits. Removal =
  `git worktree remove` (non-force) + `git branch -d` + `git worktree prune`
- [ ] Unregistered `<repo>-*` sibling directories: report-only listing, never deleted
- [ ] `preflight.sh` surface mirroring `--create-worktree` (e.g. `--gc-worktrees [--apply]`)
- [ ] `cleanup.worktrees: "on-merge" | "report" | "never"` in `config.json.example` (default `report`)
- [ ] Lifecycle hook in docs: SKILL.md Final Completion and the Reviewed PR Landing Command gain a
  post-merge teardown step for the run's own recorded worktree; kickoff template staging list
  mentions teardown expectation
- [ ] Record the created worktree path in `.elves-session.json` at staging so gc/teardown can
  distinguish Elves-created worktrees from operator-created ones
- [ ] Dogfood: run report mode, then `--apply`, on this machine's elves checkout

**Acceptance criteria:**
- [ ] [B2-A1] Fixture tests prove gc refuses: dirty worktree, unmerged branch, unpushed commits,
  current directory, main worktree, unregistered sibling directory
- [ ] [B2-A2] Fixture test proves gc removes a clean merged worktree and deletes its local branch,
  and `git worktree list` no longer shows it
- [ ] [B2-A3] Report mode performs zero mutations (asserted on fixture repo state)
- [ ] [B2-A4] Dogfood evidence: the five merged elves worktrees removed; the five unmerged
  benchmark worktrees and all unregistered siblings untouched and listed in the report
- [ ] [B2-A5] Repo-consistency CI stays green (pinned worktree phrases unchanged or pins updated
  together with the prose)

**Docs likely touched:** SKILL.md, README (brief), kickoff-prompt-template.md, config.json.example, CHANGELOG

**Risk:** `standard` — mutating git state; mitigated by non-force removal and fixture coverage.
**Caution:** `git branch -d` (never `-D`); a failed removal must leave the registry consistent
(`git worktree prune` only after successful removals).
**Affected surfaces:** scripts, preflight.sh, config example, SKILL.md, templates
**Constitution impacts:** none (forbidden-commands list unaffected; gc uses git's own guarded removal)
**Review focus:** the candidate predicate — every clause tested; no path where report mode mutates
**Focused tests:** new `tests/test_worktree_gc.py`, `tests/test_preflight_worktree.py`,
`tests/test_preflight_sh.py`
**Depends on:** none

---

### Batch 3 [B3]: Make the worker packet a staging deliverable

**Coordinator-to-implementer handoff (required when an external or less-capable worker implements):**
- **Intent / why:** staging and the packet live in different parts of the skill; nothing ties them
  together for worker-destined runs. A coordinator staging a delegable run never sees the packet
  as a staging deliverable because it only appears in the Grok launch recipe
  (`full-run-prepare --packet`), i.e. at launch time.
- **Non-obvious rationale:** one checklist sentence plus one named field in each of the two
  schemas is what closes the gap; a named field the staging step fills is not forgettable.
- **Build On targets:** `SKILL.md ## Staging`, `references/survival-guide-template.md` Run Control,
  `references/schema-and-acceptance.md`, `references/plan-template.md` handoff block,
  `references/kickoff-prompt-template.md` "Your job in this call", `scripts/acceptance_contract.py`.
- **Owned surfaces:** those six files, `scripts/consistency_policy.py` pins if new phrases are
  pinned, matching tests.
- **Forbidden surfaces:** launch/dispatch runtime behavior; packet content format.
- **Acceptance evidence:** validator warning fires on a staged session with
  `work_driver != host-native` and no `worker_packet_path`; templates and schema show the field.
- **Failure modes / pitfalls:** warning, not blocker — host-native runs legitimately skip the
  packet. Do not make `worker_packet_path` required in the session schema.
- **HEAD / run-doc paths / route-session identity / output format:** recorded at staging.

**Tasks:**
- [ ] SKILL.md `## Staging` launch-ready checklist gains the conditional line: if Run Control
  `Work driver` ≠ host-native (or the run may be delegated), the standalone
  coordinator→implementer packet is written and its path recorded — staging is not launch-ready
  without it
- [ ] `Worker packet:` field in the survival-guide template Run Control block;
  `worker_packet_path` optional key documented in `schema-and-acceptance.md`
- [ ] Plan-template handoff note states the rule plainly: the per-batch handoff always lives in
  the plan; the consolidated standalone packet is a staging deliverable for any run that might be
  delegated; the two are not substitutes
- [ ] Kickoff template: packet bullet added to both "Your job in this call" staging lists
  (template and example)
- [ ] `acceptance_contract.py validate` emits a **warning** (not a blocker) when the session
  records a non-host-native work driver and no `worker_packet_path`
- [ ] Commit-cadence rule in the handoff standard (user-directed addition, 2026-07-16): SKILL.md's
  Coordinator-to-Implementer Handoff Standard and the packet/kickoff template guidance state that
  an implementing worker pushes at least one non-Close progress slice before `Close`, with the
  first slice due as soon as a failing test or first surface change exists; a single monolithic
  `Close` commit is a reconcile-visible defect the driver logs
- [ ] Reconcile the Work driver enum drift: survival-guide template lists
  `host-native | grok-build | devin-cli | untrusted-writer` while
  `cobbler_runtime/behavior_policy.py` documents `host_native | grok_build | untrusted_writer | n_a`
  (no devin, underscore form). Document the canonical spelling and mapping in one place; align the
  validator's normalization

**Acceptance criteria:**
- [ ] [B3-A1] Validator test: staged session with `work_driver: grok-build` and no
  `worker_packet_path` produces the warning; with the path recorded, no warning; host-native
  produces no warning
- [ ] [B3-A2] All six documentation surfaces carry the packet-at-staging rule and cross-link
  rather than restate it
- [ ] [B3-A3] Repo-consistency CI green; any newly pinned phrases added deliberately and minimally
- [ ] [B3-A4] Work-driver spellings normalize identically in validator and docs (devin covered)
- [ ] [B3-A5] The commit-cadence rule (≥1 pushed non-Close progress slice before Close; first
  slice at first failing test or surface change) appears in SKILL.md's handoff standard and the
  packet/kickoff template guidance, with consistency pins updated in the same commit

**Docs likely touched:** SKILL.md, survival-guide-template.md, schema-and-acceptance.md,
plan-template.md, kickoff-prompt-template.md, CHANGELOG

**Risk:** `low` — docs, schema documentation, and one advisory validator warning.
**Caution:** keep the warning out of `--ci` strict-failure paths; it is advice, not a gate.
**Affected surfaces:** templates, SKILL.md, acceptance_contract.py, consistency pins
**Constitution impacts:** none
**Review focus:** that the warning cannot block a legitimate host-native run
**Focused tests:** `tests/test_acceptance_contract.py`, `tests/test_check_repo_consistency.py`
**Depends on:** none

---

### Batch 4 [B4]: Consolidate security-critical duplicate helpers

**Coordinator-to-implementer handoff (required when an external or less-capable worker implements):**
- **Intent / why:** duplicated helpers with divergent security posture are how B1's bug happened.
  Current state: four `run_git` implementations (`leases.py` canonical/hardened,
  `delegated_git.py` weakest, `audit.py` thin wrapper, `public_api_snapshot.py` bytes-mode) while
  `full_run.py` inlines 16 raw `subprocess.run(["git"…])` calls plus private `_git_head`/
  `_git_common_dir` twins; two `ensure_private_dir` (storage.py fd-anchored vs context.py weak);
  `.elves-session.json` as a string literal in six modules.
- **Non-obvious rationale:** consolidation target is the *hardened* variant in each pair; the weak
  `context.ensure_private_dir` is deleted so nobody can import the wrong one. Public-API-snapshot
  gate will flag removals — record intentional breaks in `api-break-approvals.json` with a plan
  reference.
- **Build On targets:** `leases.run_git` + `hardened_git_env`; `storage.ensure_private_dir`;
  one new shared constant for the session filename.
- **Owned surfaces:** `cobbler_runtime/*.py` call sites, `api-break-approvals.json`, tests.
- **Forbidden surfaces:** behavior of the git contract checks themselves; sandbox profiles;
  redaction semantics (done in B1).
- **Acceptance evidence:** grep-level proof of single definitions; suite green; api-break
  approvals validated by `verify_repo.py`.
- **Failure modes / pitfalls:** `full_run.py` git calls run in varied cwd/env contexts — preserve
  each call's env exactly (hardened env where it was hardened, explicit env where deliberate);
  do not change bytes-mode snapshot reads.
- **HEAD / run-doc paths / route-session identity / output format:** recorded at staging.

**Tasks:**
- [ ] One canonical `run_git` (hardened) exported from a single module; `delegated_git.py`,
  `audit.py` delegate to it; `full_run.py` raw git subprocess calls and `_git_head`/
  `_git_common_dir` twins migrate to it (bytes-mode snapshot reader may remain, documented)
- [ ] Delete `context.ensure_private_dir`; all callers on `storage.ensure_private_dir`;
  approval entry recorded
- [ ] `.elves-session.json` filename becomes one shared constant across the six modules
- [ ] Remove function-local imports that guard non-existent cycles (~13 sites, e.g.
  `native_worker.py` L361 comment claims a cycle that does not exist); keep any that a test proves
  necessary

**Acceptance criteria:**
- [ ] [B4-A1] Exactly one `run_git` definition body remains (delegators allowed); zero raw
  `subprocess.run(["git"` call sites left in `full_run.py`
- [ ] [B4-A2] `context.ensure_private_dir` gone; `api-break-approvals.json` entry passes strict
  `verify_repo.py --ci` validation
- [ ] [B4-A3] Full suite green; `test_storage_isolation_git.py`, `test_dispatch_isolation.py`,
  `test_full_run_supervisor.py` specifically green (regression preservation)
- [ ] [B4-A4] Session filename literal appears exactly once in `cobbler_runtime/`

**Docs likely touched:** CHANGELOG; `.ai-docs/context-index.md` if it names moved helpers

**Risk:** `high` — wide mechanical change through the largest module's git plumbing.
**Caution:** environment construction differs per call site (host env vs hardened env vs child
env); migrate call-by-call with the env choice preserved and reviewed.
**Affected surfaces:** most of `cobbler_runtime/`
**Constitution impacts:** none intended — that is the review question
**Review focus:** each migrated git call's env/cwd equivalence; approval-file correctness
**Focused tests:** the three named above plus `tests/test_public_api_snapshot.py`
**Depends on:** B1 (shares the "one collector" pattern), otherwise none

---

### Batch 5 [B5]: README restructure and glossary

**Tasks:**
- [ ] README.md reduced to: what/why, install (both hosts), one first run, worker choice summary,
  and a linked reference index into `references/` — target ≤ 450 lines; the task-first tutorial
  remains `guide/index.html`
- [ ] All version narration ("v2.1 adds…", "v2.3 joyful runs…") moves to CHANGELOG.md; feature
  docs describe current behavior only
- [ ] `references/glossary.md` created: every coined term (Cobbler and its sub-modes,
  chat-to-work/chat-to-land, survival guide, parked driver, full-run, Stop Gate, Ride-Along, …)
  gets a one-line definition; docs link to the glossary on first use
- [ ] "Council" reduced to a single compatibility line (deprecated alias of Cobbler); "joyful
  runs" retired as a concept name (contract doc may keep its filename with an alias note)
- [ ] Near-duplicate README sections merged (e.g. `### Tool configuration` / `## Tool
  Configuration`, `### Batch sizing` / `## Batch Sizing`, `### Review` / `### Review methods`)
- [ ] Each workflow contract declared authoritative in exactly one references/ file; README,
  SKILL.md, AGENTS.md link instead of restating (SKILL.md stays the compact canonical workflow;
  its non-normative restatements become links)

**Acceptance criteria:**
- [ ] [B5-A1] `wc -l README.md` ≤ 450; no `v2.x adds`-style narration outside CHANGELOG.md
- [ ] [B5-A2] `references/glossary.md` exists and covers every term it promises; no dead links
- [ ] [B5-A3] Repo-consistency CI green with pins updated alongside prose in the same commits
- [ ] [B5-A4] Install + first-run path still complete: following README alone reaches a working
  `install_doctor.py --startup` pass (regression preservation for onboarding)

**Docs likely touched:** README.md, SKILL.md, AGENTS.md, references/*, CHANGELOG.md

**Risk:** `standard` — wide but mechanical; the consistency engine will catch missed pins.
**Caution:** every deleted README sentence that a pin requires must have its pin updated in the
same commit, or CI reds cascade.
**Affected surfaces:** all human-facing docs, consistency_policy.py pins
**Constitution impacts:** none
**Review focus:** nothing normative got deleted — moved or linked only
**Focused tests:** `tests/test_check_repo_consistency.py`, `tests/test_release_checklist.py`
**Depends on:** B3 (staging wording landed first so B5 doesn't churn it twice)

---

### Batch 6 [B6]: Shrink the consistency engine

**Tasks:**
- [ ] With contracts single-sourced by B5, delete phrase pins that exist only to keep restated
  prose synchronized; keep version alignment, structural/link checks, recovery-order, and the
  handful of safety-kernel sentences that must stay verbatim
- [ ] `consistency_policy.py` reduced accordingly; document the policy for adding a new pin
  (a pin is a last resort, not the default)

**Acceptance criteria:**
- [ ] [B6-A1] `consistency_policy.py` line count reduced by ≥ 50% with CI still green
- [ ] [B6-A2] A deliberate drift injected in a test fixture is still caught for the retained
  safety-kernel pins (regression preservation for the checker itself)

**Docs likely touched:** none beyond comments; CHANGELOG

**Risk:** `standard` — deleting checks needs evidence each was made redundant by single-sourcing.
**Caution:** do not remove pins for sentences that still exist in >1 file.
**Affected surfaces:** consistency_policy.py, consistency_engine.py, its tests
**Constitution impacts:** none
**Review focus:** the retained pin set — is every multi-file sentence still covered
**Focused tests:** `tests/test_check_repo_consistency.py`
**Depends on:** B5

---

### Batch 7 [B7]: full_run.py split, phase 1

**Tasks:**
- [ ] Extract the embedded provider-supervisor program (`_PROVIDER_SUPERVISOR_SCRIPT`, ~938 lines
  as a raw string) into its own Python file, loaded as data or generated at build of the launch
  argv, so it is lintable and unit-testable in place
- [ ] Extract the git-contract / protected-refs section into a module (using B4's canonical
  `run_git`)
- [ ] Characterization tests lock behavior before each extraction

**Acceptance criteria:**
- [ ] [B7-A1] `full_run.py` ≤ 7,500 lines (measurable god-file bar, not a structure lock alone)
- [ ] [B7-A2] Supervisor script passes lint as a real file; at least one direct unit test exercises
  its argument/protocol surface
- [ ] [B7-A3] `tests/test_full_run_supervisor.py` and the full suite green (regression preservation)

**Docs likely touched:** `.ai-docs/context-index.md`, CHANGELOG

**Risk:** `high` — the supervisor is process-lifecycle code; extraction must be byte-equivalent in
what gets executed, proven by the characterization tests.
**Caution:** the supervisor string is consumed by launch argv construction; keep the handoff shape
identical (same interpreter invocation, same env contract).
**Affected surfaces:** full_run.py, new modules, tests
**Constitution impacts:** none intended
**Review focus:** exact equivalence of the launched supervisor before/after
**Focused tests:** `tests/test_full_run_supervisor.py`
**Depends on:** B4

---

## Master Acceptance

- [ ] [M-A1] Full suite green in an environment that includes short-valued secret-named env vars
  (Claude Code session): `CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH=1 python3 -m unittest discover -s tests`
- [ ] [M-A2] Dogfooded gc: five merged elves worktrees removed, unmerged benchmarks and
  unregistered siblings reported untouched; `git worktree list` reflects it
- [ ] [M-A3] A staged delegable run records `worker_packet_path`; the validator warns when it is
  missing for non-host-native drivers; both schemas and both staging templates carry the field
- [ ] [M-A4] README ≤ 450 lines, glossary exists, version narration lives only in CHANGELOG,
  repo-consistency CI green, `verify_repo.py --ci` green at the release version
- [ ] [M-A5] No unapproved public API breaks: `api-break-approvals.json` entries exist for every
  intentional removal and strict verification accepts them

---

## Non-Negotiables

- Never weaken pattern-based redaction; the minimum-length guard applies only to exact-value
  matching and mirrors the existing `_secret_env_values` precedent
- Worktree gc never uses `--force`, never deletes unregistered directories, never touches a dirty
  or unmerged worktree, and never mutates in report mode
- Never weaken, delete, or skip a test merely to obtain green
- Repo-consistency CI and `verify_repo.py` strict checks green at every batch Close
- The user owns whether Elves may merge. Default is user-merges; merge-on-green or an explicit
  reviewed-PR landing command (`\land-pr` or `/land-pr`) is the only opt-in. In either opt-in path,
  Elves lands a regular merge commit (never a squash) only after final readiness.

---

## Test Strategy

- **Primary gate:** `python3 -m unittest discover -s tests` (~3.5 min, 1,027 tests)
- **Secondary gate:** `python3 scripts/verify_repo.py --version <release-version>` (maintainers'
  strict gate; use `--ci --base-ref origin/main` form on clean tips)
- **Env-sensitivity check (new, from B1):** run the primary gate once with
  `CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH=1` exported — this is the regression trap for the redaction bug
- **Known pre-existing failure:** `test_cli_gate_failure_exit_code` fails before B1 in Claude Code
  sessions; that is the bug under fix, not flakiness
- **Durable doc expectations:** promote reusable lessons to learnings; update
  `.ai-docs/context-index.md` when helpers move (B4, B7)

---

## Batch Sizing

```yaml
team-size: 2
sprint-length: 1 week
```

---

## Notes

- Machine litter measured 2026-07-16: elves ×10 registered worktrees (merged: delegated-worker-v2-1,
  devin-cli-worker, faster-goal-runs, grok-build-open-source, joyful-elves-runs), love-spark ×6,
  geometry-exploration ×2, Aigora-site-next.js ×1, plus 4 unregistered elves siblings
  (2 mktemp-suffixed slice dirs, a canary fixture with synthetic origins, one full clone
  `elves-v1.20.1-grok-worker`). ~13 GB total. Unregistered dirs are operator-owned: report, never delete.
- The pinned sentence "does not reuse, delete, or repair existing worktrees" (five files) remains
  true throughout — it describes the create helper; gc is a separate helper.
- Work-driver enum drift to reconcile in B3: template `devin-cli` vs `behavior_policy.py` comment
  enum lacking devin and using underscores; adapters.py uses `devin-cli`.
- Release versioning (whether this lands as v2.7.0) is decided at staging; run
  `scripts/release_checklist.py` if the version bumps.
- Follow-up candidates deliberately out of scope: `.elves/runtime/` retention policy (57 files in
  `reviews/` today), `/tmp` Elves-report retention, full six-way `full_run.py` decomposition,
  README example-block trimming beyond the 450-line bar.
