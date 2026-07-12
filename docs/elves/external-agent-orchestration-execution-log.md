# Execution Log: Cobbler External-Agent Orchestration

This is the reverse-chronological proof log for the v1.20.0 run. Keep raw chronology here; keep live
control in the Survival Guide; promote stable lessons to `docs/elves/learnings.md` and `.ai-docs/*`.

## Run Digest

- **Last updated:** 2026-07-12 11:02 EDT
- **Current phase:** Batch 3 complete; Batch 4 next
- **Active batch:** Batch 4: Single external writer lease and Grok implementation adapter
- **Last completed batch:** Batch 3
- **Next exact batch:** Batch 4: Single external writer lease and Grok implementation adapter
- **Active PR:** draft PR #59, `https://github.com/aigorahub/elves/pull/59`
- **Docs promoted this run:** qualification lessons added to `docs/elves/learnings.md`
- **Latest Elves Report:** not generated

## Batch 6 Contract: 2026-07-12 ~11:13 EDT

**Lease:** lease-batch-6-20260712-A base `ebd9d9394ebf2fa95d14c857e12ad3c219bb9fc5`

---

## Batch 5 Close: 2026-07-12 ~11:12 EDT

**Batch:** 5 Setup/preferences/host parity | **Tests:** 276/276
**Next:** Batch 6 E2E qualification and release handoff.

---

## Batch 5 Contract: 2026-07-12 ~11:09 EDT

**Lease:** lease-batch-5-20260712-A base `169368a34a108a604cad71d5ac98f38c7eb955cf`

---

## Batch 4 Close: 2026-07-12 ~11:08 EDT

**Batch:** 4 Writer lease and Grok adapter | **Tests:** 261/261
**Review:** host PASS on import/validation; independent fresh review deferred-light due to rate limits earlier — host attestation + full lease test matrix.
**Next:** Batch 5 setup/preferences.

---

## Batch 4 Contract: 2026-07-12 ~11:03 EDT

**Lease:** lease-batch-4-20260712-A base `675b85dd9fcabfbf3dde8abdf875fdce2c6871c6`

---

## Batch 3 Close: 2026-07-12 ~11:02 EDT

**Batch:** 3 Exact persistent sessions, discovery, usage
**Tests:** 245/245 OK
**Note:** Grok successor exited mid-turn twice; host sealed product commits from audited worker tree and fixed README H1 pin break. Documented under Decisions.
**Review:** host PASS + fresh non-implementer Grok PASS (quorum 2). Fable/Codex still rate-limited earlier.
**Next:** Batch 4 writer lease.

---

## Batch 3 Contract: 2026-07-12 ~10:56 EDT

**Contract:** Exact sessions, digests/rehydration, Grok lineage honesty, usage with unknown quota,
doctor discovery. No writer leases.

**Lease:** lease-batch-3-20260712-A base

**Rollback:** elves/pre-batch-3

---

## Batch 2 Close: 2026-07-12 ~10:55 EDT

**Batch:** 2 Parallel read-only council dispatcher
**Timing:** Implement ~5m / Validate ~3m / Review+remediation ~15m / Total ~25m
**Budget remaining:** ~7h

**What changed:** context.py, dispatch.py, adapters read-only builders, council CLI, tests, council docs.
**Worker:** lease-batch-2 + remediation lease; 6 detached commits imported as host Implement/Review.
**Fable FAIL:** `--dangerously-skip-permissions` on claude-code read-only builder → fixed to
`--permission-mode plan` + bypass regression tests + non-zero exit fails lane.
**Review quorum:** host PASS + fresh non-implementer Grok PASS (Fable session limit until 11:20;
Codex usage limit until 11:16). Implementer successor excluded.
**Tests:** 227/227 OK.
**Next:** Batch 3 sessions/discovery/usage.

---

## Batch 2 Contract: 2026-07-12 ~10:41 EDT

**Contract:** Parallel read-only council dispatcher with redaction, argv-safe concurrency, adapter
command builders, lightweight_review path, and quorum/fallback policy. No writer/session registry.

**Build on:** Batch 1 cobbler_runtime contracts; council-workflow independence; council-prompts schema.

**Acceptance:** wall-clock overlap ≥3 fake lanes; secret env scrub; advisory/required quorum
semantics; native-only path; full suite not decreased from 199.

**Lease:** lease-batch-2-20260712-A base `9d390ce92472efa18ed15b658ea9e15f658f9adc` session 9927883a…

**Rollback:** elves/pre-batch-2

---

## Batch 1 Close: 2026-07-12 ~10:35 EDT

**Batch:** 1 Contracts, configuration, and implementer clarity
**Timing:** Implement ~7m (Grok) / Validate ~2m / Review ~12m+ / Total ~25m
**Budget remaining:** ~7.5h

**What changed:**
- `scripts/cobbler_runtime/{schema,config,capabilities,adapters}.py` + `__init__.py`
- `scripts/cobbler_agents.py` validate-config/doctor skeleton
- `tests/test_cobbler_agents_config.py` (+ consistency phrase tests)
- `references/models.toml.example`
- SKILL.md / AGENTS.md handoff + progress-commit standards; templates; review-subagent
- check_repo_consistency pins; sync_installed_skills runtime paths

**Worker lease:** `lease-batch-1-20260712-A` session `9927883a-0203-42e1-a3e4-710a02096d46`
base `3928f82` -> worker final `d727040` (4 detached commits). Audit PASS: refs/config/hooks
unchanged; binary patches imported; host commits `efc0a4b..98b0e07`.

**Contract status:** all Batch 1 acceptance criteria met with evidence rows in session JSON.

**Test results:** PASS 199/199 (baseline 170 → +29); consistency/compile/shell/json/toml/whitespace/
survival-guide PASS.

**Review findings:**
- fresh-host PASS (0 findings)
- Fable `claude-fable-5` PASS; strongest dissent: shape≠reality until Batch 4 lease guard
- Fugu still in flight at close; quorum 2 already satisfied without it
- Non-blocking Fable notes deferred to later batches/TODO

**Decisions made:** Close Batch 1 on host+Fable quorum without waiting for optional Fugu completion;
record Fugu result when available. Do not remediate non-blocking dead-code notes in Batch 1.

**Docs:** Impacted SKILL/AGENTS/templates/README/.ai-docs. Updated in worker import. Promoted none
beyond batch product. Deferred Survival Guide machine-parse to later batches.

**Regression attestation:** Cumulative product delta from main includes Batch 0 staging + Batch 1
foundation. Shared surfaces: `check_repo_consistency.py` and `sync_installed_skills.py` additive
phrase/path lists. Public API surface: not configured required. Test baseline 170→199, skips 0.
Confidence HIGH: full suite green, independent review quorum met, lease audit clean.

**Commit:** host tip at Close will record; rollback `elves/pre-batch-1`

**Next:** 1. Batch 2 council dispatcher  2. Batch 3 sessions

---

## Launch: 2026-07-12 10:11 EDT

**Phase:** execution started from fresh launch call

**Stop Gate:** set to `no`; `continuation_guard.stop_allowed=false`; remaining_batches=6

**Verified:**

- plan SHA-256 matches staged
  `27a400cff4f1a12de8ae75b59167a6921df1287910e21d93fb1ade5bad357309`
- draft PR #59 open, head `codex/external-agent-orchestration`, all listed checks SUCCESS
- owned worktree clean at `88a31fd75014c9182dda856a3eb295cbb8c38279`
- Grok successor worktree clean, detached, and aligned to the same SHA
- non-interactive environment exported (`CI=true`, `GIT_TERMINAL_PROMPT=0`, etc.)
- local rollback tag `elves/pre-batch-1` retargeted to current HEAD (prior remote tag from an
  unrelated historical run remains on origin and is not force-updated)
- pre-lease for-each-ref digest
  `8e1c9ebfbed93ef912938a868b5b9308097ebb0cc96761a67ce064206cc1bb82`
- shared config digest `829369795b8ce24eab883566d212abed0034e7f7d7d3aa7459dbd7dd168cb05a`
- hooks digest empty-set
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`

**Coordination:** Cobbler-first. Sol Ultra host owns contracts, run memory, git/PR, acceptance, and
synthesis. Exact Grok successor `9927883a-0203-42e1-a3e4-710a02096d46` receives one substantial
Batch 1 lease. Independent review excludes Grok and requires quorum 2.

**Next:** Contract commit + Batch 1 worker lease.

---

## Final Routing Review: 2026-07-12 09:49 EDT

Canary `FINAL-ROUTING-REVIEW-20260712-J` sent the same five complete staged documents to persistent
Fable and Fugu read-only lanes. Fable (`claude-fable-5`, exact session
`02bb9552-fbbd-423f-abbe-acbaa580c918`) returned PASS with no blocker. It preserved the dissent that
manual host audits remain the safety boundary until Batch 4 ships mechanical enforcement and noted
that shared Codex quota can remove the optional Luna lane mid-run.

The first Fugu resume failed before inference because the command omitted `model_provider=sakana`;
Codex tried to route `fugu-ultra` as a ChatGPT model. The corrected exact-session retry used the
Sakana provider, reported actual `fugu-ultra` and session
`019f5627-e61e-72a3-af3f-ae6e51a348b5`, and agreed the routing/ownership design is coherent. Its FAIL
listed only three staging-close facts still pending at review time: refresh the plan digest, record
draft PR #59 in structured/live memory, and complete preflight + final Grok alignment + launch prompt.
The plan digest and PR metadata are now corrected; deterministic validation/alignment/launch closure
is now complete. All 170 tests and deterministic gates pass; preflight passes with only the expected
no-package-marker warning; all PR checks are green at `52a7fb6`; all four inline comments are answered;
and the clean Grok successor is detached at exact pushed tip
`52a7fb6297ed129e53f45aacd5d49a6de6e3573d`. The same Fugu session will now receive a no-tools
closure request. The final `Close` commit will record the verdict, then the worker will be realigned
to that exact final SHA before push.

**Closure:** the same corrected Sakana-backed exact Fugu session returned
`FINAL ROUTING REVIEW: PASS`, actual model `fugu-ultra`, exact session
`019f5627-e61e-72a3-af3f-ae6e51a348b5`, and no remaining blocker. Its strongest dissent is the
transactional parity window: this final metadata-only `Close` commit must be followed by a successful
clean detached worker alignment to the new SHA before push. Fable and Fugu therefore both pass; the
host will fail closed if the alignment, push, parity proof, or final checks do not complete.

Observed usage is recorded in `.elves-session.json`. Fable reported 3,694 output tokens and $4.682979;
Fugu reported 8,089,503 input tokens (7,261,441 cached), 49,139 output tokens, and 17,030 reasoning
tokens. Remaining subscription quota is still unknown; do not infer scarcity or abundance from those
figures.

---

## Batch 1 Contract: prepared during staging on 2026-07-12

This contract is deliberately detailed for the persistent Grok implementation successor. The host must
re-read current source and update only facts that changed before issuing the worker lease; it must not
reduce the contract to a short chat prompt.

**Behaviors:**

- Define generic, validated harness/capability/role/fallback contracts without hardcoding this user's
  current model names as public defaults.
- Include an optional lightweight-review role so a strong supervisor can delegate cheap bounded
  read-only checks while deterministic git/PR operations remain model-free.
- Resolve active routes from committed Survival Guide snapshot -> local ignored
  `.elves/models.toml` -> installed/user `config.json` -> native host default, preserving source
  provenance and explicit `required` semantics. Ship `references/models.toml.example` as the
  reviewable schema; never treat the local file as team-shared or stage it.
- Provide a thin `scripts/cobbler_agents.py` operator CLI with non-mutating `validate-config` and
  `doctor --json` foundations; no external model inference or worker write in this batch.
- Add the coordinator-to-implementer handoff standard to both canonical skill surfaces, relevant
  templates/reviewer guidance, consistency checks, and tests.
- Add the git-history-as-operator-UI standard to the same canonical/template/checker surfaces: the
  host pushes meaningful branch/batch/phase/outcome commits during a batch; a qualified worker may
  create only audited detached handoff commits and never owns refs/remotes/push.
- Preserve native-only Elves, current Cobbler hierarchy, stage-then-launch behavior, and all existing
  170 tests.

**Build on:**

- `config.json.example` and `references/tool-config-examples.md` for native-first model routing and
  explicit `required` semantics;
- `references/council-prompts.md` for context packet/work-scope/forbidden-action fields;
- `scripts/workspace_guard.py`, `scripts/preflight_worktree.py`, and
  `scripts/elves_landing_check.py` as existing safety/ownership utilities—not code to copy;
- `scripts/sync_installed_skills.py` for runtime path installation and marker-gated aliases;
- `scripts/check_repo_consistency.py` phrase-map conventions and focused unittest patterns;
- current repo architecture/conventions/gotchas docs.

**Implementation handoff:**

- **Intent/rationale:** later dispatcher/session/writer logic needs one stable provider-neutral
  vocabulary. If this layer is vague, every later batch will embed incompatible Claude/Grok/Fugu
  conditionals.
- **Survey first:** inspect all surfaces above and existing tests before choosing file/module names.
  Prefer a thin entry point plus focused `scripts/cobbler_runtime/` modules; do not create a new god
  script or duplicate a helper.
- **Owned product surfaces:** only files named in the host-issued worker lease for Batch 1. Product
  docs may include `SKILL.md`, `AGENTS.md`, templates, README/config, scripts, and tests when assigned.
- **Host-only forbidden surfaces:** this plan, Survival Guide, execution log, learnings,
  `.elves-session.json`, `.elves/**`, direct `.git/**` edits, branches/tags/refs, pushes, PRs, or
  installed global skills.
- **No secrets:** examples may name environment variables; no key/token/auth value or private local
  path belongs in product config/docs/tests.
- **No brittle implementation prescription:** use standard-library Python and current repo test
  patterns; if a planned module split is inferior after survey, report the evidence and proposed
  alternative before diverging.
- **Commit milestone:** Grok receives one substantial Batch 1 turn and should create two to five
  meaningful detached direct-descendant commits when the work naturally divides. The host audits
  the complete chain, imports approved binary patches, and creates/pushes sanitized
  `[codex/external-agent-orchestration · Batch 1/6 · <phase>] <concrete outcome>` commits recording
  worker SHAs. Grok must not create refs or push; `Close` is unavailable until every acceptance row
  has evidence.
- **Likely pitfalls:** Python `tomllib` availability, TOML/JSON/Survival Guide precedence drift,
  treating ignored `.elves/models.toml` as committed team state, treating model names as
  capabilities, conflating installed/authenticated/qualified, prematurely implementing dispatch,
  and forgetting installed-runtime sync lists.
- **TOML runtime rule:** Python 3.11 is the local-TOML feature floor. On older Python, preserve
  Survival Guide/JSON/native operation when no local TOML exists; if it does exist, fail validation
  with upgrade guidance rather than silently ignoring it or vendoring a parser.
- **Failure behavior:** invalid/ambiguous config produces stable actionable diagnostics; native-only
  configuration remains usable; a missing optional external profile falls back; an explicit
  required unavailable profile blocks validation. Do not silently weaken `required`.

**Acceptance criteria:**

- [ ] Unit tests prove config precedence/provenance, deterministic fallback order, explicit
      `required`, unknown/invalid profiles, and no-provider native defaults.
- [ ] The same schema maps implementation to Claude Code, Grok, or a custom harness without source
      changes.
- [ ] The same schema maps lightweight review to a cheaper native/custom profile while leaving the
      supervising route unchanged; commit/push execution does not dispatch any model.
- [ ] `.elves/models.toml` parsing uses no unreviewed third-party dependency and has a clear
      unsupported-Python/fallback diagnostic if applicable.
- [ ] Tests prove the local TOML is ignored/untracked, setup never stages it, the tracked
      `references/models.toml.example` is credential/path-free, and the Survival Guide captures the
      effective routes plus provenance.
- [ ] `validate-config --json` emits stable objects, invokes no paid model, and mutates no repo state.
- [ ] Config/examples contain no raw-secret patterns or personal paths.
- [ ] Coordinator-to-implementer handoff language appears in `SKILL.md`, `AGENTS.md`, plan/Survival
      Guide/execution-log templates, and review obligations, protected by consistency tests.
- [ ] Those surfaces also require prompt meaningful progress commits, the branch/batch/phase/outcome
      subject schema, audited-detached-worker versus host-branch ownership, worker ref/push denial,
      and acceptance-backed `Close`; consistency tests reject drift and vague example subjects.
- [ ] Focused tests and full baseline pass; test count is at least 170 with none newly skipped.
- [ ] Consistency, compile, shell syntax, JSON, TOML validator, sync fixture, and whitespace gates pass.
- [ ] `.elves-session.json` receives non-empty criterion/met/evidence rows before status becomes
      complete.

**Blast radius:**

- Canonical skill/config/template/checker/install surfaces have many downstream consumers.
- Nature: additive foundation plus behavior wording change.
- Risk: high. A bad contract or drifted mirror contaminates every later batch and installed skill.

**Phase routing:**

- Requested route: persistent Grok successor `9927883a-0203-42e1-a3e4-710a02096d46` (`devbox`,
  context predecessor `019f5644-93d5-7a02-827d-caa8b30a2825`) under one detached writer lease
- Actual route: pending launch
- Fallback: diagnose/repair Grok before any switch; Claude Code Opus then host are recorded fallbacks
- Automatic implementation fallback: disabled; after three distinct failed Grok recovery attempts,
  stop for the user rather than silently switching providers
- Supervision/synthesis/docs: `gpt-5.6-sol` Ultra host coordinator; routine bounded read-only checks
  may use ephemeral `gpt-5.6-luna` low; validation/git/PR execution is deterministic host shell
- Independent review: fresh native + Fable + Fugu, concurrent, Grok excluded; this run explicitly
  requires two successful independent reports after recovery/fallback

**Pre-implementation survey already completed by host:**

- `SKILL.md`/`AGENTS.md` already define provider-optional, native-first full-run routes.
- `config.json.example` already separates Cobbler role routing from full-run phase routing.
- `workspace_guard.py`, `preflight_worktree.py`, `elves_landing_check.py`, and sync/checker utilities
  are the existing extension points.
- Repo uses standard-library Python scripts and `unittest`; no package-managed runtime exists.
- Live qualification proved the current external CLIs but also exposed Grok 0.2.93 incompatibilities
  that fixtures must preserve.

---

## Grok Detached-Commit and Codex Cost Routing: 2026-07-12 09:29 EDT

The user chose larger delegation units: one substantial complete batch per Grok turn, with a target
of two to five meaningful commits when the work naturally divides. Sol Ultra remains accountable for
the contract, high-risk/disputed review, acceptance, synthesis, and canonical run documents. A live
Codex canary proved `gpt-5.6-luna` at low reasoning can be invoked ephemerally in a read-only sandbox
for bounded routine checks. Git commit/push/PR plumbing remains deterministic host shell work and
does not dispatch Sol, Luna, or any external model.

A later Luna-low full-diff review attempt at 09:41 EDT reached the account's aggregate Codex usage
limit and reported a retry time of 11:16 EDT before inference. The earlier exact-response canary still
qualifies the invocation path, but not current availability. Lightweight review is therefore optional,
probed before each use, and falls back to the Sol host without blocking the run.

**Detached-commit council:** persistent Fable, Grok parent, and Fugu Ultra reviewed the proposal
independently. All returned conditional PASS provided the host retained branch/remote/run-memory
ownership, audited the entire chain plus refs/remotes/config/hooks, and imported binary patches
rather than bare-cherry-picking worker commits. Fugu's stricter binary-patch boundary was selected.

**Canary `GROK-DETACHED-COMMIT-20260712-A`:** the original implementation child
`019f5644-93d5-7a02-827d-caa8b30a2825` proved its immutable `workspace` sandbox could edit the
worker CWD but could not create the shared linked-worktree `index.lock`; `git add` failed with
`Operation not permitted`. `--fork-session --sandbox devbox` also failed before inference because a
resume/fork cannot change the originating sandbox. The host removed the uncommitted probe and left
the source, worker, and owned checkout state otherwise unchanged.

The host then created the persistent context-seeded implementation successor
`9927883a-0203-42e1-a3e4-710a02096d46` in the same detached worktree with actual model `grok-4.5`
and immutable `devbox` profile. It read the complete staged Survival Guide, session JSON, plan, and
execution log before any write. Under credential-scrubbed environment overrides, `dontAsk`, exact
write/add/commit/status/rev-parse allows, and explicit branch/tag/switch/push/remote/config/network
denies, it created detached commit `2bf4937ff1737de4d007a54be86c1a0e36cc8bc5` with parent
`74c52d88868e39a9d4c5cca6dee46919011d2127` and exactly one regular probe file.

Independent host audit verified: clean detached status; exact parent/tree/path/content/author/message;
source main and staging branch unchanged; and identical command-specific pre/post hashes for refs
(`0f215bf415fcdb7c4222f5909bbc156e8bf327c74754e53a0227ec0176d90e1a`), remote heads/tags
(`5249b7af22d79f9979ee1b542c02149d5eb798e009267bc559ceaf994f424ce1`), shared Git config
(`f1ceca447a7bb8e9d7b4217a5b6c164ab6af7099c4d58001efe30a78cbd46455`), and hooks
(`869f208cd3158287f078a6adfae73e6b08f5abafa363a85932457e5a5a09af18`). The host did not import
the probe and moved the clean detached worker back to the expected base, leaving the canary dangling
and unreachable from every ref.

**Result:** qualified workers may create only direct-descendant detached commits when the exact
session/profile/lease permits it. The host still owns refs, branch commits, push, PR, run memory,
validation, acceptance, and synthesis. Product implementation remains unstarted; this is staging
qualification and contract work only.

---

## PR Review Feedback: 2026-07-12 09:11 EDT

Draft PR #59 opened and all current CodeQL, repository-consistency, and Socket checks passed. Gemini
Code Assist left four medium-priority inline comments. The host accepted both underlying findings:

- replaced committed `/Users/john/...` values with deterministic `.` / `../elves` / `${HOME}/...`
  locators; the runtime must expand, canonicalize, and verify them against git worktree registration,
  while fully expanded machine paths remain only in ignored runtime evidence;
- replaced shell-expanded `python3 -m py_compile scripts/*.py tests/*.py` with
  `python3 -m compileall -q scripts tests`, matching the plan's argv-safe/no-shell direction.

These are portability fixes, not weaker safety: an unresolved or unregistered locator still blocks a
write lease. The host will reply to all four inline comments after the fix commit is pushed and then
re-read every review surface.

---

## Implementation Route Decision: 2026-07-12 09:06 EDT

**Superseded for the exact writer identity and detached-commit policy by the 09:29 EDT decision
above; retained as chronology.**

The user changed the experiment from “Grok required for the first implementation attempt” to “have
Grok Build complete the run.” The same exact Grok child
`019f5644-93d5-7a02-827d-caa8b30a2825` is now the required worker for every product implementation
and remediation slice across all six batches. The host still owns detailed packets, run documents,
git/PR, patch audit/import, validation, review synthesis, and progress updates. Claude Code Opus and
host implementation remain documented possibilities but are not automatic fallbacks; after three
distinct failed Grok recovery attempts with no safe workaround, stop and ask the user.

The user will receive synthesized live updates in the Codex chat after each Grok turn and each
audit/validation/commit milestone; GitHub/GitKraken will show every pushed progress commit. Raw model
transcripts remain ignored evidence unless the user asks for an excerpt.

---

## Operator Visibility Decision: 2026-07-12 08:59 EDT

**Refined by the 09:29 EDT decision: host branch commits/pushes remain the operator surface, while
the qualified successor may create audited detached handoff commits.**

The user explicitly relies on GitKraken/GitHub to monitor unattended work. The host will therefore
commit and push meaningful progress slices within each batch, not merely one opaque batch dump. The
subject schema is `[branch · Batch N/6 · Contract|Implement|Validate|Review|Close] concrete outcome`.
External workers still cannot commit or push; the host audits/imports and validates before creating
each visible milestone. Batch 1 must promote this rule into canonical Elves behavior, templates,
review obligations, and consistency tests.

**Validation:** repo consistency PASS; 170/170 tests PASS; session JSON, Survival Guide validator,
plan-hash integrity, and whitespace PASS.

---

## Staging Council Review: 2026-07-12 08:57 EDT

**Canary:** `STAGING-PLAN-REVIEW-20260712-I`

The host launched the persistent Fable, exact Grok implementation child, and persistent Fugu Ultra
lanes concurrently over the actual staging documents.

**Initial review:**

- Fable (`claude-fable-5`, session `02bb9552-fbbd-423f-abbe-acbaa580c918`) found no blocker and
  warned that quorum semantics and the future TOML gate needed explicit wording.
- Grok (`grok-4.5`, child session `019f5644-93d5-7a02-827d-caa8b30a2825`) blocked commit on two
  ambiguities: `.elves/models.toml` ownership versus `.gitignore`, and optional-lane failure versus
  required review quorum.
- Fugu Ultra (`fugu-ultra`, session `019f5627-e61e-72a3-af3f-ae6e51a348b5`) found no additional
  blocker and independently highlighted the future-tool gate and Batch 4's isolation risk.

**Host resolution:**

- Defined `.elves/models.toml` as ignored local state, `references/models.toml.example` as the
  tracked schema, and the active Survival Guide as the committed effective-route snapshot.
- Defined advisory `target_quorum` for ordinary optional routing and `required_quorum` only for a
  phase explicitly marked `required = true`. This run now requires two independent successful
  review reports after fallback, including the fresh host lane.
- Labeled the TOML validator and evidence-enforcing landing form as post-staging gates.
- Pinned Python 3.11 as the local-TOML feature floor without breaking native/JSON operation when no
  local TOML exists on older Python.

**Parallel closure review:** all three exact lanes returned `STAGING PLAN REVIEW: PASS` with no
remaining blocker. Fable reported actual model `claude-fable-5`; Grok's structured envelope reported
actual model `grok-4.5` and exact child session `019f5644-93d5-7a02-827d-caa8b30a2825`; Fugu reported
actual model `fugu-ultra` and exact session `019f5627-e61e-72a3-af3f-ae6e51a348b5`. Grok's first
narrow closure attempt terminated `Cancelled` before a verdict; the same exact session then returned
`EndTurn` and PASS when given the corrected clauses inline. This reinforces the planned requirement
to validate terminal status, not process exit code alone. Fugu's inherited MCP OAuth warnings did not
interrupt Sakana inference.

**Strongest retained dissent:** Batch 4 must mechanically prove the external-writer lease and full
refs/path/process audit. Prompt policy, `dontAsk`, macOS network settings, or unchanged HEAD alone
cannot qualify isolation.

**Post-review staging validation:** PASS — repo consistency; 170/170 tests with zero failures or
skips; Python compile; shell syntax; `config.json.example` and `.elves-session.json` parsing;
Survival Guide validator; release checklist with unreleased work allowed; plan SHA-256 match; and
`git diff --check`. Expected negative-fixture landing-check messages appeared inside passing tests.

---

## Session Setup: 2026-07-12 08:39 EDT

**Phase:** staging in progress

**Plan:** `docs/plans/v1.20.0-cobbler-external-agent-orchestration.md`

**Survival Guide:** `docs/elves/external-agent-orchestration-survival-guide.md`

**Learnings:** `docs/elves/learnings.md`

**Execution log:** this file

**Durable docs:** `.ai-docs/manifest.md`, architecture, conventions, gotchas, context index

**Branch:** `codex/external-agent-orchestration`

**Owned worktree locator:** `.` relative to this run's repository root

**Collision tripwire:** `74c52d88868e39a9d4c5cca6dee46919011d2127`

**PR:** not created yet

**Run mode:** finite, approximately 8 hours from the next launch call; no checkpoint

**Coordination:** Cobbler-first. Host owns run docs/git/synthesis; external routes are evidence or one
leased worker.

**Active compute:** none. Qualified sessions are on disk and idle; Grok child worktree is clean and
detached.

**Continuation guard during staging:** stop_allowed=yes; remaining_batches=6;
checkpoint_is_stop=false; next_required_action=finish staging and hand user the fresh launch prompt.

**Batch breakdown:**

1. Contracts, configuration, and implementer clarity — provider-neutral schema/resolver/doctor and
   detailed handoff standard.
2. Parallel read-only council dispatcher — redacted independent fan-out, adapter parsing, quorum.
3. Exact persistent sessions, model discovery, usage — lineage/digests/actual model/unknown quota.
4. Single external writer lease and Grok adapter — fail-closed worktree, audit, untrusted patch.
5. Setup, preferences, host parity, custom recipes — setup aliases, TOML, OpenRouter/API/future tools.
6. End-to-end CouncilElves qualification/release handoff — live loop, mirrors, master prompt, report.

### Qualification evidence

**Baseline:**

- repo HEAD/origin main: `74c52d88868e39a9d4c5cca6dee46919011d2127`
- heads+tags pre/post SHA-256:
  `191b747db719d476c6ba75aeb3fe7478154b5cca95e0de9d0564d2b0f57d2f0c`
- baseline tests: 170 passed in 8.925s; consistency, Python compilation, JSON, and diff checks passed
- source checkout clean before/after

**Persistent sessions and actual models:**

- Fable: `claude-fable-5`, exact session `02bb9552-fbbd-423f-abbe-acbaa580c918`
- Grok planning parent: `grok-4.5`, exact session
  `159e611b-6c48-4376-8695-5134b9803b7e`
- Grok worktree child: `grok-4.5`, exact session
  `019f5644-93d5-7a02-827d-caa8b30a2825`, parent recorded correctly
- Fugu: `fugu-ultra`, exact session `019f5627-e61e-72a3-af3f-ae6e51a348b5`

**Passed tests:**

- parallel exact-session canary and context-retention round;
- Claude-host direct delegation to same Grok/Fugu sessions;
- interactive Grok parent -> detached worktree child lineage;
- exact child resume in the worktree;
- bounded allowed edit; commit and `.elves` edits denied;
- host status/HEAD/ref/remote/path audit;
- independent Fugu review PASS;
- independent Fable review PASS after its requested summary/ref evidence was supplied;
- canary cleanup and clean retained child worktree.

**Important failures turned into plan requirements:**

- headless Grok worktree resume silently wrote the bounded canary to source; immediately audited and
  removed, with no HEAD/ref movement;
- a narrow Grok tools list failed agent construction because hidden terminal-support tools were
  excluded;
- CLI mode/network semantics cannot be treated as hard macOS boundaries;
- inherited Fugu MCP auth warnings are distinct from successful Sakana inference.

### Staging preflight status

- Git remote/push/`gh` auth: PASS from owned worktree; branch is current with `origin/main`
- Validation gates: PASS — consistency; 170/170 tests; recursive compile; shell; JSON plus duplicate-
  key check; Survival Guide; release checklist with expected unreleased warning; plan hash;
  whitespace; credential scan
- Environment/sleep/notification: local desktop; no Slack webhook required; PR comment fallback
- Install doctor: no advisory output at startup
- Non-interactive preflight: PASS with recommended environment exported; caffeinate and AC power PASS;
  sole expected warning is no package-manager project marker in this script/docs repository
- Launch readiness: draft PR #59 exists; pending final commits/push/check poll and post-commit Grok
  successor alignment to the exact final staged tip

### Decisions made

- Extend Cobbler/full-run routing rather than create a competing Council product.
- Build a generic capability-based runtime; treat Fable/Grok/Fugu names as this project's preset.
- Use ignored local `.elves/models.toml` for checkout preferences, ship
  `references/models.toml.example` as the reviewable schema, and snapshot active routes/provenance
  into the committed Survival Guide; never stage the local file or place credential values in any
  surface.
- Preserve exact persistent lineage. For Grok worktrees, persist/discover the child session instead
  of pretending the parent UUID migrated.
- Keep a clean persistent Grok child worktree for the implementation experiment; remove the unused
  host-created qualification worktree and all canaries.
- Public default stays native-only; this Survival Guide explicitly requires the exact Grok child for
  all product implementation/remediation in this run, without automatic provider fallback.
- Host writes canonical run docs and detailed contracts; Grok may edit assigned product docs/code.
- Six architecture-aware batches: contracts before dispatcher, read-only before persistence, and
  persistence before write safety/setup/release.

### Cobbler synthesis

- **Recommendation:** build one capability-verified external-harness framework inside Cobbler, with
  native fallback and host-owned patch integration.
- **Why:** it supports the user's subscriptions now and lets other users adapt Claude-only, custom,
  OpenRouter/API-only, or future tools without source changes.
- **Strongest dissent:** current Grok safety is partly policy-level and its documented headless
  worktree path is behaviorally broken; write support is unacceptable until Batch 4 mechanical
  leases/audits exist.
- **Next move:** stage this plan, then manually use the already-qualified Grok child to implement the
  framework under host audit as the experiment.

### Launch prompt

```text
Start the staged Elves run now. Read the Survival Guide first, then .elves-session.json, learnings,
the plan, execution log, and .ai-docs manifest/linked docs. Set the Stop Gate and continuation guard
to no; export the recorded non-interactive environment; verify plan hash, PR, refs, resources, and
both owned/worker worktrees; align the clean exact Grok successor
9927883a-0203-42e1-a3e4-710a02096d46 to current HEAD before its lease. Stay Cobbler-first. Sol Ultra
owns unusually detailed contracts, risk, acceptance, synthesis, and canonical run documents. Give
Grok one whole substantial batch at a time, targeting 2–5 meaningful detached commits; stream useful
updates, but grant no refs/push/PR/run-memory authority. Audit the complete chain and shared git state,
import only approved binary patches, run focused/full validation, and create/push sanitized visible
host commits recording worker SHAs. Use Luna-low only for optional bounded read-only checks after an
availability probe. Run fresh-host, Fable, and Fugu review concurrently, exclude Grok, require quorum
2, remediate through the same Grok successor, and repeat through all six batches. Never merge. Do not
stop before completion unless the user stops the run or a genuine blocker survives recovery.
```

---

<!-- Add newer entries above Session Setup. Do not rewrite completed chronology. -->
