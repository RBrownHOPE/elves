# Project Learnings

> This file is durable memory across Elves runs. Read it after the survival guide and
> `.elves-session.json`, before the plan and execution log. Keep only stable, reusable, actionable
> lessons here.

---

## Promotion Rules

Promote something into this file only if it is:

- **Reusable:** likely to help a later batch or a future run
- **Stable:** not expected to change again in the next hour
- **Actionable:** changes what the agent should do, avoid, or verify
- **Specific:** concrete enough that another session can apply it without guessing

When a learning becomes outdated, move it to `## Retired Learnings` with a short note instead of
silently deleting it.

## Repo Conventions

- [2026-07-12] The host coordinator owns canonical run documents, git, PRs, validation,
  acceptance evidence, and final synthesis. Write every batch contract/context packet for a
  potentially less-capable, context-poor implementer: include intent, rationale, existing Build On
  targets, owned/forbidden surfaces, acceptance evidence, failure modes, and pitfalls without
  prescribing brittle line-by-line code.
- [2026-07-12] Git history is an operator-facing progress surface. The host should promptly commit
  and push meaningful, reviewable slices within each batch using branch/batch/phase/outcome subjects;
  avoid vague giant dumps and noisy micro-commits, reserve `Close` for acceptance-backed completion,
  and never delegate branch/ref/push ownership to an external implementation worker. A qualified
  worker may use detached commits as audited internal handoff boundaries.
- [2026-07-08] Batch `status: complete` must carry plan Acceptance proof in session JSON
  (`acceptance: [{criterion, met, evidence}]`). Green CI alone is not landable. Structure/regex
  characterization tests may lock god-file splits but must not alone complete them unless the plan
  explicitly allows characterization-only. Prefer one batch per close commit; use
  `scripts/elves_landing_check.py` before Final Readiness.
- [2026-04-11] This repo has two canonical skill surfaces: `SKILL.md` for Claude-compatible agents
  and `AGENTS.md` for Codex. Any behavior change to Elves must update both in the same release.
- [2026-04-11] Elves works best when staging and launch are treated as separate phases even if the
  user asks for a full unattended run in one session; the plan and run-memory docs should be stable
  before implementation batches begin.
- [2026-04-14] Run control is live metadata, not a planning-time note. If the user changes stop
  behavior, checkpoint meaning, or whether work may continue after a deadline, rewrite `## Run
  Control` immediately and log the change in the execution log.
- [2026-04-14] The survival guide is a live operator brief, not a history log. Rewrite `Run
  Control`, `Current Phase`, `Active Compute`, and `Next Exact Batch` in place; leave chronology to
  the execution log.
- [2026-04-14] Stopping should require positive permission, not inference. The survival guide
  should carry a `Stop Gate`, and `.elves-session.json` should carry a `continuation_guard`, so a
  recovered context can tell whether it must keep going without rereading the whole run.
- [2026-05-09] Substantial finite runs should end with a temporary human-facing Elves Report when
  stopping is allowed. The report is a worker-to-manager morning briefing sourced from the survival
  guide, execution log, learnings, plan, session JSON, and live PR/CI state. It should foreground
  problems found, lessons learned, verification proof, residual risks, human next steps, and
  collapsible batch summaries instead of forcing the user to reconstruct the run from raw logs.

## Validation and Tooling

- [2026-07-12] Never update an accepted session context digest before rehydration proof. Store
  pending digests/heads on expected canonical drift and promote only after an exact resume matches
  the pending packet; otherwise a later resume silently erases the rehydration obligation.
- [2026-07-12] Do not normalize repo-relative paths with `str.lstrip("./")`. That API strips a set of
  characters, turning `.elves/...` into `elves/...` and breaking forbidden-prefix checks. Strip only
  explicit `./` segments.
- [2026-07-12] Setup `smoked=true` requires a real model response from an explicit smoke executor.
  Opt-in acknowledgment alone is not qualification.


- [2026-07-12] External-harness capabilities must be qualified behaviorally and versioned. CLI
  flags, installed executables, authentication state, actual model identity, persistent-session
  behavior, and safe-write behavior are separate facts; do not infer one from another.
- [2026-07-12] Subscription harnesses may expose observed token/cost usage without exposing
  remaining quota. Preserve `remaining_quota: unknown`; never invent a limit or treat unknown as
  zero.
- [2026-04-11] `./scripts/preflight.sh` is the repo's best built-in environment check. It is useful
  for git/auth/setup validation even though this repo has no package-managed build/test pipeline.
- [2026-04-14] If a run uses paid compute, remote jobs, or long-lived local servers, track them in
  the survival guide's `Active Compute` section and reconcile them after every push or topology
  change.

## Review Heuristics

- [2026-04-11] For this repo, the most common regression risk is documentation drift across
  `SKILL.md`, `AGENTS.md`, templates, and README. Review should verify conceptual alignment, not
  just prose quality.
- [2026-04-11] Treat stale docs as `PENDING-DOCS`, not as a vague warning. If recovery docs,
  durable docs, or human docs lag behind a behavior change, the batch is not clean yet.

## Product and Domain Invariants

- [2026-07-12] Native-only Cobbler remains the zero-config default. External Claude/Grok/Sakana,
  OpenRouter, API-only models, and future custom tools are optional role routes; only an explicit
  project Survival Guide may make one required.
- [2026-07-12] External implementation is an untrusted detached-commit/patch workflow: one writer
  lease, optional direct-descendant commits only when the exact session+sandbox capability is
  qualified, no worker ref/push/PR/run-memory ownership, full chain+ref+remote+config+hook+path audit,
  and host-only binary-patch import, validation, branch commit, and push. The implementer is excluded
  from independent review quorum.
- [2026-07-12] **Default implementer is the host** (Claude Code or Codex). Grok Build, multi-provider
  plan/review, and the host-import writer lease are optional upgrades when those tools exist — same
  pattern as the math module. Do not imply overnight Elves requires Grok or “Lane A.”
- [2026-07-12] Optional plan/review routes follow the geometry-exploration multi-model panel
  pattern: OpenRouter via `OPENROUTER_API_KEY` + wrapper + named `or-…` presets (any
  `provider/model-id`); Meta Muse Spark 1.1 via `META_API_KEY`/`MODEL_API_KEY` + wrapper, model id
  **`muse-spark-1.1`**, preset e.g. `meta-muse-spark11`. Independent read-only lanes only; pin the
  Meta catalog id (not assumed aliases); native fallback when key/wrapper missing; never sole
  authority. Recipes: `references/cobbler-setup-recipes.md`, `references/council-provider-config.md`.
- [2026-07-12] Google Cloud AlphaEvolve is an optional math-module evolutionary-search lane for
  numerical examples / counterexample signals: managed mutation + local deterministic evaluator,
  gcloud impersonation (no SA keys), independent replay before promotion. Role
  `evolutionary_search`. Not a proof engine. Guide: `references/math-alphaevolve.md`.
- [2026-07-12] Model onboarding is host-mediated on both Claude Code and Codex: `onboard
  plan → interview → apply → probe`. Preferences in ignored `.elves/models.toml`; structural probe
  by default; live smoke opt-in; never print secrets. Protocol: `references/model-onboarding.md`.
- [2026-07-12] Prefer high-quality Claude/Codex for plan+review and a labor model for implement
  (`*-planning` / `*-labor` profiles + local `requested_model`). Google Gemini CLI / Antigravity
  CLI are optional plan/review lenses, usually not cost-effective for the main implement batch.
- [2026-07-12] **Supported Elves main drivers are Claude Code and Codex only.** Optional routes
  (Antigravity, Gemini CLI, Muse, OpenRouter, Grok, AlphaEvolve) may work as tools the host calls;
  that is not our focus, is not heavily tested, and is not support for those products as the
  overnight host. Prefer contributor PRs (or issues) when optional paths fail.
- [2026-07-12] When the user *does* have Grok Build and wants it, prefer
  `implementation_lane: fast` with one whole-batch launch
  (`--prompt-file <packet> --yolo --effort medium`, session create/resume, sensible `--max-turns`)
  over nested host driving. Operator surface:
  `python3 scripts/cobbler_agents.py implement prepare|launch|gate|resume-batch|status`. Use
  `untrusted` (host-import lease) only when proving the writer boundary or repairing that runtime.
  Docs: `docs/plans/smart-plan-grok-implement.md`, `references/grok-implementer-launch-prompt.md`.
- [2026-04-11] Elves is intentionally lightweight. Borrow architectural ideas from richer systems,
  but avoid pulling in hydration, skeleton generation, or opaque automation unless the repo
  genuinely needs them.
- [2026-04-11] The user always merges. PRs are collaboration and review surfaces, not autonomous
  delivery endpoints.

## Known Traps

- [2026-07-12] Grok Build headless turns can exit before detached commits despite incomplete work.
  Prefer absolute `--prompt-file` paths (worker CWD is not the host runtime dir). If the process
  exits with dirty porcelain twice, host-seal audited worker tree commits with explicit recovery
  notes rather than thrashing the same lease indefinitely.
- [2026-07-12] Independent review quorum can tolerate optional lane rate limits when host + another
  independent non-implementer lane still meet required_quorum. Never use the exact implementer
  successor as its own independent reviewer.
- [2026-07-12] Accidental `#` H1 lines inside README sections break `extract_markdown_section`
  phrase pins for Cobbler; demote accidental headings before closing docs batches.


- [2026-07-12] Grok Build 0.2.93 headless `--worktree --resume` can silently retain the source
  checkout. Interactive worktree resume creates a discoverable child session with the full parent
  transcript. Verify actual CWD, registered detached worktree, parent/child identity, and HEAD; never
  treat the flag itself or an unchanged requested session ID as isolation proof.
- [2026-07-12] A Grok sandbox profile is fixed at session startup and cannot be changed by resume or
  fork. In a linked worktree, `workspace` may allow source edits while denying the shared Git
  `index.lock` outside the CWD; edit capability therefore does not imply commit capability. Start a
  separately qualified commit-capable session before implementation, seed it from canonical run
  memory, constrain Git to detached commits, and audit all shared-repo state afterward.
- [2026-07-12] Narrow Grok `--tools` allowlists can remove terminal background-support tools and
  make agent construction fail. Treat a harness toolset as a behaviorally qualified bundle rather
  than composing individual advertised capabilities ad hoc.
- [2026-07-12] Grok's CLI `dontAsk` behavior is documented as incompletely wired, and child-process
  network restrictions are a no-op on macOS. Prompt rules and mode names are not hard boundaries;
  combine explicit policy, hard hooks/custom sandbox where available, credential/environment
  isolation, and mechanical post-turn audit.
- [2026-07-12] Fugu may inherit unrelated Codex MCP servers. An optional MCP OAuth `invalid_grant`
  warning is not evidence that Sakana inference failed; report model health and MCP health
  separately.
- [2026-04-11] Repo-level changes can look complete after updating one skill file, but `SKILL.md`,
  `AGENTS.md`, templates, README, and CHANGELOG often drift unless they are reviewed as a set.
- [2026-04-11] If `.elves-session.json` is intentionally committed during a live Elves run, do not
  also ignore it in `.gitignore`; reviewers will correctly read that as contradictory workflow
  guidance.
- [2026-04-14] A checkpoint, return time, or delivery target is not automatically a stop
  condition. If the survival guide does not explicitly call it a hard stop, the agent should treat
  it as a relaunch point and keep going.
- [2026-04-14] Clean commits, green CI, summaries, and user silence are all false stop signals.
  If work remains and the Stop Gate says `no`, the agent should update docs, push, and continue.

## Retired Learnings

- None yet.
