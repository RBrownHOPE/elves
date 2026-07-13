# Conventions

## Cross-file sync

- `SKILL.md` and `AGENTS.md` must change together when Elves behavior changes.
- Template updates in `references/*` should reflect the same model as the skill files.
- A release version bump is incomplete until the skill metadata, `AGENTS.md`, and
  `CHANGELOG.md` all agree.
- Coordinator-to-implementer handoff packets must carry intent/why, non-obvious rationale,
  Build On targets, owned/forbidden surfaces, acceptance evidence, failure modes/pitfalls, and
  HEAD/run-doc/route-session/output identity. Pin drift with consistency phrases.
- Git history is an operator UI: prefer
  `[branch · Batch N/total · Contract|Implement|Validate|Review|Close] concrete outcome`,
  forbid vague subjects, push meaningful mid-batch slices, and reserve `Close` for acceptance-
  backed completion. External workers may create only audited detached handoff commits.
- Run control is live metadata. If stop behavior, checkpoint meaning, or continuation policy
  changes mid-run, rewrite the survival guide's `Run Control` block immediately and log the change
  in the execution log.
- The live survival-guide sections are `Run Control`, `Current Phase`, `Active Compute`,
  `Cobbler Session State`, `Stop Gate`, and `Next Exact Batch`. Rewrite them in place; do not
  stack old updates there.
- If a run uses paid compute, remote jobs, or long-lived local services, keep `Active Compute`
  current after every push and after every resource-topology change.
- Every completed batch must end with `update docs -> commit -> push -> re-read survival guide`
  before later work begins.
- Stopping should be explicit state, not interpretation. Use the survival guide's `Stop Gate` and
  `.elves-session.json` `continuation_guard` to record whether stopping is currently allowed.
- Cobbler is the default coordinator after an Elves invocation. Persist that in the survival
  guide's `Cobbler Session State` block and in `.elves-session.json` `cobbler.default_for_session`
  for real runs. Do not add durable state for one-off Quick Cobbler answers.
- Math is a Cobbler-managed domain workflow. Keep provider keys optional by default and record
  math-specific evidence in `docs/math/*` ledgers, not in a separate Council ledger.
- Installed Claude/Codex skill bundles should ship only the installable runtime surface:
  `SKILL.md`, `AGENTS.md` (Codex), `config.json.example`, `references/`,
  `scripts/preflight.sh`, `scripts/preflight_worktree.py`, `scripts/notify.sh`,
  `scripts/install_doctor.py`, `scripts/validate_survival_guide.py`,
  `scripts/elves_landing_check.py`, and `scripts/cobbler_agents.py` plus
  `scripts/cobbler_runtime/` when those runtime modules exist.
  Repo-only maintenance helpers stay in the checkout.
- Local `.elves/models.toml` is ignored checkout preference; track only
  `references/models.toml.example` as the schema. Never stage credentials or personal paths.
- Startup installation/update checks must stay advisory-only. They may alert the user, but they
  must never block a run or auto-update the installed skill.
- When you add a cross-file behavioral concept, pin it with a `*_PHRASES` map in
  `scripts/check_repo_consistency.py` (e.g. `WORKSPACE_ISOLATION_PHRASES`,
  `NONSTOP_GUARDRAIL_PHRASES`) so the SKILL / AGENTS / README / template mirror can't silently drift.

## Documentation as part of done

- Treat documentation freshness as part of batch completion, not cleanup theater at the end.
- Explicitly record docs that were impacted, updated, promoted, or deferred.
- Prefer promoting durable knowledge into `learnings` or `.ai-docs/*` instead of burying it in the
  execution log.
- Kickoff prompts, report templates, and other operator-facing forms should mirror the same
  run-control model as the core skill files.

## Product direction

- Keep Elves lightweight and operationally realistic.
- Borrow architecture from richer systems when it reduces confusion, but do not import heavy
  hydration or automation machinery without a clear need.
- Preserve stage-before-implementation as an internal phase boundary and the PR-centric review
  loop. The recommended user path is one kickoff that continues after launch readiness; a second
  launch call exists only when the user explicitly selects legacy two-call.
- One run owns one branch and one checkout. Never share a working tree or branch with another active
  agent; use a dedicated `git worktree` when agents may share a repo, and treat an unexpected
  branch-tip move as a collision (Hard Stop), not a normal diverge.
- Merge policy: by default the agent never merges and never squashes — the user merges. The user may
  opt into a `merge-on-green` preference, in which case the agent lands a regular merge commit (never
  a squash) only after the Final Readiness Review passes.


## v2.1.0 full-run note

Trusted Grok full-run uses one session, feature-branch progress, parked-monitor driver, and bounded events. Untrusted detached leases remain a distinct authority model.
