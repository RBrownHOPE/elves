# Execution Log: True Prewalk v2.8.0 Landing

## Current state

- Original B0-B2 implementation and readiness completed on PR #80.
- User explicitly extended the run to promote v2.8.0, land the PR, and publish the GitHub release.
- Driver authorization is now true; worker merge authority remains false.
- Release target is v2.8.0 from the future verified regular merge commit.

## B3 release audit

- GitHub latest published release before this batch: v2.6.0.
- Repository source version before this batch: 2.7.0 on `origin/main` and PR #80.
- Version choice: 2.8.0, a minor release for the new exact-session prewalk capability.
- Documentation blocker found: summary surfaces described multiple fidelity states as currently
  usable, but the implemented persisted-instruction delivery activates only proven
  `retained_safe`; `pruned` and `turn_scoped` are future transport states.
- Release proof and live PR/release results: pending.

## B3 implementation evidence

- Promoted SKILL and thin AGENTS metadata, README/current commands, public guide, changelog, and
  durable verification examples to 2.8.0; `Unreleased` is empty.
- Reconciled retained-safe-only activation across SKILL, README, guide, adaptive routing, host
  parity, architecture, learnings, config guidance, and release notes.
- `release_checklist.py --version 2.8.0 --json`: `ok: true`, no failures.
- `check_repo_consistency.py`: pass at version 2.8.0.
- 186 focused release-checklist, consistency, prewalk, routing, and installed-bundle tests: pass.

## Decisions made

- Preserve no-live-call scope. Qualification remains unavailable after release.
- Update the public guide visibly for v2.8.0 and retain honest host-specific syntax.
- Merge only with `gh pr merge --merge`; tag/release only the resulting merge commit.
- Recreate operational run memory for exact-tip acceptance, then remove it again before landing.

## Next action

Commit the release promotion, then run full strict verification and cumulative release review.
