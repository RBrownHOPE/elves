# Plan: Realign the Grok handoff to open-source grok-build

## Mission

Elves' optional Grok worker lane was built against the closed, npm-era `grok` CLI and is pinned to
**Grok Build 0.2.93** throughout. The open-source tool at `xai-org/grok-build` is a Rust rewrite
(`xai-grok-*` crates, binary still `grok`, installed via `x.ai/cli`). Our emitted argv, our OAuth
env var, our hardcoded model ids, and our "goal mode" probing were all derived from the older
lineage and several no longer match the real parser.

Realign the **optional** Grok lane to the open-source grok-build: verify the real CLI surface against
an installed binary, correct the drifted argv/auth/model assumptions, re-baseline the version story,
and add the net-new onboarding that tells users to install and authenticate the open-source build.

Done means: a parity harness proves the argv we emit matches an installed `grok`; the optional Grok
full-run and the read-only Grok council/review lens both launch with **zero invalid-flag failures**;
auth is exposed through `GROK_HOME`; models come from the live `grok models` catalog with a safe
fallback; and the docs point users at open-source grok-build. Native-first behavior and every
non-Grok lane are untouched.

## Planning Classification

- **Execution reasoning:** `medium` — mostly deterministic argv, config, and documentation work, but
  it must be reconciled against a live binary and must not weaken the credential-isolation kernel.
- **Review risk:** `high` — the Grok launch path touches argv correctness, credential isolation, and
  the trusted-worker authority boundary; a wrong flag or auth path silently breaks unattended
  launches or quietly weakens isolation.
- **Worker recommendation for this run:** separate native worker inheriting the live driver model at
  `medium` (this is our own repo; dogfood the native lane).
- **Terminal review emphasis:** no unverified flag reaches spawn; credential isolation strength is
  unchanged; the provider-neutral contract stays the fallback; the native happy path is not modified.

## Scope

### In Scope

- A **parity harness** that snapshots the installed `grok` (`--version`, `--help`, `models`,
  presence of `agent`) and diffs it against the argv `adapters.py` emits for `grok-build`, producing
  a verified confirmed/refuted/unknown drift ledger.
- Corrected Grok argv in `scripts/cobbler_runtime/adapters.py` (read-only lens and session-create)
  and the full-run launch invariants in `references/grok-implementer-launch-prompt.md`.
- Auth realignment in `scripts/cobbler_runtime/full_run.py`: expose the validated owner-private
  `auth.json` through `GROK_HOME`; remove the `GROK_AUTH_PATH` mechanism; re-baseline or soften the
  `0.2.93` version floor and the ~27 in-repo `0.2.93` references.
- Model policy in `scripts/cobbler_runtime/worker_routing.py`: prefer the live `grok models` catalog,
  keep `grok-composer-2.5-fast` / `grok-4.5` only as fallbacks, and recognize the `grok-build`
  default, the `auto` meta-id, and `grok-code-fast-1`.
- Net-new install/auth **onboarding** that points users at open-source grok-build, plus reconciling
  the "goal mode" language with reality (there is no goal abstraction; the unattended primitive is
  headless `-p --max-turns N` + `--resume`).
- Focused tests, installed-bundle parity (`sync_installed_skills`), and CHANGELOG/learnings updates.

### Out of Scope

- Native `--output-format streaming-json` follow transport and `--json-schema` report enforcement
  (**Phase 2**, separate plan).
- The ACP (`grok agent stdio`) permission-gated lane (**Phase 2** spike, separate plan).
- Making Grok a default or non-optional route; the native-first happy path is unchanged.
- Any change to the native Claude Code / Codex worker lanes, landing authority, or merge policy.
- Changing the credential-isolation *design* (private HOME, executable/ancestor validation); this
  plan re-points which env var carries auth, it does not relax isolation.
- Vendoring community plugins; MCP/marketplace packaging; media pipeline.
- Publishing a release, moving a tag, or merging this PR.

## Batches

### Batch 0 [B0]: Ground-truth parity harness

**Coordinator-to-implementer handoff:**

- **Intent / why:** convert inferred CLI drift into evidence before any product argv changes. Half of
  the suspected drift is read from `xai-org/grok-build` source, not confirmed against the binary we
  actually launch. The fixes in B1–B3 must land on a verified ledger, not on assumptions.
- **Non-obvious rationale:** the binary is named `grok` in both lineages, so version alone does not
  prove which tool is installed; confirm the open-source build by the presence of the `agent`
  subcommand and the Rust `--help` shape. Availability of a flag in source `main` does not prove it
  exists on the operator's installed version.
- **Build On targets:** `scripts/cobbler_runtime/worker_routing.py` (`probe_grok_capabilities`
  already runs `grok models` / `grok --help`), existing `tests/` fixtures, `scripts/verify_repo.py`.
- **Owned surfaces:** a new parity/snapshot helper and its focused test; no product argv changes.
- **Forbidden surfaces:** canonical run memory, credentials, protected refs, other worktrees.
- **Acceptance evidence:** a normalized capability snapshot from a real binary plus a drift ledger
  that marks every suspected item confirmed or refuted with `--help` evidence.
- **Failure modes / pitfalls:** an unauthenticated binary still answers `--help`/`--version` but not
  `models`; record `models` as `unavailable` with reason rather than fabricating a catalog.
- **HEAD / run-doc paths / route-session identity / output format:** build on the staged branch tip;
  canonical paths in `.elves-session.json`; commit concrete slices with the Elves subject schema.

**Tasks:**
- [ ] Add a helper that snapshots the installed `grok` (`--version`, `--help`, `models`, `agent`
  presence) into a normalized, redaction-safe structure.
- [ ] Diff that snapshot against the argv `adapters.py` emits for the `grok-build` read-only and
  session-create paths and the documented full-run launch invariants; emit a confirmed/refuted/unknown
  drift ledger.
- [ ] Record the lineage answer (open-source Rust build vs. legacy CLI) and whether `agent stdio` and
  `--output-format streaming-json` exist on the installed version, to scope Phase 2.

**Acceptance criteria:**
- [ ] B0-A1: The harness runs against a real authenticated `grok` and emits a normalized capability
  snapshot; when auth is absent it records `models` as `unavailable` with a reason rather than
  inventing a catalog.
- [ ] [B0-A2] Every suspected drift item in **Notes** (`--permission-mode auto`, `--no-subagents`,
  `--no-memory`, `--disable-web-search`, `--new-session`, `--check`) is marked confirmed or refuted
  with a quoted `--help` line as evidence.
- [ ] B0-A3: The ledger answers whether the installed binary is the open-source Rust build (by
  `agent` subcommand presence and `--help` shape) and whether `streaming-json`/`agent stdio` exist.
- [ ] [B0-A4] This batch changes no product argv; the harness is additive and its test is isolated.

**Docs likely touched:** learnings; a short harness usage note. Product docs unchanged in B0.

**Risk:** `standard` — depends on a reachable, authenticated binary; degrade gracefully to offline
`--help`/`--version` when `models` is unavailable.
**Caution:** same binary name across lineages; never infer the tool from version alone.
**Affected surfaces:** new parity helper, capability probing, its focused test.
**Constitution impacts:** deterministic orchestration; secret-safe snapshots.
**Review focus:** snapshot redaction, honest `unavailable` handling, evidence-quality of the ledger.
**Focused tests:** parity-harness unit test with recorded `--help` fixtures (real + legacy shapes).
**Depends on:** none.

### Batch 1 [B1]: Argv parity fixes

**Tasks:**
- [ ] Apply every **confirmed** drift fix from B0 to `adapters.py`: replace `--permission-mode auto`
  with a confirmed autonomous mode (`bypassPermissions`/`acceptEdits`) or rely on `--yolo`; replace
  `--no-subagents` with `--disallowed-tools Agent`; move memory/web-search suppression to
  `GROK_MEMORY` env / `--disallowed-tools`; replace `--new-session` with `--session-id <uuidv7>`;
  drop `--check` unless B0 confirmed it.
- [ ] Update `_RESERVED_CONTROL_FLAGS["grok-build"]`, `build_readonly_invocation`, and
  `build_session_create_invocation` to the verified flag set; keep `decode_grok_json` (confirmed
  correct) and tolerate the additional real fields (`requestId`, `usage`, `structuredOutput`).
- [ ] Update the full-run launch invariants in `references/grok-implementer-launch-prompt.md` to the
  verified flags and remove the invalid-flag anti-patterns.

**Acceptance criteria:**
- [ ] [B1-A1] The argv emitted for the read-only Grok lens and the full-run launch contains only
  flags B0 marked confirmed-present; the parity harness passes against the installed binary.
- [ ] B1-A2: A refuted flag never appears in any emitted `grok-build` argv (regression: an assertion
  test over the emitted argv rejects the removed flags).
- [ ] [B1-A3] Regression preservation: no other adapter's emitted argv changes; `decode_grok_json`
  still parses `{text, stopReason, sessionId}` and ignores the extra fields.

**Docs likely touched:** `references/grok-implementer-launch-prompt.md`, adapter notes, learnings.

**Risk:** `high` — a wrong autonomous-mode choice either fails to auto-approve writes or over-permits.
**Caution:** `--yolo` is auto-approve; `--allow`/`--deny` are additive rules and **deny wins even
under `--yolo`** — prefer them for the untrusted lane rather than widening blanket approval.
**Affected surfaces:** `adapters.py`, grok launch reference, adapter tests.
**Constitution impacts:** worker authority boundary, unattended autonomy.
**Review focus:** exact flag set vs. ledger, session-id create grammar, no invalid flag at spawn.
**Focused tests:** adapter argv builders (read-only + create), reserved-flag guard.
**Depends on:** B0.

### Batch 2 [B2]: Auth via GROK_HOME and version re-baseline

**Tasks:**
- [ ] Expose the validated owner-private `auth.json` to the worker through `GROK_HOME` only; remove
  the `GROK_AUTH_PATH` mechanism from `full_run.py` and its references while keeping the private-HOME,
  executable-probe, and ancestor-validation isolation exactly as strong.
- [ ] Re-derive the supported version floor from a real `grok --version` (`GROK_AUTH_PATH_MIN_VERSION`
  and the ~27 `0.2.93` references); soften prose that hard-pins the legacy version.
- [ ] Keep the `XAI_API_KEY` path unchanged; document `grok login --device-auth` as the headless
  device-code flow and note the enterprise auth env vars as optional.

**Acceptance criteria:**
- [ ] [B2-A1] The OAuth grant path exposes `auth.json` via `GROK_HOME`; no `GROK_AUTH_PATH` remains in
  code or docs; isolation tests (private HOME, executable/ancestor validation) still pass unchanged.
- [ ] B2-A2: The version floor and in-repo version references reflect the open-source tool's real
  `grok --version`; `scripts/check_repo_consistency.py` passes.
- [ ] [B2-A3] Regression preservation: the `XAI_API_KEY` (CI/untrusted) launch path is unchanged and
  still forbidden from combining with the OAuth route.

**Docs likely touched:** `references/grok-implementer-launch-prompt.md`, SKILL/AGENTS pins, CHANGELOG.

**Risk:** `high` — auth env drift can send the worker into an unattended device-login wait.
**Caution:** one canonical `auth.json` preserves Grok's lock/refresh-token rotation; do not fan it out.
**Affected surfaces:** `full_run.py`, launch reference, consistency checker fixtures.
**Constitution impacts:** credential isolation, no ambient HOME/SSH/git identity inheritance.
**Review focus:** isolation strength unchanged, no lingering `GROK_AUTH_PATH`, version accuracy.
**Focused tests:** full-run auth-grant isolation tests; consistency checker.
**Depends on:** B0.

### Batch 3 [B3]: Live models, onboarding, and honest long-running language

**Tasks:**
- [ ] Make `worker_routing.py` prefer the live `grok models` catalog; keep `GROK_COMPOSER_MODEL` /
  `GROK_COMPLEX_MODEL` as documented fallbacks; recognize the `grok-build` default, the `auto`
  meta-id, and `grok-code-fast-1`.
- [ ] Simplify or repoint the "goal mode" probing (`detect_native_grok_goal` and its behavioral-
  verification scaffolding) to reflect that no goal abstraction exists; the supported unattended
  primitive is headless `-p --max-turns N` + `--resume`.
- [ ] Add a net-new **install/authenticate** onboarding section pointing at open-source grok-build
  (`curl -fsSL https://x.ai/cli/install.sh | bash`, `grok --version`, `grok login` or `XAI_API_KEY`,
  links to `xai-org/grok-build` and `docs.x.ai/build`); thread it through SKILL/AGENTS/references/README.

**Acceptance criteria:**
- [ ] [B3-A1] Model selection uses the live catalog when available and documents the exact fallback
  when it is not; `grok-composer-2.5-fast` / `grok-4.5` are no longer the sole hardcoded truth.
- [ ] B3-A2: A user with no prior Grok setup can follow one onboarding section to install and
  authenticate open-source grok-build; the section names the install command, auth options, and
  upstream links.
- [ ] [B3-A3] "Goal mode" language across SKILL/AGENTS/references reflects reality; no doc claims a
  behaviorally verified goal mode that the tool does not provide.
- [ ] [B3-A4] Regression preservation: the native-first default flow and non-Grok routing text are
  unchanged; docs/code parity (`check_repo_consistency`, `sync_installed_skills`) passes.

**Docs likely touched:** README, SKILL, AGENTS, `references/*grok*`, `references/model-onboarding.md`,
CHANGELOG, learnings, `.ai-docs/*` where durable.

**Risk:** `standard` — onboarding can drift into making Grok feel required; keep it clearly optional.
**Caution:** the live catalog is authoritative but network-dependent; fallback must be explicit.
**Affected surfaces:** `worker_routing.py`, `implement.py` goal detection, public docs, examples.
**Constitution impacts:** provider honesty, native-first simplicity, host parity.
**Review focus:** optionality of Grok, fallback honesty, no phantom capabilities, doc/code parity.
**Focused tests:** routing/model-selection tests, consistency and installed-bundle parity tests.
**Depends on:** B0, B1, B2.

## Master Acceptance

- [ ] [M-A1] The optional Grok lane — read-only council/review lens and trusted full-run — launches
  against the open-source grok-build with zero invalid-flag failures, proven by the B0 parity harness
  run against a real binary.
- [ ] [M-A2] Auth is exposed through `GROK_HOME`, models come from the live catalog with a documented
  fallback, the version story reflects the open-source tool, and credential-isolation strength is
  unchanged.
- [ ] [M-A3] Users are told how to install and authenticate open-source grok-build, and
  SKILL/AGENTS/references/README/CHANGELOG/learnings agree with the shipped behavior; native-first
  and all non-Grok lanes are untouched.
- [ ] [M-A4] One terminal readiness review of `git diff origin/main...HEAD` finds no unresolved
  serious issue, and the branch is presented as a reviewed PR without merging.

## Non-Negotiables

- Native-first is preserved; Grok stays strictly optional. The native happy path and every non-Grok
  lane are not modified by this plan.
- No unverified flag may reach a real spawn: every Grok flag Elves emits must be confirmed against the
  installed binary by the B0 ledger before B1 changes it.
- Credential-isolation strength (private HOME, executable/ancestor validation, no ambient
  HOME/SSH/git identity) must not weaken; this plan re-points auth, it does not relax isolation.
- The provider-neutral shared contract remains the fallback; native-Grok optimizations
  (streaming-json, `--json-schema`, ACP) are additive Phase 2 work, never a replacement, and are out
  of scope here.
- The user owns whether Elves may merge; this run ends at a reviewed PR and does not merge.

## Test Strategy

- **Primary new gate:** the B0 parity harness — emitted `grok-build` argv must match the installed
  `grok --help`. This is the gate that would have caught the `--no-subagents`/`--no-memory`/
  `--disable-web-search` triple in the read-only lens.
- **During implementation:** focused unit tests for touched adapter, routing, and full-run surfaces
  only; commit verified slices with the Elves subject schema.
- **Terminal gate:** one cumulative `python3 scripts/verify_repo.py --version Unreleased`, plus
  `check_repo_consistency.py` and `sync_installed_skills.py` parity.
- **Known baseline:** the clean branch may report the public-API approval manifest as stale for the
  current version; identify it rather than hiding it, and reconcile only if final readiness requires.

## Notes

- **Ground-truth source:** `xai-org/grok-build` `main` (Rust). Authoritative CLI parser is
  `crates/codegen/xai-grok-pager/src/app/cli.rs`; headless engine `headless.rs`. Binary is `grok`
  (symlink to `xai-grok-pager`); install `curl -fsSL https://x.ai/cli/install.sh | bash`.
- **Confirmed correct today (do not "fix"):** `-p`/`--prompt-file`; `--output-format json` →
  `{text, stopReason, sessionId, requestId, usage, structuredOutput, …}`; `--yolo` (alias
  `--always-approve`); `--reasoning-effort`/`--effort`; `--max-turns`; `--resume <id>` /
  `-s/--session-id`; `XAI_API_KEY`; exact-session resume; checkpoints are **TUI-only** (`/rewind`,
  `/fork`), so our git rollback-ref design stays correct; there is no real "goal mode".
- **Suspected drift for B0 to adjudicate:** CLI `--permission-mode` values are
  `default|dontAsk|bypassPermissions|acceptEdits|plan` (`auto` is a *config* value, not a CLI flag);
  subagents are gated by `--disallowed-tools Agent` / `GROK_SUBAGENTS` (no `--no-subagents`); memory
  via `GROK_MEMORY` (no `--no-memory`); web search is a tool (`--disallowed-tools`, no
  `--disable-web-search`); new session via `--session-id <uuidv7>` (no `--new-session`); `--check`
  not seen in the parser.
- **Auth:** OAuth tokens live at `$GROK_HOME/auth.json` (default `~/.grok`); there is no
  `GROK_AUTH_PATH` in the real tool. Headless device flow is `grok login --device-auth`. Optional
  enterprise: `GROK_AUTH_PROVIDER_COMMAND`, `GROK_DEPLOYMENT_KEY`, OIDC (`GROK_OIDC_*`).
- **Models:** the production catalog is fetched remotely and cached to `~/.grok/models_cache.json`;
  the repo ships only a fallback (`grok-build` default). `grok-code-fast-1` and the `auto` meta-id are
  real; `grok-composer-2.5-fast` / `grok-4.5` are plausible but must come from `grok models`.
- **Lineage question:** `docs/plans/adaptive-worker-routing.md` notes a locally observed
  `Grok Build 0.2.101`. B0 must confirm whether that install is the open-source Rust build (same
  `x.ai/cli` channel, `agent` subcommand present) or a separate legacy binary; the answer decides
  whether B1–B3 re-point one tool or must support two.
- **Phase 2 (separate plan):** native `--output-format streaming-json` as the follow transport;
  `--json-schema references/implement-done-report.schema.json` to enforce the final report at
  generation time; the ACP lane (`grok agent stdio`, JSON-RPC 2.0, native permission requests) as the
  structured replacement for the cooperative `high_risk_checkpoint` machinery. Principle: optimize the
  grok adapter behind the neutral contract, never replace it.

<!-- v2.3 risk/proof policy pins -->
- thin safety kernel; risk low|standard|high independent of trust trusted|untrusted
- validate once, verify changes, attest final
- impact-selected proof during work; broad proof once at terminal readiness and explicit high-risk checkpoints
- mid-run nonblocking new/unresolved PR feedback; terminal waits for required checks
