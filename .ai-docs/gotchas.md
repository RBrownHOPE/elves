# Gotchas

- This repo is documentation-heavy, so regressions usually show up as drift between `SKILL.md`,
  `AGENTS.md`, templates, README, and changelog rather than broken code execution.
- `README.md` repeats concepts from the skill files and often lags unless it is updated as part of
  the same batch.
- A morning checkpoint, return time, or delivery target can look like a natural stopping point, but
  it is not a stop condition unless the survival guide explicitly says it is a hard stop.
- Clean commits, green CI, a good summary, or user silence can also look like permission to stop.
  They are not. Use the `Stop Gate` and `continuation_guard`, not vibes.
- "The remaining work is a lot for one turn" and "this is a natural place to check in" feel like stop
  signals but are exactly the rationalizations the Pre-Final Guard exists to defeat. The volume of
  remaining work is the reason the run exists, not a reason to stop.
- Two agents (e.g. Claude and Codex) in the same working tree on the same branch will overwrite each
  other's files and move the branch out from under each other mid-run. One run owns one branch and
  one checkout; use a dedicated `git worktree` per run when agents share a repo, record the branch
  tip at staging as a collision tripwire, and stop if it moves to a commit you didn't create.
- The survival guide can silently rot into an append-only history log if updates get stacked at the
  bottom. Rewrite the live sections in place and keep chronology in the execution log.
- Paid pods, remote jobs, and long-lived local services become invisible quickly unless `Active
  Compute` is updated after every push and resource change.
- `.elves-session.json` is ignored by default in the repo baseline, but live Elves runs may need to
  force-add it so the branch carries structured session state during the run.
- Local project installs can quietly shadow global installs. When behavior differs from what the
  user expects, check `scripts/install_doctor.py --doctor` before assuming the upgrade failed.
- PR review automation only becomes useful once the branch is pushed and the PR exists. Opening the
  PR late starves the review loop.
- This repo has no package-managed lint/typecheck/build/test pipeline, so proof comes from
  preflight sanity, reference consistency, and PR review cleanliness.
- Provider wording drifts easily. Normal Cobbler and ordinary Elves must not require OpenRouter.
  Math may show `openrouter:<model-id>` as an optional role route, but default config should keep
  `math-required-env: []` unless a project survival guide explicitly opts in.
- `status: complete` in `.elves-session.json` is self-certified unless paired with per-batch
  `acceptance: [{criterion, met, evidence}]`. Green CI plus complete flags is not landable; plan
  Acceptance with proof is. Less-disciplined models especially tend to close god-file / split
  batches on structure or regex lock tests alone.
- Multi-batch "close remaining" commits can make unfinished work look shippable. Prefer one batch
  per close commit; otherwise require separate Validate sections per batch id and run
  `scripts/elves_landing_check.py` before Final Readiness.
- Vague commit subjects (`Updates`, `progress`, `WIP`, bare `fixes`) hide operator progress in
  GitHub/GitKraken. Prefer phase-aware subjects and push mid-batch slices, not one opaque dump.
- Incomplete coordinator-to-implementer packets are coordinator defects. Do not expect a context-poor
  worker to reconstruct intent, Build On targets, or forbidden surfaces from chat memory.
- `.elves/models.toml` is local and ignored. Staging it or treating personal model IDs as public
  defaults reintroduces provider lock-in. Use `references/models.toml.example` as the reviewable
  schema and snapshot effective routes in the Survival Guide.
- Python 3.11+ supplies stdlib `tomllib` for local models TOML. On older Python, absence of the file
  is fine; presence without `tomllib` must fail validation rather than being silently ignored.
- `python3 scripts/cobbler_agents.py validate-config --json` and `doctor --json` never launch paid
  model turns and must not mutate the repo.
- Council lanes must launch in parallel. Sequential fan-out is not independence and fails the
  wall-clock overlap tests in `tests/test_cobbler_agents_dispatch.py`.
- Exit code 0 is not inference success. Structured role-report JSON must validate; actual-model
  mismatches fail the lane when a requested model was set.
- Strip secret env **names** from child processes and never log secret values. Allowlisting a
  secret-looking name must not reintroduce it.
- `lightweight-review` is a utility lane, not a council vote. It cannot close high-risk review or
  satisfy independent review quorum by itself.
- `target_quorum` degrades with a confidence drop; `required_quorum` only applies when the phase is
  explicitly `required=true` and blocks when unmet after fallback.
- Never use bare `--resume`, `--continue`, or `--last` for session selection. Exact session IDs only.
  Canonical disk state (plan/Survival Guide/session registry) outranks chat memory.
- Grok parent→worktree child lineage uses a **new** child UUID. Headless `--worktree --resume` on
  Grok Build 0.2.93 is broken (retains source CWD); fail closed without verified CWD/worktree
  registration, then resume the discovered child exactly from that worktree.
- `remaining_quota` is `unknown` unless a harness explicitly sets `quota_known`. Never invent limits
  from token counts, and never treat unknown as zero.
- Unexpected model/CWD/parent/worktree drift blocks write reuse; expected HEAD/plan digest change
  yields rehydration, not silent continuation on stale assumptions.
- Only one external writer lease may be live. Dirty, branch-attached (when detached required), HEAD
  mismatch, or unregistered worktrees fail closed at prepare.
- Never bare-cherry-pick worker commits. Export binary patches, `git apply --check --index` on the
  host, then create sanitized host commits that record source worker SHAs.
- Grok write profile forbids headless `--worktree --resume` as isolation (especially 0.2.93). Resume
  the exact child from a registered detached worktree CWD under a `devbox` (or equivalent) sandbox.
- A `workspace` sandbox linked worktree must not be assumed commit-capable; leases force
  `detached_commits_permitted=false` for that profile.
- Ref/remote/config/hook mutations, out-of-scope paths (including `.elves/` and run docs), symlink
  escapes, push attempts, and process leaks fail the post-turn audit even when the file diff looks right.
