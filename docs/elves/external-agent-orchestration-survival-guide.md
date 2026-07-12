# READ THIS FILE FIRST AFTER ANY COMPACTION OR RESTART

This Survival Guide is the authoritative live brief for the v1.20.0 Cobbler external-agent
orchestration run. If chat memory conflicts with this file, trust this file and canonical disk state.

Read order after restart: this file -> `.elves-session.json` -> `docs/elves/learnings.md` -> plan ->
execution log -> `.ai-docs/manifest.md` and linked docs -> `TODO.md`.

## Mission

Build and qualify a generic external-agent runtime for Cobbler that can plan and review in parallel,
delegate one substantial implementation batch to a persistent qualified worker, audit/import its
detached commit chain as binary patches, and repeat. Ship native-only fallback, configurable roles/
fallbacks including cheap utility review, Claude/Codex parity, setup helpers, exact session identity,
a safe Grok writer path, and a master CouncilElves launch prompt.

The current Fable 5, Grok 4.5, and Fugu Ultra setup is the live experiment, not a hardcoded public
default. The host coordinator owns all run memory, branch refs/commits, pushes, PRs, validation,
acceptance, synthesis, and final integration.

## Run Control

- **Run mode:** finite
- **Stop policy:** plan-complete-or-true-blocker
- **User intent:** “Test everything before we code it up. Once all the pieces work, plan and stage an
  Elves run; then get Grok Build to do it as an experiment.” The user also requires the main smart
  coordinator to write detailed Elves documents for a potentially less-capable implementation agent,
  requires meaningful pushed commit history because GitKraken/GitHub are progress monitors, and now
  requires the exact persistent Grok successor to perform product implementation throughout the run.
- **Checkpoint due by:** none; assume an approximately 8-hour execution budget from the fresh launch
  call because no return time was specified
- **Checkpoint semantics:** none
- **May continue after checkpoint:** yes; there is no checkpoint stop
- **Actual stop conditions during execution:** all six batches complete with clean Final Readiness
  Review; explicit user stop; or a genuine blocker with no reasonable in-scope workaround
- **Workspace ownership:** owned branch `codex/external-agent-orchestration` in dedicated worktree
  `.` (the canonical directory containing this Survival Guide); no other agent may edit this checkout
- **Branch tip at start (collision tripwire):** `74c52d88868e39a9d4c5cca6dee46919011d2127`
- **External worker checkout:** clean detached Grok worktree
  `${HOME}/.grok/worktrees/dev-elves/2026-07-12-e8fa7ada`; expand/canonicalize the locator and verify
  it against `git worktree list --porcelain`; never attach it to the owned branch;
  align it to the current owned tip only under host control and only while clean
- **Merge policy:** user-merges; never merge, squash, rebase, publish a release, or push a release tag
  without a later explicit opt-in
- **Final-response policy:** staging-only response is allowed after launch readiness; once launched,
  final response is disallowed until plan completion or a true blocker
- **Coordination mode:** Cobbler-first; independent lenses for non-trivial planning, contract, risk,
  debugging, review, and synthesis; host owns git/docs and delegates only scoped worker edits
- **Progress visibility rule:** the host creates and pushes each operator-visible branch slice using
  `[branch · Batch N/6 · phase] concrete outcome`. The qualified Grok successor may create two to
  five meaningful detached handoff commits per substantial batch turn; those never move refs or
  reach the remote. Avoid vague/noisy commits and reserve `Close` for accepted batch completion.
- **Model-cost rule:** keep `gpt-5.6-sol` Ultra as supervisor for contracts, risk, disputes,
  acceptance, and synthesis; delegate only bounded routine read-only checks to ephemeral
  `gpt-5.6-luna` low. Git commit/push/PR commands are deterministic host operations and invoke no
  model.
- **Batch completion rule:** update execution log -> update Survival Guide/session JSON -> `Close`
  commit -> push -> re-read this file. Never begin a later batch with completed work only in the
  working tree, and never hide hours of already-validated progress until batch close.
- **Re-read rule:** immediately after every commit and push, read this file before any other action.
- **Checkpoint rule:** if a later checkpoint is marked delivery-only, log it, push it, and continue;
  a checkpoint is never a stop unless this Run Control block explicitly makes it a hard boundary.
- **Continuation rule:** once launched, if work remains and no actual stop condition applies, continue
  without waiting for user acknowledgment.
- **Staging boundary:** staging complete; this launch call set Stop Gate to `no` and began execution.

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
- **Execution starts:** 2026-07-12 ~10:11 EDT (this launch call)
- **User returns:** approximately 8 hours after launch unless the user gives a different time
- **Checkpoint expectation:** review-ready progress within the finite budget; no artificial stop at
  eight hours if the finite plan can be safely completed in the same active run
- **Time budget:** approximately 8 hours from launch, with judgment to complete a near-finished finite
  batch or stop cleanly at a genuine hard boundary
- **Average batch time so far:** N/A; execution not launched
- **Batches remaining:** 6 of 6

## Stop Gate

- **Planned batches remaining:** 6
- **Stop allowed right now:** no
- **Why:** launch call received; finite run in progress with six incomplete batches; no user stop and
  no true blocker
- **Next required action:** complete Batch 1 under the Grok successor lease, then continue through
  Batches 2–6 with independent review quorum 2 after each batch

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
   format;
8. the intended two-to-five detached worker commit boundaries plus the host-owned branch milestone
   and outcome-focused subject if the batch passes audit and validation. The worker receives no
   branch/tag/ref/push permission.

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

- Host owns run documents, validation, acceptance, branch refs/commits, pushes, PR, synthesis, and
  patch integration. The qualified external worker may create only audited detached commits under
  its lease; it never creates/moves refs, pushes, merges, owns PRs, or edits run memory.
- Native-only behavior remains complete; external tools/keys are optional by default, and only this
  project Survival Guide may mark a route required.
- No credential values enter config, prompts, logs, git, or child environments; write routes fail
  closed when capability, scope, session, CWD, or worktree is not verified.
- Read-only council lanes launch concurrently and independently; only one external writer lease is
  active; the implementation agent is excluded from independent review quorum.
- Every coordinator packet is executable by a less-capable/context-poor worker and every behavior
  change keeps `SKILL.md`, `AGENTS.md`, templates/docs/config, sync surfaces, consistency checks, and
  tests aligned.
- Host-owned git history remains a live operator surface: push meaningful Contract/Implement/
  Validate/Review/Close slices with concrete subjects; do not wait until batch close to show progress.
- Never merge, squash, rebase, force-push, publish a release, or push a release tag in this run.

## Launch Readiness

- [x] Plan cleaned, detailed, and saved
- [x] Survival Guide current
- [x] Existing learnings refreshed with stable qualification facts
- [x] Execution log initialized with six batches and qualification evidence
- [x] Branch created in a dedicated staging worktree
- [x] Branch tip/collision tripwire recorded
- [x] Draft PR #59 opened and recorded
- [x] Preflight and all baseline gates pass in the staging worktree; the sole expected warning is
      that this script/docs repository has no package-manager project marker
- [x] `.elves-session.json` validates and records Cobbler, routes, sessions, test baseline, and guard
- [x] Fresh-call requirement recorded: Stop Gate initialized with `Stop allowed right now: no`
      before Batch 1; this staging call itself remains explicitly `yes`, as required to hand off
- [x] Clean Grok successor is recorded and aligned to pushed tip `52a7fb6`; after the final staging
      `Close` commit the host must realign it to that exact SHA before push and post proof on PR #59
- [x] No active paid/long-running process remains ambiguous
- [x] Short next-call launch prompt prepared below and in the execution log

## Current Phase

**Status:** Execution launched; Batch 1 implementation lease about to issue

**Active batch:** Batch 1: Contracts, configuration, and implementer clarity

**What was just finished:** Launch call verified plan hash
`27a400cff4f1a12de8ae75b59167a6921df1287910e21d93fb1ade5bad357309`, draft PR #59 all-green, owned and
worker worktrees clean and aligned at `88a31fd75014c9182dda856a3eb295cbb8c38279`, non-interactive
environment exported, local rollback tag `elves/pre-batch-1` retargeted to current HEAD, and Stop Gate
set to `no`.

**Single next action:** issue the Batch 1 writer lease to exact Grok successor
`9927883a-0203-42e1-a3e4-710a02096d46`, audit its detached chain, import approved binary patches, and
push sanitized host progress commits.

## Active Compute

No paid inference or long-running process is active at staging close. These are on-disk persistent
resources only:

| Resource | Purpose | Current status | Last verified | Stop / repurpose trigger |
| --- | --- | --- | --- | --- |
| Fable session `02bb9552-fbbd-423f-abbe-acbaa580c918` | planning/contextual review/Claude host | idle; no process | 2026-07-12 | resume exact ID for a bounded turn; record usage afterward |
| Grok parent `159e611b-6c48-4376-8695-5134b9803b7e` | planning lineage | idle; no process | 2026-07-12 | read-only planning only; never source-checkout writes |
| Grok qualification child `019f5644-93d5-7a02-827d-caa8b30a2825` | persistence/write-deny provenance | idle; immutable `workspace` profile; not the writer | 2026-07-12 | retain on disk; do not route writes or try to change its sandbox |
| Grok implementation successor `9927883a-0203-42e1-a3e4-710a02096d46` | required implementation/remediation worker for all six batches | idle; `devbox`; clean detached worktree; commit canary passed | 2026-07-12 | align to staged tip before launch; one substantial batch lease at a time |
| Fugu Ultra `019f5627-e61e-72a3-af3f-ae6e51a348b5` | planning/contextual review | idle; no process | 2026-07-12 | resume exact ID read-only; inherited MCP warnings are separate health |

## Next Exact Batch

**Batch:** 1: Contracts, configuration, and implementer clarity

**Scope:**

- Host writes the complete Batch 1 contract and worker packet from the plan.
- Grok successor implements typed harness/config/capability foundations and the handoff standard
  inside its detached lease checkout, targeting two to five meaningful detached commits.
- Host audits the full chain, imports approved binary patches, runs focused/full tests, and updates
  product docs/run memory before creating and pushing sanitized branch commits.
- Luna-low may perform bounded routine read-only checks; fresh Sol/native + Fable + Fugu review in
  parallel closes risk, with remediation returning to the same Grok successor.

**Acceptance criteria:** use every Batch 1 criterion in the plan, with non-empty evidence rows in
`.elves-session.json`; do not close on tests alone.

**Risk:** provider-specific assumptions or a weak config contract becoming the foundation
for every later batch.

**Rollback tag:** `elves/pre-batch-1`

## Post-Checkpoint Control Loop

Every completed batch must end with a commit and push. Every meaningful progress slice must also end
with a host-owned commit and push, and batch completion specifically uses an acceptance-backed
`Close` commit. Immediately after every commit and push, re-read this survival guide before doing anything else.

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
  session/predecessor, context digest, immutable sandbox profile, allowed Git commands, credential-
  scrubbed environment names, remotes/config/hooks snapshot, and process baseline.
- Worker may edit/test only assigned product files and, for the qualified `devbox` successor, create
  two to five meaningful non-merge commits directly descended from the recorded detached base. It
  may not create/move refs, push, edit `.elves` or canonical run docs, mutate config/hooks/remotes,
  touch credentials/aliases outside scope, or touch another worktree.
- Post-turn evidence: process exit, clean status/index, exact base-to-HEAD commit chain, every parent/
  tree/author/message/path set, refs/remotes/config/hooks, symlinks, binary patches, and forbidden-
  path checks.
- An unexpected commit/parent/merge, any ref/remote/config/hook change, out-of-scope path, ambiguous
  CWD, wrong model/session/profile, or audit failure rejects the handoff and stops that lease. Do not
  route around a denial.
- Host exports/audits each accepted worker boundary, applies binary patches to the owned branch with
  a no-commit check, validates/reviews, writes run state, creates sanitized host commits recording
  worker SHAs, pushes, then moves the clean detached worker to the new tip under host control.

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
    supervise:
      preference: codex:gpt-5.6-sol
      reasoning-effort: ultra
      responsibilities: [contracts, risk, disputed-findings, acceptance, synthesis, run-memory]
      required: true
    planning:
      preference: parallel-independent-council
      routes:
        - host-coordinator
        - claude-code:claude-fable-5
        - grok-build:grok-4.5-parent
        - codex-fugu:fugu-ultra
      required: false
    implement:
      preference: grok-build:grok-4.5-successor
      exact-session: 9927883a-0203-42e1-a3e4-710a02096d46
      context-predecessor: 019f5644-93d5-7a02-827d-caa8b30a2825
      worktree: ${HOME}/.grok/worktrees/dev-elves/2026-07-12-e8fa7ada
      sandbox: devbox
      turn-size: one-substantial-batch
      target-detached-commits: [2, 5]
      required: true
      fallback-chain:
        - claude-code:opus
        - host-coordinator
      automatic-fallback: false
      failure-policy: three-distinct-recovery-attempts-then-stop-for-user
    lightweight_review:
      preference: codex:gpt-5.6-luna
      reasoning-effort: low
      execution: ephemeral-read-only
      availability: probe-before-use
      scope: [routine-diff-sanity, commit-subject, low-risk-invariant]
      fallback-chain: [host-supervisor]
      required: false
    validate:
      preference: deterministic-host-shell
      required: true
    review:
      preference: parallel-independent-council
      routes:
        - fresh-host-native-reviewer
        - claude-code:claude-fable-5
        - codex-fugu:fugu-ultra
      exclude:
        - grok-build:grok-4.5-successor
      required: true
      required-quorum: 2
      quorum-policy: block-below-required-after-recovery
    synthesize:
      preference: codex:gpt-5.6-sol
      reasoning-effort: ultra
      required: true
    git_operations:
      preference: deterministic-host-shell
      model: none
      required: true
```

This run explicitly requires two successful independent review reports, and a fresh host-native lane
counts as one. An individual optional external lane may fail if another independent fallback still
meets the quorum. If fewer than two reports remain after recovery/fallback, the review phase is
blocked; do not call the work council-verified. This project override does not change the shipped
default, where an advisory target quorum may degrade to host synthesis with a recorded confidence
drop. If any other required route fails after three distinct recovery attempts and no safe workaround
exists, log the exact blocker and stop for the user.

The Luna capability canary passed, but a 09:41 EDT full-review attempt hit the shared Codex account
usage limit until 11:16 EDT. Treat availability as dynamic: probe before use and fall back to the Sol
host without blocking or weakening review.

The implementation fallback names are user-decision options only. They are not automatic routes for
this run: every product implementation/remediation slice goes first to the exact Grok successor,
and a switch requires a later explicit user instruction. The Luna route is optional and may never
close a high-risk review or mutate the repo. Commit, push, tag, PR, and comment operations are
ordinary host commands; do not spend Sol, Luna, or provider tokens to execute them.

## Tool Configuration

The normal consistency/test/compile/shell/JSON/whitespace gates are active during staging. The
`toml` gate activates after Batch 1 creates `scripts/cobbler_agents.py` and is required before Batch 1
closes. The evidence-enforcing `landing` form activates once the first batch has acceptance evidence;
its absence during staging is not a preflight failure.

```yaml
lint: python3 scripts/check_repo_consistency.py
typecheck: python3 -m compileall -q scripts tests
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
- [ ] Worker pre/post lease and detached-chain audit passes; host imported approved binary patches
- [ ] Shared surfaces and consumers were traced
- [ ] External route requested/actual/fallback and observed usage recorded
- [ ] Independent review council excludes the implementer and has no blocking finding
- [ ] PR feedback queue is read and dispositions recorded
- [ ] Docs and durable memory are current
- [ ] Batch has meaningful visible progress commits plus one acceptance-backed `Close` commit/push;
      Survival Guide is re-read immediately after each
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
- **PR number:** 59 (`https://github.com/aigorahub/elves/pull/59`)
- **Plan SHA-256 at staging:** `27a400cff4f1a12de8ae75b59167a6921df1287910e21d93fb1ade5bad357309`

## Fresh-Call Launch Prompt

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
