# Execution Log: True Trajectory-Preserving Prewalk

## Run metadata

- Branch: `codex/true-prewalk`
- Worktree: `/Users/john/aigora/dev/elves-true-prewalk`
- Base: `origin/main` at `206a625b68bbb42e7fd6e8283ac65945c0f73648`
- Plan: `docs/plans/true-prewalk.md`
- Survival guide: `docs/elves/survival-guide-true-prewalk.md`
- Learnings: `docs/elves/learnings.md`
- Worker route: host-native; no paid/live model calls
- Merge authority: none; open an unmerged reviewed PR

## Baseline and staging

- Read the complete Elves skill, its staging/proof/host-parity references, the 1,103-line
  authoritative implementation specification, and the current native-worker/routing surfaces.
- Fetched current `origin/main`; it remains the specification's inspected baseline `206a625`.
- Created `/Users/john/aigora/dev/elves-true-prewalk` on `codex/true-prewalk` with the repository
  preflight helper. The earlier prototype worktree remains registered and untouched.
- The Stencil URL was queried without making a model call; the site did not return readable body
  content through the available fetch paths. The detailed local specification remains authoritative.
- `./scripts/preflight.sh` passed origin, GitHub authentication, explicit branch push, worktree
  ownership, staleness, and acceptance gates. Its advisory warnings were only the repository's
  intentionally unclassified project type and shell-local non-interactive exports; bounded commands
  use explicit non-interactive environment values.
- `python3 scripts/verify_repo.py --version 2.7.0` passed compile, shell, JSON, evidence selection,
  and repository consistency, then reproduced a baseline release-checklist failure because current
  `origin/main` already contains an Unreleased changelog entry after the 2.7.0 heading. Terminal
  verification will use `--version Unreleased` unless the branch is promoted to a numeric release.
- `git push --dry-run origin HEAD:refs/heads/codex/true-prewalk` proved explicit push access.
- Contract commit `064004f` was pushed to `origin/codex/true-prewalk`; the survival guide was
  re-read and rollback ref `refs/elves/rollback/true-prewalk-2026-07-17/host/b0-contract` records
  the exact pre-B0 tip.

## Decisions made

- Use host-native execution rather than an Elves delegated worker because the user explicitly
  prohibited paid model calls and live canaries.
- Treat this as one high-risk finite chat-to-work run with three acceptance-bearing batches and no
  merge authority.
- Keep `prewalk=auto` conservative/unavailable until both hosts have version-bound behavioral
  qualification; help fixtures prove grammar only.
- Implement instruction fidelity as honest `retained_safe` or another proven state; never infer
  pruning from omission of a resume flag.

## Batch status

- B0 host-neutral contracts/routing: complete. The focused module validates bounded TODO and
  checkpoint identity, meaningful edits, safe runtime paths, capability evidence, and canonical
  prompts; routing/preferences expose distinct guide/execution decisions with conservative fallback.
- B1 multi-phase supervisor/parity: complete. One version-3 supervisor owns guide, transition,
  execution, recovery, terminal authority, and one private follow stream. Clean `auto` fallback is
  explicitly a fresh single-phase run; dirty fallback is prohibited.
- B2 docs/install/readiness/PR: complete. The normative, user, parity, schema, guide, release,
  prior-design, and durable AI surfaces now share the exact-trajectory definition; fresh Claude and
  Codex installs ship and exercise the new reference/runtime; PR #80 is open and intentionally unmerged.

## Verification evidence

- Staging validation: `acceptance_contract.py sync-session --write` and `validate` passed.
- B0 first slice: 38 prewalk artifact, meaningful-edit, capability, preference, and routing tests
  pass. The new host-neutral module keeps provider process control out of the schema/validator layer,
  static help remains advertised-only, and unqualified auto routing stays single-phase.
- B0 close evidence: the same 38 focused cases pass after the path-prefix correction; installed
  Codex 0.144.1 and Claude 2.1.207 help advertise exact resume and route overrides but correctly
  remain behaviorally unqualified with instruction fidelity `unsupported` and zero model calls.
- B1 focused proof: 127 prewalk, routing, session grammar, and existing native-worker lifecycle tests
  pass. Nine supervisor scenarios prove the normal two-phase lifecycle, tiny task completion, exact
  guide recovery without packet replay, explicit clean auto fallback, dirty fallback prohibition,
  malformed artifacts, forbidden paths, branch drift, session mismatch, execution failure, and
  exact-session transient recovery with the canonical 5/10/20-minute backoff policy.
- Codex and Claude table tests pin distinct guide/execution models and efforts, exact session resume,
  registered CWD, narrow Git roots, and reject ambiguous latest/continue selectors.
- B2 focused proof: 226 prewalk, routing, consistency, installed-bundle, session, and worker
  lifecycle review cases pass; `git diff --check` and repository consistency are clean.
- Canonical verification: `python3 scripts/verify_repo.py --version Unreleased` passes before and
  after the cumulative review fixes.
- Read-only host probes: installed Codex 0.144.1 and Claude 2.1.207 advertise exact resume/route
  overrides but remain honestly unqualified with `unsupported` fidelity and zero probe model calls.
- Terminal cumulative self-review (live model calls prohibited) found and fixed connected gaps:
  qualification is bound to exact routes and the current retained-safe delivery mechanism,
  behavioral evidence reports actual model-call provenance, runtime artifacts reject symlinked
  paths and driver-owned memory, clean fallback rejects Git authority drift, every resumed phase
  must emit the exact session ID, and orphaned childless supervision fails closed. No unresolved
  serious finding remains.
- PR: https://github.com/aigorahub/elves/pull/80 is open against `main`, ready for checks and final
  exact-tip landing proof, and remains unmerged.
