# Execution Log — audit-follow-ups run

Chronological record. Newest entries at the bottom. Archive old entries under
`## Completed Archive` if this grows large.

---

## 2026-07-18 ~23:00 — Batch 0: Staging

**What happened:**
- Audit deliverable landed separately: `docs/reviews/2026-07-repo-audit-grok-prewalk.md` on
  branch `audit/repo-audit-grok-prewalk`, PR #82 against upstream `aigorahub/elves`.
- User authorized the full arc in-session: stage run -> hand off to Fable medium -> review ->
  land the PR (chat-to-land). Landing target is fork `RBrownHOPE/elves` `main` because the user
  has READ on upstream.
- Created dedicated worktree `/Users/ruthbrown-ennis/research/dev/elves-audit-follow-ups`,
  branch `feat/audit-follow-ups` from `origin/main` (`f32ce0d`, collision tripwire).
- Wrote plan (`docs/plans/audit-follow-ups.md`, batches B1-B4 + M-A1..M-A3), survival guide,
  learnings, this log, `.elves-session.json`.

**Preflight notes:**
- `gh auth status`: authenticated as RBrownHOPE; push to fork verified (audit branch pushed
  earlier this session).
- `install_doctor.py --startup`: advisory only (repo checkout active).
- Python: only 3.9.6 available locally; CI matrix is 3.10/3.12/3.14. Known pre-existing local
  suite state: 1134 tests, 18 errors confined to sync/bundle modules (3.10+ `Path.parents`
  slicing), 14 deterministic skips. B1 makes local runs green-with-skips.
- `claude` CLI binary: not installed. Supervised `native-worker` CLI transport unavailable;
  worker = in-session native subagent pinned `claude-fable-5` effort `medium`. Requested/actual
  route recorded in `.elves-session.json` `model_routes`.
- Prewalk for this run's worker: requested `auto`, actual `off`
  (`supervised_cli_transport_unavailable`). The plan's content still implements the Grok prewalk
  port (feature-gated off).
- Repo consistency smoke: deferred to first Verify Green (runs as part of gates).

**Batch breakdown and estimates:**
- B1 runtime correctness/robustness — est. 45-75 min including suite runs (~5 min per full run
  on this machine).
- B2 host registry + grok arm (feature-gated) — est. 90-150 min; highest blast radius.
- B3 grok qualification tooling — est. 60-90 min.
- B4 contracts/glossary/changelog/pins/hygiene — est. 60-90 min; consistency-pin churn risk.

**Decisions made:**
- Single-kickoff continuation after staging (user explicitly authorized the full arc; recorded
  in Run Control).
- No release promotion; all changelog content under `[Unreleased]`.
- Git mode `host_only`: worker subagents edit files; driver owns commits/pushes with the
  five-phase subject schema.

**Next:** commit staging docs, push, open PR against fork main, staging acceptance validation,
write worker packet, tag `elves/pre-batch-1`, launch B1 worker.

---

## 2026-07-18 ~10:55 — Batch 1: Runtime correctness and robustness fixes — COMPLETE

**Timing:** worker implement ~13m (fable/medium, 77 tool calls) · driver validate ~15m · review ~20m · review fixes ~7m.

**Contract:** plan Batch 1, B1-A1..B1-A6 — all met (evidence in `.elves-session.json`).

**What changed:** floor guards + version-gated tests (sync_installed_skills, installed_bundle_smoke); event-typed identity capture (`_IDENTITY_EVENT_KEYS`, system/init subtype gate); transient markers scoped to stderr + provider error events; canonical `schema.AMBIGUOUS_SESSION_TOKENS` (5 consumer sites after review fixes); TimeoutExpired-safe terminalization with honest `native_worker_git_timeout`; torn-line follower with seek-back; stderr_tail preserved alongside provider events; guide-recovery post-edit reclassification; PREWALK_FAILURE_CODES source-scrape enforcement (6 emission shapes); dead helper removed; fd close on child streams. New `tests/test_native_worker_hardening.py` (24 tests).

**Validation:** focused 154 OK (27 skips); consumer suites 252 OK (2 skips) after review fixes; full suite on 3.9: **Ran 1158, OK, 38 skips, exit 0** (baseline was 1134 with 18 pre-existing errors + 14 skips); `check_repo_consistency.py` exit 0; compileall clean.

**Review (independent subagent):** 0 BLOCKING, 6 WARNING. Dispositions: W1 per-host identity strictness → deferred to B2 registry (by design; registry owns identity event types per host); W2 residual token-set literals → fixed now (full_run.py, openrouter_lens.py) + comment corrected; W3 inventory regex blind spots → fixed now (missing_code/invalid_code/code= shapes); W4 unbounded git helpers in git_contract/leases default run_git → pre-existing, deferred `[elves-scout]` (log here; TODO.md restructure lands in B4); W5 authority-timeout misattribution → fixed now (distinct reason when the only error is the timeout sentinel); W6 dead `_is_provider_event` + double parse → fixed now.

**Decisions made:** kept the identity map host-agnostic for B1 (per-host strictness is a B2 registry column); extended token unification beyond the plan's named sites because centralize-over-duplicate is the plan's own principle and the swap is behavior-narrowing only.

**Regression attestation:**
1. Cumulative diff: `git diff main...HEAD --stat` = staging docs + 11 code/test files, all in-scope; no unexpected deletions.
2. Shared surfaces: native_worker.py (consumers: cobbler_agents native-worker CLI, worker_routing, prewalk supervisor tests), schema.py additive constant, adapters/implement/full_run/openrouter_lens token-set swap (behavior-narrowing: more tokens rejected). Consumer suites green: full_run_supervisor 146, worker_cli_lifecycle, adaptive_worker_routing, dispatch/implement 139, sessions 41, public_api_snapshot 40.
3. Public API surface: additive only (`AMBIGUOUS_SESSION_TOKENS`); snapshot suite green.
4. Test baseline: total 1134 → 1158 (+24, nothing deleted); errors 18 → 0 (floor-gated skips); skips 14 → 38 (24 = version-gated sync/bundle classes incl. 9 that formerly ran on 3.9 — accepted per floor policy, CI covers them).
5. Confidence: HIGH — narrowing changes are proven by targeted tests on both accept and reject sides; every consumer suite ran green; the one coverage trade (9 tests now skipped locally on 3.9) is explicitly covered by the CI matrix.

**Commits:** d785112 (Implement). Rollback tag: `elves/audit-follow-ups/pre-batch-1`.

**Docs impacted:** none durable this batch (CHANGELOG + .ai-docs land in B4 per plan). PENDING-DOCS: changelog entry for B1 carried to B4 by design.

**Next:** tag pre-batch-2; launch B2 worker.

---

## 2026-07-18 ~12:20 — Batch 2: Host profile registry and feature-gated Grok prewalk arm — COMPLETE

**Timing:** worker implement ~42m (fable/medium, 79 tool calls) · driver validate ~12m · dual review ~26m (standard + regression-only lenses, parallel) · review fixes ~17m.

**Contract:** plan Batch 2, B2-A1..B2-A5 — all met (evidence in `.elves-session.json`).

**What changed:** new `cobbler_runtime/host_profiles.py` registry (codex/claude/fixture/grok rows) consumed by `build_native_worker_spec`, child-env secret projection, launch identity readiness, prewalk advertised/probe functions, and worker_routing transport naming; per-host identity event typing (resolves B1-review W1); feature-gated grok arm with exact verified argv; qualification-based prewalk gating replacing the categorical veto, with the B3 data-in seam. Worker wrote byte-identity tests against the pre-refactor code before rewiring (as directed).

**Validation:** worker suite 1179 OK; driver re-run 1179 OK; post-review-fix full suite **1180 OK / 0 fail / 0 error / 38 skips (3.9)**; miniforge 3.13: touched suites 86 OK (floor-gated modules actually run), dispatch modules 20 failures pre-existing at HEAD (env artifact; green on 3.9); consistency gate aligned; public API snapshot ok=true, breaking=[].

**Review:** two parallel lenses. Standard: 0 BLOCKING, 4 WARNING — all four fixed in-batch: W1 launch gate now registry-driven (`launch_ready`) at both CLI and `launch_native_worker`; W2 `GROK_AUTH_PATH` removed from the grok allowlist (isolation-control contract; API-key route only until the provider_auth-validated projection is wired); W3 unqualified-grok payload now reports grok-scoped capability facts (native evidence can no longer appear under grok_build transports); W4 `qualification_fixture_evidence_forbidden` in the gate + tests updated to loader-realistic evidence sources. Regression-only: 0 CONFIRMED-BREAK; 3 plausible-risks accepted and recorded (unknown-host error precedence, message reword pinned nowhere, host-less union vocabulary growth); independently re-proved byte-identity via a 32-case differential harness against HEAD.

**Decisions made:** grok resume argv keeps `--permission-mode auto --output-format streaming-json` (conservative; sandbox resume-sticky per verified grammar); commit mode named `permission_gated_worker_commit`; B3 preconditions recorded — loader must never emit fixture-sourced evidence and must bind host=grok/transport=grok_build exactly.

**Regression attestation:**
1. Cumulative diff: staging docs + B1 files + 7 B2 files; no unexpected deletions; untracked registry/test files staged (regression-lens reminder).
2. Shared surfaces: spec builder, exact-session validator, identity capture, profiles view, probe functions, route decision — all consumers traced by the regression lens; native behavior byte-identical (differential matrix), grok-only additions fail closed.
3. Public API surface: additions only (compatibility gate ok).
4. Test baseline: 1158 → 1180 (+22; nothing deleted/weakened — the two adjusted new-test expectations tightened assertions to honest behavior before ever landing).
5. Confidence: HIGH for native surfaces (dual-lens + differential proof); grok surfaces are unreachable in production paths (three independent fail-closed gates: CLI, launch API, routing qualification).

**Commits:** 912dacf (Implement, review fixes folded). Rollback tag: `elves/audit-follow-ups/pre-batch-2`.

**Docs impacted:** none durable this batch (B4 owns changelog/.ai-docs/contract amendments). PENDING-DOCS: B2 registry architecture note carried to B4 by design.

**Next:** tag pre-batch-3; launch B3 worker.

---

## 2026-07-18 ~13:05 — Batch 3: Grok prewalk qualification tooling — COMPLETE

**Timing:** worker implement ~20m (fable/medium, 48 tool calls) · driver validate ~11m · adversarial review ~11m · review fixes ~15m.

**Contract:** plan Batch 3, B3-A1..B3-A3 — all met (evidence in `.elves-session.json`).

**What changed:** static probe `prewalk-capabilities --host grok` (registry-driven, zero model calls, fail-closed); `grok_prewalk_qualification_canary` schema v1 + `load_grok_prewalk_qualification` (18-field exact set, canonical UUID, version+build binding, fd-bound O_NOFOLLOW reader); `route-worker --grok-prewalk-qualification`; current-version help fixture; 14 new tests incl. 29-case mutation coverage. `worker_routing.py` unchanged — the B2 seam absorbed the loader as designed.

**Validation:** full 3.9 suite **1194 OK / 0 / 0 / 38 skips**; consistency aligned; public API additions only; 3.13 spot suites (prewalk + routing, 93 tests) OK.

**Review (adversarial-parser lens):** 0 BLOCKING, 2 WARNING, both fixed in-batch: (1) TOCTOU check-then-read window → replaced with the goal-canary fd-bound pattern (O_NOFOLLOW open, fstat dev/ino identity, mode/size checks on the read descriptor); (2) dead `installed_build_commit` parameter → probe now parses the `(hex)` build commit from `grok --version` and binds the artifact to it, with mismatch tests. Reviewer judged worker decisions #3 (advertised-from-artifact: behavioral create/resume evidence subsumes help grammar) and #4 (self-binding artifact, no probe pairing) SOUND. Added 3 reviewer-suggested mutation locks (string schema_version, urn-form UUID, uppercase artifact_type). One in-flight defect caught by tests before commit: missing `os` import in prewalk.py.

**Deferred:** native evidence loader (`load_prewalk_capability_evidence`) shares the older path-based read pattern — `[elves-scout]` hardening item (same fd-bound treatment); optional narrowing of standalone advertised_* payload annotation.

**Regression attestation:**
1. Cumulative diff: in-scope files only; fixture added; no deletions.
2. Shared surfaces: prewalk.py additions + one behavior-identical extraction (`_qualification_phase_routes`) proven by the untouched native loader tests; worker_routing untouched.
3. Public API: additions only.
4. Test baseline: 1180 → 1194 (+14; nothing deleted/weakened).
5. Confidence: HIGH — hostile-artifact review with empirical attacks; every attack failed closed; the two hardening gaps found were fixed and test-locked.

**Commits:** 9dbe649 (Implement, review fixes folded). Rollback tag: `elves/audit-follow-ups/pre-batch-3`.

**Docs impacted:** none this batch (B4 owns all doc surfaces; operator canary procedure documented there per plan).

**Next:** tag pre-batch-4; launch B4 worker (final batch), then final readiness review and landing.

---

## 2026-07-18 ~13:45 — Batch 4: Contracts, glossary, changelog, consistency pins, doc hygiene — COMPLETE

**Timing:** worker implement ~20m (fable/medium, 73 tool calls) · driver validate ~12m · honesty/accuracy review ~11m · review fixes ~6m.

**Contract:** plan Batch 4, B4-A1..B4-A3 — all met (evidence in `.elves-session.json`).

**What changed:** prewalk.md doorway amendment + Grok parity column (feature-gated, unqualified) + qualification-artifact documentation; adaptive-worker-routing/host-parity provider=grok semantics + release-honesty extension; grok-open-source-worker non-yolo prewalk-lane section + operator canary proof obligations; 12 glossary entries added, dead entry removed; five hygiene items closed; CHANGELOG [Unreleased] for all four batches; .ai-docs registry note + pin-coupling gotcha; 15 strictly-additive consistency pins landed with their sentences.

**Validation:** full 3.9 suite **1194 OK / 0 / 0 / 38 skips** (docs batch, no test delta); consistency gate green after every touched file and at close; linter tests 80/80; release_checklist --allow-unreleased exit 0.

**Review (honesty/accuracy lens):** 0 BLOCKING, 1 WARNING fixed (repo-only audit-doc citation hedged with the installed-bundle formula), cardinal-rule verdict **CLEAN** — no sentence claims Grok prewalk availability or qualification; all technical doc claims verified against B1-B3 code; CHANGELOG verified 1:1 against actual commits. F1 precision nit also fixed (allow_grok=false-veto phrasing); F2-F5 accepted as summary-level imprecision. Worker self-flagged one packet deviation (a git stash/pop cycle to baseline release_checklist); driver verified tree integrity (identical diffstat, empty stash list) — recorded, no harm.

**Regression attestation:** docs + additive pins only; no scripts touched except consistency_policy.py (additive); suite unchanged at 1194; confidence HIGH.

**Commits:** 92e69ca (Implement, review fixes folded). Rollback tag: `elves/audit-follow-ups/pre-batch-4`.

**Next:** final cumulative readiness review; Elves report; landing check; operational-artifact cleanup; merge PR #1 into fork main (chat-to-land authorized). M-A1/M-A2 recorded met with evidence; M-A3 pending the landing check. Known constraint for the user: fork Actions are dormant (one-click activation at github.com/RBrownHOPE/elves/actions) — CI matrix will run once activated or when an upstream sibling PR is opened.
