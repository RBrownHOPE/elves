# Subscription-native worker design checkpoint

**Status:** research checkpoint; implementation not started
**Date:** 2026-07-15

## Decision summary

Elves should make a **subscription-native worker** a first-class path. A user who has only Codex or
Claude Code should be able to keep the smart host agent focused on planning, authority, terminal
review, and landing while a separate lower-effort agent performs the implementation run on the same
subscription.

The abstraction should be the worker seat, not recursive CLI invocation:

1. Prefer the host's supported native custom-agent/subagent surface.
2. Use a supervised CLI child only when the native surface cannot provide the required lifecycle.
3. Keep Grok Build, Devin, Cursor, and other workers as optional routes rather than prerequisites.

The host agent does not need to lower its own effort. It hands off one complete execution packet,
parks without model polling, and wakes only for a deterministic failure, an explicit blocker,
completion, or a Stop Gate event. It then performs the cumulative review, focused proof, CI and PR
feedback reconciliation, and authorized landing.

```text
smart host driver
  plan + risk/caution map + immutable execution packet
                         |
                         v
subscription-native worker (same account, lower effort, separate context/worktree)
  implement + focused tests + regular progress commits
                         |
                         v
smart host driver
  terminal review + consolidated fixes + delta review + land when authorized
```

## Preferred host transports

| Host | Preferred worker surface | Qualified fallback |
|---|---|---|
| Codex | Named custom agent with `model` and `model_reasoning_effort` | Supervised `codex exec` child |
| Claude Code | Custom subagent with `model`, `effort`, `background`, and `isolation: worktree` | Externally launched Claude session, not Claude-inside-Claude recursion |

This is semantic parity, not identical transport. Both hosts must preserve the same packet,
authority, isolation, progress, failure, review, and landing contracts.

### Codex

Current Codex documentation supports project or user custom agents that can pin both `model` and
`model_reasoning_effort`. The app and CLI expose the resulting child threads so the user can watch
the worker directly.

A useful default agent can inherit the parent's exact model while setting low effort. An optional
fast agent can select a smaller native model, but that is a quality/routing decision and should not
be conflated with cache preservation.

The current Codex collaboration tool in this desktop session does not expose a model or effort
argument directly. Elves therefore must capability-probe whether a named configured agent is
actually selectable on the active host. Codex custom-agent configuration also does not currently
offer Claude's declarative `isolation: worktree` field, so Elves must create and bind the worktree
itself and independently verify the child's effective working root.

If native selection or lifecycle control is insufficient, a supervised `codex exec` child is a
credible fallback. A read-only local probe succeeded using existing subscription authentication.
The fallback must capture the `thread.started.thread_id`, never use ambiguous `--last`, and resume
with `codex exec resume <SESSION_ID>`. The supervisor must set the resumed process's OS working
directory to the registered worktree because the installed `exec resume` surface has no `-C` flag.

### Claude Code

Current Claude Code custom subagents natively support model selection, effort, background
execution, tool/permission restrictions, durable subagent transcripts, and worktree isolation.
That is the natural same-subscription worker surface.

Claude Code sets `CLAUDECODE=1` in shells it spawns and intentionally rejects nested Claude Code
launches because the sessions share runtime resources. Elves must not build a product path around
unsetting that sentinel. `claude -p` remains useful when launched by an external supervisor, but it
is not the normal Claude-inside-Claude mechanism.

Claude's `--bare` mode is also unsuitable for a subscription-native fallback because it skips OAuth
and keychain reads. A future external CLI adapter should retain subscription auth while explicitly
controlling MCP, hooks, plugins, permissions, and supplied context.

## Worker contract

The native worker receives one standalone packet containing:

- intent and non-obvious rationale;
- plan acceptance and risk/caution map;
- Build On targets;
- owned and forbidden surfaces;
- expected evidence and focused tests;
- branch, worktree, starting HEAD, and packet identity;
- permission to edit, test, commit regularly, and push only when explicitly granted;
- prohibition on PR operations, merge, tags, protected refs, and canonical run-memory edits.

The worker should execute the whole plan without mid-run driver review. Its native agent view or raw
stream is the user's observation window. Git commits remain the durable progress UI. A missing
Elves Report or completion summary is not grounds to reject otherwise valid work; the host can
construct the report during reconciliation.

The worker may be wrong. That is expected and is not a reason to add a correctness gate after its
first edit. Terminal host review, relevant tests, CI, and actionable GitHub feedback are the quality
backstops.

## Cache conclusions

Three different things must remain distinct:

1. **Session continuity** preserves provider-managed conversation history and tool results when an
   exact worker session is resumed.
2. **Prompt/KV caching** is provider-side reuse of matching request prefixes.
3. **Hidden model state** is not a portable artifact that Elves can export or hand to another
   process or model.

There is no supported Codex or Claude Code CLI mechanism to serialize a cache from the parent and
pass it to the worker. A session ID is not a cache handle. Provider-side caching may occur
automatically, but it is an optimization rather than an Elves guarantee.

### Local Codex evidence

The installed `codex-cli 0.144.1` was probed without repository writes:

| Probe | Result |
|---|---|
| Fresh ephemeral low-effort child | Succeeded in 2.6 seconds; 14,867 input tokens and 0 cached input tokens |
| Fresh dedicated session at medium effort, after the first probe | 17,276 input tokens; 6,912 cached input tokens |
| Exact same session resumed at low effort | Same thread ID; 34,686 input tokens; 16,896 cached input tokens |

This demonstrates that the current Codex/provider combination can automatically reuse useful
prefix cache across a same-default-model medium-to-low effort change. It does **not** show that
Elves passed a cache, that a child inherited the parent's conversation cache, or that the result is
guaranteed on another version/account/model.

The first nested Codex invocation also emitted state-database discrepancy warnings while still
succeeding. A CLI fallback should use a dedicated worker session, isolate SQLite-backed runtime
state where supported, and qualify concurrent parent/child behavior rather than assuming it.

### Cache policy

- Prefer the exact same model with lower effort when the goal is to preserve capability and maximize
  the chance of prefix-cache reuse.
- Preserve the exact worker session and keep system instructions, tool definitions, and the packet
  prefix stable.
- Do not resume or concurrently mutate the smart driver's own session.
- Treat a switch to another model, even in the same named family, as a cache miss unless the
  provider reports otherwise. The textual session may continue while the new model reprocesses it.
- Record provider cache telemetry when exposed (`cached_input_tokens` or Claude cache usage fields),
  but never make a cache hit an acceptance or launch gate.
- Do not add orchestration complexity solely to chase cache reuse until representative runs show a
  meaningful latency or quota benefit.

Anthropic documents that changes to thinking parameters can invalidate message-level cache sections
while leaving earlier tool/system sections reusable. OpenAI documents automatic exact-prefix prompt
caching and cache telemetry. Neither subscription CLI exposes a transferable cache object.

## Recommended implementation sequence

### 1. Native worker MVP

Add one transport-neutral `subscription_native_worker` lane:

- one smart host driver;
- one complete packet;
- one lower-effort native worker;
- one registered worktree and feature branch;
- regular concrete progress commits;
- native worker view/raw stream;
- parked host with deterministic wake conditions;
- one cumulative terminal review and focused revision loop.

Start the worker directly at the selected labor effort. The smart host's plan already supplies the
high-quality orientation. Do not add a high-to-low worker transition, swarms, parallel write lanes,
or model councils to the MVP.

### 2. Host-native agent definitions

Provide semantically matched installed definitions:

- Codex: inherit the host model and set `model_reasoning_effort` to `low`.
- Claude Code: inherit the host model, set `effort: low`, and use `isolation: worktree`.

Keep an optional careful profile for high-risk runs and an optional smaller-model profile for
low-risk work. Planning selects the profile from declared risk; the driver does not micromanage it
mid-run.

### 3. Capability qualification

Before making a route available, prove:

- installed version and subscription authentication;
- named worker selection and requested model/effort binding;
- actual model/effort evidence where the host exposes it;
- dedicated session identity and exact resume behavior;
- registered-worktree binding and collision protection;
- live progress visibility and completion/failure signaling;
- unattended edit, focused-test, and commit permissions;
- MCP, plugin, hook, environment, and credential boundaries;
- stop, death, timeout, and recovery behavior;
- separate explicit grant for worker push authority;
- informational cache telemetry.

Unsupported optional capabilities must fall back honestly. Only a user- or project-declared
`required: true` capability should block a run.

### 4. Codex CLI fallback

After qualification, add a supervised child adapter that:

- launches `codex exec --json` in the registered worktree;
- uses `--ignore-user-config` while retaining auth from `CODEX_HOME`;
- disables unnecessary rules/MCP/customization and sets non-interactive permissions explicitly;
- captures the exact thread ID and JSONL transcript;
- records PID/fingerprint, heartbeat, route, packet, branch, worktree, and starting HEAD;
- resumes only the exact session from the exact worktree;
- wakes the host on process death, stall, malformed completion, blocker, or success.

The host must not semantically inspect the stream during a healthy run. The user can watch it
directly, while the supervisor performs only deterministic process checks.

### 5. Optional trajectory experiment

Implemented and superseded by the feature-gated exact-session prewalk contract in
`references/prewalk.md`. The dedicated worker begins on the guide route, writes a bounded TODO and
first-meaningful-edit checkpoint, then resumes the same exact session and worktree on the execution
route without a driver correctness gate. Unsupported hosts stay on the single-phase worker path.

## Existing repository gaps to fix before CLI fallback

The current generic session builders should not be reused unchanged:

- `scripts/cobbler_runtime/adapters.py` emits `--session-create` for Claude and Codex, but neither
  installed CLI supports that flag.
- The same module builds Claude resume commands with unsupported `--cwd` and Codex resume commands
  as `codex exec --session-id ... --cwd`; installed Codex requires
  `codex exec resume <SESSION_ID>`.
- `tests/test_cobbler_agents_sessions.py` mostly checks that expected strings appear in argv; it
  does not qualify builders against versioned real CLI grammars.
- `scripts/cobbler_runtime/full_run.py` assumes fixture/Grok/Devin transports in important launch
  paths and needs a transport-neutral native-worker route.
- `scripts/cobbler_runtime/schema.py` records requested model information but does not yet model
  requested/actual effort and worker transport comprehensively.
- Existing isolated HOME/XDG behavior can hide subscription auth. Native auth inheritance or a
  narrow explicit auth-state grant is required; ambient credential projection is not.

These are implementation findings, not a request to broaden the first PR. Fix and test the minimum
surface needed by the native-worker lane.

## External challenge and unresolved questions

Gemini 3.1 Pro recommended treating recursive CLI as an adapter, isolating runtime/config state,
pre-authorizing unattended tools, and budgeting as if cache reuse were zero. The native-agent-first
design adopts the isolation and adapter advice. The local Codex measurement shows that cache reuse
can be substantial, so the design records and exploits it opportunistically rather than assuming
zero or promising a hit.

A Sakana Fugu Ultra request did not return before this checkpoint, so no Sakana opinion is
attributed to this design.

Open questions for implementation qualification:

- Can every current Codex host surface select a named custom worker with verifiable model/effort?
- What is the strongest enforceable worktree binding for a Codex native subagent?
- Do native worker threads survive host compaction/restart well enough for full-run recovery, or
  should Git checkpoints plus a CLI session remain the durable fallback?
- How does Claude background-subagent completion wake the parent without model polling?
- Can a Claude subagent's effort be changed on exact resume, or should Claude stay single-effort?
- What quota headroom should be reserved so a long worker cannot prevent terminal host review?
- On representative tasks, how do same-model/low-effort native workers compare with Grok Composer
  and other optional workers in elapsed time, quality, and quota use?

## Primary references

- [Codex subagents and custom agents](https://developers.openai.com/codex/subagents)
- [Codex non-interactive mode](https://developers.openai.com/codex/non-interactive-mode)
- [OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)
- [Claude Code custom subagents](https://code.claude.com/docs/en/sub-agents)
- [Claude Code worktree isolation](https://code.claude.com/docs/en/worktrees)
- [Claude Code non-interactive mode](https://code.claude.com/docs/en/headless)
- [Claude Code environment variables](https://code.claude.com/docs/en/env-vars)
- [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
