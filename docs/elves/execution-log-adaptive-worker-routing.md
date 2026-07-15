# Execution Log: Adaptive Native Worker Routing

## Run metadata

- Branch: `codex/adaptive-worker-routing`
- Base: `origin/main` at `171f60b`
- Starting tip: `47190f2`
- Plan: `docs/plans/adaptive-worker-routing.md`
- Survival guide: `docs/elves/survival-guide-adaptive-worker-routing.md`
- Worker route: exact `gpt-5.6-sol` / `medium` Codex CLI session
- Primary worker session: `019f674a-53e6-7f21-bbaa-43082fe59541`
- Targeted revision session: `019f6766-7d3a-7972-977e-8c82395e538a`
- Merge authority: none; reviewed PR only

## Baseline

- `python3 scripts/verify_repo.py --version 2.4.0` passes all gates except the pre-existing stale
  public-API approval for `export:cobbler_runtime.BUILTIN_ADAPTER_NAMES`.
- `python3 scripts/verify_repo.py --version Unreleased` passes all gates except that the same
  approval declares release `2.4.0`.
- Working tree was otherwise clean before contract staging.

## Batch B0: Safe preferences and routing policy

- Status: complete
- Acceptance evidence: shared XDG preferences, private atomic writes, safe-field rejection,
  deterministic provenance, and the isolated route matrix all passed focused tests.
- Commit: `339f6fa` — safe preferences and deterministic worker decisions.

**Validate for batch 0:** route/preference tests and isolated config cases passed; terminal review
confirmed that repository vetoes are absolute while explicit run choices outrank convenience
defaults.

## Batch B1: Native worker parity and optional Grok Build

- Status: complete
- Acceptance evidence: exact Codex/Claude session grammar, supervised private follow log, PID and
  worktree binding, honest Grok probing, and Composer-versus-4.5 production argv are covered.
- Commits: `a4720ef`, `29d4518`, and terminal revision `c791a91`.

**Validate for batch 1:** 135 full-run supervisor tests passed with one expected skip. Fresh
Claude/Codex bundle smokes passed. Driver delta review added the missing writable Codex resume
override and its focused regression test passed.

## Batch B2: Joyful user flow, proof, and documentation parity

- Status: complete
- Acceptance evidence: public docs now offer one natural flow and capability-bound visibility;
  SKILL/AGENTS, both host references, examples, changelog, learnings, and installed bundles align.
- Commits: `06c8e88`, `29d4518`, and terminal revision `c791a91`.

**Validate for batch 2:** revision proof passed 200 affected tests with three expected skips,
repository consistency, both fresh installed-bundle smokes, and `git diff --check`. The cumulative
verifier's sole stale public-API approval was reconciled after the strict current diff showed zero
breaks.

## Terminal review

- Status: complete
- Findings: independent review found five blocking policy/visibility/default/safety/capability
  issues; one consolidated revision resolved them. Driver delta review found and fixed one further
  Codex resume sandbox defect. No serious issue remains.
- Verification: targeted revision suites, 97 final route/goal/CLI tests (three expected skips),
  installed bundles, consistency, diff checks, landing acceptance, and GitHub required checks.
- PR: [#74](https://github.com/aigorahub/elves/pull/74), reviewed and unmerged.
