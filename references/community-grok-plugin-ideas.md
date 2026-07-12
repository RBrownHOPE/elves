# Community Grok plugin ideas (future backlog)

**Credit:** Ideas below were reviewed from the open-source companions  
[stdevMac/grok-in-claude](https://github.com/stdevMac/grok-in-claude) and  
[stdevMac/grok-in-codex](https://github.com/stdevMac/grok-in-codex) (Apache-2.0).  
Those plugins keep Claude Code / Codex as the orchestrator and hand labor to the local Grok CLI.

Elves already owns overnight runs, writer leases, packets, gates, and Cobbler routing. We **do not**
vendor those plugins. This file records patterns worth considering later, and what we already
adapted.

## Already adapted into Elves

| Pattern | Where |
| --- | --- |
| Grok model aliases `fast` / `deep` | `implement prepare/launch --model`; `resolve_implement_model` |
| Optional Grok `--check` | `implement launch --check` / `resume-batch --check` |
| Humanized Grok CLI failure messages | `humanize_grok_failure` on failed `--exec` |
| Denylist vs allowlist tool gating note (0.2.93) | `references/grok-implementer-launch-prompt.md` |
| Main driver vs Grok work driver framing | PR #66 native-first docs (pre-existing Elves design) |

## Future candidates (not implemented)

### Shared structured review schema

- JSON envelope: `verdict`, `summary`, `findings[]` (severity/title/body/file/lines), `next_steps[]`
- Useful for OpenRouter lens, optional Grok review labor, and host synthesis
- Elves already has adversarial review prose in `references/review-subagent.md`; a schema would unify machine output
- **Action later:** add Elves-owned `references/review-output.schema.json` (inspired by, not copied from, their schema) and optional lens conformance

### Review packet builder

- Auto package working-tree / `base...HEAD` / `gh pr diff` with size caps
- Replaces ad-hoc dogfood packet scripts
- **Action later:** `scripts/build_review_packet.py` host helper (no paid calls)

### Concurrent external job status

- Background Grok jobs with pid, progress (`streaming-json`), status/result/cancel, ambiguous-id errors
- Fits multi-lens parallel review better than overnight single-lease implement
- **Action later:** only if Cobbler dispatches multiple long external jobs; do not replace writer lease

### Routing skill bullets

- When to stay on host vs call Grok (stuck debug, second opinion, best-of-N, media)
- **Action later:** fold into Cobbler/Grok recipes if operators keep re-deriving the same rules

### best-of-n / multi-session task list

- Expensive; opt-in scout use only
- Prefer exact session ids (Elves rule) over `resume-last`

### Stop-time review gate hook

- Their Claude Stop hook runs Grok review and blocks stop on critical/high
- They warn it burns usage; Elves already has validation + review + legality
- **Skip as product default**; operators may install their plugin separately

### Media pipeline

- Image/video under `.grok-media/` — outside Elves mission

### Transcript transfer (`grok import`)

- Overlaps host session resume tools; CLI import support is best-effort
- **Skip** unless Grok documents stable import

## What not to copy

- MCP / marketplace plugin packaging as Elves core
- Default write-capable “rescue” without host-import lease
- Replacing Lane A leases with ad-hoc worktree flags on Grok 0.2.93

## Attribution when landing future work

When implementing anything from this backlog, note in CHANGELOG / commit body:

> Inspired by patterns in stdevMac/grok-in-claude and stdevMac/grok-in-codex (Apache-2.0).
> Elves keeps host-owned implement leases and run memory; this is not a fork of those plugins.
