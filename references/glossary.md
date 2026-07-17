# Glossary

One line per coined term. Docs link here on first use instead of re-explaining. If a term is not
in this file, it is not project vocabulary — plain English wins.

- **Elves** — the execution system: plans, branches, PRs, validation, review, run memory, landing.
- **Driver** — the live capable session (Claude Code or Codex) that stages, reviews, and lands.
- **Worker** — the separate session that implements batches; never merges, never owns protected refs.
- **Cobbler** — the default coordination model: classify a task, route it, preserve dissent, fit
  one answer. *Quick Cobbler* = read-only one-off answer; *Cobbler Mode* = current-thread chat
  state; *Cobbler session state* = durable run state in the survival guide and session file.
- **Council** — deprecated alias of Cobbler, kept for invocation compatibility only.
- **Chat-to-work** — end-to-end run that stops at a landable PR; the user merges.
- **Chat-to-land** — the same run with an explicit in-session user authorization to merge.
- **Full-run** — trusted delegation shape: one packet, worker owns internal batches on the feature
  branch while the driver parks.
- **Parked driver / parked monitor** — the driver between launch and wake: no model inference, no
  timed chat, wakes on material events only.
- **Follow mode** — the sanitized non-model stream a parked driver (or the user) can watch.
- **Worker packet** — the standalone coordinator→implementer handoff written at staging
  (`worker_packet_path` in the session); the plan's per-batch handoff blocks are not a substitute.
- **Progress ledger** — the worker's untracked orientation note under `.elves/runtime/`,
  refreshed at each milestone so a cold re-drive starts oriented.
- **Survival guide** — the run's live operator brief and compaction-recovery anchor.
- **Execution log** — chronological proof of what happened, with decisions.
- **Learnings** — durable reusable lessons that outlive a run.
- **Stop Gate** — the explicit "may I stop now?" answer in the survival guide; silence never
  grants stopping.
- **Run Control** — the survival-guide section recording run mode, stop policy, merge policy,
  workspace ownership, and delegation shape.
- **Batch** — one independently shippable slice (`B#`), with stable acceptance ids (`B#-A#`).
- **Master Acceptance** — branch-level outcomes (`M-A#`) proving the whole run is landable.
- **Close** — the single acceptance-backed commit that completes a batch; driver reconciles use
  the `Review` label instead.
- **Collision tripwire / START_TIP** — the recorded branch tip at staging; any unexplained move is
  a collision and a Hard Stop.
- **Rollback ref** — host-owned `refs/elves/rollback/...` pointer for recovery.
- **Landing / land-pr** — the reviewed merge path (regular merge commit, never squash) after
  readiness at the exact final HEAD.
- **Readiness** — plan Acceptance with proof at the exact HEAD; independent of merge authority.
- **Thin safety kernel** — the invariants that never weaken (identity, credentials, no worker
  merge authority, test integrity, exact-HEAD readiness).
- **Legality check / constitution** — human-owned intentions the run must not violate; judged
  PASS/WARN/FAIL per intention.
- **Scout mode** — post-plan spare-time work: adjacent bugs, tests, docs.
- **Ride-along** — a user message prefixed `ra:`/`ride-along:` answered in 1–3 sentences without
  breaking the run.
- **Handling matrix** — how a task routes: direct edit, bounded batch, or full-run.
- **Worktree gc** — the reclaim helper (`preflight.sh --gc-worktrees`) that removes only clean,
  fully merged, fully pushed worktrees; separate from the create helper.
- **Elves report** — the static HTML end-of-run report under `/tmp`.
- **Domain workflow** — a specialized Cobbler-managed pack (math is the first).
- **Codex Goals** — optional Codex continuation plumbing; distinct from Grok Build goal mode.
