# Plan: Integrate open-source Grok Build as an autonomous goal worker

## Mission

Align Elves' optional Grok Build lane with the published `xai-org/grok-build` source and the
installed CLI that Elves actually launches. The useful integration is a clean autonomous handoff:
Elves supplies one complete implementation packet, Grok uses capability-proven headless `/goal` or
the compatible one-packet prompt fallback, and a sanitized streaming view exposes progress without
waking the driver for routine narration.

Native Codex and Claude Code workers remain the default. Grok remains optional and must still pass
the repository permission, authentication, model, and isolation checks before selection.

Done means the read-only lens and trusted full-run lane emit no invalid flags, a capability-proven
Grok launch uses goal mode and streaming output, older or reduced-capability installs fall back
honestly to the existing one-packet launch, and one final cumulative review can verify the result
without batch-by-batch driver review.

## Planning classification

- **Execution reasoning:** `medium`. Most changes are deterministic CLI adaptation, parsing, and
  documentation. The worker must preserve several security and authority boundaries exactly.
- **Review risk:** `high`. Authentication isolation, autonomous launch semantics, terminal-state
  detection, and protected-ref authority deserve extra attention in final review.
- **Worker route:** a separate subscription-native Codex worker, inheriting the available model at
  `medium` effort. This avoids asking Grok to rewrite its own Elves adapter.
- **Review emphasis:** auth isolation, goal fallback, streaming parser safety, model honesty, and no
  behavioral change to native Codex or Claude Code lanes.

## Ground truth established during planning

The planning probe used installed Grok Build `0.2.101` and the open-source repository as a semantic
reference. The installed binary remains launch authority because the public repository is
periodically synchronized and may not exactly match a released build.

- `--permission-mode auto`, `--no-subagents`, `--no-memory`, `--disable-web-search`, `--check`,
  `--output-format streaming-json`, `--json-schema`, and `agent stdio` are present.
- Session creation accepts `--session-id <UUID>`; Elves' current `--new-session` is invalid.
- `GROK_AUTH_PATH` is implemented upstream. It is the narrow credential projection Elves should
  retain alongside its private `HOME` and `GROK_HOME`; broadening the projected config is not a fix.
- Headless `/goal status` resolves successfully in an isolated probe that uses the narrow auth
  projection without catalog lookup or model inference. This proves command resolution only. The
  source shows that headless prompts share the slash-command resolver and that `/goal` drives the
  autonomous goal harness.
- Goal behavior requires a validated terminal-canary JSON artifact bound to the exact installed
  version/build, canonical session, packet-backed prompt digest, successful exit, and matching end
  event. The 0.2.101 canary did not reach terminal state, so this run uses the one-packet fallback.
- `streaming-json` emits newline-delimited typed events, including text, thought, end, and error
  records with session and usage metadata. New event types may appear and must be tolerated.
- `grok agent stdio` exposes a richer ACP transport, including tool calls, plans, permissions, and
  reconnect semantics. It is a later transport option, not required for this integration.
- The authenticated live catalog reports `grok-composer-2.5-fast` as default and also offers
  `grok-4.5`. Elves must select only models returned by the live catalog.

## Scope

### In scope

- A redaction-safe capability contract based on `grok version --json`, `grok --help`, `grok models`,
  `grok agent stdio --help`, and a model-free `/goal status` probe.
- Correct session creation and exact emitted-argv tests for read-only, create, resume, and trusted
  full-run paths.
- Preservation and accurate documentation of the private-home plus `GROK_AUTH_PATH` OAuth grant.
- Separation of basic Grok provider qualification from the optional goal-mode enhancement.
- A capability-proven headless `/goal` launch for trusted full runs, with the current one-packet
  headless launch as the explicit fallback.
- Streaming JSON as the default trusted-Grok follow transport, decoded into a sanitized operator
  view with terminal, error, usage, and exact-session handling.
- Live-catalog default-model parsing and catalog-constrained selection.
- Focused tests, onboarding, host-parity documentation, changelog, and learnings updates.

### Out of scope

- Replacing the headless transport with ACP or building a persistent JSON-RPC permission client.
- Requiring a JSON-schema completion report before accepting otherwise valid committed work.
- Making Grok the default worker or changing native Codex and Claude Code worker behavior.
- Relaxing authentication, worktree, branch, protected-ref, PR, or merge boundaries.
- Parallel worker lanes, plugins, media generation, or other new Grok features.
- Publishing a release, moving a tag, merging, or changing the current `2.5.0` version.

## Batches

### Batch 0 [B0]: Establish the executable capability contract

**Coordinator-to-implementer handoff**

- **Intent:** make every Grok-specific choice inspectable before product launch code depends on it.
- **Build on:** `probe_grok_capabilities` in `scripts/cobbler_runtime/worker_routing.py`, adapter argv
  builders in `scripts/cobbler_runtime/adapters.py`, and existing route tests.
- **Owned surfaces:** Grok capability probing, safe normalized snapshots, fixtures, and focused tests.
- **Forbidden surfaces:** product launch argv, credentials, canonical run memory, remotes, PRs,
  protected refs, and non-Grok adapters.
- **Pitfalls:** unauthenticated `models` calls and network failures are valid states; report them as
  unavailable with a reason. Never serialize secrets or raw OAuth output.
- **Acceptance evidence:** focused fixtures plus a safe live snapshot and confirmed/refuted ledger.

**Tasks**

- [x] Add a normalized capability snapshot that records version, supported flags, authenticated
  model catalog and default, goal behavior, streaming/schema support, and ACP presence.
- [x] Compare the argv Elves can emit with that capability set and make unsupported items explicit.
- [x] Record the upstream source commit used for semantic comparison without treating it as release
  authority.

**Acceptance criteria:**

- [x] [B0-A1] The snapshot handles authenticated and unauthenticated installs honestly, records unavailable capabilities with reasons, and never contains credentials or raw OAuth output.
- [x] [B0-A2] The capability ledger proves the supported read-only, session, goal, streaming, schema, and ACP surfaces and explicitly identifies `--new-session` as unsupported.
- [x] [B0-A3] A model-free isolated `/goal status` probe using the narrow auth projection verifies goal-command resolution independently from catalog lookup and model inference.
- [x] [B0-A4] Batch 0 is additive: it changes no product launch argv or non-Grok adapter behavior.

**Risk:** `standard`

**Review focus:** redaction, honest unknown states, and separation of installed-release evidence from
upstream-source evidence.

**Focused tests:** capability snapshot and ledger fixtures, including missing auth and unknown event
shapes.

**Depends on:** none.

### Batch 1 [B1]: Correct session and authentication semantics

**Coordinator-to-implementer handoff**

- **Intent:** remove the confirmed invalid flag while preserving the isolation design that already
  matches the open-source implementation.
- **Build on:** Grok builders in `adapters.py`, auth environment construction in `full_run.py`, and
  the launch contract in `references/grok-implementer-launch-prompt.md`.
- **Owned surfaces:** Grok argv, reserved-control flags, auth documentation, and focused tests.
- **Forbidden surfaces:** non-Grok argv, XAI API-key behavior, permission widening, ambient home or
  git identity, PR/merge authority, and unrelated version references.
- **Pitfalls:** a new session requires a caller-generated UUID. `--resume` is a different operation.
  `GROK_AUTH_PATH` is valid and narrower than projecting a user's full Grok home.
- **Acceptance evidence:** exact emitted argv and private-environment tests against the installed
  capability contract.

**Tasks**

- [x] Replace `--new-session` with caller-generated `--session-id <UUID>` and preserve exact resume
  semantics.
- [x] Keep only capability-confirmed read-only and autonomous flags in emitted argv.
- [x] Retain the validated private `HOME`/`GROK_HOME` and narrow `GROK_AUTH_PATH` OAuth projection;
  clarify that the minimum version is a capability floor, not a product-lineage claim.

**Acceptance criteria:**

- [x] [B1-A1] Read-only, create, resume, and trusted full-run argv contain only installed-binary-supported flags, and new sessions use a caller-generated UUID through `--session-id`.
- [x] [B1-A2] OAuth launches retain private `HOME` and `GROK_HOME` plus the validated narrow `GROK_AUTH_PATH`; ambient config, SSH state, and git identity are not inherited.
- [x] [B1-A3] XAI API-key behavior and every non-Grok adapter's emitted argv remain unchanged.
- [x] [B1-A4] A reduced-capability or unsupported Grok install fails or falls back with a concrete reason instead of entering an interactive login or invalid-flag loop.

**Risk:** `high`

**Review focus:** UUID grammar, isolation strength, and no accidental widening of autonomous
permissions.

**Focused tests:** adapter create/resume/read-only builders, reserved flags, and full-run auth
environment isolation.

**Depends on:** B0.

### Batch 2 [B2]: Add goal-mode launch and streaming follow

**Coordinator-to-implementer handoff**

- **Intent:** give Grok one complete autonomous objective and expose its progress without routine
  driver inference.
- **Build on:** worker packet generation, trusted full-run supervisor, parked follow mode, and the
  existing provider-neutral one-packet fallback.
- **Owned surfaces:** Grok goal qualification and argv, streaming decoder, sanitized follow output,
  crash/terminal handling, and focused tests.
- **Forbidden surfaces:** worker merge/PR/protected-ref authority, periodic driver narration,
  canonical run memory, ACP transport, native worker supervision, and acceptance relaxation.
- **Pitfalls:** provider availability must not depend on goal support. Unknown stream event types are
  forward-compatible. Thought/tool text may contain secrets and must use the existing sanitizer.
- **Acceptance evidence:** fixtures and one bounded authenticated canary in a throwaway repository.

**Tasks**

- [x] Qualify Grok from permission, authentication, and live-model evidence; record goal capability
  separately as an enhancement.
- [x] When goal support is proven, launch `/goal` with one complete packet-backed objective. When it
  is not, use the compatible one-packet headless path and state the fallback.
- [x] Decode `streaming-json` into sanitized progress, session identity, usage, terminal, and error
  records; tolerate unknown types and preserve raw private logs for recovery.
- [x] Keep the driver parked during ordinary progress and wake it only for terminal completion or an
  existing material safety/stall condition.

**Acceptance criteria:**

- [x] [B2-A1] Grok provider qualification is independent from goal support, and an unavailable goal capability selects the documented one-packet fallback without disabling an otherwise valid provider.
- [x] [B2-A2] An isolated authenticated throwaway-repository canary records whether the packet-backed headless `/goal` launch reaches terminal state with the exact requested session identity; a complete terminal artifact enables goal mode, while an incomplete canary keeps goal mode disabled and proves selection of the one-packet fallback.
- [x] [B2-A3] The streaming follow view exposes sanitized progress, usage, terminal state, and typed errors; it tolerates unknown event types and never requires timed driver narration.
- [x] [B2-A4] Worker branch, commit, crash recovery, acceptance, protected-ref, PR, and merge authority remain identical to the provider-neutral full-run contract.

**Risk:** `high`

**Review focus:** honest fallback, secret-safe streaming, exact terminal/session handling, and no new
authority.

**Focused tests:** goal detection/routing matrix, stream decoder fixtures, supervisor terminal/error
cases, and one bounded live canary.

**Depends on:** B0 and B1.

### Batch 3 [B3]: Align models, onboarding, and public contracts

**Coordinator-to-implementer handoff**

- **Intent:** make the new lane understandable and prevent model names or public instructions from
  drifting away from the executable contract.
- **Build on:** `worker_routing.py`, model onboarding, Grok launch reference, README, guide, SKILL,
  both host adapters, changelog, and learnings.
- **Owned surfaces:** live-catalog parsing/selection and Grok-specific public documentation.
- **Forbidden surfaces:** changing native-first defaults, inventing unavailable models, marketing
  copy, release/tag/merge actions, and host-specific workflow forks.
- **Pitfalls:** `models` is network/auth dependent. A hardcoded model is not safe merely because an
  upstream source file mentions it.
- **Acceptance evidence:** catalog fixtures, doc consistency, installed-bundle parity, and focused
  routing tests.

**Tasks**

- [x] Parse the live default model and select only catalog-returned models. Prefer the live default
  for regular work and `grok-4.5` for complex work only when present and explicitly selected by the
  routing policy.
- [x] Add concise installation, authentication, capability, goal/fallback, and follow-view guidance
  linked to the open-source repository and official Build documentation.
- [x] Align SKILL, AGENTS, Claude/Codex references, README, guide, examples, changelog, and learnings
  without duplicating the canonical workflow.

**Acceptance criteria:**

- [x] [B3-A1] Model selection uses the authenticated live catalog and parsed default; no unavailable model, including `auto` or `grok-code-fast-1`, is selected unless the catalog returns it.
- [x] [B3-A2] A user can install, authenticate, capability-check, launch, follow, and recover the optional open-source Grok worker from one concise documentation path.
- [x] [B3-A3] SKILL, AGENTS, Claude/Codex references, README, guide, examples, changelog, and learnings agree on native-first routing, goal enhancement, one-packet fallback, and authority boundaries.
- [x] [B3-A4] Installed Codex and Claude Code bundle checks pass, and native worker routing remains parity-aligned, commit-capable, and authority-safe.

**Risk:** `standard`

**Review focus:** catalog-constrained selection, Grok optionality, host parity, and plain-language
onboarding.

**Focused tests:** model-catalog and routing fixtures, repository consistency, guide checks, and
installed-bundle smoke checks.

**Depends on:** B0, B1, and B2.

## Master acceptance

- [x] [M-A1] Against installed open-source Grok Build, the optional read-only and trusted full-run lanes emit no invalid flags and use capability-proven goal plus streaming behavior or the documented compatible fallback.
- [x] [M-A2] Authentication isolation, session create/resume identity, model selection, crash recovery, and worker authority match the executable and provider-neutral contracts exactly.
- [x] [M-A3] Native Codex and Claude Code remain the default and parity-aligned, while all user-facing and installed Elves documentation accurately describes the optional open-source Grok path.
- [x] [M-A4] Focused proof, one cumulative final review, targeted revisions, and terminal repository checks leave a reviewed PR ready for the user without merging or moving a version tag.

## Non-negotiables

- Native-first remains the built-in default. Repository permission is not user consent to use Grok.
- The installed binary is launch authority. Upstream source explains semantics but never licenses an
  unverified flag or model on an older installed release.
- Credential isolation may not weaken. Keep the narrow `GROK_AUTH_PATH` projection and private home
  unless executable evidence requires a different equally narrow mechanism.
- The worker may commit and push only its feature branch. It may not own PRs, protected refs, merge,
  tags, or canonical run memory.
- Batch gates verify claimed work and focused tests. The driver performs one cumulative review after
  the worker finishes, with extra attention to the high-risk surfaces named above.
- The user has not authorized merge or release work.

## Test strategy

- Establish a redaction-safe, model-free live capability baseline before launch.
- During implementation, run focused tests for the adapter, route, auth, stream, and supervisor
  surface changed in that batch. Do not repeat unaffected suites between batches.
- Use one bounded authenticated `/goal` canary in an isolated throwaway repository to select either
  proven goal mode or the honest one-packet fallback. It is behavioral evidence, not a
  network-dependent CI requirement.
- At terminal review, run the affected suites once, `scripts/check_repo_consistency.py`, installed
  Codex/Claude bundle smokes, `git diff --check`, the acceptance check, and
  `scripts/verify_repo.py --version Unreleased` once.
- A revision reruns only tests invalidated by that revision, followed by the final lightweight
  consistency and acceptance checks.

## Deferred follow-ups

- Evaluate ACP after the headless goal/streaming lane has real usage data. Its richer plans, tool
  calls, and permission events may justify a persistent client later.
- Consider optional JSON-schema completion reports as convenience evidence. Missing reports must not
  invalidate otherwise proven committed work; the driver can reconstruct an Elves report.
