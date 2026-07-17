# Host parity: Claude Code and Codex

Workflow semantics are identical. Invocation surfaces differ.

| Concern | Claude Code | Codex |
|---------|-------------|-------|
| Skill load | Project/global Agent Skill | Project/global Agent Skill |
| Primary invoke | `/elves`, natural language | `$elves`, natural language |
| Cobbler | `/cobbler`, `/cobbler-mode` | `$elves cobbler: …`, natural chat |
| Setup | `/setup-cobbler` | `$elves setup-cobbler` |
| Land PR | `/land-pr` or `\land-pr` | natural language or alias |
| Continuation | optional | optional **Codex Goals** (seatbelt, not memory) |
| Native worker | Separate custom/background session; supervised CLI uses safe mode and classifier-approved commits | Separate custom agent or sandboxed `codex exec`; narrow Git roots permit commits |
| Visibility | Proven native agent view or exact private-log follow command | Proven native agent view or exact private-log follow command |
| Exact resume | `--resume <uuid>` | `codex exec resume <thread-id>` from registered worktree CWD |
| Grok Build goal | proven enhancement or one-packet fallback | same proven enhancement or fallback |

Both hosts read safe worker preferences from the same XDG file and make the same deterministic
decision. Transport syntax differs; packet, authority, fallback, follow, and terminal-review
semantics do not. See [`adaptive-worker-routing.md`](adaptive-worker-routing.md).
When checking a route, pass `--host claude` from Claude Code and `--host codex` from Codex so any
native fallback uses the live driver's transport.

## Exact-session prewalk parity

Prewalk is supported only through a behaviorally qualified supervised transport. Both hosts must
provide the same guide→meaningful-edit checkpoint→execution lifecycle and make the same user claim;
syntax alone is not parity.

| Concern | Claude Code 2.1.207 grammar | Codex 0.144.1 grammar | Shared requirement |
|---|---|---|---|
| Fresh identity | caller-generated `--session-id <uuid>` | capture `thread.started.thread_id` | exact ID before transition |
| Guide route | `--model`, `--effort` | `--model`, `model_reasoning_effort` | explicit guide model/effort |
| Resume | `--resume <uuid>` | `codex exec resume <id>` | never `--continue`, `--last`, or latest |
| Resume route | model/effort flags with resume | route/sandbox/Git-root flags before `resume` | explicit execution model/effort |
| Worktree | supervisor CWD + narrow allowed roots | `-C` on create; supervisor OS CWD on resume | exact registered worktree/branch |
| Stream | stream JSON | JSONL | one redacted logical follow stream with phase labels |
| TODO/checkpoint | native mechanism plus private JSON mirror | native mechanism plus private JSON mirror | same bounded provider-neutral schema |
| Instruction fidelity | version-bound behavioral evidence | version-bound behavioral evidence | honest `pruned`, `turn_scoped`, `retained_safe`, or `unsupported` |
| Git authority | safe mode, `auto` classifier, narrow roots | workspace sandbox, narrow roots | no push/protected-ref/PR/merge authority |
| Failure | exact-session recovery | exact-session recovery | no post-edit cold fallback; same stable codes |

The packet appears only on the guide turn and execution receives only `Continue.`. Static help
fixtures prove advertised create/resume/route flags but not conversation continuity or instruction
pruning. The current persisted-instruction transport activates only for behaviorally proven
`retained_safe`; `pruned` and `turn_scoped` remain schema states for future delivery mechanisms.
Consequently the shipped code is feature-gated and `auto` remains actually off for an unqualified
installed version on either host. A release must not claim general prewalk availability when one
primary host would silently start a new session. See [`prewalk.md`](prewalk.md).

## Do not confuse

- **Codex Goals** — host continuation plumbing for long Codex sessions. Not Grok.
- **Grok Build goal mode** — optional trusted-worker orchestration when capability-proven.
  `/goal status` uses the narrow auth projection and proves command resolution independently of
  catalog lookup and model inference. A validated terminal objective-canary artifact bound to the
  exact installed version/build, canonical session, prompt digest, successful exit, and matching end
  event is required for behavioral goal mode; otherwise the compatible one-packet fallback is
  recorded honestly without disabling an authenticated provider whose core launch capabilities and
  live catalog qualify.

Both hosts apply the same installed-binary capability ledger, caller-generated Grok session UUID,
narrow auth projection, catalog-only model selection, and sanitized streaming follower. See
[`grok-open-source-worker.md`](grok-open-source-worker.md).

## Canonical docs

| Doc | Role |
|-----|------|
| `SKILL.md` | Compact canonical workflow |
| `AGENTS.md` | Thin Codex repository adapter (not a second fork) |
| `README.md` | Operator documentation |
| `references/*` | Runtime, authority, follow, proof, schema details |

Native-only overnight runs require no Grok, OpenRouter, or other external provider.
