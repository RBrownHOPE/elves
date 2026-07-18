# Explicit handoff contract cleanup

## Intent

Reconcile the unpublished `codex/explicit-prewalk-handoff` work with Elves v2.8.0. Preserve the
useful machine-readable handoff validation without changing the established advisory behavior for
otherwise valid delegated sessions that have not opted into the new schema.

## Scope

- Acceptance staging validation for explicitly declared coordinator-to-worker handoff state.
- Equivalent Markdown and JSON worker-packet coverage.
- Exact repository/session/packet identity and acceptance-ownership checks.
- Canonical workflow, schema, changelog, and durable architecture documentation.

## Non-goals

- Changing exact-session prewalk routing or claiming that a packet capsule proves trajectory
  continuity.
- Replacing the full-run supervisor's existing immutable plan/session/packet binding.
- Requiring legacy or ordinary v2.8 delegated sessions to adopt the explicit schema.
- Merging without separate user authorization.

## Decisions

- The existing missing-`worker_packet_path` diagnostic remains advisory and exit-zero.
- A session opts into strict handoff v1 validation by declaring a top-level `handoff` field. Once
  declared, malformed or drifting handoff state blocks staging.
- Markdown packets carry a leading `elves-handoff-v1` comment capsule; JSON packets carry the same
  capsule as top-level `elves_handoff`.
- The capsule is handoff-state evidence only. A cold handoff remains non-prewalk, and exact-session
  prewalk still depends on the native-worker continuity proof in `references/prewalk.md`.

## Batch 0 [B0]: Reconcile and harden explicit handoff v1

**Intent / why:** Convert the speculative unpublished implementation into a compatible, documented,
and reviewable extension of the v2.8 staging contract.

**Non-obvious rationale:** Existing runtime launch code already binds plan/session/packet acceptance
identity. This batch adds prelaunch state/ownership validation only when the coordinator explicitly
declares it, avoiding a surprise breaking migration.

**Build On targets:** `scripts/acceptance_contract.py`, shared stable-ID grammar in
`scripts/cobbler_runtime/acceptance.py`, full-run packet formats, and
`references/schema-and-acceptance.md`.

**Owned surfaces:** acceptance validator/tests; handoff/schema/workflow docs; changelog and durable
architecture guidance.

**Forbidden surfaces:** exact-session prewalk supervisor semantics, provider launch authority,
protected refs, the main checkout, other worktrees, credentials, release tags, and merge authority.

**Acceptance evidence:** Focused validator tests, full repository verification, cumulative diff
review, exact-tip CI, and PR feedback.

**Failure modes / pitfalls:** Accidentally blocking v2.8 sessions, accepting a capsule away from the
packet start, Markdown/JSON drift, fake/non-ancestor checkpoint commits, or implying that packet
state establishes prewalk continuity.

**HEAD / run-doc paths / route-session identity / output format:** Start from `6dff595`; this plan,
`docs/elves/survival-guide-explicit-handoff-cleanup.md`,
`docs/elves/execution-log-explicit-handoff-cleanup.md`, and `.elves-session.json` are authoritative;
host-native implementation; finish at a landable PR without merge.

### Tasks

- [x] Reconcile the validator with v2.8 compatibility and both canonical packet formats.
- [x] Expand category tests for compatibility, exact identity, ownership, bounds, and format parity.
- [x] Update canonical and durable documentation without redefining prewalk.
- [x] Promote the additive public contract as release-ready v2.9.0 source and guide metadata.
- [x] Complete broad verification, cumulative review, PR checks, and operational cleanup.

### Acceptance criteria

- [x] B0-A1: Delegated sessions that do not declare explicit handoff v1 retain the v2.8 advisory-only missing-packet behavior and exit successfully when all established acceptance checks pass.
- [x] B0-A2: A declared handoff v1 accepts exact fresh-start and resume state while blocking malformed state, ownership gaps/overlap, repository identity drift, and non-ancestor completed-slice commits.
- [x] B0-A3: Markdown and JSON worker packets carry equivalent handoff state and acceptance mappings; capsule placement and packet size are bounded and fail closed with stable diagnostics.
- [x] B0-A4: SKILL, schema/template guidance, changelog, and durable architecture/gotcha docs describe the opt-in boundary and explicitly preserve exact-session prewalk semantics.
- [x] B0-A5: Focused acceptance tests and the repository's broad terminal verification pass on the current branch tip.
- [x] B0-A6: Source metadata, changelog, README examples, public guide, and the release checklist agree on v2.9.0 with an empty Unreleased section.

**Docs likely touched:** `SKILL.md`, `CHANGELOG.md`, `README.md`, `guide/index.html`, `references/schema-and-acceptance.md`,
`references/plan-template.md`, `.ai-docs/architecture.md`, `.ai-docs/context-index.md`,
`.ai-docs/gotchas.md`, and `docs/elves/learnings.md`.

**Risk:** standard — the validator is a staging safety boundary, but the feature remains opt-in.

**Caution:** Keep current advisory compatibility and do not duplicate or weaken full-run launch
binding.

**Affected surfaces:** staging validation, worker-packet parsing, workflow documentation, installed
bundle contents.

**Constitution impacts:** none; the repository has no conflicting constitution.

**Review focus:** compatibility for undeclared sessions, exact packet parsing, Git identity proof,
stable diagnostics, and prewalk wording.

**Focused tests:** `tests/test_acceptance_contract.py`, `tests/test_check_repo_consistency.py`,
installed-bundle smoke, and terminal `scripts/verify_repo.py --ci`.

**Depends on:** v2.8.0 merge commit `34bb785` and unpublished commit `2cd7349`.

## Master Acceptance

- [x] M-A1: The cumulative diff preserves every established v2.8 staging/prewalk invariant while making explicitly declared handoff state deterministic and fail-closed.
- [x] M-A2: The acceptance-bearing branch tip is current, documented, broadly verified, clean, and presented as a reviewable PR.

Operational run artifacts remain a required final task, but are deliberately outside acceptance:
Elves proves and records the acceptance-bearing tip before removing those artifacts in a separate
post-readiness commit.
