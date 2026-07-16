# Execution log: Open-source Grok Build integration

## Run metadata

- Run: `grok-build-open-source-2026-07-15`
- Branch: `codex/grok-build-open-source`
- Worktree: `/Users/john/aigora/dev/elves-grok-build-open-source`
- Base and start head: `origin/main` at `4bbb7b3b6c4f5d57bfa0cc4bc8b0014c39559080`
- Plan: `docs/plans/grok-build-open-source-realignment.md`
- Worker route: exact `gpt-5.6-sol` native Codex session; High Prewalk through one meaningful edit,
  then exact-session resume at Medium for the complete run
- Landing outcome: reviewed PR; merge and release unauthorized

## Planning evidence

- Installed binary: `/Users/john/.grok/bin/grok`, version `0.2.101` stable.
- Authenticated catalog: live default `grok-composer-2.5-fast`; `grok-4.5` also present.
- Confirmed installed surfaces: autonomous/read-only controls, `--check`, caller-provided
  `--session-id`, exact resume, streaming JSON, JSON schema, and `agent stdio`.
- Confirmed defect: `--new-session` is not accepted by the installed parser.
- Confirmed auth contract: open-source source implements `GROK_AUTH_PATH`; the current private-home
  plus narrow credential projection should be retained.
- Confirmed goal contract: isolated headless `/goal status` used the narrow auth projection and
  returned successfully without catalog lookup or model inference. The bounded 0.2.101 objective
  canary emitted work but did not reach a terminal exact-session event, so the run keeps goal mode
  disabled and selects the one-packet fallback.
- Transport decision: implement headless goal plus streaming first; defer ACP until its persistent
  permission/reconnect client provides enough additional value.

## Baseline and staging

- Main checkout was clean and matched `origin/main` before the dedicated worktree was created.
- The inherited remote plan contained refuted assumptions about goal mode, auth projection, and
  several flags. The canonical plan now records the executable evidence and corrected scope.
- No product code has been changed and no implementation worker has been launched.

## Batch status

- Prewalk: complete in exact Codex session `019f684d-ad07-78a3-8067-27f3131ecefd`; one additive B0
  capability-ledger slice and its focused test are present but intentionally uncommitted
- B0 capability contract: in progress
- B1 session and auth semantics: pending
- B2 goal launch and streaming follow: pending
- B3 models, onboarding, and public contracts: pending
- Terminal cumulative review: pending

## Staging proof

- Acceptance identity: plan, session, and ignored full-run packet match across all 20 stable
  criteria.
- Preflight: passed. Advisories were limited to the repository's intentional lack of a conventional
  package manifest and unset optional unattended-shell environment variables.
- Repository consistency and `git diff --check`: passed.
- Draft PR: [#77](https://github.com/aigorahub/elves/pull/77), containing contract artifacts only.

## Next action

The run is paused at the user's request. The exact-session Medium revision completed in commits
`b37c375` and `66d1299`. Focused worker proof passed, including 87 implement/session tests, 240
routing/dispatch/native-worker/consistency/bundle tests with 10 skips, 5 targeted
streaming/redaction/session tests, both installed-host bundle smokes, repository consistency, Python
compilation, and `git diff --check`.

The host then ran `python3 -m unittest tests.test_full_run_supervisor` outside the worker sandbox.
Result: 142 tests, 3 errors, 1 skip. Each error is a stale synthetic fixture using a non-UUID Grok
session ID after the runtime correctly began enforcing Grok's canonical UUID grammar:

- `test_failed_oauth_launch_preserves_canonical_auth_without_copy`
- `test_live_shared_oauth_rotation_survives_monitor_report_and_stop`
- `test_real_resume_archives_attempt_after_committed_pushed_checkpoint`

On resume, replace only those three fixture IDs with canonical UUIDs, rerun the supervisor module,
then continue the terminal cumulative review. The branch is otherwise clean. Merge and release
remain unauthorized.

## Resume checkpoint

- Replaced only the three stale synthetic Grok session labels with canonical UUID fixtures.
- Targeted result: 3 tests passed.
- Supervisor result: 142 tests passed with 1 skip in 66.967 seconds.
- Cumulative review: root plus three independent reviewers inspected the full branch diff. The
  consolidated blocker set was limited to live catalog row grammar, free-form goal evidence,
  cross-record credential redaction, overbroad linked-worktree Git access, and stale acceptance/docs.
- Revision: parse the installed `-`/`*` catalog grammar; require a bounded build-bound terminal
  canary artifact; quarantine adjacent stream records; restrict native workers to feature Git
  metadata with stripped Git credentials and terminal authority verification; align Claude/Codex
  recovery and acceptance wording.
- Targeted delta proof: 32 adaptive-routing/native-worker tests, 24 Grok stream/goal-state tests,
  76 consistency tests, the repository consistency checker, guide HTML parsing, compilation, a
  live installed-binary Grok 4.5 route snapshot, and `git diff --check` pass.
- Acceptance evidence: all 16 batch criteria and 4 master criteria are checked with concrete proof;
  plan/session/packet identity validates with no issues.
- Next: one canonical final-readiness proof after acceptance evidence is recorded, followed by the
  narrow operational-artifact cleanup and current-tip CI attestation.
