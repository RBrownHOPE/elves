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
  the module's minimum-exact-length constant, default 8). Unguarded `implement.py:_inherited_secret_values` and
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

## B1 reconcile (driver)

- 2026-07-16 14:05 · Reconcile · B1 worker done summary verified on tip 8c33a91: session B1 rows
  complete with evidence; spot gates green (test_cobbler_agents_dispatch OK; consistency OK);
  subjects follow schema (Implement 5729f97, Close 8c33a91); tripwire chain intact.
- 2026-07-16 14:06 · Reconcile · Commit cadence: worker initially accumulated ~6 files uncommitted;
  driver mid-run instruction produced the Implement slice push before Close. Lesson promoted to
  learnings; durable rule added to plan as B3-A5 (user-directed).
- 2026-07-16 14:08 · Reconcile · Plan amended (B3 task + B3-A5); `sync-session --write` added the
  row; B1 proof rows preserved by the helper's refuse-to-rewrite rule; validate OK.

## Batch 2 [B2] — Worktree gc helper and lifecycle hook

- 2026-07-16 14:15 · B2 Contract · Candidate predicate, guarded removal, and zero-mutation report
  pinned as 22 failing fixture tests before any implementation: new `tests/test_worktree_gc.py`
  (20 tests; tempfile repos with a `file://` bare origin so merged/unmerged/ahead/gone-upstream
  states are real) plus two `preflight.sh` wrapper dispatch tests in `tests/test_preflight_sh.py`.
  Git behavior probed first in scratch fixtures: ignored files do not block non-force
  `git worktree remove`; `git branch -d` with a gone upstream falls back to HEAD containment.
  Commit `726cfe5` (red by design, per the D5 cadence rule).
- 2026-07-16 14:25 · B2 Implement · New `scripts/worktree_gc.py` modeled on
  `preflight_worktree.py` idioms (`run_git`, porcelain `parse_worktrees`, argparse,
  `WorktreeError`, advisory-by-default). Report mode is the default and strictly read-only
  (status probes use `git --no-optional-locks`); `--apply` removes candidates via
  `git worktree remove` (never --force) + `git branch -d` (never -D) + one `git worktree prune`
  gated on at least one successful removal. Candidate = registered linked worktree, not the main
  worktree, not the invoking directory, clean tracked+untracked, tip an ancestor of `origin/main`
  (`merge-base --is-ancestor`), zero commits ahead of upstream; gone/missing upstream falls back
  to the ancestor containment proof. Locked, detached, bare, and prunable registrations are
  refused with reasons; unregistered `<repo>-*` siblings are listed operator-owned and never
  deleted. `preflight.sh` gains first-arg `--gc-worktrees` dispatch mirroring the
  `--create-worktree` exec pattern; optional `--path <worktree-dir>` scopes teardown to the run's
  recorded worktree. Focused: 43/43 OK. Commit `f10e202`.
- 2026-07-16 14:35 · B2 Implement · Lifecycle wiring: `config.json.example`
  `cleanup.worktrees: on-merge | report | never` (default `report`, documented preference only —
  no config-driven automation); SKILL.md Reviewed PR Landing step 7 and Final Completion gain the
  post-merge teardown of the run's own recorded worktree; Structured Session Data documents the
  `worktree_path` key (recorded in this run's `.elves-session.json`, which the acceptance
  validator accepts unchanged); kickoff template staging bullet records `worktree_path` and the
  teardown expectation with the pinned create-helper sentence intact; README one-run-one-checkout
  section gains the reclaim paragraph + structure-tree entry. CHANGELOG under `## [Unreleased]`.
  Consistency + release checklist green. Commit `8c90f2c`.
- 2026-07-16 14:45 · B2 Validate · Full suite `python3 -m unittest discover -s tests`:
  `Ran 1056 tests ... OK (skipped=12)` both plain and with
  `CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH=1` exported (baseline 1034 + 22 new). `verify_repo.py --ci
  --version Unreleased --base-ref origin/main`: compileall (53 files)/shell/json/evidence-review
  (full unittest, consistency, release, installed smokes)/consistency/release all OK; sole FAIL is
  the pre-existing B4-owned public-api approval staleness (unchanged from the B2 baseline run of
  the same command). `check_repo_consistency.py` exit 0; `release_checklist.py
  --allow-unreleased` exit 0. B2-A1/A2/A3/A5 evidence recorded in session; B2-A4 (machine dogfood
  removing the merged worktrees from the main checkout) is deliberately left `met: false` for the
  driver at reconcile — workers never touch the main checkout; batch status stays `in_progress`.

## Decisions made (continued)

- D5 (post-B1, user-directed): Worker commit cadence becomes part of the handoff standard —
  ≥1 pushed non-Close progress slice before Close; first slice at first failing test or surface
  change; monolithic Close = reconcile defect. Lands with B3 (B3-A5); enforced by driver packets
  for B2 onward in this run.
- D6 (post-B1): Two pre-existing verify_repo blockers surfaced by B1, routed not fixed:
  (a) `verify_repo.py --version 2.6.0` release step now stops on populated `## [Unreleased]` —
  expected during batches; per-batch gate is the step set the worker ran (compileall/shell/json/
  evidence-review/consistency/release with --allow-unreleased); strict full `--version` runs at
  terminal after D2's promotion decision. (b) Stale `api-break-approvals.json` entry
  (`cli:cobbler_agents implement full-run-prepare`, release 2.6.0) is rejected in current-diff
  scope — pre-existing on main; B4 owns that manifest and refreshes/removes it with its own
  approvals.
- D7 (B2): Unpushed-commit clause semantics — when a branch's upstream ref resolves, the gc
  requires zero commits ahead of it (fixture-isolated: a tip merged into origin/main but ahead of
  its own upstream is refused). When the upstream is gone or was never set, the ancestor check
  against origin/main is the containment proof: everything reachable from origin/main is on the
  remote, and the post-merge GitHub branch auto-delete makes gone upstreams the normal state for
  exactly the worktrees this helper exists to reclaim. A genuinely unpushed commit can never be an
  ancestor of origin/main, so the contract sentence "a clean-but-unpushed worktree is refused"
  holds in both regimes.

## B2 reconcile (driver)

- 2026-07-16 15:05 · Reconcile · B2 verified on tip 9264c6a: 4 cadenced slices
  (Contract 726cfe5 → Implement f10e202 → Implement 8c90f2c → Close 9264c6a); focused
  test_worktree_gc OK; session rows A1/A2/A3/A5 met, A4 correctly deferred.
- 2026-07-16 15:10 · Reconcile · B2-A4 dogfood executed from the main checkout (driver-owned):
  report mode listed exactly the five staging-measured merged worktrees as candidates; --apply
  removed all five and deleted their local branches; refusals held (main+invoking checkout, five
  unmerged benchmarks — two also unpushed, this run's worktree); four unregistered siblings listed
  operator-owned, untouched; post-state registry = main + 5 benchmarks + run worktree. B2 marked
  complete in session with evidence.

## Decisions made (continued 2)

- D7 (from B2 worker, ratified at reconcile): post-merge upstream-gone regime — when a branch's
  upstream no longer resolves (GitHub auto-delete after merge), the origin/main ancestor check is
  the unpushed-work containment proof; when upstream resolves, ahead-count must be 0. Both regimes
  fixture-tested.
- D8 (driver): dogfood invoked the branch copy of worktree_gc.py by absolute path with the main
  checkout as cwd — same pattern as installed-skill helpers (helper from skill root, target repo
  as working directory). No flag surface existed on main yet; this is the expected mid-run shape.

## B3 (host-native takeover by driver)

- 2026-07-16 16:05 · Blocker · B3 worker session crashed 3x on transient API 529 (before any edits
  each time; tip verified clean at e47d9f8 after each). Re-drive budget (2) exhausted -> host-native
  takeover per Run Control.
- 2026-07-16 16:10 · Contract · Failing validator tests pushed (340b166): WorkerPacketStagingWarningTests
  pins warn-not-block, spelling normalization, silence for host-native/with-path.
- 2026-07-16 16:20 · Implement · Advisory warnings channel in acceptance_contract.py
  (_worker_packet_warnings + _normalize_work_driver; warnings never touch exit codes); test class
  made standalone to avoid re-collecting the parent suite (d30481f).
- 2026-07-16 16:40 · Implement · Six doc surfaces (SKILL.md staging line + cadence/phase-roles in
  handoff standard; survival-guide template field; schema-and-acceptance canonical sections;
  plan-template note; kickoff bullets x2; grok launch prompt echo); behavior_policy.py comment
  points at the canonical map, values unchanged.
- 2026-07-16 16:55 · Validate · consistency exit 0 (no pin churn); full suite 1,062/0 plain AND
  with CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH=1; release_checklist --allow-unreleased exit 0.

## Decisions made (continued 3)

- D9 (B3): host-native takeover after 3x 529 crashes consumed the 2-re-drive budget. The takeover
  driver authors B3's single Close as the implementer; reconcile remains a separate Review commit.
- D10 (defect log, per new phase-role rule): two pre-B3 driver reconcile commits are mislabeled by
  the now-codified convention — 39bd71d and e47d9f8 used a second `Close` for their batch and
  39bd71d also carried a B3 plan amendment under a Batch 1/7 label. History is not rewritten
  (force-push forbidden); the convention now lives in SKILL.md via B3-A5.

## Decisions made (continued 4)

- D11 (user-directed, during B4 transient crashes): plan gains B8 — worker failure recovery
  policy. Transient provider errors retry with escalating backoff (5m → 10m → 20m) and never
  consume the substantive re-drive budget; workers maintain an untracked progress ledger under
  .elves/runtime/ from orientation onward. Applied operationally to B4 immediately (two 529
  crashes so far, zero budget consumed, backoffs 5m then 10m); codified in contract text by B8.
  Amendment committed under the Batch 8 · Contract label per the B3 phase-role rule, in the
  driver-safe window while no worker was active (index-race avoidance).

## B4 (split: driver host-native + stopped worker)

- 2026-07-16 21:35 · Implement · Driver slice 5501593: ensure_private_dir consolidation (live
  divergence found: dispatch modules imported the weak variant), ELVES_SESSION_BASENAME + 4
  adopters, cycle-free hoists (native_worker/config/worker_routing/preflight_cache), consumed
  api-break approval removed (public-api gate green, count=0).
- 2026-07-16 22:05 · Validate · Driver slice 5b84c0c: openrouter_lens stdin-before-fail-closed
  hang fixed (3-hour verify hang root-caused: inherited never-closing pipe + unpinned test stdin);
  proven terminating under the exact hang scenario; full verify --ci green bounded.
- 2026-07-16 22:40 · Implement · User directed worker stop + host-native finish. Worker's B8-style
  ledger survived its final crash and confirmed orientation-only state (mechanism validated).
  Driver migrated all 16 full_run.py git call sites to run_git (hardened with stdin=DEVNULL +
  timeout param), swapped the last session literal, hoisted leases/implement/delegated_git lazies,
  and corrected two lazy-import comments that guard REAL cycles (risk_policy and behavior_policy
  import full_run at module level — the no-cycles survey assumption was wrong for these two).
- 2026-07-16 22:55 · Close evidence · Suite 1,062/0 both env shapes; characterization
  test_full_run_supervisor 145/OK; consistency 0; verify --ci zero FAILs; 0 raw git subprocess
  sites; literal count exactly 1.

## Decisions made (continued 5)

- D12 (user-directed): all remaining work is driver host-native; B4 worker stopped after a fourth
  silent transient death. The worker progress ledger proved its value (orientation state survived).
- D13: leases.run_git hardened as the B9 chokepoint for git subprocesses (stdin always closed,
  optional timeout) — 72 existing call sites inherit it; no stdin-reading git subcommand exists in
  the codebase (verified).

## Decisions made (continued 6)

- D14 (B9 defect found in-run and fixed): B9 closed with evidence that held only under lucky
  runners — `discover -s tests` never imports tests/__init__, so the fd-0 guard did not load under
  the gate invocation, and the hermeticity probe itself hung on a socket stdin (55m wall / 36s CPU
  signature). Fix: `-t .` on the canonical invocation everywhere, self-verifying guard, hang-proof
  probe (fstat first, non-blocking read). Post-Close corrections labeled Batch 9/9 · Validate;
  B9-A2 evidence amended in session. Lesson: a guard's own test must fail loudly when the guard
  did not load, and proofs must run under the hostile runner they claim to defend against.

## B5 (driver host-native)

- 2026-07-17 · Implement · README rewritten 1,688 -> 372 lines; glossary + operations-guide
  created; narration swept from 7 feature docs; 22 pin-sets removed with 3 forbidden corpora
  restored and minimal true pins re-added; meta-tests updated to linked-not-restated doctrine.
- 2026-07-17 · Validate · Full suite 1,067/0 under a hostile socket-stdin runner via the fixed
  `discover -s tests -t .` invocation (219s) — doubles as B9's re-proof.

## Decisions made (continued 7)

- D15 (defect log, self-reported): the Batch 9/9 Validate commit 660f0db used `git add -A` in a
  working tree that also carried B5 content, so B5's files landed under a Batch 9 label —
  violating the batch-content rule codified in B3. History stays (no force push); staging is
  explicit-paths-only for the rest of the run.

## B6 (driver host-native)

- 2026-07-17 · Implement · AGENTS.md rewritten 108 -> 89 lines as a validated thin pointer file
  (all 33 pointer phrases + Cobbler section + recovery order + installed helper-path contract);
  policy collapse: 24 shims + 26 AGENTS entries + stray landing-check comprehension handled; one
  AGENTS_POINTER_PHRASES whole-file check wired; meta-tests updated to the pointer doctrine.
- 2026-07-17 · Validate · checker 0; meta-tests OK; installed-bundle smoke OK (helper-path contract
  restored); full suite 1,067/0 under hostile socket-stdin runner.

## Decisions made (continued 8)

- D16 (user-authorized re-scope): B6-A1 amended from ">=50% line reduction" to the achieved
  mechanical collapse (17.5%) + documented pin policy, because the remaining mass is per-reference
  prose pins whose removal requires single-sourcing ~40 reference files — a follow-up plan, not a
  wrap-up item. Recorded in the plan text itself.

## B7 (driver host-native)

- 2026-07-17 · Implement · Three extractions with AST-driven external scans and fixpoint constant
  moves; byte-identity proof for the supervisor program; smoke failure (importing the program
  executes it) fixed by relocating it outside the package; 29 mock-namespace errors root-caused to
  one lazily-cached module global and one patch-location inversion.
- 2026-07-17 · Validate · Characterization 145/OK at every step; full suite 1,067/0 hostile;
  consistency 0; full_run.py 7,019 lines.
