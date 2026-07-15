# Plan: Adaptive Native Worker Routing

## Mission

Make Elves pleasant to start from either Codex or Claude Code with the subscription and tools a
user already has. The planning/review driver should select a separate, appropriately capable
worker from a small policy surface, remember safe user preferences globally, and hand off one
complete packet without lowering the intelligence of the live driver session or requiring routine
mid-run supervision.

Done means the native path works without Grok, optional Grok Build is discovered and selected
sensibly when allowed, both hosts expose equivalent behavior, and the public documentation starts
with the user flow rather than orchestration internals.

## Planning Classification

- **Execution reasoning:** `medium` — the implementation is mostly deterministic policy, config,
  subprocess, and documentation work, with a few integration choices.
- **Review risk:** `high` — preference precedence, authority boundaries, and worker command grammar
  can create surprising or unsafe behavior if they drift.
- **Worker recommendation for this run:** same exact Sol model as the driver at `medium` reasoning.
- **Terminal review emphasis:** authority cannot be persisted, repo policy beats convenience,
  unavailable providers fail honestly, and Codex/Claude behavior remains equivalent.

## Scope

### In Scope

- A shared machine-global Elves preference file under the XDG config directory, with safe
  precedence, atomic management, provenance, and natural show/set/reset operations.
- Deterministic plan classification and worker routing across host-native and optional Grok Build
  lanes, including execution effort, review risk, advisory driver upgrades, and honest fallback.
- Subscription-native worker profiles and launch plumbing for Codex and Claude Code, plus Grok
  discovery and explicit Composer 2.5 Fast versus Grok 4.5 selection.
- Focused regression tests, installed-bundle parity, changelog/learnings updates, and a simpler
  user-first explanation of the default run.

### Out of Scope

- Changing the host model or reasoning level inside the user's live driver session.
- Claiming prompt-cache transfer between independent worker sessions.
- Persisting merge/destructive/protected-ref authority, credentials, or approval bypasses.
- Building speculative multi-worker parallel lanes, automatic merge, or provider billing logic.
- Publishing a release, moving a tag, globally installing the result, or merging this PR.

## Batches

### Batch 0 [B0]: Safe preferences and routing policy

**Coordinator-to-implementer handoff:**

- **Intent / why:** turn the design note into a small deterministic policy layer whose decisions
  can be explained and tested without invoking a model.
- **Non-obvious rationale:** global preferences are convenience, never authority; explicit run
  intent and repo policy must override them; plan execution reasoning and review risk are separate.
- **Build On targets:** `scripts/cobbler_runtime/config.py`, `schema.py`, `risk_policy.py`, existing
  config CLI/schema/test conventions, and `docs/plans/subscription-native-worker.md`.
- **Owned surfaces:** runtime configuration/routing modules, their CLIs, examples, and focused tests.
- **Forbidden surfaces:** canonical run memory, credentials, protected refs, other worktrees, merge
  or PR operations, and unrelated release/tag state.
- **Acceptance evidence:** isolated-XDG tests demonstrate precedence and safe atomic persistence;
  a decision matrix demonstrates deterministic routes and reasoning recommendations.
- **Failure modes / pitfalls:** do not overwrite unknown fields, leak secrets, let a global setting
  bypass repo policy, or silently treat provider availability as permission.
- **HEAD / run-doc paths / route-session identity / output format:** build on the staged branch tip;
  canonical paths are recorded in `.elves-session.json`; commit concrete slices using the Elves
  subject format and finish with a concise acceptance/evidence report.

**Tasks:**

- [x] Add shared XDG preference discovery, schema/provenance, atomic show/set/reset management, and
  the documented precedence chain.
- [x] Add deterministic execution-reasoning, review-risk, provider, effort, and advisory driver
  recommendation decisions.
- [x] Cover unsafe fields, repo-policy overrides, unknown-field preservation, and isolated home/XDG
  behavior with focused tests.

**Acceptance criteria:**

- [x] B0-A1: Codex and Claude resolve the same versioned global preference file from `${XDG_CONFIG_HOME:-~/.config}/elves/config.json`, while explicit run intent and repository policy take precedence.
- [x] [B0-A2] Preference management writes atomically, preserves supported unknown fields, and rejects credentials, merge/destructive authority, protected-ref authority, and approval-bypass settings.
- [x] B0-A3: A deterministic routing decision reports provider, worker model policy, worker effort, review risk, provenance, and any advisory driver-upgrade recommendation without invoking a model.
- [x] [B0-A4] Focused tests cover native-only, Grok-preferred, unavailable-provider, repo-prohibited, and low/medium/high reasoning cases with isolated global config state.

**Docs likely touched:** configuration reference/example, README, learnings.

**Risk:** `high` — precedence or schema mistakes could turn remembered convenience into unintended authority.
**Caution:** availability, preference, recommendation, and authorization are four distinct concepts.
**Affected surfaces:** configuration resolution, routing policy, schemas, command-line helpers, tests.
**Constitution impacts:** user authority, secret handling, deterministic orchestration.
**Review focus:** safe fields, precedence, atomicity, provenance, no implicit permission.
**Focused tests:** config resolution/CLI and route-policy unit tests.
**Depends on:** none.

### Batch 1 [B1]: Native worker parity and optional Grok Build

**Tasks:**

- [x] Provide equivalent Codex and Claude native worker profiles/launch specifications that inherit
  the current model by default and select plan-matched lower reasoning in a separate session.
- [x] Correct the native CLI command/session grammar needed for supervised launches and record the
  exact worker session identity; never select a "last" or ambiguous session.
- [x] Add silent Grok Build capability discovery, explicit model pinning, one-time preference
  behavior, Composer 2.5 Fast for regular clear work, and Grok 4.5 only for genuinely complex work.
- [x] Verify honest native fallback and capability-qualified goal mode.

**Acceptance criteria:**

- [x] [B1-A1] Codex and Claude expose semantically equivalent native worker profiles that run in a separate session, inherit the current model unless explicitly routed otherwise, and map plan reasoning to a lower worker effort without changing the live driver.
- [x] B1-A2: Native launch commands are covered by grammar fixtures or behavioral tests, capture an exact session identifier, and never rely on ambiguous `--last` continuation.
- [x] [B1-A3] When Grok Build is available and permitted, regular clear implementation explicitly pins Composer 2.5 Fast while genuinely complex execution can explicitly pin Grok 4.5; availability alone never changes authorization.
- [x] B1-A4: Missing, unauthenticated, prohibited, or insufficient Grok capabilities fall back honestly to the native route, and goal mode is claimed only when behaviorally qualified.

**Docs likely touched:** host parity, follow mode, model routing, worker launch references, examples.

**Risk:** `high` — subprocess/session mistakes can target the wrong agent or overstate capabilities.
**Caution:** do not promise cache handoff; do not infer the driver's exact model when the host cannot expose it reliably.
**Affected surfaces:** native dispatch, capability inventory, Grok implementer, agent/profile installation, tests.
**Constitution impacts:** worker isolation, session identity, provider honesty, host parity.
**Review focus:** argv correctness, exact session capture, fallback, explicit Grok model pins, worker authority.
**Focused tests:** native dispatch/isolation, capabilities, Grok routing, installed-bundle parity.
**Depends on:** B0.

### Batch 2 [B2]: Joyful user flow, proof, and documentation parity

**Tasks:**

- [x] Rewrite the public entry path so a user can ask naturally, see the plan/worker recommendation,
  make at most one useful preference choice, watch the worker stream, and receive final review.
- [x] Document natural preference controls, routing/fallback behavior, cache limits, safety boundaries,
  and the distinction between worker completion and driver-owned PR landing.
- [x] Update `SKILL.md`, `AGENTS.md`, references, examples, changelog, learnings, and `.ai-docs` where
  the implementation changes durable architecture or gotchas.
- [x] Run focused tests first, then one cumulative repository verification and parity scan.

**Acceptance criteria:**

- [x] B2-A1: User-facing docs lead with one natural-language flow and explain native default, optional Grok choice, remembered preferences, live worker visibility, and final driver review without requiring users to learn internal route vocabulary.
- [x] [B2-A2] `SKILL.md`, `AGENTS.md`, Claude-facing references, Codex-facing references, examples, and installed artifacts describe equivalent policy and valid host-specific invocation surfaces.
- [x] B2-A3: The changelog and durable learnings record the adaptive native-worker behavior, Composer-versus-4.5 policy, safe global preferences, and the no-cache-handoff limitation.
- [x] [B2-A4] Focused affected-surface tests pass, then one cumulative verification run passes or reports only an explicitly evidenced pre-existing baseline issue.

**Docs likely touched:** README, SKILL, AGENTS, references, CHANGELOG, learnings, `.ai-docs`.

**Risk:** `standard` — documentation can easily drift into a second workflow or expose internals as required user ceremony.
**Caution:** preserve host invocation differences while keeping workflow semantics identical.
**Affected surfaces:** public docs, adapters, examples, sync/install manifests, verification.
**Constitution impacts:** simplicity, host parity, reviewed-PR landing authority.
**Review focus:** user journey, terminology, doc/code parity, no duplicate or contradictory protocol.
**Focused tests:** consistency/doc tests, sync-installed-skills tests, cumulative repository verification.
**Depends on:** B0, B1.

## Master Acceptance

- [x] [M-A1] From either Codex or Claude Code, Elves can plan with the capable live driver, select and launch a separate plan-matched worker using the user's available subscription or permitted Grok Build, remain quiet during execution, and return control for cumulative review.
- [x] [M-A2] Route decisions are deterministic, inspectable, capability-honest, and unable to persist or infer merge, destructive, protected-ref, credential, or approval-bypass authority.
- [x] [M-A3] The implementation, focused tests, installed bundles, README, SKILL/AGENTS adapters, references, changelog, learnings, and architecture documentation agree on the shipped behavior.
- [x] [M-A4] One terminal readiness review of `git diff origin/main...HEAD` finds no unresolved serious issue, and the branch is presented as a reviewed PR without merging.

## Non-Negotiables

- The live planning/review driver is never downgraded in place; implementation runs in a separate
  identified worker session.
- Preferences never grant merge/destructive/protected-ref authority, store credentials, or bypass
  approval and repository policy.
- Provider and model capabilities are reported honestly and unavailable optional routes fall back
  safely.
- Codex and Claude Code have equivalent workflow semantics; only their native invocation surfaces
  may differ.
- The user owns whether Elves may merge; this run ends at a reviewed PR and does not merge.

## Test Strategy

- **During implementation:** run only focused unit/integration tests for touched config, routing,
  dispatch, capability, installation, and documentation surfaces.
- **At batch boundaries:** verify the claimed slice and commit it; do not run an independent driver
  review or the entire suite.
- **Terminal gate:** run one cumulative `python3 scripts/verify_repo.py --version Unreleased`, plus
  any narrowly targeted reruns required by fixes from final review.
- **Known baseline:** the clean starting branch reports the existing public-API approval manifest as
  stale for `2.4.0` and as release-mismatched for `Unreleased`; reconcile it only if final readiness
  requires doing so, and identify it rather than hiding it.

## Notes

- Design foundation: `docs/plans/subscription-native-worker.md`.
- Locally observed optional worker: Grok Build 0.2.101, authenticated, with `grok-composer-2.5-fast`
  as its local default and `grok-4.5` available. Runtime policy must still probe rather than assume.
- This run is itself the first native-flow trial: Sol Ultra plans/reviews and a separate
  `gpt-5.6-sol` worker at `medium` reasoning implements one full packet.
