# Execution Log — hygiene-and-hardening (2026-07-16)

Chronological proof. Newest entries at the bottom. Format: timestamp · phase · what happened · evidence.

## Staging

- 2026-07-16 13:10 · Staging · Plan authored and verified against repo reality (five external-advice
  claims checked true; work-driver enum drift found: template `devin-cli` vs behavior_policy
  underscore enum without devin). Plan: `docs/plans/hygiene-and-hardening.md`.
- 2026-07-16 13:14 · Staging · Dedicated worktree created via
  `./scripts/preflight.sh --create-worktree elves/hygiene-and-hardening --base origin/main
  --worktree-dir /Users/john/aigora/dev/elves-hygiene-and-hardening`. START_TIP
  `d9862a46fbbcf59759d9b7bb9230494db88d5dec` (collision tripwire).
- 2026-07-16 13:16 · Staging · `.elves-session.json` seeded (run_id, branch, start_head,
  plan_path, worker_packet_path, work_driver, cobbler.default_for_session, continuation_guard);
  `acceptance_contract.py sync-session --write` derived B1–B7 + M-A1..M-A5 rows;
  `validate` → **OK**.
- 2026-07-16 13:18 · Staging · Consolidated coordinator→implementer packet (the prewalk) written to
  `.elves/runtime/worker-packet-hygiene-and-hardening.md` and recorded in session + survival guide.
  This dogfoods B3's packet-at-staging rule before the batch implements it.
- 2026-07-16 13:22 · Staging · Rollback ref `refs/elves/rollback/hygiene-and-hardening-2026-07-16/s1/b0`
  → d9862a46. Staging commit `3eef6b1` pushed; branch tracking origin. PR opened: **#78**
  (https://github.com/aigorahub/elves/pull/78).
- 2026-07-16 13:24 · Staging · Full preflight in worktree: **green**, 3 advisories (survival-guide
  env var, no package manifest — expected for this repo, non-interactive env vars). Survival guide
  validator: first pass flagged missing `## After Any Compaction`; second pass flagged Forbidden
  Stop Reasons wording and the Stop Gate readiness checkbox (the 13:24 commit's "validator green"
  subject was premature — corrected here). Third pass surfaced the full pinned field vocabulary
  (Run Control delegated fields, Cobbler/Stop Gate/Current Phase/Next Exact Batch fields, pinned
  effort and control-loop sentences); guide rewritten fully conformant to the template. Validator
  exit 0 at 13:31. Staging complete → executing.

## Batch 1 [B1] — Redaction exact-value minimum-length guard

- 2026-07-16 17:25 · B1 Repro · Bug reproduced at staging tip in a scratch tmp dir:
  `CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH=1 python3 scripts/cobbler_agents.py implement gate --batch 9
  --focused --cwd <tmp> --repo-root <tmp> --json` emitted `gate_path`/`cwd`/`recorded_at` with every
  literal `1` replaced by `[REDACTED:exact_grant]` (e.g. `python@3.[REDACTED:exact_grant]4`,
  `2026-07-[REDACTED:exact_grant]6T…`). Focused module failed 1/64
  (`test_cli_gate_failure_exit_code`), matching baseline D4.
- 2026-07-16 17:31 · B1 Implement · One shared env-derived exact-secret collector:
  `context.collect_secret_env_values` (keyword-overridable `min_length` defaulting to
  `MIN_EXACT_SECRET_VALUE_LENGTH = 8`). Unguarded `implement.py:_inherited_secret_values` and
  duplicated `cobbler_agents.py:_secret_env_values` deleted; both modules import the collector
  (implement.py:30, cobbler_agents.py:76; all seven cobbler_agents call sites plus `run_gate`
  migrated). `SECRET_VALUE_PATTERNS`/pattern redaction untouched. Regression tests both directions:
  gate record and CLI JSON stay unmangled under short-valued secret-named flags; an exactly-8-char
  secret-named env value still redacts in returned and persisted gate evidence; collector unit tests
  pin the boundary and the allowlist. Commit `5729f97` (+207/−26 across 6 files, incl. CHANGELOG
  under `[Unreleased]`).
- 2026-07-16 17:32 · B1 Validate · After-fix repro emits unmangled `gate_path` that exists on disk
  (`…/T/tmp.tOJh0TNbFH/.elves/runtime/implement/gates/batch-9.json`, 1266 bytes on disk). Revert-check:
  with the source fix stashed and new tests kept, both corruption-direction regressions fail with the
  exact mangled-path signature; the preserve-direction test passes on both sides (proves the tests pin
  the bug, not the implementation).
- 2026-07-16 17:52 · B1 Validate · Focused: `tests.test_cobbler_agents_implement` → 67 tests OK
  (3 skips) with `CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH=1 CLAUDE_CODE_CHILD_SESSION=1` exported;
  `tests.test_cobbler_agents_dispatch` (redaction/`redact_structure` collision tests) → 71 tests OK.
  Full suite twice: flags exported → `Ran 1034 tests … OK (skipped=12)`; Claude Code vars unset
  (CI-like) → identical. Baseline 1,027 tests/1 failure → 1,034/0. `check_repo_consistency.py` → OK.
- 2026-07-16 18:05 · B1 Validate · `verify_repo.py` gates: compileall, shell, json, evidence-review
  (impact-selected focused tests incl. both B1 test modules), consistency, and release
  (`--version Unreleased` mode) all OK; `release_checklist.py --allow-unreleased` exit 0 (warnings
  only, pre-existing staging plan-file review note); `acceptance_contract.py validate --session
  --plan` OK. Two pre-existing, B1-independent verify blockers reported instead of resolved
  (surfaces owned elsewhere): (a) pinned `--version 2.6.0` now stops at the release step because
  `## [Unreleased]` has content — the CHANGELOG entry this batch is required to add; promotion to
  v2.7.0 is terminal work (D2); (b) in either version scope the public-api step rejects the
  `api-break-approvals.json` entry for `cli:cobbler_agents implement full-run-prepare` (declares
  2.6.0; stale against any post-2.6.0-merge diff — reproduced by direct `check_public_api`
  simulation at HEAD and structurally true at the staging tip). That manifest is B4's owned surface.
  B1's own API impact is clean: `compatibility_gate` → ok=True, breaking=[].

## Decisions made

- D1 (staging): Worker route = host-native subscription worker sessions (one per batch, per-batch
  packet derived from the consolidated packet). No external providers probed: none configured, the
  native default satisfies the plan, and the user asked for "the worker agent" without a provider
  preference.
- D2 (staging): Version stays 2.6.0 during batches; expect promotion to v2.7.0 at terminal via
  `release_checklist.py` (new user-facing behavior lands in B2/B3/B5).
- D3 (staging): Merge authorization = explicit chat-to-land from the user in-session
  (2026-07-16). Readiness remains independent and is attested at the exact final HEAD.
- D4 (staging): Known-red baseline recorded — `test_cli_gate_failure_exit_code` fails under
  Claude Code env flags; B1 is the fix and must flip the suite to 0 failures.
