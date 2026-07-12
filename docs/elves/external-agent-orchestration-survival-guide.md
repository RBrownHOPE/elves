# READ THIS FILE FIRST AFTER ANY COMPACTION OR RESTART

This Survival Guide is the authoritative live brief for the v1.20.0 Cobbler external-agent
orchestration run. If chat memory conflicts with this file, trust this file and canonical disk state.

Read order after restart: this file -> `.elves-session.json` -> `docs/elves/learnings.md` -> plan ->
execution log -> `.ai-docs/manifest.md` and linked docs -> `TODO.md`.

## Mission

Build and qualify a generic external-agent runtime for Cobbler that can plan and review in parallel,
delegate one bounded implementation task to a persistent qualified worker, audit/import its patch,
and repeat. Ship native-only fallback, configurable roles/fallbacks, Claude/Codex parity, setup
helpers, exact session lineage, a safe Grok writer path, and a master CouncilElves launch prompt.

The current Fable 5, Grok 4.5, and Fugu Ultra setup is the live experiment, not a hardcoded public
default. The host coordinator owns all run memory, git, PRs, validation, acceptance, synthesis, and
final integration.

## Run Control

- **Run mode:** finite
- **Stop policy:** plan-complete-or-true-blocker
- **User intent:** “Test everything before we code it up. Once all the pieces work, plan and stage an
  Elves run; then get Grok Build to do it as an experiment.” The user also requires the main smart
  coordinator to write detailed Elves documents for a potentially less-capable implementation agent.
- **Checkpoint due by:** none; assume an approximately 8-hour execution budget from the fresh launch
  call because no return time was specified
- **Checkpoint semantics:** none
- **May continue after checkpoint:** yes; there is no checkpoint stop
- **Actual stop conditions during execution:** all six batches complete with clean Final Readiness
  Review; explicit user stop; or a genuine blocker with no reasonable in-scope workaround
- **Workspace ownership:** owned branch `codex/external-agent-orchestration` in dedicated worktree
  `/Users/john/aigora/dev/elves-external-agent-orchestration`; no other agent may edit this checkout
- **Branch tip at start (collision tripwire):** `74c52d88868e39a9d4c5cca6dee46919011d2127`
- **External worker checkout:** clean detached Grok worktree
  `/Users/john/.grok/worktrees/dev-elves/2026-07-12-e8fa7ada`; never attach it to the owned branch;
  align it to the current owned tip only under host control and only while clean
- **Merge policy:** user-merges; never merge, squash, rebase, publish a release, or push a release tag
  without a later explicit opt-in
- **Final-response policy:** staging-only response is allowed after launch readiness; once launched,
  final response is disallowed until plan completion or a true blocker
- **Coordination mode:** Cobbler-first; independent lenses for non-trivial planning, contract, risk,
  debugging, review, and synthesis; host owns git/docs and delegates only scoped worker edits
- **Batch completion rule:** update execution log -> update Survival Guide/session JSON -> commit ->
  push -> re-read this file. Never begin a later batch with completed work only in the working tree.
- **Re-read rule:** immediately after every commit and push, read this file before any other action.
- **Checkpoint rule:** if a later checkpoint is marked delivery-only, log it, push it, and continue;
  a checkpoint is never a stop unless this Run Control block explicitly makes it a hard boundary.
- **Continuation rule:** once launched, if work remains and no actual stop condition applies, continue
  without waiting for user acknowledgment.
- **Staging boundary:** this call must stop after the branch/PR/docs/preflight are launch-ready. The
  next fresh call changes the Stop Gate to `no` and begins Batch 1.

## Cobbler Session State

- **Cobbler default:** on
- **Activated by:** explicit Elves invocation and user request for a planning/review council
- **Scope:** current Elves run
- **Behavior:** treat follow-up run prompts as Cobbler-mediated; answer simply when simple, fan out
  independent read-only lenses when useful, and delegate only bounded worker tasks
- **Persistence:** this Survival Guide and `.elves-session.json`
- **Exit phrases:** “Cobbler Mode: off”, “leave Cobbler Mode”, “stop using Cobbler by default”

## Session Budget

- **Staging started:** 2026-07-12 08:39 EDT
- **Execution starts:** only from the user's next fresh launch call
- **User returns:** approximately 8 hours after launch unless the user gives a different time
- **Checkpoint expectation:** review-ready progress within the finite budget; no artificial stop at
  eight hours if the finite plan can be safely completed in the same active run
- **Time budget:** approximately 8 hours from launch, with judgment to complete a near-finished finite
  batch or stop cleanly at a genuine hard boundary
- **Average batch time so far:** N/A; execution not launched
- **Batches remaining:** 6 of 6

## Stop Gate

- **Planned batches remaining:** 6
- **Stop allowed right now:** yes
- **Why:** this is the explicit staging-only call; implementation must begin from a fresh launch call
- **Next required action:** user sends the prepared launch prompt; host sets this gate to `no`, aligns
  the clean Grok child worktree to the staged tip, and writes the Batch 1 contract

## Effort Standard

- Work as hard as you can for the full execution run. Do not be lazy or coast after the first green
  result.
- Maintain equal effort in implementation, validation, and review, including the final batch.
- Do not settle for the minimum acceptable change or superficial docs-only completion when runtime
  behavior or live qualification remains.
- When one task is complete, take the next highest-value action from the plan or review queue.

## Forbidden Stop Reasons

Once launched, none of these permits stopping while work remains:

- A checkpoint or estimated return time arrives without being an explicit hard stop.
- A commit or push succeeds, the PR exists, or CI turns green.
- A useful summary or Elves Report has been written.
- One batch finishes while later batches remain.
- An optional provider falls back successfully.
- The user is silent or the remaining scope feels large.

Use the Stop Gate, not intuition.

## Coordinator-to-Implementer Handoff Standard

The host coordinator is assumed to be smarter and to hold more context than the external worker.
Before every worker turn, the host writes a task packet that can stand alone after compaction and
contains:

1. product intent and why the batch exists;
2. non-obvious architecture/rationale and the exact existing patterns/utilities to build on;
3. owned files/modules and explicit forbidden surfaces;
4. observable behaviors and acceptance evidence, not “make tests green”;
5. forbidden shortcuts, especially duplicate utilities, prompt-only safety, git ownership, and
   hardcoded provider/model assumptions;
6. likely failure modes, tool/version gotchas, and recovery behavior;
7. current HEAD, plan/run-document paths, context digest, route/model/session identity, and output
   format.

The packet should not prescribe brittle line-by-line code. The worker must survey current source,
use judgment inside the contract, and report uncertainty. Reviewers must treat an incomplete or
chat-dependent handoff as a blocking coordinator defect before implementation begins.

Canonical run documents are host-only: this plan, this Survival Guide, the execution log, learnings,
and `.elves-session.json`. A Grok worker may edit product docs such as `SKILL.md`, `AGENTS.md`, README,
or references only when the batch contract assigns them.

## Memory Surfaces

- **Plan:** `docs/plans/v1.20.0-cobbler-external-agent-orchestration.md`
- **Survival Guide:** this file; live run controls and next exact batch
- **Learnings:** `docs/elves/learnings.md`; stable reusable lessons only
- **Execution log:** `docs/elves/external-agent-orchestration-execution-log.md`; chronology and proof
- **Structured state:** `.elves-session.json`; batch/route/continuation/acceptance state
- **Local external-agent state:** `.elves/runtime/`; ignored capability/session/lease/transcript state
- **Durable repo docs:** `.ai-docs/*`; curated architecture, conventions, and gotchas

Promotion flow: execution log -> learnings -> `.ai-docs/*`.

## Strategic Forgetting

- Rewrite the live sections in this file; do not append stale status blocks.
- Keep chronology in the execution log and archive older entries when it becomes noisy.
- Promote stable external-agent behavior to learnings/`.ai-docs`; keep personal IDs and transient
  usage in local runtime/session state.
- Stop idle child processes and paid work after each turn. On-disk sessions and clean worktrees are
  not active compute.
- Write a concise reactivation handoff if a fresh chat would be faster.
- Never delete unrelated Codex/Claude/Grok app state, sessions, databases, worktrees, skills,
  plugins, or automations.

## Non-Negotiables

- Host owns run documents, validation, acceptance, git, PR, synthesis, and patch integration;
  external workers never commit/push/merge/own PRs/edit run memory.
- Native-only behavior remains complete; external tools/keys are optional by default, and only this
  project Survival Guide may mark a route required.
- No credential values enter config, prompts, logs, git, or child environments; write routes fail
  closed when capability, scope, session, CWD, or worktree is not verified.
- Read-only council lanes launch concurrently and independently; only one external writer lease is
  active; the implementation agent is excluded from independent review quorum.
- Every coordinator packet is executable by a less-capable/context-poor worker and every behavior
  change keeps `SKILL.md`, `AGENTS.md`, templates/docs/config, sync surfaces, consistency checks, and
  tests aligned.
- Never merge, squash, rebase, force-push, publish a release, or push a release tag in this run.

## Launch Readiness

- [x] Plan cleaned, detailed, and saved
- [x] Survival Guide current
- [x] Existing learnings refreshed with stable qualification facts
- [x] Execution log initialized with six batches and qualification evidence
- [x] Branch created in a dedicated staging worktree
- [x] Branch tip/collision tripwire recorded
- [ ] Draft PR opened and recorded
- [ ] Preflight and all baseline gates pass in the staging worktree
- [x] `.elves-session.json` validates and records Cobbler, routes, sessions, test baseline, and guard
- [ ] Stop Gate initialized with `Stop allowed right now: no` in the fresh launch call; staging uses
      an explicit `yes` boundary so this preparation call can hand off cleanly
- [ ] Clean Grok child worktree/session recorded and aligned to final staged tip
- [x] No active paid/long-running process remains ambiguous
- [ ] Short next-call launch prompt prepared

## Current Phase

**Status:** Staging

**Active batch:** Batch 0: qualification and run scaffolding

**What was just finished:** All connector, host-parity, persistent-lineage, isolated-write, and
independent-review qualification gates passed; the parallel staging council closed its two findings;
the dedicated worktree, detailed plan, and run memory pass all baseline gates including 170 tests.

**Single next action:** finish run memory, validate/stage/push Batch 0, open the draft PR, run
preflight, align the clean Grok child worktree to the staged tip, and prepare the launch prompt.

## Active Compute

No paid inference or long-running process is active at staging close. These are on-disk persistent
resources only:

| Resource | Purpose | Current status | Last verified | Stop / repurpose trigger |
| --- | --- | --- | --- | --- |
| Fable session `02bb9552-fbbd-423f-abbe-acbaa580c918` | planning/contextual review/Claude host | idle; no process | 2026-07-12 | resume exact ID for a bounded turn; record usage afterward |
| Grok parent `159e611b-6c48-4376-8695-5134b9803b7e` | planning lineage | idle; no process | 2026-07-12 | read-only planning only; never source-checkout writes |
| Grok child `019f5644-93d5-7a02-827d-caa8b30a2825` | required first implementation worker | idle; clean detached worktree | 2026-07-12 | align to staged tip before launch; one lease at a time |
| Fugu Ultra `019f5627-e61e-72a3-af3f-ae6e51a348b5` | planning/contextual review | idle; no process | 2026-07-12 | resume exact ID read-only; inherited MCP warnings are separate health |

## Next Exact Batch

**Batch:** 1: Contracts, configuration, and implementer clarity

**Scope:**

- Host writes the complete Batch 1 contract and worker packet from the plan.
- Grok child implements typed harness/config/capability foundations and the handoff standard only
  inside its detached lease checkout.
- Host audits/imports the patch, runs focused and full tests, and updates product docs/run memory.
- Fresh native review + Fable + Fugu review in parallel; remediate through the same Grok child.

**Acceptance criteria:** use every Batch 1 criterion in the plan, with non-empty evidence rows in
`.elves-session.json`; do not close on tests alone.

**Risk:** provider-specific assumptions or a weak config contract becoming the foundation
for every later batch.

**Rollback tag:** `elves/pre-batch-1`

## Post-Checkpoint Control Loop

Every completed batch must end with a commit and push. Immediately after every commit and push,
re-read this survival guide before doing anything else.

After every commit and push during execution:

1. re-read this file;
2. confirm the owned branch tip and remote tip match the host's commit;
3. reconcile child processes, leases, sessions, and worktrees;
4. update route/fallback and usage evidence;
5. answer which unfinished batch starts now;
6. ask: does the Stop Gate still say `Stop allowed right now: no`? If yes, continue immediately;
7. stop only when this guide positively permits it or a true blocker applies.

## External Writer Lease Policy

- Exactly one lease, one exact session ID, one detached registered worktree, and one allowed path set.
- The host pauses edits to leased surfaces while the worker runs.
- Pre-turn evidence: clean status, HEAD, full refs digest/snapshot, CWD/worktree registration, model,
  session parent/child, context digest, policy profile, environment names, and process baseline.
- Worker may edit/test only assigned product files. It may not edit `.git`, `.elves`, this plan,
  Survival Guide, execution log, learnings, session JSON, credentials, aliases outside scope, or
  another worktree.
- Post-turn evidence: process exit, status/staging, HEAD, refs, changed paths, symlinks, patch, and
  forbidden path checks.
- Any commit, ref change, out-of-scope path, ambiguous CWD, wrong model/session, or audit failure
  rejects the patch and stops that lease. Do not route around a denial.
- Host applies accepted patch to the owned branch, validates, reviews, writes run state, commits,
  pushes, then refreshes the clean worker worktree to the new tip under host control.

## Configuration Ownership for This Run

- Installed/user `config.json` supplies machine defaults and is not setup-owned project state.
- `.elves/models.toml` is an intentionally ignored local-checkout preference file. Setup may update
  it, but neither the host nor a worker may stage or commit it.
- `references/models.toml.example` is the future tracked schema/example delivered by Batch 1; it
  contains no machine paths, model credentials, or personal choices.
- This Survival Guide is the committed, reviewable snapshot of the effective routes and their
  provenance for this run. If local preferences change, the host rewrites this block before using
  the new route.
- No source-controlled team preference is inferred. A team may define one separately, but ordinary
  Elves remains native-first and this run still records the resolved routes here.

## Model Routing for This Run

This block is an explicit project override, not a shipped default:

```yaml
model-routing:
  enabled: true
  policy: qualified-capability-first
  fallback: host-native
  document-owner: host-coordinator
  phases:
    planning:
      preference: parallel-independent-council
      routes:
        - host-coordinator
        - claude-code:claude-fable-5
        - grok-build:grok-4.5-parent
        - codex-fugu:fugu-ultra
      required: false
    implement:
      preference: grok-build:grok-4.5-child
      exact-session: 019f5644-93d5-7a02-827d-caa8b30a2825
      worktree: /Users/john/.grok/worktrees/dev-elves/2026-07-12-e8fa7ada
      required: true
      fallback-chain:
        - claude-code:opus
        - host-coordinator
      fallback-policy: diagnose-and-record-before-switching
    validate:
      preference: host-coordinator
      required: true
    review:
      preference: parallel-independent-council
      routes:
        - fresh-host-native-reviewer
        - claude-code:claude-fable-5
        - codex-fugu:fugu-ultra
      exclude:
        - grok-build:grok-4.5-child
      required: true
      required-quorum: 2
      quorum-policy: block-below-required-after-recovery
    synthesize:
      preference: host-coordinator
      required: true
```

This run explicitly requires two successful independent review reports, and a fresh host-native lane
counts as one. An individual optional external lane may fail if another independent fallback still
meets the quorum. If fewer than two reports remain after recovery/fallback, the review phase is
blocked; do not call the work council-verified. This project override does not change the shipped
default, where an advisory target quorum may degrade to host synthesis with a recorded confidence
drop. If any other required route fails after three distinct recovery attempts and no safe workaround
exists, log the exact blocker and stop for the user.

## Tool Configuration

The normal consistency/test/compile/shell/JSON/whitespace gates are active during staging. The
`toml` gate activates after Batch 1 creates `scripts/cobbler_agents.py` and is required before Batch 1
closes. The evidence-enforcing `landing` form activates once the first batch has acceptance evidence;
its absence during staging is not a preflight failure.

```yaml
lint: python3 scripts/check_repo_consistency.py
typecheck: python3 -m py_compile scripts/*.py tests/*.py
build: bash -n scripts/preflight.sh scripts/notify.sh
test: python3 -m unittest discover -s tests -p 'test_*.py' -v
json: python3 -m json.tool config.json.example
toml: python3 scripts/cobbler_agents.py validate-config
whitespace: git diff --check
landing: python3 scripts/elves_landing_check.py --session .elves-session.json --plan docs/plans/v1.20.0-cobbler-external-agent-orchestration.md --execution-log docs/elves/external-agent-orchestration-execution-log.md --evidence-root .elves/evidence --require-evidence-dirs
review: github-pr-comments plus fresh-host/Fable/Fugu parallel council for medium/high-risk batches
notification: gh-pr-comment
```

### Public API surface snapshot

```yaml
api-surface-snapshot:
  enabled: auto
  required: false
  baseline-path: .elves/api-surface/baseline.json
  current-path: .elves/api-surface/current.json
  diff-path: .elves/api-surface/diff.md
  sources:
    cli: auto
    config: auto
  policy:
    unavailable-source: warning
    additive-change: info
    intentional-breaking-change: requires-plan-note
    unexpected-breaking-change: blocking
```

CLI/config snapshots are shape evidence only and do not replace tests or review.

## Documentation Triggers

- Runtime architecture -> `.ai-docs/architecture.md`
- Stable coordinator/handoff/ownership rules -> `.ai-docs/conventions.md`
- CLI/version/session/sandbox traps -> `.ai-docs/gotchas.md`
- User-visible setup/routes -> README, `docs/cobbler.md`, config and reference docs
- Behavior changes -> both `SKILL.md` and `AGENTS.md`, templates, consistency checks, tests
- Reusable run discovery -> `docs/elves/learnings.md`
- Raw chronology and personal session evidence -> execution log/local runtime state

## Acceptance Checks Before Any Batch Closes

- [ ] Plan criterion has direct evidence, not only a green umbrella test
- [ ] `.elves-session.json` acceptance rows are non-empty and `met: true`
- [ ] Focused and full gates pass; test total does not decrease
- [ ] Worker pre/post lease audit passes and host imported the patch
- [ ] Shared surfaces and consumers were traced
- [ ] External route requested/actual/fallback and observed usage recorded
- [ ] Independent review council excludes the implementer and has no blocking finding
- [ ] PR feedback queue is read and dispositions recorded
- [ ] Docs and durable memory are current
- [ ] Batch closes in one commit/push; Survival Guide is re-read immediately
- [ ] Rollback tag existed before implementation

## Evidence Layout

Ignored evidence root: `.elves/evidence/`

```text
.elves/evidence/
  qualification/
  batch-1/{lint,typecheck,build,test,review,lease-audit}
  batch-2/{lint,typecheck,build,test,review,lease-audit}
  batch-3/{lint,typecheck,build,test,review,lease-audit}
  batch-4/{lint,typecheck,build,test,review,lease-audit}
  batch-5/{lint,typecheck,build,test,review,lease-audit}
  batch-6/{lint,typecheck,build,test,review,lease-audit}
```

Do not commit raw transcripts or credentials. Summarize material evidence in run docs/session JSON.

## Rollback and Safety Rules

1. Create `elves/pre-batch-N` before every batch and push the tag only when policy allows.
2. Never force-push, rebase the working branch, or use destructive cleanup.
3. Never merge by default.
4. Stage specific intended files; never use blind `git add -A`.
5. If the owned tip moves unexpectedly, stop as a collision.
6. If the worker checkout is ambiguous, preserve evidence and stop that lease; do not reset first.

## Plan and Log Paths

- **Plan:** `docs/plans/v1.20.0-cobbler-external-agent-orchestration.md`
- **Survival Guide:** `docs/elves/external-agent-orchestration-survival-guide.md`
- **Learnings:** `docs/elves/learnings.md`
- **Execution log:** `docs/elves/external-agent-orchestration-execution-log.md`
- **Durable docs manifest:** `.ai-docs/manifest.md`
- **Branch:** `codex/external-agent-orchestration`
- **PR number:** not created yet
- **Plan SHA-256 at staging:** `af80812a93172582ff4c62bd5c09bc6bde49bd4ddd93669231bdc62eafa2b97e`

## Elves Report

- Generate substantial finite-run report at `/tmp/elves-report-elves-external-agents-2026-07-12.html`.
- Do not commit it unless requested.
- Include problems found, qualification evidence, batch timeline, validation/review proof, route and
  usage/fallback decisions, residual risks, and human next steps.
- Final Readiness Review must be clean before reporting review readiness.

## After Any Compaction

1. Read this file, especially the Run Control section and Stop Gate.
2. Read `.elves-session.json`, including `continuation_guard`, routes, lease, tests, and acceptance.
3. Read learnings, plan, execution log, then `.ai-docs/manifest.md` and relevant linked docs.
4. Verify active resources and owned/worker worktrees before starting any process.
5. Compare plan hash and owned branch tip.
6. Identify first incomplete batch and its host-written contract.
7. Resume exact configured sessions only; rehydrate from disk on expected context changes.
8. If execution is launched and stop is not allowed, continue without asking.

# READ THIS FILE FIRST AFTER ANY COMPACTION OR RESTART
