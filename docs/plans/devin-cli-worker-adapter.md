# Plan: Devin CLI Worker Adapter

## Mission

Add Devin CLI as an optional Elves implementation worker, with SWE-1.7 Lightning as the pinned
first-class model route. Claude Code and Codex remain the only supported main drivers; the host
retains planning, canonical run memory, final review, PR operations, readiness, and merge authority.

## Scope

### In Scope

- A built-in `devin-cli` adapter and onboarding/setup detection that preserves adapter identity.
- Trusted parked full-run execution through Devin CLI, including launch, monitoring, completion,
  failure detection, exact-session recovery, and honest requested/actual model evidence.
- Dedicated-worktree and assigned-feature-branch authority with no worker PR, merge, tag, protected
  ref, or landing authority.
- Focused fixtures and tests covering command construction, lifecycle transitions, resume identity,
  isolation, redaction, and host-owned reconciliation.
- Claude Code/Codex parity documentation, setup recipes, README guidance, and Unreleased changelog.

### Out of Scope

- Supporting Devin as the main Elves driver.
- Making Devin or SWE-1.7 mandatory for native Elves runs.
- Devin Cloud/API orchestration, parallel Devin fleets, or a new generic remote-agent platform.
- Publishing a release, changing the current version, tagging, merging, or modifying global installs.

## Batch 0 [B0]: Devin CLI Worker Integration

**Coordinator-to-implementer handoff:**

- **Intent / why:** let a Codex or Claude driver hand one clear implementation goal to the very fast
  SWE-1.7 Lightning worker, park, and return for one terminal review.
- **Non-obvious rationale:** integrate the `devin` CLI as the harness around SWE-1.7. Do not ask
  Devin to become a second Elves driver or to manage canonical run ceremony.
- **Build On targets:** `cobbler_runtime/adapters.py`, `full_run.py`, `implement.py`, setup/onboarding
  registries, existing Grok full-run fixtures, exact-session helpers, and redaction/authority tests.
- **Owned surfaces:** runtime implementation, focused tests/fixtures, README, SKILL/AGENTS pointers,
  relevant references, examples, and the Unreleased changelog.
- **Forbidden surfaces:** `.git` internals, `main`, remotes, push, PRs, merge/tag operations,
  credentials, global user installs, canonical `.elves` run memory, and unrelated product changes.
- **Acceptance evidence:** focused unit/fixture tests for every criterion plus the canonical repository
  verification appropriate to touched surfaces.
- **Failure modes / pitfalls:** Devin cannot preallocate a session ID; capture and bind the exact new
  session deterministically and never use latest/continue semantics. Keep Grok behavior unchanged.
  Do not treat model-written reports as transport authority. Do not require Devin for native runs.
- **HEAD / run docs / route:** start `c5c64bc65ebf602c8fb77e5acc6f1357903e5e12` on
  `codex/devin-cli-worker`; plan is this file; runtime memory is under ignored `.elves/`; worker
  route is `devin` pinned to `swe-1-7-lightning`; final output should summarize commits, tests,
  unresolved risks, and exact changed surfaces.

**Tasks:**

- Add the adapter, configuration surfaces, and capability detection.
- Extend the parked full-run lifecycle without forking the canonical workflow.
- Add deterministic lifecycle, authority, redaction, and regression tests.
- Update user/operator documentation and the Unreleased changelog.

**Acceptance criteria:**

- [ ] B0-A1: `devin-cli` is a canonical built-in optional worker adapter and never silently degrades
  to `custom-cli`; setup/onboarding reports its availability and pinned model honestly.
- [ ] [B0-A2] A fixture-backed full run can prepare, launch, monitor/await, complete, and reconcile
  through the Devin adapter while existing Grok full-run behavior remains green.
- [ ] B0-A3: Devin session creation and resume bind one exact captured session ID; ambiguous
  latest/continue recovery and cross-worktree session selection are rejected.
- [ ] [B0-A4] Worker authority remains limited to the assigned worktree/feature branch, transport
  evidence is host-derived, secrets are redacted, and PR/merge/tag/protected-ref authority remains
  host-owned.
- [ ] B0-A5: Documentation explains SWE-1.7 Lightning selection, launch/follow/recovery behavior,
  trust graduation, and identical Claude Code/Codex workflow semantics.
- [ ] [B0-A6] Impact-selected adapter/full-run/setup tests and the applicable canonical repository
  gate pass without weakening existing tests or making an optional provider mandatory.

**Docs likely touched:** README, SKILL/AGENTS pointers as needed, model onboarding/setup recipes,
follow/full-run references, and CHANGELOG Unreleased.

**Risk:** standard — lifecycle generalization and session discovery can create subtle recovery or
authority regressions if implemented as Devin-specific shortcuts.
**Caution:** preserve the thin safety kernel and existing Grok path; prefer small adapter-specific
hooks behind provider-neutral lifecycle boundaries.
**Affected surfaces:** adapter registry/builders, full-run state/supervisor, setup/onboarding,
tests/fixtures, docs.
**Constitution impacts:** worker authority, exact identity, protected operations, test integrity,
host parity, and exact-HEAD readiness must remain unchanged.
**Review focus:** exact session provenance, crash/hang handling, command/credential boundaries,
Grok regressions, and truthful docs.
**Focused tests:** adapter/config tests, full-run supervisor fixtures, isolation/redaction tests,
setup/onboarding tests, repository consistency checks.
**Depends on:** none.

## Master Acceptance

- [ ] [M-A1] Codex or Claude can stage a single Elves packet and run Devin CLI with
  `swe-1-7-lightning` as an optional parked implementation worker through terminal reconciliation.
- [ ] [M-A2] The complete diff preserves existing Grok and host-native behavior, Elves authority
  boundaries, exact-session recovery, redaction, and impact-selected verification.
- [ ] [M-A3] User-facing docs and the Unreleased changelog describe the supported route honestly,
  including its optional status and Claude Code/Codex parity.

## Non-Negotiables

- Devin is a worker, never the main driver or landing authority.
- No worker push, PR, merge, tag, protected-ref, credential, or global-install operations in this run.
- Preserve existing Grok and host-native behavior and never weaken tests to obtain green.
- The user owns merge; this run ends with a landable PR and does not merge.

## Test Strategy

Start with focused adapter, setup, and full-run fixture tests selected from the touched modules.
Run the canonical repository verifier once at terminal readiness. Re-review only the revision delta
and unresolved blockers; do not repeatedly rerun unaffected suites.
