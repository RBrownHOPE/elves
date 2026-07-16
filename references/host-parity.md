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
| Native worker | Separate custom/background session; model inherited, effort plan-matched | Separate custom agent or supervised `codex exec`; model inherited, effort plan-matched |
| Visibility | Proven native agent view or exact private-log follow command | Proven native agent view or exact private-log follow command |
| Exact resume | `--resume <uuid>` | `codex exec resume <thread-id>` from registered worktree CWD |
| Grok Build goal | proven enhancement or one-packet fallback | same proven enhancement or fallback |

Both hosts read safe worker preferences from the same XDG file and make the same deterministic
decision. Transport syntax differs; packet, authority, fallback, follow, and terminal-review
semantics do not. See [`adaptive-worker-routing.md`](adaptive-worker-routing.md).

## Do not confuse

- **Codex Goals** — host continuation plumbing for long Codex sessions. Not Grok.
- **Grok Build goal mode** — optional trusted-worker orchestration when capability-proven.
  Otherwise the compatible one-packet fallback is recorded honestly without disabling an
  authenticated provider whose core launch capabilities and live catalog qualify.

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
