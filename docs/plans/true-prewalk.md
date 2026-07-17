# Plan: True Trajectory-Preserving Prewalk

## Mission

Implement exact-session prewalk for Elves' supervised native workers. A guide phase must explore,
create a bounded validation-bearing TODO, make the first meaningful task edit, and then yield to an
execution route that resumes the same provider session in the same worktree with only `Continue.`.
The result must work equivalently for Codex and Claude, preserve all existing driver and Git
authority boundaries, and remain backward compatible with single-phase native workers.

## Planning Classification

- **Execution reasoning:** `high` — this changes subprocess lifecycle, exact-session identity,
  runtime state, routing, capability evidence, recovery, and cross-host command grammar.
- **Review risk:** `high` — a false continuity claim, packet replay, worktree drift, or cold fallback
  would violate the product mechanism and could weaken worker authority boundaries.
- **Work driver:** host-native Codex session; no delegated or paid model calls and no live canaries.
- **Terminal review emphasis:** exact trajectory continuity, fail-closed recovery, retained safety
  controls, single-phase compatibility, documentation honesty, and Codex/Claude parity.

## Scope

### In Scope

- Host-neutral prewalk schemas, prompts, path safety, digests, meaningful-edit validation, stable
  failure codes, state transitions, fidelity, and capability records.
- One shared multi-phase native-worker supervisor with exact Codex and Claude resume, one packet
  send, minimal continuation input, one logical follow stream, recovery, and version-2 compatibility.
- Deterministic routing and safe preference resolution for `off`, `auto`, and `required`, including
  honest capability fallback and conservative rollout.
- Fixture-driven lifecycle tests, host grammar/parity tests, installed-bundle coverage, repository
  consistency checks, canonical documentation, user guide, changelog, TODO, and durable AI docs.

### Out of Scope

- Driver-context transfer, provider cache/KV transfer, copied transcript handoffs, or fresh-session
  approximations described as prewalk.
- Paid behavioral canaries, automatic model-ID discovery, optional external-provider support, or a
  general coordinator/worker ownership partition system.
- Changing PR, merge, tag, protected-ref, landing, canonical-run-memory, or worker push authority.
- Copying or modifying `/Users/john/aigora/dev/elves-explicit-prewalk-handoff`.
- Publishing a release, installing the changed skill globally, merging the PR, or deleting worktrees.

## Batches

### Batch 0 [B0]: Host-neutral contracts and deterministic routing

**Coordinator-to-implementer handoff:**

- **Intent / why:** define the mechanism independently of provider grammar so both hosts share one
  bounded TODO/checkpoint, transition, capability, fidelity, routing, and failure contract.
- **Non-obvious rationale:** prewalk is trajectory continuity after a real edit, not a better packet;
  static help proves advertised grammar only, while behavioral continuity and pruning remain separate.
- **Build On targets:** `cobbler_runtime/schema.py`, `worker_routing.py`, `config.py`,
  `canonical_contract.py`, native-worker Git/path utilities, and existing safe preference precedence.
- **Owned surfaces:** runtime schemas/policy/helpers, config example, focused tests, and plan memory.
- **Forbidden surfaces:** `acceptance_contract.py` ownership expansion, other worktrees, credentials,
  protected refs, PR/merge/tag operations, and unrelated providers.
- **Acceptance evidence:** pure schema, path, meaningful-edit, preference, routing, fallback, and
  capability tests with deterministic fixtures and no model inference.
- **Failure modes / pitfalls:** source-extension allowlists, path escapes, accepting runtime-only
  edits, treating help as behavioral proof, or enabling an unqualified `auto` path.
- **HEAD / run-doc paths / route-session identity / output format:** start at
  `206a625b68bbb42e7fd6e8283ac65945c0f73648`; run docs and session paths are recorded below;
  host-native implementation commits concrete Elves slices and records exact test evidence.

**Tasks:**

- [x] Add provider-neutral prewalk types, constants, schemas, safe artifact paths, prompts, digests,
  and meaningful-edit/transition validation in a focused runtime module.
- [x] Add prewalk preference resolution and separate guide/execution route decisions with provenance,
  capability evidence, fidelity, conservative auto behavior, and honest fallback.
- [x] Add stable canonical language and focused deterministic contract/routing tests.

**Acceptance criteria:**

- [x] B0-A1: Valid bounded TODO and checkpoint artifacts pass, while malformed IDs, missing validation, limits, multiple active items, identity mismatches, and runtime-root escapes fail with stable codes.
- [x] B0-A2: Model-free transition validation accepts task-relevant source, test-first, or product-documentation edits and rejects empty, staging-only, forbidden, outside-worktree, branch, origin, tag, or protected-ref changes.
- [x] B0-A3: Routing reports requested and actual prewalk mode, distinct guide/execution routes, advertised versus behavioral capability facts, instruction fidelity, provenance, fallback, and zero qualification model calls.
- [x] B0-A4: `worker.prewalk` accepts only `off`, `auto`, or `required`, preserves existing safe preference precedence, grants no authority, and leaves unqualified auto runs on the backward-compatible single-phase route.

**Docs likely touched:** config example, canonical contract, plan/run memory.

**Risk:** `high` — shared contracts can silently overclaim continuity or accept a fake boundary.
**Caution:** do not make correctness judgment part of the model-free transition validator.
**Affected surfaces:** prewalk module, provider-neutral schema, routing/preferences, canonical policy, tests.
**Constitution impacts:** provider honesty, safety kernel, user authority, host parity.
**Review focus:** path safety, stable diagnostics, meaningful-edit boundary, qualification truthfulness.
**Focused tests:** new prewalk unit tests and adaptive worker routing tests.
**Depends on:** none.

### Batch 1 [B1]: Exact-session multi-phase supervisor and host parity

**Tasks:**

- [ ] Refactor native-worker execution into a reusable phase runner and add version-3 prewalk state,
  automatic transition, one logical follow stream, and exact-session recovery without cold fallback.
- [ ] Build explicit guide-create and execution-resume specs for Codex and Claude with pinned routes,
  one packet send, `Continue.` resume input, same worktree, and preserved environment/Git protections.
- [ ] Extend the CLI for prewalk launch/status/follow/capabilities while retaining existing off-mode
  arguments and version-2 single-phase behavior.
- [ ] Add deterministic fixture lifecycle tests and table-driven Codex/Claude semantic parity tests.

**Acceptance criteria:**

- [ ] B1-A1: A fixture run traverses staged, guide, transition, execution, and complete states; sends the packet once; resumes with only `Continue.`; retains one exact session/worktree; and exposes both phases in one private follow log.
- [ ] B1-A2: Missing or malformed TODO/checkpoint, session/worktree mismatch, forbidden edits, guide failure, resume failure, and post-edit cold fallback fail closed with stable actionable state while preserving the worktree and logs.
- [ ] B1-A3: Codex uses exact `codex exec resume <session-id>` and Claude uses exact `--resume <uuid>` with explicit guide/execution model and effort, registered CWD/write roots, no ambiguous selector, and no packet replay.
- [ ] B1-A4: Tiny all-complete tasks may terminalize after the guide only when the task-complete checkpoint and normal completion/Git contracts hold; otherwise a zero guide exit means transition-ready, not complete.
- [ ] B1-A5: Existing version-2 and single-phase native-worker launch, status, follow, redaction, environment scrubbing, sandboxing, PID identity, Git authority, and no-push tests remain green.

**Docs likely touched:** none beyond execution evidence until the mechanism is proven.

**Risk:** `high` — supervisor regressions can lose identity, corrupt state, replay authority, or misreport completion.
**Caution:** transition is automatic and model-free; the driver must not review the first edit.
**Affected surfaces:** native-worker runtime, CLI parser/commands, lifecycle fixtures, host grammar tests.
**Constitution impacts:** exact-session safety, protected refs, credential isolation, worker authority.
**Review focus:** state ordering, atomic writes, child cleanup, redaction, session/CWD equality, fail-closed recovery.
**Focused tests:** native-worker prewalk, adaptive routing native lifecycle, worker CLI lifecycle.
**Depends on:** B0.

### Batch 2 [B2]: Documentation, installation parity, and terminal readiness

**Tasks:**

- [ ] Update the normative prewalk reference, SKILL, thin AGENTS adapter, README, host parity,
  adaptive routing, E2E/schema references, guide, changelog, TODO, learnings, and `.ai-docs`.
- [ ] Add both host help fixtures and installed Codex/Claude bundle coverage for the runtime and docs.
- [ ] Run focused proof, consistency checks, the canonical repository verifier, cumulative diff review,
  any consolidated fixes, and exact-tip readiness checks.
- [ ] Push `codex/true-prewalk` and open a new unmerged PR with the required mechanism, parity,
  instruction-fidelity, test, and rollout explanation.

**Acceptance criteria:**

- [ ] B2-A1: Canonical and user-facing docs define prewalk only as exact-session guide-to-execution trajectory continuity, explicitly reject cold handoffs, preserve driver authority, and give equivalent valid Codex/Claude operations.
- [ ] B2-A2: Documentation reports retained-safe versus pruned instruction fidelity honestly, states that static help is not behavioral proof, and keeps automatic rollout disabled or conservative until both hosts are live-qualified.
- [ ] B2-A3: Fresh installed Codex and Claude bundles contain the prewalk runtime/reference and pass smoke checks without source-checkout-only helper dependencies.
- [ ] B2-A4: Focused suites, repository consistency, `git diff --check`, and `python3 scripts/verify_repo.py --version Unreleased` pass on the reviewed exact tip.
- [ ] B2-A5: The new PR is open against `main`, unmerged, and its description explains the old cold-handoff gap, implemented exact-session mechanism, parity evidence, pruning fidelity, verification, and remaining live qualification/rollout limits.

**Docs likely touched:** all specification-required canonical, user, release, and durable AI-facing docs.

**Risk:** `standard` — documentation and bundles can drift from the implemented CLI and capability gates.
**Caution:** `AGENTS.md` stays a thin Codex adapter; normative semantics live in SKILL/references/code.
**Affected surfaces:** documentation, guide, installer/smoke manifests, consistency checks, PR metadata.
**Constitution impacts:** host parity, operator honesty, reviewed-PR landing authority.
**Review focus:** terminology, CLI examples, bundle completeness, no overclaim, residual rollout limits.
**Focused tests:** installed bundle smoke, consistency, canonical repository verifier.
**Depends on:** B0, B1.

## Master Acceptance

- [ ] M-A1: One supervised native-worker run can begin on an explicit guide route, create a bounded validation-bearing TODO, make a meaningful edit, and automatically resume the exact same provider session and worktree on an explicit execution route with only `Continue.`.
- [ ] M-A2: The packet is sent exactly once; fresh sessions, copied transcripts, progress-note handoffs, ambiguous resume selectors, and post-edit cold fallback are never accepted or described as prewalk.
- [ ] M-A3: Codex and Claude provide equivalent state, route, TODO/checkpoint, follow, recovery, failure, installation, and documentation semantics with fixture-backed tests and honest behavioral-qualification limits.
- [ ] M-A4: Existing single-phase workers and all environment, redaction, sandbox, Git-authority, protected-ref, PR, merge, and terminal-review contracts remain intact.
- [ ] M-A5: The exact final tip passes focused and canonical verification, cumulative review finds no unresolved serious issue, and an unmerged PR is ready for human review.

## Non-Negotiables

- Prewalk always means exact provider-session and worktree trajectory continuity; never a cold handoff.
- The first meaningful edit transitions automatically without driver review or approval.
- No paid/live behavioral canary or external model call is made in this run.
- Codex/Claude parity is a release gate, and unqualified behavior fails or falls back before launch.
- Existing worker/driver Git, PR, merge, protected-ref, credential, and terminal-review authority remains unchanged.
- The user owns merge; this run opens an unmerged PR and stops there.

## Test Strategy

- **Focused contracts:** `python3 -m unittest tests.test_native_worker_prewalk`
- **Focused routing:** `python3 -m unittest tests.test_adaptive_worker_routing`
- **CLI/session grammar:** `python3 -m unittest tests.test_cobbler_agents_sessions tests.test_worker_cli_lifecycle`
- **Installation/consistency:** installed-bundle smoke tests and `python3 scripts/check_repo_consistency.py`
- **Terminal gate:** `python3 scripts/verify_repo.py --version Unreleased` while the branch carries
  Unreleased changes; use a numeric version only if the branch is promoted to that release.
- **Read-only host grammar:** version/help commands and committed deterministic fixtures only.
- **Known flaky tests:** none declared; never weaken or skip tests for green.

## Notes

- Authoritative specification: `/Users/john/Desktop/Elves-True-Prewalk-Redesign.md`.
- Primary conceptual source: `https://stencil.so/blog/prewalk`; the local specification is the
  implementation and acceptance contract and was reconciled against current `origin/main`.
- The source and target baseline are both `206a625b68bbb42e7fd6e8283ac65945c0f73648`.
- No constitution file exists in this repository; legality is unchanged unless one appears.
