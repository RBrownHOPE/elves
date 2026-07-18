# Audit follow-ups: runtime hardening, Grok prewalk host port, contract amendments

Source: `docs/reviews/2026-07-repo-audit-grok-prewalk.md` (PR #82, branch
`audit/repo-audit-grok-prewalk`). That review audited this repo at `f32ce0d` (v2.9.0) and verified
Grok Build 0.2.102 source capabilities with 14 adversarial verification passes. This plan
implements its recommendations. All changes stay under `[Unreleased]` in the changelog; no release
promotion in this run.

## Why

The audit confirmed exact-session prewalk on a Grok Build worker is mechanically feasible: the
harness supports caller-UUID `--session-id`, strict exact `--resume`, model/effort route override
applied on resume, `--cwd`, streaming JSON, and a non-yolo `--permission-mode auto` lane. What
blocks it is Elves-side by design: a categorical external-provider veto, ~10 scattered host
`if/elif` sites with no host abstraction, and missing Grok qualification tooling. Separately, the
audit found runtime robustness defects worth fixing before a third host multiplies them.

Prewalk for Grok remains **feature-gated off** after this run: we build the port and the
qualification tooling, but activation still requires operator-authorized live canaries and
version-bound `retained_safe` evidence that no host (including Codex/Claude) has yet. No release
may claim Grok prewalk availability.

## Constraints and non-negotiables

- Local interpreter is Python 3.9.6; CI runs 3.10/3.12/3.14. After B1, the local suite must be
  green-with-skips on 3.9; CI behavior must be unchanged.
- Never weaken or delete a test to get green. The 18 pre-existing 3.9 errors are fixed by explicit
  version gates with clear messages, not by loosening assertions.
- Repository consistency gates (`check_repo_consistency.py`, `verify_repo.py`) must pass; every
  edited normative sentence needs its consistency-policy pins updated in the same batch.
- `allow_grok=false` remains an absolute veto; consent rules unchanged; no new authority for any
  worker. The current supervised transport activates prewalk only on `retained_safe` evidence.
- Public API snapshot gate: additions are expected (new registry/probe surfaces); no breaking
  changes to existing exported names (`full_run` re-export doctrine applies).

## Batches

### Batch 1: Runtime correctness and robustness fixes

Scope (audit recommendations #1, #2, #3, #5, #7, #11 plus two small verified defects):

1. Interpreter floor: add an explicit Python >= 3.10 guard with a one-line clear error to
   `scripts/sync_installed_skills.py` and `scripts/installed_bundle_smoke.py` entry points;
   version-gate their test modules (`unittest.skipIf(sys.version_info < (3, 10), ...)`) so a 3.9
   run reports skips, not 18 `TypeError`s. Do not change behavior on >= 3.10.
2. Event-typed identity capture: `_provider_session_id` acceptance must require the host's known
   identity event type value (codex: `thread.started`; claude/fixture: the documented init/system
   event or caller-preset id), not merely the presence of any string `type` key
   (`scripts/cobbler_runtime/native_worker.py:465-471, 672-692, 824-842`).
3. Transient-failure scoping: `_transient_provider_failure` markers must apply only to stderr
   lines and provider-error events, never to task stdout content
   (`native_worker.py:445-462, 810-818`).
4. Forbidden-session-token unification: one canonical forbidden set (including `continue`,
   `most-recent`, `most_recent`) shared by `native_worker.py:158-162`, `implement.py:1443`, and
   the sessions/adapters checks; keep the leading-dash guard.
5. Supervisor error handling: catch `subprocess.TimeoutExpired` around git helpers on the
   supervise/status paths with a terminal state write; make `follow_native_worker` tolerate torn
   JSON lines; preserve a bounded `stderr_tail` even when provider events were emitted.
6. Failure-code inventory wiring: emitted `prewalk_*` failure reasons must validate against
   `PREWALK_FAILURE_CODES` (test-time enforcement is acceptable); fix
   `_guide_recovery_failure_reason` to report the post-edit code when HEAD moved.

Acceptance criteria:

- [ ] B1-A1: On Python 3.9, `python3 -m unittest discover -s tests -t .` reports 0 failures and 0
  errors, with the sync/bundle modules skipped via an explicit version gate whose message names
  the 3.10 floor.
- [ ] B1-A2: A worker stdout line `{"type": "log", "session_id": "<foreign-uuid>"}` no longer
  binds or mismatches session identity on any host; a new regression test proves it, and the
  codex `thread.started` and claude caller-preset paths still bind correctly.
- [ ] B1-A3: A failing execution phase whose stdout contains the word "timeout" is classified
  terminal (not transient); a stderr/provider-error transport failure is still classified
  transient; both proven by new tests.
- [ ] B1-A4: A session id literally spelled `continue` (and `most-recent`/`most_recent`) is
  rejected by the shared validator everywhere it is used, proven by tests exercising the shared
  set from at least two call sites.
- [ ] B1-A5: New tests prove a hung-git `TimeoutExpired` produces a terminal failed state (not a
  raw traceback), a torn follow-log line is skipped by the follower, and `stderr_tail` survives
  when provider events exist.
- [ ] B1-A6: A test asserts every failure reason the supervisor can emit is a member of
  `PREWALK_FAILURE_CODES`, and the guide-recovery misclassification case now reports
  `prewalk_post_edit_cold_fallback_forbidden`.

Blast radius: `native_worker.py` (shared with all native-worker launches), `implement.py`,
`prewalk.py`, two entry scripts, their tests. Additive plus behavior-narrowing on identity/
transient classification; existing fixture-host tests must keep passing unchanged. Risk: medium.

### Batch 2: Host profile registry and feature-gated Grok prewalk arm

Scope (audit recommendation #4, phase 1 of the port):

1. Extract a real host-profile registry in `cobbler_runtime` (single table consumed by code, not
   display-only): per host — create/resume argv builders, effort flag grammar, transport name,
   identity event type and source, provider-secret allowlist, help-probe argv, commit mode.
   Rewire `build_native_worker_spec`, `_native_worker_child_env`, `launch_native_worker` identity
   readiness, `prewalk.py` advertised/probe functions, and the `worker_routing.py` transport
   ternaries to consume it. `native_worker_profiles()` becomes a view of the registry.
2. Add the `grok` host entry, feature-gated: create argv
   `grok --session-id <uuid> --cwd <worktree> --model <id> --effort <level> --permission-mode auto
   --output-format streaming-json` with prompt via the packet surface; resume argv
   `grok --resume <uuid>` plus execution model/effort; caller-generated UUID identity (claude-style);
   secret allowlist `XAI_API_KEY`/`GROK_AUTH_PATH` (reuse `provider_auth` validation, do not
   duplicate); never `--always-approve`, never `--yolo`, never `dontAsk` on this lane.
3. Replace the categorical veto at `worker_routing.py:997-999` with qualification-based gating:
   non-native provider prewalk requires repo allow (not vetoed), explicit consent, and a valid
   Grok prewalk qualification artifact; absent any of those, record honest
   `prewalk_capability_unavailable:grok_prewalk_unqualified:<concrete-reason>`. Default outcome
   for every current environment is unchanged: actual mode `off`.
4. CLI: `native-worker` `--host` gains `grok` for `spec` and `prewalk-capabilities` only; `launch`
   with `--host grok` fails closed with a stable diagnostic until a qualification artifact is
   supplied and valid.

Acceptance criteria:

- [ ] B2-A1: A host-profile registry exists and is consumed by spec construction, child-env
  secrets, identity readiness, probe argv, and transport naming; the codex/claude `if/elif`
  chains at the audited sites are gone, and all pre-existing native-worker/prewalk/routing tests
  pass unchanged.
- [ ] B2-A2: `native-worker spec --host grok` emits the exact create/resume argv above (proven by
  test), with `--permission-mode auto`, without any yolo/always-approve/dontAsk token, and with
  the caller-generated UUID recorded before launch.
- [ ] B2-A3: With no qualification artifact, `route-worker` with provider grok and prewalk
  auto/required reports actual mode `off`/fails before launch respectively, with the new concrete
  fallback reason; `allow_grok=false` still vetoes regardless of evidence, proven by tests.
- [ ] B2-A4: The grok child environment passes through only the documented auth names via the
  registry allowlist, proven by a test that asserts `XAI_API_KEY` survives and arbitrary secrets
  are stripped.
- [ ] B2-A5: Public API snapshot gate passes with additions only; no existing exported name
  changes.

Blast radius: high — `native_worker.py`, `prewalk.py`, `worker_routing.py`, `cobbler_agents.py`
shared by every native run. Mitigation: registry extraction first with tests green, grok arm
second; behavior for codex/claude must be byte-identical argv (assert in tests). Risk: high.

### Batch 3: Grok prewalk qualification tooling

Scope (audit recommendation #4, phase 2 of the port):

1. Static probe: `native-worker prewalk-capabilities --host grok --json` reads installed
   `grok --help` grammar (no model calls) and reports only `advertised_exact_resume` and
   `advertised_route_override_on_resume`, mirroring the codex/claude probes; a recorded
   grok help fixture (current-version) backs the tests.
2. Behavioral qualification artifact schema for Grok prewalk, modeled on
   `grok_goal_terminal_canary` and the existing prewalk evidence loader: bounded (<= 64 KiB),
   regular non-symlink file, exactly-required fields binding host `grok`, transport `grok_build`,
   exact installed version and build commit, canonical session UUID, both phase routes with model
   and effort, create/resume exit records, same-worktree/session/stream continuity facts,
   guide-only fact retention, no-packet-replay, model-call provenance, and an explicit
   instruction-fidelity result. Loader fails closed on any missing/mismatched/unsafe field.
3. `qualified()` semantics for grok mirror the native rule: activation only on `retained_safe`
   under the current persisted-instruction transport; `pruned`/`turn_scoped` remain recorded but
   non-activating.
4. Document the operator canary procedure (what a live canary must prove, including the
   unattended-commit question for `--permission-mode auto`) in the B4 docs; tooling here only
   validates artifacts, it never fabricates them.

Acceptance criteria:

- [ ] B3-A1: `prewalk-capabilities --host grok` returns fixture-backed advertised grammar with
  zero model calls; absent an installed grok binary it reports a concrete unavailable reason
  instead of erroring.
- [ ] B3-A2: The artifact loader accepts a golden valid grok qualification artifact and rejects
  each single-field mutation (wrong host, wrong version, wrong session, missing route, fidelity
  `pruned`, oversized, symlink) with a stable diagnostic, all proven by tests.
- [ ] B3-A3: With a golden `retained_safe` artifact present, `route-worker` provider grok +
  prewalk required passes the gate in a fixture environment; with `pruned` it fails before
  launch; both proven by tests without any live grok invocation.

Blast radius: new module surface plus `worker_routing.py`/`prewalk.py` integration. Risk: medium.

### Batch 4: Contracts, glossary, changelog, consistency pins, doc hygiene

Scope (audit recommendations #6, #9, #10 and the port's contract amendments):

1. `references/prewalk.md`: amend the external-provider clause — providers remain off unless
   separately qualified (the `adaptive-worker-routing.md` doorway becomes the governing sentence
   here too); reword the Promise to "subscription-native or separately qualified worker session";
   add the Grok column to the host table (fresh identity `--session-id` UUID; exact resume
   `--resume`; route flags with resume; `--cwd`; streaming JSON; `--permission-mode auto`
   authority; sandbox resume-sticky note; TODO mirror is the private JSON, not host plan state).
2. `references/adaptive-worker-routing.md` and `references/host-parity.md`: document
   `prewalk-capabilities --host grok`, provider=grok x `worker.prewalk` semantics (auto falls back
   honestly; required fails before launch when unqualified), and extend the release-honesty rule
   to external providers.
3. `references/grok-open-source-worker.md`: add the prewalk-lane section (non-yolo, no push
   grants, narrow authority) distinct from the trusted full-run lane; note 0.2.102 verification
   and the vestigial `plan.json` caveat.
4. `references/glossary.md`: add prewalk, guide route, execution route, instruction fidelity (all
   four states), behavioral qualification artifact, canary, Lane A, Mode A1, main driver, work
   driver, implementation lane, state capsule; remove the dead "Handling matrix" entry.
5. Doc hygiene: fix the installed-bundle dead link in `references/councilelves-launch-prompt.md`;
   add `opencode-cli` to the canonical work-driver spelling map in
   `references/schema-and-acceptance.md`; reconcile the `grok-4.5` framing between `SKILL.md` and
   the routing doc; add version-applicability markers (0.2.93 vs >= 0.2.101) in
   `references/grok-implementer-launch-prompt.md`; split `TODO.md` into a live backlog with a
   `## Completed Archive`.
6. `CHANGELOG.md` `[Unreleased]`: one entry per batch theme. Update `consistency_policy.py`
   phrase pins for every edited normative sentence, and `.ai-docs/*` where architecture truths
   changed (host registry, grok prewalk gating).

Acceptance criteria:

- [ ] B4-A1: `python3 scripts/check_repo_consistency.py` passes with the amended contracts and
  updated pins; no forbidden-phrase guard regressions.
- [ ] B4-A2: Every glossary term listed above resolves (new entries present, dead entry removed),
  and `prewalk.md`/`host-parity.md`/`adaptive-worker-routing.md` carry the Grok amendments with
  no sentence claiming Grok prewalk is available or qualified.
- [ ] B4-A3: The five doc-hygiene items are fixed and `CHANGELOG.md` `[Unreleased]` describes all
  four batches accurately.

Blast radius: docs plus `consistency_policy.py`; risk concentrated in phrase-pin synchronization.
Risk: medium.

## Master Acceptance

- [ ] M-A1: Full suite green on CI matrix (3.10/3.12/3.14, ubuntu+macos) and green-with-skips
  locally on 3.9; no test deleted or weakened; test total strictly increased.
- [ ] M-A2: Prewalk actual mode remains `off` for every host in every default environment, with
  honest concrete fallback reasons; no doc or code claims Grok prewalk availability or behavioral
  qualification.
- [ ] M-A3: `verify_repo.py --ci --version Unreleased` passes on CI for the final tip, and the
  landing check (`elves_landing_check.py`) passes locally before cleanup.

## Batch sizing

- team-size: 4, sprint-length: 2 weeks (default). B2 is the largest batch; split registry
  extraction from the grok arm inside the batch if review churns.

## Out of scope (explicitly deferred)

- `full_run.py` monitor/await decomposition (audit rec #8) — separate run.
- Live Grok canaries, any activation of prewalk for any host, release promotion to v2.10.0.
- Devin/OpenCode host arms (registry makes them table rows later).
