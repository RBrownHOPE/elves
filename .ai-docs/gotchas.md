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
