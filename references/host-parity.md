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
| Grok Build goal | optional worker capability | same optional worker capability |

## Do not confuse

- **Codex Goals** — host continuation plumbing for long Codex sessions. Not Grok.
- **Grok Build goal mode** — optional trusted-worker orchestration when capability-proven.
  Otherwise the compatible one-packet fallback is recorded honestly.

## Canonical docs

| Doc | Role |
|-----|------|
| `SKILL.md` | Compact canonical workflow |
| `AGENTS.md` | Thin Codex repository adapter (not a second fork) |
| `README.md` | Operator documentation |
| `references/*` | Runtime, authority, follow, proof, schema details |

Native-only overnight runs require no Grok, OpenRouter, or other external provider.
