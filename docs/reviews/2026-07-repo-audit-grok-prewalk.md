# Elves repository audit and Grok Build prewalk feasibility review

- **Date:** 2026-07-17
- **Repo under audit:** `aigorahub/elves` at `f32ce0d` (v2.9.0)
- **Comparison source:** [`xai-org/grok-build`](https://github.com/xai-org/grok-build) at
  `98c3b2438aa922fbbe6178a5c0a4c48f85edc8ce` (workspace version **0.2.102**, monorepo
  `SOURCE_REV 124d85bc5dc6e7805560215fcc6d5413944920e1`)
- **Method:** four parallel deep-read audits (docs/contracts, runtime scripts, test suite,
  grok-build source), followed by an adversarial verification pass in which 14 independent
  reviewers attempted to refute every load-bearing claim against the actual sources. Verdicts are
  marked below; claims that were corrected during verification appear here only in corrected form.
  All analysis of grok-build was read-only source review; no grok binary was executed.

---

## 1. Executive summary

**The headline question — can prewalk work with a Claude Code/Codex driver supervising a Grok
Build worker? Yes, it is mechanically feasible.** Every hard transport primitive the prewalk
contract demands of a host is present and verified in the grok-build 0.2.102 source: caller-chosen
exact session identity (`--session-id <uuid>`), strict exact resume (`-r/--resume <id>`, never
"latest"), **model and reasoning-effort override applied on resume before the prompt runs** (the
single most load-bearing requirement, confirmed at
`xai-grok-pager/src/headless.rs:730-819,1074-1087`), pinned working directory (`--cwd`), streaming
JSON output, and a non-yolo `--permission-mode auto` headless lane that fails closed on unapproved
tool calls — structurally parallel to the Claude Code `auto`-classifier lane Elves already uses
for native prewalk workers.

**What blocks it today is not Grok Build; it is Elves, deliberately.** Three gates, in ascending
order of effort:

1. **A one-line policy veto.** `scripts/cobbler_runtime/worker_routing.py:997-999` unconditionally
   records `prewalk_capability_unavailable:external_provider_not_qualified` for any non-native
   provider, mirroring the normative sentence in `references/prewalk.md:62` ("External providers
   are not enabled by analogy"). The docs leave exactly one doorway:
   `references/adaptive-worker-routing.md:87-88` — "External providers remain off unless their
   trajectory semantics are separately qualified."
2. **A ~10-site scatter-edit.** There is no host abstraction layer in the native-worker path;
   codex/claude behavior lives in `if/elif` chains across four modules (§4.4 lists every touch
   point). Adding a `grok` arm is a few days of mechanical work plus fixtures and tests.
3. **The qualification pipeline — the deliberate part.** Prewalk activation requires a
   version-bound behavioral qualification artifact and `retained_safe` instruction-fidelity
   evidence. Grok resume replays the full persisted conversation to the model (verified), so Grok
   lands in exactly the same `retained_safe` bucket as the current Claude/Codex transports.
   Notably, **even Codex and Claude are not behaviorally qualified yet** — actual prewalk mode
   resolves to `off` everywhere. Porting to Grok therefore lands at the same maturity as the
   native hosts: implemented but gated until operator-authorized canaries run.

One reading of the question deserves a direct answer: prewalk is **not** a context transfer from a
Claude/Codex session into a Grok session. It is a trajectory property inside one worker session
(guide route → checkpoint → exact resume on an execution route). Moving accumulated Claude/Codex
context into Grok is, by Elves' own contract, a **cold handoff** — the v2.9 explicit-handoff
capsule covers it and is explicitly "not prewalk continuity proof." Grok Build does ship a
foreign-session importer that can ingest Claude Code/Codex/Cursor sessions
(`xai-grok-pager/src/app/foreign_sessions.rs`), which is a genuinely interesting cold-handoff
transport, but a different model consuming a transplanted transcript has no trajectory identity
and must not be reported as prewalk.

**General audit verdict:** this is an unusually disciplined repository. The test suite is real
(1,134 tests, ~97% behavioral, zero genuine failures), the security posture of the runtime is
strong (no `shell=True`, argv-only subprocess, hardened atomic writes, fail-closed validators),
and the docs are internally consistent to a degree that is rare — enforced by a shipped
consistency linter. The costs of that discipline are the main findings: a 7,121-line supervisor
god-module, ~10 hand-synchronized copies of key normative sentences, a glossary that is missing
its own headline term ("prewalk"), and an implicit Python ≥3.10 floor that fails as 18 opaque
`TypeError`s on a 3.9 interpreter instead of one clear message.

---

## 2. Repository overview

Elves is a portable Agent Skill, not an application: a Claude Code or Codex **driver** plans,
stages, reviews, and lands; a separate **worker** session implements; durable run files under
`.elves/runtime/` and `docs/` survive context compaction. The repository is ~60k LOC of Python
under `scripts/` (thin CLI `cobbler_agents.py` + 37-module `cobbler_runtime/` package), ~40
contract documents under `references/`, 38k LOC of tests, and a phrase-pinning consistency linter
wired into CI (Ubuntu/macOS × Python 3.10/3.12/3.14).

Worker lanes today:

| Lane | Worker | Authority |
|---|---|---|
| Native (default) | separate Claude/Codex session | narrow git roots, no push to protected refs |
| Native + prewalk (v2.8, feature-gated off) | same, two-phase guide→execution | same |
| Trusted full-run "Lane A" | **Grok Build** (or Devin) via `implement full-run-*` | feature-branch commits/pushes, `--always-approve`, isolated per-run HOME, one-shot credential grant |
| Untrusted writer lease | any external CLI | detached commits, host audit/import only |

So Grok is already a first-class *implementer* with a hardened launch path (probed executable
identity, ACL-validated `GROK_AUTH_PATH` projection, live-catalog-only model selection, sanitized
streaming, typed terminal failures). What it is not — yet — is a *native-worker host*, which is
the surface prewalk lives on.

---

## 3. What prewalk requires (the contract as shipped)

From `references/prewalk.md` (normative), `references/host-parity.md:24-49`, and
`references/adaptive-worker-routing.md:78-103`, a qualifying host transport must provide:

1. **Fresh exact identity** — caller-generated UUID (Claude) or captured `thread.started.thread_id`
   (Codex) before transition.
2. **Exact resume** — resume that ID and only that ID; never `--last`/`--continue`/latest.
3. **Route override on resume** — explicit execution model/effort different from the guide route,
   same session, same registered worktree.
4. **One redacted logical follow stream** with phase labels.
5. **Bounded TODO + checkpoint artifacts** — the guide mirrors its native TODO mechanism into
   private JSON (`todo.json`, `checkpoint.json`, `session.json` under
   `.elves/runtime/prewalk/<run>/`); a **model-free transition validator** then requires a clean
   registered start, unchanged branch/origin/protected refs, a real source/test/product-doc edit
   tied to the checkpoint, and no `Close` commit — runtime-only/plan-only/empty/mismatched deltas
   fail closed.
6. **Narrow git authority, no bypass** — Claude uses `--permission-mode auto` (never
   `bypassPermissions`); Codex uses the workspace sandbox; neither may push or touch protected
   refs.
7. **Version-bound behavioral qualification** — static `--help` probes establish only
   *advertised* grammar; activation additionally requires a bounded JSON artifact proving, for the
   exact installed version: create/resume in one session and worktree, a guide-only fact retained
   after resume, no packet replay, one logical stream, and an honest instruction-fidelity result.
   The current persisted-instruction transport activates **only** on `retained_safe`; verification
   confirmed `PrewalkCapabilities.qualified()` (`scripts/cobbler_runtime/prewalk.py:119-129`) also
   requires non-null qualified guide/execution efforts, and that a *stronger* `pruned` artifact
   still leaves `qualified()` false by design.
8. **Fail-closed lifecycle** — one exact-session guide recovery; transport-only failures back off
   300/600/1200 s; pre-edit `auto` may abandon to a fresh normal worker (not claimed as prewalk);
   **post-edit cold fallback is forbidden**.

---

## 4. Grok Build against that contract

### 4.1 Verified capability matrix

Every row below was checked in grok-build source by an adversarial reviewer instructed to refute
it. "Confirmed" means the claim survived; corrections are folded in.

| Prewalk requirement | Codex 0.144.1 | Claude Code 2.1.207 | **Grok Build 0.2.102 (verified)** |
|---|---|---|---|
| Fresh exact identity | capture `thread.started.thread_id` | caller UUID `--session-id` | **Caller UUID `-s/--session-id`** — create-only; rejects non-UUID or existing id (`session_startup.rs:429-441`); with `--resume` it errors unless `--fork-session`. Caveats: streaming JSON emits `sessionId` only on the terminal `end` event, so pre-generate the UUID (Elves' full-run path already does); with `--worktree` the already-exists check is skipped |
| Exact resume | `codex exec resume <id>` | `--resume <uuid>` | **`-r/--resume <id>`** — strict; never creates; "most recent" is a distinct `-c/--continue` sentinel Elves would simply never emit; may restore the session from remote storage when allowed rather than failing immediately |
| Route override on resume | flags before `resume` | model/effort flags with resume | **Confirmed end-to-end**: headless resume path calls `apply_headless_model_and_effort` after session load and before the prompt, sending ACP `SetSessionModelRequest` with `_meta.reasoningEffort` (`headless.rs:1018-1087`); without flags, the session's persisted model/effort from `summary.json` are restored. Effort grammar `none…xhigh|max`; unknown tokens hard-fail |
| Worktree binding | `-C` on create | supervisor CWD | **`--cwd <path>`** pins the process working directory; sessions are keyed by cwd with an any-cwd fallback scan on resume-by-id |
| Follow stream | JSONL | stream JSON | **`--output-format streaming-json`**, one JSON object per line — but see gap G-2 below (no tool-call events) |
| Native TODO mechanism | plan tool | TodoWrite | **`todo_write` exists** (merge/replace, id/content/status) and mirrors to ACP `plan` session updates — but see gap G-3 (cross-resume persistence is vestigial) |
| Non-bypass authority lane | workspace sandbox | `--permission-mode auto` classifier | **`--permission-mode auto` exists headless** (feature gate defaults on, admin-pinnable off) plus `--allow`/`--deny` rules and OS sandbox profiles (`workspace|read-only|strict|…`); without yolo, any permission prompt that still reaches the headless pager is auto-cancelled and the tool call refused — fails closed |
| Exact-version discipline | required by contract | required by contract | **Justified empirically by Grok itself**: flag semantics churned 0.2.93 → 0.2.101 → 0.2.102 (tool-allowlist failures, yolo/permission-mode precedence). Elves' exact-version binding is the right defense |

Instruction fidelity: on `session/load`, grok-build restores the full persisted `chat_history`
into the model-side conversation unconditionally (`acp_agent.rs:1338-1362` →
`spawn.rs:939 replace_conversation`); `_meta.noReplay` gates only client/UI replay. There is no
resume-time prompt-prefix pruning (the only generic mutation is per-request soft-trimming of old
tool-result contents past 50% context utilization — not resume-specific). Consequently a
cooperative guide instruction persists into the resumed process **exactly as it does on the
current Claude/Codex transports**, and Grok's honest fidelity ceiling under today's delivery
mechanism is the same `retained_safe` the native hosts are gated on.

A pleasant verification bonus: Elves' v2.9.0 flag rule for the trusted lane ("use
`--always-approve` alone; never combine with `--permission-mode auto`") is **stronger than
documented**. In 0.2.102 source, the combination yields *neither* mode in the TUI resolver and
*auto* (not yolo) in headless — on no path does yolo survive the combination
(`permissions.rs:176-239`, `xai-grok-pager-bin/src/main.rs:1927-1958`). The rule stands verified.

### 4.2 Genuine gaps to engineer around (grok-build side)

- **G-1 · No session-start identity event.** Streaming JSON carries `sessionId` only on the final
  `end` event. Resolution: caller-generated UUID via `-s`, which is already Elves' Claude-style
  grammar and its existing Grok full-run practice. Cost: none.
- **G-2 · Headless stream has no tool-call/plan events** — only `text`, `thought`, `end`, `error`,
  `max_turns_reached`, `auto_compact_*`, `auto_continue_completed`, `image_compressed` (verified
  exhaustively in `headless.rs`). The redacted phase-labeled follow log would be thinner than
  Codex JSONL. Resolutions, in ascending effort: accept text/thought-level follow fidelity; tail
  the session's on-disk `updates.jsonl` (the authoritative ACP update stream, including
  `tool_call` and `plan` updates); or drive `grok agent stdio` over ACP, where `session/load` +
  `session/set_model` + `session/set_mode` are first-class — the ACP route is literally the
  mechanism the headless CLI itself uses internally.
- **G-3 · `todo_write` state does not survive resume in the reachable code path.** The `plan.json`
  read/write plumbing exists but is vestigial: the only writer is a post-compaction reset that
  stores an empty list, the resume loader discards `plan_state`, and the full loader that would
  carry it is marked dead code (`todo/mod.rs`, `compaction.rs:1631-1637`, `acp_agent.rs:1355`,
  `persistence.rs:2291`). This does **not** block prewalk — the contract's durable artifact is the
  packet-instructed private `todo.json` mirror, not the host's internal plan state — but "mirrors
  its native TODO mechanism" would be aspirational for Grok until upstream wires the restore path,
  and the transition validator must rely on the JSON mirror alone.
- **G-4 · Sandbox profile is resume-sticky.** A session's sandbox is fixed at creation; an
  explicit differing `--sandbox` on resume is refused (`SandboxStartup::Conflict`,
  `cli.rs:751-891`). Guide and execution phases must therefore run one identical sandbox profile —
  acceptable (prewalk changes model/effort, not authority), but it forecloses any
  tighten-on-execution design.
- **G-5 · Unattended-commit capability under `--permission-mode auto` is unproven.** The auto
  classifier's willingness to approve `git commit` headlessly (the equivalent of the Claude lane's
  commit-capable `auto` mode) is exactly the kind of fact Elves' contract refuses to assume from
  help text — it is a canary question, and the honest answer today is "unknown until behaviorally
  qualified." If auto proves non-commit-capable, `--allow` rules scoped to git are the fallback
  surface.

### 4.3 The Elves-side gates

- **Policy veto (one line + one sentence).** `worker_routing.py:997-999` is the first branch of
  the prewalk decision chain: any non-native provider → fallback
  `external_provider_not_qualified`, regardless of evidence (verified). The doc counterpart is
  `prewalk.md:62`. The doorway sentence in `adaptive-worker-routing.md:87-88` means amending this
  is a *planned* contract evolution, not a rule-break.
- **No host abstraction.** `native_worker_profiles()` looks like a registry but is display-only;
  nothing consumes it for command construction. Host behavior is `if/elif` on host-name tokens.
  Complete touch-point list for a `grok` arm:
  1. `worker_routing.py:997-999` — the veto itself, replaced by a qualification path;
  2. `worker_routing.py:819-821, 981-985, 1062` — driver-host allowlist and two hardcoded
     transport ternaries;
  3. `native_worker.py:253-349` — `build_native_worker_spec` create/resume argv branch
     (create: `-s <uuid> --cwd <wt> --model <m> --effort <e> --permission-mode auto
     --output-format streaming-json`; resume: `-r <uuid>` + route flags);
  4. `native_worker.py:557-560` — child-env provider-secret allowlist (without adding
     `XAI_API_KEY`/`GROK_AUTH_PATH` the secret-stripping filter launches Grok unauthenticated;
     the far richer auth-chain validation in `provider_auth.py` already exists on the full-run
     path and should be reused, not duplicated);
  5. `native_worker.py:672-692, 1082-1084` — session-identity capture (`sessionId` is already an
     accepted key; the codex-only identity-readiness special case needs a grok arm);
  6. `prewalk.py:705-777` — advertised-grammar checks and help-probe argv per host;
  7. `prewalk.py:827-848` — qualification-artifact host/transport binding;
  8. `cobbler_agents.py:2223, 507-513` — `--host` choices and error text;
  9. `native_worker.py:128-155` — profile inventory entry;
  10. tests + recorded help fixtures (a grok 0.2.93 help fixture already exists in
      `tests/fixtures/`; it needs a current-version refresh).
- **Qualification artifacts.** A grok prewalk canary needs a new bounded schema proving
  session/worktree/stream continuity, route change, guide-only fact retention, no packet replay,
  and fidelity — the existing `grok_goal_terminal_canary` artifact
  (`references/grok-open-source-worker.md`) is a working template for exactly this artifact
  style, already version/build/digest-bound.
- **Authority-model decision.** The trusted full-run lane grants `--always-approve` and push
  credentials at spawn — both forbidden in a prewalk guide phase. A Grok prewalk worker is a
  *native-worker-shaped* lane: no yolo, no `--grant-github-push`, narrow git authority, terminal
  git-contract checks. The credential-grant model must express "no push in either phase" (simpler
  than the phase-split grant the full-run contract cannot currently express).
- **Documented contract amendments** (from the docs audit): redefine the prewalk Promise away from
  "native worker session"; add the Grok column to the parity tables in `prewalk.md` and
  `host-parity.md`; extend `prewalk-capabilities` beyond `--host codex|claude`; define
  `provider=grok` × `worker.prewalk=required` semantics (currently undocumented); specify the
  TODO-mirror expectation for a host whose native plan state does not persist (G-3); extend the
  release-honesty rule ("must not claim general prewalk availability…") to external providers.

### 4.4 Verdict and recommended path

**Feasible — and the port is smaller than it looks, because Elves' hardest prewalk machinery is
already host-neutral.** The transition validator, TODO/checkpoint schemas, backoff ladder,
fail-closed diagnostics, and supervisor lifecycle in `prewalk.py`/`native_worker.py` contain no
host-specific logic; only spec construction, probes, env, and identity capture do. Recommended
sequencing:

1. **Phase 0 — decide the policy.** Accept the `adaptive-worker-routing.md:87-88` doorway as the
   governing clause and write the contract amendments (§4.3 last bullet). Keep `allow_grok=false`
   as an absolute veto and consent rules unchanged.
2. **Phase 1 — mechanical port.** The ~10-site scatter-edit, behind the same feature gate that
   keeps Codex/Claude prewalk actually-off. Ship with `actual mode: off` and honest
   `external_provider_not_qualified` replaced by `grok_prewalk_unqualified:<concrete reason>`.
   (Opportunistic: introduce the host-profile registry the code currently fakes, so a fourth host
   is a table row, not another scatter-edit.)
3. **Phase 2 — qualification tooling.** `prewalk-capabilities --host grok` static probe against
   installed `grok --help`; define the grok prewalk-canary artifact schema modeled on the goal
   canary.
4. **Phase 3 — operator-authorized live canaries** on the exact installed version: one
   session/worktree, guide→resume with route change, guide-only fact retention, no packet replay,
   stream identity, `retained_safe` fidelity, and the G-5 unattended-commit question. Only then
   may `auto` activate for that exact version — the same bar the native hosts still have to clear.

Do not pursue: claiming the explicit-handoff capsule or Grok's foreign-session import as prewalk
(definitionally cold), or a yolo-based prewalk lane (violates the guide-phase authority contract).

---

## 5. General audit findings

### 5.1 Test suite — strong, with one environment trap

Full run: **1,134 tests in ~310 s, zero genuine assertion failures.** 18 errors share one root
cause: `sync_installed_skills.py:323` slices `Path.parents`, a Python ≥3.10 feature, and the
audited machine's system interpreter is 3.9 — so the entire install/sync surface fails as 18
opaque `TypeError`s. CI (3.10/3.12/3.14) never sees it. **Finding: add an explicit interpreter
floor** (one `sys.version_info` guard in the entry scripts, or `python_requires` metadata) so
below-floor interpreters fail with one clear message. 14 skips are all deterministic environment
gates; no flake-style skips.

Quality: ~97% of tests exercise real behavior — real temp git repos with bare remotes, a compiled
C argv-forwarding launcher for the Grok spawn path, real Darwin ACL acceptance/rejection tests on
the OAuth projection, recorded CLI help fixtures (claude 2.1.207, codex 0.144.1, grok 0.2.93). The
doc-phrase-assertion anti-pattern is confined to ~30 tests (~2.6%) that pin the consistency
linter's phrase corpora — change-detectors by design, though they duplicate the corpus they test.
Prewalk's transition validator and supervisor lifecycle are well covered behaviorally
(`test_native_worker_prewalk.py`, 35 tests), and the Grok full-run supervisor is the
heaviest-covered surface in the repo (146 tests). Thin spots: `git_contract.py` (332 LOC, no
direct tests), `dispatch_host_native.py` (touched only by a line-count check),
`landing_authority.py` (6 direct tests for 525 LOC).

Also worth knowing: a default `verify_repo.py` run on a clean tree **skips the broad unittest
gate** under its "evidence-aware focused plan" and reports the gate as passed-by-skip; only
`--ci`/`--final-readiness` force live broad gates. That is by design, but operators should not
read a green default `verify_repo.py` as "the suite ran."

### 5.2 Runtime code quality (ranked, all verified at cited lines)

1. **`full_run.py` remains a god-module** — 7,121 lines *after* the v2.7 phase-1 split, with
   ~750-line (`monitor_full_run`) and ~550-line (`await_full_run`) functions. `audit.py` (4,144)
   and the 3,037-line CLI have the same shape. This is the top structural risk for future edits —
   including the prewalk-port touch points that neighbor it.
2. **Worker-stream identity capture is under-typed.** On the native-worker path, any stdout JSON
   object with *any* string `type` field plus a session-id-shaped key can bind session identity
   when unset, or trigger a spurious fail-closed `session_mismatch`
   (`native_worker.py:672-692, 824-842`; the `_is_provider_event` gate at 465-471 checks that a
   `type` key *exists* but never its value — contrast `parse_codex_thread_id`, which requires
   `thread.started`). Fails closed, so robustness not authority — but identity capture should be
   event-typed per host, and must be for a grok arm.
3. **Transient-failure detection is substring matching over task output.**
   `_transient_provider_failure` scans every redacted line for markers like `timeout`/`429`;
   a genuinely failed execution phase whose test logs merely contain such a word burns the full
   300+600+1200 s blocking backoff ladder and three paid resumes
   (`native_worker.py:445-462, 810-818, 1249-1277`). Markers should apply to provider/stderr
   channels or typed provider events, not task stdout.
4. **Duplicated validators with drifting semantics.** Two `_run_key` implementations (strict vs
   lax) on the two sides of one prewalk launch; three bounded-JSON readers; private-JSON writers
   in `native_worker.py`/`implement.py` lacking the hardened `storage.atomic_write_json`
   symlink/dir-fd defenses; and the forbidden-session-token set at `native_worker.py:158-162` is
   `{last, latest, --last}` — a session id literally spelled `continue` passes there while
   `implement.py:1443` forbids it, contradicting `prewalk.md`'s "never `--last`/`--continue`"
   (verified). Small fix, worth making before a third host multiplies the copies.
5. **Error-handling gaps in the prewalk supervisor:** uncaught `subprocess.TimeoutExpired` from
   the git helpers can strand a run in `prewalking`/`executing` with a raw traceback; the follower
   dies on one torn JSON line in the follow log; a failure *after* provider events are emitted
   records `stderr_tail: None` in state.
6. **Misc:** dead inventories (`PREWALK_FAILURE_CODES` is not used to validate emitted codes — a
   typo'd new code passes silently); `_guide_recovery_failure_reason` reports a
   worktree-continuity code where the post-edit-fallback code is accurate; display-only
   `native_worker_profiles()` masquerading as a registry (§4.3).

**Security posture — positive.** No `shell=True`/`eval`/`os.system` anywhere in `scripts/`;
argv-list subprocess construction throughout; `-`-prefixed session ids rejected before argv
placement; O_NOFOLLOW dir-fd atomic 0600 writes; child envs strip `GIT_*`/`GH_TOKEN`/SSH state and
disable push URLs (`remote.origin.pushurl=disabled://`, `GIT_ALLOW_PROTOCOL=file`); the provider
stop capability is an HMAC secret passed once over a pre-closed stdin pipe; Grok credential and
executable chains are ACL/owner/mode-validated fail-closed. The trusted lane is honestly framed as
credential minimization, not malicious-worker containment.

### 5.3 Documentation and contracts

The corpus is exceptionally internally consistent — because a 2,172-line phrase-pinning policy
enforces it. Findings:

- **Restatement debt.** The `retained_safe`-only activation rule appears normatively in ≥6 files;
  the prewalk summary cluster in 8; the yolo-flag rule in 6 — each a hand-synchronized sentence,
  in tension with README's own "each contract lives in exactly one file" doctrine. The linter
  keeps them aligned today; every new copy raises the cost of the §4.3 contract amendments.
- **Glossary gaps.** `glossary.md` declares "if a term is not in this file, it is not project
  vocabulary" — yet **prewalk**, guide/execution route, instruction fidelity,
  `retained_safe`/`pruned`/`turn_scoped`, Lane A, Mode A1, main/work driver,
  `implementation_lane`, and state capsule are all absent, while the glossary's "Handling matrix"
  entry is used nowhere. The headline v2.8 feature is undefined in the file whose job is defining
  terms.
- **Stale/rot risks.** Exact tool versions pinned in durable tables (`host-parity.md:30`); mixed
  0.2.93/0.2.101 guidance in the Grok launch prompt with no applicability markers (and the
  open-source tree is already 0.2.102); a run-specific canary result stated in normative present
  tense (`grok-open-source-worker.md:50-52`); `TODO.md` is ~95% completed-checkbox archive with
  the one live item buried mid-file; an installed-bundle dead link
  (`councilelves-launch-prompt.md:96` → repo-only `docs/plans/` path, which the neighboring
  contract forbids depending on).
- **Small contradictions.** The canonical work-driver spelling map omits `opencode-cli`, which
  other docs use; `grok-4.5` is framed as plausibly-valid in SKILL.md and as an
  invented/unavailable id in the routing doc; `/goal` is overloaded between Codex Goals and Grok
  goal mode (the docs themselves need "Do not confuse" warnings to manage it); stale `(v2.1)`/
  `(v2.3)` stamps survive the "version narration lives only in the changelog" rule.

### 5.4 Process observation

607 commits in ~4 months, with worker-batch commit trailers, a Pages-deployed guide, and a
consistency gate on every PR. The discipline is real. The recurring pattern across all findings
is the same: **contracts are duplicated into prose, code, and phrase-pins faster than
abstractions are extracted** — the prewalk host table exists in two docs and zero code
interfaces, the normative sentences exist in up to eight files, and the host grammar exists in
four `if/elif` sites. The Grok port is the natural forcing function to extract the host profile
registry and collapse the duplication.

---

## 6. Prioritized recommendations

| # | Priority | Recommendation | Anchor |
|---|---|---|---|
| 1 | High | Add an explicit Python ≥3.10 interpreter-floor guard with a clear error | `sync_installed_skills.py:323`, entry scripts |
| 2 | High | Event-type the session-identity capture per host (value, not presence, of `type`) | `native_worker.py:465-471, 672-692` |
| 3 | High | Scope transient-failure markers to provider/stderr channels, not task stdout | `native_worker.py:445-462` |
| 4 | High | If Grok prewalk is wanted: adopt the §4.4 phased plan; Phase 1 includes a real host-profile registry | `worker_routing.py:997-999` + §4.3 list |
| 5 | Medium | Unify the forbidden-session-token sets (include `continue`) and the duplicated `_run_key`/JSON readers/writers on the hardened variants | `native_worker.py:158-162`, `implement.py:1443`, `storage.py` |
| 6 | Medium | Add glossary entries for prewalk and its vocabulary; delete the dead entry | `references/glossary.md` |
| 7 | Medium | Catch `subprocess.TimeoutExpired` in the prewalk supervisor and the follower's torn-line case; preserve `stderr_tail` after provider events | `native_worker.py:660-722, 864-868, 1591-1611` |
| 8 | Medium | Continue the `full_run.py` decomposition (monitor/await extraction) before further feature work lands on it | `full_run.py:5473-7121` |
| 9 | Low | Add applicability markers to version-specific Grok guidance (0.2.93 vs ≥0.2.101) and refresh the pinned parity versions | `grok-implementer-launch-prompt.md:172-229` |
| 10 | Low | Split `TODO.md` into archive and live backlog; fix the installed-bundle dead link; reconcile `opencode-cli` and `grok-4.5` spellings | `TODO.md`, `councilelves-launch-prompt.md:96`, `schema-and-acceptance.md:149-156` |
| 11 | Low | Wire `PREWALK_FAILURE_CODES` into emission-time validation so typo'd codes fail tests | `prewalk.py:36-65` |

---

## Appendix: verification record

Fourteen adversarial verifiers each attempted to refute one load-bearing claim against source.
Outcomes: 9 confirmed, 5 corrected, 0 refuted. Material corrections (all folded into the body
above): headless yolo/auto flag-combination semantics were inverted in the draft (the operational
rule survives, stronger); grok `todo_write` cross-resume persistence is vestigial in the
open-source tree; the Elves stream-identity gate does check for a `type` key's presence (the gap
is its value); `-s/--session-id` uniqueness has `--fork-session` and `--worktree` edge cases;
`qualified()` additionally requires non-null qualified efforts. Grok-build findings describe the
published source at the commit above; per Elves' own contract, the installed executable's observed
behavior — not upstream source — remains launch authority, and nothing in this review claims
behavioral qualification for any host.
