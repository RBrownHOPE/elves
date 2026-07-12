# Execution Log: Cobbler External-Agent Orchestration

This is the reverse-chronological proof log for the v1.20.0 run. Keep raw chronology here; keep live
control in the Survival Guide; promote stable lessons to `docs/elves/learnings.md` and `.ai-docs/*`.

## Run Digest

- **Last updated:** 2026-07-12 08:39 EDT
- **Current phase:** Staging
- **Active batch:** Batch 0: qualification and run scaffolding
- **Last completed batch:** none; implementation has not started
- **Next exact batch:** Batch 1: Contracts, configuration, and implementer clarity
- **Active PR:** not created yet
- **Docs promoted this run:** qualification lessons added to `docs/elves/learnings.md`
- **Latest Elves Report:** not generated

## Batch 1 Contract: prepared during staging on 2026-07-12

This contract is deliberately detailed for the persistent Grok implementation child. The host must
re-read current source and update only facts that changed before issuing the worker lease; it must not
reduce the contract to a short chat prompt.

**Behaviors:**

- Define generic, validated harness/capability/role/fallback contracts without hardcoding this user's
  current model names as public defaults.
- Resolve active routes from committed Survival Guide snapshot -> local ignored
  `.elves/models.toml` -> installed/user `config.json` -> native host default, preserving source
  provenance and explicit `required` semantics. Ship `references/models.toml.example` as the
  reviewable schema; never treat the local file as team-shared or stage it.
- Provide a thin `scripts/cobbler_agents.py` operator CLI with non-mutating `validate-config` and
  `doctor --json` foundations; no external model inference or worker write in this batch.
- Add the coordinator-to-implementer handoff standard to both canonical skill surfaces, relevant
  templates/reviewer guidance, consistency checks, and tests.
- Add the git-history-as-operator-UI standard to the same canonical/template/checker surfaces: the
  host pushes meaningful branch/batch/phase/outcome commits during a batch, and workers never commit.
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
  `.elves-session.json`, `.elves/**`, `.git/**`, commits, tags, pushes, PRs, or installed global skills.
- **No secrets:** examples may name environment variables; no key/token/auth value or private local
  path belongs in product config/docs/tests.
- **No brittle implementation prescription:** use standard-library Python and current repo test
  patterns; if a planned module split is inferior after survey, report the evidence and proposed
  alternative before diverging.
- **Commit milestone:** the host, not Grok, will audit/import each reviewable slice and use
  `[codex/external-agent-orchestration · Batch 1/6 · <phase>] <concrete outcome>`. Grok must not run
  git; `Close` is unavailable until every acceptance row has evidence.
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
      subject schema, external-worker git denial, and acceptance-backed `Close`; consistency tests
      reject drift and vague example subjects.
- [ ] Focused tests and full baseline pass; test count is at least 170 with none newly skipped.
- [ ] Consistency, compile, shell syntax, JSON, TOML validator, sync fixture, and whitespace gates pass.
- [ ] `.elves-session.json` receives non-empty criterion/met/evidence rows before status becomes
      complete.

**Blast radius:**

- Canonical skill/config/template/checker/install surfaces have many downstream consumers.
- Nature: additive foundation plus behavior wording change.
- Risk: high. A bad contract or drifted mirror contaminates every later batch and installed skill.

**Phase routing:**

- Requested route: persistent Grok child `019f5644-93d5-7a02-827d-caa8b30a2825` under one
  detached writer lease
- Actual route: pending launch
- Fallback: diagnose/repair Grok before any switch; Claude Code Opus then host are recorded fallbacks
- Automatic implementation fallback: disabled; after three distinct failed Grok recovery attempts,
  stop for the user rather than silently switching providers
- Validation/synthesis/docs/git: host coordinator
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

## Implementation Route Decision: 2026-07-12 09:06 EDT

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

**Owned worktree:** `/Users/john/aigora/dev/elves-external-agent-orchestration`

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
- baseline tests: 170 passed in 8.925s; consistency, py_compile, JSON, and diff checks passed
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

- Git remote/push/`gh` auth: previously PASS; rerun from owned worktree before readiness
- Validation gate dry run: baseline PASS; rerun after staging docs/session state
- Environment/sleep/notification: local desktop; no Slack webhook required; PR comment fallback
- Install doctor: no advisory output at startup
- Launch readiness: pending docs validation, commit/push, draft PR, PR/check poll, and Grok child
  alignment to final staged tip

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

Pending final PR number, staged tip, preflight results, and readiness checklist. It will be written
into this entry before staging closes and repeated in the final handoff.

---

<!-- Add newer entries above Session Setup. Do not rewrite completed chronology. -->
